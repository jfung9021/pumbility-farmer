from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from statistics import median

from canonical_json import canonical_json_sha256 as digest
from scoring_targets import SSS
from sss_scoring import RANK_FIELDS, _composite, rank_variants
from sss_tail_tiers import (NAMESPACE, SCRIPT_VERSION, apply_sss_tail_tiers,
                            recover_sss_tail_tiers, validate_sss_tail_tiers)
from target_tail_policy import apply_tail_policy, exception_evidence, select_exceptions, tail_value
from tests.test_sssplus_tiers import payload, row


def sss_row(cid, level, delta, **kwargs):
    result = row(cid, level, delta, **kwargs)
    scoring = result["scoringVariants"].pop("sssplus")
    scoring.update(scoringMethod=SSS.model_method, scoringTarget=SSS.score)
    result["scoringVariants"]["sss"] = scoring
    result["pumbilityVariants"]["sss"] = result["pumbilityVariants"].pop("sssplus")
    for goal in ("general", "ss", "ssplus", "sssplus"):
        result["scoringVariants"][goal] = {"estimatedDifficulty": level + .7, "preserved": goal}
        result["pumbilityVariants"][goal] = {"estimatedDifficulty": level + .6, "preserved": goal}
    return result


def synthetic_base(rows, *, schema=57):
    result = payload(rows)
    result.update(schemaVersion=schema, generatedAtUtc="2026-10-20T00:00:00Z", mix="combined")
    result["summary"] = {"scriptVersion": "frozen-predecessor", "method": {"preserved": {"value": 1}}}
    for key in ("singles", "doubles"):
        rank_variants(result[key], goal="sss")
    return result


def historical_payloads():
    """Reconstruct exactly the checked-in Python/TypeScript predecessor fixtures."""
    directory = Path(__file__).parent / "fixtures"
    load = lambda name: json.loads((directory / name).read_text(encoding="utf-8"))
    source = load("source-aware-width.json")["candidate"]
    submitted, goals = load("submitted-sssplus-calibration.json"), load("scoring-goal-overlays.json")
    base = copy.deepcopy(source)
    base["schemaVersion"] = 57
    base["summary"]["method"]["submittedSssplusCalibration"] = submitted["method"]
    for saved in submitted["doubles"]:
        chart = next(chart for chart in base["doubles"] if chart["chartId"] == saved["chartId"])
        chart["scoringVariants"]["sssplus"], chart["pumbilityVariants"]["sssplus"] = saved["scoring"], saved["pumbility"]
        if saved.get("recovery"):
            chart["submittedSssplusCalibrationBaseline"] = saved["recovery"]
    expanded = copy.deepcopy(base)
    expanded["schemaVersion"] = 58
    expanded["summary"]["method"].update(goals["method"])
    for key in ("singles", "doubles"):
        for saved in goals[key]:
            chart = next(chart for chart in expanded[key] if chart["chartId"] == saved["chartId"])
            chart["scoringVariants"].update(saved["scoringVariants"])
            chart["pumbilityVariants"].update(saved["pumbilityVariants"])
    return base, expanded


