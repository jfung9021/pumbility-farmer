from __future__ import annotations

import copy
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
            "VERCEL_OIDC_TOKEN",
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
        adopted_label="iad1",
    )


def _api(*, latency_passed: bool) -> dict[str, object]:
    def metric() -> dict[str, float]:
        return {"p50": 10.0, "p95": 10.0, "p99": 10.0, "max": 10.0}

    def domain_result() -> dict[str, object]:
        return {
            "scoredAttempts": 100,
            "scoredSuccesses": 100,
            "scoredErrors": 0,
            "scoredTransportFailures": 0,
            "scoredTransportRetries": 0,
            "warmupAttempts": 3,
            "warmupSuccesses": 3,
            "warmupErrors": 0,
            "warmupTransportFailures": 0,
            "warmupTransportRetries": 0,
            "cacheHits": 0,
            "gzipResponses": 100,
            "p99Scored": True,
            "expectedCandidateReadEvents": 103,
            "telemetryCountGate": "pending-server-log-reconciliation",
            "endToEndMs": metric(),
            "ttfbMs": metric(),
            "downloadMs": metric(),
            "jsonParseMs": metric(),
        }

    parity_domain = {
        "comparedResponses": 103,
        "exactMatches": 103,
        "mismatches": 0,
        "missingPairs": 0,
        "passed": True,
    }
    return {
        "schemaVersion": 1,
        "generatedAtUtc": "2026-08-15T00:00:00+00:00",
        "status": "passed" if latency_passed else "failed",
        "comparisonKind": "authenticated-protected-preview",
        "probeConfiguration": {
            "domains": ["analysis", "tier-list"],
            "samples": 100,
            "warmupSamples": 3,
            "windowMinutes": 15,
            "p99Scored": True,
            "canaryTelemetryExpected": True,
            "authenticatedWithVercelCli": True,
            "bypassTokenUsed": False,
            "maxPreResponseTransportRetriesPerRequest": 1,
            "timingSemantics": {
                "ttfbAndDownload": "curl request timing after authenticated CLI setup",
                "jsonParse": "local decoded-body JSON parse timing",
                "endToEnd": "curl network total plus local JSON parse",
                "cliStartupIncluded": False,
            },
        },
        "responseParity": {
            "passed": True,
            "domains": {
                "analysis": dict(parity_domain),
                "tier-list": dict(parity_domain),
            },
        },
        "identityDisclosure": {
            "deploymentReferencesPrintedOrStored": False,
            "urlsOrHostsPrintedOrStored": False,
            "responseHashesPrintedOrStored": False,
            "responseBodiesPrintedOrStored": False,
            "requestPathsOrQueryValuesPrintedOrStored": False,
            "commandOutputOrErrorsPrintedOrStored": False,
            "rawTransportErrorsPrintedOrStored": False,
            "secretsPrintedOrStored": False,
        },
        "deployments": [
            {
                "deploymentLabel": label,
                "domains": ["analysis", "tier-list"],
                "scoredSamplesPerDomain": 100,
                "warmupSamplesPerDomain": 3,
                "requestedWindowMinutes": 15,
                "elapsedScoredMinutes": 15,
                "compressionRequested": True,
                "cacheBypass": {
                    "requested": True,
                    "mechanisms": [
                        "unique-query-nonce",
                        "cache-control-no-cache-no-store",
                        "pragma-no-cache",
                    ],
                    "gate": "zero-x-vercel-cache-HIT",
                },
                "telemetry": {
                    "expected": True,
                    "countGateComplete": False,
                    "expectedCandidateReadEventsTotal": 206,
                    "requirement": "pending server-log reconciliation",
                },
                "results": {
                    "analysis": domain_result(),
                    "tier-list": domain_result(),
                },
            }
            for label in ("iad1", "cle1")
        ],
        "latencyComparison": {},
        "latencyGate": {
            "status": "passed" if latency_passed else "failed",
            "passed": latency_passed,
            "complete": True,
            "target": {
                "p95MaximumIncreasePercent": 10.0,
                "p99MaximumIncreasePercent": 20.0,
            },
            "domains": {},
            "ownerLatencyWaiver": {"acceptedHere": False},
        },
        "cacheBypassGatePassed": True,
        "telemetryCountGateComplete": False,
        "adoptionDecision": "pending",
    }


