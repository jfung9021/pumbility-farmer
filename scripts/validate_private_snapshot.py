#!/usr/bin/env python3
"""Run the analyzer against private Blob data without persisting or printing raw rows."""

from __future__ import annotations

import json

from analysis_runtime import CURRENT_SNAPSHOT_PATH, PrivateBlobStore
from phoenix2_sync import analyzer_input
from piu_misgrade_analyzer import AnalysisConfig, analyze_snapshot, build_web_payload


def main() -> int:
    snapshot = PrivateBlobStore().get_json(CURRENT_SNAPSHOT_PATH)
    if snapshot is None:
        raise RuntimeError("The current private Phoenix 2 snapshot was not found.")
    players, charts, scores = analyzer_input(
        snapshot, minimum_scores_per_mode=30, eligible_only=True
    )
    results, _, summary, _ = analyze_snapshot(
        players, charts, scores, AnalysisConfig(bootstrap_samples=0)
    )
    payload = build_web_payload(results, summary)
    s23 = results[results["folder"] == "S23"]
    measured_s23 = s23[s23["difficultyDelta"].notna()]
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
        "s23Measured": len(measured_s23),
        "s23DeltaRange": [
            round(float(measured_s23["difficultyDelta"].min()), 4),
            round(float(measured_s23["difficultyDelta"].max()), 4),
        ],
        "s23TierRange": [
            int(measured_s23["relativeGroupRank"].min()),
            int(measured_s23["relativeGroupRank"].max()),
        ],
        "s23Below23": int((measured_s23["estimatedDifficulty"] < 23.0).sum()),
        "allBelowOfficial": len(below_official),
    }
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
