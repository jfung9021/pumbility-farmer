"""Schema-54 local continuous rare effects and smooth, midpoint-centered spread.

The schema-53 payload is recoverable byte-for-byte as canonical JSON. Historical
fits and snapshots stay frozen; only official S/D20+ SSS+ projections are replaced.
"""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from statistics import median
from typing import Any, Mapping

from official_cross_level_calibration import inverse_chart_cost
from sss_scoring import MODES, RANK_FIELDS, _derived, payload_sha256
from sssplus_final_curve import inverse_final_cost
from sssplus_median_calibration import (_apply as apply_final_median,
    recover_median_baseline, validate_median_calibration)
from sssplus_tiers import _recompute_means
from target_tail_policy import (INTERVAL_METHOD, TAIL_METHOD, TAIL_PARAMETERS,
    finite, select_exceptions, tail_value)
from tier_postprocessing import compress_difficulty, compression_factor

SCHEMA_VERSION = 54
NAMESPACE = "sssplusSpreadPolicy"
STAGE = "sssplusSpreadBaseline"
GOAL = "sssplus"
METHOD = "sssplus-continuous-rare-asinh-spread-v1"
SPREAD_METHOD = "midpoint-asinh-pre-tail-v1"
SCALE = .55
BOUNDARY_POLICY = {"version": 1, "method": "one-sided-conditional-score-gap-v1",
    "minimumRecords": 200, "minimumGapToValidationMae": 1.25,
    "slopeMultipliers": [.75, 1., 1.25], "endpointAdjustment": "toward-target-by-mode-heldout-mae",
    "minimumStableDistance": 2., "distanceCeiling": 2., "formalInterval": False,
    "interpretation": "conditional-sensitivity-diagnostic-not-confidence-interval"}
FIELDS = ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High")
STAGE_ORDER = ["recover-frozen-refined-latent", "replace-rare-band-with-conditional-posterior",
    "supported-folder-median-anchors", "final-v3-curve", "residual-median-offset",
    "single-midpoint-compression", "midpoint-asinh-spread", "bounded-global-tail-policy",
    "component-mean-and-ranks"]


def spread_value(value: float | None, level: int, *, scale: float = SCALE) -> float | None:
    if value is None:
        return None
    if not finite(value) or not finite(scale) or scale <= 0:
        raise ValueError("Spread projection requires finite values and a positive scale.")
    midpoint = level + .5
    return midpoint + scale * math.asinh((value - midpoint) / scale)


def boundary_exception_value(value: float | None, level: int) -> float | None:
    """Capped one-sided evidence gets the more conservative two-point ceiling."""
    return None if value is None else level+.5+max(-2., min(2., value-level-.5))


def _without_ranks(value: dict) -> dict:
    result = copy.deepcopy(value)
    for field in RANK_FIELDS:
        result.pop(field, None)
        result.get("initialFit", {}).pop(field, None)
    return result