def _blob(label: str, latency: float) -> dict[str, object]:
    artifact = {
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
    return {
        "schemaVersion": 1,
        "status": "passed",
        "evidenceKind": "private-blob-region-read",
        "execution": {
            "deploymentRegionLabel": label,
            "regionAttestedByEnvironment": True,
            "isolatedDiagnosticTaskRequired": True,
        },
        "configuration": {
            "scoredSamplesPerArtifact": 100,
            "warmupSamplesPerArtifact": 3,
            "p99Scored": True,
        },
        "identityDisclosure": {
            "urlsPrinted": False,
            "digestsPrinted": False,
            "bodiesPrinted": False,
            "tokensPrinted": False,
        },
        "artifacts": {
            name: dict(artifact)
            for name in (
                "analysis-pointer",
                "tier-pointer",
                "recommendation-pointer",
                "numeric-model",
            )
        },
    }


def _events(*, latency: float) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for label in ("iad1", "cle1"):
        for domain in ("analysis", "tier-list"):
            events.append(
                {
                    "kind": "telemetry",
                    "label": label,
                    "domain": domain,
                    "outcome": "candidate-served",
                    "count": 103,
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
        if label == "iad1":
            correlation = "d" * 64
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
        self.assertEqual(manifest["adoptedLabel"], "iad1")
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

        with self.assertRaises(ManifestError):
            create_manifest(
                _deployment("iad1", "iad1"),
                _deployment("cle1", "cle1"),
                topology_kind="region",
                adopted_label="other",
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

    def test_protected_api_and_blob_scope_fail_closed_when_evidence_is_omitted(self) -> None:
        common = {
            "manifest": _manifest(),
            "events": _events(latency=100),
            "checklist": _checklist(),
            "owner_latency_waiver": False,
        }
        protected_api = _api(latency_passed=True)
        self.assertEqual(
            qualify(
                api=protected_api,
                blobs=[_blob("iad1", 100), _blob("cle1", 100)],
                **common,
            )["status"],
            "passed",
        )

        missing_domain = copy.deepcopy(protected_api)
        del missing_domain["deployments"][0]["results"]["tier-list"]
        with self.assertRaises(QualificationError):
            qualify(
                api=missing_domain,
                blobs=[_blob("iad1", 100), _blob("cle1", 100)],
                **common,
            )

        insufficient_api_warmups = copy.deepcopy(protected_api)
        insufficient_api_warmups["probeConfiguration"]["warmupSamples"] = 2
        with self.assertRaises(QualificationError):
            qualify(
                api=insufficient_api_warmups,
                blobs=[_blob("iad1", 100), _blob("cle1", 100)],
                **common,
            )

        missing_artifact = [_blob("iad1", 100), _blob("cle1", 100)]
        for report in missing_artifact:
            del report["artifacts"]["numeric-model"]
        self.assertEqual(
            qualify(api=protected_api, blobs=missing_artifact, **common)["status"],
            "failed",
        )

        insufficient_blob_warmups = [_blob("iad1", 100), _blob("cle1", 100)]
        for report in insufficient_blob_warmups:
            report["configuration"]["warmupSamplesPerArtifact"] = 2
            for artifact in report["artifacts"].values():
                artifact["warmupAttempts"] = 2
        self.assertEqual(
            qualify(
                api=protected_api,
                blobs=insufficient_blob_warmups,
                **common,
            )["status"],
            "failed",
        )

    def test_protected_api_allows_one_pre_response_transport_retry(self) -> None:
        api = _api(latency_passed=True)
        result = api["deployments"][0]["results"]["analysis"]
        result["scoredAttempts"] = 101
        result["scoredTransportFailures"] = 1
        result["scoredTransportRetries"] = 1
        result["warmupAttempts"] = 4
        result["warmupTransportFailures"] = 1
        result["warmupTransportRetries"] = 1
        result["expectedCandidateReadEvents"] = 105
        api["deployments"][0]["telemetry"]["expectedCandidateReadEventsTotal"] = 208
        events = _events(latency=100)
        next(
            event
            for event in events
            if event["kind"] == "telemetry"
            and event["label"] == "iad1"
            and event["domain"] == "analysis"
        )["count"] = 105

        report = qualify(
            manifest=_manifest(),
            api=api,
            blobs=[_blob("iad1", 100), _blob("cle1", 100)],
            events=events,
            checklist=_checklist(),
            owner_latency_waiver=False,
        )

        self.assertEqual(report["status"], "passed")
        self.assertTrue(
            report["nonLatencyGates"]["apiCorrectnessAndExactParity"]["passed"]
        )

    def test_protected_api_retry_contract_fails_closed(self) -> None:
        common = {
            "manifest": _manifest(),
            "blobs": [_blob("iad1", 100), _blob("cle1", 100)],
            "events": _events(latency=100),
            "checklist": _checklist(),
            "owner_latency_waiver": False,
        }

        scenarios: dict[str, dict[str, object]] = {}

        insufficient_successes = _api(latency_passed=True)
        insufficient_result = insufficient_successes["deployments"][0]["results"]["analysis"]
        insufficient_result["scoredAttempts"] = 99
        insufficient_result["scoredSuccesses"] = 99
        scenarios["fewer than 100 successful scored samples"] = insufficient_successes

        application_error = _api(latency_passed=True)
        error_result = application_error["deployments"][0]["results"]["analysis"]
        error_result["scoredAttempts"] = 101
        error_result["scoredErrors"] = 1
        scenarios["HTTP or application error"] = application_error

        exhausted_retry = _api(latency_passed=True)
        exhausted_result = exhausted_retry["deployments"][0]["results"]["analysis"]
        exhausted_result["scoredAttempts"] = 102
        exhausted_result["scoredTransportFailures"] = 2
        exhausted_result["scoredTransportRetries"] = 1
        scenarios["exhausted pre-response retry"] = exhausted_retry

        missing_attempt = _api(latency_passed=True)
        missing_attempt_result = missing_attempt["deployments"][0]["results"]["analysis"]
        missing_attempt_result["scoredTransportFailures"] = 1
        missing_attempt_result["scoredTransportRetries"] = 1
        scenarios["transport failure omitted from attempt count"] = missing_attempt

        unsafe_transport_details = _api(latency_passed=True)
        unsafe_transport_details["identityDisclosure"][
            "rawTransportErrorsPrintedOrStored"
        ] = True
        scenarios["raw transport error disclosure"] = unsafe_transport_details

        for name, api in scenarios.items():
            with self.subTest(name=name):
                report = qualify(api=api, **common)
                self.assertEqual(report["status"], "failed")
                self.assertFalse(
                    report["nonLatencyGates"]["apiCorrectnessAndExactParity"]["passed"]
                )

        excessive_retry_policy = _api(latency_passed=True)
        excessive_retry_policy["probeConfiguration"][
            "maxPreResponseTransportRetriesPerRequest"
        ] = 2
        with self.assertRaises(QualificationError):
            qualify(api=excessive_retry_policy, **common)

        retained_raw_detail = _api(latency_passed=True)
        retained_raw_detail["identityDisclosure"]["lastTransportError"] = "private"
        with self.assertRaises(QualificationError):
            qualify(api=retained_raw_detail, **common)

    def test_cron_is_required_only_for_the_adopted_topology(self) -> None:
        common = {
            "manifest": _manifest(),
            "api": _api(latency_passed=True),
            "blobs": [_blob("iad1", 100), _blob("cle1", 100)],
            "checklist": _checklist(),
            "owner_latency_waiver": False,
        }
        adopted_only = _events(latency=100)
        self.assertEqual(qualify(events=adopted_only, **common)["status"], "passed")

        missing_adopted = [
            event for event in adopted_only if event["kind"] != "cron"
        ]
        self.assertEqual(
            qualify(events=missing_adopted, **common)["status"],
            "failed",
        )

        wrong_topology = copy.deepcopy(adopted_only)
        wrong_topology.append(
            {
                "kind": "cron",
                "label": "cle1",
                "source": "route",
                "correlationSha256": "e" * 64,
                "count": 1,
                "authorized": True,
            }
        )
        with self.assertRaises(QualificationError):
            qualify(events=wrong_topology, **common)


if __name__ == "__main__":
    unittest.main()
