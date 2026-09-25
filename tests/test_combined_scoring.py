from __future__ import annotations

import unittest
from collections import defaultdict
from copy import deepcopy

from piu_recommendations import build_combined_chart_results, build_combined_tier_payload, build_scoring_tier_results
from pumbility_contract import scoring_method_identity


def snapshots():
    charts = [{
        "id": f"{origin}-{mode}-{index}", "songName": f"{origin} {mode} {index}",
        "type": mode, "level": 18 + index // (10 if origin == "legacy" else 15),
        "difficulty": f"{'S' if mode == 'Single' else 'D'}{18 + index // (10 if origin == 'legacy' else 15)}",
        "noteCount": 1000,
    } for origin in ("legacy", "new") for mode in ("Single", "Double") for index in range(60)]
    sources = []
    for source in ("phoenix1", "phoenix2"):
        catalog = [r for r in charts if source == "phoenix2" or r["id"].startswith("legacy")]
        scores = []
        for chart in catalog:
            index = int(chart["id"].split("-")[-1])
            for player in range(7 if source == "phoenix1" else 6):
                # Player 5 has 60 P2 scores per mode overall and qualifies
                # across the combined legacy/new chart history.
                if source == "phoenix2" and player == 5 and index >= 30:
                    continue
                if source == "phoenix2" and player == 4 and index % 9 == 0:
                    continue
                scores.append({
                    "playerId": f"player-{player}", "chartId": chart["id"],
                    "pumbility": chart["level"] * 100 + player * 90 + index % 7,
                    "score": 940_000 + index * 60 + player * 100,
                    "plate": "RG", "isBroken": False,
                    "recordedAt": "2026-09-25T00:00:00Z",
                })
        sources.append({"charts": deepcopy(catalog), "scores": scores, "players": []})
    return sources


class CombinedScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p1, cls.p2 = snapshots()
        cls.rows, cls.slopes, cls.metadata = build_combined_chart_results(cls.p1, cls.p2)

    def test_combined_history_qualifies_players_and_uses_shared_folder_ranks(self):
        groups = defaultdict(list)
        for row in self.rows:
            self.assertNotIn("scoringOrigin", row)
            self.assertNotIn("scoringCenterOffset", row)
            groups[(row["type"], row["level"])].append(row)
        self.assertNotIn("scoringCohorts", self.metadata)
        for mode in ("singles", "doubles"):
            self.assertEqual(self.metadata["modes"][mode]["eligiblePlayers"], 7)
        new_first = next(row for row in self.rows if row["chartId"] == "new-Single-1")
        # This lower-level chart is in player 5's short, pooled history; other
        # players' contribution windows select higher charts.
        self.assertEqual(new_first["phoenix2Contributors"], 1)
        self.assertEqual(new_first["phoenix1Contributors"], 0)
        for _, rows in groups.items():
            measured = [row for row in rows if row["estimatedDifficulty"] is not None]
            self.assertEqual({row["levelComparisonCharts"] for row in measured}, {len(measured)})
            self.assertEqual(sorted(row["levelRank"] for row in measured), list(range(1, len(measured) + 1)))
        new_21 = next(row for row in self.rows if row["chartId"] == "new-Single-50")
        self.assertIsNotNone(next(value["estimatedDifficulty"] for value in new_21["whatIfEstimates"] if value["level"] == 22))

    def test_new_charts_share_fit_and_source_histories_with_legacy_charts(self):
        changed = deepcopy(self.p2)
        for score in changed["scores"]:
            if score["chartId"].startswith("new") and score["playerId"] == "player-0":
                score["pumbility"] *= 1.7
        rows, _, _ = build_combined_chart_results(self.p1, changed)
        baseline = {row["chartId"]: row for row in self.rows}
        self.assertTrue(any(row["estimatedDifficulty"] != baseline[row["chartId"]]["estimatedDifficulty"] for row in rows if row["chartId"].startswith("legacy")))
        self.assertTrue(any(row["estimatedDifficulty"] != baseline[row["chartId"]]["estimatedDifficulty"] for row in rows if row["chartId"].startswith("new")))

    def test_fewer_than_fifty_new_charts_still_uses_complete_mode_history(self):
        changed = deepcopy(self.p2)
        changed["scores"] = [row for row in changed["scores"] if not row["chartId"].startswith("new") or int(row["chartId"].split("-")[-1]) < 49]
        rows, _, _ = build_combined_chart_results(self.p1, changed)
        new_rows = [row for row in rows if row["chartId"].startswith("new") and int(row["chartId"].split("-")[-1]) < 49]
        self.assertTrue(new_rows)
        self.assertTrue(all(row["estimatedDifficulty"] is not None for row in new_rows))

    def test_consuming_and_tier_paths_share_identical_combined_model(self):
        consumed = deepcopy(self.p1)
        self.assertEqual(build_combined_chart_results(consumed, self.p2, consume_phoenix1_snapshot=True), (self.rows, self.slopes, self.metadata))
        self.assertEqual(consumed, {})
        self.assertEqual(build_scoring_tier_results(self.p1, self.p2), (self.rows, self.metadata))
        payload = build_combined_tier_payload(self.rows, self.metadata)
        self.assertEqual(payload["summary"]["method"]["scoring"], scoring_method_identity())
        self.assertEqual(payload["summary"]["method"]["observationWeighting"]["sourceWeights"], {"phoenix1": 1.0, "phoenix2": 1.0})
        for row in self.rows:
            clearing = row["tierMetrics"]["clearing"]["estimatedDifficulty"]
            scoring = row["estimatedDifficulty"]
            if scoring is not None and clearing is not None:
                self.assertAlmostEqual(row["tierMetrics"]["pumbility"]["estimatedDifficulty"], (scoring + clearing) / 2, places=5)


if __name__ == "__main__":
    unittest.main()
