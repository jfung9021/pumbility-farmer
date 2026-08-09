"""Daily recommendation-model artifacts and lightweight player-only refreshes."""

from __future__ import annotations

import hashlib
import math
import os
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Callable, Mapping, Protocol, Sequence

import pandas as pd

from phoenix2_pumbility import PlateProjectionModel
from phoenix2_sync import isoformat_utc, merge_best_scores, parse_utc, utc_now
from piu_recommendations import (
    BASELINE_END_RANK,
    BASELINE_START_RANK,
    DIFFICULTY_DELTA_SCALE,
    MIN_TARGET_LEVEL,
    PHOENIX2_RATING_SCORE_THRESHOLD,
    RECOMMENDATION_RATING_SCORE_COUNT,
    RECOMMENDATION_SCHEMA_VERSION,
    SCORE_PROJECTION_MODEL_NAME,
    TOP_PUMBILITY_COUNT,
    ScoreResponseModel,
    _clean_snapshot_frames,
    _prepare_phoenix1_rating_frames,
    _recommendation_chart_rows,
    build_player_recommendation,
    fit_score_response_model,
    public_player_key,
)


PLAYER_REFRESH_FRESHNESS = timedelta(seconds=60)
PLAYER_ARTIFACT_SHARD_SIZE = 10
MODEL_ARTIFACT_SCHEMA_VERSION = 3
PLAYER_STATE_SCHEMA_VERSION = 1
PLAYER_REFRESH_STORAGE_SCHEMA_VERSION = 3


class JsonStore(Protocol):
    def get_json(self, pathname: str) -> dict[str, Any] | None: ...
    def put_json(self, pathname: str, payload: Mapping[str, Any]) -> None: ...
    def get_bytes(self, pathname: str) -> bytes | None: ...
    def put_bytes(self, pathname: str, payload: bytes, *, content_type: str) -> None: ...


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


def _frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    # pandas' JSON encoder normalizes NaN/NaT to null and numpy scalars to JSON values.
    import json

    return json.loads(frame.to_json(orient="records", double_precision=10))


def _recommendation_method(
    slopes: Mapping[str, float],
    score_projection_metadata: Mapping[str, Any],
    phoenix1_cap: int,
) -> dict[str, Any]:
    return {
        "catalog": "Phoenix 2 authoritative catalog",
        "overlapRule": "best Phoenix 2 score always replaces Phoenix 1 for the same player and chart",
        "phoenix1RerateHandling": "Phoenix 1 Pumbility is shifted by its source slope times the Phoenix 2 minus Phoenix 1 level delta before ranking and normalization",
        "crossVersionNormalization": "Phoenix 1 scores rebased to Phoenix 2 levels, then version- and mode-specific Pumbility residuals converted to level units",
        "difficultyDeltaScale": DIFFICULTY_DELTA_SCALE,
        "pumbilityPerLevel": dict(slopes),
        "scoreProjectionCoverage": dict(score_projection_metadata),
        "scoreProjectionData": "matched Phoenix 1 + Phoenix 2 raw scores on the Phoenix 2 catalog, with Phoenix 2 precedence for overlapping player/chart rows",
        "baselineRanks": [BASELINE_START_RANK, BASELINE_END_RANK],
        "recommendationRatingRanks": [1, RECOMMENDATION_RATING_SCORE_COUNT],
        "phoenix2RatingScoreThreshold": PHOENIX2_RATING_SCORE_THRESHOLD,
        "ratingSource": "per mode, use Phoenix 2 at 10 valid scores; otherwise use Phoenix 1 when available, then available Phoenix 2 history",
        "shortHistoryBaseline": "within the selected rating source, use all available scores when fewer than 10 qualifying scores are available",
        "candidateUpperRadius": 0.0,
        "candidateLowerBound": None,
        "topPumbilityCount": TOP_PUMBILITY_COUNT,
        "projection": "projected raw score converted with the official Phoenix 2 grade-and-plate Pumbility formula",
        "plateProjection": "hierarchical player, mode, and Phoenix 2 letter-grade distribution using Phoenix 2 observations plus a held-out-tuned capped Phoenix 1 prior and population smoothing",
        "phoenix1PlatePriorCap": phoenix1_cap,
        "projectedGain": "probability-weighted change to the Phoenix 2 top-50 total; each plate outcome replaces the current chart PB and the number-50 chart only when it improves the retained top 50",
        "projectedGainTieBreak": "equal displayed projected gains are ordered by estimated difficulty from easiest to hardest, then expected Pumbility and chart name",
        "skillRatingCatalog": "all valid charts retained by the Phoenix 2 catalog, including levels below the display minimum",
        "currentStateSource": "Phoenix 2 only for played status, existing Pumbility, current top 50, and projected gain",
        "displayMinimumOfficialLevel": MIN_TARGET_LEVEL,
        "scoreProjection": "skill-distance-weighted median (50th-percentile) raw score from at least five other players of similar rating whose result on the exact chart ranked in their mode's top 100; the rating window expands from plus or minus 0.25 to 0.50 before falling back to the player-balanced population response surface",
        "scoreProjectionModel": SCORE_PROJECTION_MODEL_NAME,
    }


