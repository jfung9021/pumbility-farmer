"""Prove hosted parity, then populate typed Supabase shadow rows without cutover.

Run only through ``vercel env run -e production``. The command keeps Vercel
authoritative, never updates a compatibility publication pointer, and requires
an explicit process-only confirmation before writing typed shadow metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis_runtime import VercelPrivateBlobStore  # noqa: E402
from phoenix2_sync import sanitize_snapshot  # noqa: E402
from piu_recommendations import (  # noqa: E402
    build_combined_chart_results,
    build_combined_tier_payload,
)
from pumbility_store import (  # noqa: E402
    CANONICAL_SNAPSHOT_WRITE_ENV,
    EXPECTED_PUMBILITY_MIGRATION,
    SHADOW_STRICT_ENV,
    _assert_schema,
    _enabled,
)
from recommendation_refresh import (  # noqa: E402
    MODEL_ARTIFACT_SCHEMA_VERSION,
    build_recommendation_model_artifacts,
    recommendation_index_path,
    recommendation_model_path,
    recommendation_phoenix1_shard_path,
    recommendation_phoenix2_shard_path,
    recommendation_score_model_path,
)
from scripts.analyze_pumbility_supabase import (  # noqa: E402
    DEFAULT_BOOTSTRAP_SAMPLES,
    AnalysisOutput,
    DatabaseInput,
    _analyze,
    _canonical_bytes,
    _persist_analysis,
    _read_database_input,
    _sha256,
)
from scripts.backfill_pumbility_production import (  # noqa: E402
    EXPECTED_PROJECT_REF,
    _assert_boundary_unchanged,
    _assert_database_target,
    _read_stable_boundary,
)
from scripts.capture_pumbility_migration_baseline import (  # noqa: E402
    _exact_json_bytes,
    _required_production_bytes,
    _required_production_json,
    _semantic_analysis_payload,
)
from scripts.reconcile_pumbility_production import session_url_from_runtime  # noqa: E402
from scripts.reconcile_pumbility_supabase import reconcile  # noqa: E402


CONFIRMATION_ENV = "PUMBILITY_PRODUCTION_POPULATION_CONFIRMATION"
CONFIRMATION = f"POPULATE {EXPECTED_PROJECT_REF} {EXPECTED_PUMBILITY_MIGRATION}"
MAX_INPUT_SHARDS = 1_000
NUMERIC_MODEL_ABSOLUTE_TOLERANCE = 1e-8


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--bootstrap-samples",
        type=int,
        default=DEFAULT_BOOTSTRAP_SAMPLES,
        help="Production-equivalent analysis bootstrap count (default: 500).",
    )
    return parser


def _assert_flags_off(environment: Mapping[str, str]) -> None:
    backend = str(environment.get("PUMBILITY_DATA_BACKEND", "vercel")).strip().casefold()
    if (backend or "vercel") not in {"vercel", "shadow"}:
        raise RuntimeError("Hosted population requires Vercel-authoritative reads.")
    if _enabled(environment.get(SHADOW_STRICT_ENV)):
        raise RuntimeError("Hosted population requires strict shadow mode to remain disabled.")
    if _enabled(environment.get(CANONICAL_SNAPSHOT_WRITE_ENV)):
        raise RuntimeError("Hosted population requires canonical snapshot writes to remain disabled.")


def _npz_difference_summary(
    first: bytes,
    second: bytes,
    *,
    absolute_tolerance: float = 0.0,
) -> list[dict[str, Any]]:
    if absolute_tolerance < 0:
        raise ValueError("The numeric-model absolute tolerance cannot be negative.")
    mismatches: list[dict[str, Any]] = []
    try:
        with np.load(io.BytesIO(first), allow_pickle=False) as left, np.load(
            io.BytesIO(second), allow_pickle=False
        ) as right:
            for name in sorted(set(left.files) | set(right.files)):
                if name not in left.files or name not in right.files:
                    mismatches.append({"array": name, "reason": "missing"})
                    continue
                if left[name].dtype != right[name].dtype or left[name].shape != right[name].shape:
                    mismatches.append(
                        {
                            "array": name,
                            "reason": "shape-or-dtype",
                            "candidateShape": list(left[name].shape),
                            "sourceShape": list(right[name].shape),
                            "candidateDtype": str(left[name].dtype),
                            "sourceDtype": str(right[name].dtype),
                        }
                    )
                    continue
                if left[name].dtype.kind in {"f", "c"}:
                    matches = np.isclose(
                        left[name],
                        right[name],
                        rtol=0.0,
                        atol=absolute_tolerance,
                        equal_nan=True,
                    )
                    equal = bool(np.all(matches))
                else:
                    matches = left[name] == right[name]
                    equal = bool(np.all(matches))
                if not equal:
                    difference_count = int(np.count_nonzero(~matches))
                    detail: dict[str, Any] = {
                        "array": name,
                        "reason": "values",
                        "differenceCount": difference_count,
                    }
                    if left[name].dtype.kind in {"f", "c", "i", "u"}:
                        finite = np.isfinite(left[name]) & np.isfinite(right[name])
                        detail["maxAbsoluteDifference"] = (
                            float(np.max(np.abs(left[name][finite] - right[name][finite])))
                            if np.any(finite)
                            else None
                        )
                    mismatches.append(detail)
    except (KeyError, OSError, ValueError):
        return [{"array": "container", "reason": "invalid"}]
    return mismatches


def _npz_arrays_equal(first: bytes, second: bytes) -> bool:
    return not _npz_difference_summary(first, second)


def _assert_json_equal(actual: Mapping[str, Any], expected: Mapping[str, Any], role: str) -> None:
    if _exact_json_bytes(actual) != _exact_json_bytes(expected):
        raise RuntimeError(f"Hosted {role} parity failed.")


def _assert_source_rows_equal(
    source: Mapping[str, Any], database_input: DatabaseInput
) -> None:
    normalized = sanitize_snapshot(source, mix=database_input.mix_key)
    for entity in ("players", "charts", "scores"):
        if normalized[entity] != database_input.snapshot[entity]:
            raise RuntimeError("Hosted typed analysis input rows failed exact parity.")


def _verify_relational(
    source_snapshots: Mapping[str, Mapping[str, Any]],
    inputs: Mapping[str, DatabaseInput],
    *,
    private_key: bytes,
) -> dict[str, Any]:
    results = {
        mix: reconcile(
            sanitize_snapshot(source_snapshots[mix], mix=mix),
            inputs[mix].snapshot,
            key=private_key,
            accepted_changes=set(),
        )
        for mix in ("phoenix1", "phoenix2")
    }
    unexplained = sum(int(result["unexplainedMismatchCount"]) for result in results.values())
    if unexplained:
        raise RuntimeError("Hosted typed population found relational mismatches.")
    for mix in ("phoenix1", "phoenix2"):
        _assert_source_rows_equal(source_snapshots[mix], inputs[mix])
    return results


def _verify_analysis(
    outputs: Mapping[str, AnalysisOutput],
    pointers: Mapping[str, Mapping[str, Any]],
) -> None:
    expected = _semantic_analysis_payload(pointers["phoenix2Analysis"])
    actual = _semantic_analysis_payload(outputs["phoenix2"].payload)
    if _canonical_bytes(actual) != _canonical_bytes(expected):
        raise RuntimeError("Hosted Phoenix 2 analysis semantic parity failed.")


def _verify_model(
    source: VercelPrivateBlobStore,
    inputs: Mapping[str, DatabaseInput],
    pointers: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], bytes, list[dict[str, Any]], list[dict[str, Any]]]:
    combined_charts, slopes, metadata = build_combined_chart_results(
        inputs["phoenix1"].snapshot,
        inputs["phoenix2"].snapshot,
    )
    combined_generated_at = str(pointers["combinedTier"].get("generatedAtUtc") or "")
    combined = build_combined_tier_payload(
        combined_charts,
        metadata,
        generated_at_utc=combined_generated_at,
    )
    _assert_json_equal(combined, pointers["combinedTier"], "combined-tier")
    print(json.dumps({"status": "stage-completed", "stage": "combined-parity"}, sort_keys=True))

    active_index = dict(pointers["recommendations"])
    generation = str(active_index.get("generationKey") or "")
    generated_at = str(
        active_index.get("modelGeneratedAtUtc") or active_index.get("generatedAtUtc") or ""
    )
    shard_count = int(active_index.get("inputShardCount") or 0)
    if not generation or not generated_at or shard_count < 1 or shard_count > MAX_INPUT_SHARDS:
        raise RuntimeError("The active hosted recommendation model boundary is invalid.")
    artifacts = build_recommendation_model_artifacts(
        inputs["phoenix1"].snapshot,
        inputs["phoenix2"].snapshot,
        combined_charts=combined_charts,
        phoenix2_slopes=slopes,
        generation_key=generation,
        generated_at_utc=generated_at,
    )
    print(json.dumps({"status": "stage-completed", "stage": "model-compute"}, sort_keys=True))
    index, model, score_bytes, phoenix1_shards, phoenix2_shards = artifacts
    _assert_json_equal(index, active_index, "recommendation-index")
    print(json.dumps({"status": "stage-completed", "stage": "model-index-parity"}, sort_keys=True))
    _assert_json_equal(
        model,
        _required_production_json(source, recommendation_model_path(generation), "model"),
        "recommendation-model",
    )
    print(json.dumps({"status": "stage-completed", "stage": "model-json-parity"}, sort_keys=True))
    versioned = _required_production_json(
        source, recommendation_index_path(generation), "versioned recommendation index"
    )
    _assert_json_equal(index, versioned, "versioned recommendation-index")
    print(json.dumps({"status": "stage-completed", "stage": "versioned-index-parity"}, sort_keys=True))
    if len(phoenix1_shards) != shard_count or len(phoenix2_shards) != shard_count:
        raise RuntimeError("Hosted recommendation input shard counts failed parity.")
    for shard in range(shard_count):
        _assert_json_equal(
            phoenix1_shards[shard],
            _required_production_json(
                source,
                recommendation_phoenix1_shard_path(generation, shard),
                "Phoenix 1 recommendation input",
            ),
            "Phoenix 1 recommendation-input",
        )
        _assert_json_equal(
            phoenix2_shards[shard],
            _required_production_json(
                source,
                recommendation_phoenix2_shard_path(generation, shard),
                "Phoenix 2 recommendation input",
            ),
            "Phoenix 2 recommendation-input",
        )
    print(json.dumps({"status": "stage-completed", "stage": "model-shard-parity"}, sort_keys=True))
    source_score_bytes = _required_production_bytes(
        source, recommendation_score_model_path(generation), "numeric recommendation model"
    )
    exact_npz_mismatches = _npz_difference_summary(score_bytes, source_score_bytes)
    npz_mismatches = _npz_difference_summary(
        score_bytes,
        source_score_bytes,
        absolute_tolerance=NUMERIC_MODEL_ABSOLUTE_TOLERANCE,
    )
    if npz_mismatches:
        print(
            json.dumps(
                {
                    "status": "mismatch",
                    "stage": "model-numeric-parity",
                    "arrays": npz_mismatches,
                },
                sort_keys=True,
            )
        )
        raise RuntimeError("Hosted numeric recommendation-model parity failed.")
    max_observed_difference = max(
        (
            float(detail.get("maxAbsoluteDifference") or 0.0)
            for detail in exact_npz_mismatches
        ),
        default=0.0,
    )
    print(
        json.dumps(
            {
                "status": "stage-completed",
                "stage": "model-numeric-parity",
                "exactArrayMatch": not exact_npz_mismatches,
                "absoluteTolerance": NUMERIC_MODEL_ABSOLUTE_TOLERANCE,
                "maxObservedAbsoluteDifference": max_observed_difference,
            },
            sort_keys=True,
        )
    )
    return index, model, source_score_bytes, phoenix1_shards, phoenix2_shards


def _persist_model_generation(
    connection: Any,
    *,
    analysis_run_id: Any,
    inputs: Mapping[str, DatabaseInput],
    artifacts: tuple[
        dict[str, Any],
        dict[str, Any],
        bytes,
        list[dict[str, Any]],
        list[dict[str, Any]],
    ],
) -> Any:
    from psycopg.types.json import Jsonb

    index, model, score_bytes, phoenix1_shards, phoenix2_shards = artifacts
    generation = str(index["generationKey"])
    input_hash = _sha256(
        {mix: inputs[mix].snapshot for mix in ("phoenix1", "phoenix2")}
    )
    output_hash = _sha256(
        {
            "index": index,
            "model": model,
            "numericModelSha256": hashlib.sha256(score_bytes).hexdigest(),
        }
    )
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            select id from pumbility.artifacts
            where object_key = %s and validated_at is not null
            """,
            (recommendation_model_path(generation),),
        )
        artifact = cursor.fetchone()
        if artifact is None:
            raise RuntimeError("The validated hosted model artifact is unavailable.")
        artifact_id = artifact[0]
        cursor.execute(
            """
            insert into pumbility.model_generations (
                generation_key, analysis_run_id, artifact_id, status,
                model_schema_version, input_hash, output_hash, metadata, completed_at
            ) values (%s, %s, %s, 'shadow', %s, %s, %s, %s, now())
            on conflict (generation_key) do nothing
            returning id
            """,
            (
                generation,
                analysis_run_id,
                artifact_id,
                str(MODEL_ARTIFACT_SCHEMA_VERSION),
                input_hash,
                output_hash,
                Jsonb(
                    {
                        "parity": "exact",
                        "playerCount": len(index.get("players", [])),
                        "inputShardCount": len(phoenix1_shards),
                        "phoenix2InputShardCount": len(phoenix2_shards),
                    }
                ),
            ),
        )
        inserted = cursor.fetchone()
        if inserted is not None:
            return inserted[0]
        cursor.execute(
            """
            select id, artifact_id, model_schema_version, input_hash, output_hash, status
            from pumbility.model_generations where generation_key = %s
            """,
            (generation,),
        )
        existing = cursor.fetchone()
        if existing is None or tuple(existing[1:5]) != (
            artifact_id,
            str(MODEL_ARTIFACT_SCHEMA_VERSION),
            input_hash,
            output_hash,
        ) or existing[5] not in {"shadow", "published"}:
            raise RuntimeError("The immutable hosted model generation conflicts with parity output.")
        return existing[0]


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.bootstrap_samples != DEFAULT_BOOTSTRAP_SAMPLES:
        raise ValueError("Hosted population requires the production-equivalent bootstrap count.")
    _assert_flags_off(os.environ)
    if not os.getenv("BLOB_READ_WRITE_TOKEN", "").strip():
        raise RuntimeError("Run hosted population through `vercel env run -e production`.")
    runtime_url = os.getenv("PUMBILITY_DATABASE_URL", "").strip()
    if not runtime_url:
        raise RuntimeError("The hosted runtime database URL was not injected.")
    if args.apply and os.getenv(CONFIRMATION_ENV) != CONFIRMATION:
        raise RuntimeError("The exact hosted population confirmation is required for --apply.")
    session_url = session_url_from_runtime(runtime_url)
    source = VercelPrivateBlobStore()
    pointers, phoenix1, phoenix2 = _read_stable_boundary(source)
    print(json.dumps({"status": "stage-completed", "stage": "source-boundary"}, sort_keys=True))
    source_snapshots = {"phoenix1": phoenix1, "phoenix2": phoenix2}
    private_key = os.environ["BLOB_READ_WRITE_TOKEN"].encode("utf-8")

    import psycopg

    with psycopg.connect(session_url, prepare_threshold=None) as connection:
        with connection.cursor() as cursor:
            _assert_schema(cursor)
            _assert_database_target(cursor)
        inputs = {
            mix: _read_database_input(connection, mix)
            for mix in ("phoenix1", "phoenix2")
        }
    relational = _verify_relational(source_snapshots, inputs, private_key=private_key)
    print(json.dumps({"status": "stage-completed", "stage": "relational"}, sort_keys=True))
    outputs = {
        mix: _analyze(inputs[mix], args.bootstrap_samples)
        for mix in ("phoenix1", "phoenix2")
    }
    print(json.dumps({"status": "stage-completed", "stage": "analysis-compute"}, sort_keys=True))
    _verify_analysis(outputs, pointers)
    print(json.dumps({"status": "stage-completed", "stage": "analysis-parity"}, sort_keys=True))
    artifacts = _verify_model(source, inputs, pointers)
    print(json.dumps({"status": "stage-completed", "stage": "model-parity"}, sort_keys=True))
    _assert_boundary_unchanged(source, pointers, phoenix2)
    print(json.dumps({"status": "stage-completed", "stage": "boundary"}, sort_keys=True))

    if args.apply:
        run_ids: dict[str, Any] = {}
        for mix in ("phoenix1", "phoenix2"):
            with psycopg.connect(session_url, prepare_threshold=None) as connection:
                run_ids[mix] = _persist_analysis(
                    connection,
                    outputs[mix],
                    run_key_prefix="production-shadow-analysis",
                )
        with psycopg.connect(session_url, prepare_threshold=None) as connection:
            _persist_model_generation(
                connection,
                analysis_run_id=run_ids["phoenix2"],
                inputs=inputs,
                artifacts=artifacts,
            )
        print(json.dumps({"status": "stage-completed", "stage": "typed-persistence"}, sort_keys=True))
        _assert_boundary_unchanged(source, pointers, phoenix2)
        print(json.dumps({"status": "stage-completed", "stage": "post-write-boundary"}, sort_keys=True))

    index = artifacts[0]
    print(
        json.dumps(
            {
                "status": "completed" if args.apply else "planned",
                "productionBackend": "vercel",
                "canonicalSnapshotWrites": False,
                "strictShadow": False,
                "publicationPointersChanged": False,
                "unexplainedMismatchCount": sum(
                    int(result["unexplainedMismatchCount"])
                    for result in relational.values()
                ),
                "analysis": {
                    mix: {
                        "chartResults": len(outputs[mix].chart_results),
                        "playerModeFeatures": len(outputs[mix].baselines),
                        "contributions": len(outputs[mix].contributions),
                    }
                    for mix in ("phoenix1", "phoenix2")
                },
                "model": {
                    "players": len(index.get("players", [])),
                    "inputShards": len(artifacts[3]),
                    "jsonParity": "exact",
                    "numericArrayParity": "absolute-tolerance",
                    "numericAbsoluteTolerance": NUMERIC_MODEL_ABSOLUTE_TOLERANCE,
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Pumbility hosted population failed safely; private details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
