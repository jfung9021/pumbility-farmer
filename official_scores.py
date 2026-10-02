"""Separate official-board evidence for the high-level Phoenix 2 tier lists.

An entry means leaderboard appearance, not a claim about a complete clear history.
No PIUScores account identifiers, names, or submitted scores enter this snapshot.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlencode
from uuid import UUID


OFFICIAL_SNAPSHOT_SCHEMA_VERSION = 1
OFFICIAL_SOURCE = "piuscores-official"
OFFICIAL_MIX = "Phoenix2"
OFFICIAL_BOARD_CAP = 300
OFFICIAL_SCORING_MINIMUM_LEVELS = {"Single": 25, "Double": 26}
OFFICIAL_CLEARING_MINIMUM_LEVELS = {"Single": 25, "Double": 26}
OFFICIAL_HISTORY_MINIMUM_LEVELS = {"Single": 22, "Double": 23}
DEFAULT_OFFICIAL_SNAPSHOT_PATH = (
    Path(__file__).resolve().parent
    / ".local-data" / "piu-scores" / "official" / "phoenix2" / "current.json"
)
_PLAYER_ID = re.compile(r"official:[1-9][0-9]*\Z")


def is_official_target(chart: Mapping[str, Any]) -> bool:
    """Select by current catalog mode and level, never estimated difficulty."""
    try:
        level = float(chart.get("level"))
    except (TypeError, ValueError):
        return False
    return math.isfinite(level) and (
        (chart.get("type") == "Single" and level >= OFFICIAL_SCORING_MINIMUM_LEVELS["Single"])
        or (chart.get("type") == "Double" and level >= OFFICIAL_SCORING_MINIMUM_LEVELS["Double"])
    )


def _chart_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Official board chartId must be a UUID string.")
    try:
        return str(UUID(value))
    except ValueError:
        raise ValueError("Official board chartId must be a UUID string.") from None


def is_official_history_chart(chart: Mapping[str, Any]) -> bool:
    try:
        level = float(chart.get("level"))
    except (TypeError, ValueError):
        return False
    minimum = OFFICIAL_HISTORY_MINIMUM_LEVELS.get(chart.get("type"))
    return minimum is not None and math.isfinite(level) and level >= minimum


def target_chart_ids(charts: Sequence[Mapping[str, Any]], *, include_history: bool = False) -> list[str]:
    return sorted({
        _chart_id(chart.get("id", chart.get("chartId")))
        for chart in charts if is_official_target(chart) or (include_history and is_official_history_chart(chart))
    })


def is_official_clearing_target(chart: Mapping[str, Any]) -> bool:
    try:
        level = float(chart.get("level"))
    except (TypeError, ValueError):
        return False
    minimum = OFFICIAL_CLEARING_MINIMUM_LEVELS.get(chart.get("type"))
    return minimum is not None and math.isfinite(level) and level >= minimum


def _timestamp(value: Any, field: str) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise ValueError(f"Official snapshot {field} must be a timezone-aware timestamp.") from None


def _integer(value: Any, field: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"Official board {field} is outside the expected integer range.")
    return value


def normalize_official_board(chart_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an API board and keep cap diagnostics before player deduplication."""
    chart_id = _chart_id(chart_id)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise ValueError("Official board must contain a data array.")
    as_of = payload.get("asOf")
    _timestamp(as_of, "asOf")
    raw_rows = payload["data"]
    entries: dict[str, dict[str, Any]] = {}
    raw_ranks: list[int] = []
    raw_scores: list[int] = []
    for row in raw_rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("player"), Mapping):
            raise ValueError("Official board entry must contain a player object.")
        player = row["player"]
        if player.get("isSupplemented") is not False:
            raise ValueError("Official board entry is supplemented or lacks source provenance.")
        player_id = f"official:{_integer(player.get('playerId'), 'playerId', 1)}"
        score = _integer(row.get("score"), "score", 0, 1_000_000)
        place = _integer(row.get("place"), "place", 1)
        raw_ranks.append(place)
        raw_scores.append(score)
        candidate = {"playerId": player_id, "score": score, "place": place}
        previous = entries.get(player_id)
        if previous is None or (score, -place) > (previous["score"], -previous["place"]):
            entries[player_id] = candidate
    raw_count = len(raw_rows)
    max_rank = max(raw_ranks, default=0)
    return {
        "chartId": chart_id,
        "asOf": as_of,
        "rawRowCount": raw_count,
        "maxRank": max_rank,
        "cutoffScore": min(raw_scores) if raw_scores else None,
        "possiblyTruncated": raw_count >= OFFICIAL_BOARD_CAP or max_rank >= OFFICIAL_BOARD_CAP,
        "entries": sorted(entries.values(), key=lambda row: (row["place"], row["playerId"])),
    }


