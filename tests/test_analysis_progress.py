from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import timedelta
import json
import unittest
from unittest.mock import Mock, patch

from analysis_runtime import (
    ANALYSIS_CONTINUATION_FIELD, LEGACY_TYPED_CHECKPOINT_SCHEMA_VERSION,
    LATEST_BLOB_PATH, MemoryBlobStore, MemoryJobStore,
    _database_cursor_token, execute_analysis_job, new_job, request_refresh, typed_checkpoint_path,
)
from production_tiers import FINISH_STAGES, PREFIX, STAGE_SCHEMAS, TierAnalysisPending
from pumbility_contract import combined_tier_blob_path
from pumbility_store import JobLeaseHeldError, JobLeaseLostError
from tests.test_analysis_runtime import (
    NOW, WorkerClient, _combined_tier_payload_fixture,
    _recommendation_mode_artifact_fixture, _recommendation_model_artifacts_fixture,
)
from tests.test_production_tiers import context_fixture, fit_for


class TypedMemoryStore(MemoryBlobStore):
    typed_persistence_enabled = True
    fail_progress_ordinal = None

    def put_json(self, pathname, payload):
        progress = payload.get("tierProgress")
        if (pathname.startswith("analysis/private/runtime-checkpoints/") and progress
                and progress["ordinal"] == self.fail_progress_ordinal):
            self.fail_progress_ordinal = None
            raise RuntimeError("private database URL and credential must not appear in logs")
        return super().put_json(pathname, payload)

    def persist_typed_generation(self, **kwargs):
        return "analysis-run", "model-generation" if kwargs["phase"] == "model" else None


