from __future__ import annotations

import copy
import math
import unittest
from statistics import median

from scoring_targets import SSS, SSSPLUS
from scripts.build_local_sssplus_tiers import _valid_replay_policy
from sss_scoring import _composite, _derived, fit_target_curve
from sss_scoring_model import _prepare
from sss_scoring_refinement import boundary_estimate, classify_board
from target_tail_policy import apply_tail_policy, exception_evidence, select_exceptions, tail_delta, tail_value
from tier_postprocessing import FIT_FIELDS


def row(cid, level, delta, *, mode="Single", source=None, center=True, eligible=True):
    value = level + .5 + delta
    metric = {**{field: None for field in FIT_FIELDS}, **_derived(value, level),
        "scoringMethod": "target-attainment-rasch-v1", "scoringEstimateBranch": "mixed",
        "scoringTarget": 995000, "scoringTargetSuccesses": 10, "scoringBelowTarget": 20,
        "nContributors": 30, "scoringSource": source or ("official" if level >= 20 else "submitted"),
        "scoringFitConverged": True, "scoringMainComponent": True, "scoringComparability": "same",
        "scoringProvisionalReasons": [], "scoringPossiblyTruncated": False,
        "scoringExtrapolated": False, "evidenceStatus": "Published", "scoringUnratedReason": None,
        "scoringRefinement": {"range": {"supported": True, "kind": "observed"},
                              "fullBoard": {"possiblyTruncated": False}}}
    metric["initialFit"] = {**{field: None for field in FIT_FIELDS},
        **_derived(level + .5 + 2 * delta, level)}
    for side, offset in (("Low", -.05), ("High", .05)):
        metric[f"difficultyCi95{side}"] = value + offset
        metric[f"difficultyDeltaCi95{side}"] = delta + offset
        metric["initialFit"][f"difficultyCi95{side}"] = level + .5 + 2 * (delta + offset)
        metric["initialFit"][f"difficultyDeltaCi95{side}"] = 2 * (delta + offset)
    if center and level >= 20:
        metric["scoringFolderCentering"] = {"basis": "folder"}
    if not eligible:
        metric["scoringPossiblyTruncated"] = True
    result = {"chartId": cid, "level": level, "type": mode, "songName": cid,
              "scoringVariants": {"sssplus": metric}, "tierMetrics": {"clearing": {
                  "estimatedDifficulty": level + .5, "ratedClearCount": 35,
                  "initialFit": {"estimatedDifficulty": level + .5}, "evidenceStatus": "Published"}},
              "pumbilityVariants": {}}
    result["pumbilityVariants"]["sssplus"] = _composite(result, metric)
    return result


def payload(rows):
    return {"singles": [r for r in rows if r["type"] == "Single"],
            "doubles": [r for r in rows if r["type"] == "Double"]}


