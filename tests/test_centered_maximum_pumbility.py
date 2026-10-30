import copy
import json
import unittest
from pathlib import Path
from statistics import median

from centered_maximum_pumbility import (
    CALCULATION, METHOD, NAMESPACE, STAGE, apply_centered_maximum_pumbility,
    recover_centered_maximum_pumbility, validate_centered_maximum_pumbility,
)
from retained_tiers import scoring_goals
from sss_scoring import _derived, payload_sha256


def fixtures():
    return json.loads((Path(__file__).parent / "fixtures/approved-tier-ratings.json").read_text(encoding="utf-8"))


def half_downward_fixture_base():
    base = recover_centered_maximum_pumbility(fixtures()["candidate"])
    # Anti-correlated component values preserve each component's folder median
    # while making the median of their maximum strictly exceed the midpoint.
    for key, level in (("singles", 24), ("doubles", 25)):
        rows = [row for row in base[key] if row["level"] == level]
        for row, delta in zip(rows, (.4, .3, 0., -.3, -.4)):
            for goal in scoring_goals(base):
                scoring = row["scoringVariants"][goal]
                scoring.update(_derived(level + .5 + delta, level))
                for side in ("Low", "High"):
                    field = "difficultyCi95" + side
                    if scoring.get(field) is not None:
                        scoring[field] += delta
                        scoring["difficultyDeltaCi95" + side] = scoring[field] - level - .5
            row.update(_derived(row["scoringVariants"]["general"]["estimatedDifficulty"], level))
    return base


class CenteredMaximumPumbilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = fixtures()
        cls.legacy = data["legacyCandidate"]
        cls.base = half_downward_fixture_base()
        cls.candidate, cls.report = apply_centered_maximum_pumbility(cls.base)

    def test_downward_offsets_are_halved_before_points_and_intervals_are_derived(self):
        marker = self.candidate["summary"]["method"][NAMESPACE]
        self.assertEqual((marker["version"], marker["method"], marker["calculation"]), (2, METHOD, CALCULATION))
        self.assertEqual((marker["downwardAdjustmentScale"], marker["upwardAdjustmentScale"]), (.5, 1))
        negatives = 0
        for key, mode in (("singles", "Single"), ("doubles", "Double")):
            for level in {row["level"] for row in self.candidate[key]}:
                rows = [row for row in self.candidate[key] if row["level"] == level]
                for goal in scoring_goals(self.base):
                    record = marker["folders"][mode][str(level)][goal]
                    full = record["fullOffset"]
                    offset = full * .5 if full < 0 else full
                    self.assertEqual(record["offset"], offset)
                    negatives += full < 0
                    for row in rows:
                        scoring = row["scoringVariants"][goal]
                        clearing = row["tierMetrics"]["clearing"]["estimatedDifficulty"]
                        metric = row["pumbilityVariants"][goal]
                        raw = None if scoring["estimatedDifficulty"] is None or clearing is None else max(scoring["estimatedDifficulty"], clearing)
                        self.assertEqual(metric[NAMESPACE], {"version": 2, "unshiftedDifficulty": raw,
                                                             "fullOffset": full, "offset": offset})
                        self.assertEqual(metric["estimatedDifficulty"], None if raw is None else raw + offset)
                        for side in ("Low", "High"):
                            endpoint = scoring.get("difficultyCi95" + side)
                            expected = None if raw is None or endpoint is None else max(endpoint, clearing) + offset
                            self.assertEqual(metric["difficultyCi95" + side], expected)
        self.assertGreater(negatives, 0)

    def test_positive_zero_negative_even_odd_singleton_empty_and_tied_populations(self):
        for pairs in (
            [(25., 24.)], [(24., 23.)], [(24.5, 24.)],
            [(24., 25.), (25., 24.)],
            [(24.1, 24.), (24.7, 24.3), (24.8, 24.2)],
            [(None, 24.1), (24.3, None)],
        ):
            with self.subTest(pairs=pairs):
                base = copy.deepcopy(self.base)
                template = base["singles"][0]
                base["singles"], base["doubles"] = [], []
                for i, (scoring, clearing) in enumerate(pairs):
                    row = copy.deepcopy(template)
                    row.update(chartId=f"edge-{i}", level=24)
                    row["tierMetrics"]["clearing"].update(_derived(clearing, 24))
                    for goal in scoring_goals(base):
                        row["scoringVariants"][goal].update(_derived(scoring, 24))
                        row["scoringVariants"][goal].update(difficultyCi95Low=None if scoring is None else scoring - .2,
                                                           difficultyCi95High=None if scoring is None else scoring + .2)
                        if scoring is None or clearing is None:
                            row["pumbilityVariants"][goal].update(_derived(None, 24), evidenceStatus="Unrated")
                    base["singles"].append(row)
                result, report = apply_centered_maximum_pumbility(base)
                for goal in scoring_goals(base):
                    raw = [max(s, c) for s, c in pairs if s is not None and c is not None]
                    values = [row["pumbilityVariants"][goal]["estimatedDifficulty"] for row in result["singles"]]
                    record = result["summary"]["method"][NAMESPACE]["folders"]["Single"]["24"][goal]
                    if raw:
                        full = 24.5 - median(raw)
                        expected = median(raw) + (full * .5 if full < 0 else full)
                        self.assertAlmostEqual(median(v for v in values if v is not None), expected)
                        if len(raw) == 2 and raw[0] == raw[1]:
                            self.assertEqual(values[0], values[1])
                    else:
                        self.assertEqual(record["offset"], 0.)
                        self.assertEqual(record["fullOffset"], 0.)
                        self.assertEqual(values, [None] * len(pairs))
                        self.assertTrue(all(row["pumbilityVariants"][goal]["levelRank"] is None for row in result["singles"]))
                for record in report["folders"]:
                    self.assertEqual(record["finalMedian"], None if not record["ratedCharts"] else
                                     record["medianBefore"] + record["offset"])

    def test_components_evidence_history_aliases_ranks_and_exact_recovery_are_preserved(self):
        before_hash = payload_sha256(self.base)
        self.assertEqual(recover_centered_maximum_pumbility(self.candidate), self.base)
        self.assertEqual(validate_centered_maximum_pumbility(self.candidate, self.base), self.base)
        self.assertEqual(payload_sha256(self.base), before_hash)
        self.assertEqual(self.candidate["coop"], self.base["coop"])
        full, _ = apply_centered_maximum_pumbility(self.base, policy_version=1)
        for key in ("singles", "doubles"):
            for old, row, old_full in zip(self.base[key], self.candidate[key], full[key]):
                self.assertEqual(row["scoringVariants"], old["scoringVariants"])
                self.assertEqual(row["tierMetrics"]["clearing"], old["tierMetrics"]["clearing"])
                self.assertEqual(row["tierMetrics"]["pumbility"], row["pumbilityVariants"]["general"])
                self.assertEqual({k: v for k, v in old.items() if k not in ("tierMetrics", "pumbilityVariants")},
                                 {k: v for k, v in row.items() if k not in ("tierMetrics", "pumbilityVariants", STAGE)})
                for goal in scoring_goals(self.base):
                    metric, prior = row["pumbilityVariants"][goal], old["pumbilityVariants"][goal]
                    for field in ("evidenceStatus", "scoringSupportCount", "clearingSupportCount", "initialFit"):
                        self.assertEqual(metric.get(field), prior.get(field))
                    self.assertEqual(metric["levelRank"], old_full["pumbilityVariants"][goal]["levelRank"])
                    self.assertEqual(metric["levelComparisonCharts"], old_full["pumbilityVariants"][goal]["levelComparisonCharts"])

    def test_legacy_full_adjustment_payload_validates_and_recovers_without_reinterpretation(self):
        self.assertEqual(self.legacy["summary"]["method"][NAMESPACE]["version"], 1)
        restored = validate_centered_maximum_pumbility(self.legacy)
        recreated, _ = apply_centered_maximum_pumbility(restored, policy_version=1)
        self.assertEqual(recreated, self.legacy)

    def test_report_uses_actual_final_medians(self):
        self.assertEqual(self.report["schemaVersion"], 162)
        raised_medians = 0
        for record in self.report["folders"]:
            rows = self.candidate["singles" if record["mode"] == "Single" else "doubles"]
            values = [row["pumbilityVariants"][record["goal"]]["estimatedDifficulty"] for row in rows
                      if row["level"] == record["level"]]
            values = [value for value in values if value is not None]
            self.assertEqual(record["finalMedian"], median(values) if values else None)
            raised_medians += bool(values and record["finalMedian"] > record["level"] + .5 + 1e-8)
        self.assertGreater(raised_medians, 0)

    def test_tampering_and_reapplication_are_rejected(self):
        mutations = (
            lambda p: p["summary"]["method"][NAMESPACE].update(version=3),
            lambda p: p["summary"]["method"][NAMESPACE].update(downwardAdjustmentScale=1),
            lambda p: p["summary"]["method"][NAMESPACE].update(upwardAdjustmentScale=.5),
            lambda p: p["singles"][0]["pumbilityVariants"]["sssplus"][NAMESPACE].update(fullOffset=99),
            lambda p: p["singles"][0]["pumbilityVariants"]["sssplus"][NAMESPACE].update(offset=99),
            lambda p: p["singles"][0]["pumbilityVariants"]["sssplus"].update(estimatedDifficulty=99),
            lambda p: p["singles"][0]["pumbilityVariants"]["sssplus"].update(difficultyCi95Low=99),
            lambda p: p["singles"][0]["pumbilityVariants"]["sssplus"].update(levelRank=999),
        )
        for mutate in mutations:
            payload = copy.deepcopy(self.candidate)
            mutate(payload)
            with self.assertRaises(ValueError):
                validate_centered_maximum_pumbility(payload)
        with self.assertRaises(ValueError):
            apply_centered_maximum_pumbility(self.candidate)


if __name__ == "__main__":
    unittest.main()
