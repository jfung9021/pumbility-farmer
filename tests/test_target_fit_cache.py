import copy
import json
import unittest
from unittest.mock import patch

import numpy as np

import sss_scoring_model as model
import target_fit_cache as cache
from sss_scoring_refinement import recover_point_fit


def boards_fixture():
    rng = np.random.default_rng(31)
    return {str(chart): {str(player): 995000 if rng.random() <
        1 / (1 + np.exp(-(player / 8 - 2 - chart / 5))) else 985000
        for player in range(32)} for chart in range(8)}


class JsonStore:
    def __init__(self):
        self.values = {}

    def get_json(self, path):
        return json.loads(self.values[path]) if path in self.values else None

    def put_json(self, path, value):
        self.values[path] = json.dumps(value, allow_nan=False)


class TargetFitCacheTests(unittest.TestCase):
    def setUp(self):
        cache._POINT_CONTEXTS.clear()
        self.boards = boards_fixture()
        self.store = JsonStore()

    def test_backend_json_integrity_failure_recomputes_from_current_inputs(self):
        from pumbility_store import PumbilityArtifactIntegrityError
        kwargs = dict(bootstrap_samples=0, regularization_grid=(.05,))
        expected = model.fit_target_model(self.boards, **kwargs)
        with patch.object(self.store, "get_json", side_effect=PumbilityArtifactIntegrityError(
                digest_matches=False, byte_size_matches=True)):
            self.assertEqual(cache.load_or_fit_target(self.store, self.boards, **kwargs), expected)
            self.assertFalse(cache.hydrate_point_context(self.store, self.boards, expected))

    def test_200_bootstrap_parity_and_same_binary_reuse_with_fresh_raw_diagnostics(self):
        calls = []

        def predictor(training, entries):
            for cid, player in entries:
                self.assertNotIn(player, training[cid])
            mean = np.mean([score for board in training.values() for score in board.values()])
            calls.append(float(mean))
            return [float((mean - 980000) / 20000)] * len(entries)

        kwargs = dict(bootstrap_samples=200, seed=20261018, general_predictor=predictor)
        expected = model.fit_target_model(self.boards, source_boards={"one": self.boards}, **kwargs)
        actual = cache.load_or_fit_target(self.store, self.boards,
                                         source_boards={"one": self.boards}, **kwargs)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["diagnostics"]["bootstrap"]["completed"], 200)
        self.assertEqual(actual["diagnostics"]["bootstrap"]["failed"], 0)
        changed = {cid: {player: value + 1 for player, value in board.items()}
                   for cid, board in self.boards.items()}
        fresh = model.fit_target_model(changed, source_boards={"one": changed}, **kwargs)
        with patch.object(model, "fit_target_model", side_effect=AssertionError("Main fit repeated")), \
             patch.object(model, "_fit", side_effect=AssertionError("Source fit repeated")):
            reused = cache.load_or_fit_target(self.store, changed, source_boards={"one": changed}, **kwargs)
        self.assertEqual(reused, fresh)
        self.assertNotEqual(reused["diagnostics"]["validation"]["generalTargetBenchmark"],
                            actual["diagnostics"]["validation"]["generalTargetBenchmark"])
        self.assertEqual(calls[-1], calls[-2])
        self.assertEqual(set(reused), {"charts", "policy", "diagnostics"})

    def test_source_only_change_does_not_repeat_main_or_bootstrap(self):
        kwargs = dict(bootstrap_samples=12, regularization_grid=(.05,))
        original = cache.load_or_fit_target(self.store, self.boards,
                                           source_boards={"one": self.boards}, **kwargs)
        source = copy.deepcopy(self.boards)
        source["0"]["0"] = 1000000 if source["0"]["0"] < 990000 else 980000
        expected = model.fit_target_model(self.boards, source_boards={"one": source}, **kwargs)
        with patch.object(model, "fit_target_model", side_effect=AssertionError("Main fit repeated")), \
             patch.object(model, "_fit", wraps=model._fit) as point_fit:
            actual = cache.load_or_fit_target(self.store, self.boards,
                                             source_boards={"one": source}, **kwargs)
        self.assertEqual(actual, expected)
        self.assertEqual(point_fit.call_count, 1)
        self.assertEqual(actual["diagnostics"]["bootstrap"], original["diagnostics"]["bootstrap"])

    def test_changed_binary_graph_policy_and_runtime_identity_never_reuse_samples(self):
        kwargs = dict(bootstrap_samples=0, regularization_grid=(.05,))
        cache.load_or_fit_target(self.store, self.boards, **kwargs)
        outcome = copy.deepcopy(self.boards)
        outcome["0"]["0"] = 1000000 if outcome["0"]["0"] < 990000 else 980000
        membership = copy.deepcopy(self.boards)
        membership["0"].pop("0")
        ineligible = copy.deepcopy(self.boards)
        ineligible["0"]["new"] = 985000
        empty_chart = {**self.boards, "empty": {}}
        variants = [(outcome, kwargs), (membership, kwargs), (ineligible, kwargs), (empty_chart, kwargs),
            (self.boards, {**kwargs, "seed": 7}),
            (self.boards, {**kwargs, "score_target": 995000}),
            (self.boards, {**kwargs, "bootstrap_samples": 1}),
            (self.boards, {**kwargs, "regularization_grid": (.05, .2)})]
        for boards, policy in variants:
            with self.subTest(policy=policy), patch.object(model, "fit_target_model", wraps=model.fit_target_model) as fit:
                actual = cache.load_or_fit_target(self.store, boards, **policy)
                self.assertEqual(fit.call_count, 1)
                self.assertEqual(actual, model.fit_target_model(boards, **policy))
        with patch.object(cache, "_code_identity", return_value="changed-runtime"), \
             patch.object(model, "fit_target_model", wraps=model.fit_target_model) as fit:
            cache.load_or_fit_target(self.store, self.boards, **kwargs)
            self.assertEqual(fit.call_count, 1)

    def test_private_point_context_roundtrip_avoids_replay_and_legacy_still_replays(self):
        kwargs = dict(bootstrap_samples=8, regularization_grid=(.05,))
        legacy = model.fit_target_model(self.boards, **kwargs)
        self.assertFalse(cache.hydrate_point_context(self.store, self.boards, legacy))
        with patch("sss_scoring_refinement._fit", wraps=model._fit) as fit:
            expected = recover_point_fit(self.boards, legacy)
            self.assertEqual(fit.call_count, 1)
        cached = cache.load_or_fit_target(self.store, self.boards, **kwargs)
        self.assertEqual(cached, legacy)
        cache._POINT_CONTEXTS.clear()
        self.assertTrue(cache.hydrate_point_context(self.store, self.boards, cached))
        with patch("sss_scoring_refinement._fit", side_effect=AssertionError("Point replay repeated")):
            actual = recover_point_fit(self.boards, cached)
        self.assertEqual(actual["diagnostics"], expected["diagnostics"])
        for key in actual["fit"]:
            if isinstance(actual["fit"][key], np.ndarray):
                np.testing.assert_array_equal(actual["fit"][key], expected["fit"][key])
            else:
                self.assertEqual(actual["fit"][key], expected["fit"][key])

    def test_raw_diagnostic_requests_do_not_leak_into_subsequent_cache_reads(self):
        kwargs = dict(bootstrap_samples=0, regularization_grid=(.05,))
        cache.load_or_fit_target(self.store, self.boards, general_predictor=lambda b, e: [.5] * len(e),
                                 general_predictions={cid: .4 for cid in self.boards}, **kwargs)
        actual = cache.load_or_fit_target(self.store, self.boards, **kwargs)
        self.assertEqual(actual, model.fit_target_model(self.boards, **kwargs))
        self.assertNotIn("externalGeneralPredictionDiagnostic", actual["diagnostics"]["validation"])

    def test_corrupt_optional_context_rebuilds_and_hydration_uses_legacy_replay(self):
        expected = cache.load_or_fit_target(self.store, self.boards, bootstrap_samples=0)
        path = next(path for path in self.store.values if "/main/" in path)
        corrupted = json.loads(self.store.values[path])
        corrupted["payload"]["context"]["pointFit"]["effect"][0] += 1
        self.store.values[path] = json.dumps(corrupted)
        cache._POINT_CONTEXTS.clear()
        self.assertFalse(cache.hydrate_point_context(self.store, self.boards, expected))
        with patch("sss_scoring_refinement._fit", wraps=model._fit) as replay:
            recover_point_fit(self.boards, expected)
            self.assertEqual(replay.call_count, 1)
        with patch.object(model, "fit_target_model", wraps=model.fit_target_model) as rebuild:
            actual = cache.load_or_fit_target(self.store, self.boards, bootstrap_samples=0)
            self.assertEqual(rebuild.call_count, 1)
        self.assertEqual(actual, expected)

    def test_malformed_optional_records_rebuild_with_exact_parity(self):
        expected = cache.load_or_fit_target(self.store, self.boards, bootstrap_samples=0)
        path = next(path for path in self.store.values if "/main/" in path)
        original = self.store.values[path]
        malformed_context = json.loads(original)
        malformed_context["payload"]["context"]["pointFit"]["ability"] = []
        malformed_context["payloadSha256"] = cache.canonical_json_sha256(malformed_context["payload"])
        for malformed in ([], {}, {"version": 1}, malformed_context):
            with self.subTest(record_type=type(malformed).__name__):
                self.store.values[path] = json.dumps(malformed)
                cache._POINT_CONTEXTS.clear()
                self.assertFalse(cache.hydrate_point_context(self.store, self.boards, expected))
                with patch.object(model, "fit_target_model", wraps=model.fit_target_model) as rebuild:
                    actual = cache.load_or_fit_target(self.store, self.boards, bootstrap_samples=0)
                    self.assertEqual(rebuild.call_count, 1)
                self.assertEqual(actual, expected)

    def test_corrupt_optional_source_component_rebuilds_without_main_refit(self):
        kwargs = dict(bootstrap_samples=0, source_boards={"one": self.boards})
        expected = cache.load_or_fit_target(self.store, self.boards, **kwargs)
        path = next(path for path in self.store.values if "/source/" in path)
        self.store.values[path] = json.dumps({"version": 1})
        with patch.object(model, "fit_target_model", side_effect=AssertionError("Main fit repeated")), \
             patch.object(model, "refresh_source_sensitivity", wraps=model.refresh_source_sensitivity) as rebuild:
            actual = cache.load_or_fit_target(self.store, self.boards, **kwargs)
            self.assertEqual(rebuild.call_count, 1)
        self.assertEqual(actual, expected)

    def test_store_read_errors_propagate_and_empty_legacy_outputs_match(self):
        with patch.object(self.store, "get_json", side_effect=OSError("Store unavailable")):
            with self.assertRaisesRegex(OSError, "Store unavailable"):
                cache.load_or_fit_target(self.store, self.boards)
        empty = {"empty": {"one-off": 985000}}
        kwargs = dict(source_boards={"source": empty},
                      general_predictor=lambda b, e: self.fail("No eligible holdout"))
        self.assertEqual(cache.load_or_fit_target(self.store, empty, **kwargs),
                         model.fit_target_model(empty, **kwargs))


if __name__ == "__main__":
    unittest.main()
