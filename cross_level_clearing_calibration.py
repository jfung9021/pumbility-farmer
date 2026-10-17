"""One monotone Clearing scale per mode from exact median-threshold anchors."""
from __future__ import annotations

import bisect
import math
from typing import Any, Mapping, Sequence


def fit_clearing_curve(anchors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted((dict(anchor) for anchor in anchors), key=lambda anchor: anchor["level"])
    if len(ordered) < 3:
        raise ValueError("Clearing calibration requires at least three supported folders.")
    for index, anchor in enumerate(ordered):
        level, threshold = anchor["level"], anchor["threshold"]
        if (isinstance(level, bool) or not isinstance(level, int) or level < 16
                or not math.isfinite(threshold) or anchor["referenceCharts"] < 5
                or (index and (level <= ordered[index - 1]["level"]
                               or threshold <= ordered[index - 1]["threshold"]))):
            raise ValueError("Clearing reference medians must strictly increase: "
                             + str([(a["level"], a["threshold"]) for a in ordered]))
        anchor["difficulty"] = level + .5
    return {"version": 1, "anchors": ordered, "tailPolicy": "boundary-linear",
            "supportedRange": [ordered[0]["difficulty"], ordered[-1]["difficulty"]]}


def clearing_difficulty(threshold: float, curve: Mapping[str, Any]) -> float:
    if not math.isfinite(threshold):
        raise ValueError("Clearing threshold must be finite.")
    anchors = curve["anchors"]
    index = max(0, min(len(anchors) - 2,
                       bisect.bisect_right([anchor["threshold"] for anchor in anchors], threshold) - 1))
    left, right = anchors[index:index + 2]
    return left["difficulty"] + (right["level"] - left["level"]) * (
        threshold - left["threshold"]) / (right["threshold"] - left["threshold"])