def validate_official_snapshot(
    snapshot: Mapping[str, Any], charts: Sequence[Mapping[str, Any]] | None = None,
    *, allow_previous_history_scope: bool = False,
) -> dict[str, Any]:
    """Reject incompatible, mixed-week, incomplete, or corrupted local evidence."""
    if (
        not isinstance(snapshot, Mapping)
        or snapshot.get("schemaVersion") != OFFICIAL_SNAPSHOT_SCHEMA_VERSION
        or snapshot.get("mix") != OFFICIAL_MIX
        or snapshot.get("source") != OFFICIAL_SOURCE
        or not isinstance(snapshot.get("boards"), list)
    ):
        raise ValueError("Official snapshot schema, source, or mix is incompatible.")
    _timestamp(snapshot.get("fetchedAtUtc"), "fetchedAtUtc")
    as_of = _timestamp(snapshot.get("asOf"), "asOf")
    seen: set[str] = set()
    for board in snapshot["boards"]:
        if not isinstance(board, Mapping):
            raise ValueError("Official snapshot board is invalid.")
        chart_id = _chart_id(board.get("chartId"))
        if chart_id in seen:
            raise ValueError("Official snapshot contains duplicate chart boards.")
        seen.add(chart_id)
        if _timestamp(board.get("asOf"), "board asOf") != as_of:
            raise ValueError("Official snapshot mixes different weekly snapshots.")
        raw_count = _integer(board.get("rawRowCount"), "rawRowCount", 0)
        max_rank = _integer(board.get("maxRank"), "maxRank", 0)
        capped = raw_count >= OFFICIAL_BOARD_CAP or max_rank >= OFFICIAL_BOARD_CAP
        if type(board.get("possiblyTruncated")) is not bool or board["possiblyTruncated"] != capped:
            raise ValueError("Official board cap metadata is inconsistent.")
        entries = board.get("entries")
        if not isinstance(entries, list) or len(entries) > raw_count:
            raise ValueError("Official board entry count is inconsistent.")
        if raw_count == 0:
            if entries or max_rank != 0 or board.get("cutoffScore") is not None:
                raise ValueError("Empty official board metadata is inconsistent.")
        elif not entries or max_rank == 0:
            raise ValueError("Nonempty official board metadata is inconsistent.")
        cutoff = _integer(board.get("cutoffScore"), "cutoffScore", 0, 1_000_000) if raw_count else None
        players: set[str] = set()
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise ValueError("Official board normalized entry is invalid.")
            player_id = entry.get("playerId")
            if not isinstance(player_id, str) or not _PLAYER_ID.fullmatch(player_id) or player_id in players:
                raise ValueError("Official board identities are invalid or duplicated.")
            players.add(player_id)
            score = _integer(entry.get("score"), "score", 0, 1_000_000)
            place = _integer(entry.get("place"), "place", 1)
            if score < cutoff or place > max_rank:
                raise ValueError("Official board rank or cutoff metadata is inconsistent.")
    history_levels = snapshot.get("historyMinimumLevels")
    previous_scope = allow_previous_history_scope and history_levels == {"Single": 23, "Double": 24}
    if history_levels is not None and history_levels != OFFICIAL_HISTORY_MINIMUM_LEVELS and not previous_scope:
        raise ValueError("Official snapshot clearing-history scope is incompatible.")
    if charts is not None:
        required = set(target_chart_ids(charts, include_history=history_levels is not None))
        catalog_ids = {str(chart["id"]) for chart in charts}
        # A broader cached snapshot remains usable after narrowing tier scope.
        # Only required targets/history are consumed by the tier builder.
        if not required.issubset(seen) or not seen.issubset(catalog_ids):
            raise ValueError("Official snapshot does not match all current target catalog charts.")
    return dict(snapshot)


