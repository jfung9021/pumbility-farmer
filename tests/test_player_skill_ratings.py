from __future__ import annotations

import unittest
from copy import deepcopy

from player_skill_ratings import (
    cleared_chart_ids_by_player,
    clearing_ratings_by_player_mode,
    clearing_skill_for_chart_ids,
    clearing_skill_method,
)


def charts_for_mode(mode="Single", count=60):
    return [{"id": f"{mode}-{index:03d}", "type": mode, "level": 30 - index // 10} for index in range(count)]


class ClearingSkillTests(unittest.TestCase):
    def test_qualification_and_exact_rank_window(self):
        charts = charts_for_mode(count=80)
        for count in (0, 10, 11, 29, 30, 49, 50, 51, 80):
            with self.subTest(count=count):
                result = clearing_skill_for_chart_ids([row["id"] for row in charts[:count]], charts, "Single")
                self.assertEqual(result["clearingSkill"]["uniqueClearCount"], count)
                self.assertEqual(result["clearingSkill"]["requiredClearCount"], 50)
                self.assertEqual(result["clearingSkill"]["ranks"], [1, 50])
                if count < 50:
                    self.assertIsNone(result["clearingRating"])
                    self.assertEqual(result["clearingSkill"]["selectedCount"], 0)
                    self.assertEqual(result["clearingSkill"]["status"], "insufficient-clears")
                else:
                    self.assertEqual(result["clearingRating"], 28.0)
                    self.assertEqual(result["clearingSkill"]["selectedCount"], 50)
                    self.assertEqual(result["clearingSkill"]["status"], "rated")

    def test_ties_and_duplicate_source_records_do_not_expand_window(self):
        charts = [{**row, "level": 22} for row in charts_for_mode()]
        ids = [row["id"] for row in charts]
        result = clearing_skill_for_chart_ids(ids + list(reversed(ids)), charts, "Single")
        self.assertEqual(result["clearingRating"], 22)
        self.assertEqual(result["clearingSkill"]["selectedCount"], 50)
        self.assertEqual(result["clearingSkill"]["uniqueClearCount"], 60)
        short = clearing_skill_for_chart_ids(ids[:25] * 2, charts, "Single")
        self.assertIsNone(short["clearingRating"])
        self.assertEqual(short["clearingSkill"]["uniqueClearCount"], 25)

    def test_top_and_fiftieth_clear_are_included_but_fifty_first_is_not(self):
        charts = [{**row, "level": 20} for row in charts_for_mode(count=51)]
        charts[0]["level"] = 30
        charts[49]["level"] = 12
        charts[50]["level"] = 1
        result = clearing_skill_for_chart_ids([row["id"] for row in reversed(charts)], charts, "Single")
        self.assertEqual(result["clearingRating"], (30 + 48 * 20 + 12) / 50)
        self.assertEqual(result["clearingSkill"]["selectedCount"], 50)

    def test_modes_qualify_independently_and_bulk_agrees(self):
        charts = charts_for_mode(count=50) + charts_for_mode("Double", count=49)
        ids = [row["id"] for row in charts]
        singles = clearing_skill_for_chart_ids(ids, charts, "Single")
        doubles = clearing_skill_for_chart_ids(ids, charts, "Double")
        self.assertEqual(singles["clearingSkill"]["uniqueClearCount"], 50)
        self.assertEqual(singles["clearingRating"], 28.0)
        self.assertEqual(doubles["clearingSkill"]["uniqueClearCount"], 49)
        self.assertIsNone(doubles["clearingRating"])
        self.assertEqual(clearing_ratings_by_player_mode({chart_id: {"player"} for chart_id in ids}, charts), {("player", "Single"): 28.0})
        with self.assertRaises(ValueError):
            clearing_skill_for_chart_ids(ids, charts, "CoOp")

    def test_membership_validates_raw_clears_modes_and_current_levels(self):
        current = [
            {"id": "zero", "type": "Single", "level": 10},
            {"id": "rerated", "type": "Single", "level": 22},
            {"id": "mode-changed", "type": "Double", "level": 23},
            {"id": "invalid-level", "type": "Single", "level": float("nan")},
            {"id": "malformed", "type": "Single", "level": "bad"},
            {"id": "broken", "type": "Single", "level": 20},
            {"id": "missing-pb", "type": "Single", "level": 20},
        ]
        old = [{**row, "level": 30, "type": "Single"} for row in current] + [{"id": "removed", "type": "Single", "level": 20}]
        scores = [{"playerId": "player", "chartId": row["id"], "pumbility": 0} for row in old]
        scores += [dict(scores[0])]
        next(row for row in scores if row["chartId"] == "broken")["isBroken"] = True
        next(row for row in scores if row["chartId"] == "missing-pb").pop("pumbility")
        snapshot = {"charts": old, "scores": scores}
        self.assertEqual(cleared_chart_ids_by_player(snapshot, current), {"player": {"zero", "rerated"}})
        updated = deepcopy(snapshot)
        for row in updated["scores"]:
            if row["chartId"] in {"zero", "rerated"}:
                row.update(pumbility=1000, score=999_999, letterGrade="SSS+")
        self.assertEqual(cleared_chart_ids_by_player(updated, current), cleared_chart_ids_by_player(snapshot, current))

    def test_current_rerates_and_sub_sixteen_clears_supply_official_levels(self):
        charts = [{**row, "level": 12} for row in charts_for_mode(count=50)]
        ids = [row["id"] for row in charts]
        self.assertEqual(clearing_skill_for_chart_ids(ids, charts, "Single")["clearingRating"], 12)
        self.assertEqual(clearing_skill_method()["difficultyBasis"], "current-official-level")


if __name__ == "__main__":
    unittest.main()
