"""Private, deterministic target-attainment model for observed best scores.

No unobserved player/chart pairs are manufactured.  Chart coordinates increase
with target difficulty; their origin is the mean ability of the fitted cohort.
Calibration to official levels is deliberately outside this module.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any, Callable, Mapping, Sequence

import numpy as np

MODEL_VERSION = "target-rasch-observed-best-v1"
WEIGHTING = "each-player-total-one-inverse-observed-chart-count-v1"
INTERVAL_METHOD = "player-cluster-refit-percentile-fixed-calibration-v1"
MINIMUM_PLAYER_CHARTS = 4
REGULARIZATION_GRID = (.01, .05, .2, 1.)
DEFAULT_BOOTSTRAP_SAMPLES = 200


def _sigmoid(value: np.ndarray) -> np.ndarray:
    # This form is finite even for complete outcome separation.
    return np.exp(-np.logaddexp(0., -value))


def _losses(outcome: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    probability = np.clip(probability, 1e-12, 1 - 1e-12)
    return {"logLoss": float(np.mean(-outcome * np.log(probability)
                                    - (1 - outcome) * np.log1p(-probability))),
            "brier": float(np.mean((outcome - probability) ** 2))}


def _prepare(boards: Mapping[str, Mapping[str, float]], target: float) -> dict[str, Any]:
    charts = sorted(boards)
    histories: dict[str, list[str]] = defaultdict(list)
    for cid in charts:
        for player, value in sorted(boards[cid].items()):
            if isinstance(value, bool) or not np.isfinite(value) or not 0 <= value <= 1_000_000:
                raise ValueError("Target observations must be finite normalized successful scores in [0, 1000000].")
            histories[player].append(cid)
    eligible = sorted(player for player, history in histories.items()
                      if len(history) >= MINIMUM_PLAYER_CHARTS)
    eligible_set = set(eligible)
    observations = [(cid, player, float(value >= target))
                    for cid in charts for player, value in sorted(boards[cid].items())
                    if player in eligible_set]
    # Union chart nodes that share an eligible player. Empty charts have no component.
    parent = {cid: cid for cid in charts if any(p in eligible_set for p in boards[cid])}

    def find(cid: str) -> str:
        while parent[cid] != cid:
            parent[cid] = parent[parent[cid]]
            cid = parent[cid]
        return cid

    for player in eligible:
        history = histories[player]
        root = find(history[0])
        for cid in history[1:]:
            other = find(cid)
            if other != root:
                parent[other] = root
    grouped: dict[str, list[str]] = defaultdict(list)
    for cid in parent:
        grouped[find(cid)].append(cid)
    groups = sorted(grouped.values(), key=lambda group: (
        -sum(len([p for p in boards[cid] if p in eligible_set]) for cid in group),
        -len(group), sorted(group)))
    components = {cid: index for index, group in enumerate(groups) for cid in group}
    sizes = []
    for index, group in enumerate(groups):
        group_set = set(group)
        records = [(cid, player, y) for cid, player, y in observations if cid in group_set]
        sizes.append({"id": index, "chartCount": len(group),
                      "playerCount": len({player for _, player, _ in records}),
                      "observationCount": len(records), "mainComponent": index == 0})
    main_charts = sorted(groups[0]) if groups else []
    main_records = [entry for entry in observations if components.get(entry[0]) == 0]
    players = sorted({player for _, player, _ in main_records})
    chart_index, player_index = ({cid: i for i, cid in enumerate(main_charts)},
                                 {player: i for i, player in enumerate(players)})
    return {"charts": charts, "players": players, "mainCharts": main_charts,
            "records": main_records, "allRecords": observations,
            "p": np.asarray([player_index[player] for _, player, _ in main_records], dtype=np.int64),
            "c": np.asarray([chart_index[cid] for cid, _, _ in main_records], dtype=np.int64),
            "y": np.asarray([y for _, _, y in main_records], dtype=float),
            "components": components, "componentSizes": sizes, "eligiblePlayers": len(eligible),
            "inputPlayers": len(histories)}


def _fit(p: np.ndarray, c: np.ndarray, y: np.ndarray, player_count: int,
         chart_count: int, regularization: float, *, multiplicity: np.ndarray | None = None,
         initial: np.ndarray | None = None) -> dict[str, Any]:
    from scipy.optimize import minimize

    counts = np.bincount(p, minlength=player_count).astype(float)
    multiplicity = np.ones(player_count) if multiplicity is None else multiplicity.astype(float)
    active = counts > 0
    multiplicity = multiplicity * active
    center_weight = multiplicity / max(float(np.sum(multiplicity)), 1.)
    observation_weight = multiplicity[p] / np.maximum(counts[p], 1.)

    def objective(parameters: np.ndarray) -> tuple[float, np.ndarray]:
        raw_ability = parameters[:player_count]
        ability = raw_ability - np.dot(center_weight, raw_ability)
        effect = parameters[player_count:]
        eta = ability[p] - effect[c]
        probability = _sigmoid(eta)
        residual = observation_weight * (probability - y)
        value = float(np.sum(observation_weight * (np.logaddexp(0., eta) - y * eta))
                      + regularization / 2 * (np.dot(multiplicity, ability ** 2)
                                               + np.dot(effect, effect)))
        ability_gradient = np.bincount(p, weights=residual, minlength=player_count)
        ability_gradient -= center_weight * np.sum(residual)
        penalty = regularization * multiplicity * ability
        ability_gradient += penalty - center_weight * np.sum(penalty)
        effect_gradient = -np.bincount(c, weights=residual, minlength=chart_count)
        effect_gradient += regularization * effect
        return value, np.concatenate((ability_gradient, effect_gradient))

    start = np.zeros(player_count + chart_count) if initial is None else initial.copy()
    result = minimize(objective, start, jac=True, method="L-BFGS-B",
                      options={"maxiter": 400, "ftol": 1e-10, "gtol": 1e-6, "maxcor": 10})
    ability = result.x[:player_count]
    ability = ability - np.dot(center_weight, ability)
    effect = result.x[player_count:]
    probability = _sigmoid(ability[p] - effect[c])
    variance = observation_weight * probability * (1 - probability)
    player_information = np.bincount(p, weights=variance, minlength=player_count) + regularization * multiplicity
    conditional_information = np.bincount(c, weights=variance, minlength=chart_count)
    correction = np.bincount(c, weights=variance ** 2 / np.maximum(player_information[p], 1e-12),
                             minlength=chart_count)
    return {"parameters": np.concatenate((ability, effect)), "ability": ability, "effect": effect,
            "probability": probability, "information": np.maximum(conditional_information - correction, 0.),
            "converged": bool(result.success and np.all(np.isfinite(result.x))),
            "iterations": int(result.nit), "gradientMaximum": float(np.max(np.abs(result.jac))),
            "objective": float(result.fun), "message": str(result.message)}


def _holdout(p: np.ndarray, c: np.ndarray, player_count: int, chart_count: int,
             seed: int) -> np.ndarray:
    """Keep four training charts per player and two records per held-out chart."""
    p_left = np.bincount(p, minlength=player_count)
    c_left = np.bincount(c, minlength=chart_count)
    holdout = np.zeros(len(p), dtype=bool)
    requested = int(len(p) * .2)
    per_player = np.maximum(1, (p_left * .2).astype(int))
    selected = np.zeros(player_count, dtype=int)
    total_selected = 0
    for index in np.random.default_rng(seed).permutation(len(p)):
        player, chart = p[index], c[index]
        if p_left[player] > 4 and c_left[chart] > 2 and selected[player] < per_player[player]:
            holdout[index] = True
            p_left[player] -= 1
            c_left[chart] -= 1
            selected[player] += 1
            total_selected += 1
            if total_selected >= requested:
                break
    return holdout


def _calibration_bins(outcome: np.ndarray, probability: np.ndarray) -> list[dict[str, Any]]:
    bins = []
    for lower in np.arange(0., 1., .1):
        selected = (probability >= lower) & (probability < min(lower + .1, 1.000001))
        if np.any(selected):
            bins.append({"lower": round(float(lower), 1), "upper": round(float(lower + .1), 1),
                         "records": int(np.sum(selected)), "meanPrediction": float(np.mean(probability[selected])),
                         "observedRate": float(np.mean(outcome[selected]))})
    return bins


def _general_validation(validation: dict[str, Any], data: Mapping[str, Any],
                        boards: Mapping[str, Mapping[str, float]], heldout: np.ndarray,
                        probability: np.ndarray, baseline: np.ndarray, *,
                        general_predictor=None, general_predictions=None) -> None:
    """Refresh raw-score diagnostics without repeating binary target fitting."""
    y = data["y"]
    entries = [(data["records"][i][0], data["records"][i][1]) for i in np.flatnonzero(heldout)]
    if general_predictor is not None:
        training_boards: dict[str, dict[str, float]] = {cid: {} for cid in data["mainCharts"]}
        for index in np.flatnonzero(~heldout):
            cid, player, _ = data["records"][index]
            training_boards[cid][player] = boards[cid][player]
        predicted = general_predictor(training_boards, entries)
        if isinstance(predicted, Mapping):
            predicted = [predicted.get((cid, player)) for cid, player in entries]
        values = np.asarray([np.nan if value is None else value for value in predicted], dtype=float)
        if len(values) != len(entries):
            raise ValueError("General holdout predictor returned the wrong number of entries.")
        finite = np.isfinite(values) & (values >= 0) & (values <= 1)
        if np.any(finite):
            validation["generalTargetBenchmark"] = {**_losses(y[heldout][finite], values[finite]),
                "records": int(np.sum(finite)), "trainingOnly": True,
                "targetModelSameRecords": _losses(y[heldout][finite], probability[finite]),
                "chartRateSameRecords": _losses(y[heldout][finite], baseline[finite])}
            validation["generalTargetBenchmarkReason"] = None
        else:
            validation["generalTargetBenchmarkReason"] = "General training-only fit supplied no usable predictions."
    if general_predictions is not None:
        values = np.asarray([general_predictions.get(cid, {}).get(player, np.nan)
            if isinstance(general_predictions.get(cid), Mapping) else general_predictions.get(cid, np.nan)
            for cid, player in entries], dtype=float)
        finite = np.isfinite(values) & (values >= 0) & (values <= 1)
        validation["externalGeneralPredictionDiagnostic"] = {
            **(_losses(y[heldout][finite], values[finite]) if np.any(finite) else {}),
            "records": int(np.sum(finite)), "trainingOnly": False}


def fit_target_model(boards: Mapping[str, Mapping[str, float]], *, score_target: int = 990000,
                     bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES, seed: int = 20261017,
                     regularization_grid: Sequence[float] = REGULARIZATION_GRID,
                     general_predictions: Mapping[str, Any] | None = None,
                     general_predictor: Callable[[dict, list[tuple[str, str]]], Any] | None = None,
                     source_boards: Mapping[str, Mapping[str, Mapping[str, float]]] | None = None,
                     _private_context: dict[str, Any] | None = None,
                     _bootstrap_checkpoint=None) -> dict[str, Any]:
    """Fit an observed-best target model; returned diagnostics contain no player IDs.

    ``general_predictor`` receives training boards and held-out chart/player keys,
    never the held-out scores. Only this callback is a training-only benchmark.
    ``general_predictions`` can provide a separately labelled external diagnostic.
    Source-removal fits reuse the selected regularization, not its validation.
    """
    if not 0 < score_target <= 1_000_000 or bootstrap_samples < 0:
        raise ValueError("Invalid target-model score target or bootstrap count.")
    grid = sorted(set(float(value) for value in regularization_grid))
    if not grid or any(not np.isfinite(value) or value <= 0 for value in grid):
        raise ValueError("Regularization candidates must be finite and strictly positive.")
    data = _prepare(boards, score_target)
    p, c, y = data["p"], data["c"], data["y"]
    player_count, chart_count = len(data["players"]), len(data["mainCharts"])
    counts: dict[str, list[int]] = {cid: [0, 0] for cid in data["charts"]}
    for cid, _, outcome in data["allRecords"]:
        counts[cid][int(outcome)] += 1
    policy = {"modelVersion": MODEL_VERSION, "scoreTarget": score_target, "minimumPlayerCharts": MINIMUM_PLAYER_CHARTS,
              "weighting": WEIGHTING, "playerTotalWeight": 1., "location": "mean-player-target-skill-zero-per-fit",
              "regularizationGrid": grid, "bootstrapSamples": bootstrap_samples,
              "intervalMethod": INTERVAL_METHOD, "intervalExcludes": ["anchor-uncertainty", "effort", "selective-observation"],
              "weakInformationBelow": .25, "regularizationSensitivityAbove": 1.}
    charts = {}
    for cid in data["charts"]:
        below, success = counts[cid]
        reasons = []
        if success + below < 5:
            reasons.append("fewer-than-five-usable-contributors")
        if not success or not below:
            reasons.append("one-sided-target-evidence" if success + below else "no-eligible-observations")
        if data["components"].get(cid) != 0:
            reasons.append("outside-main-comparison-component")
        charts[cid] = {"effect": None, "effectLow": None, "effectHigh": None,
                       "usableContributors": success + below, "targetSuccesses": success, "belowTarget": below,
                       "componentId": data["components"].get(cid), "calibratedComponent": data["components"].get(cid) == 0,
                       "ratingEligible": False, "provisionalReasons": reasons,
                       "information": None, "sensitivity": {}}
    diagnostics: dict[str, Any] = {"eligiblePlayers": data["eligiblePlayers"], "inputPlayers": data["inputPlayers"],
        "observations": len(data["allRecords"]), "fitObservations": len(y), "componentCount": len(data["componentSizes"]),
        "componentSizes": data["componentSizes"], "converged": False,
        "validation": {"records": 0, "reason": "no-eligible-comparison-component"},
        "bootstrap": {"requested": bootstrap_samples, "completed": 0, "converged": 0, "failed": 0},
        "sourceRemoval": {}}
    result = {"charts": charts, "diagnostics": diagnostics, "policy": policy}
    if not len(y):
        return result

    resume = _bootstrap_checkpoint.load() if _bootstrap_checkpoint is not None else None
    from canonical_json import canonical_json_sha256
    parent = canonical_json_sha256({"target": score_target, "samples": bootstrap_samples, "seed": seed,
        "grid": grid, "charts": data["charts"], "records": data["allRecords"],
        "players": data["players"], "inputPlayers": data["inputPlayers"], "components": data["components"]})
    if _bootstrap_checkpoint is not None and (general_predictor is not None or general_predictions is not None or source_boards):
        raise ValueError("Bootstrap recovery is limited to the private binary main component.")
    if resume is not None and resume.get("parent") != parent:
        raise ValueError("The bootstrap checkpoint belongs to different inputs or policy.")
    if resume is None:
        heldout = _holdout(p, c, player_count, chart_count, seed)
        train = ~heldout
        candidates, validation_fits = [], {}
        for penalty in grid:
            fitted = _fit(p[train], c[train], y[train], player_count, chart_count, penalty)
            validation_fits[penalty] = fitted
            entry = {"regularization": penalty, "converged": fitted["converged"]}
            if np.any(heldout):
                probability = _sigmoid(fitted["ability"][p[heldout]] - fitted["effect"][c[heldout]])
                entry.update(_losses(y[heldout], probability))
            else:
                entry.update(logLoss=None, brier=None)
            candidates.append(entry)
        valid_candidates = [entry for entry in candidates if entry["converged"] and entry["logLoss"] is not None]
        selected = min(valid_candidates, key=lambda entry: (entry["logLoss"], entry["regularization"]))["regularization"] \
            if valid_candidates else grid[len(grid) // 2]
        policy["selectedRegularization"] = selected
        validation: dict[str, Any] = {"records": int(np.sum(heldout)), "seed": seed,
            "splitMethod": "observed-entry-holdout-20pct-player-min4-chart-min2-v1",
            "abilityFitExcludesHeldOutEntries": True, "candidates": candidates,
            "selectedRegularization": selected, "generalTargetBenchmark": None,
            "generalTargetBenchmarkReason": "No training-only General predictor was supplied."}
        if np.any(heldout):
            fitted = validation_fits[selected]
            probability = _sigmoid(fitted["ability"][p[heldout]] - fitted["effect"][c[heldout]])
            train_n = np.bincount(c[train], minlength=chart_count)
            train_success = np.bincount(c[train], weights=y[train], minlength=chart_count)
            baseline = (train_success[c[heldout]] + 1) / (train_n[c[heldout]] + 2)
            validation.update(targetModel=_losses(y[heldout], probability),
                              chartRateBaseline=_losses(y[heldout], baseline),
                              calibrationBins=_calibration_bins(y[heldout], probability))
            _general_validation(validation, data, boards, heldout, probability, baseline,
                                general_predictor=general_predictor, general_predictions=general_predictions)
        else:
            validation["reason"] = "Insufficient observations for the constrained holdout."
        diagnostics["validation"] = validation

        fitted = _fit(p, c, y, player_count, chart_count, selected)
        if _private_context is not None:
            _private_context.update(pointFit={key: value.tolist() if isinstance(value, np.ndarray) else value
                                             for key, value in fitted.items()},
                heldout=heldout.tolist(), validationProbability=probability.tolist() if np.any(heldout) else [],
                validationBaseline=baseline.tolist() if np.any(heldout) else [])
        diagnostics.update(converged=fitted["converged"], iterations=fitted["iterations"],
                           gradientMaximum=fitted["gradientMaximum"], objective=fitted["objective"],
                           convergenceMessage=fitted["message"])
        full_sensitivity = {}
        for penalty in grid:
            alternate = fitted if penalty == selected else _fit(p, c, y, player_count, chart_count, penalty,
                                                                initial=fitted["parameters"])
            full_sensitivity[str(penalty)] = alternate
        diagnostics["regularizationSensitivity"] = [
            {"regularization": float(penalty), "converged": alternative["converged"]}
            for penalty, alternative in full_sensitivity.items()]

    else:
        result = copy.deepcopy(resume["result"])
        policy, diagnostics, charts = result["policy"], result["diagnostics"], result["charts"]
        selected = policy["selectedRegularization"]
        fitted = copy.deepcopy(resume["fitted"])
        for key in ("parameters", "ability", "effect", "probability", "information"):
            fitted[key] = np.asarray(fitted[key], dtype=float)
        full_sensitivity = {penalty: {"converged": entry["converged"], "effect": np.asarray(entry["effect"], dtype=float)}
                            for penalty, entry in resume["sensitivity"].items()}
        if _private_context is not None:
            _private_context.update(copy.deepcopy(resume["context"]))

    bootstrap_effects: list[list[float]] = [[] for _ in data["mainCharts"]]
    alignment_indices = [index for index, cid in enumerate(data["mainCharts"])
                         if min(counts[cid]) >= 5]
    if not alignment_indices:
        alignment_indices = [index for index, cid in enumerate(data["mainCharts"])
                             if min(counts[cid]) > 0 and sum(counts[cid]) >= 5]
    if not alignment_indices:
        alignment_indices = list(range(chart_count))
    policy["bootstrapLocationAlignment"] = "mean-effect-difference-over-fixed-full-fit-reference-charts-v1"
    policy["bootstrapAlignmentReferenceCharts"] = [data["mainCharts"][index] for index in alignment_indices]
    rng = np.random.default_rng(seed + 1)
    bootstrap_converged = 0
    completed = 0
    if resume is not None:
        completed = resume["completed"]
        if (type(completed) is not int or not 0 <= completed <= bootstrap_samples
                or resume["alignment"] != alignment_indices or len(resume["effects"]) != chart_count):
            raise ValueError("The bootstrap checkpoint has incompatible progress or alignment.")
        rng.bit_generator.state = resume["rngState"]
        bootstrap_effects = copy.deepcopy(resume["effects"])
        bootstrap_converged = resume["converged"]

    def checkpoint(count):
        if _bootstrap_checkpoint is not None:
            _bootstrap_checkpoint.save({"parent": parent, "result": result,
                "fitted": {key: value.tolist() if isinstance(value, np.ndarray) else value for key, value in fitted.items()},
                "sensitivity": {penalty: {"converged": entry["converged"], "effect": entry["effect"].tolist()}
                                for penalty, entry in full_sensitivity.items()},
                "context": _private_context or {}, "alignment": alignment_indices,
                "completed": count, "rngState": rng.bit_generator.state,
                "effects": bootstrap_effects, "converged": bootstrap_converged})

    if resume is None:
        checkpoint(0)
    from analysis_budget import work_budget_available
    for replicate in range(completed, bootstrap_samples):
        if _bootstrap_checkpoint is not None and not work_budget_available():
            checkpoint(replicate)
        multiplicity = np.bincount(rng.integers(0, player_count, size=player_count), minlength=player_count)
        refit = _fit(p, c, y, player_count, chart_count, selected, multiplicity=multiplicity,
                     initial=fitted["parameters"])
        if refit["converged"]:
            observed = np.bincount(c, weights=multiplicity[p], minlength=chart_count) > 0
            if np.all(observed[alignment_indices]):
                bootstrap_converged += 1
                location_difference = float(np.mean(refit["effect"][alignment_indices]
                                                     - fitted["effect"][alignment_indices]))
                for index in np.flatnonzero(observed):
                    bootstrap_effects[index].append(float(refit["effect"][index] - location_difference))
        if (replicate + 1) % 10 == 0 or replicate + 1 == bootstrap_samples:
            checkpoint(replicate + 1)
    diagnostics["bootstrap"] = {"requested": bootstrap_samples, "completed": bootstrap_samples,
        "converged": bootstrap_converged, "failed": bootstrap_samples - bootstrap_converged,
        "method": INTERVAL_METHOD, "calibrationRefitted": False}

    for index, cid in enumerate(data["mainCharts"]):
        row = charts[cid]
        row["effect"] = float(fitted["effect"][index])
        row["information"] = float(fitted["information"][index])
        effects = {penalty: float(value["effect"][index]) for penalty, value in full_sensitivity.items()
                   if value["converged"]}
        span = max(effects.values()) - min(effects.values()) if effects else None
        row["sensitivity"] = {"regularizationEffects": effects, "regularizationEffectRange": span}
        if fitted["converged"] and row["usableContributors"] >= 5 and row["targetSuccesses"] and row["belowTarget"]:
            row["ratingEligible"] = True
        if not fitted["converged"]:
            row["provisionalReasons"].append("model-did-not-converge")
        if min(row["targetSuccesses"], row["belowTarget"]) < 5:
            row["provisionalReasons"].append("fewer-than-five-outcomes-on-each-side")
        if row["information"] < .25:
            row["provisionalReasons"].append("weak-target-information")
        if span is not None and span > 1.:
            row["provisionalReasons"].append("regularization-sensitive")
        samples = bootstrap_effects[index]
        row["bootstrapUsableSamples"] = len(samples)
        if bootstrap_samples and len(samples) >= max(10, int(.8 * bootstrap_samples)):
            row["effectLow"], row["effectHigh"] = map(float, np.quantile(samples, [.025, .975]))
        elif bootstrap_samples:
            row["provisionalReasons"].append("insufficient-converged-bootstrap-refits")

    return refresh_source_sensitivity(result, source_boards or {}, score_target=score_target)


def refresh_source_sensitivity(fit: Mapping[str, Any],
                               source_boards: Mapping[str, Mapping[str, Mapping[str, float]]], *,
                               score_target: int = 990000) -> dict[str, Any]:
    """Refresh only source-removal fits without repeating the main fit or bootstrap.

    The main point estimates, intervals, eligibility, validation and bootstrap
    diagnostics are preserved exactly. Each source coordinate is aligned to the
    full fit over its common fixed reference charts before comparison. No player
    identities are included in the returned diagnostics.
    """
    result = copy.deepcopy(fit)
    policy = result["policy"]
    if policy.get("scoreTarget") != score_target:
        raise ValueError("Source sensitivity must use the original target threshold.")
    charts, diagnostics = result["charts"], result["diagnostics"]
    diagnostics["sourceRemoval"] = {}
    diagnostics["sourceRemovalVersion"] = "fixed-reference-location-aligned-source-refit-v1"
    for chart in charts.values():
        chart.setdefault("sensitivity", {}).pop("sourceRemovalEffects", None)
    selected = policy.get("selectedRegularization")
    if selected is None:
        for source in source_boards:
            diagnostics["sourceRemoval"][source] = {"converged": False, "reason": "full-fit-has-no-selected-regularization"}
        return result
    reference_ids = policy.get("bootstrapAlignmentReferenceCharts", [])
    # These comparisons intentionally do not reselect lambda or reuse General.
    for source, source_input in sorted(source_boards.items()):
        reduced = _prepare(source_input, score_target)
        if not len(reduced["y"]):
            diagnostics["sourceRemoval"][source] = {"converged": False, "reason": "no-eligible-comparison-component"}
            continue
        refit = _fit(reduced["p"], reduced["c"], reduced["y"], len(reduced["players"]),
                     len(reduced["mainCharts"]), selected)
        diagnostics["sourceRemoval"][source] = {"converged": refit["converged"],
            "eligiblePlayers": reduced["eligiblePlayers"], "observations": len(reduced["y"]),
            "location": "aligned-to-full-fit-common-reference-chart-mean", "calibrationRefitted": False}
        if refit["converged"]:
            source_index = {cid: index for index, cid in enumerate(reduced["mainCharts"])}
            reference_charts = [cid for cid in reference_ids if cid in source_index
                                and charts.get(cid, {}).get("effect") is not None]
            diagnostics["sourceRemoval"][source]["alignmentReferenceChartCount"] = len(reference_charts)
            if not reference_charts:
                diagnostics["sourceRemoval"][source]["reason"] = "no-common-location-reference"
                continue
            shift = float(np.mean([refit["effect"][source_index[cid]] - charts[cid]["effect"]
                                   for cid in reference_charts]))
            for index, cid in enumerate(reduced["mainCharts"]):
                if cid in charts:
                    charts[cid]["sensitivity"].setdefault("sourceRemovalEffects", {})[source] = float(refit["effect"][index] - shift)
    return result
