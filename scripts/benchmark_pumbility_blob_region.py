"""Run a read-only private-Blob benchmark inside one qualified topology.

The target file contains private URLs and expected SHA-256 values and therefore
belongs in ignored local evidence.  Results contain only generic artifact names,
aggregate latency, exact-match counts, and the platform-attested region label.
No Blob URL, token, response body, or digest is emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA = PROJECT_ROOT / ".local-data"
LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class BlobBenchmarkError(RuntimeError):
    """The benchmark configuration or evidence is unsafe or incomplete."""


def _label(value: object, field: str = "label") -> str:
    normalized = str(value or "").strip().casefold()
    if not LABEL_RE.fullmatch(normalized):
        raise BlobBenchmarkError(f"{field} is malformed.")
    return normalized


def _validate_url(value: object) -> str:
    parsed = urlsplit(str(value or ""))
    host = str(parsed.hostname or "").casefold().rstrip(".")
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if not host or parsed.username or parsed.password:
        raise BlobBenchmarkError("A private Blob target is malformed.")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise BlobBenchmarkError("Private Blob targets require HTTPS.")
    if parsed.fragment:
        raise BlobBenchmarkError("Private Blob targets cannot contain fragments.")
    return str(value)


def load_targets(path: Path) -> list[dict[str, str]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping) or set(value) != {"schemaVersion", "artifacts"}:
        raise BlobBenchmarkError("The private target manifest is malformed.")
    if value.get("schemaVersion") != 1 or not isinstance(value.get("artifacts"), list):
        raise BlobBenchmarkError("The private target manifest schema is unsupported.")
    targets: list[dict[str, str]] = []
    names: set[str] = set()
    for target in value["artifacts"]:
        if not isinstance(target, Mapping) or set(target) != {
            "name",
            "url",
            "expectedSha256",
        }:
            raise BlobBenchmarkError("A private Blob target is malformed.")
        name = _label(target.get("name"), "artifact name")
        digest = str(target.get("expectedSha256") or "").casefold()
        if name in names or not SHA256_RE.fullmatch(digest):
            raise BlobBenchmarkError("Artifact identity evidence is malformed.")
        names.add(name)
        targets.append(
            {"name": name, "url": _validate_url(target.get("url")), "sha256": digest}
        )
    if not targets:
        raise BlobBenchmarkError("At least one private Blob target is required.")
    return targets


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return round(ordered[index], 3)


def _request(url: str, *, token: str | None, timeout: float) -> tuple[int, bytes, float]:
    headers = {"Accept": "application/octet-stream", "Cache-Control": "no-cache"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return int(response.status), body, (time.perf_counter() - started) * 1000
    except urllib.error.HTTPError as error:
        # Discard error bodies and messages so target details cannot escape.
        return int(error.code), b"", (time.perf_counter() - started) * 1000


def run_benchmark(
    *,
    label: str,
    targets: Sequence[Mapping[str, str]],
    token: str,
    samples: int,
    warmups: int,
    timeout_seconds: float,
    attested_region: str,
) -> dict[str, object]:
    normalized_label = _label(label)
    if _label(attested_region, "attested region") != normalized_label:
        raise BlobBenchmarkError("The platform-attested region does not match the label.")
    artifacts: dict[str, dict[str, object]] = {}
    all_passed = True
    for target in targets:
        url = target["url"]
        expected = target["sha256"]
        private_status, _, _ = _request(url, token=None, timeout=timeout_seconds)
        private_denied = private_status in {401, 403}
        warmup_errors = 0
        for _ in range(warmups):
            try:
                warmup_status, warmup_body, _ = _request(
                    url, token=token, timeout=timeout_seconds
                )
                if (
                    warmup_status != 200
                    or hashlib.sha256(warmup_body).hexdigest() != expected
                ):
                    warmup_errors += 1
            except Exception:
                warmup_errors += 1
        latencies: list[float] = []
        successes = 0
        exact_matches = 0
        errors = 0
        for _ in range(samples):
            try:
                status, body, latency = _request(
                    url, token=token, timeout=timeout_seconds
                )
                if status == 200:
                    successes += 1
                    latencies.append(latency)
                    if hashlib.sha256(body).hexdigest() == expected:
                        exact_matches += 1
                else:
                    errors += 1
            except Exception:
                errors += 1
        passed = bool(
            private_denied
            and warmup_errors == 0
            and successes == samples
            and exact_matches == samples
            and errors == 0
        )
        all_passed = all_passed and passed
        artifacts[str(target["name"])] = {
            "scoredAttempts": samples,
            "warmupAttempts": warmups,
            "warmupErrors": warmup_errors,
            "successes": successes,
            "errors": errors,
            "exactExpectedMatches": exact_matches,
            "unauthenticatedReadDenied": private_denied,
            "latencyMs": {
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "p99": _percentile(latencies, 0.99) if samples >= 100 else None,
                "max": _percentile(latencies, 1.0),
            },
            "passed": passed,
        }
    return {
        "schemaVersion": 1,
        "status": "passed" if all_passed else "failed",
        "evidenceKind": "private-blob-region-read",
        "execution": {
            "deploymentRegionLabel": normalized_label,
            "regionAttestedByEnvironment": True,
            "isolatedDiagnosticTaskRequired": True,
        },
        "configuration": {
            "scoredSamplesPerArtifact": samples,
            "warmupSamplesPerArtifact": warmups,
            "p99Scored": samples >= 100,
        },
        "artifacts": artifacts,
        "identityDisclosure": {
            "urlsPrinted": False,
            "digestsPrinted": False,
            "bodiesPrinted": False,
            "tokensPrinted": False,
        },
    }


def _output(path: Path) -> Path:
    resolved = path.resolve()
    root = LOCAL_DATA.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise BlobBenchmarkError("The output must be a file below .local-data.")
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--warmup-samples", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--token-env", default="BLOB_READ_WRITE_TOKEN")
    parser.add_argument("--region-env", default="VERCEL_REGION")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.samples < 1 or args.warmup_samples < 0 or args.timeout_seconds <= 0:
        raise BlobBenchmarkError("Sample and timeout values are invalid.")
    if args.samples < 100:
        raise BlobBenchmarkError("Qualification requires at least 100 scored Blob reads.")
    if not ENV_NAME_RE.fullmatch(args.token_env) or not ENV_NAME_RE.fullmatch(args.region_env):
        raise BlobBenchmarkError("Environment variable names are malformed.")
    token = os.environ.get(args.token_env, "")
    region = os.environ.get(args.region_env, "")
    if not token or not region:
        raise BlobBenchmarkError("Required injected environment values are absent.")
    report = run_benchmark(
        label=args.label,
        targets=load_targets(args.targets),
        token=token,
        samples=args.samples,
        warmups=args.warmup_samples,
        timeout_seconds=args.timeout_seconds,
        attested_region=region,
    )
    output = _output(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "passed" else 4


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Pumbility Blob benchmark failed safely; target and private details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
