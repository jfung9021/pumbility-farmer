from __future__ import annotations

import copy
import io
import json
import unittest
from decimal import Decimal

import numpy as np

from general_pumbility import GeneralPumbilityModel, METHOD
from phoenix2_pumbility import phoenix2_pumbility
from pumbility_peer_policy import linear_median, projected_target, select_pumbility_peers
from recommendation_ranking import RANKING_METHOD, rank_recommendations

AT = "2026-11-28T00:00:00Z"


def fixture(*, players=12, count=50, modes=("Single", "Double"), base=100):
    charts, scores = [], []
    for kind in modes:
        prefix = "s" if kind == "Single" else "d"
        for index in range(count):
            cid = f"{prefix}-base-{index:02}"
            charts.append({"id": cid, "type": kind, "level": 10, "songName": cid})
            for player in range(players):
                scores.append({"playerId": f"p{player:02}", "chartId": cid,
                               "pumbility": base, "score": 970000.0, "plate": "FG", "isBroken": False})
        charts.append({"id": f"{prefix}-target", "type": kind, "level": 16, "songName": "Target"})
        for player in range(1, players):
            scores.append({"playerId": f"p{player:02}", "chartId": f"{prefix}-target",
                           "pumbility": 0, "score": 995000.0, "plate": "MG", "isBroken": False})
    return {"mix": "Phoenix2", "generatedAtUtc": AT, "charts": charts, "scores": scores}


def build(snapshot, **kwargs):
    return GeneralPumbilityModel.from_snapshot(snapshot).build_player("p00", include_candidates=True, **kwargs)["modes"]


def candidates(mode):
    return {row["chartId"]: row for row in mode["filterCandidates"]}


