import unittest
import numpy as np
from official_clearing_calibration import fit_clearing_spreads, calibrate_clearing_delta


def folder(values, players=50):
    return {"deltas":values,"players":[players]*len(values)}


def estimate(v,p,**kw):
    args=dict(players=50,coverage=1,interval_low=None,interval_high=None)
    args.update(kw)
    return calibrate_clearing_delta(v,p,**args)


class ClearingCalibrationTests(unittest.TestCase):
    def test_narrow_signals_expand_to_reference_width_and_preserve_ties(self):
        values=[-.2,-.1,0,0,0,.1,.2]
        p=fit_clearing_spreads({("Single",24):folder(values)})[("Single",24)]
        self.assertAlmostEqual(p["scale"],2.25)
        mapped=[estimate(v,p)[0] for v in values]
        self.assertAlmostEqual(np.quantile(np.abs(mapped),.90),.45)
        self.assertEqual(mapped,sorted(mapped))
        self.assertEqual(mapped[2:5],[0,0,0])

    def test_both_signs_share_the_supported_absolute_quantile_scale(self):
        values=list(np.linspace(-1,2,20))
        p=fit_clearing_spreads({("Double",25):folder(values)})[("Double",25)]
        self.assertAlmostEqual(p["referenceSpread"],np.quantile(np.abs(values),.90))
        self.assertAlmostEqual(p["scale"],.45/p["referenceSpread"])
        self.assertEqual(estimate(-.5,p)[0],-estimate(.5,p)[0])

    def test_tails_cross_without_outlier_eligibility_and_without_maximum_shift(self):
        p=fit_clearing_spreads({("Double",26):folder(list(np.linspace(-.1,.1,100))+[-3,3])})[("Double",26)]
        for v in [-3,3]:
            self.assertGreater(abs(estimate(v,p)[0]),3)
            self.assertEqual(estimate(v,p),(v*p["scale"],False))
            self.assertEqual(estimate(v,p,interval_low=v-.1,interval_high=v+.1),(v*p["scale"],True))
            self.assertEqual(estimate(v,p,players=19,interval_low=v-.1,interval_high=v+.1),(v*p["scale"],False))

    def test_every_reference_chart_needs_ten_players(self):
        data=folder([-.2,-.1,0,.1,.2,1000])
        data["players"][-1]=9
        p=fit_clearing_spreads({("Single",24):data})[("Single",24)]
        self.assertEqual(p["supportedCharts"],5)
        self.assertAlmostEqual(p["scale"],2.25)
        data["players"][0]=9
        self.assertEqual(fit_clearing_spreads({("Single",24):data})[("Single",24)]["basis"],"fallback")

    def test_sparse_folders_borrow_within_mode_and_fallback_is_linear(self):
        p=fit_clearing_spreads({("Single",24):folder([-.2,-.1,0,.1,.2]),
                               ("Single",26):folder([-.2,.2]), ("Double",29):folder([0],1)})
        self.assertEqual(p[("Single",26)]["referenceLevel"],24)
        self.assertEqual(p[("Single",26)]["scale"],p[("Single",24)]["scale"])
        self.assertGreater(p[("Single",26)]["scale"],1)
        self.assertIsNone(p[("Single",26)]["lowerFence"])
        self.assertEqual(estimate(2,p[("Double",29)]),(2,False))

    def test_flat_supported_reference_keeps_unit_scale(self):
        p=fit_clearing_spreads({("Single",24):folder([0]*10)})[("Single",24)]
        self.assertEqual(p["referenceSpread"],0)
        self.assertEqual(p["scale"],1)
        self.assertEqual(estimate(0,p),(0,False))


if __name__ == "__main__": unittest.main()
