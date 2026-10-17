#!/usr/bin/env python3
"""Fit a local SSS variant from pinned caches; no fetching or production writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import marshal
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cross_level_clearing import current_clearing_catalog
from official_player_scoring import fit_cross_level_player_comparisons
from official_score_merge import merge_official_scores
from official_scoring_sources import load_official_scoring_manifest
from scripts.build_local_recommendations import _read_snapshot
from sss_scoring import (MODES, apply_sss_variants, payload_sha256, prepare_target_observations,
                         validate_sss_variants)
import sss_scoring_model as target_model
from sss_scoring_model import fit_target_model


def _core_code_identity() -> str:
    """Fingerprint numeric helpers separately from source-only diagnostics."""
    names = ("_prepare", "_fit", "_holdout", "_sigmoid", "_losses", "_calibration_bins")
    return hashlib.sha256(b"".join(marshal.dumps(getattr(target_model, name).__code__) for name in names)).hexdigest()


def _valid_replay_policy(fit: dict, samples: int) -> bool:
    policy = fit.get("policy", {})
    return (policy.get("modelVersion") == "target-rasch-observed-best-v1"
        and policy.get("scoreTarget") == 990000 and policy.get("minimumPlayerCharts") == 4
        and policy.get("weighting") == "each-player-total-one-inverse-observed-chart-count-v1"
        and policy.get("bootstrapSamples") == samples
        and policy.get("regularizationGrid") == [.01, .05, .2, 1.]
        and (not fit["diagnostics"].get("fitObservations") or policy.get("bootstrapLocationAlignment") == "mean-effect-difference-over-fixed-full-fit-reference-charts-v1"))


def training_general_predictor(boards: dict, entries: list) -> dict:
    """Fit the existing additive score model using only the target holdout's train set."""
    fitted = fit_cross_level_player_comparisons(boards, bootstrap_samples=0)
    effects = {cid: row["scoringChartCost"] for cid, row in fitted["charts"].items()
               if row.get("scoringMainComponent") and row.get("scoringChartCost") is not None}
    histories = defaultdict(list)
    for cid, board in boards.items():
        if cid in effects:
            for player, score in board.items():
                histories[player].append(score + effects[cid])
    abilities = {p: sum(values) / len(values) for p, values in histories.items() if len(values) >= 3}
    predictions = {}
    for cid, player in entries:
        if cid in effects and player in abilities:
            logit = (abilities[player] - effects[cid] - 990_000) / 10_000
            predictions[cid, player] = 1 / (1 + math.exp(-max(-700, min(700, logit))))
    return predictions


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=ROOT / ".local-data/sss-scoring-experiment/general-baseline.json")
    parser.add_argument("--manifest", type=Path, default=ROOT / ".local-data/piu-scores/official/scoring/manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".local-data/piu-scores/combined/experiments/sss/web_results.json")
    parser.add_argument("--private-output", type=Path, default=ROOT / ".local-data/sss-scoring-experiment")
    parser.add_argument("--bootstrap-samples", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261017)
    parser.add_argument("--reuse-fits", action="store_true", help="Reuse only fits whose full input/policy identity matches.")
    args = parser.parse_args()
    started = time.monotonic()
    base = json.loads(args.base.read_text(encoding="utf-8"))
    first, second = _read_snapshot("phoenix1"), _read_snapshot("phoenix2")
    catalog = current_clearing_catalog(base, second)
    # Clearing's display catalog has extra derived metadata. Validate the pinned
    # capture itself, then require matching chart identity/mode/level below.
    manifest = load_official_scoring_manifest(args.manifest)
    merged = merge_official_scores(manifest)
    if base["summary"]["method"]["officialCrossLevelScoring"].get("manifestSha256") != merged["manifestSha256"]:
        raise ValueError("SSS must use the same pinned official manifest as the General baseline.")
    captured = {str(c["id"]): c for c in merged["currentCatalog"]}
    for chart in catalog:
        if chart.get("type") in ("Single", "Double") and chart.get("level", 0) >= 20:
            reference = captured.get(str(chart["id"]))
            if (not reference or any(reference.get(k) != chart.get(k) for k in ("type", "level"))
                    or chart.get("noteCount") is not None and reference.get("noteCount") != chart.get("noteCount")):
                raise ValueError("Pinned official capture does not match the General chart catalog.")
    prepared = prepare_target_observations(first, second, merged, catalog)
    source_hashes = {"phoenix1": payload_sha256(first), "phoenix2": payload_sha256(second),
                     "currentCatalog": payload_sha256(catalog), "officialManifest": merged["manifestSha256"]}
    fits = {"submitted": {}, "official": {}}
    for source, minimum in (("official", 20), ("submitted", 16)):
        for mode, _ in MODES:
            boards = {cid: scores for cid, scores in prepared[source]["boards"].items()
                      if prepared["catalog"][cid]["type"] == mode and prepared["catalog"][cid]["level"] >= minimum}
            source_boards = {name: {cid: entries for cid, entries in mix.items() if cid in boards}
                             for name, mix in prepared[source]["sourceBoards"].items()}
            identity = {"boards": payload_sha256(boards), "sourceBoards": payload_sha256(source_boards),
                "bootstrapSamples": args.bootstrap_samples, "seed": args.seed, "modelVersion": "target-rasch-observed-best-v1",
                "generalBenchmark": "strict-training-additive-cost-fixed-10000-logistic-v1" if source == "official" else None}
            fit_path = args.private_output / f"{source}-{mode.lower()}-fit.json"
            cached = json.loads(fit_path.read_text(encoding="utf-8")) if args.reuse_fits and fit_path.exists() else None
            replay = bool(cached and cached.get("identity") == identity
                and _valid_replay_policy(cached["fit"], args.bootstrap_samples)
                and cached.get("codeIdentity", {}).get("coreCodeSha256", _core_code_identity()) == _core_code_identity())
            print(json.dumps({"stage": "fit", "source": source, "mode": mode, "charts": len(boards),
                "observations": sum(map(len, boards.values())), "reusing": replay}), flush=True)
            if replay:
                fit = cached["fit"]
            else:
                fit = fit_target_model(boards, bootstrap_samples=args.bootstrap_samples, seed=args.seed,
                    source_boards=source_boards,
                    general_predictor=training_general_predictor if source == "official" else None)
                if source == "official":
                    fit["diagnostics"]["generalBenchmarkPolicy"] = {"method": "training-only-General-additive-score-cost",
                        "logisticScoreScale": 10000, "scaleSelection": "fixed-before-holdout", "heldoutRecordsUsedInFit": False}
                else:
                    fit["diagnostics"]["validation"]["generalTargetBenchmarkReason"] = "Submitted General uses score profiles rather than additive score costs; no equivalent training-only target predictor."
            sensitivity_code = hashlib.sha256(marshal.dumps(target_model.refresh_source_sensitivity.__code__)).hexdigest()
            code_identity = {"coreCodeSha256": _core_code_identity(), "sourceSensitivityCodeSha256": sensitivity_code}
            if (not cached or cached.get("codeIdentity") != code_identity
                    or fit["diagnostics"].get("sourceRemovalVersion") != "fixed-reference-location-aligned-source-refit-v1"):
                fit = target_model.refresh_source_sensitivity(fit, source_boards)
            _write(fit_path, {"identity": identity, "codeIdentity": code_identity, "fit": fit})
            fits[source][mode] = fit
            print(json.dumps({"stage": "fit-complete", "source": source, "mode": mode,
                "elapsedSeconds": round(time.monotonic() - started, 1), "diagnostics": fit["diagnostics"]}), flush=True)
    candidate = apply_sss_variants(base, prepared, fits, source_hashes=source_hashes,
                                   bootstrap_samples=args.bootstrap_samples, seed=args.seed)
    assert validate_sss_variants(candidate) == base
    _write(args.output, candidate)
    report = {"schemaVersion": candidate["schemaVersion"], "output": str(args.output),
        "baselineFileSha256": __import__("hashlib").sha256(args.base.read_bytes()).hexdigest(),
        "basePayloadSha256": payload_sha256(base), "sourceHashes": source_hashes,
        "elapsedSeconds": round(time.monotonic() - started, 1),
        "coverage": {key: {"general": sum(r["estimatedDifficulty"] is not None for r in candidate[key]),
            "sss": sum(r["scoringVariants"]["sss"]["estimatedDifficulty"] is not None for r in candidate[key]),
            "sssPumbility": sum(r["pumbilityVariants"]["sss"]["estimatedDifficulty"] is not None for r in candidate[key])} for _, key in MODES},
        "calibration": candidate["summary"]["method"]["sssScoring"]["calibration"],
        "diagnostics": candidate["summary"]["method"]["sssScoring"]["diagnostics"],
        "preservedGeneralPayload": True}
    _write(args.private_output / "verification.json", report)
    print(json.dumps({k: v for k, v in report.items() if k not in ("diagnostics", "calibration")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
