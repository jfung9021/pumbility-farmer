"""Central-range clearer-ability calibration; outlier labels do not gate estimates."""
from __future__ import annotations
from typing import Any, Mapping, Sequence
from official_range_calibration import fit_central_ranges


def fit_clearing_spreads(folders: Mapping[tuple[str, int], Mapping[str, Sequence[float]]]) -> dict:
    inputs = {k: {"offsets": v["deltas"], "contributors": v["players"]} for k,v in folders.items()}
    return fit_central_ranges(inputs, minimum_charts=5, fallback_scale=1.,
                              outlier_iqr_multiplier=3., minimum_iqr=.07, reference_quantile=.90,
                              maximum_scale=None)


def calibrate_clearing_delta(raw_delta: float, parameters: Mapping[str, Any], *,
                             players: int, coverage: float,
                             interval_low: float | None, interval_high: float | None) -> tuple[float, bool]:
    supported = (parameters["basis"] == "folder" and players >= 20 and coverage >= .5
                 and interval_low is not None and interval_high is not None)
    extreme = bool(supported and (
        (raw_delta < parameters["lowerFence"] and interval_high < parameters["lowerFence"])
        or (raw_delta > parameters["upperFence"] and interval_low > parameters["upperFence"])))
    return parameters["scale"] * raw_delta, extreme


def clearing_calibration_identity() -> dict[str, Any]:
    return {
        "method": "robust-folder-spread", "version": 4,
        "referenceQuantile": .90, "targetHalfWidth": .45, "maximumScale": None,
        "rangePolicy": "unbounded", "minimumSupportedCharts": 5,
        "minimumReferencePlayers": 10, "minimumIqr": .07,
        "outlierIqrMultiplier": 3, "outlierMinimumPlayers": 20,
        "outlierMinimumCoverage": .5, "requireIntervalBeyondFence": True,
        "outlierUse": "diagnostic-only", "fallbackScale": 1.,
    }
