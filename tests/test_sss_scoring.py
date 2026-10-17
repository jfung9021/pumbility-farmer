from __future__ import annotations

import copy
import unittest

from official_cross_level_calibration import inverse_chart_cost
from sss_scoring import (FIT_FIELDS, _composite, apply_sss_variants, fit_target_curve,
                         general_scoring_view, prepare_target_observations, recover_general_payload,
                         validate_sss_variants)


def fixture():
    base = {"schemaVersion": 48, "summary": {"method": {"unchanged": True}},
            "singles": [], "doubles": [], "coop": [{"unmodified": [1, 2, 3]}]}
    catalog, fits = {}, {"submitted": {}, "official": {}}
    prepared = {"catalog": catalog, "sourceDates": {}, "manifestSha256": "a" * 64}
    for source, minimum in (("submitted", 16), ("official", 20)):
        prepared[source] = {"charts": {}}
        for mode, key in (("Single", "singles"), ("Double", "doubles")):
            charts = {}
            fits[source][mode] = {"charts": charts, "diagnostics": {"converged": True}, "policy": {}}
            for level in range(minimum, minimum + 3):
                for index in range(5):
                    cid = f"{mode}-{level}-{index}"
                    catalog[cid] = {"id": cid, "type": mode, "level": level}
                    prepared[source]["charts"][cid] = {"anchorEligible": True, "comparability": "comparable", "provisional": False}
                    charts[cid] = {"effect": (level - minimum + 1) ** 2 + index * .02,
                        "effectLow": (level - minimum + 1) ** 2 + index * .02 - .1,
                        "effectHigh": (level - minimum + 1) ** 2 + index * .02 + .1,
                        "usableContributors": 25, "targetSuccesses": 10, "belowTarget": 15,
                        "componentId": 0, "calibratedComponent": True, "ratingEligible": True,
                        "provisionalReasons": [], "information": 1, "bootstrapUsableSamples": 200}
                    # Submitted histories20+ are fitting-only, not duplicated display rows.
                    if source == "submitted" and level >= 20:
                        continue
                    initial = {**{k: None for k in FIT_FIELDS}, "estimatedDifficulty": level + .9,
                        "difficultyDelta": .4}
                    clearing = {"estimatedDifficulty": level + .6, "evidenceStatus": "Published", "ratedClearCount": 30,
                        "initialFit": {"estimatedDifficulty": level + .7}}
                    pumbility = {"estimatedDifficulty": level + .65, "evidenceStatus": "Published", "maximumBaseline": {"untouched": True}}
                    base[key].append({"chartId": cid, "songName": cid, "type": mode, "level": level,
                        **{k: None for k in FIT_FIELDS}, "estimatedDifficulty": level + .7,
                        "difficultyDelta": .2, "evidenceStatus": "Published", "nContributors": 31,
                        "nPlayersScored": 40, "initialFit": initial,
                        "scoringMethod": "official-cross-level-v1" if level >= 20 else "submitted-score-profile-v2",
                        "tierMetrics": {"clearing": clearing, "pumbility": pumbility}})
    return base, prepared, fits


