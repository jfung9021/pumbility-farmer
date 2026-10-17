"""Frozen-range second pass for locally fitted SSS difficulty.

The first-pass Rasch effects, median calibration and bootstrap are never refit by
the second pass. A deterministic point replay only recovers omitted private
player abilities. This module returns no player identities in public metadata.
"""
from __future__ import annotations

import copy
import hashlib
import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

import numpy as np

from scoring_targets import SSS, ScoringTarget
from official_cross_level_calibration import chart_cost, inverse_chart_cost
from sss_scoring import MODES, SCORE_TARGET, _derived, payload_sha256
from sss_scoring_model import _fit, _prepare, _sigmoid
from tier_postprocessing import FIT_FIELDS, compress_difficulty

REFINEMENT_VERSION = 2
NO_SSS_ENDPOINT_POLICY = {"method": "mean-top-available", "maximumScores": 3, "minimumScores": 1,
                          "selection": "deduplicated-full-board-before-history-filter"}
BOUNDARY_METHOD = "sss-boundary-score-gap-v1"
RARE_METHOD = "sss-rare-success-upper-band-v1"
GAP_POLICY = "positive-through-origin-soft-l1-chart-holdout-v1"
INTERVAL_REASON = "Second-pass rule has no formal interval; first-pass bootstrap does not describe this estimate."


def _finite(value: Any) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def classify_board(board: Mapping[str, float], *, possibly_truncated: bool = False, score_target: int = SCORE_TARGET, generic_branches: bool = False) -> dict[str, Any]:
    """Use the entire already selected board, independently of model eligibility."""
    values = list(board.values())
    if any(not _finite(value) or not 0 <= value <= 1_000_000 for value in values):
        raise ValueError("SSS boundary observations must be valid normalized best scores.")
    successes = sum(value >= score_target for value in values)
    branch = "no-data" if not values else "no-sss" if not successes else "all-sss" if successes == len(values) else "mixed"
    if generic_branches:
        branch = {"no-sss": "no-target", "all-sss": "all-target"}.get(branch, branch)
    result = {"branch": branch, "records": len(values), "successes": successes,
            "belowTarget": len(values) - successes, "highestScore": max(values) if values else None,
            "lowestScore": min(values) if values else None, "possiblyTruncated": possibly_truncated}
    if branch in ("no-sss", "no-target"):
        result["topScores"] = sorted(values, reverse=True)[:3]
    return result


def freeze_ranges(payload: Mapping[str, Any], *, goal: str = "sss") -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
    """Freeze actual finite first-pass edges, then fill sparse folders by offsets."""
    result: dict[str, Any] = {source: {mode: {} for mode, _ in MODES} for source in ("submitted", "official")}
    for mode, key in MODES:
        groups: dict[tuple[str, int], list[tuple[str, float]]] = defaultdict(list)
        requested: dict[str, set[int]] = defaultdict(set)
        for row in payload.get(key, []):
            source, level = ("official" if row["level"] >= 20 else "submitted"), int(row["level"])
            metric = row["scoringVariants"][goal]
            requested[source].add(level)
            if metric.get("evidenceStatus") != "Unrated" and _finite(metric.get("estimatedDifficulty")):
                groups[source, level].append((str(row["chartId"]), metric["estimatedDifficulty"]))
        for source, levels in requested.items():
            supported = {}
            for level in sorted(levels):
                population = sorted(groups[source, level])
                if len(population) < 5:
                    continue
                lower, upper = min(v for _, v in population), max(v for _, v in population)
                if upper <= lower:
                    continue
                supported[level] = {"source": source, "mode": mode, "level": level,
                    "lower": lower, "upper": upper, "kind": "observed", "supported": True,
                    "referenceChartIds": [cid for cid, _ in population], "donorLevels": [level],
                    "lowerOffset": lower - level - .5, "upperOffset": upper - level - .5,
                    "firstPassRatedCharts": len(population), "provisional": False}
            for level in sorted(levels):
                if level in supported:
                    record = supported[level]
                elif supported:
                    donors = sorted(supported)
                    below = [donor for donor in donors if donor < level]
                    above = [donor for donor in donors if donor > level]
                    selected = [below[-1], above[0]] if below and above else [min(donors, key=lambda donor: abs(level - donor))]
                    ratio = (level - selected[0]) / (selected[-1] - selected[0]) if len(selected) == 2 else 0.
                    offsets = {field: supported[selected[0]][field] + ratio * (supported[selected[-1]][field] - supported[selected[0]][field])
                               for field in ("lowerOffset", "upperOffset")}
                    record = {"source": source, "mode": mode, "level": level,
                        "lower": level + .5 + offsets["lowerOffset"], "upper": level + .5 + offsets["upperOffset"],
                        "kind": "interpolated" if len(selected) == 2 else "nearest", "supported": False,
                        "referenceChartIds": sorted({cid for donor in selected for cid in supported[donor]["referenceChartIds"]}),
                        "donorLevels": selected, **offsets, "firstPassRatedCharts": len(groups[source, level]),
                        "provisional": True}
                else:
                    continue
                result[source][mode][str(level)] = copy.deepcopy(record)
    return result