class GeneralPumbilityTests(unittest.TestCase):
    def test_public_history_and_targets_keep_current_catalog_bpm_with_older_annotations(self):
        snapshot = fixture(players=3, modes=("Double",))
        annotations = []
        current_bpms = {"d-base-00": 195, "d-base-01": 200, "d-target": 230}
        old_bpms = {"d-base-00": None, "d-base-01": 201, "d-target": 229.5}
        for chart in snapshot["charts"]:
            cid = chart["id"]
            if cid in current_bpms:
                current = current_bpms[cid]
                chart.update(bpmMin=current, bpmMax=current, bpmFocal=current, songType="Arcade")
                annotations.append({"chartId": cid, "bpmMin": old_bpms[cid], "bpmMax": old_bpms[cid],
                                    "songType": "Old variant", "estimatedDifficulty": 16.25})
        modes = build(snapshot, chart_annotations=annotations)
        history = {row["chartId"]: row for row in modes["doubles"]["topScores"]}
        target = candidates(modes["doubles"])["d-target"]
        overall_target = candidates(modes["overall"])["d-target"]
        for row in (history["d-base-00"], history["d-base-01"], target, overall_target):
            with self.subTest(chart=row["chartId"]):
                bpm = current_bpms[row["chartId"]]
                self.assertEqual((row["bpmMin"], row["bpmFocal"], row["bpmMax"]), (bpm, bpm, bpm))
                self.assertEqual(row["songType"], "Arcade")
                self.assertEqual(row["estimatedDifficulty"], 16.25)

    def test_actual_top50_scores_and_pure_median_targets(self):
        modes = build(fixture())
        for mode, cid in (("singles", "s-target"), ("doubles", "d-target")):
            result = modes[mode]
            self.assertEqual(result["skillMethod"], METHOD)
            self.assertEqual(result["modePumbility"], 5000)
            self.assertEqual(result["requiredScoreCount"], 50)
            self.assertEqual(result["cohort"]["playerCount"], 11)
            self.assertEqual(result["cohort"]["upwardWindow"], 200)
            self.assertEqual(result["cohort"]["minimumPlayers"], 20)
            row = candidates(result)[cid]
            self.assertEqual(row["projectedScore"], 995000)
            self.assertEqual(row["projectedPlateCode"], "MG")
            self.assertEqual(row["plateProjectionSource"], "piuscores-peer-median")
            self.assertNotIn("assumedPlate", row)
            self.assertEqual(row["peerScoreCount"], 11)
            self.assertEqual(row["scoreAtOrAboveTargetCount"], 11)
            self.assertEqual((row["gainRank"], row["scoreCountRank"], row["rankScore"], row["recommendationRank"]), (1, 1, 2, 1))
            self.assertIsNone(row["estimatedDifficulty"])
            self.assertEqual(row["expectedPumbility"], phoenix2_pumbility(row["type"], 16, "SSS+", "MG"))
            self.assertEqual(row["projectedGain"], round(row["expectedPumbility"] - 100, 2))
            self.assertNotIn("scoringRating", result)
            self.assertNotIn("clearingSkill", result)
            self.assertNotIn("projectionRating", result)
        self.assertEqual(modes["overall"]["currentTop50Pumbility"], 5000)
        self.assertEqual(modes["overall"]["validScoreCount"], 100)

    def test_49_unique_is_unavailable_and_known_zero_is_a_valid_50th(self):
        snapshot = fixture(players=1, count=49, modes=("Single",))
        snapshot["scores"].append(copy.deepcopy(snapshot["scores"][0]))
        self.assertFalse(build(snapshot)["singles"]["eligible"])
        snapshot["charts"].append({"id": "zero", "type": "Single", "level": 1})
        snapshot["scores"].append({"playerId": "p00", "chartId": "zero", "pumbility": 0, "score": None})
        result = build(snapshot)["singles"]
        self.assertTrue(result["eligible"])
        self.assertEqual(result["validScoreCount"], 50)
        self.assertEqual(result["currentTop50CutoffPumbility"], 0)
        self.assertEqual(result["topScores"][-1]["pumbility"], 0)
        self.assertIsNone(result["topScores"][-1]["score"])

    def test_normalization_dedup_broken_removed_and_invalid_raw_scores(self):
        snapshot = fixture(players=2, modes=("Single",))
        snapshot["scores"].extend([
            {"playerId": "p00", "chartId": "s-base-00", "pumbility": 100, "score": 980000, "recordedAt": "a"},
            {"playerId": "p00", "chartId": "s-base-00", "pumbility": 100, "score": 980000, "recordedAt": "b", "plate": "MG"},
            {"playerId": "p00", "chartId": "s-base-00", "pumbility": 9999, "score": 1000000, "isBroken": True},
            {"playerId": "p00", "chartId": "removed", "pumbility": 9999},
            {"playerId": "p00", "chartId": "s-base-01", "pumbility": float("inf")},
            {"playerId": "p00", "chartId": "s-base-01", "pumbility": -1},
            {"playerId": "p00", "chartId": "s-base-01", "pumbility": True},
        ])
        model = GeneralPumbilityModel.from_snapshot(snapshot)
        self.assertEqual(model.player_counts("p00")["singles"], 50)
        self.assertEqual(model.facts["p00"]["s-base-00"]["score"], 980000)
        self.assertEqual(model.facts["p00"]["s-base-00"]["plate"], "Marvelous Game")
        for invalid in (True, 995000.5, -1, 1000001, float("nan"), None):
            changed = copy.deepcopy(snapshot)
            changed["scores"] = [row for row in changed["scores"] if row["chartId"] != "s-target"]
            changed["scores"].append({"playerId": "p01", "chartId": "s-target", "pumbility": 0, "score": invalid, "plate": "MG"})
            with self.subTest(invalid=invalid):
                result = build(changed)["singles"]
                self.assertNotIn("s-target", candidates(result))
                self.assertEqual(result["unobservedChartCount"], 1)

    def test_float_integral_scores_and_actual_plates_do_not_follow_score_thresholds(self):
        snapshot = fixture(players=3, modes=("Single",))
        targets = [row for row in snapshot["scores"] if row["chartId"] == "s-target"]
        for scores, plates, expected, plate in (((995000.0, 995000.0), ("FG", "FG"), 995000, "FG"),
                                                ((995000.0, 995001.0), ("FG", "MG"), 995000.5, "FG"),
                                                ((1000000.0, 1000000.0), ("FG", "FG"), 1000000, "FG"),
                                                ((1000000.0, 1000000.0), ("PG", "PG"), 1000000, "PG")):
            for row, value, recorded_plate in zip(targets, scores, plates):
                row["score"] = value
                row["plate"] = recorded_plate
            result = candidates(build(snapshot)["singles"])["s-target"]
            self.assertEqual(result["rawPercentileScore"], expected)
            self.assertEqual(result["projectedPlateCode"], plate)
            self.assertNotIn("assumedPlate", result)
            self.assertEqual(result["targetScore"], 1000000 if plate == "PG" else 995000)
            self.assertEqual(result["peerScoreCount"], 2)
            self.assertTrue(result["limitedData"])

    def test_raw_and_lower_ordinal_plate_medians_use_the_same_complete_sample(self):
        snapshot = fixture(players=6, modes=("Single",))
        target_rows = [row for row in snapshot["scores"] if row["chartId"] == "s-target"]
        for row, score, plate in zip(target_rows, (900000, 1000000, 990000, 995000, None),
                                    ("fg", None, "invalid plate", "Superb Game", "PG")):
            row.update(score=score, plate=plate)
        model = GeneralPumbilityModel.from_snapshot(snapshot)
        result = model.build_player("p00", include_candidates=True)["modes"]["singles"]
        row = candidates(result)["s-target"]
        self.assertEqual(row["rawPercentileScore"], 947500)
        self.assertEqual(row["targetGrade"], "AA+")
        self.assertEqual(row["targetScore"], 940000)
        self.assertEqual(row["projectedPlate"], "Fair Game")
        self.assertEqual(row["projectedPlateCode"], "FG")
        self.assertEqual(row["peerScoreCount"], 2)
        self.assertEqual(row["peerPairedScoreCount"], 2)
        self.assertEqual(row["peerRawScoreCount"], 4)
        self.assertEqual(row["peerMissingPlateCount"], 2)
        self.assertEqual(row["scoreProjectionSupportCount"], 2)
        self.assertEqual(row["scoreAtOrAboveTargetCount"], 1)
        self.assertEqual(result["pairedObservationCount"], 2)
        self.assertEqual(result["missingPlateObservationCount"], 2)
        self.assertEqual(model.player_counts("p02")["singles"], 51)
        self.assertEqual(model.facts["p02"]["s-target"]["cents"], 0)
        restored = GeneralPumbilityModel.from_npz_bytes(model.to_npz_bytes())
        self.assertEqual(restored.build_player("p00", include_candidates=True), model.build_player("p00", include_candidates=True))

    def test_missing_or_invalid_plates_never_fall_back_to_assumed_targets(self):
        snapshot = fixture(modes=("Single",))
        for index, row in enumerate(snapshot["scores"]):
            if row["chartId"] == "s-target":
                row["plate"] = None if index % 2 else "invalid"
        result = build(snapshot)["singles"]
        self.assertTrue(result["eligible"])
        self.assertEqual(result["currentTop50Pumbility"], 5000)
        self.assertEqual(result["unobservedChartCount"], 1)
        self.assertEqual(result["missingPlateObservationCount"], 11)
        self.assertEqual(result["pairedObservationCount"], 0)
        self.assertEqual(result["topRecommendations"], [])
        self.assertFalse(result["projectionAvailable"])

    def test_plate_median_uses_game_order_and_lower_middle_at_even_counts(self):
        snapshot = fixture(players=9, modes=("Single",))
        target_rows = [row for row in snapshot["scores"] if row["chartId"] == "s-target"]
        # In game order the middle pair is MG/SG; alphabetical order disagrees.
        for row, plate in zip(target_rows, ("PG", "RG", "UG", "FG", "EG", "TG", "SG", "MG")):
            row["plate"] = plate
        row = candidates(build(snapshot)["singles"])["s-target"]
        self.assertEqual(row["projectedPlateCode"], "MG")
        for pair, expected in ((("RG", "PG"), "RG"), (("FG", "UG"), "FG"),
                               (("TG", "EG"), "TG"), (("MG", "SG"), "MG"),
                               (("SG", "EG"), "SG"), (("EG", "UG"), "EG"),
                               (("UG", "PG"), "UG")):
            small = fixture(players=3, modes=("Single",))
            for record, plate in zip((r for r in small["scores"] if r["chartId"] == "s-target"), pair):
                record["plate"] = plate
            with self.subTest(pair=pair):
                self.assertEqual(candidates(build(small)["singles"])["s-target"]["projectedPlateCode"], expected)

    def test_mode_independence_and_no_official_or_phoenix1_dependency(self):
        snapshot = fixture()
        before = build(snapshot)
        for row in snapshot["scores"]:
            if row["chartId"].startswith("d-"):
                row["pumbility"] = 10
                row["score"] = 1000000
        snapshot["scores"].append({"mix": "Phoenix1", "playerId": "p00", "chartId": "s-target", "pumbility": 99999, "score": 1000000})
        snapshot["officialProfiles"] = [{"playerId": "p00", "pumbility": 999999}]
        after = build(snapshot)
        self.assertEqual(before["singles"], after["singles"])
        self.assertNotEqual(before["doubles"]["modePumbility"], after["doubles"]["modePumbility"])

    def test_existing_top50_chart_replaces_itself_outside_chart_replaces_cutoff(self):
        snapshot = fixture(modes=("Single",))
        snapshot["charts"].extend([
            {"id": "own", "type": "Single", "level": 24, "songName": "Own"},
            {"id": "outside", "type": "Single", "level": 24, "songName": "Outside"},
        ])
        for pid in range(12):
            snapshot["scores"].extend([
                {"playerId": f"p{pid:02}", "chartId": "own", "pumbility": 110, "score": 995000, "plate": "MG"},
                {"playerId": f"p{pid:02}", "chartId": "outside", "pumbility": 90, "score": 995000, "plate": "MG"},
            ])
        rows = candidates(build(snapshot)["singles"])
        self.assertEqual(rows["own"]["replacedPumbility"], 110)
        self.assertEqual(rows["outside"]["replacedPumbility"], 100)
        self.assertEqual(rows["outside"]["existingPumbility"], 90)

    def test_overall_replaces_combined_cutoff_and_retains_mode_gain(self):
        snapshot = fixture()
        for row in snapshot["scores"]:
            if row["chartId"].startswith("d-base"):
                row["pumbility"] = 200
        modes = build(snapshot)
        mode_row = candidates(modes["singles"])["s-target"]
        row = candidates(modes["overall"])["s-target"]
        self.assertEqual(row["replacedPumbility"], 200)
        self.assertEqual(row["modeProjectedGain"], mode_row["projectedGain"])
        self.assertEqual(row["projectedGain"], round(row["expectedPumbility"] - 200, 2))
        self.assertEqual(modes["overall"]["top50ModeCounts"], {"singles": 0, "doubles": 50})
        double_mode = candidates(modes["doubles"])["d-target"]
        double_overall = candidates(modes["overall"])["d-target"]
        self.assertEqual(double_mode["gainRank"], 1)
        self.assertEqual(double_overall["gainRank"], 2)
        self.assertEqual(double_overall["scoreAtOrAboveTargetCount"], double_mode["scoreAtOrAboveTargetCount"])

    def test_partial_mode_facts_contribute_to_overall_with_one_ready_mode(self):
        snapshot = fixture()
        snapshot["scores"] = [row for row in snapshot["scores"]
                              if not (row["playerId"] == "p00" and row["chartId"] == "d-base-49")]
        for row in snapshot["scores"]:
            if row["chartId"].startswith("d-base"):
                row["pumbility"] = 200
        modes = build(snapshot)
        self.assertFalse(modes["doubles"]["eligible"])
        self.assertTrue(modes["overall"]["eligible"])
        self.assertEqual(modes["overall"]["top50ModeCounts"], {"singles": 1, "doubles": 49})
        self.assertEqual({row["sourceMode"] for row in modes["overall"]["filterCandidates"]}, {"singles"})

    def test_both_partial_modes_do_not_invent_ready_mode_opportunities(self):
        modes = build(fixture(count=49))
        self.assertEqual(modes["overall"]["currentTop50Count"], 50)
        self.assertFalse(modes["overall"]["eligible"])
        self.assertEqual(modes["overall"]["filterCandidates"], [])

    def test_catalog_candidates_survive_missing_annotations_old_bounds_and_cap(self):
        snapshot = fixture(modes=("Single",))
        for index in range(60):
            cid = f"target-{index:02}"
            snapshot["charts"].append({"id": cid, "type": "Single", "level": 16 if index == 0 else 30, "songName": cid})
            snapshot["scores"].append({"playerId": "p01", "chartId": cid, "pumbility": 0, "score": 995000, "plate": "MG"})
        modes = build(snapshot)
        for mode in ("singles", "overall"):
            self.assertEqual(len(modes[mode]["topRecommendations"]), 50)
            self.assertEqual(len(modes[mode]["filterCandidates"]), 61)
            self.assertEqual([row["recommendationRank"] for row in modes[mode]["filterCandidates"]], list(range(1, 62)))
            self.assertEqual([row["recommendationRank"] for row in modes[mode]["topRecommendations"]], list(range(1, 51)))
            self.assertIn("target-00", candidates(modes[mode]))
            self.assertIn("target-59", candidates(modes[mode]))
            self.assertEqual(modes[mode]["filterCandidates"][0]["chartId"], "target-01")

    def test_subject_refresh_and_serialization_parity_with_frozen_peers(self):
        snapshot = fixture()
        model = GeneralPumbilityModel.from_snapshot(snapshot)
        restored = GeneralPumbilityModel.from_npz_bytes(model.to_npz_bytes())
        self.assertEqual(restored.metadata, model.metadata)
        self.assertEqual(restored.build_player("p00", include_candidates=True), model.build_player("p00", include_candidates=True))
        own = [row for row in snapshot["scores"] if row["playerId"] == "p00"]
        explicit = restored.build_player("p00", scores=own, chart_catalog=snapshot["charts"], include_candidates=True)
        self.assertEqual(explicit, model.build_player("p00", include_candidates=True))
        own.append({"chartId": "s-target", "score": 1000000, "pumbility": 101})
        result = restored.build_player("p00", scores=own, include_candidates=True)["modes"]["singles"]
        self.assertEqual(result["modePumbility"], 5001)
        row = candidates(result)["s-target"]
        self.assertEqual(row["rawPercentileScore"], 995000)
        self.assertEqual(row["replacedPumbility"], 101)
        self.assertEqual(model.player_counts("p00")["singles"], 50)
        self.assertNotIn("playerId", json.dumps(restored.metadata))
        self.assertNotIn("p01", json.dumps(result))

    def test_latest_subject_catalog_extends_history_without_changing_peer_generation(self):
        snapshot = fixture(modes=("Single",))
        model = GeneralPumbilityModel.from_snapshot(snapshot)
        own = [row for row in snapshot["scores"] if row["playerId"] == "p00"]
        latest = snapshot["charts"] + [{"id": "new", "type": "Single", "level": 20}]
        own.append({"chartId": "new", "pumbility": 101, "score": 1000000})
        result = model.build_player("p00", scores=own, chart_catalog=latest, player_synced_at_utc="later")["modes"]["singles"]
        self.assertEqual(result["validScoreCount"], 51)
        self.assertEqual(result["modePumbility"], 5001)
        self.assertEqual(result["peerGenerationAtUtc"], AT)
        self.assertEqual(result["playerSyncedAtUtc"], "later")

    def test_split_model_merge_matches_complete_model_and_rejects_legacy_artifact(self):
        snapshot = fixture()
        full = GeneralPumbilityModel.from_snapshot(snapshot)
        parts = [GeneralPumbilityModel.from_snapshot(snapshot, mode_types=(kind,)) for kind in ("Single", "Double")]
        merged = GeneralPumbilityModel.merge_models(parts)
        self.assertEqual(full.build_player("p00", include_candidates=True), merged.build_player("p00", include_candidates=True))
        with self.assertRaises(ValueError):
            GeneralPumbilityModel.from_snapshot({"mix": "Phoenix1"})
        stream = io.BytesIO()
        np.savez_compressed(stream, old=np.asarray([1, 2]))
        with self.assertRaises(ValueError):
            GeneralPumbilityModel.from_npz_bytes(stream.getvalue())
        with np.load(io.BytesIO(full.to_npz_bytes()), allow_pickle=False) as saved:
            arrays = {key: saved[key] for key in saved.files}
        header = json.loads(arrays["header"].tobytes().decode("utf-8"))
        for version in (1, 2, 3):
            header["method"] = f"general-piuscores-p50-modes-overall-top50-phoenix2-v{version}"
            arrays["header"] = np.frombuffer(json.dumps(header).encode("utf-8"), dtype=np.uint8)
            old_identity = io.BytesIO()
            np.savez_compressed(old_identity, **arrays)
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "Incompatible"):
                GeneralPumbilityModel.from_npz_bytes(old_identity.getvalue())

    def test_raw_score_support_includes_other_p2_coop_accounts_roundtrips_and_merges(self):
        snapshot = fixture()
        snapshot["charts"].append({"id": "coop", "type": "CoOp", "level": 2})
        snapshot["scores"].extend([
            {"playerId": "p00", "chartId": "coop", "score": 1000000},
            {"playerId": "p01", "chartId": "coop", "score": 990000},
            {"playerId": "p01", "chartId": "coop", "score": 995000.0},
            {"playerId": "p02", "chartId": "coop", "score": 995000},
            {"playerId": "p03", "chartId": "coop", "score": 994999},
            {"playerId": "p04", "chartId": "coop", "score": 1000000, "isBroken": True},
            {"playerId": "p05", "chartId": "coop", "score": 1000000, "mix": "Phoenix1"},
            {"playerId": "p06", "chartId": "coop", "score": 995000.5},
            {"playerId": "p07", "chartId": "coop", "score": None},
            {"playerId": "only-coop", "chartId": "coop", "score": 1000000, "pumbility": None},
            {"playerId": "only-coop", "chartId": "removed", "score": 1000000},
        ])
        model = GeneralPumbilityModel.from_snapshot(snapshot)
        restored = GeneralPumbilityModel.from_npz_bytes(model.to_npz_bytes())
        parts = [GeneralPumbilityModel.from_snapshot(snapshot, mode_types=(kind,)) for kind in ("Single", "Double")]
        merged = GeneralPumbilityModel.merge_models(parts)
        for candidate in (model, restored, merged):
            self.assertEqual(candidate.count_scores_at_least("p00", "coop", 995000.0), 3)
            self.assertEqual(candidate.count_scores_at_least("p00", "coop", 1000000), 1)
            self.assertEqual(candidate.count_scores_at_least("p00", "coop", 0), 4)
            self.assertEqual(candidate.count_scores_at_least("p00", "removed", 0), 0)
            self.assertEqual(candidate.player_counts("only-coop"), {"singles": 0, "doubles": 0})
        self.assertEqual(model.metadata["rankingMethod"], RANKING_METHOD)
        with self.assertRaises(ValueError):
            model.count_scores_at_least("p00", "coop", 995000.5)

    def test_local_200_window_retains_extra_peers_after_default_100_succeeds(self):
        from pro_pumbility import _cohort

        snapshot = fixture(players=13, modes=("Single",))
        for row in snapshot["scores"]:
            if row["playerId"] in {"p11", "p12"} and row["chartId"].startswith("s-base"):
                row["pumbility"] = 103
        default = GeneralPumbilityModel.from_snapshot(snapshot, peer_windows=(100, 150, 200), minimum_peers=10)
        local = GeneralPumbilityModel.from_snapshot(snapshot, peer_windows=(200,), minimum_peers=10)
        default_mode = default.build_player("p00", include_candidates=True)["modes"]["singles"]
        local_mode = local.build_player("p00", include_candidates=True)["modes"]["singles"]
        self.assertEqual(default.metadata["peerWindows"], [100, 150, 200])
        self.assertEqual(local.metadata["peerWindows"], [200])
        self.assertEqual(default_mode["cohort"]["upwardWindow"], 100)
        self.assertEqual(default_mode["cohort"]["playerCount"], 10)
        self.assertEqual(local_mode["cohort"]["upwardWindow"], 200)
        self.assertEqual(local_mode["cohort"]["playerCount"], 12)
        self.assertEqual(candidates(default_mode)["s-target"]["peerScoreCount"], 10)
        self.assertEqual(candidates(local_mode)["s-target"]["peerScoreCount"], 12)
        self.assertEqual(local_mode["modePumbility"], default_mode["modePumbility"])
        official_subject = {"playerId": "self", "rank": 1, "rating": Decimal(5000)}
        official_peers = [{"playerId": f"peer-{index}", "rank": index + 1,
                           "rating": Decimal(5000 if index <= 10 else 5150)} for index in range(1, 13)]
        self.assertEqual(_cohort(official_peers, official_subject)[1]["upwardWindow"], 100)
        self.assertEqual(_cohort(official_peers, official_subject)[1]["playerCount"], 10)

    def test_local_window_policy_roundtrips_merges_and_older_npz_defaults(self):
        snapshot = fixture()
        local = GeneralPumbilityModel.from_snapshot(snapshot, peer_windows=(200,))
        restored = GeneralPumbilityModel.from_npz_bytes(local.to_npz_bytes())
        parts = [GeneralPumbilityModel.from_snapshot(snapshot, mode_types=(kind,), peer_windows=(200,))
                 for kind in ("Single", "Double")]
        merged = GeneralPumbilityModel.merge_models(parts)
        for model in (restored, merged):
            self.assertEqual(model.peer_windows, (200,))
            self.assertEqual(model.metadata["peerWindows"], [200])
            self.assertEqual(model.build_player("p00", include_candidates=True), local.build_player("p00", include_candidates=True))
        default = GeneralPumbilityModel.from_snapshot(snapshot, peer_windows=(100, 150, 200), minimum_peers=10)
        with self.assertRaisesRegex(ValueError, "peer window"):
            GeneralPumbilityModel.merge_models([default, local])
        with np.load(io.BytesIO(default.to_npz_bytes()), allow_pickle=False) as saved:
            arrays = {key: saved[key] for key in saved.files}
        header = json.loads(arrays["header"].tobytes().decode("utf-8"))
        header.pop("peerWindows")
        header.pop("minimumPeers")
        arrays["header"] = np.frombuffer(json.dumps(header).encode("utf-8"), dtype=np.uint8)
        previous = io.BytesIO()
        np.savez_compressed(previous, **arrays)
        old = GeneralPumbilityModel.from_npz_bytes(previous.getvalue())
        self.assertEqual(old.peer_windows, (100, 150, 200))
        self.assertEqual(old.minimum_peers, 10)
        self.assertEqual(old.build_player("p00", include_candidates=True), default.build_player("p00", include_candidates=True))

    def test_local_minimum_20_backfills_15_upper_peers_but_retains_more_than_20(self):
        from pro_pumbility import _cohort

        snapshot = fixture(players=21, modes=("Single",))
        for row in snapshot["scores"]:
            if row["playerId"] >= "p16" and row["chartId"].startswith("s-base"):
                row["pumbility"] = 99
        local = GeneralPumbilityModel.from_snapshot(snapshot, peer_windows=(200,), minimum_peers=20)
        result = local.build_player("p00", include_candidates=True)["modes"]["singles"]
        self.assertEqual(result["cohort"]["minimumPlayers"], 20)
        self.assertEqual(result["cohort"]["playerCount"], 20)
        self.assertEqual(result["cohort"]["backfillCount"], 5)
        self.assertFalse(result["cohort"]["limitedData"])
        self.assertEqual(candidates(result)["s-target"]["peerScoreCount"], 20)
        larger = GeneralPumbilityModel.from_snapshot(fixture(players=24, modes=("Single",)), peer_windows=(200,), minimum_peers=20)
        larger_result = larger.build_player("p00")["modes"]["singles"]
        self.assertEqual(larger_result["cohort"]["playerCount"], 23)
        self.assertEqual(larger_result["cohort"]["backfillCount"], 0)
        official_subject = {"playerId": "self", "rank": 1, "rating": Decimal(5000)}
        official_peers = [{"playerId": f"peer-{index}", "rank": index + 1,
                           "rating": Decimal(5000 if index <= 15 else 4950)} for index in range(1, 21)]
        official_cohort = _cohort(official_peers, official_subject)[1]
        self.assertEqual(official_cohort["minimumPlayers"], 10)
        self.assertEqual(official_cohort["playerCount"], 15)
        self.assertEqual(official_cohort["backfillCount"], 0)

    def test_local_minimum_peer_policy_roundtrips_merges_and_rejects_mixed_parts(self):
        snapshot = fixture()
        local = GeneralPumbilityModel.from_snapshot(snapshot, peer_windows=(200,), minimum_peers=20)
        restored = GeneralPumbilityModel.from_npz_bytes(local.to_npz_bytes())
        parts = [GeneralPumbilityModel.from_snapshot(snapshot, mode_types=(kind,), peer_windows=(200,), minimum_peers=20)
                 for kind in ("Single", "Double")]
        merged = GeneralPumbilityModel.merge_models(parts)
        for model in (restored, merged):
            self.assertEqual(model.minimum_peers, 20)
            self.assertEqual(model.metadata["minimumPeers"], 20)
            self.assertEqual(model.build_player("p00", include_candidates=True), local.build_player("p00", include_candidates=True))
            self.assertTrue(model.build_player("p00")["modes"]["singles"]["cohort"]["limitedData"])
        default_minimum = GeneralPumbilityModel.from_snapshot(snapshot, peer_windows=(200,), minimum_peers=10)
        with self.assertRaisesRegex(ValueError, "minimum peer"):
            GeneralPumbilityModel.merge_models([default_minimum, local])

    def test_zero_positive_gains_is_ready_and_has_observed_projections(self):
        modes = build(fixture(base=1000))
        self.assertTrue(modes["singles"]["eligible"])
        self.assertEqual(modes["singles"]["status"], "ready")
        self.assertTrue(modes["singles"]["projectionAvailable"])
        self.assertEqual(modes["singles"]["topRecommendations"], [])


