from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

import numpy as np

from canonical_json import canonical_json_sha256 as digest
from scoring_goal_tiers import (NAMESPACE, apply_goal_artifacts, build_goal_artifact,
                                recover_goal_tiers, validate_goal_tiers)
from scoring_targets import SS, SSPLUS, SSS, SSSPLUS, get_scoring_target
from sss_scoring import _composite, _target_metric, rank_variants
from sss_scoring_model import _prepare
from tests.test_sss_scoring import fixture
from tests.test_submitted_sssplus_calibration import calibration_fixture
from submitted_sssplus_calibration import apply_submitted_sssplus_calibration

INPUTS = {"version": 1, "rawScores": "a" * 64, "catalog": "b" * 64, "policy": "c" * 64}


def references(goal):
    return [{"goal": goal, "source": source, "mode": mode, "identity": {
        "target": get_scoring_target(goal).score, "samples": 200, "seed": 20261018,
        "boards": "d" * 64, "sourceBoards": "e" * 64}}
        for source in ("official", "submitted") for mode in ("Single", "Double")]


def legacy_fixture():
    base, fit, identity = calibration_fixture()
    return apply_submitted_sssplus_calibration(base, fit, fit_identity=identity)


def artifact_fixture(base, goal):
    """Real builder output on synthetic fits, plus unsupported high-level rows."""
    target = get_scoring_target(goal)
    artifact = GoalBuilderTests().build(target)
    artifact["inputIdentity"] = copy.deepcopy(INPUTS)
    temporary = copy.deepcopy(base)
    for key in ("singles", "doubles"):
        for row in temporary[key]:
            metric = copy.deepcopy(artifact["charts"].get(str(row["chartId"])))
            if metric is None:
                metric = _target_metric(row, "official", None, {}, None,
                                        "No supported target calibration.", target=target)
            row["scoringVariants"][goal] = metric
            row["pumbilityVariants"][goal] = _composite(row, metric)
        rank_variants(temporary[key], goal=goal)
    artifact["charts"] = {str(row["chartId"]): row["scoringVariants"][goal]
                          for key in ("singles", "doubles") for row in temporary[key]}
    return artifact


class GoalOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = legacy_fixture()
        cls.artifacts = {goal: artifact_fixture(cls.base, goal) for goal in ("ss", "ssplus")}

    def apply(self, artifacts=None):
        return apply_goal_artifacts(self.base, artifacts or self.artifacts, expected_input_identity=INPUTS)

    def test_exact_legacy_recovery_and_no_mutation(self):
        frozen = copy.deepcopy((self.base, self.artifacts))
        result = self.apply()
        self.assertEqual(result["schemaVersion"], 58)
        self.assertEqual(recover_goal_tiers(result), self.base)
        self.assertEqual(validate_goal_tiers(result), self.base)
        self.assertEqual((self.base, self.artifacts), frozen)
        self.assertEqual(result["summary"]["method"]["availableScoringGoals"],
                         ["general", "ss", "ssplus", "sss", "sssplus"])
        for key in ("singles", "doubles"):
            for before, after in zip(self.base[key], result[key]):
                for goal in ("general", "sss", "sssplus"):
                    self.assertEqual(before["scoringVariants"][goal], after["scoringVariants"][goal])
                    self.assertEqual(before["pumbilityVariants"][goal], after["pumbilityVariants"][goal])
                self.assertEqual(before["tierMetrics"], after["tierMetrics"])

    def test_partial_backfill_advertises_only_completed_goal(self):
        result = self.apply({"ss": self.artifacts["ss"]})
        self.assertEqual(result["summary"]["method"]["availableScoringGoals"],
                         ["general", "ss", "sss", "sssplus"])
        self.assertTrue(all("ssplus" not in row["scoringVariants"] for row in result["singles"]))

    def test_rejects_stale_inputs_population_target_and_incomplete_fits(self):
        mutations = [
            lambda a: a["inputIdentity"].update(rawScores="9" * 64),
            lambda a: a["charts"].pop(next(iter(a["charts"]))),
            lambda a: a["targetConfig"].update(score=995000),
            lambda a: a["method"]["fitReferences"].pop(),
            lambda a: a["method"]["fitReferences"][0]["identity"].update(samples=100),
        ]
        for mutate in mutations:
            artifact = copy.deepcopy(self.artifacts["ss"])
            mutate(artifact)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                self.apply({"ss": artifact})

    def test_rejects_point_composite_count_rank_and_legacy_tampering(self):
        mutations = [
            lambda p: p["singles"][0]["scoringVariants"]["ss"].update(estimatedDifficulty=99.),
            lambda p: p["singles"][0]["scoringVariants"]["ss"].update(nContributors=999),
            lambda p: p["singles"][0]["scoringVariants"]["ss"].update(levelRank=999),
            lambda p: p["singles"][0]["pumbilityVariants"]["ss"].update(estimatedDifficulty=99.),
            lambda p: p["singles"][0]["tierMetrics"]["clearing"].update(estimatedDifficulty=99.),
            lambda p: p["summary"]["method"]["availableScoringGoals"].remove("ss"),
        ]
        for mutate in mutations:
            result = self.apply()
            mutate(result)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                validate_goal_tiers(result)

    def test_hash_is_bound_to_new_chart_evidence_even_if_composite_is_updated(self):
        result = self.apply()
        row = result["singles"][0]
        row["scoringVariants"]["ss"]["scoringProvisionalReasons"].append("tampered")
        with self.assertRaisesRegex(ValueError, "artifact"):
            validate_goal_tiers(result)

    def test_public_projection_keeps_availability_and_omits_private_artifact_diagnostics(self):
        from tier_public import prepare_tier_publication, tier_chart_details
        result = self.apply()
        # This legacy fixture's Co-op sentinel is not a chart; goal DTOs are S/D.
        with patch("tier_public.MODES", ("singles", "doubles")):
            compact, shards = prepare_tier_publication(result, "synthetic-five-goals")
        method = compact["summary"]["method"]
        self.assertEqual(method["scoringGoalPolicies"], result["summary"]["method"]["scoringGoalPolicies"])
        self.assertEqual(method["availableScoringGoals"], ["general", "ss", "ssplus", "sss", "sssplus"])
        self.assertNotIn(NAMESPACE, method)
        self.assertEqual(len(shards), 32)
        for key in ("singles", "doubles"):
            for before, row in zip(result[key], compact[key]):
                for goal in ("ss", "ssplus"):
                    metric = row["scoringVariants"][goal]
                    self.assertEqual(metric["estimatedDifficulty"], before["scoringVariants"][goal]["estimatedDifficulty"])
                    self.assertNotIn("scoringRefinement", metric)
                    self.assertNotIn("initialFit", metric)
        detail = tier_chart_details(result, result["singles"][0]["chartId"])
        self.assertEqual(detail["chart"]["scoringVariants"]["ss"], result["singles"][0]["scoringVariants"]["ss"])

    def test_boundary_support_projection_stays_independent_per_goal(self):
        from tier_public import compact_tier_payload
        result = self.apply()
        row = result["singles"][0]
        for goal, count in (("ss", 9), ("ssplus", 10)):
            row["scoringVariants"][goal].update(scoringMethod="target-boundary-score-gap-v1",
                scoringRefinement={"fullBoard": {"records": count, "topScores": [999999]}})
        compact = compact_tier_payload(result)
        self.assertEqual(compact["singles"][0]["scoringVariants"]["ss"]["scoringBoundaryRecords"], 9)
        self.assertEqual(compact["singles"][0]["scoringVariants"]["ssplus"]["scoringBoundaryRecords"], 10)
        self.assertNotIn("topScores", str(compact["singles"][0]["scoringVariants"]))


