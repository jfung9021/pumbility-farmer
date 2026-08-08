from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from piu_recommendations import (
    PHOENIX2_RATING_SCORE_THRESHOLD,
    TOP_RECOMMENDATION_COUNT,
    ScoreProjectionResult,
    _projected_gain_sort_key,
    _recommendation_chart_rows,
    _top50_marginal_gain,
    build_combined_tier_payload,
    build_manual_recommendation_mode,
    build_player_recommendation,
    build_recommendation_index,
    fit_score_response_model,
    merge_source_contributions,
    public_player_key,
    rebase_source_rows_to_catalog,
    retain_catalog_source_rows,
    retain_phoenix2_catalog_contributions,
)

try:
    from api.recommendations import (
        PLAYER_LIST_CACHE_CONTROL,
        get_recommendation_players,
    )
except ModuleNotFoundError as exc:
    if exc.name != "fastapi":
        raise
    PLAYER_LIST_CACHE_CONTROL = ""
    get_recommendation_players = None


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


class ScoreProjectionFitTests(unittest.TestCase):
    def test_joined_fit_uses_both_sources_and_phoenix2_wins_overlap(self) -> None:
        charts = [
            {
                "id": f"joined-{level}",
                "songName": f"Joined {level}",
                "type": "Single",
                "level": level,
            }
            for level in (20, 21, 22)
        ]
        phoenix1_scores = [
            {
                "playerId": f"player-{player_index:02d}",
                "chartId": chart["id"],
                "pumbility": 100.0 + 10.0 * chart["level"],
                "score": 950_000,
                "recordedAt": "2026-08-01T00:00:00Z",
                "isBroken": False,
            }
            for player_index in range(10)
            for chart in charts
        ]
        overlap = {
            **phoenix1_scores[0],
            "score": 975_000,
            "recordedAt": "2026-08-08T00:00:00Z",
        }
        phoenix1 = {"players": [], "charts": charts, "scores": phoenix1_scores}
        phoenix2 = {"players": [], "charts": charts, "scores": [overlap]}
        combined = [
            {
                "chartId": chart["id"],
                "type": "Single",
                "estimatedDifficulty": float(chart["level"]) + 0.5,
            }
            for chart in charts
        ]

        model, coverage = fit_score_response_model(
            phoenix1,
            phoenix2,
            combined,
        )

        self.assertEqual(coverage["model"], "population-crossfit-monotone-v1")
        self.assertFalse(coverage["personalRawScoreInput"])
        self.assertEqual(coverage["phoenix2OverlapRowsRemovedFromPhoenix1"], 1)
        self.assertEqual(
            coverage["modes"]["singles"]["sourceRows"],
            {"phoenix1": 29, "phoenix2": 1},
        )
        self.assertEqual(
            coverage["modes"]["singles"]["sourcePlayers"],
            {"phoenix1": 10, "phoenix2": 1},
        )
        self.assertIn("player-00", model.training_player_ids)


