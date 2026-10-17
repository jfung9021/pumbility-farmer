from __future__ import annotations

import copy
import os
import tempfile
import unittest
from unittest.mock import patch

from analysis_runtime import _official_tier_arguments, _validate_checkpoint_combined_tier
from cross_level_clearing import apply_cross_level_clearing
from hybrid_tiers import validate_hybrid_tiers
from pumbility_average import apply_average_pumbility, average_difficulties, recover_maximum_pumbility
from scoring_percentile import build_production_tier_payload
from tests import test_cross_level_runtime as runtime_fixtures
from tests.test_hybrid_tiers import fixture
from tier_postprocessing import finalize_tiers, initial_fit_payload, validate_postprocessing


def maximum_fixture():
    base, first, second, charts = fixture()
    return finalize_tiers(apply_cross_level_clearing(base, first, second, charts, hybrid_lower_tiers=True))


class AveragePumbilityTests(unittest.TestCase):
    def test_mean_null_precision_and_invalid_values(self):
        self.assertEqual(average_difficulties(20.9, 23), 21.95)
        self.assertEqual(average_difficulties(23.013, 24.217), (23.013 + 24.217) / 2)
        self.assertIsNone(average_difficulties(None, 23))
        self.assertIsNone(average_difficulties(23, None))
        for invalid in (float("nan"), float("inf"), True, "23"):
            with self.assertRaises(ValueError):
                average_difficulties(invalid, 23)

    def test_average_preserves_every_component_and_maximum_and_is_repeatable(self):
        base = maximum_fixture()
        frozen = copy.deepcopy(base)
        candidate = apply_average_pumbility(base)
        self.assertEqual(base, frozen)
        self.assertEqual(candidate["schemaVersion"], 48)
        self.assertEqual(recover_maximum_pumbility(candidate), base)
        self.assertEqual(apply_average_pumbility(candidate), candidate)
        self.assertEqual(finalize_tiers(candidate), candidate)
        self.assertEqual(initial_fit_payload(candidate), initial_fit_payload(base))
        self.assertEqual(validate_postprocessing(candidate)["schemaVersion"], 46)
        validate_hybrid_tiers(candidate)
        self.assertEqual(candidate["coop"], base["coop"])
        for mode in ("singles", "doubles"):
            for row, original in zip(candidate[mode], base[mode]):
                self.assertEqual({key: value for key, value in row.items() if key != "tierMetrics"},
                                 {key: value for key, value in original.items() if key != "tierMetrics"})
                self.assertEqual(row["tierMetrics"]["clearing"], original["tierMetrics"]["clearing"])
                p, before = row["tierMetrics"]["pumbility"], original["tierMetrics"]["pumbility"]
                self.assertEqual(p["maximumBaseline"], before)
                for field in ("evidenceStatus", "scoringSupportCount", "clearingSupportCount"):
                    self.assertEqual(p[field], before[field])
                self.assertEqual(p["estimatedDifficulty"],
                                 (row["estimatedDifficulty"] + row["tierMetrics"]["clearing"]["estimatedDifficulty"]) / 2)

    def test_missing_component_stays_unrated_and_rejects_corrupted_composites(self):
        base, first, second, charts = fixture()
        base["singles"][0]["estimatedDifficulty"] = None
        maximum = finalize_tiers(apply_cross_level_clearing(base, first, second, charts, hybrid_lower_tiers=True))
        candidate = apply_average_pumbility(maximum)
        self.assertIsNone(candidate["singles"][0]["tierMetrics"]["pumbility"]["estimatedDifficulty"])
        self.assertEqual(recover_maximum_pumbility(candidate), maximum)
        for field, value in (("estimatedDifficulty", 123), ("levelRank", 999), ("scoringSupportCount", 999),
                             ("evidenceStatus", "Unrated")):
            altered = copy.deepcopy(candidate)
            altered["singles"][1]["tierMetrics"]["pumbility"][field] = value
            with self.assertRaises(ValueError):
                recover_maximum_pumbility(altered)
        altered = copy.deepcopy(candidate)
        altered["summary"]["method"]["pumbilityExperiment"]["version"] = 1
        with self.assertRaisesRegex(ValueError, "policy"):
            recover_maximum_pumbility(altered)

    def test_equal_mean_ties_use_unicode_code_points_then_chart_id(self):
        base = maximum_fixture()
        template = base["singles"][0]
        names = ["a", "Z", "é", "\ue000", "\U00010000", "😀"]
        rows = []
        for index, name in enumerate(names):
            row = copy.deepcopy(template)
            row.update(songName=name, chartId=f"tie-{index}", estimatedDifficulty=20.5)
            row["initialFit"]["estimatedDifficulty"] = 20.5
            row["tierMetrics"]["clearing"].update(estimatedDifficulty=20.5)
            row["tierMetrics"]["clearing"]["initialFit"]["estimatedDifficulty"] = 20.5
            rows.append(row)
        base["singles"] = rows
        candidate = apply_average_pumbility(base)
        actual = sorted(candidate["singles"], key=lambda row: row["tierMetrics"]["pumbility"]["levelRank"])
        self.assertEqual([row["songName"] for row in actual], sorted(names))


