"""Median-cost anchors with a deterministic exponential interior and linear tails."""
from __future__ import annotations

import bisect
import math
from typing import Any, Mapping, Sequence

import numpy as np

CALIBRATION_VERSION = 1
GROWTH_GRID = np.linspace(.001, 1.2, 1600)


def fit_cross_level_curve(anchors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Fit curvature only; every measured reference median remains an exact anchor."""
    ordered = sorted((dict(anchor) for anchor in anchors), key=lambda anchor: anchor["level"])
    levels = [int(anchor["level"]) for anchor in ordered]
    if len(ordered) < 3 or levels[0] != 20 or len(set(levels)) != len(levels):
        raise ValueError("Cross-level calibration requires three distinct supported anchors including level 20.")
    costs = np.array([anchor["cost"] for anchor in ordered], dtype=float)
    if not np.all(np.isfinite(costs)) or not np.all(np.diff(costs) > 0):
        raise ValueError("Reference median costs must be finite and strictly increasing: " + str(levels))
    span = float(costs[-1] - costs[0])
    z = (costs - costs[0]) / span
    u = np.array(levels, dtype=float) - 20
    candidates = []
    for k in GROWTH_GRID:
        design = np.column_stack((np.ones(len(z)), np.exp(k * u)))
        offset, amplitude = np.linalg.lstsq(design, z, rcond=None)[0]
        error = float(np.mean((design @ [offset, amplitude] - z) ** 2))
        if math.isfinite(error) and math.isfinite(offset) and math.isfinite(amplitude) and offset < 0 and amplitude > 0:
            candidates.append((float(k), float(offset), float(amplitude), error))
    if not candidates:
        raise ValueError("No valid positive-growth offset exponential fits these reference medians.")
    minimum = min(candidate[3] for candidate in candidates)
    k, normalized_offset, amplitude, mse = next(candidate for candidate in candidates if candidate[3] <= minimum + 1e-12)
    offset = float(costs[0] + normalized_offset * span)
    if not math.isfinite(offset) or not offset < costs[0]:
        raise ValueError("The fitted exponential offset must be finite and below every reference median.")
    for anchor in ordered:
        anchor.update(level=int(anchor["level"]), difficulty=int(anchor["level"]) + .5, cost=float(anchor["cost"]))
    return {
        "version": CALIBRATION_VERSION, "anchors": ordered, "offset": offset,
        "fit": {"growth": k, "normalizedOffset": normalized_offset,
                "normalizedAmplitude": amplitude, "normalizedMse": mse,
                "boundHit": k in (float(GROWTH_GRID[0]), float(GROWTH_GRID[-1]))},
        "tailPolicy": "tangent-linear", "supportedRange": [levels[0] + .5, levels[-1] + .5],
        "referenceMinimumPlayers": 10, "referenceMinimumCharts": 5,
    }


def _segment(curve: Mapping[str, Any], index: int) -> tuple[Mapping[str, Any], Mapping[str, Any], float]:
    low, high = curve["anchors"][index:index + 2]
    beta = math.log1p((high["cost"] - low["cost"]) / (low["cost"] - curve["offset"])) / (high["level"] - low["level"])
    return low, high, beta


def inverse_chart_cost(cost: float, curve: Mapping[str, Any]) -> float:
    """Return difficulty on the entire real cost line, including costs below offset."""
    if not math.isfinite(cost):
        raise ValueError("Chart cost must be finite.")
    anchors, offset = curve["anchors"], curve["offset"]
    if cost < anchors[0]["cost"]:
        endpoint, _, beta = _segment(curve, 0)
        return endpoint["difficulty"] + (cost - endpoint["cost"]) / (beta * (endpoint["cost"] - offset))
    if cost > anchors[-1]["cost"]:
        _, endpoint, beta = _segment(curve, len(anchors) - 2)
        return endpoint["difficulty"] + (cost - endpoint["cost"]) / (beta * (endpoint["cost"] - offset))
    index = min(bisect.bisect_right([anchor["cost"] for anchor in anchors], cost) - 1, len(anchors) - 2)
    low, _, beta = _segment(curve, index)
    return low["difficulty"] + math.log1p((cost - low["cost"]) / (low["cost"] - offset)) / beta


def chart_cost(difficulty: float, curve: Mapping[str, Any]) -> float:
    """Forward curve, used to verify anchors, tails and inverse consistency."""
    if not math.isfinite(difficulty):
        raise ValueError("Difficulty must be finite.")
    anchors, offset = curve["anchors"], curve["offset"]
    if difficulty < anchors[0]["difficulty"]:
        endpoint, _, beta = _segment(curve, 0)
        return endpoint["cost"] + beta * (endpoint["cost"] - offset) * (difficulty - endpoint["difficulty"])
    if difficulty > anchors[-1]["difficulty"]:
        _, endpoint, beta = _segment(curve, len(anchors) - 2)
        return endpoint["cost"] + beta * (endpoint["cost"] - offset) * (difficulty - endpoint["difficulty"])
    index = min(bisect.bisect_right([anchor["difficulty"] for anchor in anchors], difficulty) - 1, len(anchors) - 2)
    low, _, beta = _segment(curve, index)
    return low["cost"] + (low["cost"] - offset) * math.expm1(beta * (difficulty - low["difficulty"]))
