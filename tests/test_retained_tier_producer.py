"""Retained producer parity over existing deterministic model/assembly fixtures."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import Mock, patch

import numpy as np

from analysis_runtime import MemoryBlobStore
import production_tiers as tiers
from retained_tiers import (BASE_SCHEMA_VERSION, NAMESPACE, apply_retained_baseline,
                            validate_marker)
from tests.test_production_tiers import context_fixture, fit_for


def product_rows(payload):
    """Compare active chart evidence, allowing only retired branches/history out."""
    def active(value):
        if isinstance(value, dict):
            return {key: ({goal: active(metric) for goal, metric in item.items()
                           if goal in ("general", "sssplus")}
                          if key in ("scoringVariants", "pumbilityVariants") else active(item))
                    for key, item in value.items() if not key.endswith("Baseline")}
        return [active(item) for item in value] if isinstance(value, list) else value
    return {key: active(payload.get(key, [])) for key in ("singles", "doubles", "coop")}


def point_context(boards, cached, **kwargs):
    from sss_scoring_model import _prepare
    data = _prepare(boards, kwargs.get("score_target", 990000))
    return {"data": data, "fit": None if not len(data["y"]) else {
        "ability": np.zeros(len(data["players"])), "effect": np.zeros(len(data["mainCharts"]))},
        "diagnostics": {"converged": bool(len(data["y"])), "pointFits": 1, "bootstrapRefits": 0}}


class RetainedTierProducerTests(unittest.TestCase):
    def test_retained_checkpoints_resume_four_fits_and_skip_retired_publication_overlays(self):
        store, context = MemoryBlobStore(), context_fixture()
        timestamp = "2026-10-26T00:00:00Z"
        args = {"official_scoring_manifest": {"_captures": {"Phoenix2": {}}}}
        fitter = Mock(side_effect=lambda store, boards, **kw: fit_for(boards, kw["score_target"]))
        public = {"public": "retained"}
        def finish(stage, *unused):
            return {"schemaVersion": tiers.STAGE_SCHEMAS[stage], "generatedAtUtc": timestamp,
                    "summary": {"generatedAtUtc": timestamp}}
        with patch.object(tiers, "load_context", return_value=context), \
             patch.object(tiers, "_retained_dependencies", return_value=(context[2], context[3], "source")), \
             patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"), \
             patch("scoring_percentile.build_production_tier_payload", return_value={"schemaVersion": 48}), \
             patch("target_fit_cache.load_or_fit_target", fitter), \
             patch.object(tiers, "pipeline_code_identity", return_value="p" * 64), \
             patch.object(tiers, "numerical_pipeline_recipe", return_value={"retained": True}), \
             patch.object(tiers, "_finish_stage", side_effect=finish) as producer, \
             patch("sss_tail_tiers.apply_sss_tail_tiers", side_effect=AssertionError("Retired SSS tail scheduled")), \
             patch("level20_scoring_width.apply_retained_level20_scoring_width", side_effect=lambda value: {**value, "schemaVersion": 61}), \
             patch.object(tiers, "public_tiers", return_value=public), \
             patch.object(tiers, "validate_public_tiers"), \
             patch("tier_public.prepare_tier_publication", return_value=(public, {})), \
             patch("tier_public.validate_tier_publication"):
            for ordinal in range(1, 9):
                with self.assertRaises(tiers.TierAnalysisPending) as pending:
                    tiers.build_retained_hosted_tiers([], {}, {}, {}, store=store, arguments=args,
                        generated_at_utc=timestamp)
                self.assertEqual(pending.exception.fit_count, 4)
            generation = pending.exception.generation
            for _ in range(26):
                try:
                    result = tiers.resume_tier_generation(store, generation, generated_at_utc=timestamp)
                    break
                except tiers.TierAnalysisPending as progress:
                    self.assertEqual(progress.total_stages, 20)
            else:
                self.fail("The retained checkpoint chain never finished.")
            self.assertEqual(result, public)
            self.assertEqual(fitter.call_count, 4)
            self.assertEqual([call.args[0] for call in producer.call_args_list], list(tiers.RETAINED_FINISH_STAGES))
            state = store.get_json(tiers.PREFIX + "generations/" + generation + "/state.json")
            self.assertNotIn("tailOverlay", state)
            self.assertNotIn("goalOverlay", state)
            self.assertNotIn("widthOverlay", state)
            self.assertEqual(next(r for r in state["stages"] if r["stage"] == "retained-width")["schemaVersion"], 61)
            self.assertEqual(state["stages"][-1]["schemaVersion"], 162)
            self.assertEqual(tiers.resume_tier_generation(store, generation, generated_at_utc=timestamp), public)
            self.assertEqual(fitter.call_count, 4)

    def test_only_four_retained_fits_and_dynamic_progress_then_legacy_resume_still_has_eight(self):
        store, context = MemoryBlobStore(), context_fixture()
        timestamp = "2026-10-26T00:00:00Z"
        fitter = Mock(side_effect=lambda store, boards, **kw: fit_for(boards, kw["score_target"]))
        args = {"official_scoring_manifest": {"_captures": {"Phoenix2": {}}}}
        pending = []
        with patch.object(tiers, "load_context", return_value=context), \
             patch.object(tiers, "_retained_dependencies", return_value=(context[2], context[3], "source")), \
             patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"), \
             patch("scoring_percentile.build_production_tier_payload", return_value={"schemaVersion": 48}), \
             patch("target_fit_cache.load_or_fit_target", fitter), \
             patch.object(tiers, "training_predictor", side_effect=AssertionError("Research benchmark scheduled")), \
             patch.object(tiers, "_advance_finish", return_value={"retained": True}):
            for _ in range(30):
                try:
                    result = tiers.build_retained_hosted_tiers([], {}, {}, {}, store=store,
                        arguments=args, generated_at_utc=timestamp)
                    break
                except tiers.TierAnalysisPending as progress:
                    pending.append(progress)
            else:
                self.fail("The bounded retained producer never finished preparation/fits.")
        self.assertEqual(result, {"retained": True})
        self.assertEqual(fitter.call_count, 4)
        self.assertEqual([call.kwargs["score_target"] for call in fitter.call_args_list], [995000] * 4)
        self.assertTrue(all(call.kwargs["bootstrap_samples"] == 200
                            and call.kwargs["diagnostics_policy"] == "product-only"
                            and call.kwargs["general_predictor"] is None for call in fitter.call_args_list))
        self.assertEqual([item.stage for item in pending[:8]],
            ["preparation-general", "preparation-phase-model", "preparation-phase-official", "preparation-phase-provenance",
             "preparation-cohort-1", "preparation-cohort-2", "preparation-cohort-3", "preparation-cohort-4"])
        self.assertTrue(all(item.fit_count == 4 and item.total_stages == 20 for item in pending))
        self.assertEqual(tiers._preparation_cohorts({}), tiers.PREPARATION_COHORTS)
        self.assertEqual(tiers._fit_count({}), 8)

    def test_new_baseline_and_all_retained_assembly_match_legacy_exactly(self):
        from tests.test_sssplus_tiers import stage_fixture
        from sssplus_tiers import apply_sssplus_tiers
        from sss_folder_centering import recover_uncentered
        from refined_tiers import recover_first_pass
        from sss_scoring import recover_general_payload
        from sssplus_median_calibration import apply_median_calibration
        from sssplus_spread import apply_sssplus_spread
        from combined_official_clearing import apply_combined_official_clearing
        from source_aware_tiers import apply_source_aware_tiers
        from submitted_sssplus_calibration import apply_submitted_sssplus_calibration
        from sss_tail_tiers import apply_sss_tail_tiers
        from level20_scoring_width import apply_level20_scoring_width, apply_retained_level20_scoring_width
        from tests.test_combined_official_clearing import merged_fixture
        from chart_source_exclusions import ExclusionPolicy
        from tests.test_sss_scoring import fixture as general_fixture

        def timestamped_fixture():
            values = general_fixture()
            values[0]["generatedAtUtc"] = "2026-10-26T00:00:00Z"
            return values

        with patch("sssplus_tiers.apply_sssplus_tiers", wraps=apply_sssplus_tiers) as builder, \
                patch("tests.test_sss_scoring.fixture", side_effect=timestamped_fixture):
            legacy_base, legacy = stage_fixture()
        _, prepared, fits = builder.call_args.args
        general = recover_general_payload(recover_first_pass(recover_uncentered(legacy_base)))
        capture = {"source": "piuscores-official", "mix": "Phoenix2", "asOf": "2026-10-25T00:00:00Z",
            "catalog": list(prepared["catalog"].values()), "boards": {}}
        retained_base = apply_retained_baseline(general, capture)
        self.assertEqual(retained_base["schemaVersion"], BASE_SCHEMA_VERSION)
        with patch("sss_scoring_refinement.recover_point_fit", side_effect=point_context):
            retained, _ = apply_sssplus_tiers(retained_base, prepared, fits,
                source_hashes={"submitted": "b" * 64}, bootstrap_samples=200, seed=7)
        self.assertEqual(product_rows(legacy), product_rows(retained))

        _, merged = merged_fixture()
        merged["manifestSha256"] = prepared["manifestSha256"]
        policy = ExclusionPolicy({"version": 1, "rules": []}, list(prepared["catalog"].values()))
        identity = {"boards": "a" * 64, "sourceBoards": "b" * 64, "target": 995000, "samples": 200, "seed": 7}
        provenance = {**policy.public(), "targetFits": [{"goal": "sssplus", "source": "submitted", "mode": "Double", "identity": identity}]}
        fit = copy.deepcopy(fits["submitted"]["Double"])
        fit["policy"].update(bootstrapSamples=200, selectedRegularization=.01)
        for producer in (
            lambda value: apply_median_calibration(value, policy_version=3),
            lambda value: apply_sssplus_spread(value, rare_adjustments={}, rare_policy={"fixture": True}),
            lambda value: apply_combined_official_clearing(value, merged)[0],
            lambda value: apply_source_aware_tiers(value, value, provenance),
            lambda value: apply_submitted_sssplus_calibration(value, fit, fit_identity=identity, local_only=False),
        ):
            legacy, retained = producer(legacy), producer(retained)
            self.assertEqual(product_rows(legacy), product_rows(retained))
        legacy = apply_level20_scoring_width(apply_sss_tail_tiers(legacy))
        retained = apply_retained_level20_scoring_width(retained)
        self.assertEqual(retained["schemaVersion"], 61)
        validate_marker(retained)
        self.assertEqual(product_rows(legacy), product_rows(retained))
        self.assertEqual(legacy["summary"]["method"]["sssplusTailPolicy"], retained["summary"]["method"]["sssplusTailPolicy"])
        self.assertEqual({k: v for k, v in legacy["summary"]["method"]["level20ScoringWidth"]["folders"].items() if ":sssplus:" in k},
                         retained["summary"]["method"]["level20ScoringWidth"]["folders"])
        self.retained_result = retained

    def test_extracted_d26_clearing_preserves_points_intervals_defaults_general_means_and_ranks(self):
        from tests.test_official_high_level_clearing import measured_fixture
        from refined_tiers import _general_composite
        from official_high_level_clearing import apply_official_high_level_clearing
        from sss_scoring import FIT_FIELDS, general_scoring_view
        from tier_postprocessing import _rank_secondary

        general, capture = measured_fixture()
        general["schemaVersion"] = 48
        general["coop"] = []
        for key in ("singles", "doubles"):
            for row in general[key]:
                level = row["level"]
                row.update({field: None for field in FIT_FIELDS})
                row.update(estimatedDifficulty=level+.7, evidenceStatus="Published", nContributors=35,
                    initialFit={"estimatedDifficulty": level+.9})
                row["tierMetrics"]["clearing"] = {"estimatedDifficulty": level+.5, "evidenceStatus": "Published", "ratedClearCount": 30,
                    "initialFit": {"estimatedDifficulty": level+.5}}
                row["tierMetrics"]["pumbility"] = {"estimatedDifficulty": (level+.7+level+.5)/2, "evidenceStatus": "Published",
                    "initialFit": {"estimatedDifficulty": (level+.9+level+.5)/2}}
                row.pop("scoringVariants", None)
        legacy = copy.deepcopy(general)
        for key in ("singles", "doubles"):
            for row in legacy[key]:
                row["scoringVariants"] = {"general": general_scoring_view(row)}
        apply_official_high_level_clearing(legacy, capture)
        for key in ("singles", "doubles"):
            for row in legacy[key]:
                if key == "doubles" and row["level"] >= 26:
                    _general_composite(row)
            _rank_secondary(legacy[key], "clearing")
            _rank_secondary(legacy[key], "pumbility")
            for row in legacy[key]:
                row["pumbilityVariants"] = {"general": copy.deepcopy(row["tierMetrics"]["pumbility"])}
        retained = apply_retained_baseline(general, capture)
        self.assertEqual(product_rows(legacy), product_rows(retained))
        from retained_tiers import recover_retained_baseline, validate_retained_baseline
        self.assertEqual(recover_retained_baseline(retained), general)
        bad = copy.deepcopy(retained)
        bad["doubles"][0]["nContributors"] += 1
        with self.assertRaisesRegex(ValueError, "exact General predecessor"):
            validate_retained_baseline(bad)


if __name__ == "__main__":
    unittest.main()
