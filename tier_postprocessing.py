"""Versioned, repeatable compression of calibrated tier deviations."""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from typing import Any, Mapping

import numpy as np

from piu_misgrade_analyzer import difficulty_effect_band, relative_difficulty_group
from tier_difficulty import refresh_pumbility_metrics

SCHEMA_VERSION = 44
FIT_FIELDS = (
    "estimatedDifficulty", "difficultyDelta", "difficultyCi95Low", "difficultyCi95High",
    "difficultyDeltaCi95Low", "difficultyDeltaCi95High", "effectBand", "effectBandRank",
    "levelRank", "levelComparisonCharts", "modeRank", "levelPercentile",
    "relativeGroup", "relativeGroupRank",
)


def compression_policy(base_schema: int) -> dict[str, Any]:
    if base_schema not in (26, 41, 42, 43):
        raise ValueError("Unsupported initial-fit tier schema.")
    return {"version": 1, "method": "official-midpoint-compression", "factor": .5,
            "reference": "current-official-level-plus-0.5", "metrics": ["scoring", "clearing"],
            "baseAnalysisSchemaVersion": base_schema}


def compress_difficulty(value: float | None, level: int) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Initial fitted difficulty must be finite or null.")
    midpoint = level + .5
    return midpoint + .5 * (value - midpoint)


def _metrics(row):
    return (row, row["tierMetrics"]["clearing"], row["tierMetrics"]["pumbility"])


def _fit(metric):
    return {key: copy.deepcopy(metric[key]) for key in FIT_FIELDS if key in metric}


