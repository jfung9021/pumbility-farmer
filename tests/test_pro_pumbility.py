import copy
import unittest

from pro_pumbility import (
    ProPumbilityError,
    build_pro_artifacts,
    build_pro_recommendation,
    percentile50,
    projected_target,
    select_peer_cohort,
)


AS_OF = "2026-11-22T17:33:26.426950+00:00"
NOW = "2026-11-23T03:00:00+00:00"


def ranking(pid, rating, rank=None):
    return {"rank": rank or pid, "rating": rating,
            "player": {"playerId": pid, "gameTag": f"PLAYER{pid}#1234", "isSupplemented": False}}


def chart(cid, kind="Single", level=24):
    return {"id": cid, "songName": cid, "type": kind, "level": level,
            "difficulty": ("S" if kind == "Single" else "D") + str(level)}


def fixture():
    rankings = [ranking(1, 1_000, 20)] + [ranking(pid, 1_000 + pid, pid) for pid in range(2, 12)]
    charts = [chart("s24"), chart("d25", "Double", 25), chart("s22", level=22), chart("d23", "Double", 23)]
    boards = [{"chartId": cid, "asOf": AS_OF, "possiblyTruncated": False,
               "entries": [{"playerId": f"official:{pid}", "score": 1_000_000, "place": pid} for pid in range(2, 12)]}
              for cid in ("s24", "d25")]
    snapshot = {"schemaVersion": 1, "mix": "Phoenix2", "snapshotAt": AS_OF,
                "fetchedAtUtc": NOW, "rankings": {"snapshotAt": AS_OF, "ratingIsOfficial": True, "data": rankings},
                "charts": charts, "boards": boards}
    placements = []
    for index in range(50):
        cid = f"baseline-{index}"
        charts.append(chart(cid, level=20))
        placements.append({"chartId": cid, "score": 1_000_000, "isSupplemented": False})
    snapshot["officialProfiles"] = [{"mix": "Phoenix2", "source": "official-leaderboard", "asOf": AS_OF,
        "fetchedAtUtc": NOW, "playerId": "official:1", "pumbility": 1_000, "rank": 20, "placements": placements}]
    profile = {"mix": "Phoenix2", "playerKey": "a" * 20, "displayName": "Selected player",
               "officialPlayerId": "official:1", "scores": [], "scoresComplete": True,
               "scoresSyncedAtUtc": NOW}
    return snapshot, profile


def score(cid, pumbility, *, raw=990_000, plate="Marvelous Game", mix="Phoenix2"):
    return {"mix": mix, "chartId": cid, "playerId": "private-account-id", "pumbility": pumbility,
            "score": raw, "plate": plate, "isBroken": False}


class ProCohortTests(unittest.TestCase):
    def test_first_window_keeps_every_eligible_peer_and_inclusive_bounds(self):
        rows = [ranking(1, 1_000)] + [ranking(pid, 1_000 + (pid - 2) * 10) for pid in range(2, 13)]
        peers, cohort = select_peer_cohort(rows + [copy.deepcopy(rows[1])], "official:1")
        self.assertEqual(cohort["upwardWindow"], 100)
        self.assertEqual(cohort["playerCount"], 11)
        self.assertEqual(cohort["backfillCount"], 0)
        self.assertEqual(cohort["minimumPumbility"], 1_000)
        self.assertEqual(cohort["maximumPumbility"], 1_100)
        self.assertEqual(len(set(peers)), 11)
        self.assertNotIn("official:1", peers)

    def test_widens_only_below_ten_and_stops_at_150_or_200(self):
        for edge, expected_window in ((100, 100), (150, 150), (200, 200)):
            with self.subTest(edge=edge):
                rows = [ranking(1, 1_000)] + [ranking(pid, 1_001 + pid) for pid in range(2, 11)]
                rows += [ranking(11, 1_000 + edge), ranking(12, 1_201)]
                peers, cohort = select_peer_cohort(rows, 1)
                self.assertEqual(cohort["upwardWindow"], expected_window)
                self.assertEqual(cohort["playerCount"], 10)
                self.assertEqual(cohort["backfillCount"], 0)
                self.assertIn("official:11", peers)
                self.assertNotIn("official:12", peers)

    def test_backfills_nearest_lower_players_including_below_rank_300(self):
        rows = [ranking(1, 1_000, 300), ranking(2, 1_050, 290), ranking(3, 1_200, 280), ranking(4, 1_201, 270)]
        rows += [ranking(pid, 1_000 - pid, 300 + pid) for pid in range(5, 16)]
        peers, cohort = select_peer_cohort(rows, 1)
        self.assertEqual(cohort["upwardWindow"], 200)
        self.assertEqual(cohort["backfillCount"], 8)
        self.assertEqual(cohort["playerCount"], 10)
        self.assertEqual(set(peers), {f"official:{pid}" for pid in (2, 3, *range(5, 13))})
        self.assertNotIn("official:4", peers)

    def test_number_one_gets_next_ten_and_equal_rating_ties_are_stable(self):
        rows = [ranking(1, 2_000)] + [ranking(pid, 2_000 - pid) for pid in range(2, 15)]
        peers, cohort = select_peer_cohort(list(reversed(rows)), 1)
        self.assertEqual(set(peers), {f"official:{pid}" for pid in range(2, 12)})
        self.assertEqual(cohort["backfillCount"], 10)
        rows = [ranking(1, 2_000)] + [ranking(pid, 1_999, 30 - pid) for pid in range(2, 15)]
        peers, _ = select_peer_cohort(rows, 1)
        self.assertEqual(set(peers), {f"official:{pid}" for pid in range(5, 15)})

    def test_insufficient_population_reports_limited_support(self):
        peers, cohort = select_peer_cohort([ranking(1, 1_000), ranking(2, 999), ranking(3, 998)], 1)
        self.assertEqual(len(peers), 2)
        self.assertTrue(cohort["limitedData"])
        self.assertEqual(cohort["backfillCount"], 2)


