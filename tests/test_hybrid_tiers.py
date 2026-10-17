from __future__ import annotations

import copy
import os
import unittest
from unittest.mock import patch

from cross_level_clearing import apply_cross_level_clearing, prepare_clearing_evidence
from hybrid_tiers import retain_lower_submitted_scoring, validate_hybrid_tiers
from official_cross_level_scoring import apply_cross_level_scoring
from pumbility_contract import hybrid_tier_method_identity
from tests.test_cross_level_clearing import fixture as clearing_fixture
from tests.test_official_cross_level_scoring import fixture as scoring_fixture
from tier_postprocessing import finalize_tiers, initial_fit_payload, validate_postprocessing


def fixture():
    base, first, second, charts = clearing_fixture()
    for mode, key in (("Single", "singles"), ("Double", "doubles")):
        for row in base[key]:
            row.update(level=row["level"] + 4, scoringMethod="official-cross-level-v1")
            row["folder"] = f"{'S' if mode == 'Single' else 'D'}{row['level']}"
        for chart in charts:
            if chart["type"] == mode and "-target-" in chart["id"]:
                chart["level"] += 4
        for index in range(6):
            cid = f"{mode}-lower-{index}"
            charts.append({"id": cid, "type": mode, "level": 19, "noteCount": 100})
            base[key].append({"chartId": cid, "type": mode, "level": 19, "songName": cid,
                "folder": f"{'S' if mode == 'Single' else 'D'}19", "scoringMethod": "submitted-score-profile-v2",
                "estimatedDifficulty": 19.7, "difficultyDelta": .2, "evidenceStatus": "Published", "nContributors": 40,
                "difficultyCi95Low": 19.5, "difficultyCi95High": 19.9, "tierMetrics": {}})
            first["scores"].extend({"playerId": f"private:{mode}:{2 if index == 5 else 0}:{p}",
                "chartId": cid, "pumbility": 0, "score": 1, "isBroken": False} for p in range(20))
    base["summary"]["method"].update(hybridTiers={**hybrid_tier_method_identity(), "submittedScoring": {
        "method": {"calibration": "folder-scaled-score-profile"}, "scoreProfileCalibration": {}, "confidenceIntervals": {}}},
        officialCrossLevelScoring={"version": 1, "minimumOfficialLevel": 20, "source": "piuscores-official",
            "mergePolicy": "maximum-raw-score", "minimumOtherCharts": 3, "modelVersion": 1,
            "calibrationVersion": 1, "bootstrapSamples": 1000, "manifestSha256": "a" * 64})
    return base, first, second, charts


