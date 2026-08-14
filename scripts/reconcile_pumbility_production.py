"""Read-only exact reconciliation of hosted Supabase against stable production Blob."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis_runtime import VercelPrivateBlobStore  # noqa: E402
from phoenix2_sync import sanitize_snapshot  # noqa: E402
from pumbility_store import (  # noqa: E402
    CANONICAL_SNAPSHOT_WRITE_ENV,
    PumbilityArtifactIntegrityError,
    PumbilityArtifactStore,
    SHADOW_STRICT_ENV,
    _assert_schema,
    _enabled,
)
from scripts.backfill_pumbility_production import (  # noqa: E402
    EXPECTED_PROJECT_REF,
    _assert_boundary_unchanged,
    _assert_database_target,
    _read_stable_boundary,
    _recommendation_paths,
    validate_production_database_url,
)
from scripts.capture_pumbility_migration_baseline import (  # noqa: E402
    COMBINED_TIER_POINTER,
    PHOENIX2_ANALYSIS_POINTER,
    RECOMMENDATION_POINTER,
    _exact_json_bytes,
    _required_production_bytes,
    _required_production_json,
)
from scripts.reconcile_pumbility_supabase import _database_snapshot, reconcile  # noqa: E402


def _target_json(
    target: PumbilityArtifactStore,
    pathname: str,
    *,
    stage: str,
    artifact_index: int,
) -> dict[str, object] | None:
    try:
        return target.get_json(pathname)
    except PumbilityArtifactIntegrityError as error:
        print(
            json.dumps(
                {
                    "status": "integrity-failed",
                    "stage": stage,
                    "artifactIndex": artifact_index,
                    "digestMatches": error.digest_matches,
                    "byteSizeMatches": error.byte_size_matches,
                },
                sort_keys=True,
            )
        )
        raise RuntimeError("A hosted JSON artifact failed integrity validation.") from None
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "read-failed",
                    "stage": stage,
                    "artifactIndex": artifact_index,
                    "side": "target",
                    "errorType": type(error).__name__,
                },
                sort_keys=True,
            )
        )
        raise RuntimeError("A hosted JSON artifact could not be read safely.") from None


def session_url_from_runtime(runtime_url: str) -> str:
    parsed = urlsplit(runtime_url)
    if parsed.hostname != "aws-1-us-east-2.pooler.supabase.com" or parsed.port != 6543:
        raise ValueError("The runtime URL is not the approved transaction pooler.")
    if not parsed.username or not parsed.password:
        raise ValueError("The runtime URL is incomplete.")
    result = urlunsplit(
        (
            parsed.scheme,
            f"{parsed.username}:{parsed.password}@{parsed.hostname}:5432",
            parsed.path,
            parsed.query,
            "",
        )
    )
    validate_production_database_url(result, expected_project_ref=EXPECTED_PROJECT_REF)
    return result


def _verify_artifacts(
    source: VercelPrivateBlobStore,
    target: PumbilityArtifactStore,
    pointers: dict[str, dict[str, object]],
) -> dict[str, int]:
    json_paths, binary_path = _recommendation_paths(pointers["recommendations"])
    pointer_paths = {
        PHOENIX2_ANALYSIS_POINTER: pointers["phoenix2Analysis"],
        COMBINED_TIER_POINTER: pointers["combinedTier"],
        RECOMMENDATION_POINTER: pointers["recommendations"],
    }
    for artifact_index, pathname in enumerate(json_paths):
        try:
            source_value = _required_production_json(source, pathname, "source artifact")
        except Exception as error:
            print(
                json.dumps(
                    {
                        "status": "read-failed",
                        "stage": "model-json",
                        "artifactIndex": artifact_index,
                        "side": "source",
                        "errorType": type(error).__name__,
                    },
                    sort_keys=True,
                )
            )
            raise RuntimeError("A source JSON artifact could not be read safely.") from None
        target_value = _target_json(
            target, pathname, stage="model-json", artifact_index=artifact_index
        )
        if target_value is None or _exact_json_bytes(source_value) != _exact_json_bytes(target_value):
            print(
                json.dumps(
                    {
                        "status": "mismatch",
                        "stage": "model-json",
                        "artifactIndex": artifact_index,
                        "sourceType": type(source_value).__name__,
                        "targetType": type(target_value).__name__,
                    },
                    sort_keys=True,
                )
            )
            raise RuntimeError("A hosted JSON artifact failed exact reconciliation.")
    print(json.dumps({"status": "stage-completed", "stage": "model-json"}, sort_keys=True))
    for artifact_index, (pathname, source_value) in enumerate(pointer_paths.items()):
        target_value = _target_json(
            target, pathname, stage="pointers", artifact_index=artifact_index
        )
        if target_value is None or _exact_json_bytes(source_value) != _exact_json_bytes(target_value):
            raise RuntimeError("A hosted publication pointer failed exact reconciliation.")
    print(json.dumps({"status": "stage-completed", "stage": "pointers"}, sort_keys=True))
    source_bytes = _required_production_bytes(source, binary_path, "source numeric model")
    if target.get_bytes(binary_path) != source_bytes:
        raise RuntimeError("The hosted numeric model failed exact reconciliation.")
    print(json.dumps({"status": "stage-completed", "stage": "numeric-model"}, sort_keys=True))

    cached_count = 0
    for prefix in (
        "analysis/private/recommendation-player-state/",
        "analysis/recommendations/players/",
    ):
        source_objects = list(source.list(prefix))
        source_paths = {item.pathname for item in source_objects}
        target_paths = {item.pathname for item in target.list(prefix)}
        if target_paths != source_paths:
            raise RuntimeError("Hosted cached player artifact paths failed exact reconciliation.")
        for artifact_index, item in enumerate(source_objects):
            source_value = _required_production_json(source, item.pathname, "source cached player")
            target_value = _target_json(
                target,
                item.pathname,
                stage="player-caches",
                artifact_index=artifact_index,
            )
            if target_value is None or _exact_json_bytes(source_value) != _exact_json_bytes(target_value):
                raise RuntimeError("A hosted cached player artifact failed exact reconciliation.")
            cached_count += 1
    print(json.dumps({"status": "stage-completed", "stage": "player-caches"}, sort_keys=True))
    return {
        "jsonArtifacts": len(json_paths) + len(pointer_paths),
        "binaryArtifacts": 1,
        "cachedPlayerArtifacts": cached_count,
    }


def _assert_reconciliation_state(
    environment: Mapping[str, str],
    *,
    allow_canonical_shadow_writes: bool = False,
) -> str:
    backend = str(environment.get("PUMBILITY_DATA_BACKEND", "vercel")).strip().casefold()
    backend = backend or "vercel"
    if backend not in {"vercel", "shadow"}:
        raise RuntimeError("Production reconciliation requires Vercel-authoritative reads.")
    if _enabled(environment.get(SHADOW_STRICT_ENV)):
        raise RuntimeError("Production reconciliation requires fail-open shadow mode.")
    canonical_writes = _enabled(environment.get(CANONICAL_SNAPSHOT_WRITE_ENV))
    if canonical_writes and not (
        allow_canonical_shadow_writes and backend == "shadow"
    ):
        raise RuntimeError("Production reconciliation requires canonical snapshot writes off.")
    return backend


def main(*, allow_canonical_shadow_writes: bool = False) -> int:
    backend = _assert_reconciliation_state(
        os.environ,
        allow_canonical_shadow_writes=allow_canonical_shadow_writes,
    )
    runtime_url = os.getenv("PUMBILITY_DATABASE_URL", "").strip()
    private_key = os.getenv("BLOB_READ_WRITE_TOKEN", "").encode("utf-8")
    if not runtime_url or len(private_key) < 32:
        raise RuntimeError("Required production credentials were not injected.")
    session_url = session_url_from_runtime(runtime_url)
    source = VercelPrivateBlobStore()
    pointers, phoenix1, phoenix2 = _read_stable_boundary(source)
    print(json.dumps({"status": "stage-completed", "stage": "source-boundary"}, sort_keys=True))

    import psycopg

    with psycopg.connect(session_url, prepare_threshold=None) as connection:
        with connection.cursor() as cursor:
            _assert_schema(cursor)
            _assert_database_target(cursor)
        results = {
            mix: reconcile(
                sanitize_snapshot(source_snapshot, mix=mix),
                _database_snapshot(connection, mix),
                key=private_key,
                accepted_changes=set(),
            )
            for mix, source_snapshot in {"phoenix1": phoenix1, "phoenix2": phoenix2}.items()
        }
    unexplained = sum(int(result["unexplainedMismatchCount"]) for result in results.values())
    if unexplained:
        print(
            json.dumps(
                {
                    "status": "mismatch",
                    "stage": "relational",
                    "unexplainedMismatchCount": unexplained,
                },
                sort_keys=True,
            )
        )
        raise RuntimeError("Hosted relational reconciliation found unexplained differences.")
    print(json.dumps({"status": "stage-completed", "stage": "relational"}, sort_keys=True))
    artifact_counts = _verify_artifacts(
        source, PumbilityArtifactStore(database_url=session_url), pointers
    )
    print(json.dumps({"status": "stage-completed", "stage": "artifacts"}, sort_keys=True))
    _assert_boundary_unchanged(source, pointers, phoenix2)
    print(json.dumps({"status": "stage-completed", "stage": "boundary"}, sort_keys=True))
    print(
        json.dumps(
            {
                "status": "passed",
                "privacyScan": "passed",
                "unexplainedMismatchCount": 0,
                "mixes": {
                    mix: {
                        "exactMatches": result["exactMatches"],
                        "unexplainedMismatchCount": result["unexplainedMismatchCount"],
                    }
                    for mix, result in results.items()
                },
                "artifacts": artifact_counts,
                "productionBackend": backend,
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
            "Pumbility production reconciliation failed safely; private details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
