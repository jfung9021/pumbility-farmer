import copy
import os
import tempfile
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

from tier_postprocessing import compress_difficulty, finalize_tiers, initial_fit_payload, validate_postprocessing
from tests import test_cross_level_runtime as runtime_fixtures
from scripts import build_local_recommendations as builder
from tests import test_official_cross_level_scoring as scoring_fixtures
from tests import test_cross_level_clearing as clearing_fixtures
from analysis_runtime import _official_tier_arguments, _validate_checkpoint_combined_tier


def fixture():
    def row(cid, level, scoring, clearing):
        return {"chartId": cid, "type": "Single", "level": level, "folder": f"S{level}", "songName": cid,
                "estimatedDifficulty": scoring, "difficultyDelta": None if scoring is None else scoring-level-.5,
                "difficultyCi95Low": None if scoring is None else scoring-.2,
                "difficultyCi95High": None if scoring is None else scoring+.2,
                "difficultyDeltaCi95Low": None if scoring is None else scoring-.2-level-.5,
                "difficultyDeltaCi95High": None if scoring is None else scoring+.2-level-.5,
                "evidenceStatus": "Unrated" if scoring is None else "Published", "nContributors": 40,
                "scoringChartCost": 10000, "scoringExtrapolated": True,
                "tierMetrics": {"clearing": {"estimatedDifficulty": clearing, "difficultyDelta": None if clearing is None else clearing-level-.5,
                                             "evidenceStatus": "Unrated" if clearing is None else "Provisional", "ratedClearCount": 25,
                                             "q10Skill": 23.7, "extrapolated": True, "difficultyCi95Low": None, "difficultyCi95High": None},
                                "pumbility": {"estimatedDifficulty": None}}}
    return {"schemaVersion": 43, "singles": [row("hard",25,26.6,24.4),row("easy",25,24.4,25.5),row("missing",25,None,25.4)],
            "doubles": [], "coop": [{"chartId":"coop","estimatedDifficulty":19}], "summary":{"method":{},"modes":{}}}