class ProTargetTests(unittest.TestCase):
    def test_known_linear_percentile_and_score_validation(self):
        self.assertEqual(percentile50([900_000, 910_000, 920_000, 930_000]), 915_000)
        self.assertEqual(percentile50([930_000, 900_000, 910_000]), 910_000)
        self.assertEqual(percentile50([1_000_000]), 1_000_000)
        with self.assertRaises(ProPumbilityError):
            percentile50([])

    def test_grade_truncation_and_exact_plate_boundaries(self):
        for raw, grade, boundary, plate, displayed in (
            (993_400, "SSS", 990_000, "MG", 990_000),
            (995_000, "SSS+", 995_000, "MG", 995_000),
            (995_000.25, "SSS+", 995_000, "SG", 995_000),
            (999_999.99, "SSS+", 995_000, "SG", 995_000),
            (1_000_000, "SSS+", 995_000, "PG", 1_000_000),
        ):
            with self.subTest(raw=raw):
                target = projected_target(raw)
                self.assertEqual(target["rawPercentileScore"], raw)
                self.assertEqual(target["targetGrade"], grade)
                self.assertEqual(target["gradeBoundaryScore"], boundary)
                self.assertEqual(target["assumedPlate"], plate)
                self.assertEqual(target["targetScore"], displayed)


