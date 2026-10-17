import unittest
import copy
from collections import Counter

import numpy as np

from official_player_scoring import fit_cross_level_player_comparisons
from sss_scoring_model import fit_target_model, refresh_source_sensitivity


def observed_boards(players=60, charts=8, seed=31):
    rng = np.random.default_rng(seed)
    return {str(chart): {str(player): 995000 if rng.random() <
            1 / (1 + np.exp(-(player / (players / 4) - 2 - chart / 5))) else 985000
            for player in range(players)} for chart in range(charts)}


class TargetModelTests(unittest.TestCase):
    def test_target_is_inclusive_and_no_missing_outcomes_are_created(self):
        boards = observed_boards()
        boards['0'] = {str(p): [989999, 990000, 1000000][p % 3] for p in range(60)}
        before = fit_target_model(boards, bootstrap_samples=0)
        chart = before['charts']['0']
        self.assertEqual(chart['usableContributors'], 60)
        self.assertEqual(chart['targetSuccesses'], 40)
        self.assertEqual(chart['belowTarget'], 20)
        # Three isolated records do not meet the four-unique-chart history rule.
        for cid in ['0', '1', '2']:
            boards[cid]['one-off'] = 1000000
        after = fit_target_model(boards, bootstrap_samples=0)
        self.assertEqual(before['charts'], after['charts'])
        self.assertEqual(after['diagnostics']['observations'], 480)
        self.assertEqual(after['policy']['playerTotalWeight'], 1)

    def test_observed_target_order_can_differ_from_general_score_order(self):
        boards = observed_boards(players=60)
        boards['A'] = {str(p): 990001 if p < 45 else 10000 for p in range(60)}
        boards['B'] = {str(p): 1000000 if p < 30 else 989999 for p in range(60)}
        target = fit_target_model(boards, bootstrap_samples=0)['charts']
        general = fit_cross_level_player_comparisons(boards, bootstrap_samples=0)['charts']
        self.assertLess(target['A']['effect'], target['B']['effect'])
        self.assertGreater(general['A']['scoringChartCost'], general['B']['scoringChartCost'])

    def test_adjusts_shared_player_population_instead_of_raw_attainment_rate(self):
        rng = np.random.default_rng(21)
        boards = {str(chart): {} for chart in range(6)}
        for player in range(800):
            ability = -2. if player < 400 else 2.
            for chart in range(4):
                success = rng.random() < 1 / (1 + np.exp(-(ability - (chart - 1.5))))
                boards[str(chart)][str(player)] = 995000 if success else 985000
            # Harder target is observed predominantly by much stronger players.
            chart, effect = ('A', 1.) if player >= 400 else ('B', 0.)
            success = rng.random() < 1 / (1 + np.exp(-(ability - effect)))
            boards.setdefault(chart, {})[str(player)] = 995000 if success else 985000
        fitted = fit_target_model(boards, bootstrap_samples=0, regularization_grid=(.01,))
        a, b = fitted['charts']['A'], fitted['charts']['B']
        self.assertGreater(a['targetSuccesses'] / a['usableContributors'],
                           b['targetSuccesses'] / b['usableContributors'])
        self.assertGreater(a['effect'], b['effect'])

    def test_one_sided_and_disconnected_charts_are_unrated(self):
        boards = observed_boards()
        boards['perfect'] = {str(p): 1000000 for p in range(60)}
        for chart in range(4):
            boards[f'isolated{chart}'] = {f'other{p}': 995000 if p % 2 else 985000 for p in range(10)}
        result = fit_target_model(boards, bootstrap_samples=0)
        perfect = result['charts']['perfect']
        self.assertFalse(perfect['ratingEligible'])
        self.assertIn('one-sided-target-evidence', perfect['provisionalReasons'])
        self.assertIsNone(perfect['effectLow'])
        isolated = result['charts']['isolated0']
        self.assertFalse(isolated['ratingEligible'])
        self.assertFalse(isolated['calibratedComponent'])
        self.assertIsNone(isolated['effect'])
        self.assertEqual(result['diagnostics']['componentCount'], 2)

    def test_general_validation_callback_never_receives_target_observations(self):
        boards = observed_boards()
        calls = []

        def general_predictor(training, entries):
            counts = Counter(player for scores in training.values() for player in scores)
            self.assertGreaterEqual(min(counts.values()), 4)
            for cid, player in entries:
                self.assertNotIn(player, training[cid])
            calls.append(len(entries))
            return [.5] * len(entries)

        result = fit_target_model(boards, bootstrap_samples=0, general_predictor=general_predictor)
        validation = result['diagnostics']['validation']
        self.assertEqual(calls, [validation['records']])
        self.assertTrue(validation['generalTargetBenchmark']['trainingOnly'])
        self.assertTrue(validation['abilityFitExcludesHeldOutEntries'])
        self.assertTrue(np.isfinite(validation['chartRateBaseline']['logLoss']))
        self.assertEqual(len(validation['candidates']), 4)
        # The optimizer, selection and constrained split are deterministic.
        self.assertEqual(result, fit_target_model(boards, bootstrap_samples=0,
                                                  general_predictor=general_predictor))

    def test_player_cluster_refits_and_location_alignment_are_declared(self):
        result = fit_target_model(observed_boards(), bootstrap_samples=200, regularization_grid=(.05,))
        self.assertEqual(result['diagnostics']['bootstrap']['completed'], 200)
        self.assertGreaterEqual(result['diagnostics']['bootstrap']['converged'], 160)
        self.assertFalse(result['diagnostics']['bootstrap']['calibrationRefitted'])
        self.assertEqual(result['policy']['bootstrapLocationAlignment'],
                         'mean-effect-difference-over-fixed-full-fit-reference-charts-v1')
        self.assertGreater(len(result['policy']['bootstrapAlignmentReferenceCharts']), 0)
        for row in result['charts'].values():
            self.assertIsNotNone(row['effectLow'])
            self.assertLessEqual(row['effectLow'], row['effectHigh'])

    def test_invalid_normalized_scores_are_rejected(self):
        for value in [float('nan'), -1, 1000001, True]:
            with self.assertRaises(ValueError):
                fit_target_model({'a': {'p': value}}, bootstrap_samples=0)

    def test_source_removal_is_aligned_and_reports_unsupported_cohorts(self):
        boards = observed_boards()
        result = fit_target_model(boards, bootstrap_samples=0,
                                  source_boards={'same': boards, 'sparse': {'0': {'p': 995000}}})
        self.assertTrue(result['diagnostics']['sourceRemoval']['same']['converged'])
        self.assertEqual(result['diagnostics']['sourceRemoval']['sparse']['reason'],
                         'no-eligible-comparison-component')
        for chart in result['charts'].values():
            self.assertAlmostEqual(chart['effect'], chart['sensitivity']['sourceRemovalEffects']['same'])

    def test_sensitivity_only_refresh_preserves_main_fit_and_bootstrap_exactly(self):
        boards = observed_boards()
        original = fit_target_model(boards, bootstrap_samples=20)
        preserved = copy.deepcopy(original)
        refreshed = refresh_source_sensitivity(original, {'same': boards})
        self.assertEqual(original, preserved)  # The helper never mutates cached input.
        self.assertTrue(refreshed['diagnostics']['sourceRemoval']['same']['converged'])
        self.assertEqual(refreshed['diagnostics']['bootstrap'], original['diagnostics']['bootstrap'])
        for chart in refreshed['charts'].values():
            chart['sensitivity'].pop('sourceRemovalEffects', None)
        refreshed['diagnostics']['sourceRemoval'] = {}
        original['diagnostics'].pop('sourceRemovalVersion', None)
        refreshed['diagnostics'].pop('sourceRemovalVersion', None)
        self.assertEqual(refreshed, original)


if __name__ == '__main__':
    unittest.main()
