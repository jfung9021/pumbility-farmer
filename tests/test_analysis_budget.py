from __future__ import annotations

import unittest
from unittest.mock import patch

from analysis_budget import invocation_budget, work_budget_available, work_budget_remaining


class InvocationBudgetTests(unittest.TestCase):
    def test_elapsed_work_includes_loading_and_budget_does_not_leak_to_next_callback(self):
        with patch("analysis_budget.time.monotonic", side_effect=[100, 699, 700, 701]):
            with invocation_budget():
                self.assertEqual(work_budget_remaining(), 1)
                self.assertFalse(work_budget_available())
                self.assertEqual(work_budget_remaining(), -1)
        self.assertIsNone(work_budget_remaining())

    def test_failure_resets_budget_and_standalone_calls_remain_unbounded(self):
        with self.assertRaisesRegex(RuntimeError, "failed"):
            with invocation_budget(0):
                self.assertFalse(work_budget_available())
                raise RuntimeError("failed")
        self.assertTrue(work_budget_available())


if __name__ == "__main__":
    unittest.main()
