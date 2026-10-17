from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from statistics import median
from unittest.mock import patch

from sss_folder_centering import (_apply, apply_folder_centering, centering_folders,
    centering_policy, recover_uncentered, validate_folder_centering)
from sss_scoring import _composite, _derived, rank_variants
from tier_postprocessing import FIT_FIELDS, compress_difficulty


def fixture():
    """Small stage-only fixture; the historical model is validated separately."""
    template = json.loads((Path(__file__).parent / "fixtures/sss-target-variants.json").read_text(encoding="utf-8"))
    payload = {"schemaVersion": 50, "summary": {"method": {"sssRefinement": {
        "ranges": {"official": {"Single": {}, "Double": {}}}}}}, "singles": [], "doubles": [], "coop": [{"preserved": True}]}

    def add(mode, level, values, *, first=True, branch=None):
        key = "singles" if mode == "Single" else "doubles"
        for index, value in enumerate(values):
            row = copy.deepcopy(template[key][0])
            row.update(chartId=f"{mode}-{level}-{len(payload[key])}-{index}", level=level, type=mode,
                       songName=f"{mode} {level} {index}", difficulty=f"{'S' if mode == 'Single' else 'D'}{level}")
            metric = row["scoringVariants"]["sss"]
            raw = None if value is None else level + .5 + 2 * (value - level - .5)
            final = compress_difficulty(raw, level)
            metric.update({field: None for field in FIT_FIELDS})
            metric.update(_derived(final, level), evidenceStatus="Published" if value is not None else "Unrated",
                scoringProvisional=False, scoringProvisionalReasons=[], scoringMethod="sss-target-rasch-v1",
                scoringTargetSuccesses=6, scoringBelowTarget=24, nContributors=30,
                scoringUnratedReason=None if value is not None else "No data.")
            metric["initialFit"] = {**{field: None for field in FIT_FIELDS}, **_derived(raw, level)}
            if value is not None:
                for side, sign in (("Low", -1), ("High", 1)):
                    endpoint = raw + sign * .2
                    metric["initialFit"].update({f"difficultyCi95{side}": endpoint, f"difficultyDeltaCi95{side}": endpoint - level - .5})
                    metric.update({f"difficultyCi95{side}": compress_difficulty(endpoint, level),
                        f"difficultyDeltaCi95{side}": compress_difficulty(endpoint, level) - level - .5})
            metric["scoringFirstPass"] = copy.deepcopy(metric)
            if not first:
                metric["scoringFirstPass"].update(estimatedDifficulty=None, evidenceStatus="Unrated")
            if branch:
                metric.update(scoringEstimateBranch=branch, scoringMethod="sss-rare-success-upper-band-v1" if branch == "rare-sss" else "sss-boundary-score-gap-v1", evidenceStatus="Provisional")
                for side in ("Low", "High"):
                    metric[f"difficultyCi95{side}"] = metric[f"difficultyDeltaCi95{side}"] = None
                    metric["initialFit"][f"difficultyCi95{side}"] = metric["initialFit"][f"difficultyDeltaCi95{side}"] = None
                if branch == "rare-sss":
                    metric["scoringRefinement"] = {"rare": {"upperBandLow": level + .35, "upperBandHigh": level + .4}}
            row["pumbilityVariants"]["sss"] = _composite(row, metric)
            payload[key].append(row)
        if first and len(values) >= 5:
            payload["summary"]["method"]["sssRefinement"]["ranges"]["official"][mode][str(level)] = {"lower": min(values), "upper": max(values)}

    add("Single", 19, [19.2])
    add("Single", 20, [20.0, 20.1, 20.2, 20.3, 20.4])
    add("Single", 20, [None], first=False)
    add("Single", 21, [21.0], first=False, branch="no-sss")
    add("Single", 22, [22.0, 22.2, 22.4, 22.6, 22.8, 23.0])
    add("Single", 23, [23.8], first=False, branch="no-sss")
    add("Double", 20, [20.2, 20.4, 20.6, 20.8, 21.0])
    add("Double", 21, [21.2], first=False, branch="rare-sss")
    for key in ("singles", "doubles"):
        rank_variants(payload[key])
    return payload