class PopulationScoreResponseTests(unittest.TestCase):
    @staticmethod
    def _fixture() -> tuple[dict, list[dict]]:
        difficulties = [17.0 + 0.5 * index for index in range(23)]
        charts = [
            {
                "id": f"surface-chart-{index:02d}",
                "songName": f"Surface Chart {index}",
                "type": "Single",
                "level": int(difficulty),
            }
            for index, difficulty in enumerate(difficulties)
        ]
        combined = [
            {
                "chartId": chart["id"],
                "type": "Single",
                "estimatedDifficulty": difficulty,
            }
            for chart, difficulty in zip(charts, difficulties, strict=True)
        ]
        scores: list[dict] = []
        for player_index in range(40):
            ability = 18.0 + 0.25 * (player_index % 33)
            for chart, difficulty in zip(charts, difficulties, strict=True):
                # This deliberately has a steeper local response near/above the
                # player's frontier than on charts well below it.
                frontier = max(0.0, difficulty - ability + 1.0)
                deficit = 4_000.0 + 1_500.0 * (difficulty - 16.0) + 6_500.0 * frontier**2
                scores.append(
                    {
                        "playerId": f"surface-player-{player_index:02d}",
                        "chartId": chart["id"],
                        "pumbility": 1_000.0 - 20.0 * abs(difficulty - ability),
                        "score": int(max(0.0, min(1_000_000.0, 1_000_000.0 - deficit))),
                        "recordedAt": "2026-08-08T00:00:00Z",
                        "isBroken": False,
                    }
                )
        snapshot = {
            "players": [
                {
                    "playerId": f"surface-player-{index:02d}",
                    "username": f"SURFACE {index:02d}",
                }
                for index in range(40)
            ],
            "charts": charts,
            "scores": scores,
        }
        return snapshot, combined

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot, cls.combined = cls._fixture()
        cls.model, cls.metadata = fit_score_response_model(
            None,
            cls.snapshot,
            cls.combined,
        )

    def test_predictions_are_monotone_bounded_and_nonlinear(self) -> None:
        player_id = "not-a-training-player"
        easier_player = self.model.predict(player_id, "singles", 21.0, 22.0)
        stronger_player = self.model.predict(player_id, "singles", 22.0, 22.0)
        easy = self.model.predict(player_id, "singles", 22.0, 20.0)
        easy_plus = self.model.predict(player_id, "singles", 22.0, 20.5)
        hard = self.model.predict(player_id, "singles", 22.0, 22.5)
        hard_plus = self.model.predict(player_id, "singles", 22.0, 23.0)

        predictions = [easier_player, stronger_player, easy, easy_plus, hard, hard_plus]
        self.assertTrue(all(result.score is not None for result in predictions))
        self.assertTrue(all(0 <= int(result.score) <= 1_000_000 for result in predictions))
        self.assertGreaterEqual(stronger_player.score, easier_player.score)
        self.assertGreaterEqual(easy.score, easy_plus.score)
        self.assertGreaterEqual(easy_plus.score, hard.score)
        self.assertGreaterEqual(hard.score, hard_plus.score)
        easy_drop = int(easy.score) - int(easy_plus.score)
        hard_drop = int(hard.score) - int(hard_plus.score)
        self.assertGreater(hard_drop, easy_drop)

    def test_training_players_use_deterministic_crossfit_exclusion(self) -> None:
        player_id = "surface-player-00"
        before = self.model.predict(player_id, "singles", 22.0, 22.0)
        changed_scores = [
            {
                **row,
                "score": 100_000,
            }
            if row["playerId"] == player_id
            else row
            for row in self.snapshot["scores"]
        ]
        changed_model, _ = fit_score_response_model(
            None,
            {**self.snapshot, "scores": changed_scores},
            self.combined,
        )
        after = changed_model.predict(player_id, "singles", 22.0, 22.0)

        self.assertEqual(before.source, "population-crossfit")
        self.assertEqual(after.source, "population-crossfit")
        self.assertEqual(after, before)

    def test_new_players_use_full_surface_and_out_of_support_is_unavailable(self) -> None:
        supported = self.model.predict("new-player", "singles", 22.0, 22.0)
        unsupported = self.model.predict("new-player", "singles", 99.0, 99.0)

        self.assertEqual(supported.source, "population-full")
        self.assertIsNotNone(supported.score)
        self.assertGreaterEqual(supported.support_count, 5)
        self.assertIn(supported.confidence, {"low", "medium", "high"})
        self.assertIsNone(unsupported.score)
        self.assertEqual(unsupported.support_count, 0)
        self.assertEqual(unsupported.confidence, "unavailable")


