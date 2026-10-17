import unittest

import numpy as np

from sssplus_rare_continuous import conditional_map, select_scale


class ConditionalRareTests(unittest.TestCase):
    def test_finite_one_sided_and_stationary(self):
        for outcomes in ([0, 0, 0], [1, 1, 1], [1, 0, 0]):
            result = conditional_map([0, 1, 2], outcomes, [1, .5, .25], 1., 4.)
            self.assertAlmostEqual(result['solverResidual'], 0., places=10)
            self.assertTrue(np.isfinite(result['posteriorEffect']))

    def test_more_success_means_easier(self):
        a = [0, 1, 2, 3]; w = [1]*4
        hard = conditional_map(a, [0, 0, 0, 1], w, 2., 9.)
        easy = conditional_map(a, [0, 0, 1, 1], w, 2., 9.)
        self.assertLess(easy['posteriorEffect'], hard['posteriorEffect'])

    def test_stronger_failed_players_mean_harder(self):
        low = conditional_map([0, 1, 2], [1, 0, 0], [1]*3, 1., 4.)
        high = conditional_map([0, 2, 3], [1, 0, 0], [1]*3, 1., 4.)
        self.assertGreater(high['posteriorEffect'], low['posteriorEffect'])

    def test_location_equivariance_and_permutation(self):
        a = np.array([0., 1., 3.]); y = np.array([1, 0, 0]); w = np.array([.2, .4, .8])
        first = conditional_map(a, y, w, 2., 3.)
        moved = conditional_map(a[::-1]+7, y[::-1], w[::-1], 9., 3.)
        self.assertAlmostEqual(first['posteriorEffect']+7, moved['posteriorEffect'])

    def test_invalid_inputs_and_empty(self):
        for args in [([], [], [], 0, 1), ([1], [2], [1], 0, 1), ([1], [0], [0], 0, 1), ([1], [0], [1], 0, 0)]:
            with self.assertRaises(ValueError):
                conditional_map(*args)

    def test_prior_scale_uses_likelihood_and_is_deterministic(self):
        donors = [{'a': [0, 1, 2, 3], 'y': [1, 0, 0, 0], 'w': [1]*4, 'mean': 2., 'increment': 1.}]*3
        result = select_scale(donors)
        self.assertEqual(result, select_scale(list(reversed(donors))))
        self.assertEqual(result['scale'], min(result['candidates'], key=lambda x: (x['objective'], x['scale']))['scale'])


if __name__ == '__main__':
    unittest.main()
