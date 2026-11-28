"""Source-independent Pumbility cohort and projection rules shared by recommenders."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Mapping, Sequence

from phoenix2_pumbility import GRADE_BANDS, grade_for_score

MINIMUM_PEERS = 10
UPWARD_WINDOWS = (100, 150, 200)


def decimal_number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError):
        return None
    return result if result.is_finite() else None


def pumbility_cents(value: Any) -> int | None:
    number = decimal_number(value)
    if number is None or number < 0:
        return None
    return int((number * 100).to_integral_value(rounding=ROUND_HALF_UP))


def select_pumbility_peers(
    rows: Sequence[Mapping[str, Any]], subject: Mapping[str, Any], *,
    tie_key: Callable[[Mapping[str, Any]], Any] | None = None,
) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    """Choose one upward-window cohort, then the nearest lower-rated backfill."""
    rating, player_id = subject["rating"], subject["playerId"]
    tie_key = tie_key or (lambda row: str(row["playerId"]))
    peers: list[Mapping[str, Any]] = []
    window = UPWARD_WINDOWS[-1]
    for window in UPWARD_WINDOWS:
        peers = [row for row in rows if row["playerId"] != player_id
                 and rating <= row["rating"] <= rating + window]
        if len(peers) >= MINIMUM_PEERS:
            break
    backfill_count = 0
    if len(peers) < MINIMUM_PEERS:
        lower = sorted((row for row in rows if row["playerId"] != player_id and row["rating"] < rating),
                       key=lambda row: (-row["rating"], tie_key(row)))
        addition = lower[:MINIMUM_PEERS - len(peers)]
        peers.extend(addition)
        backfill_count = len(addition)
    values = [row["rating"] for row in peers]
    return peers, {"minimumPlayers": MINIMUM_PEERS, "upwardWindow": window,
                   "backfillCount": backfill_count, "playerCount": len(peers),
                   "minimumPumbility": float(min(values)) if values else None,
                   "maximumPumbility": float(max(values)) if values else None,
                   "limitedData": len(peers) < MINIMUM_PEERS}


def linear_median(scores: Sequence[int]) -> Decimal:
    if not scores or any(type(score) is not int or not 0 <= score <= 1_000_000 for score in scores):
        raise ValueError("A median target requires valid integer scores.")
    ordered = sorted(scores)
    lower = (len(ordered) - 1) // 2
    upper = len(ordered) // 2
    return (Decimal(ordered[lower]) + Decimal(ordered[upper])) / 2


def projected_target(raw_score: Any) -> dict[str, Any]:
    value = decimal_number(raw_score)
    if value is None or not 0 <= value <= 1_000_000:
        raise ValueError("A projected score is outside the expected range.")
    plate = "PG" if value == 1_000_000 else "SG" if value > 995_000 else "MG"
    grade = grade_for_score(value)
    boundary = next(threshold for threshold, label, _ in GRADE_BANDS if label == grade)
    return {"rawPercentileScore": float(value), "gradeBoundaryScore": boundary,
            "targetScore": 1_000_000 if plate == "PG" else boundary,
            "targetGrade": grade, "assumedPlate": plate}


def marginal_gain_cents(expected: int, chart_id: str, top: Mapping[str, int], cutoff: int | None) -> tuple[int, int | None]:
    replacement = top.get(chart_id, cutoff)
    return (max(0, expected - replacement), replacement) if replacement is not None else (0, None)
