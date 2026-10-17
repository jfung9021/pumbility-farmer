"""Conditional rare-target estimates over pinned player abilities.

The prior scale is learned from mixed-chart observation likelihoods, not the
spread of already regularized chart effects. These are conditional previews;
full-fit abilities do not supply independent predictive validation or intervals.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping

import numpy as np
from scipy.optimize import brentq

from official_cross_level_calibration import chart_cost
from sss_scoring import MODES
from sss_scoring_model import _sigmoid
from sss_scoring_refinement import recover_point_fit

METHOD = "fixed-ability-pooled-prior-chart-v1"
SCALE_GRID = (.25, .5, 1., 2., 4., 8.)
POLICY = {"version": 1, "method": METHOD, "source": "official", "minimumLevel": 20,
          "abilities": "frozen-full-fit-deterministic-point-replay",
          "weighting": "one-total-weight-per-player-in-source-mode",
          "prior": "Gaussian-replaces-chart-penalty-not-added-to-fitted-effect",
          "priorCenter": "frozen-original-curve-at-folder-midpoint",
          "scaleSelection": "conditional-Laplace-marginal-likelihood",
          "normalizedScaleGrid": list(SCALE_GRID), "minimumLocalCharts": 20,
          "minimumDonorFolders": 3, "poolPriorCharts": 20,
          "donors": "comparable-mixed-20-contributors-five-per-outcome",
          "fallback": "frozen-selected-chart-regularization",
          "unsupportedScale": "insufficient-donors-or-pooled-grid-boundary",
          "conditionalOnly": True, "predictiveValidation": "not-performed-full-fit-abilities",
          "intervals": "unavailable-new-conditional-estimator"}


def conditional_map(abilities, outcomes, weights, mean: float, variance: float) -> dict:
    a, y, w = (np.asarray(v, dtype=float) for v in (abilities, outcomes, weights))
    if (not len(y) or a.shape != y.shape or w.shape != y.shape or
            any(not np.all(np.isfinite(v)) for v in (a, y, w)) or
            np.any(w <= 0) or np.any((y != 0) & (y != 1)) or
            not math.isfinite(mean) or not math.isfinite(variance) or variance <= 0):
        raise ValueError("A conditional chart MAP needs valid observations and a proper prior.")
    observed = float(w @ y)
    def score(c):
        return float(w @ _sigmoid(a-c)) - observed - (c-mean)/variance
    lo, hi = min(float(min(a)), mean)-100., max(float(max(a)), mean)+100.
    effect = float(brentq(score, lo, hi, xtol=1e-12))
    probability = _sigmoid(a-effect)
    eta = a-effect
    likelihood = float(w @ (np.logaddexp(0., eta)-y*eta))
    curvature = float(w @ (probability*(1-probability))) + 1/variance
    prior_score = (effect-mean)/variance
    expected = float(w @ probability)
    return {"posteriorEffect": effect, "weightedSuccesses": observed,
            "expectedAtPosterior": expected, "likelihoodScore": expected-observed,
            "priorScore": prior_score, "solverResidual": expected-observed-prior_score,
            "observations": len(y), "priorMean": mean, "priorVariance": variance,
            "laplaceObjective": likelihood+(effect-mean)**2/(2*variance)
                + .5*math.log(variance*curvature)}


def select_scale(donors: list[dict]) -> dict:
    """A fixed grid avoids optimizing a histogram or a particular target chart."""
    candidates = []
    for scale in SCALE_GRID:
        objective = sum(conditional_map(d["a"], d["y"], d["w"], d["mean"],
                                       (scale*d["increment"])**2)["laplaceObjective"] for d in donors)
        candidates.append({"scale": scale, "objective": objective})
    selected = min(candidates, key=lambda c: (c["objective"], c["scale"]))
    return {"scale": selected["scale"], "candidates": candidates,
            "boundarySelected": selected["scale"] in (SCALE_GRID[0], SCALE_GRID[-1])}


def build_rare_adjustments(base53: Mapping[str, Any], boards_by_mode: Mapping[str, Any],
                           fits_by_mode: Mapping[str, Any]) -> tuple[dict, dict]:
    adjustments, policy = {}, {**POLICY, "modes": {}}
    for mode, key in MODES:
        cached = fits_by_mode[mode]
        context = recover_point_fit(boards_by_mode[mode], cached, score_target=995000)
        data, point = context["data"], context["fit"]
        curve = base53["summary"]["method"]["sssplusScoring"]["calibration"]["official"][mode]
        if point is None or curve is None:
            raise ValueError("Continuous rare estimates require a supported frozen model.")
        weights = 1 / np.bincount(data["p"])[data["p"]]
        indices = {cid: i for i, cid in enumerate(data["mainCharts"])}
        rows = {str(r["chartId"]): r for r in base53[key] if r["level"] >= 20}
        observations, donors = {}, defaultdict(list)
        for cid, row in rows.items():
            if cid not in indices:
                continue
            selected = data["c"] == indices[cid]
            level = row["level"]; mid = level+.5
            item = {"cid": cid, "a": point["ability"][data["p"][selected]],
                    "y": data["y"][selected], "w": weights[selected],
                    "mean": chart_cost(mid, curve),
                    "increment": chart_cost(mid+1, curve)-chart_cost(mid, curve)}
            observations[cid] = item
            first = row["scoringVariants"]["sssplus"].get("scoringFirstPass", row["scoringVariants"]["sssplus"])
            estimate = cached["charts"].get(cid, {})
            if (estimate.get("ratingEligible") and estimate.get("usableContributors", 0) >= 20
                    and min(estimate.get("targetSuccesses", 0), estimate.get("belowTarget", 0)) >= 5
                    and first.get("scoringComparability") in ("comparable", "same", "single-source-known")):
                donors[level].append(item)
        donor_levels = sorted(level for level, entries in donors.items() if len(entries) >= 20)
        pool = [entry for level in donor_levels for entry in donors[level]]
        pooled = select_scale(pool) if len(donor_levels) >= 3 else None
        local = {level: select_scale(donors[level]) for level in donor_levels} if pooled else {}
        mode_policy = {"pointReplay": context["diagnostics"], "donorLevels": donor_levels,
                       "donorChartIds": {str(level): sorted(d["cid"] for d in donors[level]) for level in donor_levels},
                       "pooled": pooled, "local": {str(k): v for k, v in local.items()},
                       "fallbackRegularization": cached["policy"]["selectedRegularization"]}
        policy["modes"][mode] = mode_policy
        for cid, row in rows.items():
            metric = row["scoringVariants"]["sssplus"]
            rare = metric.get("scoringRefinement", {}).get("rare")
            if metric.get("scoringEstimateBranch") != "rare-target" or not rare or cid not in observations:
                continue
            d = observations[cid]; level = row["level"]
            usable_pool = pooled is not None and not pooled["boundarySelected"]
            if usable_pool:
                variance_scale = pooled["scale"]**2
                source = "pooled"
                if level in local and not local[level]["boundarySelected"]:
                    weight = len(donors[level])/(len(donors[level])+20)
                    variance_scale = weight*local[level]["scale"]**2+(1-weight)*variance_scale
                    source = "local-pooled"
                variance = variance_scale*d["increment"]**2
            else:
                variance = 1/cached["policy"]["selectedRegularization"]
                source = "frozen-regularization"
            estimate = conditional_map(d["a"], d["y"], d["w"], d["mean"], variance)
            estimate.pop("laplaceObjective")
            metadata = {"version": 1, "method": METHOD, **estimate,
                        "originalConditionalEffect": rare["conditionalEffect"], "priorSource": source,
                        "priorDonorLevels": donor_levels if usable_pool else [], "conditionalOnly": True,
                        "intervals": "unavailable-new-conditional-estimator"}
            adjustments[cid] = {"effect": estimate["posteriorEffect"], "metadata": metadata}
    return adjustments, policy
