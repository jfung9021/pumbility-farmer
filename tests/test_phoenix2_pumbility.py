import unittest

from phoenix1_score_overrides import SLAM_D24_CHART_ID
from phoenix2_pumbility import (
    PLATE_CODES,
    SKILL_RATING_REFERENCE_MULTIPLIER,
    PlateProjectionModel,
    _snapshot_observations,
    _weighted_median_plate,
    grade_for_score,
    normalize_plate,
    phoenix2_coop_rating,
    phoenix2_pumbility,
    skill_rating_for_pumbility,
)


def snapshot(scores):
    return {
        "charts": [
            {"id": "s18", "type": "Single", "level": 18},
            {"id": "d18", "type": "Double", "level": 18},
        ],
        "scores": scores,
    }


class Phoenix2PumbilityTests(unittest.TestCase):
    def test_grade_boundaries_use_phoenix2_thresholds(self) -> None:
        cases = {
            1_000_000: "SSS+",
            995_000: "SSS+",
            994_999: "SSS",
            970_000: "S",
            969_999: "AAA+",
            920_000: "AA",
            900_000: "A+",
            899_999: "A",
            800_000: "A",
            799_999: "B",
            700_000: "B",
            600_000: "C",
            500_000: "D",
            0: "F",
        }
        for score, grade in cases.items():
            with self.subTest(score=score):
                self.assertEqual(grade_for_score(score), grade)

    def test_slam_score_conversion_is_scoped_to_phoenix1_observations(self) -> None:
        source = {
            "scores": [{
                "playerId": "p",
                "chartId": SLAM_D24_CHART_ID,
                "score": 925_641,
                "plate": "Fair Game",
                "isBroken": False,
            }]
        }
        catalog_types = {SLAM_D24_CHART_ID: "Double"}

        phoenix1, _ = _snapshot_observations(
            source, catalog_types, phoenix1=True
        )
        phoenix2, _ = _snapshot_observations(source, catalog_types)

        self.assertEqual(phoenix1[0][3], "A")
        self.assertEqual(phoenix2[0][3], "AA")

    def test_formula_uses_mode_grade_plate_and_truncates(self) -> None:
        self.assertEqual(phoenix2_pumbility("Single", 18, "AA+", "Fair Game"), 313.2)
        self.assertEqual(phoenix2_pumbility("Double", 18, "AA+", "FG"), 306.24)
        self.assertEqual(phoenix2_pumbility("Single", 24, "SSS+", "PG"), 395.2)
        self.assertEqual(normalize_plate("ug"), "Ultimate Game")
        self.assertEqual(PLATE_CODES["Perfect Game"], "PG")

    def test_formula_uses_mode_specific_rank_and_plate_values(self) -> None:
        self.assertEqual(phoenix2_pumbility("Double", 24, "AA", "FG"), 343.0)
        self.assertEqual(phoenix2_pumbility("Single", 15, "SSS+", "UG"), 318.57)
        self.assertEqual(phoenix2_pumbility("Double", 18, "SSS+", "UG"), 333.52)
        self.assertEqual(phoenix2_pumbility("Single", 15, "SSS+", "MG"), 316.26)
        self.assertEqual(phoenix2_pumbility("Single", 16, "SSS+", "EG"), 325.51)
        self.assertEqual(phoenix2_pumbility("Double", 17, "SSS+", "EG"), 325.08)
        expected_double_penalties = {
            "AA": 13,
            "A+": 15,
            "A": 20,
            "B": 25,
            "C": 30,
            "D": 40,
            "F": 50,
        }
        for grade, penalty in expected_double_penalties.items():
            with self.subTest(grade=grade):
                base = 375.0
                expected = int(base * (750 - 5 * penalty) / 750 * 100) / 100
                self.assertEqual(
                    phoenix2_pumbility("Double", 24, grade, "RG"), expected
                )

    def test_coop_formula_uses_fixed_base_and_double_penalty_units(self) -> None:
        self.assertEqual(phoenix2_coop_rating("SSS+", "PG"), 121.6)
        self.assertEqual(phoenix2_coop_rating("F", "Rough Game"), 80.0)
        self.assertEqual(phoenix2_coop_rating("AA", "FG"), 109.76)
        with self.assertRaises(ValueError):
            phoenix2_coop_rating("invalid", "FG")
        with self.assertRaises(ValueError):
            phoenix2_coop_rating("SSS+", "invalid")

    def test_formula_matches_the_official_top_fifty_screenshot(self) -> None:
        cases = [
            ("Single", 21, "SS+", "TG", 356.16),
            ("Single", 21, "SS", "TG", 353.76),
            ("Double", 21, "SSS", "MG", 351.56),
            ("Double", 22, "S+", "TG", 351.36),
            ("Single", 21, "S+", "TG", 351.36),
            ("Double", 21, "SS+", "MG", 349.21),
            ("Single", 20, "SS+", "FG", 348.27),
            ("Single", 20, "SS", "MG", 346.86),
            ("Single", 20, "SS", "FG", 345.92),
            ("Double", 21, "SS", "RG", 345.45),
            ("Double", 23, "AAA", "RG", 345.45),
            ("Single", 19, "SSS", "MG", 344.08),
            ("Double", 20, "SSS", "MG", 344.08),
            ("Single", 19, "SSS", "TG", 343.62),
            ("Double", 22, "AAA+", "RG", 343.20),
            ("Double", 24, "AA", "FG", 343.00),
            ("Single", 19, "SS+", "MG", 341.78),
            ("Single", 20, "S", "TG", 341.69),
            ("Single", 20, "S", "TG", 341.69),
            ("Single", 19, "SS+", "TG", 341.32),
            ("Single", 20, "S", "FG", 341.22),
            ("Double", 23, "AA+", "FG", 341.04),
            ("Single", 21, "AAA", "RG", 338.40),
            ("Single", 20, "AAA+", "MG", 337.46),
            ("Single", 20, "AAA+", "TG", 336.99),
            ("Single", 20, "AAA+", "FG", 336.52),
            ("Single", 20, "AAA+", "FG", 336.52),
            ("Double", 18, "SSS+", "PG", 334.40),
            ("Double", 18, "SSS+", "PG", 334.40),
            ("Double", 18, "SSS+", "PG", 334.40),
            ("Single", 18, "SS+", "MG", 334.35),
            ("Double", 19, "SS+", "TG", 333.90),
            ("Double", 18, "SSS+", "UG", 333.52),
            ("Double", 18, "SSS+", "UG", 333.52),
            ("Double", 18, "SSS+", "EG", 332.64),
            ("Double", 18, "SSS+", "EG", 332.64),
            ("Double", 20, "AAA+", "FG", 329.36),
            ("Double", 18, "SSS", "MG", 329.12),
            ("Single", 17, "SSS", "MG", 329.12),
            ("Single", 16, "SSS+", "PG", 326.80),
            ("Double", 18, "S+", "TG", 322.08),
            ("Single", 16, "SSS", "SG", 322.07),
            ("Double", 16, "SSS+", "PG", 319.20),
            ("Single", 15, "SSS+", "UG", 318.57),
            ("Single", 15, "SSS+", "UG", 318.57),
            ("Single", 15, "SSS+", "UG", 318.57),
            ("Double", 16, "SSS+", "EG", 317.52),
            ("Single", 16, "SS", "TG", 316.91),
            ("Single", 15, "SSS+", "MG", 316.26),
            ("Single", 16, "S+", "TG", 314.76),
        ]
        calculated = []
        for chart_type, level, grade, plate, expected in cases:
            with self.subTest(
                chart_type=chart_type, level=level, grade=grade, plate=plate
            ):
                value = phoenix2_pumbility(chart_type, level, grade, plate)
                self.assertEqual(value, expected)
                calculated.append(value)
        self.assertEqual(len(cases), 50)
        self.assertAlmostEqual(sum(calculated), 16_800.65)

    def test_skill_rating_inverts_the_continuous_s_fair_game_reference(self) -> None:
        self.assertAlmostEqual(SKILL_RATING_REFERENCE_MULTIPLIER, 0.968)
        self.assertAlmostEqual(
            skill_rating_for_pumbility("Single", 375.0 * 0.968), 23.0
        )
        self.assertAlmostEqual(
            skill_rating_for_pumbility("Double", 375.0 * 0.968), 24.0
        )
        self.assertAlmostEqual(
            skill_rating_for_pumbility("Single", 390.0 * 0.968), 24.0
        )

    def test_skill_rating_rejects_invalid_inputs(self) -> None:
        for value in (-1, float("nan"), True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    skill_rating_for_pumbility("Single", value)
        with self.assertRaises(ValueError):
            skill_rating_for_pumbility("Co-op", 300)

    def test_plate_model_uses_p2_precedence_and_player_history(self) -> None:
        p1 = snapshot(
            [
                {
                    "playerId": "p1",
                    "chartId": "s18",
                    "score": 995_000,
                    "plate": "Rough Game",
                    "pumbility": 1,
                    "isBroken": False,
                },
                {
                    "playerId": "p1",
                    "chartId": "d18",
                    "score": 995_000,
                    "plate": "Fair Game",
                    "pumbility": 1,
                    "isBroken": False,
                },
            ]
        )
        p2 = snapshot(
            [
                {
                    "playerId": "p1",
                    "chartId": "s18",
                    "score": 995_000,
                    "plate": "Perfect Game",
                    "pumbility": 1,
                    "isBroken": False,
                }
            ]
        )
        model = PlateProjectionModel(p1, p2)
        singles = model.distribution("p1", "Single", "SSS+")
        doubles = model.distribution("p1", "Double", "SSS+")
        self.assertEqual(singles.source, "phoenix2")
        self.assertGreater(
            singles.probabilities["Perfect Game"],
            singles.probabilities["Rough Game"],
        )
        self.assertEqual(doubles.source, "phoenix1")
        self.assertGreater(
            doubles.probabilities["Fair Game"],
            doubles.probabilities["Rough Game"],
        )
        self.assertAlmostEqual(sum(singles.probabilities.values()), 1.0)

    def test_weighted_median_plate_uses_order_and_lower_exact_boundary(self) -> None:
        exact_boundary = {
            "Rough Game": 0.2,
            "Fair Game": 0.3,
            "Talented Game": 0.4,
            "Marvelous Game": 0.1,
        }
        self.assertEqual(_weighted_median_plate(exact_boundary), "Fair Game")
        self.assertEqual(
            _weighted_median_plate({"Rough Game": 0.49, "Perfect Game": 0.51}),
            "Perfect Game",
        )
        self.assertEqual(
            _weighted_median_plate({"Marvelous Game": 1.0}), "Marvelous Game"
        )


if __name__ == "__main__":
    unittest.main()
