from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from api.operator import run_hosted_precanary_reconciliation


class HostedPreCanaryOperatorTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
