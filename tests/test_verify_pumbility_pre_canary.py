from __future__ import annotations

import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pumbility_store import (
    EXPECTED_PUMBILITY_MIGRATION,
    PumbilityArtifactIntegrityError,
    PumbilityArtifactStore,
    _canonical_json_bytes,
)
from scripts.verify_pumbility_pre_canary import (
    PreCanaryGateError,
    REGRESSION_TESTS,
    REQUIRED_RECONCILIATION_STAGES,
    assert_pre_canary_environment,
    run_regression_checks,
    verify_reconciliation_output,
)


def _connection_with_rows(*rows: object) -> Mock:
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.fetchone.side_effect = rows
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.cursor.return_value = cursor
    pipeline = Mock()
    pipeline.__enter__ = Mock(return_value=pipeline)
    pipeline.__exit__ = Mock(return_value=False)
    connection.pipeline.return_value = pipeline
    return connection


def _passing_reconciliation_output(*, backend: str = "shadow") -> str:
    events: list[dict[str, object]] = [
        {"status": "stage-completed", "stage": stage}
        for stage in REQUIRED_RECONCILIATION_STAGES
    ]
    events.append(
        {
            "status": "passed",
            "privacyScan": "passed",
            "unexplainedMismatchCount": 0,
            "productionBackend": backend,
            "mixes": {
                "phoenix1": {"exactMatches": 3, "unexplainedMismatchCount": 0},
                "phoenix2": {"exactMatches": 4, "unexplainedMismatchCount": 0},
            },
            "artifacts": {
                "jsonArtifacts": 2,
                "binaryArtifacts": 1,
                "cachedPlayerArtifacts": 3,
            },
        }
    )
    return "\n".join(json.dumps(event) for event in events)


class ArtifactIntegritySafetyTests(unittest.TestCase):
    def test_json_read_rejects_digest_or_byte_size_mismatch(self) -> None:
        payload = {"schemaVersion": 1, "value": 2}
        body = _canonical_json_bytes(payload)
        valid_digest = hashlib.sha256(body).hexdigest()
        cases = (
            ("0" * 64, len(body), False, True),
            (valid_digest, len(body) + 1, True, False),
        )
        for digest, size, digest_matches, byte_size_matches in cases:
            with self.subTest(
                digest_matches=digest_matches,
                byte_size_matches=byte_size_matches,
            ):
                connection = _connection_with_rows(
                    (EXPECTED_PUMBILITY_MIGRATION, True, payload, digest, size),
                )
                with (
                    patch("pumbility_store._read_connect", return_value=connection),
                    self.assertRaises(PumbilityArtifactIntegrityError) as captured,
                ):
                    PumbilityArtifactStore(
                        database_url="postgresql://localhost/local"
                    ).get_json("analysis/public.json")
                self.assertEqual(captured.exception.digest_matches, digest_matches)
                self.assertEqual(
                    captured.exception.byte_size_matches, byte_size_matches
                )
                self.assertNotIn("analysis/public.json", str(captured.exception))
                self.assertNotIn(digest, str(captured.exception))

    def test_json_read_rejects_wrong_schema_migration(self) -> None:
        connection = _connection_with_rows(
            ("unexpected-migration", False, None, None, None)
        )
        with (
            patch("pumbility_store._read_connect", return_value=connection),
            self.assertRaisesRegex(RuntimeError, "application-required migration"),
        ):
            PumbilityArtifactStore(
                database_url="postgresql://localhost/local"
            ).get_json("analysis/public.json")


class PreCanaryGateTests(unittest.TestCase):
    @staticmethod
    def _safe_environment() -> dict[str, str]:
        return {
            "PUMBILITY_DATA_BACKEND": "shadow",
            "PUMBILITY_SHADOW_STRICT": "false",
            "PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED": "true",
            "PUMBILITY_BLOB_MIRROR_ENABLED": "false",
            "PUMBILITY_BLOB_READ_FALLBACK_ENABLED": "false",
            "PUMBILITY_SUPABASE_READ_CANARY": "",
            "PLAYER_RECOMMENDATION_REFRESH_ENABLED": "false",
        }

    def test_accepts_only_the_documented_pre_canary_flag_state(self) -> None:
        result = assert_pre_canary_environment(self._safe_environment())
        self.assertTrue(result["vercelAuthoritative"])
        self.assertFalse(result["readCanary"])

        rollback = {
            **self._safe_environment(),
            "PUMBILITY_DATA_BACKEND": "vercel",
            "PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED": "false",
        }
        rollback_result = assert_pre_canary_environment(rollback)
        self.assertEqual(rollback_result["productionBackend"], "vercel")
        self.assertFalse(rollback_result["canonicalShadowWrites"])

        unsafe_updates = (
            {"PUMBILITY_DATA_BACKEND": "supabase"},
            {"PUMBILITY_SHADOW_STRICT": "true"},
            {"PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED": "false"},
            {"PUMBILITY_BLOB_MIRROR_ENABLED": "true"},
            {"PUMBILITY_BLOB_READ_FALLBACK_ENABLED": "true"},
            {"PUMBILITY_SUPABASE_READ_CANARY": "analysis"},
            {"PLAYER_RECOMMENDATION_REFRESH_ENABLED": "true"},
        )
        for update in unsafe_updates:
            with self.subTest(update=update), self.assertRaises(RuntimeError):
                assert_pre_canary_environment({**self._safe_environment(), **update})

    def test_requires_complete_exact_private_safe_reconciliation_evidence(self) -> None:
        result = verify_reconciliation_output(_passing_reconciliation_output())
        self.assertTrue(result["exactRelationalParity"])
        self.assertTrue(result["exactArtifactParity"])
        self.assertEqual(result["privacyScan"], "passed")
        self.assertEqual(
            verify_reconciliation_output(
                _passing_reconciliation_output(backend="vercel")
            )["productionBackend"],
            "vercel",
        )

        events = _passing_reconciliation_output().splitlines()
        with self.assertRaisesRegex(PreCanaryGateError, "every required stage"):
            verify_reconciliation_output("\n".join(events[1:]))

        summary = json.loads(events[-1])
        summary["privacyScan"] = "failed"
        with self.assertRaisesRegex(PreCanaryGateError, "privacy"):
            verify_reconciliation_output(
                "\n".join([*events[:-1], json.dumps(summary)])
            )

    def test_regression_runner_uses_only_the_fixed_focused_suite(self) -> None:
        command_runner = Mock(return_value=SimpleNamespace(returncode=0))
        self.assertEqual(
            run_regression_checks(command_runner=command_runner), len(REGRESSION_TESTS)
        )
        command = command_runner.call_args.args[0]
        self.assertEqual(command[:4], [command[0], "-m", "unittest", "-v"])
        self.assertEqual(tuple(command[4:]), REGRESSION_TESTS)

        command_runner.return_value = SimpleNamespace(returncode=1)
        with self.assertRaisesRegex(PreCanaryGateError, "regression"):
            run_regression_checks(command_runner=command_runner)


if __name__ == "__main__":
    unittest.main()
