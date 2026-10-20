from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path
from statistics import median

from level20_scoring_width import (FIELDS, GOALS, INTERVAL_METHOD, METHOD, NAMESPACE,
    POLICY, STAGE, apply_level20_scoring_width, distribution, recover_level20_scoring_width,
    source_values, validate_level20_scoring_width, validate_public_level20_scoring_width,
    width_parameters, width_value)
from sss_scoring import RANK_FIELDS, _composite, rank_variants
from target_tail_policy import apply_tail_policy
from tests.test_sssplus_tiers import row


def synthetic_base(n=101, *, goals=GOALS, values=None):
    charts = []
    values = values if values is not None else [20.5+10*((i-(n-1)/2)/((n-1)/2))**3 for i in range(n)]
    for mode in ("Single", "Double"):
        for level in (19, 20, 21):
            for i, value in enumerate(values if level == 20 else [level+.4, level+.5, level+.6]):
                chart = row(f"{mode}-{level}-{i:03}", level, value-level-.5, mode=mode)
                metric = chart["scoringVariants"]["sssplus"]
                for goal in goals:
                    chart["scoringVariants"][goal] = copy.deepcopy(metric)
                    chart["pumbilityVariants"][goal] = _composite(chart, chart["scoringVariants"][goal])
                for goal in set(GOALS)-set(goals):
                    chart["scoringVariants"].pop(goal, None)
                    chart["pumbilityVariants"].pop(goal, None)
                chart["scoringVariants"]["general"] = {"estimatedDifficulty": level+.3, "untouched": [1, 2]}
                chart["pumbilityVariants"]["general"] = {"estimatedDifficulty": level+.4, "untouched": [3, 4]}
                charts.append(chart)
    result = {"schemaVersion": 59, "generatedAtUtc": "2026-10-21T00:00:00Z",
        "summary": {"scriptVersion": "frozen-59", "method": {"unchanged": True}},
        "singles": [c for c in charts if c["type"] == "Single"],
        "doubles": [c for c in charts if c["type"] == "Double"], "coop": [{"unchanged": True}]}
    for goal in goals:
        apply_tail_policy(result, goal=goal)
        for key in ("singles", "doubles"):
            for chart in result[key]:
                chart["pumbilityVariants"][goal] = _composite(chart, chart["scoringVariants"][goal])
            rank_variants(result[key], goal=goal)
    return result


def parameters(values, **kwargs):
    return width_parameters([{"id": str(i), "input": v, "metric": kwargs.get("metric", {})}
                             for i, v in enumerate(values)])


