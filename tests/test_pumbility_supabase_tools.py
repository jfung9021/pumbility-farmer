from __future__ import annotations

import unittest

from scripts.backfill_pumbility_supabase import _digest, _timestamp
from scripts.reconcile_pumbility_supabase import _key_hmac, _typed_score_payload, reconcile


class BackfillToolTests(unittest.TestCase):
    def test_logical_digest_is_key_order_independent(self) -> None:
        self.assertEqual(_digest({"b": 2, "a": 1}), _digest({"a": 1, "b": 2}))

    def test_timestamp_normalizes_valid_values_and_rejects_invalid(self) -> None:
        self.assertEqual(_timestamp("2026-08-13T00:00:00Z"), "2026-08-13T00:00:00+00:00")
        self.assertIsNone(_timestamp("not-a-time"))
        self.assertIsNone(_timestamp(None))

    def test_typed_score_columns_reconstruct_the_source_contract(self) -> None:
        self.assertEqual(
            _typed_score_payload(
                ("player", "chart", 12.34, 999999, "SSS+", "Perfect Game", "raw-time", False)
            ),
            {
                "playerId": "player",
                "chartId": "chart",
                "pumbility": 12.34,
                "score": 999999,
                "letterGrade": "SSS+",
                "plate": "Perfect Game",
                "recordedAt": "raw-time",
                "isBroken": False,
            },
        )


class ReconciliationTests(unittest.TestCase):
    KEY = b"a" * 32
    SNAPSHOT = {
        "players": [
            {
                "playerId": "private-player",
                "username": "private-name",
                "lastSyncedAtUtc": "2026-08-13T00:00:00Z",
                "lastScoreRecordedAtUtc": None,
            }
        ],
        "charts": [{"id": "chart-1", "songName": "Song", "type": "Single"}],
        "scores": [
            {
                "playerId": "private-player",
                "chartId": "chart-1",
                "pumbility": 12.34,
                "score": 999999,
                "letterGrade": "SSS+",
                "plate": "Perfect Game",
                "recordedAt": "2026-08-13T00:00:00Z",
                "isBroken": False,
            }
        ],
    }

    def test_exact_reconciliation_passes(self) -> None:
        result = reconcile(
            self.SNAPSHOT,
            self.SNAPSHOT,
            key=self.KEY,
            accepted_changes=set(),
        )
        self.assertEqual(result["result"], "passed")
        self.assertEqual(result["unexplainedMismatchCount"], 0)

    def test_unexplained_change_fails(self) -> None:
        changed = {**self.SNAPSHOT, "scores": []}
        result = reconcile(
            self.SNAPSHOT,
            changed,
            key=self.KEY,
            accepted_changes=set(),
        )
        self.assertEqual(result["result"], "failed")
        self.assertEqual(result["unexplainedMismatchCount"], 1)

    def test_ledger_marker_explains_only_the_named_change(self) -> None:
        natural_key = ("private-player", "chart-1")
        accepted = {("scores", _key_hmac("scores", natural_key, self.KEY))}
        result = reconcile(
            self.SNAPSHOT,
            {**self.SNAPSHOT, "scores": []},
            key=self.KEY,
            accepted_changes=accepted,
        )
        self.assertEqual(result["result"], "passed")
        self.assertEqual(result["explainedChanges"], 1)


if __name__ == "__main__":
    unittest.main()
