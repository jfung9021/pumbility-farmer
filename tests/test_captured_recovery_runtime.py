"""New-job adoption and pre-claim admission using verified, small saved bundles."""
import copy
import unittest
from datetime import timedelta
from importlib import import_module
from unittest.mock import Mock, patch

import analysis_runtime as runtime
from captured_run_inputs import load_captured_run_inputs
from canonical_json import canonical_json_sha256 as digest
from mix_registry import resolve_mix
from tests import test_captured_run_inputs as input_fixtures

ARGUMENTS = {"official_snapshot": None, "cross_level_clearing": True, "midpoint_compression": True,
             "hybrid_lower_tiers": True, "average_pumbility": True}


class ReservedJobs(runtime.MemoryJobStore):
    blocked = False

    def captured_recovery_admission(self, job_id=None):
        return {"blocked": True, "state": "draining", "intentId": "a" * 64} if self.blocked else None


class CapturedRecoveryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = input_fixtures.CapturedRunInputsTests(methodName="runTest")
        self.fixture.setUp()
        result = self.fixture.prepare()
        self.store = self.fixture.store
        self.bundle = load_captured_run_inputs(self.store, result["bundleId"])
        self.jobs = ReservedJobs()
        self.stamp = input_fixtures.NOW + timedelta(hours=4)
        self.job_id = "analysis-captured-replacement"
        self.job = {**runtime.new_job(self.job_id, self.stamp),
            "capturedRunBundleId": result["bundleId"], "capturedSourceJobId": self.fixture.job_id,
            "capturedRecoveryIntentId": "a" * 64, "refreshOfficialSources": False}
        self.jobs.save(self.job)
        self.jobs.set_active_job_id(self.job_id)
        self.checkpoint_path = runtime.typed_checkpoint_path(self.job_id)

    def adopt(self):
        module = import_module("captured_run_runtime")
        return module.adopt_captured_run_checkpoint(blob_store=self.store, job_store=self.jobs,
            job_id=self.job_id, mix_spec=resolve_mix("phoenix2"), bundle=self.bundle,
            checkpoint_path=self.checkpoint_path, lease_heartbeat=None, now=lambda: self.stamp)

    def test_saved_input_adoption_writes_fresh_owned_envelopes_without_base_fits_or_upstream(self):
        before = copy.deepcopy(self.store.values)
        binaries_before = copy.deepcopy(self.store.binary_values)
        with patch("analysis_runtime.PiuScoresClient", side_effect=AssertionError("No upstream client")), \
             patch("analysis_runtime.synchronize_phoenix2_snapshot", side_effect=AssertionError("No submitted fetch")), \
             patch("official_scoring_sources.refresh_hosted_official_scoring_sources", side_effect=AssertionError("No official probe")), \
             patch("analysis_runtime.analyze_snapshot", side_effect=AssertionError("No base reanalysis")):
            returned = self.adopt()
        checkpoint = self.store.get_json(self.checkpoint_path)
        self.assertEqual(checkpoint, returned)
        self.assertEqual(checkpoint["jobId"], self.job_id)
        self.assertFalse(checkpoint["reanalyzeOnly"])
        self.assertNotEqual(checkpoint["snapshot"]["pathname"], self.fixture.checkpoint["snapshot"]["pathname"])
        snapshot = runtime._load_typed_checkpoint_snapshot(self.store, checkpoint=checkpoint,
            reference=checkpoint["snapshot"], mix_spec=resolve_mix("phoenix2"))
        self.assertEqual(snapshot, self.fixture.second)
        for name, dataset in runtime._typed_manifest(checkpoint)["datasets"].items():
            loaded = []
            for descriptor in dataset["shards"]:
                loaded.extend(runtime._load_typed_checkpoint_shard(self.store, checkpoint=checkpoint,
                    dataset=name, descriptor=descriptor))
            self.assertEqual(loaded, self.bundle["typedFrames"][name])
            self.assertTrue(all(self.job_id in shard["pathname"] for shard in dataset["shards"]))
        for path, value in before.items():
            self.assertEqual(self.store.get_json(path), value)
        for path, value in binaries_before.items():
            self.assertEqual(self.store.get_bytes(path), value)

    def test_waiting_reservation_blocks_worker_before_claim_or_checkpoint_work(self):
        self.jobs.blocked = True
        with patch.object(self.jobs, "save", side_effect=AssertionError("No claim while draining")), \
             patch("analysis_runtime._resume_typed_analysis_checkpoint", side_effect=AssertionError("No resume while draining")), \
             patch("analysis_runtime.PiuScoresClient", side_effect=AssertionError("No upstream")), \
             patch("analysis_runtime.analyze_snapshot", side_effect=AssertionError("No numerical work")):
            result = runtime.execute_analysis_job(self.job_id, blobs=self.store, jobs=self.jobs, now=lambda: self.stamp)
        self.assertEqual(result["status"], "queued")
        self.assertIsNone(self.store.get_json(self.checkpoint_path))
        self.assertEqual(self.jobs.active_job_id(), self.job_id)

    def test_adoption_budget_yields_a_durable_worker_continuation(self):
        from captured_run_runtime import CapturedAdoptionPending
        self.store.typed_persistence_enabled = True
        with patch("captured_run_runtime.validate_captured_context"), \
             patch("analysis_runtime._adopt_captured_run_checkpoint",
                   side_effect=CapturedAdoptionPending("saved-rows-4", 4, 12)), \
             patch("analysis_runtime.analyze_snapshot", side_effect=AssertionError("No base fit")), \
             patch("analysis_runtime.PiuScoresClient", side_effect=AssertionError("No upstream")):
            result = runtime.execute_analysis_job(self.job_id, blobs=self.store, jobs=self.jobs,
                now=lambda: self.stamp, yield_after_typed_checkpoint=True)
        self.assertEqual(result["status"], "running")
        self.assertEqual(result[runtime.ANALYSIS_CONTINUATION_FIELD], "captured-adoption")
        self.assertEqual(result["progress"]["current"], 4)
        self.assertEqual(result["progress"]["total"], 12)

    def test_waiting_reservation_blocks_normal_cron_or_operator_before_creation(self):
        self.jobs.blocked = True
        enqueue = Mock()
        with patch.object(self.jobs, "save", side_effect=AssertionError("No competing queued job")):
            status, _ = runtime.request_refresh(self.store, self.jobs, enqueue,
                force_refresh=True, trigger="cron", now=self.stamp)
        self.assertEqual(status, 409)
        enqueue.assert_not_called()
        self.assertEqual(self.jobs.active_job_id(), self.job_id)

    def test_current_worker_resumes_new_owned_checkpoint_without_recomputing_base_analysis(self):
        self.adopt()
        self.store.typed_persistence_enabled = True
        current = self.store.get_json(self.checkpoint_path)
        with patch("analysis_runtime._resume_typed_analysis_checkpoint", return_value={"id": self.job_id, "status": "running"}) as resume, \
             patch("captured_run_runtime.validate_captured_context") as validate_context, \
             patch("analysis_runtime.PiuScoresClient", side_effect=AssertionError("No upstream")), \
             patch("analysis_runtime.analyze_snapshot", side_effect=AssertionError("No base reanalysis")):
            result = runtime.execute_analysis_job(self.job_id, blobs=self.store, jobs=self.jobs, now=lambda: self.stamp)
        self.assertEqual(result["status"], "running")
        self.assertEqual(resume.call_args.args[0], current)
        self.assertEqual(resume.call_args.kwargs["job_id"], self.job_id)
        self.assertNotEqual(resume.call_args.args[0]["jobId"], self.fixture.job_id)
        validate_context.assert_called_once_with(self.store, self.bundle["context"])

    def test_adopted_job_reaches_joined_publication_without_any_upstream_or_base_reanalysis(self):
        from tests import test_analysis_runtime as fixtures
        self.store.typed_persistence_enabled = True
        self.jobs.lease_owner = "fixture-owner"
        self.store.tier_publication_revisions = Mock(return_value={
            "analysis/private/source-revisions/phoenix1.json": {"sha256": "first", "byteSize": 1, "revision": "v1"},
            "analysis/private/source-revisions/phoenix2.json": {"sha256": "consent", "byteSize": 1, "revision": "v2"},
            runtime.official_scoring_manifest_path(): {"sha256": "manifest", "byteSize": 1, "revision": "v3"}})
        self.bundle["context"].setdefault("sourceGuard", {}).setdefault("sources", {})
        adoption_guard = {"capturedAtUtc": self.fixture.second["generatedAtUtc"],
                          "sourceRevision": self.store.tier_publication_revisions()["analysis/private/source-revisions/phoenix2.json"]}
        self.bundle["context"]["canonicalAdoptionGuard"] = adoption_guard
        memory_put = self.store.put_json
        self.store.put_json = Mock(side_effect=lambda path, value, **kwargs: memory_put(path, value))
        self.store.persist_typed_generation = Mock(side_effect=lambda **kwargs:
            ("analysis-run", "model-generation" if kwargs["phase"] == "model" else None))
        self.store.publish_generation = Mock(side_effect=lambda payloads, **kwargs: self.store.put_json_bundle(payloads))
        with patch("captured_run_runtime.validate_captured_context"), \
             patch("analysis_runtime.load_captured_recovery_inputs", return_value=self.bundle), \
             patch("analysis_runtime.build_combined_chart_results", return_value=([], {}, {})), \
             patch("analysis_runtime.build_combined_tier_payload", side_effect=fixtures._combined_tier_payload_fixture), \
             patch("analysis_runtime.build_recommendation_model_artifacts", side_effect=fixtures._recommendation_model_artifacts_fixture), \
             patch("analysis_runtime.PiuScoresClient", side_effect=AssertionError("No upstream client")), \
             patch("analysis_runtime.synchronize_phoenix2_snapshot", side_effect=AssertionError("No submitted fetch")), \
             patch("official_scoring_sources.refresh_hosted_official_scoring_sources", side_effect=AssertionError("No official probe")), \
             patch("analysis_runtime.analyze_snapshot", side_effect=AssertionError("No base reanalysis")):
            result = runtime.execute_analysis_job(self.job_id, blobs=self.store, jobs=self.jobs, now=lambda: self.stamp)
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(self.store.get_json(runtime.current_snapshot_path()), self.fixture.second)
        canonical_calls = [call for call in self.store.put_json.call_args_list if call.args[0] == runtime.current_snapshot_path()]
        self.assertEqual(len(canonical_calls), 1)
        self.assertEqual(canonical_calls[0].kwargs["captured_recovery_source"],
                         {**adoption_guard, "jobId": self.job_id, "bundleId": self.bundle["bundleId"]})
        self.assertEqual(self.store.get_json(runtime.latest_blob_path())["generatedAtUtc"], runtime.isoformat_utc(self.stamp))
        self.store.publish_generation.assert_called_once()
        guard = self.store.publish_generation.call_args.kwargs["captured_recovery"]
        self.assertEqual(guard["jobId"], self.job_id)
        self.assertEqual(guard["leaseOwner"], "fixture-owner")
        self.assertIn("analysis/private/source-revisions/phoenix2.json", guard["sourceRevisions"])
        phases = [call.kwargs["phase"] for call in self.store.persist_typed_generation.call_args_list]
        self.assertIn("analysis-start", phases)
        self.assertIn("model", phases)

    def test_saved_context_source_drift_fails_before_policy_or_numerical_work(self):
        module = import_module("captured_run_runtime")
        context = {"version": 1, "sourceGuard": {"flags": {}, "sources": {"fixture-source": "old"}},
                   "producerRecipe": "recipe", "catalog": {}, "sourcePolicy": {},
                   "arguments": ARGUMENTS, "originalProducerIdentity": {"generalArguments": digest(ARGUMENTS)}}
        with patch("analysis_runtime._combined_preparation_guard", return_value={"flags": {}, "sources": {"fixture-source": "new"}}), \
             patch("chart_source_exclusions.load_exclusions", side_effect=AssertionError("No policy replay after source drift")), \
             patch("production_tiers.numerical_pipeline_recipe", side_effect=AssertionError("No recipe replay after source drift")):
            with self.assertRaisesRegex(ValueError, "source revisions"):
                module.validate_captured_context(self.store, context)

    def test_context_flags_recipe_and_exclusion_policy_must_match(self):
        module = import_module("captured_run_runtime")
        context = {"version": 1, "sourceGuard": {"flags": {"policy": "1"}, "sources": {}},
                   "producerRecipe": "recipe", "catalog": {}, "sourcePolicy": {"expected": True},
                   "arguments": ARGUMENTS, "originalProducerIdentity": {"generalArguments": digest(ARGUMENTS)}}
        for change in ("flags", "recipe", "policy"):
            with self.subTest(change=change), \
                 patch("analysis_runtime._combined_preparation_guard", return_value={"flags": {"policy": "0" if change == "flags" else "1"}, "sources": {}}), \
                 patch("production_tiers.numerical_pipeline_recipe", return_value="changed" if change == "recipe" else "recipe"), \
                 patch("chart_source_exclusions.load_exclusions", return_value=Mock(public=Mock(return_value={"expected": change != "policy"}))):
                expected = {"flags": "source revisions", "recipe": "producer changed", "policy": "exclusion policy"}[change]
                with self.assertRaisesRegex(ValueError, expected): module.validate_captured_context(self.store, context)

    def test_runtime_preview_context_uses_validated_historical_policy_and_prepared_catalog(self):
        from canonical_json import canonical_json_sha256 as digest
        from chart_source_exclusions import load_exclusions
        from production_tiers import PREFIX
        from tier_pipeline_checkpoints import write_checkpoint
        module = import_module("captured_run_runtime")
        catalog = [{"id": "22ee894e-8601-4b55-9d04-c31187f8fa22", "type": "Double"}]
        policy = load_exclusions(catalog).public()
        arguments = {"official_snapshot": None, "cross_level_clearing": True, "midpoint_compression": True,
                     "hybrid_lower_tiers": True, "average_pumbility": True}
        identity = {"pipelineCode": "historical-code", "generalArguments": digest(arguments)}
        generation = digest(identity)
        model = write_checkpoint(self.store, {"prepared": {"catalog": catalog}}, stage="phase-inputs-model",
            identity=digest({"generation": generation, "kind": "model"}))
        provenance = write_checkpoint(self.store, {"policy": policy}, stage="phase-inputs-provenance",
            identity=digest({"generation": generation, "kind": "provenance"}))
        self.store.put_json(PREFIX + "generations/" + generation + "/state.json",
            {"generation": generation, "identity": identity, "phaseInputs": {"model": model, "provenance": provenance}})
        checkpoint = copy.deepcopy(self.fixture.checkpoint)
        checkpoint["tierProgress"] = {"version": 1, "generation": generation, "ordinal": 0, "stage": "general"}
        with patch("analysis_runtime._combined_preparation_guard", return_value={"flags": {}, "sources": {}}), \
             patch("analysis_runtime._official_tier_arguments", return_value=arguments), \
             patch("production_tiers.numerical_pipeline_recipe", return_value="qualified-retained-recipe"), \
             patch("production_tiers.inventory_retained_fits", return_value=({"cohorts": []}, [])), \
             patch("analysis_runtime.PiuScoresClient", side_effect=AssertionError("No upstream")), \
             patch("analysis_runtime.analyze_snapshot", side_effect=AssertionError("No numerical work")):
            context = module.captured_run_recovery_context(self.store, checkpoint)
        self.assertEqual(context["catalog"], catalog)
        self.assertEqual(context["sourcePolicy"], policy)
        self.assertEqual(context["arguments"], arguments)
        self.assertEqual(context["originalProducerIdentity"], identity)


if __name__ == "__main__": unittest.main()
