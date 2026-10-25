"""Private numerical work yields without changing draws, fits or stall fences."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from analysis_budget import invocation_budget
from analysis_runtime import (MemoryBlobStore, _audit_checkpoint_resume,
    _database_cursor_token, _reconcile_tier_progress, _validated_tier_progress)
import numeric_recipe as recipes
import production_tiers as tiers
import sss_scoring_model as model
import target_fit_cache as cache
from tests.test_target_fit_cache import JsonStore, boards_fixture
from tests import test_required_scoring_goals as required_goals_fixture
from tests import test_analysis_progress as progress_fixture
from tests.test_production_tiers import context_fixture


class TargetFitBudgetTests(unittest.TestCase):
    def setUp(self):
        self.boards = boards_fixture()
        self.kwargs = {"bootstrap_samples": 200, "regularization_grid": (.05,)}
        cache._POINT_CONTEXTS.clear()

    def test_checkpoint_zero_and_ten_yield_then_resume_all_200_with_exact_parity(self):
        expected = cache.load_or_fit_target(JsonStore(), self.boards, **self.kwargs)
        for cost, completed in ((310.0, 0), (50.0, 10)):
            with self.subTest(completed=completed):
                store, clock, calls = JsonStore(), {"now": 0.0, "cost": cost}, []
                original = model._fit

                def timed(*args, **kwargs):
                    result = original(*args, **kwargs)
                    clock["now"] += clock["cost"]
                    calls.append(kwargs.get("multiplicity") is not None)
                    return result

                with patch("analysis_budget.time.monotonic", side_effect=lambda: clock["now"]), \
                        patch.object(model, "_fit", side_effect=timed):
                    with invocation_budget(), self.assertRaises(cache.TargetFitPending) as pending:
                        cache.load_or_fit_target(store, self.boards, **self.kwargs)
                    self.assertEqual(pending.exception.progress["ordinal"], completed)
                    self.assertEqual(cache.pending_fit_progress(store, self.boards, **self.kwargs),
                                     pending.exception.progress)
                    clock["cost"] = 1.0
                    with invocation_budget():
                        actual = cache.load_or_fit_target(store, self.boards, **self.kwargs)
                self.assertEqual(actual, expected)
                self.assertEqual(sum(calls), 200)
                self.assertEqual(len(calls) - sum(calls), 2)

    def test_arbitrary_sample_boundary_yields_before_next_rng_draw(self):
        expected = cache.load_or_fit_target(JsonStore(), self.boards, **self.kwargs)
        store, clock = JsonStore(), {"now": 0.0, "cost": 50.0}
        original = model._fit

        def timed(*args, **kwargs):
            result = original(*args, **kwargs)
            clock["now"] += clock["cost"]
            return result

        with patch("analysis_budget.time.monotonic", side_effect=lambda: clock["now"]), \
                patch.object(model, "_fit", side_effect=timed):
            with invocation_budget(250), self.assertRaises(cache.TargetFitPending) as pending:
                cache.load_or_fit_target(store, self.boards, **self.kwargs)
            # Two initial solves plus exactly three samples use this invocation.
            self.assertEqual(pending.exception.progress["ordinal"], 3)
            self.assertEqual(clock["now"], 250)
            clock["cost"] = 1.0
            with invocation_budget():
                self.assertEqual(cache.load_or_fit_target(store, self.boards, **self.kwargs), expected)

    def test_completed_main_and_source_components_survive_budget_handoff(self):
        kwargs = {**self.kwargs, "source_boards": {"one": self.boards}}
        expected = cache.load_or_fit_target(JsonStore(), self.boards, **kwargs)
        for component, ordinal in (("main", 201), ("source", 202)):
            with self.subTest(component=component):
                store, clock = JsonStore(), {"now": 0.0, "interrupted": False}
                original_put = store.put_json

                def put(path, value):
                    original_put(path, value)
                    if f"/{component}/" in path and not clock["interrupted"]:
                        clock.update(now=601.0, interrupted=True)

                with patch("analysis_budget.time.monotonic", side_effect=lambda: clock["now"]), \
                        patch.object(store, "put_json", side_effect=put), \
                        patch.object(model, "_fit", wraps=model._fit) as fitter:
                    with invocation_budget(), self.assertRaises(cache.TargetFitPending) as pending:
                        cache.load_or_fit_target(store, self.boards, **kwargs)
                    self.assertEqual(pending.exception.progress["ordinal"], ordinal)
                    self.assertEqual(cache.pending_fit_progress(store, self.boards, **kwargs)["ordinal"], ordinal)
                    with invocation_budget():
                        self.assertEqual(cache.load_or_fit_target(store, self.boards, **kwargs), expected)
                    # CV, full fit, all 200 samples, and one source-removal fit.
                    self.assertEqual(fitter.call_count, 203)

    def test_verified_inner_progress_advances_audit_and_real_stalls_still_fail(self):
        store = MemoryBlobStore()
        checkpoint = {"phase": "analysis", "tierProgress": {"version": 1,
            "generation": "a" * 64, "ordinal": 0, "stage": "general",
            "preparationOrdinal": 12}}
        tokens = set()
        for ordinal in (0, 10, 20, 30, 200, 201, 202):
            progress = {"version": 1, "generation": "a" * 64, "ordinal": 0,
                "stage": "target-fit", "preparedContext": True,
                "fitProgress": {"identity": "b" * 64, "ordinal": ordinal}}
            checkpoint = _reconcile_tier_progress(checkpoint, progress)
            checkpoint = _audit_checkpoint_resume(store, "runtime.json", checkpoint)
            tokens.add(_database_cursor_token(checkpoint))
            self.assertEqual(checkpoint["resumeAudit"]["observations"], 1)
        self.assertEqual(len(tokens), 7)
        checkpoint = _audit_checkpoint_resume(store, "runtime.json", checkpoint)
        with self.assertRaisesRegex(ValueError, "no progress"):
            _audit_checkpoint_resume(store, "runtime.json", checkpoint)
        for change in (None, {"identity": "c" * 64, "ordinal": 202},
                       {"identity": "b" * 64, "ordinal": 201}):
            bad = {**progress}
            if change is None:
                bad.pop("fitProgress")
            else:
                bad["fitProgress"] = change
            with self.assertRaisesRegex(ValueError, "inner target-fit progress"):
                _reconcile_tier_progress(checkpoint, bad)

    def test_inner_progress_rejects_stale_parent_and_invalid_counts(self):
        store, clock = JsonStore(), {"now": 0.0}
        put = store.put_json

        def expired_after_zero(path, value):
            put(path, value)
            if "/bootstrap/" in path:
                clock["now"] = 601

        with patch("analysis_budget.time.monotonic", side_effect=lambda: clock["now"]), \
                patch.object(store, "put_json", side_effect=expired_after_zero):
            with invocation_budget(), self.assertRaises(cache.TargetFitPending):
                cache.load_or_fit_target(store, self.boards, **self.kwargs)
        path = next(path for path in store.values if "/bootstrap/" in path)
        original = json.loads(store.values[path])
        for key, value in (("parent", "changed"), ("completed", 201), ("completed", True)):
            broken = copy.deepcopy(original)
            broken["payload"][key] = value
            broken["payloadSha256"] = cache.canonical_json_sha256(broken["payload"])
            store.values[path] = json.dumps(broken)
            with self.assertRaisesRegex(ValueError, "incompatible inputs or policy"):
                cache.pending_fit_progress(store, self.boards, **self.kwargs)
        progress = {"version": 1, "generation": "a" * 64, "ordinal": 0,
                    "stage": "target-fit", "preparedContext": True,
                    "fitProgress": {"identity": "b" * 64, "ordinal": 0}}
        for invalid in (-1, 203, True, "10"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                _validated_tier_progress({**progress,
                    "fitProgress": {"identity": "b" * 64, "ordinal": invalid}})

    def test_budget_orchestration_keeps_exact_kernel_identity_and_unknown_math_changes_do_not(self):
        self.assertEqual(recipes.source_recipe("sss_scoring_model.py"), recipes.BUDGET_TARGET_RECIPE)
        self.assertEqual(recipes.compatible_target_source(), recipes.F395_TARGET_RECIPE)
        with patch.object(recipes, "source_recipe", return_value="unreviewed-change"):
            self.assertEqual(recipes.compatible_target_source(), "unreviewed-change")

    def test_reviewed_deadline_guard_preserves_all_goal_and_finishing_component_recipes(self):
        read = Path.read_bytes
        model_source = read(recipes.ROOT / "sss_scoring_model.py")
        previous = {
            "sss_scoring_model.py": recipes.reviewed_target_dependency_source(model_source),
            "numeric_recipe.py": recipes.reviewed_recipe_dependency_source(read(recipes.ROOT / "numeric_recipe.py"))}
        self.assertEqual(recipes._ast_recipe(previous["sss_scoring_model.py"]), recipes.BATCH_TARGET_RECIPE)

        def snapshot():
            recipes.general_producer_recipe.cache_clear()
            recipes.finish_producer_recipe.cache_clear()
            tiers.numerical_pipeline_recipe.cache_clear()
            return {"model": tiers.model_code_identity(), "general": tiers.general_code_identity(),
                "pipeline": tiers.numerical_pipeline_recipe(),
                "ss": tiers._goal_recipe("ss"), "ssplus": tiers._goal_recipe("ssplus")}

        current = snapshot()
        with patch.object(Path, "read_bytes", new=lambda path: previous.get(path.name, b"")
                          if path.name in previous else read(path)):
            self.assertEqual(snapshot(), current)
        # A numerical edit is outside both exact-source allowlists.
        changed = model_source.replace(b'"maxiter": 400', b'"maxiter": 401')
        self.assertNotEqual(changed, model_source)
        self.assertEqual(recipes.reviewed_target_dependency_source(changed), changed)
        with patch.object(Path, "read_bytes", new=lambda path: changed
                          if path.name == "sss_scoring_model.py" else read(path)):
            self.assertNotEqual(tiers._goal_recipe("ss"), current["ss"])
        snapshot()  # Restore process-local recipe caches for subsequent tests.


class GoalFitManifestRecoveryTests(unittest.TestCase):
    def test_cached_goal_fit_is_reconciled_before_old_stall_audit_without_refitting(self):
        fixture = required_goals_fixture.RequiredScoringGoalsTests(
            "test_normal_refresh_builds_both_missing_goals_before_real_public_projection")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for _ in range(40):
            try:
                fixture.advance()
            except tiers.TierAnalysisPending as pending:
                if pending.stage == "scoring-goals":
                    generation = pending.generation
                    path = tiers.PREFIX + "generations/" + generation + "/state.json"
                    if len(fixture.store.get_json(path)["requiredGoalJobs"]["ss"]["cohorts"]) == 4:
                        break
        else:
            self.fail("Required SS goal never reached its first fit")
        write = fixture.store.put_json

        class PlatformStop(BaseException):
            pass

        def interrupted_manifest(key, value):
            if key == path and len(value.get("requiredGoalJobs", {}).get("ss", {}).get("fits", [])) == 1:
                raise PlatformStop()
            write(key, value)

        with patch.object(fixture.store, "put_json", side_effect=interrupted_manifest), \
                self.assertRaises(PlatformStop):
            fixture.advance()
        fits = fixture.fitter.call_count
        checkpoint = {"phase": "analysis", "tierProgress": {"version": 1,
            "generation": generation, "ordinal": 9 + len(tiers.FINISH_STAGES),
            "stage": "scoring-goals", "goalProgressOrdinal": 4}}
        checkpoint["resumeAudit"] = {"token": _database_cursor_token(checkpoint), "observations": 2}

        def record(value):
            nonlocal checkpoint
            checkpoint = _reconcile_tier_progress(checkpoint, value)
            checkpoint = _audit_checkpoint_resume(fixture.store, "runtime.json", checkpoint)

        with self.assertRaises(tiers.TierAnalysisPending):
            tiers.resume_tier_generation(fixture.store, generation,
                generated_at_utc=fixture.timestamp, on_progress=record)
        self.assertEqual(checkpoint["tierProgress"]["goalProgressOrdinal"], 5)
        self.assertEqual(checkpoint["resumeAudit"]["observations"], 1)
        self.assertEqual(fixture.fitter.call_count, fits)
        self.assertEqual(len(fixture.store.get_json(path)["requiredGoalJobs"]["ss"]["fits"]), 1)


def eligible_context():
    context = list(context_fixture())
    boards = boards_fixture()
    catalog = {cid: {"type": "Single", "level": 20} for cid in boards}
    context[2] = list(catalog.values())
    context[-1] = {"catalog": catalog, **{
        source: {"boards": boards, "sourceBoards": {source: boards}}
        for source in ("official", "submitted")}}
    return context


class InnerFitProducerProgressTests(unittest.TestCase):
    def test_first_target_cohort_resumes_inner_work_five_times_without_false_stall(self):
        fixture = progress_fixture.AnalysisProgressTests(
            "test_all_nested_fits_and_finishing_resumes_reach_publication")
        fixture.setUp()
        actual_loader, actual_model, actual_fit = cache.load_or_fit_target, model.fit_target_model, model._fit
        clock = {"now": 0.0}

        def timed(*args, **kwargs):
            result = actual_fit(*args, **kwargs)
            clock["now"] += 80
            return result

        with fixture.hosted_pipeline(), patch("production_tiers.load_context", return_value=eligible_context()), \
                patch.object(cache, "load_or_fit_target", new=actual_loader), \
                patch.object(model, "fit_target_model", new=actual_model), \
                patch.object(model, "_fit", side_effect=timed), \
                patch("analysis_budget.time.monotonic", side_effect=lambda: clock["now"]):
            fixture.pin_preparation_inputs()
            samples = []
            for _ in range(5):
                with invocation_budget():
                    result = fixture.resume()
                self.assertEqual(result["status"], "running", result.get("error"))
                checkpoint = fixture.blobs.get_json(fixture.path)
                samples.append(checkpoint["tierProgress"]["fitProgress"]["ordinal"])
                self.assertEqual(checkpoint["tierProgress"]["ordinal"], 0)
                self.assertLessEqual(checkpoint["resumeAudit"]["observations"], 2)
        self.assertEqual(samples, [0, 8, 16, 24, 32])

    def test_required_ss_cohort_reports_verified_inner_work_before_resume_audit(self):
        actual_loader, actual_fit = cache.load_or_fit_target, model._fit
        fixture = required_goals_fixture.RequiredScoringGoalsTests(
            "test_normal_refresh_builds_both_missing_goals_before_real_public_projection")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        eligible = eligible_context()
        fixture.context[2], fixture.context[-1] = eligible[2], eligible[-1]
        for _ in range(40):
            try:
                fixture.advance()
            except tiers.TierAnalysisPending as pending:
                if pending.stage == "scoring-goals":
                    generation = pending.generation
                    statepath = tiers.PREFIX + "generations/" + generation + "/state.json"
                    if len(fixture.store.get_json(statepath)["requiredGoalJobs"]["ss"]["cohorts"]) == 4:
                        break
        else:
            self.fail("SS goal preparation never completed")
        checkpoint = {"phase": "analysis", "tierProgress": {"version": 1,
            "generation": generation, "ordinal": 9 + len(tiers.FINISH_STAGES),
            "stage": "scoring-goals", "goalProgressOrdinal": 4}}
        clock, samples = {"now": 0.0}, []

        def timed(*args, **kwargs):
            result = actual_fit(*args, **kwargs)
            clock["now"] += 80
            return result

        with patch.object(cache, "load_or_fit_target", new=actual_loader), \
                patch.object(model, "_fit", side_effect=timed), \
                patch("analysis_budget.time.monotonic", side_effect=lambda: clock["now"]):
            for _ in range(5):
                audited = False

                def record(value):
                    nonlocal checkpoint, audited
                    checkpoint = _reconcile_tier_progress(checkpoint, value)
                    if not audited:
                        checkpoint = _audit_checkpoint_resume(fixture.store, "runtime.json", checkpoint)
                        audited = True

                with invocation_budget(), self.assertRaises(tiers.TierAnalysisPending):
                    tiers.resume_tier_generation(fixture.store, generation,
                        generated_at_utc=fixture.timestamp, on_progress=record)
                samples.append(checkpoint["tierProgress"]["fitProgress"]["ordinal"])
                self.assertEqual(checkpoint["tierProgress"]["goalProgressOrdinal"], 4)
                self.assertLessEqual(checkpoint["resumeAudit"]["observations"], 2)
        self.assertEqual(samples, [0, 8, 16, 24, 32])


if __name__ == "__main__":
    unittest.main()
