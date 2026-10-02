import unittest
import numpy as np
from official_player_scoring import fit_player_comparisons
from official_scoring_calibration import fit_scoring_spreads


class PlayerScoringTests(unittest.TestCase):
    def test_adjusts_for_player_skill_and_selected_chart_sets(self):
        effects = dict(a=-3000, b=-1000, c=1000, d=3000, e=7000)
        boards = {c: {} for c in effects}
        for p, charts, ability in [('low', 'abcd', 940000), ('high', 'bcde', 990000), ('all', 'abcde', 960000)]:
            for c in charts:
                boards[c][p] = ability - effects[c]
        fit = fit_player_comparisons(boards)
        anchor = np.median(list(effects.values()))
        for c, row in fit['charts'].items():
            self.assertAlmostEqual(row['scoringMeanGap'], effects[c] - anchor)
            personal = list(fit['personal'][c].values())
            self.assertAlmostEqual(row['scoringMeanGap'], np.mean([r['gap'] for r in personal]))
            self.assertTrue(all(r['otherCharts'] >= 3 for r in personal))
        # Shifting every score of one player does not alter chart difficulty.
        for scores in boards.values():
            if 'low' in scores:
                scores['low'] += 15000
        after = fit_player_comparisons(boards)
        for c in boards:
            self.assertAlmostEqual(after['charts'][c]['scoringMeanGap'], fit['charts'][c]['scoringMeanGap'])

    def test_exact_mean_of_personal_difficulties_even_with_disagreement(self):
        boards = {c: {str(p): 980000 - i * 2000 + (p % 3) * i * 1100 for p in range(15)} for i,c in enumerate('abcd')}
        fit = fit_player_comparisons(boards)
        rows = fit['charts']
        params = fit_scoring_spreads({('Single',25): {'offsets':[r['scoringMeanGap']/10000 for r in rows.values()], 'contributors':[15]*4}})[('Single',25)]
        for c, row in rows.items():
            personal = [25.5 + params['scale'] * v['gap']/10000 for v in fit['personal'][c].values()]
            self.assertAlmostEqual(np.mean(personal), 25.5 + params['scale'] * row['scoringMeanGap']/10000)
            self.assertIsNotNone(row['scoringGapCi95Low'])
        self.assertEqual(fit['charts'], fit_player_comparisons(boards)['charts'])

    def test_sparse_disconnected_and_missing_histories(self):
        two = fit_player_comparisons({'a': {'p':990000}, 'b':{'p':980000}})
        self.assertTrue(two['charts']['a']['scoringProvisional'])
        self.assertEqual(two['charts']['a']['scoringMinimumOtherCharts'],1)
        one = fit_player_comparisons({'a': {'p':990000}})['charts']['a']
        self.assertIsNone(one['scoringMeanGap'])
        disconnected = fit_player_comparisons({'a': {'p':990000}, 'b':{'p':980000}, 'c':{'q':970000}, 'd':{'q':990000}})
        self.assertEqual(disconnected['componentCount'],2)
        self.assertTrue(all(r['scoringProvisional'] for r in disconnected['charts'].values()))

    def test_ineligible_players_do_not_influence_normal_panel(self):
        boards = {c: {'repeat': 970000 + i*1000} for i,c in enumerate('abcd')}
        before = fit_player_comparisons(boards)
        boards['a']['one-off'] = 100
        boards['b']['two'] = 500000
        boards['c']['two'] = 500000
        after = fit_player_comparisons(boards)
        self.assertEqual(before['charts'],after['charts'])


if __name__ == '__main__':
    unittest.main()