def _mode_counts(
    scores: pd.DataFrame, catalog: pd.DataFrame
) -> dict[str, dict[str, int]]:
    if scores.empty:
        return {}
    chart_types = dict(zip(catalog["chartId"].astype(str), catalog["type"]))
    typed = scores[["playerId", "chartId"]].copy()
    typed["type"] = typed["chartId"].map(chart_types)
    result: dict[str, dict[str, int]] = defaultdict(dict)
    for (player_id, chart_type), group in typed.groupby(
        ["playerId", "type"], sort=False
    ):
        mode = "singles" if chart_type == "Single" else "doubles"
        result[str(player_id)][mode] = int(len(group))
    return dict(result)


def build_recommendation_model_artifacts(
    phoenix1_snapshot: Mapping[str, Any],
    phoenix2_snapshot: Mapping[str, Any],
    *,
    combined_charts: Sequence[Mapping[str, Any]],
    phoenix2_slopes: Mapping[str, float],
    generation_key: str,
    generated_at_utc: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    bytes,
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Build a global model, compact index, and per-player input shards."""
    charts_for_players = _recommendation_chart_rows(combined_charts)
    score_model, score_metadata = fit_score_response_model(
        phoenix1_snapshot, phoenix2_snapshot, charts_for_players
    )
    phoenix2_catalog, phoenix2_scores = _clean_snapshot_frames(phoenix2_snapshot)
    _, phoenix1_scores = _prepare_phoenix1_rating_frames(
        phoenix1_snapshot, phoenix2_catalog
    )
    plate_model = PlateProjectionModel(phoenix1_snapshot, phoenix2_snapshot)
    slopes = dict(phoenix2_slopes)
    method = _recommendation_method(slopes, score_metadata, plate_model.phoenix1_cap)

    catalog_fields = [
        column
        for column in (
            "chartId",
            "songName",
            "type",
            "level",
            "difficulty",
            "imageUrl",
            "noteCount",
            "stepArtist",
            "bpmMin",
            "bpmMax",
        )
        if column in phoenix2_catalog.columns
    ]
    model = {
        "artifactSchemaVersion": MODEL_ARTIFACT_SCHEMA_VERSION,
        "recommendationSchemaVersion": RECOMMENDATION_SCHEMA_VERSION,
        "generationKey": generation_key,
        "generatedAtUtc": generated_at_utc,
        "catalog": _frame_records(phoenix2_catalog[catalog_fields]),
        "recommendationCharts": [dict(row) for row in charts_for_players],
        "phoenix2Slopes": slopes,
        "scoreResponseModelPath": recommendation_score_model_path(generation_key),
        "scoreProjectionMetadata": score_metadata,
        "plateModel": plate_model.global_payload(),
        "method": method,
    }

    p1_counts = _mode_counts(phoenix1_scores, phoenix2_catalog)
    p2_counts = _mode_counts(phoenix2_scores, phoenix2_catalog)
    p1_by_player = {
        str(player_id): _frame_records(group)
        for player_id, group in phoenix1_scores.groupby("playerId", sort=False)
    }
    p2_by_player = {
        str(player_id): [dict(row) for row in group]
        for player_id, group in _raw_scores_by_player(
            phoenix2_snapshot, set(phoenix2_catalog["chartId"].astype(str))
        ).items()
    }
    p1_plate_by_player = _raw_scores_by_player(
        phoenix1_snapshot,
        set(phoenix2_catalog["chartId"].astype(str)),
        compact_plate=True,
    )
    p1_rating_keys = {
        (str(row.playerId), str(row.chartId))
        for row in phoenix1_scores[["playerId", "chartId"]].itertuples(index=False)
    }
    p1_plate_by_player = {
        player_id: [
            row
            for row in rows
            if (player_id, str(row.get("chartId") or "")) not in p1_rating_keys
        ]
        for player_id, rows in p1_plate_by_player.items()
    }
    metadata_by_player = {
        str(row.get("playerId") or row.get("userId")): row
        for row in phoenix2_snapshot.get("players", [])
        if isinstance(row, Mapping)
        and str(row.get("playerId") or row.get("userId") or "").strip()
    }
    named = [
        (player_id, row)
        for player_id, row in metadata_by_player.items()
        if str(row.get("username") or "").strip()
    ]
    named.sort(
        key=lambda item: (
            str(item[1].get("username") or "").strip().casefold(),
            public_player_key(item[0]),
        )
    )
    username_counts = Counter(
        str(row.get("username") or "").strip().casefold() for _, row in named
    )

    index_players: list[dict[str, Any]] = []
    p1_shards: list[dict[str, Any]] = []
    p2_shards: list[dict[str, Any]] = []
    for shard_number, offset in enumerate(
        range(0, len(named), PLAYER_ARTIFACT_SHARD_SIZE)
    ):
        p1_players: list[dict[str, Any]] = []
        p2_players: list[dict[str, Any]] = []
        for player_id, row in named[offset : offset + PLAYER_ARTIFACT_SHARD_SIZE]:
            username = str(row.get("username") or "").strip()
            player_key = public_player_key(player_id)
            suffix = player_key[-4:]
            display_name = (
                f"{username} · {suffix}"
                if username_counts[username.casefold()] > 1
                else username
            )
            eligibility = {
                mode: bool(
                    p1_counts.get(player_id, {}).get(mode, 0)
                    or p2_counts.get(player_id, {}).get(mode, 0)
                )
                for mode in ("singles", "doubles")
            }
            index_players.append(
                {
                    "playerKey": player_key,
                    "internalPlayerId": player_id,
                    "username": username,
                    "displayName": display_name,
                    "eligibility": eligibility,
                    "inputShard": shard_number,
                }
            )
            p1_players.append(
                {
                    "playerId": player_id,
                    "scores": p1_by_player.get(player_id, []),
                    "plateScores": p1_plate_by_player.get(player_id, []),
                }
            )
            p2_players.append(
                {
                    "playerId": player_id,
                    "username": username,
                    "lastSyncedAtUtc": str(row.get("lastSyncedAtUtc") or generated_at_utc),
                    "lastScoreRecordedAtUtc": row.get("lastScoreRecordedAtUtc"),
                    "scores": p2_by_player.get(player_id, []),
                }
            )
        p1_shards.append(
            {
                "schemaVersion": PLAYER_STATE_SCHEMA_VERSION,
                "generationKey": generation_key,
                "players": p1_players,
            }
        )
        p2_shards.append(
            {
                "schemaVersion": PLAYER_STATE_SCHEMA_VERSION,
                "generationKey": generation_key,
                "players": p2_players,
            }
        )

    index = {
        "schemaVersion": RECOMMENDATION_SCHEMA_VERSION,
        "storageSchemaVersion": PLAYER_REFRESH_STORAGE_SCHEMA_VERSION,
        "generationKey": generation_key,
        "modelGeneratedAtUtc": generated_at_utc,
        "generatedAtUtc": generated_at_utc,
        "modelPath": recommendation_model_path(generation_key),
        "refreshSupported": True,
        "method": method,
        "players": index_players,
        "inputShardCount": len(p1_shards),
        "inputShardSize": PLAYER_ARTIFACT_SHARD_SIZE,
    }
    return index, model, score_model.to_npz_bytes(), p1_shards, p2_shards


def _raw_scores_by_player(
    snapshot: Mapping[str, Any],
    valid_chart_ids: set[str],
    *,
    compact_plate: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Retain all valid best-score rows, including zero-Pumbility plate history."""
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in snapshot.get("scores", []):
        if not isinstance(row, Mapping):
            continue
        player_id = str(row.get("playerId") or "").strip()
        chart_id = str(row.get("chartId") or "").strip()
        if not player_id or chart_id not in valid_chart_ids or bool(row.get("isBroken")):
            continue
        if compact_plate:
            result[player_id].append(
                {
                    "playerId": player_id,
                    "chartId": chart_id,
                    "score": row.get("score"),
                    "plate": row.get("plate"),
                    "isBroken": False,
                }
            )
        else:
            result[player_id].append(dict(row))
    return dict(result)


def publish_recommendation_model_artifacts(
    store: JsonStore,
    *,
    index: Mapping[str, Any],
    model: Mapping[str, Any],
    score_model_bytes: bytes,
    phoenix1_shards: Sequence[Mapping[str, Any]],
    phoenix2_shards: Sequence[Mapping[str, Any]],
    index_path: str,
    publish_index: bool = True,
) -> None:
    generation_key = str(index.get("generationKey") or "")
    if not generation_key:
        raise ValueError("A recommendation model generation key is required.")
    writes: list[tuple[Callable[..., None], tuple[Any, ...], dict[str, Any]]] = [
        (store.put_json, (recommendation_model_path(generation_key), model), {}),
        (
            store.put_bytes,
            (recommendation_score_model_path(generation_key), score_model_bytes),
            {"content_type": "application/x-npz"},
        ),
    ]
    writes.extend(
        (store.put_json, (recommendation_phoenix1_shard_path(generation_key, shard), payload), {})
        for shard, payload in enumerate(phoenix1_shards)
    )
    writes.extend(
        (store.put_json, (recommendation_phoenix2_shard_path(generation_key, shard), payload), {})
        for shard, payload in enumerate(phoenix2_shards)
    )
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(function, *args, **kwargs) for function, args, kwargs in writes]
        for future in futures:
            future.result()
    # A versioned pointer makes every generation directly recoverable.
    store.put_json(recommendation_index_path(generation_key), index)
    if publish_index:
        # The index is the generation pointer and must be replaced last.
        store.put_json(index_path, index)


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


