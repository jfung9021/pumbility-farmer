"""Collect privacy-safe Pumbility topology events from bounded Vercel logs.

Deployment references are accepted only through fixed process environment
variables. Vercel log envelopes, platform log IDs, deployment references, and
raw messages remain in memory; the output contains only verifier-allowlisted
event objects below the repository's ignored ``.local-data`` directory.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA = PROJECT_ROOT / ".local-data"
FIRST_DEPLOYMENT_ENV = "PUMBILITY_FIRST_PREVIEW_DEPLOYMENT"
SECOND_DEPLOYMENT_ENV = "PUMBILITY_SECOND_PREVIEW_DEPLOYMENT"
DEPLOYMENT_REFERENCE_RE = re.compile(
    r"^(?:dpl_[A-Za-z0-9]+|[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)$"
)
LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_DOMAINS = frozenset(
    {
        "analysis",
        "tier-list",
        "recommendation-players",
        "recommendation-player",
        "job-status",
    }
)
SAFE_TELEMETRY_OUTCOMES = frozenset(
    {
        "candidate-served",
        "candidate-error",
        "fallback",
        "mismatch",
        "authority-error",
    }
)
RAW_TELEMETRY_OUTCOMES = {
    "candidate-served": "candidate-served",
    "candidate-error-fallback": "candidate-error",
    "comparison-error-fallback": "fallback",
    "mismatch-fallback": "mismatch",
    "authority-error": "authority-error",
}
SAFE_TOPICS = frozenset({"analysis", "player-recommendations"})
SAFE_WORKER_COMPONENTS = frozenset({"analysis", "player-recommendations"})
SAFE_COLD_COMPONENTS = frozenset(
    {"api", "analysis-worker", "player-recommendations-worker"}
)
EVENT_KEYS = {
    "telemetry": {"kind", "label", "domain", "outcome", "count"},
    "worker": {
        "kind",
        "label",
        "component",
        "outcome",
        "count",
        "isolatedDiagnostic",
    },
    "cron": {
        "kind",
        "label",
        "source",
        "correlationSha256",
        "count",
        "authorized",
    },
    "queue": {
        "kind",
        "label",
        "topic",
        "stage",
        "identitySha256",
        "attempt",
    },
    "cold-start": {
        "kind",
        "label",
        "component",
        "durationMs",
        "success",
        "cold",
    },
    "capacity": {
        "kind",
        "label",
        "activeConnections",
        "connectionLimit",
        "connectionErrors",
        "deadlineErrors",
    },
}
RAW_TELEMETRY_RE = re.compile(
    r"(?:^|:)pumbility_store operation=[a-z_-]+ "
    r"domain=(analysis|tier-list|recommendation-players|recommendation-player|job-status) "
    r"outcome=(candidate-served|candidate-error-fallback|comparison-error-fallback|"
    r"mismatch-fallback|authority-error)(?:\s|$)"
)

CommandRunner = Callable[..., Any]


class CollectionError(RuntimeError):
    """Hosted log evidence is incomplete, malformed, or unsafe."""


def _positive_int(value: object, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CollectionError("A topology event count is malformed.")
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CollectionError("A topology event duration is malformed.")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise CollectionError("A topology event duration is malformed.")
    return result


def _label(value: object) -> str:
    result = str(value or "").strip().casefold()
    if not LABEL_RE.fullmatch(result):
        raise CollectionError("A topology label is malformed.")
    return result


def _sha256(value: object) -> str:
    result = str(value or "").strip().casefold()
    if not SHA256_RE.fullmatch(result):
        raise CollectionError("A topology event identity is malformed.")
    return result


def validate_event(value: Mapping[str, Any], *, expected_label: str) -> dict[str, Any]:
    """Return one exact verifier-compatible event or fail closed."""
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in EVENT_KEYS:
        raise CollectionError("A topology event kind is not allowlisted.")
    if set(value) != EVENT_KEYS[kind]:
        raise CollectionError("A topology event contains non-allowlisted fields.")
    event = dict(value)
    label = _label(event.get("label"))
    if label != expected_label:
        raise CollectionError("A topology event came from the wrong deployment label.")
    event["label"] = label

    if kind == "telemetry":
        if event.get("domain") not in SAFE_DOMAINS:
            raise CollectionError("A telemetry domain is not allowlisted.")
        if event.get("outcome") not in SAFE_TELEMETRY_OUTCOMES:
            raise CollectionError("A telemetry outcome is not allowlisted.")
        _positive_int(event.get("count"), minimum=1)
    elif kind == "worker":
        if event.get("component") not in SAFE_WORKER_COMPONENTS:
            raise CollectionError("A worker component is not allowlisted.")
        if event.get("outcome") not in {"succeeded", "failed"}:
            raise CollectionError("A worker outcome is not allowlisted.")
        _positive_int(event.get("count"), minimum=1)
        if event.get("isolatedDiagnostic") is not True:
            raise CollectionError("A worker event is not an isolated diagnostic.")
    elif kind == "cron":
        if event.get("source") not in {"platform-scheduler", "route", "manual"}:
            raise CollectionError("A cron source is not allowlisted.")
        event["correlationSha256"] = _sha256(event.get("correlationSha256"))
        _positive_int(event.get("count"), minimum=1)
        if type(event.get("authorized")) is not bool:
            raise CollectionError("A cron authorization result is malformed.")
    elif kind == "queue":
        if event.get("topic") not in SAFE_TOPICS:
            raise CollectionError("A queue topic is not allowlisted.")
        if event.get("stage") not in {
            "published",
            "consumed",
            "durable-effect",
            "error",
        }:
            raise CollectionError("A queue stage is not allowlisted.")
        event["identitySha256"] = _sha256(event.get("identitySha256"))
        _positive_int(event.get("attempt"), minimum=1)
    elif kind == "cold-start":
        if event.get("component") not in SAFE_COLD_COMPONENTS:
            raise CollectionError("A cold-start component is not allowlisted.")
        _number(event.get("durationMs"))
        if type(event.get("success")) is not bool or event.get("cold") is not True:
            raise CollectionError("A cold-start result is malformed.")
    else:
        _positive_int(event.get("activeConnections"))
        _positive_int(event.get("connectionLimit"), minimum=1)
        _positive_int(event.get("connectionErrors"))
        _positive_int(event.get("deadlineErrors"))
    return event


def event_from_message(message: object, *, expected_label: str) -> dict[str, Any] | None:
    """Extract one safe event without retaining the source log message."""
    if not isinstance(message, str) or not message:
        return None
    try:
        value = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        match = RAW_TELEMETRY_RE.search(message)
        if match is None:
            return None
        return {
            "kind": "telemetry",
            "label": expected_label,
            "domain": match.group(1),
            "outcome": RAW_TELEMETRY_OUTCOMES[match.group(2)],
            "count": 1,
        }
    if not isinstance(value, Mapping) or "kind" not in value:
        return None
    return validate_event(value, expected_label=expected_label)


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise CollectionError("A log-window timestamp is malformed.") from error
    if result.tzinfo is None:
        raise CollectionError("Log-window timestamps must include UTC offsets.")
    return result.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _windows_safe_vercel_command(command: Sequence[str]) -> list[str]:
    result = list(command)
    if os.name != "nt" or not result:
        return result
    shim = Path(result[0])
    if shim.suffix.casefold() != ".cmd":
        return result
    entrypoint = shim.parent / "node_modules" / "vercel" / "dist" / "vc.js"
    bundled_node = shim.parent / "node.exe"
    node = str(bundled_node) if bundled_node.is_file() else shutil.which("node")
    if not entrypoint.is_file() or not node:
        raise CollectionError("The Vercel CLI could not be resolved safely.")
    return [node, str(entrypoint), *result[1:]]


def _read_window(
    *,
    deployment: str,
    started: datetime,
    ended: datetime,
    limit: int,
    vercel_cli: str,
    command_runner: CommandRunner,
    timeout_seconds: float,
) -> list[Mapping[str, Any]]:
    command = _windows_safe_vercel_command(
        (
            vercel_cli,
            "logs",
            "--deployment",
            deployment,
            "--since",
            _format_utc(started),
            "--until",
            _format_utc(ended),
            "--limit",
            str(limit),
            "--json",
            "--no-branch",
            "--no-color",
            "--non-interactive",
        )
    )
    try:
        completed = command_runner(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception:
        raise CollectionError("A bounded Vercel log query failed safely.") from None
    if completed.returncode != 0:
        raise CollectionError("A bounded Vercel log query failed safely.")
    output = completed.stdout or b""
    if isinstance(output, bytes):
        try:
            text = output.decode("utf-8")
        except UnicodeDecodeError:
            raise CollectionError("Vercel logs were not valid UTF-8.") from None
    else:
        text = str(output)
    records: list[Mapping[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            raise CollectionError("Vercel returned malformed JSON logs.") from None
        if not isinstance(record, Mapping):
            raise CollectionError("A Vercel log envelope is malformed.")
        records.append(record)
    if len(records) > limit:
        raise CollectionError("A Vercel log query exceeded its declared limit.")
    return records


def _merge_records(
    target: dict[str, Mapping[str, Any]], records: Sequence[Mapping[str, Any]]
) -> None:
    for record in records:
        platform_id = record.get("id")
        if not isinstance(platform_id, str) or not platform_id:
            raise CollectionError("A Vercel log record lacks a platform identity.")
        previous = target.get(platform_id)
        if previous is not None and previous != record:
            raise CollectionError("A platform log identity has conflicting records.")
        target[platform_id] = record


def _collect_windows(
    *,
    deployment: str,
    started: datetime,
    ended: datetime,
    limit: int,
    minimum_window: timedelta,
    vercel_cli: str,
    command_runner: CommandRunner,
    timeout_seconds: float,
) -> dict[str, Mapping[str, Any]]:
    records = _read_window(
        deployment=deployment,
        started=started,
        ended=ended,
        limit=limit,
        vercel_cli=vercel_cli,
        command_runner=command_runner,
        timeout_seconds=timeout_seconds,
    )
    if len(records) < limit:
        result: dict[str, Mapping[str, Any]] = {}
        _merge_records(result, records)
        return result
    if ended - started <= minimum_window:
        raise CollectionError("A minimum log window remained limit-saturated.")
    midpoint = started + (ended - started) / 2
    left = _collect_windows(
        deployment=deployment,
        started=started,
        ended=midpoint,
        limit=limit,
        minimum_window=minimum_window,
        vercel_cli=vercel_cli,
        command_runner=command_runner,
        timeout_seconds=timeout_seconds,
    )
    right = _collect_windows(
        deployment=deployment,
        started=midpoint,
        ended=ended,
        limit=limit,
        minimum_window=minimum_window,
        vercel_cli=vercel_cli,
        command_runner=command_runner,
        timeout_seconds=timeout_seconds,
    )
    _merge_records(left, tuple(right.values()))
    return left


def _deployment_reference(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not DEPLOYMENT_REFERENCE_RE.fullmatch(value):
        raise CollectionError("A protected deployment reference is unavailable.")
    return value


def _safe_output(path: Path) -> Path:
    result = path.resolve()
    local_root = LOCAL_DATA.resolve()
    if result == local_root or not result.is_relative_to(local_root):
        raise CollectionError("Topology events must be written below .local-data.")
    return result


def collect_topology_events(
    *,
    first_label: str,
    second_label: str,
    started: datetime,
    ended: datetime,
    output: Path,
    environment: Mapping[str, str],
    limit: int = 1000,
    minimum_window: timedelta = timedelta(milliseconds=1),
    vercel_cli: str = "vercel",
    command_runner: CommandRunner = subprocess.run,
    timeout_seconds: float = 120.0,
) -> dict[str, int]:
    """Collect two deployments while persisting no log-envelope identity."""
    first = _deployment_reference(environment, FIRST_DEPLOYMENT_ENV)
    second = _deployment_reference(environment, SECOND_DEPLOYMENT_ENV)
    first_label = _label(first_label)
    second_label = _label(second_label)
    if first == second or first_label == second_label:
        raise CollectionError("Two distinct deployment labels and references are required.")
    if started.tzinfo is None or ended.tzinfo is None:
        raise CollectionError("The log window must be timezone-aware.")
    started = started.astimezone(timezone.utc)
    ended = ended.astimezone(timezone.utc)
    if ended <= started:
        raise CollectionError("The bounded log window is empty.")
    if limit < 2 or timeout_seconds <= 0 or minimum_window <= timedelta(0):
        raise CollectionError("Log collection bounds are invalid.")

    events: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for label, deployment in ((first_label, first), (second_label, second)):
        records = _collect_windows(
            deployment=deployment,
            started=started,
            ended=ended,
            limit=limit,
            minimum_window=minimum_window,
            vercel_cli=vercel_cli,
            command_runner=command_runner,
            timeout_seconds=timeout_seconds,
        )
        label_events: list[dict[str, Any]] = []
        for record in records.values():
            event = event_from_message(record.get("message"), expected_label=label)
            if event is not None:
                label_events.append(event)
        counts[label] = len(label_events)
        events.extend(label_events)
    if not events:
        raise CollectionError("No verifier-compatible topology events were found.")

    destination = _safe_output(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = "".join(
        json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
        for event in events
    )
    forbidden = (first, second)
    if any(value in encoded for value in forbidden) or re.search(
        r"https?://|\bdpl_[A-Za-z0-9]+\b", encoded, re.I
    ):
        raise CollectionError("Sanitized topology events retained deployment identity.")
    destination.write_text(encoded, encoding="utf-8")
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-label", required=True)
    parser.add_argument("--second-label", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--minimum-window-ms", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--vercel-cli", default=shutil.which("vercel") or "vercel")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    counts = collect_topology_events(
        first_label=args.first_label,
        second_label=args.second_label,
        started=_parse_utc(args.since),
        ended=_parse_utc(args.until),
        output=args.output,
        environment=os.environ,
        limit=args.limit,
        minimum_window=timedelta(milliseconds=args.minimum_window_ms),
        vercel_cli=args.vercel_cli,
        timeout_seconds=args.timeout_seconds,
    )
    print(
        json.dumps(
            {
                "status": "collected",
                "labels": [
                    {"label": label, "events": count}
                    for label, count in counts.items()
                ],
                "privateLogEnvelopesPersisted": False,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Pumbility topology event collection failed safely; private details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
