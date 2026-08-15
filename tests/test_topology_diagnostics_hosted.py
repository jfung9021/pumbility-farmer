from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from celery.exceptions import Reject
from starlette.requests import Request

from analysis_runtime import MemoryBlobStore
from api import topology as topology_api
from api.cron import _emit_topology_cron_route_event
from scripts.capture_pumbility_topology_manifest import ManifestError, create_manifest
from scripts.verify_pumbility_topology_qualification import verify_topology
from topology_diagnostics import (
    action_result_path,
    require_diagnostic_environment,
    require_runtime_database_url,
)
from worker.bootstrap import register_worker_boot, reset_worker_boot_for_tests
from worker.tasks import (
    topology_action_probe,
    topology_capacity_probe,
    topology_queue_probe,
)


def _environment() -> dict[str, str]:
    return {
        "VERCEL_ENV": "preview",
        "VERCEL_REGION": "iad1",
        "PUMBILITY_TOPOLOGY_DIAGNOSTIC_ENABLED": "true",
        "PUMBILITY_TOPOLOGY_LABEL": "iad1",
        "PUMBILITY_TOPOLOGY_CONNECTION_LIMIT": "12",
        "PUMBILITY_TOPOLOGY_CRON_CORRELATION_SHA256": "c" * 64,
        "PUMBILITY_DATABASE_URL": (
            "postgresql://pumbility_runtime_login.gsiyqhkcgegjrvqcqioc:"
            "test-only@aws-1-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require"
        ),
    }


class HostedTopologyDiagnosticTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_worker_boot_for_tests()

    def test_environment_requires_preview_region_attestation_and_safe_limit(self) -> None:
        self.assertEqual(require_diagnostic_environment(_environment()), ("iad1", 12))
        for update in (
            {"VERCEL_ENV": "production"},
            {"VERCEL_REGION": "cle1"},
            {"PUMBILITY_TOPOLOGY_CONNECTION_LIMIT": "3"},
        ):
            with self.subTest(update=update), self.assertRaises(RuntimeError):
                require_diagnostic_environment({**_environment(), **update})

    def test_runtime_database_url_is_validated_without_session_conversion(self) -> None:
        environment = _environment()
        self.assertEqual(
            require_runtime_database_url(environment),
            environment["PUMBILITY_DATABASE_URL"],
        )
        with self.assertRaises(RuntimeError):
            require_runtime_database_url(
                {
                    **environment,
                    "PUMBILITY_DATABASE_URL": environment[
                        "PUMBILITY_DATABASE_URL"
                    ].replace(":6543/", ":5432/"),
                }
            )

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

    @patch("worker.tasks.topology_action_probe.apply_async")
    def test_timeout_fault_route_queues_worker_execution(self, publish) -> None:
        with patch.dict(os.environ, _environment(), clear=False):
            response = topology_api.inject_topology_timeout_faults(
                "timeouts", "analysis"
            )
        self.assertEqual(response.status_code, 202)
        payload = json.loads(response.body)
        self.assertEqual(payload["action"], "timeout-faults")
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(publish.call_args.kwargs["queue"], "analysis")

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

    def test_capacity_uses_the_actual_bounded_runtime_pool(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = (4,)
        connection = MagicMock()
        connection.transaction.return_value.__enter__.return_value = None
        connection.cursor.return_value.__enter__.return_value = cursor
        pooled = MagicMock()
        pooled.__enter__.return_value = connection
        captured = io.StringIO()
        with (
            patch.dict(os.environ, _environment(), clear=False),
            patch("pumbility_store._read_connect", return_value=pooled) as read_connect,
            patch("pumbility_store._assert_schema"),
            patch("scripts.backfill_pumbility_production._assert_database_target"),
            redirect_stdout(captured),
        ):
            result = topology_capacity_probe.run("iad1", 12)
        self.assertEqual(result["status"], "completed")
        read_connect.assert_called_once_with(_environment()["PUMBILITY_DATABASE_URL"])
        event = json.loads(captured.getvalue().splitlines()[0])
        self.assertEqual(event["activeConnections"], 4)

    def test_worker_action_failure_is_fixed_and_sanitized(self) -> None:
        store = MemoryBlobStore()
        captured = io.StringIO()
        with (
            patch.dict(os.environ, _environment(), clear=False),
            patch("analysis_runtime.VercelPrivateBlobStore", return_value=store),
            patch(
                "api.topology._run_timeout_faults",
                side_effect=RuntimeError("private database detail"),
            ),
            redirect_stdout(captured),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "hosted topology action diagnostic failed"
            ) as raised:
                topology_action_probe.run(
                    "iad1", "analysis", "b" * 64, "timeout-faults"
                )
        self.assertIsNone(raised.exception.__cause__)
        marker = store.get_json(action_result_path("iad1", "b" * 64))
        self.assertEqual(marker["error"], "diagnostic-failed")
        self.assertNotIn("private database detail", captured.getvalue())
        events = [json.loads(line) for line in captured.getvalue().splitlines()]
        self.assertEqual(events[-1]["outcome"], "failed")

    def test_worker_crash_recovers_on_redelivery_with_one_effect(self) -> None:
        class ProcessTerminated(BaseException):
            pass

        store = MemoryBlobStore()
        captured = io.StringIO()
        with (
            patch.dict(os.environ, _environment(), clear=False),
            patch("analysis_runtime.VercelPrivateBlobStore", return_value=store),
            patch(
                "worker.tasks._terminate_topology_worker_process",
                side_effect=ProcessTerminated,
            ),
            patch("worker.tasks._create_topology_effect_once", return_value=True) as effect,
            redirect_stdout(captured),
        ):
            with self.assertRaises(ProcessTerminated):
                topology_action_probe.run(
                    "iad1", "analysis", "d" * 64, "worker-crash"
                )
            result = topology_action_probe.run(
                "iad1", "analysis", "d" * 64, "worker-crash"
            )
            duplicate = topology_action_probe.run(
                "iad1", "analysis", "d" * 64, "worker-crash"
            )
        self.assertEqual(result["status"], "completed")
        self.assertFalse(duplicate["effectCreated"])
        effect.assert_called_once()
        marker = store.get_json(action_result_path("iad1", "d" * 64))
        self.assertTrue(marker["crashObserved"])
        self.assertTrue(marker["redeliveryRecovered"])
        self.assertEqual(marker["attempts"], 2)

    def test_worker_cold_start_uses_entrypoint_boot_and_emits_once(self) -> None:
        store = MemoryBlobStore()
        captured = io.StringIO()
        with patch("worker.bootstrap.time.perf_counter", return_value=10.0):
            register_worker_boot("analysis-worker")
        with (
            patch.dict(os.environ, _environment(), clear=False),
            patch("analysis_runtime.VercelPrivateBlobStore", return_value=store),
            patch("worker.tasks._create_topology_effect_once", return_value=True),
            patch("worker.tasks.perf_counter", return_value=10.5),
            redirect_stdout(captured),
        ):
            topology_queue_probe.run("iad1", "analysis", "e" * 64, False)
            topology_queue_probe.run("iad1", "analysis", "f" * 64, False)
        events = [json.loads(line) for line in captured.getvalue().splitlines()]
        cold = [event for event in events if event["kind"] == "cold-start"]
        self.assertEqual(len(cold), 1)
        self.assertEqual(cold[0]["durationMs"], 500.0)
        self.assertEqual(cold[0]["component"], "analysis-worker")

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
        with (
            patch.dict(os.environ, environment, clear=False),
            patch("api.cron._topology_cron_claim", return_value=("iad1", "c" * 64, False)),
            redirect_stdout(captured),
        ):
            _emit_topology_cron_route_event(
                request("operator"), status=202, outcome="started"
            )
            _emit_topology_cron_route_event(
                request("vercel-cron/1.0"), status=503, outcome="failed"
            )
            _emit_topology_cron_route_event(
                request("vercel-cron/1.0"), status=202, outcome="started"
            )
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
