"""Schema-53 final scale over frozen SSS+ fits; no observations are refitted."""
from __future__ import annotations

import copy
import math
from collections import Counter
from statistics import median
from typing import Any, Mapping

from official_cross_level_calibration import chart_cost
from sss_folder_centering import centering_folders, shift_scoring
from sss_scoring import MODES, RANK_FIELDS, _derived, payload_sha256
from sssplus_final_curve import SCALE_POLICY, fit_final_curve, inverse_final_cost
from sssplus_tiers import _recompute_means, validate_sssplus_tiers
from target_tail_policy import apply_tail_policy, finite
from tier_postprocessing import compress_difficulty

SCHEMA_VERSION = 53
POLICY_VERSION = 3
GOAL = "sssplus"
NAMESPACE = "sssplusMedianCalibration"
STAGE = "sssplusMedianBaseline"


def _method(policy_version: int) -> str:
    if policy_version not in (1, 2, 3):
        raise ValueError("Unsupported final median calibration policy.")
    return f"sssplus-refined-median-calibration-v{policy_version}"


def refined_population(base: Mapping[str, Any], *, policy_version: int = POLICY_VERSION) -> tuple[dict, dict, dict]:
    """Recover corrected coordinates, before schema-52 centering and tail mapping."""
    _method(policy_version)
    preview = copy.deepcopy(base)
    latents, original_curves = {}, base["summary"]["method"]["sssplusScoring"]["calibration"]["official"]
    for mode, key in MODES:
        for row in preview[key]:
            if row["level"] < 20:
                continue
            metric = copy.deepcopy(row["sssplusCenteringBaseline"]["scoring"])
            row["scoringVariants"][GOAL] = metric
            value = metric["initialFit"]["estimatedDifficulty"]
            latents[str(row["chartId"])] = None if value is None else chart_cost(value, original_curves[mode])
    return preview, latents, centering_folders(preview, goal=GOAL, require_mixed_first_pass=policy_version == 1)


def final_calibrations(base: Mapping[str, Any], *, policy_version: int = POLICY_VERSION) -> tuple[dict, dict, dict, dict]:
    preview, latents, support = refined_population(base, policy_version=policy_version)
    curves, errors, populations = {}, {}, {}
    for mode, key in MODES:
        anchors, populations[mode] = [], {}
        for level, folder in support[mode].items():
            rows = [r for r in preview[key] if str(r["chartId"]) in folder["populationChartIds"]]
            record = {"level": int(level), "supported": folder["basis"] == "folder",
                "populationChartIds": folder["populationChartIds"], "mixedFirstPassChartIds": folder["mixedFirstPassChartIds"],
                "branches": dict(sorted(Counter(r["scoringVariants"][GOAL]["scoringRefinement"]["branch"] for r in rows).items())),
                "possiblyTruncatedCharts": sum(bool(r["scoringVariants"][GOAL]["scoringRefinement"]["fullBoard"]["possiblyTruncated"]) for r in rows),
                "fewerThanFiveScores": sum(r["scoringVariants"][GOAL]["scoringRefinement"]["fullBoard"]["records"] < 5 for r in rows),
                "medianLatent": median(latents[str(r["chartId"])] for r in rows) if rows else None}
            populations[mode][level] = record
            if record["supported"]:
                anchors.append({"level": int(level), "cost": record["medianLatent"],
                    "referenceCharts": len(rows), "chartIds": folder["populationChartIds"]})
        try:
            curves[mode], errors[mode] = fit_final_curve(anchors, policy_version=policy_version), None
        except ValueError as exc:
            curves[mode], errors[mode] = None, str(exc)
    return latents, populations, curves, errors


def _project(metric: dict, level: int, old_curve: dict, curve: dict | None, error: str | None) -> None:
    for field in ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"):
        old = metric["initialFit"][field]
        value = None if old is None or curve is None else inverse_final_cost(chart_cost(old, old_curve), curve)
        metric["initialFit"][field] = value
        metric[field] = compress_difficulty(value, level)
        if field != "estimatedDifficulty":
            metric["initialFit"][field.replace("difficulty", "difficultyDelta", 1)] = None if value is None else value - level - .5
            metric[field.replace("difficulty", "difficultyDelta", 1)] = None if metric[field] is None else metric[field] - level - .5
    metric["initialFit"].update(_derived(metric["initialFit"]["estimatedDifficulty"], level))
    metric.update(_derived(metric["estimatedDifficulty"], level))
    if curve is None:
        metric.update(evidenceStatus="Unrated", scoringUnratedReason="Final SSS+ median calibration unavailable: " + str(error))


