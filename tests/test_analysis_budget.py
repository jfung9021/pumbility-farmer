from __future__ import annotations

import unittest
from unittest.mock import patch

from analysis_budget import (
    ANALYSIS_IO_SECONDS,
    invocation_budget,
    io_budget_remaining,
    work_budget_available,
    work_budget_remaining,
)


class InvocationBudgetTests(unittest.TestCase):
    def test_elapsed_work_includes_loading_and_budget_does_not_leak_to_next_callback(self):
        with patch("analysis_budget.time.monotonic", side_effect=[100, 699, 700, 701]):
            with invocation_budget():
                self.assertEqual(work_budget_remaining(), 1)
                self.assertFalse(work_budget_available())
                self.assertEqual(work_budget_remaining(), -1)
        self.assertIsNone(work_budget_remaining())
        self.assertIsNone(io_budget_remaining())

    def test_failure_resets_budget_and_standalone_calls_remain_unbounded(self):
        with self.assertRaisesRegex(RuntimeError, "failed"):
            with invocation_budget(0):
                self.assertFalse(work_budget_available())
                raise RuntimeError("failed")
        self.assertTrue(work_budget_available())
        self.assertIsNone(io_budget_remaining())

    def test_checkpoint_io_keeps_150_seconds_after_numerical_work_expires(self):
        with patch("analysis_budget.time.monotonic", return_value=100.0) as clock:
            with invocation_budget():
                self.assertEqual(work_budget_remaining(), 600)
                self.assertEqual(io_budget_remaining(), 750)
                clock.return_value = 700.0
                self.assertFalse(work_budget_available())
                self.assertEqual(io_budget_remaining(), 150)
                clock.return_value = 849.0
                self.assertEqual(io_budget_remaining(), 1)
                clock.return_value = 850.0
                self.assertEqual(io_budget_remaining(), 0)
        self.assertLess(ANALYSIS_IO_SECONDS, 800)
        self.assertIsNone(work_budget_remaining())
        self.assertIsNone(io_budget_remaining())

    def test_nested_scopes_restore_both_deadlines_without_extending_parent(self):
        with patch("analysis_budget.time.monotonic", return_value=0.0) as clock:
            with invocation_budget(10):
                clock.return_value = 5.0
                with invocation_budget():
                    self.assertEqual(work_budget_remaining(), 5)
                    self.assertEqual(io_budget_remaining(), 155)
                with invocation_budget(1):
                    self.assertEqual(work_budget_remaining(), 1)
                    self.assertEqual(io_budget_remaining(), 151)
                self.assertEqual(work_budget_remaining(), 5)
                self.assertEqual(io_budget_remaining(), 155)
        self.assertIsNone(work_budget_remaining())
        self.assertIsNone(io_budget_remaining())


if __name__ == "__main__":
    unittest.main()
