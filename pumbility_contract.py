"""Lightweight artifact and version contracts shared by APIs and workers.

This module deliberately has no numeric-analysis or task-runner dependencies so
read-only API processes can resolve stored artifact locations without importing
NumPy, pandas, or Celery worker tasks.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from phoenix2_sync import parse_utc, utc_now


SCRIPT_VERSION = "6.18.0-midpoint-compression"
HYBRID_TIER_SCRIPT_VERSION = "6.19.0-hybrid-average"
PHOENIX2_MINIMUM_ANALYSIS_SCORES = 50
CLEARING_TIER_MINIMUM_SCORE_RECORDS = 100

PLAYER_REFRESH_FRESHNESS = timedelta(seconds=60)
RECOMMENDATION_SCHEMA_VERSION = 28
MODEL_ARTIFACT_SCHEMA_VERSION = 6
PLAYER_REFRESH_STORAGE_SCHEMA_VERSION = 3
COMBINED_TIER_SCHEMA_VERSION = 26
OFFICIAL_TIER_SCHEMA_VERSION = 41
CROSS_LEVEL_TIER_SCHEMA_VERSION = 42
CROSS_LEVEL_CLEARING_SCHEMA_VERSION = 43
POSTPROCESSED_TIER_SCHEMA_VERSION = 44
HYBRID_TIER_SCHEMA_VERSION = 46
HYBRID_POSTPROCESSED_TIER_SCHEMA_VERSION = 47
HYBRID_AVERAGE_TIER_SCHEMA_VERSION = 48


def cross_level_clearing_method_identity() -> dict[str, Any]:
    return {
        "version": 1, "source": "piuscores-submitted", "minimumOfficialLevel": 16,
        "skillMethod": {"version": 2, "topClearCount": 30,
                        "minimumScoreRecords": CLEARING_TIER_MINIMUM_SCORE_RECORDS,
                        "minimumUniqueClears": 30, "difficultyBasis": "current-official-level",
                        "scope": "same-mode-current-catalog", "recordDeduplication": "account-chart-across-mixes"},
        "percentile": .10, "percentileMethod": "linear",
        "calibrationMethod": "median-anchored-piecewise-linear", "calibrationVersion": 1,
        "referenceMinimumClearers": 20, "referenceMinimumCharts": 5,
        "minimumAnchors": 3, "tailPolicy": "boundary-linear", "intervalMethod": "none",
    }


def hybrid_clearing_method_identity() -> dict[str, Any]:
    return {**cross_level_clearing_method_identity(), "version": 2,
            "minimumCalibrationLevel": 20,
            "calibrationMethod": "20plus-median-anchored-piecewise-linear-with-lower-folder-fallback",
            "lowerLevelMethod": {"method": "folder-centered-q10", "difficultyDeltaScale": .70,
                                 "referenceMinimumClearers": 20, "referenceMinimumCharts": 5}}


def hybrid_tier_method_identity() -> dict[str, Any]:
    return {"version": 1, "minimumDisplayLevel": 16,
            "scoring": {"below20": "piuscores-submitted-score-profile-v2",
                        "atLeast20": "official-cross-level-v1", "officialMinimumLevel": 20,
                        "missingOfficialPolicy": "unrated"},
            "clearing": {"below20": "submitted-folder-centered-clearing-v1",
                         "atLeast20": "submitted-cross-level-clearing-v2", "minimumCalibrationLevel": 20}}


def official_tier_snapshot_path() -> str:
    """Private official-board input; deliberately separate from submitted scores."""
    return "analysis/private/official/phoenix2.json"


def official_scoring_manifest_path() -> str:
    """Private identity-retaining dual-mix Scoring evidence, separate from Clearing."""
    return "analysis/private/official/scoring/manifest.json"


def scoring_method_identity() -> dict[str, Any]:
    return {
        "version": 1,
        "population": "combined",
        "calibration": "original-residual-centering",
    }


def scoring_tier_method_identity() -> dict[str, Any]:
    """Public tier identity; player scoring skill and recommendations stay separate."""
    return {
        "version": 2,
        "population": "combined",
        "calibration": "folder-scaled-score-profile",
        "percentiles": [0.50, 0.75, 0.90],
        "profileWeights": [1, 1, 2],
        "preferredCentralWidth": 1.0,
    }


def recommendation_blob_path() -> str:
    return "analysis/recommendations/latest.json"


def recommendation_generation_key(job_id: object) -> str:
    return hashlib.sha256(str(job_id).encode("utf-8")).hexdigest()[:20]


def recommendation_shard_prefix(generation_key: object | None = None) -> str:
    base = "analysis/recommendations/generations/"
    return base if generation_key is None else f"{base}{generation_key}/shards/"


def recommendation_shard_path(generation_key: object, shard: int) -> str:
    return f"{recommendation_shard_prefix(generation_key)}{int(shard):04d}.json"


def combined_tier_blob_path() -> str:
    return "analysis/combined/latest.json"


def phoenix1_snapshot_path() -> str:
    return "analysis/private/phoenix1.json"


def recommendation_model_path(generation_key: str) -> str:
    return f"analysis/recommendations/models/{generation_key}.json"


def recommendation_score_model_path(generation_key: str) -> str:
    return f"analysis/recommendations/models/{generation_key}.npz"


def recommendation_index_path(generation_key: str) -> str:
    return f"analysis/recommendations/indexes/{generation_key}.json"


def recommendation_phoenix1_shard_path(generation_key: str, shard: int) -> str:
    return (
        f"analysis/private/recommendation-inputs/{generation_key}/phoenix1/"
        f"{int(shard):04d}.json"
    )


def recommendation_phoenix2_shard_path(generation_key: str, shard: int) -> str:
    return (
        f"analysis/private/recommendation-inputs/{generation_key}/phoenix2/"
        f"{int(shard):04d}.json"
    )


def recommendation_player_state_path(player_key: str) -> str:
    return f"analysis/private/recommendation-player-state/{player_key}.json"


def recommendation_player_path(player_key: str) -> str:
    return f"analysis/recommendations/players/{player_key}.json"


def player_refresh_job_id(player_key: str, now: datetime | None = None) -> str:
    bucket = (now or utc_now()).astimezone(timezone.utc).strftime("%Y%m%dT%H%M")
    safe_key = "".join(character for character in player_key if character.isalnum())[:32]
    if not safe_key:
        raise ValueError("A player refresh requires a valid player key.")
    return f"recommendation-{safe_key}-{bucket}"


def player_refresh_enabled(index: Mapping[str, Any]) -> bool:
    configured = os.getenv("PLAYER_RECOMMENDATION_REFRESH_ENABLED", "").strip().lower()
    return (
        configured in {"1", "true", "yes", "on"}
        and bool(index.get("refreshSupported"))
        and int(index.get("schemaVersion") or 0) >= RECOMMENDATION_SCHEMA_VERSION
        and int(index.get("storageSchemaVersion") or 0)
        >= PLAYER_REFRESH_STORAGE_SCHEMA_VERSION
    )


def find_player_metadata(
    index: Mapping[str, Any], player_key: str
) -> dict[str, Any] | None:
    return next(
        (
            dict(row)
            for row in index.get("players", [])
            if isinstance(row, Mapping) and row.get("playerKey") == player_key
        ),
        None,
    )


def cached_player_is_fresh(
    payload: Mapping[str, Any] | None,
    index: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if not player_model_is_compatible(payload, index):
        return False
    synced = parse_utc(payload.get("playerSyncedAtUtc"))
    return bool(synced and (now or utc_now()) - synced < PLAYER_REFRESH_FRESHNESS)


def player_model_is_compatible(
    payload: Mapping[str, Any], index: Mapping[str, Any]
) -> bool:
    """Compare reusable model content and the player's pinned baseline inputs.

    Publication IDs remain the compatibility boundary for legacy artifacts.
    New artifacts can survive a no-change publication, but a changed player
    baseline, removed player, or serving-model dependency must invalidate them.
    """
    if int(payload.get("schemaVersion") or 0) != int(index.get("schemaVersion") or 0):
        return False
    cached_identity = payload.get("numericalModelIdentity")
    current_identity = index.get("numericalModelIdentity")
    if cached_identity is not None or current_identity is not None:
        player = payload.get("player")
        player_key = player.get("playerKey") if isinstance(player, Mapping) else None
        metadata = find_player_metadata(index, str(player_key or ""))
        cached_inputs = payload.get("playerInputIdentity")
        return bool(
            isinstance(cached_identity, str) and cached_identity
            and cached_identity == current_identity
            and isinstance(cached_inputs, str) and cached_inputs
            and metadata is not None
            and cached_inputs == metadata.get("playerInputIdentity")
        )
    return payload.get("modelGeneration") == index.get("generationKey")


def with_staleness(
    payload: Mapping[str, Any], index: Mapping[str, Any]
) -> dict[str, Any]:
    value = dict(payload)
    value["stale"] = not player_model_is_compatible(payload, index)
    value["currentModelGeneratedAtUtc"] = index.get(
        "modelGeneratedAtUtc", index.get("generatedAtUtc")
    )
    if index.get("modelComputedAtUtc"):
        value["currentModelComputedAtUtc"] = index["modelComputedAtUtc"]
    if index.get("sourceCheckedAtUtc"):
        value["currentSourceCheckedAtUtc"] = index["sourceCheckedAtUtc"]
    return value
