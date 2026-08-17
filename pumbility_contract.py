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


SCRIPT_VERSION = "6.6.0-phoenix2-50-score-minimum"
PHOENIX2_MINIMUM_ANALYSIS_SCORES = 50

PLAYER_REFRESH_FRESHNESS = timedelta(seconds=60)
RECOMMENDATION_SCHEMA_VERSION = 25
PLAYER_REFRESH_STORAGE_SCHEMA_VERSION = 3


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
    if int(payload.get("schemaVersion") or 0) != int(index.get("schemaVersion") or 0):
        return False
    if payload.get("modelGeneration") != index.get("generationKey"):
        return False
    synced = parse_utc(payload.get("playerSyncedAtUtc"))
    return bool(synced and (now or utc_now()) - synced < PLAYER_REFRESH_FRESHNESS)


def with_staleness(
    payload: Mapping[str, Any], index: Mapping[str, Any]
) -> dict[str, Any]:
    value = dict(payload)
    value["stale"] = (
        int(payload.get("schemaVersion") or 0) != int(index.get("schemaVersion") or 0)
        or payload.get("modelGeneration") != index.get("generationKey")
    )
    value["currentModelGeneratedAtUtc"] = index.get(
        "modelGeneratedAtUtc", index.get("generatedAtUtc")
    )
    return value
