from __future__ import annotations

import json
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api_service import app
from pumbility_contract import (
    combined_tier_blob_path, recommendation_blob_path,
    recommendation_phoenix2_shard_path, recommendation_player_state_path,
)
from tier_scores import build_local_tier_score_artifact, read_tier_player_scores


PLAYER_KEY = "a" * 20


def score(chart: str, value: int = 996_999, *, pumbility: float = 300, plate: str = "MG") -> dict:
    return {
        "playerId": "private-player", "chartId": chart, "score": value,
        "pumbility": pumbility, "plate": plate, "recordedAt": "2026-10-01T00:00:00Z",
        "isBroken": False,
    }


class Store:
    def __init__(self):
        self.values = {
            recommendation_blob_path(): {
                "generationKey": "daily",
                "players": [{"playerKey": PLAYER_KEY, "internalPlayerId": "private-player", "inputShard": 0,
                             "eligibility": {"singles": False}}],
            },
            combined_tier_blob_path(): {
                "singles": [{"chartId": f"s{index}"} for index in range(60)] + [{"chartId": "new"}],
                "doubles": [{"chartId": "d1"}], "coop": [{"chartId": "c1"}],
            },
            recommendation_phoenix2_shard_path("daily", 0): {"players": [{
                "playerId": "private-player", "lastSyncedAtUtc": "2026-10-01T00:00:00Z",
                "scores": [score(f"s{index}") for index in range(60)] + [score("d1"), score("c1")],
            }]},
        }

    def get_json(self, pathname):
        return self.values.get(pathname)


class TierScoreTests(unittest.TestCase):
    def test_all_visible_scores_are_projected_beyond_top50_without_eligibility_gate(self):
        payload = read_tier_player_scores(Store(), PLAYER_KEY, "singles")
        self.assertEqual(len(payload["scores"]), 60)
        self.assertEqual(set(payload), {"playerKey", "mode", "syncedAtUtc", "scores"})
        for row in payload["scores"]:
            self.assertEqual(set(row), {"chartId", "score", "plateCode"})
            self.assertEqual(row["plateCode"], "MG")
        self.assertNotIn("private-player", json.dumps(payload))

    def test_live_overlay_preserves_same_row_plate_and_existing_best_policy(self):
        store = Store()
        store.values[recommendation_player_state_path(PLAYER_KEY)] = {
            "playerId": "private-player", "lastSyncedAtUtc": "2026-10-03T00:00:00Z",
            "scores": [score("s0", 980_000, pumbility=301, plate="Perfect Game"),
                       score("s1", 999_999, pumbility=299, plate="RG"),
                       score("new", 923_999, plate="FG"), score("unknown-private")],
            "catalog": [{"chartId": "unknown-private", "songName": "Private catalog field"}],
        }
        payload = read_tier_player_scores(store, PLAYER_KEY, "singles")
        rows = {row["chartId"]: row for row in payload["scores"]}
        self.assertEqual(rows["s0"], {"chartId": "s0", "score": 980_000, "plateCode": "PG"})
        self.assertEqual(rows["s1"]["score"], 996_999)
        self.assertEqual(rows["new"]["score"], 923_999)
        self.assertNotIn("unknown-private", rows)
        self.assertEqual(payload["syncedAtUtc"], "2026-10-03T00:00:00Z")
        for mode, chart in (("doubles", "d1"), ("coop", "c1")):
            self.assertEqual([row["chartId"] for row in read_tier_player_scores(store, PLAYER_KEY, mode)["scores"]], [chart])

    def test_missing_invalid_broken_and_zero_scores_are_distinct(self):
        store = Store()
        base = store.values[recommendation_phoenix2_shard_path("daily", 0)]["players"][0]
        base["scores"] = [score("s0", 0, pumbility=0, plate="unrecognized"),
                          score("s1", -1), score("s2", 1_000_001),
                          score("s3", True), {**score("s4"), "isBroken": True},
                          score("s5", 999_999.5), score("s6", None)]
        self.assertEqual(read_tier_player_scores(store, PLAYER_KEY, "singles")["scores"],
                         [{"chartId": "s0", "score": 0, "plateCode": None}])

    def test_hosted_route_validates_queries_and_returns_no_store(self):
        with patch("api.tier_list.PrivateBlobStore", return_value=Store()):
            client = TestClient(app)
            for query, status in (("", 400), (f"?playerKey={PLAYER_KEY}&mode=overall", 400),
                                  ("?playerKey=missing&mode=singles", 404),
                                  (f"?playerKey={PLAYER_KEY}&mode=singles", 200)):
                response = client.get("/api/tier-list/scores" + query)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.headers["cache-control"], "no-store")
        with patch("api.tier_list.PrivateBlobStore", side_effect=RuntimeError("private storage detail")):
            response = TestClient(app).get(f"/api/tier-list/scores?playerKey={PLAYER_KEY}&mode=singles")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private storage detail", response.text)

    def test_local_artifact_matches_hosted_dto_and_omits_unnamed_players(self):
        snapshot = {
            "players": [{"playerId": "private-player", "username": "NAME", "lastSyncedAtUtc": "2026-10-01T00:00:00Z"},
                        {"playerId": "not-listed", "username": ""}],
            "scores": [score("s0"), score("d1", 1_000_000, plate="PG"), score("outside")],
        }
        store = Store()
        artifact = build_local_tier_score_artifact(snapshot, store.values[combined_tier_blob_path()], lambda _: PLAYER_KEY)
        self.assertEqual(artifact["schemaVersion"], 1)
        self.assertEqual(len(artifact["players"]), 1)
        player = artifact["players"][0]
        self.assertEqual(set(player), {"playerKey", "syncedAtUtc", "scores"})
        self.assertEqual(player["scores"], [{"chartId": "d1", "score": 1_000_000, "plateCode": "PG"},
                                            {"chartId": "s0", "score": 996_999, "plateCode": "MG"}])
        self.assertNotIn("private-player", json.dumps(artifact))

    def test_import_keeps_numeric_and_worker_dependencies_deferred(self):
        result = subprocess.run([sys.executable, "-c", "import tier_scores, sys; assert not any(name in sys.modules for name in ('numpy', 'pandas', 'celery', 'recommendation_refresh', 'piu_recommendations'))"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_score_only_build_does_not_rebuild_or_prune_recommendations(self):
        from scripts import build_local_recommendations as builder

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tiers = root / "tiers.json"
            tiers.write_text(json.dumps({"singles": [{"chartId": "s0"}]}), encoding="utf-8")
            index = root / "latest.json"
            index.write_text('{"keep":"recommendation generation"}', encoding="utf-8")
            with (
                patch.object(sys, "argv", ["builder", "--tier-scores-only"]),
                patch.object(builder, "OUTPUT_PATH", index),
                patch.object(builder, "COMBINED_OUTPUT_PATH", tiers),
                patch.object(builder, "_read_snapshot", return_value={
                    "players": [{"playerId": "private-player", "username": "NAME"}],
                    "scores": [score("s0")],
                }) as read,
                patch.object(builder, "build_combined_chart_results", side_effect=AssertionError("No numeric rebuild")),
                patch.object(builder, "_prune_unpublished_generations", side_effect=AssertionError("No prune")),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(builder.main(), 0)
            read.assert_called_once_with("phoenix2")
            self.assertEqual(json.loads(index.read_text()), {"keep": "recommendation generation"})
            artifact = json.loads((root / "tier-scores.json").read_text())
            self.assertEqual(artifact["players"][0]["scores"], [{"chartId": "s0", "score": 996_999, "plateCode": "MG"}])


if __name__ == "__main__":
    unittest.main()