class ProRecommendationTests(unittest.TestCase):
    def test_only_s24_and_d25_candidates_and_raw_pg_display(self):
        snapshot, profile = fixture()
        result = build_pro_recommendation(snapshot, profile, generated_at_utc=NOW)
        self.assertEqual(result["status"], "ready")
        self.assertIsNone(result["message"])
        self.assertEqual(result["scope"], "overall")
        self.assertEqual(result["candidateCount"], 2)
        self.assertEqual({row["chartId"] for row in result["recommendations"]}, {"s24", "d25"})
        for row in result["recommendations"]:
            self.assertEqual(row["targetScore"], 1_000_000)
            self.assertEqual(row["gradeBoundaryScore"], 995_000)
            self.assertEqual(row["assumedPlate"], "PG")
            self.assertEqual(row["expectedPumbility"], 395.2)
            self.assertEqual(row["scoreAtOrAboveTargetCount"], 10)
            self.assertEqual(row["gainRank"], 1)
            self.assertEqual(row["scoreCountRank"], 1)
            self.assertEqual(row["rankScore"], 2)

    def test_overall_engine_final_sort_uses_gain_and_target_count_ranks(self):
        snapshot, profile = fixture()
        for cid, level, count in (("s25-high", 25, 1), ("s23-low", 23, 9)):
            snapshot["charts"].append(chart(cid, level=level))
            snapshot["boards"].append({**snapshot["boards"][0], "chartId": cid,
                                       "entries": copy.deepcopy(snapshot["boards"][0]["entries"][:count])})
        rows = build_pro_recommendation(snapshot, profile)["recommendations"]
        self.assertEqual({row["chartId"] for row in rows[:2]}, {"s24", "d25"})
        self.assertEqual(rows[2]["chartId"], "s25-high")
        self.assertEqual([row["recommendationRank"] for row in rows], [1, 2, 3, 4])

    def test_self_and_duplicates_do_not_inflate_percentile_or_coverage(self):
        snapshot, profile = fixture()
        board = snapshot["boards"][0]
        board["entries"] = [{"playerId": "official:1", "score": 1_000_000},
                            {"playerId": "official:2", "score": 990_000},
                            {"playerId": "official:2", "score": 995_000},
                            {"playerId": "official:3", "score": 990_000}]
        board["possiblyTruncated"] = True
        row = next(row for row in build_pro_recommendation(snapshot, profile)["recommendations"] if row["chartId"] == "s24")
        self.assertEqual(row["peerScoreCount"], 2)
        self.assertEqual(row["cohortPlayerCount"], 10)
        self.assertEqual(row["rawPercentileScore"], 992_500)
        self.assertEqual(row["targetGrade"], "SSS")
        self.assertEqual(row["assumedPlate"], "MG")
        self.assertTrue(row["boardPossiblyTruncated"])
        self.assertTrue(row["limitedData"])

    def test_shared_top_fifty_baseline_uses_all_standard_levels_and_real_values(self):
        snapshot, profile = fixture()
        for index in range(49):
            cid = f"low-{index}"
            snapshot["charts"].append(chart(cid, "Single" if index % 2 else "Double", 20))
            profile["scores"].append(score(cid, 350))
        profile["scores"].append(score("s24", 390, plate="Ultimate Game"))
        result = build_pro_recommendation(snapshot, profile)
        self.assertEqual(result["baseline"]["scoreCount"], 50)
        self.assertEqual(result["baseline"]["top50Pumbility"], 17_540)
        self.assertEqual(result["baseline"]["cutoffPumbility"], 350)
        rows = {row["chartId"]: row for row in result["recommendations"]}
        self.assertEqual(rows["s24"]["projectedGain"], 5.2)
        self.assertEqual(rows["s24"]["currentPlate"], "Ultimate Game")
        self.assertEqual(rows["d25"]["projectedGain"], 45.2)

    def test_outside_top_fifty_pb_replaces_cutoff_not_its_existing_value(self):
        snapshot, profile = fixture()
        for index in range(50):
            cid = f"low-{index}"
            snapshot["charts"].append(chart(cid, level=20))
            profile["scores"].append(score(cid, 380))
        profile["scores"].append(score("s24", 300))
        row = next(row for row in build_pro_recommendation(snapshot, profile)["recommendations"] if row["chartId"] == "s24")
        self.assertEqual(row["projectedGain"], 15.2)

    def test_personal_better_result_is_preserved_and_only_positive_gains_remain(self):
        snapshot, profile = fixture()
        profile["scores"] = [score("s24", 400), score("s24", 390), score("d25", 400)]
        for index in range(48):
            cid = f"personal-{index}"
            snapshot["charts"].append(chart(cid, level=20))
            profile["scores"].append(score(cid, 350))
        result = build_pro_recommendation(snapshot, profile)
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["baseline"]["scoreCount"], 50)
        self.assertEqual(result["baseline"]["top50Pumbility"], 17_600)
        self.assertEqual(result["baseline"]["source"], "piuscores")

    def test_missing_scores_are_unknown_and_empty_board_is_reported(self):
        snapshot, profile = fixture()
        snapshot["boards"][0]["entries"] = []
        result = build_pro_recommendation(snapshot, profile)
        self.assertEqual(result["unobservedChartCount"], 1)
        self.assertEqual([row["chartId"] for row in result["recommendations"]], ["d25"])

    def test_rank_500_qualifies_501_does_not(self):
        for rank, expected in ((300, "ready"), (301, "ready"), (500, "ready"), (501, "ineligible")):
            with self.subTest(rank=rank):
                snapshot, profile = fixture()
                snapshot["rankings"]["data"][0]["rank"] = rank
                snapshot["officialProfiles"][0]["rank"] = rank
                self.assertEqual(build_pro_recommendation(snapshot, profile)["status"], expected)

    def test_new_s23_and_d24_candidates_are_included(self):
        snapshot, profile = fixture()
        for cid, kind, level in (("s23", "Single", 23), ("d24", "Double", 24)):
            snapshot["charts"].append(chart(cid, kind, level))
            snapshot["boards"].append({**snapshot["boards"][0], "chartId": cid})
        result = build_pro_recommendation(snapshot, profile)
        self.assertEqual(result["candidateCount"], 4)
        self.assertEqual({row["chartId"] for row in result["recommendations"]}, {"s24", "d25", "s23", "d24"})

    def test_explicit_identity_and_baseline_unavailable_states(self):
        snapshot, profile = fixture()
        profile["officialPlayerId"] = None
        self.assertEqual(build_pro_recommendation(snapshot, profile)["status"], "identity-unresolved")
        profile["officialPlayerId"] = "official:1"
        profile["scoresComplete"] = False
        fallback = build_pro_recommendation(snapshot, profile)
        self.assertEqual(fallback["status"], "ready")
        self.assertEqual(fallback["baseline"]["source"], "official-leaderboard")
        del snapshot["officialProfiles"]
        result = build_pro_recommendation(snapshot, profile)
        self.assertEqual(result["status"], "baseline-unavailable")
        self.assertTrue(result["eligible"])
        self.assertEqual(result["cohort"]["playerCount"], 10)
        self.assertEqual(result["recommendations"], [])

    def test_empty_import_reconstructs_real_cutoff_instead_of_empty_slots(self):
        snapshot, profile = fixture()
        result = build_pro_recommendation(snapshot, profile)
        baseline = result["baseline"]
        self.assertEqual(baseline["source"], "official-leaderboard")
        self.assertTrue(baseline["estimated"])
        self.assertEqual(len(baseline["top50"]), 50)
        self.assertEqual(baseline["cutoffPumbility"], 357.2)
        self.assertEqual(result["recommendations"][0]["projectedGain"], 38.0)
        self.assertTrue(all(row["plateEstimated"] for row in baseline["top50"]))

    def test_fewer_than_fifty_official_scores_never_turn_into_empty_slots(self):
        snapshot, profile = fixture()
        snapshot["officialProfiles"][0]["placements"].pop()
        result = build_pro_recommendation(snapshot, profile)
        self.assertEqual(result["status"], "baseline-unavailable")
        self.assertEqual(result["baseline"]["scoreCount"], 49)
        self.assertEqual(result["recommendations"], [])

    def test_incomplete_import_below_official_total_also_reconstructs(self):
        snapshot, profile = fixture()
        profile["scores"] = [score(f"baseline-{index}", 350) for index in range(50)]
        snapshot["rankings"]["data"][0]["rating"] = 18_000
        snapshot["officialProfiles"][0]["pumbility"] = 18_000
        result = build_pro_recommendation(snapshot, profile)
        self.assertEqual(result["baseline"]["source"], "official-leaderboard")
        self.assertIn("incomplete import", result["baseline"]["fallbackReason"])

    def test_official_current_chart_score_is_deduplicated_and_uses_guessed_plate(self):
        snapshot, profile = fixture()
        snapshot["officialProfiles"][0]["placements"].extend([
            {"chartId": "s24", "score": 990_000, "isSupplemented": False},
            {"chartId": "s24", "score": 996_000, "isSupplemented": False}])
        result = build_pro_recommendation(snapshot, profile)
        row = next(row for row in result["recommendations"] if row["chartId"] == "s24")
        self.assertEqual(row["currentScore"], 996_000)
        self.assertEqual(row["currentPlate"], "SG")
        self.assertTrue(row["currentPlateEstimated"])
        self.assertEqual(row["currentPumbility"], 392.08)
        self.assertEqual(row["projectedGain"], 3.12)

    def test_phoenix1_rows_cannot_change_personal_baseline_or_targets(self):
        snapshot, profile = fixture()
        expected = build_pro_recommendation(snapshot, profile)
        profile["scores"].append(score("s24", 99_999, raw=1_000_000, plate="Perfect Game", mix="Phoenix1"))
        actual = build_pro_recommendation(snapshot, profile)
        self.assertEqual(actual["baseline"], expected["baseline"])
        self.assertEqual(actual["recommendations"], expected["recommendations"])
        profile["mix"] = "Phoenix1"
        with self.assertRaises(ProPumbilityError):
            build_pro_recommendation(snapshot, profile)

    def test_mixed_or_incomplete_or_supplemented_capture_is_rejected(self):
        for mutation in (
            lambda snap: snap.update(mix="Phoenix1"),
            lambda snap: snap["boards"][0].update(asOf=NOW),
            lambda snap: snap["boards"].pop(),
            lambda snap: snap["rankings"]["data"][0]["player"].update(isSupplemented=True),
            lambda snap: snap["boards"][0].update(mix="Phoenix1"),
        ):
            snapshot, profile = fixture()
            mutation(snapshot)
            with self.assertRaises(ProPumbilityError):
                build_pro_recommendation(snapshot, profile)

    def test_artifact_generation_changes_when_personal_scores_change(self):
        snapshot, profile = fixture()
        index, payloads = build_pro_artifacts(snapshot, [profile], generated_at_utc=NOW)
        self.assertEqual(index["generation"], payloads[profile["playerKey"]]["generation"])
        self.assertEqual(index["generatedAtUtc"], NOW)
        self.assertEqual(len(index["players"]), 11)
        self.assertEqual(sum(row["status"] == "baseline-unavailable" for row in index["players"]), 10)
        profile["scores"].append(score("s24", 400))
        changed, _ = build_pro_artifacts(snapshot, [profile], generated_at_utc=NOW)
        self.assertNotEqual(changed["generation"], index["generation"])


if __name__ == "__main__":
    unittest.main()
