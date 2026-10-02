"""Lightweight, privacy-minimized projections of retained Phoenix 2 scores."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Callable, Mapping, Protocol, Sequence

from phoenix2_pumbility import PLATE_CODES, normalize_plate
from phoenix2_sync import merge_best_scores, parse_utc
from pumbility_contract import (
    combined_tier_blob_path,
    find_player_metadata,
    recommendation_blob_path,
    recommendation_phoenix2_shard_path,
    recommendation_player_state_path,
)


TIER_SCORE_MODES = frozenset({"singles", "doubles", "coop"})
LOCAL_TIER_SCORES_SCHEMA_VERSION = 1


class TierScoresNotFoundError(ValueError):
    """The selected player or required score artifacts are unavailable."""


class JsonReader(Protocol):
    def get_json(self, pathname: str) -> dict | None: ...


def tier_chart_ids(payload: Mapping[str, Any], mode: str) -> set[str]:
    return {
        str(row["chartId"])
        for row in payload.get(mode, [])
        if isinstance(row, Mapping) and row.get("chartId")
    }


def public_tier_scores(
    rows: Sequence[Mapping[str, Any]], allowed_chart_ids: set[str]
) -> list[dict[str, Any]]:
    """Expose the exact retained score and its own plate, with no play history."""
    result: list[dict[str, Any]] = []
    for row in rows:
        chart_id = str(row.get("chartId") or "")
        score = row.get("score")
        if (
            chart_id not in allowed_chart_ids
            or bool(row.get("isBroken"))
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not float(score).is_integer()
            or not 0 <= score <= 1_000_000
        ):
            continue
        plate = normalize_plate(row.get("plate"))
        result.append({
            "chartId": chart_id,
            "score": int(score),
            "plateCode": PLATE_CODES.get(plate) if plate else None,
        })
    return sorted(result, key=lambda row: row["chartId"])


def _sync_time(*values: object) -> str | None:
    valid = [(parsed, str(value)) for value in values if (parsed := parse_utc(value))]
    return max(valid, key=lambda pair: pair[0])[1] if valid else None


def read_tier_player_scores(store: JsonReader, player_key: str, mode: str) -> dict:
    if mode not in TIER_SCORE_MODES or not player_key:
        raise ValueError("A playerKey and mode of singles, doubles, or coop are required.")
    index = store.get_json(recommendation_blob_path())
    if index is None:
        raise TierScoresNotFoundError("The player list is unavailable.")
    metadata = find_player_metadata(index, player_key)
    if metadata is None:
        raise TierScoresNotFoundError("The selected player was not found.")
    generation = str(index.get("generationKey") or "")
    shard_number = metadata.get("inputShard")
    player_id = str(metadata.get("internalPlayerId") or "")
    if not generation or not player_id or isinstance(shard_number, bool) or not isinstance(shard_number, int) or shard_number < 0:
        raise TierScoresNotFoundError("Tier scores require a current player score snapshot.")
    tier_payload = store.get_json(combined_tier_blob_path())
    if tier_payload is None:
        raise TierScoresNotFoundError("The combined tier list is unavailable.")
    shard = store.get_json(recommendation_phoenix2_shard_path(generation, shard_number))
    base = next((row for row in (shard or {}).get("players", [])
                 if isinstance(row, Mapping) and str(row.get("playerId")) == player_id), None)
    live = store.get_json(recommendation_player_state_path(player_key))
    if live is not None and str(live.get("playerId")) != player_id:
        raise RuntimeError("The selected player's score state is invalid.")
    if base is None and live is None:
        raise TierScoresNotFoundError("The selected player's scores are unavailable.")
    rows = merge_best_scores(
        [row for row in (base or {}).get("scores", []) if isinstance(row, Mapping)],
        [row for row in (live or {}).get("scores", []) if isinstance(row, Mapping)],
        player_id=player_id,
    )
    return {
        "playerKey": player_key,
        "mode": mode,
        "syncedAtUtc": _sync_time((base or {}).get("lastSyncedAtUtc"), (live or {}).get("lastSyncedAtUtc")),
        "scores": public_tier_scores(rows, tier_chart_ids(tier_payload, mode)),
    }


def build_local_tier_score_artifact(
    snapshot: Mapping[str, Any],
    tier_payload: Mapping[str, Any],
    player_key_for_id: Callable[[str], str],
) -> dict:
    allowed_ids = set().union(*(tier_chart_ids(tier_payload, mode) for mode in TIER_SCORE_MODES))
    by_player: dict[str, list[dict]] = defaultdict(list)
    for row in merge_best_scores([], [row for row in snapshot.get("scores", []) if isinstance(row, Mapping)]):
        by_player[row["playerId"]].append(row)
    players = []
    for player in snapshot.get("players", []):
        if not isinstance(player, Mapping) or not str(player.get("username") or "").strip():
            continue
        player_id = str(player.get("playerId") or player.get("userId") or "")
        if not player_id:
            continue
        players.append({
            "playerKey": player_key_for_id(player_id),
            "syncedAtUtc": _sync_time(player.get("lastSyncedAtUtc"), snapshot.get("generatedAtUtc")),
            "scores": public_tier_scores(by_player.get(player_id, []), allowed_ids),
        })
    return {"schemaVersion": LOCAL_TIER_SCORES_SCHEMA_VERSION, "players": players}
