from __future__ import annotations

import unittest

import numpy as np

from tier_difficulty import build_tier_metrics, inclusive_percentile_skill


class PercentileSkillTests(unittest.TestCase):
    def test_boundaries_and_all_ties_are_inclusive(self) -> None:
        result = inclusive_percentile_skill([10, 11, 12, 13, 14])
        self.assertEqual(result, {
            "q25Skill": 11.0, "q50Skill": 12.0, "meanSkill": 11.5, "selectedCount": 2,
        })
        tied = inclusive_percentile_skill([10, 11, 11, 11, 11, 12, 13, 14])
        self.assertEqual(tied["selectedCount"], 4)
        self.assertEqual(tied["meanSkill"], 11)

    def test_interpolated_cutoffs_do_not_invent_observations(self) -> None:
        result = inclusive_percentile_skill([10, 20])
        self.assertEqual(result["q25Skill"], 12.5)
        self.assertEqual(result["q50Skill"], 15)
        self.assertEqual(result["selectedCount"], 0)
        self.assertIsNone(result["meanSkill"])
        result = inclusive_percentile_skill([10, 11, 12, 100])
        self.assertEqual(result["meanSkill"], 11)

    def test_empty_nonfinite_and_single_player(self) -> None:
        for values in ([], [float("nan"), float("inf")]):
            result = inclusive_percentile_skill(values)
            self.assertIsNone(result["q25Skill"])
            self.assertIsNone(result["meanSkill"])
            self.assertEqual(result["selectedCount"], 0)
        self.assertEqual(inclusive_percentile_skill([20])["meanSkill"], 20)


class TierMetricTests(unittest.TestCase):
    def _calculate(self, specs: list[tuple[str, int, float | None]], *, count: int = 1):
        charts, clearers, skills = [], {}, {}
        for index, (chart_type, level, mean) in enumerate(specs):
            chart_id = f"chart-{index}"
            charts.append({
                "chartId": chart_id, "songName": chart_id, "type": chart_type,
                "level": level, "estimatedDifficulty": level + 0.612345678,
                "nContributors": 30, "evidenceStatus": "Published",
            })
            clearers[chart_id] = {f"player-{index}-{player}" for player in range(count)}
            if mean is not None:
                skills.update({(player, chart_type): mean for player in clearers[chart_id]})
        metrics, references = build_tier_metrics(charts, clearers, skills)
        return charts, metrics, references

    def test_exact_folder_medians_odd_even_modes_and_below_official_level(self) -> None:
        _, metrics, references = self._calculate([
            ("Single", 20, 19.7), ("Single", 20, 21), ("Single", 20, 22.3),
            ("Double", 20, 10), ("Double", 20, 14),
            ("Single", 21, 30), ("Single", 21, 32),
        ])
        estimates = [metric["clearing"]["estimatedDifficulty"] for metric in metrics]
        self.assertAlmostEqual(estimates[0], 19.2)
        self.assertAlmostEqual(float(np.median(estimates[:3])), 20.5)
        self.assertAlmostEqual(float(np.median(estimates[3:5])), 20.5)
        self.assertAlmostEqual(float(np.median(estimates[5:])), 21.5)
        self.assertEqual(references, {"S20": 21, "D20": 12, "S21": 31})
        self.assertEqual(metrics[0]["clearing"]["effectBand"], "Overrated")
        self.assertEqual(metrics[0]["clearing"]["levelRank"], 1)
        self.assertEqual(metrics[2]["clearing"]["levelComparisonCharts"], 3)

    def test_sparse_finite_estimates_are_included_and_missing_skills_counted(self) -> None:
        _, metrics, _ = self._calculate([("Single", 20, 18), ("Single", 20, None)])
        self.assertEqual(metrics[0]["clearing"]["estimatedDifficulty"], 20.5)
        self.assertEqual(metrics[0]["clearing"]["evidenceStatus"], "Insufficient")
        missing = metrics[1]["clearing"]
        self.assertEqual((missing["clearCount"], missing["ratedClearCount"], missing["missingSkillCount"]), (1, 0, 1))
        self.assertIsNone(missing["estimatedDifficulty"])
        self.assertIsNone(missing["levelRank"])
        self.assertEqual(missing["evidenceStatus"], "Unrated")

    def test_composite_uses_unrounded_components_and_weaker_evidence(self) -> None:
        charts, metrics, _ = self._calculate([("Single", 20, 21)], count=5)
        clearing, pumbility = metrics[0]["clearing"], metrics[0]["pumbility"]
        self.assertEqual(pumbility["estimatedDifficulty"], (charts[0]["estimatedDifficulty"] + clearing["estimatedDifficulty"]) / 2)
        self.assertEqual(pumbility["evidenceStatus"], "Provisional")
        self.assertEqual((pumbility["scoringSupportCount"], pumbility["clearingSupportCount"]), (30, 5))
        self.assertNotIn("nContributors", pumbility)
        for count, status in ((1, "Insufficient"), (5, "Provisional"), (10, "Published")):
            self.assertEqual(self._calculate([("Single", 20, 21)], count=count)[1][0]["clearing"]["evidenceStatus"], status)

    def test_missing_component_and_weaker_scoring_evidence(self) -> None:
        charts = [{"chartId": "a", "type": "Single", "level": 20, "estimatedDifficulty": None, "evidenceStatus": "Unrated", "nContributors": 0}]
        clearers = {"a": {f"p{index}" for index in range(10)}}
        skills = {(player, "Single"): 21 for player in clearers["a"]}
        metrics, _ = build_tier_metrics(charts, clearers, skills)
        self.assertIsNone(metrics[0]["pumbility"]["estimatedDifficulty"])
        self.assertEqual(metrics[0]["pumbility"]["evidenceStatus"], "Unrated")
        charts[0].update(estimatedDifficulty=20, evidenceStatus="Insufficient", nContributors=2)
        metrics, _ = build_tier_metrics(charts, clearers, skills)
        self.assertEqual(metrics[0]["pumbility"]["evidenceStatus"], "Insufficient")


if __name__ == "__main__":
    unittest.main()
