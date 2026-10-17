import math
import unittest
from statistics import median
from final_folder_width import width_parameters,width_value

def params(values):return width_parameters([{'id':str(i),'value':v} for i,v in enumerate(values)],26)

class FinalWidthTests(unittest.TestCase):
    def test_even_median_and_narrow_asymmetric_sides(self):
        values=[26.2,26.45,26.55,26.57,26.6,26.8]
        p=params(values);out=[width_value(v,p) for v in values]
        self.assertAlmostEqual(median(out),26.5)
        self.assertGreater(out[-1]-26.5,.4);self.assertGreater(26.5-out[0],.4)
        self.assertEqual(sorted(out),out)

    def test_odd_median_ties_and_dense_monotonicity(self):
        p=params([26.2,26.4,26.4,26.4,26.6])
        self.assertEqual(width_value(26.4,p),26.5)
        vals=[width_value(25+i/1000,p) for i in range(3001)]
        self.assertTrue(all(a<=b for a,b in zip(vals,vals[1:])))
        self.assertEqual(width_value(None,p),None)

    def test_already_wide_sides_unchanged(self):
        values=[25.2,25.9,26.4,26.6,27.1,28.2];p=params(values)
        self.assertEqual([width_value(v,p) for v in values],values)

    def test_sparse_and_zero_spread_do_not_inflate(self):
        for values in [[26.1,26.2,26.3],[26.5]*4,[26.5,26.5,26.5,26.500000001]]:
            p=params(values);self.assertEqual(p['basis'],'unavailable')
            self.assertEqual([width_value(v,p) for v in values],values)

    def test_four_chart_folder_is_provisional_and_can_cross_level(self):
        values=[26.2,26.4,26.5,27.1];p=params(values)
        self.assertTrue(p['provisional'])
        self.assertAlmostEqual(median([width_value(v,p) for v in values]),26.5)
        self.assertGreater(width_value(27.1,p),27.)

    def test_cap_defaults_do_not_supply_width(self):
        p=width_parameters([{'id':str(i),'value':26+i*.1,'capDefault':i<2} for i in range(5)],26)
        self.assertEqual(p['basis'],'unavailable');self.assertEqual(p['ratedCharts'],3)

    def test_nonfinite_is_rejected(self):
        with self.assertRaises(ValueError):width_value(math.inf,params([26.,26.4,26.6,27.]))

if __name__=='__main__':unittest.main()
