"""Compare two authenticated, protected Pumbility preview deployments safely.

Deployment references are read from named environment variables and are used
only as arguments to ``vercel curl --deployment`` and ``vercel inspect``.  The
persisted evidence contains generic labels, timings, counts, and exact-parity
results, but never deployment identity, URLs, request paths, response bodies,
digests, command output, or secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.compare_pumbility_preview_regions import (
    VALID_DOMAINS,
    compare_latency,
    compare_response_hashes,
    evaluate_latency_gate,
    validate_label,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / ".local-data" / "pumbility-protected-preview-benchmarks"
)
DEFAULT_FIRST_DEPLOYMENT_ENV = "PUMBILITY_FIRST_PREVIEW_DEPLOYMENT"
DEFAULT_SECOND_DEPLOYMENT_ENV = "PUMBILITY_SECOND_PREVIEW_DEPLOYMENT"
DEFAULT_JOB_ID_ENV = "PUMBILITY_PREVIEW_JOB_ID"
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEPLOYMENT_REFERENCE_RE = re.compile(
    r"^(?:dpl_[A-Za-z0-9]+|[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)$"
)
URL_RE = re.compile(r"(?i)https?://|\.vercel\.app\b")
METRIC_MARKER = b"CODEX_PUMBILITY_METRICS_V1\t"
METRIC_FORMAT = (
    "\nCODEX_PUMBILITY_METRICS_V1\t%{http_code}\t%{time_starttransfer}"
    "\t%{time_total}\t%{size_download}\t%{content_type}"
)
TELEMETRY_EVENTS_PER_REQUEST = {
    "analysis": 1,
    "tier-list": 1,
    "recommendation-players": 1,
    "recommendation-player": 2,
    "job-status": 1,
}
CommandRunner = Callable[..., Any]


class ProtectedPreviewProbeError(RuntimeError):
    """Protected-preview evidence could not be produced without weakening a gate."""


@dataclass
class _Sample:
    sanitized: dict[str, object]
    digest: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two protected previews through authenticated Vercel CLI requests."
    )
    parser.add_argument(
        "--first-deployment-env", default=DEFAULT_FIRST_DEPLOYMENT_ENV
    )
    parser.add_argument(
        "--second-deployment-env", default=DEFAULT_SECOND_DEPLOYMENT_ENV
    )
    parser.add_argument("--first-label", required=True)
    parser.add_argument("--second-label", required=True)
    parser.add_argument(
        "--domain",
        action="append",
        choices=VALID_DOMAINS,
        required=True,
        dest="domains",
    )
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--warmup-samples", type=int, default=3)
    parser.add_argument("--window-minutes", type=float, default=15.0)
    parser.add_argument("--skip-p99", action="store_true")
    parser.add_argument("--expect-canary-telemetry", action="store_true")
    parser.add_argument("--job-id-env", default=DEFAULT_JOB_ID_ENV)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def _validated_output_root(path: Path) -> Path:
    resolved = path.resolve()
    local_root = (PROJECT_ROOT / ".local-data").resolve()
    if resolved != local_root and not resolved.is_relative_to(local_root):
        raise ValueError("Protected-preview evidence must remain under .local-data.")
    return resolved


def _environment_value(name: str, *, description: str) -> str:
    if not ENV_NAME_RE.fullmatch(name):
        raise ValueError(f"The {description} environment-variable name is invalid.")
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"The {description} environment variable is empty.")
    return value


def _deployment_reference_from_environment(name: str, *, ordinal: str) -> str:
    value = _environment_value(name, description=f"{ordinal} deployment")
    if not DEPLOYMENT_REFERENCE_RE.fullmatch(value):
        raise ValueError(f"The {ordinal} deployment reference is invalid.")
    return value


def _run_command(
    command: Sequence[str],
    *,
    command_runner: CommandRunner,
    timeout: float,
) -> Any:
    try:
        return command_runner(
            list(command),
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except Exception:
        raise ProtectedPreviewProbeError(
            "An authenticated preview command failed safely."
        ) from None


def _attest_preview_deployment(
    *,
    vercel_cli: str,
    deployment: str,
    label: str,
    command_runner: CommandRunner,
) -> None:
    completed = _run_command(
        (
            vercel_cli,
            "inspect",
            deployment,
            "--no-color",
            "--non-interactive",
            "--timeout",
            "10s",
        ),
        command_runner=command_runner,
        timeout=30.0,
    )
    output = bytes(completed.stdout or b"") + bytes(completed.stderr or b"")
    if completed.returncode != 0 or not re.search(rb"(?i)\bpreview\b", output):
        raise ProtectedPreviewProbeError(
            "A deployment could not be attested as a Preview deployment."
        )
    if label in {"iad1", "cle1"} and not re.search(
        rb"(?i)(?<![a-z0-9])" + label.encode("ascii") + rb"(?![a-z0-9])",
        output,
    ):
        raise ProtectedPreviewProbeError(
            "A deployment did not attest the requested region label."
        )


def _safe_header(header_bytes: bytes, name: str) -> str | None:
    matches = re.findall(
        rb"(?im)^" + re.escape(name.encode("ascii")) + rb":\s*([^\r\n]+)",
        header_bytes,
    )
    if not matches:
        return None
    return matches[-1].decode("ascii", errors="ignore").strip()


def _parse_metrics(stdout: bytes) -> tuple[int, float, float, int, str]:
    if stdout.count(METRIC_MARKER) != 1:
        raise ProtectedPreviewProbeError(
            "Vercel curl did not provide an exact timing decomposition."
        )
    fields = stdout.rsplit(METRIC_MARKER, 1)[1].splitlines()[0].split(b"\t")
    if len(fields) != 5:
        raise ProtectedPreviewProbeError(
            "Vercel curl returned malformed timing evidence."
        )
    try:
        status = int(fields[0])
        ttfb_seconds = float(fields[1])
        total_seconds = float(fields[2])
        wire_bytes = int(float(fields[3]))
        content_type = fields[4].decode("ascii", errors="ignore").strip()
    except (ValueError, OverflowError):
        raise ProtectedPreviewProbeError(
            "Vercel curl returned malformed timing evidence."
        ) from None
    numbers = (ttfb_seconds, total_seconds)
    if (
        any(not math.isfinite(value) or value < 0 for value in numbers)
        or total_seconds < ttfb_seconds
        or wire_bytes < 0
    ):
        raise ProtectedPreviewProbeError(
            "Vercel curl returned invalid timing evidence."
        )
    return status, ttfb_seconds, total_seconds, wire_bytes, content_type


def _nonce_path(path: str) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}probeNonce={uuid.uuid4().hex}"


def _invoke_request(
    *,
    vercel_cli: str,
    deployment: str,
    label: str,
    domain: str,
    path: str,
    phase: str,
    sample_index: int,
    expect_canary_telemetry: bool,
    command_runner: CommandRunner,
) -> tuple[_Sample, Mapping[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="pumbility-protected-preview-") as temporary:
        temporary_root = Path(temporary)
        body_path = temporary_root / "body.bin"
        header_path = temporary_root / "headers.txt"
        command = [
            vercel_cli,
            "curl",
            _nonce_path(path),
            "--deployment",
            deployment,
            "--",
            "--silent",
            "--show-error",
            "--compressed",
            "--no-progress-meter",
            "--connect-timeout",
            "15",
            "--max-time",
            "90",
            "--header",
            "Accept: application/json",
            "--header",
            "Cache-Control: no-cache, no-store, max-age=0",
            "--header",
            "Pragma: no-cache",
            "--output",
            str(body_path),
            "--dump-header",
            str(header_path),
            "--write-out",
            METRIC_FORMAT,
        ]
        completed = _run_command(
            command, command_runner=command_runner, timeout=120.0
        )
        if completed.returncode != 0:
            raise ProtectedPreviewProbeError(
                "An authenticated preview request failed safely."
            )
        status, ttfb_seconds, total_seconds, wire_bytes, content_type = (
            _parse_metrics(bytes(completed.stdout or b""))
        )
        if status != 200:
            raise ProtectedPreviewProbeError(
                "An authenticated preview request returned an unsuccessful status."
            )
        if not body_path.is_file() or not header_path.is_file():
            raise ProtectedPreviewProbeError(
                "An authenticated preview request omitted required evidence."
            )
        body = body_path.read_bytes()
        if not body or not re.match(r"(?i)^application/json(?:;|$)", content_type):
            raise ProtectedPreviewProbeError(
                "An authenticated preview request did not return JSON."
            )
        try:
            body_text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise ProtectedPreviewProbeError(
                "An authenticated preview response contained invalid JSON."
            ) from None
        parse_started = time.perf_counter()
        try:
            parsed = json.loads(body_text)
        except json.JSONDecodeError:
            raise ProtectedPreviewProbeError(
                "An authenticated preview response contained invalid JSON."
            ) from None
        parse_ms = (time.perf_counter() - parse_started) * 1000.0
        if not isinstance(parsed, Mapping):
            raise ProtectedPreviewProbeError(
                "An authenticated preview response was not a JSON object."
            )
        headers = header_path.read_bytes()
        content_encoding = (_safe_header(headers, "Content-Encoding") or "none").lower()
        cache_value = (_safe_header(headers, "x-vercel-cache") or "absent").upper()
        if cache_value not in {"ABSENT", "BYPASS", "MISS", "HIT", "STALE", "PRERENDER"}:
            cache_value = "OTHER"
        ttfb_ms = ttfb_seconds * 1000.0
        network_total_ms = total_seconds * 1000.0
        expected_events = (
            TELEMETRY_EVENTS_PER_REQUEST[domain] if expect_canary_telemetry else 0
        )
        record: dict[str, object] = {
            "deploymentLabel": label,
            "domain": domain,
            "phase": phase,
            "sampleIndex": sample_index,
            "ok": True,
            "httpStatus": status,
            "ttfbMs": round(ttfb_ms, 3),
            "downloadMs": round(max(0.0, network_total_ms - ttfb_ms), 3),
            "networkTotalMs": round(network_total_ms, 3),
            "jsonParseMs": round(parse_ms, 3),
            "endToEndMs": round(network_total_ms + parse_ms, 3),
            "wireBytes": wire_bytes,
            "decodedBodyBytes": len(body),
            "contentEncoding": content_encoding,
            "vercelCache": cache_value,
            "compressionRequested": True,
            "cacheBypassRequested": True,
            "cacheBypassSatisfied": cache_value != "HIT",
            "candidateTelemetryExpected": expect_canary_telemetry,
            "expectedCandidateReadEvents": expected_events,
        }
        return (
            _Sample(record, hashlib.sha256(body).hexdigest()),
            parsed,
        )


def _percentile(records: Sequence[_Sample], property_name: str, value: float) -> float:
    numbers = sorted(float(record.sanitized[property_name]) for record in records)
    if not numbers:
        raise ProtectedPreviewProbeError("A latency percentile had no samples.")
    index = max(0, math.ceil(value * len(numbers)) - 1)
    return round(numbers[index], 3)


def _metric_summary(
    records: Sequence[_Sample], property_name: str, *, p99_scored: bool
) -> dict[str, float | None]:
    return {
        "p50": _percentile(records, property_name, 0.50),
        "p95": _percentile(records, property_name, 0.95),
        "p99": _percentile(records, property_name, 0.99) if p99_scored else None,
        "max": _percentile(records, property_name, 1.0),
    }


def _summary_for_label(
    *,
    label: str,
    samples: Sequence[_Sample],
    domains: Sequence[str],
    scored_samples: int,
    warmup_samples: int,
    window_minutes: float,
    elapsed_scored_minutes: float,
    skip_p99: bool,
    expect_canary_telemetry: bool,
) -> dict[str, object]:
    label_samples = [
        sample for sample in samples if sample.sanitized["deploymentLabel"] == label
    ]
    p99_scored = not skip_p99 and scored_samples >= 100
    results: dict[str, object] = {}
    for domain in domains:
        domain_samples = [
            sample
            for sample in label_samples
            if sample.sanitized["domain"] == domain
        ]
        scored = [
            sample
            for sample in domain_samples
            if sample.sanitized["phase"] == "scored"
        ]
        warmups = [
            sample
            for sample in domain_samples
            if sample.sanitized["phase"] == "warmup"
        ]
        if len(scored) != scored_samples or len(warmups) != warmup_samples:
            raise ProtectedPreviewProbeError("A protected preview sample set is incomplete.")
        results[domain] = {
            "scoredAttempts": len(scored),
            "scoredSuccesses": len(scored),
            "scoredErrors": 0,
            "warmupAttempts": len(warmups),
            "warmupErrors": 0,
            "cacheHits": sum(
                sample.sanitized["vercelCache"] == "HIT" for sample in scored
            ),
            "gzipResponses": sum(
                sample.sanitized["contentEncoding"] == "gzip" for sample in scored
            ),
            "p99Scored": p99_scored,
            "expectedCandidateReadEvents": sum(
                int(sample.sanitized["expectedCandidateReadEvents"])
                for sample in domain_samples
            ),
            "telemetryCountGate": (
                "pending-server-log-reconciliation"
                if expect_canary_telemetry
                else "not-applicable-baseline"
            ),
            "endToEndMs": _metric_summary(scored, "endToEndMs", p99_scored=p99_scored),
            "ttfbMs": _metric_summary(scored, "ttfbMs", p99_scored=p99_scored),
            "downloadMs": _metric_summary(scored, "downloadMs", p99_scored=p99_scored),
            "jsonParseMs": _metric_summary(scored, "jsonParseMs", p99_scored=p99_scored),
        }
    return {
        "deploymentLabel": label,
        "domains": list(domains),
        "scoredSamplesPerDomain": scored_samples,
        "warmupSamplesPerDomain": warmup_samples,
        "requestedWindowMinutes": window_minutes,
        "elapsedScoredMinutes": round(elapsed_scored_minutes, 3),
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
            "expected": expect_canary_telemetry,
            "countGateComplete": not expect_canary_telemetry,
            "expectedCandidateReadEventsTotal": sum(
                int(sample.sanitized["expectedCandidateReadEvents"])
                for sample in label_samples
            ),
            "requirement": (
                "Reconcile the exact expected count in server logs; every event must be "
                "candidate-served, with zero errors, fallbacks, or mismatches."
                if expect_canary_telemetry
                else "Not applicable to this control run."
            ),
        },
        "results": results,
    }


def _comparison_records(samples: Sequence[_Sample], *, label: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for sample in samples:
        if sample.sanitized["deploymentLabel"] != label:
            continue
        records.append(
            {
                "ok": True,
                "domain": sample.sanitized["domain"],
                "phase": sample.sanitized["phase"],
                "sampleIndex": sample.sanitized["sampleIndex"],
                "responseSha256": sample.digest,
            }
        )
    return records


def _mark_pair_parity(
    samples: Sequence[_Sample], *, first_label: str, second_label: str
) -> None:
    digests = {
        (
            str(sample.sanitized["deploymentLabel"]),
            str(sample.sanitized["domain"]),
            str(sample.sanitized["phase"]),
            int(sample.sanitized["sampleIndex"]),
        ): sample.digest
        for sample in samples
    }
    for sample in samples:
        label = str(sample.sanitized["deploymentLabel"])
        peer_label = second_label if label == first_label else first_label
        peer = digests.get(
            (
                peer_label,
                str(sample.sanitized["domain"]),
                str(sample.sanitized["phase"]),
                int(sample.sanitized["sampleIndex"]),
            )
        )
        sample.sanitized["exactPairMatch"] = peer is not None and peer == sample.digest


def _assert_sanitized(
    encoded: str,
    *,
    forbidden_values: Sequence[str],
    digests: Sequence[str],
) -> None:
    if URL_RE.search(encoded) or re.search(r"\bdpl_[A-Za-z0-9]+\b", encoded):
        raise ProtectedPreviewProbeError("Sanitized evidence retained endpoint identity.")
    if any(value and value in encoded for value in (*forbidden_values, *digests)):
        raise ProtectedPreviewProbeError("Sanitized evidence retained private data.")
    if re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", encoded, re.I):
        raise ProtectedPreviewProbeError("Sanitized evidence retained a response digest.")


def run_comparison(
    *,
    first_deployment: str,
    first_label: str,
    second_deployment: str,
    second_label: str,
    domains: Sequence[str],
    scored_samples: int,
    warmup_samples: int,
    window_minutes: float,
    skip_p99: bool,
    expect_canary_telemetry: bool,
    job_id: str,
    output_root: Path,
    vercel_cli: str,
    command_runner: CommandRunner | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[int, dict[str, object], Path]:
    if command_runner is None:
        command_runner = subprocess.run
    first_label = validate_label(first_label)
    second_label = validate_label(second_label)
    if first_deployment == second_deployment:
        raise ValueError("Protected-preview deployment references must be different.")
    if first_label == second_label:
        raise ValueError("Protected-preview labels must be different.")
    if not domains or len(set(domains)) != len(domains):
        raise ValueError("Each protected-preview domain must be supplied exactly once.")
    if any(domain not in VALID_DOMAINS for domain in domains):
        raise ValueError("A protected-preview domain is invalid.")
    if scored_samples < 1 or warmup_samples < 0 or window_minutes < 0:
        raise ValueError("Samples and timing values must be non-negative and complete.")
    if not skip_p99 and scored_samples < 100:
        raise ValueError("P99 comparisons require at least 100 scored samples per domain.")
    if "job-status" in domains and not job_id:
        raise ValueError("The job-status domain requires a current job ID environment value.")
    output_root = _validated_output_root(output_root)
    _attest_preview_deployment(
        vercel_cli=vercel_cli,
        deployment=first_deployment,
        label=first_label,
        command_runner=command_runner,
    )
    _attest_preview_deployment(
        vercel_cli=vercel_cli,
        deployment=second_deployment,
        label=second_label,
        command_runner=command_runner,
    )

    variants = ((first_label, first_deployment), (second_label, second_deployment))
    paths: dict[str, dict[str, str]] = {
        label: {
            "analysis": "/api/analyze?mix=phoenix2",
            "tier-list": "/api/tier-list?mix=phoenix2",
            "recommendation-players": "/api/recommendations/players",
            "job-status": f"/api/analyze?mix=phoenix2&jobId={quote(job_id, safe='')}",
        }
        for label, _ in variants
    }
    samples: list[_Sample] = []
    if "recommendation-player" in domains:
        for label, deployment in variants:
            discovery, payload = _invoke_request(
                vercel_cli=vercel_cli,
                deployment=deployment,
                label=label,
                domain="recommendation-players",
                path=paths[label]["recommendation-players"],
                phase="discovery",
                sample_index=0,
                expect_canary_telemetry=expect_canary_telemetry,
                command_runner=command_runner,
            )
            players = payload.get("players")
            player_key = (
                str(players[0].get("playerKey") or "")
                if isinstance(players, list)
                and players
                and isinstance(players[0], Mapping)
                else ""
            )
            if not player_key:
                raise ProtectedPreviewProbeError(
                    "Protected-preview player discovery returned no public key."
                )
            paths[label]["recommendation-player"] = (
                f"/api/recommendations?playerKey={quote(player_key, safe='')}"
            )
            samples.append(discovery)

    for warmup in range(1, warmup_samples + 1):
        for domain in domains:
            for label, deployment in variants:
                sample, _ = _invoke_request(
                    vercel_cli=vercel_cli,
                    deployment=deployment,
                    label=label,
                    domain=domain,
                    path=paths[label][domain],
                    phase="warmup",
                    sample_index=warmup,
                    expect_canary_telemetry=expect_canary_telemetry,
                    command_runner=command_runner,
                )
                samples.append(sample)

    scored_started = clock()
    for index in range(1, scored_samples + 1):
        if index > 1 and scored_samples > 1 and window_minutes > 0:
            target = (
                (index - 1) * window_minutes * 60.0 / (scored_samples - 1)
            )
            remaining = scored_started + target - clock()
            if remaining > 0:
                sleeper(remaining)
        ordered_variants = variants if index % 2 else tuple(reversed(variants))
        for domain in domains:
            for label, deployment in ordered_variants:
                sample, _ = _invoke_request(
                    vercel_cli=vercel_cli,
                    deployment=deployment,
                    label=label,
                    domain=domain,
                    path=paths[label][domain],
                    phase="scored",
                    sample_index=index,
                    expect_canary_telemetry=expect_canary_telemetry,
                    command_runner=command_runner,
                )
                samples.append(sample)
    elapsed_minutes = (clock() - scored_started) / 60.0

    first_summary = _summary_for_label(
        label=first_label,
        samples=samples,
        domains=domains,
        scored_samples=scored_samples,
        warmup_samples=warmup_samples,
        window_minutes=window_minutes,
        elapsed_scored_minutes=elapsed_minutes,
        skip_p99=skip_p99,
        expect_canary_telemetry=expect_canary_telemetry,
    )
    second_summary = _summary_for_label(
        label=second_label,
        samples=samples,
        domains=domains,
        scored_samples=scored_samples,
        warmup_samples=warmup_samples,
        window_minutes=window_minutes,
        elapsed_scored_minutes=elapsed_minutes,
        skip_p99=skip_p99,
        expect_canary_telemetry=expect_canary_telemetry,
    )
    parity = compare_response_hashes(
        _comparison_records(samples, label=first_label),
        _comparison_records(samples, label=second_label),
    )
    _mark_pair_parity(
        samples, first_label=first_label, second_label=second_label
    )
    latency = compare_latency(
        first_summary,
        second_summary,
        domains=domains,
        first_label=first_label,
        second_label=second_label,
    )
    latency_gate = evaluate_latency_gate(
        latency, domains=domains, p99_scored=not skip_p99
    )
    cache_gate = all(
        sample.sanitized["vercelCache"] != "HIT" for sample in samples
    )
    if not parity["passed"] or not cache_gate:
        status = "failed"
    elif latency_gate["status"] == "passed":
        status = "passed"
    elif latency_gate["status"] == "not-scored":
        status = "smoke-passed"
    else:
        status = "failed"
    report: dict[str, object] = {
        "schemaVersion": 1,
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "comparisonKind": "authenticated-protected-preview",
        "probeConfiguration": {
            "domains": list(domains),
            "samples": scored_samples,
            "warmupSamples": warmup_samples,
            "windowMinutes": window_minutes,
            "p99Scored": not skip_p99,
            "canaryTelemetryExpected": expect_canary_telemetry,
            "authenticatedWithVercelCli": True,
            "bypassTokenUsed": False,
            "timingSemantics": {
                "ttfbAndDownload": "curl request timing after authenticated CLI setup",
                "jsonParse": "local decoded-body JSON parse timing",
                "endToEnd": "curl network total plus local JSON parse",
                "cliStartupIncluded": False,
            },
        },
        "deployments": [first_summary, second_summary],
        "responseParity": parity,
        "latencyComparison": latency,
        "latencyGate": latency_gate,
        "cacheBypassGatePassed": cache_gate,
        "telemetryCountGateComplete": not expect_canary_telemetry,
        "identityDisclosure": {
            "deploymentReferencesPrintedOrStored": False,
            "urlsOrHostsPrintedOrStored": False,
            "responseHashesPrintedOrStored": False,
            "responseBodiesPrintedOrStored": False,
            "requestPathsOrQueryValuesPrintedOrStored": False,
            "commandOutputOrErrorsPrintedOrStored": False,
            "secretsPrintedOrStored": False,
        },
        "adoptionDecision": "pending",
    }
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_directory = output_root / f"protected-preview-comparison-{run_id}"
    run_directory.mkdir(exist_ok=False)
    report_text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    sample_text = "".join(
        json.dumps(sample.sanitized, ensure_ascii=False, sort_keys=True) + "\n"
        for sample in samples
    )
    forbidden = (first_deployment, second_deployment, job_id)
    digests = tuple(sample.digest for sample in samples)
    _assert_sanitized(report_text, forbidden_values=forbidden, digests=digests)
    _assert_sanitized(sample_text, forbidden_values=forbidden, digests=digests)
    (run_directory / "comparison.json").write_text(report_text, encoding="utf-8")
    (run_directory / "samples.jsonl").write_text(sample_text, encoding="utf-8")
    return (0 if status in {"passed", "smoke-passed"} else 4), report, run_directory


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    first_label = validate_label(args.first_label)
    second_label = validate_label(args.second_label)
    first_deployment = _deployment_reference_from_environment(
        args.first_deployment_env, ordinal="first"
    )
    second_deployment = _deployment_reference_from_environment(
        args.second_deployment_env, ordinal="second"
    )
    job_id = ""
    if "job-status" in args.domains:
        job_id = _environment_value(args.job_id_env, description="job ID")
    vercel_cli = shutil.which("vercel.cmd") or shutil.which("vercel")
    if not vercel_cli:
        raise ProtectedPreviewProbeError(
            "The authenticated Vercel CLI is required for protected previews."
        )
    status, report, _ = run_comparison(
        first_deployment=first_deployment,
        first_label=first_label,
        second_deployment=second_deployment,
        second_label=second_label,
        domains=tuple(args.domains),
        scored_samples=args.samples,
        warmup_samples=args.warmup_samples,
        window_minutes=args.window_minutes,
        skip_p99=args.skip_p99,
        expect_canary_telemetry=args.expect_canary_telemetry,
        job_id=job_id,
        output_root=args.output_root,
        vercel_cli=vercel_cli,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return status


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Protected Pumbility preview comparison failed safely; "
            "private details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
