from __future__ import annotations

import copy
import unittest

import numpy as np

from official_high_level_clearing import METHOD, apply_official_high_level_clearing


def chart(chart_id, level, mode="Double"):
    return {"id": chart_id, "chartId": chart_id, "type": mode, "level": level, "songName": chart_id}


def board(chart_id, players, **extra):
    entries = [{"playerId": player, "score": 950000, "place": index + 1,
                "isSupplemented": False} for index, player in enumerate(players)]
    return {"chartId": chart_id, "entries": entries, "rawRowCount": len(entries),
            "maxRank": len(entries), "cutoffScore": 950000 if entries else None,
            "asOf": "2026-10-11T17:25:07+00:00", "possiblyTruncated": False, **extra}


def payload(charts):
    result = {"schemaVersion": 49, "summary": {"method": {"unchanged": True}},
              "singles": [], "doubles": [], "coop": [{"chartId": "coop", "unchanged": True}]}
    for item in charts:
        row = {**item, "estimatedDifficulty": 99, "scoringVariants": {"sss": {"unchanged": True}},
               "officialEvidence": {"source": "other-scoring-source"},
               "tierMetrics": {"clearing": {"estimatedDifficulty": 99, "oldField": True},
                               "pumbility": {"estimatedDifficulty": 99, "unchanged": True}}}
        result["singles" if item["type"] == "Single" else "doubles"].append(row)
    return result


def capture(charts, boards):
    return {"source": "piuscores-official", "mix": "Phoenix2", "asOf": "2026-10-11T17:25:07+00:00",
            "catalog": charts, "boards": {b["chartId"]: b for b in boards}}


def history(charts, boards, prefix, players, level=23, count=25, mode="Double"):
    for index in range(count):
        chart_id = f"{prefix}-{index}"
        charts.append(chart(chart_id, level, mode))
        boards.append(board(chart_id, players))


def measured_fixture():
    targets = [chart(name, 26) for name in ("easy", "low", "middle", "upper", "hard", "cap", "missing", "no-history")]
    targets += [chart("d25", 25), chart("s26", 26, "Single")]
    charts, boards = targets.copy(), []
    for name, level in zip(("easy", "low", "middle", "upper", "hard"), range(23, 28)):
        players = [f"{name}:{index}" for index in range(20)]
        history(charts, boards, name + "-history", players, level=level)
        boards.append(board(name, players))
    boards += [board("cap", ["cap:0", "cap:1"], rawRowCount=300, maxRank=300),
               board("no-history", ["unknown:0", "unknown:1"])]
    return payload(targets), capture(charts, boards)


def metrics(value):
    return {r["chartId"]: r["tierMetrics"]["clearing"] for r in value["doubles"]}