class Level20WidthTests(unittest.TestCase):
    def test_all_eight_populations_meet_revised_distribution_without_clipping(self):
        base = synthetic_base()
        saved = copy.deepcopy(base)
        result = apply_level20_scoring_width(base)
        self.assertEqual(base, saved)
        self.assertEqual(recover_level20_scoring_width(result), base)
        self.assertEqual(validate_level20_scoring_width(result, full=False), base)
        folders = result["summary"]["method"][NAMESPACE]["folders"]
        self.assertEqual(len(folders), 8)
        for folder in folders.values():
            self.assertTrue(folder["coreTargetMet"])
            self.assertTrue(.6 <= folder["after"]["coreFraction"] <= .7)
            self.assertEqual(folder["after"]["deepTailCharts"], 0)
            self.assertEqual(folder["after"]["extremeTailCharts"], 0)
            self.assertEqual(folder["after"]["distinctPoints"], folder["before"]["distinctPoints"])
            self.assertEqual(folder["after"]["median"], 20.5)

    def test_recovery_preserves_unrelated_metrics_raw_fits_and_evidence(self):
        base = synthetic_base(40)
        result = apply_level20_scoring_width(base)
        self.assertEqual(result["coop"], base["coop"])
        for key in ("singles", "doubles"):
            for old, chart in zip(base[key], result[key]):
                self.assertEqual(old["tierMetrics"], chart["tierMetrics"])
                self.assertEqual(old["scoringVariants"]["general"], chart["scoringVariants"]["general"])
                self.assertEqual(old["pumbilityVariants"]["general"], chart["pumbilityVariants"]["general"])
                for goal in GOALS:
                    before, after = old["scoringVariants"][goal], chart["scoringVariants"][goal]
                    self.assertEqual(before["initialFit"], after["initialFit"])
                    self.assertEqual(before["scoringRefinement"], after["scoringRefinement"])
                    self.assertEqual(before["scoringTailPolicy"], after["scoringTailPolicy"])
                    if chart["level"] != 20:
                        for field in (*FIELDS, "intervalMethod"):
                            self.assertEqual(before.get(field), after.get(field))

    def test_order_ties_median_pair_and_conditional_intervals(self):
        values = [10+i*.2 for i in range(100)]
        values[10] = values[11]
        p = parameters(values)
        out = [width_value(v, p) for v in values]
        self.assertEqual(out, sorted(out))
        self.assertEqual(out[10], out[11])
        self.assertEqual(len(set(values)), len(set(out)))
        self.assertAlmostEqual(median(out), 20.5)
        dense = [width_value(0+i/100, p) for i in range(5001)]
        self.assertTrue(all(a < b for a, b in zip(dense, dense[1:])))
        self.assertGreater(dense[0], 18.7)
        self.assertLess(dense[-1], 22.4)
        self.assertIsNone(width_value(None, p))
        for bad in (math.nan, math.inf, True):
            with self.assertRaises(ValueError):
                width_value(bad, p)

    def test_sparse_and_tied_populations_are_reported_without_rank_jitter(self):
        for values, basis in (([20.5]*100, "tied-core"), ([20., 20.5, 21.], "insufficient-charts")):
            p = parameters(values)
            self.assertEqual(p["basis"], basis)
            self.assertEqual([width_value(v, p) for v in values], values)
        base = synthetic_base(values=[20.5]*25)
        result = apply_level20_scoring_width(base)
        for p in result["summary"]["method"][NAMESPACE]["folders"].values():
            self.assertFalse(p["coreTargetMet"])
            self.assertEqual(p["diagnostics"], ["tied-or-sparse-population-preserved"])

    def test_optional_goals_and_unrated_charts_do_not_remove_existing_goals(self):
        base = synthetic_base(30, goals=("sss", "sssplus"))
        result = apply_level20_scoring_width(base)
        self.assertEqual(result["summary"]["method"][NAMESPACE]["goals"], ["sss", "sssplus"])
        self.assertEqual(recover_level20_scoring_width(result), base)

    def test_source_reads_post_width_tail_and_preserves_supported_exceptions(self):
        metric = {"estimatedDifficulty": 18.7, "difficultyCi95Low": 18.65, "difficultyCi95High": 18.75,
            "finalFolderWidth": {"input": {"estimatedDifficulty": -100}},
            "scoringTailPolicy": {"preTailDifficulty": 15., "preTailDifficultyCi95Low": 14., "preTailDifficultyCi95High": 16.}}
        points, source = source_values(metric)
        self.assertEqual(points, dict(zip(FIELDS, (15., 14., 16.))))
        self.assertEqual(source, "stored-final-width-pre-tail")
        metric["scoringTailPolicy"]["exceptionSelected"] = True
        points, source = source_values(metric)
        self.assertEqual(points["estimatedDifficulty"], 15.)
        self.assertEqual(source, "stored-final-width-pre-tail")
        with self.assertRaises(ValueError):
            source_values({"estimatedDifficulty": 18.6})

    def test_deep_and_extreme_guards_require_existing_support_and_never_split_ties(self):
        values = [-1e8, -1e6, -1e5, -1e4, -1e3]+[19+i*.03 for i in range(96)]
        eligible = {"nContributors": 200, "scoringComparability": "same", "scoringTailPolicy": {
            "exceptionSelected": True, "exceptionEligible": True, "preTailDifficultyCi95High": 17.9}}
        points = [{"id": str(i), "input": v, "metric": eligible if i < 5 else {}} for i, v in enumerate(values)]
        p = width_parameters(points)
        out = [width_value(v, p) for v in values]
        self.assertEqual(len(p["deepEligibleChartIds"]), 5)
        self.assertEqual(len(p["extremeEligibleChartIds"]), 1)
        self.assertEqual(out, sorted(out))
        self.assertLessEqual(distribution(out)["deepTailCharts"], 5)
        self.assertLessEqual(distribution(out)["extremeTailCharts"], 1)
        self.assertGreaterEqual(min(out), 18)
        for point in points:
            point["metric"] = {}
        p = width_parameters(points)
        self.assertEqual(p["lowerGuards"], [])
        self.assertGreater(min(width_value(v, p) for v in values), 18.7)
        # An ineligible outer chart blocks the entire tail; a six-way outer tie
        # cannot be split to manufacture the requested exception count.
        tied = [{"id": str(i), "input": -100 if i < 6 else 19+i*.03, "metric": eligible} for i in range(101)]
        self.assertEqual(width_parameters(tied)["deepEligibleChartIds"], [])

    def test_upper_guard_preserves_existing_exceptions_without_expanding_budget(self):
        values = [18+i*.04 for i in range(96)]+[100., 1000., 10000., 100000., 1000000.]
        points = [{"id": str(i), "input": v, "metric": {"scoringTailPolicy": {"exceptionSelected": i >= 96}}}
                  for i, v in enumerate(values)]
        p = width_parameters(points)
        out = [width_value(v, p) for v in values]
        self.assertEqual(p["upperLimit"], 22.4)
        self.assertEqual(len(p["upperEligibleChartIds"]), 5)
        self.assertEqual(out, sorted(out))
        self.assertTrue(all(v < 22.4 for v in out[:96]))
        self.assertTrue(all(v < 23 for v in out[96:]))
        self.assertLessEqual(sum(v >= 22.4 for v in out), 5)

    def test_reapplication_mutations_and_non_full_payload_are_rejected(self):
        base = synthetic_base(25)
        result = apply_level20_scoring_width(base)
        with self.assertRaises(ValueError):
            apply_level20_scoring_width(result)
        compact = {**base, "tierListView": {"kind": "list"}}
        with self.assertRaises(ValueError):
            apply_level20_scoring_width(compact)
        for change in ("point", "interval", "composite", "policy", "outside", "evidence"):
            altered = copy.deepcopy(result)
            chart = next(c for c in altered["singles"] if c["level"] == 20)
            if change == "point":
                chart["scoringVariants"]["ss"]["estimatedDifficulty"] += .1
            elif change == "interval":
                chart["scoringVariants"]["ss"]["difficultyCi95Low"] -= .1
            elif change == "composite":
                chart["pumbilityVariants"]["ss"]["estimatedDifficulty"] += .1
            elif change == "policy":
                altered["summary"]["method"][NAMESPACE]["policy"]["targetCoreFraction"] = .8
            elif change == "outside":
                altered["singles"][0]["scoringVariants"]["general"]["estimatedDifficulty"] += .1
            else:
                chart["scoringVariants"]["ss"]["scoringTargetSuccesses"] += 1
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_level20_scoring_width(altered, full=False)

    def test_public_validation_cannot_skip_populations_by_omitting_goals(self):
        result = apply_level20_scoring_width(synthetic_base(25))
        result["summary"]["method"][NAMESPACE]["goals"] = []
        result["summary"]["method"][NAMESPACE]["folders"] = {}
        with self.assertRaisesRegex(ValueError, "target availability"):
            validate_public_level20_scoring_width(result)

    def test_historical_schema59_predecessor_remains_fully_valid(self):
        from tests.test_sss_tail_tiers import historical_payloads
        from sss_tail_tiers import apply_sss_tail_tiers
        baseline = apply_sss_tail_tiers(historical_payloads()[1])
        result = apply_level20_scoring_width(baseline)
        self.assertEqual(validate_level20_scoring_width(result), baseline)


def write_fixture():
    """Bounded, synthetic cross-language curve replay; no player/source data."""
    cases = []
    for name, values in (("asymmetric", [10+20*(i/100)**3 for i in range(101)]),
                         ("even", [10+i*.2 for i in range(100)]),
                         ("tied", [20.5]*25), ("sparse", [19., 20.5, 21.])):
        p = parameters(values)
        inputs = sorted(set([*values, 0., 18., 18.6, 20., 20.5, 21., 23., 50.]))
        cases.append({"name": name, "parameters": p,
                      "vectors": [{"input": v, "output": width_value(v, p)} for v in inputs],
                      "distribution": distribution([width_value(v, p) for v in values])})
    target = Path(__file__).parent / "fixtures" / "level20-scoring-width.json"
    target.write_text(json.dumps({"version": 1, "method": METHOD, "policy": POLICY, "cases": cases},
                                 sort_keys=True, separators=(",", ":"))+"\n", encoding="utf-8")


if __name__ == "__main__":
    import sys
    if "--write-fixture" in sys.argv:
        write_fixture()
    else:
        unittest.main()