def _recalibrated_range(value: dict | None, level: int, old: dict, new: dict | None) -> dict | None:
    if not value or not new:
        return None
    midpoint = level + .5
    return {side: compress_difficulty(inverse_final_cost(chart_cost(midpoint + 2 * (value[side] - midpoint), old), new), level)
            for side in ("lower", "upper")}


def _apply(base: Mapping[str, Any], *, policy_version: int = POLICY_VERSION) -> dict:
    result = copy.deepcopy(base)
    result["schemaVersion"] = SCHEMA_VERSION
    method = _method(policy_version)
    latents, populations, curves, errors = final_calibrations(base, policy_version=policy_version)
    original = base["summary"]["method"]["sssplusScoring"]["calibration"]["official"]
    for mode, key in MODES:
        for row, old_row in zip(result[key], base[key]):
            row[STAGE] = {"scoring": copy.deepcopy(old_row["scoringVariants"][GOAL]),
                "pumbility": copy.deepcopy(old_row["pumbilityVariants"][GOAL]),
                "tailBaseline": copy.deepcopy(old_row["sssplusTailBaseline"])}
            if row["level"] < 20:
                # Replay the unchanged submitted tail map, never apply it twice.
                row["scoringVariants"][GOAL] = copy.deepcopy(old_row["sssplusTailBaseline"]["scoring"])
                continue
            metric = copy.deepcopy(old_row["sssplusCenteringBaseline"]["scoring"])
            # A derived anchor cannot promote previously provisional evidence.
            for field in ("evidenceStatus", "scoringProvisional", "scoringProvisionalReasons", "scoringExtrapolated"):
                if field in old_row["scoringVariants"][GOAL]:
                    metric[field] = copy.deepcopy(old_row["scoringVariants"][GOAL][field])
            _project(metric, row["level"], original[mode], curves[mode], errors[mode])
            row["scoringVariants"][GOAL] = metric
    residuals = centering_folders(result, goal=GOAL, require_mixed_first_pass=policy_version == 1)
    for mode, key in MODES:
        for row in result[key]:
            if row["level"] < 20:
                continue
            level, cid, metric = row["level"], str(row["chartId"]), row["scoringVariants"][GOAL]
            folder = residuals[mode][str(level)]
            ref = metric.get("scoringRefinement", {})
            frozen_range = _recalibrated_range(ref.get("range"), level, original[mode], curves[mode])
            shift_scoring(metric, level, folder, frozen_range)
            rare = ref.get("rare")
            band = _recalibrated_range({"lower": rare["upperBandLow"], "upper": rare["upperBandHigh"]} if rare else None,
                                      level, original[mode], curves[mode])
            metric["scoringFolderCentering"]["effectiveRareBand"] = None if band is None else {k: v + folder["finalShift"] for k, v in band.items()}
            cost = latents[cid]
            limits = curves[mode]["anchors"] if curves[mode] else []
            metric["scoringMedianCalibration"] = {"version": policy_version, "method": method, "source": "official", "mode": mode,
                "refinedLatent": cost, "anchorKind": "derived-provisional-full-folder-median",
                "anchor": copy.deepcopy(populations[mode][str(level)]), "calibrationError": errors[mode],
                "preliminaryExtrapolated": metric.get("scoringExtrapolated", False),
                "finalExtrapolated": None if cost is None or not limits else cost < limits[0]["cost"] or cost > limits[-1]["cost"],
                "intervals": "existing-player-bootstrap-fixed-final-curve-residual-and-tail-no-anchor-uncertainty"}
            if policy_version == 3:
                metric["scoringMedianCalibration"]["finalCurveScale"] = None if curves[mode] is None else copy.deepcopy(curves[mode]["finalScale"])
    _recompute_means(result)
    tail = apply_tail_policy(result)
    result["summary"]["method"]["sssplusTailPolicy"] = tail
    result["summary"]["method"][NAMESPACE] = {"version": policy_version, "method": method, "baseSchemaVersion": 52,
        "basePayloadSha256": payload_sha256(base), "localOnly": True, "target": 995000,
        "population": "all-finite-refined-official-charts-equal-chart-weight", "minimumMixedCharts": 5 if policy_version == 1 else 0, "minimumFinalCharts": 5,
        "anchorKind": "derived-provisional-full-folder-median", "minimumLevel": 20,
        "stageOrder": ["frozen-refined-latent", "supported-folder-median-anchors", "final-curve", "residual-median-offset",
                       "single-midpoint-compression", "unchanged-global-tail-policy", "component-mean-and-ranks"],
        "compressionApplications": 1, "compressionFactor": .5, "modelRefits": 0, "dataFetches": 0,
        "originalOfficialCalibration": copy.deepcopy(original), "populations": populations, "calibration": curves,
        "calibrationErrors": errors, "residualFolders": residuals,
        "previousTailPolicy": copy.deepcopy(base["summary"]["method"]["sssplusTailPolicy"])}
    if policy_version >= 2:
        result["summary"]["method"][NAMESPACE]["anchorEligibility"] = "minimum-five-finite-refined-charts-v1"
    if policy_version == 3:
        result["summary"]["method"][NAMESPACE]["finalScalePolicy"] = copy.deepcopy(SCALE_POLICY)
    _recompute_means(result)
    return result


