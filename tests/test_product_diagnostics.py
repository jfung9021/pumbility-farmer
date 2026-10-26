"""Routine fits retain every numerical result without research-only refits."""
import copy
import unittest
from unittest.mock import patch

import sss_scoring_model as model
import target_fit_cache as cache
from tests.test_target_fit_cache import JsonStore, boards_fixture


class ProductDiagnosticsTests(unittest.TestCase):
    def test_existing_main_reuses_exact_values_without_source_or_general_refits(self):
        boards, store = boards_fixture(), JsonStore()
        kwargs = dict(bootstrap_samples=12, seed=20261018)
        full = cache.load_or_fit_target(store, boards, source_boards={"one": boards},
            general_predictor=lambda b, e: [.5] * len(e), **kwargs)
        with patch.object(model, "fit_target_model", side_effect=AssertionError("main repeated")), \
             patch.object(model, "refresh_source_sensitivity", side_effect=AssertionError("source diagnostics ran")), \
             patch.object(model, "_general_validation", side_effect=AssertionError("General benchmark ran")):
            product = cache.load_or_fit_target(store, boards, source_boards={"one": boards},
                general_predictor=lambda b, e: self.fail("predictor ran"),
                diagnostics_policy="product-only", **kwargs)
        expected_charts = copy.deepcopy(full["charts"])
        for row in expected_charts.values():
            row["sensitivity"].pop("sourceRemovalEffects", None)
        self.assertEqual(product["charts"], expected_charts)
        self.assertEqual(product["policy"], full["policy"])
        self.assertEqual(product["diagnostics"]["bootstrap"], full["diagnostics"]["bootstrap"])
        self.assertEqual(product["diagnostics"]["regularizationSensitivity"], full["diagnostics"]["regularizationSensitivity"])
        self.assertEqual(product["diagnostics"]["validation"]["selectedRegularization"],
                         full["diagnostics"]["validation"]["selectedRegularization"])
        self.assertEqual(product["diagnostics"]["optionalDiagnostics"]["sourceRemoval"], "not-computed")
        self.assertIsNone(product["diagnostics"]["validation"]["generalTargetBenchmark"])
        self.assertIsNotNone(full["diagnostics"]["validation"]["generalTargetBenchmark"])
        progress = cache.pending_fit_progress(store, boards, diagnostics_policy="product-only", **kwargs)
        self.assertEqual(progress["ordinal"], 13)

    def test_cold_product_and_legacy_cache_share_main_and_context(self):
        boards, store = boards_fixture(), JsonStore()
        kwargs = dict(bootstrap_samples=8, seed=20261018, regularization_grid=(.05,))
        with patch.object(model, "_fit", wraps=model._fit) as solve:
            product = cache.load_or_fit_target(store, boards, diagnostics_policy="product-only", **kwargs)
        self.assertEqual(solve.call_count, 10)  # validation + point + eight bootstrap solves
        self.assertFalse(any("/source/" in key for key in store.values))
        with patch.object(model, "fit_target_model", side_effect=AssertionError("main repeated")):
            legacy = cache.load_or_fit_target(store, boards, **kwargs)
        self.assertEqual(product["charts"], legacy["charts"])
        self.assertEqual(product["policy"], legacy["policy"])
        self.assertTrue(cache.hydrate_point_context(store, boards, product))
        self.assertNotIn("optionalDiagnostics", legacy["diagnostics"])

    def test_unknown_policy_is_rejected_before_computation(self):
        with patch.object(model, "fit_target_model", side_effect=AssertionError("fit ran")):
            with self.assertRaisesRegex(ValueError, "diagnostics policy"):
                cache.load_or_fit_target(JsonStore(), boards_fixture(), diagnostics_policy="unrecognized")
            with self.assertRaisesRegex(ValueError, "diagnostics policy"):
                cache.pending_fit_progress(JsonStore(), boards_fixture(), diagnostics_policy="unrecognized")


if __name__ == "__main__":
    unittest.main()