def _adjusted_base(base53: Mapping[str, Any], adjustments: Mapping[str, Any]) -> dict:
    base = recover_median_baseline(base53)
    curves = base["summary"]["method"]["sssplusScoring"]["calibration"]["official"]
    found = set()
    for mode, key in MODES:
        for row in base[key]:
            cid = str(row["chartId"])
            if cid not in adjustments:
                continue
            metric = row["sssplusCenteringBaseline"]["scoring"]
            if row["level"] < 20 or metric.get("scoringRefinement", {}).get("branch") != "rare-target":
                raise ValueError("Continuous rare replacement is restricted to official rare-target charts.")
            adjustment = adjustments[cid]
            effect = adjustment.get("effect")
            meta = adjustment.get("metadata", {})
            if not finite(effect) or meta.get("posteriorEffect") != effect:
                raise ValueError("Rare replacement requires a finite posterior effect and matching provenance.")
            numeric = ("priorMean", "priorVariance", "weightedSuccesses", "expectedAtPosterior",
                "likelihoodScore", "priorScore", "solverResidual", "observations")
            if (meta.get("method") != "fixed-ability-pooled-prior-chart-v1" or meta.get("version") != 1
                    or meta.get("conditionalOnly") is not True or meta.get("intervals") != "unavailable-new-conditional-estimator"
                    or not all(finite(meta.get(field)) for field in numeric) or meta["priorVariance"] <= 0
                    or meta["observations"] <= 0 or abs(meta["solverResidual"]) > 1e-7
                    or not math.isclose(meta["priorScore"], (effect-meta["priorMean"])/meta["priorVariance"], abs_tol=1e-8)
                    or not math.isclose(meta["likelihoodScore"], meta["expectedAtPosterior"]-meta["weightedSuccesses"], abs_tol=1e-8)
                    or not math.isclose(meta["solverResidual"], meta["likelihoodScore"]-meta["priorScore"], abs_tol=1e-8)):
                raise ValueError("Rare replacement has invalid conditional posterior provenance.")
            raw = inverse_chart_cost(effect, curves[mode])
            metric["initialFit"].update(_derived(raw, row["level"]))
            metric.update(_derived(compress_difficulty(raw, row["level"], compression_factor(base53)), row["level"]))
            metric.update(scoringChartEffect=effect, scoringMethod="target-conditional-pooled-prior-v1",
                scoringProvisional=True, evidenceStatus="Provisional", scoringBootstrapValidReplicates=0,
                intervalMethod="unavailable-new-conditional-estimator",
                intervalUnavailableReason="New fixed-ability conditional chart estimator has no player-bootstrap interval.")
            reasons = set(metric.get("scoringProvisionalReasons", [])) - {"rare-target-upper-band-policy"}
            metric["scoringProvisionalReasons"] = sorted(reasons | {"conditional-pooled-prior-estimate"})
            for field in ("difficultyCi95Low", "difficultyCi95High", "difficultyDeltaCi95Low", "difficultyDeltaCi95High"):
                metric[field] = metric["initialFit"][field] = None
            metric["scoringChartEffectCi95Low"] = metric["scoringChartEffectCi95High"] = None
            found.add(cid)
    if found != set(adjustments):
        raise ValueError("Rare adjustment refers to a chart outside the official S/D population.")
    expected = {str(row["chartId"]) for _, key in MODES for row in base53[key]
        if row["level"] >= 20 and row["scoringVariants"][GOAL].get("scoringEstimateBranch") == "rare-target"}
    if found != expected:
        raise ValueError("Every official rare-target chart requires a continuous replacement.")
    return base


def _official_groups(payload: Mapping[str, Any]) -> dict:
    groups = defaultdict(list)
    for mode, key in MODES:
        for row in payload[key]:
            if row["level"] >= 20 and finite(row["scoringVariants"][GOAL].get("estimatedDifficulty")):
                groups[(mode, int(row["level"]))].append(row)
    return groups


