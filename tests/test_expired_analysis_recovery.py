"""Refresh coordination after a database-fenced stale-job recovery attempt."""

from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from analysis_runtime import (
    MemoryBlobStore, MemoryJobStore, isoformat_utc, new_job, request_refresh,
)
from api_service import app


NOW = datetime(2026, 10, 20, 4, 45, tzinfo=timezone.utc)


class ExpiredAnalysisRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.blobs = MemoryBlobStore()
        self.jobs = MemoryJobStore()
        self.old = {**new_job("orphaned-analysis", NOW - timedelta(hours=1)),
                    "status": "running"}
        self.jobs.save(self.old)
        self.jobs.set_active_job_id(self.old["id"])
        self.jobs.set_latest_job_id(self.old["id"])
        self.enqueued = []

    def request(self):
        return request_refresh(self.blobs, self.jobs, self.enqueued.append,
                               now=NOW, force_refresh=True, reanalyze_only=True)

    def release_orphan(self, job_id, *, stale_after_seconds):
        self.assertEqual((job_id, stale_after_seconds), (self.old["id"], 300))
        self.jobs.save({**self.old, "status": "failed",
                        "updatedAtUtc": isoformat_utc(NOW),
                        "retryAllowedAtUtc": isoformat_utc(NOW),
                        "error": "The analysis worker stopped reporting progress."})
        self.jobs.set_active_job_id(None)
        return True

    def test_expired_job_is_released_and_stored_input_refresh_is_enqueued_once(self):
        recover = Mock(side_effect=self.release_orphan)
        self.jobs.fail_stale_active_job = recover

        status, result = self.request()
        status_again, duplicate = self.request()

        self.assertEqual((status, result["outcome"]), (202, "started"))
        self.assertTrue(result["job"]["reanalyzeOnly"])
        self.assertFalse(result["job"]["fullSync"])
        self.assertEqual(self.jobs.get(self.old["id"])["status"], "failed")
        self.assertEqual(self.enqueued, [result["job"]["id"]])
        self.assertEqual((status_again, duplicate["outcome"]), (202, "existing"))
        self.assertEqual(duplicate["job"]["id"], result["job"]["id"])
        recover.assert_called_once_with(self.old["id"], stale_after_seconds=300)

    def test_live_database_lease_is_not_revoked_by_stale_payload(self):
        self.jobs.fail_stale_active_job = Mock(return_value=False)
        with patch.object(self.jobs, "save", side_effect=AssertionError("live job mutated")):
            status, result = self.request()
        self.assertEqual((status, result["outcome"]), (202, "existing"))
        self.assertEqual(self.jobs.get(self.old["id"]), self.old)
        self.assertEqual(self.jobs.active_job_id(), self.old["id"])
        self.assertEqual(self.enqueued, [])

    def test_head_replaced_during_recovery_is_reloaded_and_preserved(self):
        replacement = new_job("replacement-worker", NOW)
        self.jobs.save(replacement)

        def moved_head(*args, **kwargs):
            self.jobs.set_active_job_id(replacement["id"])
            return False

        self.jobs.fail_stale_active_job = Mock(side_effect=moved_head)
        status, result = self.request()
        self.assertEqual((status, result["outcome"]), (202, "existing"))
        self.assertEqual(result["job"]["id"], replacement["id"])
        self.assertEqual(self.jobs.active_job_id(), replacement["id"])
        self.assertEqual(self.jobs.get(self.old["id"]), self.old)
        self.assertEqual(self.enqueued, [])

    def test_renewed_heartbeat_is_reloaded_without_mutating_or_requeueing(self):
        renewed = {**self.old, "updatedAtUtc": isoformat_utc(NOW)}

        def heartbeat(*args, **kwargs):
            self.jobs.save(renewed)
            return False

        self.jobs.fail_stale_active_job = Mock(side_effect=heartbeat)
        _, result = self.request()
        self.assertEqual(result["job"], renewed)
        self.assertEqual(self.jobs.active_job_id(), self.old["id"])
        self.assertEqual(self.enqueued, [])

    def test_completed_job_is_preserved_when_completion_wins_the_race(self):
        completed = {**self.old, "status": "completed", "updatedAtUtc": isoformat_utc(NOW)}

        def completion(*args, **kwargs):
            self.jobs.save(completed)
            self.jobs.set_active_job_id(None)
            return False

        self.jobs.fail_stale_active_job = Mock(side_effect=completion)
        _, result = self.request()
        self.assertEqual(self.jobs.get(self.old["id"]), completed)
        self.assertEqual(result["outcome"], "started")
        self.assertEqual(self.enqueued, [result["job"]["id"]])

    def test_recent_job_does_not_attempt_recovery(self):
        self.jobs.save({**self.old, "updatedAtUtc": isoformat_utc(NOW)})
        recover = self.jobs.fail_stale_active_job = Mock()
        _, result = self.request()
        self.assertEqual(result["outcome"], "existing")
        recover.assert_not_called()
        self.assertEqual(self.enqueued, [])

    def test_recovery_database_error_stops_before_queueing_or_head_clear(self):
        self.jobs.fail_stale_active_job = Mock(side_effect=RuntimeError("database unavailable"))
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            self.request()
        self.assertEqual(self.jobs.get(self.old["id"]), self.old)
        self.assertEqual(self.jobs.active_job_id(), self.old["id"])
        self.assertEqual(self.enqueued, [])

    def test_existing_operator_password_recovers_without_cron_secret(self):
        recover = self.jobs.fail_stale_active_job = Mock(side_effect=self.release_orphan)
        client = TestClient(app)
        with (
            patch.dict(os.environ, {"JONATHAN_PASSWORD": "operator-test", "CRON_SECRET": ""}),
            patch("api._shared.PrivateBlobStore", return_value=self.blobs),
            patch("api._shared.RuntimeJobStore", return_value=self.jobs),
            patch("api._shared.enqueue_analysis", side_effect=self.enqueued.append),
        ):
            unauthorized = client.post("/api/jonathan/refresh?mode=reanalyze")
            self.assertEqual(unauthorized.status_code, 401)
            recover.assert_not_called()
            self.assertEqual(self.enqueued, [])
            with patch("analysis_runtime.utc_now", return_value=NOW):
                response = client.post("/api/jonathan/refresh?mode=reanalyze",
                                       headers={"X-Jonathan-Password": "operator-test"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["outcome"], "started")
        self.assertTrue(response.json()["job"]["reanalyzeOnly"])
        self.assertFalse(response.json()["job"]["fullSync"])
        self.assertEqual(len(self.enqueued), 1)
        recover.assert_called_once()


if __name__ == "__main__":
    unittest.main()
