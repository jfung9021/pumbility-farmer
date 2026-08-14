from __future__ import annotations

import json
import os
import unittest
from unittest.mock import Mock, patch

from api.operator import (
    _repair_numeric_artifact,
    _run_shadow_restore,
    _validated_shadow_restore_environment,
    run_hosted_precanary_artifact_repair,
    run_hosted_precanary_reconciliation,
    run_hosted_shadow_backfill,
    run_hosted_shadow_population,
)


class HostedPreCanaryOperatorTests(unittest.TestCase):
    def test_shadow_restore_routes_require_preview_and_both_controls(self) -> None:
        environments = (
            {},
            {
                "VERCEL_ENV": "production",
                "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED": "true",
                "PUMBILITY_PRECANARY_SHADOW_RESTORE_ENABLED": "true",
            },
            {
                "VERCEL_ENV": "preview",
                "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED": "true",
                "PUMBILITY_PRECANARY_SHADOW_RESTORE_ENABLED": "false",
            },
            {
                "VERCEL_ENV": "preview",
                "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED": "false",
                "PUMBILITY_PRECANARY_SHADOW_RESTORE_ENABLED": "true",
            },
        )
        for environment in environments:
            with self.subTest(environment=environment), patch.dict(
                "os.environ", environment, clear=True
            ), patch("api.operator._run_shadow_restore") as restore:
                backfill = run_hosted_shadow_backfill()
                population = run_hosted_shadow_population()
            self.assertEqual(backfill.status_code, 404)
            self.assertEqual(population.status_code, 404)
            restore.assert_not_called()

    def test_shadow_restore_routes_return_only_aggregate_evidence(self) -> None:
        environment = {
            "VERCEL_ENV": "preview",
            "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED": "true",
            "PUMBILITY_PRECANARY_SHADOW_RESTORE_ENABLED": "true",
        }
        evidence = {
            "status": "completed",
            "gate": "hosted-shadow-restoration",
            "databaseTargetVerified": True,
            "productionBackendChanged": False,
        }
        with patch.dict("os.environ", environment, clear=True), patch(
            "api.operator._run_shadow_restore", return_value=evidence
        ) as restore:
            backfill = run_hosted_shadow_backfill()
            population = run_hosted_shadow_population()
        self.assertEqual(json.loads(backfill.body), evidence)
        self.assertEqual(json.loads(population.body), evidence)
        self.assertEqual(
            [call.kwargs["action"] for call in restore.call_args_list],
            ["backfill", "populate"],
        )

    def test_shadow_restore_environment_requires_exact_hosted_targets(self) -> None:
        environment = {
            "PUMBILITY_DATABASE_URL": "runtime-url",
            "PUMBILITY_SUPABASE_URL": "https://gsiyqhkcgegjrvqcqioc.supabase.co",
            "PUMBILITY_SUPABASE_SERVICE_ROLE_KEY": "s" * 32,
            "PUMBILITY_STORAGE_BUCKET": "pumbility-artifacts",
            "BLOB_READ_WRITE_TOKEN": "b" * 32,
        }
        flags = {
            "productionBackend": "vercel",
            "canonicalShadowWrites": False,
        }
        with patch(
            "scripts.verify_pumbility_pre_canary.assert_pre_canary_environment",
            return_value=flags,
        ) as safe_flags, patch(
            "scripts.reconcile_pumbility_production.session_url_from_runtime",
            return_value="session-url",
        ) as session:
            result = _validated_shadow_restore_environment(environment)
        self.assertEqual(result, (flags, "session-url"))
        safe_flags.assert_called_once_with(environment)
        session.assert_called_once_with("runtime-url")

        for key, value in (
            ("PUMBILITY_SUPABASE_URL", "https://wrong-project.supabase.co"),
            ("PUMBILITY_SUPABASE_SERVICE_ROLE_KEY", "short"),
            ("PUMBILITY_STORAGE_BUCKET", "wrong-bucket"),
            ("BLOB_READ_WRITE_TOKEN", "short"),
        ):
            invalid = {**environment, key: value}
            with self.subTest(key=key), patch(
                "scripts.verify_pumbility_pre_canary.assert_pre_canary_environment",
                return_value=flags,
            ), patch(
                "scripts.reconcile_pumbility_production.session_url_from_runtime",
                return_value="session-url",
            ), self.assertRaises(RuntimeError):
                _validated_shadow_restore_environment(invalid)

    def test_shadow_backfill_runs_plan_then_apply_and_restores_confirmations(self) -> None:
        environment = {"PUMBILITY_DATABASE_URL": "runtime-url"}
        flags = {"productionBackend": "vercel"}
        with patch.dict(
            "os.environ",
            {"PUMBILITY_PRODUCTION_DATABASE_URL": "original-session"},
            clear=True,
        ), patch(
            "api.operator._validated_shadow_restore_environment",
            return_value=(flags, "session-url"),
        ), patch("scripts.backfill_pumbility_production.main", return_value=0) as backfill:
            result = _run_shadow_restore(environment, action="backfill")
            self.assertEqual(
                os.environ["PUMBILITY_PRODUCTION_DATABASE_URL"], "original-session"
            )
            self.assertNotIn("PUMBILITY_PRODUCTION_CONFIRMATION", os.environ)
        self.assertEqual(
            [call.args[0] for call in backfill.call_args_list],
            [
                ["--expected-project-ref", "gsiyqhkcgegjrvqcqioc"],
                ["--expected-project-ref", "gsiyqhkcgegjrvqcqioc", "--apply"],
            ],
        )
        self.assertEqual(result["action"], "backfill")
        self.assertFalse(result["typedPopulationCompleted"])

    def test_shadow_population_uses_advisory_lock_and_releases_on_failure(self) -> None:
        environment = {"PUMBILITY_DATABASE_URL": "runtime-url"}
        flags = {"productionBackend": "vercel"}
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.cursor.return_value = cursor
        with patch.dict("os.environ", {}, clear=True), patch(
            "api.operator._validated_shadow_restore_environment",
            return_value=(flags, "session-url"),
        ), patch("psycopg.connect", return_value=connection), patch(
            "pumbility_store._assert_schema"
        ) as schema, patch(
            "scripts.backfill_pumbility_production._assert_database_target"
        ) as target, patch(
            "scripts.backfill_pumbility_production._claim_lock"
        ) as claim, patch(
            "scripts.backfill_pumbility_production._release_lock"
        ) as release, patch(
            "scripts.populate_pumbility_production.main",
            side_effect=RuntimeError("private failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "private failure"):
                _run_shadow_restore(environment, action="populate")
        schema.assert_called_once_with(cursor)
        target.assert_called_once_with(cursor)
        claim.assert_called_once_with(cursor)
        release.assert_called_once_with(cursor)
        self.assertEqual(connection.commit.call_count, 2)
        self.assertNotIn("PUMBILITY_PRODUCTION_POPULATION_CONFIRMATION", os.environ)

    def test_route_is_absent_outside_explicit_preview_diagnostic(self) -> None:
        for environment in (
            {},
            {
                "VERCEL_ENV": "production",
                "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED": "true",
            },
            {
                "VERCEL_ENV": "preview",
                "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED": "false",
            },
        ):
            with (
                patch.dict("os.environ", environment, clear=True),
                patch("api.operator._run_hosted_gate") as gate,
            ):
                response = run_hosted_precanary_reconciliation()
            self.assertEqual(response.status_code, 404)
            self.assertEqual(json.loads(response.body), {"error": "Not found."})
            gate.assert_not_called()

    def test_enabled_preview_returns_only_aggregate_gate_evidence(self) -> None:
        evidence = {
            "status": "passed",
            "gate": "hosted-pre-canary-reconciliation",
            "safeFlags": {"vercelAuthoritative": True},
            "reconciliation": {"privacyScan": "passed"},
        }
        with (
            patch.dict(
                "os.environ",
                {
                    "VERCEL_ENV": "preview",
                    "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED": "true",
                },
                clear=True,
            ),
            patch("api.operator._run_hosted_gate", return_value=evidence) as gate,
        ):
            response = run_hosted_precanary_reconciliation()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), evidence)
        gate.assert_called_once()

    def test_failure_is_sanitized(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "VERCEL_ENV": "preview",
                    "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED": "true",
                    "PUMBILITY_DATABASE_URL": "postgresql://private-host/db",
                    "BLOB_READ_WRITE_TOKEN": "x" * 32,
                },
                clear=True,
            ),
            patch(
                "api.operator._run_hosted_gate",
                side_effect=RuntimeError("PRIVATE_DATABASE_HOST PRIVATE_PATH"),
            ),
        ):
            response = run_hosted_precanary_reconciliation()

        body = response.body.decode("utf-8")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("PRIVATE_DATABASE_HOST", body)
        self.assertNotIn("PRIVATE_PATH", body)
        self.assertEqual(
            json.loads(response.body)["diagnostic"],
            {
                "failureCode": "relational-reconciliation",
                "databaseConfigured": True,
                "blobConfigured": True,
            },
        )

    def test_missing_credentials_are_reported_without_values(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "VERCEL_ENV": "preview",
                    "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED": "true",
                },
                clear=True,
            ),
            patch(
                "api.operator._run_hosted_gate",
                side_effect=RuntimeError("Required production credentials were not injected."),
            ),
        ):
            response = run_hosted_precanary_reconciliation()

        self.assertEqual(
            json.loads(response.body)["diagnostic"],
            {
                "failureCode": "credentials-unavailable",
                "databaseConfigured": False,
                "blobConfigured": False,
            },
        )

    def test_safe_reconciliation_evidence_is_forwarded(self) -> None:
        from scripts.verify_pumbility_pre_canary import PreCanaryGateError

        error = PreCanaryGateError(
            "Production reconciliation did not pass.",
            safe_evidence={
                "completedStages": ["source-boundary", "relational"],
                "failureEvent": {
                    "status": "mismatch",
                    "stage": "model-json",
                    "artifactIndex": 2,
                },
            },
        )
        with (
            patch.dict(
                "os.environ",
                {
                    "VERCEL_ENV": "preview",
                    "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED": "true",
                    "PUMBILITY_DATABASE_URL": "postgresql://private-host/db",
                    "BLOB_READ_WRITE_TOKEN": "x" * 32,
                },
                clear=True,
            ),
            patch("api.operator._run_hosted_gate", side_effect=error),
        ):
            response = run_hosted_precanary_reconciliation()

        diagnostic = json.loads(response.body)["diagnostic"]
        self.assertEqual(
            diagnostic["reconciliation"],
            error.safe_evidence,
        )

    def test_artifact_repair_requires_both_preview_controls(self) -> None:
        for environment in (
            {},
            {
                "VERCEL_ENV": "production",
                "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED": "true",
                "PUMBILITY_PRECANARY_ARTIFACT_REPAIR_ENABLED": "true",
            },
            {
                "VERCEL_ENV": "preview",
                "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED": "true",
                "PUMBILITY_PRECANARY_ARTIFACT_REPAIR_ENABLED": "false",
            },
        ):
            with (
                patch.dict("os.environ", environment, clear=True),
                patch("api.operator._repair_numeric_artifact") as repair,
            ):
                response = run_hosted_precanary_artifact_repair()
            self.assertEqual(response.status_code, 404)
            repair.assert_not_called()

    def test_artifact_repair_route_returns_only_aggregate_evidence(self) -> None:
        evidence = {
            "status": "repaired",
            "binaryArtifacts": 1,
            "exactReadback": True,
            "stableBoundary": True,
            "productionBackendChanged": False,
        }
        with (
            patch.dict(
                "os.environ",
                {
                    "VERCEL_ENV": "preview",
                    "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED": "true",
                    "PUMBILITY_PRECANARY_ARTIFACT_REPAIR_ENABLED": "true",
                },
                clear=True,
            ),
            patch(
                "api.operator._repair_numeric_artifact", return_value=evidence
            ) as repair,
        ):
            response = run_hosted_precanary_artifact_repair()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), evidence)
        repair.assert_called_once()

    def test_numeric_repair_is_locked_exact_and_boundary_checked(self) -> None:
        environment = {
            "PUMBILITY_DATABASE_URL": "runtime-url",
            "PUMBILITY_SUPABASE_URL": "https://supabase.invalid",
            "PUMBILITY_SUPABASE_SERVICE_ROLE_KEY": "x" * 32,
            "PUMBILITY_STORAGE_BUCKET": "private-bucket",
        }
        source = Mock()
        pointers = {
            "recommendations": {
                "storageSchemaVersion": 3,
                "generationKey": "a" * 20,
                "inputShardCount": 1,
            }
        }
        target = Mock()
        target.get_bytes.return_value = b"numeric-model"
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.cursor.return_value = cursor
        with (
            patch(
                "scripts.verify_pumbility_pre_canary.assert_pre_canary_environment"
            ) as flags,
            patch(
                "scripts.reconcile_pumbility_production.session_url_from_runtime",
                return_value="session-url",
            ),
            patch(
                "analysis_runtime.VercelPrivateBlobStore", return_value=source
            ),
            patch(
                "scripts.backfill_pumbility_production._read_stable_boundary",
                return_value=(pointers, {}, {"schemaVersion": 1}),
            ),
            patch(
                "scripts.capture_pumbility_migration_baseline._required_production_bytes",
                return_value=b"numeric-model",
            ),
            patch("pumbility_store.PumbilityArtifactStore", return_value=target),
            patch("psycopg.connect", return_value=connection),
            patch("pumbility_store._assert_schema") as schema,
            patch(
                "scripts.backfill_pumbility_production._assert_database_target"
            ) as database_target,
            patch("scripts.backfill_pumbility_production._claim_lock") as claim,
            patch("scripts.backfill_pumbility_production._release_lock") as release,
            patch(
                "scripts.backfill_pumbility_production._assert_boundary_unchanged"
            ) as boundary,
        ):
            result = _repair_numeric_artifact(environment)

        flags.assert_called_once_with(environment)
        schema.assert_called_once_with(cursor)
        database_target.assert_called_once_with(cursor)
        claim.assert_called_once_with(cursor)
        release.assert_called_once_with(cursor)
        target.put_bytes.assert_called_once_with(
            "analysis/recommendations/models/" + "a" * 20 + ".npz",
            b"numeric-model",
            content_type="application/x-npz",
        )
        boundary.assert_called_once_with(source, pointers, {"schemaVersion": 1})
        self.assertTrue(result["exactReadback"])


if __name__ == "__main__":
    unittest.main()
