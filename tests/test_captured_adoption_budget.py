"""Bounded adoption retains verified row receipts across deadlines and crashes."""
import copy
import unittest
from datetime import timedelta
from unittest.mock import patch

import analysis_runtime as runtime
from captured_run_inputs import load_captured_run_inputs
from captured_run_runtime import CapturedAdoptionPending, adopt_captured_run_checkpoint
from mix_registry import resolve_mix
from tier_pipeline_checkpoints import write_checkpoint
from tests import test_captured_run_inputs as input_fixtures

NOW = input_fixtures.NOW


class CapturedAdoptionBudgetTests(unittest.TestCase):
    def setUp(self):
        fixture = input_fixtures.CapturedRunInputsTests(methodName="runTest")
        fixture.setUp()
        result = fixture.prepare()
        self.store, self.jobs = fixture.store, fixture.jobs
        self.bundle = load_captured_run_inputs(self.store, result["bundleId"])
        self.job_id, self.spec = "analysis-bounded-adoption", resolve_mix("phoenix2")
        self.path = runtime.typed_checkpoint_path(self.job_id)
        self.cursor = runtime.typed_checkpoint_shard_prefix(self.job_id) + "captured-adoption.json"
        self.stamp = NOW + timedelta(hours=4)
        self.jobs.save(runtime.new_job(self.job_id, self.stamp))

    def adopt(self, **kwargs):
        return adopt_captured_run_checkpoint(blob_store=self.store, job_store=self.jobs,
            job_id=self.job_id, mix_spec=self.spec, bundle=self.bundle, checkpoint_path=self.path,
            lease_heartbeat=None, now=lambda: self.stamp, **kwargs)

    def test_expired_budget_saves_empty_cursor_without_rows_or_main_checkpoint(self):
        with patch("analysis_budget.work_budget_available", return_value=False), \
             patch.object(self.store, "put_json", wraps=self.store.put_json) as writes, \
             patch("target_fit_cache.adopt_reviewed_retained_components", side_effect=AssertionError("No fit adoption after deadline")):
            with self.assertRaises(CapturedAdoptionPending) as pending:
                self.adopt()
        self.assertEqual((pending.exception.completed, pending.exception.total), (0, 4))
        self.assertEqual([call.args[0] for call in writes.call_args_list], [self.cursor])
        self.assertIsNone(self.store.get_json(self.path))

    def test_unit_budget_resumes_all_rows_without_rewriting_committed_shards(self):
        self.bundle["typedFrames"]["baselines"] = [{"value": i} for i in range(2 * runtime.TYPED_CHECKPOINT_ROW_LIMIT + 1)]
        old_inputs = copy.deepcopy(self.store.values)
        with patch.object(self.store, "put_json", wraps=self.store.put_json) as writes:
            with self.assertRaises(CapturedAdoptionPending) as pending:
                self.adopt(max_units=3)
            self.assertEqual((pending.exception.completed, pending.exception.total), (3, 6))
            self.assertIsNone(self.store.get_json(self.path))
            self.stamp += timedelta(minutes=10)
            checkpoint = self.adopt(max_units=3)
            again = self.adopt(max_units=3)
        self.assertEqual(checkpoint, again)
        self.assertEqual(checkpoint["createdAtUtc"], (NOW + timedelta(hours=4)).isoformat().replace("+00:00", "Z"))
        for name, data in runtime._typed_manifest(checkpoint)["datasets"].items():
            rows = []
            for descriptor in data["shards"]:
                self.assertEqual(sum(call.args[0] == descriptor["pathname"] for call in writes.call_args_list), 1)
                rows.extend(runtime._load_typed_checkpoint_shard(self.store, checkpoint=checkpoint, dataset=name, descriptor=descriptor))
            self.assertEqual(rows, self.bundle["typedFrames"][name])
        for path, value in old_inputs.items():
            self.assertEqual(self.store.get_json(path), value)

    def test_deadline_after_one_chunk_retains_that_chunk_for_next_invocation(self):
        with patch("analysis_budget.work_budget_available", side_effect=[True, True, False]):
            with self.assertRaises(CapturedAdoptionPending) as pending:
                self.adopt()
        self.assertEqual(pending.exception.completed, 1)
        first = self.store.get_json(self.cursor)["shards"]["baselines"][0]["pathname"]
        with patch.object(self.store, "put_json", wraps=self.store.put_json) as writes:
            self.adopt()
        self.assertFalse(any(call.args[0] == first for call in writes.call_args_list))

    def test_orphan_shard_after_cursor_write_interruption_is_adopted_without_rewrite(self):
        real_put = self.store.put_json
        def interrupted(path, value):
            if path == self.cursor:
                raise RuntimeError("interrupted cursor persistence")
            return real_put(path, value)
        with patch.object(self.store, "put_json", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "interrupted cursor"):
                self.adopt()
        first = runtime.typed_checkpoint_shard_path(self.job_id, "baselines", 0)
        self.assertIsNotNone(self.store.get_json(first))
        with patch.object(self.store, "put_json", wraps=self.store.put_json) as writes:
            self.adopt()
        self.assertFalse(any(call.args[0] == first for call in writes.call_args_list))

    def test_completed_compatibility_adoption_is_not_repeated_on_continuation(self):
        item = {"goal": "sssplus", "source": "official", "mode": "Single", "identity": {"target": 995000}}
        item["inputs"] = write_checkpoint(self.store, {"boards": {}, "sources": {}}, stage="cohort-inputs", identity="verified-cohort")
        self.bundle["sourceGeneration"]["preparedFitInputs"] = [item]
        with patch("target_fit_cache.adopt_reviewed_retained_components", return_value={"adopted": True}) as adopt:
            with self.assertRaises(CapturedAdoptionPending) as pending:
                self.adopt(max_units=1)
            self.assertEqual(pending.exception.completed, 1)
            self.adopt(max_units=4)
        adopt.assert_called_once()

    def test_snapshot_interruption_resumes_orphan_without_recopied_snapshot(self):
        with self.assertRaises(CapturedAdoptionPending) as pending:
            self.adopt(max_units=3)
        self.assertEqual(pending.exception.completed, 3)
        self.assertIsNone(self.store.get_json(runtime.typed_checkpoint_snapshot_path(self.job_id)))
        real_snapshot = runtime._write_typed_checkpoint_snapshot
        def interrupted(*args, **kwargs):
            real_snapshot(*args, **kwargs)
            raise RuntimeError("interrupted after snapshot write")
        with patch("analysis_runtime._write_typed_checkpoint_snapshot", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "after snapshot"):
                self.adopt()
        self.assertIsNone(self.store.get_json(self.path))
        with patch("analysis_runtime._write_typed_checkpoint_snapshot", side_effect=AssertionError("Snapshot must not be recopied")):
            checkpoint = self.adopt()
        self.assertEqual(checkpoint["snapshot"]["pathname"], runtime.typed_checkpoint_snapshot_path(self.job_id))

    def test_changed_cursor_or_corrupt_committed_shard_cannot_advance(self):
        with self.assertRaises(CapturedAdoptionPending):
            self.adopt(max_units=1)
        original = self.store.get_json(self.cursor)
        changed = copy.deepcopy(original)
        changed["identity"]["bundleId"] = "f" * 64
        self.store.put_json(self.cursor, changed)
        with self.assertRaisesRegex(ValueError, "cursor inputs"):
            self.adopt()
        self.store.put_json(self.cursor, original)
        first = original["shards"]["baselines"][0]["pathname"]
        changed = self.store.get_json(first)
        changed["rows"][0]["value"] += 1
        self.store.put_json(first, changed)
        with self.assertRaisesRegex(ValueError, "count/hash"):
            self.adopt()
        self.assertIsNone(self.store.get_json(self.path))


if __name__ == "__main__":
    unittest.main()
