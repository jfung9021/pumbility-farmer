"""Compare two read-only Pumbility preview deployments with identical probes.

This tool never deploys, edits flags, or writes application/database data.  It
accepts two explicit preview URLs, runs the tracked latency probe for each, and
stores only sanitized endpoint-label, exact-response-parity, and timing evidence
under ``.local-data``.  URLs, hosts, response hashes, bodies, and subprocess
errors are not emitted in the report.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROBE = PROJECT_ROOT / "scripts" / "probe_pumbility_read_domains.ps1"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / ".local-data" / "pumbility-region-benchmarks"
PRODUCTION_HOST_ALIASES = frozenset({"pumbility-farmer.vercel.app"})
VALID_DOMAINS = (
    "analysis",
    "tier-list",
    "recommendation-players",
    "recommendation-player",
    "job-status",
)
LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PreviewComparisonError(RuntimeError):
    """The preview comparison could not produce safe, complete evidence."""


def _is_loopback(hostname: str) -> bool:
    return hostname.casefold() in {"localhost", "127.0.0.1", "::1"}


def validate_preview_url(value: str) -> str:
    """Accept an origin-only HTTPS preview URL, plus HTTP loopback for tests."""
    parsed = urlsplit(value)
    hostname = str(parsed.hostname or "").casefold().rstrip(".")
    if not hostname or parsed.username or parsed.password:
        raise ValueError("A preview URL must contain a host and no credentials.")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and _is_loopback(hostname)
    ):
        raise ValueError("Preview comparisons require HTTPS endpoints.")
    if hostname in PRODUCTION_HOST_ALIASES:
        raise ValueError("The production host alias cannot be benchmarked by this tool.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("A preview URL must be an origin without a path, query, or fragment.")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("The preview URL has an invalid port.") from error
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        authority += f":{port}"
    return f"{parsed.scheme}://{authority}"


def validate_label(value: str) -> str:
    normalized = value.strip().casefold()
    if not LABEL_RE.fullmatch(normalized):
        raise ValueError(
            "Region labels must start with a letter and contain only lowercase "
            "letters, digits, underscores, or hyphens."
        )
    return normalized


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-url", required=True)
    parser.add_argument("--first-label", required=True)
    parser.add_argument("--second-url", required=True)
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
    parser.add_argument("--job-id", default="")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def _validated_output_root(path: Path) -> Path:
    resolved = path.resolve()
    local_root = (PROJECT_ROOT / ".local-data").resolve()
    if resolved != local_root and not resolved.is_relative_to(local_root):
        raise ValueError("Preview benchmark evidence must remain under .local-data.")
    return resolved


def _read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise PreviewComparisonError("A probe summary is malformed.")
    return value


def _read_records(path: Path) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise PreviewComparisonError("A probe sample is malformed.")
        records.append(value)
    return records


def _run_probe(
    *,
    base_url: str,
    label: str,
    domains: Sequence[str],
    samples: int,
    warmup_samples: int,
    window_minutes: float,
    skip_p99: bool,
    expect_canary_telemetry: bool,
    job_id: str,
    run_directory: Path,
    command_runner: Any = subprocess.run,
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    output_directory = run_directory / label
    output_directory.mkdir(parents=True, exist_ok=False)
    command = [
        "powershell.exe",
        "-NoProfile",
        "-File",
        str(PROBE),
        "-DomainCsv",
        ",".join(domains),
        "-Samples",
        str(samples),
        "-WarmupSamples",
        str(warmup_samples),
        "-WindowMinutes",
        str(window_minutes),
        "-BaseUrl",
        base_url,
        "-OutputDirectory",
        str(output_directory),
        "-SuppressBaseHost",
    ]
    if skip_p99:
        command.append("-SkipP99")
    if expect_canary_telemetry:
        command.append("-ExpectCanaryTelemetry")
    if job_id:
        command.extend(("-JobId", job_id))
    # The timeout bounds the probe but is deliberately generous enough for the
    # probe's own 90-second per-request limit plus the requested observation window.
    timeout_seconds = max(
        120.0,
        window_minutes * 60.0
        + (samples + warmup_samples + 1) * len(domains) * 90.0
        + 120.0,
    )
    completed = command_runner(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise PreviewComparisonError(f"The {label} preview probe failed safely.")
    summaries = list(output_directory.glob("*-summary.json"))
    sample_files = list(output_directory.glob("*-samples.jsonl"))
    if len(summaries) != 1 or len(sample_files) != 1:
        raise PreviewComparisonError(f"The {label} preview probe evidence is incomplete.")
    summary = _read_json(summaries[0])
    if summary.get("baseHost") is not None:
        raise PreviewComparisonError("A preview probe retained endpoint identity.")
    if (
        tuple(summary.get("domains") or ()) != tuple(domains)
        or summary.get("scoredSamplesPerDomain") != samples
        or summary.get("warmupSamplesPerDomain") != warmup_samples
        or _finite_number(summary.get("requestedWindowMinutes")) != window_minutes
        or summary.get("compressionRequested") is not True
    ):
        raise PreviewComparisonError("A preview probe did not use the requested configuration.")
    telemetry = summary.get("telemetry")
    if not isinstance(telemetry, Mapping) or telemetry.get("expected") is not bool(
        expect_canary_telemetry
    ):
        raise PreviewComparisonError("A preview probe telemetry mode is inconsistent.")
    return summary, _read_records(sample_files[0])


def _response_index(
    records: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, int], str]:
    indexed: dict[tuple[str, str, int], str] = {}
    for record in records:
        if not record.get("ok"):
            raise PreviewComparisonError("A preview probe contains an unsuccessful request.")
        domain = str(record.get("domain") or "")
        phase = str(record.get("phase") or "")
        sample_index = record.get("sampleIndex")
        digest = str(record.get("responseSha256") or "")
        if (
            domain not in VALID_DOMAINS
            or phase not in {"discovery", "warmup", "scored"}
            or isinstance(sample_index, bool)
            or not isinstance(sample_index, int)
            or not SHA256_RE.fullmatch(digest)
        ):
            raise PreviewComparisonError("A preview response identity is malformed.")
        key = (domain, phase, sample_index)
        if key in indexed:
            raise PreviewComparisonError("A preview probe contains duplicate response evidence.")
        indexed[key] = digest
    return indexed


def compare_response_hashes(
    first: Sequence[Mapping[str, Any]],
    second: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    """Compare exact body identities without returning either digest."""
    first_index = _response_index(first)
    second_index = _response_index(second)
    keys = sorted(set(first_index) | set(second_index))
    domains: dict[str, dict[str, object]] = {}
    for domain in sorted({key[0] for key in keys}):
        domain_keys = [key for key in keys if key[0] == domain]
        missing = sum(
            key not in first_index or key not in second_index for key in domain_keys
        )
        mismatches = sum(
            key in first_index
            and key in second_index
            and first_index[key] != second_index[key]
            for key in domain_keys
        )
        exact = len(domain_keys) - missing - mismatches
        domains[domain] = {
            "comparedResponses": len(domain_keys),
            "exactMatches": exact,
            "mismatches": mismatches,
            "missingPairs": missing,
            "passed": len(domain_keys) > 0 and missing == 0 and mismatches == 0,
        }
    return {
        "passed": bool(domains) and all(value["passed"] for value in domains.values()),
        "domains": domains,
    }


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _delta_percent(first: float | None, second: float | None) -> float | None:
    if first is None or second is None or first <= 0:
        return None
    return round(((second - first) / first) * 100.0, 3)


def compare_latency(
    first_summary: Mapping[str, Any],
    second_summary: Mapping[str, Any],
    *,
    domains: Sequence[str],
    first_label: str,
    second_label: str,
) -> dict[str, object]:
    first_results = first_summary.get("results")
    second_results = second_summary.get("results")
    if not isinstance(first_results, Mapping) or not isinstance(second_results, Mapping):
        raise PreviewComparisonError("A preview latency summary is incomplete.")
    comparison: dict[str, object] = {}
    for domain in domains:
        first_domain = first_results.get(domain)
        second_domain = second_results.get(domain)
        if not isinstance(first_domain, Mapping) or not isinstance(second_domain, Mapping):
            raise PreviewComparisonError("A preview domain latency summary is missing.")
        domain_result: dict[str, object] = {}
        for metric in ("ttfbMs", "downloadMs", "jsonParseMs", "endToEndMs"):
            first_metric = first_domain.get(metric)
            second_metric = second_domain.get(metric)
            if not isinstance(first_metric, Mapping) or not isinstance(second_metric, Mapping):
                raise PreviewComparisonError("A preview latency metric is missing.")
            percentiles: dict[str, object] = {}
            for percentile in ("p50", "p95", "p99"):
                first_value = _finite_number(first_metric.get(percentile))
                second_value = _finite_number(second_metric.get(percentile))
                percentiles[percentile] = {
                    first_label: first_value,
                    second_label: second_value,
                    "secondVsFirstPercent": _delta_percent(first_value, second_value),
                }
            domain_result[metric] = percentiles
        comparison[domain] = domain_result
    return comparison


def _sanitized_probe_summary(
    summary: Mapping[str, Any], *, label: str
) -> dict[str, object]:
    return {
        "deploymentRegionLabel": label,
        "domains": summary.get("domains"),
        "scoredSamplesPerDomain": summary.get("scoredSamplesPerDomain"),
        "warmupSamplesPerDomain": summary.get("warmupSamplesPerDomain"),
        "requestedWindowMinutes": summary.get("requestedWindowMinutes"),
        "elapsedScoredMinutes": summary.get("elapsedScoredMinutes"),
        "compressionRequested": summary.get("compressionRequested"),
        "cacheBypass": summary.get("cacheBypass"),
        "telemetry": summary.get("telemetry"),
        "results": summary.get("results"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    first_url = validate_preview_url(args.first_url)
    second_url = validate_preview_url(args.second_url)
    if first_url == second_url:
        raise ValueError("The two preview origins must be different.")
    first_label = validate_label(args.first_label)
    second_label = validate_label(args.second_label)
    if first_label == second_label:
        raise ValueError("The two preview region labels must be different.")
    domains = tuple(args.domains)
    if len(set(domains)) != len(domains):
        raise ValueError("Each preview probe domain may be specified only once.")
    if args.samples < 1 or args.warmup_samples < 0 or args.window_minutes < 0:
        raise ValueError("Samples and timing values must be non-negative and complete.")
    if not args.skip_p99 and args.samples < 100:
        raise ValueError("P99 comparisons require at least 100 scored samples per domain.")
    job_id = args.job_id.strip()
    if "job-status" in domains and not job_id:
        raise ValueError("The job-status domain requires --job-id for a current job.")

    output_root = _validated_output_root(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_directory = output_root / f"preview-region-comparison-{timestamp}"
    run_directory.mkdir(exist_ok=False)

    first_summary, first_records = _run_probe(
        base_url=first_url,
        label=first_label,
        domains=domains,
        samples=args.samples,
        warmup_samples=args.warmup_samples,
        window_minutes=args.window_minutes,
        skip_p99=args.skip_p99,
        expect_canary_telemetry=args.expect_canary_telemetry,
        job_id=job_id,
        run_directory=run_directory,
    )
    second_summary, second_records = _run_probe(
        base_url=second_url,
        label=second_label,
        domains=domains,
        samples=args.samples,
        warmup_samples=args.warmup_samples,
        window_minutes=args.window_minutes,
        skip_p99=args.skip_p99,
        expect_canary_telemetry=args.expect_canary_telemetry,
        job_id=job_id,
        run_directory=run_directory,
    )
    parity = compare_response_hashes(first_records, second_records)
    report = {
        "schemaVersion": 1,
        "status": "passed" if parity["passed"] else "failed",
        "comparisonKind": "read-only-preview-region",
        "probeConfiguration": {
            "domains": domains,
            "samples": args.samples,
            "warmupSamples": args.warmup_samples,
            "windowMinutes": args.window_minutes,
            "p99Scored": not args.skip_p99,
            "canaryTelemetryExpected": args.expect_canary_telemetry,
        },
        "deployments": [
            _sanitized_probe_summary(first_summary, label=first_label),
            _sanitized_probe_summary(second_summary, label=second_label),
        ],
        "responseParity": parity,
        "latencyComparison": compare_latency(
            first_summary,
            second_summary,
            domains=domains,
            first_label=first_label,
            second_label=second_label,
        ),
        "identityDisclosure": {
            "urlsPrinted": False,
            "hostsPrinted": False,
            "responseHashesPrinted": False,
            "responseBodiesPrinted": False,
        },
        "adoptionDecision": "pending",
        "requiredBeforeRegionAdoption": [
            "worker",
            "blob",
            "cron",
            "queue",
            "cold-start",
            "connection-capacity",
            "failure-and-rollback",
        ],
    }
    report_path = run_directory / "comparison.json"
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    report_path.write_text(encoded, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if parity["passed"] else 4


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Pumbility preview-region comparison failed safely; endpoint and private details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
