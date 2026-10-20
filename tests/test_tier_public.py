from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from analysis_runtime import MemoryBlobStore
from api_service import app
from canonical_json import canonical_json_sha256
import production_tiers as tiers
from pumbility_contract import combined_tier_blob_path
from tier_public import DETAIL_PREFIX, compact_tier_payload, detail_shard_path, prepare_tier_publication, tier_chart_details, tier_detail_generated_at, validate_tier_publication


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
    def test_five_goal_budget_preserves_legacy_limit_and_bounds_compressed_output(self):
        legacy = source()
        expanded = copy.deepcopy(legacy)
        expanded["schemaVersion"] = 58
        for row in expanded["doubles"]:
            for field in ("scoringVariants", "pumbilityVariants"):
                for goal in ("ss", "ssplus"):
                    row[field][goal] = copy.deepcopy(row[field]["sssplus"])
        old = compact_tier_payload(legacy)
        current = compact_tier_payload(expanded)
        size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        self.assertGreater(size(current), size(old))
        with patch("tier_public.MAX_LIST_BYTES", size(old)), \
             patch("tier_public.MAX_EXPANDED_LIST_BYTES", size(current)):
            validate_tier_publication(legacy, old)
            validate_tier_publication(expanded, current)
            for payload in (legacy, expanded):
                oversized = copy.deepcopy(payload)
                oversized["doubles"][0]["songName"] += "over budget"
                with self.assertRaisesRegex(ValueError, "decoded publication size budget"):
                    validate_tier_publication(oversized, compact_tier_payload(oversized))
            with patch("tier_public.MAX_COMPRESSED_LIST_BYTES", 1):
                with self.assertRaisesRegex(ValueError, "compressed publication size budget"):
                    validate_tier_publication(expanded, current)

    def test_bounded_sss_schema59_uses_expanded_budgets_and_keeps_only_replay_inputs(self):
        full = source()
        full["schemaVersion"] = 59
        full["summary"]["method"]["sssTailPolicy"] = {"version": 1, "method": "sss-target-bounded-tails-v1",
            "baseSchemaVersion": 57, "basePayloadSha256": "a"*64,
            "tailPolicy": {"selectedChartIds": [], "counts": {"ratedCharts": 1}}}
        full["doubles"][0]["scoringVariants"]["sss"]["scoringTailPolicy"] = {
            "preTailDifficulty": 18.1, "preTailDifficultyCi95Low": 17.9,
            "preTailDifficultyCi95High": 18.5, "rejectionReasons": ["detail-only"],
            "finalFolderRange": {"lower": 17, "upper": 19}}
        compact = compact_tier_payload(full)
        self.assertEqual(compact["summary"]["method"]["sssTailPolicy"], full["summary"]["method"]["sssTailPolicy"])
        evidence = compact["doubles"][0]["scoringVariants"]["sss"]["scoringTailPolicy"]
        self.assertEqual(set(evidence), {"preTailDifficulty", "preTailDifficultyCi95Low", "preTailDifficultyCi95High"})
        size = len(json.dumps(compact, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        with patch("tier_public.MAX_LIST_BYTES", 1), patch("tier_public.MAX_EXPANDED_LIST_BYTES", size):
            validate_tier_publication(full, compact)
        with patch("tier_public.MAX_EXPANDED_LIST_BYTES", size-1):
            with self.assertRaisesRegex(ValueError, "decoded publication size budget"):
                validate_tier_publication(full, compact)
        with patch("tier_public.MAX_COMPRESSED_LIST_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "compressed publication size budget"):
                validate_tier_publication(full, compact)

    def test_schema60_preserves_sparse_width_replay_and_expanded_budgets(self):
        full = source()
        full["schemaVersion"] = 60
        full["summary"]["method"]["level20ScoringWidth"] = {
            "version": 1, "folders": {"Double:sss:20": {"basis": "folder", "knots": []}}}
        metric = full["doubles"][0]["scoringVariants"]["sss"]
        width = {"version": 1, "method": "level20-target-quantile-width-v1", "group": "Double:sss:20",
            "source": "stored-pre-tail", "original": {"estimatedDifficulty": 18.12345678912345},
            "input": {"estimatedDifficulty": 17.12345678912345},
            "final": {"estimatedDifficulty": 18.12345678912345}}
        metric["scoringLevel20Width"] = {**width, "unusedAudit": ["detail-only"]}
        compact = compact_tier_payload(full)
        self.assertEqual(compact["summary"]["method"]["level20ScoringWidth"],
                         full["summary"]["method"]["level20ScoringWidth"])
        self.assertEqual(compact["doubles"][0]["scoringVariants"]["sss"]["scoringLevel20Width"], width)
        size = len(json.dumps(compact, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        with patch("tier_public.MAX_LIST_BYTES", 1), patch("tier_public.MAX_EXPANDED_LIST_BYTES", 1), \
             patch("tier_public.MAX_WIDTH_LIST_BYTES", size):
            validate_tier_publication(full, compact)
        with patch("tier_public.MAX_WIDTH_LIST_BYTES", size-1):
            with self.assertRaisesRegex(ValueError, "decoded publication size budget"):
                validate_tier_publication(full, compact)
        with patch("tier_public.MAX_COMPRESSED_LIST_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "compressed publication size budget"):
                validate_tier_publication(full, compact)

    def test_compact_boundary_count_retains_used_evidence_without_full_boards(self):
        full = source()
        row = full["doubles"][0]
        row["scoringMethod"] = "target-boundary-score-gap-v1"
        row["scoringRefinement"] = {"fullBoard": {"records": 10, "privateScores": [999999]}}
        row["nContributors"] = 0
        row["scoringVariants"]["sss"].update(scoringMethod="sss-boundary-score-gap-v1")
        row["scoringVariants"]["sssplus"].update(scoringMethod="target-boundary-score-gap-v1")
        row["scoringVariants"]["sss"]["scoringRefinement"]["fullBoard"]["records"] = 9
        row["scoringVariants"]["sssplus"]["scoringRefinement"]["fullBoard"]["records"] = 10
        before = copy.deepcopy(full)
        projected = compact_tier_payload(full)["doubles"][0]
        self.assertEqual(projected["scoringBoundaryRecords"], 10)
        self.assertEqual(projected["scoringVariants"]["sss"]["scoringBoundaryRecords"], 9)
        self.assertEqual(projected["scoringVariants"]["sssplus"]["scoringBoundaryRecords"], 10)
        self.assertNotIn("scoringRefinement", json.dumps(projected))
        self.assertNotIn("privateScores", json.dumps(projected))
        self.assertEqual(full, before)
        row["scoringRefinement"]["fullBoard"]["records"] = 10.0
        self.assertEqual(compact_tier_payload(full)["doubles"][0]["scoringBoundaryRecords"], 10)
        for invalid in (None, True, -1, "10", 10.5):
            row["scoringRefinement"]["fullBoard"]["records"] = invalid
            self.assertNotIn("scoringBoundaryRecords", compact_tier_payload(full)["doubles"][0])

    def test_reused_details_use_the_computation_time_and_keep_current_client_generation(self):
        full = source()
        compact, shards = prepare_tier_publication(full, "computed-generation")
        compact["tierListView"].pop("detailsGeneratedAtUtc", None)  # Historical PR127 payload.
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

    def test_fresh_five_goal_details_reuse_numeric_fit_but_have_current_publication_time(self):
        full = source()
        numeric_at, published_at = full["generatedAtUtc"], "2026-10-20T05:00:03.095205+00:00"
        full["schemaVersion"] = 59
        full["generatedAtUtc"] = published_at
        for name in ("scoringVariants", "pumbilityVariants"):
            for goal in ("ss", "ssplus"):
                full["doubles"][0][name][goal] = copy.deepcopy(full["doubles"][0][name]["sssplus"])
        inputs = {"generationTimestamp": published_at, "synthetic": "independent-goal-overlays"}
        full["summary"]["method"]["productionTierPublication"] = {
            "inputs": inputs, "computedAtUtc": numeric_at, "reusedComputation": True}
        compact, shards = prepare_tier_publication(full, canonical_json_sha256(inputs))
        self.assertEqual(compact["tierListView"]["detailsGeneratedAtUtc"], published_at)
        # The production publication already exists without the new provenance.
        compact["tierListView"].pop("detailsGeneratedAtUtc", None)
        values = {combined_tier_blob_path(): compact, **shards}
        class Store:
            def get_json(self, path): return values.get(path)
        with patch("api.tier_list.PrivateBlobStore", return_value=Store()):
            client = TestClient(app)
            response = client.get("/api/tier-list/chart", params={"chartId": "d18", "generatedAtUtc": published_at})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["generatedAtUtc"], published_at)
            self.assertEqual(set(response.json()["chart"]["scoringVariants"]), {"general", "ss", "ssplus", "sss", "sssplus"})
            self.assertEqual(tier_detail_generated_at(compact), published_at)
            actual_path = detail_shard_path(compact["tierListView"]["detailsPrefix"], "d18")
            for wrong in (numeric_at, "2026-10-21T00:00:00Z"):
                values[actual_path]["generatedAtUtc"] = wrong
                self.assertEqual(client.get("/api/tier-list/chart", params={
                    "chartId": "d18", "generatedAtUtc": published_at}).status_code, 503)
            values[actual_path]["generatedAtUtc"] = published_at
            # A different valid prefix cannot claim the fresh publication date.
            compact["tierListView"]["detailsPrefix"] = DETAIL_PREFIX + "0"*64 + "/"
            values[detail_shard_path(compact["tierListView"]["detailsPrefix"], "d18")] = values[actual_path]
            self.assertEqual(client.get("/api/tier-list/chart", params={
                "chartId": "d18", "generatedAtUtc": published_at}).status_code, 503)

    def test_later_warm_publications_pin_the_intermediate_detail_time(self):
        full = source()
        numeric_at, details_at = full["generatedAtUtc"], "2026-10-20T05:00:03.095205+00:00"
        full["generatedAtUtc"] = details_at
        inputs = {"generationTimestamp": details_at, "synthetic": "independent-goal-overlays"}
        full["summary"]["method"]["productionTierPublication"] = {
            "inputs": inputs, "computedAtUtc": numeric_at, "computedInputs": {"generationTimestamp": numeric_at},
            "computedGeneration": "b"*64, "reusedComputation": True,
            "fullArtifactPath": "analysis/private/synthetic-detail-audit.json.gz", "fullArtifactSha256": "c"*64}
        computed, shards = prepare_tier_publication(full, canonical_json_sha256(inputs))
        computed["tierListView"].pop("detailsGeneratedAtUtc")  # Already-published five-goal format.
        publication = computed["summary"]["method"]["productionTierPublication"]
        publication["publicPayloadSha256"] = tiers.public_digest(computed)
        frozen_prefix = computed["tierListView"]["detailsPrefix"]
        reference = {"blobSha256": "a"*64}
        store = MemoryBlobStore()
        for current_at in ("2026-10-20T06:00:00Z", "2026-10-20T07:00:00Z"):
            previous = copy.deepcopy(computed)
            original = {"generation": canonical_json_sha256(previous["summary"]["method"]["productionTierPublication"]["inputs"]),
                "createdAtUtc": previous["generatedAtUtc"],
                "identity": previous["summary"]["method"]["productionTierPublication"]["inputs"],
                "stages": [reference], "tailOverlay": reference,
                "ready": {"payloadSha256": canonical_json_sha256(previous)}}
            current_inputs = {"generationTimestamp": current_at, "synthetic": "independent-goal-overlays"}
            state = {"generation": canonical_json_sha256(current_inputs), "identity": current_inputs,
                "createdAtUtc": current_at, "computationGeneration": original["generation"],
                "computationCreatedAtUtc": original["createdAtUtc"], "computationIdentity": {"synthetic": "unchanged"}}
            with patch.object(tiers, "_advance_finish", return_value=previous), \
                 patch.object(tiers, "_captured_computation", return_value=original), \
                 patch.object(tiers, "validate_public_tiers"):
                computed = tiers._advance_reused_computation(store, state, verified_computation=original)
            self.assertEqual(computed["tierListView"]["detailsPrefix"], frozen_prefix)
            self.assertEqual(computed["tierListView"]["detailsGeneratedAtUtc"], details_at)
            self.assertEqual(computed["generatedAtUtc"], current_at)
            marker = computed["summary"]["method"]["productionTierPublication"]
            self.assertEqual(marker["computedAtUtc"], numeric_at)
            self.assertEqual(marker["publicPayloadSha256"], tiers.public_digest(computed))
            if current_at.endswith("06:00:00Z"):
                self.assertNotIn("detailsGeneratedAtUtc", previous["tierListView"])
            values = {combined_tier_blob_path(): computed, **shards}
            class Store:
                def get_json(self, path): return values.get(path)
            with patch("api.tier_list.PrivateBlobStore", return_value=Store()):
                client = TestClient(app)
                response = client.get("/api/tier-list/chart", params={"chartId": "d18", "generatedAtUtc": current_at})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["generatedAtUtc"], current_at)
                self.assertEqual(client.get("/api/tier-list/chart", params={
                    "chartId": "d18", "generatedAtUtc": details_at}).status_code, 409)
                path = detail_shard_path(frozen_prefix, "d18")
                for wrong in (numeric_at, current_at):
                    values[path]["generatedAtUtc"] = wrong
                    self.assertEqual(client.get("/api/tier-list/chart", params={
                        "chartId": "d18", "generatedAtUtc": current_at}).status_code, 503)
                values[path]["generatedAtUtc"] = details_at
                computed["tierListView"]["detailsGeneratedAtUtc"] = None
                self.assertEqual(client.get("/api/tier-list/chart", params={
                    "chartId": "d18", "generatedAtUtc": current_at}).status_code, 503)
                computed["tierListView"]["detailsGeneratedAtUtc"] = details_at

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
