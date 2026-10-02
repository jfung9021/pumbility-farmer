"""Central-range scales preserve the equal-player mean without bounding tails."""
from __future__ import annotations
from typing import Any, Mapping
from official_range_calibration import fit_central_ranges
from official_scores import OFFICIAL_SCORING_MINIMUM_LEVELS

MINIMUM_LEVELS = OFFICIAL_SCORING_MINIMUM_LEVELS


def scoring_calibration_identity() -> dict[str, Any]:
    return {
        "method": "shared-linear-player-gaps", "version": 6,
        "minimumLevels": dict(MINIMUM_LEVELS), "referenceQuantile": .90,
        "targetHalfWidth": .45, "maximumScale": 1., "rangePolicy": "unbounded",
        "minimumSupportedCharts": 8, "minimumReferencePlayers": 10,
        "outlierIqrMultiplier": 2., "minimumIqr": .2, "minimumOutlierPlayers": 10,
        "outlierUse": "diagnostic-only", "fallbackScale": .4,
    }


def fit_scoring_spreads(folders: Mapping[tuple[str, int], Mapping[str, Any]]) -> dict:
    return fit_central_ranges({k:v for k,v in folders.items() if k[1] >= MINIMUM_LEVELS[k[0]]},
                              minimum_charts=8, fallback_scale=.4,
                              outlier_iqr_multiplier=2., minimum_iqr=.2, reference_quantile=.90,
                              maximum_scale=1.)


def calibrate_scoring_delta(raw: float, params: Mapping[str, Any], count: int,
                            eligible: bool = True) -> tuple[float, bool]:
    extreme = bool(eligible and params["basis"] == "folder" and count >= 10
                   and (raw < params["lowerFence"] or raw > params["upperFence"]))
    return params["scale"] * raw, extreme
