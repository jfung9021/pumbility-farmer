#!/usr/bin/env python3
"""Run the analyzer against private Blob data without persisting or printing raw rows."""

from __future__ import annotations

import json
import argparse

from analysis_runtime import PrivateBlobStore, current_snapshot_path
from mix_registry import DEFAULT_MIX_KEY, resolve_mix
from phoenix2_sync import analyzer_input
from piu_misgrade_analyzer import AnalysisConfig, analyze_snapshot, build_web_payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mix", default=DEFAULT_MIX_KEY)
    args = parser.parse_args()
    mix_spec = resolve_mix(args.mix)
    snapshot = PrivateBlobStore().get_json(current_snapshot_path(mix_spec))
    if snapshot is None:
        raise RuntimeError(f"The current private {mix_spec.label} snapshot was not found.")
    config = AnalysisConfig(mix=mix_spec.key, bootstrap_samples=0)
    players, charts, scores = analyzer_input(
        snapshot,
        minimum_scores_per_mode=config.minimum_scores_per_player,
        eligible_only=True,
    )
    results, _, summary, _ = analyze_snapshot(
        players,
        charts,
        scores,
        config,
    )
    payload = build_web_payload(results, summary)
    measured = results[results["difficultyDelta"].notna()]
    audit_folder = "S23" if "S23" in set(measured["folder"]) else (
        str(measured["folder"].iloc[0]) if not measured.empty else None
    )
    folder_rows = measured[measured["folder"] == audit_folder] if audit_folder else measured
    below_official = results[
        results["estimatedDifficulty"].notna()
        & (results["estimatedDifficulty"] < results["level"].astype(float))
    ]
    report = {
        "snapshotPlayers": len(snapshot.get("players", [])),
        "eligiblePlayers": len(players),
        "catalogCharts": len(results),
        "measuredCharts": int(results["difficultyDelta"].notna().sum()),
        "scriptVersion": payload["summary"]["scriptVersion"],
        "mix": payload["mix"],
        "auditFolder": audit_folder,
        "auditFolderMeasured": len(folder_rows),
        "auditFolderDeltaRange": [
            round(float(folder_rows["difficultyDelta"].min()), 4),
            round(float(folder_rows["difficultyDelta"].max()), 4),
        ] if not folder_rows.empty else None,
        "allBelowOfficial": len(below_official),
        "overrated": int((results["effectBand"] == "Overrated").sum()),
        "underrated": int((results["effectBand"] == "Underrated").sum()),
        "pumbilityPerLevel": {
            mode: details.get("pumbilityPerLevel")
            for mode, details in summary["modes"].items()
        },
        "shrinkageK": {
            mode: details.get("shrinkage", {}).get("k")
            for mode, details in summary["modes"].items()
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
