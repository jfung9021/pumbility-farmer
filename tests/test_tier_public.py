from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api_service import app
from pumbility_contract import combined_tier_blob_path
from tier_public import compact_tier_payload, detail_shard_path, prepare_tier_publication, tier_chart_details, validate_tier_publication


def source():
    metric = {"estimatedDifficulty": 18.12345678912345, "difficultyDelta": -.37654321087655,
              "difficultyCi95Low": 17.987654321, "difficultyCi95High": 18.543210987,
              "levelRank": 7, "levelComparisonCharts": 91, "modeRank": 123,
              "effectBandRank": 2, "evidenceStatus": "Provisional"}
    clear = {**metric, "estimatedDifficulty": 18.7, "method": "submitted-folder-centered-clearing-v1", "ratedClearCount": 42}
    row = {**metric, "mode": "Doubles", "type": "Double", "level": 18, "chartId": "d18", "songName": "Example",
           "imageUrl": None, "stepArtist": "Artist", "noteCount": 1234, "bpmMin": 150, "bpmMax": 159,
           "scoringVariants": {goal: {**metric, "scoringTarget": 995000, "scoringSpreadPolicy": {"midpoint": 18.5, "scale": .55},
                                           "scoringRefinement": {"fullBoard": {"records": 42}}, "proofBaseline": {"secret": "old"}}
                               for goal in ("general", "sss", "sssplus")},
           "pumbilityVariants": {goal: {**metric, "estimatedDifficulty": (metric["estimatedDifficulty"] + 18.7) / 2, "aggregation": "average"}
                                  for goal in ("general", "sss", "sssplus")},
           "tierMetrics": {"clearing": clear, "pumbility": {**metric, "aggregation": "average"}},
           "chartSourceExclusions": {"rules": [{"reason": "Changed chart"}]}, "proofBaseline": {"old": metric}}
    return {"schemaVersion": 57, "generatedAtUtc": "2026-10-18T00:00:00Z", "mix": {"key": "combined"},
            "summary": {"scriptVersion": "test", "method": {"sssScoring": {"version": 1, "baseSchemaVersion": 48, "hugeAudit": [1]*100},
                       "sourceAwareTiers": {"sourcePolicy": {"effectivePolicySha256": "policy"}},
                       "submittedSssplusCalibration": {"version": 1, "localOnly": False, "hugeAudit": [1]*100}}, "modes": {}, "coverage": {}},
            "singles": [], "doubles": [row], "coop": [], "relativeGroups": [], "effectBands": []}


class TierPublicTests(unittest.TestCase):
    def test_reused_details_use_the_computation_time_and_keep_current_client_generation(self):
        full = source()
        compact, shards = prepare_tier_publication(full, "computed-generation")
        published_at = "2026-10-20T00:00:00Z"
        compact["generatedAtUtc"] = published_at
        publication = {"reusedComputation": True, "computedAtUtc": full["generatedAtUtc"]}
        compact["summary"]["method"]["productionTierPublication"] = publication
        values = {combined_tier_blob_path(): compact, **shards}

        class Store:
            def get_json(self, path):
                return values.get(path)

        with patch("api.tier_list.PrivateBlobStore", return_value=Store()):
            client = TestClient(app)
            response = client.get("/api/tier-list/chart", params={"chartId": "d18", "generatedAtUtc": published_at})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["generatedAtUtc"], published_at)
            self.assertIn("scoringRefinement", response.json()["chart"]["scoringVariants"]["sssplus"])
            self.assertEqual(client.get("/api/tier-list/chart", params={
                "chartId": "d18", "generatedAtUtc": full["generatedAtUtc"]}).status_code, 409)
            for changed in ({"reusedComputation": False}, {"computedAtUtc": "wrong"},
                            {"computedAtUtc": None}):
                compact["summary"]["method"]["productionTierPublication"] = {**publication, **changed}
                self.assertEqual(client.get("/api/tier-list/chart", params={
                    "chartId": "d18", "generatedAtUtc": published_at}).status_code, 503)

    def test_projection_preserves_numeric_identity_and_does_not_mutate_full_source(self):
        full = source(); before = copy.deepcopy(full)
        compact = compact_tier_payload(full)
        row, original = compact["doubles"][0], full["doubles"][0]
        for goal in ("sss", "sssplus"):
            for field in ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High", "levelRank", "modeRank"):
                self.assertEqual(row["scoringVariants"][goal][field], original["scoringVariants"][goal][field])
        self.assertEqual(row["estimatedDifficulty"], original["estimatedDifficulty"])
        self.assertEqual(row["pumbilityVariants"]["general"]["estimatedDifficulty"], original["pumbilityVariants"]["general"]["estimatedDifficulty"])
        self.assertNotIn("scoringRefinement", row["scoringVariants"]["sssplus"])
        self.assertEqual(compact["summary"]["method"]["submittedSssplusCalibration"], {"version": 1, "localOnly": False})
        self.assertEqual(full, before)
        validate_tier_publication(full, compact)

    def test_stable_shards_retain_complete_explanations_without_rollback_copies(self):
        full = source(); compact, shards = prepare_tier_publication(full, "generation")
        self.assertEqual(len(shards), 32)
        prefix = compact["tierListView"]["detailsPrefix"]
        chart = shards[detail_shard_path(prefix, "d18")]["charts"]["d18"]
        self.assertEqual(chart, tier_chart_details(full, "d18")["chart"])
        self.assertEqual(chart["chartSourceExclusions"], full["doubles"][0]["chartSourceExclusions"])
        self.assertNotIn("Baseline", json.dumps(chart))
        self.assertIn("scoringRefinement", chart["scoringVariants"]["sssplus"])
        with self.assertRaisesRegex(ValueError, "prefix"):
            detail_shard_path("analysis/private/other/", "d18")
        changed = copy.deepcopy(compact); changed["doubles"][0]["scoringVariants"]["sssplus"]["estimatedDifficulty"] += .1
        with self.assertRaisesRegex(ValueError, "differs"):
            validate_tier_publication(full, changed)

    def test_hosted_full_fallback_and_sharded_routes_are_generation_bound(self):
        full = source(); compact, shards = prepare_tier_publication(full, "generation")
        for stored in (full, compact):
            values = {combined_tier_blob_path(): stored, **shards}
            class Store:
                def get_json(self, path): return values.get(path)
            with patch("api.tier_list.PrivateBlobStore", return_value=Store()):
                client = TestClient(app)
                listing = client.get("/api/tier-list")
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(listing.json()["tierListView"]["kind"], "list")
                response = client.get("/api/tier-list/chart", params={"chartId": "d18", "generatedAtUtc": full["generatedAtUtc"]})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertIn("scoringRefinement", response.json()["chart"]["scoringVariants"]["sssplus"])
                self.assertEqual(client.get("/api/tier-list/chart", params={"chartId": "d18", "generatedAtUtc": "old"}).status_code, 409)
                self.assertEqual(client.get("/api/tier-list/chart").status_code, 400)
                self.assertEqual(client.get("/api/tier-list/chart", params={"chartId": "missing", "generatedAtUtc": full["generatedAtUtc"]}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
