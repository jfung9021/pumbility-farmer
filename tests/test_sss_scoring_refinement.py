from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

import numpy as np

from official_cross_level_calibration import chart_cost
from sss_scoring import _derived, fit_target_curve
from sss_scoring_model import _fit, _prepare
from sss_scoring_refinement import (BOUNDARY_METHOD, RARE_METHOD, allocate_upper_band,
    apply_sss_refinement, boundary_estimate, classify_board, conditional_chart_fit,
    fit_gap_calibration, freeze_ranges, rare_blend, recover_point_fit)
from tier_postprocessing import FIT_FIELDS, compress_difficulty


def curve():
    return fit_target_curve([{"level": 20 + i, "cost": float((i + 1) ** 2)} for i in range(3)], minimum_level=20)


def metric(value, level=20, *, success=6, total=30):
    raw = None if value is None else level + .5 + 2 * (value - level - .5)
    return {**{field: None for field in FIT_FIELDS}, **_derived(value, level),
        "evidenceStatus": "Published" if value is not None else "Unrated",
        "scoringUnratedReason": None if value is not None else "One-sided evidence.",
        "scoringTargetSuccesses": success, "scoringBelowTarget": total-success,
        "nContributors": total, "nPlayersScored": total, "scoringMethod": "sss-target-rasch-v1",
        "scoringProvisionalReasons": [], "scoringPossiblyTruncated": False,
        "scoringChartEffect": 1., "scoringChartEffectCi95Low": .5,
        "scoringChartEffectCi95High": 1.5, "scoringBootstrapValidReplicates": 200,
        "initialFit": {**{field: None for field in FIT_FIELDS}, **_derived(raw, level)}}


