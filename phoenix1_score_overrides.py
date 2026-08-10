"""Chart-specific corrections for frozen Phoenix 1 raw-score evidence."""

from __future__ import annotations

import math
from typing import Any


SOLVE_MY_HURT_SHORTCUT_D26_CHART_ID = "24228275-4be2-492c-827d-afd6e38f2d8e"
SOLVE_MY_HURT_SHORTCUT_D26_NAME = "Solve My Hurt - SHORT CUT - D26"
SOLVE_MY_HURT_SHORTCUT_D26_FORMULA = (
    "(((score / 1000000 * 1566) - 540) / 1026) * 1000000"
)

# The frozen Phoenix 1 API rows for D26 establish these score-band multipliers.
# Every existing score for the corrected chart, before and after conversion, is
# at least 900,000, so no unobserved lower band is inferred here.
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
)


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def convert_phoenix1_score(chart_id: object, score: object) -> float | None:
    """Apply the supplied Excel conversion only to Solve My Hurt Shortcut D26."""
    value = _finite_number(score)
    if value is None:
        return None
    if str(chart_id) != SOLVE_MY_HURT_SHORTCUT_D26_CHART_ID:
        return value
    return (((value / 1_000_000 * 1566) - 540) / 1026) * 1_000_000


def _phoenix1_pumbility_multiplier(score: object) -> float | None:
    value = _finite_number(score)
    if value is None:
        return None
    return next(
        (multiplier for threshold, multiplier in _PHOENIX1_PUMBILITY_MULTIPLIERS
         if value >= threshold),
        None,
    )


def convert_phoenix1_pumbility(
    chart_id: object,
    original_score: object,
    pumbility: object,
) -> float | None:
    """Re-band frozen Phoenix 1 Pumbility after the chart score conversion."""
    value = _finite_number(pumbility)
    if value is None:
        return None
    if str(chart_id) != SOLVE_MY_HURT_SHORTCUT_D26_CHART_ID:
        return value
    converted_score = convert_phoenix1_score(chart_id, original_score)
    original_multiplier = _phoenix1_pumbility_multiplier(original_score)
    converted_multiplier = _phoenix1_pumbility_multiplier(converted_score)
    if original_multiplier is None or converted_multiplier is None:
        return value
    return value * converted_multiplier / original_multiplier


def phoenix1_score_override_metadata() -> dict[str, Any]:
    return {
        "chartId": SOLVE_MY_HURT_SHORTCUT_D26_CHART_ID,
        "chart": SOLVE_MY_HURT_SHORTCUT_D26_NAME,
        "formula": SOLVE_MY_HURT_SHORTCUT_D26_FORMULA,
        "source": "phoenix1 only",
    }
