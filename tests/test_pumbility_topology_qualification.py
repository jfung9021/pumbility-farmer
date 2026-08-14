from __future__ import annotations

import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

from scripts.benchmark_pumbility_blob_region import run_benchmark
from scripts.capture_pumbility_topology_manifest import ManifestError, create_manifest
from scripts.compare_pumbility_preview_regions import evaluate_latency_gate
from scripts.verify_pumbility_topology_qualification import (
    QualificationError,
    qualify,
)


def _flags() -> dict[str, object]:
    return {
        "backend": "shadow",
        "shadowStrict": False,
        "canonicalSnapshotWriteEnabled": True,
        "blobMirrorEnabled": False,
        "blobReadFallbackEnabled": False,
        "readCanaryDomains": [],
        "selectedPlayerRefreshEnabled": False,
    }


def _deployment(label: str, region: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "label": label,
        "region": region,
        "gitCommit": "1ca5399",
        "sourceSha256": "a" * 64,
        "lockSha256": "b" * 64,
        "runtime": "python3.12",
        "memoryMb": 2048,
        "maxDurationSeconds": 800,
        "workerConcurrency": 4,
        "databaseConnectionLimit": 12,
        "connectionStrategy": "transaction-pooler",
        "environmentKeyNames": [
            "BLOB_READ_WRITE_TOKEN",
            "PUMBILITY_DATABASE_URL",
            "QSTASH_TOKEN",
        ],
        "rolloutFlags": _flags(),
    }


def _manifest() -> dict[str, object]:
    return create_manifest(
        _deployment("iad1", "iad1"),
        _deployment("cle1", "cle1"),
        topology_kind="region",
        boundary={
            "schemaVersion": 1,
            "publicationFrozen": True,
            "exactReconciliationPassed": True,
            "firstBoundarySha256": "c" * 64,
            "secondBoundarySha256": "c" * 64,
        },
    )


def _api(*, latency_passed: bool) -> dict[str, object]:
    result = {
        "analysis": {
            "scoredAttempts": 100,
            "scoredSuccesses": 100,
            "scoredErrors": 0,
            "warmupErrors": 0,
            "cacheHits": 0,
            "p99Scored": True,
            "expectedCandidateReadEvents": 100,
        }
    }
    return {
        "responseParity": {"passed": True},
        "identityDisclosure": {
            "urlsPrinted": False,
            "hostsPrinted": False,
            "responseHashesPrinted": False,
            "responseBodiesPrinted": False,
        },
        "deployments": [
            {
                "deploymentRegionLabel": label,
                "telemetry": {"expected": True},
                "results": result,
            }
            for label in ("iad1", "cle1")
        ],
        "latencyGate": {
            "status": "passed" if latency_passed else "failed",
            "passed": latency_passed,
            "complete": True,
        },
    }


def _blob(label: str, latency: float) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "status": "passed",
        "execution": {
            "deploymentRegionLabel": label,
            "regionAttestedByEnvironment": True,
            "isolatedDiagnosticTaskRequired": True,
        },
        "configuration": {
            "scoredSamplesPerArtifact": 100,
            "p99Scored": True,
        },
        "identityDisclosure": {
            "urlsPrinted": False,
            "digestsPrinted": False,
            "bodiesPrinted": False,
            "tokensPrinted": False,
        },
        "artifacts": {
            "analysis": {
                "scoredAttempts": 100,
                "warmupAttempts": 3,
                "warmupErrors": 0,
                "successes": 100,
                "errors": 0,
                "exactExpectedMatches": 100,
                "unauthenticatedReadDenied": True,
                "latencyMs": {"p95": latency, "p99": latency},
                "passed": True,
            }
        },
    }