class TargetOverlayTests(unittest.TestCase):
    def build(self):
        base, prepared, fits = fixture()
        result = apply_sss_variants(base, prepared, fits, source_hashes={"submitted": "b" * 64}, bootstrap_samples=200, seed=7)
        return base, prepared, fits, result

    def test_preserves_complete_general_and_clearing_and_averages_full_precision(self):
        base, prepared, fits = fixture()
        frozen = copy.deepcopy((base, prepared, fits))
        result = apply_sss_variants(base, prepared, fits, source_hashes={}, bootstrap_samples=200, seed=7)
        self.assertEqual((base, prepared, fits), frozen)
        self.assertEqual(recover_general_payload(result), base)
        self.assertEqual(validate_sss_variants(result), base)
        for key in ("singles", "doubles"):
            for row, original in zip(result[key], base[key]):
                self.assertEqual(row["scoringVariants"]["general"], general_scoring_view(original))
                s, p, c = row["scoringVariants"]["sss"], row["pumbilityVariants"]["sss"], row["tierMetrics"]["clearing"]
                self.assertAlmostEqual(p["estimatedDifficulty"], (s["estimatedDifficulty"] + c["estimatedDifficulty"]) / 2)
                self.assertEqual(p["scoringSupportCount"], 25)
                self.assertEqual(p["clearingSupportCount"], 30)
                self.assertEqual(s["scoringBootstrapValidReplicates"], 200)
                for side in ("Low", "High"):
                    self.assertAlmostEqual(s[f"difficultyCi95{side}"], row["level"] + .5 + .5 * (s["initialFit"][f"difficultyCi95{side}"] - row["level"] - .5))
                    self.assertAlmostEqual(p[f"difficultyCi95{side}"], (s[f"difficultyCi95{side}"] + c["estimatedDifficulty"]) / 2)

    def test_calibration_maps_both_minimum_origins_and_fails_closed(self):
        for minimum in (16, 20):
            anchors = [{"level": minimum + i, "cost": float((i + 1) ** 2)} for i in range(3)]
            curve = fit_target_curve(anchors, minimum_level=minimum)
            for anchor in anchors:
                self.assertAlmostEqual(inverse_chart_cost(anchor["cost"], curve), anchor["level"] + .5)
            for broken in (anchors[1:], [{**a, "cost": 1.} for a in anchors]):
                with self.assertRaises(ValueError):
                    fit_target_curve(broken, minimum_level=minimum)
            with self.assertRaisesRegex(ValueError, str([minimum, minimum + 1, minimum + 2]).replace("[", r"\[").replace("]", r"\]")):
                fit_target_curve([{**a, "cost": 1.} for a in anchors], minimum_level=minimum)
        base, prepared, fits = fixture()
        for estimate in fits["official"]["Single"]["charts"].values():
            estimate["effect"] = 1.
        result = apply_sss_variants(base, prepared, fits, source_hashes={}, bootstrap_samples=200, seed=7)
        upper = [r for r in result["singles"] if r["level"] >= 20]
        self.assertTrue(all(r["scoringVariants"]["sss"]["estimatedDifficulty"] is None for r in upper))
        self.assertTrue(all("calibration" in r["scoringVariants"]["sss"]["scoringUnratedReason"] for r in upper))

    def test_one_sided_missing_disconnected_truncated_and_weakest_evidence(self):
        base, prepared, fits = fixture()
        row = next(r for r in base["singles"] if r["level"] == 20)
        cid = row["chartId"]
        estimate = fits["official"]["Single"]["charts"][cid]
        estimate.update(targetSuccesses=0, belowTarget=25, ratingEligible=False)
        result = apply_sss_variants(base, prepared, fits, source_hashes={}, bootstrap_samples=200, seed=7)
        s = next(r for r in result["singles"] if r["chartId"] == cid)["scoringVariants"]["sss"]
        self.assertIsNone(s["estimatedDifficulty"])
        self.assertIn("One-sided", s["scoringUnratedReason"])
        self.assertEqual(s["scoringBelowTarget"], 25)
        self.assertIsNone(s["difficultyCi95Low"])
        base, _, _, result = self.build()
        row = result["singles"][0]
        row["tierMetrics"]["clearing"]["evidenceStatus"] = "Insufficient"
        self.assertEqual(_composite(row, row["scoringVariants"]["sss"])["evidenceStatus"], "Insufficient")
        base, prepared, fits = fixture()
        cid = base["singles"][-1]["chartId"]
        prepared["official"]["charts"][cid]["sourceStates"] = {"Phoenix2": {"possiblyTruncated": True}}
        result = apply_sss_variants(base, prepared, fits, source_hashes={}, bootstrap_samples=200, seed=7)
        s = result["singles"][-1]["scoringVariants"]["sss"]
        self.assertTrue(s["scoringPossiblyTruncated"])
        self.assertEqual(s["evidenceStatus"], "Provisional")

    def test_successful_selection_threshold_after_normalization_and_missing_unknown(self):
        charts = [{"id": str(i), "type": "Single", "level": 20, "noteCount": 100} for i in range(5)]
        first = {"charts": charts, "scores": [{"chartId": "0", "playerId": "p", "score": 990000, "isBroken": False}]}
        second = {"charts": charts, "scores": [{"chartId": "0", "playerId": "p", "score": 989999, "isBroken": False},
            {"chartId": "1", "playerId": "p", "score": 1000000, "isBroken": True},
            {"chartId": "2", "playerId": "p", "score": 990000, "isBroken": False}]}
        prepared = prepare_target_observations(first, second, {"boards": {}, "charts": {}}, charts)
        self.assertEqual(prepared["submitted"]["boards"]["0"]["submitted:p"], 989999)
        self.assertNotIn("1", prepared["submitted"]["boards"])
        self.assertNotIn("3", prepared["submitted"]["boards"])
        self.assertEqual(prepared["submitted"]["boards"]["2"]["submitted:p"], 990000)

    def test_corrupted_component_and_general_compatibility_are_rejected(self):
        _, _, _, result = self.build()
        for mutate in (lambda p: p["singles"][0]["scoringVariants"]["sss"].update(estimatedDifficulty=100.),
                       lambda p: p["singles"][0]["pumbilityVariants"]["sss"].update(estimatedDifficulty=100.),
                       lambda p: p["singles"][0]["scoringVariants"]["general"].update(nContributors=0),
                       lambda p: p["coop"][0].update(unmodified=[])):
            changed = copy.deepcopy(result)
            mutate(changed)
            with self.assertRaises(ValueError):
                validate_sss_variants(changed)


if __name__ == "__main__":
    unittest.main()
