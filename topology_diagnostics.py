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
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit


TOPOLOGY_DIAGNOSTIC_ENV = "PUMBILITY_TOPOLOGY_DIAGNOSTIC_ENABLED"
TOPOLOGY_LABEL_ENV = "PUMBILITY_TOPOLOGY_LABEL"
TOPOLOGY_CONNECTION_LIMIT_ENV = "PUMBILITY_TOPOLOGY_CONNECTION_LIMIT"
TOPOLOGY_CRON_CORRELATION_ENV = "PUMBILITY_TOPOLOGY_CRON_CORRELATION_SHA256"
TOPOLOGY_DIAGNOSTIC_PREFIX = "analysis/private/topology-diagnostic"
TOPOLOGY_ALLOWED_LABELS = frozenset({"iad1", "cle1"})
TOPOLOGY_ALLOWED_TOPICS = frozenset({"analysis", "player-recommendations"})
TOPOLOGY_ALLOWED_ACTIONS = frozenset(
    {"blob-read", "blob-mutation", "timeout-faults", "worker-crash"}
)
TOPOLOGY_COLD_COMPONENTS = frozenset(
    {"api", "analysis-worker", "player-recommendations-worker"}
)
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


def require_action(value: object) -> str:
    action = str(value or "").strip().casefold()
    if action not in TOPOLOGY_ALLOWED_ACTIONS:
        raise ValueError("The topology diagnostic action is invalid.")
    return action


def require_runtime_database_url(environment: Mapping[str, str]) -> str:
    """Validate and return the unchanged approved transaction-pool URL."""
    from scripts.backfill_pumbility_production import (
        EXPECTED_DATABASE,
        EXPECTED_HOST,
        EXPECTED_LOGIN,
        EXPECTED_PROJECT_REF,
    )

    value = environment.get("PUMBILITY_DATABASE_URL", "").strip()
    parsed = urlsplit(value)
    query = parse_qs(parsed.query)
    expected_user = f"{EXPECTED_LOGIN}.{EXPECTED_PROJECT_REF}"
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or (parsed.hostname or "").casefold() != EXPECTED_HOST
        or parsed.port != 6543
        or parsed.username != expected_user
        or not parsed.password
        or parsed.path != f"/{EXPECTED_DATABASE}"
        or query.get("sslmode") != ["require"]
        or parsed.fragment
    ):
        raise RuntimeError("The runtime database URL is not the approved transaction pooler.")
    return value


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


def action_result_path(label: str, digest: str) -> str:
    if label not in TOPOLOGY_ALLOWED_LABELS or not SHA256_RE.fullmatch(digest):
        raise ValueError("The topology diagnostic action scope is invalid.")
    return f"{diagnostic_prefix(label)}actions/{digest}.json"


def cold_marker_path(label: str, component: str, digest: str) -> str:
    if (
        label not in TOPOLOGY_ALLOWED_LABELS
        or component not in TOPOLOGY_COLD_COMPONENTS
        or not SHA256_RE.fullmatch(digest)
    ):
        raise ValueError("The topology cold-start marker scope is invalid.")
    return f"{diagnostic_prefix(label)}cold/{component}/{digest}.json"


def cron_marker_path(label: str, digest: str) -> str:
    if label not in TOPOLOGY_ALLOWED_LABELS or not SHA256_RE.fullmatch(digest):
        raise ValueError("The topology cron marker scope is invalid.")
    return f"{diagnostic_prefix(label)}cron/{digest}.json"


def emit_event(event: Mapping[str, Any]) -> None:
    print(json.dumps(dict(event), separators=(",", ":"), sort_keys=True))


def emit_cold_start(*, label: str, component: str, duration_ms: float) -> None:
    if label not in TOPOLOGY_ALLOWED_LABELS or component not in TOPOLOGY_COLD_COMPONENTS:
        raise ValueError("The topology cold-start event scope is invalid.")
    emit_event(
        {
            "kind": "cold-start",
            "label": label,
            "component": component,
            "durationMs": round(float(duration_ms), 3),
            "success": True,
            "cold": True,
        }
    )


def validated_cron_correlation(environment: Mapping[str, str]) -> str:
    value = environment.get(TOPOLOGY_CRON_CORRELATION_ENV, "").strip().casefold()
    if not SHA256_RE.fullmatch(value):
        raise RuntimeError("The topology cron correlation is unavailable.")
    return value
