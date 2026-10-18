import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.jonathan import router


class TierPublicationApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_storage_preflight_requires_operator_and_returns_only_safe_metadata(self):
        with patch.dict(os.environ, {"JONATHAN_PASSWORD": "operator"}), \
             patch("analysis_runtime.PrivateBlobStore") as store:
            store.return_value.tier_storage_status.return_value = {"httpStatus": 200, "exists": True, "fileSizeLimit": 50_000_000}
            self.assertEqual(self.client.get("/api/jonathan/tier-storage").status_code, 401)
            store.assert_not_called()
            response = self.client.get("/api/jonathan/tier-storage", headers={"X-Jonathan-Password": "operator"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["fileSizeLimit"], 50_000_000)

    def test_authenticated_trigger_returns_job_without_running_analysis(self):
        request = Mock(return_value=(202, {"id": "tier-job", "status": "queued"}))
        status = Mock(return_value={"id": "tier-job", "status": "running", "stage": "spread"})
        seam = SimpleNamespace(request_tier_publication=request, get_tier_publication_status=status)
        with patch.dict(os.environ, {"JONATHAN_PASSWORD": "operator"}), \
             patch.dict(sys.modules, {"tier_publication_jobs": seam}):
            self.assertEqual(self.client.post("/api/jonathan/refresh?mode=tiers").status_code, 401)
            self.assertEqual(self.client.get("/api/jonathan/tier-publication").status_code, 401)
            request.assert_not_called()
            status.assert_not_called()
            headers = {"X-Jonathan-Password": "operator"}
            response = self.client.post("/api/jonathan/refresh?mode=tiers", headers=headers)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["id"], "tier-job")
            response = self.client.get("/api/jonathan/tier-publication?jobId=tier-job", headers=headers)
            self.assertEqual(response.json()["stage"], "spread")
            status.assert_called_once_with("tier-job")
            status.return_value = None
            self.assertEqual(self.client.get("/api/jonathan/tier-publication?jobId=missing", headers=headers).status_code, 404)
