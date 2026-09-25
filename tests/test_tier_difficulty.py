from __future__ import annotations

import unittest

import numpy as np

from tier_difficulty import build_tier_metrics, percentile_skill, tier_metric_method


class PercentileSkillTests(unittest.TestCase):
    def test_tenth_percentile_uses_linear_interpolation_and_all_ratings(self) -> None:
        self.assertEqual(percentile_skill([10, 11, 12, 13, 14]), 10.4)
        self.assertEqual(percentile_skill(list(range(11))), 1.0)
        self.assertEqual(percentile_skill([14, 12, 10, 13, 11]), 10.4)
        self.assertEqual(percentile_skill([10, 11, 11, 11, 11, 12, 13, 14]), 10.7)

    def test_interpolated_percentile_is_a_value_not_a_selected_subset(self) -> None:
        self.assertEqual(percentile_skill([10, 20]), 11)
        self.assertEqual(percentile_skill([10, 11, 12, 100]), 10.3)

    def test_empty_nonfinite_and_single_player(self) -> None:
        for values in ([], [float("nan"), float("inf")]):
            self.assertIsNone(percentile_skill(values))
        self.assertEqual(percentile_skill([20]), 20)
        self.assertEqual(percentile_skill([10, float("nan"), 20, float("inf")]), 11)


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
        self.assertAlmostEqual(estimates[0], 19.59)
        self.assertAlmostEqual(float(np.median(estimates[:3])), 20.5)
        self.assertAlmostEqual(float(np.median(estimates[3:5])), 20.5)
        self.assertAlmostEqual(float(np.median(estimates[5:])), 21.5)
        self.assertEqual(references, {"S20": 21, "D20": 12, "S21": 31})
        self.assertEqual(metrics[0]["clearing"]["effectBand"], "Overrated")
        # Crossing into S21 still uses S20's reference and preserves the skill order.
        self.assertAlmostEqual(estimates[2], 21.41)
        self.assertEqual(metrics[2]["clearing"]["folderReferenceSkill"], 21)
        self.assertEqual(metrics[2]["clearing"]["levelRank"], 3)
        self.assertAlmostEqual(metrics[2]["clearing"]["difficultyDelta"], 0.91)
        self.assertEqual(metrics[2]["clearing"]["levelComparisonCharts"], 3)

    def test_crossing_boundaries_never_uses_other_folder_references(self) -> None:
        specs = [("Double", 23, mean) for mean in (21, 21.2, 22, 22.8, 23)]
        _, baseline, _ = self._calculate(specs)
        _, with_neighbors, references = self._calculate(specs + [
            ("Double", 22, 10), ("Double", 24, 40), ("Single", 23, 50),
        ])
        self.assertEqual(with_neighbors[:5], baseline)
        for metric, expected in zip(baseline, (22.8, 22.94, 23.5, 24.06, 24.2)):
            self.assertAlmostEqual(metric["clearing"]["estimatedDifficulty"], expected)
            self.assertEqual(metric["clearing"]["folderReferenceSkill"], 22)
            for field in ("initialEstimatedDifficulty", "assessmentLevel", "reassessmentStatus"):
                self.assertNotIn(field, metric["clearing"])
        method = tier_metric_method(references)
        self.assertEqual(method["clearing"]["percentile"], 0.1)
        self.assertEqual(method["clearing"]["difficultyDeltaScale"], 0.70)
        self.assertNotIn("reassessment", method["clearing"])

    def test_sparse_finite_estimates_are_included_and_missing_skills_counted(self) -> None:
        _, metrics, _ = self._calculate([("Single", 20, 18), ("Single", 20, None)])
        self.assertEqual(metrics[0]["clearing"]["estimatedDifficulty"], 20.5)
        self.assertEqual(metrics[0]["clearing"]["evidenceStatus"], "Insufficient")
        missing = metrics[1]["clearing"]
        self.assertEqual((missing["clearCount"], missing["ratedClearCount"], missing["missingSkillCount"]), (1, 0, 1))
        self.assertIsNone(missing["estimatedDifficulty"])
        self.assertIsNone(missing["levelRank"])
        self.assertEqual(missing["evidenceStatus"], "Unrated")

    def test_folders_are_independent_and_ignore_input_order(self) -> None:
        specs = [("Double", 22, 20), ("Double", 22, 21.5), ("Double", 22, 23),
                 ("Double", 23, 21.4), ("Double", 23, 22), ("Double", 23, 22.6),
                 ("Single", 22, 40)]
        charts, metrics, references = self._calculate(specs)
        clearers = {chart["chartId"]: {chart["chartId"]} for chart in charts}
        skills = {(chart["chartId"], chart["type"]): spec[2] for chart, spec in zip(charts, specs)}
        reverse_metrics, reverse_references = build_tier_metrics(list(reversed(charts)), clearers, skills)
        self.assertEqual(reverse_references, references)
        self.assertEqual(list(reversed(reverse_metrics)), metrics)
        incoming = metrics[3]["clearing"]
        self.assertEqual(incoming["folderReferenceSkill"], 22)
        self.assertAlmostEqual(incoming["estimatedDifficulty"], 23.08)
        self.assertEqual(incoming["levelComparisonCharts"], 3)
        self.assertEqual(charts[3]["level"], 23)
        self.assertEqual(references["D22"], 21.5)
        self.assertEqual(references["S22"], 40)
        skills[(charts[3]["chartId"], "Double")] = 21.45
        changed, changed_references = build_tier_metrics(charts, clearers, skills)
        self.assertEqual(changed_references, references)
        self.assertEqual(changed[:3], metrics[:3])

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

    def test_quantile_evidence_counts_the_full_eligible_population(self) -> None:
        charts = [{"chartId": "a", "type": "Single", "level": 20, "estimatedDifficulty": 20.5, "evidenceStatus": "Published", "nContributors": 20}]
        clearers = {"a": {f"p{index}" for index in range(11)}}
        skills = {(f"p{index}", "Single"): float(index) for index in range(10)}
        metrics, _ = build_tier_metrics(charts, clearers, skills)
        clearing = metrics[0]["clearing"]
        self.assertEqual(clearing["q10Skill"], 0.9)
        self.assertEqual(clearing["ratedClearCount"], 10)
        self.assertEqual(clearing["missingSkillCount"], 1)
        self.assertEqual(clearing["evidenceStatus"], "Published")
        self.assertEqual(metrics[0]["pumbility"]["clearingSupportCount"], 10)
        self.assertNotIn("selectedCount", clearing)
        self.assertNotIn("meanSkill", clearing)


if __name__ == "__main__":
    unittest.main()
