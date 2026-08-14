from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.backfill_pumbility_production import (
    EXPECTED_PROJECT_REF,
    _assert_database_target,
    _claim_lock,
    _copy_cached_players,
    _read_stable_boundary,
    validate_production_database_url,
)


class ProductionTargetTests(unittest.TestCase):
    def test_accepts_only_exact_session_pooler_target(self) -> None:
        validate_production_database_url(
            "postgresql://pumbility_runtime_login.gsiyqhkcgegjrvqcqioc:secret@"
            "aws-1-us-east-2.pooler.supabase.com:5432/postgres?sslmode=require",
            expected_project_ref=EXPECTED_PROJECT_REF,
        )

    def test_rejects_transaction_pooler_and_wrong_project(self) -> None:
        with self.assertRaisesRegex(ValueError, "port 5432"):
            validate_production_database_url(
                "postgresql://pumbility_runtime_login.gsiyqhkcgegjrvqcqioc:secret@"
                "aws-1-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require",
                expected_project_ref=EXPECTED_PROJECT_REF,
            )
        with self.assertRaisesRegex(ValueError, "dedicated Pumbility login"):
            validate_production_database_url(
                "postgresql://pumbility_runtime_login.wrong:secret@"
                "aws-1-us-east-2.pooler.supabase.com:5432/postgres?sslmode=require",
                expected_project_ref=EXPECTED_PROJECT_REF,
            )

    def test_rejects_missing_tls_and_password(self) -> None:
        with self.assertRaisesRegex(ValueError, "sslmode=require"):
            validate_production_database_url(
                "postgresql://pumbility_runtime_login.gsiyqhkcgegjrvqcqioc:secret@"
                "aws-1-us-east-2.pooler.supabase.com:5432/postgres",
                expected_project_ref=EXPECTED_PROJECT_REF,
            )
        with self.assertRaisesRegex(ValueError, "missing its password"):
            validate_production_database_url(
                "postgresql://pumbility_runtime_login.gsiyqhkcgegjrvqcqioc@"
                "aws-1-us-east-2.pooler.supabase.com:5432/postgres?sslmode=require",
                expected_project_ref=EXPECTED_PROJECT_REF,
            )

    def test_database_fingerprint_requires_narrow_login(self) -> None:
        cursor = Mock()
        cursor.fetchone.return_value = (
            "postgres",
            "pumbility_runtime_login",
            "20260813010000",
            True,
            False,
            False,
            True,
        )
        _assert_database_target(cursor)
        cursor.fetchone.return_value = (
            "postgres",
            "postgres",
            "20260813010000",
            True,
            True,
            True,
            True,
        )
        with self.assertRaisesRegex(RuntimeError, "fingerprint"):
            _assert_database_target(cursor)

    def test_operator_statement_timeout_finishes_before_serverless_ceiling(self) -> None:
        cursor = Mock()
        cursor.fetchone.return_value = (True,)
        _claim_lock(cursor)
        cursor.execute.assert_any_call("set statement_timeout = '12min'")


class ProductionBoundaryTests(unittest.TestCase):
    def test_retries_only_until_snapshot_and_pointers_are_stable(self) -> None:
        store = Mock()
        pointer_a = {
            "payload": {"schemaVersion": 1},
        }
        pointer_b = {
            "payload": {"schemaVersion": 2},
        }
        stable_pointers = {
            "phoenix2Analysis": {"schemaVersion": 1},
            "combinedTier": {"schemaVersion": 2},
            "recommendations": {"schemaVersion": 21},
        }
        store.get_json.side_effect = [
            pointer_a["payload"],
            pointer_a["payload"],
            pointer_a["payload"],
            {"mix": "Phoenix"},
            {"mix": "Phoenix2", "value": 1},
            {"mix": "Phoenix2", "value": 2},
            pointer_a["payload"],
            pointer_a["payload"],
            pointer_b["payload"],
            stable_pointers["phoenix2Analysis"],
            stable_pointers["combinedTier"],
            stable_pointers["recommendations"],
            {"mix": "Phoenix"},
            {"mix": "Phoenix2", "value": 3},
            {"mix": "Phoenix2", "value": 3},
            stable_pointers["phoenix2Analysis"],
            stable_pointers["combinedTier"],
            stable_pointers["recommendations"],
        ]
        pointers, phoenix1, phoenix2 = _read_stable_boundary(store, max_attempts=2)
        self.assertEqual(pointers, stable_pointers)
        self.assertEqual(phoenix1["mix"], "Phoenix")
        self.assertEqual(phoenix2["value"], 3)

    def test_cached_player_copy_deletes_target_only_revoked_objects(self) -> None:
        source = Mock()
        target = Mock()
        allowed = "analysis/recommendations/players/allowed.json"
        revoked = "analysis/recommendations/players/revoked.json"
        source.list.side_effect = lambda prefix: (
            [SimpleNamespace(pathname=allowed)]
            if prefix == "analysis/recommendations/players/"
            else []
        )
        source.get_json.return_value = {"player": {}}
        target.list.side_effect = lambda prefix: (
            [SimpleNamespace(pathname=allowed), SimpleNamespace(pathname=revoked)]
            if prefix == "analysis/recommendations/players/"
            else []
        )

        self.assertEqual(_copy_cached_players(source, target), 1)

        target.delete.assert_called_once_with([revoked])
        target.put_json.assert_called_once_with(allowed, {"player": {}})


if __name__ == "__main__":
    unittest.main()