def build_boundary_candidates(projected: Mapping[str, Any], base: Mapping[str, Any]) -> dict:
    """Replay score-gap sensitivity using cached parameters, without new fits."""
    median_policy = projected["summary"]["method"]["sssplusMedianCalibration"]
    gaps = base["summary"]["method"]["sssplusRefinement"]["gapCalibration"]["official"]
    candidates = {}
    for mode, key in MODES:
        curve = median_policy["calibration"][mode]
        gap_fit = gaps.get(mode, {})
        for row in projected[key]:
            if row["level"] < 20:
                continue
            metric, level, cid = row["scoringVariants"][GOAL], row["level"], str(row["chartId"])
            ref = metric.get("scoringRefinement", {})
            branch = ref.get("branch")
            if branch not in ("all-target", "no-target"):
                continue
            boundary, board, local_range = ref.get("boundary", {}), ref.get("fullBoard", {}), ref.get("range", {})
            point = metric.get("estimatedDifficulty")
            delta = None if point is None else point-level-.5
            gap, mae = boundary.get("gapPoints"), gap_fit.get("validationMeanAbsoluteError")
            ratio = gap/mae if finite(gap) and finite(mae) and mae > 0 else None
            sources = []
            for source, state in sorted(metric.get("scoringSourceStates", {}).items()):
                if state.get("status") == "available":
                    cutoff = state.get("cutoffScore")
                    sources.append({"source": source, "records": state.get("rawRowCount", 0),
                        "cutoffScore": cutoff, "possiblyTruncated": bool(state.get("possiblyTruncated")),
                        "sameSide": finite(cutoff) and (cutoff >= 995000 if branch == "all-target" else cutoff < 995000)})
            slopes = []
            slope = gap_fit.get("pointsPerLatentUnit")
            if finite(slope) and slope > 0:
                slopes.extend({"pointsPerLatentUnit": slope*factor, "kind": "multiplier", "multiplier": factor}
                              for factor in BOUNDARY_POLICY["slopeMultipliers"])
            slopes.extend({"pointsPerLatentUnit": candidate["pointsPerLatentUnit"], "kind": "cached-robust-scale",
                           "softL1Scale": candidate.get("softL1Scale")}
                          for candidate in gap_fit.get("candidates", [])
                          if finite(candidate.get("pointsPerLatentUnit")) and candidate["pointsPerLatentUnit"] > 0)
            folder = median_policy["residualFolders"][mode][str(level)]
            direction = -1 if branch == "all-target" else 1
            stress = []
            if curve and finite(gap) and finite(mae) and finite(boundary.get("edgeLatent")):
                for candidate in slopes:
                    for endpoint_gap in (gap, max(0., gap-mae)):
                        latent = boundary["edgeLatent"] + direction*endpoint_gap/candidate["pointsPerLatentUnit"]
                        raw = inverse_final_cost(latent, curve)+folder["initialShift"]
                        difficulty = compress_difficulty(raw, level, compression_factor(base))
                        stress.append({**candidate, "gapPoints": endpoint_gap, "difficulty": difficulty,
                            "distance": direction*(difficulty-level-.5)})
            stable = bool(stress) and all(s["distance"] >= 2 for s in stress)
            reasons = []
            if board.get("records", 0) < 200:
                reasons.append("fewer-than-200-records")
            if metric.get("scoringComparability") not in ("comparable", "same", "single-source-known"):
                reasons.append("unknown-or-changed-chart")
            if not local_range.get("supported") or local_range.get("kind") != "observed" or local_range.get("donorLevels") != [level]:
                reasons.append("unsupported-own-folder-range")
            if folder["basis"] != "folder":
                reasons.append("unsupported-derived-folder-centering")
            if not gap_fit.get("supported"):
                reasons.append("unsupported-mode-gap-calibration")
            if delta is None or direction*delta < 2:
                reasons.append("inside-two-point-boundary-or-opposite-side")
            if ratio is None or ratio <= 1.25:
                reasons.append("gap-not-above-1.25-times-validation-mae")
            if not sources or not all(source["records"] >= 200 and source["sameSide"] for source in sources):
                reasons.append("source-cutoff-support-unavailable-or-opposite-side")
            if not stable:
                reasons.append("conditional-sensitivity-not-stably-beyond-two")
            minimum = min((s["distance"] for s in stress), default=None)
            candidates[cid] = {"eligible": not reasons, "strength": minimum if minimum is not None else 0.,
                "rejectionReasons": reasons, "exceptionBasis": BOUNDARY_POLICY["method"],
                "oneSidedEvidence": {"records": board.get("records", 0), "gapPoints": gap,
                    "validationMAE": mae, "minimumGapRatio": 1.25, "gapToMAERatio": ratio,
                    "calibrationSupported": bool(gap_fit.get("supported")), "sourceSupport": sources,
                    "conditionalSensitivity": {"method": "fixed-curve-gap-slope-and-endpoint-v1",
                        "slopeMultipliers": BOUNDARY_POLICY["slopeMultipliers"], "endpointAdjustmentPoints": mae,
                        "minimumDistance": minimum, "sameSide": bool(stress) and all(s["distance"] > 0 for s in stress),
                        "beyondTwo": stable, "scenarios": stress}, "formalInterval": False,
                    "sourceRemoval": "unavailable-single-known-source" if len(sources) == 1 else "source-cutoff-same-side-only",
                    "possiblyTruncated": bool(board.get("possiblyTruncated"))}}
    return candidates


