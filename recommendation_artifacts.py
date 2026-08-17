"""Compact storage helpers for player recommendation artifacts.

The public recommendation response keeps bounded Overall candidate and top
lists. Every Overall row is otherwise a copy of its Single or Double row, with
only ``projectedGain`` recalculated against the shared S+D top-50 pool. Cached
artifacts store ordered override references instead of duplicating chart
objects. Readers materialize the established public response shape.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping


PLAYER_CACHE_STORAGE_SCHEMA_FIELD = "playerCacheStorageSchemaVersion"
PLAYER_CACHE_STORAGE_SCHEMA_VERSION = 3
OVERALL_FILTER_REFS_FIELD = "filterCandidateRefs"
OVERALL_TOP_REFS_FIELD = "topRecommendationRefs"
RECOMMENDATION_MODE_KEYS = frozenset(
    {"overall", "singles", "doubles", "coop"}
)
OFFICIAL_DIFFICULTY_RE = re.compile(r"^[SD][1-9][0-9]?$", re.IGNORECASE)
MIN_RECOMMENDATION_LEVEL = 16
RECOMMENDATION_OFFICIAL_LEVEL_RADIUS = 2
RECOMMENDATION_DISPLAY_COUNT = 50
RECOMMENDATION_ESTIMATED_DIFFICULTY_UPPER_RADIUS = 1.0


class RecommendationArtifactQueryError(ValueError):
    """The requested public projection cannot be served from the artifact."""


def normalize_recommendation_difficulty(
    mode: str | None,
    difficulty: str | None,
) -> str | None:
    normalized = str(difficulty).strip().upper() if difficulty is not None else None
    if normalized == "":
        return None
    if normalized is not None and (
        mode != "overall" or OFFICIAL_DIFFICULTY_RE.fullmatch(normalized) is None
    ):
        raise RecommendationArtifactQueryError(
            "difficulty requires mode=overall and an S16/D17-style value."
        )
    return normalized


def _mode_mapping(player: Mapping[str, Any]) -> Mapping[str, Any]:
    modes = player.get("modes")
    if not isinstance(modes, Mapping):
        raise ValueError("A cached recommendation player has invalid modes.")
    return modes


def _standard_candidate_index(
    modes: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for mode_key in ("singles", "doubles"):
        raw_mode = modes.get(mode_key)
        if not isinstance(raw_mode, Mapping):
            continue
        candidates = raw_mode.get("filterCandidates", [])
        if not isinstance(candidates, list):
            raise ValueError("A cached recommendation candidate pool is invalid.")
        for raw_candidate in candidates:
            if not isinstance(raw_candidate, Mapping):
                raise ValueError("A cached recommendation candidate is invalid.")
            candidate = dict(raw_candidate)
            chart_id = str(candidate.get("chartId") or "").strip()
            if not chart_id or chart_id in result:
                raise ValueError(
                    "Cached recommendation candidates require unique chart IDs."
                )
            result[chart_id] = candidate
    return result


def _official_difficulty(candidate: Mapping[str, Any]) -> str | None:
    chart_type = str(candidate.get("type") or "")
    raw_level = candidate.get("level")
    if isinstance(raw_level, bool):
        return None
    try:
        level = int(raw_level)
    except (TypeError, ValueError, OverflowError):
        return None
    if level < MIN_RECOMMENDATION_LEVEL:
        return None
    prefix = "S" if chart_type == "Single" else "D" if chart_type == "Double" else None
    return f"{prefix}{level}" if prefix else None


def recommendation_official_level_range(
    scoring_rating: object,
) -> tuple[int, int] | None:
    """Return the inclusive official-level window for one visible skill rating."""
    if isinstance(scoring_rating, bool) or not isinstance(
        scoring_rating, (int, float)
    ):
        return None
    rating = float(scoring_rating)
    if not math.isfinite(rating):
        return None
    base = math.floor(rating)
    return (
        max(MIN_RECOMMENDATION_LEVEL, base - RECOMMENDATION_OFFICIAL_LEVEL_RADIUS),
        base + RECOMMENDATION_OFFICIAL_LEVEL_RADIUS,
    )


def _candidate_matches_standard_mode(
    candidate: Mapping[str, Any],
    mode_key: str,
    bounds: tuple[int, int] | None,
) -> bool:
    if bounds is None:
        return False
    expected_type = "Single" if mode_key == "singles" else "Double"
    if candidate.get("type") != expected_type:
        return False
    difficulty = _official_difficulty(candidate)
    if difficulty is None:
        return False
    level = int(difficulty[1:])
    return bounds[0] <= level <= bounds[1]


def _bounded_standard_mode(
    mode_key: str,
    raw_mode: Mapping[str, Any],
) -> dict[str, Any]:
    """Clip current and legacy candidate lists to the persisted skill window."""
    mode = dict(raw_mode)
    bounds = recommendation_official_level_range(mode.get("scoringRating"))
    bounded_lists: dict[str, list[dict[str, Any]]] = {}
    for field in ("filterCandidates", "topRecommendations"):
        raw_candidates = mode.get(field, [])
        if not isinstance(raw_candidates, list):
            raise ValueError("A cached recommendation candidate pool is invalid.")
        if not all(isinstance(candidate, Mapping) for candidate in raw_candidates):
            raise ValueError("A cached recommendation candidate is invalid.")
        candidates = [
            dict(candidate)
            for candidate in raw_candidates
            if _candidate_matches_standard_mode(candidate, mode_key, bounds)
        ]
        if field == "topRecommendations":
            candidates = candidates[:RECOMMENDATION_DISPLAY_COUNT]
        bounded_lists[field] = candidates
        mode[field] = candidates
    if "filterCandidateCount" in raw_mode:
        mode["filterCandidateCount"] = len(bounded_lists["filterCandidates"])
    rating = mode.get("scoringRating")
    maximum_estimated = (
        float(rating) + RECOMMENDATION_ESTIMATED_DIFFICULTY_UPPER_RADIUS
        if bounds is not None
        else None
    )
    if "candidateCount" in raw_mode:
        mode["candidateCount"] = sum(
            1
            for candidate in bounded_lists["filterCandidates"]
            if maximum_estimated is not None
            and isinstance(candidate.get("estimatedDifficulty"), (int, float))
            and not isinstance(candidate.get("estimatedDifficulty"), bool)
            and math.isfinite(float(candidate["estimatedDifficulty"]))
            and float(candidate["estimatedDifficulty"]) <= maximum_estimated
        )
    return mode


def _bounded_modes(raw_modes: Mapping[str, Any]) -> dict[str, Any]:
    modes = {
        str(key): dict(value) if isinstance(value, Mapping) else value
        for key, value in raw_modes.items()
    }
    for mode_key in ("singles", "doubles"):
        raw_mode = modes.get(mode_key)
        if isinstance(raw_mode, Mapping):
            modes[mode_key] = _bounded_standard_mode(mode_key, raw_mode)
    return modes


def _overall_difficulty_options(
    source_by_chart: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    values = {
        value
        for candidate in source_by_chart.values()
        if (value := _official_difficulty(candidate)) is not None
    }
    return sorted(
        values,
        key=lambda value: (
            0 if value.startswith("S") else 1,
            int(value[1:]),
        ),
    )


def compact_player_recommendation_cache(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return bounded canonical S/D objects plus compact Overall references."""
    result = dict(payload)
    raw_player = result.get("player")
    if not isinstance(raw_player, Mapping):
        raise ValueError("A player recommendation cache requires a player object.")
    player = dict(raw_player)
    raw_modes = _mode_mapping(player)
    modes = _bounded_modes(raw_modes)
    raw_overall = modes.get("overall")
    if not isinstance(raw_overall, Mapping):
        raise ValueError("A player recommendation cache requires Overall results.")
    overall = dict(raw_overall)
    raw_candidates = overall.get("filterCandidates")
    if not isinstance(raw_candidates, list):
        raise ValueError(
            "A player recommendation cache requires the Overall candidate pool."
        )

    raw_top = overall.get("topRecommendations")
    if not isinstance(raw_top, list):
        raise ValueError(
            "A player recommendation cache requires Overall top recommendations."
        )

    source_by_chart = _standard_candidate_index(modes)
    source_top_ids: set[str] = set()
    for mode_key in ("singles", "doubles"):
        source_mode = modes.get(mode_key)
        if not isinstance(source_mode, Mapping):
            continue
        source_top_ids.update(
            str(candidate.get("chartId") or "").strip()
            for candidate in source_mode.get("topRecommendations", [])
            if isinstance(candidate, Mapping)
        )

    def references_for(
        candidates: list[Any],
        *,
        allowed_ids: set[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        references: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_candidate in candidates:
            if not isinstance(raw_candidate, Mapping):
                raise ValueError("An Overall recommendation candidate is invalid.")
            candidate = dict(raw_candidate)
            chart_id = str(candidate.get("chartId") or "").strip()
            source = source_by_chart.get(chart_id)
            # Legacy caches can contain rows that the v3 skill window removes.
            if source is None or (allowed_ids is not None and chart_id not in allowed_ids):
                continue
            if chart_id in seen:
                raise ValueError("Overall recommendation candidates must be unique.")
            candidate_without_gain = {
                key: value
                for key, value in candidate.items()
                if key != "projectedGain"
            }
            source_without_gain = {
                key: value for key, value in source.items() if key != "projectedGain"
            }
            if candidate_without_gain != source_without_gain:
                raise ValueError(
                    "An Overall recommendation candidate differs from its source chart."
                )
            references.append(
                {
                    "chartId": chart_id,
                    "projectedGain": candidate.get("projectedGain"),
                }
            )
            seen.add(chart_id)
            if limit is not None and len(references) >= limit:
                break
        return references

    filter_references = references_for(raw_candidates)
    top_references = references_for(
        raw_top,
        allowed_ids=source_top_ids,
        limit=RECOMMENDATION_DISPLAY_COUNT,
    )

    overall.pop("filterCandidates", None)
    overall.pop("topRecommendations", None)
    overall[OVERALL_FILTER_REFS_FIELD] = filter_references
    overall[OVERALL_TOP_REFS_FIELD] = top_references
    if "filterCandidateCount" in raw_overall:
        overall["filterCandidateCount"] = len(filter_references)
    if "candidateCount" in raw_overall:
        overall["candidateCount"] = len(source_top_ids)
    if "sourceRecommendationCounts" in raw_overall:
        overall["sourceRecommendationCounts"] = {
            mode_key: len(modes.get(mode_key, {}).get("topRecommendations", []))
            if isinstance(modes.get(mode_key), Mapping)
            else 0
            for mode_key in ("singles", "doubles")
        }
    modes["overall"] = overall
    player["modes"] = modes
    result["player"] = player
    result[PLAYER_CACHE_STORAGE_SCHEMA_FIELD] = PLAYER_CACHE_STORAGE_SCHEMA_VERSION
    return result


def _materialized_overall(
    modes: Mapping[str, Any],
    *,
    difficulty: str | None,
    include_full_pool: bool,
    compact_top: bool,
) -> dict[str, Any]:
    raw_overall = modes.get("overall")
    if not isinstance(raw_overall, Mapping):
        raise ValueError("A cached recommendation player has no Overall results.")
    overall = dict(raw_overall)
    raw_references = overall.pop(OVERALL_FILTER_REFS_FIELD, None)
    if not isinstance(raw_references, list):
        raise ValueError("A compact Overall recommendation pool is invalid.")
    source_by_chart = _standard_candidate_index(modes)
    source_top_ids: set[str] = set()
    for mode_key in ("singles", "doubles"):
        source_mode = modes.get(mode_key)
        if not isinstance(source_mode, Mapping):
            continue
        source_top_ids.update(
            str(candidate.get("chartId") or "").strip()
            for candidate in source_mode.get("topRecommendations", [])
            if isinstance(candidate, Mapping)
        )
    difficulty_options = _overall_difficulty_options(source_by_chart)
    if difficulty is not None and difficulty not in difficulty_options:
        raise RecommendationArtifactQueryError(
            "The requested Overall difficulty is not available."
        )
    if not include_full_pool:
        overall["difficultyOptions"] = difficulty_options
    def materialize_references(
        references: list[Any],
        *,
        allowed_ids: set[str] | None = None,
        selected_difficulty: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_reference in references:
            if not isinstance(raw_reference, Mapping):
                raise ValueError(
                    "A compact Overall recommendation reference is invalid."
                )
            chart_id = str(raw_reference.get("chartId") or "").strip()
            source = source_by_chart.get(chart_id)
            # Schema-2 caches can retain rows that the read-time v3 window clips.
            if source is None or (allowed_ids is not None and chart_id not in allowed_ids):
                continue
            if not chart_id or chart_id in seen:
                raise ValueError(
                    "A compact Overall recommendation reference is invalid."
                )
            seen.add(chart_id)
            if (
                selected_difficulty is not None
                and _official_difficulty(source) != selected_difficulty
            ):
                continue
            candidates.append(
                {
                    **source,
                    "projectedGain": raw_reference.get("projectedGain"),
                }
            )
            if limit is not None and len(candidates) >= limit:
                break
        return candidates

    if compact_top:
        raw_top_references = overall.pop(OVERALL_TOP_REFS_FIELD, None)
        if not isinstance(raw_top_references, list):
            raise ValueError(
                "A compact Overall top-recommendation pool is invalid."
            )
        overall["topRecommendations"] = materialize_references(
            raw_top_references,
            allowed_ids=source_top_ids,
            limit=RECOMMENDATION_DISPLAY_COUNT,
        )
    else:
        raw_top = overall.get("topRecommendations", [])
        if not isinstance(raw_top, list) or not all(
            isinstance(candidate, Mapping) for candidate in raw_top
        ):
            raise ValueError("A cached Overall top-recommendation pool is invalid.")
        overall["topRecommendations"] = [
            dict(candidate)
            for candidate in raw_top
            if str(candidate.get("chartId") or "").strip() in source_top_ids
        ][:RECOMMENDATION_DISPLAY_COUNT]

    valid_filter_reference_ids = {
        str(reference.get("chartId") or "").strip()
        for reference in raw_references
        if isinstance(reference, Mapping)
        and str(reference.get("chartId") or "").strip() in source_by_chart
    }
    if "sourceRecommendationCounts" in raw_overall:
        overall["sourceRecommendationCounts"] = {
            mode_key: len(modes.get(mode_key, {}).get("topRecommendations", []))
            if isinstance(modes.get(mode_key), Mapping)
            else 0
            for mode_key in ("singles", "doubles")
        }
    if "candidateCount" in raw_overall:
        overall["candidateCount"] = len(source_top_ids)
    if difficulty is None and not include_full_pool:
        if "filterCandidateCount" in raw_overall:
            overall["filterCandidateCount"] = len(valid_filter_reference_ids)
        return overall
    candidates = materialize_references(
        raw_references,
        selected_difficulty=difficulty,
    )
    if "filterCandidateCount" in raw_overall:
        overall["filterCandidateCount"] = len(valid_filter_reference_ids)
    overall["filterCandidates"] = candidates
    return overall


def _project_legacy_overall(
    modes: Mapping[str, Any],
    *,
    difficulty: str | None,
    include_full_pool: bool,
) -> dict[str, Any]:
    raw_overall = modes.get("overall")
    if not isinstance(raw_overall, Mapping):
        raise ValueError("A cached recommendation player has no Overall results.")
    overall = dict(raw_overall)
    raw_candidates = overall.pop("filterCandidates", None)
    if not isinstance(raw_candidates, list) or not all(
        isinstance(candidate, Mapping) for candidate in raw_candidates
    ):
        raise ValueError("A cached Overall recommendation pool is invalid.")
    source_by_chart = _standard_candidate_index(modes)
    source_top_ids = {
        str(candidate.get("chartId") or "").strip()
        for mode_key in ("singles", "doubles")
        if isinstance(modes.get(mode_key), Mapping)
        for candidate in modes[mode_key].get("topRecommendations", [])
        if isinstance(candidate, Mapping)
    }
    difficulty_options = _overall_difficulty_options(source_by_chart)
    if difficulty is not None and difficulty not in difficulty_options:
        raise RecommendationArtifactQueryError(
            "The requested Overall difficulty is not available."
        )
    overall["topRecommendations"] = [
        dict(candidate)
        for candidate in overall.get("topRecommendations", [])
        if isinstance(candidate, Mapping)
        and str(candidate.get("chartId") or "").strip() in source_top_ids
    ][:RECOMMENDATION_DISPLAY_COUNT]
    if "sourceRecommendationCounts" in raw_overall:
        overall["sourceRecommendationCounts"] = {
            mode_key: len(modes.get(mode_key, {}).get("topRecommendations", []))
            if isinstance(modes.get(mode_key), Mapping)
            else 0
            for mode_key in ("singles", "doubles")
        }
    if "candidateCount" in raw_overall:
        overall["candidateCount"] = len(source_top_ids)
    bounded_candidates = [
        dict(candidate)
        for candidate in raw_candidates
        if str(candidate.get("chartId") or "").strip() in source_by_chart
    ]
    if "filterCandidateCount" in raw_overall:
        overall["filterCandidateCount"] = len(bounded_candidates)
    if not include_full_pool:
        overall["difficultyOptions"] = difficulty_options
    if difficulty is not None or include_full_pool:
        overall["filterCandidates"] = [
            candidate
            for candidate in bounded_candidates
            if difficulty is None or _official_difficulty(candidate) == difficulty
        ]
    return overall


def materialize_player_recommendation_cache(
    payload: Mapping[str, Any],
    *,
    mode: str | None = None,
    difficulty: str | None = None,
) -> dict[str, Any]:
    """Return the public envelope, optionally containing only one requested mode."""
    if mode is not None and mode not in RECOMMENDATION_MODE_KEYS:
        raise RecommendationArtifactQueryError(
            "The requested recommendation mode is invalid."
        )
    normalized_difficulty = normalize_recommendation_difficulty(mode, difficulty)
    result = dict(payload)
    storage_schema = int(result.get(PLAYER_CACHE_STORAGE_SCHEMA_FIELD) or 0)
    compact = storage_schema >= 2
    compact_top = storage_schema >= PLAYER_CACHE_STORAGE_SCHEMA_VERSION
    result.pop(PLAYER_CACHE_STORAGE_SCHEMA_FIELD, None)
    raw_player = result.get("player")
    if not isinstance(raw_player, Mapping):
        raise ValueError("A cached recommendation response has no player object.")
    player = dict(raw_player)
    raw_modes = _mode_mapping(player)
    bounded_modes = _bounded_modes(raw_modes)

    requested_keys = tuple(bounded_modes) if mode is None else (mode,)
    public_modes: dict[str, Any] = {}
    for mode_key in requested_keys:
        raw_value = bounded_modes.get(mode_key)
        if not isinstance(raw_value, Mapping):
            continue
        if mode_key == "overall" and compact:
            public_modes[mode_key] = _materialized_overall(
                bounded_modes,
                difficulty=normalized_difficulty,
                include_full_pool=mode is None,
                compact_top=compact_top,
            )
        elif mode_key == "overall":
            public_modes[mode_key] = _project_legacy_overall(
                bounded_modes,
                difficulty=normalized_difficulty,
                include_full_pool=mode is None,
            )
        else:
            value = dict(raw_value)
            value.pop(OVERALL_FILTER_REFS_FIELD, None)
            public_modes[mode_key] = value
    player["modes"] = public_modes
    result["player"] = player
    return result
