from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from celery.exceptions import Reject
from starlette.requests import Request

from analysis_runtime import MemoryBlobStore
from api import topology as topology_api
from api.cron import _emit_topology_cron_route_event
from scripts.capture_pumbility_topology_manifest import ManifestError, create_manifest
from scripts.verify_pumbility_topology_qualification import verify_topology
from topology_diagnostics import require_diagnostic_environment
from worker.tasks import topology_queue_probe


def _environment() -> dict[str, str]:
    return {
        "VERCEL_ENV": "preview",
        "VERCEL_REGION": "iad1",
        "PUMBILITY_TOPOLOGY_DIAGNOSTIC_ENABLED": "true",
        "PUMBILITY_TOPOLOGY_LABEL": "iad1",
        "PUMBILITY_TOPOLOGY_CONNECTION_LIMIT": "12",
        "PUMBILITY_TOPOLOGY_CRON_CORRELATION_SHA256": "c" * 64,
    }


class HostedTopologyDiagnosticTests(unittest.TestCase):
    def test_environment_requires_preview_region_attestation_and_safe_limit(self) -> None:
        self.assertEqual(require_diagnostic_environment(_environment()), ("iad1", 12))
        for update in (
            {"VERCEL_ENV": "production"},
            {"VERCEL_REGION": "cle1"},
            {"PUMBILITY_TOPOLOGY_CONNECTION_LIMIT": "3"},
        ):
            with self.subTest(update=update), self.assertRaises(RuntimeError):
                require_diagnostic_environment({**_environment(), **update})

    @patch("worker.tasks.topology_queue_probe.apply_async")
    def test_queue_publisher_emits_only_verifier_allowlisted_events(self, publish) -> None:
        captured = io.StringIO()
        identities = iter(f"private-probe-{index}" for index in range(100))
        with (
            patch.dict(os.environ, _environment(), clear=False),
            patch("api.topology.new_identity", side_effect=identities),
            redirect_stdout(captured),
        ):
            response = topology_api.publish_queue_diagnostics("analysis", 100)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(publish.call_count, 100)
        events = [json.loads(line) for line in captured.getvalue().splitlines()]
        self.assertEqual(len(events), 100)
        self.assertTrue(
            all(
                set(event)
                == {
                    "kind",
                    "label",
                    "topic",
                    "stage",
                    "identitySha256",
                    "attempt",
                }
                for event in events
            )
        )
        self.assertEqual(len({event["identitySha256"] for event in events}), 100)
        self.assertTrue(
            all(len(call.kwargs["args"][2]) == 64 for call in publish.call_args_list)
        )
        self.assertNotIn("private-probe", captured.getvalue())

    @patch("api.topology._inject_blob_timeout", return_value=True)
    @patch("api.topology._inject_supabase_timeout", return_value=True)
    def test_timeout_fault_route_reports_only_sanitized_outcomes(
        self, inject_supabase, inject_blob
    ) -> None:
        with (
            patch.dict(os.environ, _environment(), clear=False),
            patch(
                "scripts.reconcile_pumbility_production.session_url_from_runtime",
                return_value="postgresql://diagnostic.invalid/database",
            ),
        ):
            response = topology_api.inject_topology_timeout_faults()
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(
            set(payload), {"schemaVersion", "status", "supabaseTimeout", "blobTimeout"}
        )
        inject_supabase.assert_called_once()
        inject_blob.assert_called_once()

    @patch("worker.tasks.topology_capacity_probe.apply_async")
    def test_capacity_publisher_queues_exactly_thirty_samples(self, publish) -> None:
        with patch.dict(os.environ, _environment(), clear=False):
            response = topology_api.publish_capacity_diagnostics(30)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(publish.call_count, 30)
        self.assertTrue(
            all(
                call.kwargs["args"] == ["iad1", 12]
                and call.kwargs["queue"] == "analysis"
                for call in publish.call_args_list
            )
        )

    def test_queue_probe_forces_one_redelivery_and_one_durable_effect(self) -> None:
        store = MemoryBlobStore()
        captured = io.StringIO()
        with (
            patch.dict(os.environ, _environment(), clear=False),
            patch("analysis_runtime.VercelPrivateBlobStore", return_value=store),
            patch(
                "worker.tasks._create_topology_effect_once",
                side_effect=[True, False],
            ),
            redirect_stdout(captured),
        ):
            with self.assertRaises(Reject):
                topology_queue_probe.run("iad1", "analysis", "a" * 64, True)
            result = topology_queue_probe.run(
                "iad1", "analysis", "a" * 64, True
            )
            duplicate = topology_queue_probe.run(
                "iad1", "analysis", "a" * 64, True
            )
        self.assertEqual(result["status"], "completed")
        self.assertFalse(duplicate["effectCreated"])
        events = [json.loads(line) for line in captured.getvalue().splitlines()]
        queue_events = [event for event in events if event["kind"] == "queue"]
        self.assertEqual(
            [event["attempt"] for event in queue_events if event["stage"] == "consumed"],
            [1, 2, 3],
        )
        self.assertEqual(
            len([event for event in queue_events if event["stage"] == "durable-effect"]),
            1,
        )
        self.assertNotIn("private-identity", captured.getvalue())

    def test_cron_route_event_requires_platform_user_agent(self) -> None:
        def request(user_agent: str) -> Request:
            return Request(
                {
                    "type": "http",
                    "headers": [(b"user-agent", user_agent.encode("ascii"))],
                }
            )

        captured = io.StringIO()
        environment = {**_environment(), "VERCEL_ENV": "production"}
        with patch.dict(os.environ, environment, clear=False), redirect_stdout(captured):
            _emit_topology_cron_route_event(request("operator"))
            _emit_topology_cron_route_event(request("vercel-cron/1.0"))
        events = [json.loads(line) for line in captured.getvalue().splitlines()]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "route")
        self.assertTrue(events[0]["authorized"])

    def test_manifest_allows_only_the_two_protected_read_domains(self) -> None:
        from tests.test_pumbility_topology_qualification import _deployment

        first = _deployment("iad1", "iad1")
        second = _deployment("cle1", "cle1")
        for deployment in (first, second):
            deployment["rolloutFlags"]["readCanaryDomains"] = [
                "analysis",
                "tier-list",
            ]
        result = create_manifest(
            first,
            second,
            topology_kind="region",
            boundary={
                "schemaVersion": 1,
                "publicationFrozen": True,
                "exactReconciliationPassed": True,
                "firstBoundarySha256": "d" * 64,
                "secondBoundarySha256": "d" * 64,
            },
        )
        self.assertTrue(result["safeFlagsProven"])
        topology, _labels, _concurrency, _limits = verify_topology(result)
        self.assertTrue(topology["passed"])
        second["rolloutFlags"]["readCanaryDomains"] = ["job-status"]
        with self.assertRaises(ManifestError):
            create_manifest(
                first,
                second,
                topology_kind="region",
                boundary={
                    "schemaVersion": 1,
                    "publicationFrozen": True,
                    "exactReconciliationPassed": True,
                    "firstBoundarySha256": "d" * 64,
                    "secondBoundarySha256": "d" * 64,
                },
            )
        second["rolloutFlags"]["readCanaryDomains"] = ["analysis"]
        with self.assertRaises(ManifestError):
            create_manifest(
                first,
                second,
                topology_kind="region",
                boundary={
                    "schemaVersion": 1,
                    "publicationFrozen": True,
                    "exactReconciliationPassed": True,
                    "firstBoundarySha256": "d" * 64,
                    "secondBoundarySha256": "d" * 64,
                },
            )


if __name__ == "__main__":
    unittest.main()
