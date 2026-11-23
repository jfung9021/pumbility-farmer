from __future__ import annotations

import unittest

from piu_recommendations import public_player_key
from scripts.capture_pro_pumbility import identity_profiles, is_candidate, normalize_official_profile, same_instant, timestamp


class ProCaptureTests(unittest.TestCase):
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
        self.assertTrue(is_candidate({"type": "Single", "level": 24}))
        self.assertTrue(is_candidate({"type": "Double", "level": 25}))
        self.assertFalse(is_candidate({"type": "Single", "level": 23}))
        self.assertFalse(is_candidate({"type": "Double", "level": 24}))
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