class HybridClearingTests(unittest.TestCase):
    def test_upper_anchors_lower_formula_support_and_single_compression(self):
        base, first, second, charts = fixture()
        frozen = copy.deepcopy((base, first, second, charts))
        evidence = prepare_clearing_evidence(first, second, charts)
        result = apply_cross_level_clearing(base, first, second, charts, hybrid_lower_tiers=True)
        self.assertEqual((base, first, second, charts), frozen)
        self.assertEqual(result["schemaVersion"], 46)
        for mode, key in (("Single", "singles"), ("Double", "doubles")):
            anchors = result["summary"]["method"]["crossLevelClearing"]["calibration"][mode]["anchors"]
            self.assertEqual([a["level"] for a in anchors], [20, 21, 22])
            for row in result[key]:
                metric = row["tierMetrics"]["clearing"]
                for field in ("q10Skill", "clearCount", "ratedClearCount", "sourceCounts", "comparability"):
                    self.assertEqual(metric[field], evidence["charts"][row["chartId"]][field])
                if row["level"] < 20:
                    self.assertAlmostEqual(metric["estimatedDifficulty"],
                        19.5 + .70 * (metric["q10Skill"] - metric["folderReferenceSkill"]))
                    self.assertFalse(metric["extrapolated"])
                    self.assertIsNone(metric["supportedRange"])
        compressed = finalize_tiers(result)
        self.assertEqual(compressed["schemaVersion"], 47)
        self.assertEqual(compressed, finalize_tiers(compressed))
        self.assertEqual(validate_postprocessing(compressed)["schemaVersion"], 46)
        validate_hybrid_tiers(compressed)
        self.assertEqual(compressed["coop"], base["coop"])

    def test_below20_pool_does_not_change_upper_curve_with_fixed_evidence(self):
        base, first, second, charts = fixture()
        evidence = prepare_clearing_evidence(first, second, charts)
        with patch("cross_level_clearing.prepare_clearing_evidence", return_value=evidence):
            before = apply_cross_level_clearing(base, first, second, charts, hybrid_lower_tiers=True)
        changed = copy.deepcopy(evidence)
        for cid, chart in changed["catalog"].items():
            if chart["level"] < 20:
                changed["charts"][cid]["q10Skill"] = 999
                changed["charts"][cid]["anchorEligible"] = False
        with patch("cross_level_clearing.prepare_clearing_evidence", return_value=changed):
            after = apply_cross_level_clearing(base, first, second, charts, hybrid_lower_tiers=True)
        self.assertEqual(before["summary"]["method"]["crossLevelClearing"]["calibration"],
                         after["summary"]["method"]["crossLevelClearing"]["calibration"])
        for row in after["singles"]:
            if row["level"] == 19:
                self.assertIsNone(row["tierMetrics"]["clearing"]["estimatedDifficulty"])
                self.assertIn("five comparable", row["tierMetrics"]["clearing"]["unratedReason"])

    def test_missing_20_anchor_and_nonincreasing_upper_medians_fail(self):
        base, first, second, charts = fixture()
        for change in ("missing", "nonincreasing"):
            evidence = prepare_clearing_evidence(first, second, charts)
            for cid, chart in evidence["catalog"].items():
                if chart["level"] == 20:
                    if change == "missing":
                        evidence["charts"][cid]["anchorEligible"] = False
                    else:
                        evidence["charts"][cid]["q10Skill"] = 999
            with patch("cross_level_clearing.prepare_clearing_evidence", return_value=evidence):
                with self.assertRaises(ValueError):
                    apply_cross_level_clearing(base, first, second, charts, hybrid_lower_tiers=True)

    def test_histories_below20_satisfy_record_gate_and_top30_skill(self):
        charts = [{"id": str(i), "type": "Single", "level": 10 if i < 180 else 20, "noteCount": 100}
                  for i in range(200)]
        scores = [{"playerId": "p", "chartId": str(i), "pumbility": 0, "isBroken": False} for i in range(200)]
        source = {"charts": charts, "scores": scores}
        evidence = prepare_clearing_evidence(source, source, charts)
        self.assertEqual(evidence["diagnostics"]["Single"]["qualifiedPlayers"], 1)
        self.assertAlmostEqual(evidence["charts"]["199"]["q10Skill"], (20 * 20 + 10 * 10) / 30)
        without_lower = {"charts": charts, "scores": scores[180:]}
        self.assertEqual(prepare_clearing_evidence(without_lower, without_lower, charts)["charts"]["199"]["ratedClearCount"], 0)


