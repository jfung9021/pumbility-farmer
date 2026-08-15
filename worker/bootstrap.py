"""Process-local worker bootstrap timing for hosted topology diagnostics."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerBootClaim:
    component: str
    identity_sha256: str
    started_at: float


_lock = threading.Lock()
_states: dict[str, tuple[str, float, bool]] = {}


def register_worker_boot(component: str) -> None:
    """Record application-entrypoint startup before task modules are imported."""
    normalized = str(component or "").strip().casefold()
    if normalized not in {"analysis-worker", "player-recommendations-worker"}:
        raise ValueError("The topology worker component is invalid.")
    with _lock:
        if normalized in _states:
            return
        raw_identity = secrets.token_hex(32)
        _states[normalized] = (
            hashlib.sha256(raw_identity.encode("utf-8")).hexdigest(),
            time.perf_counter(),
            False,
        )


def claim_first_worker_invocation(component: str) -> WorkerBootClaim | None:
    """Claim this process's first invocation once; failed claims are never relabeled cold."""
    normalized = str(component or "").strip().casefold()
    with _lock:
        state = _states.get(normalized)
        if state is None or state[2]:
            return None
        identity_sha256, started_at, _claimed = state
        _states[normalized] = (identity_sha256, started_at, True)
        return WorkerBootClaim(
            component=normalized,
            identity_sha256=identity_sha256,
            started_at=started_at,
        )


def reset_worker_boot_for_tests() -> None:
    with _lock:
        _states.clear()
