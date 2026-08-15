from __future__ import annotations

import io
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from analysis_runtime import MemoryBlobStore
from phoenix2_pumbility import (
    PlateProjectionModel,
    phoenix2_pumbility,
    skill_rating_for_pumbility,
)
from phoenix1_score_overrides import (
    SLAM_D24_CHART_ID,
    SOLVE_MY_HURT_SHORTCUT_D26_CHART_ID,
    convert_phoenix1_pumbility,
    convert_phoenix1_score,
    phoenix1_score_overrides_metadata,
)
from piu_recommendations import (
    COMBINED_TIER_SCHEMA_VERSION,
    PHOENIX2_RATING_SCORE_THRESHOLD,
    RECOMMENDATION_SCHEMA_VERSION,
    SCORE_PROJECTION_MODEL_NAME,
    SCORE_RESPONSE_MODEL_NAME,
    TOP_PUMBILITY_COUNT,
    TOP_RECOMMENDATION_COUNT,
    ScoreProjectionResult,
    ScoreResponseModel,
    _PeerScoreCohort,
    _ScoreSurface,
    _apply_phoenix1_score_overrides,
    _build_score_surface,
    _effective_sample_size,
    _observation_weight,
    _peer_cohort_key,
    _prepare_phoenix1_rating_frames,
    _projected_gain_sort_key,
    _rating_lookup,
    _recommendation_chart_rows,
    _top50_marginal_gain,
    _weighted_residual_statistics,
    _what_if_residual_shift,
    build_chart_what_if_estimates,
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
    what_if_levels,
)
from recommendation_refresh import (
    build_recommendation_model_artifacts,
    cached_player_is_fresh,
    player_refresh_enabled,
    publish_recommendation_model_artifacts,
    recommendation_player_path,
    recommendation_player_state_path,
    refresh_player_recommendations,
    with_staleness,
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
    def test_source_and_ability_weights_use_strict_midpoint_boundaries(self) -> None:
        self.assertEqual(_observation_weight("phoenix1", 23.5, 24), 1.0)
        self.assertEqual(_observation_weight("phoenix2", 25.5, 24), 2.0)
        self.assertEqual(_observation_weight("phoenix1", 23.499, 24), 0.5)
        self.assertEqual(_observation_weight("phoenix2", 25.501, 24), 1.0)
        self.assertEqual(_observation_weight("phoenix2", None, 24), 2.0)

    def test_weighted_chart_statistics_favor_phoenix2_and_keep_effective_support(self) -> None:
        statistics = _weighted_residual_statistics(
            np.asarray([0.0, 2.0]),
            np.asarray([1.0, 2.0]),
        )

        self.assertAlmostEqual(statistics["mean"], 4.0 / 3.0)
        self.assertAlmostEqual(statistics["location"], 4.0 / 3.0)
        self.assertEqual(statistics["median"], 2.0)
        self.assertAlmostEqual(statistics["effectiveSupport"], 9.0 / 5.0)
        self.assertAlmostEqual(
            statistics["effectiveSupport"],
            _effective_sample_size(np.asarray([1.0, 2.0])),
        )

    def test_solve_my_hurt_shortcut_converts_only_phoenix1_score_rows(self) -> None:
        chart_id = SOLVE_MY_HURT_SHORTCUT_D26_CHART_ID
        self.assertAlmostEqual(
            convert_phoenix1_score(chart_id, 950_000),
            923_684.2105263158,
        )
        self.assertEqual(convert_phoenix1_score(chart_id, 1_000_000), 1_000_000)
        self.assertEqual(convert_phoenix1_score("another-chart", 950_000), 950_000)

        rows = pd.DataFrame([
            {
                "playerId": "p",
                "chartId": chart_id,
                "score": 983_532,
                "pumbility": 1_927.2,
            },
            {
                "playerId": "p",
                "chartId": "another-chart",
                "score": 983_532,
                "pumbility": 1_927.2,
            },
        ])
        adjusted = _apply_phoenix1_score_overrides(rows)

        special = adjusted[adjusted["chartId"] == chart_id].iloc[0]
        ordinary = adjusted[adjusted["chartId"] == "another-chart"].iloc[0]
        self.assertAlmostEqual(float(special["score"]), 974_864.6315789473)
        self.assertAlmostEqual(float(special["pumbility"]), 1_752.0)
        self.assertEqual(float(ordinary["score"]), 983_532)
        self.assertEqual(float(ordinary["pumbility"]), 1_927.2)

    def test_slam_d24_converts_and_rebands_phoenix1_score_rows(self) -> None:
        self.assertAlmostEqual(
            convert_phoenix1_score(SLAM_D24_CHART_ID, 950_000),
            928_693.1818181818,
        )
        self.assertEqual(
            convert_phoenix1_score(SLAM_D24_CHART_ID, 1_000_000),
            1_000_000,
        )
        self.assertAlmostEqual(
            convert_phoenix1_score(SLAM_D24_CHART_ID, 909_322),
            870_680.8068181819,
        )
        self.assertEqual(
            convert_phoenix1_pumbility(SLAM_D24_CHART_ID, 909_322, 1_150),
            1_035,
        )

        rows = pd.DataFrame([
            {
                "playerId": "p",
                "chartId": SLAM_D24_CHART_ID,
                "score": 909_322,
                "pumbility": 1_150,
            },
            {
                "playerId": "p",
                "chartId": "another-chart",
                "score": 909_322,
                "pumbility": 1_150,
            },
        ])
        adjusted = _apply_phoenix1_score_overrides(rows)

        slam = adjusted[adjusted["chartId"] == SLAM_D24_CHART_ID].iloc[0]
        ordinary = adjusted[adjusted["chartId"] == "another-chart"].iloc[0]
        self.assertAlmostEqual(float(slam["score"]), 870_680.8068181819)
        self.assertEqual(float(slam["pumbility"]), 1_035)
        self.assertEqual(float(ordinary["score"]), 909_322)
        self.assertEqual(float(ordinary["pumbility"]), 1_150)

        metadata = phoenix1_score_overrides_metadata()
        self.assertEqual(
            [item["chartId"] for item in metadata],
            [SOLVE_MY_HURT_SHORTCUT_D26_CHART_ID, SLAM_D24_CHART_ID],
        )
        self.assertEqual(
            metadata[1]["formula"],
            "(((score / 1000000 * 1004) - 300) / 704) * 1000000",
        )

    def test_phoenix1_ratings_recompute_pumbility_with_current_phoenix2_rules(self) -> None:
        snapshot = {
            "charts": [
                {
                    "id": "current",
                    "songName": "Current",
                    "type": "Single",
                    "level": 18,
                },
                {
                    "id": "missing-plate",
                    "songName": "Missing Plate",
                    "type": "Single",
                    "level": 20,
                },
            ],
            "scores": [
                {
                    "playerId": "p",
                    "chartId": "current",
                    "pumbility": 999,
                    "score": 970_000,
                    "plate": "Fair Game",
                    "isBroken": False,
                },
                {
                    "playerId": "p",
                    "chartId": "missing-plate",
                    "pumbility": 999,
                    "score": 970_000,
                    "plate": None,
                    "isBroken": False,
                },
            ],
        }
        catalog = pd.DataFrame(
            [
                {"chartId": "current", "type": "Single", "level": 20},
                {"chartId": "missing-plate", "type": "Single", "level": 20},
            ]
        )

        _, scores = _prepare_phoenix1_rating_frames(snapshot, catalog)

        self.assertEqual(scores["chartId"].tolist(), ["current"])
        self.assertEqual(
            scores.iloc[0]["pumbility"],
            phoenix2_pumbility("Single", 20, "S", "Fair Game"),
        )

    def test_special_phoenix1_rating_uses_the_converted_score(self) -> None:
        chart_id = SOLVE_MY_HURT_SHORTCUT_D26_CHART_ID
        snapshot = {
            "charts": [{
                "id": chart_id,
                "songName": "Solve My Hurt - SHORT CUT -",
                "type": "Double",
                "level": 26,
            }],
            "scores": [{
                "playerId": "p",
                "chartId": chart_id,
                "pumbility": 1_927.2,
                "score": 983_532,
                "plate": "Talented Game",
                "isBroken": False,
            }],
        }
        catalog = pd.DataFrame([
            {"chartId": chart_id, "type": "Double", "level": 26}
        ])

        _, scores = _prepare_phoenix1_rating_frames(snapshot, catalog)

        self.assertAlmostEqual(float(scores.iloc[0]["score"]), 974_864.6315789473)
        self.assertEqual(
            float(scores.iloc[0]["pumbility"]),
            phoenix2_pumbility("Double", 26, "S", "Talented Game"),
        )

    def test_slam_phoenix1_rating_uses_the_converted_score(self) -> None:
        snapshot = {
            "charts": [{
                "id": SLAM_D24_CHART_ID,
                "songName": "Slam",
                "type": "Double",
                "level": 24,
            }],
            "scores": [{
                "playerId": "p",
                "chartId": SLAM_D24_CHART_ID,
                "pumbility": 1_207.5,
                "score": 925_641,
                "plate": "Fair Game",
                "isBroken": False,
            }],
        }
        catalog = pd.DataFrame([{
            "chartId": SLAM_D24_CHART_ID,
            "type": "Double",
            "level": 24,
        }])

        _, scores = _prepare_phoenix1_rating_frames(snapshot, catalog)

        self.assertAlmostEqual(float(scores.iloc[0]["score"]), 893_953.9261363638)
        self.assertEqual(
            float(scores.iloc[0]["pumbility"]),
            phoenix2_pumbility("Double", 24, "A", "Fair Game"),
        )

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
                "id": f"joined-{index:02d}",
                "songName": f"Joined {index:02d}",
                "type": "Single",
                "level": 20 + index % 3,
            }
            for index in range(31)
        ]
        phoenix1_scores = [
            {
                "playerId": f"player-{player_index:02d}",
                "chartId": chart["id"],
                "pumbility": 100.0 + 10.0 * chart["level"],
                "score": 950_000,
                "plate": "Fair Game",
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

        self.assertEqual(coverage["model"], SCORE_PROJECTION_MODEL_NAME)
        self.assertEqual(
            coverage["populationFallbackModel"], SCORE_RESPONSE_MODEL_NAME
        )
        self.assertFalse(coverage["personalRawScoreInput"])
        self.assertEqual(coverage["sourceWeights"], {"phoenix1": 1, "phoenix2": 2})
        self.assertFalse(coverage["abilityDistanceWeighting"])
        self.assertEqual(coverage["phoenix2OverlapRowsRemovedFromPhoenix1"], 1)
        self.assertEqual(coverage["peerProjection"]["minimumUsablePeers"], 5)
        self.assertEqual(
            coverage["peerProjection"]["supportTargets"], [20, 10, 5]
        )
        self.assertEqual(coverage["peerProjection"]["initialRatingRadius"], 0.2)
        self.assertEqual(coverage["peerProjection"]["maximumRatingRadius"], 0.5)
        self.assertEqual(coverage["peerProjection"]["ratingRadiusStep"], 0.1)
        self.assertEqual(
            coverage["peerProjection"]["percentileWeighting"],
            "Phoenix 1 = 1; Phoenix 2 = 2",
        )
        self.assertEqual(
            coverage["modes"]["singles"]["sourceRows"],
            {"phoenix1": 309, "phoenix2": 1},
        )
        self.assertEqual(
            coverage["modes"]["singles"]["sourcePlayers"],
            {"phoenix1": 10, "phoenix2": 1},
        )
        self.assertEqual(
            len(model.peer_cohorts[_peer_cohort_key("singles", "joined-00")].scores),
            10,
        )
        self.assertEqual(
            sorted(
                model.peer_cohorts[
                    _peer_cohort_key("singles", "joined-00")
                ].weights.tolist()
            ),
            [1.0] * 9 + [2.0],
        )
        self.assertIn("player-00", model.training_player_ids)

    def test_peer_cohorts_use_all_joined_normalized_scores_regardless_of_rank(self) -> None:
        charts = [
            {
                "id": f"ranked-{index:03d}",
                "songName": f"Ranked {index:03d}",
                "type": "Single",
                "level": 20,
            }
            for index in range(301)
        ]
        scores = [
            {
                "playerId": f"peer-{player_index}",
                "chartId": chart["id"],
                "pumbility": 344.85 - chart_index * 0.001,
                "score": 999_999 - 1_000 * chart_index,
                "plate": "Fair Game",
                "recordedAt": "2026-08-08T00:00:00Z",
                "isBroken": False,
            }
            for player_index in range(6)
            for chart_index, chart in enumerate(charts)
        ]
        snapshot = {"players": [], "charts": charts, "scores": scores}
        combined = [
            {
                "chartId": chart["id"],
                "type": "Single",
                "estimatedDifficulty": 20.5,
            }
            for chart in charts
        ]

        model, _ = fit_score_response_model(None, snapshot, combined)
        included = model.predict(
            "new-player", "singles", 20.5, 20.5, "ranked-099"
        )
        expanded = model.predict(
            "new-player", "singles", 20.5, 20.5, "ranked-100"
        )
        unrestricted = model.predict(
            "new-player", "singles", 20.5, 20.5, "ranked-300"
        )

        self.assertEqual(included.source, "peer-all-q50")
        self.assertEqual(included.support_count, 6)
        self.assertEqual(expanded.source, "peer-all-q50")
        self.assertEqual(expanded.support_count, 6)
        self.assertEqual(unrestricted.source, "peer-all-q50")
        self.assertEqual(unrestricted.support_count, 6)

    def test_scores_without_a_normalizable_plate_are_excluded_from_all_peer_stages(self) -> None:
        charts = [
            {
                "id": f"normalization-{index:02d}",
                "songName": f"Normalization {index:02d}",
                "type": "Single",
                "level": 20,
            }
            for index in range(32)
        ]
        scores = [
            {
                "playerId": f"peer-{player_index}",
                "chartId": chart["id"],
                "pumbility": 344.85 - chart_index * 0.001,
                "score": 990_000 - chart_index,
                "plate": None if chart_index == 0 else "Fair Game",
                "recordedAt": "2026-08-08T00:00:00Z",
                "isBroken": False,
            }
            for player_index in range(6)
            for chart_index, chart in enumerate(charts)
        ]
        snapshot = {"players": [], "charts": charts, "scores": scores}
        combined = [
            {
                "chartId": chart["id"],
                "type": "Single",
                "estimatedDifficulty": 20.5,
            }
            for chart in charts
        ]

        model, _ = fit_score_response_model(None, snapshot, combined)
        invalid = model.predict(
            "new-player", "singles", 20.5, 20.5, "normalization-00"
        )
        valid = model.predict(
            "new-player", "singles", 20.5, 20.5, "normalization-01"
        )

        self.assertEqual(invalid.source, "population-full")
        self.assertEqual(valid.source, "peer-all-q50")


class PopulationScoreResponseTests(unittest.TestCase):
    def test_population_surface_applies_double_phoenix2_source_weight(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "playerId": f"p1-{index}",
                    "chartId": f"p1-chart-{index}",
                    "source": "phoenix1",
                    "scoringRating": 20.0,
                    "estimatedDifficulty": 20.0,
                    "calibratedScore": 100_000.0,
                }
                for index in range(5)
            ]
            + [
                {
                    "playerId": "p2",
                    "chartId": "p2-chart",
                    "source": "phoenix2",
                    "scoringRating": 20.0,
                    "estimatedDifficulty": 20.0,
                    "calibratedScore": 800_000.0,
                }
            ]
        )

        surface = _build_score_surface(rows)

        self.assertIsNotNone(surface)
        center = len(surface.rating_axis) // 2
        self.assertAlmostEqual(surface.score_grid[center, center], 300_000.0)

    @staticmethod
    def _fixture() -> tuple[dict, list[dict]]:
        difficulties = [17.0 + 0.5 * index for index in range(31)]
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
                        "pumbility": (
                            7.5 * (ability + 27.0)
                            if ability <= 23.0
                            else 375.0 + 15.0 * (ability - 23.0)
                        )
                        * 0.968,
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
        easier_player = self.model.predict(player_id, "singles", 21.5, 22.0)
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

    def test_serialized_model_preserves_predictions(self) -> None:
        restored = ScoreResponseModel.from_payload(self.model.to_payload())

        for player_id in ("surface-player-00", "new-player"):
            self.assertEqual(
                restored.predict(player_id, "singles", 22.0, 22.0),
                self.model.predict(player_id, "singles", 22.0, 22.0),
            )

    def test_projection_rating_lookup_uses_ranks_eleven_to_thirty_and_promotes_rank_thirty_one(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "chartId": f"chart-{index:02d}",
                    "pumbility": 400 - index,
                    "score": 990_000 - index,
                }
                for index in range(31)
            ]
        )

        full, leave_one_out = _rating_lookup(rows, "Single")

        self.assertAlmostEqual(full, skill_rating_for_pumbility("Single", 380.5))
        self.assertAlmostEqual(
            leave_one_out["chart-00"],
            skill_rating_for_pumbility("Single", 379.5),
        )
        self.assertAlmostEqual(leave_one_out["chart-30"], full)

    def test_projection_rating_lookup_requires_rank_thirty_one_for_leave_one_out(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "chartId": f"chart-{index:02d}",
                    "pumbility": 400 - index,
                    "score": 990_000 - index,
                }
                for index in range(30)
            ]
        )

        full, leave_one_out = _rating_lookup(rows, "Single")

        self.assertIsNotNone(full)
        self.assertEqual(set(leave_one_out), set(rows["chartId"]))
        self.assertTrue(all(value is None for value in leave_one_out.values()))


