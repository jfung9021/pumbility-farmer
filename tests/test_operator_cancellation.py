from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from analysis_runtime import MemoryJobStore, new_job
from api.analyze import router as analysis_router
from api.jonathan import router as operator_router


class OperatorCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(analysis_router)
        app.include_router(operator_router)
        self.client = TestClient(app)
        self.jobs = MemoryJobStore()
        job = new_job("analysis-test", datetime(2026, 11, 28, tzinfo=timezone.utc))
        self.jobs.save({**job, "status": "running"})
        self.jobs.set_active_job_id(job["id"])

    def test_operator_password_cancels_without_cron_secret(self) -> None:
        with patch.dict("os.environ", {"JONATHAN_PASSWORD": "operator-test", "CRON_SECRET": ""}), \
             patch("api.analyze.RuntimeJobStore", return_value=self.jobs):
            response = self.client.post("/api/jonathan/cancel?jobId=analysis-test",
                                        headers={"X-Jonathan-Password": "operator-test"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["outcome"], "cancelled")
        cancelled = response.json()["job"]
        self.assertEqual(cancelled["status"], "failed")
        self.assertTrue(cancelled["cancelRequested"])
        self.assertIsNone(cancelled["retryAllowedAtUtc"])
        self.assertIsNone(self.jobs.active_job_id())
        self.assertNotIn("operator-test", response.text)

    def test_wrong_operator_password_is_rejected_before_storage(self) -> None:
        with patch.dict("os.environ", {"JONATHAN_PASSWORD": "operator-test"}), \
             patch("api.analyze.RuntimeJobStore") as store:
            response = self.client.post("/api/jonathan/cancel?jobId=analysis-test",
                                        headers={"X-Jonathan-Password": "incorrect"})
        self.assertEqual(response.status_code, 401)
        store.assert_not_called()
        self.assertFalse(self.jobs.get("analysis-test").get("cancelRequested", False))

    def test_completed_job_is_preserved(self) -> None:
        self.jobs.save({**self.jobs.get("analysis-test"), "status": "completed"})
        self.jobs.set_active_job_id("different-active-job")
        with patch.dict("os.environ", {"JONATHAN_PASSWORD": "operator-test"}), \
             patch("api.analyze.RuntimeJobStore", return_value=self.jobs):
            response = self.client.post("/api/jonathan/cancel?jobId=analysis-test",
                                        headers={"X-Jonathan-Password": "operator-test"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["outcome"], "completed")
        self.assertEqual(self.jobs.active_job_id(), "different-active-job")

    def test_existing_cron_endpoint_keeps_its_authentication(self) -> None:
        with patch.dict("os.environ", {"CRON_SECRET": "cron-test", "JONATHAN_PASSWORD": "operator-test"}), \
             patch("api.analyze.RuntimeJobStore", return_value=self.jobs):
            denied = self.client.post("/api/analyze/cancel?jobId=analysis-test",
                                      headers={"X-Jonathan-Password": "operator-test"})
            accepted = self.client.post("/api/analyze/cancel?jobId=analysis-test",
                                        headers={"Authorization": "Bearer cron-test"})
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["outcome"], "cancelled")

    def test_missing_and_unknown_job_ids_have_explicit_errors(self) -> None:
        with patch.dict("os.environ", {"JONATHAN_PASSWORD": "operator-test"}), \
             patch("api.analyze.RuntimeJobStore", return_value=self.jobs):
            missing = self.client.post("/api/jonathan/cancel",
                                       headers={"X-Jonathan-Password": "operator-test"})
            unknown = self.client.post("/api/jonathan/cancel?jobId=unknown",
                                       headers={"X-Jonathan-Password": "operator-test"})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(unknown.status_code, 404)


if __name__ == "__main__":
    unittest.main()
