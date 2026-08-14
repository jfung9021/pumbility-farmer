"""Preview-only helpers for hosted Pumbility topology qualification.

The public contracts expose only verifier-allowlisted aggregates. Raw run and
message identities remain inside queue payloads or isolated private Blob keys.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from typing import Any, Mapping


TOPOLOGY_DIAGNOSTIC_ENV = "PUMBILITY_TOPOLOGY_DIAGNOSTIC_ENABLED"
TOPOLOGY_LABEL_ENV = "PUMBILITY_TOPOLOGY_LABEL"
TOPOLOGY_CONNECTION_LIMIT_ENV = "PUMBILITY_TOPOLOGY_CONNECTION_LIMIT"
TOPOLOGY_CRON_CORRELATION_ENV = "PUMBILITY_TOPOLOGY_CRON_CORRELATION_SHA256"
TOPOLOGY_DIAGNOSTIC_PREFIX = "analysis/private/topology-diagnostic"
TOPOLOGY_ALLOWED_LABELS = frozenset({"iad1", "cle1"})
TOPOLOGY_ALLOWED_TOPICS = frozenset({"analysis", "player-recommendations"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def enabled(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def diagnostic_enabled(environment: Mapping[str, str]) -> bool:
    return bool(
        environment.get("VERCEL_ENV", "").strip().casefold() == "preview"
        and enabled(environment.get(TOPOLOGY_DIAGNOSTIC_ENV))
    )


def cron_diagnostic_enabled(environment: Mapping[str, str]) -> bool:
    return bool(
        environment.get("VERCEL_ENV", "").strip().casefold() == "production"
        and enabled(environment.get(TOPOLOGY_DIAGNOSTIC_ENV))
    )


def _require_attested_topology(environment: Mapping[str, str]) -> tuple[str, int]:
    label = environment.get(TOPOLOGY_LABEL_ENV, "").strip().casefold()
    region = environment.get("VERCEL_REGION", "").strip().casefold()
    if label not in TOPOLOGY_ALLOWED_LABELS or region != label:
        raise RuntimeError("The topology label is not attested by the runtime region.")
    try:
        connection_limit = int(
            environment.get(TOPOLOGY_CONNECTION_LIMIT_ENV, "").strip()
        )
    except ValueError as error:
        raise RuntimeError("The diagnostic connection limit is invalid.") from error
    if connection_limit < 4 or connection_limit > 100:
        raise RuntimeError("The diagnostic connection limit is outside the safe bound.")
    return label, connection_limit


def require_diagnostic_environment(
    environment: Mapping[str, str],
) -> tuple[str, int]:
    if not diagnostic_enabled(environment):
        raise RuntimeError("Topology diagnostics are unavailable.")
    return _require_attested_topology(environment)


def require_cron_diagnostic_environment(
    environment: Mapping[str, str],
) -> tuple[str, int]:
    if not cron_diagnostic_enabled(environment):
        raise RuntimeError("Topology cron diagnostics are unavailable.")
    return _require_attested_topology(environment)


def require_topic(value: object) -> str:
    topic = str(value or "").strip().casefold()
    if topic not in TOPOLOGY_ALLOWED_TOPICS:
        raise ValueError("The topology diagnostic topic is invalid.")
    return topic


def identity_digest(raw_identity: str) -> str:
    return hashlib.sha256(raw_identity.encode("utf-8")).hexdigest()


def new_identity() -> str:
    return secrets.token_hex(32)


def queue_marker_path(label: str, topic: str, digest: str) -> str:
    if label not in TOPOLOGY_ALLOWED_LABELS or topic not in TOPOLOGY_ALLOWED_TOPICS:
        raise ValueError("The topology diagnostic marker scope is invalid.")
    if not SHA256_RE.fullmatch(digest):
        raise ValueError("The topology diagnostic identity is malformed.")
    return f"{TOPOLOGY_DIAGNOSTIC_PREFIX}/{label}/{topic}/{digest}.json"


def diagnostic_prefix(label: str) -> str:
    if label not in TOPOLOGY_ALLOWED_LABELS:
        raise ValueError("The topology diagnostic label is invalid.")
    return f"{TOPOLOGY_DIAGNOSTIC_PREFIX}/{label}/"


def emit_event(event: Mapping[str, Any]) -> None:
    print(json.dumps(dict(event), separators=(",", ":"), sort_keys=True))


_WORKER_IMPORT_STARTED = time.perf_counter()
_WORKER_COLD_REPORTED: set[str] = set()


def emit_worker_cold_start_once(*, label: str, component: str) -> None:
    if component in _WORKER_COLD_REPORTED:
        return
    _WORKER_COLD_REPORTED.add(component)
    emit_event(
        {
            "kind": "cold-start",
            "label": label,
            "component": component,
            "durationMs": round((time.perf_counter() - _WORKER_IMPORT_STARTED) * 1000, 3),
            "success": True,
            "cold": True,
        }
    )


def validated_cron_correlation(environment: Mapping[str, str]) -> str:
    value = environment.get(TOPOLOGY_CRON_CORRELATION_ENV, "").strip().casefold()
    if not SHA256_RE.fullmatch(value):
        raise RuntimeError("The topology cron correlation is unavailable.")
    return value
