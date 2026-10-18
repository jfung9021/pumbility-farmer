#!/usr/bin/env python3
"""Bounded, read-only comparison of two submitted SSS+ observation weights.

This writes aggregate diagnostics only. It does not publish ratings, alter the
production model, fetch records, or run model bootstrap refits.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sss_scoring import payload_sha256
from sss_scoring_model import _calibration_bins, _holdout, _losses, _prepare, _sigmoid

EXPERIMENT_VERSION = "submitted-sssplus-player-folder-weight-diagnostic-v1"
WEIGHTINGS = ("inverse-history", "equal-observed-folders")
PENALTIES = (.001, .003, .01)
TARGET = 995000
SEED = 20261018


def observation_weights(players: np.ndarray, folders: np.ndarray, *,
                        player_count: int, weighting: str) -> np.ndarray:
    """Compute weights from the supplied records, never a larger history.

Both policies give every observed player total weight one. The alternative
splits that one equally among their observed nominal folders, then equally
among their records within each folder.
    """
    if len(players) != len(folders) or weighting not in WEIGHTINGS:
        raise ValueError("Invalid weighting or mismatched record arrays.")
    if not len(players):
        return np.empty(0, dtype=float)
    counts = np.bincount(players, minlength=player_count)
    if weighting == "inverse-history":
        return 1. / counts[players]
    pairs = np.column_stack((players, folders))
    distinct_pairs, pair_index, pair_counts = np.unique(
        pairs, axis=0, return_inverse=True, return_counts=True)
    folder_counts = np.bincount(distinct_pairs[:, 0], minlength=player_count)
    return 1. / (folder_counts[players] * pair_counts[pair_index])


def split_weights(players: np.ndarray, folders: np.ndarray, train: np.ndarray,
                  *, player_count: int, weighting: str) -> np.ndarray:
    """The validation contract deliberately exposes only training records."""
    return observation_weights(players[train], folders[train],
                               player_count=player_count, weighting=weighting)


def fit_weighted(p: np.ndarray, c: np.ndarray, y: np.ndarray, weights: np.ndarray,
                 player_count: int, chart_count: int, penalty: float) -> dict[str, Any]:
    """Same objective/optimizer as the current model, with explicit weights."""
    from scipy.optimize import minimize

    active = np.bincount(p, minlength=player_count) > 0
    center = active / max(float(np.sum(active)), 1.)

    def objective(parameters):
        raw = parameters[:player_count]
        ability = raw - np.dot(center, raw)
        effect = parameters[player_count:]
        eta = ability[p] - effect[c]
        residual = weights * (_sigmoid(eta) - y)
        value = float(np.sum(weights * (np.logaddexp(0., eta) - y * eta))
                      + penalty / 2 * (np.dot(active, ability ** 2)
                                       + np.dot(effect, effect)))
        player_gradient = np.bincount(p, weights=residual, minlength=player_count)
        player_gradient -= center * np.sum(residual)
        prior = penalty * active * ability
        player_gradient += prior - center * np.sum(prior)
        chart_gradient = -np.bincount(c, weights=residual, minlength=chart_count)
        chart_gradient += penalty * effect
        return value, np.concatenate((player_gradient, chart_gradient))

    result = minimize(objective, np.zeros(player_count + chart_count), jac=True,
                      method="L-BFGS-B", options={"maxiter": 400, "ftol": 1e-10,
                                                  "gtol": 1e-6, "maxcor": 10})
    ability = result.x[:player_count]
    ability -= np.dot(center, ability)
    return {"ability": ability, "effect": result.x[player_count:],
            "converged": bool(result.success and np.all(np.isfinite(result.x))),
            "iterations": int(result.nit), "objective": float(result.fun),
            "gradientMaximum": float(np.max(np.abs(result.jac))),
            "message": str(result.message)}


def predictive_metrics(y: np.ndarray, probability: np.ndarray,
                       players: np.ndarray | None = None) -> dict[str, Any]:
    """Record-average metrics match the existing held-out selection criterion."""
    if not len(y):
        return {"records": 0, "logLoss": None, "brier": None,
                "calibrationBins": [], "expectedCalibrationError": None}
    bins = _calibration_bins(y, probability)
    result = {"records": len(y), **_losses(y, probability),
              "meanPrediction": float(np.mean(probability)),
              "observedRate": float(np.mean(y)), "calibrationBins": bins,
              "expectedCalibrationError": sum(
                  row["records"] * abs(row["meanPrediction"] - row["observedRate"])
                  for row in bins) / len(y)}
    if players is not None:
        _, ids, counts = np.unique(players, return_inverse=True, return_counts=True)
        weight = 1. / counts[ids]
        clipped = np.clip(probability, 1e-12, 1 - 1e-12)
        losses = -y * np.log(clipped) - (1 - y) * np.log1p(-clipped)
        result["equalPlayer"] = {"players": len(counts),
            "logLoss": float(np.average(losses, weights=weight)),
            "brier": float(np.average((y - probability) ** 2, weights=weight))}
    return result


def distribution(values: np.ndarray) -> dict[str, Any]:
    if not len(values):
        return {"count": 0}
    return {"count": len(values), **dict(zip(
        ("minimum", "q10", "median", "q90", "maximum"),
        map(float, np.quantile(values, [0, .1, .5, .9, 1]))))}


def weight_diagnostics(p, c, folders, weights, player_count, chart_levels):
    chart_weights = np.bincount(c, weights=weights, minlength=len(chart_levels))
    player_weights = np.bincount(p, weights=weights, minlength=player_count)
    result = {"records": len(p), "observedPlayers": int(np.sum(player_weights > 0)),
              "observationWeights": distribution(weights),
              "playerTotalWeights": distribution(player_weights[player_weights > 0]),
              "folders": {}}
    for level in sorted(set(map(int, chart_levels))):
        selected = folders == level
        effective = np.bincount(p[selected], weights=weights[selected], minlength=player_count)
        result["folders"][str(level)] = {
            "records": int(np.sum(selected)), "totalWeight": float(np.sum(weights[selected])),
            "observationWeights": distribution(weights[selected]),
            "chartTotalWeights": distribution(chart_weights[chart_levels == level]),
            "observedPlayerFolderWeights": distribution(effective[effective > 0])}
    return result


def folder_effects(effect, levels, reference):
    result = {}
    for level in sorted(set(map(int, levels))):
        selected = levels == level
        comparable = selected & reference
        result[str(level)] = {"charts": int(np.sum(selected)),
            "medianEffect": float(np.median(effect[selected])),
            "referenceCharts": int(np.sum(comparable)),
            "referenceMedianEffect": float(np.median(effect[comparable])) if np.any(comparable) else None}
    for field in ("medianEffect", "referenceMedianEffect"):
        a, b = result.get("16", {}).get(field), result.get("18", {}).get(field)
        result.setdefault("contrasts", {})[f"D18MinusD16_{field}"] = None if a is None or b is None else b - a
    return result


def rank_stability(a: np.ndarray, b: np.ndarray, levels: np.ndarray) -> dict[str, Any]:
    from scipy.stats import spearmanr
    result = {}
    for level in sorted(set(map(int, levels))):
        selected = levels == level
        x, z = a[selected], b[selected]
        correlation = float(spearmanr(x, z).statistic) if len(x) >= 2 else None
        result[str(level)] = {"charts": len(x), "spearman": correlation,
                              "medianAbsoluteEffectChange": float(np.median(np.abs(x - z)))}
    return result


def matched_panels(boards: Mapping[str, Mapping[str, float]], catalog: Mapping[str, Mapping],
                   *, seed: int = SEED, bootstrap_samples: int = 5000) -> list[dict[str, Any]]:
    """Coverage qualification never depends on score or target attainment."""
    histories = defaultdict(lambda: defaultdict(list))
    for cid, board in sorted(boards.items()):
        level = catalog[cid]["level"]
        if level in (16, 18):
            for player, score in sorted(board.items()):
                histories[player][level].append(float(score))
    result = []
    for minimum in (5, 10, 20):
        # Sorting fixes bootstrap order without writing player identifiers.
        paired = [history for _, history in sorted(histories.items())
                  if all(len(history[level]) >= minimum for level in (16, 18))]
        row = {"minimumUniqueChartsEachFolder": minimum, "players": len(paired),
               "qualification": "record-coverage-only-independent-of-score-and-target",
               "equalPlayerWeight": True, "folders": {}}
        if paired:
            rate, score = [], []
            for level in (16, 18):
                rates = np.asarray([np.mean(np.asarray(h[level]) >= TARGET) for h in paired])
                scores = np.asarray([np.mean(h[level]) for h in paired])
                rate.append(rates); score.append(scores)
                row["folders"][str(level)] = {"sssplusRate": float(np.mean(rates)),
                    "meanScore": float(np.mean(scores)),
                    "records": sum(len(h[level]) for h in paired)}
            delta = np.column_stack((rate[1] - rate[0], score[1] - score[0]))
            draws = np.random.default_rng(seed + minimum).integers(
                len(paired), size=(bootstrap_samples, len(paired)))
            sampled = np.mean(delta[draws], axis=1)
            row["D18MinusD16"] = {"sssplusRate": float(np.mean(delta[:, 0])),
                "meanScore": float(np.mean(delta[:, 1])),
                "sssplusRatePaired95": list(map(float, np.quantile(sampled[:, 0], [.025, .975]))),
                "meanScorePaired95": list(map(float, np.quantile(sampled[:, 1], [.025, .975]))),
                "bootstrapSamples": bootstrap_samples,
                "intervalScope": "conditional-on-selected-observed-best-records-not-effort-controlled"}
        result.append(row)
    return result


def _write(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local-data/submitted-sssplus-calibration/weighting")
    args = parser.parse_args()
    started = time.monotonic()
    from scripts.build_local_configurable_fits import PRIVATE, inputs

    first, second, _, _, policy, _, prepared = inputs()
    catalog = prepared["catalog"]
    boards = {cid: board for cid, board in prepared["submitted"]["boards"].items()
              if catalog[cid]["type"] == "Double" and catalog[cid]["level"] >= 16}
    source_boards = {source: {cid: board for cid, board in entries.items() if cid in boards}
                     for source, entries in prepared["submitted"]["sourceBoards"].items()}
    cached = json.loads((PRIVATE / "sssplus/submitted-double-fit.json").read_text(encoding="utf-8"))
    if cached["identity"]["boards"] != payload_sha256(boards) or cached["identity"]["sourceBoards"] != payload_sha256(source_boards):
        raise ValueError("Source-corrected cached fit input identities do not match.")
    if cached["identity"]["target"] != TARGET or cached["identity"]["seed"] != SEED:
        raise ValueError("Cached model target or deterministic split seed does not match.")
    data = _prepare(boards, TARGET)
    p, c, y = data["p"], data["c"], data["y"]
    pc, cc = len(data["players"]), len(data["mainCharts"])
    levels = np.asarray([int(catalog[cid]["level"]) for cid in data["mainCharts"]])
    folders = levels[c]
    heldout = _holdout(p, c, pc, cc, SEED)
    train = ~heldout
    counts = np.bincount(c, minlength=cc)
    successes = np.bincount(c, weights=y, minlength=cc)
    reference = np.asarray([prepared["submitted"]["charts"][cid].get("anchorEligible", False)
                            for cid in data["mainCharts"]]) & (counts >= 20) & (successes >= 5) & (counts - successes >= 5)
    identity = {"experimentVersion": EXPERIMENT_VERSION, "input": cached["identity"],
        "sourcePolicySha256": policy.semantic_hash(), "catalogSha256": payload_sha256(catalog),
        "producerSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "modelModuleSha256": hashlib.sha256((ROOT / "sss_scoring_model.py").read_bytes()).hexdigest(),
        "splitMaskSha256": hashlib.sha256(heldout.tobytes()).hexdigest()}
    report = {"identity": identity, "policy": {
        "target": TARGET, "penalties": list(PENALTIES), "weightings": list(WEIGHTINGS),
        "trainingWeightsUseTrainingRecordsOnly": True, "modelBootstrapRefits": 0,
        "maximumTrainingFits": 6, "maximumFullFits": 6,
        "split": "existing-observed-entry-holdout-20pct-player-min4-chart-min2-v1",
        "penaltySelection": "minimum-record-average-heldout-logloss-among-converged-fixed-candidates",
        "adoption": "diagnostic-only-no-active-model-or-rating-changes",
        "limitations": ["selected-observed-best-records", "no-attempt-count-or-true-play-time",
                        "validation-used-for-penalty-selection-not-independent-final-test",
                        "no-model-bootstrap-intervals-for-this-experiment"]},
        "inputSummary": {"players": pc, "charts": cc, "records": len(y),
            "trainingRecords": int(np.sum(train)), "heldoutRecords": int(np.sum(heldout)),
            "components": data["componentSizes"],
            "sourceDates": prepared["sourceDates"],
            "snapshotHashes": {"phoenix1": payload_sha256(first), "phoenix2": payload_sha256(second)}},
        "weights": {}, "candidates": [], "matchedPanels": {}}
    for source, entries in {"combined-submitted-p2-precedence": boards, **source_boards}.items():
        report["matchedPanels"][source] = matched_panels(entries, catalog)
    fitted_effects = {}
    for weighting in WEIGHTINGS:
        full_weights = observation_weights(p, folders, player_count=pc, weighting=weighting)
        training_weights = split_weights(p, folders, train, player_count=pc, weighting=weighting)
        report["weights"][weighting] = {
            "full": weight_diagnostics(p, c, folders, full_weights, pc, levels),
            "training": weight_diagnostics(p[train], c[train], folders[train], training_weights, pc, levels)}
        for penalty in PENALTIES:
            key = f"{weighting}-{penalty}"
            checkpoint = args.output / f"{key}.json"
            saved = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else None
            if saved and saved.get("identity") == identity:
                entry = saved["candidate"]
                effect = np.asarray(saved["chartEffects"])
                reused = True
            else:
                training_fit = fit_weighted(p[train], c[train], y[train], training_weights, pc, cc, penalty)
                probability = _sigmoid(training_fit["ability"][p[heldout]] - training_fit["effect"][c[heldout]])
                full_fit = fit_weighted(p, c, y, full_weights, pc, cc, penalty)
                entry = {"weighting": weighting, "penalty": penalty,
                    "trainingFit": {k: training_fit[k] for k in ("converged", "iterations", "objective", "gradientMaximum", "message")},
                    "fullFit": {k: full_fit[k] for k in ("converged", "iterations", "objective", "gradientMaximum", "message")},
                    "heldout": predictive_metrics(y[heldout], probability, p[heldout]),
                    "heldoutFolders": {str(level): predictive_metrics(
                        y[heldout][folders[heldout] == level], probability[folders[heldout] == level],
                        p[heldout][folders[heldout] == level]) for level in sorted(set(map(int, levels)))},
                    "folderEffects": folder_effects(full_fit["effect"], levels, reference)}
                effect = full_fit["effect"]
                _write(checkpoint, {"identity": identity, "candidate": entry,
                                    "chartIds": data["mainCharts"], "chartEffects": effect.tolist()})
                reused = False
            fitted_effects[key] = effect
            report["candidates"].append(entry)
            print(json.dumps({"stage": "weight-candidate", "weighting": weighting, "penalty": penalty,
                              "reused": reused, "trainingConverged": entry["trainingFit"]["converged"],
                              "fullConverged": entry["fullFit"]["converged"],
                              "heldoutLogLoss": entry["heldout"]["logLoss"],
                              "seconds": round(time.monotonic() - started, 1)}), flush=True)
    baseline = fitted_effects["inverse-history-0.01"]
    cached_effect = np.asarray([cached["fit"]["charts"][cid]["effect"] for cid in data["mainCharts"]])
    report["cachedBaselineCheck"] = {"maximumAbsoluteEffectDifference": float(np.max(np.abs(baseline - cached_effect))),
        "withinFolderRankStability": rank_stability(baseline, cached_effect, levels)}
    report["rankStabilityAgainstCurrentWeightPenalty001"] = {
        key: rank_stability(baseline, effect, levels) for key, effect in fitted_effects.items()}
    report["selectedDiagnosticCandidates"] = {}
    for weighting in WEIGHTINGS:
        candidates = [entry for entry in report["candidates"] if entry["weighting"] == weighting
                      and entry["trainingFit"]["converged"] and entry["fullFit"]["converged"]]
        report["selectedDiagnosticCandidates"][weighting] = min(
            candidates, key=lambda entry: (entry["heldout"]["logLoss"], entry["penalty"]))["penalty"] if candidates else None
    report["runtimeSeconds"] = round(time.monotonic() - started, 2)
    _write(args.output / "report.json", report)
    print(json.dumps({"stage": "weight-investigation-complete", "output": str(args.output / "report.json"),
                      "selectedDiagnosticCandidates": report["selectedDiagnosticCandidates"],
                      "seconds": report["runtimeSeconds"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
