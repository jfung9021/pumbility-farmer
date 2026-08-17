"""PIUScores catalog-derived Phoenix 1 to Phoenix 2 score normalization."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence, TypedDict


MAX_SCORE = 1_000_000.0


class Phoenix1ScoreNormalization(TypedDict):
    chart: str
    sourceDifficulty: str
    targetDifficulty: str
    sourceNoteCount: int
    targetNoteCount: int
    sourceType: str
    targetType: str


Phoenix1ScoreNormalizations = Mapping[str, Phoenix1ScoreNormalization]


# The frozen Phoenix 1 API rows establish these score-band multipliers. Current
# rows affected by a catalog-derived normalization, before and after conversion,
# are all at least 825,000, so no unobserved lower band is inferred here.
_PHOENIX1_PUMBILITY_MULTIPLIERS = (
    (995_000, 1.00),
    (990_000, 0.96),
    (985_000, 0.92),
    (980_000, 0.88),
    (975_000, 0.84),
    (970_000, 0.80),
    (960_000, 23 / 30),
    (950_000, 22 / 30),
    (925_000, 21 / 30),
    (900_000, 20 / 30),
    (825_000, 18 / 30),
)


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive_integer(value: object) -> int | None:
    number = _finite_number(value)
    if number is None or number <= 0 or not number.is_integer():
        return None
    return int(number)


def _chart_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("chartId") or "").strip()


def build_phoenix1_score_normalizations(
    phoenix1_charts: Sequence[Mapping[str, Any]],
    phoenix2_charts: Sequence[Mapping[str, Any]],
) -> dict[str, Phoenix1ScoreNormalization]:
    """Derive score conversions from stable IDs with changed positive note counts."""
    source_by_id = {
        chart_id: row
        for row in phoenix1_charts
        if (chart_id := _chart_id(row))
    }
    target_by_id = {
        chart_id: row
        for row in phoenix2_charts
        if (chart_id := _chart_id(row))
    }
    result: dict[str, Phoenix1ScoreNormalization] = {}
    for chart_id in sorted(source_by_id.keys() & target_by_id.keys()):
        source = source_by_id[chart_id]
        target = target_by_id[chart_id]
        source_type = str(source.get("type") or "").strip()
        target_type = str(target.get("type") or "").strip()
        source_notes = _positive_integer(source.get("noteCount"))
        target_notes = _positive_integer(target.get("noteCount"))
        if (
            not source_type
            or source_type != target_type
            or source_notes is None
            or target_notes is None
            or source_notes == target_notes
        ):
            continue
        song_name = str(target.get("songName") or source.get("songName") or chart_id)
        source_difficulty = str(source.get("difficulty") or "")
        target_difficulty = str(target.get("difficulty") or "")
        result[chart_id] = {
            "chart": f"{song_name} {target_difficulty}".strip(),
            "sourceDifficulty": source_difficulty,
            "targetDifficulty": target_difficulty,
            "sourceNoteCount": source_notes,
            "targetNoteCount": target_notes,
            "sourceType": source_type,
            "targetType": target_type,
        }
    return result


def convert_phoenix1_score(
    chart_id: object,
    score: object,
    normalizations: Phoenix1ScoreNormalizations | None = None,
) -> float | None:
    """Normalize one Phoenix 1 score to the Phoenix 2 chart note denominator."""
    value = _finite_number(score)
    if value is None:
        return None
    normalization = (normalizations or {}).get(str(chart_id))
    if normalization is None:
        return value
    source_notes = normalization["sourceNoteCount"]
    target_notes = normalization["targetNoteCount"]
    converted = (
        (
            (value / MAX_SCORE * source_notes)
            - (source_notes - target_notes)
        )
        / target_notes
    ) * MAX_SCORE
    return min(MAX_SCORE, max(0.0, converted))


def _phoenix1_pumbility_multiplier(score: object) -> float | None:
    value = _finite_number(score)
    if value is None:
        return None
    return next(
        (
            multiplier
            for threshold, multiplier in _PHOENIX1_PUMBILITY_MULTIPLIERS
            if value >= threshold
        ),
        None,
    )


def convert_phoenix1_pumbility(
    chart_id: object,
    original_score: object,
    pumbility: object,
    normalizations: Phoenix1ScoreNormalizations | None = None,
) -> float | None:
    """Re-band frozen Phoenix 1 Pumbility after score normalization."""
    value = _finite_number(pumbility)
    if value is None:
        return None
    if str(chart_id) not in (normalizations or {}):
        return value
    converted_score = convert_phoenix1_score(
        chart_id, original_score, normalizations
    )
    original_multiplier = _phoenix1_pumbility_multiplier(original_score)
    converted_multiplier = _phoenix1_pumbility_multiplier(converted_score)
    if original_multiplier is None or converted_multiplier is None:
        return value
    return value * converted_multiplier / original_multiplier


def phoenix1_score_overrides_metadata(
    normalizations: Phoenix1ScoreNormalizations | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic provenance for compatibility with public schemas."""
    return [
        {
            "chartId": chart_id,
            "chart": str(normalization["chart"]),
            "formula": (
                "1000000 - ((1000000 - score) * "
                f"{normalization['sourceNoteCount']} / "
                f"{normalization['targetNoteCount']})"
            ),
            "sourceDifficulty": normalization["sourceDifficulty"],
            "targetDifficulty": normalization["targetDifficulty"],
            "sourceNoteCount": normalization["sourceNoteCount"],
            "targetNoteCount": normalization["targetNoteCount"],
            "source": "PIUScores Phoenix 1 and Phoenix 2 chart catalogs",
        }
        for chart_id, normalization in sorted((normalizations or {}).items())
    ]
