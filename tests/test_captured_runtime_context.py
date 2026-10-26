"""Real pinned policy/context validation without numerical or upstream work."""
import copy
import unittest
from contextlib import ExitStack
from dataclasses import asdict
from unittest.mock import patch

import analysis_runtime as runtime
from canonical_json import canonical_json_sha256 as digest
from captured_run_runtime import captured_run_recovery_context, validate_captured_context
from chart_source_exclusions import ExclusionPolicy, load_exclusions
from mix_registry import resolve_mix
from piu_misgrade_analyzer import AnalysisConfig
from production_tiers import PREFIX, numerical_pipeline_recipe
from tier_pipeline_checkpoints import write_checkpoint


class CapturedRuntimeContextTests(unittest.TestCase):
    def setUp(self):
        self.store = runtime.MemoryBlobStore()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.job_id = "analysis-real-captured-context"
        cid = "22ee894e-8601-4b55-9d04-c31187f8fa22"
        self.catalog = {cid: {"chartId": cid, "type": "Double", "level": 26, "noteCount": 1000}}
        self.policy = load_exclusions(list(self.catalog.values())).public()
        self.arguments = {"official_snapshot": None, "cross_level_clearing": True,
            "midpoint_compression": True, "hybrid_lower_tiers": True, "average_pumbility": True}
        identity = {"generalArguments": digest(self.arguments), "pipelineCode": "historical-producer"}
        self.generation = digest(identity)
        references = {}
        for name, value in (("model", {"prepared": {"catalog": self.catalog}}),
                            ("provenance", {"policy": self.policy})):
            references[name] = write_checkpoint(self.store, value, stage="phase-inputs-" + name,
                identity=digest({"generation": self.generation, "kind": name}))
        self.store.put_json(PREFIX + "generations/" + self.generation + "/state.json",
            {"generation": self.generation, "identity": identity, "phaseInputs": references})
        self.guard = {"code": "historical-model-code", "flags": {"PIU_CROSS_LEVEL_CLEARING": "1"},
                      "sources": {"analysis/private/source-revisions/phoenix1.json": {"sha256": "a" * 64, "revision": "42"}}}
        self.live_guard = copy.deepcopy(self.guard)
        self.live_guard["code"] = "new-qualified-model-code"
        snapshot = {"pathname": runtime.typed_checkpoint_snapshot_path(self.job_id), "sha256": "b" * 64}
        path = runtime._combined_preparation_path(self.job_id, resolve_mix("phoenix2"))
        prepared = {"version": 1, "jobId": self.job_id, "mix": "phoenix2", "snapshotReference": snapshot,
                    "sourceGuard": self.guard}
        self.store.put_json(path, prepared)
        self.checkpoint = {"jobId": self.job_id, "mix": "phoenix2", "snapshot": snapshot,
            "config": asdict(AnalysisConfig()), "combinedPreparation": {"pathname": path, "sha256": digest(prepared)},
            "tierProgress": {"version": 1, "generation": self.generation, "ordinal": 0, "stage": "general"}}
        self.recipe = numerical_pipeline_recipe(True)
        self.stack.enter_context(patch("analysis_runtime._combined_preparation_guard", side_effect=lambda store: copy.deepcopy(self.live_guard)))
        self.stack.enter_context(patch("analysis_runtime._official_tier_arguments", side_effect=lambda store: copy.deepcopy(self.arguments)))
        self.stack.enter_context(patch("production_tiers.numerical_pipeline_recipe", side_effect=lambda retained=False: copy.deepcopy(self.recipe)))
        self.inventory_summary = {"version": 1, "generation": self.generation, "producer": "retained-goals-v1", "cohorts": []}
        self.inventory = self.stack.enter_context(patch("production_tiers.inventory_retained_fits",
            return_value=(self.inventory_summary, [{"kind": "main", "path": "analysis/private/private-fit"}])))
        for target in ("analysis_runtime.PiuScoresClient", "analysis_runtime.analyze_snapshot",
                       "sss_scoring_model.fit_target_model", "target_fit_cache.load_or_fit_target"):
            self.stack.enter_context(patch(target, side_effect=AssertionError("Context validation must not fetch or fit")))

    def context(self, checkpoint=None):
        return captured_run_recovery_context(self.store, checkpoint or self.checkpoint)

    def test_real_historical_policy_catalog_arguments_and_inventory_are_preserved(self):
        context = self.context()
        self.assertEqual(context["catalog"], self.catalog)
        self.assertEqual(context["sourcePolicy"], self.policy)
        self.assertEqual(context["arguments"], self.arguments)
        self.assertEqual(context["sourceGuard"], self.guard)
        self.assertEqual(context["producerRecipe"], self.recipe)
        self.assertEqual(context["fitInventory"], self.inventory_summary)
        self.assertNotIn("analysis/private/private-fit", str(context))
        self.inventory.assert_called_once_with(self.store, self.generation)
        validate_captured_context(self.store, context)

    def test_changed_live_source_or_enabled_policy_is_rejected_before_inventory(self):
        for section in ("flags", "sources"):
            with self.subTest(section=section):
                before = copy.deepcopy(self.live_guard)
                self.live_guard[section] = {"changed": "value"}
                with self.assertRaisesRegex(ValueError, "source revisions|enabled policy"):
                    self.context()
                self.inventory.assert_not_called()
                self.live_guard = before

    def test_changed_general_configuration_is_rejected_before_inventory(self):
        self.arguments["average_pumbility"] = False
        with self.assertRaisesRegex(ValueError, "General policy arguments"):
            self.context()
        self.inventory.assert_not_called()

    def test_changed_recipe_or_real_exclusion_policy_is_rejected(self):
        context = self.context()
        self.recipe = {**self.recipe, "changed": "producer"}
        with self.assertRaisesRegex(ValueError, "producer changed"):
            validate_captured_context(self.store, context)
        self.recipe = copy.deepcopy(context["producerRecipe"])
        alternate = ExclusionPolicy({"version": 1, "rules": []}, list(self.catalog.values()))
        with patch("chart_source_exclusions.load_exclusions", return_value=alternate):
            with self.assertRaisesRegex(ValueError, "exclusion policy"):
                validate_captured_context(self.store, context)

    def test_corrupt_pinned_phase_or_preparation_is_rejected(self):
        path = runtime._combined_preparation_path(self.job_id, resolve_mix("phoenix2"))
        prepared = self.store.get_json(path)
        self.store.put_json(path, {**prepared, "unexpected": True})
        with self.assertRaisesRegex(ValueError, "preparation failed"):
            self.context()
        self.store.put_json(path, prepared)
        state = self.store.get_json(PREFIX + "generations/" + self.generation + "/state.json")
        binary = state["phaseInputs"]["model"]["path"]
        self.store.binary_values[binary] += b"corrupt"
        with self.assertRaises(ValueError):
            self.context()
        self.inventory.assert_not_called()

    def test_mismatched_base_analysis_mix_is_rejected_before_inventory(self):
        checkpoint = copy.deepcopy(self.checkpoint)
        checkpoint["config"]["mix"] = "phoenix1"
        with self.assertRaises(ValueError):
            self.context(checkpoint)
        self.inventory.assert_not_called()

    def test_context_argument_tamper_is_rejected_after_export(self):
        context = self.context()
        context["arguments"]["average_pumbility"] = False
        with self.assertRaises(ValueError):
            validate_captured_context(self.store, context)


if __name__ == "__main__":
    unittest.main()
