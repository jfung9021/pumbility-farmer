import copy
import unittest
from unittest.mock import patch

from pro_pumbility import ProPumbilityError
from pro_pumbility_modes import METHOD, build_pro_artifacts, consolidate_pro_modes, percentile50

AT = "2026-11-22T17:33:26Z"
NOW = "2026-11-23T03:00:00Z"
KEY = "a" * 20


def fixture():
    charts = [{"id": "s24", "type": "Single", "level": 24, "difficulty": "S24", "songName": "Single target"},
              {"id": "d25", "type": "Double", "level": 25, "difficulty": "D25", "songName": "Double target"}]
    placements = []
    for kind, level, prefix in (("Single", 20, "s"), ("Double", 21, "d")):
        for i in range(50):
            cid = f"{prefix}-base-{i}"
            charts.append({"id": cid, "type": kind, "level": level, "difficulty": prefix.upper() + str(level), "songName": cid})
            placements.append({"chartId": cid, "score": 1_000_000, "isSupplemented": False})
    rankings = [{"rank": pid, "rating": 20000 - pid, "player": {
        "playerId": pid, "gameTag": f"PLAYER{pid}#1234", "isSupplemented": False}} for pid in range(1,13)]
    histories = [{"mix": "Phoenix2", "source": "official-leaderboard", "playerId": f"official:{pid}",
                  "asOf": AT, "fetchedAtUtc": NOW, "rank": pid, "pumbility": 20000-pid,
                  "placements": copy.deepcopy(placements)} for pid in range(1,13)]
    snapshot = {"schemaVersion": 1, "mix": "Phoenix2", "snapshotAt": AT, "fetchedAtUtc": NOW,
                "charts": charts, "rankings": {"snapshotAt": AT, "ratingIsOfficial": True, "data": rankings},
                "officialProfiles": histories,
                "boards": [{"chartId": cid, "asOf": AT, "possiblyTruncated": False,
                            "entries": [{"playerId": f"official:{pid}", "score": 995000, "place": pid} for pid in range(2,13)]}
                           for cid in ("s24", "d25")]}
    profile = {"mix": "Phoenix2", "playerKey": KEY, "displayName": "Selected", "officialPlayerId": "official:1",
               "scoresComplete": True, "scores": [], "scoresSyncedAtUtc": NOW}
    return snapshot, profile


def build(snapshot, profiles):
    return build_pro_artifacts(snapshot, profiles, generated_at_utc=NOW)[1][KEY]