class SssCenteringTests(unittest.TestCase):
    def test_odd_even_complete_population_and_same_mode_donors(self):
        base = fixture()
        folders = centering_folders(base)
        single, double = folders["Single"], folders["Double"]
        self.assertEqual(single["20"]["ratedChartCount"], 5)
        self.assertAlmostEqual(single["20"]["finalShift"], .3)
        self.assertAlmostEqual(single["22"]["finalShift"], 0.)
        self.assertEqual(single["21"]["basis"], "interpolated")
        self.assertEqual(single["21"]["donorLevels"], [20, 22])
        self.assertAlmostEqual(single["21"]["finalShift"], .15)
        self.assertEqual(single["23"]["donorLevels"], [22])
        self.assertAlmostEqual(double["21"]["finalShift"], -.1)
        self.assertNotIn("19", single)

    def test_supported_medians_intervals_once_only_compression_and_spacing(self):
        base = fixture()
        result = _apply(base, centering_policy(base))
        for key in ("singles", "doubles"):
            for old, row in zip(base[key], result[key]):
                scoring = row["scoringVariants"]["sss"]
                center = scoring.get("scoringFolderCentering")
                if center and scoring["estimatedDifficulty"] is not None:
                    self.assertAlmostEqual(scoring["estimatedDifficulty"] - old["scoringVariants"]["sss"]["estimatedDifficulty"], center["finalShift"])
                    for field in ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"):
                        self.assertEqual(scoring[field], compress_difficulty(scoring["initialFit"][field], row["level"]))
                self.assertEqual(row["tierMetrics"], old["tierMetrics"])
                self.assertEqual(row["scoringVariants"]["general"], old["scoringVariants"]["general"])
                self.assertEqual(row["pumbilityVariants"]["general"], old["pumbilityVariants"]["general"])
            for level in (20, 22):
                group = [r["scoringVariants"]["sss"] for r in result[key] if r["level"] == level and r["scoringVariants"]["sss"]["estimatedDifficulty"] is not None]
                if group:
                    self.assertAlmostEqual(median(r["estimatedDifficulty"] for r in group), level + .5)
        self.assertEqual(base["coop"], result["coop"])

    def test_sparse_points_bands_and_pumbility_are_not_independently_centered(self):
        base = fixture()
        result = _apply(base, centering_policy(base))
        single = next(r for r in result["singles"] if r["level"] == 21)
        self.assertAlmostEqual(single["scoringVariants"]["sss"]["estimatedDifficulty"], 21.15)
        row = next(r for r in result["doubles"] if r["level"] == 21)
        metric = row["scoringVariants"]["sss"]
        self.assertAlmostEqual(metric["scoringFolderCentering"]["effectiveRareBand"]["lower"], 21.25)
        self.assertAlmostEqual(metric["scoringFolderCentering"]["effectiveRareBand"]["upper"], 21.3)
        self.assertIsNone(metric["difficultyCi95Low"])
        self.assertIsNone(row["pumbilityVariants"]["sss"]["difficultyCi95Low"])
        self.assertEqual(row["pumbilityVariants"]["sss"]["estimatedDifficulty"], (metric["estimatedDifficulty"] + row["tierMetrics"]["clearing"]["estimatedDifficulty"]) / 2)

    def test_no_donor_does_not_borrow_from_the_other_mode_or_below20(self):
        base = fixture()
        base["singles"] = [r for r in base["singles"] if r["level"] in (19, 21)]
        folder = centering_folders(base)["Single"]["21"]
        self.assertEqual((folder["basis"], folder["donorLevels"], folder["finalShift"]), ("unavailable", [], 0.))
        result = _apply(base, centering_policy(base))
        self.assertEqual(result["singles"][1]["scoringVariants"]["sss"]["estimatedDifficulty"], base["singles"][1]["scoringVariants"]["sss"]["estimatedDifficulty"])

    def test_exact_ties_remain_equal_and_ordered(self):
        base = fixture()
        rows = [r for r in base["singles"] if r["level"] == 20 and r["scoringVariants"]["sss"]["estimatedDifficulty"] is not None]
        rows[1]["scoringVariants"]["sss"] = copy.deepcopy(rows[0]["scoringVariants"]["sss"])
        result = _apply(base, centering_policy(base))
        output = [r for r in result["singles"] if r["chartId"] in (rows[0]["chartId"], rows[1]["chartId"])]
        self.assertEqual(output[0]["scoringVariants"]["sss"]["estimatedDifficulty"], output[1]["scoringVariants"]["sss"]["estimatedDifficulty"])

    def test_recovery_validation_and_rejecting_another_compression(self):
        base = fixture()
        result = _apply(base, centering_policy(base))
        self.assertEqual(recover_uncentered(result), base)
        with patch("sss_folder_centering.validate_refined_tiers"):
            self.assertEqual(validate_folder_centering(result, base), base)
            for mutate in (
                lambda p: p["summary"]["method"]["sssFolderCentering"]["folders"]["Single"]["20"].update(finalShift=0),
                lambda p: p["singles"][1]["scoringVariants"]["sss"].update(estimatedDifficulty=20.5),
                lambda p: p["singles"][1]["pumbilityVariants"]["sss"].update(estimatedDifficulty=20.5),
            ):
                bad = copy.deepcopy(result); mutate(bad)
                with self.assertRaises(ValueError): validate_folder_centering(bad, base)
        with self.assertRaises(ValueError): apply_folder_centering(result)


if __name__ == "__main__":
    unittest.main()