def _find_shard_player(
    shard: Mapping[str, Any] | None, player_id: str
) -> dict[str, Any] | None:
    if shard is None:
        return None
    return next(
        (
            dict(row)
            for row in shard.get("players", [])
            if isinstance(row, Mapping) and str(row.get("playerId")) == player_id
        ),
        None,
    )


def _merged_player_state(
    base: Mapping[str, Any], live: Mapping[str, Any] | None
) -> dict[str, Any]:
    if not isinstance(live, Mapping):
        return dict(base)
    base_time = parse_utc(base.get("lastSyncedAtUtc"))
    live_time = parse_utc(live.get("lastSyncedAtUtc"))
    newer = live if live_time is not None and (base_time is None or live_time >= base_time) else base
    result = dict(newer)
    player_id = str(base.get("playerId") or live.get("playerId") or "")
    result["scores"] = merge_best_scores(
        [row for row in base.get("scores", []) if isinstance(row, Mapping)],
        [row for row in live.get("scores", []) if isinstance(row, Mapping)],
        player_id=player_id or None,
    )
    return result


_MODEL_CACHE_LOCK = threading.RLock()
_MODEL_CACHE: dict[str, tuple[dict[str, Any], ScoreResponseModel]] = {}


def _load_model(
    store: JsonStore, generation: str
) -> tuple[dict[str, Any], ScoreResponseModel]:
    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(generation)
        if cached is not None:
            return cached
    model = store.get_json(recommendation_model_path(generation))
    score_bytes = store.get_bytes(recommendation_score_model_path(generation))
    if model is None or score_bytes is None:
        raise RuntimeError("The current recommendation model artifacts are incomplete.")
    restored = ScoreResponseModel.from_npz_bytes(score_bytes)
    value = (model, restored)
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE[generation] = value
        while len(_MODEL_CACHE) > 2:
            del _MODEL_CACHE[next(iter(_MODEL_CACHE))]
    return value


