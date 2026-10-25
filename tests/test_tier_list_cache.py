"""Public list caching must never apply to recovery, errors, or player scores."""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.tier_list import router
from analysis_runtime import MemoryBlobStore
from pumbility_contract import combined_tier_blob_path


class TierListCacheTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.store = MemoryBlobStore()
        self.store.put_json(combined_tier_blob_path(), {
            "generatedAtUtc": "2026-10-25T00:00:00Z",
            "summary": {"method": {}}, "singles": [], "doubles": [], "coop": [],
        })

    def test_public_success_can_be_reused_by_browser_and_cdn(self):
        with patch("api.tier_list.PrivateBlobStore", return_value=self.store):
            response = self.client.get("/api/tier-list?bpmPolicy=focal-v1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "public, max-age=30")
        self.assertEqual(response.headers["vercel-cdn-cache-control"],
                         "public, max-age=60, stale-while-revalidate=60")

    def test_explicit_recovery_reads_latest_data_without_caching(self):
        with patch("api.tier_list.PrivateBlobStore", return_value=self.store):
            first = self.client.get("/api/tier-list?bpmPolicy=focal-v1")
            self.store.put_json(combined_tier_blob_path(), {
                "generatedAtUtc": "2026-10-25T01:00:00Z", "summary": {"method": {}},
                "singles": [], "doubles": [], "coop": [],
            })
            latest = self.client.get("/api/tier-list?bpmPolicy=focal-v1&fresh=1")
        self.assertNotEqual(first.json()["generatedAtUtc"], latest.json()["generatedAtUtc"])
        self.assertEqual(latest.headers["cache-control"], "no-store")
        self.assertNotIn("vercel-cdn-cache-control", latest.headers)

    def test_failures_and_player_score_responses_are_not_cached(self):
        with patch("api.tier_list.PrivateBlobStore", return_value=MemoryBlobStore()):
            missing = self.client.get("/api/tier-list")
        with patch("api.tier_list.PrivateBlobStore", side_effect=RuntimeError("unavailable")):
            unavailable = self.client.get("/api/tier-list")
        invalid = self.client.get("/api/tier-list?bpmPolicy=invalid")
        player = self.client.get("/api/tier-list/scores?mode=singles")
        for response in (missing, unavailable, invalid, player):
            self.assertGreaterEqual(response.status_code, 400)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("vercel-cdn-cache-control", response.headers)
