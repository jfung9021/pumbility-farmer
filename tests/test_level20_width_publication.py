"""Terminal level-20 width publication, immutable evidence and retry safety."""
from __future__ import annotations

import copy
import unittest
import weakref
from unittest.mock import patch

from analysis_runtime import MemoryBlobStore
import production_tiers as tiers
from level20_scoring_width import (apply_level20_scoring_width,
    recover_level20_scoring_width)
from sss_tail_tiers import apply_sss_tail_tiers
import tests.test_sss_tail_publication as tail_fixtures
from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
from tier_public import detail_shard_path, prepare_tier_publication


class Level20WidthPublicationTests(unittest.TestCase):
    # Reuse the real reversible predecessor fixtures and publication context.
    @classmethod
    def setUpClass(cls):
        tail_fixtures.SssTailPublicationTests.setUpClass()
        cls.base = tail_fixtures.SssTailPublicationTests.base
        cls.expanded = tail_fixtures.SssTailPublicationTests.expanded
        cls.policy_hash = tail_fixtures.SssTailPublicationTests.policy_hash

    publication_context = tail_fixtures.SssTailPublicationTests.publication_context
    public = tail_fixtures.SssTailPublicationTests.public

    def test_schema60_publishes_available_targets_and_matching_details_without_history(self):
        for predecessor in (self.base, self.expanded):
            with self.subTest(base_schema=predecessor["schemaVersion"]), self.publication_context():
                frozen = apply_sss_tail_tiers(predecessor)
                full = apply_level20_scoring_width(frozen)
                self.assertEqual(recover_level20_scoring_width(full), frozen)
                public = self.public(full)
                self.assertEqual(public["summary"]["scriptVersion"], tiers.WIDTH_VERSION)
                compact, shards = prepare_tier_publication(public, "width-"+str(predecessor["schemaVersion"]))
                tiers.validate_public_tiers(compact)
                self.assertEqual(compact["schemaVersion"], 60)
                self.assertNotIn("Baseline", str(public))
                self.assertEqual(set(compact["doubles"][0]["scoringVariants"]),
                                 set(predecessor["doubles"][0]["scoringVariants"]))
                self.assertEqual(compact["summary"]["method"]["level20ScoringWidth"],
                                 full["summary"]["method"]["level20ScoringWidth"])
                for key in ("singles", "doubles"):
                    for row in compact[key]:
                        detail = shards[detail_shard_path(compact["tierListView"]["detailsPrefix"], row["chartId"])]["charts"][row["chartId"]]
                        for component in ("scoringVariants", "pumbilityVariants"):
                            for goal, metric in row[component].items():
                                self.assertEqual(metric["estimatedDifficulty"], detail[component][goal]["estimatedDifficulty"])
                        for goal, metric in row["scoringVariants"].items():
                            if "scoringLevel20Width" in metric:
                                self.assertEqual(metric["scoringLevel20Width"],
                                                 detail["scoringVariants"][goal]["scoringLevel20Width"])

    def test_interrupted_width_upload_reuses_saved_sss_layer_and_applies_width_once(self):
        store = MemoryBlobStore()
        predecessor = write_checkpoint(store, self.expanded, stage="goal-overlays", identity="synthetic-expanded")
        state = {"generation": tiers.digest({"synthetic": "width-resume"}), "stages": [predecessor]}
        real_writer = write_checkpoint
        interrupted = False

        def writer(*args, **kwargs):
            nonlocal interrupted
            if kwargs["stage"] == "level20-width" and not interrupted:
                interrupted = True
                raise TimeoutError("synthetic width upload interruption")
            return real_writer(*args, **kwargs)

        with patch("sss_tail_tiers.apply_sss_tail_tiers", wraps=apply_sss_tail_tiers) as taper, \
             patch("level20_scoring_width.apply_level20_scoring_width", wraps=apply_level20_scoring_width) as width, \
             patch("tier_pipeline_checkpoints.write_checkpoint", side_effect=writer):
            with self.assertRaisesRegex(TimeoutError, "width upload"):
                tiers._publication_overlays(store, state, predecessor)
            persisted = store.get_json(tiers.PREFIX+"generations/"+state["generation"]+"/state.json")
            self.assertIn("tailOverlay", persisted)
            self.assertNotIn("widthOverlay", persisted)
            reference = tiers._publication_overlays(store, persisted, predecessor)
            self.assertEqual(taper.call_count, 1)
            self.assertEqual(width.call_count, 2)
            persisted["archive"] = reference
            self.assertEqual(tiers._publication_overlays(store, persisted, predecessor), reference)
            self.assertEqual(width.call_count, 2)
        self.assertEqual(reference["predecessorSha256"], persisted["tailOverlay"]["payloadSha256"])
        full = read_checkpoint(store, reference, stage="level20-width", identity=reference["identity"])
        self.assertEqual(full, apply_level20_scoring_width(apply_sss_tail_tiers(self.expanded)))
        bad = copy.deepcopy(persisted)
        bad["widthOverlay"]["predecessorSha256"] = "0"*64
        with self.assertRaisesRegex(ValueError, "level-20 width overlay checkpoint chain"):
            tiers._publication_overlays(store, bad, predecessor)
        bad = copy.deepcopy(persisted)
        bad["archive"] = bad["tailOverlay"]
        with self.assertRaisesRegex(ValueError, "final publication overlay"):
            tiers._publication_overlays(store, bad, predecessor)

    def test_tail_result_is_released_before_width_predecessor_is_loaded(self):
        class TrackedPayload(dict):
            pass

        store = MemoryBlobStore()
        predecessor = write_checkpoint(store, self.expanded, stage="goal-overlays", identity="synthetic-expanded")
        state = {"generation": tiers.digest({"synthetic": "width-memory"}), "stages": [predecessor]}
        tail_results, width_loads = [], []

        def reader(*args, **kwargs):
            if kwargs.get("stage") == "sss-tail":
                width_loads.append(True)
                self.assertEqual(len(tail_results), 1)
                self.assertIsNone(tail_results[0](), "Decoded SSS tail audit retained during its reload")
            return read_checkpoint(*args, **kwargs)

        def taper(*args, **kwargs):
            value = TrackedPayload(apply_sss_tail_tiers(*args, **kwargs))
            tail_results.append(weakref.ref(value))
            return value

        with patch("tier_pipeline_checkpoints.read_checkpoint", new=reader), \
             patch("sss_tail_tiers.apply_sss_tail_tiers", new=taper):
            tiers._publication_overlays(store, state, predecessor)
        self.assertEqual(len(width_loads), 1)
        self.assertTrue(all(reference() is None for reference in tail_results))


if __name__ == "__main__":
    unittest.main()
