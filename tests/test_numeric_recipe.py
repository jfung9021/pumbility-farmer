import copy
import json
import subprocess
import types
import unittest
from unittest.mock import patch

import numpy as np

import numeric_recipe as recipes
import production_tiers as tiers
import sss_scoring_model as model
import target_fit_cache as cache
from analysis_runtime import MemoryBlobStore
from scoring_targets import get_scoring_target
from tests.test_target_fit_cache import boards_fixture, JsonStore
from tests.test_production_tiers import context_fixture, fit_for


class BatchCheckpoint:
    def __init__(self, fail_at=None):
        self.value = None
        self.fail_at = fail_at

    def load(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        self.value = json.loads(json.dumps(value))
        if value["completed"] == self.fail_at:
            self.fail_at = None
            raise OSError("interrupted batch")


class NumericRecipeTests(unittest.TestCase):
    def test_format_registry_and_cache_transport_do_not_invalidate_numerics(self):
        self.assertEqual(recipes._ast_recipe('"doc"\nVALUE = 1 # comment\n'),
                         recipes._ast_recipe('VALUE=1\r\n'))
        self.assertNotEqual(recipes._ast_recipe('VALUE=1'), recipes._ast_recipe('VALUE=2'))
        baseline = tiers.model_code_identity()
        real = recipes.source_recipe
        def changed(name, *args):
            return "changed-nonmath" if name in {"scoring_targets.py", "target_fit_cache.py"} else real(name, *args)
        with patch.object(recipes, "source_recipe", side_effect=changed):
            self.assertEqual(tiers.model_code_identity(), baseline)
        with patch.object(recipes, "source_recipe", return_value="changed-math"):
            self.assertNotEqual(tiers.model_code_identity(), baseline)
            self.assertIsNone(recipes.legacy_outer_identity())

    def test_f395_uninterrupted_and_interrupted_200_bootstrap_are_exact(self):
        # The named producer is the sole migration allowlist entry; compare its
        # original algorithm directly rather than updating numerical fixtures.
        source = subprocess.check_output(["git", "show", "f395b6d:sss_scoring_model.py"])
        original = types.ModuleType("f395_target_reference")
        exec(compile(source, "f395_target_reference.py", "exec"), original.__dict__)
        for players in (31, 32):
            boards = boards_fixture()
            if players == 31:
                for board in boards.values():
                    board.pop("31")
            expected = original.fit_target_model(boards, bootstrap_samples=200, seed=tiers.SEED)
            actual = model.fit_target_model(boards, bootstrap_samples=200, seed=tiers.SEED)
            self.assertEqual(actual, expected)
            checkpoint = BatchCheckpoint(fail_at=20)
            with self.assertRaisesRegex(OSError, "interrupted batch"):
                model.fit_target_model(boards, bootstrap_samples=200, seed=tiers.SEED,
                                       _bootstrap_checkpoint=checkpoint)
            with patch.object(model, "_fit", wraps=model._fit) as remaining:
                resumed = model.fit_target_model(boards, bootstrap_samples=200, seed=tiers.SEED,
                                                 _bootstrap_checkpoint=checkpoint)
            self.assertEqual(remaining.call_count, 180)
            self.assertEqual(resumed, expected)
            self.assertEqual(checkpoint.value["completed"], 200)
            with self.assertRaisesRegex(ValueError, "different inputs or policy"):
                model.fit_target_model(boards, bootstrap_samples=200, seed=tiers.SEED+1,
                                       _bootstrap_checkpoint=checkpoint)

    def test_verified_legacy_fit_and_private_component_are_adopted(self):
        boards = boards_fixture()
        context = {}
        fit = model.fit_target_model(boards, bootstrap_samples=200, seed=tiers.SEED, _private_context=context)
        data = model._prepare(boards, 990000)
        store = JsonStore()
        identity = cache._identity(data, score_target=990000, bootstrap_samples=200,
                                   seed=tiers.SEED, regularization_grid=model.REGULARIZATION_GRID)
        old_component = {**identity, "code": recipes.legacy_component_code()}
        self.assertIsNotNone(old_component["code"])
        cache._write(store, "main", old_component, {"fit": fit, "context": context})
        raw = {"boards": tiers.digest(boards), "sourceBoards": "sources", "target": 990000,
               "samples": 200, "seed": tiers.SEED}
        old_path = tiers.PREFIX + "fits/" + tiers.digest({"inputs": raw, "code": recipes.legacy_outer_identity()}) + ".json"
        store.put_json(old_path, {"identity": raw, "fit": fit})
        with patch.object(model, "fit_target_model", side_effect=AssertionError("Bootstrap rebuilt")):
            adopted = tiers._cached_fit(store, boards, raw)
            reused = cache.load_or_fit_target(store, boards, bootstrap_samples=200, seed=tiers.SEED)
        self.assertEqual(adopted["fit"], fit)
        self.assertEqual(reused, fit)

    def test_training_general_is_shared_across_targets_without_heldout_rows(self):
        from official_player_scoring import fit_cross_level_player_comparisons
        boards = boards_fixture()
        data = model._prepare(boards, 990000)
        heldout = model._holdout(data["p"], data["c"], len(data["players"]), len(data["mainCharts"]), tiers.SEED)
        entries = [(cid, player) for i, (cid, player, _) in enumerate(data["records"]) if heldout[i]]
        training = {cid: {} for cid in boards}
        for i, (cid, player, _) in enumerate(data["records"]):
            if not heldout[i]:
                training[cid][player] = boards[cid][player]
        self.assertTrue(all(player not in training[cid] for cid, player in entries))
        expected = [tiers.training_predictor(target)(training, entries) for target in (980000, 985000, 990000, 995000)]
        store = JsonStore()
        with patch("official_player_scoring.fit_cross_level_player_comparisons", wraps=fit_cross_level_player_comparisons) as fit:
            actual = [tiers.training_predictor(target, store)(training, entries) for target in (980000, 985000, 990000, 995000)]
        self.assertEqual(fit.call_count, 1)
        self.assertEqual(actual, expected)

    def test_optional_training_general_corruption_rebuilds_but_timeout_propagates(self):
        from pumbility_store import PumbilityArtifactIntegrityError
        boards, entries = boards_fixture(), [("0", "0")]
        expected = tiers.training_predictor(980000)(boards, entries)
        store = JsonStore()
        with patch.object(store, "get_json", side_effect=PumbilityArtifactIntegrityError(
                digest_matches=False, byte_size_matches=True)):
            self.assertEqual(tiers.training_predictor(980000, store)(boards, entries), expected)
        with patch.object(store, "get_json", side_effect=TimeoutError("unavailable")):
            with self.assertRaises(TimeoutError):
                tiers.training_predictor(980000, store)(boards, entries)


class PreparedGoalTests(unittest.TestCase):
    def test_prefit_resume_never_reloads_context_and_only_schedules_missing_fits(self):
        context, store = context_fixture(), MemoryBlobStore()
        kwargs = dict(store=store, arguments={"official_scoring_manifest": {"_captures": {"Phoenix2": {}}}},
                      generated_at_utc="2026-10-20T00:00:00Z")
        progress = []
        def fitted(boards, **kw):
            states = [v for k, v in store.values.items() if k.endswith("/state.json")]
            self.assertTrue(any(s.get("preparedFitInputs") and s.get("phaseInputs") for s in states))
            return fit_for(boards, kw["score_target"])
        with patch("production_tiers.load_context", return_value=context), \
             patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"), \
             patch("scoring_percentile.build_production_tier_payload", return_value={"schemaVersion": 48}), \
             patch("sss_scoring_model.fit_target_model", side_effect=fitted) as fit:
            with self.assertRaises(tiers.TierAnalysisPending) as pending:
                tiers.build_hosted_tiers([], {}, {}, {}, on_progress=progress.append, **kwargs)
            self.assertTrue(progress[-1]["preparedContext"])
            with patch("production_tiers.load_context", side_effect=AssertionError("Context rebuilt")):
                with self.assertRaises(tiers.TierAnalysisPending):
                    tiers.resume_tier_generation(store, pending.exception.generation,
                        generated_at_utc=kwargs["generated_at_utc"], on_progress=progress.append)
            self.assertEqual(fit.call_count, 2)

    def test_goal_backfill_is_independent_bounded_and_bound_to_inputs(self):
        context, store = context_fixture(), MemoryBlobStore()
        inputs = tiers.pin_tier_publication_inputs(store, first={}, second={}, manifest={}, records=[], metadata={},
                                                   generated_at_utc="2026-10-20T00:00:00Z")
        target = get_scoring_target("ss")
        artifact = {"schemaVersion": 1, "goal": "ss", "targetConfig": target.public(), "charts": {}, "method": {}}
        with patch("production_tiers.load_context", return_value=context) as prepare, \
             patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"), \
             patch("sss_scoring_model.fit_target_model", side_effect=lambda b, **kw: fit_for(b, kw["score_target"])) as fit, \
             patch("scoring_goal_tiers.build_goal_artifact", return_value=artifact) as builder:
            for count in range(1, 5):
                result = tiers.advance_goal_backfill(store, pinned_inputs_reference=inputs, goal="ss")
                self.assertFalse(result["done"])
                self.assertEqual(result["completedTargetFits"], count)
            result = tiers.advance_goal_backfill(store, pinned_inputs_reference=inputs, goal="ss")
            self.assertTrue(result["done"])
            # The four independent cohort requests contain only ineligible
            # observations here; identical empty numeric cores reuse two solves.
            self.assertEqual(fit.call_count, 2)
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(builder.call_args.args[0][3].semantic_hash(), context[3].semantic_hash())
        self.assertFalse(any(k.endswith("/public.json") for k in store.values))
        self.assertTrue(result["artifactReference"]["sha256"])


if __name__ == "__main__":
    unittest.main()
