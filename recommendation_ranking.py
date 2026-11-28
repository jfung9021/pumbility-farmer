"""Equal-weight competition ranks for recommendation gain and target support."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

RANKING_METHOD = "equal-weight-gain-and-target-score-count-competition-ranks-v1"


def _number(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("Recommendation ranking requires finite numeric metrics.")
    try:
        number = Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError("Recommendation ranking requires finite numeric metrics.") from exc
    if not number.is_finite():
        raise ValueError("Recommendation ranking requires finite numeric metrics.")
    return number


def rank_recommendations(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copy and rank the complete viable pool before filtering or display limits."""
    output = [dict(row) for row in rows]
    gains = [_number(row.get("projectedGain")) for row in output]
    counts = [_number(row.get("scoreAtOrAboveTargetCount")) for row in output]
    if any(value < 0 or value != value.to_integral_value() for value in counts):
        raise ValueError("Recommendation score counts must be nonnegative integers.")

    def competition(values: Sequence[Decimal]) -> dict[Decimal, int]:
        ranks = {}
        for position, value in enumerate(sorted(values, reverse=True), 1):
            ranks.setdefault(value, position)
        return ranks

    gain_ranks, count_ranks = competition(gains), competition(counts)
    for row, gain, count in zip(output, gains, counts):
        row.update(scoreAtOrAboveTargetCount=int(count), gainRank=gain_ranks[gain],
                   scoreCountRank=count_ranks[count], rankScore=gain_ranks[gain] + count_ranks[count])

    def sort_key(row: Mapping[str, Any]) -> tuple:
        expected = row.get("expectedPumbility")
        if expected is None:
            expected = row.get("expectedCoopRating")
        return (row["rankScore"], -_number(row["projectedGain"]), -row["scoreAtOrAboveTargetCount"],
                -_number(expected if expected is not None else 0),
                str(row.get("songName") or "").casefold(), str(row.get("chartId") or ""))

    output.sort(key=sort_key)
    for position, row in enumerate(output, 1):
        row["recommendationRank"] = position
    return output