class HybridScoringTests(unittest.TestCase):
    def test_official_overlay_keeps_submitted_below20_and_upper_fit_unchanged(self):
        base, merged, charts = scoring_fixture()
        base["summary"]["method"].update(scoring={"version": 2, "calibration": "folder-scaled-score-profile"},
            scoreProfileCalibration={"sentinel": .123456789}, confidenceIntervals={"method": "submitted"})
        for key in ("singles", "doubles"):
            base[key][-1].update(difficultyDelta=.123, nContributors=20, evidenceStatus="Published",
                scoringProfileMatchDifficulty=19.7654321, difficultyCi95Low=19.1, difficultyCi95High=19.8,
                effectBandRank=4, effectBand="Average")
        missing = {"id": "missing-official20", "type": "Single", "level": 20, "songName": "missing"}
        charts.append(missing)
        base["singles"].append({**copy.deepcopy(base["singles"][0]), **missing,
                                "chartId": missing["id"], "estimatedDifficulty": 99})
        old = apply_cross_level_scoring(base, merged, charts)
        hybrid = apply_cross_level_scoring(base, merged, charts, retain_submitted_below20=True)
        for key in ("singles", "doubles"):
            for before, after in zip(old[key], hybrid[key]):
                if before["level"] >= 20:
                    self.assertEqual({k: v for k, v in before.items() if k not in ("modeRank", "tierMetrics")},
                                     {k: v for k, v in after.items() if k not in ("modeRank", "tierMetrics")})
                else:
                    self.assertEqual(after["estimatedDifficulty"], 99)
                    self.assertEqual(after["scoringProfileMatchDifficulty"], 19.7654321)
                    self.assertEqual(after["difficultyCi95Low"], 19.1)
                    self.assertEqual(after["scoringMethod"], "submitted-score-profile-v2")
        self.assertEqual(old["summary"]["method"]["officialCrossLevelScoring"],
                         hybrid["summary"]["method"]["officialCrossLevelScoring"])
        absent = next(row for row in hybrid["singles"] if row["chartId"] == missing["id"])
        self.assertIsNone(absent["estimatedDifficulty"])
        self.assertEqual(absent["scoringMethod"], "official-cross-level-v1")

    def test_pinned_restore_only_changes_lower_scoring_and_requires_matching_catalog(self):
        base, first, second, charts = fixture()
        base["schemaVersion"] = 43
        submitted = copy.deepcopy(base)
        submitted["schemaVersion"] = 26
        submitted["summary"]["method"].update(scoring={"calibration": "folder-scaled-score-profile"},
                                               scoreProfileCalibration={"kept": True})
        before = copy.deepcopy(base)
        restored = retain_lower_submitted_scoring(base, submitted)
        self.assertEqual(base, before)
        for a, b in zip(restored["singles"], base["singles"]):
            if a["level"] >= 20:
                self.assertEqual(a, b)
        submitted["singles"][-1]["level"] = 18
        with self.assertRaisesRegex(ValueError, "current chart catalog"):
            retain_lower_submitted_scoring(base, submitted)


class HybridRuntimeTests(unittest.TestCase):
    def test_hybrid_checkpoint_is_local_explicit_and_does_not_reinterpret_44(self):
        from analysis_runtime import _validate_checkpoint_combined_tier
        base, first, second, charts = fixture()
        candidate = finalize_tiers(apply_cross_level_clearing(base, first, second, charts, hybrid_lower_tiers=True))
        flags = {"PIU_LOCAL_HYBRID_TIERS": "1", "PIU_LOCAL_ANALYSIS": "1", "PIU_OFFICIAL_CROSS_LEVEL_SCORING": "1",
                 "PIU_CROSS_LEVEL_CLEARING": "1", "PIU_TIER_MIDPOINT_COMPRESSION": "1"}
        with patch.dict(os.environ, flags):
            _validate_checkpoint_combined_tier(candidate)
            altered = copy.deepcopy(candidate)
            altered["summary"]["method"]["crossLevelClearing"]["minimumCalibrationLevel"] = 19
            with self.assertRaises(ValueError):
                _validate_checkpoint_combined_tier(altered)
            with self.assertRaises(ValueError):
                _validate_checkpoint_combined_tier({**candidate, "schemaVersion": 44})
        with patch.dict(os.environ, {**flags, "PIU_LOCAL_ANALYSIS": "0"}):
            with self.assertRaisesRegex(ValueError, "explicit local"):
                _validate_checkpoint_combined_tier(candidate)


if __name__ == "__main__":
    unittest.main()