def initial_fit_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Recover the original calibration contract for fitting and validation."""
    result = copy.deepcopy(payload)
    policy = result.get("summary", {}).get("method", {}).get("ratingPostprocessing")
    if result.get("schemaVersion") != SCHEMA_VERSION:
        if policy is not None:
            raise ValueError("Post-processing metadata requires its versioned schema.")
        return result
    if not isinstance(policy, dict) or policy != compression_policy(policy.get("baseAnalysisSchemaVersion")):
        raise ValueError("Incompatible midpoint compression policy.")
    for mode in ("singles", "doubles"):
        for row in result.get(mode, []):
            for metric in _metrics(row):
                fitted = metric.pop("initialFit", None)
                if (not isinstance(fitted, dict) or "estimatedDifficulty" not in fitted
                        or set(fitted) - set(FIT_FIELDS)):
                    raise ValueError("A compressed metric requires its original fitted values.")
                compress_difficulty(fitted["estimatedDifficulty"], row["level"])
                for key in FIT_FIELDS:
                    metric.pop(key, None)
                metric.update(fitted)
    result["schemaVersion"] = policy["baseAnalysisSchemaVersion"]
    del result["summary"]["method"]["ratingPostprocessing"]
    _refresh_summary(result)
    return result


def _refresh_summary(payload):
    for mode in ("singles", "doubles"):
        rows = payload.get(mode, [])
        folders = payload.get("summary", {}).get("modes", {}).get(mode, {}).get("folders", {})
        for folder, info in folders.items():
            group = [row for row in rows if row.get("folder") == folder]
            info["overratedCharts"] = sum(row.get("effectBandRank") == 1 for row in group)
            info["underratedCharts"] = sum(row.get("effectBandRank") == 7 for row in group)


def _rank_scoring(rows):
    measured = sorted((r for r in rows if r["estimatedDifficulty"] is not None),
                      key=lambda r: (r["difficultyDelta"], -int(r.get("nContributors") or 0),
                                     r.get("songName") or "", r["chartId"]))
    folders = defaultdict(list)
    for rank, row in enumerate(measured, 1):
        row["modeRank"] = rank
        folders[row["level"]].append(row)
    for group in folders.values():
        for rank, row in enumerate(group, 1):
            ties = [i + 1 for i, other in enumerate(group) if other["difficultyDelta"] == row["difficultyDelta"]]
            percentile = (float(np.mean(ties)) - .5) / len(group)
            relative_rank, relative = relative_difficulty_group(percentile)
            row.update(levelRank=rank, levelComparisonCharts=len(group), levelPercentile=percentile,
                       relativeGroupRank=relative_rank, relativeGroup=relative)
    for row in rows:
        if row["estimatedDifficulty"] is None:
            for key in ("modeRank", "levelRank", "levelComparisonCharts", "levelPercentile", "relativeGroupRank", "relativeGroup"):
                row[key] = None


def _rank_secondary(rows, name):
    groups = defaultdict(list)
    for row in rows:
        if row["tierMetrics"][name]["estimatedDifficulty"] is not None:
            groups[row["level"]].append(row)
    for group in groups.values():
        # Preserve the fitted order when the affine transform collapses adjacent floats.
        group.sort(key=lambda r: (r["tierMetrics"][name]["estimatedDifficulty"],
                                  r["tierMetrics"][name]["initialFit"]["estimatedDifficulty"],
                                  r.get("songName") or "", r["chartId"]))
        for rank, row in enumerate(group, 1):
            row["tierMetrics"][name].update(levelRank=rank, levelComparisonCharts=len(group))


def finalize_tiers(payload: Mapping[str, Any], *, newly_fitted_metrics=()) -> dict[str, Any]:
    """Always derive public values from the initial fit, never from a prior compression."""
    if set(newly_fitted_metrics) - {"scoring", "clearing"}:
        raise ValueError("Unknown newly fitted tier metric.")
    processed = payload.get("schemaVersion") == SCHEMA_VERSION
    raw = initial_fit_payload(payload)
    if processed:
        for mode in ("singles", "doubles"):
            for original, row in zip(payload.get(mode, []), raw.get(mode, [])):
                for name, source, target in zip(("scoring", "clearing"), _metrics(original), _metrics(row)):
                    if name in newly_fitted_metrics:
                        for key in FIT_FIELDS:
                            target.pop(key, None)
                        target.update(_fit(source))
    compression_policy(raw["schemaVersion"])
    refresh_pumbility_metrics(raw)
    result = copy.deepcopy(raw)
    for mode in ("singles", "doubles"):
        for row in result.get(mode, []):
            for metric in _metrics(row):
                metric["initialFit"] = _fit(metric)
            for metric in _metrics(row)[:2]:
                fitted = metric["initialFit"]
                value = compress_difficulty(fitted["estimatedDifficulty"], row["level"])
                metric["estimatedDifficulty"] = value
                metric["difficultyDelta"] = None if value is None else value - row["level"] - .5
                metric["effectBandRank"], metric["effectBand"] = (None, None) if value is None else difficulty_effect_band(metric["difficultyDelta"])
                for side in ("Low", "High"):
                    key = f"difficultyCi95{side}"
                    if key in fitted:
                        endpoint = compress_difficulty(fitted[key], row["level"])
                        metric[key] = endpoint
                        if f"difficultyDeltaCi95{side}" in fitted:
                            metric[f"difficultyDeltaCi95{side}"] = None if endpoint is None else endpoint - row["level"] - .5
        _rank_scoring(result.get(mode, []))
        _rank_secondary(result.get(mode, []), "clearing")
    pumbility_fits = {row["chartId"]: row["tierMetrics"]["pumbility"]["initialFit"]
                     for mode in ("singles", "doubles") for row in result.get(mode, [])}
    refresh_pumbility_metrics(result)
    for mode in ("singles", "doubles"):
        for row in result.get(mode, []):
            row["tierMetrics"]["pumbility"]["initialFit"] = pumbility_fits[row["chartId"]]
        _rank_secondary(result.get(mode, []), "pumbility")
    result["summary"]["method"]["ratingPostprocessing"] = compression_policy(raw["schemaVersion"])
    result["schemaVersion"] = SCHEMA_VERSION
    _refresh_summary(result)
    return result


def validate_postprocessing(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = initial_fit_payload(payload)
    expected = finalize_tiers(raw)
    for mode in ("singles", "doubles"):
        for actual, correct in zip(payload.get(mode, []), expected.get(mode, [])):
            for a, b in zip(_metrics(actual), _metrics(correct)):
                for key in (*FIT_FIELDS, "initialFit"):
                    av, bv = a.get(key), b.get(key)
                    if isinstance(bv, (float, int)) and not isinstance(bv, bool):
                        if not isinstance(av, (float, int)) or isinstance(av, bool) or not math.isfinite(av) or abs(av - bv) > 1e-8:
                            raise ValueError("Tier value disagrees with midpoint compression.")
                    elif av != bv:
                        raise ValueError("Tier metadata disagrees with midpoint compression.")
    return raw
