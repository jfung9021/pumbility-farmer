from __future__ import annotations

import copy
import json
import unittest

import numpy as np

from official_scores import is_official_clearing_target, is_official_target
from official_tiers import CLEAR_ABILITY_METRIC, apply_official_tiers, official_player_ability


def chart(cid, level, mode="Single"):
    return {"id": cid, "type": mode, "level": level, "songName": cid,
            "difficulty": f"{'S' if mode == 'Single' else 'D'}{level}"}


def board(cid, players, base_score=950000, **extra):
    entries = [{"playerId": player, "score": base_score + index * 100,
                "place": len(players) - index} for index, player in enumerate(players)]
    return {"chartId": cid, "asOf": "2026-09-29", "rawRowCount": len(entries),
            "maxRank": len(entries) if entries else None,
            "cutoffScore": base_score if entries else None,
            "possiblyTruncated": False, "entries": entries, **extra}


def payload(charts):
    result = {"schemaVersion": 26, "summary": {"method": {}, "coverage": {}, "modes": {}},
              "singles": [], "doubles": [], "coop": [{"chartId": "coop", "estimatedDifficulty": 16,
                                                        "evidenceStatus": "Published"}]}
    for item in charts:
        mode = "singles" if item["type"] == "Single" else "doubles"
        result[mode].append({
            **item, "chartId": item["id"], "folder": item["difficulty"],
            "mode": "Singles" if mode == "singles" else "Doubles", "modeRank": 42,
            "estimatedDifficulty": 99.0, "difficultyDelta": 99.0,
            "levelRank": 99, "levelComparisonCharts": 99, "levelPercentile": .5,
            "relativeGroup": "High", "relativeGroupRank": 5,
            "effectBand": "Underrated", "effectBandRank": 7, "evidenceStatus": "Published",
            "nContributors": 900, "nPlayersScored": 900,
            "phoenix1Contributors": 800, "phoenix2Contributors": 100,
            "scoringScoreProfile": [123] * 5, "scoringProfileMatchDifficulty": 99,
            "scoringFolderReferenceDifficulty": 99, "scoringDifficultyScale": 1,
            "scoringProfileRmse": 999, "scoringProfileExtrapolated": True,
            "difficultyCi95Low": 98, "difficultyCi95High": 100,
            "difficultyDeltaCi95Low": 97, "difficultyDeltaCi95High": 101,
            "scoringProfileMatchCi95Low": 98, "scoringProfileMatchCi95High": 100,
            "tierMetrics": {"clearing": {"q10Skill": 27, "estimatedDifficulty": item["level"] + .2,
                "difficultyDelta": -.3, "levelRank": 1, "levelComparisonCharts": 1,
                "effectBandRank": 2, "effectBand": "Easy", "evidenceStatus": "Published",
                "clearCount": 90, "ratedClearCount": 80, "missingSkillCount": 10, "folderReferenceSkill": 27.4},
                            "pumbility": {"estimatedDifficulty": 99}},
            "whatIfEstimates": [{"level": 99, "estimatedDifficulty": 99}],
        })
    return result


def fixture():
    charts = [chart("below-s", 20), chart("below-d", 21, "Double")]
    boards = []
    # Median 100, with both an easier and harder chart and two capped boards.
    for mode, prefix, level in (("Single", "s", 25), ("Double", "d", 26)):
        for label, count, score in (("hard", 20, 940000), ("middle", 60, 950000),
                                    ("easy", 100, 970000), ("cap", 300, 970000),
                                    ("cap2", 300, 960000)):
            cid = prefix + label
            charts.append(chart(cid, level, mode))
            boards.append(board(cid, [f"official:{i}" for i in range(count)], score))
    charts.append(chart("allcap", 27))
    boards.append(board("allcap", [f"official:{i}" for i in range(300)], 960000))
    for mode, prefix, level in (("Single", "s", 22), ("Double", "d", 23)):
        for i in range(25):
            cid = f"{prefix}history{i}"
            charts.append(chart(cid, level, mode))
            boards.append(board(cid, [f"official:{j}" for j in range(300)], 960000))
    return payload(charts), {"schemaVersion": 1, "source": "piuscores-official", "mix": "Phoenix2",
                             "asOf": "2026-09-29", "boards": boards}, charts


def by_id(result):
    return {row["chartId"]: row for row in result["singles"] + result["doubles"]}


def without_mode_rank(row):
    return {key: value for key, value in row.items() if key != "modeRank"}