class AnalysisProgressTests(unittest.TestCase):
    def setUp(self):
        self.blobs, self.jobs = TypedMemoryStore(), MemoryJobStore()
        self.job = new_job("nested-progress-regression", NOW)
        self.jobs.save(self.job)
        self.jobs.set_active_job_id(self.job["id"])
        self.blobs.put_json("analysis/private/phoenix1.json",
            {"mix": "Phoenix", "players": [], "charts": [], "scores": []})
        first = self.resume()
        self.assertEqual(first[ANALYSIS_CONTINUATION_FIELD], "combined")
        self.path = typed_checkpoint_path(self.job["id"])
        self.timestamp = self.blobs.get_json(self.path)["payload"]["generatedAtUtc"]

    def resume(self):
        return execute_analysis_job(self.job["id"], blobs=self.blobs, jobs=self.jobs,
            client=WorkerClient(), now=lambda: NOW, yield_after_typed_checkpoint=True)

    @contextmanager
    def hosted_pipeline(self, *, required_goals=False):
        context = context_fixture()
        public = _combined_tier_payload_fixture(generated_at_utc=self.timestamp)
        fitter = Mock(side_effect=lambda boards, **kw: fit_for(boards, kw["score_target"]))
        finish = Mock(side_effect=lambda stage, *a: {"schemaVersion": STAGE_SCHEMAS[stage],
            "generatedAtUtc": self.timestamp, "summary": {"generatedAtUtc": self.timestamp}})
        arguments = {"source_aware_store": self.blobs, "hybrid_lower_tiers": True,
            "average_pumbility": True, "midpoint_compression": True, "cross_level_clearing": True,
            "official_scoring_manifest": {"_captures": {"Phoenix2": {}}}}
        with ExitStack() as stack:
            patches = {
                "analysis_runtime._official_tier_arguments": {"return_value": arguments},
                "analysis_runtime.build_combined_chart_results": {"return_value": ([], {}, {})},
                "analysis_runtime.analyze_snapshot": {"side_effect": AssertionError("base analysis repeated")},
                "analysis_runtime.build_recommendation_model_artifacts": {"side_effect": _recommendation_model_artifacts_fixture},
                "analysis_runtime.build_recommendation_score_mode_artifact": {"side_effect": _recommendation_mode_artifact_fixture},
                "production_tiers.load_context": {"return_value": context},
                "production_tiers.pipeline_code_identity": {"return_value": "fixture-pipeline-code"},
                "recommendation_component_cache.computation_code_identity": {"return_value": "fixture-recommendation-code"},
                "official_scoring_sources.canonical_scoring_manifest_sha256": {"return_value": "official"},
                "scoring_percentile.build_production_tier_payload": {"return_value": {"schemaVersion": 48}},
                "sss_scoring_model.fit_target_model": {"new": fitter},
                "target_fit_cache.load_or_fit_target": {"new": lambda store, boards, **kwargs: fitter(boards, **kwargs)},
                "production_tiers._finish_stage": {"new": finish},
                "production_tiers._advance_required_goals": {},
                "production_tiers.public_tiers": {"return_value": public},
                "sss_tail_tiers.apply_sss_tail_tiers": {"side_effect": lambda full: {**full, "schemaVersion": 59}},
                "level20_scoring_width.apply_level20_scoring_width": {"side_effect": lambda full: {**full, "schemaVersion": 60}},
                "production_tiers.validate_public_tiers": {},
                "tier_public.prepare_tier_publication": {"return_value": (public, {})},
                "tier_public.validate_tier_publication": {},
            }
            if required_goals:
                from tier_pipeline_checkpoints import write_checkpoint

                patches["production_tiers.REQUIRED_REFRESH_GOALS"] = {"new": ("ss", "ssplus")}
                patches.pop("production_tiers._advance_required_goals")
                patches["production_tiers._store_goal_artifact"] = {"side_effect":
                    lambda store, *, goal, **kwargs: write_checkpoint(store, {"goal": goal},
                        stage="scoring-goal", identity=goal)}
                patches["scoring_goal_tiers.apply_goal_artifacts"] = {"side_effect":
                    lambda full, *args, **kwargs: {**full, "schemaVersion": 58}}
            for target, options in patches.items():
                stack.enter_context(patch(target, **options))
            yield fitter, finish, public

    def test_all_nested_fits_and_finishing_resumes_reach_publication(self):
        ordinals, preparation = [], []
        with self.hosted_pipeline() as (fitter, finish, public):
            # 12 preparation units, 8 fits, 9 finish/public steps, then at most
            # 20 typed model, shard and pointer continuations for this fixture.
            for _ in range(12 + 8 + 9 + 20):
                result = self.resume()
                self.assertNotEqual(result["status"], "failed", result.get("error"))
                checkpoint = self.blobs.get_json(self.path)
                if result["status"] == "completed":
                    break
                if checkpoint["phase"] == "analysis":
                    value = checkpoint["tierProgress"]
                    if value["ordinal"] == 0:
                        preparation.append(value["preparationOrdinal"])
                    else:
                        ordinals.append(value["ordinal"])
                    self.assertEqual(result["stage"], "analyzing")
                    progress = result["progress"]
                    self.assertEqual(progress["phase"], "tier-analysis")
                    self.assertFalse(progress["indeterminate"])
                    self.assertEqual(progress["current"], checkpoint["tierProgress"]["ordinal"])
                    self.assertEqual(progress["total"], 18)
                    self.assertLess(progress["percent"], 100)
            else:
                self.fail("nested tier continuations never reached publication")
        self.assertEqual(ordinals, list(range(1, 18)))
        self.assertEqual(preparation, list(range(1, 13)))
        self.assertEqual(fitter.call_count, 8)
        self.assertEqual(finish.call_count, len(FINISH_STAGES))
        self.assertIsNotNone(self.blobs.get_json(LATEST_BLOB_PATH))
        self.assertEqual(self.blobs.get_json(combined_tier_blob_path()), public)

    def test_required_goal_continuations_reach_typed_publication_without_stalling(self):
        units = []
        with self.hosted_pipeline(required_goals=True) as (fitter, finish, public):
            # Include all 18 real SS/SS+ pin/fit/artifact work units in addition
            # to the bounded preparation, finish and typed persistence phases.
            for _ in range(12 + 8 + 9 + 18 + 20):
                result = self.resume()
                self.assertNotEqual(result["status"], "failed", result.get("error"))
                if result["status"] == "completed":
                    break
                progress = self.blobs.get_json(self.path).get("tierProgress", {})
                if progress.get("stage") == "scoring-goals":
                    units.append(progress["goalProgressOrdinal"])
            else:
                self.fail("SS/SS+ continuations never reached typed publication")
        self.assertEqual(units, list(range(1, 19)))
        self.assertEqual(fitter.call_count, 16)
        self.assertEqual(finish.call_count, len(FINISH_STAGES))
        self.assertIsNotNone(self.blobs.get_json(LATEST_BLOB_PATH))
        self.assertEqual(self.blobs.get_json(combined_tier_blob_path()), public)

    def test_crash_after_fit_or_finishing_commit_reconciles_before_stall_audit(self):
        for crash_ordinal in (3, 10):
            with self.subTest(crash_ordinal=crash_ordinal):
                if crash_ordinal != 3:
                    self.setUp()
                self.blobs.fail_progress_ordinal = crash_ordinal
                with self.hosted_pipeline() as (fitter, finish, _):
                    self.pin_preparation_inputs()
                    with self.assertLogs("pumbility.publication", level="ERROR") as captured:
                        for _ in range(12):
                            result = self.resume()
                            if result["status"] == "failed":
                                break
                        else:
                            self.fail("simulated crash did not occur")
                    self.assertNotIn("private database URL", "\n".join(captured.output))
                    event = json.loads(captured.records[-1].message)
                    self.assertEqual(event["event"], "analysis_terminal_failure")
                    self.assertEqual(event["stage"], "analyzing")
                    checkpoint = self.blobs.get_json(self.path)
                    self.assertEqual(checkpoint["tierProgress"]["ordinal"], crash_ordinal-1)
                    checkpoint["resumeAudit"] = {"token": _database_cursor_token(checkpoint), "observations": 2}
                    self.blobs.put_json(self.path, checkpoint)
                    counts = fitter.call_count, finish.call_count
                    self.jobs.save({**self.jobs.get(self.job["id"]), "status": "queued"})
                    result = self.resume()
                    self.assertEqual(result["status"], "running", result.get("error"))
                    progress = self.blobs.get_json(self.path)["tierProgress"]
                    self.assertEqual(progress["ordinal"], crash_ordinal+1)
                    self.assertEqual((fitter.call_count, finish.call_count),
                        (counts[0]+1, counts[1]) if crash_ordinal == 3 else (counts[0], counts[1]+1))

    def test_unchanged_committed_progress_still_fails_on_third_observation(self):
        def stalled(*args, on_progress, **kwargs):
            on_progress({"version": 1, "generation": "a"*64, "ordinal": 0, "stage": "target-fit"})
            raise TierAnalysisPending("same-sequence", 0, generation="a"*64)
        with self.hosted_pipeline(), patch("production_tiers.build_hosted_tiers", side_effect=stalled):
            self.assertEqual(self.resume()["status"], "running")
            self.assertEqual(self.resume()["status"], "running")
            with self.assertLogs("pumbility.publication", level="ERROR"):
                result = self.resume()
        self.assertEqual(result["status"], "failed")
        self.assertIn("no progress", result["error"])
        self.assertIsNone(self.jobs.active_job_id())

    def test_old_checkpoint_without_committed_progress_keeps_existing_stall_budget(self):
        checkpoint = self.blobs.get_json(self.path)
        checkpoint["resumeAudit"] = {"token": "analysis", "observations": 2}
        self.blobs.put_json(self.path, checkpoint)
        with self.hosted_pipeline() as (fitter, _, _), self.assertLogs("pumbility.publication", level="ERROR"):
            result = self.resume()
        self.assertEqual(result["status"], "failed")
        self.assertIn("no progress", result["error"])
        fitter.assert_not_called()

    def test_legacy_checkpoint_recovers_only_after_reconciling_committed_fits(self):
        with self.hosted_pipeline() as (fitter, _, _):
            self.pin_preparation_inputs()
            self.assertEqual(self.resume()["status"], "running")
            checkpoint = self.blobs.get_json(self.path)
            checkpoint["schemaVersion"] = LEGACY_TYPED_CHECKPOINT_SCHEMA_VERSION
            checkpoint.pop("tierProgress")
            checkpoint["resumeAudit"] = {"token": "analysis", "observations": 2}
            self.blobs.put_json(self.path, checkpoint)
            result = self.resume()
            self.assertEqual(result["status"], "running", result.get("error"))
        self.assertEqual(fitter.call_count, 2)
        self.assertEqual(self.blobs.get_json(self.path)["tierProgress"]["ordinal"], 2)

    def test_changed_generation_or_corrupt_fit_cannot_advance_old_progress(self):
        for change in ("inputs", "code", "corrupt-fit", "invalid-progress"):
            with self.subTest(change=change):
                if change != "inputs":
                    self.setUp()
                with self.hosted_pipeline() as (fitter, _, _):
                    if change == "corrupt-fit":
                        # A saved fit is checked when its next finish phase
                        # consumes it; finish preparation/fitting first.
                        self.pin_finishing_inputs()
                    else:
                        self.pin_preparation_inputs()
                        self.assertEqual(self.resume()["status"], "running")
                    with ExitStack() as stack:
                        if change == "inputs":
                            source = self.blobs.get_json("analysis/private/phoenix1.json")
                            self.blobs.put_json("analysis/private/phoenix1.json",
                                {**source, "generatedAtUtc": "changed-source"})
                        elif change == "code":
                            stack.enter_context(patch("production_tiers.pipeline_code_identity", return_value="changed-code"))
                        elif change == "corrupt-fit":
                            path = next(path for path in self.blobs.values if path.startswith(PREFIX+"fits/"))
                            saved = self.blobs.get_json(path)
                            saved["identity"]["boards"] = "corrupt"
                            self.blobs.put_json(path, saved)
                        else:
                            checkpoint = self.blobs.get_json(self.path)
                            checkpoint["tierProgress"]["ordinal"] = "invalid"
                            self.blobs.put_json(self.path, checkpoint)
                        with self.assertLogs("pumbility.publication", level="ERROR"):
                            result = self.resume()
                    self.assertEqual(result["status"], "failed")
                    self.assertEqual(fitter.call_count, 8 if change == "corrupt-fit" else 1)
                    self.assertIsNone(self.blobs.get_json(LATEST_BLOB_PATH))

    def test_live_duplicate_retains_delivery_and_database_failures_surface(self):
        with patch.object(self.jobs, "save", side_effect=JobLeaseHeldError("live owner")):
            with self.assertRaises(JobLeaseHeldError):
                self.resume()
        with patch.object(self.jobs, "save", side_effect=RuntimeError("database unavailable")):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                self.resume()

    def revision_backed_store(self):
        from analysis_runtime import _canonical_json_sha256
        self.source_revision = {"value": "original"}
        self.blobs.tier_publication_revisions = lambda: {
            "analysis/private/source-revisions/phoenix1.json": dict(self.source_revision)}
        self.blobs.artifact_revision = lambda path: (
            {"sha256": _canonical_json_sha256(self.blobs.values[path])}
            if path in self.blobs.values else None)

    def pin_preparation_inputs(self):
        for _ in range(12):
            result = self.resume()
            self.assertEqual(result["status"], "running", result.get("error"))
        checkpoint = self.blobs.get_json(self.path)
        self.assertEqual(checkpoint["tierProgress"].get("preparationOrdinal"), 12)
        return checkpoint

    def pin_finishing_inputs(self):
        for _ in range(12 + 8 + 1):
            result = self.resume()
            self.assertEqual(result["status"], "running", result.get("error"))
            checkpoint = self.blobs.get_json(self.path)
            if checkpoint.get("tierProgress", {}).get("ordinal") == 9:
                return checkpoint
        self.fail("tier phase inputs were not pinned")

    def test_global_finish_resume_avoids_snapshot_official_and_cohort_reconstruction(self):
        self.revision_backed_store()
        with self.hosted_pipeline() as (_, finish, _):
            self.pin_finishing_inputs()
            with (
                patch("analysis_runtime._load_typed_checkpoint_snapshot", side_effect=AssertionError("snapshot hydrated")),
                patch("analysis_runtime._build_combined_analysis_checkpoint", side_effect=AssertionError("combined rebuilt")),
                patch("analysis_runtime._official_tier_arguments", side_effect=AssertionError("official hydrated")),
                patch("production_tiers.load_context", side_effect=AssertionError("cohorts rebuilt")),
            ):
                result = self.resume()
            self.assertEqual(result["status"], "running", result.get("error"))
            self.assertEqual(self.blobs.get_json(self.path)["tierProgress"]["ordinal"], 10)
            self.assertEqual(finish.call_count, 1)

    def test_fast_resume_rejects_changed_source_snapshot_or_pinned_preparation(self):
        for change in ("source", "snapshot", "preparation"):
            with self.subTest(change=change):
                if change != "source":
                    self.setUp()
                self.revision_backed_store()
                with self.hosted_pipeline() as (_, finish, _):
                    checkpoint = self.pin_finishing_inputs()
                    if change == "source":
                        self.source_revision["value"] = "changed"
                    elif change == "snapshot":
                        path = checkpoint["snapshot"]["pathname"]
                        self.blobs.put_json(path, {**self.blobs.get_json(path), "unexpected": True})
                    else:
                        path = checkpoint["combinedPreparation"]["pathname"]
                        self.blobs.put_json(path, {**self.blobs.get_json(path), "inputSha256": "changed"})
                    with self.assertLogs("pumbility.publication", level="ERROR"):
                        result = self.resume()
                    self.assertEqual(result["status"], "failed")
                    finish.assert_not_called()
                    self.assertIsNone(self.blobs.get_json(LATEST_BLOB_PATH))

    def test_stale_payload_cannot_cancel_another_workers_running_lease(self):
        jobs = MemoryJobStore()
        active = {**new_job("live-lease", NOW-timedelta(minutes=6)), "status": "running"}
        jobs.save(active)
        jobs.set_active_job_id(active["id"])
        enqueue = Mock()
        with patch.object(jobs, "save", side_effect=JobLeaseLostError("another owner")):
            status, response = request_refresh(MemoryBlobStore(), jobs, enqueue, now=NOW)
        self.assertEqual((status, response["outcome"]), (202, "existing"))
        self.assertEqual(jobs.active_job_id(), active["id"])
        enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