def _prepared_frames(
    catalog_records: Sequence[Mapping[str, Any]],
    score_rows: Sequence[Mapping[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    charts = [
        {
            **{key: value for key, value in row.items() if key != "chartId"},
            "id": str(row.get("chartId") or row.get("id") or ""),
        }
        for row in catalog_records
    ]
    scores = list(score_rows)
    if not scores:
        scores = [
            {
                "playerId": "__empty__",
                "chartId": "__empty__",
                "pumbility": -1,
                "score": None,
                "recordedAt": "",
                "isBroken": False,
            }
        ]
    return _clean_snapshot_frames({"charts": charts, "scores": scores})


def player_recommendation_response(
    *,
    metadata: Mapping[str, Any],
    model: Mapping[str, Any],
    score_model: ScoreResponseModel,
    phoenix1_scores: Sequence[Mapping[str, Any]],
    phoenix1_plate_scores: Sequence[Mapping[str, Any]],
    phoenix2_state: Mapping[str, Any],
    generated_at_utc: str,
) -> dict[str, Any]:
    player_id = str(metadata["internalPlayerId"])
    catalog_rows = [
        row for row in model.get("catalog", []) if isinstance(row, Mapping)
    ]
    catalog, p2_scores = _prepared_frames(
        catalog_rows,
        [row for row in phoenix2_state.get("scores", []) if isinstance(row, Mapping)],
    )
    _, p1_scores = _prepared_frames(catalog_rows, phoenix1_scores)
    catalog_types = dict(zip(catalog["chartId"].astype(str), catalog["type"].astype(str)))
    p1_snapshot = {
        "scores": [
            *[dict(row) for row in phoenix1_scores],
            *[dict(row) for row in phoenix1_plate_scores],
        ]
    }
    p2_snapshot = {
        "scores": [
            dict(row)
            for row in phoenix2_state.get("scores", [])
            if isinstance(row, Mapping)
        ]
    }
    plate_model = PlateProjectionModel.from_global_payload(
        model.get("plateModel", {}),
        p1_snapshot,
        p2_snapshot,
        catalog_types,
    )
    recommendation = build_player_recommendation(
        player_id,
        {},
        model.get("recommendationCharts", []),
        model.get("phoenix2Slopes", {}),
        score_model,
        prepared_phoenix2=(catalog, p2_scores),
        prepared_phoenix1=(catalog, p1_scores),
        plate_model=plate_model,
        include_candidates=False,
    )
    recommendation.update(
        {
            "username": metadata.get("username"),
            "displayName": metadata.get("displayName"),
        }
    )
    model_generated = str(model.get("generatedAtUtc") or "")
    player_synced = str(phoenix2_state.get("lastSyncedAtUtc") or generated_at_utc)
    return {
        "schemaVersion": RECOMMENDATION_SCHEMA_VERSION,
        "generatedAtUtc": generated_at_utc,
        "recommendationsGeneratedAtUtc": generated_at_utc,
        "modelGeneratedAtUtc": model_generated,
        "playerSyncedAtUtc": player_synced,
        "modelGeneration": model.get("generationKey"),
        "stale": False,
        "method": model.get("method", {}),
        "player": recommendation,
    }


def refresh_player_recommendations(
    store: JsonStore,
    client: Any,
    *,
    index_path: str,
    player_key: str,
    now: Callable[[], datetime] = utc_now,
    timings: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Synchronize one player's scores and publish a compact recommendation."""
    started = now()
    total_started = perf_counter()
    model_started = perf_counter()
    index = store.get_json(index_path)
    if index is None:
        raise ValueError("The daily recommendation model is not available yet.")
    metadata = find_player_metadata(index, player_key)
    if metadata is None:
        raise ValueError("The selected recommendation player was not found.")
    generation = str(index.get("generationKey") or "")
    shard_number = int(metadata.get("inputShard"))
    model, score_model = _load_model(store, generation)
    p1_shard = store.get_json(
        recommendation_phoenix1_shard_path(generation, shard_number)
    )
    p2_shard = store.get_json(
        recommendation_phoenix2_shard_path(generation, shard_number)
    )
    if p1_shard is None or p2_shard is None:
        raise RuntimeError("The current recommendation model artifacts are incomplete.")
    if timings is not None:
        timings["modelLoadMs"] = round((perf_counter() - model_started) * 1000, 3)
    player_id = str(metadata["internalPlayerId"])
    p1_player = _find_shard_player(p1_shard, player_id) or {
        "playerId": player_id,
        "scores": [],
    }
    base_state = _find_shard_player(p2_shard, player_id)
    if base_state is None:
        raise RuntimeError("The selected player's daily score state is unavailable.")
    live_state = store.get_json(recommendation_player_state_path(player_key))
    state = _merged_player_state(base_state, live_state)
    params: dict[str, Any] = {"mix": "Phoenix2", "limit": 100}
    recorded_after = str(state.get("lastSyncedAtUtc") or "").strip()
    if recorded_after:
        params["recordedAfter"] = recorded_after
    fetch_started = perf_counter()
    incoming = client.fetch_page_collection(
        f"api/v2/players/{player_id}/scores", params
    )
    if timings is not None:
        timings["upstreamFetchMs"] = round((perf_counter() - fetch_started) * 1000, 3)

    # If the daily generation changed during the network request, switch all
    # selected-player inputs together once before merging and publishing.
    latest_index = store.get_json(index_path) or index
    if latest_index.get("generationKey") != generation:
        latest_metadata = find_player_metadata(latest_index, player_key)
        if latest_metadata is not None:
            metadata = latest_metadata
            generation = str(latest_index.get("generationKey") or "")
            latest_shard = int(metadata.get("inputShard"))
            latest_model, latest_score_model = _load_model(store, generation)
            latest_p1 = store.get_json(
                recommendation_phoenix1_shard_path(generation, latest_shard)
            )
            latest_p2 = store.get_json(
                recommendation_phoenix2_shard_path(generation, latest_shard)
            )
            latest_base = _find_shard_player(latest_p2, player_id)
            if latest_p1 is None or latest_base is None:
                raise RuntimeError(
                    "The current recommendation model artifacts are incomplete."
                )
            model = latest_model
            score_model = latest_score_model
            p1_player = _find_shard_player(latest_p1, player_id) or p1_player
            state = _merged_player_state(latest_base, state)

    valid_ids = {
        str(row.get("chartId") or row.get("id"))
        for row in model.get("catalog", [])
        if isinstance(row, Mapping)
    }
    merge_started = perf_counter()
    merged = merge_best_scores(
        [row for row in state.get("scores", []) if isinstance(row, Mapping)],
        [
            row
            for row in incoming
            if isinstance(row, Mapping) and str(row.get("chartId") or "") in valid_ids
        ],
        player_id=player_id,
    )
    boundary = isoformat_utc(started)
    refreshed_state = {
        "schemaVersion": PLAYER_STATE_SCHEMA_VERSION,
        "playerId": player_id,
        "username": metadata.get("username"),
        "lastSyncedAtUtc": boundary,
        "scores": merged,
    }
    if timings is not None:
        timings["mergeMs"] = round((perf_counter() - merge_started) * 1000, 3)
    store.put_json(recommendation_player_state_path(player_key), refreshed_state)

    compute_started = perf_counter()
    response = player_recommendation_response(
        metadata=metadata,
        model=model,
        score_model=score_model,
        phoenix1_scores=[
            row for row in p1_player.get("scores", []) if isinstance(row, Mapping)
        ],
        phoenix1_plate_scores=[
            row
            for row in p1_player.get("plateScores", [])
            if isinstance(row, Mapping)
        ],
        phoenix2_state=refreshed_state,
        generated_at_utc=isoformat_utc(now()),
    )
    if timings is not None:
        timings["computeMs"] = round((perf_counter() - compute_started) * 1000, 3)
    publish_started = perf_counter()
    store.put_json(recommendation_player_path(player_key), response)
    if timings is not None:
        timings["publishMs"] = round((perf_counter() - publish_started) * 1000, 3)
        timings["totalMs"] = round((perf_counter() - total_started) * 1000, 3)
    return response


def cached_player_is_fresh(
    payload: Mapping[str, Any] | None,
    index: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if payload.get("modelGeneration") != index.get("generationKey"):
        return False
    synced = parse_utc(payload.get("playerSyncedAtUtc"))
    return bool(synced and (now or utc_now()) - synced < PLAYER_REFRESH_FRESHNESS)


def with_staleness(
    payload: Mapping[str, Any], index: Mapping[str, Any]
) -> dict[str, Any]:
    value = dict(payload)
    value["stale"] = payload.get("modelGeneration") != index.get("generationKey")
    value["currentModelGeneratedAtUtc"] = index.get(
        "modelGeneratedAtUtc", index.get("generatedAtUtc")
    )
    return value
