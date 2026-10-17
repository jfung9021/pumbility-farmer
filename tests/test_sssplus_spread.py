import copy
import json
import math
import unittest
from pathlib import Path
from statistics import median

from sssplus_spread import (SCALE, _apply, apply_sssplus_spread, recover_spread_baseline,
    spread_value, validate_sssplus_spread, _select_boundary_exceptions,
    build_boundary_candidates, boundary_exception_value, _adjusted_base)
from target_tail_policy import tail_value


class SpreadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = json.loads(Path("tests/fixtures/sssplus-stable-scale.json").read_text(encoding="utf-8"))["candidate"]
        cls.result = apply_sssplus_spread(cls.base, rare_adjustments={}, rare_policy={"fixture": True})

    def test_smooth_center_order_and_spacing(self):
        values = [spread_value(value, 20) for value in (0., 12., 18., 20., 20.5, 21., 23., 30.)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(spread_value(20.5, 20), 20.5)
        self.assertAlmostEqual(spread_value(19., 20)+spread_value(22., 20), 41.)
        self.assertGreater(spread_value(14., 20)-spread_value(13., 20), 0.)
        self.assertIsNone(spread_value(None, 20))
        for scale in (0., -1., float("inf")):
            with self.assertRaises(ValueError):
                spread_value(20., 20, scale=scale)

    def test_recovery_replay_and_no_mutation(self):
        frozen = copy.deepcopy(self.base)
        self.assertEqual(recover_spread_baseline(self.result), frozen)
        self.assertEqual(validate_sssplus_spread(self.result, frozen), frozen)
        self.assertEqual(apply_sssplus_spread(self.base, rare_adjustments={}, rare_policy={"fixture": True}), self.result)
        self.assertEqual(self.base, frozen)

    def test_preserves_general_clearing_coop_and_below20_values(self):
        from sssplus_spread import _without_ranks
        self.assertEqual(self.result["coop"], self.base["coop"])
        for key in ("singles", "doubles"):
            for row, old in zip(self.result[key], self.base[key]):
                for field in old:
                    if field not in ("scoringVariants", "pumbilityVariants", "sssplusTailBaseline"):
                        self.assertEqual(row[field], old[field])
                for variants in ("scoringVariants", "pumbilityVariants"):
                    for goal in old[variants]:
                        if goal != "sssplus":
                            self.assertEqual(row[variants][goal], old[variants][goal])
                    if row["level"] < 20:
                        self.assertEqual(_without_ranks(row[variants]["sssplus"]), _without_ranks(old[variants]["sssplus"]))

    def test_once_compressed_points_intervals_and_supported_medians(self):
        policy = self.result["summary"]["method"]["sssplusSpreadPolicy"]
        self.assertEqual(policy["compressionApplications"], 1)
        self.assertEqual(policy["scale"], .55)
        for mode, key in (("Single", "singles"), ("Double", "doubles")):
            for row in self.result[key]:
                if row["level"] < 20:
                    continue
                m, mid = row["scoringVariants"]["sssplus"], row["level"]+.5
                detail = m["scoringSpreadPolicy"]
                self.assertAlmostEqual(detail["preSpreadDifficulty"], mid+.5*(m["initialFit"]["estimatedDifficulty"]-mid))
                for original, output in (("preSpreadDifficulty", "estimatedDifficulty"),
                        ("preSpreadDifficultyCi95Low", "difficultyCi95Low"), ("preSpreadDifficultyCi95High", "difficultyCi95High")):
                    expected = tail_value(spread_value(detail[original], row["level"]), row["level"], exceptional=m["scoringTailPolicy"]["exceptionSelected"])
                    if expected is None:
                        self.assertIsNone(m[output])
                    else:
                        self.assertAlmostEqual(m[output], expected)
                composite = row["pumbilityVariants"]["sssplus"]
                self.assertAlmostEqual(composite["estimatedDifficulty"], (m["estimatedDifficulty"]+row["tierMetrics"]["clearing"]["estimatedDifficulty"])/2)
            for level, folder in policy["medianCalibration"]["residualFolders"][mode].items():
                if folder["basis"] == "folder":
                    values = [r["scoringVariants"]["sssplus"]["estimatedDifficulty"] for r in self.result[key] if r["level"] == int(level)]
                    self.assertAlmostEqual(median(values), int(level)+.5)

    def test_rejects_changed_projection_or_frozen_metadata(self):
        for field in ("scale", "compressionApplications", "basePayloadSha256"):
            bad = copy.deepcopy(self.result)
            bad["summary"]["method"]["sssplusSpreadPolicy"][field] = "bad"
            with self.assertRaises(ValueError):
                validate_sssplus_spread(bad, self.base)
        bad = copy.deepcopy(self.result)
        bad["doubles"][0]["scoringVariants"]["sssplus"]["estimatedDifficulty"] = 10.
        with self.assertRaises(ValueError):
            validate_sssplus_spread(bad, self.base)

    def test_tail_counts_use_actual_post_spread_pre_tail_values(self):
        rows = [row for key in ("singles", "doubles") for row in self.result[key] if row["level"] >= 20]
        before = [row["scoringVariants"]["sssplus"]["scoringTailPolicy"]["preTailDifficulty"]-row["level"]-.5 for row in rows]
        after = [row["scoringVariants"]["sssplus"]["estimatedDifficulty"]-row["level"]-.5 for row in rows]
        counts = self.result["summary"]["method"]["sssplusSpreadPolicy"]["tailPolicy"]["counts"]
        self.assertEqual(counts["preTailMinDelta"], min(before))
        self.assertEqual(counts["preTailMaxDelta"], max(before))
        self.assertEqual(counts["preTailAbsAbove1_5"], sum(abs(value)>1.5 for value in before))
        self.assertEqual(counts["preTailAbsAtLeast2"], sum(abs(value)>=2 for value in before))
        self.assertEqual(counts["changedCharts"], sum(a != b for a,b in zip(before, after)))

    def test_wrong_source_rare_adjustment_is_rejected(self):
        cid = str(next(r for r in self.base["singles"] if r["level"] < 20)["chartId"])
        with self.assertRaises(ValueError):
            _apply(self.base, {cid: {"effect": 1., "metadata": {"posteriorEffect": 1.}}}, {}, {}, {})

    def test_exception_budget_protects_ties_and_outer_blocks(self):
        def row(cid, value):
            return {"chartId": cid, "level": 20, "scoringVariants": {"sssplus": {"estimatedDifficulty": value}}}
        payload = {"singles": [row(str(i), 17.) for i in range(11)]+[row("center", 20.5)], "doubles": []}
        candidates = {str(i): {"eligible": True, "strength": 4.} for i in range(11)}
        selected, _ = _select_boundary_exceptions(payload, candidates, set())
        self.assertEqual(selected, set())
        # An ineligible outer chart blocks an eligible interior one.
        payload["singles"] = [row("outer", 17.), row("inner", 18.), row("mid", 20.5), row("high", 21.)]
        selected, _ = _select_boundary_exceptions(payload, {"inner": {"eligible": True, "strength": 4.}}, set())
        self.assertEqual(selected, set())

    def test_one_sided_gap_gate_stress_and_conservative_ceiling(self):
        projected = copy.deepcopy(self.result)
        row = next(r for r in projected["doubles"] if r["level"] == 20)
        m = row["scoringVariants"]["sssplus"]
        m.update(estimatedDifficulty=15., scoringComparability="single-source-known",
            scoringSourceStates={"Phoenix2": {"status": "available", "rawRowCount": 300,
                "cutoffScore": 999000, "possiblyTruncated": True}})
        m["scoringRefinement"] = {"branch": "all-target",
            "fullBoard": {"records": 300, "possiblyTruncated": True},
            "range": {"supported": True, "kind": "observed", "donorLevels": [20]},
            "boundary": {"gapPoints": 4000, "edgeLatent": -10.}}
        p = projected["summary"]["method"]["sssplusSpreadPolicy"]["medianCalibration"]
        projected["summary"]["method"]["sssplusMedianCalibration"] = p
        base = copy.deepcopy(self.base)
        base["summary"]["method"]["sssplusRefinement"]["gapCalibration"]["official"]["Double"] = {
            "supported": True, "pointsPerLatentUnit": 500., "validationMeanAbsoluteError": 1000., "candidates": []}
        evidence = build_boundary_candidates(projected, base)[str(row["chartId"])]
        self.assertTrue(evidence["eligible"])
        self.assertFalse(evidence["oneSidedEvidence"]["formalInterval"])
        self.assertGreaterEqual(evidence["strength"], 2.)
        self.assertEqual(boundary_exception_value(12., 20), 18.5)
        self.assertEqual(boundary_exception_value(28., 20), 22.5)
        m["scoringRefinement"]["boundary"]["gapPoints"] = 1200.
        rejected = build_boundary_candidates(projected, base)[str(row["chartId"])]
        self.assertFalse(rejected["eligible"])
        self.assertIn("gap-not-above-1.25-times-validation-mae", rejected["rejectionReasons"])

    def test_rare_replacement_uses_posterior_coordinate_and_drops_old_intervals(self):
        from sssplus_rare_continuous import conditional_map, METHOD
        from official_cross_level_calibration import chart_cost
        changed = copy.deepcopy(self.base)
        row = next(r for r in changed["doubles"] if r["level"] == 22)
        for metric in (row["scoringVariants"]["sssplus"], row["sssplusCenteringBaseline"]["scoring"]):
            metric["scoringEstimateBranch"] = "rare-target"
            metric["scoringRefinement"]["branch"] = "rare-target"
        estimate = conditional_map([0., 1., 2., 3.], [0, 0, 0, 1], [1., 1., 1., 1.], 5., 4.)
        estimate.pop("laplaceObjective")
        metadata = {"version": 1, "method": METHOD, **estimate,
            "conditionalOnly": True, "intervals": "unavailable-new-conditional-estimator"}
        cid = str(row["chartId"])
        adjusted = _adjusted_base(changed, {cid: {"effect": estimate["posteriorEffect"], "metadata": metadata}})
        projected = next(r for r in adjusted["doubles"] if str(r["chartId"]) == cid)["sssplusCenteringBaseline"]["scoring"]
        curve = adjusted["summary"]["method"]["sssplusScoring"]["calibration"]["official"]["Double"]
        self.assertAlmostEqual(chart_cost(projected["initialFit"]["estimatedDifficulty"], curve), estimate["posteriorEffect"])
        self.assertIsNone(projected["difficultyCi95Low"])
        self.assertIsNone(projected["initialFit"]["difficultyCi95High"])
        bad = copy.deepcopy(metadata)
        bad["priorVariance"] *= 2
        with self.assertRaises(ValueError):
            _adjusted_base(changed, {cid: {"effect": estimate["posteriorEffect"], "metadata": bad}})


if __name__ == "__main__":
    unittest.main()