def load_official_snapshot(
    path: Path = DEFAULT_OFFICIAL_SNAPSHOT_PATH,
    charts: Sequence[Mapping[str, Any]] | None = None,
    *, allow_previous_history_scope: bool = False,
) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return validate_official_snapshot(json.load(handle), charts, allow_previous_history_scope=allow_previous_history_scope)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def capture_official_snapshot(
    client: Any,
    charts: Sequence[Mapping[str, Any]],
    output_path: Path = DEFAULT_OFFICIAL_SNAPSHOT_PATH,
    *,
    restart: bool = False,
    include_history: bool = False,
    extend_existing: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Atomically publish a complete official snapshot; resume only the same week.

    The first board is always read live to establish the upstream snapshot. A
    partial checkpoint with a different asOf is discarded, never merged in.
    """
    chart_ids = target_chart_ids(charts, include_history=include_history)
    if not chart_ids:
        raise ValueError("The current catalog contains no official target charts.")
    output_path = Path(output_path)
    pending_path = output_path.with_suffix(".pending.json")
    cached: dict[str, Mapping[str, Any]] = {}
    pinned_as_of = None
    if extend_existing:
        if not include_history or restart:
            raise ValueError("Extending clearing histories requires history scope and cannot restart.")
        current = load_official_snapshot(output_path, allow_previous_history_scope=True)
        cached.update({board["chartId"]: board for board in current["boards"]})
        pinned_as_of = _timestamp(current["asOf"], "asOf")
    if not restart and pending_path.exists():
        pending = load_official_snapshot(pending_path, allow_previous_history_scope=extend_existing)
        if pinned_as_of is None or _timestamp(pending["asOf"], "asOf") == pinned_as_of:
            cached.update({board["chartId"]: board for board in pending["boards"]})
    if extend_existing:
        chart_ids.sort(key=lambda cid: cid in cached)  # First live read is a newly added board.
    boards: list[dict[str, Any]] = []
    as_of: str | None = None
    snapshot: dict[str, Any] = {}
    for chart_id in chart_ids:
        old_board = cached.get(chart_id)
        if as_of is not None and old_board is not None and _timestamp(old_board["asOf"], "asOf") == _timestamp(as_of, "asOf"):
            board = dict(old_board)
        else:
            # Put filters in the URL so the existing client's retry path keeps
            # mix and supplemented=false on every attempt.
            query = urlencode({"mix": OFFICIAL_MIX, "supplemented": "false"})
            payload = client._get_json(f"api/v2/official/charts/{chart_id}/board?{query}")
            board = normalize_official_board(chart_id, payload)
        if as_of is None:
            as_of = board["asOf"]
            if pinned_as_of is not None and _timestamp(as_of, "asOf") != pinned_as_of:
                raise ValueError("The upstream week changed; cannot extend the existing official snapshot without refreshing it.")
        elif _timestamp(board["asOf"], "asOf") != _timestamp(as_of, "asOf"):
            raise ValueError("Official boards changed snapshot during capture; retry the capture.")
        boards.append(board)
        snapshot = {
            "schemaVersion": OFFICIAL_SNAPSHOT_SCHEMA_VERSION,
            "mix": OFFICIAL_MIX,
            "source": OFFICIAL_SOURCE,
            "fetchedAtUtc": now().astimezone(timezone.utc).isoformat(),
            "asOf": as_of,
            "boards": boards,
        }
        if include_history:
            snapshot["historyMinimumLevels"] = dict(OFFICIAL_HISTORY_MINIMUM_LEVELS)
        _atomic_write_json(pending_path, snapshot)
        if progress is not None:
            progress(len(boards), len(chart_ids))
    validate_official_snapshot(snapshot, charts)
    _atomic_write_json(output_path, snapshot)
    pending_path.unlink()
    return snapshot
