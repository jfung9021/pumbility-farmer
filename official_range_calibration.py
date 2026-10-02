"""Calibrate central distributions with a metric-specific scale cap and unbounded tails."""

from __future__ import annotations

from typing import Any, Mapping
import numpy as np

TARGET_HALF_WIDTH = .45
MINIMUM_REFERENCE_PLAYERS = 10


def fit_central_ranges(folders: Mapping[tuple[str, int], Mapping[str, Any]], *,
                       minimum_charts: int, fallback_scale: float,
                       outlier_iqr_multiplier: float, minimum_iqr: float,
                       reference_quantile: float, maximum_scale: float | None) -> dict:
    fitted = {}
    for key, data in sorted(folders.items()):
        values = np.asarray(data["offsets"], dtype=float)
        eligible = np.asarray(data.get("eligible", [True] * len(values)), dtype=bool)
        counts = np.asarray(data["contributors"])
        reference = values[(counts >= MINIMUM_REFERENCE_PLAYERS) & eligible]
        supported = len(reference) >= minimum_charts
        spread = lower = upper = None
        scale = fallback_scale
        if supported:
            spread = float(np.quantile(np.abs(reference), reference_quantile, method="linear"))
            scale = TARGET_HALF_WIDTH / spread if spread > 0 else 1.
            if maximum_scale is not None:
                scale = min(maximum_scale, scale)
            q25, q75 = np.quantile(reference, [.25, .75], method="linear")
            iqr = max(float(q75 - q25), minimum_iqr)
            lower = float(q25 - outlier_iqr_multiplier * iqr)
            upper = float(q75 + outlier_iqr_multiplier * iqr)
        fitted[key] = {
            "scale": scale, "referenceSpread": spread, "supportedCharts": len(reference),
            "lowerFence": lower, "upperFence": upper,
            "basis": "folder" if supported else "fallback",
            "referenceLevel": key[1] if supported else None,
        }
    # Borrow only from an independently supported folder, never another borrower.
    for key, params in fitted.items():
        if params["basis"] == "folder":
            continue
        donors = [k for k, p in fitted.items() if k[0] == key[0] and p["basis"] == "folder"]
        if not donors:
            continue
        donor = min(donors, key=lambda k: (abs(k[1] - key[1]), k[1]))
        params.update(scale=fitted[donor]["scale"], referenceSpread=fitted[donor]["referenceSpread"],
                      basis="neighbor-folder", referenceLevel=donor[1])
    return fitted