def recover_point_fit(boards: Mapping[str, Mapping[str, float]], cached: Mapping[str, Any], *, score_target: int = SCORE_TARGET) -> dict[str, Any]:
    """Replay exactly one point fit and prove that its chart effects match cache."""
    data = _prepare(boards, score_target)
    if not len(data["y"]):
        return {"data": data, "fit": None, "diagnostics": {"converged": False, "reason": "no-main-component"}}
    penalty = cached.get("policy", {}).get("selectedRegularization")
    if not _finite(penalty) or penalty <= 0:
        raise ValueError("Frozen SSS fit has no valid selected regularization.")
    fitted = _fit(data["p"], data["c"], data["y"], len(data["players"]), len(data["mainCharts"]), penalty)
    discrepancies = [abs(float(fitted["effect"][index]) - cached["charts"][cid]["effect"])
                     for index, cid in enumerate(data["mainCharts"])
                     if _finite(cached.get("charts", {}).get(cid, {}).get("effect"))]
    maximum = max(discrepancies, default=0.)
    if not fitted["converged"] or maximum > 1e-7 or len(discrepancies) != len(data["mainCharts"]):
        raise ValueError(f"Frozen SSS point replay differs from its cached fit ({maximum:g}).")
    return {"data": data, "fit": fitted, "diagnostics": {"converged": True,
        "maximumChartEffectDifference": maximum, "charts": len(data["mainCharts"]),
        "players": len(data["players"]), "records": len(data["y"]), "regularization": penalty,
        "bootstrapRefits": 0, "pointFits": 1}}


def _positive_slope(x: np.ndarray, y: np.ndarray, weights: np.ndarray, scale: float) -> float | None:
    from scipy.optimize import least_squares

    if not len(x) or float(np.sum(weights * x * x)) <= 1e-12:
        return None
    initial = max(1., float(np.sum(weights * x * y) / np.sum(weights * x * x)))
    fitted = least_squares(lambda value: np.sqrt(weights) * (value[0] * x - y), [initial],
                           bounds=([1e-9], [np.inf]), loss="soft_l1", f_scale=scale,
                           ftol=1e-11, xtol=1e-11, gtol=1e-11)
    slope = float(fitted.x[0])
    return slope if fitted.success and math.isfinite(slope) and slope > 1e-6 else None