def stage_fixture():
    """Small deterministic cross-language stage fixture; no private real player data."""
    import json
    import numpy as np
    from pathlib import Path
    from unittest.mock import patch
    from tests.test_sss_scoring import fixture as general_fixture
    from sss_scoring import apply_sss_variants, payload_sha256, rank_variants
    from sss_scoring_refinement import apply_sss_refinement
    from sss_folder_centering import apply_folder_centering
    from refined_tiers import INTEGRATION_METHOD
    from sssplus_tiers import apply_sssplus_tiers

    general, prepared, fits = general_fixture()
    from tier_postprocessing import _rank_secondary
    for key in ("singles", "doubles"):
        for row_ in general[key]:
            row_["tierMetrics"]["pumbility"].update(scoringSupportCount=31,clearingSupportCount=30,aggregation="average",
                initialFit={**_derived(row_["level"]+.8,row_["level"]),"levelRank":None,"levelComparisonCharts":None})
        _rank_secondary(general[key],"clearing")
        _rank_secondary(general[key],"pumbility")
    for source in ("submitted", "official"):
        prepared[source]["boards"] = {}
        for mode in ("Single", "Double"):
            for cid in fits[source][mode]["charts"]:
                if source == "official":
                    prepared[source]["charts"][cid]["comparability"] = "same"
                prepared[source]["boards"][cid] = {f"synthetic{j}": 997000 if j < 10 else 985000 for j in range(25)}
    first = apply_sss_variants(general, prepared, fits, source_hashes={"submitted":"b"*64}, bootstrap_samples=200, seed=7)

    def context(boards, cached, **kwargs):
        data = _prepare(boards, kwargs.get("score_target",990000))
        return {"data": data, "fit": None if not len(data["y"]) else {
            "ability": np.zeros(len(data["players"])), "effect": np.zeros(len(data["mainCharts"]))},
            "diagnostics": {"converged": bool(len(data["y"])), "pointFits": 1, "bootstrapRefits": 0}}

    with patch("sss_scoring_refinement.recover_point_fit",side_effect=context):
        refined, _ = apply_sss_refinement(first, prepared, fits)
    refined["schemaVersion"] = 50
    # Complete public official Clearing policy is unchanged; this fixture has no D26+ charts.
    refined["summary"]["method"]["officialHighLevelClearing"] = {"version":1,"method":"official-d26-clearer-ability-v1",
        "source":"piuscores-official","mix":"Phoenix2","mode":"Double","minimumLevel":26,
        "percentile":.2,"difficultyDeltaScale":.7,"compressionFactor":.5,
        "playerSkill":{"method":"leave-one-chart-out-top-official-levels","topCharts":25,"minimumOtherCharts":25,
            "historyMinimumLevel":23,"levelMidpointOffset":.5,"separateModes":True},"uncertainty":{"samples":1000}}
    refined["summary"]["method"]["refinedTiers"] = {"version":1,"method":INTEGRATION_METHOD,
        "baseSchemaVersion":49,"basePayloadSha256":payload_sha256(first),"localOnly":True,
        "clearingDispatch":{"mode":"Double","minimumLevel":26},"pumbilityAggregation":"average","compressionApplications":1}
    for key in ("singles", "doubles"):
        for row_, old in zip(refined[key],first[key]):
            row_["refinementBaseline"] = {"sssPumbility":copy.deepcopy(old["pumbilityVariants"]["sss"])}
        rank_variants(refined[key])
    historical = apply_folder_centering(refined)
    target_fits = copy.deepcopy(fits)
    for source in ("submitted", "official"):
        for mode in ("Single", "Double"):
            target_fits[source][mode]["policy"] = {"scoreTarget":995000}
    target_fits["official"]["Single"]["charts"]["Single-20-0"].update(effect=-100., effectLow=-100.1,effectHigh=-99.9)
    target_fits["official"]["Double"]["charts"]["Double-22-4"].update(effect=100.,effectLow=99.9,effectHigh=100.1)
    with patch("sss_scoring_refinement.recover_point_fit",side_effect=context):
        candidate, _ = apply_sssplus_tiers(historical,prepared,target_fits,source_hashes={"submitted":"b"*64},bootstrap_samples=200,seed=7)
    return historical,candidate