def _select_boundary_exceptions(payload: Mapping[str, Any], candidates: Mapping[str, Any],
                                ordinary_selected: set[str]) -> tuple[set[str], dict[str, Any]]:
    """Choose only contiguous outer tie blocks; a shared budget never splits ties."""
    selected = set(ordinary_selected)
    evidence = copy.deepcopy(dict(candidates))
    tails = []
    for (_, level), rows in sorted(_official_groups(payload).items()):
        ordered = sorted(rows, key=lambda r: (r["scoringVariants"][GOAL]["estimatedDifficulty"], str(r["chartId"])))
        middle = {ordered[(len(ordered)-1)//2]["scoringVariants"][GOAL]["estimatedDifficulty"],
                  ordered[len(ordered)//2]["scoringVariants"][GOAL]["estimatedDifficulty"]}
        for direction in (-1, 1):
            outer = [r for r in ordered if direction*(r["scoringVariants"][GOAL]["estimatedDifficulty"]-level-.5) > 0]
            outer.sort(key=lambda r: (-abs(r["scoringVariants"][GOAL]["estimatedDifficulty"]-level-.5), str(r["chartId"])))
            blocks, start = [], 0
            while start < len(outer):
                stop = start + 1
                value = outer[start]["scoringVariants"][GOAL]["estimatedDifficulty"]
                while stop < len(outer) and outer[stop]["scoringVariants"][GOAL]["estimatedDifficulty"] == value:
                    stop += 1
                block = outer[start:stop]
                if value in middle or not all(evidence.get(str(r["chartId"]), {}).get("eligible", False) for r in block):
                    break
                blocks.append(block)
                start = stop
            if blocks:
                tails.append(blocks)
    remaining = max(0, 10-len(selected))
    while tails and remaining:
        tails.sort(key=lambda blocks: (-min(evidence[str(r["chartId"])]["strength"] for r in blocks[0]),
                                       min(str(r["chartId"]) for r in blocks[0])))
        blocks = tails.pop(0)
        block = blocks.pop(0)
        new = {str(r["chartId"]) for r in block}-selected
        if len(new) > remaining:
            continue
        selected.update(new)
        remaining -= len(new)
        if blocks:
            tails.append(blocks)
    return selected, evidence


def _apply(base53: Mapping[str, Any], adjustments: Mapping[str, Any], rare_policy: Mapping[str, Any],
           boundary_candidates: Mapping[str, Any], boundary_policy: Mapping[str, Any]) -> dict:
    projected = apply_final_median(_adjusted_base(base53, adjustments), policy_version=3)
    result = copy.deepcopy(base53)
    result["schemaVersion"] = SCHEMA_VERSION
    median_policy = copy.deepcopy(projected["summary"]["method"]["sssplusMedianCalibration"])
    official_ids = {str(r["chartId"]) for _, key in MODES for r in projected[key] if r["level"] >= 20}
    for _, key in MODES:
        for row in projected[key]:
            if row["level"] >= 20:
                row["scoringVariants"][GOAL] = copy.deepcopy(row["sssplusTailBaseline"]["scoring"])
                if str(row["chartId"]) in adjustments:
                    metric = row["scoringVariants"][GOAL]
                    metric["scoringProvisionalReasons"] = sorted(set(metric["scoringProvisionalReasons"])
                        - {"rare-target-upper-band-policy"} | {"conditional-pooled-prior-estimate"})
    # Select traditional two-sided exceptions against the pre-spread evidence.
    ordinary_selected, evidence = select_exceptions(projected, goal=GOAL)
    ordinary_selected &= official_ids
    computed_boundary = build_boundary_candidates(projected, base53)
    if boundary_candidates and dict(boundary_candidates) != computed_boundary:
        raise ValueError("One-sided candidate evidence differs from its frozen score-gap sensitivity replay.")
    if boundary_policy and dict(boundary_policy) != BOUNDARY_POLICY:
        raise ValueError("Unsupported one-sided boundary exception policy.")
    boundary_candidates, boundary_policy = computed_boundary, BOUNDARY_POLICY
    selected, boundary_evidence = _select_boundary_exceptions(projected, boundary_candidates, ordinary_selected)
    for _, key in MODES:
        for row, old, rebuilt in zip(result[key], base53[key], projected[key]):
            row[STAGE] = {"scoring": copy.deepcopy(old["scoringVariants"][GOAL]),
                "pumbility": copy.deepcopy(old["pumbilityVariants"][GOAL]),
                "tailBaseline": copy.deepcopy(old["sssplusTailBaseline"])}
            if row["level"] < 20:
                continue
            level, cid = row["level"], str(row["chartId"])
            metric = copy.deepcopy(rebuilt["scoringVariants"][GOAL])
            before = {field: metric.get(field) for field in FIELDS}
            detail = {"version": 1, "method": SPREAD_METHOD, "scale": SCALE, "midpoint": level+.5,
                "preSpreadDifficulty": before["estimatedDifficulty"],
                "preSpreadDifficultyCi95Low": before["difficultyCi95Low"],
                "preSpreadDifficultyCi95High": before["difficultyCi95High"],
                "rare": copy.deepcopy(adjustments.get(cid, {}).get("metadata")),
                "boundaryEvidence": copy.deepcopy(boundary_evidence.get(cid))}
            for field in FIELDS:
                metric[field] = spread_value(before[field], level)
            detail.update(spreadDifficulty=metric["estimatedDifficulty"],
                spreadDifficultyCi95Low=metric["difficultyCi95Low"], spreadDifficultyCi95High=metric["difficultyCi95High"])
            exceptional = cid in selected
            tail_detail = {"version": 1, "method": TAIL_METHOD, "midpoint": level+.5,
                "preTailDifficulty": metric["estimatedDifficulty"],
                "preTailDifficultyCi95Low": metric["difficultyCi95Low"],
                "preTailDifficultyCi95High": metric["difficultyCi95High"],
                "ordinaryDifficulty": tail_value(metric["estimatedDifficulty"], level), "exceptionSelected": exceptional,
                **copy.deepcopy(evidence.get(cid, {"exceptionEligible": False, "rejectionReasons": ["unrated"],
                    "conservativeDistance": None, "protectedMedian": False}))}
            if cid in boundary_evidence:
                tail_detail["boundaryEvidence"] = copy.deepcopy(boundary_evidence[cid])
                if exceptional:
                    tail_detail.update(exceptionEligible=True, rejectionReasons=[],
                        conservativeDistance=boundary_evidence[cid].get("strength"))
            for field in FIELDS:
                if exceptional and cid in boundary_evidence:
                    metric[field] = boundary_exception_value(before[field], level)
                else:
                    metric[field] = tail_value(metric.get(field), level, exceptional=exceptional)
            metric.update(_derived(metric["estimatedDifficulty"], level))
            for side in ("Low", "High"):
                point = metric[f"difficultyCi95{side}"]
                metric[f"difficultyDeltaCi95{side}"] = None if point is None else point-level-.5
            if metric["difficultyCi95Low"] is not None:
                metric["intervalMethod"] = "conditional-asinh-tail-fixed-calibration-no-anchor-uncertainty-v1"
            metric["scoringFolderCentering"]["effectiveRareBand"] = None
            metric["scoringSpreadPolicy"] = detail
            tail_detail.update(finalDifficulty=metric["estimatedDifficulty"],
                finalDifficultyCi95Low=metric["difficultyCi95Low"], finalDifficultyCi95High=metric["difficultyCi95High"])
            metric["scoringTailPolicy"] = tail_detail
            row["scoringVariants"][GOAL] = metric
            row["sssplusTailBaseline"] = {"scoring": copy.deepcopy(rebuilt["scoringVariants"][GOAL]),
                "pumbility": copy.deepcopy(rebuilt["pumbilityVariants"][GOAL])}
    for rows in _official_groups(result).values():
        values = [r["scoringVariants"][GOAL]["estimatedDifficulty"] for r in rows]
        for row in rows:
            row["scoringVariants"][GOAL]["scoringTailPolicy"]["finalFolderRange"] = {"lower": min(values), "upper": max(values)}
    _recompute_means(result)
    # Below-20 values and their evidence contracts are frozen; global ranks may move.
    for _, key in MODES:
        for row, old in zip(result[key], base53[key]):
            if row["level"] < 20:
                for variant in ("scoringVariants", "pumbilityVariants"):
                    ranks = {field: row[variant][GOAL].get(field) for field in RANK_FIELDS}
                    initial_ranks = {field: row[variant][GOAL].get("initialFit", {}).get(field) for field in RANK_FIELDS}
                    row[variant][GOAL] = copy.deepcopy(old[variant][GOAL])
                    row[variant][GOAL].update(ranks)
                    row[variant][GOAL].get("initialFit", {}).update(initial_ranks)
    official = [r for rows in _official_groups(result).values() for r in rows]
    pre = [r["scoringVariants"][GOAL]["scoringTailPolicy"]["preTailDifficulty"]-r["level"]-.5 for r in official]
    after = [r["scoringVariants"][GOAL]["estimatedDifficulty"]-r["level"]-.5 for r in official]
    counts = {"ratedCharts": len(after), "changedCharts": sum(a != b for a,b in zip(pre, after)),
        "preTailAbsAbove1_5": sum(abs(d)>1.5 for d in pre), "preTailAbsAtLeast2": sum(abs(d)>=2 for d in pre),
        "finalAbsAbove1_5": sum(abs(d)>1.5 for d in after), "finalAbsAtLeast2": sum(abs(d)>=2 for d in after),
        "finalAbsAbove2_5": sum(abs(d)>2.5 for d in after),
        "displayedAbsAtLeast2": sum(abs(math.trunc((r["scoringVariants"][GOAL]["estimatedDifficulty"]*10+1e-9))/10-r["level"]-.5)>=2 for r in official),
        "preTailMinDelta": min(pre, default=None), "preTailMaxDelta": max(pre, default=None),
        "finalMinDelta": min(after, default=None), "finalMaxDelta": max(after, default=None)}
    tail = {**TAIL_PARAMETERS, "targetKey": GOAL, "selectedChartIds": sorted(selected), "counts": counts,
        "intervalMethod": INTERVAL_METHOD, "selection": "continuous-spread-outer-block-global-budget-v1"}
    result["summary"]["method"]["sssplusTailPolicy"] = tail
    result["summary"]["method"][NAMESPACE] = {"version": 1, "method": METHOD, "baseSchemaVersion": 53,
        "basePayloadSha256": payload_sha256(base53), "localOnly": True, "target": 995000,
        "source": "official", "minimumLevel": 20, "scale": SCALE, "compressionApplications": 1,
        "compressionFactor": compression_factor(base53), "stageOrder": STAGE_ORDER, "dataFetches": 0, "fullPlayerRefits": 0,
        "rareAdjustments": copy.deepcopy(dict(adjustments)), "rarePolicy": copy.deepcopy(dict(rare_policy)),
        "boundaryCandidates": copy.deepcopy(dict(boundary_candidates)), "boundaryPolicy": copy.deepcopy(dict(boundary_policy)),
        "medianCalibration": median_policy, "previousTailPolicy": copy.deepcopy(base53["summary"]["method"]["sssplusTailPolicy"]),
        "tailPolicy": tail}
    return result


def recover_spread_baseline(payload: Mapping[str, Any]) -> dict:
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("Spread recovery requires schema 54.")
    result = copy.deepcopy(payload)
    result["schemaVersion"] = 53
    policy = result["summary"]["method"].pop(NAMESPACE)
    result["summary"]["method"]["sssplusTailPolicy"] = policy["previousTailPolicy"]
    for _, key in MODES:
        for row in result[key]:
            original = row.pop(STAGE)
            row["scoringVariants"][GOAL], row["pumbilityVariants"][GOAL] = original["scoring"], original["pumbility"]
            row["sssplusTailBaseline"] = original["tailBaseline"]
    return result


def validate_sssplus_spread(payload: Mapping[str, Any], frozen: Mapping[str, Any] | None = None) -> dict:
    base = recover_spread_baseline(payload)
    if frozen is not None and base != frozen:
        raise ValueError("Spread recovery differs from the immutable schema-53 baseline.")
    validate_median_calibration(base)
    policy = payload["summary"]["method"][NAMESPACE]
    expected = _apply(base, policy["rareAdjustments"], policy["rarePolicy"], policy["boundaryCandidates"], policy["boundaryPolicy"])
    if payload != expected:
        raise ValueError("Spread effects, medians, intervals, exceptions, composites, ranks or preserved fields differ.")
    for _, key in MODES:
        for row, old in zip(payload[key], base[key]):
            if row["level"] < 20:
                for field in ("scoringVariants", "pumbilityVariants"):
                    if _without_ranks(row[field][GOAL]) != _without_ranks(old[field][GOAL]):
                        raise ValueError("Spread changed a below-20 value or evidence contract.")
    for (mode, level), rows in _official_groups(payload).items():
        folder = policy["medianCalibration"]["residualFolders"][mode][str(level)]
        values = [r["scoringVariants"][GOAL]["estimatedDifficulty"] for r in rows]
        if folder["basis"] == "folder" and not math.isclose(median(values), level+.5, abs_tol=1e-9):
            raise ValueError("Spread moved a supported folder median.")
        ordered = sorted(rows, key=lambda r: (r["scoringVariants"][GOAL]["scoringSpreadPolicy"]["preSpreadDifficulty"], str(r["chartId"])))
        projected = [r["scoringVariants"][GOAL]["estimatedDifficulty"] for r in ordered]
        if projected != sorted(projected):
            raise ValueError("Spread or exception selection inverted a chart ordering.")
    counts = policy["tailPolicy"]["counts"]
    if counts["finalAbsAtLeast2"] > 10 or counts["displayedAbsAtLeast2"] > 10 or counts["finalAbsAbove2_5"]:
        raise ValueError("Spread exceeded the global exception budget.")
    return base


def apply_sssplus_spread(base: Mapping[str, Any], *, rare_adjustments: Mapping[str, Any],
                         rare_policy: Mapping[str, Any], boundary_candidates: Mapping[str, Any] | None = None,
                         boundary_policy: Mapping[str, Any] | None = None) -> dict:
    if base.get("schemaVersion") != 53 or base["summary"]["method"]["sssplusMedianCalibration"].get("version") != 3:
        raise ValueError("Spread requires the frozen schema-53 policy-v3 baseline.")
    validate_median_calibration(base)
    result = _apply(base, rare_adjustments, rare_policy, boundary_candidates or {}, boundary_policy or {})
    validate_sssplus_spread(result, base)
    return result