class ModeEngineTests(unittest.TestCase):
    def test_q50_is_linear_and_validates_inputs(self):
        self.assertEqual(percentile50([900000,910000,920000,930000]), 915000)
        self.assertEqual(percentile50([930000,900000,910000]), 910000)
        self.assertEqual(percentile50([995000]), 995000)
        for values in ([], [True], [1000001]):
            with self.assertRaises(ProPumbilityError):
                percentile50(values)

    def test_modes_use_median_targets_and_truncate_the_earned_grade(self):
        snapshot, profile = fixture()
        for board in snapshot["boards"]:
            board["entries"] = board["entries"][:4]
            for entry, score in zip(board["entries"], [970000, 980000, 990000, 1000000]):
                entry["score"] = score
        result = build(snapshot, [profile])
        for mode in ("singles", "doubles"):
            row = result["modes"][mode]["recommendations"][0]
            self.assertEqual(row["rawPercentileScore"], 985000)
            self.assertEqual(row["targetGrade"], "SS+")
            self.assertEqual(row["targetScore"], 985000)
            self.assertEqual(row["assumedPlate"], "MG")
            self.assertEqual(row["scoreAtOrAboveTargetCount"], 2)
            self.assertEqual(row["gainRank"], 1)
            self.assertEqual(row["scoreCountRank"], 1)
            self.assertEqual(row["rankScore"], 2)
            self.assertEqual(row["recommendationRank"], 1)
            overall_row = next(r for r in result["overall"]["recommendations"] if r["chartId"] == row["chartId"])
            self.assertEqual(overall_row["rawPercentileScore"], row["rawPercentileScore"])
            self.assertEqual(overall_row["modeProjectedGain"], row["projectedGain"])

    def test_independent_mode_ratings_and_combined_overall_baseline(self):
        snapshot, profile = fixture()
        result = build(snapshot, [profile])
        self.assertEqual(result["schemaVersion"], 2)
        self.assertEqual(result["method"], METHOD)
        self.assertNotIn("baseline", result)
        for mode, cid in (("singles", "s24"), ("doubles", "d25")):
            state = result["modes"][mode]
            self.assertEqual(state["modePumbility"], 17860)
            self.assertEqual(state["baseline"]["cutoffPumbility"], 357.2)
            self.assertEqual(state["cohort"]["playerCount"], 11)
            row = state["recommendations"][0]
            self.assertEqual(row["chartId"], cid)
            self.assertEqual(row["sourceMode"], mode)
            self.assertEqual(row["projectedGain"], 34.36)
            overall_row = next(r for r in result["overall"]["recommendations"] if r["chartId"] == cid)
            self.assertEqual(overall_row["modeProjectedGain"], row["projectedGain"])
            self.assertEqual(overall_row["projectedGain"], 34.36)
        self.assertEqual(len(result["overall"]["recommendations"]), 2)
        self.assertEqual(result["overall"]["baseline"]["scoreCount"], 100)
        self.assertEqual(len(result["overall"]["baseline"]["top50"]), 50)
        for row in result["overall"]["recommendations"]:
            self.assertEqual(row["scoreAtOrAboveTargetCount"], 11)
        self.assertEqual([row["recommendationRank"] for row in result["overall"]["recommendations"]], [1, 2])

    def test_full_mode_rank_aggregation_promotes_supported_target_and_overall_reranks(self):
        snapshot, profile = fixture()
        base_board = snapshot["boards"][0]
        for cid, level, count in (("s25-high-gain", 25, 1), ("s23-lower-gain", 23, 10)):
            snapshot["charts"].append({"id": cid, "type": "Single", "level": level, "songName": cid})
            snapshot["boards"].append({**base_board, "chartId": cid, "entries": copy.deepcopy(base_board["entries"][:count])})
        result = build(snapshot, [profile])
        rows = result["modes"]["singles"]["recommendations"]
        self.assertEqual([row["chartId"] for row in rows], ["s24", "s25-high-gain", "s23-lower-gain"])
        self.assertEqual([(row["gainRank"], row["scoreCountRank"], row["rankScore"]) for row in rows], [(2, 1, 3), (1, 3, 4), (3, 2, 5)])
        overall = {row["chartId"]: row for row in result["overall"]["recommendations"]}
        self.assertEqual(overall["s24"]["scoreAtOrAboveTargetCount"], 11)
        self.assertNotEqual(overall["s25-high-gain"]["scoreCountRank"], rows[1]["scoreCountRank"])
        self.assertEqual([row["recommendationRank"] for row in result["overall"]["recommendations"]], [1, 2, 3, 4])

    def test_score_count_is_at_target_not_total_observed_or_plate_threshold(self):
        snapshot, profile = fixture()
        board = snapshot["boards"][0]
        board["entries"] = board["entries"][:4]
        for row, score in zip(board["entries"], (995000, 995001, 995002, 1000000)):
            row["score"] = score
        result = build(snapshot, [profile])["modes"]["singles"]["recommendations"][0]
        self.assertEqual(result["assumedPlate"], "SG")
        self.assertEqual(result["targetScore"], 995000)
        self.assertEqual(result["peerScoreCount"], 4)
        self.assertEqual(result["scoreAtOrAboveTargetCount"], 4)

    def test_overall_uses_combined_cutoff_without_mutating_mode_gains(self):
        snapshot, profile = fixture()
        profile["scores"] = [{"chartId": f"d-base-{i}", "score": 1000000, "pumbility": 380, "plate": "PG"} for i in range(50)]
        result = build(snapshot, [profile])
        self.assertEqual(result["overall"]["baseline"]["top50Pumbility"], 19000)
        self.assertEqual(result["overall"]["baseline"]["cutoffPumbility"], 380)
        single = result["modes"]["singles"]["recommendations"][0]
        overall = next(r for r in result["overall"]["recommendations"] if r["chartId"] == "s24")
        self.assertEqual(single["projectedGain"], 34.36)
        self.assertEqual(overall["modeProjectedGain"], 34.36)
        self.assertEqual(overall["replacedPumbility"], 380)
        self.assertEqual(overall["projectedGain"], 11.56)
        self.assertEqual(overall["rawPercentileScore"], single["rawPercentileScore"])

    def test_overall_filters_targets_below_its_cutoff_and_never_zero_fills(self):
        snapshot, profile = fixture()
        profile["scores"] = [{"chartId": f"d-base-{i}", "score": 1000000, "pumbility": 400, "plate": "PG"} for i in range(50)]
        result = build(snapshot, [profile])
        self.assertTrue(result["modes"]["singles"]["recommendations"])
        self.assertEqual(result["overall"]["recommendations"], [])
        profile["scores"] = []
        snapshot["officialProfiles"][0]["placements"] = snapshot["officialProfiles"][0]["placements"][:49]
        result = build(snapshot, [profile])
        self.assertEqual(len(result["overall"]["baseline"]["top50"]), 49)
        self.assertIsNone(result["overall"]["baseline"]["cutoffPumbility"])
        self.assertEqual(result["overall"]["recommendations"], [])

    def test_doubles_changes_cannot_change_single_calculations(self):
        snapshot, profile = fixture()
        before = build(snapshot, [profile])["modes"]["singles"]
        for official in snapshot["officialProfiles"]:
            for row in official["placements"]:
                if row["chartId"].startswith("d-"):
                    row["score"] = 800000
        snapshot["boards"][1]["entries"][0]["score"] = 900000
        after = build(snapshot, [profile])["modes"]["singles"]
        self.assertEqual({k:v for k,v in before.items() if k != "generation"}, {k:v for k,v in after.items() if k != "generation"})

    def test_singles_changes_cannot_change_double_calculations(self):
        snapshot, profile = fixture()
        before = build(snapshot, [profile])["modes"]["doubles"]
        snapshot["officialProfiles"][0]["placements"][0]["score"] = 900000
        after = build(snapshot, [profile])["modes"]["doubles"]
        self.assertEqual({k:v for k,v in before.items() if k != "generation"}, {k:v for k,v in after.items() if k != "generation"})

    def test_actual_facts_override_matching_official_guesses_and_fill_missing(self):
        snapshot, profile = fixture()
        profile["scores"] = [{"mix": "Phoenix2", "chartId": "s-base-0", "score": 1000000,
                              "pumbility": 357.2, "plate": "Perfect Game", "isBroken": False}]
        result = build(snapshot, [profile])
        baseline = result["modes"]["singles"]["baseline"]
        self.assertEqual(baseline["source"], "mixed")
        self.assertEqual(len(baseline["top50"]), 50)
        row = next(r for r in baseline["top50"] if r["chartId"] == "s-base-0")
        self.assertFalse(row["plateEstimated"])
        self.assertEqual(result["modes"]["singles"]["modePumbility"], 17860)
        self.assertLess(17860, result["player"]["overallPumbility"])

    def test_partial_mode_is_not_zero_filled_and_other_mode_still_consolidates(self):
        snapshot, profile = fixture()
        snapshot["officialProfiles"][0]["placements"] = [r for r in snapshot["officialProfiles"][0]["placements"] if r["chartId"] != "s-base-0"]
        result = build(snapshot, [profile])
        self.assertEqual(result["modes"]["singles"]["status"], "insufficient-data")
        self.assertIsNone(result["modes"]["singles"]["modePumbility"])
        self.assertEqual([r["chartId"] for r in result["overall"]["recommendations"]], [r["chartId"] for r in result["modes"]["doubles"]["recommendations"]])
        self.assertEqual(result["overall"]["unavailableModes"], ["singles"])

    def test_incomplete_peer_population_is_rejected(self):
        snapshot, profile = fixture()
        snapshot["officialProfiles"].pop()
        with self.assertRaisesRegex(ProPumbilityError, "complete ranked"):
            build(snapshot, [profile])

    def test_lower_overall_rank_can_be_a_mode_peer_and_not_a_recipient(self):
        snapshot, profile = fixture()
        snapshot["rankings"]["data"][1]["rank"] = 501
        snapshot["officialProfiles"][1]["rank"] = 501
        result = build(snapshot, [profile])
        self.assertEqual(result["modes"]["singles"]["cohort"]["playerCount"], 11)
        profile["officialPlayerId"] = "official:2"
        self.assertEqual(build(snapshot, [profile])["status"], "ineligible")

    def test_rank_500_receives_new_s23_and_d24_mode_targets(self):
        snapshot, profile = fixture()
        snapshot["rankings"]["data"][0]["rank"] = 500
        snapshot["officialProfiles"][0]["rank"] = 500
        for chart in snapshot["charts"][:2]:
            chart["level"] -= 1
            chart["difficulty"] = ("S" if chart["type"] == "Single" else "D") + str(chart["level"])
        result = build(snapshot, [profile])
        self.assertTrue(result["eligible"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["modes"]["singles"]["recommendations"][0]["level"], 23)
        self.assertEqual(result["modes"]["doubles"]["recommendations"][0]["level"], 24)
        snapshot["rankings"]["data"][0]["rank"] = 501
        snapshot["officialProfiles"][0]["rank"] = 501
        self.assertFalse(build(snapshot, [profile])["eligible"])

    def test_duplicate_account_links_do_not_inflate_peer_population(self):
        snapshot, profile = fixture()
        alias = {**profile, "playerKey": "b" * 20}
        result = build(snapshot, [profile, alias])
        self.assertEqual(result["modes"]["singles"]["cohort"]["playerCount"], 11)

    def test_consolidator_does_not_call_scoring_and_rejects_stale_children(self):
        snapshot, profile = fixture()
        result = build(snapshot, [profile])
        with patch("pro_pumbility_modes.percentile50", side_effect=AssertionError("No scoring in Overall")):
            overall = consolidate_pro_modes(result["modes"], result["generation"])
        self.assertEqual(overall, result["overall"])
        result["modes"]["doubles"]["generation"] = "stale"
        with self.assertRaises(ProPumbilityError):
            consolidate_pro_modes(result["modes"], result["generation"])

    def test_existing_chart_replaces_its_own_value(self):
        snapshot, profile = fixture()
        snapshot["officialProfiles"][0]["placements"].append({"chartId": "s24", "score": 996000, "isSupplemented": False})
        for row in snapshot["boards"][0]["entries"]:
            row["score"] = 1000000
        result = build(snapshot, [profile])
        row = result["modes"]["singles"]["recommendations"][0]
        self.assertEqual(row["currentPumbility"], 392.08)
        self.assertEqual(row["projectedGain"], 3.12)
        self.assertEqual(row["targetScore"], 1000000)
        overall = next(r for r in result["overall"]["recommendations"] if r["chartId"] == "s24")
        self.assertEqual(overall["replacedPumbility"], 392.08)
        self.assertEqual(overall["projectedGain"], 3.12)

    def test_zero_positive_gains_is_ready_and_phoenix1_never_changes_mode(self):
        snapshot, profile = fixture()
        for board in snapshot["boards"]:
            for row in board["entries"]:
                row["score"] = 800000
        result = build(snapshot, [profile])
        self.assertEqual(result["overall"]["recommendations"], [])
        self.assertEqual(result["overall"]["availableModes"], ["singles", "doubles"])
        profile["scores"].append({"mix": "Phoenix1", "chartId": "s-base-0", "pumbility": 99999, "score": 1000000})
        changed = build(snapshot, [profile])
        self.assertEqual(result["modes"]["singles"]["baseline"], changed["modes"]["singles"]["baseline"])


if __name__ == "__main__":
    unittest.main()