class PeerPolicyTests(unittest.TestCase):
    def test_windows_are_inclusive_stop_at_success_and_retain_every_peer(self):
        subject = {"playerId": "self", "rating": Decimal(1000)}
        for edge in (100, 150, 200):
            rows = [subject] + [{"playerId": f"peer-{i:02}", "rating": Decimal(1000 + edge)} for i in range(11)]
            peers, meta = select_pumbility_peers(rows, subject)
            self.assertEqual(meta["upwardWindow"], edge)
            self.assertEqual(len(peers), 11)
            self.assertEqual(meta["backfillCount"], 0)
        peers, _ = select_pumbility_peers([subject, {"playerId": "equal", "rating": Decimal(1000)}], subject)
        self.assertEqual([row["playerId"] for row in peers], ["equal"])

    def test_lower_backfill_uses_account_ties_and_does_not_take_high_outliers(self):
        subject = {"playerId": "self", "rating": Decimal(1000)}
        rows = [{"playerId": f"peer-{i:02}", "rating": Decimal(999)} for i in range(12)]
        peers, meta = select_pumbility_peers(list(reversed(rows)) + [subject, {"playerId": "high", "rating": Decimal(1201)}], subject)
        self.assertEqual([row["playerId"] for row in peers], [f"peer-{i:02}" for i in range(10)])
        self.assertEqual(meta["backfillCount"], 10)
        peers, meta = select_pumbility_peers([subject, rows[0]], subject)
        self.assertEqual(len(peers), 1)
        self.assertTrue(meta["limitedData"])

    def test_single_configured_window_skips_earlier_windows(self):
        subject = {"playerId": "self", "rating": Decimal(1000)}
        rows = [{"playerId": f"peer-{index}", "rating": Decimal(1050 if index < 10 else 1150)} for index in range(12)]
        self.assertEqual(select_pumbility_peers(rows, subject)[1]["playerCount"], 10)
        peers, metadata = select_pumbility_peers(rows, subject, upward_windows=(200,))
        self.assertEqual(len(peers), 12)
        self.assertEqual(metadata["upwardWindow"], 200)

    def test_shared_policy_matches_pro_wrappers(self):
        from pro_pumbility import _cohort, percentile50, projected_target as pro_target
        from pro_pumbility_modes import percentile50 as mode_median
        subject = {"playerId": "self", "rank": 1, "rating": Decimal(1000)}
        peers = [{"playerId": f"peer-{i:02}", "rank": i + 2, "rating": Decimal(1000 + i)} for i in range(12)]
        self.assertEqual(_cohort(peers, subject), select_pumbility_peers(peers, subject))
        for scores in ([970000, 980000, 990000, 1000000], [995000, 995001], [1000000]):
            self.assertEqual(percentile50(scores), float(linear_median(scores)))
            self.assertEqual(mode_median(scores), linear_median(scores))
            self.assertEqual(pro_target(linear_median(scores)), projected_target(linear_median(scores)))


