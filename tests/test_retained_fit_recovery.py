"""Exact reviewed-runtime main/partial adoption, without modifying source work."""
import copy
import json
import unittest
from unittest.mock import patch

from analysis_runtime import MemoryBlobStore
import numeric_recipe as recipes
import production_tiers as tiers
import sss_scoring_model as model
import target_fit_cache as cache
from tests.test_target_fit_cache import boards_fixture
from tier_pipeline_checkpoints import write_checkpoint


class StopAtTen(RuntimeError):
    pass


class RetainedFitRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.boards = boards_fixture()
        cls.kwargs = dict(score_target=995000, bootstrap_samples=200, seed=tiers.SEED)
        cls.data = model._prepare(cls.boards, 995000)
        cls.identity = cache._identity(cls.data, **cls.kwargs, regularization_grid=model.REGULARIZATION_GRID)
        cls.legacy_identity = {**cls.identity, "code": recipes.legacy_component_code()}
        cls.legacy_producer = recipes.legacy_outer_identity()
        store = MemoryBlobStore()
        cls.expected = cache.load_or_fit_target(store, cls.boards, diagnostics_policy="product-only", **cls.kwargs)
        cls.main = store.get_json(cache._path("main", cls.identity))["payload"]
        interrupted = MemoryBlobStore()
        base = cache._BootstrapCheckpoint(interrupted, cls.legacy_identity)

        class Partial:
            load = base.load
            def save(self, value):
                base.save(value)
                if value["completed"] == 10:
                    raise StopAtTen()

        with unittest.TestCase().assertRaises(StopAtTen):
            model.fit_target_model(cls.boards, **cls.kwargs, _private_context={}, _bootstrap_checkpoint=Partial())
        cls.partial = interrupted.get_json(base.path)["payload"]

    def setUp(self):
        self.store = MemoryBlobStore()

    def install(self, kind, payload):
        cache._write(self.store, kind, self.legacy_identity, payload)

    def generation(self):
        identity = {"modelCode": self.legacy_producer, "pipelineCode": "historical-pinned-worker"}
        generation = tiers.digest(identity)
        state = {"version": 1, "identity": identity, "generation": generation,
            "general": {"stage": "general", "identity": "original-general"},
            "phaseInputs": {name: {"stage": "phase-inputs-" + name,
                "identity": tiers.digest({"generation": generation, "kind": name})}
                for name in tiers.PHASE_INPUT_NAMES}, "preparedFitInputs": []}
        from sss_scoring import payload_sha256
        for goal, source, mode in tiers.PREPARATION_COHORTS:
            fit_identity = {"boards": payload_sha256(self.boards), "sourceBoards": payload_sha256({source: self.boards}),
                "target": 990000 if goal == "sss" else 995000, "samples": 200, "seed": tiers.SEED}
            reference = write_checkpoint(self.store, {"boards": self.boards, "sources": {source: self.boards}},
                stage="cohort-inputs", identity=tiers.digest({"generation": generation, "goal": goal,
                    "source": source, "mode": mode, "identity": fit_identity}))
            state["preparedFitInputs"].append({"goal": goal, "source": source, "mode": mode,
                "identity": fit_identity, "inputs": reference})
        self.store.put_json(tiers.PREFIX + "generations/" + generation + "/state.json", state)
        return generation, state

    def test_completed_main_adopts_without_solves_and_preserves_original(self):
        self.install("main", self.main)
        old = copy.deepcopy(self.store.values)
        with patch.object(model, "_fit", side_effect=AssertionError("Completed fit solved again")):
            actual = cache.load_or_fit_target(self.store, self.boards, diagnostics_policy="product-only",
                legacy_producer_identity=self.legacy_producer, **self.kwargs)
        self.assertEqual(actual, self.expected)
        for path, value in old.items():
            self.assertEqual(self.store.values[path], value)

    def test_partial_bootstrap_continues_exact_rng_and_never_repeats_initial_solves(self):
        self.install("bootstrap", self.partial)
        old = copy.deepcopy(self.store.values)
        with patch.object(model, "_fit", wraps=model._fit) as fit:
            actual = cache.load_or_fit_target(self.store, self.boards, diagnostics_policy="product-only",
                legacy_producer_identity=self.legacy_producer, **self.kwargs)
        self.assertEqual(actual, self.expected)
        self.assertEqual(fit.call_count, 190)
        self.assertTrue(all(call.kwargs.get("multiplicity") is not None for call in fit.call_args_list))
        for path, value in old.items():
            self.assertEqual(self.store.values[path], value)

    def test_completed_legacy_main_supersedes_current_partial_without_solving(self):
        self.install("main", self.main)
        cache._write(self.store, "bootstrap", self.identity, self.partial)
        original_partial = copy.deepcopy(self.store.get_json(cache._path("bootstrap", self.identity)))
        with patch.object(model, "_fit", side_effect=AssertionError("Completed legacy main ignored")):
            actual = cache.load_or_fit_target(self.store, self.boards, diagnostics_policy="product-only",
                legacy_producer_identity=self.legacy_producer, **self.kwargs)
        self.assertEqual(actual, self.expected)
        self.assertEqual(self.store.get_json(cache._path("bootstrap", self.identity)), original_partial)

    def test_bad_parent_rng_context_or_missing_runtime_receipt_never_adopts(self):
        for change in ("parent", "rng", "context", "effect", "runtime"):
            with self.subTest(change=change):
                self.store = MemoryBlobStore()
                payload = copy.deepcopy(self.partial)
                if change == "parent": payload["parent"] = "wrong-parent"
                elif change == "rng": payload["rngState"]["state"]["state"] += 1
                elif change == "context": payload["context"]["pointFit"]["effect"][0] += 1
                elif change == "effect": payload["effects"][0][0] = float("inf")
                if change == "effect":
                    # Canonical stores reject non-finite numbers before load.
                    self.assertFalse(cache.validated_bootstrap_checkpoint(self.data, self.legacy_identity, payload))
                    continue
                self.install("bootstrap", payload)
                before = copy.deepcopy(self.store.values)
                self.assertFalse(cache.adopt_reviewed_retained_components(self.store, self.boards,
                    legacy_producer_identity=None if change == "runtime" else self.legacy_producer))
                self.assertEqual(self.store.values, before)

    def test_policy_kernel_and_unknown_producer_mismatch_never_adopts(self):
        self.install("bootstrap", self.partial)
        for changed in (dict(score_target=990000), dict(seed=7), dict(bootstrap_samples=199),
                        dict(regularization_grid=(.05,))):
            self.assertFalse(cache.adopt_reviewed_retained_components(self.store, self.boards,
                legacy_producer_identity=self.legacy_producer, **changed))
        with patch("numeric_recipe.legacy_component_code", return_value=None):
            self.assertFalse(cache.adopt_reviewed_retained_components(self.store, self.boards,
                legacy_producer_identity=self.legacy_producer))

    def test_inventory_is_sanitized_read_only_and_validates_pinned_inputs(self):
        self.install("bootstrap", self.partial)
        generation, state = self.generation()
        before = copy.deepcopy((self.store.values, self.store.binary_values))
        with patch.object(model, "_fit", side_effect=AssertionError("Inventory ran optimizer")), \
                patch.object(self.store, "put_json", side_effect=AssertionError("Inventory wrote data")):
            summary, artifacts = tiers.inventory_retained_fits(self.store, generation)
        self.assertEqual(len(summary["cohorts"]), 4)
        self.assertTrue(all(item["bootstrapCompleted"] == 10 and item["adoptable"]
                            and item["remainingBootstrapAttempts"] == 190 for item in summary["cohorts"]))
        self.assertTrue(artifacts)
        self.assertNotIn("path", json.dumps(summary))
        self.assertNotIn("rngState", json.dumps(summary))
        self.assertEqual((self.store.values, self.store.binary_values), before)
        state["preparedFitInputs"][-1]["identity"]["seed"] += 1
        self.store.put_json(tiers.PREFIX + "generations/" + generation + "/state.json", state)
        with self.assertRaisesRegex(ValueError, "provenance differs"):
            tiers.inventory_retained_fits(self.store, generation)