def fit_gap_calibration(boards: Mapping[str, Mapping[str, float]], metadata: Mapping[str, Any],
                        cached: Mapping[str, Any], context: Mapping[str, Any], *, seed: int = 20261018, target: ScoringTarget = SSS) -> dict[str, Any]:
    """Select robust score conversion on whole held-out mixed charts only."""
    data, fitted = context["data"], context["fit"]
    policy = {"method": GAP_POLICY, "scoreRange": [target.near_target_minimum, 999999], "excludePerfectScores": True,
        "minimumChartContributors": 20, "minimumPerOutcome": 5, "minimumCharts": 7,
        "minimumRecords": 100, "heldOutFraction": .2, "minimumHeldOutCharts": 2,
        "candidateSoftL1Scales": [500., 1000., 2000.], "chartEqualWeight": True,
        "origin": "zero-score-deficit-at-zero-chart-minus-player-effect", "seed": seed}
    result = {"supported": False, "pointsPerLatentUnit": None, "policy": policy,
        "trainingChartIds": [], "validationChartIds": [], "reason": None}
    if fitted is None:
        result["reason"] = "No converged frozen shared-player fit."
        return result
    eligible = {cid for cid, estimate in cached["charts"].items()
                if estimate.get("ratingEligible") and estimate.get("usableContributors", 0) >= 20
                and min(estimate.get("targetSuccesses", 0), estimate.get("belowTarget", 0)) >= 5
                and metadata.get(cid, {}).get("anchorEligible", False)}
    selected = np.array([cid in eligible and target.near_target_minimum <= boards[cid][player] <= 999999
                         for cid, player, _ in data["records"]], dtype=bool)
    chart_indices = data["c"][selected]
    chart_ids = sorted({data["mainCharts"][index] for index in chart_indices})
    result.update(supportedCharts=len(chart_ids), records=int(sum(selected)))
    if len(chart_ids) < 7 or int(sum(selected)) < 100:
        result["reason"] = "Score-gap calibration requires seven supported mixed charts and 100 uncapped near-target records."
        return result
    # Hash ordering is stable if input mappings change insertion order.
    shuffled = sorted(chart_ids, key=lambda cid: hashlib.sha256(f"{seed}:{cid}".encode()).digest())
    held_ids = set(shuffled[:max(2, int(len(shuffled) * .2))])
    validation = np.asarray([data["mainCharts"][index] in held_ids for index in chart_indices], dtype=bool)
    x = fitted["effect"][data["c"][selected]] - fitted["ability"][data["p"][selected]]
    y = np.asarray([target.score - boards[cid][player] for cid, player, _ in data["records"]], dtype=float)[selected]
    # Each chart contributes the same total calibration weight.
    counts = np.bincount(chart_indices, minlength=len(data["mainCharts"]))
    weights = 1 / counts[chart_indices]
    validation_weights = weights[validation]
    baseline = float(np.average(np.abs(y[validation]), weights=validation_weights))
    candidates = []
    for scale in policy["candidateSoftL1Scales"]:
        slope = _positive_slope(x[~validation], y[~validation], weights[~validation], scale)
        error = None if slope is None else float(np.average(np.abs(y[validation] - slope * x[validation]), weights=validation_weights))
        candidates.append({"softL1Scale": scale, "pointsPerLatentUnit": slope, "validationMeanAbsoluteError": error})
    result.update(trainingChartIds=sorted(set(chart_ids) - held_ids), validationChartIds=sorted(held_ids),
        validationRecords=int(sum(validation)), trainingRecords=int(sum(~validation)),
        validationZeroDeficitMeanAbsoluteError=baseline, candidates=candidates,
        validationPolicy="chart-heldout-slope-fit-fixed-first-pass-effects")
    valid = [entry for entry in candidates if entry["validationMeanAbsoluteError"] is not None]
    if not valid:
        result["reason"] = "No positive robust score-gap conversion converged."
        return result
    chosen = min(valid, key=lambda entry: (entry["validationMeanAbsoluteError"], entry["softL1Scale"]))
    if chosen["validationMeanAbsoluteError"] >= baseline:
        result["reason"] = "Held-out score-gap conversion did not improve on the zero-deficit baseline."
        return result
    slope = _positive_slope(x, y, weights, chosen["softL1Scale"])
    if slope is None:
        result["reason"] = "Final positive robust score-gap conversion did not converge."
        return result
    result.update(supported=True, pointsPerLatentUnit=slope, selectedSoftL1Scale=chosen["softL1Scale"],
        validationMeanAbsoluteError=chosen["validationMeanAbsoluteError"], finalFitChartIds=chart_ids,
        finalFitRecords=len(x), reason=None, latentRange=[float(min(x)), float(max(x))])
    return result


