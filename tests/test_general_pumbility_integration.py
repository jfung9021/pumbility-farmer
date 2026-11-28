from __future__ import annotations

import json
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock

from analysis_runtime import MemoryBlobStore
from general_pumbility import GeneralPumbilityModel, METHOD
from phoenix2_pumbility import phoenix2_pumbility
from piu_recommendations import (
    build_player_coop_mode,
    build_player_recommendation,
    build_recommendation_index,
    coop_master_goal_for_estimated_difficulty,
    public_player_key,
)
from pumbility_contract import recommendation_model_path, recommendation_player_path
from recommendation_artifacts import (
    compact_player_recommendation_cache,
    materialize_player_recommendation_cache,
)
from recommendation_refresh import (
    _MODEL_CACHE,
    _load_model,
    build_recommendation_model_artifacts,
    build_recommendation_score_mode_artifact,
    publish_recommendation_model_artifacts,
    refresh_player_recommendations,
)


class GeneralPumbilityIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        _MODEL_CACHE.clear()
        self.charts = [self.chart(f"base-{i:02d}", 10) for i in range(50)]
        self.charts += [self.chart("low-target", 16), self.chart("high-target", 28)]
        players = [{"playerId": "subject", "username": "Subject"}]
        scores = [self.score("subject", f"base-{i:02d}", 100) for i in range(50)]
        for number in range(10):
            player = f"peer-{number:02d}"
            players.append({"playerId": player, "username": player})
            scores += [self.score(player, f"base-{i:02d}", 101) for i in range(50)]
            scores += [self.score(player, cid, 0, 995000 + number % 2)
                       for cid in ("low-target", "high-target")]
        self.snapshot = {"mix": "Phoenix2", "charts": self.charts, "players": players,
                         "scores": scores, "generatedAtUtc": "2026-11-27T00:00:00Z"}
        self.phoenix1 = {"charts": self.charts, "scores": [], "players": []}
        self.index_path = "analysis/recommendations/latest.json"

    @staticmethod
    def chart(cid: str, level: int) -> dict:
        return {"id": cid, "songName": cid, "type": "Single", "level": level,
                "difficulty": f"S{level}", "noteCount": 1000}

    @staticmethod
    def score(player: str, cid: str, pumbility: float, raw: int = 950000) -> dict:
        return {"playerId": player, "chartId": cid, "pumbility": pumbility,
                "score": raw, "plate": "MG", "isBroken": False,
                "recordedAt": "2026-11-27T00:00:00Z"}

    def artifacts(self) -> tuple:
        return build_recommendation_model_artifacts(
            self.phoenix1, self.snapshot, combined_charts=[], phoenix2_slopes={},
            generation_key=self.id(), generated_at_utc="2026-11-28T00:00:00Z",
        )

    def publish(self) -> tuple:
        index, model, binary, p1, p2 = artifacts = self.artifacts()
        store = MemoryBlobStore()
        publish_recommendation_model_artifacts(
            store, index=index, model=model, score_model_bytes=binary,
            phoenix1_shards=p1, phoenix2_shards=p2, index_path=self.index_path,
        )
        return store, artifacts

    def test_bulk_and_persisted_refresh_agree_without_skill_fields(self) -> None:
        full = build_recommendation_index(self.phoenix1, self.snapshot,
                                         combined_charts=[], phoenix2_slopes={})
        expected = next(row for row in full["players"] if row["playerKey"] == public_player_key("subject"))
        store, (index, model, _, _, _) = self.publish()
        client = Mock()
        client.fetch_page_collection.return_value = []
        actual = refresh_player_recommendations(
            store, client, index_path=self.index_path, player_key=expected["playerKey"],
            now=lambda: datetime(2026, 11, 28, tzinfo=timezone.utc),
        )
        self.assertEqual(full["schemaVersion"], 29)
        self.assertEqual(model["artifactSchemaVersion"], 8)
        self.assertEqual(index["method"]["id"], METHOD)
        for mode in ("singles", "doubles", "overall"):
            result = actual["player"]["modes"][mode]
            for key in ("modePumbility", "validScoreCount", "eligible", "topScores", "filterCandidates", "topRecommendations"):
                self.assertEqual(result[key], expected["modes"][mode][key], (mode, key))
            for retired in ("scoringRating", "projectionRating", "clearingRating", "clearingSkill"):
                self.assertNotIn(retired, result)
        self.assertEqual(actual["peerGenerationAtUtc"], "2026-11-28T00:00:00Z")
        self.assertEqual(actual["player"]["modes"]["singles"]["peerGenerationAtUtc"], "2026-11-27T00:00:00Z")
        public = json.dumps(actual)
        self.assertNotIn("peer-00", public)
        self.assertNotIn('"playerId"', public)

    def test_unrated_low_and_high_targets_survive_compact_overall_projection(self) -> None:
        peer_model = GeneralPumbilityModel.from_snapshot(self.snapshot)
        player = peer_model.build_player("subject", include_candidates=True)
        player["playerKey"] = public_player_key("subject")
        payload = {"schemaVersion": 29, "player": player}
        cached = compact_player_recommendation_cache(payload)
        materialized = materialize_player_recommendation_cache(cached)
        self.assertEqual(materialized, payload)
        for mode in ("singles", "overall"):
            rows = materialized["player"]["modes"][mode]["topRecommendations"]
            self.assertEqual({row["chartId"] for row in rows}, {"low-target", "high-target"})
            self.assertTrue(all(row["estimatedDifficulty"] is None for row in rows))
        scoped = materialize_player_recommendation_cache(cached, mode="overall", difficulty="S16")
        self.assertEqual([row["chartId"] for row in scoped["player"]["modes"]["overall"]["filterCandidates"]], ["low-target"])
        overall = materialized["player"]["modes"]["overall"]["topRecommendations"][0]
        self.assertIn("modeProjectedGain", overall)
        self.assertEqual(overall["projectedPlateCode"], "MG")
        self.assertEqual(overall["plateProjectionSource"], "piuscores-peer-median")

    def test_split_worker_models_merge_to_same_population(self) -> None:
        parts = []
        for kind in ("Single", "Double"):
            binary, metadata = build_recommendation_score_mode_artifact(
                self.phoenix1, self.snapshot, combined_charts=[], chart_type=kind,
            )
            self.assertEqual(metadata["skillMethod"], METHOD)
            parts.append(GeneralPumbilityModel.from_npz_bytes(binary))
        merged = GeneralPumbilityModel.merge_models(parts)
        original = GeneralPumbilityModel.from_snapshot(self.snapshot)
        self.assertEqual(merged.metadata, original.metadata)
        self.assertEqual(merged.build_player("subject", include_candidates=True),
                         original.build_player("subject", include_candidates=True))

    def test_overall_recomputed_ranks_survive_compact_references(self) -> None:
        snapshot = deepcopy(self.snapshot)
        low_expected = phoenix2_pumbility("Single", 16, "SSS+", "MG")
        high_expected = phoenix2_pumbility("Single", 28, "SSS+", "MG")
        snapshot["scores"] = [row for row in snapshot["scores"]
                              if not (row["playerId"] == "subject" and row["chartId"] == "base-49")]
        snapshot["scores"].append(self.score("subject", "high-target", high_expected - 50))
        for number in range(50):
            cid = f"double-base-{number:02d}"
            snapshot["charts"].append({**self.chart(cid, 10), "type": "Double", "difficulty": "D10"})
            snapshot["scores"].append(self.score("subject", cid, low_expected - 25))
        player = GeneralPumbilityModel.from_snapshot(snapshot).build_player("subject", include_candidates=True)
        player["playerKey"] = public_player_key("subject")
        payload = {"schemaVersion": 29, "player": player}
        single = {row["chartId"]: row for row in player["modes"]["singles"]["filterCandidates"]}
        overall = {row["chartId"]: row for row in player["modes"]["overall"]["filterCandidates"]}
        self.assertEqual(single["low-target"]["recommendationRank"], 1)
        self.assertEqual(overall["low-target"]["recommendationRank"], 2)
        self.assertEqual(single["high-target"]["recommendationRank"], 2)
        self.assertEqual(overall["high-target"]["recommendationRank"], 1)
        cached = compact_player_recommendation_cache(payload)
        self.assertEqual(materialize_player_recommendation_cache(cached), payload)
        scoped = materialize_player_recommendation_cache(cached, mode="overall", difficulty="S16")
        self.assertEqual(scoped["player"]["modes"]["overall"]["filterCandidates"][0]["recommendationRank"], 2)

    def test_coop_ranks_use_frozen_piuscores_support_without_changing_goals(self) -> None:
        charts, annotations, scores = [], [], []
        for cid, estimate, count in (("a", 10, 1), ("b", 12, 2), ("c", 17, 4), ("d", 24, 3)):
            chart = {"id": cid, "type": "CoOp", "level": 2, "difficulty": "2x", "songName": cid}
            charts.append(chart)
            annotations.append({**chart, "chartId": cid, "estimatedDifficulty": estimate})
            target, _, _ = coop_master_goal_for_estimated_difficulty(estimate)
            for number in range(5):
                scores.append(self.score(f"peer-{number}", cid, 0, target if number < count else target - 1))
            scores.append(self.score("peer-0", cid, 0, target - 1))
        snapshot = {"charts": charts, "scores": scores, "players": [{"playerId": "subject", "username": "Subject"}]}
        peer_model = GeneralPumbilityModel.from_snapshot(snapshot)
        peer_model = GeneralPumbilityModel.from_npz_bytes(peer_model.to_npz_bytes())
        mode = build_player_coop_mode("subject", {"charts": charts, "scores": []}, annotations,
                                      pumbility_model=peer_model)
        self.assertEqual([row["chartId"] for row in mode["topRecommendations"]], ["c", "a", "b", "d"])
        by_id = {row["chartId"]: row for row in mode["filterCandidates"]}
        for cid, estimate, count, gain_rank, count_rank in (
            ("a", 10, 1, 1, 4), ("b", 12, 2, 2, 3), ("c", 17, 4, 3, 1), ("d", 24, 3, 4, 2),
        ):
            row = by_id[cid]
            self.assertEqual(row["projectedScore"], coop_master_goal_for_estimated_difficulty(estimate)[0])
            self.assertEqual(row["scoreAtOrAboveTargetCount"], count)
            self.assertEqual(row["gainRank"], gain_rank)
            self.assertEqual(row["scoreCountRank"], count_rank)
            self.assertEqual(row["rankScore"], gain_rank + count_rank)
            self.assertEqual(peer_model.count_scores_at_least("peer-0", cid, row["projectedScore"]), count - 1)
        wrapped = build_player_recommendation("subject", snapshot, annotations, {}, peer_model,
                                              phoenix1_goals={})["modes"]["coop"]
        self.assertEqual(wrapped, mode)
        index, model, binary, p1, p2 = build_recommendation_model_artifacts(
            {"charts": charts, "scores": []}, snapshot, combined_charts=annotations,
            phoenix2_slopes={}, generation_key=self.id(), generated_at_utc="2026-11-28T00:00:00Z",
        )
        store = MemoryBlobStore()
        publish_recommendation_model_artifacts(
            store, index=index, model=model, score_model_bytes=binary,
            phoenix1_shards=p1, phoenix2_shards=p2, index_path=self.index_path,
        )
        client = Mock()
        client.fetch_page_collection.return_value = []
        refreshed = refresh_player_recommendations(
            store, client, index_path=self.index_path, player_key=public_player_key("subject"),
            now=lambda: datetime(2026, 11, 28, tzinfo=timezone.utc),
        )
        for field in ("filterCandidates", "topRecommendations"):
            projection_fields = (
                "chartId", "projectedScore", "projectedGrade", "projectedPlateCode",
                "expectedCoopRating", "projectedGain", "scoreAtOrAboveTargetCount",
                "gainRank", "scoreCountRank", "rankScore", "recommendationRank",
            )
            self.assertEqual(
                [{key: row[key] for key in projection_fields} for row in refreshed["player"]["modes"]["coop"][field]],
                [{key: row[key] for key in projection_fields} for row in mode[field]],
            )

    def test_coop_ranks_entire_candidate_pool_before_fifty_row_cap(self) -> None:
        charts, annotations, scores = [], [], []
        for number in range(55):
            cid = f"coop-{number:02d}"
            chart = {"id": cid, "type": "CoOp", "level": 2, "difficulty": "2x", "songName": cid}
            charts.append(chart)
            annotations.append({**chart, "chartId": cid, "estimatedDifficulty": 17})
            scores.extend(self.score(f"peer-{peer}", cid, 0, 950000) for peer in range(number))
        mode = build_player_coop_mode("subject", {"charts": charts, "scores": scores}, annotations)
        self.assertEqual(len(mode["filterCandidates"]), 55)
        self.assertEqual(len(mode["topRecommendations"]), 50)
        self.assertEqual(mode["topRecommendations"], mode["filterCandidates"][:50])
        self.assertEqual([row["recommendationRank"] for row in mode["filterCandidates"]], list(range(1, 56)))
        self.assertEqual(mode["topRecommendations"][0]["chartId"], "coop-54")
        self.assertEqual(mode["filterCandidates"][-1]["chartId"], "coop-00")

    def test_missing_actual_peer_plates_leave_known_history_without_targets(self) -> None:
        snapshot = deepcopy(self.snapshot)
        for row in snapshot["scores"]:
            if row["playerId"].startswith("peer-"):
                row["plate"] = None
        peers = GeneralPumbilityModel.from_snapshot(snapshot)
        result = peers.build_player("subject", include_candidates=True)
        mode = result["modes"]["singles"]
        self.assertEqual(peers.player_counts("peer-00")["singles"], 52)
        self.assertEqual(mode["modePumbility"], 5000)
        self.assertTrue(mode["eligible"])
        self.assertEqual(mode["cohort"]["playerCount"], 10)
        self.assertEqual(mode["topRecommendations"], [])
        self.assertEqual(result["modes"]["overall"]["topRecommendations"], [])

    def test_actual_ordinal_peer_plate_changes_gain_without_score_threshold_inference(self) -> None:
        outputs = {}
        for lower_plate in ("FG", "MG"):
            snapshot = deepcopy(self.snapshot)
            for row in snapshot["scores"]:
                if row["playerId"].startswith("peer-") and row["chartId"] == "low-target":
                    row["plate"] = lower_plate if int(row["playerId"][-2:]) < 5 else "SG"
            mode = GeneralPumbilityModel.from_snapshot(snapshot).build_player("subject")["modes"]["singles"]
            outputs[lower_plate] = next(row for row in mode["topRecommendations"] if row["chartId"] == "low-target")
        for code, row in outputs.items():
            self.assertEqual(row["rawPercentileScore"], 995000.5)
            self.assertEqual(row["projectedPlateCode"], code)
            self.assertEqual(row["expectedPumbility"], phoenix2_pumbility("Single", 16, "SSS+", code))
            self.assertEqual(row["projectedGain"], round(row["expectedPumbility"] - 100, 2))
            self.assertEqual(row["peerPairedScoreCount"], 10)
            self.assertNotIn("assumedPlate", row)
        self.assertNotEqual(outputs["FG"]["projectedGain"], outputs["MG"]["projectedGain"])

    def test_score_and_plate_medians_share_only_complete_peer_observations(self) -> None:
        snapshot = deepcopy(self.snapshot)
        for row in snapshot["scores"]:
            if row["playerId"].startswith("peer-") and row["chartId"] == "low-target":
                number = int(row["playerId"][-2:])
                row["score"] = 980000 + number * 10000 if number < 2 else 1000000
                row["plate"] = "MG" if number < 2 else None
        mode = GeneralPumbilityModel.from_snapshot(snapshot).build_player("subject")["modes"]["singles"]
        target = next(row for row in mode["topRecommendations"] if row["chartId"] == "low-target")
        self.assertEqual(target["rawPercentileScore"], 985000)
        self.assertEqual(target["projectedGrade"], "SS+")
        self.assertEqual(target["projectedPlateCode"], "MG")
        self.assertEqual(target["peerRawScoreCount"], 10)
        self.assertEqual(target["peerPairedScoreCount"], 2)
        self.assertEqual(target["peerMissingPlateCount"], 8)
        self.assertTrue(target["limitedData"])

    def test_latest_subject_replaces_only_subject_while_peers_stay_immutable(self) -> None:
        store, (index, _, _, _, _) = self.publish()
        key = public_player_key("subject")
        _, peers = _load_model(store, index["generationKey"])
        before = peers.to_npz_bytes()
        client = Mock()
        client.fetch_page_collection.return_value = [self.score("subject", "high-target", 3000, 1000000)]
        response = refresh_player_recommendations(
            store, client, index_path=self.index_path, player_key=key,
            now=lambda: datetime(2026, 11, 28, tzinfo=timezone.utc),
        )
        mode = response["player"]["modes"]["singles"]
        self.assertEqual(mode["validScoreCount"], 51)
        self.assertEqual(mode["modePumbility"], 7900)
        self.assertEqual(peers.to_npz_bytes(), before)
        self.assertEqual(peers.player_counts("subject")["singles"], 50)
        cached = store.get_json(recommendation_player_path(key))
        self.assertEqual(materialize_player_recommendation_cache(cached)["player"], response["player"])

    def test_stale_model_schema_requires_rebuild(self) -> None:
        store, (index, model, _, _, _) = self.publish()
        old = deepcopy(model)
        old["artifactSchemaVersion"] = 6
        store.put_json(recommendation_model_path(index["generationKey"]), old)
        with self.assertRaisesRegex(RuntimeError, "rebuilt"):
            _load_model(store, index["generationKey"])

    def test_cached_numeric_reuse_restores_current_peer_provenance_from_json(self) -> None:
        store = MemoryBlobStore()
        first = build_recommendation_model_artifacts(
            self.phoenix1, self.snapshot, combined_charts=[], phoenix2_slopes={},
            generation_key=self.id() + "-first", generated_at_utc="2026-11-28T00:00:00Z",
            component_store=store,
        )
        checked = deepcopy(self.snapshot)
        checked["generatedAtUtc"] = "2026-11-28T00:00:00Z"
        second = build_recommendation_model_artifacts(
            self.phoenix1, checked, combined_charts=[], phoenix2_slopes={},
            generation_key=self.id() + "-second", generated_at_utc="2026-11-29T00:00:00Z",
            component_store=store,
        )
        self.assertEqual(first[2], second[2])
        self.assertEqual(first[0]["numericalModelIdentity"], second[0]["numericalModelIdentity"])
        self.assertEqual(first[0]["modelComputedAtUtc"], second[0]["modelComputedAtUtc"])
        self.assertNotEqual(first[0]["sourceCheckedAtUtc"], second[0]["sourceCheckedAtUtc"])
        self.assertIsNone(GeneralPumbilityModel.from_npz_bytes(second[2]).source_timestamp)
        index, model, binary, p1, p2 = second
        publish_recommendation_model_artifacts(
            store, index=index, model=model, score_model_bytes=binary,
            phoenix1_shards=p1, phoenix2_shards=p2, index_path=self.index_path,
        )
        loaded_json, loaded_peers = _load_model(store, index["generationKey"])
        self.assertEqual(loaded_json["peerSourceAtUtc"], checked["generatedAtUtc"])
        self.assertEqual(loaded_peers.source_timestamp, checked["generatedAtUtc"])
        self.assertEqual(loaded_json["method"]["sourceTimestamp"], checked["generatedAtUtc"])
        self.assertEqual(loaded_peers.build_player("subject")["modes"]["singles"]["peerGenerationAtUtc"],
                         checked["generatedAtUtc"])


if __name__ == "__main__":
    unittest.main()
