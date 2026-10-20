"""The operator backfill and ordinary reanalysis must find the same goals."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from analysis_runtime import MemoryBlobStore
from chart_source_exclusions import ExclusionPolicy
from piu_recommendations import build_combined_chart_results
import production_tiers as tiers
from scoring_targets import get_scoring_target
from tests.test_combined_scoring import snapshots
from tests.test_production_tiers import fit_for
from tier_release import submitted_tier_inputs


class GoalInputIdentityTests(unittest.TestCase):
    def test_real_operator_backfill_attaches_to_normal_combined_inputs(self):
        first, second = snapshots()
        for chart in second["charts"]:
            chart.update(bpmMin=180, bpmMax=180)
        # One nullable number promotes the populated pandas column to floats.
        # Also retain fractional BPM and mixed integer/float input values.
        second["charts"][0].update(noteCount=None, bpmMin=None, bpmMax=None)
        second["charts"][1].update(noteCount=1000.0, bpmMin=180.5, bpmMax=181.25)
        records, _, metadata = build_combined_chart_results(first, second)
        base = {"coop": [], "summary": {"modes": metadata["modes"], "method": {
            "phoenix1ScoreOverrides": metadata.get("phoenix1ScoreOverrides", [])}}}
        stripped_records, stripped_metadata = submitted_tier_inputs(base, second)
        self.assertNotEqual(records, stripped_records)
        self.assertNotEqual(metadata, stripped_metadata)
        store = MemoryBlobStore()
        manifest = {"_captures": {"Phoenix2": {}}}
        merged = {"boards": {}, "charts": {}, "sourceDates": {},
                  "manifestSha256": "a" * 64, "_privateProvenance": {}}
        # The submitted fixtures have no official-capture files. The real
        # context/filter/catalog and combined-input builders remain in use.
        with patch("official_score_merge.merge_official_scores", return_value=merged), \
             patch("chart_source_exclusions.load_exclusions",
                   return_value=ExclusionPolicy({"version": 1, "rules": []}, second["charts"])), \
             patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="a" * 64):
            context = tiers.load_context(first, second, manifest, stripped_records)
            normal_context = tiers.load_context(first, second, manifest, records)
            self.assertEqual(context[2], normal_context[2])
            self.assertEqual(context[-1], normal_context[-1])
            self.assertNotEqual(tiers.digest(context[2]), tiers.digest(normal_context[2]))
            completed = {}
            for goal in ("ss", "ssplus"):
                for source in ("official", "submitted"):
                    for mode, _ in tiers.MODES:
                        boards, _, identity = tiers.cohort(context[-1], goal, source, mode)
                        self.assertEqual(identity, tiers.cohort(normal_context[-1], goal, source, mode)[2])
                        store.put_json(tiers.fit_path(identity), {
                            "identity": identity, "fit": fit_for(boards, identity["target"])})
            fit_keys = {key for key in store.values if key.startswith(tiers.PREFIX + "fits/")}
            pinned = tiers.pin_tier_publication_inputs(store, first=first, second=second,
                manifest=manifest, records=stripped_records, metadata=stripped_metadata,
                generated_at_utc="2026-10-20T03:00:00+00:00")
            def artifact(*args, goal, **kwargs):
                return {"schemaVersion": 1, "goal": goal,
                        "targetConfig": get_scoring_target(goal).public(), "charts": {}, "method": {}}
            with patch("target_fit_cache.hydrate_point_context"), \
                 patch("scoring_goal_tiers.build_goal_artifact", side_effect=artifact), \
                 patch("target_fit_cache.load_or_fit_target", side_effect=AssertionError("Unexpected goal refit")) as fitter:
                for goal in ("ss", "ssplus"):
                    completed[goal] = tiers.advance_goal_backfill(store, pinned_inputs_reference=pinned, goal=goal)
                    self.assertTrue(completed[goal]["done"])
                    self.assertEqual(completed[goal]["completedTargetFits"], 4)
                fitter.assert_not_called()
            self.assertEqual({key for key in store.values if key.startswith(tiers.PREFIX + "fits/")}, fit_keys)
            # Advance the real normal reanalysis far enough to record its goal
            # lookup. Numerical fits are deliberately outside this identity test.
            with patch("scoring_percentile.build_production_tier_payload", return_value={"schemaVersion": 48}), \
                 patch("target_fit_cache.load_or_fit_target", side_effect=RuntimeError("stop-before-legacy-fit")):
                with self.assertRaisesRegex(RuntimeError, "stop-before-legacy-fit"):
                    tiers.build_hosted_tiers(records, metadata, first, second, store=store,
                        arguments={"official_scoring_manifest": manifest},
                        generated_at_utc="2026-10-20T04:00:00+00:00")
            states = [value for path, value in store.values.items()
                      if path.startswith(tiers.PREFIX + "generations/") and path.endswith("/state.json")]
            self.assertEqual(len(states), 1)
            self.assertEqual(set(states[0]["goalArtifacts"]), {"ss", "ssplus"})
            for goal in ("ss", "ssplus"):
                reference = states[0]["goalArtifacts"][goal]
                self.assertEqual(reference["path"], completed[goal]["artifactReference"]["path"])
                self.assertEqual(reference["payloadSha256"], completed[goal]["artifactReference"]["sha256"])

    def test_catalog_normalization_preserves_fractional_values_nulls_bools_and_other_metadata(self):
        catalog = [{"id": "synthetic-chart", "level": 20, "noteCount": 1000,
                    "bpmMin": 180.5, "bpmMax": None, "sourceRank": 1}]
        original = copy.deepcopy(catalog)
        arguments = {"manifest_sha256": "a" * 64, "policy": {"exclusions": []}}
        expected = tiers.goal_input_identity({}, {}, catalog=catalog, **arguments)
        equivalent = copy.deepcopy(catalog)
        equivalent[0].update(level=20.0, noteCount=1000.0)
        self.assertEqual(tiers.goal_input_identity({}, {}, catalog=equivalent, **arguments), expected)
        self.assertEqual(catalog, original)
        self.assertIs(type(equivalent[0]["noteCount"]), float)
        for field, value in (("level", 21), ("level", True), ("noteCount", 1000.5),
                             ("noteCount", None), ("bpmMin", 180), ("bpmMin", 180.75),
                             ("bpmMax", 0), ("sourceRank", 1.0), ("songName", "changed")):
            changed = copy.deepcopy(catalog)
            changed[0][field] = value
            with self.subTest(field=field, value=value):
                self.assertNotEqual(tiers.goal_input_identity({}, {}, catalog=changed, **arguments), expected)
        integral_bpm = copy.deepcopy(catalog)
        integral_bpm[0].update(bpmMin=180, bpmMax=181)
        floating_bpm = copy.deepcopy(integral_bpm)
        floating_bpm[0].update(bpmMin=180.0, bpmMax=181.0)
        self.assertEqual(tiers.goal_input_identity({}, {}, catalog=integral_bpm, **arguments),
                         tiers.goal_input_identity({}, {}, catalog=floating_bpm, **arguments))

    def test_consumed_sources_catalog_manifest_and_policy_remain_exact(self):
        first = {"scores": [{"score": 980000}], "players": [{"id": "synthetic-a"}]}
        second = {"scores": [{"score": 985000}], "players": [{"id": "synthetic-b"}]}
        arguments = {"catalog": [{"id": "synthetic-chart", "level": 20}],
                     "manifest_sha256": "a" * 64, "policy": {"exclusions": []}}
        expected = tiers.goal_input_identity(first, second, **arguments)
        changed_first, changed_second = copy.deepcopy(first), copy.deepcopy(second)
        changed_first["scores"][0]["score"] += 1
        changed_second["scores"][0]["score"] += 1
        self.assertNotEqual(tiers.goal_input_identity(changed_first, second, **arguments), expected)
        self.assertNotEqual(tiers.goal_input_identity(first, changed_second, **arguments), expected)
        revoked = {**second, "players": []}
        self.assertNotEqual(tiers.goal_input_identity(first, revoked, **arguments), expected)
        for field, value in (("catalog", [{"id": "synthetic-chart", "level": 21}]),
                             ("manifest_sha256", "b" * 64), ("policy", {"exclusions": ["synthetic-chart"]})):
            with self.subTest(field=field):
                self.assertNotEqual(tiers.goal_input_identity(first, second, **{**arguments, field: value}), expected)


if __name__ == "__main__":
    unittest.main()
