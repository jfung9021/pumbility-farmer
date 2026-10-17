from __future__ import annotations

import copy
import json
import math
import unittest

import numpy as np

from official_cross_level_calibration import chart_cost, fit_cross_level_curve, inverse_chart_cost
from official_cross_level_scoring import apply_cross_level_scoring
from official_player_scoring import fit_cross_level_player_comparisons


def fixture():
    charts, boards, metadata = [], {}, {}
    base = {"schemaVersion": 41, "summary": {"method": {"officialTiers": {
        "version": 15, "mix": "Phoenix2", "minimumLevels": {"Single": 23, "Double": 25},
        "clearing": {"fixed": "clearing-metadata"}, "scoring": {"stale": True}}}},
        "singles": [], "doubles": [], "coop": [{"chartId": "coop", "estimatedDifficulty": 17.125}]}
    for mode, key in (("Single", "singles"), ("Double", "doubles")):
        for level in (20, 21, 22):
            for index in range(5):
                cid = f"{mode}-{level}-{index}"
                chart = {"id": cid, "type": mode, "level": level, "songName": cid,
                         "difficulty": f"{'S' if mode == 'Single' else 'D'}{level}"}
                cost = (math.exp((level - 20) * .4) - 1) * 10000 + (index - 2) * 100
                boards[cid] = {f"opaque:{p}": int(990000 - p * 123 - cost) for p in range(12)}
                charts.append(chart)
                metadata[cid] = {"anchorEligible": True, "comparability": "same", "provisional": False,
                                 "sourceStates": {mix: {"status": "available", "asOf": date,
                                     "rawRowCount": 12, "maxRank": 12, "cutoffScore": min(boards[cid].values()),
                                     "possiblyTruncated": False}
                                     for mix, date in (("Phoenix", "2026-09-06"), ("Phoenix2", "2026-10-11"))}}
                base[key].append({**chart, "chartId": cid, "folder": chart["difficulty"], "estimatedDifficulty": 99,
                    "difficultyDelta": 99, "evidenceStatus": "Published", "nContributors": 900,
                    "scoringDifficultyScale": 3, "scoringProfileMatchDifficulty": 99,
                    "officialContributors": 2, "officialEvidence": {"mix": "Phoenix2", "unchanged": 123},
                    "tierMetrics": {"clearing": {"estimatedDifficulty": level + .123456789,
                        "ratedClearCount": 13, "evidenceStatus": "Provisional", "sentinel": [1, .123456789]},
                                    "pumbility": {"estimatedDifficulty": 99}}})
        cid = f"{mode}-below"
        charts.append({"id": cid, "type": mode, "level": 19, "songName": cid, "difficulty": "S19"})
        base[key].append({"chartId": cid, "type": mode, "level": 19, "songName": cid,
                          "estimatedDifficulty": 99, "tierMetrics": {"clearing": {"estimatedDifficulty": 19.1,
                            "ratedClearCount": 90, "evidenceStatus": "Published"}}})
    merged = {"boards": boards, "charts": metadata, "sourceDates": {"Phoenix": "2026-09-06", "Phoenix2": "2026-10-11"},
              "manifestSha256": "a" * 64}
    return base, merged, charts


class CalibrationTests(unittest.TestCase):
    def anchors(self):
        return [{"level": level, "cost": cost, "referenceCharts": 5, "chartIds": [str(i) for i in range(5)]}
                for level, cost in ((20, -12000), (21, -7000), (23, 10000), (25, 45000))]

    def test_exact_anchor_cost_medians_inverse_tails_and_invariance(self):
        anchors = self.anchors()
        curve = fit_cross_level_curve(anchors)
        for anchor in curve["anchors"]:
            self.assertAlmostEqual(inverse_chart_cost(anchor["cost"], curve), anchor["level"] + .5)
            self.assertAlmostEqual(chart_cost(anchor["level"] + .5, curve), anchor["cost"])
        costs = [-1_000_000, curve["offset"] - 1, *np.linspace(-12000, 45000, 100), 1_000_000]
        ratings = [inverse_chart_cost(cost, curve) for cost in costs]
        self.assertTrue(all(a < b for a, b in zip(ratings, ratings[1:])))
        for cost, rating in zip(costs, ratings):
            self.assertAlmostEqual(chart_cost(rating, curve), cost, places=6)
        for factor, shift in ((1, 123456), (2.75, -4000)):
            transformed = fit_cross_level_curve([{**anchor, "cost": factor * anchor["cost"] + shift} for anchor in anchors])
            self.assertEqual(curve["fit"]["growth"], transformed["fit"]["growth"])
            for cost in costs:
                self.assertAlmostEqual(inverse_chart_cost(factor * cost + shift, transformed), inverse_chart_cost(cost, curve), places=8)
        for anchor in (curve["anchors"][0], curve["anchors"][-1]):
            x = anchor["difficulty"]
            left = (chart_cost(x, curve) - chart_cost(x - 1e-5, curve)) / 1e-5
            right = (chart_cost(x + 1e-5, curve) - chart_cost(x, curve)) / 1e-5
            self.assertAlmostEqual(left / right, 1, places=4)

    def test_invalid_support_or_nonmonotonic_medians_fail(self):
        anchors = self.anchors()
        for invalid in (anchors[:2], anchors[1:], [{**anchor, "cost": 0} for anchor in anchors],
                        [{**anchor, "cost": -anchor["cost"]} for anchor in anchors]):
            with self.assertRaises(ValueError):
                fit_cross_level_curve(invalid)

    def test_even_pool_anchor_is_inverse_of_midpoint_not_mean_of_inverses(self):
        curve = fit_cross_level_curve(self.anchors())
        low, high = -9000, -5000
        self.assertAlmostEqual(inverse_chart_cost((low + high) / 2, curve), 21.5)
        self.assertNotAlmostEqual((inverse_chart_cost(low, curve) + inverse_chart_cost(high, curve)) / 2, 21.5)


