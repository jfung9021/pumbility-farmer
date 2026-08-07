import unittest

from piu_misgrade_analyzer import (
    AnalysisConfig,
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
        self.assertEqual(relative_difficulty_group(-3.3), (1, "Extremely Easy"))
        self.assertEqual(relative_difficulty_group(0.0), (6, "Typical"))
        self.assertEqual(relative_difficulty_group(2.2), (10, "Extremely Hard"))

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


if __name__ == "__main__":
    unittest.main()
