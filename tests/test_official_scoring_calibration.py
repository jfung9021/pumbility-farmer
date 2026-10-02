import unittest
import numpy as np
from official_scoring_calibration import fit_scoring_spreads, calibrate_scoring_delta


def folder(values, counts=None, eligible=None):
    return {"offsets": values, "contributors": counts if counts is not None else [10]*len(values),
            "eligible": eligible if eligible is not None else [True]*len(values)}


class OfficialScoringCalibrationTests(unittest.TestCase):
    def test_uses_supported_absolute_quantile_and_independent_levels(self):
        values=list(np.linspace(-1,1,21))
        fitted=fit_scoring_spreads({("Double",26):folder(values), ("Double",27):folder([2*v for v in values]),
                                   ("Single",25):folder(values)})
        for key,p in fitted.items():
            multiple=2 if key==("Double",27) else 1
            self.assertAlmostEqual(p["referenceSpread"],np.quantile(np.abs(values),.90)*multiple)
            self.assertAlmostEqual(p["scale"],.45/p["referenceSpread"])
        p=fitted[("Double",26)]
        self.assertGreater(calibrate_scoring_delta(1.1,p,10)[0],.45)

    def test_below_scope_folders_cannot_supply_scoring_calibration(self):
        fitted = fit_scoring_spreads({("Single",24):folder(list(np.linspace(-1,1,20))),
                                     ("Double",25):folder(list(np.linspace(-1,1,20))),
                                     ("Single",25):folder([-.1,.1]), ("Double",26):folder([-.1,.1])})
        self.assertEqual(set(fitted), {("Single",25),("Double",26)})
        self.assertTrue(all(p["basis"] == "fallback" for p in fitted.values()))

    def test_narrow_and_zero_distributions_are_not_stretched(self):
        for values in [list(np.linspace(-.1,.1,20)),[0]*20]:
            p=fit_scoring_spreads({("Single",25):folder(values)})[("Single",25)]
            self.assertEqual(p["scale"],1)
            self.assertEqual([calibrate_scoring_delta(v,p,10)[0] for v in values],values)

    def test_outlier_labels_do_not_gate_level_crossings_or_limit_tails(self):
        values=list(np.linspace(-.1,.1,100))+[-4,4]
        p=fit_scoring_spreads({("Double",26):folder(values)})[("Double",26)]
        for v in [-4,4]:
            self.assertEqual(calibrate_scoring_delta(v,p,10),(v,True))
            self.assertEqual(calibrate_scoring_delta(v,p,9),(v,False))
            self.assertEqual(calibrate_scoring_delta(v,p,10,False),(v,False))

    def test_unreliable_charts_do_not_set_reference_or_compress_other_estimates(self):
        values=list(np.linspace(-1,1,20))
        p=fit_scoring_spreads({("Double",26):folder(values)})[("Double",26)]
        after=fit_scoring_spreads({("Double",26):folder(values+[1000,-1000],[10]*20+[9,50],[True]*20+[True,False])})[("Double",26)]
        self.assertEqual(p,after)

    def test_sparse_folders_borrow_only_supported_same_mode_scales(self):
        data={("Double",26):folder(list(np.linspace(-1,1,20))),
              ("Double",28):folder(list(np.linspace(-2,2,20))),
              ("Double",27):folder([-10,10],[1,1]), ("Single",26):folder([-.1,.1])}
        fitted=fit_scoring_spreads(data)
        self.assertEqual(fitted[("Double",27)]["referenceLevel"],26)
        self.assertEqual(fitted[("Double",27)]["scale"],fitted[("Double",26)]["scale"])
        self.assertEqual(fitted[("Double",27)]["basis"],"neighbor-folder")
        self.assertEqual(fitted[("Single",26)]["scale"],.4)
        self.assertIsNone(fitted[("Single",26)]["referenceSpread"])

    def test_one_linear_scale_preserves_order_ties_and_personal_average(self):
        values=list(np.linspace(-2,3,30))
        p=fit_scoring_spreads({("Double",26):folder(values)})[("Double",26)]
        personal=[-5,-5,.2,1.5,8]
        mapped=[calibrate_scoring_delta(v,p,10)[0] for v in personal]
        self.assertEqual(mapped,sorted(mapped))
        self.assertEqual(mapped[0],mapped[1])
        self.assertAlmostEqual(np.mean(mapped),calibrate_scoring_delta(float(np.mean(personal)),p,10)[0])


if __name__ == "__main__": unittest.main()
