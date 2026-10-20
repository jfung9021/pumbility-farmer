"""Optional goal lookup reports bounded outcomes without private identities."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from analysis_runtime import MemoryBlobStore
import production_tiers as tiers
from scoring_targets import get_scoring_target
from tier_pipeline_checkpoints import write_checkpoint


class GoalLookupDiagnosticsTests(unittest.TestCase):
    def test_lookup_reasons_are_distinct_and_never_log_private_payloads(self):
        inputs = {"version": 1, "privateCanary": "synthetic-do-not-log-input"}
        recipe = {"version": 1, "goal": "ss", "privateCanary": "synthetic-do-not-log-recipe"}
        cases = (("absent", "absent-index"), ("malformed-index", "invalid-checkpoint"),
                 ("identity", "identity-mismatch"), ("recipe", "recipe-mismatch"),
                 ("missing-reference", "invalid-checkpoint"), ("null-reference", "invalid-checkpoint"),
                 ("corrupt-bytes", "invalid-checkpoint"), ("artifact-identity", "identity-mismatch"),
                 ("artifact-target", "invalid-checkpoint"), ("hit", "hit"))
        with patch("production_tiers._goal_recipe", side_effect=lambda goal: {**recipe, "goal": goal}):
            for case, expected in cases:
                with self.subTest(case=case):
                    store = MemoryBlobStore()
                    artifact = {"schemaVersion": 1, "inputIdentity": copy.deepcopy(inputs),
                                "targetConfig": get_scoring_target("ss").public()}
                    if case == "artifact-identity":
                        artifact["inputIdentity"] = {"privateCanary": "synthetic-do-not-log-stale-input"}
                    if case == "artifact-target":
                        artifact["targetConfig"] = get_scoring_target("ssplus").public()
                    ref = write_checkpoint(store, artifact, stage="scoring-goal",
                        identity=tiers.digest({"inputs": inputs, "recipe": recipe}))
                    index = {"inputIdentity": copy.deepcopy(inputs), "recipe": copy.deepcopy(recipe),
                             "reference": ref}
                    if case == "malformed-index":
                        index = ["synthetic-do-not-log-index"]
                    elif case == "identity":
                        index["inputIdentity"] = {"privateCanary": "synthetic-do-not-log-stale-input"}
                    elif case == "recipe":
                        index["recipe"] = {"privateCanary": "synthetic-do-not-log-stale-recipe"}
                    elif case == "missing-reference":
                        del index["reference"]
                    elif case == "null-reference":
                        index["reference"] = None
                    elif case == "corrupt-bytes":
                        store.binary_values[ref["path"]] = b"synthetic-do-not-log-corrupt-checkpoint"
                    if case != "absent":
                        store.values[tiers._goal_index_path(inputs, "ss")] = index
                    with patch("tier_diagnostics.event") as diagnostic:
                        matched = tiers.matching_goal_artifacts(store, inputs)
                    self.assertEqual(matched, {"ss": ref} if case == "hit" else {})
                    self.assertEqual(len(diagnostic.call_args_list), 2)
                    for call in diagnostic.call_args_list:
                        self.assertEqual(call.args, ("scoring_goal_artifact_lookup",))
                        self.assertEqual(set(call.kwargs), {"stage", "goal", "outcome"})
                        self.assertEqual(call.kwargs["stage"], "scoring-goal")
                        self.assertNotIn("synthetic-do-not-log", repr(call))
                    self.assertEqual(diagnostic.call_args_list[0].kwargs,
                        {"stage": "scoring-goal", "goal": "ss", "outcome": expected})
                    self.assertEqual(diagnostic.call_args_list[1].kwargs,
                        {"stage": "scoring-goal", "goal": "ssplus", "outcome": "absent-index"})

    def test_storage_service_failure_is_not_hidden_as_an_optional_cache_miss(self):
        store = MemoryBlobStore()
        with patch("production_tiers._goal_recipe", return_value={}), \
             patch.object(store, "get_json", side_effect=RuntimeError("synthetic service failure")), \
             patch("tier_diagnostics.event") as diagnostic:
            with self.assertRaisesRegex(RuntimeError, "synthetic service failure"):
                tiers.matching_goal_artifacts(store, {})
            diagnostic.assert_not_called()


if __name__ == "__main__":
    unittest.main()