class GlobalFitTests(unittest.TestCase):
    def test_global_recovery_strict_four_chart_history_and_shared_bootstrap(self):
        costs = dict(a=-3000, b=-1000, c=1000, d=3000, e=7000)
        boards = {cid: {} for cid in costs}
        for player in range(12):
            for cid in costs:
                boards[cid][str(player)] = 980000 + 100 * player - costs[cid]
        fitted = fit_cross_level_player_comparisons(boards, seed_key="test")
        for cid in boards:
            row = fitted["charts"][cid]
            self.assertAlmostEqual(row["scoringChartCost"], costs[cid] - 1000)
            self.assertAlmostEqual(row["scoringMeanGap"], row["scoringChartCost"])
            self.assertIsNotNone(row["scoringChartCostCi95Low"])
        boards["a"]["three-only"] = 1
        boards["b"]["three-only"] = 2
        boards["c"]["three-only"] = 3
        self.assertEqual(fitted, fit_cross_level_player_comparisons(boards, seed_key="test"))
        sparse = fit_cross_level_player_comparisons({cid: {"p": 999999} for cid in "abc"})
        self.assertEqual(sparse["eligiblePlayers"], 0)
        self.assertTrue(all(row["scoringMeanGap"] is None for row in sparse["charts"].values()))

    def test_main_component_selection_and_disconnected_reason(self):
        boards = {cid: {"p": 980000 - i * 100} for i, cid in enumerate("abcd")}
        boards.update({cid: {"q": 980000 - i * 100} for i, cid in enumerate("efgh")})
        fitted = fit_cross_level_player_comparisons(boards, bootstrap_samples=0)
        self.assertEqual(fitted["componentSizes"], [4, 4])
        self.assertTrue(fitted["charts"]["a"]["scoringMainComponent"])
        self.assertFalse(fitted["charts"]["e"]["scoringMainComponent"])
        self.assertIn("outside", fitted["charts"]["e"]["scoringUnratedReason"])