class GoalBuilderTests(unittest.TestCase):
    def build(self, target, *, empty_double=False):
        _, prepared, fits = fixture()
        for source in ("official", "submitted"):
            prepared[source]["boards"] = {}
            for mode in ("Single", "Double"):
                fitted = fits[source][mode]
                fitted["policy"] = {"modelVersion": "target-rasch-observed-best-v1", "scoreTarget": target.score,
                    "bootstrapSamples": 200, "minimumPlayerCharts": 4,
                    "weighting": "each-player-total-one-inverse-observed-chart-count-v1",
                    "regularizationGrid": [.01, .05, .2, 1.]}
                fitted["diagnostics"]["bootstrap"] = {"completed": 200, "failed": 0}
                for cid in fitted["charts"]:
                    prepared[source]["boards"][cid] = {f"synthetic-{j}": target.score + 2000 if j < 10 else target.score - 2000
                                                         for j in range(25)}
                if empty_double and mode == "Double":
                    fitted["charts"] = {}
                    prepared[source]["boards"] = {cid: board for cid, board in prepared[source]["boards"].items()
                                                   if prepared["catalog"][cid]["type"] != "Double"}
        from types import SimpleNamespace
        context = ({}, {}, list(prepared["catalog"].values()), SimpleNamespace(semantic_hash=lambda: "f" * 64),
                   {"manifestSha256": "a" * 64}, prepared)
        frozen = digest(prepared), digest(fits)

        def point_context(boards, cached, **kwargs):
            data = _prepare(boards, kwargs.get("score_target", 990000))
            point = None if not len(data["y"]) else {"ability": np.zeros(len(data["players"])),
                                                      "effect": np.zeros(len(data["mainCharts"]))}
            return {"data": data, "fit": point, "diagnostics": {"converged": point is not None}}

        with patch("sss_scoring_refinement.recover_point_fit", side_effect=point_context):
            result = build_goal_artifact(context, fits, references(target.key), "2026-10-20T00:00:00Z", goal=target.key)
        self.assertEqual((digest(prepared), digest(fits)), frozen)
        return result

    def test_targets_get_independent_calibration_and_honest_metadata(self):
        for target in (SS, SSPLUS):
            with self.subTest(goal=target.key):
                result = self.build(target)
                self.assertEqual(result["targetConfig"], target.public())
                self.assertEqual(result["method"]["bootstrapSamples"], 200)
                self.assertEqual(result["method"]["compressionApplications"], 1)
                self.assertEqual(result["method"]["refinement"]["scoreTarget"], target.score)
                self.assertEqual(result["method"]["refinement"]["gapCalibration"]["official"]["Single"]["policy"]["scoreRange"],
                                 [target.score-10000, 999999])
                for metric in result["charts"].values():
                    self.assertEqual(metric["scoringTarget"], target.score)
                    self.assertEqual(metric["nContributors"], 25)
                    self.assertEqual(metric["scoringTargetSuccesses"], 10)
                    self.assertEqual(metric["scoringBelowTarget"], 15)
                    self.assertLessEqual(abs(metric["difficultyDelta"]), 2.5)

    def test_sparse_mode_stays_unrated_without_borrowing_other_modes(self):
        result = self.build(SS, empty_double=True)
        double = [metric for cid, metric in result["charts"].items() if cid.startswith("Double")]
        self.assertTrue(double)
        self.assertTrue(all(metric["estimatedDifficulty"] is None and metric["scoringUnratedReason"]
                            and metric["difficultyCi95Low"] is None for metric in double))

    def test_all_targets_are_inclusive_and_unknown_goals_fail(self):
        for target in (SS, SSPLUS, SSS, SSSPLUS):
            boards = {str(i): {"below": target.score-1, "equal": target.score, "above": target.score+1}
                      for i in range(4)}
            prepared = _prepare(boards, target.score)
            self.assertEqual(int(prepared["y"].sum()), 8)
            self.assertEqual(len(prepared["y"]), 12)
        with self.assertRaises(ValueError):
            get_scoring_target("unknown")


if __name__ == "__main__":
    unittest.main()
