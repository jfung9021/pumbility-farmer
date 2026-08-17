"""Prove hosted parity, then populate typed Supabase shadow rows without cutover.

Run only through ``vercel env run -e production``. The command keeps Vercel
authoritative, never updates a compatibility publication pointer, and requires
an explicit process-only confirmation before writing typed shadow metadata.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import io
import json
import os
import re
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis_runtime import VercelPrivateBlobStore  # noqa: E402
from phoenix2_sync import parse_utc, sanitize_snapshot  # noqa: E402
from piu_recommendations import (  # noqa: E402
    COMBINED_TIER_SCHEMA_VERSION,
    ScoreResponseModel,
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
MAX_RECOMMENDATION_PLAYER_DRIFT = 10
MAX_VERSIONED_INDEX_TIMESTAMP_DRIFT = timedelta(hours=24)
_PUBLIC_COMBINED_PARITY_FIELDS = (
    "schemaVersion",
    "generatedAtUtc",
    "mix",
    "summary",
    "singles",
    "doubles",
    "coop",
    "relativeGroups",
    "effectBands",
)
_PUBLIC_RECOMMENDATION_INDEX_FIELDS = (
    "schemaVersion",
    "storageSchemaVersion",
    "generationKey",
    "modelGeneratedAtUtc",
    "generatedAtUtc",
    "modelPath",
    "refreshSupported",
    "method",
    "players",
    "inputShardCount",
    "inputShardSize",
)
_PUBLIC_SUMMARY_FIELDS = (
    "scriptVersion",
    "generatedAtUtc",
    "mix",
    "method",
    "coverage",
    "modes",
)
_PUBLIC_RECOMMENDATION_METHOD_FIELDS = (
    "catalog",
    "overlapRule",
    "phoenix1RerateHandling",
    "crossVersionNormalization",
    "difficultyDeltaScale",
    "phoenix1ScoreOverrides",
    "pumbilityPerLevel",
    "scoreProjectionCoverage",
    "scoreProjectionData",
    "baselineRanks",
    "recommendationRatingRanks",
    "projectionRatingRanks",
    "phoenix1RatingRanks",
    "phoenix2RatingRanks",
    "phoenix2RatingScoreThreshold",
    "projectionRatingScoreThreshold",
    "ratingReference",
    "ratingReferenceGrade",
    "ratingReferencePlate",
    "ratingReferenceMultiplier",
    "ratingSource",
    "projectionRatingSource",
    "shortHistoryBaseline",
    "candidateUpperRadius",
    "candidateLowerBound",
    "candidateOfficialLevelWindow",
    "topPumbilityCount",
    "overallPumbility",
    "overallRecommendations",
    "actualPumbilitySource",
    "projection",
    "plateProjection",
    "plateProjectionStatistic",
    "pumbilityProjectionStatistic",
    "phoenix1PlatePriorCap",
    "projectedGain",
    "projectedGainTieBreak",
    "skillRatingCatalog",
    "currentStateSource",
    "displayMinimumOfficialLevel",
    "scoreProjection",
    "scoreProjectionModel",
    "coopScoreProjectionModel",
    "coopScoreProjection",
    "coopRating",
)
_PUBLIC_RECOMMENDATION_PLAYER_FIELDS = (
    "playerKey",
    "internalPlayerId",
    "username",
    "displayName",
    "eligibility",
    "scoreProgress",
    "inputShard",
)
_LIVE_RECOMMENDATION_INDEX_FIELDS = frozenset(
    {"method", "players", "inputShardCount"}
)
_LIVE_RECOMMENDATION_METHOD_FIELDS = frozenset(
    {"pumbilityPerLevel", "scoreProjectionCoverage"}
)
_LIVE_RECOMMENDATION_MODEL_FIELDS = frozenset(
    {
        "catalog",
        "recommendationCharts",
        "phoenix2Slopes",
        "scoreProjectionMetadata",
        "plateModel",
        "method",
    }
)
_PINNED_RECOMMENDATION_MODEL_FIELDS = frozenset(
    {
        "artifactSchemaVersion",
        "recommendationSchemaVersion",
        "generationKey",
        "generatedAtUtc",
        "catalog",
        "recommendationCharts",
        "phoenix2Slopes",
        "scoreResponseModelPath",
        "scoreProjectionMetadata",
        "plateModel",
        "method",
    }
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--bootstrap-samples",
        type=int,
        default=DEFAULT_BOOTSTRAP_SAMPLES,
        help="Production-equivalent analysis bootstrap count (default: 500).",
    )
    parser.add_argument(
        "--pinned-model-only",
        action="store_true",
        help="Validate and persist the pinned source model without rebuilding live-derived data.",
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


def _parity_mismatch_evidence(
    actual: Mapping[str, Any], expected: Mapping[str, Any], role: str
) -> dict[str, Any]:
    recommendation_index = role in {
        "recommendation-index",
        "versioned recommendation-index",
    }
    parity_fields = (
        _PUBLIC_RECOMMENDATION_INDEX_FIELDS
        if recommendation_index
        else _PUBLIC_COMBINED_PARITY_FIELDS
    )
    mismatched_fields = [
        field
        for field in parity_fields
        if _exact_json_bytes({field: actual.get(field)})
        != _exact_json_bytes({field: expected.get(field)})
    ]
    evidence: dict[str, Any] = {
        "parityRole": role,
        "mismatchedFields": mismatched_fields,
    }
    summary_actual = actual.get("summary")
    summary_expected = expected.get("summary")
    if not recommendation_index and isinstance(summary_actual, Mapping) and isinstance(
        summary_expected, Mapping
    ):
        evidence["mismatchedSummaryFields"] = [
            field
            for field in _PUBLIC_SUMMARY_FIELDS
            if _exact_json_bytes({field: summary_actual.get(field)})
            != _exact_json_bytes({field: summary_expected.get(field)})
        ]
    list_evidence: dict[str, dict[str, int]] = {}
    list_fields = (
        ("players",)
        if recommendation_index
        else ("singles", "doubles", "coop", "relativeGroups", "effectBands")
    )
    for field in list_fields:
        actual_items = actual.get(field)
        expected_items = expected.get(field)
        if not isinstance(actual_items, list) or not isinstance(expected_items, list):
            continue
        differing_items = abs(len(actual_items) - len(expected_items)) + sum(
            _exact_json_bytes({"item": left}) != _exact_json_bytes({"item": right})
            for left, right in zip(actual_items, expected_items)
        )
        list_evidence[field] = {
            "actualCount": len(actual_items),
            "expectedCount": len(expected_items),
            "differingItems": differing_items,
        }
        if field == "players" and all(
            isinstance(item, Mapping) for item in actual_items + expected_items
        ):
            paired_players = list(zip(actual_items, expected_items))
            field_difference_counts = {
                player_field: sum(
                    _exact_json_bytes({player_field: left.get(player_field)})
                    != _exact_json_bytes({player_field: right.get(player_field)})
                    for left, right in paired_players
                )
                for player_field in _PUBLIC_RECOMMENDATION_PLAYER_FIELDS
            }
            field_difference_counts = {
                field_name: count
                for field_name, count in field_difference_counts.items()
                if count
            }
            evidence["mismatchedPlayerFields"] = list(field_difference_counts)
            evidence["playerFieldDifferenceCounts"] = field_difference_counts
            actual_keys = {
                str(item.get("playerKey"))
                for item in actual_items
                if item.get("playerKey") is not None
            }
            expected_keys = {
                str(item.get("playerKey"))
                for item in expected_items
                if item.get("playerKey") is not None
            }
            list_evidence[field]["playerKeySetDifferenceCount"] = len(
                actual_keys.symmetric_difference(expected_keys)
            )
            list_evidence[field]["playerOrderDifferenceCount"] = sum(
                left.get("playerKey") != right.get("playerKey")
                for left, right in paired_players
            )
            list_evidence[field]["fieldKeySymmetricDifferenceCount"] = sum(
                len(set(left).symmetric_difference(right))
                for left, right in paired_players
            )
    evidence["lists"] = list_evidence
    if recommendation_index:
        method_actual = actual.get("method")
        method_expected = expected.get("method")
        if isinstance(method_actual, Mapping) and isinstance(method_expected, Mapping):
            evidence["mismatchedMethodFields"] = [
                field
                for field in _PUBLIC_RECOMMENDATION_METHOD_FIELDS
                if _exact_json_bytes({field: method_actual.get(field)})
                != _exact_json_bytes({field: method_expected.get(field)})
            ]
            evidence["method"] = {
                "actualFieldCount": len(method_actual),
                "expectedFieldCount": len(method_expected),
                "fieldKeySymmetricDifferenceCount": len(
                    set(method_actual).symmetric_difference(method_expected)
                ),
            }
        evidence["topLevelKeySymmetricDifferenceCount"] = len(
            set(actual).symmetric_difference(expected)
        )
    return evidence


def _assert_json_equal(actual: Mapping[str, Any], expected: Mapping[str, Any], role: str) -> None:
    if _exact_json_bytes(actual) != _exact_json_bytes(expected):
        error = RuntimeError(f"Hosted {role} parity failed.")
        error.safe_evidence = _parity_mismatch_evidence(actual, expected, role)
        raise error


def _mapping_mismatches(
    actual: Mapping[str, Any], expected: Mapping[str, Any]
) -> set[str]:
    return {
        field
        for field in set(actual) | set(expected)
        if _exact_json_bytes({field: actual.get(field)})
        != _exact_json_bytes({field: expected.get(field)})
    }


def _recommendation_player_keys(index: Mapping[str, Any], role: str) -> list[str]:
    players = index.get("players")
    if not isinstance(players, list) or not all(isinstance(row, Mapping) for row in players):
        raise RuntimeError(f"The {role} recommendation player index is invalid.")
    keys = [str(row.get("playerKey") or "") for row in players]
    if any(not key for key in keys) or len(set(keys)) != len(keys):
        raise RuntimeError(f"The {role} recommendation player keys are invalid.")
    expected_fields = set(players[0]) if players else set()
    if any(set(row) != expected_fields for row in players):
        raise RuntimeError(f"The {role} recommendation player shape is inconsistent.")
    shard_count = int(index.get("inputShardCount") or 0)
    shard_size = int(index.get("inputShardSize") or 0)
    if shard_count < 1 or shard_size < 1:
        raise RuntimeError(f"The {role} recommendation shard boundary is invalid.")
    expected_shard_count = (len(players) + shard_size - 1) // shard_size
    if shard_count != expected_shard_count or any(
        not isinstance(row.get("inputShard"), int)
        or int(row["inputShard"]) < 0
        or int(row["inputShard"]) >= shard_count
        for row in players
    ):
        raise RuntimeError(f"The {role} recommendation shard assignment is invalid.")
    return keys


def _assert_recommendation_live_drift(
    candidate: Mapping[str, Any], active: Mapping[str, Any]
) -> dict[str, int]:
    if set(candidate) != set(active):
        raise RuntimeError("The active recommendation index shape changed.")
    unexpected_index_fields = _mapping_mismatches(candidate, active).difference(
        _LIVE_RECOMMENDATION_INDEX_FIELDS
    )
    if unexpected_index_fields:
        raise RuntimeError("The recommendation index has non-live-data differences.")

    candidate_method = candidate.get("method")
    active_method = active.get("method")
    if not isinstance(candidate_method, Mapping) or not isinstance(active_method, Mapping):
        raise RuntimeError("The recommendation method contract is invalid.")
    if set(candidate_method) != set(active_method):
        raise RuntimeError("The recommendation method shape changed.")
    unexpected_method_fields = _mapping_mismatches(
        candidate_method, active_method
    ).difference(_LIVE_RECOMMENDATION_METHOD_FIELDS)
    if unexpected_method_fields:
        raise RuntimeError("The recommendation method has non-live-data differences.")

    candidate_keys = _recommendation_player_keys(candidate, "candidate")
    active_keys = _recommendation_player_keys(active, "active")
    count_difference = abs(len(candidate_keys) - len(active_keys))
    key_set_difference = len(set(candidate_keys).symmetric_difference(active_keys))
    if (
        count_difference > MAX_RECOMMENDATION_PLAYER_DRIFT
        or key_set_difference > MAX_RECOMMENDATION_PLAYER_DRIFT
    ):
        raise RuntimeError("The recommendation player drift exceeds the approved bound.")
    return {
        "candidatePlayers": len(candidate_keys),
        "activePlayers": len(active_keys),
        "playerCountDifference": count_difference,
        "playerKeySetDifferenceCount": key_set_difference,
    }


def _assert_recommendation_model_live_drift(
    candidate: Mapping[str, Any], active: Mapping[str, Any]
) -> None:
    if set(candidate) != set(active):
        raise RuntimeError("The active recommendation model shape changed.")
    unexpected_fields = _mapping_mismatches(candidate, active).difference(
        _LIVE_RECOMMENDATION_MODEL_FIELDS
    )
    if unexpected_fields:
        raise RuntimeError("The recommendation model has non-live-data differences.")
    candidate_method = candidate.get("method")
    active_method = active.get("method")
    if not isinstance(candidate_method, Mapping) or not isinstance(active_method, Mapping):
        raise RuntimeError("The recommendation model method contract is invalid.")
    if set(candidate_method) != set(active_method) or _mapping_mismatches(
        candidate_method, active_method
    ).difference(_LIVE_RECOMMENDATION_METHOD_FIELDS):
        raise RuntimeError("The recommendation model method has non-live-data differences.")


def _assert_versioned_index_timestamp_variance(
    active: Mapping[str, Any], versioned: Mapping[str, Any]
) -> int:
    timestamp_fields = ("modelGeneratedAtUtc", "generatedAtUtc")
    active_without_timestamps = {
        field: value for field, value in active.items() if field not in timestamp_fields
    }
    versioned_without_timestamps = {
        field: value for field, value in versioned.items() if field not in timestamp_fields
    }
    _assert_json_equal(
        active_without_timestamps,
        versioned_without_timestamps,
        "versioned recommendation-index",
    )
    parsed_pairs = []
    for field in timestamp_fields:
        active_timestamp = parse_utc(active.get(field))
        versioned_timestamp = parse_utc(versioned.get(field))
        if active_timestamp is None or versioned_timestamp is None:
            raise RuntimeError("A recommendation index timestamp is invalid.")
        parsed_pairs.append((active_timestamp, versioned_timestamp))
    if parsed_pairs[0][0] != parsed_pairs[1][0] or parsed_pairs[0][1] != parsed_pairs[1][1]:
        raise RuntimeError("A recommendation index timestamp pair is inconsistent.")
    maximum_difference = max(
        abs(active_timestamp - versioned_timestamp)
        for active_timestamp, versioned_timestamp in parsed_pairs
    )
    if maximum_difference > MAX_VERSIONED_INDEX_TIMESTAMP_DRIFT:
        raise RuntimeError("The recommendation index timestamp variance exceeds the approved bound.")
    return int(maximum_difference.total_seconds())


def _assert_source_input_shards(
    source: VercelPrivateBlobStore,
    *,
    generation: str,
    shard_count: int,
    player_count: int,
) -> None:
    counts = {"phoenix1": 0, "phoenix2": 0}
    for shard in range(shard_count):
        payloads = {
            "phoenix1": _required_production_json(
                source,
                recommendation_phoenix1_shard_path(generation, shard),
                "Phoenix 1 recommendation input",
            ),
            "phoenix2": _required_production_json(
                source,
                recommendation_phoenix2_shard_path(generation, shard),
                "Phoenix 2 recommendation input",
            ),
        }
        for mix, payload in payloads.items():
            players = payload.get("players")
            if payload.get("generationKey") != generation or not isinstance(players, list):
                raise RuntimeError("A pinned recommendation input shard is invalid.")
            counts[mix] += len(players)
    if counts != {"phoenix1": player_count, "phoenix2": player_count}:
        raise RuntimeError("Pinned recommendation input shard counts are inconsistent.")


def _combined_payload_for_active_generation(
    payload: Mapping[str, Any], active: Mapping[str, Any]
) -> dict[str, Any]:
    active_schema = int(active.get("schemaVersion") or 0)
    result = dict(payload)
    if active_schema == COMBINED_TIER_SCHEMA_VERSION:
        return result
    raise RuntimeError(
        "The active combined-tier schema cannot represent adjacent-level What-if data."
    )


def _recommendation_index_for_active_generation(
    index: Mapping[str, Any], active: Mapping[str, Any]
) -> dict[str, Any]:
    result = dict(index)
    active_players = active.get("players")
    if not isinstance(active_players, list) or any(
        isinstance(player, Mapping) and "scoreProgress" in player
        for player in active_players
    ):
        return result
    result["players"] = [
        {key: value for key, value in dict(player).items() if key != "scoreProgress"}
        for player in result.get("players", [])
    ]
    return result


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


def _load_pinned_model_artifacts(
    source: VercelPrivateBlobStore,
    active_index: Mapping[str, Any],
) -> tuple[dict[str, Any], bytes, int]:
    generation = str(active_index.get("generationKey") or "")
    shard_count = int(active_index.get("inputShardCount") or 0)
    active_player_keys = _recommendation_player_keys(active_index, "active")
    if not generation or shard_count < 1 or shard_count > MAX_INPUT_SHARDS:
        raise RuntimeError("The active hosted recommendation model boundary is invalid.")
    source_model = _required_production_json(
        source, recommendation_model_path(generation), "model"
    )
    if set(source_model) != _PINNED_RECOMMENDATION_MODEL_FIELDS:
        raise RuntimeError("The pinned recommendation model shape is invalid.")
    if (
        source_model.get("artifactSchemaVersion") != MODEL_ARTIFACT_SCHEMA_VERSION
        or source_model.get("recommendationSchemaVersion")
        != active_index.get("schemaVersion")
        or source_model.get("generationKey") != generation
        or parse_utc(source_model.get("generatedAtUtc")) is None
        or source_model.get("scoreResponseModelPath")
        != recommendation_score_model_path(generation)
        or not isinstance(source_model.get("catalog"), list)
        or not isinstance(source_model.get("recommendationCharts"), list)
        or not isinstance(source_model.get("phoenix2Slopes"), Mapping)
        or not isinstance(source_model.get("scoreProjectionMetadata"), Mapping)
        or not isinstance(source_model.get("plateModel"), Mapping)
    ):
        raise RuntimeError("The pinned recommendation model contract is invalid.")
    source_method = source_model.get("method")
    active_method = active_index.get("method")
    if not isinstance(source_method, Mapping) or not isinstance(active_method, Mapping):
        raise RuntimeError("The pinned recommendation method contract is invalid.")
    if set(source_method) != set(active_method) or _mapping_mismatches(
        source_method, active_method
    ).difference(_LIVE_RECOMMENDATION_METHOD_FIELDS):
        raise RuntimeError("The pinned recommendation method contract is inconsistent.")
    print(json.dumps({"status": "stage-completed", "stage": "pinned-model-json"}, sort_keys=True))

    versioned = _required_production_json(
        source, recommendation_index_path(generation), "versioned recommendation index"
    )
    timestamp_variance_seconds = _assert_versioned_index_timestamp_variance(
        active_index, versioned
    )
    print(
        json.dumps(
            {
                "status": "stage-completed",
                "stage": "versioned-index-parity",
                "timestampOnlyVarianceAccepted": bool(timestamp_variance_seconds),
                "maximumTimestampVarianceSeconds": timestamp_variance_seconds,
            },
            sort_keys=True,
        )
    )
    _assert_source_input_shards(
        source,
        generation=generation,
        shard_count=shard_count,
        player_count=len(active_player_keys),
    )
    print(json.dumps({"status": "stage-completed", "stage": "pinned-model-shards"}, sort_keys=True))
    source_score_bytes = _required_production_bytes(
        source, recommendation_score_model_path(generation), "numeric recommendation model"
    )
    try:
        ScoreResponseModel.from_npz_bytes(source_score_bytes)
    except ValueError:
        raise RuntimeError("The pinned recommendation numeric model is invalid.") from None
    print(json.dumps({"status": "stage-completed", "stage": "pinned-model-numeric"}, sort_keys=True))
    return source_model, source_score_bytes, shard_count


def _verify_model(
    source: VercelPrivateBlobStore,
    inputs: Mapping[str, DatabaseInput],
    pointers: Mapping[str, Mapping[str, Any]],
    *,
    rebuild_live_model: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], bytes, int, int]:
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
    combined = _combined_payload_for_active_generation(
        combined, pointers["combinedTier"]
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
    if not rebuild_live_model:
        source_model, source_score_bytes, shard_count = _load_pinned_model_artifacts(
            source, active_index
        )
        print(
            json.dumps(
                {
                    "status": "stage-completed",
                    "stage": "model-rebuild",
                    "mode": "pinned-source-after-bounded-live-drift-evidence",
                },
                sort_keys=True,
            )
        )
        return active_index, source_model, source_score_bytes, shard_count, shard_count
    artifacts = build_recommendation_model_artifacts(
        inputs["phoenix1"].snapshot,
        inputs["phoenix2"].snapshot,
        combined_charts=combined_charts,
        phoenix2_slopes=slopes,
        generation_key=generation,
        generated_at_utc=generated_at,
    )
    print(json.dumps({"status": "stage-completed", "stage": "model-compute"}, sort_keys=True))
    candidate_index, candidate_model, candidate_score_bytes, phoenix1_shards, phoenix2_shards = (
        artifacts
    )
    candidate_index = _recommendation_index_for_active_generation(
        candidate_index, active_index
    )
    live_drift = _assert_recommendation_live_drift(candidate_index, active_index)
    print(
        json.dumps(
            {
                "status": "stage-completed",
                "stage": "model-index-live-drift",
                "acceptedLiveDrift": True,
                **live_drift,
            },
            sort_keys=True,
        )
    )

    source_model = _required_production_json(
        source, recommendation_model_path(generation), "model"
    )
    _assert_recommendation_model_live_drift(candidate_model, source_model)
    source_method = source_model.get("method")
    active_method = active_index.get("method")
    if source_model.get("generationKey") != generation or not isinstance(
        source_method, Mapping
    ) or not isinstance(active_method, Mapping):
        raise RuntimeError("The pinned recommendation model boundary is inconsistent.")
    if set(source_method) != set(active_method) or _mapping_mismatches(
        source_method, active_method
    ).difference(_LIVE_RECOMMENDATION_METHOD_FIELDS):
        raise RuntimeError("The pinned recommendation method contract is inconsistent.")
    print(json.dumps({"status": "stage-completed", "stage": "model-json-live-drift"}, sort_keys=True))
    versioned = _required_production_json(
        source, recommendation_index_path(generation), "versioned recommendation index"
    )
    timestamp_variance_seconds = _assert_versioned_index_timestamp_variance(
        active_index, versioned
    )
    print(
        json.dumps(
            {
                "status": "stage-completed",
                "stage": "versioned-index-parity",
                "timestampOnlyVarianceAccepted": bool(timestamp_variance_seconds),
                "maximumTimestampVarianceSeconds": timestamp_variance_seconds,
            },
            sort_keys=True,
        )
    )
    if (
        len(phoenix1_shards) != int(candidate_index["inputShardCount"])
        or len(phoenix2_shards) != int(candidate_index["inputShardCount"])
    ):
        raise RuntimeError("Candidate recommendation input shard counts are inconsistent.")
    _assert_source_input_shards(
        source,
        generation=generation,
        shard_count=shard_count,
        player_count=len(active_index["players"]),
    )
    print(json.dumps({"status": "stage-completed", "stage": "pinned-model-shards"}, sort_keys=True))
    source_score_bytes = _required_production_bytes(
        source, recommendation_score_model_path(generation), "numeric recommendation model"
    )
    try:
        ScoreResponseModel.from_npz_bytes(candidate_score_bytes)
        ScoreResponseModel.from_npz_bytes(source_score_bytes)
    except ValueError:
        raise RuntimeError("A recommendation numeric model is invalid.") from None
    numeric_differences = _npz_difference_summary(
        candidate_score_bytes, source_score_bytes
    )
    print(
        json.dumps(
            {
                "status": "stage-completed",
                "stage": "model-numeric-live-drift",
                "acceptedLiveDrift": bool(numeric_differences),
                "differingArrays": len(numeric_differences),
            },
            sort_keys=True,
        )
    )
    return (
        active_index,
        source_model,
        source_score_bytes,
        shard_count,
        shard_count,
    )


def _persist_model_generation(
    connection: Any,
    *,
    analysis_run_id: Any,
    inputs: Mapping[str, DatabaseInput] | None = None,
    artifacts: tuple[
        dict[str, Any], dict[str, Any], bytes, int, int
    ] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Any:
    from psycopg.types.json import Jsonb

    expected_artifacts: list[dict[str, Any]]
    source_hashes: dict[str, str] | None
    artifact_manifest_hash: str | None
    artifact_byte_size: int | None
    if metadata is not None:
        def valid_digest(value: object) -> bool:
            return bool(re.fullmatch(r"[0-9a-f]{64}", str(value or "")))

        generation = str(metadata.get("generationKey") or "").strip()
        phoenix1_shard_count = int(metadata.get("phoenix1ShardCount") or 0)
        phoenix2_shard_count = int(metadata.get("phoenix2ShardCount") or 0)
        player_count = int(metadata.get("playerCount") or 0)
        input_hash = str(metadata.get("inputSha256") or "")
        output_hash = str(metadata.get("outputSha256") or "")
        raw_source_hashes = metadata.get("sourceHashes")
        raw_manifest = metadata.get("artifactManifest")
        if (
            not generation
            or phoenix1_shard_count < 0
            or phoenix2_shard_count < 0
            or player_count < 0
            or not isinstance(raw_source_hashes, Mapping)
            or set(raw_source_hashes) != {"phoenix1", "phoenix2"}
            or not all(
                valid_digest(raw_source_hashes.get(mix))
                for mix in ("phoenix1", "phoenix2")
            )
            or not valid_digest(input_hash)
            or not valid_digest(output_hash)
            or not isinstance(raw_manifest, Mapping)
        ):
            raise ValueError("Typed model registration metadata is invalid.")
        source_hashes = {
            mix: str(raw_source_hashes[mix]) for mix in ("phoenix1", "phoenix2")
        }
        sections = raw_manifest.get("sections")
        if (
            raw_manifest.get("generationKey") != generation
            or not isinstance(sections, Mapping)
            or int(raw_manifest.get("schemaVersion") or 0) not in {0, 1}
            or raw_manifest.get("sha256") != _sha256(sections)
        ):
            raise ValueError("Typed model artifact metadata is invalid.")
        manifest_schema = int(raw_manifest.get("schemaVersion") or 0)
        expected_artifacts = []
        for name, expected_path in (
            ("index", recommendation_index_path(generation)),
            ("model", recommendation_model_path(generation)),
            ("scoreModel", recommendation_score_model_path(generation)),
        ):
            descriptor = sections.get(name)
            if (
                not isinstance(descriptor, Mapping)
                or descriptor.get("pathname") != expected_path
                or not valid_digest(descriptor.get("sha256"))
                or not isinstance(descriptor.get("byteSize"), int)
                or int(descriptor["byteSize"]) < 0
            ):
                raise ValueError("Typed model artifact metadata is invalid.")
            expected_artifacts.append(dict(descriptor))
        for name, count, path_builder in (
            (
                "phoenix1Shards",
                phoenix1_shard_count,
                recommendation_phoenix1_shard_path,
            ),
            (
                "phoenix2Shards",
                phoenix2_shard_count,
                recommendation_phoenix2_shard_path,
            ),
        ):
            group = sections.get(name)
            items = group.get("items") if isinstance(group, Mapping) else None
            if (
                not isinstance(group, Mapping)
                or int(group.get("count") or 0) != count
                or not isinstance(items, list)
                or len(items) != count
            ):
                raise ValueError("Typed model artifact metadata is invalid.")
            group_descriptors: list[dict[str, Any]] = []
            for shard, descriptor in enumerate(items):
                if (
                    not isinstance(descriptor, Mapping)
                    or descriptor.get("pathname") != path_builder(generation, shard)
                ):
                    raise ValueError("Typed model artifact metadata is invalid.")
                descriptor_value = dict(descriptor)
                if manifest_schema >= 1 and (
                    not valid_digest(descriptor_value.get("sha256"))
                    or not isinstance(descriptor_value.get("byteSize"), int)
                    or int(descriptor_value["byteSize"]) < 0
                ):
                    raise ValueError("Typed model artifact metadata is invalid.")
                group_descriptors.append(descriptor_value)
                expected_artifacts.append(descriptor_value)
            if manifest_schema >= 1 and (
                group.get("sha256") != _sha256(group_descriptors)
                or int(group.get("byteSize") or 0)
                != sum(int(item["byteSize"]) for item in group_descriptors)
            ):
                raise ValueError("Typed model artifact metadata is invalid.")
        if len(expected_artifacts) != int(raw_manifest.get("artifactCount") or 0):
            raise ValueError("Typed model artifact metadata is invalid.")
        expected_paths = [str(item["pathname"]) for item in expected_artifacts]
        if len(set(expected_paths)) != len(expected_paths):
            raise ValueError("Typed model artifact metadata is invalid.")
        known_byte_size = sum(
            int(item.get("byteSize") or 0) for item in expected_artifacts
        )
        if int(raw_manifest.get("byteSize") or 0) != known_byte_size:
            raise ValueError("Typed model artifact metadata is invalid.")
        artifact_manifest_hash = str(raw_manifest["sha256"])
        artifact_byte_size = int(raw_manifest.get("byteSize") or 0)
    else:
        if inputs is None or artifacts is None:
            raise ValueError("Model persistence requires artifacts or registration metadata.")
        (
            index,
            model,
            score_bytes,
            phoenix1_shard_count,
            phoenix2_shard_count,
        ) = artifacts
        generation = str(index["generationKey"])
        player_count = len(index.get("players", []))
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
        expected_artifacts = [{"pathname": recommendation_model_path(generation)}]
        source_hashes = None
        artifact_manifest_hash = None
        artifact_byte_size = None
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            select id, object_key, sha256, byte_size from pumbility.artifacts
            where object_key = any(%s) and validated_at is not null
            """,
            ([str(item["pathname"]) for item in expected_artifacts],),
        )
        artifact_rows = cursor.fetchall()
        artifacts_by_path = {str(row[1]): row for row in artifact_rows}
        if len(artifacts_by_path) != len(expected_artifacts):
            raise RuntimeError("The validated hosted model artifact set is unavailable.")
        for expected in expected_artifacts:
            pathname = str(expected["pathname"])
            actual = artifacts_by_path.get(pathname)
            if actual is None:
                raise RuntimeError("The validated hosted model artifact set is unavailable.")
            expected_digest = expected.get("sha256")
            expected_size = expected.get("byteSize")
            if (
                expected_digest is not None
                and str(actual[2]) != str(expected_digest)
            ) or (
                expected_size is not None and int(actual[3]) != int(expected_size)
            ):
                raise RuntimeError(
                    "The hosted model artifact metadata changed before registration."
                )
        model_artifact = artifacts_by_path.get(recommendation_model_path(generation))
        if model_artifact is None:
            raise RuntimeError("The validated hosted model artifact is unavailable.")
        artifact_id = model_artifact[0]
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
                        "playerCount": player_count,
                        "inputShardCount": phoenix1_shard_count,
                        "phoenix2InputShardCount": phoenix2_shard_count,
                        **(
                            {
                                "sourceHashes": source_hashes,
                                "artifactManifestSha256": artifact_manifest_hash,
                                "artifactCount": len(expected_artifacts),
                                "artifactByteSize": artifact_byte_size,
                            }
                            if metadata is not None
                            else {}
                        ),
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
    if args.pinned_model_only and not args.apply:
        raise ValueError("Pinned-model-only population requires --apply.")
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
    source_snapshots.clear()
    del phoenix1
    gc.collect()
    outputs = {
        mix: _analyze(inputs[mix], args.bootstrap_samples)
        for mix in ("phoenix1", "phoenix2")
    }
    print(json.dumps({"status": "stage-completed", "stage": "analysis-compute"}, sort_keys=True))
    _verify_analysis(outputs, pointers)
    print(json.dumps({"status": "stage-completed", "stage": "analysis-parity"}, sort_keys=True))
    artifacts = _verify_model(
        source,
        inputs,
        pointers,
        rebuild_live_model=not args.pinned_model_only,
    )
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
                    "inputShards": artifacts[3],
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
