import json
import unittest
from pathlib import Path

import pandas as pd

from piu_misgrade_analyzer import (
    AnalysisConfig,
    apply_within_level_difficulty,
    analyze_snapshot,
    folder_for,
    make_synthetic_snapshot,
    relative_difficulty_group,
    validate_synthetic,
)


def chart(chart_id: str, chart_type: str, level: int) -> dict:
    prefix = "S" if chart_type == "Single" else "D"
    return {
        "id": chart_id,
        "songName": chart_id,
        "type": chart_type,
        "level": level,
        "difficulty": f"{prefix}{level}",
        "imageUrl": None,
        "noteCount": 1000,
        "stepArtist": "Test",
    }


def score(player_id: str, chart_id: str, pumbility: float) -> dict:
    return {
        "playerId": player_id,
        "chartId": chart_id,
        "pumbility": pumbility,
        "score": 950000,
        "recordedAt": "2026-08-07T00:00:00Z",
        "isBroken": False,
    }


class AnalyzerTests(unittest.TestCase):
    def test_dynamic_level_filter_and_relative_groups(self) -> None:
        self.assertEqual(folder_for("Single", 20), "S20")
        self.assertEqual(folder_for("Single", 27), "S27")
        self.assertEqual(folder_for("Double", 31), "D31")
        self.assertIsNone(folder_for("Single", 19))
        self.assertEqual(relative_difficulty_group(0.02), (1, "Extremely Easy"))
        self.assertEqual(relative_difficulty_group(0.5), (6, "Typical"))
        self.assertEqual(relative_difficulty_group(0.98), (10, "Extremely Hard"))

    def test_each_folder_uses_its_own_reference_and_percentile_groups(self) -> None:
        frame = pd.DataFrame([
            {
                "folder": folder,
                "level": level,
                "chartId": f"{folder}-{index}",
                "songName": f"{folder} {index}",
                "meanResidualPb": base + offset,
                "residualCi95LowPb": base + offset,
                "residualCi95HighPb": base + offset,
                "nContributors": 10,
            }
            for folder, level, base in (("S22", 22, 0.0), ("S23", 23, 100.0))
            for index, offset in enumerate((-10.0, 10.0))
        ])
        result = apply_within_level_difficulty(
            frame, 50.0, AnalysisConfig(bootstrap_samples=0)
        )
        for folder, reference in (("S22", 0.0), ("S23", 100.0)):
            rows = result[result["folder"] == folder].sort_values("difficultyDelta")
            self.assertTrue((rows["levelReferenceResidualPb"] == reference).all())
            self.assertAlmostEqual(float(rows["difficultyDelta"].sum()), 0.0)
            self.assertEqual(list(rows["relativeGroupRank"]), [3, 8])
        self.assertGreater(
            float(result[result["folder"] == "S23"]["estimatedDifficulty"].min()),
            23.0,
        )

    def test_top_100_cutoff_excludes_rank_101(self) -> None:
        charts = [chart(f"single-{index:03d}", "Single", 20) for index in range(101)]
        scores = [
            score("player", row["id"], 700.0 - index)
            for index, row in enumerate(charts)
        ]
        results, baselines, summary, contributions = analyze_snapshot(
            [{"userId": "player"}],
            charts,
            scores,
            AnalysisConfig(bootstrap_samples=0),
        )
        excluded = results[results["chartId"] == "single-100"].iloc[0]
        self.assertEqual(excluded["nContributors"], 0)
        self.assertEqual(excluded["nPlayersScored"], 1)
        self.assertEqual(excluded["evidenceStatus"], "Unrated")
        self.assertEqual(len(contributions), 100)
        self.assertEqual(len(baselines[baselines["mode"] == "Singles"]), 1)
        self.assertEqual(summary["modes"]["singles"]["eligiblePlayers"], 1)
        self.assertEqual(summary["modes"]["doubles"]["eligiblePlayers"], 0)

    def test_single_and_double_player_baselines_are_independent(self) -> None:
        charts = []
        scores = []
        for mode, base in (("Single", 500.0), ("Double", 900.0)):
            prefix = "s" if mode == "Single" else "d"
            for index in range(30):
                row = chart(f"{prefix}-{index:02d}", mode, 20 + (index % 2))
                charts.append(row)
                scores.append(score("dual-mode-player", row["id"], base - index))
        _, baselines, _, _ = analyze_snapshot(
            [{"userId": "dual-mode-player"}],
            charts,
            scores,
            AnalysisConfig(bootstrap_samples=0),
        )
        single = float(baselines.loc[baselines["mode"] == "Singles", "baselinePumbility"].iloc[0])
        double = float(baselines.loc[baselines["mode"] == "Doubles", "baselinePumbility"].iloc[0])
        self.assertAlmostEqual(double - single, 400.0)

    def test_synthetic_recovers_order_and_allows_s20_below_20(self) -> None:
        players, charts, scores, truth = make_synthetic_snapshot(players_per_folder=5)
        results, _, _, _ = analyze_snapshot(
            players,
            charts,
            scores,
            AnalysisConfig(bootstrap_samples=0),
        )
        validation = validate_synthetic(results, truth)
        self.assertTrue(validation["passed"])
        easiest_s20 = results[results["folder"] == "S20"].sort_values("difficultyDelta").iloc[0]
        self.assertLess(float(easiest_s20["estimatedDifficulty"]), 20.0)
        self.assertEqual(int(easiest_s20["relativeGroupRank"]), 1)

    def test_production_aggregate_reclassifies_every_folder_locally(self) -> None:
        fixture_path = (
            Path(__file__).parent
            / "fixtures"
            / "production-chart-aggregates-20260807.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(len(fixture["rows"]), 1294)
        serialized = json.dumps(fixture)
        self.assertNotIn('"playerId"', serialized)
        self.assertNotIn('"username"', serialized)
        self.assertNotIn('"gameTag"', serialized)

        frame = pd.DataFrame(fixture["rows"])
        measured = frame[frame["meanResidualPb"].notna()].copy()
        rescored_modes = []
        for _, mode_rows in measured.groupby("mode", sort=True):
            slope = float(mode_rows["pumbilityPerLevel"].dropna().iloc[0])
            rescored_modes.append(
                apply_within_level_difficulty(
                    mode_rows, slope, AnalysisConfig(bootstrap_samples=0)
                )
            )
        rescored = pd.concat(rescored_modes, ignore_index=True)

        s23 = rescored[rescored["folder"] == "S23"]
        self.assertTrue((s23["difficultyDelta"] < 0).any())
        self.assertTrue((s23["difficultyDelta"] > 0).any())
        self.assertEqual(int(s23["relativeGroupRank"].min()), 1)
        self.assertEqual(int(s23["relativeGroupRank"].max()), 10)
        self.assertTrue((s23["estimatedDifficulty"] >= 23.0).all())

        below_folder = rescored[
            rescored["estimatedDifficulty"] < rescored["level"].astype(float)
        ]
        self.assertLessEqual(len(below_folder), 1)
        self.assertTrue((below_folder["relativeGroupRank"] == 1).all())

        comparison = rescored[["chartId", "relativeGroupRank"]].merge(
            measured[["chartId", "relativeGroupRank"]],
            on="chartId",
            suffixes=("New", "Old"),
            validate="one_to_one",
        )
        changed = comparison["relativeGroupRankNew"] != comparison["relativeGroupRankOld"]
        self.assertGreater(float(changed.mean()), 0.5)


if __name__ == "__main__":
    unittest.main()
