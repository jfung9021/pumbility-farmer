import unittest

from phoenix2_pumbility import (
    PLATE_CODES,
    SKILL_RATING_REFERENCE_MULTIPLIER,
    PlateProjectionModel,
    grade_for_score,
    normalize_plate,
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

    def test_formula_uses_mode_grade_plate_and_truncates(self) -> None:
        self.assertEqual(phoenix2_pumbility("Single", 18, "AA+", "Fair Game"), 313.2)
        self.assertEqual(phoenix2_pumbility("Double", 18, "AA+", "FG"), 306.24)
        self.assertEqual(phoenix2_pumbility("Single", 24, "SSS+", "PG"), 395.2)
        self.assertEqual(normalize_plate("ug"), "Ultimate Game")
        self.assertEqual(PLATE_CODES["Perfect Game"], "PG")

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


if __name__ == "__main__":
    unittest.main()
