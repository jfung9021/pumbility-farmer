import copy
import hashlib
import json
import subprocess
import sys
import types
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory
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


def f395_source_bytes():
    return {name: subprocess.check_output(["git", "show", "f395b6d:" + name])
            for name in ("sss_scoring_model.py", "official_player_scoring.py",
                         "scoring_targets.py", "target_fit_cache.py")}


def original_f395_keys(sources):
    # Reconstruct the old producers independently of the migration helpers.
    # The outer producer hashed raw files; the component producer normalized LF.
    outer = tiers.digest({"sources": {name: hashlib.sha256(value).hexdigest()
                                    for name, value in sources.items()},
        "libraries": {name: metadata.version(name) for name in ("numpy", "scipy", "pandas")},
        "python": list(sys.version_info[:3])})
    component = tiers.digest({"model": hashlib.sha256(
        sources["sss_scoring_model.py"].replace(b"\r\n", b"\n")).hexdigest(),
        "cache": hashlib.sha256(sources["target_fit_cache.py"].replace(b"\r\n", b"\n")).hexdigest(),
        "numpy": metadata.version("numpy"), "scipy": metadata.version("scipy")})
    return outer, component


def known_windows_sources(sources):
    return {name: (value.replace(b"\n", b"\r\n") if name != "target_fit_cache.py" else value)
            for name, value in sources.items()}


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
            self.assertEqual(recipes.legacy_outer_identities(), ())
            self.assertIsNone(recipes.legacy_component_code())

    def test_legacy_keys_match_original_git_and_known_windows_producers(self):
        sources = f395_source_bytes()
        git_outer, component = original_f395_keys(sources)
        windows_outer, windows_component = original_f395_keys(known_windows_sources(sources))
        self.assertEqual(recipes.F395_SOURCES, {name: hashlib.sha256(value).hexdigest()
                                             for name, value in sources.items()})
        self.assertEqual(recipes.F395_WINDOWS_SOURCES,
                         {name: hashlib.sha256(value).hexdigest()
                          for name, value in known_windows_sources(sources).items()})
        self.assertEqual(recipes.legacy_outer_identity(), git_outer)
        self.assertEqual(recipes.legacy_outer_identities(), (git_outer, windows_outer))
        self.assertEqual(recipes.legacy_component_code(), component)
        self.assertEqual(windows_component, component)
        self.assertNotEqual(git_outer, windows_outer)

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
        sources = f395_source_bytes()
        original = types.ModuleType("f395_migration_reference")
        exec(compile(sources["sss_scoring_model.py"], "f395_migration_reference.py", "exec"), original.__dict__)
        boards = boards_fixture()
        context = {}
        fit = original.fit_target_model(boards, bootstrap_samples=200, seed=tiers.SEED, _private_context=context)
        data = original._prepare(boards, 990000)
        binary = {key: data[key] for key in ("charts", "players", "mainCharts", "components",
                                            "componentSizes", "eligiblePlayers", "inputPlayers")}
        binary["allRecords"] = [list(record) for record in data["allRecords"]]
        raw = {"boards": tiers.digest(boards), "sourceBoards": "sources", "target": 990000,
               "samples": 200, "seed": tiers.SEED}
        for variant in (sources, known_windows_sources(sources)):
            with self.subTest(outer=original_f395_keys(variant)[0]):
                store = JsonStore()
                outer_code, component_code = original_f395_keys(variant)
                component_identity = {"version": 1, "code": component_code, "binary": binary,
                    "target": 990000, "samples": 200, "seed": tiers.SEED, "grid": [.01, .05, .2, 1.]}
                component_payload = {"fit": fit, "context": context}
                component_path = cache.PREFIX + "main/" + tiers.digest(component_identity) + ".json"
                store.put_json(component_path, {"version": 1, "identity": component_identity,
                    "payload": component_payload, "payloadSha256": tiers.digest(component_payload)})
                old_path = tiers.PREFIX + "fits/" + tiers.digest({"inputs": raw, "code": outer_code}) + ".json"
                store.put_json(old_path, {"identity": raw, "fit": fit})
                with patch.object(model, "fit_target_model", side_effect=AssertionError("Bootstrap rebuilt")), \
                     patch.object(model, "_fit", side_effect=AssertionError("Numerical fit repeated")), \
                     patch("tier_diagnostics.event") as diagnostics:
                    adopted = tiers._cached_fit(store, boards, raw)
                    reused = cache.load_or_fit_target(store, boards, bootstrap_samples=200, seed=tiers.SEED)
                    self.assertEqual(adopted["fit"], fit)
                    self.assertEqual(reused, fit)
                    with patch.object(recipes, "legacy_outer_identities", side_effect=AssertionError("Legacy lookup repeated")):
                        self.assertEqual(tiers._cached_fit(store, boards, raw)["fit"], fit)
                        self.assertEqual(cache.load_or_fit_target(store, boards, bootstrap_samples=200, seed=tiers.SEED), fit)
                self.assertEqual([call.kwargs for call in diagnostics.call_args_list], [
                    {"stage": "legacy-outer", "cache_hit": True, "outcome": "adopted"},
                    {"stage": "legacy-component", "cache_hit": True, "outcome": "adopted"}])

    def test_legacy_migration_rejects_changed_inputs_seed_and_producer(self):
        boards = boards_fixture()
        raw = {"boards": tiers.digest(boards), "sourceBoards": "sources", "target": 990000,
               "samples": 200, "seed": tiers.SEED}
        outer, _ = original_f395_keys(f395_source_bytes())
        old_path = tiers.PREFIX + "fits/" + tiers.digest({"inputs": raw, "code": outer}) + ".json"
        fit = fit_for(boards, 990000)
        fit["diagnostics"]["validation"]["seed"] = tiers.SEED
        store = JsonStore()
        store.put_json(old_path, {"identity": raw, "fit": fit})
        with patch.object(model, "fit_target_model", side_effect=AssertionError("Unexpected numerical work")), \
             patch("tier_diagnostics.event") as diagnostics:
            self.assertIsNone(tiers._cached_fit(store, boards, {**raw, "boards": "changed"}))
            store.put_json(old_path, {"identity": {**raw, "boards": "changed"}, "fit": fit})
            self.assertIsNone(tiers._cached_fit(store, boards, raw))
            store.put_json(old_path, {"identity": raw, "fit": fit})
            with patch.object(recipes, "source_recipe", return_value="changed-mathematics"):
                self.assertIsNone(tiers._cached_fit(store, boards, raw))
            fit["diagnostics"]["validation"]["seed"] += 1
            store.put_json(old_path, {"identity": raw, "fit": fit})
            self.assertIsNone(tiers._cached_fit(store, boards, raw))
        self.assertIsNone(store.get_json(tiers.fit_path(raw)))
        self.assertEqual([call.kwargs["outcome"] for call in diagnostics.call_args_list],
                         ["not-found", "input-mismatch", "producer-incompatible", "seed-mismatch"])

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