def _events(*, latency: float) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for label in ("iad1", "cle1"):
        events.append(
            {
                "kind": "telemetry",
                "label": label,
                "domain": "analysis",
                "outcome": "candidate-served",
                "count": 100,
            }
        )
        for component in ("analysis", "player-recommendations"):
            events.append(
                {
                    "kind": "worker",
                    "label": label,
                    "component": component,
                    "outcome": "succeeded",
                    "count": 1,
                    "isolatedDiagnostic": True,
                }
            )
        correlation = ("d" if label == "iad1" else "e") * 64
        for source in ("platform-scheduler", "route"):
            events.append(
                {
                    "kind": "cron",
                    "label": label,
                    "source": source,
                    "correlationSha256": correlation,
                    "count": 1,
                    "authorized": True,
                }
            )
        for topic in ("analysis", "player-recommendations"):
            for index in range(100):
                identity = hashlib.sha256(
                    f"{label}:{topic}:{index}".encode("utf-8")
                ).hexdigest()
                for stage in ("published", "consumed", "durable-effect"):
                    events.append(
                        {
                            "kind": "queue",
                            "label": label,
                            "topic": topic,
                            "stage": stage,
                            "identitySha256": identity,
                            "attempt": 1,
                        }
                    )
                if index == 0:
                    events.append(
                        {
                            "kind": "queue",
                            "label": label,
                            "topic": topic,
                            "stage": "consumed",
                            "identitySha256": identity,
                            "attempt": 2,
                        }
                    )
        duration = 100.0 if label == "iad1" else latency
        for component in (
            "api",
            "analysis-worker",
            "player-recommendations-worker",
        ):
            for _ in range(30):
                events.append(
                    {
                        "kind": "cold-start",
                        "label": label,
                        "component": component,
                        "durationMs": duration,
                        "success": True,
                        "cold": True,
                    }
                )
        for _ in range(30):
            events.append(
                {
                    "kind": "capacity",
                    "label": label,
                    "activeConnections": 4,
                    "connectionLimit": 12,
                    "connectionErrors": 0,
                    "deadlineErrors": 0,
                }
            )
    return events


def _checklist() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "faultScenarios": {
            name: {
                "expectedOutcomeObserved": True,
                "dataCorruptionObserved": False,
                "passed": True,
            }
            for name in (
                "supabase-timeout",
                "blob-timeout",
                "queue-redelivery",
                "worker-crash",
                "cron-replay",
            )
        },
        "privateBlobMutation": {
            "isolatedDiagnostic": True,
            "jsonWriteReadDeleteExact": True,
            "binaryWriteReadDeleteExact": True,
            "failedBundleRetainedPreviousPointer": True,
            "failedBundleLeftNoPartialPublication": True,
        },
        "rollback": {
            "durationSeconds": 120,
            "flagsSafe": True,
            "apiSmokePassed": True,
            "workerSmokePassed": True,
            "exactReconciliationPassed": True,
            "canaryTelemetryAbsent": True,
            "dataLossObserved": False,
        },
        "privacy": {
            "eventsSanitized": True,
            "noRawIdentifiers": True,
            "noUrls": True,
            "noSecrets": True,
        },
    }


class _PrivateBlobHandler(BaseHTTPRequestHandler):
    payload = b'{"stable":true}'

    def do_GET(self) -> None:  # noqa: N802
        if self.headers.get("Authorization") != "Bearer test-token":
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, format: str, *args: object) -> None:
        return


