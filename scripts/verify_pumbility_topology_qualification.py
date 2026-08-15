"""Reconcile sanitized evidence for a Pumbility topology qualification.

All inputs are local exports or reports.  The verifier performs no network call
and mutation.  A latency miss remains visible against the original +10% p95 and
+20% p99 target.  An explicit owner waiver can produce only the distinct
``owner-latency-waived`` decision and is ineligible when any non-latency gate is
missing or failing.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA = PROJECT_ROOT / ".local-data"
LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
P95_MAX_INCREASE_PERCENT = 10.0
P99_MAX_INCREASE_PERCENT = 20.0
REQUIRED_TOPICS = frozenset({"analysis", "player-recommendations"})
REQUIRED_WORKER_COMPONENTS = frozenset({"analysis", "player-recommendations"})
QUALIFICATION_CANARY_DOMAINS = ("analysis", "tier-list")
REQUIRED_BLOB_ARTIFACTS = frozenset(
    {
        "analysis-pointer",
        "tier-pointer",
        "recommendation-pointer",
        "numeric-model",
    }
)
REQUIRED_FAULT_SCENARIOS = frozenset(
    {
        "supabase-timeout",
        "blob-timeout",
        "queue-redelivery",
        "worker-crash",
        "cron-replay",
    }
)
TELEMETRY_OUTCOMES = frozenset(
    {
        "candidate-served",
        "candidate-error",
        "fallback",
        "mismatch",
        "authority-error",
    }
)


class QualificationError(RuntimeError):
    """Qualification evidence is malformed or unsafe."""


def _json_mapping(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise QualificationError("A qualification input is not a JSON object.")
    return value


def _jsonl(path: Path) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise QualificationError("A qualification event is malformed.")
        records.append(value)
    if not records:
        raise QualificationError("Qualification events are empty.")
    return records


def _label(value: object) -> str:
    normalized = str(value or "").strip().casefold()
    if not LABEL_RE.fullmatch(normalized):
        raise QualificationError("An evidence label is malformed.")
    return normalized


def _int(value: object, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise QualificationError("An evidence count is malformed.")
    return value


def _number(value: object, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QualificationError("A numeric evidence value is malformed.")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise QualificationError("A numeric evidence value is malformed.")
    return result


def _mapping(value: object, message: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise QualificationError(message)
    return value


def _exact_keys(value: Mapping[str, Any], keys: set[str], message: str) -> None:
    if set(value) != keys:
        raise QualificationError(message)


def _delta(first: float, second: float) -> float:
    if first <= 0:
        raise QualificationError("A latency baseline must be positive.")
    return round(((second - first) / first) * 100.0, 3)


def _nearest_rank(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise QualificationError("Latency samples are missing.")
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return round(ordered[index], 3)


def verify_topology(
    manifest: Mapping[str, Any],
) -> tuple[dict[str, object], list[str], dict[str, int], dict[str, int]]:
    _exact_keys(
        manifest,
        {
            "schemaVersion",
            "status",
            "topologyKind",
            "controlledDifference",
            "adoptedLabel",
            "deployments",
            "identities",
            "stableDataBoundary",
            "safeFlagsProven",
        },
        "The topology manifest contains unexpected fields.",
    )
    deployments = manifest.get("deployments")
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("status") != "passed"
        or manifest.get("safeFlagsProven") is not True
        or not isinstance(deployments, list)
        or len(deployments) != 2
    ):
        raise QualificationError("The sanitized topology manifest did not pass.")
    labels: list[str] = []
    concurrency: dict[str, int] = {}
    connection_limits: dict[str, int] = {}
    normalized_deployments: list[dict[str, object]] = []
    for deployment in deployments:
        value = _mapping(deployment, "A topology deployment is malformed.")
        _exact_keys(
            value,
            {
                "label",
                "region",
                "runtime",
                "memoryMb",
                "maxDurationSeconds",
                "workerConcurrency",
                "databaseConnectionLimit",
                "connectionStrategy",
                "environmentKeyNames",
                "rolloutFlags",
            },
            "A topology deployment contains unexpected fields.",
        )
        label = _label(value.get("label"))
        labels.append(label)
        concurrency[label] = _int(value.get("workerConcurrency"), minimum=1)
        connection_limits[label] = _int(
            value.get("databaseConnectionLimit"), minimum=1
        )
        flags = _mapping(value.get("rolloutFlags"), "Safe rollout flags are missing.")
        _exact_keys(
            flags,
            {
                "backend",
                "shadowStrict",
                "canonicalSnapshotWriteEnabled",
                "blobMirrorEnabled",
                "blobReadFallbackEnabled",
                "readCanaryDomains",
                "selectedPlayerRefreshEnabled",
            },
            "The sanitized rollout flag set is incomplete.",
        )
        safe_flags = bool(
            flags.get("backend") in {"vercel", "shadow"}
            and flags.get("shadowStrict") is False
            and flags.get("blobMirrorEnabled") is False
            and flags.get("blobReadFallbackEnabled") is False
            and flags.get("readCanaryDomains")
            in ([], list(QUALIFICATION_CANARY_DOMAINS))
            and flags.get("selectedPlayerRefreshEnabled") is False
            and (
                flags.get("backend") == "shadow"
                or flags.get("canonicalSnapshotWriteEnabled") is False
            )
            and type(flags.get("canonicalSnapshotWriteEnabled")) is bool
        )
        environment_names = value.get("environmentKeyNames")
        if not isinstance(environment_names, (list, tuple)) or not all(
            isinstance(name, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", name)
            for name in environment_names
        ):
            raise QualificationError("Environment key-name evidence is malformed.")
        normalized_deployments.append(
            {
                **dict(value),
                "label": label,
                "rolloutFlags": dict(flags),
                "safeFlags": safe_flags,
                "environmentKeyNames": tuple(environment_names),
            }
        )
    if len(set(labels)) != 2:
        raise QualificationError("Topology labels must be distinct.")
    adopted_label = _label(manifest.get("adoptedLabel"))
    if adopted_label not in labels:
        raise QualificationError("The adopted topology label is not in the manifest.")
    stable = _mapping(
        manifest.get("stableDataBoundary"), "Stable-boundary evidence is missing."
    )
    identities = _mapping(manifest.get("identities"), "Identity evidence is missing.")
    _exact_keys(
        stable,
        {
            "publicationFrozen",
            "exactReconciliationPassed",
            "exactFingerprintMatch",
            "privateFingerprintDisclosed",
        },
        "Stable-boundary evidence contains unexpected fields.",
    )
    _exact_keys(
        identities,
        {
            "gitCommitMatch",
            "sourceHashMatch",
            "lockHashMatch",
            "privateHashesDisclosed",
        },
        "Identity evidence contains unexpected fields.",
    )
    topology_kind = manifest.get("topologyKind")
    controlled = manifest.get("controlledDifference")
    if (topology_kind, controlled) not in {
        ("region", "region"),
        ("connection", "connectionStrategy"),
    }:
        raise QualificationError("The controlled topology difference is malformed.")
    first, second = normalized_deployments
    comparable = set(first) - {"label", "safeFlags", str(controlled)}
    only_controlled_difference = bool(
        all(first[field] == second[field] for field in comparable)
        and first[str(controlled)] != second[str(controlled)]
    )
    passed = bool(
        stable.get("publicationFrozen") is True
        and stable.get("exactReconciliationPassed") is True
        and stable.get("exactFingerprintMatch") is True
        and identities.get("gitCommitMatch") is True
        and identities.get("sourceHashMatch") is True
        and identities.get("lockHashMatch") is True
        and identities.get("privateHashesDisclosed") is False
        and stable.get("privateFingerprintDisclosed") is False
        and all(value["safeFlags"] is True for value in normalized_deployments)
        and only_controlled_difference
    )
    return (
        {
            "status": "passed" if passed else "failed",
            "passed": passed,
            "adoptedLabel": adopted_label,
        },
        labels,
        concurrency,
        connection_limits,
    )


def verify_api(
    report: Mapping[str, Any], *, labels: Sequence[str]
) -> tuple[dict[str, object], dict[str, object], dict[tuple[str, str], int]]:
    _exact_keys(
        report,
        {
            "schemaVersion",
            "generatedAtUtc",
            "status",
            "comparisonKind",
            "probeConfiguration",
            "deployments",
            "responseParity",
            "latencyComparison",
            "latencyGate",
            "cacheBypassGatePassed",
            "telemetryCountGateComplete",
            "identityDisclosure",
            "adoptionDecision",
        },
        "The protected-preview API report contains unexpected fields.",
    )
    configuration = _mapping(
        report.get("probeConfiguration"), "API probe configuration is missing."
    )
    _exact_keys(
        configuration,
        {
            "domains",
            "samples",
            "warmupSamples",
            "windowMinutes",
            "p99Scored",
            "canaryTelemetryExpected",
            "authenticatedWithVercelCli",
            "bypassTokenUsed",
            "timingSemantics",
        },
        "The protected-preview probe configuration contains unexpected fields.",
    )
    required_domains = list(QUALIFICATION_CANARY_DOMAINS)
    scored_samples = _int(configuration.get("samples"), minimum=100)
    warmup_samples = _int(configuration.get("warmupSamples"), minimum=0)
    if (
        report.get("schemaVersion") != 1
        or report.get("comparisonKind") != "authenticated-protected-preview"
        or not isinstance(report.get("generatedAtUtc"), str)
        or not report.get("generatedAtUtc")
        or report.get("status") not in {"passed", "failed"}
        or configuration.get("domains") != required_domains
        or warmup_samples != 3
        or configuration.get("p99Scored") is not True
        or configuration.get("canaryTelemetryExpected") is not True
        or configuration.get("authenticatedWithVercelCli") is not True
        or configuration.get("bypassTokenUsed") is not False
        or not isinstance(configuration.get("timingSemantics"), Mapping)
        or report.get("cacheBypassGatePassed") is not True
        or report.get("telemetryCountGateComplete") is not False
        or report.get("adoptionDecision") != "pending"
    ):
        raise QualificationError("The protected-preview API configuration is invalid.")

    parity = _mapping(report.get("responseParity"), "API parity evidence is missing.")
    _exact_keys(parity, {"passed", "domains"}, "API parity evidence is malformed.")
    parity_domains = _mapping(parity.get("domains"), "API parity domains are missing.")
    if set(parity_domains) != set(required_domains):
        raise QualificationError("The required API parity domains are incomplete.")
    parity_passed = parity.get("passed") is True
    for domain in required_domains:
        value = _mapping(parity_domains.get(domain), "API parity evidence is malformed.")
        _exact_keys(
            value,
            {"comparedResponses", "exactMatches", "mismatches", "missingPairs", "passed"},
            "API parity evidence contains unexpected fields.",
        )
        compared = _int(value.get("comparedResponses"), minimum=1)
        parity_passed = parity_passed and bool(
            compared == scored_samples + warmup_samples
            and _int(value.get("exactMatches")) == compared
            and _int(value.get("mismatches")) == 0
            and _int(value.get("missingPairs")) == 0
            and value.get("passed") is True
        )
    disclosure = _mapping(
        report.get("identityDisclosure"), "API identity-disclosure evidence is missing."
    )
    disclosure_keys = {
        "deploymentReferencesPrintedOrStored",
        "urlsOrHostsPrintedOrStored",
        "responseHashesPrintedOrStored",
        "responseBodiesPrintedOrStored",
        "requestPathsOrQueryValuesPrintedOrStored",
        "commandOutputOrErrorsPrintedOrStored",
        "secretsPrintedOrStored",
    }
    _exact_keys(
        disclosure,
        disclosure_keys,
        "API identity-disclosure evidence contains unexpected fields.",
    )
    deployments = report.get("deployments")
    if not isinstance(deployments, list) or len(deployments) != 2:
        raise QualificationError("API deployment evidence is incomplete.")
    expected: dict[tuple[str, str], int] = {}
    correctness_passed = bool(
        parity_passed
        and all(disclosure.get(key) is False for key in disclosure_keys)
    )
    seen_labels: list[str] = []
    for deployment in deployments:
        value = _mapping(deployment, "API deployment evidence is malformed.")
        _exact_keys(
            value,
            {
                "deploymentLabel",
                "domains",
                "scoredSamplesPerDomain",
                "warmupSamplesPerDomain",
                "requestedWindowMinutes",
                "elapsedScoredMinutes",
                "compressionRequested",
                "cacheBypass",
                "telemetry",
                "results",
            },
            "API deployment evidence contains unexpected fields.",
        )
        label = _label(value.get("deploymentLabel"))
        seen_labels.append(label)
        if (
            value.get("domains") != required_domains
            or _int(value.get("scoredSamplesPerDomain"), minimum=100) != scored_samples
            or _int(value.get("warmupSamplesPerDomain")) != warmup_samples
            or value.get("compressionRequested") is not True
        ):
            raise QualificationError("API deployment probe settings are inconsistent.")
        cache_bypass = _mapping(
            value.get("cacheBypass"), "Cache-bypass evidence is missing."
        )
        _exact_keys(
            cache_bypass,
            {"requested", "mechanisms", "gate"},
            "Cache-bypass evidence contains unexpected fields.",
        )
        if cache_bypass.get("requested") is not True:
            correctness_passed = False
        telemetry = _mapping(value.get("telemetry"), "Telemetry mode is missing.")
        _exact_keys(
            telemetry,
            {"expected", "countGateComplete", "expectedCandidateReadEventsTotal", "requirement"},
            "Telemetry mode contains unexpected fields.",
        )
        if (
            telemetry.get("expected") is not True
            or telemetry.get("countGateComplete") is not False
        ):
            correctness_passed = False
        results = _mapping(value.get("results"), "API domain results are missing.")
        if set(results) != set(required_domains):
            raise QualificationError("The required API domain results are incomplete.")
        expected_total = 0
        for domain in required_domains:
            domain_result = _mapping(
                results.get(domain), "An API domain result is malformed."
            )
            _exact_keys(
                domain_result,
                {
                    "scoredAttempts",
                    "scoredSuccesses",
                    "scoredErrors",
                    "warmupAttempts",
                    "warmupErrors",
                    "cacheHits",
                    "gzipResponses",
                    "p99Scored",
                    "expectedCandidateReadEvents",
                    "telemetryCountGate",
                    "endToEndMs",
                    "ttfbMs",
                    "downloadMs",
                    "jsonParseMs",
                },
                "An API domain result contains unexpected fields.",
            )
            attempts = _int(domain_result.get("scoredAttempts"), minimum=1)
            successes = _int(domain_result.get("scoredSuccesses"), minimum=0)
            errors = _int(domain_result.get("scoredErrors"), minimum=0)
            warmups = _int(domain_result.get("warmupAttempts"), minimum=0)
            warmup_errors = _int(domain_result.get("warmupErrors"), minimum=0)
            cache_hits = _int(domain_result.get("cacheHits"), minimum=0)
            event_count = _int(
                domain_result.get("expectedCandidateReadEvents"), minimum=1
            )
            expected[(label, domain)] = event_count
            expected_total += event_count
            correctness_passed = correctness_passed and bool(
                attempts == scored_samples
                and successes == attempts
                and errors == 0
                and warmups == warmup_samples
                and warmup_errors == 0
                and cache_hits == 0
                and domain_result.get("p99Scored") is True
                and event_count == attempts + warmups
                and domain_result.get("telemetryCountGate")
                == "pending-server-log-reconciliation"
            )
        correctness_passed = correctness_passed and bool(
            _int(telemetry.get("expectedCandidateReadEventsTotal"), minimum=1)
            == expected_total
        )
    correctness_passed = correctness_passed and list(labels) == seen_labels
    latency = _mapping(report.get("latencyGate"), "API latency gate is missing.")
    _exact_keys(
        latency,
        {"status", "passed", "complete", "target", "domains", "ownerLatencyWaiver"},
        "API latency gate contains unexpected fields.",
    )
    latency_complete = latency.get("complete") is True
    latency_passed = latency.get("status") == "passed" and latency.get("passed") is True
    return (
        {
            "status": "passed" if correctness_passed else "failed",
            "passed": correctness_passed,
            "exactResponseParity": parity.get("passed") is True,
            "errorsAndCacheHitsZero": correctness_passed,
        },
        {
            "status": "passed" if latency_passed else ("failed" if latency_complete else "incomplete"),
            "passed": latency_passed,
            "complete": latency_complete,
            "target": {
                "p95MaximumIncreasePercent": P95_MAX_INCREASE_PERCENT,
                "p99MaximumIncreasePercent": P99_MAX_INCREASE_PERCENT,
            },
        },
        expected,
    )


def verify_blob(
    reports: Sequence[Mapping[str, Any]], *, labels: Sequence[str]
) -> tuple[dict[str, object], dict[str, object]]:
    if len(reports) != 2:
        raise QualificationError("Exactly two Blob benchmark reports are required.")
    by_label: dict[str, Mapping[str, Any]] = {}
    correctness = True
    artifact_names: set[str] | None = None
    for report in reports:
        execution = _mapping(report.get("execution"), "Blob execution evidence is missing.")
        label = _label(execution.get("deploymentRegionLabel"))
        if label in by_label:
            raise QualificationError("Blob benchmark labels are duplicated.")
        by_label[label] = report
        configuration = _mapping(
            report.get("configuration"), "Blob benchmark configuration is missing."
        )
        artifacts = _mapping(report.get("artifacts"), "Blob artifact evidence is missing.")
        disclosure = _mapping(
            report.get("identityDisclosure"),
            "Blob identity-disclosure evidence is missing.",
        )
        current_names = set(artifacts)
        if artifact_names is None:
            artifact_names = current_names
        correctness = correctness and bool(
            report.get("schemaVersion") == 1
            and report.get("status") == "passed"
            and report.get("evidenceKind") == "private-blob-region-read"
            and execution.get("regionAttestedByEnvironment") is True
            and execution.get("isolatedDiagnosticTaskRequired") is True
            and _int(configuration.get("scoredSamplesPerArtifact"), minimum=100) >= 100
            and _int(configuration.get("warmupSamplesPerArtifact"), minimum=0) >= 3
            and configuration.get("p99Scored") is True
            and disclosure.get("urlsPrinted") is False
            and disclosure.get("digestsPrinted") is False
            and disclosure.get("bodiesPrinted") is False
            and disclosure.get("tokensPrinted") is False
            and current_names == REQUIRED_BLOB_ARTIFACTS
            and current_names == artifact_names
        )
        for raw_artifact in artifacts.values():
            artifact = _mapping(raw_artifact, "Blob artifact evidence is malformed.")
            attempts = _int(artifact.get("scoredAttempts"), minimum=100)
            correctness = correctness and bool(
                _int(artifact.get("warmupAttempts"), minimum=0) >= 3
                and _int(artifact.get("warmupErrors"), minimum=0) == 0
                and _int(artifact.get("successes")) == attempts
                and _int(artifact.get("exactExpectedMatches")) == attempts
                and _int(artifact.get("errors")) == 0
                and artifact.get("unauthenticatedReadDenied") is True
                and artifact.get("passed") is True
            )
    if list(by_label) != list(labels):
        raise QualificationError("Blob reports must follow the topology label order.")
    first, second = (by_label[label] for label in labels)
    first_artifacts = _mapping(first.get("artifacts"), "Blob artifacts are missing.")
    second_artifacts = _mapping(second.get("artifacts"), "Blob artifacts are missing.")
    latency_results: dict[str, dict[str, object]] = {}
    latency_complete = True
    latency_passed = True
    for name in sorted(first_artifacts):
        first_latency = _mapping(
            _mapping(first_artifacts[name], "Blob artifact evidence is malformed.").get("latencyMs"),
            "Blob latency evidence is missing.",
        )
        second_latency = _mapping(
            _mapping(second_artifacts[name], "Blob artifact evidence is malformed.").get("latencyMs"),
            "Blob latency evidence is missing.",
        )
        p95_delta = _delta(_number(first_latency.get("p95")), _number(second_latency.get("p95")))
        p99_delta = _delta(_number(first_latency.get("p99")), _number(second_latency.get("p99")))
        passed = p95_delta <= P95_MAX_INCREASE_PERCENT and p99_delta <= P99_MAX_INCREASE_PERCENT
        latency_passed = latency_passed and passed
        latency_results[name] = {
            "p95IncreasePercent": p95_delta,
            "p99IncreasePercent": p99_delta,
            "passed": passed,
        }
    return (
        {
            "status": "passed" if correctness else "failed",
            "passed": correctness,
            "exactPayloadsAndPrivateAccess": correctness,
        },
        {
            "status": "passed" if latency_passed else "failed",
            "passed": latency_passed,
            "complete": latency_complete,
            "target": {
                "p95MaximumIncreasePercent": P95_MAX_INCREASE_PERCENT,
                "p99MaximumIncreasePercent": P99_MAX_INCREASE_PERCENT,
            },
            "artifacts": latency_results,
        },
    )


def _partition_events(records: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    partitions: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        kind = record.get("kind")
        if kind not in {"telemetry", "worker", "cron", "queue", "cold-start", "capacity"}:
            raise QualificationError("An event kind is not allowlisted.")
        partitions[str(kind)].append(record)
    return partitions


def verify_telemetry(
    records: Sequence[Mapping[str, Any]], expected: Mapping[tuple[str, str], int]
) -> dict[str, object]:
    observed: Counter[tuple[str, str, str]] = Counter()
    for record in records:
        _exact_keys(record, {"kind", "label", "domain", "outcome", "count"}, "Telemetry evidence contains unsafe fields.")
        label = _label(record.get("label"))
        domain = str(record.get("domain") or "")
        outcome = str(record.get("outcome") or "")
        if (label, domain) not in expected or outcome not in TELEMETRY_OUTCOMES:
            raise QualificationError("Telemetry evidence is outside the expected scope.")
        observed[(label, domain, outcome)] += _int(record.get("count"), minimum=1)
    passed = True
    domains: dict[str, dict[str, object]] = {}
    for (label, domain), count in expected.items():
        served = observed[(label, domain, "candidate-served")]
        failures = sum(
            observed[(label, domain, outcome)]
            for outcome in TELEMETRY_OUTCOMES - {"candidate-served"}
        )
        current = served == count and failures == 0
        passed = passed and current
        domains[f"{label}:{domain}"] = {
            "expected": count,
            "candidateServed": served,
            "failureOutcomes": failures,
            "passed": current,
        }
    return {"status": "passed" if passed else "failed", "passed": passed, "domains": domains}


def verify_workers(records: Sequence[Mapping[str, Any]], labels: Sequence[str]) -> dict[str, object]:
    counts: Counter[tuple[str, str, str]] = Counter()
    isolated = True
    for record in records:
        _exact_keys(record, {"kind", "label", "component", "outcome", "count", "isolatedDiagnostic"}, "Worker evidence contains unsafe fields.")
        label = _label(record.get("label"))
        component = str(record.get("component") or "")
        outcome = str(record.get("outcome") or "")
        if (
            label not in labels
            or component not in REQUIRED_WORKER_COMPONENTS
            or outcome not in {"succeeded", "failed"}
        ):
            raise QualificationError("Worker evidence is outside the expected scope.")
        counts[(label, component, outcome)] += _int(record.get("count"), minimum=1)
        isolated = isolated and record.get("isolatedDiagnostic") is True
    passed = isolated and all(
        counts[(label, component, "succeeded")] >= 1
        and counts[(label, component, "failed")] == 0
        for label in labels
        for component in REQUIRED_WORKER_COMPONENTS
    )
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "isolatedDiagnosticTask": isolated,
        "requiredComponents": sorted(REQUIRED_WORKER_COMPONENTS),
        "successfulComponentTopologies": sum(
            counts[(label, component, "succeeded")] >= 1
            for label in labels
            for component in REQUIRED_WORKER_COMPONENTS
        ),
    }


def verify_cron(
    records: Sequence[Mapping[str, Any]],
    labels: Sequence[str],
    *,
    adopted_label: str,
) -> dict[str, object]:
    if adopted_label not in labels:
        raise QualificationError("The adopted cron topology is outside the manifest.")
    evidence: dict[str, Counter[str]] = {
        "platform-scheduler": Counter(),
        "route": Counter(),
        "manual": Counter(),
    }
    authorized = True
    for record in records:
        _exact_keys(record, {"kind", "label", "source", "correlationSha256", "count", "authorized"}, "Cron evidence contains unsafe fields.")
        label = _label(record.get("label"))
        source = str(record.get("source") or "")
        digest = str(record.get("correlationSha256") or "").casefold()
        if (
            label != adopted_label
            or source not in {"platform-scheduler", "route", "manual"}
            or not SHA256_RE.fullmatch(digest)
        ):
            raise QualificationError("Cron evidence is malformed.")
        evidence[source][digest] += _int(record.get("count"), minimum=1)
        authorized = authorized and record.get("authorized") is True
    platform = evidence["platform-scheduler"]
    route = evidence["route"]
    manual = evidence["manual"]
    genuine = bool(
        not manual
        and len(platform) == 1
        and len(route) == 1
        and platform == route
        and next(iter(platform.values()), 0) == 1
    )
    passed = authorized and genuine
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "adoptedLabel": adopted_label,
        "labels": {
            adopted_label: {
                "controlPlaneDeliveries": sum(platform.values()),
                "correlatedRouteDeliveries": sum(route.values()),
                "manualInvocations": sum(manual.values()),
                "genuineScheduledDelivery": genuine,
            }
        },
    }


def verify_queue(
    records: Sequence[Mapping[str, Any]],
    labels: Sequence[str],
) -> dict[str, object]:
    stages: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    redelivery_attempts: set[tuple[str, str, str]] = set()
    for record in records:
        _exact_keys(record, {"kind", "label", "topic", "stage", "identitySha256", "attempt"}, "Queue evidence contains unsafe fields.")
        label = _label(record.get("label"))
        topic = str(record.get("topic") or "")
        stage = str(record.get("stage") or "")
        digest = str(record.get("identitySha256") or "").casefold()
        if label not in labels or topic not in REQUIRED_TOPICS or stage not in {"published", "consumed", "durable-effect", "error"} or not SHA256_RE.fullmatch(digest):
            raise QualificationError("Queue evidence is malformed.")
        attempt = _int(record.get("attempt"), minimum=1)
        stages[(label, topic, stage)][digest] += 1
        if stage == "consumed" and attempt > 1:
            redelivery_attempts.add((label, topic, digest))
    results: dict[str, dict[str, object]] = {}
    passed = True
    for label in labels:
        for topic in sorted(REQUIRED_TOPICS):
            published = stages[(label, topic, "published")]
            consumed = stages[(label, topic, "consumed")]
            effects = stages[(label, topic, "durable-effect")]
            errors = stages[(label, topic, "error")]
            identities = set(published)
            redelivered = {
                identity
                for identity, count in consumed.items()
                if count > 1
                and (label, topic, identity) in redelivery_attempts
            }
            current = bool(
                len(identities) >= 100
                and identities == set(consumed) == set(effects)
                and all(count == 1 for count in published.values())
                and all(count >= 1 for count in consumed.values())
                and all(count == 1 for count in effects.values())
                and redelivered
                and all(effects[identity] == 1 for identity in redelivered)
                and not errors
            )
            passed = passed and current
            results[f"{label}:{topic}"] = {
                "published": sum(published.values()),
                "consumed": sum(consumed.values()),
                "durableEffects": sum(effects.values()),
                "uniqueIdentities": len(identities),
                "redeliveredIdentities": len(redelivered),
                "errors": sum(errors.values()),
                "passed": current,
            }
    return {"status": "passed" if passed else "failed", "passed": passed, "topics": results}


def verify_cold_starts(
    records: Sequence[Mapping[str, Any]], labels: Sequence[str]
) -> tuple[dict[str, object], dict[str, object]]:
    samples: dict[tuple[str, str], list[float]] = defaultdict(list)
    errors: Counter[tuple[str, str]] = Counter()
    for record in records:
        _exact_keys(record, {"kind", "label", "component", "durationMs", "success", "cold"}, "Cold-start evidence contains unsafe fields.")
        label = _label(record.get("label"))
        component = str(record.get("component") or "")
        if (
            label not in labels
            or component
            not in {"api", "analysis-worker", "player-recommendations-worker"}
            or record.get("cold") is not True
        ):
            raise QualificationError("Cold-start evidence is outside the expected scope.")
        if record.get("success") is True:
            samples[(label, component)].append(_number(record.get("durationMs")))
        else:
            errors[(label, component)] += 1
    correctness = True
    latency = True
    results: dict[str, dict[str, object]] = {}
    for component in ("api", "analysis-worker", "player-recommendations-worker"):
        first = samples[(labels[0], component)]
        second = samples[(labels[1], component)]
        complete = len(first) >= 30 and len(second) >= 30
        correctness = correctness and complete and errors[(labels[0], component)] == 0 and errors[(labels[1], component)] == 0
        if not complete:
            latency = False
            results[component] = {"complete": False, "passed": False}
            continue
        p95_delta = _delta(_nearest_rank(first, 0.95), _nearest_rank(second, 0.95))
        max_delta = _delta(max(first), max(second))
        current = p95_delta <= P95_MAX_INCREASE_PERCENT and max_delta <= P99_MAX_INCREASE_PERCENT
        latency = latency and current
        results[component] = {
            "samplesPerLabel": {labels[0]: len(first), labels[1]: len(second)},
            "p95IncreasePercent": p95_delta,
            "maxIncreasePercent": max_delta,
            "passed": current,
        }
    return (
        {"status": "passed" if correctness else "failed", "passed": correctness, "minimumSamplesPerComponentAndLabel": 30},
        {
            "status": "passed" if latency else "failed",
            "passed": latency,
            "complete": correctness,
            "target": {"p95MaximumIncreasePercent": 10.0, "maxMaximumIncreasePercent": 20.0},
            "components": results,
        },
    )


def verify_capacity(
    records: Sequence[Mapping[str, Any]],
    labels: Sequence[str],
    concurrency: Mapping[str, int],
    expected_limits: Mapping[str, int],
) -> dict[str, object]:
    samples: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        _exact_keys(record, {"kind", "label", "activeConnections", "connectionLimit", "connectionErrors", "deadlineErrors"}, "Capacity evidence contains unsafe fields.")
        label = _label(record.get("label"))
        if label not in labels:
            raise QualificationError("Capacity evidence is outside the expected scope.")
        samples[label].append(record)
    results: dict[str, dict[str, object]] = {}
    passed = True
    for label in labels:
        values = samples[label]
        if not values:
            current = False
            peak = 0
            limit = 0
            connection_errors = deadline_errors = 0
        else:
            active = [_int(value.get("activeConnections")) for value in values]
            limits = {_int(value.get("connectionLimit"), minimum=1) for value in values}
            if len(limits) != 1:
                raise QualificationError("Connection-limit evidence is inconsistent.")
            limit = next(iter(limits))
            peak = max(active)
            connection_errors = sum(_int(value.get("connectionErrors")) for value in values)
            deadline_errors = sum(_int(value.get("deadlineErrors")) for value in values)
            current = bool(
                len(values) >= 30
                and limit == expected_limits[label]
                and peak >= concurrency[label]
                and peak <= math.floor(limit * 0.75)
                and connection_errors == 0
                and deadline_errors == 0
            )
        passed = passed and current
        results[label] = {
            "samples": len(values),
            "peakConnections": peak,
            "connectionLimit": limit,
            "minimumHeadroomPercent": 25,
            "connectionErrors": connection_errors,
            "deadlineErrors": deadline_errors,
            "passed": current,
        }
    return {"status": "passed" if passed else "failed", "passed": passed, "labels": results}


def verify_checklist(
    checklist: Mapping[str, Any],
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    _exact_keys(checklist, {"schemaVersion", "faultScenarios", "privateBlobMutation", "rollback", "privacy"}, "The fault/rollback checklist contains unexpected fields.")
    if checklist.get("schemaVersion") != 1:
        raise QualificationError("The fault/rollback checklist schema is unsupported.")
    scenarios = _mapping(checklist.get("faultScenarios"), "Fault scenarios are missing.")
    if set(scenarios) != REQUIRED_FAULT_SCENARIOS:
        raise QualificationError("The complete fault scenario set is required.")
    fault_passed = True
    for value in scenarios.values():
        scenario = _mapping(value, "A fault scenario is malformed.")
        _exact_keys(scenario, {"expectedOutcomeObserved", "dataCorruptionObserved", "passed"}, "A fault scenario contains unexpected fields.")
        fault_passed = fault_passed and bool(
            scenario.get("expectedOutcomeObserved") is True
            and scenario.get("dataCorruptionObserved") is False
            and scenario.get("passed") is True
        )
    rollback = _mapping(checklist.get("rollback"), "Rollback evidence is missing.")
    blob_mutation = _mapping(
        checklist.get("privateBlobMutation"),
        "Private Blob mutation evidence is missing.",
    )
    blob_mutation_keys = {
        "isolatedDiagnostic",
        "jsonWriteReadDeleteExact",
        "binaryWriteReadDeleteExact",
        "failedBundleRetainedPreviousPointer",
        "failedBundleLeftNoPartialPublication",
    }
    _exact_keys(
        blob_mutation,
        blob_mutation_keys,
        "Private Blob mutation evidence contains unexpected fields.",
    )
    blob_mutation_passed = all(
        blob_mutation.get(key) is True for key in blob_mutation_keys
    )
    rollback_keys = {
        "durationSeconds",
        "flagsSafe",
        "apiSmokePassed",
        "workerSmokePassed",
        "exactReconciliationPassed",
        "canaryTelemetryAbsent",
        "dataLossObserved",
    }
    _exact_keys(rollback, rollback_keys, "Rollback evidence contains unexpected fields.")
    duration = _number(rollback.get("durationSeconds"))
    rollback_passed = bool(
        duration <= 300
        and rollback.get("flagsSafe") is True
        and rollback.get("apiSmokePassed") is True
        and rollback.get("workerSmokePassed") is True
        and rollback.get("exactReconciliationPassed") is True
        and rollback.get("canaryTelemetryAbsent") is True
        and rollback.get("dataLossObserved") is False
    )
    privacy = _mapping(checklist.get("privacy"), "Privacy evidence is missing.")
    privacy_keys = {"eventsSanitized", "noRawIdentifiers", "noUrls", "noSecrets"}
    _exact_keys(privacy, privacy_keys, "Privacy evidence contains unexpected fields.")
    privacy_passed = all(privacy.get(key) is True for key in privacy_keys)
    return (
        {"status": "passed" if fault_passed else "failed", "passed": fault_passed, "scenarios": sorted(REQUIRED_FAULT_SCENARIOS)},
        {
            "status": "passed" if blob_mutation_passed else "failed",
            "passed": blob_mutation_passed,
            "isolatedDiagnostic": blob_mutation.get("isolatedDiagnostic") is True,
        },
        {"status": "passed" if rollback_passed else "failed", "passed": rollback_passed, "durationSeconds": duration, "maximumDurationSeconds": 300},
        {"status": "passed" if privacy_passed else "failed", "passed": privacy_passed},
    )


def qualify(
    *,
    manifest: Mapping[str, Any],
    api: Mapping[str, Any],
    blobs: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    checklist: Mapping[str, Any],
    owner_latency_waiver: bool,
) -> dict[str, object]:
    topology, labels, concurrency, connection_limits = verify_topology(manifest)
    api_correctness, api_latency, expected_telemetry = verify_api(api, labels=labels)
    blob_correctness, blob_latency = verify_blob(blobs, labels=labels)
    partitions = _partition_events(events)
    telemetry = verify_telemetry(partitions["telemetry"], expected_telemetry)
    worker = verify_workers(partitions["worker"], labels)
    cron = verify_cron(
        partitions["cron"],
        labels,
        adopted_label=str(topology["adoptedLabel"]),
    )
    queue = verify_queue(partitions["queue"], labels)
    cold_correctness, cold_latency = verify_cold_starts(partitions["cold-start"], labels)
    capacity = verify_capacity(
        partitions["capacity"], labels, concurrency, connection_limits
    )
    faults, blob_mutation, rollback, privacy = verify_checklist(checklist)

    non_latency = {
        "topologyAndSafeFlags": topology,
        "apiCorrectnessAndExactParity": api_correctness,
        "telemetryReconciliation": telemetry,
        "privateBlobCorrectness": blob_correctness,
        "isolatedWorkerExecution": worker,
        "genuineCronDelivery": cron,
        "queuePublishConsume": queue,
        "coldStartCorrectness": cold_correctness,
        "connectionCapacity": capacity,
        "failureHandling": faults,
        "privateBlobMutation": blob_mutation,
        "rollback": rollback,
        "privacy": privacy,
    }
    latency = {
        "api": api_latency,
        "privateBlob": blob_latency,
        "coldStart": cold_latency,
    }
    non_latency_passed = all(gate.get("passed") is True for gate in non_latency.values())
    latency_complete = all(gate.get("complete") is True for gate in latency.values())
    latency_passed = latency_complete and all(gate.get("passed") is True for gate in latency.values())
    waiver_eligible = non_latency_passed and latency_complete and not latency_passed
    waiver_accepted = owner_latency_waiver and waiver_eligible
    if non_latency_passed and latency_passed:
        status = "passed"
    elif waiver_accepted:
        status = "owner-latency-waived"
    else:
        status = "failed"
    return {
        "schemaVersion": 1,
        "status": status,
        "qualifiedForTopologyAdoption": status in {"passed", "owner-latency-waived"},
        "labels": list(labels),
        "nonLatencyGates": non_latency,
        "diagnosticLatencyGates": {
            "status": "passed" if latency_passed else ("failed" if latency_complete else "incomplete"),
            "passed": latency_passed,
            "complete": latency_complete,
            "originalTargetStillReported": True,
            "gates": latency,
        },
        "ownerLatencyWaiver": {
            "requested": owner_latency_waiver,
            "eligible": waiver_eligible,
            "accepted": waiver_accepted,
            "coversOnlyLatency": True,
            "nonLatencyFailuresWaived": False,
        },
        "productionMutationPerformed": False,
    }


def _output(path: Path) -> Path:
    resolved = path.resolve()
    root = LOCAL_DATA.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise QualificationError("The output must be a file below .local-data.")
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topology-manifest", type=Path, required=True)
    parser.add_argument("--api-comparison", type=Path, required=True)
    parser.add_argument("--blob-report", type=Path, action="append", required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--fault-rollback-checklist", type=Path, required=True)
    parser.add_argument("--owner-latency-waiver", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = qualify(
        manifest=_json_mapping(args.topology_manifest),
        api=_json_mapping(args.api_comparison),
        blobs=[_json_mapping(path) for path in args.blob_report],
        events=_jsonl(args.events),
        checklist=_json_mapping(args.fault_rollback_checklist),
        owner_latency_waiver=args.owner_latency_waiver,
    )
    output = _output(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["qualifiedForTopologyAdoption"] else 4


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Pumbility topology qualification failed safely; private details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
