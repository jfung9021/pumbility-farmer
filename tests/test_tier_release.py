from __future__ import annotations

import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from analysis_runtime import MemoryBlobStore, current_snapshot_path
from api.jonathan import router
from cross_level_clearing import clearing_input_identity, current_clearing_catalog
from phoenix2_sync import sanitize_snapshot
from pumbility_contract import combined_tier_blob_path, phoenix1_snapshot_path, official_scoring_manifest_path, recommendation_blob_path
from tier_release import rebuild_submitted_profile, rebuild_tiers_only, TierReleaseBusyError


FLAGS = {name: "1" for name in ("PIU_HYBRID_TIERS", "PIU_PUMBILITY_AVERAGE", "PIU_OFFICIAL_CROSS_LEVEL_SCORING",
                                "PIU_CROSS_LEVEL_CLEARING", "PIU_TIER_MIDPOINT_COMPRESSION")}


def fixture():
    store = MemoryBlobStore()
    p1 = sanitize_snapshot({"charts": [], "players": [], "scores": []}, mix="phoenix1")
    p2 = sanitize_snapshot({"charts": [], "players": [], "scores": []}, mix="phoenix2")
    dated = {**p2, "generatedAtUtc": "2026-10-17T00:00:00Z"}
    base = {"schemaVersion": 44, "generatedAtUtc": dated["generatedAtUtc"],
        "summary": {"method": {"officialCrossLevelScoring": {"manifestSha256": "official"},
            "crossLevelClearing": {"sourceDates": {"phoenix1": None, "phoenix2": dated["generatedAtUtc"]}}}},
        "singles": [], "doubles": [], "coop": []}
    base["summary"]["method"]["crossLevelClearing"]["inputHashes"] = clearing_input_identity(p1, dated, current_clearing_catalog(base, dated))
    store.put_json_bundle({combined_tier_blob_path(): base, phoenix1_snapshot_path(): p1,
        current_snapshot_path("phoenix2"): p2, official_scoring_manifest_path(): {"source": "same"},
        recommendation_blob_path(): {"generationKey": "unchanged", "data": [1, 2, 3]}})
    jobs = SimpleNamespace(active_job_id=lambda: None, get=lambda key: None)
    return store, jobs, base


class TierReleaseTests(unittest.TestCase):
    def test_tier_only_publication_preserves_every_other_pointer_and_replays_timestamp(self):
        store, jobs, base = fixture()
        previous = copy.deepcopy(store.values)
        candidate = {**copy.deepcopy(base), "schemaVersion": 48}
        with patch.dict(os.environ, FLAGS), patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"), \
             patch("tier_release.rebuild_submitted_profile", return_value={"schemaVersion": 26}) as profile, \
             patch("hybrid_tiers.build_release_hybrid_tiers", return_value=candidate) as build:
            result = rebuild_tiers_only(store, jobs)
        self.assertEqual(result["outcome"], "tiers-rebuilt")
        self.assertEqual(result["scoreFetches"], 0)
        self.assertEqual(result["recommendationFits"], 0)
        self.assertEqual(build.call_args.args[3]["generatedAtUtc"], base["generatedAtUtc"])
        profile.assert_called_once()
        for key, value in previous.items():
            if key != combined_tier_blob_path():
                self.assertEqual(store.get_json(key), value)
        added = set(store.values) - set(previous)
        self.assertEqual(len(added), 2)
        self.assertIn("analysis/private/tier-releases/latest.json", added)

    def test_active_refresh_or_changed_cached_inputs_prevents_any_fit_or_publication(self):
        store, jobs, base = fixture()
        previous = copy.deepcopy(store.values)
        busy = SimpleNamespace(active_job_id=lambda: "active", get=lambda key: {"status": "running"})
        with patch.dict(os.environ, FLAGS), patch("tier_release.rebuild_submitted_profile") as fit:
            with self.assertRaises(TierReleaseBusyError):
                rebuild_tiers_only(store, busy)
            fit.assert_not_called()
        base["summary"]["method"]["crossLevelClearing"]["inputHashes"]["phoenix2"] = "changed"
        store.put_json(combined_tier_blob_path(), base)
        with patch.dict(os.environ, FLAGS), patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"), \
             patch("tier_release.rebuild_submitted_profile") as fit:
            with self.assertRaisesRegex(ValueError, "cached scores changed"):
                rebuild_tiers_only(store, jobs)
            fit.assert_not_called()
        self.assertEqual(set(store.values), set(previous))

    def test_explicit_source_date_does_not_replace_an_already_matching_undated_input(self):
        store, jobs, base = fixture()
        first = store.get_json(phoenix1_snapshot_path())
        second = store.get_json(current_snapshot_path("phoenix2"))
        base["summary"]["method"]["crossLevelClearing"]["inputHashes"] = clearing_input_identity(
            first, second, current_clearing_catalog(base, second))
        store.put_json(combined_tier_blob_path(), base)
        with patch.dict(os.environ, FLAGS), patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"), \
             patch("tier_release.rebuild_submitted_profile", return_value={"schemaVersion": 26}), \
             patch("hybrid_tiers.build_release_hybrid_tiers", return_value={**copy.deepcopy(base), "schemaVersion": 48}) as build:
            rebuild_tiers_only(store, jobs)
        self.assertEqual(build.call_args.args[3]["generatedAtUtc"], "")

    def test_profile_inputs_follow_submitted_catalog_and_skip_recommendation_fits(self):
        base = {"coop": [{"chartId": "coop", "type": "CoOp"}], "summary": {"method": {}, "modes": {}}}
        p2 = {"charts": [{"id": "x", "type": "Single", "level": 19, "songName": "x"},
                         {"id": "low", "type": "Single", "level": 10}]}
        with patch("scoring_percentile.build_production_tier_payload", return_value={"schemaVersion": 26}) as fit, \
             patch("piu_recommendations.build_combined_chart_results") as recommendations:
            rebuild_submitted_profile(base, {}, p2)
        records = fit.call_args.args[0]
        self.assertEqual([row["chartId"] for row in records], ["x", "coop"])
        self.assertEqual(records[0]["level"], 19)
        self.assertEqual(records[0]["mode"], "Singles")
        recommendations.assert_not_called()

    def test_route_is_password_protected_and_does_not_enqueue_full_reanalysis(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        with patch.dict(os.environ, {"JONATHAN_PASSWORD": "operator"}), \
             patch("analysis_runtime.PrivateBlobStore"), patch("analysis_runtime.RuntimeJobStore"), \
             patch("tier_publication_jobs.request_tier_publication", return_value=(202, {"id": "tier-job", "status": "queued"})) as rebuild, \
             patch("api.jonathan.start_or_reuse_analysis") as enqueue:
            self.assertEqual(client.post("/api/jonathan/refresh?mode=tiers").status_code, 401)
            rebuild.assert_not_called()
            response = client.post("/api/jonathan/refresh?mode=tiers", headers={"X-Jonathan-Password": "operator"})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["id"], "tier-job")
            rebuild.side_effect = TierReleaseBusyError("active")
            self.assertEqual(client.post("/api/jonathan/refresh?mode=tiers", headers={"X-Jonathan-Password": "operator"}).status_code, 409)
            enqueue.assert_not_called()
