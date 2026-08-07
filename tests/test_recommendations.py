from __future__ import annotations

import unittest

import pandas as pd

from piu_recommendations import (
    PHOENIX2_RATING_SCORE_THRESHOLD,
    build_combined_tier_payload,
    build_player_recommendation,
    merge_source_contributions,
    public_player_key,
    rebase_source_rows_to_catalog,
    retain_catalog_source_rows,
    retain_phoenix2_catalog_contributions,
)


class CombinedEvidenceTests(unittest.TestCase):
    def test_phoenix1_scores_are_rebased_to_phoenix2_levels(self) -> None:
        chart_id = "spooky-macaron-singles"
        charts = pd.DataFrame(
            [{"chartId": chart_id, "type": "Single", "level": 23}]
        )
        scores = pd.DataFrame(
            [{"playerId": "p", "chartId": chart_id, "pumbility": 500.0}]
        )
        phoenix2_catalog = pd.DataFrame(
            [{"chartId": chart_id, "type": "Single", "level": 22}]
        )

        rebased_charts, rebased_scores = rebase_source_rows_to_catalog(
            charts,
            scores,
            phoenix2_catalog,
            {"Single": 10.0},
        )

        self.assertEqual(rebased_charts.iloc[0]["level"], 22)
        self.assertEqual(rebased_scores.iloc[0]["pumbility"], 490.0)

    def test_any_phoenix2_score_suppresses_phoenix1_evidence(self) -> None:
        phoenix1 = pd.DataFrame(
            [
                {
                    "playerId": "p",
                    "chartId": "overlap-not-selected-in-p2",
                    "mode": "Singles",
                    "source": "phoenix1",
                    "normalizedResidual": 9.0,
                },
                {
                    "playerId": "p",
                    "chartId": "overlap-selected-in-p2",
                    "mode": "Singles",
                    "source": "phoenix1",
                    "normalizedResidual": 8.0,
                },
                {
                    "playerId": "p",
                    "chartId": "phoenix1-only-score",
                    "mode": "Singles",
                    "source": "phoenix1",
                    "normalizedResidual": 1.0,
                },
            ]
        )
        phoenix2 = pd.DataFrame(
            [
                {
                    "playerId": "p",
                    "chartId": "overlap-selected-in-p2",
                    "mode": "Singles",
                    "source": "phoenix2",
                    "normalizedResidual": -2.0,
                }
            ]
        )
        authoritative = pd.DataFrame(
            [
                {
                    "playerId": "p",
                    "chartId": "overlap-not-selected-in-p2",
                    "mode": "Singles",
                },
                {
                    "playerId": "p",
                    "chartId": "overlap-selected-in-p2",
                    "mode": "Singles",
                },
            ]
        )

        merged = merge_source_contributions(
            phoenix1,
            phoenix2,
            authoritative_phoenix2_keys=authoritative,
        )

        by_chart = {row["chartId"]: row for row in merged.to_dict(orient="records")}
        self.assertNotIn("overlap-not-selected-in-p2", by_chart)
        self.assertEqual(by_chart["overlap-selected-in-p2"]["source"], "phoenix2")
        self.assertEqual(by_chart["overlap-selected-in-p2"]["normalizedResidual"], -2.0)
        self.assertEqual(by_chart["phoenix1-only-score"]["source"], "phoenix1")

    def test_charts_absent_from_phoenix2_catalog_are_removed(self) -> None:
        contributions = pd.DataFrame(
            [
                {"chartId": "still-in-phoenix2", "source": "phoenix1"},
                {"chartId": "removed-after-phoenix1", "source": "phoenix1"},
            ]
        )
        phoenix2_catalog = pd.DataFrame([{"chartId": "still-in-phoenix2"}])

        retained = retain_phoenix2_catalog_contributions(
            contributions, phoenix2_catalog
        )

        self.assertEqual(retained["chartId"].tolist(), ["still-in-phoenix2"])

    def test_allowlist_is_applied_before_baselines_and_calibration(self) -> None:
        charts = pd.DataFrame(
            [
                {"chartId": "current", "type": "Single", "level": 20},
                {"chartId": "removed", "type": "Single", "level": 25},
            ]
        )
        scores = pd.DataFrame(
            [
                {"playerId": "p", "chartId": "current", "pumbility": 100},
                {"playerId": "p", "chartId": "removed", "pumbility": 999},
            ]
        )

        retained_charts, retained_scores = retain_catalog_source_rows(
            charts, scores, {"current"}
        )

        self.assertEqual(retained_charts["chartId"].tolist(), ["current"])
        self.assertEqual(retained_scores["chartId"].tolist(), ["current"])


class PlayerRecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        charts = []
        scores = []
        combined = []
        for index in range(35):
            chart_id = f"chart-{index:02d}"
            level = 19 if index == 34 else 20
            charts.append(
                {
                    "id": chart_id,
                    "songName": f"Chart {index}",
                    "type": "Single",
                    "level": level,
                    "difficulty": f"S{level}",
                    "imageUrl": None,
                    "noteCount": 1000,
                    "stepArtist": "Tester",
                }
            )
            estimate = 20.5
            if index == 30:
                estimate = 20.0
            elif index == 31:
                estimate = 21.0
            elif index == 32:
                estimate = 10.0
            elif index == 33:
                estimate = 21.01
            elif index == 34:
                estimate = 19.1
            combined.append(
                {
                    "mode": "Singles",
                    "songName": f"Chart {index}",
                    "difficulty": f"S{level}",
                    "type": "Single",
                    "level": level,
                    "chartId": chart_id,
                    "imageUrl": None,
                    "noteCount": 1000,
                    "stepArtist": "Tester",
                    "estimatedDifficulty": estimate,
                    "difficultyDelta": estimate - 20.5,
                    "difficultyCi95Low": estimate - 0.1,
                    "difficultyCi95High": estimate + 0.1,
                    "nContributors": 20,
                    "phoenix1Contributors": 15,
                    "phoenix2Contributors": 5,
                    "evidenceStatus": "Published",
                }
            )
            if index < 30:
                scores.append(
                    {
                        "playerId": "player",
                        "chartId": chart_id,
                        "pumbility": 300 - index,
                        "score": 990000 - index,
                        "recordedAt": "2026-08-08T00:00:00Z",
                        "isBroken": False,
                    }
                )
        self.snapshot = {
            "players": [{"playerId": "player", "username": "PLAYER"}],
            "charts": charts,
            "scores": scores,
        }
        self.combined = combined

    def test_rating_uses_ranks_11_through_30_and_one_sided_limit(self) -> None:
        result = build_player_recommendation(
            "player",
            self.snapshot,
            self.combined,
            {"singles": 10.0},
            {"singles": 10_000.0},
        )["modes"]["singles"]

        self.assertTrue(result["eligible"])
        self.assertEqual(result["baselineRanks"], [11, 30])
        self.assertEqual(result["baselinePumbility"], 280.5)
        self.assertEqual(result["scoringRating"], 20.5)
        ids = {row["chartId"] for row in result["candidates"]}
        self.assertIn("chart-30", ids)
        self.assertIn("chart-31", ids)
        self.assertIn("chart-32", ids)
        self.assertNotIn("chart-33", ids)
        self.assertNotIn("chart-34", ids)
        easy = next(row for row in result["candidates"] if row["chartId"] == "chart-30")
        hard = next(row for row in result["candidates"] if row["chartId"] == "chart-31")
        self.assertGreater(easy["expectedPumbility"], hard["expectedPumbility"])
        far_easier = next(
            row for row in result["candidates"] if row["chartId"] == "chart-32"
        )
        self.assertEqual(far_easier["projectedScore"], 1_000_000)
        self.assertLessEqual(len(result["topRecommendations"]), 20)

    def test_fewer_than_thirty_scores_use_best_half(self) -> None:
        snapshot = {**self.snapshot, "scores": self.snapshot["scores"][:29]}
        mode = build_player_recommendation(
            "player", snapshot, self.combined, {"singles": 10.0}
        )["modes"]["singles"]
        self.assertTrue(mode["eligible"])
        self.assertEqual(mode["validScoreCount"], 29)
        self.assertEqual(mode["baselineRanks"], [1, 15])
        self.assertEqual(mode["baselineScoreCount"], 15)
        self.assertEqual(mode["baselinePumbility"], 293.0)
        self.assertEqual(mode["baselineLabel"], "best 50% (15 of 29)")

    def test_single_score_uses_that_score_as_baseline(self) -> None:
        snapshot = {**self.snapshot, "scores": self.snapshot["scores"][:1]}
        mode = build_player_recommendation(
            "player", snapshot, self.combined, {"singles": 10.0}
        )["modes"]["singles"]
        self.assertTrue(mode["eligible"])
        self.assertEqual(mode["baselineRanks"], [1, 1])
        self.assertEqual(mode["baselinePumbility"], 300.0)

    def test_lower_level_scores_inform_rating_but_are_not_candidates(self) -> None:
        low_score = {
            **self.snapshot["scores"][0],
            "chartId": "chart-34",
        }
        snapshot = {**self.snapshot, "scores": [low_score]}
        mode = build_player_recommendation(
            "player", snapshot, self.combined, {"singles": 10.0}
        )["modes"]["singles"]

        self.assertTrue(mode["eligible"])
        self.assertEqual(mode["scoringRating"], 19.1)
        self.assertNotIn(
            "chart-34", {row["chartId"] for row in mode["candidates"]}
        )

    def test_public_player_key_is_stable_and_does_not_contain_id(self) -> None:
        first = public_player_key("private-player-id")
        self.assertEqual(first, public_player_key("private-player-id"))
        self.assertNotIn("private-player-id", first)

    def _rating_source_fixture(self) -> tuple[dict, list[dict], tuple[pd.DataFrame, pd.DataFrame]]:
        charts = []
        scores = []
        phoenix1_scores = []
        combined = []
        for index in range(60):
            chart_id = f"source-chart-{index:02d}"
            charts.append(
                {
                    "id": chart_id,
                    "songName": f"Source Chart {index}",
                    "type": "Single",
                    "level": 20,
                    "difficulty": "S20",
                    "imageUrl": None,
                    "noteCount": None,
                    "stepArtist": "Tester",
                }
            )
            estimate = 20.0 if 10 <= index <= 29 else 22.0
            combined.append(
                {
                    "mode": "Singles",
                    "songName": f"Source Chart {index}",
                    "difficulty": "S20",
                    "type": "Single",
                    "level": 20,
                    "chartId": chart_id,
                    "imageUrl": None,
                    "noteCount": None,
                    "stepArtist": "Tester",
                    "estimatedDifficulty": estimate,
                    "difficultyDelta": estimate - 20.5,
                    "difficultyCi95Low": estimate - 0.1,
                    "difficultyCi95High": estimate + 0.1,
                    "nContributors": 10,
                    "phoenix1Contributors": 8,
                    "phoenix2Contributors": 2,
                    "evidenceStatus": "Published",
                }
            )
            scores.append(
                {
                    "playerId": "player",
                    "chartId": chart_id,
                    "pumbility": 500 - index,
                    "score": 990_000 - index,
                    "recordedAt": "2026-08-08T00:00:00Z",
                    "isBroken": False,
                }
            )
            phoenix1_scores.append(
                {
                    "playerId": "player",
                    "chartId": chart_id,
                    "pumbility": 500 + index,
                    "score": 990_000 - index,
                    "recordedAt": "2026-08-01T00:00:00Z",
                    "isBroken": False,
                }
            )
        snapshot = {
            "players": [{"playerId": "player", "username": "PLAYER"}],
            "charts": charts,
            "scores": scores,
        }
        prepared_catalog = pd.DataFrame(charts).rename(columns={"id": "chartId"})
        return snapshot, combined, (prepared_catalog, pd.DataFrame(phoenix1_scores))

    def test_rating_source_switches_at_fifty_phoenix2_scores(self) -> None:
        snapshot, combined, prepared_phoenix1 = self._rating_source_fixture()
        below = {**snapshot, "scores": snapshot["scores"][:49]}
        below_mode = build_player_recommendation(
            "player",
            below,
            combined,
            {"singles": 10.0},
            prepared_phoenix1=prepared_phoenix1,
        )["modes"]["singles"]

        self.assertEqual(below_mode["phoenix2ScoreCount"], 49)
        self.assertEqual(
            below_mode["phoenix2ScoreThreshold"], PHOENIX2_RATING_SCORE_THRESHOLD
        )
        self.assertEqual(below_mode["ratingSource"], "phoenix1")
        self.assertEqual(below_mode["scoringRating"], 22.0)
        p1_only_chart = next(
            row for row in below_mode["candidates"] if row["chartId"] == "source-chart-59"
        )
        self.assertFalse(p1_only_chart["played"])

        at_threshold = {**snapshot, "scores": snapshot["scores"][:50]}
        threshold_mode = build_player_recommendation(
            "player",
            at_threshold,
            combined,
            {"singles": 10.0},
            prepared_phoenix1=prepared_phoenix1,
        )["modes"]["singles"]
        self.assertEqual(threshold_mode["phoenix2ScoreCount"], 50)
        self.assertEqual(threshold_mode["ratingSource"], "phoenix2")
        self.assertEqual(threshold_mode["scoringRating"], 20.0)

    def test_phoenix1_rating_without_phoenix2_history_has_no_projection(self) -> None:
        snapshot, combined, prepared_phoenix1 = self._rating_source_fixture()
        empty_scores = pd.DataFrame(columns=pd.DataFrame(snapshot["scores"]).columns)
        prepared_phoenix2 = (
            pd.DataFrame(snapshot["charts"]).rename(columns={"id": "chartId"}),
            empty_scores,
        )
        mode = build_player_recommendation(
            "player",
            snapshot,
            combined,
            {"singles": 10.0},
            prepared_phoenix1=prepared_phoenix1,
            prepared_phoenix2=prepared_phoenix2,
        )["modes"]["singles"]

        self.assertTrue(mode["eligible"])
        self.assertEqual(mode["ratingSource"], "phoenix1")
        self.assertFalse(mode["projectionAvailable"])
        self.assertEqual(mode["currentTop50Pumbility"], 0.0)
        self.assertTrue(all(not row["played"] for row in mode["candidates"]))
        self.assertTrue(all(row["projectedGain"] is None for row in mode["candidates"]))


