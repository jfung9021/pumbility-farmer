"""Mode-specific clearing skill from unique, current-catalog successful clears."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from phoenix2_sync import sanitize_score


CLEARING_SKILL_METHOD_VERSION = 2
CLEARING_SKILL_START_RANK = 1
CLEARING_SKILL_END_RANK = 50
CLEARING_SKILL_REQUIRED_CLEAR_COUNT = 50
CLEARING_SKILL_MODES = ("Single", "Double")


def clearing_skill_method() -> dict[str, Any]:
    return {
        "methodVersion": CLEARING_SKILL_METHOD_VERSION,
        "difficultyBasis": "current-official-level",
        "ranks": [CLEARING_SKILL_START_RANK, CLEARING_SKILL_END_RANK],
        "requiredClearCount": CLEARING_SKILL_REQUIRED_CLEAR_COUNT,
    }


def _catalog(charts: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, float]]:
    catalog = {}
    for chart in charts:
        if not isinstance(chart, Mapping):
            continue
        chart_id = chart.get("id", chart.get("chartId"))
        chart_type = chart.get("type")
        if chart_id is None or chart_type not in CLEARING_SKILL_MODES:
            continue
        try:
            level = float(chart.get("level"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(level) and level > 0:
            catalog[str(chart_id)] = (str(chart_type), level)
    return catalog


def cleared_chart_ids_by_player(
    source_snapshot: Mapping[str, Any],
    target_charts: Sequence[Mapping[str, Any]],
) -> dict[str, set[str]]:
    """Validate raw membership before rating filters or snapshot consumption.

    Zero-Pumbility successful scores count. Source/current mode mismatches and
    charts without a finite current official level cannot supply clearing skill.
    """
    catalog = _catalog(target_charts)
    compatible_ids = {
        str(chart.get("id", chart.get("chartId")))
        for chart in source_snapshot.get("charts", [])
        if isinstance(chart, Mapping)
        and str(chart.get("id", chart.get("chartId"))) in catalog
        and catalog[str(chart.get("id", chart.get("chartId")))][0] == chart.get("type")
    }
    result: dict[str, set[str]] = defaultdict(set)
    for raw in source_snapshot.get("scores", []):
        if not isinstance(raw, Mapping) or str(raw.get("chartId")) not in compatible_ids:
            continue
        score = sanitize_score(raw)
        if score is not None:
            result[score["playerId"]].add(score["chartId"])
    return dict(result)


def clearing_skill_for_chart_ids(
    chart_ids: Iterable[str],
    charts: Sequence[Mapping[str, Any]],
    chart_type: str,
) -> dict[str, Any]:
    """Return public skill/count metadata for one player's one-mode clear set."""
    if chart_type not in CLEARING_SKILL_MODES:
        raise ValueError("Clearing skill requires Single or Double charts.")
    catalog = _catalog(charts)
    ordered = sorted(
        (
            (chart_id, catalog[chart_id][1])
            for chart_id in {str(value) for value in chart_ids}
            if chart_id in catalog and catalog[chart_id][0] == chart_type
        ),
        key=lambda row: (-row[1], row[0]),
    )
    rated = len(ordered) >= CLEARING_SKILL_REQUIRED_CLEAR_COUNT
    selected = ordered[CLEARING_SKILL_START_RANK - 1:CLEARING_SKILL_END_RANK] if rated else []
    return {
        "clearingRating": sum(level for _, level in selected) / len(selected) if selected else None,
        "clearingSkill": {
            **clearing_skill_method(),
            "uniqueClearCount": len(ordered),
            "selectedCount": len(selected),
            "status": "rated" if rated else "insufficient-clears",
        },
    }


def clearing_ratings_by_player_mode(
    clearers_by_chart: Mapping[str, set[str]],
    charts: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], float]:
    """Invert validated clear membership once and share the single-player rule."""
    player_ids: dict[str, set[str]] = defaultdict(set)
    for chart_id, players in clearers_by_chart.items():
        for player in players:
            player_ids[player].add(chart_id)
    result = {}
    for player, ids in player_ids.items():
        for chart_type in CLEARING_SKILL_MODES:
            rating = clearing_skill_for_chart_ids(ids, charts, chart_type)["clearingRating"]
            if rating is not None:
                result[(player, chart_type)] = rating
    return result