def ability_fixture():
    novices = [f"official:n{i}" for i in range(20)]
    experts = [f"official:e{i}" for i in range(20)]
    small = [f"official:s{i}" for i in range(5)]
    charts, boards = [], []
    for i in range(25):
        charts.extend([chart(f"low{i}", 22), chart(f"high{i}", 26)])
        boards.extend([board(f"low{i}", novices + small), board(f"high{i}", experts)])
    for cid, players in (("novice", novices), ("novice-small", small), ("expert", experts)):
        charts.append(chart(cid, 25))
        boards.append(board(cid, players))
    snapshot = {"source": "piuscores-official", "mix": "Phoenix2", "boards": boards}
    return payload(charts), snapshot, charts


class OfficialTierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.snapshot, cls.charts = fixture()
        cls.result = apply_official_tiers(cls.base, cls.snapshot, cls.charts)

    def test_thresholds_preserve_lower_charts_coop_and_inputs(self):
        original = copy.deepcopy((self.base, self.snapshot, self.charts))
        result = apply_official_tiers(self.base, self.snapshot, self.charts)
        self.assertEqual((self.base, self.snapshot, self.charts), original)
        before, after = by_id(self.base), by_id(result)
        for cid in ("below-s", "below-d"):
            self.assertEqual(without_mode_rank(after[cid]), without_mode_rank(before[cid]))
            self.assertNotIn("officialEvidence", after[cid])
        self.assertEqual(result["coop"], self.base["coop"])
        for row in after.values():
            if "officialEvidence" in row:
                self.assertEqual(row["phoenix1Contributors"], 0)
                self.assertEqual(row["phoenix2Contributors"], 0)
                if is_official_clearing_target(row):
                    self.assertNotIn("q10Skill", row["tierMetrics"]["clearing"])
                else:
                    self.assertEqual(row["tierMetrics"]["clearing"], before[row["chartId"]]["tierMetrics"]["clearing"])
                self.assertNotIn("q10Appearances", row["tierMetrics"]["clearing"])

    def test_personal_comparisons_have_equal_weight_and_deduplicate(self):
        rows = by_id(self.result)
        self.assertGreater(rows["shard"]["estimatedDifficulty"], rows["seasy"]["estimatedDifficulty"])
        self.assertEqual(rows["shard"]["scoringPlayerCount"], 20)
        self.assertEqual(rows["shard"]["nContributors"], 20)
        self.assertIsNotNone(rows["shard"]["difficultyCi95Low"])
        self.assertIsNone(rows["allcap"]["estimatedDifficulty"])
        changed = copy.deepcopy(self.snapshot)
        changed["boards"][0]["entries"].append({**changed["boards"][0]["entries"][0], "score": 1})
        after = by_id(apply_official_tiers(self.base, changed, self.charts))
        self.assertEqual(after["shard"], rows["shard"])

    def test_all_eligible_scores_inform_scoring_without_changing_clearing(self):
        changed = copy.deepcopy(self.snapshot)
        changed["boards"][0]["entries"][0]["score"] = 100
        after = by_id(apply_official_tiers(self.base, changed, self.charts))
        before = by_id(self.result)
        self.assertGreater(after["shard"]["scoringMeanGap"], before["shard"]["scoringMeanGap"])
        for cid, row in before.items():
            self.assertEqual(after[cid]["tierMetrics"]["clearing"], row["tierMetrics"]["clearing"])

    def test_player_ability_requires_25_other_distinct_charts_and_averages_hardest_25(self):
        history = {"self": 29.5, **{str(i): 22.5 for i in range(24)}}
        self.assertIsNone(official_player_ability(history, "self"))
        history["25th"] = 27.5
        self.assertEqual(official_player_ability(history, "self"), 22.7)
        history["26th"] = 28.5
        self.assertAlmostEqual(official_player_ability(history, "self"), (23*22.5+27.5+28.5)/25)
        history["self"] = 100
        self.assertAlmostEqual(official_player_ability(history, "self"), (23*22.5+27.5+28.5)/25)

    def test_metric_cutoffs_select_official_scoring_and_clearing_independently(self):
        charts = [chart(f"s{level}-{i}",level) for level in range(21,26) for i in range(4)] + [
            chart(f"d{level}-{i}",level,"Double") for level in range(22,27) for i in range(4)]
        base = payload(charts)
        boards = [board(c["id"],["official:1"],950000+i*10) for i,c in enumerate(charts)]
        result = by_id(apply_official_tiers(base,{**self.snapshot,"boards":boards},charts))
        for c in charts:
            row = result[c["id"]]
            if is_official_target(c):
                self.assertIn("officialEvidence",row)
                self.assertIsNotNone(row["estimatedDifficulty"])
                if is_official_clearing_target(c):
                    self.assertEqual(row["tierMetrics"]["clearing"]["skillMetric"],CLEAR_ABILITY_METRIC)
                else:
                    self.assertEqual(row["tierMetrics"]["clearing"],by_id(base)[c["id"]]["tierMetrics"]["clearing"])
            else:
                self.assertNotIn("officialEvidence",row)
                self.assertEqual(without_mode_rank(row),without_mode_rank(by_id(base)[c["id"]]))

    def test_lower_level_histories_determine_ability_without_rerating_lower_charts(self):
        base, snapshot, charts = ability_fixture()
        result = apply_official_tiers(base, snapshot, charts)
        rows = by_id(result)
        for name in ("novice", "novice-small"):
            clearing = rows[name]["tierMetrics"]["clearing"]
            self.assertEqual(clearing["q20Skill"], 22.5)
            self.assertEqual(clearing["skillMetric"], CLEAR_ABILITY_METRIC)
            self.assertEqual(clearing["estimatedDifficulty"], 25.5)
        expert = rows["expert"]["tierMetrics"]["clearing"]
        self.assertEqual(expert["q20Skill"], 26.5)
        self.assertEqual(expert["folderReferenceSkill"], 22.5)
        self.assertAlmostEqual(expert["shrinkageWeight"], .5)
        self.assertAlmostEqual(expert["estimatedDifficulty"], 26.9)  # Sparse fallback preserves the raw delta.
        self.assertEqual(expert["ratedClearCount"], 20)
        for cid in ("low0", "low1", "low2", "low3", "low4"):
            self.assertEqual(rows[cid]["tierMetrics"]["clearing"], by_id(base)[cid]["tierMetrics"]["clearing"])
            self.assertNotIn("officialEvidence", rows[cid])
            self.assertEqual(without_mode_rank(rows[cid]), without_mode_rank(by_id(base)[cid]))

    def test_submitted_metrics_survive_caps_missing_scores_and_all_boundaries(self):
        charts = [chart(f"s{level}",level) for level in range(20,26)] + [
            chart(f"d{level}",level,"Double") for level in range(21,27)]
        base = payload(charts)
        boards = [board(c["id"],["official:1"],rawRowCount=300,maxRank=300) for c in charts
                  if c["id"] != "s23"]
        rows = by_id(apply_official_tiers(base,{**self.snapshot,"boards":boards},charts))
        for c in charts:
            row = rows[c["id"]]
            self.assertEqual("officialEvidence" in row, c["level"] >= (23 if c["type"] == "Single" else 25))
            if is_official_clearing_target(c):
                if is_official_clearing_target(c):
                    self.assertEqual(row["tierMetrics"]["clearing"]["skillMetric"],CLEAR_ABILITY_METRIC)
                else:
                    self.assertEqual(row["tierMetrics"]["clearing"],by_id(base)[c["id"]]["tierMetrics"]["clearing"])
            else:
                self.assertEqual(row["tierMetrics"]["clearing"],by_id(base)[c["id"]]["tierMetrics"]["clearing"])
        self.assertEqual(rows["s23"]["officialEvidence"]["status"],"missing")
        self.assertIsNotNone(rows["s23"]["tierMetrics"]["clearing"]["estimatedDifficulty"])
        self.assertIsNone(rows["s23"]["tierMetrics"]["pumbility"]["estimatedDifficulty"])

    def test_boards_below_history_scope_do_not_expand_official_clearing_histories(self):
        charts = [chart(f"s21-{i}",21) for i in range(25)] + [chart("target",25)]
        snapshot = {**self.snapshot,"boards":[board(c["id"],["official:1"]) for c in charts]}
        rows = by_id(apply_official_tiers(payload(charts),snapshot,charts))
        self.assertEqual(rows["target"]["tierMetrics"]["clearing"]["ratedClearCount"],0)
        self.assertIsNone(rows["target"]["tierMetrics"]["clearing"]["estimatedDifficulty"])

    def test_popularity_affects_shrinkage_not_raw_ability_and_missing_history_is_excluded(self):
        base, snapshot, charts = ability_fixture()
        before = by_id(apply_official_tiers(base, snapshot, charts))
        # Reduce the expert chart from 20 to 5 players with identical other histories.
        next(b for b in snapshot["boards"] if b["chartId"] == "expert")["entries"] = [
            e for e in next(b for b in snapshot["boards"] if b["chartId"] == "expert")["entries"][:5]]
        after = by_id(apply_official_tiers(base, snapshot, charts))
        first, second = (r["expert"]["tierMetrics"]["clearing"] for r in (before, after))
        self.assertEqual(first["q20Skill"], second["q20Skill"])
        self.assertLess(second["estimatedDifficulty"], first["estimatedDifficulty"])
        self.assertGreater(second["estimatedDifficulty"], 25.5)
        self.assertIsNotNone(second["q20SkillCi95Low"])
        target = next(b for b in snapshot["boards"] if b["chartId"] == "expert")
        target["entries"].append({"playerId": "official:no-history", "score": 990000, "place": 1})
        with_missing = by_id(apply_official_tiers(base, snapshot, charts))["expert"]["tierMetrics"]["clearing"]
        self.assertEqual(with_missing["ratedClearCount"], 5)
        self.assertEqual(with_missing["missingSkillCount"], 1)
        self.assertAlmostEqual(with_missing["abilityCoverage"], 5/6, places=5)
        self.assertEqual(with_missing["q20Skill"], second["q20Skill"])

    def test_skill_percentile_interpolates_and_intervals_are_deterministic(self):
        base, snapshot, charts = ability_fixture()
        mixed = chart("mixed", 25)
        charts.append(mixed)
        base["singles"] += payload([mixed])["singles"]
        snapshot["boards"].append(board("mixed", ["official:n0"] + [f"official:e{i}" for i in range(4)]))
        first = by_id(apply_official_tiers(base, snapshot, charts))["mixed"]["tierMetrics"]["clearing"]
        self.assertAlmostEqual(first["q20Skill"], 25.724)  # 22.62 + .8 * (26.5 - 22.62)
        second = by_id(apply_official_tiers(base, snapshot, charts))["mixed"]["tierMetrics"]["clearing"]
        self.assertEqual(first, second)
        self.assertLessEqual(first["q20SkillCi95Low"], first["q20Skill"])
        self.assertGreaterEqual(first["q20SkillCi95High"], first["q20Skill"])

    def test_caps_take_folder_minimum_and_retain_scoring(self):
        rows = by_id(self.result)
        for prefix, level in (("s", 25), ("d", 26)):
            floor = min(rows[prefix + name]["tierMetrics"]["clearing"]["estimatedDifficulty"]
                        for name in ("hard", "middle", "easy"))
            self.assertNotEqual(floor, level)
            for suffix in ("cap", "cap2"):
                row = rows[prefix + suffix]
                clearing = row["tierMetrics"]["clearing"]
                self.assertEqual(clearing["estimatedDifficulty"], floor)
                self.assertEqual(clearing["capDefaultDifficulty"], floor)
                self.assertEqual(clearing["capDefaultBasis"], "uncapped-folder-minimum")
                self.assertTrue(clearing["defaultedAtCap"])
                self.assertEqual(clearing["clearCount"], 300)
                self.assertEqual(row["officialEvidence"]["status"], "possibly-truncated")
                self.assertIsNotNone(row["estimatedDifficulty"])
                self.assertIsNotNone(row["tierMetrics"]["pumbility"]["estimatedDifficulty"])
        fallback = rows["allcap"]["tierMetrics"]["clearing"]
        self.assertEqual(fallback["estimatedDifficulty"], 27.5)
        self.assertEqual(fallback["capDefaultBasis"], "folder-midpoint-no-uncapped-charts")
        # Source cap detection survives deduplication below 300 players.
        changed = copy.deepcopy(self.snapshot)
        changed["boards"][3]["entries"] = changed["boards"][3]["entries"][:20]
        after = by_id(apply_official_tiers(self.base, changed, self.charts))["scap"]
        self.assertTrue(after["tierMetrics"]["clearing"]["defaultedAtCap"])
        self.assertEqual(after["tierMetrics"]["clearing"]["clearCount"], 300)
        self.assertEqual(after["scoringPlayerCount"], 20)

    def test_small_nonempty_boards_remain_rated(self):
        charts = [chart("one", 25), chart("ten", 25), chart("eleven", 25)]
        boards = [board(item["id"], [f"official:{i}" for i in range(count)])
                  for item, count in zip(charts, (1, 10, 11))]
        rows = by_id(apply_official_tiers(payload(charts), {**self.snapshot, "boards": boards}, charts))
        for cid, count in (("one", 1), ("ten", 10), ("eleven", 11)):
            row = rows[cid]
            self.assertGreater(row["scoringPlayerCount"], 0)
            self.assertTrue(row["scoringProvisional"])
            self.assertIsNotNone(row["estimatedDifficulty"])
            self.assertEqual(row["tierMetrics"]["clearing"]["ratedClearCount"], 0)
            self.assertIsNone(row["tierMetrics"]["clearing"]["estimatedDifficulty"])
            self.assertIsNone(row["tierMetrics"]["pumbility"]["estimatedDifficulty"])

    def test_missing_and_empty_boards_do_not_fall_back_to_submitted_data(self):
        charts = [chart("missing", 25), chart("empty", 25)]
        snapshot = {**self.snapshot, "boards": [board("empty", [])]}
        rows = by_id(apply_official_tiers(payload(charts), snapshot, charts))
        self.assertEqual(rows["missing"]["officialEvidence"]["status"], "missing")
        for row in rows.values():
            self.assertIsNotNone(row["officialEvidence"]["unavailableReason"])
            self.assertIsNone(row["estimatedDifficulty"])
            self.assertIsNone(row["tierMetrics"]["clearing"]["estimatedDifficulty"])
            self.assertIsNone(row["tierMetrics"]["pumbility"]["estimatedDifficulty"])
            self.assertIsNone(row["tierMetrics"]["clearing"]["folderReferenceSkill"])
        missing = apply_official_tiers(payload(charts), None, charts)
        self.assertTrue(all(row["estimatedDifficulty"] is None for row in missing["singles"]))

    def test_composite_uses_unrounded_components_and_eligible_support(self):
        base, snapshot, charts = ability_fixture()
        for row in apply_official_tiers(base, snapshot, charts)["singles"]:
            if "officialEvidence" not in row:
                continue
            clearing, composite = row["tierMetrics"]["clearing"], row["tierMetrics"]["pumbility"]
            if clearing["estimatedDifficulty"] is not None and row["estimatedDifficulty"] is not None:
                self.assertAlmostEqual(composite["estimatedDifficulty"], (row["estimatedDifficulty"] + clearing["estimatedDifficulty"])/2, places=5)
                self.assertEqual(composite["clearingSupportCount"], clearing["ratedClearCount"])
            else:
                self.assertIsNone(composite["estimatedDifficulty"])
                self.assertEqual(composite["evidenceStatus"], "Unrated")

    def test_source_exclusivity_metadata_and_no_identity_disclosure(self):
        altered = copy.deepcopy(self.base)
        for row in altered["singles"] + altered["doubles"]:
            row.update(estimatedDifficulty=-999, difficultyDelta=-999, nContributors=100000,
                       scoringScoreProfile=[999999] * 5, phoenix1Contributors=50000, phoenix2Contributors=50000)
            if is_official_clearing_target(row):
                row["tierMetrics"]["clearing"] = {"q10Skill": 999, "estimatedDifficulty": 999}
        changed = by_id(apply_official_tiers(altered, self.snapshot, self.charts))
        for cid, row in by_id(self.result).items():
            if "officialEvidence" in row:
                self.assertEqual(without_mode_rank(changed[cid]), without_mode_rank(row))
        self.assertNotIn('"playerId"', json.dumps(self.result))
        with self.assertRaises(ValueError):
            apply_official_tiers(self.base, {**self.snapshot, "source": "player-submitted"}, self.charts)
        method = self.result["summary"]["method"]["officialTiers"]
        self.assertEqual(method["version"], 15)
        self.assertEqual(method["capPolicy"], "clearing-folder-minimum")
        self.assertEqual(method["scoring"]["metric"], "equal-player-score-gaps")
        self.assertEqual(method["scoring"]["minimumOtherCharts"], 3)
        self.assertEqual(method["scoring"]["aggregation"], "equal-player-mean")

    def test_clearing_reference_and_cap_floor_are_separate_for_each_mode(self):
        changed = copy.deepcopy(self.snapshot)
        for item in changed["boards"]:
            if item["chartId"] == "dhard":
                item["entries"] = item["entries"][:1]
        result = by_id(apply_official_tiers(self.base, changed, self.charts))
        original = by_id(self.result)
        for cid, row in original.items():
            if cid.startswith("s") and "officialEvidence" in row:
                self.assertEqual(result[cid]["tierMetrics"]["clearing"], row["tierMetrics"]["clearing"])


if __name__ == "__main__":
    unittest.main()