class OverlayTests(unittest.TestCase):
    def test_public_source_status_and_truncation_without_private_rows(self):
        base, merged, charts = fixture()
        state = merged["charts"]["Single-20-0"]["sourceStates"]["Phoenix"]
        state.update(possiblyTruncated=True, maxRank=100, entries=[{"player": "private-tag#1234"}])
        result = apply_cross_level_scoring(base, merged, charts)
        row = next(row for row in result["singles"] if row["chartId"] == "Single-20-0")
        public_state = row["officialScoringEvidence"]["sourceStates"]["Phoenix"]
        self.assertEqual(public_state, {key: value for key, value in state.items() if key != "entries"})
        self.assertNotIn("private-tag", json.dumps(result))

    def test_clearings_coop_inputs_unchanged_costs_anchors_and_composites(self):
        base, merged, charts = fixture()
        original = copy.deepcopy((base, merged, charts))
        result = apply_cross_level_scoring(base, merged, charts)
        self.assertEqual((base, merged, charts), original)
        self.assertEqual(result["schemaVersion"], 42)
        self.assertEqual(result["coop"], base["coop"])
        for key in ("singles", "doubles"):
            before = {row["chartId"]: row for row in base[key]}
            for row in result[key]:
                self.assertEqual(row["tierMetrics"]["clearing"], before[row["chartId"]]["tierMetrics"]["clearing"])
                self.assertNotIn("scoringDifficultyScale", row)
                self.assertNotIn("scoringProfileMatchDifficulty", row)
                if row["level"] < 20:
                    self.assertIsNone(row["estimatedDifficulty"])
                    self.assertIsNone(row["tierMetrics"]["pumbility"]["estimatedDifficulty"])
                    self.assertIsNone(row["officialScoringEvidence"])
                    continue
                self.assertEqual(row["officialContributors"], before[row["chartId"]]["officialContributors"])
                self.assertEqual(row["officialEvidence"], before[row["chartId"]]["officialEvidence"])
                self.assertEqual(row["officialScoringContributors"], 12)
                estimate = row["estimatedDifficulty"]
                self.assertIsNotNone(estimate)
                self.assertAlmostEqual(row["tierMetrics"]["pumbility"]["estimatedDifficulty"],
                                       max(estimate, row["tierMetrics"]["clearing"]["estimatedDifficulty"]))
                self.assertLessEqual(row["difficultyCi95Low"], row["difficultyCi95High"])
        self.assertEqual(result["summary"]["method"]["officialTiers"]["clearing"], base["summary"]["method"]["officialTiers"]["clearing"])
        self.assertNotIn("scoring", result["summary"]["method"]["officialTiers"])
        self.assertNotIn("opaque:", json.dumps(result))
        for mode in ("Single", "Double"):
            curve = result["summary"]["method"]["officialCrossLevelScoring"]["calibration"][mode]
            self.assertEqual(len(curve["anchors"]), 3)
            for anchor in curve["anchors"]:
                self.assertAlmostEqual(inverse_chart_cost(anchor["cost"], curve), anchor["level"] + .5)

    def test_below20_observations_cannot_change_fit(self):
        base, merged, charts = fixture()
        original = apply_cross_level_scoring(base, merged, charts)
        for mode in ("Single", "Double"):
            merged["boards"][f"{mode}-below"] = {f"opaque:{p}": 1 for p in range(200)}
        self.assertEqual(original, apply_cross_level_scoring(base, merged, charts))

    def test_missing_catalog_targets_append_unrated_without_clearing(self):
        base, merged, charts = fixture()
        charts.append({"id": "new", "type": "Single", "level": 23, "songName": "new"})
        result = apply_cross_level_scoring(base, merged, charts)
        row = next(row for row in result["singles"] if row["chartId"] == "new")
        self.assertIsNone(row["estimatedDifficulty"])
        self.assertIsNone(row["tierMetrics"]["clearing"]["estimatedDifficulty"])
        self.assertIsNone(row["tierMetrics"]["pumbility"]["estimatedDifficulty"])
        self.assertEqual(row["officialScoringEvidence"]["mergedPlayerCount"], 0)

    def test_sparse_and_disconnected_charts_do_not_receive_normal_estimates(self):
        base, merged, charts = fixture()
        for cid, count in (("four", 4), ("five", 5), ("nine", 9), ("ten", 10)):
            charts.append({"id": cid, "type": "Single", "level": 21, "songName": cid})
            merged["boards"][cid] = {f"opaque:{p}": 985000 - p * 123 for p in range(count)}
            merged["charts"][cid] = {"anchorEligible": True, "comparability": "same", "provisional": False}
        for index in range(4):
            cid = f"disconnected-{index}"
            charts.append({"id": cid, "type": "Single", "level": 21, "songName": cid})
            merged["boards"][cid] = {f"unlinked:{p}": 950000 - index * 100 for p in range(12)}
            merged["charts"][cid] = {"anchorEligible": True, "comparability": "same", "provisional": False}
        rows = {row["chartId"]: row for row in apply_cross_level_scoring(base, merged, charts)["singles"]}
        self.assertIsNone(rows["four"]["estimatedDifficulty"])
        self.assertEqual(rows["four"]["evidenceStatus"], "Unrated")
        self.assertIsNone(rows["four"]["scoringChartCostCi95Low"])
        for cid in ("five", "nine"):
            self.assertIsNotNone(rows[cid]["estimatedDifficulty"])
            self.assertEqual(rows[cid]["evidenceStatus"], "Provisional")
        self.assertEqual(rows["ten"]["evidenceStatus"], "Published")
        self.assertFalse(rows["disconnected-0"]["scoringMainComponent"])
        self.assertIsNone(rows["disconnected-0"]["estimatedDifficulty"])
        self.assertIsNone(rows["disconnected-0"]["scoringChartCostCi95Low"])
        self.assertIn("outside", rows["disconnected-0"]["scoringUnratedReason"])

    def test_changed_chart_cannot_set_reference_anchor(self):
        base, merged, charts = fixture()
        # Add a sixth chart so excluding the changed one still leaves a supported pool.
        added = {"id": "changed", "type": "Single", "level": 21, "songName": "changed"}
        charts.append(added)
        merged["boards"]["changed"] = {f"opaque:{p}": 900000 for p in range(12)}
        merged["charts"]["changed"] = {"anchorEligible": False, "comparability": "changed", "provisional": True}
        result = apply_cross_level_scoring(base, merged, charts)
        curve = result["summary"]["method"]["officialCrossLevelScoring"]["calibration"]["Single"]
        self.assertTrue(all("changed" not in anchor["chartIds"] for anchor in curve["anchors"]))
        changed = next(row for row in result["singles"] if row["chartId"] == "changed")
        self.assertEqual(changed["evidenceStatus"], "Provisional")


if __name__ == "__main__":
    unittest.main()
