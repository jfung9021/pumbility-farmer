from __future__ import annotations

import copy
from contextlib import ExitStack
import unittest
from unittest.mock import patch

from analysis_budget import invocation_budget
from analysis_runtime import MemoryBlobStore
import production_tiers as tiers
from tests.test_production_tiers import context_fixture


class TierPreparationTests(unittest.TestCase):
    timestamp = "2026-10-22T06:00:00Z"

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.context = context_fixture()
        for name, value in (("load_context", self.context), ("pipeline_code_identity", "pipeline"),
                            ("model_code_identity", "model"), ("general_code_identity", "general"),
                            ("_legacy_general_code_identity", "not-legacy"),
                            ("numerical_pipeline_recipe", {"numeric": "unchanged"}),
                            ("goal_input_identity", {"goals": "inputs"}),
                            ("matching_goal_artifacts", {})):
            self.stack.enter_context(patch("production_tiers." + name, return_value=value))
        self.stack.enter_context(patch("production_tiers.stage_code_identity", return_value="stage-code"))
        self.stack.enter_context(patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"))
        self.stack.enter_context(patch("tier_diagnostics.event"))
        self.general = self.stack.enter_context(patch("scoring_percentile.build_production_tier_payload",
            return_value={"schemaVersion": 48, "values": [19.5, 20.5]}))
        self.store = MemoryBlobStore()

    def advance(self, callback=None):
        return tiers.build_hosted_tiers([], {}, {}, {}, store=self.store,
            arguments={"official_scoring_manifest": {"_captures": {"Phoenix2": {}}}},
            generated_at_utc=self.timestamp, on_progress=callback)

    def state(self):
        return next(copy.deepcopy(value) for path, value in self.store.values.items() if path.endswith("/state.json"))

    def commit(self, ordinal):
        progress = []
        with self.assertRaises(tiers.TierAnalysisPending) as pending:
            self.advance(progress.append)
        self.assertEqual(progress[-1]["preparationOrdinal"], ordinal)
        self.assertTrue(pending.exception.sequence.endswith("-prepare-" + str(ordinal)))
        return pending.exception

    def test_general_each_phase_and_each_cohort_are_separate_commits_and_resume_lazily(self):
        for ordinal in range(1, 5):
            with patch.object(self.store, "put_bytes", wraps=self.store.put_bytes) as uploads:
                self.commit(ordinal)
                self.assertEqual(uploads.call_count, 1)
        generation = self.state()["generation"]
        with patch("production_tiers.load_context", side_effect=AssertionError("raw context reconstructed")), \
             patch("production_tiers._advance_prepared_fits", return_value="fit-next") as fit:
            for ordinal in range(5, 13):
                progress = []
                with patch.object(self.store, "put_bytes", wraps=self.store.put_bytes) as uploads:
                    with self.assertRaises(tiers.TierAnalysisPending):
                        tiers.resume_tier_generation(self.store, generation, generated_at_utc=self.timestamp,
                            on_progress=progress.append)
                    self.assertEqual(uploads.call_count, 1)
                    self.assertEqual(progress[-1]["preparationOrdinal"], ordinal)
                fit.assert_not_called()
            self.assertEqual(tiers.resume_tier_generation(self.store, generation,
                generated_at_utc=self.timestamp), "fit-next")
        self.general.assert_called_once()
        self.assertEqual(len(self.state()["preparedFitInputs"]), 8)

    def test_crash_after_every_artifact_before_manifest_reuses_receipt_without_reupload(self):
        for target in range(1, 13):
            with self.subTest(unit=target):
                self.store = MemoryBlobStore()
                self.general.reset_mock()
                for ordinal in range(1, target):
                    self.commit(ordinal)
                original = self.store.put_json
                def crash(path, value):
                    if (path.endswith("/state.json") and value.get("general")
                            and tiers._preparation_ordinal(value) == target):
                        raise OSError("crash before manifest")
                    return original(path, value)
                with patch.object(self.store, "put_json", side_effect=crash):
                    with self.assertRaisesRegex(OSError, "before manifest"):
                        self.advance()
                with patch.object(self.store, "put_bytes", wraps=self.store.put_bytes) as uploads:
                    self.commit(target)
                    uploads.assert_not_called()
                self.assertEqual(self.general.call_count, 1)

    def test_crash_after_every_manifest_does_not_repeat_committed_unit(self):
        for target in range(1, 13):
            with self.subTest(unit=target):
                self.store = MemoryBlobStore()
                self.general.reset_mock()
                for ordinal in range(1, target):
                    self.commit(ordinal)
                original = self.store.put_json
                def crash(path, value):
                    result = original(path, value)
                    if (path.endswith("/state.json") and value.get("general")
                            and tiers._preparation_ordinal(value) == target):
                        raise OSError("crash after manifest")
                    return result
                with patch.object(self.store, "put_json", side_effect=crash):
                    with self.assertRaisesRegex(OSError, "after manifest"):
                        self.advance()
                if target == 1:
                    # The crash preceded the orchestration version marker. The
                    # adopted General is reported once without another upload.
                    with patch.object(self.store, "put_bytes", wraps=self.store.put_bytes) as uploads:
                        self.commit(1)
                        uploads.assert_not_called()
                elif target < 12:
                    self.commit(target + 1)
                else:
                    with patch("production_tiers._advance_prepared_fits", return_value="fit-next"):
                        self.assertEqual(self.advance(), "fit-next")
                self.assertEqual(self.general.call_count, 1)

    def test_receipt_without_uploaded_bytes_is_not_committed_progress(self):
        self.commit(1)
        with patch.object(self.store, "put_bytes", side_effect=OSError("upload interrupted")):
            with self.assertRaisesRegex(OSError, "upload interrupted"):
                self.advance()
        self.assertEqual(tiers._preparation_ordinal(self.state()), 1)
        with patch.object(self.store, "put_bytes", wraps=self.store.put_bytes) as upload:
            self.commit(2)
            self.assertEqual(upload.call_count, 1)

    def test_partial_legacy_cohorts_resume_but_cannot_be_fitted_as_complete(self):
        for ordinal in range(1, 6): self.commit(ordinal)
        state = self.state()
        state.pop("preparationVersion")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            tiers._advance_prepared_fits(self.store, state)
        path = tiers.PREFIX + "generations/" + state["generation"] + "/state.json"
        self.store.put_json(path, state)
        with patch("production_tiers.load_context", side_effect=AssertionError("raw context reconstructed")):
            progress = []
            with self.assertRaises(tiers.TierAnalysisPending):
                tiers.resume_tier_generation(self.store, state["generation"], generated_at_utc=self.timestamp,
                    on_progress=progress.append)
            self.assertEqual(progress[-1]["preparationOrdinal"], 6)

    def test_expired_work_budget_does_not_start_upload_or_advance_progress(self):
        self.commit(1)
        with invocation_budget(0), patch.object(self.store, "put_bytes", wraps=self.store.put_bytes) as uploads:
            with self.assertRaisesRegex(RuntimeError, "work budget"):
                self.advance()
            uploads.assert_not_called()
        self.assertEqual(tiers._preparation_ordinal(self.state()), 1)

    def test_stale_receipt_identity_is_not_adopted(self):
        self.commit(1)
        generation = self.state()["generation"]
        self.commit(2)
        path = tiers._preparation_receipt_path(generation, "phase-model")
        receipt = self.store.get_json(path)
        self.store.put_json(path, {**receipt, "identity": "different-generation"})
        self.assertIsNone(tiers._preparation_receipt(self.store, generation, "phase-model",
            stage="phase-inputs-model", identity=receipt["identity"]))


if __name__ == "__main__":
    unittest.main()
