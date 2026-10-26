from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from analysis_runtime import MemoryBlobStore
import production_tiers as tiers
from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint


class RetainedTierPublicationTests(unittest.TestCase):
    timestamp = "2026-10-27T00:00:00+00:00"

    def setUp(self):
        self.store = MemoryBlobStore()

    def pin(self):
        reference = tiers.pin_tier_publication_inputs(self.store, first={}, second={}, manifest={},
            records=[], metadata={}, generated_at_utc=self.timestamp)
        return {"inputs": reference, "generatedAtUtc": self.timestamp, "producer": tiers.RETAINED_PRODUCER}

    def candidate(self):
        return {"schemaVersion": 61, "summary": {"method": {
            "retainedGoalProducer": {"producer": tiers.RETAINED_PRODUCER},
            "productionTierPublication": {"method": tiers.RETAINED_VERSION,
                "inputs": {"producer": tiers.RETAINED_PRODUCER},
                "fullArtifactPath": "analysis/private/source-aware-tiers/full.json.gz",
                "fullArtifactSha256": "f" * 64}}}}

    def test_pin_declares_current_producer_and_retained_pipeline_identity(self):
        state = self.pin()
        payload = read_checkpoint(self.store, state["inputs"], stage="pinned-inputs")
        self.assertEqual(payload["producer"], tiers.RETAINED_PRODUCER)
        self.assertEqual(payload["pipelineCode"], tiers.pipeline_code_identity(True))

    def test_adapter_dispatches_the_current_entry_point_with_immutable_inputs(self):
        state, candidate = self.pin(), self.candidate()
        with patch.object(tiers, "build_retained_hosted_tiers", return_value=candidate) as retained, \
             patch.object(tiers, "build_hosted_tiers", side_effect=AssertionError("Legacy producer invoked")):
            result = tiers.advance_tier_publication(self.store, state)
        retained.assert_called_once()
        self.assertEqual(retained.call_args.kwargs["input_reference_sha256"], state["inputs"]["payloadSha256"])
        self.assertEqual(result["schemaVersion"], 61)
        self.assertEqual(result["producer"], tiers.RETAINED_PRODUCER)
        self.assertEqual(result["progressOrdinal"], 14)
        self.assertEqual(result["totalStages"], 14)
        self.assertEqual(result["completedTargetFits"], 4)

    def test_legacy_pinned_inputs_cannot_reach_any_builder(self):
        payload = {"first": {}, "second": {}, "manifest": {}, "records": [], "metadata": {},
                   "generatedAtUtc": self.timestamp, "pipelineCode": tiers.pipeline_code_identity(True)}
        reference = write_checkpoint(self.store, payload, stage="pinned-inputs", identity=tiers.digest(payload))
        state = {"inputs": reference, "generatedAtUtc": self.timestamp, "producer": tiers.RETAINED_PRODUCER}
        with patch.object(tiers, "build_retained_hosted_tiers") as retained, \
             patch.object(tiers, "build_hosted_tiers") as legacy:
            with self.assertRaises(ValueError):
                tiers.advance_tier_publication(self.store, state)
        retained.assert_not_called()
        legacy.assert_not_called()

    def test_legacy_captured_generation_cannot_resume_finish_or_promote(self):
        state = self.pin()
        identity = {"pipelineCode": tiers.pipeline_code_identity(True)}
        generation = tiers.digest(identity)
        self.store.put_json(tiers.PREFIX + "generations/" + generation + "/state.json", {
            "generation": generation, "identity": identity, "createdAtUtc": self.timestamp,
            "pinnedInputsSha256": state["inputs"]["payloadSha256"]})
        with patch.object(tiers, "_advance_finish") as finish:
            with self.assertRaises(ValueError):
                tiers.advance_tier_publication(self.store, {**state, "generation": generation})
        finish.assert_not_called()

    def test_ready_boundary_rejects_retired_or_mislabelled_candidates(self):
        candidates = []
        for schema in (57, 60):
            candidate = self.candidate()
            candidate["schemaVersion"] = schema
            candidates.append(candidate)
        for section in ("retainedGoalProducer", "productionTierPublication"):
            candidate = self.candidate()
            marker = candidate["summary"]["method"][section]
            if section == "retainedGoalProducer":
                marker["producer"] = "legacy-eight-fits"
            else:
                marker["inputs"]["producer"] = "legacy-eight-fits"
            candidates.append(candidate)
        candidate = self.candidate()
        candidate["summary"]["method"]["productionTierPublication"]["method"] = tiers.WIDTH_VERSION
        candidates.append(candidate)
        for candidate in candidates:
            with self.subTest(candidate=copy.deepcopy(candidate)):
                with self.assertRaises(ValueError):
                    tiers._ready_worker_result({"producer": tiers.RETAINED_PRODUCER}, candidate)


if __name__ == "__main__":
    unittest.main()
