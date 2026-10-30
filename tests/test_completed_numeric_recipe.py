from __future__ import annotations

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import numeric_recipe as recipes
import production_tiers as tiers
from tests import test_tier_computation_cache as fixtures


def clear_recipes():
    recipes.general_producer_recipe.cache_clear()
    recipes.finish_producer_recipe.cache_clear()
    tiers.numerical_pipeline_recipe.cache_clear()


class ProducerRecipeTests(TestCase):
    def tearDown(self):
        clear_recipes()

    def changed_source(self, filename, before, after):
        original = Path.read_bytes
        def read(path):
            source = original(path)
            return source.replace(before, after) if path.name == filename else source
        return patch.object(Path, "read_bytes", read)

    def test_public_projection_encoding_and_unrelated_contract_do_not_change_math(self):
        clear_recipes()
        expected = tiers.numerical_pipeline_recipe()
        general = tiers.general_code_identity()
        original = Path.read_bytes
        def read(path):
            value = original(path)
            if path.name == "production_tiers.py":
                value = value.replace(b'publication["publicPayloadSha256"] = public_digest(result)',
                                      b'publication["publicPayloadSha256"] = "new-DTO-format"')
            if path.name == "pumbility_contract.py":
                value = value.replace(b'RECOMMENDATION_SCHEMA_VERSION = 28', b'RECOMMENDATION_SCHEMA_VERSION = 999')
            if path.name in {"tier_public.py", "tier_pipeline_checkpoints.py", "tier_computation_cache.py"}:
                value += b'\nPUBLIC_FORMAT_ONLY = 123\n'
            return value
        with patch.object(Path, "read_bytes", read):
            clear_recipes()
            self.assertEqual(tiers.numerical_pipeline_recipe(), expected)
            self.assertEqual(tiers.general_code_identity(), general)
        with self.changed_source("scoring_targets.py", b'ScoringTarget("ss", "SS", 980000',
                                 b'ScoringTarget("ss", "SS", 980001'):
            clear_recipes()
            self.assertEqual(tiers.numerical_pipeline_recipe(), expected)

    def test_stage_policy_and_kernel_changes_invalidate_their_numeric_chain(self):
        clear_recipes()
        baseline = tiers.numerical_pipeline_recipe()
        with self.changed_source("submitted_sssplus_calibration.py", b'"initialResidualMultiplier": .8',
                                 b'"initialResidualMultiplier": .7'):
            clear_recipes()
            changed = tiers.numerical_pipeline_recipe()
            self.assertEqual(changed["general"], baseline["general"])
            for stage in tiers.FINISH_STAGES[:-1]:
                self.assertEqual(changed["stages"][stage], baseline["stages"][stage])
            self.assertNotEqual(changed["stages"]["submitted-calibration"], baseline["stages"]["submitted-calibration"])
        with patch.object(recipes, "target_kernel_recipe", return_value={"different": "math"}):
            clear_recipes()
            self.assertNotEqual(tiers.numerical_pipeline_recipe(), baseline)

    def test_general_clearing_eligibility_is_a_reachable_numeric_dependency(self):
        clear_recipes()
        baseline = tiers.general_code_identity()
        with self.changed_source("pumbility_contract.py", b"CLEARING_TIER_MINIMUM_SCORE_RECORDS = 100",
                                 b"CLEARING_TIER_MINIMUM_SCORE_RECORDS = 101"):
            clear_recipes()
            self.assertNotEqual(tiers.general_code_identity(), baseline)

    def test_pumbility_adjustment_invalidates_only_the_final_retained_numeric_stage(self):
        clear_recipes()
        baseline = tiers.numerical_pipeline_recipe(retained=True)
        with self.changed_source("centered_maximum_pumbility.py", b'record["offset"] *= .5',
                                 b'record["offset"] *= 1.'):
            clear_recipes()
            changed = tiers.numerical_pipeline_recipe(retained=True)
            self.assertEqual(changed["general"], baseline["general"])
            self.assertEqual(changed["kernel"], baseline["kernel"])
            for stage in tiers.RETAINED_FINISH_STAGES[:-1]:
                self.assertEqual(changed["stages"][stage], baseline["stages"][stage])
            self.assertNotEqual(changed["stages"]["centered-maximum"],
                                baseline["stages"]["centered-maximum"])


class CompletedNumericalReuseTests(TestCase):
    def setUp(self):
        self.harness = fixtures.CrossRunTierCacheTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def test_publication_change_reprojects_completed_raw57_with_strict_active_resume(self):
        h = self.harness
        old = h.complete(h.first_time)
        before = old["summary"]["method"]["productionTierPublication"]
        with patch("production_tiers.pipeline_code_identity", return_value="publication-two"), \
             patch("production_tiers.stage_code_identity", side_effect=lambda stage: "public-edit:" + stage):
            with self.assertRaisesRegex(ValueError, "incompatible inputs or code"):
                tiers._captured_computation(h.store, before["computedGeneration"])
            current = h.complete(h.second_time)
            warm = h.advance("2026-10-20T12:00:00+00:00")
            self.assertTrue(warm["summary"]["method"]["productionTierPublication"]["reusedComputation"])
        after = current["summary"]["method"]["productionTierPublication"]
        self.assertEqual(after["fullArtifactSha256"], before["fullArtifactSha256"])
        self.assertEqual(after["computedGeneration"], before["computedGeneration"])
        self.assertEqual(after["computedAtUtc"], h.first_time)
        self.assertEqual(current["generatedAtUtc"], h.second_time)
        self.assertTrue(after["reusedComputation"])
        self.assertEqual(h.transition.call_count, 7)
        h.general.assert_called_once()
        h.fitter.assert_not_called()

    def test_math_change_cannot_adopt_old_completed_recipe(self):
        h = self.harness
        h.complete(h.first_time)
        changed = {**tiers.numerical_pipeline_recipe(), "changed-policy": True}
        with patch("production_tiers.pipeline_code_identity", return_value="new-math"), \
             patch("production_tiers.numerical_pipeline_recipe", return_value=changed):
            h.complete(h.second_time)
        self.assertEqual(h.transition.call_count, 14)
        h.general.assert_called_once()
