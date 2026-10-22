"""Durable tier preparation advances the existing bounded resume audit."""
from __future__ import annotations

import unittest

from analysis_runtime import (
    MemoryBlobStore, TYPED_CHECKPOINT_ANALYSIS_PHASE,
    _audit_checkpoint_resume, _database_cursor_token,
    _reconcile_tier_progress, _validated_tier_progress,
)


class PreparationProgressTests(unittest.TestCase):
    def progress(self, preparation, **changes):
        return {"version": 1, "generation": "a" * 64, "ordinal": 0,
                "stage": "general", "preparationOrdinal": preparation, **changes}

    def test_all_twelve_committed_units_advance_without_disabling_stall_guard(self):
        blobs = MemoryBlobStore()
        checkpoint = {"phase": TYPED_CHECKPOINT_ANALYSIS_PHASE}
        tokens = []
        for preparation in range(1, 13):
            checkpoint = _reconcile_tier_progress(checkpoint, self.progress(preparation))
            checkpoint = _audit_checkpoint_resume(blobs, "checkpoint.json", checkpoint)
            tokens.append(_database_cursor_token(checkpoint))
            self.assertEqual(checkpoint["resumeAudit"]["observations"], 1)
        self.assertEqual(len(set(tokens)), 12)
        checkpoint = _audit_checkpoint_resume(blobs, "checkpoint.json", checkpoint)
        with self.assertRaisesRegex(ValueError, "no progress"):
            _audit_checkpoint_resume(blobs, "checkpoint.json", checkpoint)

    def test_regression_or_changed_generation_cannot_reset_progress(self):
        checkpoint = {"tierProgress": self.progress(4)}
        with self.assertRaisesRegex(ValueError, "preparation regressed"):
            _reconcile_tier_progress(checkpoint, self.progress(3))
        with self.assertRaisesRegex(ValueError, "different generation"):
            _reconcile_tier_progress(checkpoint, self.progress(5, generation="b" * 64))

    def test_fit_progress_preserves_existing_sequence_and_legacy_checkpoints(self):
        checkpoint = {"phase": TYPED_CHECKPOINT_ANALYSIS_PHASE,
                      "tierProgress": self.progress(12)}
        fit = {"version": 1, "generation": "a" * 64, "ordinal": 1,
               "stage": "target-fit", "preparedContext": True}
        checkpoint = _reconcile_tier_progress(checkpoint, fit)
        self.assertEqual(_database_cursor_token(checkpoint),
                         f"{TYPED_CHECKPOINT_ANALYSIS_PHASE}:{'a' * 64}:1")
        legacy = {"version": 1, "generation": "a" * 64, "ordinal": 0, "stage": "general"}
        self.assertEqual(_validated_tier_progress(legacy), legacy)

    def test_preparation_ordinal_requires_a_real_preparation_stage(self):
        for invalid in (0, 13, True, "4", -1):
            with self.subTest(ordinal=invalid), self.assertRaises(ValueError):
                _validated_tier_progress(self.progress(invalid))
        for changes in ({"ordinal": 1}, {"stage": "target-fit"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _validated_tier_progress(self.progress(4, **changes))


if __name__ == "__main__":
    unittest.main()
