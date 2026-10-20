"""Publication/cache integration with real, reversible synthetic goal artifacts."""
from __future__ import annotations

import copy
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from analysis_runtime import MemoryBlobStore
import production_tiers as tiers
from scoring_goal_tiers import apply_goal_artifacts, validate_goal_tiers
from tests.test_production_tiers import context_fixture, fit_for
from tests.test_scoring_goal_tiers import INPUTS, artifact_fixture
from tests.test_submitted_sssplus_calibration import calibration_fixture
from submitted_sssplus_calibration import apply_submitted_sssplus_calibration
from tier_pipeline_checkpoints import write_checkpoint
from tier_public import detail_shard_path, prepare_tier_publication


def production_fixture():
    predecessor, fitted, identity = calibration_fixture()
    return apply_submitted_sssplus_calibration(predecessor, fitted, fit_identity=identity, local_only=False)


class GoalPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = production_fixture()
        cls.artifacts = {goal: artifact_fixture(cls.base, goal) for goal in ("ss", "ssplus")}
        cls.policy_hash = cls.base["summary"]["method"]["sourceAwareTiers"]["sourcePolicy"]["effectivePolicySha256"]

    def test_real_goal_overlay_survives_public_projection_and_detail_shards(self):
        full = apply_goal_artifacts(self.base, self.artifacts, expected_input_identity=INPUTS)
        # Historical fixtures use an opaque Co-op preservation sentinel. New
        # target publication applies only to the actual Singles/Doubles charts.
        with patch("chart_source_exclusions.load_exclusions", return_value=SimpleNamespace(semantic_hash=lambda: self.policy_hash)), \
             patch("tier_public.MODES", ("singles", "doubles")):
            public = tiers.public_tiers(full, private_path="analysis/private/synthetic-goals.json.gz",
                input_identity=INPUTS, computation_identity=INPUTS)
            self.assertEqual(public["summary"]["scriptVersion"], "6.22.0-scoring-goals")
            validate_goal_tiers(public, full=False)
            compact, shards = prepare_tier_publication(public, "synthetic-goal-generation")
            tiers.validate_public_tiers(compact)
        self.assertEqual(compact["schemaVersion"], 58)
        self.assertEqual(compact["summary"]["method"]["availableScoringGoals"], ["general", "ss", "ssplus", "sss", "sssplus"])
        self.assertNotIn("scoringGoalBackfills", compact["summary"]["method"])
        for key in ("singles", "doubles"):
            chart = compact[key][0]
            detail = shards[detail_shard_path(compact["tierListView"]["detailsPrefix"], chart["chartId"])]["charts"][chart["chartId"]]
            for goal in ("ss", "ssplus"):
                self.assertEqual(chart["scoringVariants"][goal]["estimatedDifficulty"], detail["scoringVariants"][goal]["estimatedDifficulty"])
                self.assertEqual(chart["pumbilityVariants"][goal]["estimatedDifficulty"], detail["pumbilityVariants"][goal]["estimatedDifficulty"])

    def test_goal_availability_transition_and_warm_publication_reuse_legacy_math(self):
        store = MemoryBlobStore()
        context = context_fixture()
        arguments = {"official_scoring_manifest": {"_captures": {"Phoenix2": {}}}}
        first_time = self.base["generatedAtUtc"]
        second_time, third_time = "2026-10-20T01:00:00+00:00", "2026-10-20T02:00:00+00:00"
        for goal in ("sss", "sssplus"):
            for source in ("official", "submitted"):
                for mode, _ in tiers.MODES:
                    boards, _, identity = tiers.cohort(context[-1], goal, source, mode)
                    store.put_json(tiers.fit_path(identity), {"identity": identity, "fit": fit_for(boards, identity["target"])})

        def finish(stage, predecessor, *args):
            if stage == "submitted-calibration":
                return copy.deepcopy(self.base)
            return {"schemaVersion": tiers.STAGE_SCHEMAS[stage], "generatedAtUtc": first_time,
                    "summary": {"method": {}}}

        def complete(created):
            for _ in range(25):
                try:
                    return tiers.build_hosted_tiers([], {}, {}, {}, store=store, arguments=arguments,
                                                    generated_at_utc=created)
                except tiers.TierAnalysisPending:
                    pass
            self.fail("The bounded synthetic publication did not complete.")

        with ExitStack() as stack:
            stack.enter_context(patch("tier_public.MODES", ("singles", "doubles")))
            stack.enter_context(patch("production_tiers.load_context", return_value=context))
            stack.enter_context(patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"))
            stack.enter_context(patch("chart_source_exclusions.load_exclusions", return_value=SimpleNamespace(semantic_hash=lambda: self.policy_hash)))
            general = stack.enter_context(patch("scoring_percentile.build_production_tier_payload", return_value={
                "schemaVersion": 48, "generatedAtUtc": first_time}))
            fitter = stack.enter_context(patch("sss_scoring_model.fit_target_model", side_effect=AssertionError("Unnecessary refit")))
            transition = stack.enter_context(patch("production_tiers._finish_stage", side_effect=finish))
            before = complete(first_time)
            self.assertEqual(before["schemaVersion"], 60)
            self.assertEqual(before["summary"]["method"]["sssTailPolicy"]["baseSchemaVersion"], 57)
            publication = before["summary"]["method"]["productionTierPublication"]
            state = store.get_json(tiers.PREFIX + "generations/" + tiers.digest(publication["inputs"]) + "/state.json")
            goal_inputs = state["goalInputIdentity"]
            # A completed backfill adds immutable cache objects only.
            for goal, original in self.artifacts.items():
                artifact = {**original, "inputIdentity": copy.deepcopy(goal_inputs)}
                recipe = tiers._goal_recipe(goal)
                ref = write_checkpoint(store, artifact, stage="scoring-goal",
                    identity=tiers.digest({"inputs": goal_inputs, "recipe": recipe}))
                store.put_json(tiers._goal_index_path(goal_inputs, goal),
                    {"inputIdentity": goal_inputs, "recipe": recipe, "reference": ref})
            self.assertEqual(before["schemaVersion"], 60)
            current = complete(second_time)
            self.assertEqual(current["schemaVersion"], 60)
            self.assertEqual(current["summary"]["method"]["sssTailPolicy"]["baseSchemaVersion"], 58)
            tiers.validate_public_tiers(current)
            for key in ("singles", "doubles"):
                for previous, attached in zip(before[key], current[key], strict=True):
                    self.assertEqual(previous["chartId"], attached["chartId"])
                    for field in ("scoringVariants", "pumbilityVariants"):
                        for goal in ("general", "sss", "sssplus"):
                            self.assertEqual(previous[field][goal], attached[field][goal])
                    self.assertEqual(previous["tierMetrics"], attached["tierMetrics"])
            self.assertEqual(general.call_count, 1)
            self.assertEqual(transition.call_count, len(tiers.FINISH_STAGES))
            fitter.assert_not_called()
            binaries = copy.deepcopy(store.binary_values)
            warm = complete(third_time)
            tiers.validate_public_tiers(warm)
            self.assertEqual(warm["schemaVersion"], 60)
            self.assertTrue(warm["summary"]["method"]["productionTierPublication"]["reusedComputation"])
            self.assertEqual(warm["summary"]["method"]["productionTierPublication"]["computedAtUtc"], first_time)
            self.assertEqual(warm["tierListView"], current["tierListView"])
            self.assertEqual(store.binary_values, binaries)
            self.assertEqual(transition.call_count, len(tiers.FINISH_STAGES))


if __name__ == "__main__":
    unittest.main()