class TargetThresholdTests(unittest.TestCase):
    def test_inclusive995000_and_fractional_scores_and_historical_target(self):
        board = {str(index): score for index, score in enumerate([990000, 994999, 994999.9, 995000, 995000.1, 1000000])}
        self.assertEqual(classify_board(board, score_target=995000, generic_branches=True)["successes"], 3)
        self.assertEqual(classify_board(board)["successes"], 6)
        self.assertEqual(SSS.score, 990000)
        self.assertEqual(SSSPLUS.public()["nearTargetScoreRange"], [985000, 999999])
        boards = {f"c{i}": board for i in range(4)}
        self.assertEqual(int(sum(_prepare(boards, 995000)["y"])), 12)

    def test_no_target_top3_one_two_three_and_all_target_zero_gap(self):
        curve = fit_target_curve([{"level": 20+i, "cost": float((i+1)**2)} for i in range(3)], minimum_level=20)
        for scores in ([994999], [994999, 990001], [994999, 990001, 980000], [994999, 990001, 980000, 900000]):
            detail = classify_board(dict(enumerate(scores)), score_target=995000, generic_branches=True)
            self.assertEqual(detail["branch"], "no-target")
            self.assertEqual(len(detail["topScores"]), min(3, len(scores)))
            endpoint = sum(detail["topScores"])/len(detail["topScores"])
            result = boundary_estimate("no-target", endpoint, 20, {"lower": 20., "upper": 21.}, curve, 1000., score_target=995000)
            self.assertEqual(result["gapPoints"], 995000-endpoint)
        result = boundary_estimate("all-target", 995000, 20, {"lower": 20., "upper": 21.}, curve, 1000., score_target=995000)
        self.assertAlmostEqual(result["finalDifficulty"], 20.)

    def test_cache_rejects990000_and_wrong_bootstrap(self):
        fit = {"policy": {"modelVersion": "target-rasch-observed-best-v1", "scoreTarget": 995000,
            "minimumPlayerCharts": 4, "weighting": "each-player-total-one-inverse-observed-chart-count-v1",
            "bootstrapSamples": 200, "regularizationGrid": [.01,.05,.2,1.]}, "diagnostics": {}}
        self.assertTrue(_valid_replay_policy(fit, 200))
        fit["policy"]["scoreTarget"] = 990000
        self.assertFalse(_valid_replay_policy(fit, 200))
        fit["policy"]["scoreTarget"] = 995000
        self.assertFalse(_valid_replay_policy(fit, 100))


class PipelineTests(unittest.TestCase):
    def test_stage_replay_preserves_historical_payload_and_rejects_stale_tail(self):
        from sssplus_tiers import recover_sssplus_baseline, validate_sssplus_tiers
        base,candidate=stage_fixture()
        self.assertEqual(recover_sssplus_baseline(candidate),base)
        self.assertEqual(validate_sssplus_tiers(candidate,base),base)
        for key in ("singles","doubles"):
            for before,after in zip(base[key],candidate[key]):
                self.assertEqual(before["tierMetrics"],after["tierMetrics"])
                self.assertEqual(before["scoringVariants"]["sss"],after["scoringVariants"]["sss"])
                target=after["scoringVariants"]["sssplus"]
                composite=after["pumbilityVariants"]["sssplus"]
                self.assertEqual(composite["estimatedDifficulty"],(target["estimatedDifficulty"]+after["tierMetrics"]["clearing"]["estimatedDifficulty"])/2)
        bad=copy.deepcopy(candidate)
        bad["singles"][0]["scoringVariants"]["sssplus"]["estimatedDifficulty"]+=.1
        with self.assertRaises(ValueError): validate_sssplus_tiers(bad,base)