def boundary_estimate(branch: str, endpoint: float, level: int, frozen_range: Mapping[str, Any],
                      curve: Mapping[str, Any], points_per_latent_unit: float, *, score_target: int = SCORE_TARGET) -> dict[str, Any]:
    branch = {"no-target": "no-sss", "all-target": "all-sss"}.get(branch, branch)
    if branch not in ("no-sss", "all-sss") or not _finite(points_per_latent_unit) or points_per_latent_unit <= 0:
        raise ValueError("A boundary estimate needs a one-sided branch and positive score conversion.")
    midpoint = level + .5
    gap = score_target - endpoint if branch == "no-sss" else endpoint - score_target
    if gap < 0:
        raise ValueError("A boundary endpoint has the wrong side of the inclusive SSS threshold.")
    edge = frozen_range["upper" if branch == "no-sss" else "lower"]
    edge_initial = midpoint + 2 * (edge - midpoint)
    edge_latent = chart_cost(edge_initial, curve)
    target = edge_latent + (1 if branch == "no-sss" else -1) * gap / points_per_latent_unit
    initial = inverse_chart_cost(target, curve)
    return {"endpointScore": endpoint, "gapPoints": gap, "gapScalePointsPerLatentUnit": points_per_latent_unit,
        "gapDirection": "shortfall" if branch == "no-sss" else "surplus", "edgeFinal": edge,
        "edgeInitial": edge_initial, "edgeLatent": edge_latent, "targetLatent": target,
        "initialDifficulty": initial, "finalDifficulty": compress_difficulty(initial, level),
        "extrapolated": target < curve["anchors"][0]["cost"] or target > curve["anchors"][-1]["cost"]}


def rare_blend(successes: int, players: int) -> dict[str, float]:
    clip = lambda value: min(1., max(0., value))
    count = clip((5 - successes) / 4)
    support = clip((players - 5) / 15)
    rate = clip((.10 - successes / players) / .05) if players else 0.
    return {"countWeight": count, "supportWeight": support, "rateWeight": rate,
            "blendWeight": count * support * rate}


def conditional_chart_fit(abilities: np.ndarray, outcomes: np.ndarray, weights: np.ndarray) -> dict[str, float]:
    from scipy.optimize import brentq

    if not len(outcomes) or not 0 < sum(outcomes) < len(outcomes):
        raise ValueError("A finite conditional chart fit requires both outcomes.")
    if any(not np.all(np.isfinite(array)) for array in (abilities, outcomes, weights)) or np.any(weights <= 0):
        raise ValueError("Conditional observations must be finite with positive weights.")
    observed = float(np.sum(weights * outcomes))
    score = lambda effect: float(np.sum(weights * _sigmoid(abilities - effect))) - observed
    effect = float(brentq(score, float(min(abilities)) - 100., float(max(abilities)) + 100., xtol=1e-12))
    predicted = float(np.sum(weights * _sigmoid(abilities - effect)))
    return {"conditionalEffect": effect, "weightedSuccesses": observed,
        "conditionalExpectedSuccesses": predicted, "likelihoodResidual": predicted - observed}


def allocate_upper_band(candidates: Sequence[dict[str, Any]], frozen_range: Mapping[str, Any]) -> None:
    """Allocate tied conditional positions with mean ranks; never lower a point."""
    if not candidates:
        return
    floor, ceiling = frozen_range["upper"] - .05 * (frozen_range["upper"] - frozen_range["lower"]), frozen_range["upper"]
    ordered = sorted(candidates, key=lambda candidate: (candidate["conditionalDifficulty"], candidate["chartId"]))
    start = 0
    while start < len(ordered):
        stop = start + 1
        while stop < len(ordered) and ordered[stop]["conditionalDifficulty"] == ordered[start]["conditionalDifficulty"]:
            stop += 1
        rank = (start + 1 + stop) / 2
        position = (rank - 1) / (len(ordered) - 1) if len(ordered) > 1 else 1.
        target = floor + position * (ceiling - floor)
        for candidate in ordered[start:stop]:
            original = candidate["originalDifficulty"]
            value = original + candidate["blendWeight"] * (max(original, target) - original)
            candidate.update(upperBandLow=floor, upperBandHigh=ceiling, candidateCount=len(ordered),
                conditionalRank=rank, upperTarget=target, finalDifficulty=value,
                initialDifficulty=candidate["level"] + .5 + 2 * (value - candidate["level"] - .5))
        start = stop