class PlayerRecommendationTests(unittest.TestCase):
    @staticmethod
    def _fixed_score_model(score: int = 970_000) -> Mock:
        model = Mock()
        model.predict.return_value = ScoreProjectionResult(
            score,
            "population-crossfit",
            75,
            "medium",
        )
        return model

    def test_equal_projected_gains_prefer_easier_estimated_difficulty(self) -> None:
        rows = [
            {
                "chartId": "harder",
                "songName": "Harder",
                "estimatedDifficulty": 22.4,
                "projectedGain": 25.0,
                "expectedPumbility": 400.0,
            },
            {
                "chartId": "easier",
                "songName": "Easier",
                "estimatedDifficulty": 21.7,
                "projectedGain": 25.0,
                "expectedPumbility": 390.0,
            },
            {
                "chartId": "largest-gain",
                "songName": "Largest Gain",
                "estimatedDifficulty": 23.0,
                "projectedGain": 26.0,
                "expectedPumbility": 410.0,
            },
        ]

        ordered = sorted(rows, key=_projected_gain_sort_key)

        self.assertEqual(
            [row["chartId"] for row in ordered],
            ["largest-gain", "easier", "harder"],
        )

    def test_projected_gain_replaces_number_fifty_only_when_it_improves_top50(self) -> None:
        common = {
            "current_score_count": 50,
            "cutoff": 300.0,
        }
        self.assertEqual(
            _top50_marginal_gain(
                349.0,
                existing_pumbility=None,
                existing_in_top50=False,
                **common,
            ),
            49.0,
        )
        self.assertEqual(
            _top50_marginal_gain(
                299.0,
                existing_pumbility=None,
                existing_in_top50=False,
                **common,
            ),
            0.0,
        )
        self.assertEqual(
            _top50_marginal_gain(
                349.0,
                existing_pumbility=330.0,
                existing_in_top50=True,
                **common,
            ),
            19.0,
        )
        self.assertEqual(
            _top50_marginal_gain(
                349.0,
                existing_pumbility=290.0,
                existing_in_top50=False,
                **common,
            ),
            49.0,
        )
        self.assertEqual(
            _top50_marginal_gain(
                349.0,
                existing_pumbility=None,
                existing_in_top50=False,
                current_score_count=49,
                cutoff=None,
            ),
            349.0,
        )

    def setUp(self) -> None:
        charts = []
        scores = []
        combined = []
        for index in range(35):
            chart_id = f"chart-{index:02d}"
            if index == 32:
                level = 16
            elif index == 34:
                level = 15
            else:
                level = 20
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
                estimate = 20.5000000001
            elif index == 34:
                estimate = 15.1
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

    def test_sharded_index_keeps_full_players_out_of_the_dropdown_blob(self) -> None:
        players = [
            {"playerId": "player", "username": "PLAYER"},
            {"playerId": "second", "username": "SECOND"},
            {"playerId": "third", "username": "THIRD"},
        ]
        snapshot = {**self.snapshot, "players": players}
        shards: dict[int, dict] = {}
        index = build_recommendation_index(
            self.snapshot,
            snapshot,
            combined_charts=self.combined,
            phoenix2_slopes={"singles": 10.0},
            generation_key="test-generation",
            shard_writer=lambda number, payload: shards.__setitem__(
                number, dict(payload)
            ),
            shard_size=2,
        )

        self.assertEqual(index["storageSchemaVersion"], 2)
        self.assertEqual(index["schemaVersion"], 9)
        self.assertEqual(index["shardCount"], 2)
        self.assertEqual(len(index["players"]), 3)
        self.assertNotIn("charts", index)
        self.assertNotIn("modes", index["players"][0])
        self.assertIn("Phoenix 1 + Phoenix 2", index["method"]["scoreProjectionData"])
        self.assertIn("scoreProjectionCoverage", index["method"])
        self.assertEqual([len(shards[number]["players"]) for number in shards], [2, 1])
        self.assertIn("modes", shards[0]["players"][0])

    def test_rating_uses_ranks_11_through_30_and_one_sided_limit(self) -> None:
        result = build_player_recommendation(
            "player",
            self.snapshot,
            self.combined,
            {"singles": 10.0},
            self._fixed_score_model(),
        )["modes"]["singles"]

        self.assertTrue(result["eligible"])
        self.assertEqual(result["baselineRanks"], [11, 30])
        self.assertEqual(result["baselinePumbility"], 280.5)
        self.assertEqual(result["scoringRating"], 20.5)
        ids = {row["chartId"] for row in result["candidates"]}
        self.assertIn("chart-00", ids)
        self.assertIn("chart-30", ids)
        self.assertNotIn("chart-31", ids)
        self.assertIn("chart-32", ids)
        self.assertEqual(
            next(row for row in result["candidates"] if row["chartId"] == "chart-32")[
                "level"
            ],
            16,
        )
        self.assertNotIn("chart-33", ids)
        self.assertNotIn("chart-34", ids)
        self.assertEqual(result["candidateRange"], [None, 20.5])
        easy = next(row for row in result["candidates"] if row["chartId"] == "chart-30")
        self.assertIsNotNone(easy["projectedGrade"])
        self.assertIsNotNone(easy["projectedPlateCode"])
        self.assertEqual(easy["plateProjectionSource"], "population")
        self.assertIsNone(result["currentTop50CutoffPumbility"])
        far_easier = next(
            row for row in result["candidates"] if row["chartId"] == "chart-32"
        )
        self.assertEqual(far_easier["projectedScore"], 970_000)
        self.assertEqual(far_easier["scoreProjectionSource"], "population-crossfit")
        self.assertEqual(far_easier["scoreProjectionSupportCount"], 75)
        self.assertEqual(far_easier["scoreProjectionConfidence"], "medium")
        self.assertEqual(result["scoreProjectionModel"], "population-crossfit-monotone-v1")
        self.assertEqual(TOP_RECOMMENDATION_COUNT, 50)
        self.assertLessEqual(len(result["topRecommendations"]), 50)

    def test_fewer_than_thirty_scores_use_best_half(self) -> None:
        snapshot = {**self.snapshot, "scores": self.snapshot["scores"][:29]}
        mode = build_player_recommendation(
            "player", snapshot, self.combined, {"singles": 10.0}
        )["modes"]["singles"]
        self.assertTrue(mode["eligible"])
        self.assertEqual(mode["validScoreCount"], 29)
        self.assertEqual(mode["baselineRanks"], [1, 15])
        self.assertEqual(mode["baselinePumbility"], 293.0)
        self.assertEqual(mode["baselineLabel"], "best 50% (15 of 29)")

    def test_raw_score_average_is_not_used_as_a_prediction_baseline(self) -> None:
        model = self._fixed_score_model(965_000)
        original = build_player_recommendation(
            "player",
            self.snapshot,
            self.combined,
            {"singles": 10.0},
            model,
        )["modes"]["singles"]
        changed_snapshot = {
            **self.snapshot,
            "scores": [{**row, "score": 100_000} for row in self.snapshot["scores"]],
        }
        changed = build_player_recommendation(
            "player",
            changed_snapshot,
            self.combined,
            {"singles": 10.0},
            model,
        )["modes"]["singles"]

        self.assertEqual(original["scoringRating"], changed["scoringRating"])
        self.assertEqual(
            [row["projectedScore"] for row in original["candidates"]],
            [row["projectedScore"] for row in changed["candidates"]],
        )
        for legacy_field in ("baselineScore", "projectionRating", "scorePointsPerDifficulty"):
            self.assertNotIn(legacy_field, original)

    def test_single_score_uses_that_score_as_baseline(self) -> None:
        snapshot = {**self.snapshot, "scores": self.snapshot["scores"][:1]}
        mode = build_player_recommendation(
            "player", snapshot, self.combined, {"singles": 10.0}
        )["modes"]["singles"]
        self.assertTrue(mode["eligible"])
        self.assertEqual(mode["baselineRanks"], [1, 1])
        self.assertEqual(mode["baselinePumbility"], 300.0)

    def test_level_fifteen_scores_inform_rating_but_are_not_candidates(self) -> None:
        low_score = {
            **self.snapshot["scores"][0],
            "chartId": "chart-34",
        }
        snapshot = {**self.snapshot, "scores": [low_score]}
        mode = build_player_recommendation(
            "player", snapshot, self.combined, {"singles": 10.0}
        )["modes"]["singles"]

        self.assertTrue(mode["eligible"])
        self.assertEqual(mode["scoringRating"], 15.1)
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

    def test_phoenix1_rating_without_phoenix2_history_or_model_has_no_projection(self) -> None:
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

    def test_phoenix1_rating_without_phoenix2_history_uses_population_projection(self) -> None:
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
            self._fixed_score_model(970_000),
            prepared_phoenix1=prepared_phoenix1,
            prepared_phoenix2=prepared_phoenix2,
        )["modes"]["singles"]

        self.assertEqual(mode["ratingSource"], "phoenix1")
        self.assertTrue(mode["projectionAvailable"])
        self.assertEqual(mode["currentTop50Pumbility"], 0.0)
        self.assertTrue(all(not row["played"] for row in mode["candidates"]))
        self.assertTrue(all(row["projectedScore"] == 970_000 for row in mode["candidates"]))
        self.assertTrue(all(float(row["projectedGain"]) > 0 for row in mode["candidates"]))


