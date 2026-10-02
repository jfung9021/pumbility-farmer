from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from scripts.refresh_pumbility_supabase_player import LocalDatabaseScoreClient, main


class LocalDatabaseScoreClientTests(unittest.TestCase):
    @patch("scripts.refresh_pumbility_supabase_player._assert_schema")
    @patch("psycopg.connect")
    def test_catalog_refresh_reads_imported_metadata_without_upstream_access(
        self, connect: Mock, schema: Mock,
    ) -> None:
        chart = {
            "id": "new", "songName": "New song", "type": "Single", "level": 20,
            "bpmMin": 110, "bpmMax": 130,
        }
        cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [(chart,)]
        client = LocalDatabaseScoreClient("postgresql://localhost/local", "player")
        params = {"mix": "Phoenix2", "limit": 100}
        self.assertEqual(client.fetch_page_collection("api/v2/songs", params), [
            {"name": "New song", "bpm": {"min": 110, "max": 130}},
        ])
        self.assertEqual(client.fetch_page_collection("api/v2/charts", params), [chart])
        connect.assert_called_once_with("postgresql://localhost/local", prepare_threshold=None)
        schema.assert_called_once_with(cursor)

    def test_rejects_any_nonproduction_request_shape(self) -> None:
        client = LocalDatabaseScoreClient("postgresql://localhost/local", "player")
        with self.assertRaisesRegex(ValueError, "unexpected upstream shape"):
            client.fetch_page_collection(
                "api/v2/players/player/scores",
                {"mix": "Phoenix2", "limit": 100, "recordedAfter": "unexpected"},
            )


class LocalPlayerRefreshMainTests(unittest.TestCase):
    @patch("scripts.refresh_pumbility_supabase_player.refresh_player_recommendations")
    @patch("scripts.refresh_pumbility_supabase_player.PumbilityArtifactStore")
    @patch("scripts.refresh_pumbility_supabase_player.require_loopback_database_url")
    def test_uses_public_key_metadata_and_the_existing_refresh_pipeline(
        self, guard: Mock, store_type: Mock, refresh: Mock
    ) -> None:
        store = store_type.return_value
        store.get_json.return_value = {
            "players": [
                {
                    "playerKey": "public-key",
                    "internalPlayerId": "private-id",
                    "inputShard": 0,
                }
            ]
        }
        refresh.return_value = {"modelGeneration": "generation", "player": {"modes": {}}}
        with patch.dict(
            "os.environ", {"PUMBILITY_DATABASE_URL": "postgresql://localhost/local"}
        ):
            self.assertEqual(main(["--player-key", "public-key"]), 0)
        guard.assert_called_once_with("postgresql://localhost/local")
        self.assertEqual(refresh.call_args.kwargs["player_key"], "public-key")
        self.assertEqual(refresh.call_args.args[1].player_id, "private-id")


if __name__ == "__main__":
    unittest.main()
