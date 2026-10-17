import copy
import json
import math
import unittest
from pathlib import Path
from statistics import median

from official_cross_level_calibration import chart_cost, inverse_chart_cost
from sssplus_final_curve import fit_final_curve, inverse_final_cost
from sssplus_median_calibration import (apply_median_calibration, final_calibrations, refined_population,
                                        recover_median_baseline, validate_median_calibration)


class MedianCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = json.loads(Path("tests/fixtures/sssplus-target-tiers.json").read_text(encoding="utf-8"))["candidate"]
        cls.result = apply_median_calibration(cls.base)

    def test_recovery_and_replay_and_no_mutation(self):
        frozen = copy.deepcopy(self.base)
        self.assertEqual(recover_median_baseline(self.result), frozen)
        self.assertEqual(validate_median_calibration(self.result, frozen), frozen)
        self.assertEqual(apply_median_calibration(self.base), self.result)
        self.assertEqual(self.base, frozen)

    def test_equal_chart_median_and_source_mode_isolation(self):
        _, latents, support = refined_population(self.base)
        recovered, populations, curves, errors = final_calibrations(self.base)
        self.assertEqual(latents, recovered)
        for mode in ("Single", "Double"):
            self.assertIsNone(errors[mode])
            for anchor in curves[mode]["anchors"]:
                level = str(anchor["level"])
                self.assertEqual(anchor["cost"], median(latents[cid] for cid in support[mode][level]["populationChartIds"]))
                self.assertEqual(anchor["chartIds"], populations[mode][level]["populationChartIds"])
                self.assertAlmostEqual(inverse_chart_cost(anchor["cost"], curves[mode]), anchor["level"] + .5)
        changed = copy.deepcopy(self.base)
        for row in changed["singles"]:
            if row["level"] < 20:
                row["sssplusCenteringBaseline"]["scoring"]["initialFit"]["estimatedDifficulty"] = 999
        self.assertEqual(final_calibrations(changed), final_calibrations(self.base))

    def test_latent_recovery_uses_corrected_point_not_rare_conditional(self):
        changed = copy.deepcopy(self.base)
        row = next(r for r in changed["doubles"] if r["level"] == 22)
        metric = row["sssplusCenteringBaseline"]["scoring"]
        metric["scoringRefinement"]["branch"] = "rare-target"
        metric["scoringRefinement"]["rare"] = {"conditionalEffect": -999}
        _, costs, _ = refined_population(changed)
        curve = changed["summary"]["method"]["sssplusScoring"]["calibration"]["official"]["Double"]
        self.assertAlmostEqual(costs[row["chartId"]], chart_cost(metric["initialFit"]["estimatedDifficulty"], curve))

    def test_final_anchor_does_not_require_first_pass_width_but_legacy_does(self):
        changed = copy.deepcopy(self.base)
        for row in changed["doubles"]:
            if row["level"] >= 20:
                metric = row["sssplusCenteringBaseline"]["scoring"]
                metric["scoringFirstPass"]["estimatedDifficulty"] = 22
        _, pop, curves, errors = final_calibrations(changed, policy_version=1)
        self.assertIsNone(curves["Double"])
        self.assertTrue(errors["Double"])
        self.assertTrue(all(not p["supported"] for p in pop["Double"].values()))
        self.assertIsNotNone(curves["Single"])
        self.assertIsNotNone(final_calibrations(changed)[2]["Double"])

    def test_nonincreasing_anchors_are_unavailable(self):
        changed = copy.deepcopy(self.base)
        for row in changed["doubles"]:
            if row["level"] >= 20:
                row["sssplusCenteringBaseline"]["scoring"]["initialFit"]["estimatedDifficulty"] = 100-row["level"]
        self.assertIsNone(final_calibrations(changed)[2]["Double"])

    def test_boundary_only_folder_qualifies_at_five_finite_refined_charts(self):
        changed = copy.deepcopy(self.base)
        for row in changed["doubles"]:
            if row["level"] == 22:
                metric = row["sssplusCenteringBaseline"]["scoring"]
                metric["scoringFirstPass"].update(estimatedDifficulty=None, evidenceStatus="Unrated", scoringTargetSuccesses=0)
                metric["scoringRefinement"]["branch"] = "no-target"
        _, population, curves, _ = final_calibrations(changed)
        self.assertTrue(population["Double"]["22"]["supported"])
        self.assertEqual(population["Double"]["22"]["mixedFirstPassChartIds"], [])
        self.assertEqual(population["Double"]["22"]["branches"], {"no-target": 5})
        self.assertIn(22, [a["level"] for a in curves["Double"]["anchors"]])
        # Unrated charts cannot meet the minimum, even with a stale numeric point.
        next(r for r in changed["doubles"] if r["level"] == 22)["sssplusCenteringBaseline"]["scoring"]["evidenceStatus"] = "Unrated"
        self.assertFalse(final_calibrations(changed)[1]["Double"]["22"]["supported"])

    def test_legacy_policy_replays_frozen_fixture_and_rejects_unknown_versions(self):
        frozen = json.loads(Path("tests/fixtures/sssplus-median-tiers.json").read_text(encoding="utf-8"))["candidate"]
        self.assertEqual(apply_median_calibration(self.base, policy_version=1), frozen)
        self.assertEqual(validate_median_calibration(frozen, self.base), self.base)
        frozen2 = json.loads(Path("tests/fixtures/sssplus-refined-anchors.json").read_text(encoding="utf-8"))["candidate"]
        self.assertEqual(apply_median_calibration(self.base, policy_version=2), frozen2)
        self.assertEqual(validate_median_calibration(frozen2, self.base), self.base)
        with self.assertRaises(ValueError):
            apply_median_calibration(self.base, policy_version=4)

    def test_even_median_and_sparse_same_mode_residual_borrowing(self):
        from sssplus_median_calibration import _apply
        changed = copy.deepcopy(self.base)
        extra = copy.deepcopy(next(r for r in changed["doubles"] if r["level"] == 22))
        extra["chartId"] = "extra-even-median"
        extra["sssplusCenteringBaseline"]["scoring"]["initialFit"]["estimatedDifficulty"] += .1
        changed["doubles"].append(extra)
        latent, population, curves, _ = final_calibrations(changed)
        pool = population["Double"]["22"]["populationChartIds"]
        self.assertEqual(len(pool) % 2, 0)
        ordered = sorted(latent[cid] for cid in pool)
        self.assertAlmostEqual(population["Double"]["22"]["medianLatent"], (ordered[len(pool)//2-1]+ordered[len(pool)//2])/2)
        # Add a supported higher folder, then make only D21 sparse.
        for row in list(changed["doubles"]):
            if row["level"] == 22:
                donor = copy.deepcopy(row)
                donor["chartId"] += "-23"
                donor["level"] = 23
                donor["sssplusCenteringBaseline"]["scoring"]["initialFit"]["estimatedDifficulty"] += 1
                changed["doubles"].append(donor)
            if row["level"] == 21:
                row["sssplusCenteringBaseline"]["scoring"].update(estimatedDifficulty=None, evidenceStatus="Unrated")
                row["sssplusCenteringBaseline"]["scoring"]["initialFit"]["estimatedDifficulty"] = None
        candidate = _apply(changed)
        policy = candidate["summary"]["method"]["sssplusMedianCalibration"]
        self.assertFalse(policy["populations"]["Double"]["21"]["supported"])
        self.assertEqual(policy["residualFolders"]["Double"]["21"]["basis"], "interpolated")
        self.assertEqual(policy["residualFolders"]["Double"]["21"]["donorLevels"], [20, 22])
        self.assertNotIn(21, [a["level"] for a in policy["calibration"]["Double"]["anchors"]])

    def test_once_only_compression_intervals_order_and_midpoints(self):
        policy = self.result["summary"]["method"]["sssplusMedianCalibration"]
        for mode, key in (("Single", "singles"), ("Double", "doubles")):
            curve = policy["calibration"][mode]
            for level, folder in policy["residualFolders"][mode].items():
                rows = [r for r in self.result[key] if r["level"] == int(level)]
                finite_rows = [r for r in rows if r["scoringVariants"]["sssplus"]["estimatedDifficulty"] is not None]
                if folder["basis"] == "folder":
                    self.assertAlmostEqual(median(r["scoringVariants"]["sssplus"]["estimatedDifficulty"] for r in finite_rows), int(level)+.5)
                ordered = sorted(finite_rows, key=lambda r:r["scoringVariants"]["sssplus"]["scoringMedianCalibration"]["refinedLatent"])
                self.assertEqual([r["scoringVariants"]["sssplus"]["estimatedDifficulty"] for r in ordered], sorted(r["scoringVariants"]["sssplus"]["estimatedDifficulty"] for r in ordered))
                for row in finite_rows:
                    s = row["sssplusTailBaseline"]["scoring"]
                    midpoint = row["level"]+.5
                    raw = inverse_final_cost(s["scoringMedianCalibration"]["refinedLatent"], curve)+2*folder["finalShift"]
                    self.assertAlmostEqual(s["initialFit"]["estimatedDifficulty"], raw)
                    self.assertAlmostEqual(s["estimatedDifficulty"], midpoint+.5*(raw-midpoint))
                    if s["difficultyCi95Low"] is not None:
                        self.assertLessEqual(s["difficultyCi95Low"], s["difficultyCi95High"])
                        original = row["sssplusCenteringBaseline"]["scoring"]["initialFit"]
                        preliminary = policy["originalOfficialCalibration"][mode]
                        for side in ("Low", "High"):
                            field = "difficultyCi95"+side
                            endpoint = inverse_final_cost(chart_cost(original[field], preliminary), curve)+2*folder["finalShift"]
                            self.assertAlmostEqual(s[field], midpoint+.5*(endpoint-midpoint))

    def test_strict_rejection_of_stale_projection_and_anchors(self):
        for field in ("refinedLatent", "finalExtrapolated"):
            bad = copy.deepcopy(self.result)
            row = next(r for r in bad["doubles"] if r["level"] == 22)
            row["scoringVariants"]["sssplus"]["scoringMedianCalibration"][field] = 123
            with self.assertRaises(ValueError):
                validate_median_calibration(bad, self.base)


class StableFinalCurveTests(unittest.TestCase):
    anchors = [{"level":20+i,"cost":cost} for i,cost in enumerate([
        1.3719824579397155,1.822503519644659,2.7417098754554967,3.8650660093348526,
        5.061183761894252,5.93836605282502,10.887774386899316,22.717201715034914])]

    @classmethod
    def setUpClass(cls):
        cls.curve = fit_final_curve(cls.anchors, policy_version=3)

    def test_margin_and_adjacent_slope_and_exact_anchors(self):
        a,b = self.anchors[:2]
        self.assertLessEqual(self.curve["offset"],2*a["cost"]-b["cost"])
        self.assertEqual(self.curve["finalScale"]["lowerSlope"],b["cost"]-a["cost"])
        for anchor in self.anchors:
            self.assertAlmostEqual(inverse_final_cost(anchor["cost"],self.curve),anchor["level"]+.5)

    def test_boundary_continuity_ordering_and_upper_tangent(self):
        first,last = self.anchors[0],self.anchors[-1]
        self.assertAlmostEqual(inverse_final_cost(first["cost"]-1e-10,self.curve),20.5,places=8)
        self.assertAlmostEqual(inverse_final_cost(first["cost"]+1e-10,self.curve),20.5,places=8)
        costs=[-100.,-5.,0.,first["cost"]-1e-6,first["cost"],first["cost"]+1e-6,5.,last["cost"],100.]
        values=[inverse_final_cost(v,self.curve) for v in costs]
        self.assertEqual(values,sorted(values))
        self.assertEqual(inverse_final_cost(100.,self.curve),inverse_chart_cost(100.,self.curve))

    def test_lower_scale_is_independent_of_highest_anchor(self):
        short=fit_final_curve(self.anchors[:-1],policy_version=3)
        for cost in [-5.,-.1,.9,1.3]:
            self.assertEqual(inverse_final_cost(cost,short),inverse_final_cost(cost,self.curve))

    def test_affine_cost_units_do_not_change_rating(self):
        shifted=fit_final_curve([{**a,"cost":3*a["cost"]-7} for a in self.anchors],policy_version=3)
        for cost in [-5.,1.2,1.5,4.,12.,30.]:
            self.assertAlmostEqual(inverse_final_cost(cost,self.curve),inverse_final_cost(3*cost-7,shifted),places=7)


if __name__ == "__main__":
    unittest.main()
