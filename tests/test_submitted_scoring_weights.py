import unittest

import numpy as np

from scripts.analyze_submitted_scoring_weights import (
    fit_weighted, matched_panels, observation_weights, predictive_metrics,
    split_weights,
)
from sss_scoring_model import _fit


class SubmittedScoringWeightTests(unittest.TestCase):
    def test_each_player_total_one_and_equal_folder_policy(self):
        players = np.asarray([0, 0, 0, 0, 1, 1, 1])
        folders = np.asarray([16, 16, 16, 18, 16, 18, 18])
        for policy in ("inverse-history", "equal-observed-folders"):
            weights = observation_weights(players, folders, player_count=2, weighting=policy)
            np.testing.assert_allclose(np.bincount(players, weights=weights), [1, 1])
        weights = observation_weights(players, folders, player_count=2,
                                      weighting="equal-observed-folders")
        for player in (0, 1):
            for folder in (16, 18):
                self.assertAlmostEqual(float(np.sum(weights[(players == player) & (folders == folder)])), .5)

    def test_training_weights_ignore_heldout_counts_and_folders(self):
        players = np.asarray([0, 0, 0, 0, 1, 1, 1])
        folders = np.asarray([16, 16, 18, 19, 16, 18, 18])
        train = np.asarray([True, True, True, False, True, True, False])
        for policy in ("inverse-history", "equal-observed-folders"):
            expected = observation_weights(players[train], folders[train], player_count=2, weighting=policy)
            actual = split_weights(players, folders, train, player_count=2, weighting=policy)
            np.testing.assert_array_equal(actual, expected)
            changed = folders.copy(); changed[~train] = 28
            np.testing.assert_array_equal(actual, split_weights(
                players, changed, train, player_count=2, weighting=policy))
            np.testing.assert_allclose(np.bincount(players[train], weights=actual), [1, 1])

    def test_baseline_objective_matches_current_model(self):
        p = np.tile(np.arange(8), 5)
        c = np.repeat(np.arange(5), 8)
        y = np.random.default_rng(31).integers(0, 2, len(p)).astype(float)
        weights = observation_weights(p, np.repeat([16, 16, 17, 18, 18], 8),
                                      player_count=8, weighting="inverse-history")
        expected = _fit(p, c, y, 8, 5, .01)
        actual = fit_weighted(p, c, y, weights, 8, 5, .01)
        self.assertTrue(actual["converged"])
        np.testing.assert_allclose(actual["ability"], expected["ability"], atol=1e-9)
        np.testing.assert_allclose(actual["effect"], expected["effect"], atol=1e-9)
        self.assertAlmostEqual(actual["objective"], expected["objective"], places=12)

    def test_deterministic_metrics_and_equal_player_diagnostic(self):
        y, probabilities = np.asarray([1., 0., 1.]), np.asarray([.9, .2, .4])
        players = np.asarray([0, 0, 1])
        result = predictive_metrics(y, probabilities, players)
        self.assertEqual(result, predictive_metrics(y, probabilities, players))
        self.assertAlmostEqual(result["brier"], (.01 + .04 + .36) / 3)
        self.assertAlmostEqual(result["equalPlayer"]["brier"], ((.01 + .04) / 2 + .36) / 2)
        self.assertEqual(sum(row["records"] for row in result["calibrationBins"]), 3)

    def test_matched_panel_qualifies_by_coverage_not_attainment(self):
        catalog = {f"{level}-{index}": {"level": level} for level in (16, 18) for index in range(5)}
        boards = {cid: {"low": 900000, "high": 999000} for cid in catalog}
        result = matched_panels(boards, catalog, bootstrap_samples=20)
        self.assertEqual(result[0]["players"], 2)
        self.assertEqual(result[0]["D18MinusD16"]["sssplusRate"], 0)
        self.assertEqual(result[0]["folders"]["16"]["sssplusRate"], .5)
        self.assertEqual(result[1]["players"], 0)


if __name__ == "__main__":
    unittest.main()