class MidpointTests(unittest.TestCase):
    def test_reference_symmetry_precision_and_null(self):
        self.assertAlmostEqual(compress_difficulty(26.6,25),26.05)
        self.assertAlmostEqual(compress_difficulty(24.4,25),24.95)
        self.assertEqual(compress_difficulty(25.5,25),25.5)
        self.assertIsNone(compress_difficulty(None,25))
        self.assertNotEqual(compress_difficulty(26.649,25),compress_difficulty(26.6,25))

    def test_intervals_maximum_evidence_and_originals(self):
        original=fixture(); frozen=copy.deepcopy(original); p=finalize_tiers(original)
        self.assertEqual(original,frozen)
        hard=p["singles"][0]; c=hard["tierMetrics"]["clearing"]; composite=hard["tierMetrics"]["pumbility"]
        self.assertAlmostEqual(hard["estimatedDifficulty"],26.05)
        self.assertEqual(hard["initialFit"]["estimatedDifficulty"],26.6)
        self.assertAlmostEqual(hard["difficultyCi95High"]-hard["difficultyCi95Low"],.2)
        self.assertEqual(composite["estimatedDifficulty"],max(hard["estimatedDifficulty"],c["estimatedDifficulty"]))
        self.assertEqual(composite["evidenceStatus"],"Provisional")
        self.assertIsNone(c["difficultyCi95Low"])
        self.assertTrue(hard["scoringExtrapolated"]); self.assertTrue(c["extrapolated"])
        self.assertEqual(p["coop"],original["coop"])
        self.assertIsNone(p["singles"][2]["tierMetrics"]["pumbility"]["estimatedDifficulty"])

    def test_repeated_refresh_is_identical_and_restores_original_intervals(self):
        p=finalize_tiers(fixture())
        self.assertEqual(p,finalize_tiers(p))
        raw=validate_postprocessing(p)
        self.assertEqual(raw["schemaVersion"],43)
        self.assertAlmostEqual(raw["singles"][0]["difficultyCi95Low"],26.4)
        self.assertEqual(raw["singles"][0]["scoringChartCost"],10000)

    def test_partial_new_fit_replaces_old_fit_without_reshrinking_other_metric(self):
        p=finalize_tiers(fixture()); previous=copy.deepcopy(p["singles"][0]["tierMetrics"]["clearing"])
        p["singles"][0].update(estimatedDifficulty=28.2,difficultyCi95Low=28.0,difficultyCi95High=28.4)
        result=finalize_tiers(p,newly_fitted_metrics={"scoring"})
        self.assertAlmostEqual(result["singles"][0]["estimatedDifficulty"],26.85)
        self.assertEqual(result["singles"][0]["initialFit"]["estimatedDifficulty"],28.2)
        self.assertEqual(result["singles"][0]["tierMetrics"]["clearing"],previous)
        p["singles"][0].update(estimatedDifficulty=None,difficultyCi95Low=None,difficultyCi95High=None,evidenceStatus="Unrated")
        result=finalize_tiers(p,newly_fitted_metrics={"scoring"})
        self.assertIsNone(result["singles"][0]["estimatedDifficulty"])
        self.assertIsNone(result["singles"][0]["tierMetrics"]["pumbility"]["estimatedDifficulty"])

    def test_invalid_factor_missing_fit_and_tampered_final_are_rejected(self):
        for mutate in (lambda p:p["summary"]["method"]["ratingPostprocessing"].update(factor=.25),
                       lambda p:p["singles"][0].pop("initialFit"),
                       lambda p:p["singles"][0].update(estimatedDifficulty=27)):
            p=finalize_tiers(fixture());mutate(p)
            with self.assertRaises(ValueError): validate_postprocessing(p)

    def test_floating_point_ties_preserve_initial_clearing_order(self):
        p=fixture();p["singles"]=p["singles"][:2]
        for row,name,value in zip(p["singles"],("Z-low","A-high"),(16.417525773195873,16.417525773195877)):
            row.update(level=16,songName=name)
            row["tierMetrics"]["clearing"]["estimatedDifficulty"]=value
        result=finalize_tiers(p)
        clear=[r["tierMetrics"]["clearing"] for r in result["singles"]]
        self.assertEqual(clear[0]["estimatedDifficulty"],clear[1]["estimatedDifficulty"])
        self.assertEqual([c["levelRank"] for c in clear],[1,2])

    def test_runtime_opt_in_and_checkpoint_policy(self):
        env={"PIU_TIER_MIDPOINT_COMPRESSION":"1","PIU_OFFICIAL_HIGH_LEVEL_TIERS":"0","PIU_OFFICIAL_CROSS_LEVEL_SCORING":"0","PIU_CROSS_LEVEL_CLEARING":"0"}
        with patch.dict(os.environ,env):
            self.assertEqual(_official_tier_arguments(runtime_fixtures.BlobFixture()),{"midpoint_compression":True})
        with tempfile.TemporaryDirectory() as directory:
            blobs,manifest=runtime_fixtures.CrossLevelRuntimeTests().staged(directory)
            raw=runtime_fixtures.checkpoint(manifest);p=finalize_tiers(raw)
            with patch.dict(os.environ,{**env,"PIU_OFFICIAL_CROSS_LEVEL_SCORING":"1"}):
                _validate_checkpoint_combined_tier(p,blobs)
                with self.assertRaisesRegex(ValueError,"requires midpoint"):_validate_checkpoint_combined_tier(raw,blobs)
                p["summary"]["method"]["ratingPostprocessing"]["factor"] = .25
                with self.assertRaisesRegex(ValueError,"policy is incompatible"):_validate_checkpoint_combined_tier(p,blobs)

    def test_local_conversion_and_refresh_preserve_models_and_personal_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"tiers.json";path.write_text(json.dumps(fixture()),encoding="utf-8")
            protected=Path(directory)/"recommendations.json";protected.write_bytes(b"unchanged")
            with (patch.object(builder,"COMBINED_OUTPUT_PATH",path),
                  patch.object(builder,"_read_snapshot",side_effect=AssertionError("No input refit")),
                  patch.object(builder,"build_recommendation_index",side_effect=AssertionError("No recommendation rebuild")),
                  patch.object(sys,"argv",["builder","--compress-tiers"])):
                builder.main();first=json.loads(path.read_text(encoding="utf-8"))
                with patch.object(sys,"argv",["builder","--refresh-tier-metadata"]):builder.main()
                second=json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(first["singles"],second["singles"])
            self.assertEqual(first["summary"]["tierArtifactProvenance"]["base"],second["summary"]["tierArtifactProvenance"]["base"])
            self.assertEqual(protected.read_bytes(),b"unchanged")
            self.assertEqual(first["schemaVersion"],44)

    def test_partial_overlay_rebuilds_consume_initial_fit_coordinates(self):
        base,merged,charts=scoring_fixtures.fixture()
        raw=scoring_fixtures.apply_cross_level_scoring(base,merged,charts)
        rebuilt=scoring_fixtures.apply_cross_level_scoring(finalize_tiers(raw),merged,charts)
        for mode in ("singles","doubles"):
            self.assertEqual(raw[mode],rebuilt[mode])
        base,p1,p2,charts=clearing_fixtures.fixture()
        raw=clearing_fixtures.apply_cross_level_clearing(base,p1,p2,charts)
        rebuilt=clearing_fixtures.apply_cross_level_clearing(finalize_tiers(raw),p1,p2,charts)
        for mode in ("singles","doubles"):
            self.assertEqual(raw[mode],rebuilt[mode])


if __name__=="__main__":unittest.main()
