from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import analysis_runtime as runtime
from pumbility_contract import combined_tier_blob_path, phoenix1_snapshot_path, recommendation_blob_path
from recommendation_model_refresh import checkpoint_path, execute_recommendation_model_refresh


STAMP = datetime(2026, 11, 28, 10, tzinfo=timezone.utc)


class RecommendationStore(runtime.MemoryBlobStore):
    typed_persistence_enabled = True

    def __init__(self, jobs):
        super().__init__()
        self.jobs = jobs
        self.registrations = []
        self.promotions = []
        self.commit_then_raise = False
        self.reject_publication = False
        charts = [{"id": f"base-{number}", "type": "Single", "level": 10,
                   "difficulty": "S10", "songName": f"Base {number}"} for number in range(50)]
        charts.append({"id": "target", "type": "Single", "level": 16, "difficulty": "S16", "songName": "Target"})
        scores = [{"playerId": "subject", "chartId": chart["id"], "pumbility": 100,
                   "score": 950000, "plate": "MG", "isBroken": False} for chart in charts[:50]]
        for peer in range(20):
            scores.extend({"playerId": f"peer-{peer}", "chartId": chart["id"], "pumbility": 101,
                           "score": 950000, "plate": "MG", "isBroken": False} for chart in charts[:50])
            scores.append({"playerId": f"peer-{peer}", "chartId": "target", "pumbility": 0,
                           "score": 995000, "plate": "MG", "isBroken": False})
        paths = {"base": runtime.latest_blob_path("phoenix2"), "tiers": combined_tier_blob_path(),
                 "recommendations": recommendation_blob_path(), "phoenix1": phoenix1_snapshot_path(),
                 "phoenix2": runtime.current_snapshot_path("phoenix2")}
        self.put_json(paths["base"], {"generatedAtUtc": "2026-11-27T00:00:00Z", "base": "unchanged"})
        self.put_json(paths["tiers"], {"generatedAtUtc": "2026-11-27T00:00:00Z", "singles": [], "doubles": [], "coop": []})
        self.put_json(paths["recommendations"], {"generationKey": "previous"})
        self.put_json(paths["phoenix1"], {"mix": "Phoenix", "charts": charts, "players": [], "scores": []})
        self.put_json(paths["phoenix2"], {"mix": "Phoenix2", "generatedAtUtc": "2026-11-27T00:00:00Z",
            "charts": charts, "players": [{"playerId": "subject", "username": "Subject"}], "scores": scores})
        self.boundary = {"analysisRunId": "existing-published-analysis", "previousModelGenerationId": "old-model",
                         "previousGenerationKey": "previous", "artifactPaths": paths,
                         "expectedRevisions": {path: {"sha256": runtime._canonical_json_sha256(self.values[path]),
                                                       "byteSize": 1, "revision": "1"} for path in paths.values()}}

    def recommendation_refresh_boundary(self):
        return copy.deepcopy(self.boundary)

    def persist_typed_generation(self, **kwargs):
        self.registrations.append(copy.deepcopy(kwargs))
        assert kwargs["phase"] == "model"
        assert kwargs["analysis_run_id"] == self.boundary["analysisRunId"]
        return kwargs["analysis_run_id"], "new-model"

    def publish_recommendation_refresh(self, index, **kwargs):
        self.promotions.append(copy.deepcopy(kwargs))
        if self.reject_publication:
            raise RuntimeError("The publication lease or pinned inputs changed.")
        assert kwargs["expected_revisions"] == self.boundary["expectedRevisions"]
        assert kwargs["analysis_run_id"] == self.boundary["analysisRunId"]
        self.put_json(recommendation_blob_path(), index)
        self.jobs.save(kwargs["completed_job"])
        self.jobs.set_active_job_id(None)
        if self.commit_then_raise:
            raise TimeoutError("The commit reply was lost.")

    def publication_committed(self, **kwargs):
        return self.get_json(recommendation_blob_path()).get("generationKey") == kwargs["generation_key"]


