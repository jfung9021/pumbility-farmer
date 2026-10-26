"""Historical independent-goal continuation and artifact compatibility fixtures."""
from __future__ import annotations

import copy
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from analysis_runtime import (MemoryBlobStore, TYPED_CHECKPOINT_ANALYSIS_PHASE,
    _audit_checkpoint_resume, _database_cursor_token, _reconcile_tier_progress,
    _validated_tier_progress)
import production_tiers as tiers
from tests.test_production_tiers import context_fixture, fit_for
from tests.test_scoring_goal_publication import production_fixture
from tests.test_scoring_goal_tiers import artifact_fixture


class RequiredScoringGoalsTests(unittest.TestCase):
    timestamp = "2026-10-25T00:00:00+00:00"

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Replay the archived five-goal pipeline explicitly. Production defaults
        # no longer schedule these goals; test_retired_tier_goals covers that.
        self.stack.enter_context(patch("production_tiers.REQUIRED_REFRESH_GOALS", ("ss", "ssplus")))
        self.store = MemoryBlobStore()
        self.base = production_fixture()
        self.policy_hash = self.base["summary"]["method"]["sourceAwareTiers"]["sourcePolicy"]["effectivePolicySha256"]
        self.context = list(context_fixture())
        self.context[3] = SimpleNamespace(semantic_hash=lambda: self.policy_hash,
                                         public=lambda: {"policy": self.policy_hash})
        for name, value in (("load_context", self.context), ("pipeline_code_identity", "pipeline"),
                ("model_code_identity", "model"), ("general_code_identity", "general"),
                ("_legacy_general_code_identity", "not-legacy"),
                ("stage_code_identity", "stage-code")):
            self.stack.enter_context(patch("production_tiers." + name, return_value=value))
        self.stack.enter_context(patch("production_tiers._goal_recipe", side_effect=lambda goal: {"goal": goal}))
        self.stack.enter_context(patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"))
        self.stack.enter_context(patch("chart_source_exclusions.load_exclusions",
            return_value=SimpleNamespace(semantic_hash=lambda: self.policy_hash)))
        self.stack.enter_context(patch("scoring_percentile.build_production_tier_payload", return_value={"schemaVersion": 48}))
        self.stack.enter_context(patch("production_tiers._finish_stage", side_effect=self.finish))
        self.stack.enter_context(patch("target_fit_cache.hydrate_point_context"))
        self.builder = self.stack.enter_context(patch("scoring_goal_tiers.build_goal_artifact",
            side_effect=lambda *args, goal, **kwargs: artifact_fixture(self.base, goal)))
        self.fitter = self.stack.enter_context(patch("target_fit_cache.load_or_fit_target",
            side_effect=lambda store, boards, **kwargs: fit_for(boards, kwargs["score_target"])))
        self.stack.enter_context(patch("tier_diagnostics.event"))
        self.stack.enter_context(patch("tier_public.MODES", ("singles", "doubles")))
        for goal in ("sss", "sssplus"):
            for source in ("official", "submitted"):
                for mode, _ in tiers.MODES:
                    boards, _, identity = tiers.cohort(self.context[-1], goal, source, mode)
                    self.store.put_json(tiers.fit_path(identity), {"identity": identity,
                        "fit": fit_for(boards, identity["target"])})

    def finish(self, stage, *args):
        return copy.deepcopy(self.base) if stage == "submitted-calibration" else {
            "schemaVersion": tiers.STAGE_SCHEMAS[stage], "generatedAtUtc": self.timestamp,
            "summary": {"method": {}}}

    def advance(self, created=None, progress=None, first=None):
        return tiers.build_hosted_tiers([], {}, first or {}, {}, store=self.store,
            arguments={"official_scoring_manifest": {"_captures": {"Phoenix2": {}}}},
            generated_at_utc=created or self.timestamp, on_progress=progress)

    def complete(self, created=None, progress=None, first=None):
        for _ in range(65):
            try:
                return self.advance(created, progress, first)
            except tiers.TierAnalysisPending:
                pass
        self.fail("Required goal pipeline failed to finish bounded continuations")

    def test_normal_refresh_builds_both_missing_goals_before_real_public_projection(self):
        progress = []
        public = self.complete(progress=progress.append)
        self.assertEqual(public["summary"]["method"]["availableScoringGoals"],
                         ["general", "ss", "ssplus", "sss", "sssplus"])
        self.assertEqual(public["summary"]["method"]["sssTailPolicy"]["baseSchemaVersion"], 58)
        for key in ("singles", "doubles"):
            for row in public[key]:
                self.assertIn("ss", row["scoringVariants"])
                self.assertIn("ssplus", row["pumbilityVariants"])
        self.assertEqual(self.fitter.call_count, 8)
        self.assertEqual(self.builder.call_count, 2)
        units = [item["goalProgressOrdinal"] for item in progress if item.get("stage") == "scoring-goals"]
        self.assertEqual(sorted(set(units)), list(range(1, 19)))
        self.assertTrue(all(not path.endswith("goal-context.json") for path in self.store.values))

    def test_verified_goal_cache_hits_publish_without_repeating_goal_fits_or_artifacts(self):
        self.complete()
        before = self.fitter.call_count, self.builder.call_count
        progress = []
        public = self.complete("2026-10-25T01:00:00+00:00", progress.append)
        self.assertEqual((self.fitter.call_count, self.builder.call_count), before)
        self.assertFalse(any(item.get("stage") == "scoring-goals" for item in progress))
        self.assertEqual(public["summary"]["method"]["availableScoringGoals"],
                         ["general", "ss", "ssplus", "sss", "sssplus"])

    def test_changed_sources_build_new_goal_artifacts_instead_of_attaching_old_ones(self):
        self.complete()
        before = self.builder.call_count
        public = self.complete("2026-10-25T02:00:00+00:00", first={"scores": [{"score": 981234}]})
        self.assertEqual(self.builder.call_count, before + 2)
        self.assertEqual(public["summary"]["method"]["availableScoringGoals"],
                         ["general", "ss", "ssplus", "sss", "sssplus"])

    def test_goal_continuations_resume_pinned_observations_without_loading_raw_context(self):
        for _ in range(35):
            try:
                self.advance()
            except tiers.TierAnalysisPending as pending:
                if pending.stage == "scoring-goals":
                    generation = pending.generation
                    break
        else:
            self.fail("The refresh never reached required goal preparation")
        fits = self.fitter.call_count
        with patch("production_tiers.load_context", side_effect=AssertionError("Raw context reloaded")):
            for _ in range(22):
                try:
                    public = tiers.resume_tier_generation(self.store, generation,
                        generated_at_utc=self.timestamp)
                    break
                except tiers.TierAnalysisPending:
                    self.assertLessEqual(self.fitter.call_count - fits, 1)
                    fits = self.fitter.call_count
            else:
                self.fail("Pinned goal continuations failed to finish")
        self.assertEqual(self.fitter.call_count, 8)
        self.assertIn("ssplus", public["singles"][0]["scoringVariants"])

    def test_required_goal_progress_advances_stall_guard_and_rejects_regression(self):
        checkpoint = {"phase": TYPED_CHECKPOINT_ANALYSIS_PHASE}
        tokens = set()
        for unit in range(1, 19):
            value = {"version": 1, "generation": "a" * 64,
                "ordinal": 9 + len(tiers.FINISH_STAGES), "stage": "scoring-goals",
                "goalProgressOrdinal": unit}
            checkpoint = _reconcile_tier_progress(checkpoint, value)
            checkpoint = _audit_checkpoint_resume(self.store, "runtime-test.json", checkpoint)
            tokens.add(_database_cursor_token(checkpoint))
            self.assertEqual(checkpoint["resumeAudit"]["observations"], 1)
        self.assertEqual(len(tokens), 18)
        with self.assertRaisesRegex(ValueError, "scoring-goal progress regressed"):
            _reconcile_tier_progress(checkpoint, {**value, "goalProgressOrdinal": 17})
        checkpoint = _audit_checkpoint_resume(self.store, "runtime-test.json", checkpoint)
        with self.assertRaisesRegex(ValueError, "no progress"):
            _audit_checkpoint_resume(self.store, "runtime-test.json", checkpoint)
        for invalid in (0, 19, True, "4"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                _validated_tier_progress({**value, "goalProgressOrdinal": invalid})


if __name__ == "__main__":
    unittest.main()