class RecommendationRankingTests(unittest.TestCase):
    def test_equal_weight_competition_ranks_can_promote_lower_gain(self):
        rows = [{"chartId": "high", "songName": "High", "projectedGain": 30, "scoreAtOrAboveTargetCount": 1},
                {"chartId": "middle", "songName": "Middle", "projectedGain": 20, "scoreAtOrAboveTargetCount": 10},
                {"chartId": "low", "songName": "Low", "projectedGain": 10, "scoreAtOrAboveTargetCount": 9}]
        ranked = rank_recommendations(rows)
        self.assertEqual([row["chartId"] for row in ranked], ["middle", "high", "low"])
        self.assertEqual([(row["gainRank"], row["scoreCountRank"], row["rankScore"]) for row in ranked], [(2, 1, 3), (1, 3, 4), (3, 2, 5)])
        self.assertEqual([row["recommendationRank"] for row in ranked], [1, 2, 3])
        self.assertNotIn("rankScore", rows[0])
        rescaled = [{**row, "projectedGain": row["projectedGain"] * 1000,
                     "scoreAtOrAboveTargetCount": row["scoreAtOrAboveTargetCount"] * 100} for row in rows]
        self.assertEqual([row["chartId"] for row in rank_recommendations(rescaled)], ["middle", "high", "low"])

    def test_tied_metrics_share_competition_rank_and_expected_value_breaks_final_ties(self):
        rows = [{"chartId": "a", "songName": "Same", "projectedGain": 20, "scoreAtOrAboveTargetCount": 5, "expectedPumbility": 300},
                {"chartId": "b", "songName": "Same", "projectedGain": 20, "scoreAtOrAboveTargetCount": 5, "expectedPumbility": 301},
                {"chartId": "c", "songName": "Same", "projectedGain": 10, "scoreAtOrAboveTargetCount": 1, "expectedPumbility": 400}]
        ranked = rank_recommendations(rows)
        self.assertEqual([row["chartId"] for row in ranked], ["b", "a", "c"])
        self.assertEqual([row["gainRank"] for row in ranked], [1, 1, 3])
        self.assertEqual([row["scoreCountRank"] for row in ranked], [1, 1, 3])
        self.assertEqual([row["rankScore"] for row in ranked], [2, 2, 6])

    def test_coop_expected_rating_then_title_and_id_break_ties(self):
        rows = [{"chartId": cid, "songName": name, "projectedGain": 0, "scoreAtOrAboveTargetCount": 2,
                 "expectedPumbility": None, "expectedCoopRating": expected}
                for cid, name, expected in (("z", "beta", 120), ("b", "Alpha", 120), ("a", "alpha", 120), ("c", "Last", 121))]
        self.assertEqual([row["chartId"] for row in rank_recommendations(rows)], ["c", "a", "b", "z"])


if __name__ == "__main__":
    unittest.main()