class TopologyQualificationTests(unittest.TestCase):
    def test_manifest_is_sanitized_and_rejects_uncontrolled_fields(self) -> None:
        manifest = _manifest()
        encoded = json.dumps(manifest)
        self.assertEqual(manifest["status"], "passed")
        self.assertNotIn("a" * 64, encoded)
        self.assertNotIn("c" * 64, encoded)

        second = _deployment("cle1", "cle1")
        second["deploymentUrl"] = "https://private.example.test"
        with self.assertRaises(ManifestError):
            create_manifest(
                _deployment("iad1", "iad1"),
                second,
                topology_kind="region",
                boundary={
                    "schemaVersion": 1,
                    "publicationFrozen": True,
                    "exactReconciliationPassed": True,
                    "firstBoundarySha256": "c" * 64,
                    "secondBoundarySha256": "c" * 64,
                },
            )

    def test_latency_target_reports_pass_fail_and_non_gating_smoke(self) -> None:
        def comparison(p95: float, p99: float) -> dict[str, object]:
            return {
                "analysis": {
                    "endToEndMs": {
                        "p95": {"secondVsFirstPercent": p95},
                        "p99": {"secondVsFirstPercent": p99},
                    }
                }
            }

        self.assertEqual(
            evaluate_latency_gate(comparison(10, 20), domains=["analysis"], p99_scored=True)["status"],
            "passed",
        )
        failed = evaluate_latency_gate(
            comparison(10.001, 20.001), domains=["analysis"], p99_scored=True
        )
        self.assertEqual(failed["status"], "failed")
        self.assertFalse(failed["ownerLatencyWaiver"]["acceptedHere"])
        self.assertEqual(
            evaluate_latency_gate(comparison(1, 0), domains=["analysis"], p99_scored=False)["status"],
            "not-scored",
        )

    def test_private_blob_harness_proves_denial_and_exact_reads_without_identity_output(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _PrivateBlobHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/private-artifact"
            digest = hashlib.sha256(_PrivateBlobHandler.payload).hexdigest()
            report = run_benchmark(
                label="iad1",
                targets=[{"name": "analysis", "url": url, "sha256": digest}],
                token="test-token",
                samples=100,
                warmups=1,
                timeout_seconds=5,
                attested_region="iad1",
            )
            self.assertEqual(report["status"], "passed")
            encoded = json.dumps(report)
            self.assertNotIn(url, encoded)
            self.assertNotIn(digest, encoded)
            self.assertNotIn("test-token", encoded)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_owner_waiver_is_distinct_and_only_eligible_for_latency(self) -> None:
        arguments = {
            "manifest": _manifest(),
            "api": _api(latency_passed=False),
            "blobs": [_blob("iad1", 100), _blob("cle1", 150)],
            "events": _events(latency=150),
            "checklist": _checklist(),
        }
        failed = qualify(**arguments, owner_latency_waiver=False)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["diagnosticLatencyGates"]["status"], "failed")
        self.assertTrue(failed["ownerLatencyWaiver"]["eligible"])

        waived = qualify(**arguments, owner_latency_waiver=True)
        self.assertEqual(waived["status"], "owner-latency-waived")
        self.assertTrue(waived["qualifiedForTopologyAdoption"])
        self.assertEqual(waived["diagnosticLatencyGates"]["status"], "failed")
        self.assertFalse(waived["ownerLatencyWaiver"]["nonLatencyFailuresWaived"])

    def test_waiver_cannot_cover_rollback_or_unsafe_event_evidence(self) -> None:
        checklist = _checklist()
        checklist["rollback"]["flagsSafe"] = False
        report = qualify(
            manifest=_manifest(),
            api=_api(latency_passed=False),
            blobs=[_blob("iad1", 100), _blob("cle1", 150)],
            events=_events(latency=150),
            checklist=checklist,
            owner_latency_waiver=True,
        )
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["ownerLatencyWaiver"]["eligible"])

        events = _events(latency=100)
        events[0]["rawPlayerId"] = "private"
        with self.assertRaises(QualificationError):
            qualify(
                manifest=_manifest(),
                api=_api(latency_passed=True),
                blobs=[_blob("iad1", 100), _blob("cle1", 100)],
                events=events,
                checklist=_checklist(),
                owner_latency_waiver=False,
            )

    def test_all_worker_cold_queue_and_blob_mutation_evidence_is_mandatory(self) -> None:
        common = {
            "manifest": _manifest(),
            "api": _api(latency_passed=True),
            "blobs": [_blob("iad1", 100), _blob("cle1", 100)],
            "checklist": _checklist(),
            "owner_latency_waiver": False,
        }
        complete = qualify(events=_events(latency=100), **common)
        self.assertEqual(complete["status"], "passed")

        missing_worker = [
            event
            for event in _events(latency=100)
            if not (
                event["kind"] == "worker"
                and event.get("label") == "cle1"
                and event.get("component") == "player-recommendations"
            )
        ]
        self.assertEqual(
            qualify(events=missing_worker, **common)["status"],
            "failed",
        )

        missing_cold_component = [
            event
            for event in _events(latency=100)
            if not (
                event["kind"] == "cold-start"
                and event.get("label") == "cle1"
                and event.get("component") == "player-recommendations-worker"
            )
        ]
        self.assertEqual(
            qualify(events=missing_cold_component, **common)["status"],
            "failed",
        )

        no_redelivery = [
            event
            for event in _events(latency=100)
            if not (event["kind"] == "queue" and event.get("attempt") == 2)
        ]
        self.assertEqual(qualify(events=no_redelivery, **common)["status"], "failed")

        removed_identity = hashlib.sha256(b"cle1:analysis:99").hexdigest()
        under_hundred = [
            event
            for event in _events(latency=100)
            if not (
                event["kind"] == "queue"
                and event.get("identitySha256") == removed_identity
            )
        ]
        self.assertEqual(qualify(events=under_hundred, **common)["status"], "failed")

        failed_mutation = _checklist()
        failed_mutation["privateBlobMutation"][
            "failedBundleRetainedPreviousPointer"
        ] = False
        self.assertEqual(
            qualify(
                events=_events(latency=100),
                **{**common, "checklist": failed_mutation},
            )["status"],
            "failed",
        )


if __name__ == "__main__":
    unittest.main()