class AverageProductionPolicyTests(unittest.TestCase):
    flags = {"PIU_HYBRID_TIERS": "1", "PIU_PUMBILITY_AVERAGE": "1",
             "PIU_OFFICIAL_CROSS_LEVEL_SCORING": "1", "PIU_CROSS_LEVEL_CLEARING": "1",
             "PIU_TIER_MIDPOINT_COMPRESSION": "1", "PIU_OFFICIAL_HIGH_LEVEL_TIERS": "0",
             "PIU_LOCAL_ANALYSIS": "0", "PIU_LOCAL_HYBRID_TIERS": "0"}

    def test_production_opt_in_hydrates_and_writes_only_the_declared_generation(self):
        candidate = apply_average_pumbility(maximum_fixture())
        with tempfile.TemporaryDirectory() as directory:
            blobs, _ = runtime_fixtures.CrossLevelRuntimeTests().staged(directory)
            with patch.dict(os.environ, self.flags):
                args = _official_tier_arguments(blobs)
                self.assertTrue(args["hybrid_lower_tiers"])
                self.assertTrue(args["average_pumbility"])
                self.assertTrue(args["midpoint_compression"])
                _validate_checkpoint_combined_tier(candidate)
                with self.assertRaisesRegex(ValueError, "incompatible schema"):
                    _validate_checkpoint_combined_tier(maximum_fixture())
            with patch.dict(os.environ, {**self.flags, "PIU_HYBRID_TIERS": "0"}):
                with self.assertRaisesRegex(RuntimeError, "enabled hybrid"):
                    _official_tier_arguments(blobs)
                with self.assertRaisesRegex(ValueError, "source policy"):
                    _validate_checkpoint_combined_tier(candidate)

    def test_future_full_writer_composes_hybrid_compression_then_average(self):
        raw, first, second, charts = fixture()
        for mode in ("singles", "doubles"):
            for row in raw[mode]:
                row["noteCount"] = 100
        profile = copy.deepcopy(raw)
        profile["schemaVersion"] = 25
        profile["summary"].update(scriptVersion="6.19.0-hybrid-average")
        with patch("scoring_percentile.build_percentile_tier_payload", return_value=profile), \
             patch("official_score_merge.merge_official_scores", return_value={"currentCatalog": charts}), \
             patch("official_cross_level_scoring.apply_cross_level_scoring", return_value=raw) as scoring:
            result = build_production_tier_payload([], {}, first, second, official_scoring_manifest={},
                cross_level_clearing=True, midpoint_compression=True, hybrid_lower_tiers=True, average_pumbility=True)
            self.assertEqual(result["schemaVersion"], 48)
            self.assertEqual(scoring.call_args.kwargs, {"retain_submitted_below20": True})
            validate_hybrid_tiers(result)
        with patch("scoring_percentile.build_percentile_tier_payload", return_value=profile):
            with self.assertRaisesRegex(ValueError, "hybrid tiers and midpoint"):
                build_production_tier_payload([], {}, first, second, average_pumbility=True)


if __name__ == "__main__":
    unittest.main()
