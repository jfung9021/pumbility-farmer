import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from piu_misgrade_analyzer import (
    AnalysisConfig,
    _fit_level_calibration,
    apply_within_level_difficulty,
    analyze_snapshot,
    build_web_payload,
    difficulty_effect_band,
    folder_for,
    folder_range_compression,
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
        "bpmMin": 120,
        "bpmMax": 180,
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
    def test_payload_identifies_the_analyzed_mix(self) -> None:
        players, charts, scores, _ = make_synthetic_snapshot(players_per_folder=2)
        results, _, summary, _ = analyze_snapshot(
            players,
            charts,
            scores,
            AnalysisConfig(mix="phoenix1", bootstrap_samples=0),
        )
        payload = build_web_payload(results, summary)
        self.assertEqual(payload["mix"], {
            "key": "phoenix1",
            "apiValue": "Phoenix",
            "label": "Phoenix 1",
        })
        self.assertEqual(
            [
                (band["rank"], band["name"], band["low"], band["high"])
                for band in payload["effectBands"]
            ],
            [
                (1, "Overrated", None, -0.5),
                (2, "Very Easy", -0.5, -0.3),
                (3, "Easy", -0.3, -0.1),
                (4, "Medium", -0.1, 0.1),
                (5, "Hard", 0.1, 0.3),
                (6, "Very Hard", 0.3, 0.5),
                (7, "Underrated", 0.5, None),
            ],
        )

    def test_dynamic_level_filter_and_relative_groups(self) -> None:
        self.assertEqual(folder_for("Single", 16), "S16")
        self.assertEqual(folder_for("Double", 16), "D16")
        self.assertEqual(folder_for("Single", 20), "S20")
        self.assertEqual(folder_for("Single", 27), "S27")
        self.assertEqual(folder_for("Double", 31), "D31")
        self.assertIsNone(folder_for("Single", 15))
        self.assertIsNone(folder_for("Double", 15))
        self.assertEqual(relative_difficulty_group(0.02), (1, "Easiest 10%"))
        self.assertEqual(relative_difficulty_group(0.5), (6, "50–60% percentile"))
        self.assertEqual(relative_difficulty_group(0.98), (10, "Hardest 10%"))
        boundary_cases = [
            (-0.51, (1, "Overrated")),
            (-0.50, (2, "Very Easy")),
            (-0.31, (2, "Very Easy")),
            (-0.30, (3, "Easy")),
            (-0.11, (3, "Easy")),
            (-0.10, (4, "Medium")),
            (0.00, (4, "Medium")),
            (0.10, (4, "Medium")),
            (0.11, (5, "Hard")),
            (0.30, (5, "Hard")),
            (0.31, (6, "Very Hard")),
            (0.50, (6, "Very Hard")),
            (0.51, (7, "Underrated")),
        ]
        for delta, expected in boundary_cases:
            with self.subTest(delta=delta):
                self.assertEqual(difficulty_effect_band(delta), expected)

    def test_score_controlled_level_calibration(self) -> None:
        rows = []
        for player_index, base in enumerate((100.0, 240.0, 380.0, 520.0)):
            for level in range(15, 23):
                rows.append({
                    "playerId": f"p-{player_index}",
                    "score": 950000 + (level % 2) * 500,
                    "level": level,
                    "pumbility": base + 7.3 * level,
                })
        slope, diagnostics = _fit_level_calibration(pd.DataFrame(rows))
        self.assertAlmostEqual(slope, 7.3, places=6)
        self.assertEqual(
            diagnostics["method"],
            "within-player fixed effects and 2,500-point score bands",
        )

    def test_difficulty_formula_scales_delta_and_intervals_by_point_four(self) -> None:
        frame = pd.DataFrame([
            {
                "chartId": "easy",
                "folder": "S20",
                "songName": "Easy",
                "level": 20,
                "chartResidualPb": -100.0,
                "meanResidualPb": -100.0,
                "residualStdPb": 0.0,
                "residualCi95LowPb": -110.0,
                "residualCi95HighPb": -90.0,
                "nContributors": 10,
            },
            {
                "chartId": "hard",
                "folder": "S20",
                "songName": "Hard",
                "level": 20,
                "chartResidualPb": 100.0,
                "meanResidualPb": 100.0,
                "residualStdPb": 0.0,
                "residualCi95LowPb": 100.0,
                "residualCi95HighPb": 100.0,
                "nContributors": 10,
            },
        ])
        result = apply_within_level_difficulty(
            frame,
            50.0,
            AnalysisConfig(bootstrap_samples=0, shrinkage_k=0),
        )
        easy = result[result["chartId"] == "easy"].iloc[0]
        self.assertAlmostEqual(float(easy["difficultyDelta"]), 0.8)
        self.assertAlmostEqual(float(easy["estimatedDifficulty"]), 21.3)
        self.assertAlmostEqual(float(easy["difficultyDeltaCi95Low"]), 0.72)
        self.assertAlmostEqual(float(easy["difficultyDeltaCi95High"]), 0.88)
        self.assertAlmostEqual(float(easy["difficultyCi95Low"]), 21.22)
        self.assertAlmostEqual(float(easy["difficultyCi95High"]), 21.38)
        self.assertEqual((easy["effectBandRank"], easy["effectBand"]), (7, "Underrated"))

    def test_folder_range_normalization_compresses_only_large_folders(self) -> None:
        self.assertEqual(folder_range_compression(1), 1.0)
        self.assertEqual(folder_range_compression(30), 1.0)
        self.assertGreater(folder_range_compression(60), folder_range_compression(90))
        self.assertGreater(folder_range_compression(90), 0.0)

        frame = pd.DataFrame([
            {
                "folder": "S17",
                "level": 17,
                "chartId": f"S17-{index}",
                "songName": f"S17 {index}",
                "meanResidualPb": float(index),
                "residualCi95LowPb": float(index),
                "residualCi95HighPb": float(index),
                "nContributors": 10,
            }
            for index in range(60)
        ])
        result = apply_within_level_difficulty(
            frame,
            1.0,
            AnalysisConfig(bootstrap_samples=0, shrinkage_k=0),
        )
        expected = folder_range_compression(60)
        self.assertTrue((result["folderMeasuredCharts"] == 60).all())
        self.assertTrue((result["folderRangeCompression"] == expected).all())
        self.assertLess(expected, 1.0)
        self.assertAlmostEqual(
            float(result["difficultyDelta"].abs().max()),
            0.4 * 29.5 * expected,
        )

        uncompressed = apply_within_level_difficulty(
            frame,
            1.0,
            AnalysisConfig(bootstrap_samples=0, shrinkage_k=0),
            compress_large_folders=False,
        )
        self.assertTrue((uncompressed["folderMeasuredCharts"] == 60).all())
        self.assertTrue((uncompressed["folderRangeCompression"] == 1.0).all())
        self.assertAlmostEqual(
            float(uncompressed["difficultyDelta"].abs().max()),
            0.4 * 29.5,
        )

    def test_calibration_accepts_legacy_mix_scale_and_rejects_negative_slope(self) -> None:
        rows = []
        for player_index, base in enumerate((100.0, 300.0, 500.0, 700.0)):
            for level in range(15, 23):
                rows.append({
                    "playerId": f"legacy-{player_index}",
                    "score": 950000 + (level % 2) * 500,
                    "level": level,
                    "pumbility": base + 90.0 * level,
                })
        legacy_slope, diagnostics = _fit_level_calibration(pd.DataFrame(rows))
        self.assertAlmostEqual(legacy_slope, 90.0, places=6)
        self.assertEqual(diagnostics["validation"], "positive finite empirical slope")

        negative_rows = pd.DataFrame(rows)
        negative_rows["pumbility"] = 3000.0 - negative_rows["pumbility"]
        with self.assertRaisesRegex(ValueError, "positive and finite"):
            _fit_level_calibration(negative_rows)

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
            frame,
            50.0,
            AnalysisConfig(bootstrap_samples=0, shrinkage_k=0),
        )
        for folder, reference in (("S22", 0.0), ("S23", 100.0)):
            rows = result[result["folder"] == folder].sort_values("difficultyDelta")
            self.assertTrue((rows["levelReferenceResidualPb"] == reference).all())
            self.assertAlmostEqual(float(rows["difficultyDelta"].sum()), 0.0)
            self.assertAlmostEqual(float(rows["difficultyDelta"].abs().max()), 0.08)
            self.assertEqual(list(rows["relativeGroupRank"]), [3, 8])
        self.assertGreater(
            float(result[result["folder"] == "S23"]["estimatedDifficulty"].min()),
            23.0,
        )

    def test_top_and_recent_windows_are_unioned_and_deduplicated(self) -> None:
        charts = [chart(f"single-{index:03d}", "Single", 20) for index in range(500)]
        scores = []
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index, row in enumerate(charts):
            scored = score("player", row["id"], 1200.0 - index)
            timestamp_order = 600 + index if 50 <= index < 150 else index
            scored["recordedAt"] = (
                start + timedelta(days=timestamp_order)
            ).isoformat().replace("+00:00", "Z")
            scores.append(scored)
        results, baselines, summary, contributions = analyze_snapshot(
            [{"userId": "player"}],
            charts,
            scores,
            AnalysisConfig(bootstrap_samples=0, pumbility_per_level=7.3),
        )
        recent_only = results[results["chartId"] == "single-125"].iloc[0]
        self.assertEqual((recent_only["bpmMin"], recent_only["bpmMax"]), (120, 180))
        excluded = results[results["chartId"] == "single-150"].iloc[0]
        self.assertEqual(recent_only["nContributors"], 1)
        self.assertEqual(excluded["nContributors"], 0)
        self.assertEqual(excluded["nPlayersScored"], 1)
        self.assertEqual(excluded["evidenceStatus"], "Unrated")
        self.assertEqual(len(contributions), 150)
        self.assertTrue((contributions["contributionRankLimit"] == 100).all())
        self.assertFalse(contributions["usesTop100Fallback"].any())
        self.assertEqual(int(contributions["selectedByPumbility"].sum()), 100)
        self.assertEqual(int(contributions["selectedByRecency"].sum()), 100)
        self.assertEqual(int(
            (contributions["selectedByPumbility"] & contributions["selectedByRecency"]).sum()
        ), 50)
        self.assertEqual(summary["coverage"]["targetSelectedContributions"], 150)
        self.assertEqual(summary["coverage"]["playerModePairsUsingTop100Fallback"], 0)
        self.assertEqual(len(baselines[baselines["mode"] == "Singles"]), 1)
        self.assertEqual(summary["modes"]["singles"]["eligiblePlayers"], 1)
        self.assertEqual(summary["modes"]["doubles"]["eligiblePlayers"], 0)

    def test_small_union_falls_back_to_top_100_by_pumbility(self) -> None:
        charts = [chart(f"single-{index:03d}", "Single", 20) for index in range(120)]
        scores = []
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index, row in enumerate(charts):
            scored = score("player", row["id"], 700.0 - index)
            timestamp_order = 200 + index if 96 <= index < 120 else index
            scored["recordedAt"] = (
                start + timedelta(days=timestamp_order)
            ).isoformat().replace("+00:00", "Z")
            scores.append(scored)

        results, _, summary, contributions = analyze_snapshot(
            [{"userId": "player"}],
            charts,
            scores,
            AnalysisConfig(bootstrap_samples=0, pumbility_per_level=7.3),
        )

        fallback_last = results[results["chartId"] == "single-099"].iloc[0]
        recent_outside_fallback = results[results["chartId"] == "single-110"].iloc[0]
        self.assertEqual(fallback_last["nContributors"], 1)
        self.assertEqual(recent_outside_fallback["nContributors"], 0)
        self.assertEqual(len(contributions), 100)
        self.assertTrue((contributions["contributionRankLimit"] == 24).all())
        self.assertTrue(contributions["usesTop100Fallback"].all())
        self.assertTrue(contributions["selectedByTop100Fallback"].all())
        self.assertEqual(summary["coverage"]["targetSelectedContributions"], 100)
        self.assertEqual(summary["coverage"]["playerModePairsUsingTop100Fallback"], 1)

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
            AnalysisConfig(bootstrap_samples=0, pumbility_per_level=7.3),
        )
        single = float(baselines.loc[baselines["mode"] == "Singles", "baselinePumbility"].iloc[0])
        double = float(baselines.loc[baselines["mode"] == "Doubles", "baselinePumbility"].iloc[0])
        self.assertAlmostEqual(double - single, 400.0)

    def test_zero_pumbility_scores_do_not_qualify_a_player(self) -> None:
        charts = [
            chart(f"chart-{index:02d}", "Single", 16 + index % 5)
            for index in range(37)
        ]
        scores = []
        for index, row in enumerate(charts):
            scores.append(score("valid", row["id"], 500.0 - index))
            scores.append(
                score(
                    "zero-baseline",
                    row["id"],
                    330.0 - index if index < 10 else 0.0,
                )
            )
        results, baselines, summary, contributions = analyze_snapshot(
            [{"userId": "valid"}, {"userId": "zero-baseline"}],
            charts,
            scores,
            AnalysisConfig(bootstrap_samples=0, pumbility_per_level=7.3),
        )
        singles = baselines[baselines["mode"] == "Singles"]
        self.assertEqual(len(singles), 1)
        self.assertGreater(float(singles.iloc[0]["baselinePumbility"]), 0)
        self.assertEqual(summary["coverage"]["nonpositivePumbilityRowsExcluded"], 27)
        self.assertTrue((results["nContributors"] <= 1).all())
        self.assertEqual(contributions["playerHash"].nunique(), 1)

    def test_synthetic_recovers_order_with_point_four_scaled_output(self) -> None:
        players, charts, scores, truth = make_synthetic_snapshot(players_per_folder=5)
        results, _, _, _ = analyze_snapshot(
            players,
            charts,
            scores,
            AnalysisConfig(bootstrap_samples=0),
        )
        validation = validate_synthetic(results, truth)
        self.assertTrue(validation["passed"])
        self.assertEqual(len(results[results["folder"] == "S16"]), 10)
        self.assertEqual(len(results[results["folder"] == "D16"]), 10)
        easiest_s20 = results[results["folder"] == "S20"].sort_values("difficultyDelta").iloc[0]
        self.assertLessEqual(float(easiest_s20["difficultyDelta"]), -0.25)
        self.assertIn(
            str(easiest_s20["effectBand"]),
            {"Overrated", "Very Easy", "Easy"},
        )
        self.assertEqual(int(easiest_s20["relativeGroupRank"]), 1)
        measured = results[results["difficultyDelta"].notna()]
        self.assertTrue(measured["effectBandRank"].between(1, 7).all())
        for row in measured.itertuples():
            self.assertEqual(
                (int(row.effectBandRank), str(row.effectBand)),
                difficulty_effect_band(float(row.difficultyDelta)),
            )

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

        for _, group in rescored.groupby("folder"):
            self.assertAlmostEqual(
                float(group["rawEasePb"].median()), 0.0, places=6
            )
            if len(group) >= 10:
                self.assertEqual(int(group["relativeGroupRank"].min()), 1)
                self.assertEqual(int(group["relativeGroupRank"].max()), 10)
        overrated = rescored[rescored["effectBand"] == "Overrated"]
        underrated = rescored[rescored["effectBand"] == "Underrated"]
        self.assertTrue((overrated["difficultyDelta"] < -0.5).all())
        self.assertTrue((underrated["difficultyDelta"] > 0.5).all())

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