class SharedTailPolicyModelTests(unittest.TestCase):
    def test_default_policy_is_identical_for_existing_goals(self):
        for goal in ("ss", "ssplus", "sssplus"):
            data = payload([row("outer", 19, 2.3, center=False), row("ordinary", 19, -9, center=False, eligible=False)])
            for chart in data["singles"]:
                chart["scoringVariants"][goal] = chart["scoringVariants"].pop("sssplus")
                chart["pumbilityVariants"][goal] = chart["pumbilityVariants"].pop("sssplus")
            explicit = copy.deepcopy(data)
            self.assertEqual(apply_tail_policy(data, goal=goal),
                apply_tail_policy(explicit, goal=goal, expected_model_method="target-attainment-rasch-v1"))
            self.assertEqual(data, explicit)

    def test_historical_sss_mixed_model_can_qualify_and_other_branches_cannot(self):
        chart = sss_row("eligible", 19, 2.3, center=False)
        self.assertFalse(exception_evidence(chart, goal="sss", protected=False)["exceptionEligible"])
        self.assertTrue(exception_evidence(chart, goal="sss", protected=False,
                                         expected_model_method=SSS.model_method)["exceptionEligible"])
        for field, value in (("scoringMethod", "sss-boundary-score-gap-v1"),
                             ("scoringMethod", "sss-rare-success-upper-band-v1"),
                             ("scoringEstimateBranch", "all-sss"), ("scoringEstimateBranch", "no-sss"),
                             ("nContributors", 19), ("scoringBelowTarget", 4),
                             ("scoringFitConverged", False), ("scoringMainComponent", False),
                             ("scoringPossiblyTruncated", True), ("scoringExtrapolated", True),
                             ("scoringComparability", "unknown"),
                             ("scoringProvisionalReasons", ["regularization-sensitive"])):
            changed = copy.deepcopy(chart)
            changed["scoringVariants"]["sss"][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(exception_evidence(changed, goal="sss", protected=False,
                    expected_model_method=SSS.model_method)["exceptionEligible"])

    def test_historical_selection_keeps_tie_blocks_and_one_global_budget(self):
        tied = payload([sss_row(f"tie-{i}", 19, 2.3, center=False) for i in range(11)])
        self.assertEqual(select_exceptions(tied, goal="sss", expected_model_method=SSS.model_method)[0], set())
        data = payload([sss_row(f"outlier-{i}", 18 if i % 2 else 19, 2.3+i*.01,
                                mode="Double" if i % 2 else "Single", center=False) for i in range(12)])
        selected, _ = select_exceptions(data, goal="sss", expected_model_method=SSS.model_method)
        self.assertEqual(len(selected), 10)
        self.assertTrue(any(int(cid.rsplit("-", 1)[1]) % 2 for cid in selected))
        self.assertTrue(any(not int(cid.rsplit("-", 1)[1]) % 2 for cid in selected))


class SssTailOverlayTests(unittest.TestCase):
    def test_supported_odd_even_folders_keep_midpoint_order_and_ties(self):
        for deltas in ([-9, -2, -.1, 0, .1, 2, 9], [-9, -2, -.1, .1, 2, 9], [-9, -9, 0, 9, 9]):
            base = synthetic_base([sss_row(str(i), 20, delta, eligible=False) for i, delta in enumerate(deltas)])
            result = apply_sss_tail_tiers(base)
            values = [chart["scoringVariants"]["sss"]["estimatedDifficulty"] for chart in result["singles"]]
            self.assertEqual(values, sorted(values))
            self.assertAlmostEqual(median(values), 20.5, places=12)
            self.assertTrue(all(abs(value-20.5) < 1.9 for value in values))
            self.assertEqual(validate_sss_tail_tiers(result, full=False), base)

    def test_exception_endpoints_and_composites_use_same_fixed_map(self):
        base = synthetic_base([sss_row("exception", 19, 8, center=False),
                               sss_row("ordinary", 19, -9, center=False, eligible=False)])
        frozen = copy.deepcopy(base)
        result = apply_sss_tail_tiers(base)
        self.assertEqual(base, frozen)
        self.assertEqual(result["summary"]["method"][NAMESPACE]["tailPolicy"]["selectedChartIds"], ["exception"])
        for before, after in zip(base["singles"], result["singles"]):
            old, scoring = before["scoringVariants"]["sss"], after["scoringVariants"]["sss"]
            exceptional = scoring["scoringTailPolicy"]["exceptionSelected"]
            self.assertEqual(after["sssTailBaseline"], {"scoring": old, "pumbility": before["pumbilityVariants"]["sss"]})
            for key in ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"):
                self.assertEqual(scoring[key], tail_value(old[key], 19, exceptional=exceptional))
            for key, value in old["initialFit"].items():
                if key not in RANK_FIELDS:
                    self.assertEqual(scoring["initialFit"][key], value)
            composite = after["pumbilityVariants"]["sss"]
            clearing = before["tierMetrics"]["clearing"]["estimatedDifficulty"]
            for key in ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"):
                self.assertEqual(composite[key], (scoring[key] + clearing) / 2)
            for goal in ("general", "ss", "ssplus", "sssplus"):
                self.assertEqual(before["scoringVariants"][goal], after["scoringVariants"][goal])
                self.assertEqual(before["pumbilityVariants"][goal], after["pumbilityVariants"][goal])
            self.assertEqual(before["tierMetrics"], after["tierMetrics"])
        self.assertEqual(recover_sss_tail_tiers(result), base)

    def test_null_points_intervals_and_missing_script_version_stay_recoverable(self):
        chart = sss_row("unrated", 19, 3, center=False)
        scoring = chart["scoringVariants"]["sss"]
        scoring.update(estimatedDifficulty=None, difficultyDelta=None, difficultyCi95Low=None,
                       difficultyCi95High=None, difficultyDeltaCi95Low=None, difficultyDeltaCi95High=None,
                       evidenceStatus="Unrated", scoringUnratedReason="No supported calibration.")
        scoring["initialFit"].update({key: None for key in scoring["initialFit"]})
        chart["pumbilityVariants"]["sss"] = _composite(chart, scoring)
        base = synthetic_base([chart])
        base["summary"].pop("scriptVersion")
        result = apply_sss_tail_tiers(base)
        after = result["singles"][0]["scoringVariants"]["sss"]
        self.assertIsNone(after["estimatedDifficulty"])
        self.assertIsNone(after["difficultyCi95Low"])
        self.assertIsNone(after["difficultyCi95High"])
        self.assertEqual(after["scoringTailPolicy"]["rejectionReasons"], ["unrated"])
        self.assertEqual(recover_sss_tail_tiers(result), base)

    def test_repeated_projection_uses_frozen_baseline_and_double_apply_is_rejected(self):
        base = synthetic_base([sss_row("ordinary", 19, 9, center=False, eligible=False)])
        first = apply_sss_tail_tiers(base)
        self.assertEqual(first, apply_sss_tail_tiers(base))
        self.assertEqual(first, apply_sss_tail_tiers(recover_sss_tail_tiers(first)))
        with self.assertRaisesRegex(ValueError, "predecessor"):
            apply_sss_tail_tiers(first)
        masked = copy.deepcopy(first)
        masked["schemaVersion"] = 57
        with self.assertRaisesRegex(ValueError, "twice"):
            apply_sss_tail_tiers(masked)

    def test_replay_rejects_metric_evidence_rank_policy_and_legacy_tampering(self):
        result = apply_sss_tail_tiers(synthetic_base([sss_row("ordinary", 19, 9, center=False, eligible=False)]))
        mutations = [
            lambda p: p["singles"][0]["scoringVariants"]["sss"].update(estimatedDifficulty=99),
            lambda p: p["singles"][0]["scoringVariants"]["sss"].update(difficultyCi95Low=99),
            lambda p: p["singles"][0]["scoringVariants"]["sss"].update(nContributors=31),
            lambda p: p["singles"][0]["scoringVariants"]["sss"].update(levelRank=99),
            lambda p: p["singles"][0]["scoringVariants"]["sss"]["scoringTailPolicy"].update(exceptionSelected=True),
            lambda p: p["singles"][0]["pumbilityVariants"]["sss"].update(estimatedDifficulty=99),
            lambda p: p["singles"][0]["scoringVariants"]["ss"].update(estimatedDifficulty=99),
            lambda p: p["singles"][0]["tierMetrics"]["clearing"].update(estimatedDifficulty=99),
            lambda p: p["singles"][0]["sssTailBaseline"]["scoring"].update(nContributors=31),
            lambda p: p["summary"]["method"][NAMESPACE]["tailPolicy"].update(exceptionLimit=11),
            lambda p: p["summary"]["method"][NAMESPACE].update(baseSchemaVersion=58),
            lambda p: p["summary"].update(scriptVersion="stale-version"),
        ]
        for mutate in mutations:
            changed = copy.deepcopy(result)
            mutate(changed)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                validate_sss_tail_tiers(changed, full=False)

    def test_strict_recovery_of_both_historical_predecessors_and_no_extra_fit(self):
        for base in historical_payloads():
            with self.subTest(schema=base["schemaVersion"]):
                frozen = digest(base)
                result = apply_sss_tail_tiers(base)
                self.assertEqual(result["schemaVersion"], 59)
                self.assertEqual(result["summary"]["scriptVersion"], SCRIPT_VERSION)
                self.assertEqual(validate_sss_tail_tiers(result), base)
                self.assertEqual(digest(base), frozen)


if __name__ == "__main__":
    unittest.main()
