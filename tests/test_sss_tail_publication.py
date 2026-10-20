"""Terminal SSS overlay publication, resumability and compact contract checks."""
from __future__ import annotations

import copy
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

from analysis_runtime import MemoryBlobStore
import production_tiers as tiers
from scoring_goal_tiers import apply_goal_artifacts
from sss_tail_tiers import apply_sss_tail_tiers, recover_sss_tail_tiers
from level20_scoring_width import recover_level20_scoring_width
from tests.test_scoring_goal_publication import production_fixture
from tests.test_scoring_goal_tiers import INPUTS, artifact_fixture
from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
from tier_public import _without_baselines, detail_shard_path, prepare_tier_publication


class SssTailPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = production_fixture()
        cls.artifacts = {goal: artifact_fixture(cls.base, goal) for goal in ("ss", "ssplus")}
        cls.expanded = apply_goal_artifacts(cls.base, cls.artifacts, expected_input_identity=INPUTS)
        cls.policy_hash = cls.base["summary"]["method"]["sourceAwareTiers"]["sourcePolicy"]["effectivePolicySha256"]

    def publication_context(self):
        stack = ExitStack()
        stack.enter_context(patch("chart_source_exclusions.load_exclusions",
            return_value=SimpleNamespace(semantic_hash=lambda: self.policy_hash)))
        # The historical synthetic fixture uses an opaque Co-op sentinel.
        stack.enter_context(patch("tier_public.MODES", ("singles", "doubles")))
        return stack

    def public(self, full):
        return tiers.public_tiers(full, private_path="analysis/private/sss-tail-test.json.gz",
                                  input_identity=INPUTS, computation_identity=INPUTS)

    def test_both_predecessors_validate_and_publish_matching_goal_details_without_history(self):
        for base in (self.base, self.expanded):
            with self.subTest(predecessor=base["schemaVersion"]), self.publication_context():
                full = apply_sss_tail_tiers(base)
                self.assertEqual(recover_sss_tail_tiers(full), base)
                public = self.public(full)
                self.assertEqual(public["summary"]["scriptVersion"], tiers.TAIL_VERSION)
                compact, shards = prepare_tier_publication(public, "sss-tail-"+str(base["schemaVersion"]))
                tiers.validate_public_tiers(compact)
            self.assertEqual(compact["schemaVersion"], 59)
            self.assertEqual(compact["summary"]["method"]["sssTailPolicy"]["baseSchemaVersion"], base["schemaVersion"])
            self.assertNotIn("Baseline", str(public))
            self.assertEqual(set(compact["doubles"][0]["scoringVariants"]), set(base["doubles"][0]["scoringVariants"]))
            for key in ("singles", "doubles"):
                for original, row in zip(base[key], compact[key]):
                    detail = shards[detail_shard_path(compact["tierListView"]["detailsPrefix"], row["chartId"])]["charts"][row["chartId"]]
                    self.assertEqual(detail["tierMetrics"]["clearing"], original["tierMetrics"]["clearing"])
                    for goal in original["scoringVariants"]:
                        for component in ("scoringVariants", "pumbilityVariants"):
                            self.assertEqual(row[component][goal]["estimatedDifficulty"], detail[component][goal]["estimatedDifficulty"])
                            if goal != "sss":
                                self.assertEqual(detail[component][goal], _without_baselines(original[component][goal]))
                    self.assertEqual(set(row["scoringVariants"]["sss"]["scoringTailPolicy"]),
                        {"preTailDifficulty", "preTailDifficultyCi95Low", "preTailDifficultyCi95High"})

    def test_compact_replay_rejects_modified_policy_inputs_counts_and_selection(self):
        with self.publication_context():
            compact, _ = prepare_tier_publication(self.public(apply_sss_tail_tiers(self.expanded)), "tamper-test")
            for change in ("input", "counts", "selection", "parameters"):
                with self.subTest(change=change):
                    bad = copy.deepcopy(compact)
                    policy = bad["summary"]["method"]["sssTailPolicy"]["tailPolicy"]
                    if change == "input":
                        bad["doubles"][0]["scoringVariants"]["sss"]["scoringTailPolicy"]["preTailDifficulty"] += .1
                    elif change == "counts":
                        policy["counts"]["ratedCharts"] += 1
                    elif change == "selection":
                        policy["selectedChartIds"] = ["unknown-chart"]
                    else:
                        policy["ordinaryAsymptote"] = 4
                    bad["summary"]["method"]["productionTierPublication"]["publicPayloadSha256"] = tiers.public_digest(bad)
                    with self.assertRaisesRegex(ValueError, "public SSS"):
                        tiers.validate_public_tiers(bad)

    def test_overlay_upload_interruption_resumes_saved_goal_layer_without_double_taper(self):
        store = MemoryBlobStore()
        predecessor = write_checkpoint(store, self.base, stage="submitted-calibration", identity="synthetic-base")
        state = {"generation": tiers.digest({"synthetic": "sss-tail-resume"}), "stages": [predecessor],
                 "goalInputIdentity": INPUTS, "goalArtifacts": {}}
        for goal, artifact in self.artifacts.items():
            state["goalArtifacts"][goal] = write_checkpoint(store, artifact, stage="scoring-goal", identity=goal)
        real_writer = write_checkpoint
        failed = False

        def interrupted_writer(*args, **kwargs):
            nonlocal failed
            if kwargs["stage"] == "sss-tail" and not failed:
                failed = True
                raise TimeoutError("synthetic tail upload interruption")
            return real_writer(*args, **kwargs)

        with patch("scoring_goal_tiers.apply_goal_artifacts", wraps=apply_goal_artifacts) as attach, \
             patch("sss_tail_tiers.apply_sss_tail_tiers", wraps=apply_sss_tail_tiers) as taper, \
             patch("tier_pipeline_checkpoints.write_checkpoint", side_effect=interrupted_writer):
            with self.assertRaises(TimeoutError):
                tiers._publication_overlays(store, state, predecessor)
            persisted = store.get_json(tiers.PREFIX+"generations/"+state["generation"]+"/state.json")
            self.assertIn("goalOverlay", persisted)
            self.assertNotIn("tailOverlay", persisted)
            reference = tiers._publication_overlays(store, persisted, predecessor)
            self.assertEqual(attach.call_count, 1)
            self.assertEqual(taper.call_count, 2)
            self.assertEqual(tiers._publication_overlays(store, persisted, predecessor), reference)
            self.assertEqual(taper.call_count, 2)
        full = read_checkpoint(store, reference, stage="level20-width", identity=reference["identity"])
        self.assertEqual(recover_level20_scoring_width(full), apply_sss_tail_tiers(self.expanded))
        self.assertEqual(reference["predecessorSha256"], persisted["tailOverlay"]["payloadSha256"])
        persisted["tailOverlay"]["predecessorSha256"] = "0"*64
        with self.assertRaisesRegex(ValueError, "SSS tail overlay checkpoint chain"):
            tiers._publication_overlays(store, persisted, predecessor)

    def test_goal_inputs_and_result_are_released_before_loading_the_tail_predecessor(self):
        class TrackedPayload(dict):
            pass

        store = MemoryBlobStore()
        predecessor = write_checkpoint(store, self.base, stage="submitted-calibration", identity="synthetic-base")
        state = {"generation": tiers.digest({"synthetic": "sss-tail-memory"}), "stages": [predecessor],
                 "goalInputIdentity": INPUTS, "goalArtifacts": {}}
        for goal, artifact in self.artifacts.items():
            state["goalArtifacts"][goal] = write_checkpoint(store, artifact, stage="scoring-goal", identity=goal)
        goal_inputs, goal_results = [], []
        tail_predecessor_loads = 0

        def tracked_reader(*args, **kwargs):
            nonlocal tail_predecessor_loads
            if kwargs.get("stage") == "goal-overlays":
                tail_predecessor_loads += 1
                self.assertEqual(len(goal_results), 1)
                self.assertTrue(all(reference() is None for reference in goal_results),
                                "The prior full schema-58 tree is still retained during its reload.")
                self.assertTrue(all(reference() is None for reference in goal_inputs),
                                "Decoded SS/SS+ inputs are still retained during schema-58 reload.")
            value = read_checkpoint(*args, **kwargs)
            if kwargs.get("stage") == "scoring-goal":
                value = TrackedPayload(value)
                goal_inputs.append(weakref.ref(value))
            return value

        def tracked_attach(*args, **kwargs):
            value = TrackedPayload(apply_goal_artifacts(*args, **kwargs))
            goal_results.append(weakref.ref(value))
            return value

        # Plain wrappers avoid Mock.call_args retaining the payloads themselves.
        with patch("tier_pipeline_checkpoints.read_checkpoint", new=tracked_reader), \
             patch("scoring_goal_tiers.apply_goal_artifacts", new=tracked_attach):
            reference = tiers._publication_overlays(store, state, predecessor)
        self.assertEqual(tail_predecessor_loads, 1)
        self.assertEqual(len(goal_inputs), 2)
        self.assertTrue(all(reference() is None for reference in goal_inputs + goal_results))
        full = read_checkpoint(store, reference, stage="level20-width", identity=reference["identity"])
        self.assertEqual(recover_level20_scoring_width(full), apply_sss_tail_tiers(self.expanded))
        self.assertEqual(reference["predecessorSha256"], state["tailOverlay"]["payloadSha256"])


if __name__ == "__main__":
    unittest.main()