class CombinedTierPayloadTests(unittest.TestCase):
    def test_payload_uses_combined_identity_and_filters_below_level_sixteen(self) -> None:
        chart = {
            "mode": "Singles",
            "modeRank": 1,
            "levelRank": 1,
            "levelPercentile": 0.5,
            "levelComparisonCharts": 1,
            "folder": "S16",
            "relativeGroupRank": 6,
            "relativeGroup": "50-60% percentile",
            "effectBandRank": 5,
            "effectBand": "Typical",
            "songName": "Current Chart",
            "difficulty": "S16",
            "type": "Single",
            "level": 16,
            "chartId": "current",
            "imageUrl": None,
            "noteCount": None,
            "stepArtist": None,
            "estimatedDifficulty": 16.5,
            "averageDifficulty": 16.5,
            "difficultyDelta": 0.0,
            "difficultyDeltaCi95Low": -0.1,
            "difficultyDeltaCi95High": 0.1,
            "difficultyCi95Low": 16.4,
            "difficultyCi95High": 16.6,
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
            "estimatedDifficulty": 16.0,
        }
        payload = build_combined_tier_payload(
            [
                chart,
                easier,
                {**chart, "chartId": "low", "level": 15, "folder": "S15"},
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
        self.assertEqual(
            payload["summary"]["method"]["displayMinimumOfficialLevel"], 16
        )
        self.assertEqual(payload["summary"]["method"]["difficultyDeltaScale"], 0.7)


class RecommendationChartBoundaryTests(unittest.TestCase):
    def test_recommendation_chart_payload_includes_sixteen_and_excludes_fifteen(
        self,
    ) -> None:
        rows = _recommendation_chart_rows(
            [
                {"chartId": "sixteen", "type": "Single", "level": 16},
                {"chartId": "fifteen", "type": "Single", "level": 15},
            ]
        )

        self.assertEqual([row["chartId"] for row in rows], ["sixteen"])

    def test_manual_recommendations_include_sixteen_and_exclude_fifteen(self) -> None:
        mode = build_manual_recommendation_mode(
            [
                {
                    "chartId": "sixteen",
                    "songName": "Sixteen",
                    "type": "Single",
                    "level": 16,
                    "estimatedDifficulty": 16.0,
                },
                {
                    "chartId": "fifteen",
                    "songName": "Fifteen",
                    "type": "Single",
                    "level": 15,
                    "estimatedDifficulty": 15.0,
                },
                {
                    "chartId": "rating-edge",
                    "songName": "Rating Edge",
                    "type": "Single",
                    "level": 16,
                    "estimatedDifficulty": 16.0,
                },
                {
                    "chartId": "above-rating",
                    "songName": "Above Rating",
                    "type": "Single",
                    "level": 16,
                    "estimatedDifficulty": 16.0000000001,
                },
            ],
            "Single",
            16.0,
        )

        self.assertEqual(
            [row["chartId"] for row in mode["candidates"]],
            ["rating-edge", "sixteen"],
        )


@unittest.skipIf(get_recommendation_players is None, "FastAPI is not installed")
class RecommendationPlayerListRouteTests(unittest.TestCase):
    def test_success_is_publicly_cacheable_for_five_minutes(self) -> None:
        payload = {
            "generatedAtUtc": "2026-08-08T00:00:00Z",
            "method": {"displayMinimumOfficialLevel": 16},
            "players": [
                {
                    "playerKey": "public-key",
                    "username": "PLAYER",
                    "displayName": "PLAYER",
                    "modes": {
                        "singles": {"eligible": True},
                        "doubles": {"eligible": False},
                    },
                }
            ],
        }

        with patch("api.recommendations._read_index", return_value=payload):
            response = get_recommendation_players()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], PLAYER_LIST_CACHE_CONTROL)
        content = json.loads(response.body)
        self.assertEqual(content["players"][0]["playerKey"], "public-key")

    def test_errors_are_not_cacheable(self) -> None:
        with patch("api.recommendations._read_index", return_value=None):
            missing = get_recommendation_players()
        with patch(
            "api.recommendations._read_index",
            side_effect=RuntimeError("blob unavailable"),
        ):
            unavailable = get_recommendation_players()

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.headers["cache-control"], "no-store")
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
