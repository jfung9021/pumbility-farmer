from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from official_scores import (
    capture_official_snapshot,
    is_official_target,
    is_official_history_chart,
    is_official_clearing_target,
    load_official_snapshot,
    normalize_official_board,
    validate_official_snapshot,
)


S25 = "00000000-0000-0000-0000-000000000025"
D26 = "00000000-0000-0000-0000-000000000026"
AS_OF = "2026-09-28T02:22:05.1222517+09:00"
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
CHARTS = [{"id": S25, "type": "Single", "level": 25}, {"id": D26, "type": "Double", "level": 26}]


def row(player=1, score=900000, place=1):
    return {"place": place, "score": score, "player": {
        "playerId": player, "isSupplemented": False, "gameTag": "discard-me", "avatarUrl": "discard-me",
    }}


def board(rows=None, as_of=AS_OF):
    return {"asOf": as_of, "data": [row()] if rows is None else rows}


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.paths = []

    def _get_json(self, path):
        self.paths.append(path)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class OfficialScoresTests(unittest.TestCase):
    def test_history_scope_is_separate_from_scoring_scope(self):
        for mode, level, expected in [("Single", 21, False), ("Single", 22, True),
                                      ("Double", 22, False), ("Double", 23, True), ("CoOp", 25, False)]:
            chart = {"type": mode, "level": level}
            self.assertEqual(is_official_history_chart(chart), expected)
            self.assertFalse(is_official_target(chart))

    def test_extend_histories_reuses_high_boards_and_validates_complete_expanded_scope(self):
        lower = {"id": "00000000-0000-0000-0000-000000000022", "type": "Single", "level": 22}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current.json"
            original = capture_official_snapshot(FakeClient([board(), board()]), CHARTS, path, now=lambda: NOW)
            client = FakeClient([board([row(player=2)])])
            extended = capture_official_snapshot(client, [*CHARTS, lower], path, include_history=True,
                                                 extend_existing=True, now=lambda: NOW)
            self.assertEqual(len(client.paths), 1)
            self.assertEqual(extended["historyMinimumLevels"], {"Single": 22, "Double": 23})
            self.assertEqual({b["chartId"]: b for b in extended["boards"] if b["chartId"] != lower["id"]},
                             {b["chartId"]: b for b in original["boards"]})
            validate_official_snapshot(extended, [*CHARTS, lower])
            with self.assertRaises(ValueError):
                validate_official_snapshot({**extended, "boards": original["boards"]}, [*CHARTS, lower])

    def test_extend_histories_rejects_a_new_week_without_replacing_current(self):
        lower = {"id": "00000000-0000-0000-0000-000000000022", "type": "Single", "level": 22}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current.json"
            capture_official_snapshot(FakeClient([board(), board()]), CHARTS, path, now=lambda: NOW)
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "upstream week changed"):
                capture_official_snapshot(FakeClient([board(as_of="2026-10-05T00:00:00Z")]), [*CHARTS, lower],
                                          path, include_history=True, extend_existing=True, now=lambda: NOW)
            self.assertEqual(path.read_bytes(), before)

    def test_previous_history_scope_is_accepted_only_for_extension(self):
        lower = {"id": "00000000-0000-0000-0000-000000000022", "type": "Single", "level": 22}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current.json"
            original = capture_official_snapshot(FakeClient([board(),board()]), CHARTS, path, now=lambda: NOW)
            original["historyMinimumLevels"] = {"Single":23,"Double":24}
            path.write_text(json.dumps(original),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"clearing-history scope"):
                load_official_snapshot(path)
            client = FakeClient([board()])
            extended = capture_official_snapshot(client,[*CHARTS,lower],path,include_history=True,
                                                 extend_existing=True,now=lambda:NOW)
            self.assertEqual(len(client.paths),1)
            self.assertEqual(extended["historyMinimumLevels"],{"Single":22,"Double":23})
            self.assertEqual({b["chartId"]:b for b in original["boards"]},
                             {b["chartId"]:b for b in extended["boards"] if b["chartId"]!=lower["id"]})

    def test_exact_scope_and_mode_boundaries(self):
        for mode, level, expected in [
            ("Single", 20, False), ("Single", 21, False), ("Single", 23, False), ("Single", 24, False), ("Single", 25, True), ("Single", 26, True),
            ("Double", 21, False), ("Double", 22, False), ("Double", 24, False), ("Double", 25, False), ("Double", 26, True), ("Double", 29, True),
            ("CoOp", 25, False), ("Single", None, False),
        ]:
            with self.subTest(mode=mode, level=level):
                self.assertEqual(is_official_target({"type": mode, "level": level}), expected)

    def test_official_clearing_starts_at_s25_d26(self):
        for mode, level, expected in [("Single",24,False),("Single",25,True),
                                       ("Double",25,False),("Double",26,True),("CoOp",26,False)]:
            self.assertEqual(is_official_clearing_target({"type":mode,"level":level}),expected)

    def test_capture_includes_history_below_tier_boundary_only_when_requested(self):
        history = {"id":"00000000-0000-0000-0000-000000000022","type":"Single","level":22}
        lower = {"id":"00000000-0000-0000-0000-000000000021","type":"Single","level":21}
        charts = [*CHARTS, history, lower]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = capture_official_snapshot(FakeClient([board(),board(),board()]), charts,
                Path(directory)/"current.json", include_history=True, now=lambda:NOW)
            self.assertEqual({b["chartId"] for b in snapshot["boards"]}, {S25,D26,history["id"]})
            self.assertEqual(snapshot["historyMinimumLevels"],{"Single":22,"Double":23})
            tiers = capture_official_snapshot(FakeClient([board(),board()]), charts,
                Path(directory)/"tiers.json", now=lambda:NOW)
            self.assertEqual({b["chartId"] for b in tiers["boards"]}, {S25,D26})

    def test_broader_cache_remains_valid_but_missing_or_unknown_boards_do_not(self):
        lower = {"id":"00000000-0000-0000-0000-000000000021","type":"Single","level":21}
        with tempfile.TemporaryDirectory() as directory:
            snapshot = capture_official_snapshot(FakeClient([board(),board()]), CHARTS,
                Path(directory)/"current.json", now=lambda:NOW)
            snapshot["boards"].append(normalize_official_board(lower["id"], board()))
            validate_official_snapshot(snapshot, [*CHARTS,lower])
            with self.assertRaisesRegex(ValueError, "target catalog"):
                validate_official_snapshot(snapshot, CHARTS)
            with self.assertRaisesRegex(ValueError, "target catalog"):
                validate_official_snapshot({**snapshot,"boards":snapshot["boards"][1:]}, [*CHARTS,lower])

    def test_normalization_dedupes_best_score_and_removes_public_identity_fields(self):
        result = normalize_official_board(S25, board([row(9, 910000, 2), row(9, 900000, 4), row(7, 950000, 1)]))
        self.assertEqual(result["entries"], [
            {"playerId": "official:7", "score": 950000, "place": 1},
            {"playerId": "official:9", "score": 910000, "place": 2},
        ])
        self.assertEqual(result["rawRowCount"], 3)
        self.assertEqual(result["maxRank"], 4)
        self.assertEqual(result["cutoffScore"], 900000)
        self.assertNotIn("discard-me", json.dumps(result))

    def test_cap_uses_raw_rows_and_rank_before_deduplication(self):
        rows = [row(i + 1, 900000 - i, i + 1) for i in range(299)]
        self.assertFalse(normalize_official_board(S25, board(rows))["possiblyTruncated"])
        rows.append(row(1, 899000, 300))
        result = normalize_official_board(S25, board(rows))
        self.assertEqual(len(result["entries"]), 299)
        self.assertEqual(result["rawRowCount"], 300)
        self.assertTrue(result["possiblyTruncated"])
        self.assertTrue(normalize_official_board(S25, board([row(place=300)]))["possiblyTruncated"])

    def test_empty_is_valid_but_null_and_malformed_or_supplemented_rows_are_rejected(self):
        empty = normalize_official_board(S25, board([]))
        self.assertEqual((empty["entries"], empty["rawRowCount"], empty["maxRank"], empty["cutoffScore"]), ([], 0, 0, None))
        invalid = [board([row(score=None)]), board([row(score=1000001)]), board([row(score=True)]),
                   board([row(player="1")]), board([row(player=0)]), board([row(place=0)]),
                   board(as_of="2026-09-28"), {"asOf": AS_OF, "data": None}, {"data": []}]
        for value in (True, None, "false"):
            candidate = board()
            candidate["data"][0]["player"]["isSupplemented"] = value
            invalid.append(candidate)
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    normalize_official_board(S25, payload)

    def test_capture_pins_filters_and_publishes_only_complete_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current.json"
            client = FakeClient([board(), board([])])
            snapshot = capture_official_snapshot(client, CHARTS, path, now=lambda: NOW)
            self.assertEqual(load_official_snapshot(path, CHARTS), snapshot)
            self.assertEqual(snapshot["source"], "piuscores-official")
            self.assertEqual(snapshot["mix"], "Phoenix2")
            self.assertTrue(all(url.endswith("?mix=Phoenix2&supplemented=false") for url in client.paths))
            self.assertFalse(path.with_suffix(".pending.json").exists())
            with self.assertRaises(ValueError):
                validate_official_snapshot(snapshot, CHARTS[:1])
            corrupt = copy.deepcopy(snapshot)
            corrupt["boards"][0]["possiblyTruncated"] = True
            with self.assertRaises(ValueError):
                validate_official_snapshot(corrupt)

    def test_failed_or_mixed_week_capture_preserves_current(self):
        for responses in ([board(), ValueError("request failed")], [board(), board(as_of="2026-10-05T00:00:00Z")]):
            with self.subTest(responses=responses), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "current.json"
                path.write_text("previous artifact", encoding="utf-8")
                with self.assertRaises(ValueError):
                    capture_official_snapshot(FakeClient(responses), CHARTS, path, now=lambda: NOW)
                self.assertEqual(path.read_text(encoding="utf-8"), "previous artifact")

    def test_resume_reuses_only_matching_week_after_live_first_board(self):
        third = {"id": "00000000-0000-0000-0000-000000000027", "type": "Double", "level": 27}
        charts = [*CHARTS, third]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current.json"
            with self.assertRaises(ValueError):
                capture_official_snapshot(FakeClient([board(), board(), ValueError("stop")]), charts, path, now=lambda: NOW)
            resumed = FakeClient([board(), board()])
            capture_official_snapshot(resumed, charts, path, now=lambda: NOW)
            self.assertEqual(len(resumed.paths), 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current.json"
            with self.assertRaises(ValueError):
                capture_official_snapshot(FakeClient([board(), board(), ValueError("stop")]), charts, path, now=lambda: NOW)
            new_week = "2026-10-05T00:00:00Z"
            resumed = FakeClient([board(as_of=new_week)] * 3)
            snapshot = capture_official_snapshot(resumed, charts, path, now=lambda: NOW)
            self.assertEqual(len(resumed.paths), 3)
            self.assertEqual(snapshot["asOf"], new_week)


if __name__ == "__main__":
    unittest.main()
