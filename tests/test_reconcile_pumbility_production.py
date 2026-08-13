from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.reconcile_pumbility_production import (
    _assert_reconciliation_state,
    _verify_artifacts,
    session_url_from_runtime,
)


class ProductionReconciliationTargetTests(unittest.TestCase):
    def test_reconciliation_accepts_fail_open_shadow_without_canonical_writes(self) -> None:
        self.assertEqual(
            _assert_reconciliation_state({"PUMBILITY_DATA_BACKEND": "shadow"}),
            "shadow",
        )
        for environment in (
            {"PUMBILITY_DATA_BACKEND": "supabase"},
            {
                "PUMBILITY_DATA_BACKEND": "shadow",
                "PUMBILITY_SHADOW_STRICT": "true",
            },
            {
                "PUMBILITY_DATA_BACKEND": "shadow",
                "PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED": "true",
            },
        ):
            with self.assertRaises(RuntimeError):
                _assert_reconciliation_state(environment)

    def test_converts_only_the_approved_transaction_pooler(self) -> None:
        runtime = (
            "postgresql://pumbility_runtime_login.gsiyqhkcgegjrvqcqioc:secret@"
            "aws-1-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        self.assertIn(":5432/postgres", session_url_from_runtime(runtime))

    def test_refuses_session_or_unrelated_runtime_targets(self) -> None:
        with self.assertRaisesRegex(ValueError, "transaction pooler"):
            session_url_from_runtime(
                "postgresql://pumbility_runtime_login.gsiyqhkcgegjrvqcqioc:secret@"
                "aws-1-us-east-2.pooler.supabase.com:5432/postgres?sslmode=require"
            )

    @patch("scripts.reconcile_pumbility_production._required_production_bytes", return_value=b"model")
    @patch("scripts.reconcile_pumbility_production._recommendation_paths", return_value=([], "model.npz"))
    def test_reconciliation_rejects_target_only_cached_player_objects(
        self, _paths: Mock, _bytes: Mock
    ) -> None:
        source = Mock()
        target = Mock()
        pointers = {
            "phoenix2Analysis": {"same": True},
            "combinedTier": {"same": True},
            "recommendations": {"same": True},
        }
        source.list.return_value = []
        target.list.side_effect = lambda prefix: (
            [SimpleNamespace(pathname=f"{prefix}revoked.json")]
            if prefix == "analysis/recommendations/players/"
            else []
        )
        target.get_json.return_value = {"same": True}
        target.get_bytes.return_value = b"model"

        with self.assertRaisesRegex(RuntimeError, "exact reconciliation"):
            _verify_artifacts(source, target, pointers)


if __name__ == "__main__":
    unittest.main()
