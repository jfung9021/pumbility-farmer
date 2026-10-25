import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import song_bpm_metadata
from bpm_metadata_overlay import overlay_bpm_payload, select_bpm_payload
from production_tiers import public_digest
from song_bpm_metadata import bpm_metadata_identity
from scripts.build_bpm_metadata_view import stage


class BpmMetadataOverlayTests(unittest.TestCase):
    def payload(self):
        return {"generatedAtUtc": "2026-10-01T00:00:00Z", "summary": {"method": {}},
                "singles": [{"chartId": "trans", "type": "Single", "songName": "Transmission",
                             "bpmMin": 100, "bpmMax": 225, "estimatedDifficulty": 24.125,
                             "score": 997000, "plate": "UG"}]}

    def test_range_focal_and_numeric_generation_are_independent_and_idempotent(self):
        source = self.payload()
        saved = copy.deepcopy(source)
        result = overlay_bpm_payload(source)
        chart = result["singles"][0]
        self.assertEqual((chart["bpmMin"], chart["bpmMax"], chart["bpmFocal"]), (100, 225, 169))
        self.assertEqual((chart["estimatedDifficulty"], chart["score"], chart["plate"]), (24.125, 997000, "UG"))
        self.assertEqual(source, saved)
        self.assertEqual(result, overlay_bpm_payload(result))
        self.assertEqual(result["bpmMetadata"]["bpmMetadataRevision"], bpm_metadata_identity()["bpmMetadataRevision"])

    def test_one_catalog_snapshot_and_index_are_shared_across_modes_and_details(self):
        source = self.payload()
        source["doubles"] = [{**source["singles"][0], "chartId": "trans-double", "type": "Double"}]
        source["detail"] = {"chart": {**source["singles"][0], "chartId": "trans-detail"}}
        saved = copy.deepcopy(source)
        with patch("song_bpm_metadata._runtime", wraps=song_bpm_metadata._runtime) as runtime, \
                patch("song_bpm_metadata._indexes", wraps=song_bpm_metadata._indexes) as indexes:
            result = overlay_bpm_payload(source)
        runtime.assert_called_once_with()
        indexes.assert_called_once()
        pairs = [(source["singles"][0], result["singles"][0]),
                 (source["doubles"][0], result["doubles"][0]),
                 (source["detail"]["chart"], result["detail"]["chart"])]
        for original, actual in pairs:
            self.assertEqual(actual, song_bpm_metadata.enrich_chart_bpm(original))
        self.assertEqual(source, saved)

    def test_preserves_exact_variants_fixed_corrections_and_fractional_precision(self):
        source = self.payload()
        names = ["Moment Day", "Trotpris - SHORT CUT -", "Appassionata", "Neo Catharsis - SHORT CUT -"]
        source["singles"] = [{"chartId": name, "type": "Single", "songName": name} for name in names]
        rows = overlay_bpm_payload(source)["singles"]
        self.assertEqual([r["bpmFocal"] for r in rows], [158, 156.118, 210, 210])
        self.assertEqual((rows[-1]["songType"], rows[-1]["bpmMin"], rows[-1]["bpmMax"]), ("ShortCut", 120, 300))

    def test_signed_base_retains_audit_and_gets_a_valid_metadata_view_identity(self):
        source = self.payload()
        publication = {"version": 1, "method": "existing-model", "fullArtifactSha256": "b" * 64}
        source["summary"]["method"]["productionTierPublication"] = publication
        publication["publicPayloadSha256"] = public_digest(source)
        old_hash = publication["publicPayloadSha256"]
        result = overlay_bpm_payload(source)
        self.assertEqual(result["bpmMetadata"]["basePublicPayloadSha256"], old_hash)
        self.assertEqual(result["summary"]["method"]["productionTierPublication"]["fullArtifactSha256"], "b" * 64)
        self.assertEqual(result["summary"]["method"]["productionTierPublication"]["publicPayloadSha256"], public_digest(result))
        legacy = select_bpm_payload(result)
        self.assertEqual(legacy["summary"]["method"]["productionTierPublication"]["publicPayloadSha256"], public_digest(legacy))
        self.assertNotIn("bpmFocal", legacy["singles"][0])

    def test_rejects_corrupt_base_or_unknown_selector(self):
        with self.assertRaises(ValueError):
            select_bpm_payload(self.payload(), "max-bpm")
        source = self.payload()
        source["summary"]["method"]["productionTierPublication"] = {"publicPayloadSha256": "bad"}
        with self.assertRaisesRegex(ValueError, "publication identity"):
            overlay_bpm_payload(source)

    def test_unknown_ranges_are_visible_but_not_guessed(self):
        source = self.payload()
        source["singles"][0].update(songName="Future Unknown Song", bpmMin=100, bpmMax=225)
        self.assertIsNone(overlay_bpm_payload(source)["singles"][0]["bpmFocal"])

    def test_archived_baselines_are_preserved(self):
        source = self.payload()
        source["numericalBaseline"] = copy.deepcopy(source["singles"][0])
        result = overlay_bpm_payload(source)
        self.assertEqual(result["numericalBaseline"], source["numericalBaseline"])
        self.assertNotIn("bpmFocal", result["numericalBaseline"])

    def test_staged_view_retains_source_bytes_and_scores(self):
        source = self.payload()
        source["recoveryBaseline"] = copy.deepcopy(source["singles"])
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "base.json"
            candidate = Path(directory) / "candidate.json"
            raw = json.dumps(source).encode()
            original.write_bytes(raw)
            report = stage(original, candidate, public=True)
            result = json.loads(candidate.read_bytes())
            self.assertEqual(original.read_bytes(), raw)
            self.assertEqual(result["singles"][0]["score"], source["singles"][0]["score"])
            self.assertEqual(result["singles"][0]["bpmFocal"], 169)
            self.assertNotIn("recoveryBaseline", result)
            self.assertEqual((report["scoreFetches"], report["modelRefits"]), (0, 0))
            with self.assertRaises(ValueError):
                stage(original, original)

    def test_http_views_select_focal_and_pin_lazy_details_without_changing_legacy(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.tier_list import router
        from analysis_runtime import MemoryBlobStore
        from pumbility_contract import combined_tier_blob_path
        from tier_public import tier_browser_payload

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        blobs = MemoryBlobStore()
        base = self.payload()
        blobs.put_json(combined_tier_blob_path(), base)
        with patch("api.tier_list.PrivateBlobStore", return_value=blobs):
            legacy = client.get("/api/tier-list")
            self.assertEqual(legacy.json(), tier_browser_payload(base))
            focal = client.get("/api/tier-list?bpmPolicy=focal-v1")
            self.assertEqual(focal.status_code, 200)
            row = focal.json()["singles"][0]
            self.assertEqual((row["bpmMin"], row["bpmMax"], row["bpmFocal"]), (100, 225, 169))
            query = {"chartId": "trans", "generatedAtUtc": base["generatedAtUtc"], "bpmPolicy": "focal-v1"}
            self.assertEqual(client.get("/api/tier-list/chart", params=query).status_code, 409)
            query["bpmMetadataRevision"] = focal.json()["bpmMetadata"]["bpmMetadataRevision"]
            details = client.get("/api/tier-list/chart", params=query)
            self.assertEqual(details.status_code, 200)
            self.assertEqual(details.json()["chart"]["bpmFocal"], 169)
            self.assertEqual(client.get("/api/tier-list?bpmPolicy=guess").status_code, 400)
            self.assertEqual(client.get("/api/tier-list/chart", params={**query, "bpmPolicy": "guess"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
