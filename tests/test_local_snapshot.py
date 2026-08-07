from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from piu_misgrade_analyzer import ApiError, load_snapshot
from scripts.capture_private_score_snapshot import (
    capture_private_snapshot,
    validate_snapshot_directory,
    validate_snapshot_rows,
)


NOW = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    request_count = 4

    def fetch_page_collection(self, path: str, params=None):
        if path == "api/v2/players":
            return [
                {"userId": "player-2", "username": "discard me"},
                {"userId": "player-1", "gameTag": "discard me"},
            ]
        if path == "api/v2/charts":
            return [
                {
                    "id": "chart-1",
                    "songName": "Fixture",
                    "type": "Single",
                    "level": 20,
                    "difficulty": "S20",
                    "imageUrl": None,
                    "noteCount": 1000,
                    "stepArtist": "Tester",
                    "scoringLevel": 99,
                }
            ]
        if path.endswith("player-1/scores"):
            return [
                {
                    "chartId": "chart-1",
                    "pumbility": 500,
                    "score": 990000,
                    "recordedAt": "2026-08-07T10:00:00Z",
                    "isBroken": False,
                    "username": "discard me",
                }
            ]
        if path.endswith("player-2/scores"):
            return [
                {
                    "chartId": "chart-1",
                    "pumbility": 510,
                    "score": 995000,
                    "recordedAt": "2026-08-07T11:00:00Z",
                    "isBroken": False,
                }
            ]
        raise AssertionError(path)


class EmptyClient:
    request_count = 1

    def fetch_page_collection(self, path: str, params=None):
        if path == "api/v2/players":
            return []
        raise AssertionError(path)


class LocalSnapshotTests(unittest.TestCase):
    def test_capture_rejects_archived_phoenix1_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".local-data" / "piu-scores" / "phoenix1"
            with self.assertRaisesRegex(ValueError, "archived"):
                capture_private_snapshot(
                    FakeClient(), root, mix="phoenix1", now=lambda: NOW
                )
            self.assertFalse(root.exists())

    def test_capture_writes_cache_compatible_sanitized_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".local-data" / "piu-scores"
            manifest = capture_private_snapshot(FakeClient(), root, now=lambda: NOW)
            players, charts, scores = load_snapshot(root / "current")

            self.assertEqual(manifest["players"], 2)
            self.assertEqual(manifest["scoreRows"], 2)
            self.assertEqual({row["userId"] for row in players}, {"player-1", "player-2"})
            self.assertIn("discard me", json.dumps(players))
            self.assertNotIn("gameTag", json.dumps(players))
            self.assertNotIn("scoringLevel", json.dumps(charts))
            self.assertNotIn("username", json.dumps(scores))
            self.assertFalse((root / "staging" / "snapshot.json").exists())
            self.assertEqual(
                set(manifest["checksums"]),
                {"players.json", "charts.json", "scores.jsonl.gz"},
            )
            self.assertEqual(validate_snapshot_directory(root / "current"), manifest)

    def test_validation_rejects_unneeded_profile_fields(self) -> None:
        players = [{"userId": "p", "gameTag": "private"}]
        charts = [{"id": "c"}]
        scores = [{"playerId": "p", "chartId": "c", "pumbility": 1, "isBroken": False}]
        with self.assertRaisesRegex(ValueError, "forbidden fields"):
            validate_snapshot_rows(players, charts, scores)

    def test_failed_capture_does_not_replace_current_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".local-data" / "piu-scores"
            current = root / "current"
            current.mkdir(parents=True)
            marker = current / "marker.txt"
            marker.write_text("keep", encoding="utf-8")

            with self.assertRaises(ApiError):
                capture_private_snapshot(EmptyClient(), root, now=lambda: NOW)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