class RefinementHelpersTests(unittest.TestCase):
    def test_full_board_inclusive_threshold_ties_and_missing(self):
        self.assertEqual(classify_board({})["branch"], "no-data")
        self.assertEqual(classify_board({"a": 989999})["branch"], "no-sss")
        full = classify_board({"a": 990000, "b": 990000}, possibly_truncated=True)
        self.assertEqual((full["branch"], full["successes"], full["lowestScore"]), ("all-sss", 2, 990000))
        self.assertTrue(full["possiblyTruncated"])
        self.assertEqual(classify_board({"a": 990000, "b": 989999})["branch"], "mixed")
        with self.assertRaises(ValueError):
            classify_board({"a": float("nan")})

    def test_no_sss_top_scores_keep_distinct_players_with_tied_scores(self):
        board = {"a": 980000, "b": 989900, "c": 970000, "d": 989900}
        full = classify_board(board, possibly_truncated=True)
        self.assertEqual(full["topScores"], [989900, 989900, 980000])
        self.assertEqual(full["records"], 4)
        self.assertTrue(full["possiblyTruncated"])
        self.assertNotIn("topScores", classify_board({"a": 990000, "b": 989999}))

    def test_range_freezes_actual_edges_and_borrows_only_source_mode_offsets(self):
        payload = {"singles": [], "doubles": []}
        for level, offset in ((20, 0.), (22, .2)):
            for index in range(5):
                point = level + offset + index * .25
                row = {"chartId": f"{level}-{index}", "level": level,
                       "scoringVariants": {"sss": metric(point, level)}}
                if index == 0:
                    row["scoringVariants"]["sss"]["evidenceStatus"] = "Provisional"
                payload["singles"].append(row)
        for level in (19, 21, 23):
            payload["singles"].append({"chartId": str(level), "level": level,
                "scoringVariants": {"sss": metric(None, level)}})
        frozen = freeze_ranges(payload)
        s = frozen["official"]["Single"]
        self.assertEqual((s["20"]["lower"], s["20"]["upper"]), (20., 21.))
        self.assertEqual(s["20"]["referenceChartIds"], [f"20-{i}" for i in range(5)])
        self.assertEqual(s["21"]["donorLevels"], [20, 22])
        self.assertAlmostEqual(s["21"]["lower"], 21.1)
        self.assertAlmostEqual(s["23"]["lower"], 23.2)
        self.assertEqual(s["23"]["kind"], "nearest")
        self.assertNotIn("19", frozen["submitted"]["Single"])
        self.assertFalse(frozen["official"]["Double"])
        payload["singles"][0]["scoringVariants"]["sss"]["estimatedDifficulty"] = 999.
        self.assertEqual(s["20"]["upper"], 21.)

    def test_boundary_zero_gap_measured_edges_monotonic_tails_and_one_compression(self):
        c, frozen = curve(), {"lower": 20.1, "upper": 20.9}
        for branch, score, edge in (("no-sss", 990000, 20.9), ("all-sss", 990000, 20.1)):
            result = boundary_estimate(branch, score, 20, frozen, c, 1000.)
            self.assertAlmostEqual(result["finalDifficulty"], edge)
            self.assertAlmostEqual(result["finalDifficulty"], compress_difficulty(result["initialDifficulty"], 20))
        hard = [boundary_estimate("no-sss", score, 20, frozen, c, 1000.)["finalDifficulty"] for score in (989999, 980000, 900000)]
        self.assertTrue(20.9 < hard[0] < hard[1] < hard[2])
        easy = [boundary_estimate("all-sss", score, 20, frozen, c, 1000.)["finalDifficulty"] for score in (990001, 995000, 1000000)]
        self.assertTrue(20.1 > easy[0] > easy[1] > easy[2])
        with self.assertRaises(ValueError):
            boundary_estimate("all-sss", 989999, 20, frozen, c, 1000.)

    def test_conditional_likelihood_and_sparse_success_transitions(self):
        abilities = np.linspace(-2, 2, 102)
        outcomes = np.zeros(102)
        outcomes[-1] = 1
        weights = np.linspace(.01, .2, 102)
        fitted = conditional_chart_fit(abilities, outcomes, weights)
        self.assertLess(abs(fitted["likelihoodResidual"]), 1e-10)
        self.assertAlmostEqual(fitted["weightedSuccesses"], fitted["conditionalExpectedSuccesses"])
        self.assertEqual(rare_blend(1, 2)["blendWeight"], 0.)
        self.assertEqual(rare_blend(1, 20)["blendWeight"], 1.)
        self.assertEqual(rare_blend(1, 102)["blendWeight"], 1.)
        self.assertEqual(rare_blend(4, 102)["blendWeight"], .25)
        self.assertEqual(rare_blend(5, 102)["blendWeight"], 0.)
        with self.assertRaises(ValueError):
            conditional_chart_fit(abilities, np.zeros(102), weights)

    def test_upper_numeric_band_mean_ties_and_no_reduction(self):
        group = [{"chartId": cid, "level": 25, "conditionalDifficulty": value,
                  "blendWeight": weight, "originalDifficulty": original}
                 for cid, value, weight, original in (("a", 28., 1., 25.2), ("b", 28., 1., 25.4),
                                                     ("c", 29., .25, 25.8))]
        allocate_upper_band(group, {"lower": 25., "upper": 26.})
        self.assertEqual([candidate["conditionalRank"] for candidate in group], [1.5, 1.5, 3.])
        self.assertEqual(group[0]["finalDifficulty"], group[1]["finalDifficulty"])
        self.assertTrue(25.95 <= group[0]["finalDifficulty"] <= 26.)
        self.assertEqual(group[2]["finalDifficulty"], 25.85)
        solo = [{"chartId": "x", "level": 25, "conditionalDifficulty": 30., "blendWeight": 1., "originalDifficulty": 26.1}]
        allocate_upper_band(solo, {"lower": 25., "upper": 26.})
        self.assertEqual(solo[0]["finalDifficulty"], 26.1)

    def test_gap_calibration_chart_holdout_positive_scale_and_cap_exclusion(self):
        charts, players = [f"c{i}" for i in range(10)], [f"p{i}" for i in range(30)]
        ability, effects = np.linspace(-1., 1., len(players)), np.linspace(-.3, .3, len(charts))
        boards = {cid: {player: float(990000 - 2000 * (effects[ci] - ability[pi]))
                        for pi, player in enumerate(players)} for ci, cid in enumerate(charts)}
        data = _prepare(boards, 990000)
        cached = {"charts": {cid: {"ratingEligible": True, "usableContributors": 30,
                  "targetSuccesses": 15, "belowTarget": 15} for cid in charts}}
        ability_by_player = dict(zip(players, ability))
        context = {"data": data, "fit": {"ability": np.array([ability_by_player[player] for player in data["players"]]), "effect": effects}}
        metadata = {cid: {"anchorEligible": True} for cid in charts}
        result = fit_gap_calibration(boards, metadata, cached, context)
        self.assertTrue(result["supported"], result)
        self.assertAlmostEqual(result["pointsPerLatentUnit"], 2000., places=5)
        self.assertFalse(set(result["trainingChartIds"]) & set(result["validationChartIds"]))
        self.assertEqual(result["finalFitRecords"], 300)
        boards["c0"]["p0"] = 1000000
        result = fit_gap_calibration(boards, metadata, cached, context)
        self.assertEqual(result["finalFitRecords"], 299)
        for estimate in cached["charts"].values():
            estimate["targetSuccesses"] = 1
        self.assertFalse(fit_gap_calibration(boards, metadata, cached, context)["supported"])

    def test_recovered_point_fit_matches_cache_and_rejects_different_inputs(self):
        boards = {f"c{i}": {f"p{j}": 991000 if (j+i) % 3 == 0 else 989000 for j in range(12)} for i in range(5)}
        data = _prepare(boards, 990000)
        fit = _fit(data["p"], data["c"], data["y"], len(data["players"]), len(data["mainCharts"]), .01)
        cached = {"policy": {"selectedRegularization": .01},
                  "charts": {cid: {"effect": float(fit["effect"][i])} for i, cid in enumerate(data["mainCharts"])}}
        context = recover_point_fit(boards, cached)
        self.assertEqual(context["diagnostics"]["bootstrapRefits"], 0)
        self.assertEqual(context["diagnostics"]["maximumChartEffectDifference"], 0.)
        cached["charts"]["c0"]["effect"] += .1
        with self.assertRaises(ValueError):
            recover_point_fit(boards, cached)