class PeerScoreProjectionTests(unittest.TestCase):
    @staticmethod
    def _model(
        ratings: list[float],
        scores: list[float],
        *,
        player_ids: list[str] | None = None,
        ranks: list[int] | None = None,
        weights: list[float] | None = None,
    ) -> ScoreResponseModel:
        surface = _ScoreSurface(
            np.asarray([19.0, 21.0]),
            np.asarray([19.0, 21.0]),
            np.full((2, 2), 990_000.0),
            np.full((2, 2), 20.0),
        )
        ids = player_ids or [f"peer-{index}" for index in range(len(ratings))]
        cohort = _PeerScoreCohort(
            np.asarray([public_player_key(value) for value in ids], dtype=np.str_),
            np.asarray(ratings, dtype=float),
            np.asarray(scores, dtype=float),
            np.asarray(ranks or [1] * len(ratings), dtype=np.int64),
            np.asarray(weights, dtype=float) if weights is not None else None,
        )
        return ScoreResponseModel(
            {"singles": surface},
            {},
            frozenset(),
            {_peer_cohort_key("singles", "target-chart"): cohort},
        )

    def test_exactly_five_peers_enable_the_ordinary_median_projection(self) -> None:
        model = self._model(
            [20.0, 20.0, 20.0, 20.0, 20.0],
            [900_000, 910_000, 920_000, 930_000, 940_000],
            ranks=[301, 302, 303, 304, 305],
        )

        result = model.predict(
            "target-player", "singles", 20.0, 20.0, "target-chart"
        )

        self.assertEqual(result.source, "peer-all-q50")
        self.assertEqual(result.support_count, 5)
        self.assertEqual(result.confidence, "low")
        self.assertEqual(result.score, 920_000)

    def test_one_to_four_peers_fall_back_to_population(self) -> None:
        for support in range(1, 5):
            with self.subTest(support=support):
                model = self._model(
                    [20.0 + 0.1 * index for index in range(support)],
                    [900_000 + 10_000 * index for index in range(support)],
                )

                result = model.predict(
                    "target-player", "singles", 20.0, 20.0, "target-chart"
                )

                self.assertEqual(result.source, "population-full")
                self.assertEqual(result.score, 990_000)

    def test_zero_peers_within_half_a_rating_falls_back_to_population(self) -> None:
        model = self._model([20.5001], [900_000])

        result = model.predict(
            "target-player", "singles", 20.0, 20.0, "target-chart"
        )

        self.assertEqual(result.source, "population-full")
        self.assertEqual(result.score, 990_000)

    def test_twenty_peer_pass_expands_from_point_two_to_point_five(self) -> None:
        model = self._model(
            [20.1] * 5 + [20.25] * 5 + [20.35] * 5 + [20.45] * 5,
            [900_000 + 1_000 * index for index in range(20)],
        )

        result = model.predict(
            "target-player", "singles", 20.0, 20.0, "target-chart"
        )

        self.assertEqual(result.source, "peer-all-q50")
        self.assertEqual(result.support_count, 20)
        self.assertEqual(result.confidence, "high")
        self.assertEqual(result.score, 909_000)

    def test_ten_peer_pass_restarts_at_the_narrowest_radius(self) -> None:
        model = self._model(
            [20.1] * 12 + [20.45] * 4,
            [900_000 + 1_000 * index for index in range(12)]
            + [990_000, 991_000, 992_000, 993_000],
        )

        result = model.predict(
            "target-player", "singles", 20.0, 20.0, "target-chart"
        )

        self.assertEqual(result.source, "peer-all-q50")
        self.assertEqual(result.support_count, 12)
        self.assertEqual(result.confidence, "medium")
        self.assertEqual(result.score, 905_000)

    def test_five_peer_pass_restarts_at_the_narrowest_radius(self) -> None:
        model = self._model(
            [20.1] * 5 + [20.45] * 4,
            [900_000 + 1_000 * index for index in range(5)]
            + [990_000, 991_000, 992_000, 993_000],
        )

        result = model.predict(
            "target-player", "singles", 20.0, 20.0, "target-chart"
        )

        self.assertEqual(result.source, "peer-all-q50")
        self.assertEqual(result.support_count, 5)
        self.assertEqual(result.confidence, "low")
        self.assertEqual(result.score, 902_000)

    def test_weighted_percentile_includes_the_half_rating_boundary(self) -> None:
        model = self._model(
            [20.0, 20.5, 20.5, 20.5, 20.5],
            [100_000, 200_000, 300_000, 400_000, 1_000_000],
            weights=[1, 1, 1, 2, 2],
        )

        result = model.predict(
            "target-player", "singles", 20.0, 20.0, "target-chart"
        )

        self.assertEqual(result.source, "peer-all-q50")
        self.assertEqual(result.support_count, 5)
        self.assertEqual(result.score, 400_000)

    def test_peer_search_excludes_the_selected_player(self) -> None:
        model = self._model(
            [20.0, 20.1, 20.2, 20.3, 20.5, 20.0],
            [900_000, 910_000, 920_000, 930_000, 940_000, 1_000_000],
            player_ids=[
                "peer-0",
                "peer-1",
                "peer-2",
                "peer-3",
                "peer-4",
                "target-player",
            ],
        )

        result = model.predict(
            "target-player", "singles", 20.0, 20.0, "target-chart"
        )

        self.assertEqual(result.support_count, 5)
        self.assertEqual(result.score, 920_000)

    def test_peer_median_replaces_a_higher_population_projection(self) -> None:
        model = self._model(
            [20.0, 20.0, 20.0, 20.0, 20.0],
            [900_000, 910_000, 920_000, 930_000, 940_000],
        )

        peer = model.predict(
            "target-player", "singles", 20.0, 20.0, "target-chart"
        )
        population = model.predict(
            "target-player", "singles", 20.0, 20.0, "other-chart"
        )

        self.assertLess(peer.score, population.score)
        self.assertEqual(peer.source, "peer-all-q50")


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
                    "bpmMin": 90,
                    "bpmMax": 180,
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
                estimate = 21.0000000001
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
                    "bpmMin": 90,
                    "bpmMax": 180,
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
                        "pumbility": 344.85,
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
        self.assertEqual(index["schemaVersion"], RECOMMENDATION_SCHEMA_VERSION)
        self.assertEqual(index["shardCount"], 2)
        self.assertEqual(len(index["players"]), 3)
        self.assertNotIn("charts", index)
        self.assertNotIn("modes", index["players"][0])
        self.assertIn("Phoenix 1 + Phoenix 2", index["method"]["scoreProjectionData"])
        self.assertIn("scoreProjectionCoverage", index["method"])
        self.assertEqual(index["method"]["baselineRanks"], [11, 30])
        self.assertEqual(index["method"]["recommendationRatingRanks"], [1, 20])
        self.assertEqual(index["method"]["phoenix1RatingRanks"], [1, 20])
        self.assertEqual(index["method"]["phoenix2RatingRanks"], [1, 20])
        self.assertEqual(index["method"]["phoenix2RatingScoreThreshold"], 20)
        self.assertEqual(index["method"]["candidateUpperRadius"], 0.5)
        self.assertEqual([len(shards[number]["players"]) for number in shards], [2, 1])
        self.assertIn("modes", shards[0]["players"][0])

    def test_player_only_refresh_publishes_full_filter_pool_and_top_twenty(self) -> None:
        generated = "2026-08-09T06:00:00Z"
        index, model, score_model_bytes, phoenix1_shards, phoenix2_shards = (
            build_recommendation_model_artifacts(
                self.snapshot,
                self.snapshot,
                combined_charts=self.combined,
                phoenix2_slopes={"singles": 10.0},
                generation_key="daily-generation",
                generated_at_utc=generated,
            )
        )
        self.assertEqual(index["method"]["candidateUpperRadius"], 0.5)
        self.assertEqual(model["method"]["candidateUpperRadius"], 0.5)
        self.assertEqual(
            index["players"][0]["scoreProgress"],
            {
                "singles": {"validScoreCount": 30, "requiredScoreCount": 30},
                "doubles": {"validScoreCount": 0, "requiredScoreCount": 30},
            },
        )
        store = MemoryBlobStore()
        publish_recommendation_model_artifacts(
            store,
            index=index,
            model=model,
            score_model_bytes=score_model_bytes,
            phoenix1_shards=phoenix1_shards,
            phoenix2_shards=phoenix2_shards,
            index_path="analysis/recommendations/latest.json",
        )
        player_key = index["players"][0]["playerKey"]
        client = Mock()
        client.fetch_page_collection.return_value = [
            {
                **self.snapshot["scores"][0],
                "pumbility": 999.0,
                "recordedAt": "2026-08-09T06:00:01Z",
            }
        ]
        now = datetime(2026, 8, 9, 6, 0, 2, tzinfo=timezone.utc)

        response = refresh_player_recommendations(
            store,
            client,
            index_path="analysis/recommendations/latest.json",
            player_key=player_key,
            now=lambda: now,
        )

        client.fetch_page_collection.assert_called_once_with(
            "api/v2/players/player/scores",
            {"mix": "Phoenix2", "limit": 100},
        )
        self.assertEqual(response["modelGeneration"], "daily-generation")
        self.assertTrue(cached_player_is_fresh(response, index, now=now))
        incompatible = {**response, "schemaVersion": RECOMMENDATION_SCHEMA_VERSION - 1}
        self.assertFalse(cached_player_is_fresh(incompatible, index, now=now))
        self.assertTrue(with_staleness(incompatible, index)["stale"])
        for mode in response["player"]["modes"].values():
            self.assertIn("filterCandidates", mode)
            self.assertGreaterEqual(
                len(mode["filterCandidates"]), len(mode["topRecommendations"])
            )
            self.assertLessEqual(len(mode["topRecommendations"]), 20)
            self.assertTrue(
                all(
                    not any(str(key).startswith("_") for key in row)
                    for row in mode["filterCandidates"]
                )
            )
        self.assertEqual(
            store.get_json(recommendation_player_path(player_key)), response
        )

    def test_binary_score_model_round_trips_without_json_bloat(self) -> None:
        model, _ = fit_score_response_model(
            self.snapshot, self.snapshot, _recommendation_chart_rows(self.combined)
        )
        binary = model.to_npz_bytes()
        restored = ScoreResponseModel.from_npz_bytes(binary)

        self.assertEqual(restored.to_payload(), model.to_payload())
        self.assertLess(len(binary), len(json.dumps(model.to_payload()).encode("utf-8")))

    def test_legacy_binary_without_peer_weights_defaults_to_equal_weight(self) -> None:
        snapshot = {
            **self.snapshot,
            "scores": [
                {**row, "plate": "Fair Game"} for row in self.snapshot["scores"]
            ],
        }
        model, _ = fit_score_response_model(
            snapshot, snapshot, _recommendation_chart_rows(self.combined)
        )
        with np.load(io.BytesIO(model.to_npz_bytes()), allow_pickle=False) as arrays:
            legacy_arrays = {
                name: arrays[name]
                for name in arrays.files
                if name != "peer_weights"
            }
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **legacy_arrays)

        restored = ScoreResponseModel.from_npz_bytes(buffer.getvalue())

        self.assertTrue(restored.peer_cohorts)
        self.assertTrue(
            all(
                np.allclose(cohort.weights, 1.0)
                for cohort in restored.peer_cohorts.values()
            )
        )

    def test_legacy_binary_without_peer_arrays_uses_population_fallback(self) -> None:
        model, _ = fit_score_response_model(
            self.snapshot, self.snapshot, _recommendation_chart_rows(self.combined)
        )
        with np.load(io.BytesIO(model.to_npz_bytes()), allow_pickle=False) as arrays:
            legacy_arrays = {
                name: arrays[name]
                for name in arrays.files
                if not name.startswith("peer_")
            }
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **legacy_arrays)

        restored = ScoreResponseModel.from_npz_bytes(buffer.getvalue())
        result = restored.predict(
            "new-player", "singles", 20.0, 20.0, "chart-30"
        )

        self.assertEqual(restored.peer_cohorts, {})
        self.assertEqual(result.source, "population-full")

    def test_legacy_rankless_peer_binary_uses_population_fallback(self) -> None:
        snapshot = {
            **self.snapshot,
            "scores": [
                {**row, "plate": "Fair Game"} for row in self.snapshot["scores"]
            ],
        }
        model, _ = fit_score_response_model(
            snapshot, snapshot, _recommendation_chart_rows(self.combined)
        )
        self.assertTrue(model.peer_cohorts)
        with np.load(io.BytesIO(model.to_npz_bytes()), allow_pickle=False) as arrays:
            legacy_arrays = {
                name: arrays[name]
                for name in arrays.files
                if name != "peer_ranks"
            }
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **legacy_arrays)

        restored = ScoreResponseModel.from_npz_bytes(buffer.getvalue())
        result = restored.predict(
            "new-player", "singles", 20.0, 20.0, "chart-30"
        )

        self.assertEqual(restored.peer_cohorts, {})
        self.assertEqual(result.source, "population-full")

    def test_player_artifacts_match_daily_algorithm_with_zero_pumbility_plate_rows(self) -> None:
        phoenix1 = {
            **self.snapshot,
            "scores": [
                *self.snapshot["scores"],
                {
                    "playerId": "player",
                    "chartId": "chart-30",
                    "pumbility": 0,
                    "score": 985000,
                    "plate": "WG",
                    "recordedAt": "2026-08-08T00:00:00Z",
                    "isBroken": False,
                },
            ],
        }
        phoenix2 = {
            **self.snapshot,
            "scores": [
                *self.snapshot["scores"],
                {
                    "playerId": "player",
                    "chartId": "chart-31",
                    "pumbility": 0,
                    "score": 980000,
                    "plate": "SG",
                    "recordedAt": "2026-08-08T00:00:00Z",
                    "isBroken": False,
                },
            ],
        }
        generation = "exact-parity-generation"
        artifacts = build_recommendation_model_artifacts(
            phoenix1,
            phoenix2,
            combined_charts=self.combined,
            phoenix2_slopes={"singles": 10.0},
            generation_key=generation,
            generated_at_utc="2026-08-09T06:00:00Z",
        )
        index, model, score_bytes, p1_shards, p2_shards = artifacts
        store = MemoryBlobStore()
        publish_recommendation_model_artifacts(
            store,
            index=index,
            model=model,
            score_model_bytes=score_bytes,
            phoenix1_shards=p1_shards,
            phoenix2_shards=p2_shards,
            index_path="analysis/recommendations/latest.json",
        )
        player_key = index["players"][0]["playerKey"]
        client = Mock()
        client.fetch_page_collection.return_value = []

        artifact_response = refresh_player_recommendations(
            store,
            client,
            index_path="analysis/recommendations/latest.json",
            player_key=player_key,
        )
        direct_score_model, _ = fit_score_response_model(
            phoenix1, phoenix2, _recommendation_chart_rows(self.combined)
        )
        direct = build_player_recommendation(
            "player",
            phoenix2,
            self.combined,
            {"singles": 10.0},
            direct_score_model,
            phoenix1_snapshot=phoenix1,
            plate_model=PlateProjectionModel(phoenix1, phoenix2),
            include_candidates=True,
        )

        self.assertEqual(
            artifact_response["player"]["modes"],
            direct["modes"],
        )
        stored_player = p1_shards[0]["players"][0]
        self.assertTrue(
            any(row["chartId"] == "chart-30" for row in stored_player["plateScores"])
        )
        self.assertFalse(
            any(row["chartId"] == "chart-30" for row in stored_player["scores"])
        )
        self.assertTrue(
            any(
                row["chartId"] == "chart-31"
                for row in p2_shards[0]["players"][0]["scores"]
            )
        )

    def test_player_refresh_switches_all_inputs_when_daily_model_changes(self) -> None:
        first = build_recommendation_model_artifacts(
            self.snapshot,
            self.snapshot,
            combined_charts=self.combined,
            phoenix2_slopes={"singles": 10.0},
            generation_key="first-generation",
            generated_at_utc="2026-08-09T06:00:00Z",
        )
        latest_score = {
            **self.snapshot["scores"][0],
            "chartId": "chart-30",
            "pumbility": 350.0,
            "recordedAt": "2026-08-09T06:00:01Z",
        }
        latest_snapshot = {
            **self.snapshot,
            "scores": [*self.snapshot["scores"], latest_score],
        }
        latest = build_recommendation_model_artifacts(
            self.snapshot,
            latest_snapshot,
            combined_charts=self.combined,
            phoenix2_slopes={"singles": 10.0},
            generation_key="latest-generation",
            generated_at_utc="2026-08-09T06:01:00Z",
        )
        store = MemoryBlobStore()
        publish_recommendation_model_artifacts(
            store,
            index=first[0],
            model=first[1],
            score_model_bytes=first[2],
            phoenix1_shards=first[3],
            phoenix2_shards=first[4],
            index_path="analysis/recommendations/latest.json",
        )
        player_key = first[0]["players"][0]["playerKey"]

        class GenerationChangingClient:
            def fetch_page_collection(self, path: str, params: dict) -> list[dict]:
                publish_recommendation_model_artifacts(
                    store,
                    index=latest[0],
                    model=latest[1],
                    score_model_bytes=latest[2],
                    phoenix1_shards=latest[3],
                    phoenix2_shards=latest[4],
                    index_path="analysis/recommendations/latest.json",
                )
                return []

        response = refresh_player_recommendations(
            store,
            GenerationChangingClient(),
            index_path="analysis/recommendations/latest.json",
            player_key=player_key,
        )

        self.assertEqual(response["modelGeneration"], "latest-generation")
        state = store.get_json(recommendation_player_state_path(player_key))
        self.assertEqual(len(state["scores"]), 31)
        self.assertEqual(response["player"]["modes"]["singles"]["validScoreCount"], 31)

    def test_rating_uses_top_twenty_and_preserves_chart_difficulty_fields(self) -> None:
        result = build_player_recommendation(
            "player",
            self.snapshot,
            self.combined,
            {"singles": 10.0},
            self._fixed_score_model(),
        )["modes"]["singles"]

        self.assertTrue(result["eligible"])
        self.assertEqual(result["baselineRanks"], [11, 30])
        self.assertEqual(result["ratingBaselineRanks"], [1, 20])
        self.assertEqual(result["ratingBaselineLabel"], "top 20 scores")
        self.assertEqual(result["baselinePumbility"], 344.85)
        self.assertEqual(result["scoringRating"], 20.5)
        ids = {row["chartId"] for row in result["filterCandidates"]}
        self.assertIn("chart-00", ids)
        self.assertIn("chart-30", ids)
        self.assertIn("chart-31", ids)
        self.assertIn("chart-32", ids)
        self.assertEqual(
            next(row for row in result["filterCandidates"] if row["chartId"] == "chart-32")[
                "level"
            ],
            16,
        )
        self.assertIn("chart-33", ids)
        self.assertIn("chart-34", ids)
        displayed_ids = {row["chartId"] for row in result["topRecommendations"]}
        self.assertNotIn("chart-33", displayed_ids)
        self.assertNotIn("chart-34", displayed_ids)
        self.assertEqual(result["candidateRange"], [None, 21.0])
        easy = next(row for row in result["filterCandidates"] if row["chartId"] == "chart-30")
        self.assertEqual(easy["estimatedDifficulty"], 20.0)
        self.assertEqual(easy["difficultyDelta"], -0.5)
        self.assertEqual((easy["bpmMin"], easy["bpmMax"]), (90, 180))
        self.assertIsNotNone(easy["projectedGrade"])
        self.assertIsNotNone(easy["projectedPlateCode"])
        self.assertEqual(easy["plateProjectionSource"], "population")
        self.assertEqual(
            easy["expectedPumbility"],
            phoenix2_pumbility(
                easy["type"],
                easy["level"],
                easy["projectedGrade"],
                easy["projectedPlate"],
            ),
        )
        self.assertEqual(easy["projectedGain"], easy["expectedPumbility"])
        self.assertIsNone(result["currentTop50CutoffPumbility"])
        far_easier = next(
            row for row in result["filterCandidates"] if row["chartId"] == "chart-32"
        )
        self.assertEqual(far_easier["projectedScore"], 970_000)
        self.assertEqual(far_easier["scoreProjectionSource"], "population-crossfit")
        self.assertEqual(far_easier["scoreProjectionSupportCount"], 75)
        self.assertEqual(far_easier["scoreProjectionConfidence"], "medium")
        self.assertEqual(result["scoreProjectionModel"], SCORE_PROJECTION_MODEL_NAME)
        self.assertEqual(TOP_PUMBILITY_COUNT, 50)
        self.assertEqual(TOP_RECOMMENDATION_COUNT, 20)
        self.assertLessEqual(len(result["topRecommendations"]), 20)

    def test_current_pumbility_uses_the_authoritative_phoenix2_value(self) -> None:
        authoritative = 123.456
        scores = [dict(row) for row in self.snapshot["scores"]]
        scores[-1]["pumbility"] = authoritative
        mode = build_player_recommendation(
            "player",
            {**self.snapshot, "scores": scores},
            self.combined,
            {"singles": 10.0},
            self._fixed_score_model(),
        )["modes"]["singles"]

        historical = next(
            row for row in mode["filterCandidates"] if row["chartId"] == "chart-29"
        )
        self.assertEqual(historical["existingPumbility"], authoritative)
        self.assertAlmostEqual(
            mode["currentTop50Pumbility"],
            sum(sorted((float(row["pumbility"]) for row in scores), reverse=True)[:50]),
        )

    def test_current_top_fifty_includes_scores_below_level_sixteen(self) -> None:
        low_level_score = {
            **self.snapshot["scores"][0],
            "chartId": "chart-34",
            "pumbility": 123.456,
        }
        modes = build_player_recommendation(
            "player",
            {**self.snapshot, "scores": [*self.snapshot["scores"], low_level_score]},
            self.combined,
            {"singles": 10.0},
            self._fixed_score_model(),
        )["modes"]

        expected = 30 * 344.85 + 123.456
        self.assertAlmostEqual(modes["singles"]["currentTop50Pumbility"], expected)
        self.assertAlmostEqual(modes["overall"]["currentTop50Pumbility"], expected)
        self.assertIn(
            "chart-34",
            {row["chartId"] for row in modes["singles"]["filterCandidates"]},
        )
        self.assertNotIn(
            "chart-34",
            {row["chartId"] for row in modes["singles"]["topRecommendations"]},
        )

    def test_overall_uses_shared_single_and_double_top_fifty(self) -> None:
        double_charts = []
        double_scores = []
        double_combined = []
        for index, chart in enumerate(self.snapshot["charts"]):
            chart_id = f"double-{index:02d}"
            double_charts.append(
                {
                    **chart,
                    "id": chart_id,
                    "songName": f"Double Chart {index}",
                    "type": "Double",
                    "difficulty": f"D{chart['level']}",
                }
            )
            source = self.combined[index]
            double_combined.append(
                {
                    **source,
                    "mode": "Doubles",
                    "songName": f"Double Chart {index}",
                    "difficulty": f"D{chart['level']}",
                    "type": "Double",
                    "chartId": chart_id,
                }
            )
            if index < 30:
                double_scores.append(
                    {
                        **self.snapshot["scores"][index],
                        "chartId": chart_id,
                        "pumbility": 500.0 - index,
                    }
                )
        snapshot = {
            **self.snapshot,
            "charts": [*self.snapshot["charts"], *double_charts],
            "scores": [*self.snapshot["scores"], *double_scores],
        }
        modes = build_player_recommendation(
            "player",
            snapshot,
            [*self.combined, *double_combined],
            {"singles": 10.0, "doubles": 10.0},
            self._fixed_score_model(),
        )["modes"]
        overall = modes["overall"]

        expected_total = sum(500.0 - index for index in range(30)) + 20 * 344.85
        self.assertAlmostEqual(
            modes["singles"]["currentTop50Pumbility"], 30 * 344.85
        )
        self.assertAlmostEqual(
            modes["doubles"]["currentTop50Pumbility"],
            sum(500.0 - index for index in range(30)),
        )
        self.assertAlmostEqual(overall["currentTop50Pumbility"], expected_total)
        self.assertEqual(overall["currentTop50Count"], 50)
        self.assertEqual(overall["top50ModeCounts"], {"singles": 20, "doubles": 30})
        self.assertEqual(
            overall["sourceRecommendationCounts"],
            {
                "singles": len(modes["singles"]["topRecommendations"]),
                "doubles": len(modes["doubles"]["topRecommendations"]),
            },
        )
        filter_source_ids = {
            row["chartId"]
            for mode_key in ("singles", "doubles")
            for row in modes[mode_key]["filterCandidates"]
        }
        self.assertEqual(
            {row["chartId"] for row in overall["filterCandidates"]},
            filter_source_ids,
        )
        self.assertLessEqual(len(overall["topRecommendations"]), 20)
        self.assertTrue(
            {row["type"] for row in overall["topRecommendations"]}
            .issubset({"Single", "Double"})
        )
        single_mode_row = next(
            row
            for row in modes["singles"]["topRecommendations"]
            if row["chartId"] == "chart-30"
        )
        overall_row = next(
            row for row in overall["filterCandidates"] if row["chartId"] == "chart-30"
        )
        self.assertLessEqual(overall_row["projectedGain"], single_mode_row["projectedGain"])
        self.assertTrue(
            all(
                not any(str(key).startswith("_") for key in row)
                for mode in modes.values()
                for row in mode.get("filterCandidates", [])
            )
        )

    def test_overall_remains_available_when_only_one_mode_can_be_rated(self) -> None:
        modes = build_player_recommendation(
            "player",
            self.snapshot,
            self.combined,
            {"singles": 10.0},
            self._fixed_score_model(),
        )["modes"]

        overall = modes["overall"]
        self.assertTrue(overall["eligible"])
        self.assertEqual(
            overall["sourceModeEligibility"],
            {"singles": True, "doubles": False},
        )
        self.assertEqual(overall["sourceRecommendationCounts"]["doubles"], 0)
        self.assertEqual(
            overall["currentTop50Pumbility"],
            modes["singles"]["currentTop50Pumbility"],
        )
        self.assertEqual(
            [row["chartId"] for row in overall["topRecommendations"]],
            [row["chartId"] for row in modes["singles"]["topRecommendations"]],
        )

    def test_top_twenty_controls_display_and_candidates_while_ranks_eleven_to_thirty_control_projection(self) -> None:
        scores = [
            {
                **row,
                "pumbility": 400.0 if index < 10 else 344.85,
            }
            for index, row in enumerate(self.snapshot["scores"])
        ]
        model = self._fixed_score_model()
        mode = build_player_recommendation(
            "player",
            {**self.snapshot, "scores": scores},
            self.combined,
            {"singles": 10.0},
            model,
        )["modes"]["singles"]

        self.assertEqual(
            mode["scoringRating"],
            round(skill_rating_for_pumbility("Single", 372.425), 3),
        )
        self.assertEqual(mode["projectionRating"], 20.5)
        self.assertIn("chart-33", {row["chartId"] for row in mode["filterCandidates"]})
        unplayed_call = next(
            call
            for call in model.predict.call_args_list
            if call.args[-1] == "chart-30"
        )
        self.assertAlmostEqual(unplayed_call.args[2], 20.5)

    def test_fewer_than_thirty_scores_keep_top_twenty_rating_without_projection(self) -> None:
        snapshot = {**self.snapshot, "scores": self.snapshot["scores"][:29]}
        mode = build_player_recommendation(
            "player", snapshot, self.combined, {"singles": 10.0}
        )["modes"]["singles"]
        self.assertTrue(mode["eligible"])
        self.assertEqual(mode["validScoreCount"], 29)
        self.assertEqual(mode["baselineRanks"], [11, 30])
        self.assertEqual(mode["ratingBaselineRanks"], [1, 20])
        self.assertEqual(mode["ratingBaselineLabel"], "top 20 scores")
        self.assertIsNone(mode["baselinePumbility"])
        self.assertEqual(mode["baselineLabel"], "ranks 11-30")
        self.assertIsNone(mode["projectionRating"])
        self.assertFalse(mode["projectionAvailable"])

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
            [row["projectedScore"] for row in original["filterCandidates"]],
            [row["projectedScore"] for row in changed["filterCandidates"]],
        )
        self.assertEqual(original["projectionRating"], changed["projectionRating"])
        for legacy_field in ("baselineScore", "scorePointsPerDifficulty"):
            self.assertNotIn(legacy_field, original)

    def test_projected_score_is_not_floored_at_the_existing_raw_score(self) -> None:
        mode = build_player_recommendation(
            "player",
            self.snapshot,
            self.combined,
            {"singles": 10.0},
            self._fixed_score_model(800_000),
        )["modes"]["singles"]

        played = next(
            row for row in mode["filterCandidates"] if row["chartId"] == "chart-00"
        )
        existing_score = next(
            row["score"]
            for row in self.snapshot["scores"]
            if row["chartId"] == "chart-00"
        )
        self.assertGreater(existing_score, played["projectedScore"])
        self.assertEqual(played["projectedScore"], 800_000)

    def test_single_score_uses_that_score_as_baseline(self) -> None:
        snapshot = {**self.snapshot, "scores": self.snapshot["scores"][:1]}
        mode = build_player_recommendation(
            "player", snapshot, self.combined, {"singles": 10.0}
        )["modes"]["singles"]
        self.assertTrue(mode["eligible"])
        self.assertEqual(mode["baselineRanks"], [11, 30])
        self.assertEqual(mode["ratingBaselineRanks"], [1, 1])
        self.assertEqual(mode["ratingBaselineLabel"], "all 1 available score")
        self.assertIsNone(mode["baselinePumbility"])
        self.assertIsNone(mode["projectionRating"])

    def test_level_fifteen_scores_inform_rating_and_remain_filterable(self) -> None:
        low_score = {
            **self.snapshot["scores"][0],
            "chartId": "chart-34",
            "pumbility": 304.92,
        }
        snapshot = {**self.snapshot, "scores": [low_score]}
        mode = build_player_recommendation(
            "player", snapshot, self.combined, {"singles": 10.0}
        )["modes"]["singles"]

        self.assertTrue(mode["eligible"])
        self.assertEqual(mode["scoringRating"], 15.0)
        self.assertIn(
            "chart-34", {row["chartId"] for row in mode["filterCandidates"]}
        )
        self.assertNotIn(
            "chart-34", {row["chartId"] for row in mode["topRecommendations"]}
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
            estimate = 20.0 if index <= 29 else 22.0
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

    def test_rating_source_switches_at_twenty_phoenix2_scores(self) -> None:
        snapshot, combined, prepared_phoenix1 = self._rating_source_fixture()
        below = {**snapshot, "scores": snapshot["scores"][:19]}
        below_mode = build_player_recommendation(
            "player",
            below,
            combined,
            {"singles": 10.0},
            prepared_phoenix1=prepared_phoenix1,
        )["modes"]["singles"]

        self.assertEqual(below_mode["phoenix2ScoreCount"], 19)
        self.assertEqual(
            below_mode["phoenix2ScoreThreshold"], PHOENIX2_RATING_SCORE_THRESHOLD
        )
        self.assertEqual(below_mode["ratingSource"], "phoenix1")
        self.assertEqual(
            below_mode["scoringRating"],
            round(skill_rating_for_pumbility("Single", 549.5), 3),
        )
        self.assertEqual(below_mode["ratingBaselineRanks"], [1, 20])
        self.assertEqual(below_mode["ratingBaselineLabel"], "top 20 scores")
        p1_only_chart = next(
            row for row in below_mode["filterCandidates"] if row["chartId"] == "source-chart-59"
        )
        self.assertFalse(p1_only_chart["played"])

        at_threshold = {**snapshot, "scores": snapshot["scores"][:20]}
        threshold_mode = build_player_recommendation(
            "player",
            at_threshold,
            combined,
            {"singles": 10.0},
            prepared_phoenix1=prepared_phoenix1,
        )["modes"]["singles"]
        self.assertEqual(threshold_mode["phoenix2ScoreCount"], 20)
        self.assertEqual(threshold_mode["ratingSource"], "phoenix2")
        self.assertEqual(
            threshold_mode["scoringRating"],
            round(skill_rating_for_pumbility("Single", 490.5), 3),
        )
        self.assertEqual(threshold_mode["ratingBaselineLabel"], "top 20 scores")
        self.assertEqual(threshold_mode["projectionRatingSource"], "phoenix1")

        projection_threshold = {**snapshot, "scores": snapshot["scores"][:30]}
        projection_mode = build_player_recommendation(
            "player",
            projection_threshold,
            combined,
            {"singles": 10.0},
            prepared_phoenix1=prepared_phoenix1,
        )["modes"]["singles"]
        self.assertEqual(projection_mode["projectionRatingSource"], "phoenix2")
        self.assertEqual(projection_mode["projectionRatingRanks"], [11, 30])

    def test_available_phoenix2_scores_are_used_when_phoenix1_is_unavailable(self) -> None:
        snapshot, combined, prepared_phoenix1 = self._rating_source_fixture()
        below = {**snapshot, "scores": snapshot["scores"][:19]}
        empty_phoenix1 = (
            prepared_phoenix1[0],
            prepared_phoenix1[1].iloc[0:0].copy(),
        )

        mode = build_player_recommendation(
            "player",
            below,
            combined,
            {"singles": 10.0},
            prepared_phoenix1=empty_phoenix1,
        )["modes"]["singles"]

        self.assertEqual(mode["ratingSource"], "phoenix2")
        self.assertEqual(mode["ratingSourceScoreCount"], 19)
        self.assertEqual(mode["ratingBaselineRanks"], [1, 19])
        self.assertEqual(mode["ratingBaselineLabel"], "all 19 available scores")
        self.assertEqual(
            mode["scoringRating"],
            round(skill_rating_for_pumbility("Single", 491.0), 3),
        )

    def test_incomplete_phoenix1_history_does_not_replace_available_phoenix2(self) -> None:
        snapshot, combined, prepared_phoenix1 = self._rating_source_fixture()
        below = {**snapshot, "scores": snapshot["scores"][:19]}
        partial_phoenix1 = (
            prepared_phoenix1[0],
            prepared_phoenix1[1].iloc[:19].copy(),
        )

        mode = build_player_recommendation(
            "player",
            below,
            combined,
            {"singles": 10.0},
            prepared_phoenix1=partial_phoenix1,
        )["modes"]["singles"]

        self.assertEqual(mode["ratingSource"], "phoenix2")
        self.assertEqual(mode["ratingBaselineRanks"], [1, 19])
        self.assertEqual(mode["ratingBaselineLabel"], "all 19 available scores")

    def test_exactly_twenty_phoenix1_scores_supply_the_fallback_rating(self) -> None:
        snapshot, combined, prepared_phoenix1 = self._rating_source_fixture()
        below = {**snapshot, "scores": snapshot["scores"][:19]}
        complete_phoenix1 = (
            prepared_phoenix1[0],
            prepared_phoenix1[1].iloc[:20].copy(),
        )

        mode = build_player_recommendation(
            "player",
            below,
            combined,
            {"singles": 10.0},
            prepared_phoenix1=complete_phoenix1,
        )["modes"]["singles"]

        self.assertEqual(mode["ratingSource"], "phoenix1")
        self.assertEqual(mode["ratingBaselineRanks"], [1, 20])
        self.assertEqual(mode["ratingBaselineLabel"], "top 20 scores")

    def test_short_phoenix1_history_without_phoenix2_is_ineligible(self) -> None:
        snapshot, combined, prepared_phoenix1 = self._rating_source_fixture()
        empty_scores = pd.DataFrame(columns=pd.DataFrame(snapshot["scores"]).columns)
        prepared_phoenix2 = (
            pd.DataFrame(snapshot["charts"]).rename(columns={"id": "chartId"}),
            empty_scores,
        )
        short_phoenix1 = (
            prepared_phoenix1[0],
            prepared_phoenix1[1].iloc[:19].copy(),
        )

        mode = build_player_recommendation(
            "player",
            snapshot,
            combined,
            {"singles": 10.0},
            prepared_phoenix1=short_phoenix1,
            prepared_phoenix2=prepared_phoenix2,
        )["modes"]["singles"]

        self.assertFalse(mode["eligible"])
        self.assertIsNone(mode["ratingSource"])

    def test_player_refresh_requires_the_top_twenty_recommendation_schema(self) -> None:
        index = {
            "schemaVersion": RECOMMENDATION_SCHEMA_VERSION,
            "storageSchemaVersion": 3,
            "refreshSupported": True,
        }

        with patch.dict(
            "os.environ", {"PLAYER_RECOMMENDATION_REFRESH_ENABLED": "true"}
        ):
            self.assertTrue(player_refresh_enabled(index))
            self.assertFalse(
                player_refresh_enabled(
                    {**index, "schemaVersion": RECOMMENDATION_SCHEMA_VERSION - 1}
                )
            )

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
        self.assertTrue(all(not row["played"] for row in mode["filterCandidates"]))
        self.assertTrue(all(row["projectedGain"] is None for row in mode["filterCandidates"]))

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
        self.assertTrue(all(not row["played"] for row in mode["filterCandidates"]))
        self.assertTrue(all(row["projectedScore"] == 970_000 for row in mode["filterCandidates"]))
        self.assertTrue(all(float(row["projectedGain"]) > 0 for row in mode["filterCandidates"]))


class WhatIfDifficultyTests(unittest.TestCase):
    def test_levels_include_three_each_side_and_stop_at_sixteen(self) -> None:
        self.assertEqual(what_if_levels(19), [16, 17, 18, 20, 21, 22])
        self.assertEqual(what_if_levels(16), [17, 18, 19])

    def test_phoenix2_shift_revalues_pumbility_across_formula_breakpoint(
        self,
    ) -> None:
        observation = {
            "source": "phoenix2",
            "chartType": "Single",
            "chartLevel": 23,
            "sourceSlope": 7.5,
            "score": 970_000,
            "plate": "Fair Game",
        }

        shift = _what_if_residual_shift(observation, 24)
        expected = (
            phoenix2_pumbility("Single", 24, "S", "Fair Game")
            - phoenix2_pumbility("Single", 23, "S", "Fair Game")
        ) / 7.5

        self.assertAlmostEqual(shift, expected)
        self.assertAlmostEqual(shift, 1.936)
        self.assertNotEqual(shift, 1.0)

    def test_phoenix1_and_missing_plate_use_normalized_level_fallback(self) -> None:
        phoenix1 = {
            "source": "phoenix1",
            "chartType": "Double",
            "chartLevel": 20,
            "sourceSlope": 12.0,
            "score": 970_000,
            "plate": "Fair Game",
        }
        missing_plate = {
            **phoenix1,
            "source": "phoenix2",
            "plate": None,
        }

        self.assertEqual(_what_if_residual_shift(phoenix1, 17), -3.0)
        self.assertEqual(_what_if_residual_shift(missing_plate, 23), 3.0)

    def test_estimate_uses_frozen_target_folder_model(self) -> None:
        result = pd.DataFrame(
            [
                {
                    "chartId": "subject",
                    "level": 19,
                    "levelReferenceResidualPb": -100.0,
                    "folderRangeCompression": 0.01,
                    "reliabilityWeight": 0.8,
                },
                {
                    "chartId": "target-folder-chart",
                    "level": 20,
                    "levelReferenceResidualPb": 0.5,
                    "folderRangeCompression": 0.75,
                    "reliabilityWeight": 1.0,
                },
            ]
        )
        observations = pd.DataFrame(
            [
                {
                    "chartId": "subject",
                    "source": "phoenix1",
                    "normalizedResidual": -0.25,
                    "chartLevel": 19,
                }
            ]
        )

        estimates = build_chart_what_if_estimates(result, observations).iloc[0]
        target = next(item for item in estimates if item["level"] == 20)

        # The shifted chart residual is 0.75. The frozen D20 reference is 0.5,
        # its compression is 0.75, and the subject reliability remains 0.8.
        self.assertEqual(target["estimatedDifficulty"], 20.44)

    def test_estimate_reweights_ability_and_reliability_for_target_level(self) -> None:
        result = pd.DataFrame(
            [
                {
                    "chartId": "subject",
                    "level": 19,
                    "levelReferenceResidualPb": 0.0,
                    "folderRangeCompression": 1.0,
                    "reliabilityWeight": 0.9,
                    "shrinkageK": 2.0,
                },
                {
                    "chartId": "target-folder-chart",
                    "level": 20,
                    "levelReferenceResidualPb": 0.0,
                    "folderRangeCompression": 1.0,
                    "reliabilityWeight": 1.0,
                    "shrinkageK": 2.0,
                },
            ]
        )
        observations = pd.DataFrame(
            [
                {
                    "chartId": "subject",
                    "source": "phoenix1",
                    "normalizedResidual": -1.0,
                    "chartLevel": 19,
                    "playerAbility": 20.5,
                },
                {
                    "chartId": "subject",
                    "source": "phoenix2",
                    "normalizedResidual": 1.0,
                    "chartLevel": 19,
                    "playerAbility": 19.4,
                },
            ]
        )

        estimates = build_chart_what_if_estimates(result, observations).iloc[0]
        target = next(item for item in estimates if item["level"] == 20)

        # At D20 the P1 row is full weight and the P2 row is 2 * 0.5, so the
        # shifted residuals [0, 2] have location 1 and effective support 2.
        self.assertEqual(target["estimatedDifficulty"], 20.3)

    def test_missing_target_model_and_no_observations_are_unavailable(self) -> None:
        result = pd.DataFrame(
            [
                {
                    "chartId": "observed",
                    "level": 19,
                    "levelReferenceResidualPb": 0.0,
                    "folderRangeCompression": 1.0,
                    "reliabilityWeight": 1.0,
                },
                {
                    "chartId": "unobserved",
                    "level": 20,
                    "levelReferenceResidualPb": 0.0,
                    "folderRangeCompression": 1.0,
                    "reliabilityWeight": 1.0,
                },
            ]
        )
        observations = pd.DataFrame(
            [
                {
                    "chartId": "observed",
                    "source": "phoenix1",
                    "normalizedResidual": 0.0,
                    "chartLevel": 19,
                }
            ]
        )

        estimates = build_chart_what_if_estimates(result, observations)
        observed = estimates.iloc[0]
        unobserved = estimates.iloc[1]

        self.assertIsNone(
            next(item for item in observed if item["level"] == 18)[
                "estimatedDifficulty"
            ]
        )
        self.assertTrue(
            all(item["estimatedDifficulty"] is None for item in unobserved)
        )


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
            "effectBandRank": 4,
            "effectBand": "Medium",
            "songName": "Current Chart",
            "difficulty": "S16",
            "type": "Single",
            "level": 16,
            "chartId": "current",
            "imageUrl": None,
            "noteCount": None,
            "stepArtist": None,
            "estimatedDifficulty": 16.5,
            "whatIfEstimates": [
                {"level": 17, "estimatedDifficulty": 17.123456}
            ],
            "averageDifficulty": 16.5,
            "difficultyDelta": 0.0,
            "folderMeasuredCharts": 2,
            "folderRangeCompression": 1.0,
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
            "effectBandRank": 2,
            "effectBand": "Very Easy",
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
        self.assertEqual(payload["schemaVersion"], COMBINED_TIER_SCHEMA_VERSION)
        self.assertEqual(payload["schemaVersion"], 3)
        self.assertEqual(
            [row["chartId"] for row in payload["singles"]],
            ["easier", "current"],
        )
        self.assertEqual(payload["singles"][0]["phoenix1Contributors"], 10)
        self.assertEqual(
            payload["singles"][0]["whatIfEstimates"],
            [{"level": 17, "estimatedDifficulty": 17.123456}],
        )
        self.assertEqual(
            set(payload["singles"][0]["whatIfEstimates"][0]),
            {"level", "estimatedDifficulty"},
        )
        for private_field in (
            "normalizedResidual",
            "chartResidualPb",
            "levelReferenceResidualPb",
            "reliabilityWeight",
            "sourceSlope",
            "score",
            "plate",
        ):
            self.assertNotIn(private_field, payload["singles"][0])
        self.assertEqual(
            payload["summary"]["method"]["displayMinimumOfficialLevel"], 16
        )
        self.assertEqual(payload["summary"]["method"]["difficultyDeltaScale"], 0.4)
        self.assertEqual(
            payload["summary"]["method"]["folderRangeNormalization"][
                "referenceMeasuredCharts"
            ],
            30,
        )


class RecommendationChartBoundaryTests(unittest.TestCase):
    def test_recommendation_chart_payload_keeps_all_official_levels(
        self,
    ) -> None:
        rows = _recommendation_chart_rows(
            [
                {"chartId": "sixteen", "type": "Single", "level": 16},
                {"chartId": "fifteen", "type": "Single", "level": 15},
            ]
        )

        self.assertEqual([row["chartId"] for row in rows], ["sixteen", "fifteen"])

    def test_manual_recommendations_keep_all_charts_for_filters(self) -> None:
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
                    "estimatedDifficulty": 16.5,
                },
                {
                    "chartId": "above-upper-bound",
                    "songName": "Above Upper Bound",
                    "type": "Single",
                    "level": 16,
                    "estimatedDifficulty": 16.5000000001,
                },
            ],
            "Single",
            16.0,
        )

        self.assertEqual(
            [row["chartId"] for row in mode["filterCandidates"]],
            ["fifteen", "rating-edge", "sixteen", "above-rating", "above-upper-bound"],
        )
        self.assertEqual(
            [row["chartId"] for row in mode["topRecommendations"]],
            ["rating-edge", "sixteen", "above-rating"],
        )
        self.assertEqual(mode["candidateRange"], [None, 16.5])
        self.assertTrue(
            all(row["expectedPumbility"] is None for row in mode["filterCandidates"])
        )
        self.assertTrue(all(row["projectedGain"] is None for row in mode["filterCandidates"]))


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
                        "singles": {
                            "eligible": True,
                            "validScoreCount": 30,
                            "projectionRatingRequiredScoreCount": 30,
                        },
                        "doubles": {
                            "eligible": False,
                            "validScoreCount": 10,
                            "requiredScoreCount": 30,
                        },
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
        self.assertEqual(
            content["players"][0]["scoreProgress"],
            {
                "singles": {"validScoreCount": 30, "requiredScoreCount": 30},
                "doubles": {"validScoreCount": 10, "requiredScoreCount": 30},
            },
        )

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
