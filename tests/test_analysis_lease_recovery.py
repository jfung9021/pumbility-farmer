"""Regression coverage for a queue retry preceding a dead worker's lease expiry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import MagicMock, Mock, patch

from celery.exceptions import Retry

from analysis_runtime import MemoryBlobStore, MemoryJobStore, execute_analysis_job, new_job
from phoenix2_sync import isoformat_utc
from pumbility_store import JobLeaseHeldError, PumbilityJobStore
from worker.tasks import refresh_analysis


NOW = datetime(2026, 10, 21, 6, 36, 12, tzinfo=timezone.utc)
EXPIRY = datetime(2026, 10, 21, 6, 37, 13, 782000, tzinfo=timezone.utc)
JOB_ID = "analysis-lease-recovery-test"


def live_conflict(blocking_job_id=JOB_ID):
    return JobLeaseHeldError("A database claim was refused.",
        blocking_job_id=blocking_job_id, blocking_status="running",
        lease_expires_at=EXPIRY, retry_after_seconds=61.782)


class ClaimConflictMetadataTests(unittest.TestCase):
    def setUp(self):
        self.jobs = PumbilityJobStore(database_url="postgresql://unused.example/test")
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.claim = {"claimed": False, "job": {"external_key": JOB_ID,
            "status": "running", "lease_expires_at": isoformat_utc(EXPIRY),
            "lease_owner": "private-owner-must-not-escape"}}
        self.cursor.fetchone.side_effect = [("job-uuid", "running", "owner-a"), (self.claim,), (NOW,)]
        self.connect = patch("pumbility_store._connect", return_value=self.connection)
        self.schema = patch("pumbility_store._assert_schema")
        self.connect.start(); self.schema.start()
        self.addCleanup(self.connect.stop); self.addCleanup(self.schema.stop)

    def test_remaining_wait_uses_database_clock_and_contains_no_owner(self):
        with self.assertRaises(JobLeaseHeldError) as raised:
            self.jobs.save({"id": JOB_ID, "status": "running"})
        error = raised.exception
        self.assertEqual(error.blocking_job_id, JOB_ID)
        self.assertEqual(error.blocking_status, "running")
        self.assertEqual(error.lease_expires_at, EXPIRY)
        self.assertAlmostEqual(error.retry_after_seconds, 61.782)
        self.assertNotIn("private-owner", str(vars(error)))
        queries = [c.args[0] for c in self.cursor.execute.call_args_list]
        self.assertIn("select clock_timestamp()", queries)
        self.assertFalse(any(q.lstrip().startswith("update") for q in queries))

    def test_database_failure_is_not_a_verified_conflict(self):
        self.cursor.fetchone.side_effect = [("job-uuid", "running", "owner-a"), OSError("database unavailable")]
        with self.assertRaises(OSError):
            self.jobs.save({"id": JOB_ID, "status": "running"})

    def test_malformed_claim_is_not_a_verified_conflict(self):
        for claim in ({}, {"claimed": False}, {"claimed": "false"}):
            with self.subTest(claim=claim):
                self.cursor.fetchone.side_effect = [("job-uuid", "running", "owner-a"), (claim,)]
                with self.assertRaises(RuntimeError) as raised:
                    self.jobs.save({"id": JOB_ID, "status": "running"})
                self.assertNotIsInstance(raised.exception, JobLeaseHeldError)

    def test_terminal_claim_refusal_keeps_terminal_identity_without_lease(self):
        self.claim["job"].update(status="completed", lease_expires_at=None)
        with self.assertRaises(JobLeaseHeldError) as raised:
            self.jobs.save({"id": JOB_ID, "status": "running"})
        self.assertEqual(raised.exception.blocking_status, "completed")
        self.assertIsNone(raised.exception.retry_after_seconds)


class AnalysisLeaseRecoveryTests(unittest.TestCase):
    def seed(self, status="running"):
        jobs = MemoryJobStore()
        jobs.save({**new_job(JOB_ID, NOW), "status": status, "stage": "analyzing"})
        jobs.set_active_job_id(JOB_ID)
        return jobs

    def test_nonterminal_conflict_propagates_without_checkpoint_or_payload_write(self):
        jobs = self.seed()
        before = jobs.get(JOB_ID)
        blobs = MemoryBlobStore()
        with patch.object(jobs, "save", side_effect=live_conflict()) as save:
            with self.assertRaises(JobLeaseHeldError):
                execute_analysis_job(JOB_ID, blobs=blobs, jobs=jobs, now=lambda: NOW)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(jobs.get(JOB_ID), before)
        self.assertEqual(blobs.values, {})

    def test_completion_racing_claim_is_acknowledged(self):
        jobs = self.seed()

        def complete_then_refuse(_value):
            MemoryJobStore.save(jobs, {**jobs.get(JOB_ID), "status": "completed"})
            raise live_conflict()

        with patch.object(jobs, "save", side_effect=complete_then_refuse):
            result = execute_analysis_job(JOB_ID, blobs=MemoryBlobStore(), jobs=jobs)
        self.assertEqual(result["status"], "completed")

    def test_failed_cancelled_and_completed_queue_redelivery_do_no_work(self):
        for status in ("completed", "failed", "cancelled"):
            with self.subTest(status=status):
                jobs = self.seed(status)
                with patch.object(jobs, "save", side_effect=AssertionError("terminal job must not run")):
                    result = execute_analysis_job(JOB_ID, blobs=MemoryBlobStore(), jobs=jobs,
                                                  acknowledge_terminal=True)
                self.assertEqual(result["status"], status)

    def test_different_job_claim_conflict_uses_existing_active_job_policy(self):
        jobs = self.seed("queued")
        original_save = jobs.save

        def save(value):
            if value["status"] == "running":
                raise live_conflict("other-active-job")
            return original_save(value)

        with patch.object(jobs, "save", side_effect=save):
            result = execute_analysis_job(JOB_ID, blobs=MemoryBlobStore(), jobs=jobs, now=lambda: NOW)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "Another analysis refresh is already active.")
        self.assertEqual(jobs.active_job_id(), "other-active-job")

    def test_worker_signals_retained_receipt_retry_without_publishing(self):
        with (patch("worker.tasks.execute_analysis_job", side_effect=live_conflict()),
              patch("worker.tasks._begin_topology_worker_invocation", return_value=None),
              patch.object(refresh_analysis, "apply_async") as enqueue):
            with self.assertRaises(Retry) as raised:
                refresh_analysis.run(JOB_ID)
        self.assertEqual(raised.exception.when, 64)
        enqueue.assert_not_called()

    def test_unstructured_conflict_and_database_errors_are_not_success(self):
        for error in (JobLeaseHeldError("unverified refusal"), OSError("database unavailable")):
            with self.subTest(error=type(error).__name__):
                with (patch("worker.tasks.execute_analysis_job", side_effect=error),
                      patch("worker.tasks._begin_topology_worker_invocation", return_value=None),
                      patch.object(refresh_analysis, "apply_async") as enqueue):
                    with self.assertRaises(type(error)):
                        refresh_analysis.run(JOB_ID)
                enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
