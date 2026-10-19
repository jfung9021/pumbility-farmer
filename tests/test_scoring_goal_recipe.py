"""Goal cache keys follow consumed producers, excluding public-only readers."""
from pathlib import Path
import unittest
from unittest.mock import patch

from production_tiers import _goal_recipe


class GoalRecipeTests(unittest.TestCase):
    def changed_source(self, filename, before, after):
        original = Path.read_bytes

        def read(path):
            value = original(path)
            if path.name == filename:
                self.assertIn(before, value)
                return value.replace(before, after)
            return value

        return patch.object(Path, "read_bytes", read)

    def test_consumed_calibration_and_preparation_changes_invalidate_goals(self):
        expected = _goal_recipe("ss")
        mutations = (
            ("target_tail_policy.py", b".4 * (excess / (.4 + excess))", b".3 * (excess / (.4 + excess))"),
            ("sss_folder_centering.py", b"MINIMUM_CHARTS = 5", b"MINIMUM_CHARTS = 6"),
            ("sss_scoring.py", b"delta = None if value is None else value - level - .5",
             b"delta = None if value is None else value - level - .6"),
            ("production_tiers.py", b'minimum = 20 if source == "official" else 16',
             b'minimum = 21 if source == "official" else 16'),
        )
        for filename, before, after in mutations:
            with self.subTest(producer=filename), self.changed_source(filename, before, after):
                self.assertNotEqual(_goal_recipe("ss"), expected)

    def test_public_validation_text_does_not_invalidate_goal_math(self):
        expected = _goal_recipe("ss")
        with self.changed_source("scoring_goal_tiers.py", b"Missing scoring-goal baseline hash.",
                                 b"The predecessor hash is missing."):
            self.assertEqual(_goal_recipe("ss"), expected)


if __name__ == "__main__":
    unittest.main()