class TailPolicyTests(unittest.TestCase):
    def test_continuous_odd_monotone_central_identity_and_extreme_finite(self):
        values = [-1e308, -100, -2, -1.500001, -1.5, -.3, 0, .3, 1.5, 1.500001, 2, 100, 1e308]
        result = [tail_delta(value) for value in values]
        self.assertEqual(result, sorted(result))
        for value in values:
            self.assertEqual(tail_delta(-value), -tail_delta(value))
            self.assertLessEqual(abs(tail_delta(value)), 1.9)
            if abs(value) <= 1.5:
                self.assertEqual(tail_delta(value), value)
        self.assertAlmostEqual((tail_delta(1.5+1e-7)-1.5)/1e-7, 1., places=5)
        self.assertEqual(tail_delta(100, exceptional=True), 2.5)
        self.assertEqual(tail_delta(-100, exceptional=True), -2.5)
        with self.assertRaises(ValueError): tail_delta(float("inf"))

    def test_odd_even_centered_medians_order_and_tied_values(self):
        for deltas in ([-6,-2,-.1,0,.1,2,6], [-6,-2,-.1,.1,2,6], [-6,-6,0,6,6]):
            data = payload([row(f"c{i}", 20, delta, eligible=False) for i, delta in enumerate(deltas)])
            summary = apply_tail_policy(data)
            values = [r["scoringVariants"]["sssplus"]["estimatedDifficulty"] for r in data["singles"]]
            self.assertAlmostEqual(median(values), 20.5)
            self.assertEqual(values, sorted(values))
            self.assertEqual(summary["counts"]["finalAbsAtLeast2"], 0)
            self.assertEqual(summary["counts"]["displayedAbsAtLeast2"], 0)

    def test_single_global_budget_spans_modes_sources_levels_and_signs(self):
        rows = [row(f"S{i:02d}", 16+i, 2.3+i*.01, center=False) for i in range(7)]
        rows += [row(f"D{i:02d}", 20+i, -2.3-i*.01, mode="Double", center=False) for i in range(7)]
        # Measured centering is required for official folders; add central rows to avoid median protection.
        for r in rows:
            if r["level"] >= 20:
                r["scoringVariants"]["sssplus"]["scoringFolderCentering"] = {"basis": "folder"}
        for index, r in enumerate(list(rows)):
            if r["level"] >= 20:
                rows.extend([row(f"central{index}-{j}", r["level"], (j-1)*.1, mode=r["type"]) for j in range(3)])
        data = payload(rows)
        chosen, _ = select_exceptions(data)
        self.assertEqual(len(chosen), 10)
        self.assertTrue(any(cid.startswith("S") for cid in chosen))
        self.assertTrue(any(cid.startswith("D") for cid in chosen))
        summary = apply_tail_policy(data)
        self.assertEqual(summary["counts"]["finalAbsAtLeast2"], 10)
        self.assertLessEqual(summary["counts"]["displayedAbsAtLeast2"], 10)

    def test_tie_blocks_do_not_split_and_ineligible_outer_blocks_inner(self):
        rows = [row(f"t{i:02d}", 19, 2.3, center=False) for i in range(11)]
        self.assertEqual(select_exceptions(payload(rows))[0], set())
        rows = [row("outer", 19, 3., center=False, eligible=False), row("inner", 19, 2.4, center=False)]
        selected, detail = select_exceptions(payload(rows))
        self.assertEqual(selected, set())
        self.assertIn("ineligible-outer-chart-blocks-tail", detail["inner"]["rejectionReasons"])

    def test_protected_central_pair_and_unavailable_or_crossing_intervals(self):
        data = payload([row("low",20,-2.3), row("high",20,2.3)])
        self.assertEqual(select_exceptions(data)[0], set())
        for r in data["singles"]:
            self.assertIn("protected-folder-median", select_exceptions(data)[1][r["chartId"]]["rejectionReasons"])
        r = row("uncertain",19,2.3,center=False)
        r["scoringVariants"]["sssplus"]["difficultyCi95Low"] = 21.0
        self.assertFalse(exception_evidence(r,goal="sssplus",protected=False)["exceptionEligible"])
        r["scoringVariants"]["sssplus"]["difficultyCi95Low"] = None
        self.assertIn("unavailable-player-bootstrap-interval", exception_evidence(r,goal="sssplus",protected=False)["rejectionReasons"])

    def test_cap_boundary_rare_and_borrowed_never_qualify(self):
        for field, value in (("scoringPossiblyTruncated", True), ("scoringMethod", "target-boundary-score-gap-v1"),
                             ("scoringMethod", "target-rare-success-upper-band-v1"),
                             ("scoringExtrapolated",True), ("scoringFolderCentering",{"basis":"nearest"})):
            r = row("bad",20,2.3)
            r["scoringVariants"]["sssplus"][field] = value
            self.assertFalse(exception_evidence(r,goal="sssplus",protected=False)["exceptionEligible"])

    def test_interval_uses_fixed_selected_map_and_preserves_original_fit(self):
        data = payload([row("exception",19,2.3,center=False), row("ordinary",19,-9,center=False,eligible=False)])
        original = copy.deepcopy(data)
        apply_tail_policy(data)
        for before, after in zip(original["singles"], data["singles"]):
            old, new = before["scoringVariants"]["sssplus"], after["scoringVariants"]["sssplus"]
            selected = new["scoringTailPolicy"]["exceptionSelected"]
            self.assertEqual(new["initialFit"], old["initialFit"])
            self.assertEqual(new["difficultyCi95Low"], tail_value(old["difficultyCi95Low"],19,exceptional=selected))
            self.assertEqual(after["sssplusTailBaseline"]["scoring"], old)


if __name__ == "__main__":
    unittest.main()