class RecommendationModelRefreshTests(unittest.TestCase):
    def setUp(self):
        self.jobs = runtime.MemoryJobStore()
        self.jobs.lease_owner = "test-owner"
        self.job = runtime.new_job("rec-only-test", STAMP, recommendations_only=True)
        self.jobs.save(self.job)
        self.jobs.set_active_job_id(self.job["id"])
        self.store = RecommendationStore(self.jobs)

    def test_runtime_dispatch_skips_capture_analyzer_and_tier_functions(self):
        paths = self.store.boundary["artifactPaths"]
        protected = {key: copy.deepcopy(self.store.values[paths[key]]) for key in ("base", "tiers", "phoenix1", "phoenix2")}
        timestamps = {key: self.store.uploaded[paths[key]] for key in protected}
        with patch.object(runtime, "_advance_job_official_sources", side_effect=AssertionError("no official capture")), \
             patch.object(runtime, "analyze_snapshot", side_effect=AssertionError("no numeric analysis")), \
             patch.object(runtime, "build_combined_chart_results", side_effect=AssertionError("no tier fitting")), \
             patch.object(runtime, "build_combined_tier_payload", side_effect=AssertionError("no tier publication")):
            result = runtime.execute_analysis_job(self.job["id"], blobs=self.store, jobs=self.jobs, now=lambda: STAMP)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["recommendationsOnly"])
        self.assertEqual(len(self.store.registrations), 1)
        self.assertEqual(self.store.registrations[0]["phase"], "model")
        self.assertEqual(self.store.get_json(recommendation_blob_path())["baseAnalysisRunId"], "existing-published-analysis")
        self.assertIsNone(self.jobs.active_job_id())
        for key, original in protected.items():
            self.assertEqual(self.store.get_json(paths[key]), original)
            self.assertEqual(self.store.uploaded[paths[key]], timestamps[key])

    def test_three_queue_phases_resume_without_rebuilding_staged_model(self):
        self.jobs.save({**self.job, "status": "running"})
        first = execute_recommendation_model_refresh(blob_store=self.store, job_store=self.jobs,
            job_id=self.job["id"], now=lambda: STAMP, yield_after_checkpoint=True)
        self.assertEqual(first[runtime.ANALYSIS_CONTINUATION_FIELD], "database-model")
        self.assertEqual(self.store.get_json(checkpoint_path(self.job["id"]))["phase"], "staged")
        with patch("recommendation_model_refresh.build_recommendation_model_artifacts",
                   side_effect=AssertionError("staged model must be reused")):
            second = execute_recommendation_model_refresh(blob_store=self.store, job_store=self.jobs,
                job_id=self.job["id"], now=lambda: STAMP, yield_after_checkpoint=True)
            self.assertEqual(second[runtime.ANALYSIS_CONTINUATION_FIELD], "publish")
            self.assertEqual(self.store.get_json(recommendation_blob_path())["generationKey"], "previous")
            final = execute_recommendation_model_refresh(blob_store=self.store, job_store=self.jobs,
                job_id=self.job["id"], now=lambda: STAMP, yield_after_checkpoint=True)
        self.assertEqual(final["status"], "completed")
        self.assertEqual(len(self.store.registrations), 1)

    def test_failed_publication_fence_leaves_recommendation_head_unchanged(self):
        self.jobs.save({**self.job, "status": "running"})
        self.store.reject_publication = True
        with self.assertRaisesRegex(RuntimeError, "lease or pinned inputs"):
            execute_recommendation_model_refresh(blob_store=self.store, job_store=self.jobs,
                job_id=self.job["id"], now=lambda: STAMP)
        self.assertEqual(self.store.get_json(recommendation_blob_path())["generationKey"], "previous")

    def test_ambiguous_commit_is_resolved_and_terminal_replay_is_read_only(self):
        self.jobs.save({**self.job, "status": "running"})
        self.store.commit_then_raise = True
        result = execute_recommendation_model_refresh(blob_store=self.store, job_store=self.jobs,
            job_id=self.job["id"], now=lambda: STAMP)
        self.assertEqual(result["status"], "completed")
        promoted = len(self.store.promotions)
        replay = execute_recommendation_model_refresh(blob_store=self.store, job_store=self.jobs,
            job_id=self.job["id"], now=lambda: STAMP)
        self.assertEqual(replay, result)
        self.assertEqual(len(self.store.promotions), promoted)

    def test_request_never_captures_official_sources_for_recommendations(self):
        jobs = runtime.MemoryJobStore()
        with patch.dict("os.environ", {"PIU_OFFICIAL_CROSS_LEVEL_SCORING": "1"}):
            status, body = runtime.request_refresh(runtime.MemoryBlobStore(), jobs, lambda _: None,
                now=STAMP, force_refresh=True, recommendations_only=True)
        self.assertEqual(status, 202)
        self.assertTrue(body["job"]["recommendationsOnly"])
        self.assertTrue(body["job"]["reanalyzeOnly"])
        self.assertFalse(body["job"]["refreshOfficialSources"])

    def test_recommendation_request_cannot_resume_cancelled_full_analysis_checkpoint(self):
        jobs = runtime.MemoryJobStore()
        previous = runtime.new_job("cancelled-full-analysis", STAMP)
        jobs.save({**previous, "status": "failed", "cancelRequested": True})
        jobs.set_latest_job_id(previous["id"])
        jobs.requeue = Mock(side_effect=AssertionError("cancelled tier work must not resume"))
        blobs = runtime.MemoryBlobStore()
        blobs.put_json(runtime.typed_checkpoint_path(previous["id"]), {
            "phase": runtime.TYPED_CHECKPOINT_DATABASE_MODEL_PHASE, "publicationAttempts": 0,
        })
        status, body = runtime.request_refresh(blobs, jobs, lambda _: None,
            now=STAMP, force_refresh=True, recommendations_only=True)
        self.assertEqual(status, 202)
        self.assertNotEqual(body["job"]["id"], previous["id"])
        self.assertTrue(body["job"]["recommendationsOnly"])
        jobs.requeue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