class CanonicalRecipeTests(unittest.TestCase):
    def test_reviewed_transport_retains_original_producer_dependencies(self):
        original = subprocess.check_output(["git", "show", "2c9aa21:canonical_json.py"])
        current = (recipes.ROOT / "canonical_json.py").read_bytes()
        self.assertEqual(recipes._ast_recipe(recipes.CANONICAL_HASH_ORIGINAL_SOURCE),
                         recipes._ast_recipe(original))
        self.assertIn(recipes._ast_recipe(current), recipes.CANONICAL_HASH_RECIPES)
        parent = types.ModuleType("canonical_recipe_parent")
        parent.__file__ = str(recipes.ROOT / "numeric_recipe.py")
        exec(compile(subprocess.check_output(["git", "show", "2c9aa21:numeric_recipe.py"]),
                     "canonical_recipe_parent.py", "exec"), parent.__dict__)
        roots = {"canonical_json.py": {"canonical_json_sha256"}}
        with TemporaryDirectory(prefix="canonical-recipe-") as directory:
            root = Path(directory)
            parent.ROOT = root
            with patch.object(recipes, "ROOT", root):
                (root / "canonical_json.py").write_bytes(original)
                expected = parent.producer_dependencies(roots)
                for source in (original, current, current.replace(b"\n", b"\r\n")):
                    (root / "canonical_json.py").write_bytes(source)
                    self.assertEqual(recipes.producer_dependencies(roots), expected)

    def test_unapproved_transport_changes_invalidate_whole_module(self):
        original = subprocess.check_output(["git", "show", "2c9aa21:canonical_json.py"])
        current = (recipes.ROOT / "canonical_json.py").read_bytes()
        roots = {"canonical_json.py": {"canonical_json_sha256"}}
        with TemporaryDirectory(prefix="canonical-recipe-") as directory:
            root = Path(directory)
            with patch.object(recipes, "ROOT", root):
                (root / "canonical_json.py").write_bytes(original)
                expected = recipes.producer_dependencies(roots)
                for source in (original + b"\nUNREVIEWED = 1\n", current + b"\nUNREVIEWED = 1\n",
                               current.replace(b"allow_nan=False", b"allow_nan=True")):
                    self.assertIsNone(recipes.compatible_canonical_source(source))
                    (root / "canonical_json.py").write_bytes(source)
                    result = recipes.producer_dependencies(roots)
                    self.assertNotEqual(result, expected)
                    self.assertEqual(result["canonical_json.py"]["unapprovedModule"], recipes._ast_recipe(source))


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
            for ordinal in range(1, 13):
                with self.assertRaises(tiers.TierAnalysisPending):
                    tiers.build_hosted_tiers([], {}, {}, {}, on_progress=progress.append, **kwargs)
                self.assertEqual(progress[-1]["preparationOrdinal"], ordinal)
                fit.assert_not_called()
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
