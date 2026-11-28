from __future__ import annotations

import unittest
from unittest.mock import Mock

from piu_misgrade_analyzer import ApiError
from piu_recommendations import public_player_key
from scripts.capture_pro_pumbility import fetch_official_board, identity_profiles, is_candidate, normalize_official_profile, same_instant, timestamp


class ProCaptureTests(unittest.TestCase):
    def test_unmirrored_board_404_is_observed_as_missing_and_has_no_target(self):
        from pro_pumbility_modes import build_pro_artifacts
        from tests.test_pro_pumbility_modes import AT, NOW, fixture

        client = Mock()
        client._get_json.side_effect = ApiError("PIU Scores returned HTTP 404. Detail: No official board is mirrored for that chart.")
        chart_id = "00000000-0000-0000-0000-000000000001"
        board = fetch_official_board(client, chart_id, AT)
        self.assertEqual(board["entries"], [])
        self.assertEqual(board["asOf"], AT)
        self.assertEqual(board["boardStatus"], "unmirrored")
        self.assertEqual(board["asOfSource"], "official-ranking-snapshot")
        self.assertEqual(board["sourceResponse"]["statusCode"], 404)
        self.assertEqual(board["source"], "piuscores-official")
        self.assertFalse(board["possiblyTruncated"])
        snapshot, profile = fixture()
        snapshot["charts"][0]["id"] = chart_id
        snapshot["boards"][0] = board
        result = build_pro_artifacts(snapshot, [profile], generated_at_utc=NOW)[1][profile["playerKey"]]
        self.assertEqual(result["modes"]["singles"]["unobservedChartCount"], 1)
        self.assertEqual(result["modes"]["singles"]["recommendations"], [])
        self.assertEqual(len(result["modes"]["doubles"]["recommendations"]), 1)

    def test_other_official_board_errors_still_fail(self):
        for message in ("PIU Scores returned HTTP 404. Detail: Chart not found.",
                        "PIU Scores returned HTTP 500 after retries.",
                        "PIU Scores rejected the credential or access grant (HTTP 403)."):
            client = Mock()
            client._get_json.side_effect = ApiError(message)
            with self.subTest(message=message), self.assertRaises(ApiError):
                fetch_official_board(client, "00000000-0000-0000-0000-000000000001", "2026-11-22T17:33:26Z")

    def test_official_profile_is_pinned_to_player_and_snapshot(self):
        at = "2026-11-22T17:33:26.426950Z"
        ranking = {"player": {"playerId": 1}, "rating": 19000, "rank": 100}
        payload = {"player": {"playerId": 1, "isSupplemented": False},
                   "pumbilityIsSupplemented": False, "pumbility": 19000, "pumbilityRank": 100,
                   "history": [{"at": at}], "placements": [
                       {"chartId": "chart", "score": 995000, "isSupplemented": False}]}
        result = normalize_official_profile(payload, ranking, at, at)
        self.assertEqual(result["source"], "official-leaderboard")
        self.assertEqual(result["playerId"], "official:1")
        self.assertEqual(result["placements"][0]["score"], 995000)
        for override in ({"player": {"playerId": 2, "isSupplemented": False}},
                         {"history": [{"at": "2026-11-21T17:33:26Z"}]},
                         {"pumbilityIsSupplemented": True},
                         {"placements": [{"chartId": "chart", "score": 995000, "isSupplemented": True}]}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                normalize_official_profile({**payload, **override}, ranking, at, at)

    def test_current_official_chart_floors(self):
        self.assertTrue(is_candidate({"type": "Single", "level": 23}))
        self.assertTrue(is_candidate({"type": "Double", "level": 24}))
        self.assertFalse(is_candidate({"type": "Single", "level": 22}))
        self.assertFalse(is_candidate({"type": "Double", "level": 23}))
        self.assertFalse(is_candidate({"type": "CoOp", "level": 27}))

    def test_dotnet_timestamp_precision_is_accepted(self):
        self.assertEqual(timestamp("2026-11-22T17:33:26.4269505+00:00"), "2026-11-22T17:33:26.426950Z")
        self.assertTrue(same_instant("2026-11-22T17:33:26.4269505+00:00", "2026-11-23T02:33:26.426950+09:00"))
        self.assertFalse(same_instant("2026-11-22T17:33:26Z", "2026-11-23T17:33:26Z"))
        self.assertFalse(same_instant("2026-11-22T17:33:26", "2026-11-22T17:33:26Z"))

    def test_exact_tags_allow_many_accounts_but_refuse_ambiguous_official_tags(self):
        ranking = {"data": [
            {"player": {"playerId": 1, "gameTag": "PLAYER#1"}},
            {"player": {"playerId": 2, "gameTag": "DUP#1"}},
            {"player": {"playerId": 3, "gameTag": "DUP#1"}},
        ]}
        profiles = identity_profiles([
            {"userId": "a", "username": "one", "gameTag": "PLAYER#1", "isPublic": True},
            {"userId": "b", "username": "two", "gameTag": "PLAYER#1", "isPublic": True},
            {"userId": "c", "username": "three", "gameTag": "DUP#1"},
            {"userId": "d", "username": "four", "gameTag": None},
            {"userId": "e", "username": "five", "gameTag": "player#1"},
        ], ranking)
        by_key = {row["playerKey"]: row for row in profiles}
        self.assertEqual(by_key[public_player_key("a")]["officialPlayerId"], "official:1")
        self.assertEqual(by_key[public_player_key("b")]["officialPlayerId"], "official:1")
        self.assertEqual(by_key[public_player_key("c")]["matchStatus"], "ambiguous")
        self.assertIsNone(by_key[public_player_key("c")]["officialPlayerId"])
        self.assertEqual(by_key[public_player_key("d")]["matchStatus"], "missing-tag")
        self.assertEqual(by_key[public_player_key("e")]["matchStatus"], "unmatched")
        self.assertTrue(all(row["mix"] == "Phoenix2" and not row["scoresComplete"] for row in profiles))
        self.assertTrue(all("gameTag" not in row for row in profiles))


if __name__ == "__main__":
    unittest.main()
