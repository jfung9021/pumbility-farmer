from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from analysis_runtime import MemoryBlobStore, MemoryJobStore
from api.jonathan import router


class RecommendationOperatorRouteTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_password_protected_mode_starts_recommendation_only_job(self):
        with patch.dict("os.environ", {"JONATHAN_PASSWORD": "operator-test"}), \
             patch("api.jonathan.start_or_reuse_analysis", return_value=(202, {
                 "outcome": "started", "job": {"id": "new-model", "recommendationsOnly": True},
             })) as start:
            response = self.client.post("/api/jonathan/refresh?mode=recommendations",
                                        headers={"X-Jonathan-Password": "operator-test"})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["job"]["recommendationsOnly"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(start.call_args.kwargs["recommendations_only"])
        self.assertTrue(start.call_args.kwargs["reanalyze_only"])
        self.assertFalse(start.call_args.kwargs["full_sync"])

    def test_wrong_password_cannot_queue_model_refresh(self):
        with patch.dict("os.environ", {"JONATHAN_PASSWORD": "operator-test"}), \
             patch("api.jonathan.start_or_reuse_analysis") as start:
            response = self.client.post("/api/jonathan/refresh?mode=recommendations",
                                        headers={"X-Jonathan-Password": "incorrect"})
        self.assertEqual(response.status_code, 401)
        start.assert_not_called()

    def test_real_admission_propagates_model_flag_and_disables_official_capture(self):
        jobs = MemoryJobStore()
        with patch.dict("os.environ", {"JONATHAN_PASSWORD": "operator-test", "PIU_OFFICIAL_CROSS_LEVEL_SCORING": "1"}), \
             patch("api._shared.PrivateBlobStore", return_value=MemoryBlobStore()), \
             patch("api._shared.RuntimeJobStore", return_value=jobs), \
             patch("api._shared.enqueue_analysis") as enqueue:
            response = self.client.post("/api/jonathan/refresh?mode=recommendations",
                                        headers={"X-Jonathan-Password": "operator-test"})
        self.assertEqual(response.status_code, 202)
        job = response.json()["job"]
        self.assertTrue(job["recommendationsOnly"])
        self.assertTrue(job["reanalyzeOnly"])
        self.assertFalse(job["refreshOfficialSources"])
        enqueue.assert_called_once_with(job["id"])


if __name__ == "__main__":
    unittest.main()