class OfficialHighLevelClearingTests(unittest.TestCase):
    def test_only_double_26_plus_clearing_changes(self):
        value, source = measured_fixture()
        original = copy.deepcopy((value, source))
        metadata = apply_official_high_level_clearing(value, source)
        self.assertEqual(source, original[1])
        self.assertEqual(value["summary"], original[0]["summary"])
        self.assertEqual(value["singles"], original[0]["singles"])
        self.assertEqual(value["coop"], original[0]["coop"])
        for before, after in zip(original[0]["doubles"], value["doubles"]):
            self.assertEqual({k: v for k, v in before.items() if k != "tierMetrics"},
                             {k: v for k, v in after.items() if k != "tierMetrics"})
            self.assertEqual(before["tierMetrics"]["pumbility"], after["tierMetrics"]["pumbility"])
            if after["level"] < 26:
                self.assertEqual(before, after)
            else:
                self.assertEqual(after["tierMetrics"]["clearing"]["method"], METHOD)
        self.assertEqual(metadata["targetCharts"], 8)
        self.assertEqual(metadata["mix"], "Phoenix2")
        self.assertEqual(metadata["uncertainty"]["samples"], 1000)

    def test_top25_other_unique_clears_and_current_level_history_scope(self):
        target = chart("target", 26)
        charts, boards = [target], [board("target", ["one"])]
        history(charts, boards, "valid", ["one"], count=24)
        history(charts, boards, "too-low", ["one"], count=3, level=22)
        history(charts, boards, "other-mode", ["one"], count=3, level=27, mode="Single")
        value = payload([target])
        apply_official_high_level_clearing(value, capture(charts, boards))
        self.assertEqual(metrics(value)["target"]["ratedClearCount"], 0)
        self.assertIsNone(metrics(value)["target"]["estimatedDifficulty"])
        charts.append(chart("25th", 24))
        boards.append(board("25th", ["one"]))
        # Duplicated target entries cannot expand the count or history.
        boards[0]["entries"].append(copy.deepcopy(boards[0]["entries"][0]))
        value = payload([target])
        apply_official_high_level_clearing(value, capture(charts, boards))
        result = metrics(value)["target"]
        self.assertEqual(result["ratedClearCount"], 1)
        self.assertEqual(result["clearCount"], 1)
        self.assertAlmostEqual(result["q20Skill"], (24 * 23.5 + 24.5) / 25)
        charts.append(chart("26th", 29))
        boards.append(board("26th", ["one"]))
        value = payload([target])
        apply_official_high_level_clearing(value, capture(charts, boards))
        self.assertAlmostEqual(metrics(value)["target"]["q20Skill"], (23 * 23.5 + 24.5 + 29.5) / 25)

    def test_q20_linear_interpolation_and_evidence_coverage(self):
        target = chart("target", 26)
        charts, boards = [target], [board("target", [str(i) for i in range(5)] + ["no-history"])]
        for index in range(5):
            history(charts, boards, str(index) + "-history", [str(index)], level=23 + index)
        value = payload([target])
        apply_official_high_level_clearing(value, capture(charts, boards))
        result = metrics(value)["target"]
        self.assertAlmostEqual(result["q20Skill"], 24.3)
        self.assertEqual(result["ratedClearCount"], 5)
        self.assertEqual(result["missingSkillCount"], 1)
        self.assertAlmostEqual(result["abilityCoverage"], 5 / 6)
        self.assertAlmostEqual(result["shrinkageWeight"], 5 / 25 * 5 / 6)
        self.assertIsNotNone(result["q20SkillCi95Low"])

    def test_spread_cap_recentering_and_single_compression(self):
        value, source = measured_fixture()
        audit = apply_official_high_level_clearing(value, source)
        result = metrics(value)
        folder = audit["folders"]["26"]
        self.assertEqual(folder["spreadCalibration"]["basis"], "folder")
        self.assertAlmostEqual(folder["spreadCalibration"]["scale"], .45 / .7)
        self.assertEqual(folder["initialMedian"], 26.5)
        self.assertEqual(folder["finalMedian"], 26.5)
        self.assertNotEqual(folder["recenteringShift"], 0)
        for name in ("easy", "low", "middle", "upper", "hard", "cap"):
            metric = result[name]
            self.assertAlmostEqual(metric["estimatedDifficulty"], 26.5 + .5 * (metric["initialFit"]["estimatedDifficulty"] - 26.5))
        self.assertEqual(result["cap"]["estimatedDifficulty"], result["easy"]["estimatedDifficulty"])
        self.assertEqual(result["cap"]["capDefaultDifficulty"], result["cap"]["estimatedDifficulty"])
        self.assertEqual(result["cap"]["ratedClearCount"], 0)
        self.assertEqual(result["cap"]["clearCount"], 2)
        self.assertEqual(result["cap"]["evidenceStatus"], "Insufficient")
        self.assertIsNone(result["cap"]["difficultyCi95Low"])
        self.assertEqual(result["cap"]["clearingEvidence"]["rawRowCount"], 300)
        self.assertIsNone(result["missing"]["estimatedDifficulty"])
        self.assertEqual(result["missing"]["clearingEvidence"]["status"], "missing")
        self.assertIsNone(result["no-history"]["estimatedDifficulty"])

    def test_all_capped_defaults_to_exact_midpoint_without_inventing_intervals(self):
        target = chart("cap", 27)
        value = payload([target])
        audit = apply_official_high_level_clearing(value, capture([target], [board("cap", [], rawRowCount=300)]))
        result = metrics(value)["cap"]
        self.assertEqual(result["estimatedDifficulty"], 27.5)
        self.assertEqual(result["initialFit"]["estimatedDifficulty"], 27.5)
        self.assertEqual(result["capDefaultBasis"], "folder-midpoint-no-uncapped-charts")
        self.assertEqual(result["evidenceStatus"], "Insufficient")
        self.assertIsNone(result["difficultyCi95High"])
        self.assertEqual(audit["folders"]["27"]["finalMedian"], 27.5)

    def test_sparse_folder_borrows_only_independently_supported_folder(self):
        value, source = measured_fixture()
        for level in (27, 28):
            target = chart(f"sparse-{level}", level)
            source["catalog"].append(target)
            source["boards"][target["id"]] = board(target["id"], [f"hard:{i}" for i in range(20)])
            value["doubles"].append(payload([target])["doubles"][0])
        audit = apply_official_high_level_clearing(value, source)
        for level in (27, 28):
            params = audit["folders"][str(level)]["spreadCalibration"]
            self.assertEqual(params["basis"], "neighbor-folder")
            self.assertEqual(params["referenceLevel"], 26)
            result = metrics(value)[f"sparse-{level}"]
            self.assertEqual(result["evidenceStatus"], "Provisional")
            self.assertIn("borrowed-folder-scale", result["provisionalReasons"])
            self.assertEqual(result["estimatedDifficulty"], level + .5)

    def test_conditional_bootstrap_is_repeatable_and_uses_fixed_calibration(self):
        target = chart("target", 26)
        charts, boards = [target], [board("target", [str(i) for i in range(20)])]
        for index in range(20):
            history(charts, boards, str(index) + "-history", [str(index)], level=23 + index % 5)
        first, second = payload([target]), payload([target])
        source = capture(charts, boards)
        apply_official_high_level_clearing(first, source)
        apply_official_high_level_clearing(second, source)
        self.assertEqual(first, second)
        result = metrics(first)["target"]
        self.assertLess(result["q20SkillCi95Low"], result["q20SkillCi95High"])
        for side in ("Low", "High"):
            q = result[f"q20SkillCi95{side}"]
            raw = 26.5 + result["spreadCalibration"]["scale"] * .7 * result["shrinkageWeight"] * (q - result["folderReferenceSkill"]) + result["recenteringShift"]
            self.assertAlmostEqual(result["initialFit"][f"difficultyCi95{side}"], raw)
            self.assertAlmostEqual(result[f"difficultyCi95{side}"], 26.5 + .5 * (raw - 26.5))

    def test_source_and_current_catalog_mismatch_fail_closed(self):
        value, source = measured_fixture()
        for mix in ("Phoenix", "combined"):
            with self.assertRaises(ValueError):
                apply_official_high_level_clearing(copy.deepcopy(value), {**source, "mix": mix})
        wrong = copy.deepcopy(source)
        wrong["catalog"] = [c for c in wrong["catalog"] if c["id"] != "hard"]
        with self.assertRaises(ValueError):
            apply_official_high_level_clearing(copy.deepcopy(value), wrong)
        wrong = copy.deepcopy(source)
        next(c for c in wrong["catalog"] if c["id"] == "hard")["level"] = 27
        with self.assertRaises(ValueError):
            apply_official_high_level_clearing(copy.deepcopy(value), wrong)
        wrong = copy.deepcopy(source)
        wrong["boards"]["easy"]["entries"][0]["isSupplemented"] = True
        with self.assertRaises(ValueError):
            apply_official_high_level_clearing(copy.deepcopy(value), wrong)


if __name__ == "__main__":
    unittest.main()