class OverlayTests(unittest.TestCase):
    def fixture(self):
        c = curve()
        rows = [{"chartId": f"c{i}", "level": 20, "songName": f"c{i}", "scoringVariants": {"sss": metric(20.1+i*.1)}} for i in range(6)]
        rows += [{"chartId": cid, "level": 20, "songName": cid, "scoringVariants": {"sss": metric(value, success=success)}}
                 for cid, value, success in (("none", None, 0), ("all", None, 30), ("rare", 20.3, 1),
                                             ("hidden", None, 0), ("empty", None, 0))]
        players = [f"p{i}" for i in range(30)]
        boards = {f"c{i}": {p: (991000 if j < 6 else 989000) for j, p in enumerate(players)} for i in range(6)}
        boards.update(none={"new": 989000}, all={p: 990551 for p in players},
                      rare={p: (991000 if j == 0 else 989000) for j, p in enumerate(players)},
                      hidden={**{p: 989000 for p in players}, "unknown": 990000})
        data = _prepare(boards, 990000)
        counts = defaultdict_counts(data)
        fit = {"charts": {cid: {"usableContributors": sum(counts[cid]), "targetSuccesses": counts[cid][1],
            "belowTarget": counts[cid][0], "calibratedComponent": True} for cid in boards}}
        payload = {"schemaVersion": 49, "summary": {"method": {"sssScoring": {"calibration": {
            "submitted": {"Single": None, "Double": None}, "official": {"Single": c, "Double": None}}}}},
            "singles": rows, "doubles": [], "coop": [{"untouched": True}]}
        prepared = {"catalog": {cid: {"type": "Single", "level": 20} for cid in boards},
            "submitted": {"boards": {}, "charts": {}}, "official": {"boards": boards, "charts": {}}}
        fits = {source: {mode: fit if source == "official" and mode == "Single" else {"charts": {}}
                        for mode in ("Single", "Double")} for source in ("submitted", "official")}

        def recover(board, cached):
            dd = _prepare(board, 990000)
            return {"data": dd, "fit": None if not len(dd["y"]) else {"ability": np.zeros(len(dd["players"])),
                "effect": np.zeros(len(dd["mainCharts"]))}, "diagnostics": {"converged": bool(len(dd["y"]))}}

        calibration = {"supported": True, "pointsPerLatentUnit": 1000., "policy": {"method": "test", "scoreRange": [980000,999999]},
            "supportedCharts": 10, "records": 300, "validationChartIds": ["x","y"], "validationMeanAbsoluteError": 100.}
        return payload, prepared, fits, recover, calibration

    def test_overlay_classification_snapshot_rare_boundary_and_intervals(self):
        payload, prepared, fits, recover, calibration = self.fixture()
        original = copy.deepcopy((payload, prepared, fits))
        with patch("sss_scoring_refinement.recover_point_fit", side_effect=recover), patch("sss_scoring_refinement.fit_gap_calibration", return_value=calibration):
            result, audit = apply_sss_refinement(payload, prepared, fits)
        self.assertEqual((payload, prepared, fits), original)
        self.assertEqual(result["schemaVersion"], 49)
        self.assertEqual(result["coop"], payload["coop"])
        by_id = {row["chartId"]: row["scoringVariants"]["sss"] for row in result["singles"]}
        for row, old in zip(result["singles"], payload["singles"]):
            self.assertEqual(row["scoringVariants"]["sss"]["scoringFirstPass"], old["scoringVariants"]["sss"])
        self.assertEqual(by_id["none"]["scoringMethod"], BOUNDARY_METHOD)
        self.assertEqual(by_id["none"]["scoringRefinement"]["fullBoard"]["records"], 1)
        self.assertEqual(by_id["none"]["scoringRefinement"]["modelEligible"]["records"], 0)
        self.assertEqual(by_id["all"]["scoringRefinement"]["boundary"]["gapPoints"], 551)
        self.assertLess(by_id["all"]["estimatedDifficulty"], 20.1)
        self.assertEqual(by_id["hidden"]["scoringEstimateBranch"], "insufficient-history")
        self.assertIsNone(by_id["hidden"]["estimatedDifficulty"])
        self.assertEqual(by_id["empty"]["scoringEstimateBranch"], "no-data")
        self.assertEqual(by_id["rare"]["scoringMethod"], RARE_METHOD)
        self.assertAlmostEqual(by_id["rare"]["estimatedDifficulty"], 20.6)
        for cid in ("none", "all", "rare"):
            self.assertIsNone(by_id[cid]["difficultyCi95Low"])
            self.assertIsNone(by_id[cid]["initialFit"]["difficultyCi95High"])
            self.assertEqual(by_id[cid]["evidenceStatus"], "Provisional")
        normal = dict(by_id["c0"])
        normal.pop("scoringFirstPass")
        self.assertEqual(normal, payload["singles"][0]["scoringVariants"]["sss"])
        self.assertEqual(result["summary"]["method"]["sssRefinement"]["ranges"]["official"]["Single"]["20"]["upper"], 20.6)
        self.assertEqual(len(audit["changed"]), 3)

    def test_unsupported_conversion_or_curve_fails_closed(self):
        payload, prepared, fits, recover, calibration = self.fixture()
        calibration.update(supported=False, reason="Unsupported calibration.")
        with patch("sss_scoring_refinement.recover_point_fit", side_effect=recover), patch("sss_scoring_refinement.fit_gap_calibration", return_value=calibration):
            result, _ = apply_sss_refinement(payload, prepared, fits)
        none = next(row["scoringVariants"]["sss"] for row in result["singles"] if row["chartId"] == "none")
        self.assertIsNone(none["estimatedDifficulty"])
        self.assertEqual(none["scoringUnratedReason"], "Unsupported calibration.")

    def test_no_sss_endpoint_averages_up_to_three_before_history_filter(self):
        for scores in ([989999], [989999, 985001], [989999, 985001, 980001],
                       [950000, 989999, 980001, 985001]):
            with self.subTest(scores=scores):
                payload, prepared, fits, recover, calibration = self.fixture()
                prepared["official"]["boards"]["none"] = {f"new-{i}": score for i, score in enumerate(scores)}
                with patch("sss_scoring_refinement.recover_point_fit", side_effect=recover), patch("sss_scoring_refinement.fit_gap_calibration", return_value=calibration):
                    result, _ = apply_sss_refinement(payload, prepared, fits)
                metric = next(r["scoringVariants"]["sss"] for r in result["singles"] if r["chartId"] == "none")
                detail = metric["scoringRefinement"]
                selected = sorted(scores, reverse=True)[:3]
                average = sum(selected) / len(selected)
                self.assertEqual(detail["fullBoard"]["topScores"], selected)
                self.assertEqual(detail["modelEligible"]["records"], 0)
                self.assertEqual(detail["boundary"]["endpointScoreCount"], len(selected))
                self.assertEqual(detail["boundary"]["endpointPolicy"], "mean-top-3-available-v1")
                self.assertEqual(detail["boundary"]["endpointScore"], average)
                self.assertEqual(detail["boundary"]["gapPoints"], 990000-average)
                expected = boundary_estimate("no-sss", average, 20, detail["range"], curve(), 1000.)
                self.assertEqual(metric["estimatedDifficulty"], expected["finalDifficulty"])


def defaultdict_counts(data):
    counts = {cid: [0,0] for cid in data["charts"]}
    for cid, _, outcome in data["allRecords"]:
        counts[cid][int(outcome)] += 1
    return counts


if __name__ == "__main__":
    unittest.main()
