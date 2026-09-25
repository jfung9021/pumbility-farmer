from __future__ import annotations

import json
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock

from analysis_runtime import MemoryBlobStore
from piu_recommendations import ScoreResponseModel, build_player_recommendation
from pumbility_contract import (
    recommendation_model_path,
    recommendation_phoenix1_shard_path,
    recommendation_player_path,
)
from recommendation_artifacts import materialize_player_recommendation_cache
from recommendation_refresh import (
    _MODEL_CACHE,
    _load_model,
    _phoenix1_player_input,
    build_recommendation_model_artifacts,
    publish_recommendation_model_artifacts,
    refresh_player_recommendations,
)


class ClearingSkillRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        _MODEL_CACHE.clear()
        charts = []
        for prefix, chart_type, count in (("s", "Single", 54), ("d", "Double", 49)):
            for number in range(count):
                level = (28 if number < 10 else 22 if number < 20 else 21 if number < 30 else 10)
                if prefix == "d":
                    level = 23
                charts.append({
                    "id": f"{prefix}{number:02d}",
                    "songName": f"Chart {prefix}{number}",
                    "type": chart_type,
                    "level": level,
                    "difficulty": f"{prefix.upper()}{level}",
                    "noteCount": 1000,
                })
        source_charts = deepcopy(charts)
        # A source/current mode mismatch must not become a clearing record.
        next(row for row in source_charts if row["id"] == "s53")["type"] = "Double"
        players = [{"playerId": "player", "username": "PLAYER"}]
        self.phoenix1 = {
            "charts": source_charts,
            "players": players,
            "scores": [self._score(f"s{i:02d}") for i in range(40)]
            + [self._score(f"d{i:02d}") for i in range(49)]
            + [
                {**self._score("s50"), "pumbility": None},
                {**self._score("s51"), "pumbility": float("nan")},
                {**self._score("s52"), "isBroken": True},
                self._score("s53"),
                self._score("s00"),
            ],
        }
        self.phoenix2 = {
            "charts": charts,
            "players": players,
            "scores": [self._score(f"s{i:02d}") for i in range(40, 50)]
            + [self._score("s01"), {**self._score("s00"), "isBroken": True}],
        }
        self.generation = self.id()
        self.index_path = "analysis/recommendations/latest.json"

    @staticmethod
    def _score(chart_id: str) -> dict:
        return {
            "playerId": "player",
            "chartId": chart_id,
            "pumbility": 0,
            "score": 985000,
            "plate": "Fair Game",
            "recordedAt": "2026-09-25T00:00:00Z",
            "isBroken": False,
        }

    def _artifacts(self, phoenix1: dict | None = None, *, consume: bool = False) -> tuple:
        return build_recommendation_model_artifacts(
            self.phoenix1 if phoenix1 is None else phoenix1,
            self.phoenix2,
            combined_charts=[],
            phoenix2_slopes={},
            generation_key=self.generation,
            generated_at_utc="2026-09-26T00:00:00Z",
            consume_phoenix1_snapshot=consume,
        )

    def _published(self) -> tuple:
        artifacts = self._artifacts()
        index, model, score_bytes, p1_shards, p2_shards = artifacts
        store = MemoryBlobStore()
        publish_recommendation_model_artifacts(
            store,
            index=index,
            model=model,
            score_model_bytes=score_bytes,
            phoenix1_shards=p1_shards,
            phoenix2_shards=p2_shards,
            index_path=self.index_path,
        )
        return store, artifacts

    def test_compaction_keeps_valid_clear_membership_with_consuming_parity(self) -> None:
        expected = self._artifacts()
        consumed = deepcopy(self.phoenix1)
        actual = self._artifacts(consumed, consume=True)
        self.assertEqual(actual, expected)
        self.assertEqual(consumed, {})
        player = actual[3][0]["players"][0]
        self.assertEqual(player["scoreRows"], [])
        self.assertEqual(
            set(player["clearedChartIds"]),
            {f"s{i:02d}" for i in range(40)} | {f"d{i:02d}" for i in range(49)},
        )
        self.assertEqual(actual[1]["method"]["clearingSkill"]["ranks"], [1, 50])

    def test_refresh_matches_raw_calculation_and_mode_projection_preserves_skills(self) -> None:
        store, artifacts = self._published()
        index, _, score_bytes, _, _ = artifacts
        client = Mock()
        client.fetch_page_collection.return_value = []
        response = refresh_player_recommendations(
            store,
            client,
            index_path=self.index_path,
            player_key=index["players"][0]["playerKey"],
            now=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
        )
        direct = build_player_recommendation(
            "player",
            self.phoenix2,
            [],
            {},
            ScoreResponseModel.from_npz_bytes(score_bytes),
            phoenix1_snapshot=self.phoenix1,
            include_candidates=True,
        )
        for mode in ("singles", "doubles"):
            for field in ("clearingRating", "clearingSkill"):
                self.assertEqual(response["player"]["modes"][mode][field], direct["modes"][mode][field])
        singles = response["player"]["modes"]["singles"]
        doubles = response["player"]["modes"]["doubles"]
        self.assertEqual(singles["clearingRating"], 18.2)
        self.assertEqual(singles["clearingSkill"]["uniqueClearCount"], 50)
        self.assertEqual(singles["clearingSkill"]["selectedCount"], 50)
        self.assertFalse(singles["eligible"])
        self.assertIsNone(doubles["clearingRating"])
        self.assertEqual(doubles["clearingSkill"]["uniqueClearCount"], 49)
        cached = store.get_json(recommendation_player_path(index["players"][0]["playerKey"]))
        projected = materialize_player_recommendation_cache(cached, mode="singles")
        self.assertEqual(projected["player"]["modes"]["singles"], singles)
        self.assertNotIn("clearedChartIds", json.dumps(projected))

    def test_missing_membership_is_rejected_before_upstream_refresh(self) -> None:
        store, artifacts = self._published()
        index, _, _, p1_shards, _ = artifacts
        shard = deepcopy(p1_shards[0])
        del shard["players"][0]["clearedChartIds"]
        store.put_json(recommendation_phoenix1_shard_path(self.generation, 0), shard)
        client = Mock()
        with self.assertRaisesRegex(RuntimeError, "rebuild it from stored snapshots"):
            refresh_player_recommendations(
                store,
                client,
                index_path=self.index_path,
                player_key=index["players"][0]["playerKey"],
            )
        client.fetch_page_collection.assert_not_called()

    def test_legacy_compact_shard_is_not_treated_as_zero_clears(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "rebuilt from stored snapshots"):
            _phoenix1_player_input({"schemaVersion": 2, "players": [{"playerId": "player", "scores": []}]}, "player")

    def test_old_model_cannot_be_loaded_as_current_skill_method(self) -> None:
        store, artifacts = self._published()
        model = deepcopy(artifacts[1])
        model["artifactSchemaVersion"] -= 1
        store.put_json(recommendation_model_path(self.generation), model)
        with self.assertRaisesRegex(RuntimeError, "rebuilt from stored snapshots"):
            _load_model(store, self.generation)


if __name__ == "__main__":
    unittest.main()
