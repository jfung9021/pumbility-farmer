"""Early completed lookup keeps observed provenance and all real dependencies."""
import copy
from unittest import TestCase
from unittest.mock import patch

import production_tiers as tiers
import tests.test_tier_computation_cache as cache_tests
from tier_computation_cache import index_path


class RetainedComputationCacheTests(TestCase):
    first_time = cache_tests.CrossRunTierCacheTests.first_time
    second_time = cache_tests.CrossRunTierCacheTests.second_time
    finish = staticmethod(cache_tests.CrossRunTierCacheTests.finish)
    public = staticmethod(cache_tests.CrossRunTierCacheTests.public)
    complete = cache_tests.CrossRunTierCacheTests.complete

    def setUp(self):
        cache_tests.CrossRunTierCacheTests.setUp(self)
        self.arguments["official_scoring_manifest"].update(fullEnvelope="old-check", numeric="same-observations")
        self.stack.enter_context(patch("production_tiers._retained_dependencies", side_effect=lambda first, second, manifest, records:
            (self.context[2], self.context[3], manifest["numeric"])))
        self.stack.enter_context(patch("official_scoring_sources.canonical_scoring_manifest_sha256",
            side_effect=lambda manifest: tiers.digest({key: value for key, value in manifest.items() if key != "_captures"})))
        self.stack.enter_context(patch("level20_scoring_width.apply_retained_level20_scoring_width",
            side_effect=lambda full: {**full, "schemaVersion": 61}))
        self.stack.enter_context(patch("production_tiers.validate_public_tiers"))

    def advance(self, created, first=None, progress=None):
        return tiers.build_retained_hosted_tiers([], {}, first or {}, {}, store=self.store,
            arguments=self.arguments, generated_at_utc=created, on_progress=progress)

    def test_completed_check_only_reuse_precedes_context_with_truthful_new_envelope(self):
        old = self.complete(self.first_time)
        binaries = copy.deepcopy(self.store.binary_values)
        old_publication = old["summary"]["method"]["productionTierPublication"]
        self.arguments["official_scoring_manifest"]["fullEnvelope"] = "new-check"
        progress = []
        with patch.object(tiers, "load_context", side_effect=AssertionError("Prepared rows rebuilt before completed lookup")):
            current = self.advance(self.second_time, progress=progress.append)
        publication = current["summary"]["method"]["productionTierPublication"]
        self.assertNotEqual(publication["inputs"]["officialManifest"], old_publication["inputs"]["officialManifest"])
        self.assertEqual(publication["inputs"]["officialSourceNumeric"], "same-observations")
        self.assertEqual(publication["computedInputs"], old_publication["inputs"])
        self.assertEqual(publication["computedAtUtc"], self.first_time)
        self.assertEqual(current["generatedAtUtc"], self.second_time)
        self.assertEqual(publication["fullArtifactSha256"], old_publication["fullArtifactSha256"])
        self.assertEqual(publication["computationIdentity"], old_publication["computationIdentity"])
        self.assertEqual(self.store.binary_values, binaries)
        self.general.assert_called_once()
        self.assertEqual(self.transition.call_count, 7)
        self.fitter.assert_not_called()
        self.assertTrue(progress)
        self.assertTrue(all(item["producer"] == tiers.RETAINED_PRODUCER for item in progress))
        self.assertEqual(progress[-1]["ordinal"], 14)

    def test_changed_official_observations_recompute_general_and_pipeline(self):
        self.complete(self.first_time)
        self.arguments["official_scoring_manifest"]["numeric"] = "changed-actual-official-scores"
        self.complete(self.second_time)
        self.assertEqual(self.general.call_count, 2)
        self.assertEqual(self.transition.call_count, 14)

    def test_changed_general_inputs_are_retained_in_early_descriptor(self):
        self.complete(self.first_time)
        self.arguments["official_snapshot"] = {"different": "clearing-observations"}
        self.complete(self.second_time)
        self.assertEqual(self.general.call_count, 2)
        self.assertEqual(self.transition.call_count, 14)

    def test_corrupt_optional_preparation_index_rebuilds_without_accepting_bad_state(self):
        old = self.complete(self.first_time)
        old_generation = old["summary"]["method"]["productionTierPublication"]["computedGeneration"]
        state = self.store.get_json(tiers.PREFIX + "generations/" + old_generation + "/state.json")
        self.store.put_json(index_path("preparation", state["preparationDescriptor"]), {"version": 0})
        with patch.object(tiers, "load_context", wraps=tiers.load_context) as context:
            current = self.complete(self.second_time)
        self.assertEqual(current["generatedAtUtc"], self.second_time)
        self.assertGreater(context.call_count, 0)

    def test_source_validation_failure_precedes_any_cached_result(self):
        self.complete(self.first_time)
        with patch.object(tiers, "_retained_dependencies", side_effect=ValueError("Captured source hash differs")), \
                patch.object(tiers, "load_context", side_effect=AssertionError("Invalid source prepared")):
            with self.assertRaisesRegex(ValueError, "source hash differs"):
                self.advance(self.second_time)

    def test_resume_callbacks_keep_retained_producer_marker(self):
        for _ in range(8):
            with self.assertRaises(tiers.TierAnalysisPending) as pending:
                self.advance(self.first_time)
        progress = []
        with self.assertRaises(tiers.TierAnalysisPending):
            tiers.resume_tier_generation(self.store, pending.exception.generation,
                generated_at_utc=self.first_time, on_progress=progress.append)
        self.assertTrue(progress)
        self.assertTrue(all(item["producer"] == tiers.RETAINED_PRODUCER for item in progress))
