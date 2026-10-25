"""A confirmed SQL deadline resumes private work without losing stall fences."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from psycopg.errors import QueryCanceled

import analysis_runtime as runtime
from pumbility_store import JobLeaseLostError
from tests import test_analysis_progress as progress_fixture


def statement_timeout():
    return QueryCanceled("canceling statement due to statement timeout")


class AnalysisDatabaseTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.fixture = progress_fixture.AnalysisProgressTests(
            "test_all_nested_fits_and_finishing_resumes_reach_publication")
        self.fixture.setUp()
        self.blobs, self.jobs = self.fixture.blobs, self.fixture.jobs
        self.job_id, self.path = self.fixture.job["id"], self.fixture.path

    def timeout_after_audit(self, *args, on_tier_progress, **kwargs):
        on_tier_progress(None)
        raise statement_timeout()

    def audit_timeout(self):
        with patch("analysis_runtime._build_combined_analysis_checkpoint",
                   side_effect=self.timeout_after_audit):
            return self.fixture.resume()

    def test_checkpoint_read_timeout_retains_same_job_then_reaches_publication(self):
        original = self.blobs.get_json
        failed = False

        def read(path):
            nonlocal failed
            if path == self.path and not failed:
                failed = True
                raise statement_timeout()
            return original(path)

        with patch.object(self.blobs, "get_json", side_effect=read):
            result = self.fixture.resume()
        self.assertEqual(result[runtime.ANALYSIS_CONTINUATION_FIELD], "database-retry")
        self.assertEqual(result["databaseTimeoutRetry"]["attempts"], 1)
        self.assertEqual(self.jobs.active_job_id(), self.job_id)
        self.assertIsNotNone(original(self.path))
        self.assertIsNone(original(runtime.LATEST_BLOB_PATH))
        with self.fixture.hosted_pipeline():
            for _ in range(49):
                result = self.fixture.resume()
                self.assertNotEqual(result["status"], "failed", result.get("error"))
                if result["status"] == "completed":
                    break
            else:
                self.fail("The recovered checkpoint did not publish")
        self.assertIsNotNone(original(runtime.LATEST_BLOB_PATH))
        self.assertEqual(len(self.jobs.jobs), 1)

    def test_timeout_after_work_deadline_uses_checkpoint_reserve_to_requeue(self):
        from analysis_budget import invocation_budget, work_budget_available
        from database_read_policy import (
            background_database_reads, read_statement_timeout_milliseconds,
        )

        original = self.blobs.get_json

        def database_checkpoint_read(path):
            # Model a read that cannot complete under the erroneous 1ms limit.
            if read_statement_timeout_milliseconds(10_000) < 1_000:
                raise statement_timeout()
            return original(path)

        with invocation_budget(0), background_database_reads(), \
                patch.object(self.blobs, "get_json", side_effect=database_checkpoint_read):
            self.assertFalse(work_budget_available())
            result = self.audit_timeout()

        self.assertEqual(result["status"], "running")
        self.assertEqual(result[runtime.ANALYSIS_CONTINUATION_FIELD], "database-retry")
        self.assertEqual(result["databaseTimeoutRetry"]["attempts"], 1)
        self.assertEqual(self.jobs.active_job_id(), self.job_id)
        self.assertIsNotNone(original(self.path))
        self.assertIsNone(original(runtime.LATEST_BLOB_PATH))

    def test_timeout_after_audit_refunds_only_its_observation_and_exhausts_two_retries(self):
        checkpoint = self.blobs.get_json(self.path)
        prior_audit = {"token": runtime._database_cursor_token(checkpoint), "observations": 1}
        checkpoint["resumeAudit"] = prior_audit
        self.blobs.put_json(self.path, checkpoint)
        sequences = []
        for attempt in (1, 2):
            result = self.audit_timeout()
            self.assertEqual(result[runtime.ANALYSIS_CONTINUATION_FIELD], "database-retry")
            self.assertEqual(result["databaseTimeoutRetry"]["attempts"], attempt)
            sequences.append(result[runtime.ANALYSIS_CONTINUATION_SEQUENCE_FIELD])
            self.assertEqual(self.blobs.get_json(self.path)["resumeAudit"], prior_audit)
        self.assertEqual(len(set(sequences)), 2)
        with self.assertLogs("pumbility.publication", level="ERROR"):
            result = self.audit_timeout()
        self.assertEqual(result["status"], "failed")
        self.assertIn("database timeout exhausted", result["error"])
        self.assertEqual(self.blobs.get_json(self.path)["resumeAudit"], prior_audit)
        self.assertNotIn(runtime.ANALYSIS_CONTINUATION_FIELD, result)
        self.assertIsNone(self.jobs.active_job_id())
        self.assertIsNone(self.blobs.get_json(runtime.LATEST_BLOB_PATH))

    def test_refund_preserves_real_no_progress_history(self):
        checkpoint = self.blobs.get_json(self.path)
        checkpoint["resumeAudit"] = {"token": "analysis", "observations": 1}
        self.blobs.put_json(self.path, checkpoint)
        self.assertEqual(self.audit_timeout()["status"], "running")

        def stalled(*args, on_tier_progress, **kwargs):
            on_tier_progress(None)
            raise runtime.TierAnalysisPending("same-sequence", 0, generation="a" * 64)

        with patch("analysis_runtime._build_combined_analysis_checkpoint", side_effect=stalled):
            self.assertEqual(self.fixture.resume()["status"], "running")
            with self.assertLogs("pumbility.publication", level="ERROR"):
                result = self.fixture.resume()
        self.assertEqual(result["status"], "failed")
        self.assertIn("no progress", result["error"])

    def test_advanced_durable_progress_starts_a_new_finite_retry_budget(self):
        result = self.audit_timeout()
        ledger = {**result["databaseTimeoutRetry"], "attempts": 2}
        runtime.update_job(self.jobs, self.job_id, databaseTimeoutRetry=ledger)
        with self.fixture.hosted_pipeline():
            result = self.fixture.resume()
        self.assertEqual(result["status"], "running")
        advanced = self.blobs.get_json(self.path)
        result = self.audit_timeout()
        self.assertEqual(result["databaseTimeoutRetry"]["attempts"], 1)
        self.assertNotEqual(result["databaseTimeoutRetry"]["token"], ledger["token"])
        self.assertEqual(result["databaseTimeoutRetry"]["token"], runtime._database_cursor_token(advanced))

    def test_committed_work_and_other_audit_observations_are_not_rolled_back(self):
        checkpoint = self.blobs.get_json(self.path)
        checkpoint["resumeAudit"] = {"token": "analysis", "observations": 1}
        self.blobs.put_json(self.path, checkpoint)

        def advanced(*args, on_tier_progress, **kwargs):
            on_tier_progress({"version": 1, "generation": "a" * 64,
                              "ordinal": 1, "stage": "target-fit"})
            raise statement_timeout()

        with patch("analysis_runtime._build_combined_analysis_checkpoint", side_effect=advanced):
            result = self.fixture.resume()
        self.assertEqual(result["status"], "running")
        current = self.blobs.get_json(self.path)
        self.assertEqual(current["tierProgress"]["ordinal"], 1)
        self.assertEqual(current["resumeAudit"], {
            "token": runtime._database_cursor_token(current), "observations": 1})

    def test_cancelled_job_is_not_retried_or_overwritten(self):
        def cancelled(*args, on_tier_progress, **kwargs):
            on_tier_progress(None)
            runtime.update_job(self.jobs, self.job_id, status="cancelled", cancelRequested=True)
            raise statement_timeout()

        with patch("analysis_runtime._build_combined_analysis_checkpoint", side_effect=cancelled):
            result = self.fixture.resume()
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["cancelRequested"])
        self.assertNotIn("databaseTimeoutRetry", result)
        self.assertNotIn(runtime.ANALYSIS_CONTINUATION_FIELD, result)

    def test_a_different_audit_observation_is_not_refunded(self):
        def other_observation(*args, on_tier_progress, **kwargs):
            on_tier_progress(None)
            checkpoint = self.blobs.get_json(self.path)
            checkpoint["resumeAudit"]["observations"] += 1
            self.blobs.put_json(self.path, checkpoint)
            raise statement_timeout()

        with patch("analysis_runtime._build_combined_analysis_checkpoint", side_effect=other_observation):
            result = self.fixture.resume()
        self.assertEqual(result["status"], "running")
        self.assertEqual(self.blobs.get_json(self.path)["resumeAudit"], {
            "token": "analysis", "observations": 2})

    def test_running_cancellation_is_finalized_through_existing_job_fence(self):
        def cancelling(*args, on_tier_progress, **kwargs):
            on_tier_progress(None)
            runtime.update_job(self.jobs, self.job_id, cancelRequested=True)
            raise statement_timeout()

        with patch("analysis_runtime._build_combined_analysis_checkpoint", side_effect=cancelling):
            result = self.fixture.resume()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["cancelRequested"])
        self.assertIn("cancelled", result["error"])
        self.assertIsNone(self.jobs.active_job_id())
        self.assertNotIn("databaseTimeoutRetry", result)
        self.assertNotIn(runtime.ANALYSIS_CONTINUATION_FIELD, result)

    def test_lost_owner_during_retry_reservation_preserves_job_and_active_head(self):
        original = self.jobs.save

        def owner_fence(job):
            if "databaseTimeoutRetry" in job:
                raise JobLeaseLostError("Another worker owns the lease")
            return original(job)

        with patch.object(self.jobs, "save", side_effect=owner_fence):
            result = self.audit_timeout()
        self.assertEqual(result["status"], "running")
        self.assertEqual(self.jobs.active_job_id(), self.job_id)
        self.assertNotIn("databaseTimeoutRetry", result)
        self.assertNotIn(runtime.ANALYSIS_CONTINUATION_FIELD, result)

    def test_retry_storage_failure_is_terminal_without_uncounted_continuation(self):
        original = self.jobs.save

        def unavailable(job):
            if "databaseTimeoutRetry" in job:
                raise RuntimeError("Database unavailable")
            return original(job)

        with patch.object(self.jobs, "save", side_effect=unavailable), \
                self.assertLogs("pumbility.publication", level="ERROR"):
            result = self.audit_timeout()
        self.assertEqual(result["status"], "failed")
        self.assertIn("could not be recovered automatically", result["error"])
        self.assertNotIn(runtime.ANALYSIS_CONTINUATION_FIELD, result)
        self.assertIsNotNone(self.blobs.get_json(self.path))

    def test_explicit_query_cancellation_does_not_enter_timeout_recovery(self):
        with patch("analysis_runtime._build_combined_analysis_checkpoint",
                   side_effect=QueryCanceled("canceling statement due to user request")), \
                self.assertLogs("pumbility.publication", level="ERROR"):
            result = self.fixture.resume()
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("databaseTimeoutRetry", result)

    def test_missing_checkpoint_or_pointer_commit_boundary_is_not_replayed(self):
        for phase in (None, runtime.TYPED_CHECKPOINT_DATABASE_MODEL_PHASE):
            with self.subTest(phase=phase):
                if phase is not None:
                    self.setUp()
                    checkpoint = self.blobs.get_json(self.path)
                    checkpoint["phase"] = phase
                    self.blobs.put_json(self.path, checkpoint)
                else:
                    self.blobs.delete(self.path)
                with patch("analysis_runtime._resume_typed_analysis_checkpoint", side_effect=statement_timeout()), \
                     patch("analysis_runtime.analyze_snapshot", side_effect=statement_timeout()), \
                     self.assertLogs("pumbility.publication", level="ERROR"):
                    result = self.fixture.resume()
                self.assertEqual(result["status"], "failed")
                self.assertNotIn("databaseTimeoutRetry", result)

    def test_worker_dispatches_unique_timeout_continuation_in_background_scope(self):
        from database_read_policy import read_statement_timeout_milliseconds
        from worker.tasks import refresh_analysis

        result = {"status": "running", runtime.ANALYSIS_CONTINUATION_FIELD: "database-retry",
                  runtime.ANALYSIS_CONTINUATION_SEQUENCE_FIELD: "token-2"}

        def run(*args, **kwargs):
            self.assertEqual(read_statement_timeout_milliseconds(10_000), 30_000)
            return dict(result)

        with patch("worker.tasks.execute_analysis_job", side_effect=run), \
                patch.object(refresh_analysis, "apply_async") as enqueue:
            self.assertEqual(refresh_analysis.run(self.job_id), {"status": "running"})
        enqueue.assert_called_once_with(args=[self.job_id],
            task_id=f"{self.job_id}-database-retry-token-2-checkpoint-v{runtime.TYPED_CHECKPOINT_SCHEMA_VERSION}",
            queue="analysis")
        self.assertEqual(read_statement_timeout_milliseconds(10_000), 10_000)


if __name__ == "__main__":
    unittest.main()