def _replace_point(metric: dict[str, Any], level: int, raw: float, *, method: str, branch: str,
                   refinement: dict[str, Any], extrapolated: bool, target: ScoringTarget = SSS) -> None:
    if "scoringFirstPass" not in metric:
        metric["scoringFirstPass"] = copy.deepcopy(metric)
    metric.update(_derived(compress_difficulty(raw, level), level), scoringMethod=method,
        scoringEstimateBranch=branch, scoringRefinement=refinement, evidenceStatus="Provisional",
        scoringProvisional=True, scoringUnratedReason=None, scoringExtrapolated=extrapolated,
        intervalMethod="unavailable-second-pass-rule-v1", scoringBootstrapValidReplicates=0,
        intervalUnavailableReason=INTERVAL_REASON)
    metric["initialFit"] = {**{field: None for field in FIT_FIELDS}, **_derived(raw, level)}
    for field in ("difficultyCi95Low", "difficultyCi95High", "difficultyDeltaCi95Low", "difficultyDeltaCi95High",
                  "scoringChartEffectCi95Low", "scoringChartEffectCi95High"):
        metric[field] = None
    reasons = set(metric.get("scoringProvisionalReasons", []))
    reasons.add("second-pass-score-boundary" if method == target.boundary_method else ("rare-sss-upper-band-policy" if target.key == "sss" else "rare-target-upper-band-policy"))
    if refinement["range"]["provisional"]:
        reasons.add("borrowed-first-pass-range")
    if refinement["fullBoard"]["records"] < 5:
        reasons.add("fewer-than-five-full-board-records")
    metric["scoringProvisionalReasons"] = sorted(reasons)
    if method == target.boundary_method:
        metric.update(scoringChartEffect=None, scoringInformation=None, scoringFitConverged=False)
    else:
        metric["scoringChartEffect"] = chart_cost(raw, refinement.pop("_curve"))


