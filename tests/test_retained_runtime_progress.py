import unittest
from unittest.mock import patch

import analysis_runtime as runtime


class RetainedRuntimeProgressTests(unittest.TestCase):
    def value(self, **fields):
        return {"version": 1, "generation": "a" * 64, "producer": "retained-goals-v1",
                "ordinal": 6, "stage": "official-clearing", **fields}

    def test_new_stage_and_total_are_producer_bound(self):
        self.assertEqual(runtime._validated_tier_progress(self.value())["stage"], "official-clearing")
        for fields in ({"ordinal": 21}, {"stage": "centered-sss"}, {"producer": None},
                       {"producer": "unknown"}, {"stage": "scoring-goals"}):
            with self.assertRaises(ValueError):
                runtime._validated_tier_progress(self.value(**fields))

    def test_legacy_progress_still_valid_and_producers_cannot_mix(self):
        old = {"version": 1, "generation": "a" * 64, "ordinal": 10, "stage": "centered-sss"}
        self.assertEqual(runtime._validated_tier_progress(old), old)
        with self.assertRaises(ValueError):
            runtime._reconcile_tier_progress({"tierProgress": old}, self.value(ordinal=11, stage="median"))

    def test_preparation_hands_off_to_partial_bootstrap(self):
        prior = self.value(ordinal=0, stage="general", preparationOrdinal=8, preparedContext=True)
        current = self.value(ordinal=0, stage="target-fit", preparedContext=True,
                             fitProgress={"identity": "b" * 64, "ordinal": 80})
        self.assertEqual(runtime._reconcile_tier_progress({"tierProgress": prior}, current)["tierProgress"], current)
        with self.assertRaises(ValueError):
            runtime._validated_tier_progress({**current, "fitProgress": {"identity": "b" * 64, "ordinal": 202}})

    def test_hosted_runtime_uses_retained_entry_point(self):
        arguments = {name: True for name in ("hybrid_lower_tiers", "average_pumbility", "midpoint_compression", "cross_level_clearing")}
        with patch("production_tiers.build_retained_hosted_tiers", return_value={"retained": True}) as build:
            self.assertEqual(runtime.build_combined_tier_payload([], {}, {}, {}, source_aware_store=object(),
                official_scoring_manifest={}, on_tier_progress=lambda value: None, **arguments), {"retained": True})
            build.assert_called_once()
