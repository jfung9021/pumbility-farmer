"""Final SSS+ scale policy, isolated from General and preliminary target fits."""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np

from official_cross_level_calibration import GROWTH_GRID, inverse_chart_cost
from sss_scoring import fit_target_curve

SCALE_METHOD = "bounded-offset-local-lower-scale-v1"
TAIL_POLICY = "lower-secant-upper-tangent-linear-v1"
SCALE_POLICY = {"version": 1, "method": SCALE_METHOD, "minimumOffsetMarginAnchorGaps": 1.,
                "lowerExtrapolation": "adjacent-anchor-secant", "upperExtrapolation": "tangent-linear",
                "scope": "official-target-final-curve-only"}


def fit_final_curve(anchors: Sequence[Mapping[str, Any]], *, policy_version: int) -> dict:
    curve = fit_target_curve(anchors, minimum_level=20)
    if policy_version < 3:
        return curve
    ordered = curve["anchors"]
    costs = np.array([a["cost"] for a in ordered], dtype=float)
    span = float(costs[-1] - costs[0])
    z = (costs - costs[0]) / span
    u = np.array([a["level"] for a in ordered], dtype=float) - 20
    gap = float(costs[1] - costs[0])
    limit = float(costs[0] - gap)
    candidates = []
    for growth in GROWTH_GRID:
        design = np.column_stack((np.ones(len(z)), np.exp(growth * u)))
        offset, amplitude = np.linalg.lstsq(design, z, rcond=None)[0]
        error = float(np.mean((design @ [offset, amplitude] - z) ** 2))
        actual_offset = float(costs[0] + offset * span)
        if (math.isfinite(error) and math.isfinite(actual_offset) and math.isfinite(amplitude)
                and offset < 0 and amplitude > 0 and actual_offset <= limit):
            candidates.append((float(growth), float(offset), float(amplitude), error, actual_offset))
    if not candidates:
        raise ValueError("No valid final SSS+ exponential fits the lowest-anchor offset margin.")
    minimum = min(c[3] for c in candidates)
    growth, normalized_offset, amplitude, error, offset = next(c for c in candidates if c[3] <= minimum + 1e-12)
    curve.update(offset=offset, tailPolicy=TAIL_POLICY,
        fit={"growth": growth, "normalizedOffset": normalized_offset, "normalizedAmplitude": amplitude,
             "normalizedMse": error, "boundHit": growth in (float(GROWTH_GRID[0]), float(GROWTH_GRID[-1]))},
        finalScale={"version": 1, "method": SCALE_METHOD, "minimumOffsetMarginAnchorGaps": 1.,
                    "offsetLimit": limit, "lowerSlope": gap / (ordered[1]["level"] - ordered[0]["level"]),
                    "lowerAnchorLevels": [ordered[0]["level"], ordered[1]["level"]]})
    return curve


def inverse_final_cost(cost: float, curve: Mapping[str, Any]) -> float:
    if not math.isfinite(cost):
        raise ValueError("Final chart cost must be finite.")
    first = curve["anchors"][0]
    if curve.get("tailPolicy") == TAIL_POLICY and cost < first["cost"]:
        return first["difficulty"] + (cost - first["cost"]) / curve["finalScale"]["lowerSlope"]
    return inverse_chart_cost(cost, curve)