def apply_sss_refinement(payload49: Mapping[str, Any], prepared: Mapping[str, Any],
                         fits: Mapping[str, Mapping[str, Any]], *, seed: int = 20261018, target: ScoringTarget = SSS) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a second-pass overlay plus private audit; caller sets schema and means."""
    if target.key == "sss" and payload49.get("schemaVersion") != 49:
        raise ValueError("SSS refinement requires a frozen schema-49 first pass.")
    result, ranges = copy.deepcopy(payload49), freeze_ranges(payload49, goal=target.key)
    for _, key in MODES:
        for row in result.get(key, []):
            metric = row["scoringVariants"][target.key]
            metric["scoringFirstPass"] = copy.deepcopy(metric)
    method = payload49["summary"]["method"][target.key + "Scoring"]
    metadata: dict[str, Any] = {"version": REFINEMENT_VERSION, "method": "frozen-range-sss-second-pass-v1",
        "firstPassSchemaVersion": 49, "firstPassPayloadSha256": payload_sha256(payload49),
        "scoreTarget": target.score, "ranges": ranges, "gapCalibration": {}, "pointReplay": {},
        "noSssEndpointPolicy": copy.deepcopy(NO_SSS_ENDPOINT_POLICY),
        "rangePolicy": {"minimumRatedCharts": 5, "edges": "actual-finite-first-pass-min-max",
            "borrow": "same-source-mode-offset-interpolation-or-nearest", "frozenBeforeSecondPass": True},
        "rarePolicy": {"successCounts": [1, 4], "upperNumericRangeFraction": .05,
            "ordering": "conditional-difficulty-mean-tie-ranks", "minimumFullWeightPlayers": 20,
            "countWeight": "clip((5-successes)/4,0,1)", "supportWeight": "clip((players-5)/15,0,1)",
            "rateWeight": "clip((.10-successes/players)/.05,0,1)"},
        "intervalPolicy": "second-pass-intervals-unavailable", "compressionApplications": 1}
    audit: dict[str, Any] = {"changed": [], "counts": {}, "transitions": [], "firstPassRanges": copy.deepcopy(ranges)}
    for source, minimum in (("submitted", 16), ("official", 20)):
        metadata["gapCalibration"][source], metadata["pointReplay"][source] = {}, {}
        for mode, key in MODES:
            cached = fits[source][mode]
            boards = {cid: board for cid, board in prepared[source]["boards"].items()
                      if prepared["catalog"][cid]["type"] == mode and prepared["catalog"][cid]["level"] >= minimum}
            context = recover_point_fit(boards, cached) if target == SSS else recover_point_fit(boards, cached, score_target=target.score)
            calibration = fit_gap_calibration(boards, prepared[source]["charts"], cached, context, seed=seed) if target == SSS else fit_gap_calibration(boards, prepared[source]["charts"], cached, context, seed=seed, target=target)
            metadata["gapCalibration"][source][mode] = calibration
            metadata["pointReplay"][source][mode] = context["diagnostics"]
            curve = method["calibration"][source][mode]
            data, point = context["data"], context["fit"]
            chart_indices = {cid: index for index, cid in enumerate(data["mainCharts"])}
            weights = 1 / np.bincount(data["p"])[data["p"]] if point is not None else np.array([])
            candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
            candidate_rows = {}
            counts = defaultdict(int)
            for row in result.get(key, []):
                if (row["level"] >= 20) != (source == "official"):
                    continue
                cid, metric, level = str(row["chartId"]), row["scoringVariants"][target.key], int(row["level"])
                full = classify_board(boards.get(cid, {}), possibly_truncated=bool(metric.get("scoringPossiblyTruncated")), score_target=target.score)
                branch = full.pop("branch")
                counts[branch] += 1
                estimate = cached["charts"].get(cid, {})
                eligible = {"records": estimate.get("usableContributors", 0), "successes": estimate.get("targetSuccesses", 0),
                            "belowTarget": estimate.get("belowTarget", 0)}
                frozen = ranges[source][mode].get(str(level))
                detail = {"version": REFINEMENT_VERSION, "branch": branch, "fullBoard": full,
                          "modelEligible": eligible, "range": copy.deepcopy(frozen)}
                if target.key != "sss":
                    metric["scoringRefinement"] = copy.deepcopy(detail)
                    metric["scoringEstimateBranch"] = branch
                if branch in ("no-sss", "all-sss"):
                    if not curve or not frozen or not calibration["supported"]:
                        detail["unratedCalibrationReason"] = "Frozen SSS median-anchor calibration is unavailable." if not curve else "No supported same-source/mode first-pass range donor." if not frozen else calibration["reason"]
                        metric.update(scoringEstimateBranch="unsupported-calibration", scoringRefinement=detail,
                            scoringUnratedReason=detail["unratedCalibrationReason"])
                        continue
                    endpoint = sum(full["topScores"]) / len(full["topScores"]) if branch == "no-sss" else full["lowestScore"]
                    boundary = boundary_estimate(branch, endpoint, level, frozen, curve, calibration["pointsPerLatentUnit"], score_target=target.score)
                    if branch == "no-sss":
                        boundary.update(endpointPolicy="mean-top-3-available-v1", endpointScoreCount=len(full["topScores"]))
                    boundary["calibrationPolicy"] = calibration["policy"]["method"]
                    boundary["calibrationScoreRange"] = calibration["policy"]["scoreRange"]
                    boundary["calibrationCharts"] = calibration["supportedCharts"]
                    boundary["calibrationRecords"] = calibration["records"]
                    boundary["calibrationValidationCharts"] = len(calibration["validationChartIds"])
                    boundary["calibrationValidationMeanAbsoluteError"] = calibration["validationMeanAbsoluteError"]
                    detail["boundary"] = boundary
                    _replace_point(metric, level, boundary["initialDifficulty"], method=target.boundary_method,
                                   branch=branch, refinement=detail, extrapolated=boundary["extrapolated"], target=target)
                    audit["changed"].append({"chartId": cid, "mode": mode, "level": level, "branch": branch,
                        "before": metric["scoringFirstPass"]["estimatedDifficulty"], "after": metric["estimatedDifficulty"], **copy.deepcopy(detail)})
                elif branch == "mixed" and (not eligible["successes"] or not eligible["belowTarget"]):
                    # Missing histories may hide a success from the fit, never from classification.
                    metric.update(scoringEstimateBranch="insufficient-history", scoringRefinement={**detail, "branch": "insufficient-history"},
                        scoringUnratedReason="Full board has both SSS outcomes, but eligible shared-player histories lack one outcome.")
                elif branch == "no-data":
                    metric.update(scoringEstimateBranch="no-data", scoringRefinement=detail,
                                  scoringUnratedReason="No valid observed successful best scores.")
                elif (branch == "mixed" and _finite(metric.get("estimatedDifficulty")) and curve and frozen
                      and point is not None and cid in chart_indices and estimate.get("calibratedComponent")
                      and 1 <= full["successes"] <= 4 and full["successes"] == eligible["successes"]
                      and 0 < eligible["successes"] < eligible["records"]):
                    blend = rare_blend(eligible["successes"], eligible["records"])
                    if not blend["blendWeight"]:
                        continue
                    selected = data["c"] == chart_indices[cid]
                    conditional = conditional_chart_fit(point["ability"][data["p"][selected]], data["y"][selected], weights[selected])
                    raw = inverse_chart_cost(conditional["conditionalEffect"], curve)
                    candidate = {"chartId": cid, "level": level, **conditional, **blend,
                        "conditionalInitialDifficulty": raw, "conditionalDifficulty": compress_difficulty(raw, level),
                        "originalDifficulty": metric["estimatedDifficulty"]}
                    candidates[level].append(candidate)
                    candidate_rows[cid] = (row, detail)
            for level, group in candidates.items():
                allocate_upper_band(group, ranges[source][mode][str(level)])
                for candidate in group:
                    row, detail = candidate_rows[candidate["chartId"]]
                    metric = row["scoringVariants"][target.key]
                    detail.update(branch="rare-sss", rare={k: v for k, v in candidate.items() if k not in ("chartId", "level")})
                    detail["_curve"] = curve
                    raw = candidate["initialDifficulty"]
                    effect = chart_cost(raw, curve)
                    extrapolated = effect < curve["anchors"][0]["cost"] or effect > curve["anchors"][-1]["cost"]
                    _replace_point(metric, level, raw, method=target.rare_method, branch="rare-sss", refinement=detail, extrapolated=extrapolated, target=target)
                    audit["changed"].append({"chartId": candidate["chartId"], "mode": mode, "level": level,
                        "before": candidate["originalDifficulty"], "after": metric["estimatedDifficulty"], **copy.deepcopy(detail)})
                    counts["rare-corrected"] += 1
            audit["counts"][f"{source}:{mode}"] = dict(counts)
    audit["transitions"] = [{"successes": successes, "players": players, **rare_blend(successes, players)}
        for players in (2, 20, 102) for successes in (0, 1, 4, 5) if successes <= players]
    result["summary"]["method"][target.key + "Refinement"] = metadata
    if target.key != "sss":
        metadata["method"] = "frozen-range-target-second-pass-v1"
        metadata["firstPassSchemaVersion"] = 52
        metadata["noTargetEndpointPolicy"] = metadata.pop("noSssEndpointPolicy")
        for _, key in MODES:
            for row in result.get(key, []):
                metric = row["scoringVariants"][target.key]
                branch_map = {"no-sss": "no-target", "all-sss": "all-target", "rare-sss": "rare-target"}
                if "scoringEstimateBranch" in metric:
                    metric["scoringEstimateBranch"] = branch_map.get(metric["scoringEstimateBranch"], metric["scoringEstimateBranch"])
                detail = metric.get("scoringRefinement")
                if detail:
                    detail["branch"] = branch_map.get(detail["branch"], detail["branch"])
                reason = metric.get("scoringUnratedReason")
                if reason:
                    metric["scoringUnratedReason"] = reason.replace("SSS", target.grade)
    return result, audit