class CombinedTierPayloadTests(unittest.TestCase):
    def test_payload_uses_combined_identity_and_filters_below_level_twenty(self) -> None:
        chart = {
            "mode": "Singles",
            "modeRank": 1,
            "levelRank": 1,
            "levelPercentile": 0.5,
            "levelComparisonCharts": 1,
            "folder": "S20",
            "relativeGroupRank": 6,
            "relativeGroup": "50-60% percentile",
            "effectBandRank": 5,
            "effectBand": "Typical",
            "songName": "Current Chart",
            "difficulty": "S20",
            "type": "Single",
            "level": 20,
            "chartId": "current",
            "imageUrl": None,
            "noteCount": None,
            "stepArtist": None,
            "estimatedDifficulty": 20.5,
            "averageDifficulty": 20.5,
            "difficultyDelta": 0.0,
            "difficultyDeltaCi95Low": -0.1,
            "difficultyDeltaCi95High": 0.1,
            "difficultyCi95Low": 20.4,
            "difficultyCi95High": 20.6,
            "pumbilityPerLevel": 1.0,
            "nContributors": 12,
            "nPlayersScored": 12,
            "phoenix1Contributors": 10,
            "phoenix2Contributors": 2,
            "evidenceStatus": "Published",
        }
        easier = {
            **chart,
            "chartId": "easier",
            "songName": "Easier Chart",
            "difficultyDelta": -0.5,
            "estimatedDifficulty": 20.0,
        }
        payload = build_combined_tier_payload(
            [
                chart,
                easier,
                {**chart, "chartId": "low", "level": 19, "folder": "S19"},
            ],
            {
                "sourceObservations": 12,
                "phoenix1Observations": 10,
                "phoenix2Observations": 2,
                "modes": {"singles": {"eligiblePlayers": 12}},
            },
            generated_at_utc="2026-08-08T00:00:00Z",
        )

        self.assertEqual(payload["mix"]["key"], "combined")
        self.assertEqual(
            [row["chartId"] for row in payload["singles"]],
            ["easier", "current"],
        )
        self.assertEqual(payload["singles"][0]["phoenix1Contributors"], 10)


if __name__ == "__main__":
    unittest.main()