def recover_median_baseline(payload: Mapping[str, Any]) -> dict:
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("Median calibration recovery requires schema 53.")
    result = copy.deepcopy(payload)
    result["schemaVersion"] = 52
    method = result["summary"]["method"].pop(NAMESPACE)
    result["summary"]["method"]["sssplusTailPolicy"] = method["previousTailPolicy"]
    for _, key in MODES:
        for row in result[key]:
            stage = row.pop(STAGE)
            row["scoringVariants"][GOAL], row["pumbilityVariants"][GOAL] = stage["scoring"], stage["pumbility"]
            row["sssplusTailBaseline"] = stage["tailBaseline"]
    return result


def _without_ranks(value: dict) -> dict:
    result = copy.deepcopy(value)
    for field in RANK_FIELDS:
        result.pop(field, None)
        result.get("initialFit", {}).pop(field, None)
    return result


def validate_median_calibration(payload: Mapping[str, Any], frozen: Mapping[str, Any] | None = None) -> dict:
    base = recover_median_baseline(payload)
    if frozen is not None and base != frozen:
        raise ValueError("Median calibration changed a frozen schema-52 field.")
    validate_sssplus_tiers(base)
    version = payload["summary"]["method"][NAMESPACE].get("version")
    if payload != _apply(base, policy_version=version):
        raise ValueError("Median calibration anchors, projection, residual, intervals, evidence, tails or ranks differ.")
    for mode, key in MODES:
        for row, old in zip(payload[key], base[key]):
            if row["level"] < 20:
                for variants in ("scoringVariants", "pumbilityVariants"):
                    if _without_ranks(row[variants][GOAL]) != _without_ranks(old[variants][GOAL]):
                        raise ValueError("Median calibration changed a submitted below-20 target value.")
        for level, folder in payload["summary"]["method"][NAMESPACE]["residualFolders"][mode].items():
            rows = [r for r in payload[key] if r["level"] == int(level) and finite(r["scoringVariants"][GOAL]["estimatedDifficulty"])]
            if folder["basis"] == "folder" and not math.isclose(median(r["scoringVariants"][GOAL]["estimatedDifficulty"] for r in rows), int(level) + .5, abs_tol=1e-9):
                raise ValueError("Final supported median moved away from its midpoint.")
    counts = payload["summary"]["method"]["sssplusTailPolicy"]["counts"]
    if counts["finalAbsAtLeast2"] > 10 or counts["displayedAbsAtLeast2"] > 10 or counts["finalAbsAbove2_5"]:
        raise ValueError("Final calibration exceeded the global tail budget.")
    return base


def apply_median_calibration(base: Mapping[str, Any], *, policy_version: int = POLICY_VERSION) -> dict:
    if base.get("schemaVersion") != 52:
        raise ValueError("Final median calibration requires the frozen schema-52 SSS+ candidate.")
    validate_sssplus_tiers(base)
    result = _apply(base, policy_version=policy_version)
    validate_median_calibration(result, base)
    return result
