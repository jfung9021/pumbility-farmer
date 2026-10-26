"""Official capture must finish before a new refresh can pin model inputs."""
import copy
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import analysis_runtime as runtime
from official_scoring_sources import refresh_hosted_official_scoring_sources
from pumbility_contract import official_scoring_manifest_path
from tests.test_official_scoring_sources import FakeClient, chart, api_board, row, A, B, OLD, NEW
from worker import tasks

NOW = datetime(2026, 10, 26, 1, tzinfo=timezone.utc)


class CatalogClient(FakeClient):
    def fetch_page_collection(self, path):
        return [chart(), chart(B, level=21)]


class OfficialRefreshWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.blobs = runtime.MemoryBlobStore()
        self.jobs = runtime.MemoryJobStore()
        with patch.dict(os.environ, {"PIU_OFFICIAL_CROSS_LEVEL_SCORING": "1"}):
            _, result = runtime.request_refresh(self.blobs, self.jobs, lambda _: None,
                now=NOW, force_refresh=True, trigger="cron")
        self.job_id = result["job"]["id"]

    def run_job(self, client=None):
        return runtime.execute_analysis_job(self.job_id, blobs=self.blobs, jobs=self.jobs,
            client=client or object(), now=lambda: NOW, yield_after_typed_checkpoint=True)

    def test_new_cron_and_operator_requests_capture_only_when_enabled(self):
        self.assertTrue(self.jobs.get(self.job_id)["refreshOfficialSources"])
        for enabled in ("0", "1"):
            with self.subTest(enabled=enabled), patch.dict(os.environ, {"PIU_OFFICIAL_CROSS_LEVEL_SCORING": enabled}):
                jobs = runtime.MemoryJobStore()
                _, result = runtime.request_refresh(runtime.MemoryBlobStore(), jobs,
                    lambda _: None, now=NOW, force_refresh=True, trigger="jonathan", reanalyze_only=True)
                self.assertEqual(jobs.get(result["job"]["id"])["refreshOfficialSources"], enabled == "1")

    def test_real_capture_batches_cross_worker_boundary_before_sync(self):
        clients = iter([
            CatalogClient([api_board([row()], OLD), api_board([row()], OLD)]),
            CatalogClient([api_board([row(99)], NEW)]),
            CatalogClient([api_board([row(99)], NEW), api_board([row(99)], NEW)]),
        ])
        def batch(client, store, *, max_boards, refresh_id=None):
            self.assertEqual(max_boards, 100)
            # Small real captures force two partial continuations and a complete
            # promotion through the production worker's existing queue handoff.
            size = len(client.responses)
            return refresh_hosted_official_scoring_sources(client, store, max_boards=size,
                                                           refresh_id=refresh_id)
        def execute(job_id, **kwargs):
            self.assertEqual(job_id, self.job_id)
            return self.run_job(next(clients))
        with patch("official_scoring_sources.refresh_hosted_official_scoring_sources", side_effect=batch), \
             patch.object(tasks, "execute_analysis_job", side_effect=execute), \
             patch.object(tasks.refresh_analysis, "apply_async") as enqueue, \
             patch("analysis_runtime.synchronize_phoenix2_snapshot") as sync:
            ids = []
            for ordinal in range(3):
                result = tasks.refresh_analysis.run(self.job_id)
                self.assertEqual(result["status"], "running")
                sync.assert_not_called()
                ids.append(enqueue.call_args.kwargs["task_id"])
                if ordinal < 2:
                    self.assertIsNone(self.blobs.get_json(official_scoring_manifest_path()))
                    self.assertEqual(result["progress"]["phase"], "official-sources")
                else:
                    self.assertIsNotNone(self.blobs.get_json(official_scoring_manifest_path()))
            self.assertEqual(len(set(ids)), 3)
            self.assertTrue(all("official-sources" in identity for identity in ids))
        marker = self.jobs.get(self.job_id)["officialSourceCapture"]
        self.assertEqual(marker["status"], "completed")
        self.assertEqual(marker["sourceDates"], {"Phoenix": OLD, "Phoenix2": NEW})
        self.assertNotIn("PLAYER#1234", str(marker))
        with patch("official_scoring_sources.refresh_hosted_official_scoring_sources") as capture, \
             patch("analysis_runtime.synchronize_phoenix2_snapshot", side_effect=RuntimeError("stop after capture gate")) as sync:
            self.run_job()
            capture.assert_not_called()
            sync.assert_called_once()

    def test_failed_capture_preserves_manifest_and_publication_and_blocks_analysis(self):
        old = {"sentinel": "last complete manifest"}
        published = {"sentinel": "last published rankings"}
        self.blobs.put_json(official_scoring_manifest_path(), old)
        self.blobs.put_json(runtime.LATEST_BLOB_PATH, published)
        before = copy.deepcopy(self.blobs.values)
        with patch("official_scoring_sources.refresh_hosted_official_scoring_sources", side_effect=RuntimeError("capture failed")), \
             patch("analysis_runtime.synchronize_phoenix2_snapshot") as sync:
            result = self.run_job()
        self.assertEqual(result["status"], "failed")
        sync.assert_not_called()
        self.assertIsNone(self.jobs.active_job_id())
        self.assertEqual(self.blobs.get_json(official_scoring_manifest_path()), old)
        self.assertEqual(self.blobs.get_json(runtime.LATEST_BLOB_PATH), published)
        self.assertEqual(self.blobs.values, before)

    def test_capture_yields_at_work_deadline_with_durable_partial_evidence(self):
        class SingleChartClient(FakeClient):
            def fetch_page_collection(self, path):
                return [chart()]
        client = SingleChartClient([api_board([row()], OLD)])
        with patch("analysis_budget.work_budget_available", side_effect=[True, False]):
            result = refresh_hosted_official_scoring_sources(client, self.blobs, max_boards=100)
        self.assertEqual(len(client.paths), 1)
        self.assertEqual(result["outcome"], "official-sources-progress")
        self.assertEqual(result["capturedBoards"], {"Phoenix": 1})
        self.assertIsNone(self.blobs.get_json(official_scoring_manifest_path()))

    def test_same_epoch_stall_is_bounded_and_new_epoch_resets_observations(self):
        progress = {"outcome": "official-sources-progress", "capturedBoards": {"Phoenix": 2},
                    "targetCharts": 2, "progressIdentity": "a" * 64}
        with patch("official_scoring_sources.refresh_hosted_official_scoring_sources", return_value=progress), \
             patch("analysis_runtime.synchronize_phoenix2_snapshot") as sync:
            self.assertEqual(self.run_job()["status"], "running")
            self.assertEqual(self.run_job()["status"], "running")
            progress["progressIdentity"] = "b" * 64
            self.assertEqual(self.run_job()["status"], "running")
            self.assertEqual(self.jobs.get(self.job_id)["officialSourceCapture"]["observations"], 1)
            self.assertEqual(self.run_job()["status"], "running")
            self.assertEqual(self.run_job()["status"], "failed")
            sync.assert_not_called()
        self.assertIsNone(self.jobs.active_job_id())

    def test_legacy_job_and_typed_resume_do_not_recapture_pinned_sources(self):
        job = self.jobs.get(self.job_id)
        job.pop("refreshOfficialSources")
        self.jobs.save(job)
        with patch("official_scoring_sources.refresh_hosted_official_scoring_sources") as capture, \
             patch("analysis_runtime.synchronize_phoenix2_snapshot", side_effect=RuntimeError("legacy sync boundary")) as sync:
            self.run_job()
            capture.assert_not_called()
            sync.assert_called_once()
        class TypedStore(runtime.MemoryBlobStore):
            typed_persistence_enabled = True
        blobs = TypedStore()
        jobs = runtime.MemoryJobStore()
        job = runtime.new_job("historical-pinned", NOW)
        job["refreshOfficialSources"] = True
        jobs.save(job)
        blobs.put_json(runtime.typed_checkpoint_path(job["id"]), {"sentinel": "existing typed identity"})
        with patch("analysis_runtime._resume_typed_analysis_checkpoint", return_value={"status": "running"}) as resume, \
             patch("official_scoring_sources.refresh_hosted_official_scoring_sources") as capture:
            runtime.execute_analysis_job(job["id"], blobs=blobs, jobs=jobs)
            resume.assert_called_once()
            capture.assert_not_called()

    def test_terminal_delivery_does_not_fetch_sources(self):
        job = self.jobs.get(self.job_id)
        job["status"] = "completed"
        self.jobs.save(job)
        with patch("official_scoring_sources.refresh_hosted_official_scoring_sources") as capture:
            result = self.run_job()
        self.assertEqual(result["status"], "completed")
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
