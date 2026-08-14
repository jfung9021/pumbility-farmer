"""Opt-in PostgreSQL and Supabase Storage persistence adapters.

The default backend remains the existing Vercel stores.  This module is kept
free of imports from ``analysis_runtime`` so the runtime can select it lazily
without creating a circular dependency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping, Sequence
from urllib.parse import quote, urlparse

import requests


BACKEND_ENV = "PUMBILITY_DATA_BACKEND"
DATABASE_URL_ENV = "PUMBILITY_DATABASE_URL"
SUPABASE_URL_ENV = "PUMBILITY_SUPABASE_URL"
SERVICE_KEY_ENV = "PUMBILITY_SUPABASE_SERVICE_ROLE_KEY"
STORAGE_BUCKET_ENV = "PUMBILITY_STORAGE_BUCKET"
SHADOW_STRICT_ENV = "PUMBILITY_SHADOW_STRICT"
CANONICAL_SNAPSHOT_WRITE_ENV = "PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED"
READ_CANARY_ENV = "PUMBILITY_SUPABASE_READ_CANARY"
READ_POOL_ENABLED_ENV = "PUMBILITY_SUPABASE_READ_POOL_ENABLED"
READ_POOL_MAX_SIZE_ENV = "PUMBILITY_SUPABASE_READ_POOL_MAX_SIZE"
READ_POOL_MAX_WAITING_ENV = "PUMBILITY_SUPABASE_READ_POOL_MAX_WAITING"
BLOB_MIRROR_ENV = "PUMBILITY_BLOB_MIRROR_ENABLED"
BLOB_READ_FALLBACK_ENV = "PUMBILITY_BLOB_READ_FALLBACK_ENABLED"
DEFAULT_BUCKET = "pumbility-artifacts"
EXPECTED_PUMBILITY_MIGRATION = "20260813010000"
VALID_BACKENDS = frozenset({"vercel", "shadow", "supabase"})
VALID_READ_CANARIES = frozenset(
    {
        "analysis",
        "tier-list",
        "recommendation-players",
        "recommendation-player",
        "job-status",
    }
)
CURRENT_SNAPSHOT_RE = re.compile(r"^analysis/private/(phoenix1|phoenix2)-current\.json$")
STAGING_SNAPSHOT_RE = re.compile(
    r"^analysis/(?:phoenix1|phoenix2)/staging/[^/]+\.json$"
)
FROZEN_PHOENIX1_SNAPSHOT_KEY = "analysis/private/phoenix1.json"
JOB_LEASE_SECONDS = 800
JOB_HEARTBEAT_INTERVAL_SECONDS = 60.0
READ_CONNECT_TIMEOUT_SECONDS = 3
READ_POOL_ACQUIRE_TIMEOUT_SECONDS = 1.0
READ_STATEMENT_TIMEOUT_MILLISECONDS = 10_000
READ_POOL_DEFAULT_MAX_SIZE = 2
READ_POOL_DEFAULT_MAX_WAITING = 2
READ_POOL_HEALTH_CHECK_INTERVAL_SECONDS = 30.0


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def configured_backend(
    environment: Mapping[str, str] | None = None,
) -> str:
    env = environment if environment is not None else os.environ
    backend = str(env.get(BACKEND_ENV, "vercel")).strip().casefold() or "vercel"
    if backend not in VALID_BACKENDS:
        choices = ", ".join(sorted(VALID_BACKENDS))
        raise RuntimeError(f"{BACKEND_ENV} must be one of: {choices}.")
    return backend


def configured_read_canaries(
    environment: Mapping[str, str] | None = None,
) -> frozenset[str]:
    """Parse the route-domain allowlist and reject configuration typos."""
    env = environment if environment is not None else os.environ
    configured = {
        item.strip().casefold()
        for item in str(env.get(READ_CANARY_ENV, "")).split(",")
        if item.strip()
    }
    unknown = configured - VALID_READ_CANARIES
    if unknown:
        choices = ", ".join(sorted(VALID_READ_CANARIES))
        raise RuntimeError(
            f"{READ_CANARY_ENV} contains unsupported domains; expected only: {choices}."
        )
    return frozenset(configured)


def validate_rollout_configuration(
    environment: Mapping[str, str] | None = None,
) -> None:
    """Fail application startup on unsafe or internally inconsistent flag sets."""
    env = environment if environment is not None else os.environ
    backend = configured_backend(env)
    canaries = configured_read_canaries(env)
    canonical_writes = _enabled(env.get(CANONICAL_SNAPSHOT_WRITE_ENV))
    blob_mirror = _enabled(env.get(BLOB_MIRROR_ENV))
    blob_fallback = _enabled(env.get(BLOB_READ_FALLBACK_ENV))

    if backend == "supabase":
        if canaries:
            raise RuntimeError(
                f"{READ_CANARY_ENV} must be empty when {BACKEND_ENV}=supabase."
            )
        required = [
            name
            for name, enabled in (
                (CANONICAL_SNAPSHOT_WRITE_ENV, canonical_writes),
                (BLOB_MIRROR_ENV, blob_mirror),
                (BLOB_READ_FALLBACK_ENV, blob_fallback),
            )
            if not enabled
        ]
        if required:
            raise RuntimeError(
                "Supabase authority requires enabled rollback controls: "
                + ", ".join(required)
                + "."
            )
    elif blob_mirror or blob_fallback:
        raise RuntimeError(
            f"{BLOB_MIRROR_ENV} and {BLOB_READ_FALLBACK_ENV} are valid only when "
            f"{BACKEND_ENV}=supabase."
        )
    if backend == "vercel" and canonical_writes:
        raise RuntimeError(
            f"{CANONICAL_SNAPSHOT_WRITE_ENV} cannot be enabled while Vercel-only mode is active."
        )


def _read_canary_enabled(domain: str | None, configured: frozenset[str]) -> bool:
    if domain is None:
        return False
    normalized = domain.strip().casefold()
    if normalized not in VALID_READ_CANARIES:
        raise RuntimeError(f"Unsupported Pumbility read-canary domain: {domain!r}.")
    return normalized in configured


def _safe_rollout_event(
    *,
    operation: str,
    domain: str,
    outcome: str,
    authoritative_ms: float,
    candidate_ms: float | None = None,
    authoritative_phases: Mapping[str, float] | None = None,
    candidate_phases: Mapping[str, float] | None = None,
    comparison_ms: float | None = None,
    overall_ms: float | None = None,
    authoritative_error: Exception | None = None,
    candidate_error: Exception | None = None,
) -> None:
    """Emit aggregate-safe evidence without artifact keys, digests, or private IDs."""
    def phase_value(phases: Mapping[str, float] | None, phase: str) -> str:
        value = (phases or {}).get(phase)
        return "none" if value is None else f"{value:.3f}"

    # Vercel's default Python logging threshold is WARNING. Canary evidence
    # must survive that default without globally changing application logging.
    logging.getLogger("pumbility.rollout").warning(
        "pumbility_store operation=%s domain=%s outcome=%s authoritative_ms=%.3f "
        "candidate_ms=%s comparison_ms=%s overall_ms=%s "
        "authoritative_acquire_ms=%s authoritative_connect_ms=%s "
        "authoritative_health_ms=%s authoritative_schema_ms=%s "
        "authoritative_fetch_ms=%s authoritative_decode_ms=%s "
        "authoritative_integrity_ms=%s candidate_acquire_ms=%s "
        "candidate_connect_ms=%s candidate_health_ms=%s "
        "candidate_schema_ms=%s candidate_fetch_ms=%s candidate_decode_ms=%s "
        "candidate_integrity_ms=%s authoritative_error=%s candidate_error=%s",
        operation,
        domain,
        outcome,
        authoritative_ms,
        "none" if candidate_ms is None else f"{candidate_ms:.3f}",
        "none" if comparison_ms is None else f"{comparison_ms:.3f}",
        "none" if overall_ms is None else f"{overall_ms:.3f}",
        phase_value(authoritative_phases, "acquire"),
        phase_value(authoritative_phases, "connect"),
        phase_value(authoritative_phases, "health"),
        phase_value(authoritative_phases, "schema"),
        phase_value(authoritative_phases, "fetch"),
        phase_value(authoritative_phases, "decode"),
        phase_value(authoritative_phases, "integrity"),
        phase_value(candidate_phases, "acquire"),
        phase_value(candidate_phases, "connect"),
        phase_value(candidate_phases, "health"),
        phase_value(candidate_phases, "schema"),
        phase_value(candidate_phases, "fetch"),
        phase_value(candidate_phases, "decode"),
        phase_value(candidate_phases, "integrity"),
        _safe_error_code(authoritative_error),
        _safe_error_code(candidate_error),
    )


def _safe_error_code(error: Exception | None) -> str:
    """Classify rollout failures without logging messages, URLs, keys, or IDs."""
    if error is None:
        return "none"
    normalized = str(error).casefold()
    category = next(
        (
            label
            for label, markers in (
                ("authentication", ("authentication failed", "password failed")),
                ("routing", ("tenant or user not found", "user not found")),
                ("capacity", ("max client", "too many connection")),
                ("timeout", ("timeout", "timed out")),
                ("dns", ("name resolution", "getaddrinfo")),
                ("refused", ("connection refused",)),
                ("closed", ("server closed", "connection closed")),
                ("tls", ("ssl", "tls")),
            )
            if any(marker in normalized for marker in markers)
        ),
        "other",
    )
    sqlstate = str(getattr(error, "sqlstate", None) or "none")
    error_type = re.sub(r"[^A-Za-z0-9_]", "", type(error).__name__) or "Exception"
    return f"{error_type}:{category}:{sqlstate}"


def _equivalent(left: object, right: object) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return _canonical_json_bytes(left) == _canonical_json_bytes(right)
    return left == right


def _equivalent_to_exact_json(left: object, right: _ExactJsonRead) -> bool:
    if right.canonical_bytes is None:
        return left is None
    if not isinstance(left, Mapping):
        return False
    return _canonical_json_bytes(left) == right.canonical_bytes


_READ_PHASES = frozenset(
    {"acquire", "connect", "health", "schema", "fetch", "decode", "integrity"}
)
_read_telemetry = threading.local()


def _read_phase_started() -> float | None:
    """Start timing only when a canary wrapper installed a thread-local collector."""
    if getattr(_read_telemetry, "phases", None) is None:
        return None
    return time.perf_counter()


def _record_read_phase(phase: str, started: float | None) -> None:
    """Accumulate a fixed-name phase without accepting keys, IDs, or other labels."""
    if started is None:
        return
    if phase not in _READ_PHASES:
        raise ValueError("Unsupported Pumbility read telemetry phase.")
    phases = getattr(_read_telemetry, "phases", None)
    if phases is None:
        return
    _record_read_phase_ms(phase, (time.perf_counter() - started) * 1000)


def _record_read_phase_ms(phase: str, elapsed_ms: float) -> None:
    """Accumulate an already-measured fixed-name phase."""
    if phase not in _READ_PHASES:
        raise ValueError("Unsupported Pumbility read telemetry phase.")
    phases = getattr(_read_telemetry, "phases", None)
    if phases is None:
        return
    phases[phase] = phases.get(phase, 0.0) + elapsed_ms


def _checkpoint_player_path(pathname: str, player_id: str) -> str:
    digest = hashlib.sha256(player_id.encode("utf-8")).hexdigest()
    return f"{pathname}.players/{digest}.json"


def _compact_staging_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    compact = {key: value for key, value in payload.items() if key != "snapshot"}
    compact["storageSchemaVersion"] = 2
    compact["checkpointKind"] = "player-delta"
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, Mapping):
        compact["snapshotSchemaVersion"] = int(snapshot.get("schemaVersion") or 0)
    return compact


def require_loopback_database_url(database_url: str) -> None:
    """Fail closed before a local-only command can mutate a remote database."""
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("PUMBILITY_DATABASE_URL must be a PostgreSQL URL.")
    if (parsed.hostname or "").casefold() not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Refusing a non-loopback Pumbility database target.")


def _database_url(explicit: str | None = None) -> str:
    value = str(explicit if explicit is not None else os.getenv(DATABASE_URL_ENV, "")).strip()
    if not value:
        raise RuntimeError(f"{DATABASE_URL_ENV} is required for the Supabase backend.")
    return value


def _connect(database_url: str):
    started = _read_phase_started()
    try:
        try:
            import psycopg
        except ImportError as error:  # pragma: no cover - environment diagnostic
            raise RuntimeError(
                "The Supabase backend requires psycopg. Run `uv sync --frozen`."
            ) from error
        # Transaction-pooled runtime connections must not create named prepared
        # statements that can be routed to another server connection.
        return psycopg.connect(database_url, prepare_threshold=None)
    finally:
        _record_read_phase("connect", started)


_read_pool_lock = threading.Lock()
_read_pool: Any | None = None
_read_pool_key: tuple[str, int, int, int] | None = None


def _bounded_read_pool_setting(
    name: str, default: int, *, minimum: int, maximum: int
) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer.") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _read_pool_configuration() -> tuple[int, int]:
    """Return per-process capacity bounds without allowing the role limit to be bypassed."""
    return (
        _bounded_read_pool_setting(
            READ_POOL_MAX_SIZE_ENV,
            READ_POOL_DEFAULT_MAX_SIZE,
            minimum=1,
            maximum=2,
        ),
        _bounded_read_pool_setting(
            READ_POOL_MAX_WAITING_ENV,
            READ_POOL_DEFAULT_MAX_WAITING,
            minimum=1,
            maximum=16,
        ),
    )


def _check_read_connection(connection: Any) -> None:
    """Probe only connections old enough to plausibly have gone stale while idle."""
    last_checked = float(getattr(connection, "_pumbility_last_checked_at", 0.0))
    if time.monotonic() - last_checked < READ_POOL_HEALTH_CHECK_INTERVAL_SECONDS:
        return
    started = _read_phase_started()
    try:
        from psycopg_pool import ConnectionPool

        ConnectionPool.check_connection(connection)
        connection._pumbility_last_checked_at = time.monotonic()
    finally:
        _record_read_phase("health", started)


def _create_read_pool(database_url: str, *, max_size: int, max_waiting: int) -> Any:
    try:
        import psycopg
        from psycopg_pool import ConnectionPool
    except ImportError as error:  # pragma: no cover - environment diagnostic
        raise RuntimeError(
            "The Supabase read pool requires psycopg with its pool extra. "
            "Run `uv sync --frozen`."
        ) from error

    class _TimedReadConnection(psycopg.Connection):
        @classmethod
        def connect(cls, conninfo: str = "", **kwargs: Any) -> Any:
            started = time.perf_counter()
            connection = super().connect(conninfo, **kwargs)
            connection._pumbility_connect_ms = (
                time.perf_counter() - started
            ) * 1000
            connection._pumbility_last_checked_at = time.monotonic()
            return connection

    pool = ConnectionPool(
        conninfo=database_url,
        connection_class=_TimedReadConnection,
        kwargs={
            "connect_timeout": READ_CONNECT_TIMEOUT_SECONDS,
            # Supavisor transaction mode cannot safely retain named prepared statements.
            "prepare_threshold": None,
        },
        min_size=0,
        max_size=max_size,
        max_waiting=max_waiting,
        timeout=READ_POOL_ACQUIRE_TIMEOUT_SECONDS,
        max_idle=60.0,
        max_lifetime=600.0,
        reconnect_timeout=float(READ_CONNECT_TIMEOUT_SECONDS),
        num_workers=max_size,
        check=_check_read_connection,
        name="pumbility-read",
        open=False,
    )
    pool.open(wait=False)
    return pool


def _get_read_pool(database_url: str) -> Any:
    global _read_pool, _read_pool_key
    max_size, max_waiting = _read_pool_configuration()
    key = (database_url, max_size, max_waiting, os.getpid())
    with _read_pool_lock:
        if _read_pool is not None and _read_pool_key == key:
            return _read_pool
        previous = _read_pool
        _read_pool = _create_read_pool(
            database_url, max_size=max_size, max_waiting=max_waiting
        )
        _read_pool_key = key
    if previous is not None:
        previous.close(timeout=0)
    return _read_pool


@contextmanager
def _read_connect(database_url: str) -> Iterator[Any]:
    """Acquire a bounded hot-read connection, or use the direct A/B control path."""
    if not _enabled(os.getenv(READ_POOL_ENABLED_ENV)):
        started = _read_phase_started()
        try:
            try:
                import psycopg
            except ImportError as error:  # pragma: no cover - environment diagnostic
                raise RuntimeError(
                    "The Supabase backend requires psycopg. Run `uv sync --frozen`."
                ) from error
            connection = psycopg.connect(
                database_url,
                connect_timeout=READ_CONNECT_TIMEOUT_SECONDS,
                prepare_threshold=None,
            )
        finally:
            _record_read_phase("connect", started)
        with connection:
            yield connection
        return

    acquire_started = _read_phase_started()
    acquired = False
    try:
        pool = _get_read_pool(database_url)
        with pool.connection(timeout=READ_POOL_ACQUIRE_TIMEOUT_SECONDS) as connection:
            _record_read_phase("acquire", acquire_started)
            acquired = True
            connect_ms = getattr(connection, "_pumbility_connect_ms", None)
            if connect_ms is not None:
                _record_read_phase_ms("connect", float(connect_ms))
                connection._pumbility_connect_ms = None
            yield connection
    finally:
        if not acquired:
            _record_read_phase("acquire", acquire_started)


@contextmanager
def _read_cursor(database_url: str) -> Iterator[Any]:
    """Create a transaction-scoped hot-read cursor with a per-statement deadline."""
    with _read_connect(database_url) as connection, connection.cursor() as cursor:
        # Session settings are not reliable through Supavisor transaction mode.
        # Pipeline the transaction-local setting with the read to avoid another RTT.
        with connection.pipeline():
            cursor.execute(
                f"set local statement_timeout = '{READ_STATEMENT_TIMEOUT_MILLISECONDS}ms'"
            )
            yield cursor


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _json_metadata(value: object, *, pathname: str) -> tuple[str, int]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise ValueError(f"Pumbility artifact {pathname!r} is not a JSON object.")
    body = _canonical_json_bytes(value)
    return hashlib.sha256(body).hexdigest(), len(body)


def _assert_schema(cursor: Any) -> None:
    started = _read_phase_started()
    try:
        cursor.execute(
            "select value from pumbility.schema_metadata where key = 'migration_version'"
        )
        row = cursor.fetchone()
        _validate_schema_value(row[0] if row else None)
    finally:
        _record_read_phase("schema", started)


def _validate_schema_value(value: object) -> None:
    actual = str(value) if value is not None else "missing"
    if actual != EXPECTED_PUMBILITY_MIGRATION:
        raise RuntimeError(
            "The Pumbility database schema is not at the application-required migration. "
            f"Expected {EXPECTED_PUMBILITY_MIGRATION}; found {actual}."
        )


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class StoredObject:
    pathname: str
    uploaded_at: datetime | None = None


@dataclass(frozen=True)
class _ExactJsonRead:
    """Internal JSON value plus the exact canonical bytes already validated on read."""

    value: dict[str, Any] | None
    canonical_bytes: bytes | None

    def __post_init__(self) -> None:
        if (self.value is None) != (self.canonical_bytes is None):
            raise ValueError("Pumbility exact JSON read evidence is inconsistent.")


class PumbilityArtifactIntegrityError(ValueError):
    """Report checksum dimensions without exposing an artifact key or digest."""

    def __init__(self, *, digest_matches: bool, byte_size_matches: bool) -> None:
        self.digest_matches = digest_matches
        self.byte_size_matches = byte_size_matches
        super().__init__("A Pumbility JSON artifact failed checksum validation.")


class _ContinuousLeaseHeartbeat:
    """Renew one claimed job lease without sharing database connections across threads."""

    def __init__(
        self,
        callback: Callable[[], None],
        *,
        interval_seconds: float = JOB_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("The job heartbeat interval must be positive.")
        self._callback = callback
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._callback_lock = threading.Lock()
        self._error: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="pumbility-job-heartbeat",
            daemon=True,
        )

    def start(self) -> "_ContinuousLeaseHeartbeat":
        self._thread.start()
        return self

    def _raise_if_failed(self) -> None:
        if self._error is not None:
            raise RuntimeError("The Pumbility job lease heartbeat failed.") from self._error

    def _invoke(self) -> None:
        with self._callback_lock:
            self._raise_if_failed()
            try:
                self._callback()
            except BaseException as error:
                self._error = error
                self._stop.set()
                raise

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._invoke()
            except BaseException:
                return

    def pulse(self) -> None:
        """Renew immediately at a phase boundary and surface background failure."""
        self._invoke()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not threading.current_thread():
            self._thread.join()
        self._raise_if_failed()


class PumbilityArtifactStore:
    """JSON in PostgreSQL; binary objects in the private Supabase bucket."""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        supabase_url: str | None = None,
        service_key: str | None = None,
        bucket: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.database_url = _database_url(database_url)
        self.supabase_url = str(
            supabase_url if supabase_url is not None else os.getenv(SUPABASE_URL_ENV, "")
        ).strip().rstrip("/")
        self.service_key = str(
            service_key if service_key is not None else os.getenv(SERVICE_KEY_ENV, "")
        ).strip()
        self.bucket = str(
            bucket if bucket is not None else os.getenv(STORAGE_BUCKET_ENV, DEFAULT_BUCKET)
        ).strip() or DEFAULT_BUCKET
        self.timeout_seconds = timeout_seconds

    def _storage_headers(self, *, content_type: str | None = None) -> dict[str, str]:
        if not self.supabase_url or not self.service_key:
            raise RuntimeError(
                f"{SUPABASE_URL_ENV} and {SERVICE_KEY_ENV} are required for binary artifacts."
            )
        headers = {
            "Authorization": f"Bearer {self.service_key}",
            "apikey": self.service_key,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    @staticmethod
    def _put_json_row(cursor: Any, pathname: str, payload: Mapping[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        digest, byte_size = _json_metadata(payload, pathname=pathname)
        cursor.execute(
            """
            insert into pumbility.artifacts (
                object_key, media_type, payload_json, storage_bucket,
                storage_object_path, sha256, byte_size, validated_at, updated_at
            ) values (%s, 'application/json', %s, null, null, %s, %s, now(), now())
            on conflict (object_key) do update set
                media_type = excluded.media_type,
                payload_json = excluded.payload_json,
                storage_bucket = null,
                storage_object_path = null,
                sha256 = excluded.sha256,
                byte_size = excluded.byte_size,
                validated_at = excluded.validated_at,
                updated_at = excluded.updated_at
            returning payload_json
            """,
            (pathname, Jsonb(dict(payload)), digest, byte_size),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError(f"Pumbility artifact {pathname!r} was not returned after its write.")
        normalized_digest, normalized_size = _json_metadata(row[0], pathname=pathname)
        cursor.execute(
            """
            update pumbility.artifacts
            set sha256 = %s, byte_size = %s, validated_at = now(), updated_at = now()
            where object_key = %s
            """,
            (normalized_digest, normalized_size, pathname),
        )

    def get_json(self, pathname: str) -> dict[str, Any] | None:
        result = self._read_json(pathname, exact_evidence=False)
        if isinstance(result, _ExactJsonRead):  # pragma: no cover - internal invariant
            raise AssertionError("A public Pumbility JSON read returned internal evidence.")
        return result

    def _get_json_with_evidence(self, pathname: str) -> _ExactJsonRead:
        result = self._read_json(pathname, exact_evidence=True)
        if not isinstance(result, _ExactJsonRead):  # pragma: no cover - internal invariant
            raise AssertionError("A Pumbility exact JSON read omitted its evidence.")
        return result

    def _read_json(
        self, pathname: str, *, exact_evidence: bool
    ) -> dict[str, Any] | None | _ExactJsonRead:
        snapshot_match = CURRENT_SNAPSHOT_RE.fullmatch(pathname)
        snapshot_mix = snapshot_match.group(1) if snapshot_match else None
        if pathname == FROZEN_PHOENIX1_SNAPSHOT_KEY:
            snapshot_mix = "phoenix1"
        if snapshot_mix:
            from scripts.reconcile_pumbility_supabase import _database_snapshot

            with _connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    _assert_schema(cursor)
                fetch_started = _read_phase_started()
                try:
                    result = _database_snapshot(connection, snapshot_mix)
                finally:
                    _record_read_phase("fetch", fetch_started)
            if not exact_evidence:
                return result
            integrity_started = _read_phase_started()
            try:
                body = _canonical_json_bytes(result)
            finally:
                _record_read_phase("integrity", integrity_started)
            return _ExactJsonRead(result, body)
        with _read_cursor(self.database_url) as cursor:
            fetch_started = _read_phase_started()
            try:
                cursor.execute(
                    """
                    select
                        (
                            select value
                            from pumbility.schema_metadata
                            where key = 'migration_version'
                        ),
                        artifact.object_key is not null,
                        artifact.payload_json,
                        artifact.sha256,
                        artifact.byte_size
                    from (values (1)) as required_row(present)
                    left join pumbility.artifacts as artifact
                        on artifact.object_key = %s
                        and artifact.payload_json is not null
                    """,
                    (pathname,),
                )
                row = cursor.fetchone()
            finally:
                _record_read_phase("fetch", fetch_started)
        schema_started = _read_phase_started()
        try:
            _validate_schema_value(row[0] if row else None)
        finally:
            _record_read_phase("schema", schema_started)
        if not row[1]:
            return _ExactJsonRead(None, None) if exact_evidence else None
        decode_started = _read_phase_started()
        try:
            value = row[2]
            if isinstance(value, str):
                value = json.loads(value)
            if not isinstance(value, Mapping):
                raise ValueError(f"Pumbility artifact {pathname!r} is not a JSON object.")
            result = dict(value)
        finally:
            _record_read_phase("decode", decode_started)
        integrity_started = _read_phase_started()
        try:
            body = _canonical_json_bytes(result)
            digest_matches = hashlib.sha256(body).hexdigest() == str(row[3])
            byte_size_matches = len(body) == int(row[4])
        finally:
            _record_read_phase("integrity", integrity_started)
        if not digest_matches or not byte_size_matches:
            raise PumbilityArtifactIntegrityError(
                digest_matches=digest_matches,
                byte_size_matches=byte_size_matches,
            )
        reconstructed = STAGING_SNAPSHOT_RE.fullmatch(pathname) and int(
            result.get("storageSchemaVersion") or 0
        ) >= 2
        if reconstructed:
            prefix = f"{pathname}.players/"
            with _connect(self.database_url) as connection, connection.cursor() as cursor:
                _assert_schema(cursor)
                fetch_started = _read_phase_started()
                try:
                    cursor.execute(
                        """
                        select payload_json, sha256, byte_size
                        from pumbility.artifacts
                        where object_key like %s escape '\\' and payload_json is not null
                        order by object_key
                        """,
                        (
                            prefix.replace("\\", "\\\\")
                            .replace("%", "\\%")
                            .replace("_", "\\_")
                            + "%",
                        ),
                    )
                    checkpoint_rows = cursor.fetchall()
                finally:
                    _record_read_phase("fetch", fetch_started)
            player_checkpoints_by_id: dict[str, dict[str, Any]] = {}
            for raw_payload, raw_digest, raw_size in checkpoint_rows:
                decode_started = _read_phase_started()
                try:
                    checkpoint = (
                        json.loads(raw_payload)
                        if isinstance(raw_payload, str)
                        else raw_payload
                    )
                    if not isinstance(checkpoint, Mapping):
                        raise ValueError(
                            "A Pumbility player checkpoint is not a JSON object."
                        )
                    checkpoint_value = dict(checkpoint)
                finally:
                    _record_read_phase("decode", decode_started)
                integrity_started = _read_phase_started()
                try:
                    checkpoint_body = _canonical_json_bytes(checkpoint_value)
                    digest_matches = hashlib.sha256(checkpoint_body).hexdigest() == str(
                        raw_digest
                    )
                    byte_size_matches = len(checkpoint_body) == int(raw_size)
                finally:
                    _record_read_phase("integrity", integrity_started)
                if not digest_matches or not byte_size_matches:
                    raise PumbilityArtifactIntegrityError(
                        digest_matches=digest_matches,
                        byte_size_matches=byte_size_matches,
                    )
                player = checkpoint_value.get("player")
                player_id = (
                    str(player.get("playerId") or "").strip()
                    if isinstance(player, Mapping)
                    else ""
                )
                if not player_id or player_id in player_checkpoints_by_id:
                    raise RuntimeError("The Pumbility player checkpoint set is invalid.")
                player_checkpoints_by_id[player_id] = checkpoint_value
            completed_ids = {
                str(value)
                for value in result.get("completedPlayerIds", [])
                if str(value)
            }
            if not completed_ids.issubset(player_checkpoints_by_id):
                raise RuntimeError("The Pumbility player checkpoint set is incomplete.")
            result["playerCheckpoints"] = [
                player_checkpoints_by_id[player_id]
                for player_id in sorted(completed_ids)
            ]
        if not exact_evidence:
            return result
        if reconstructed:
            integrity_started = _read_phase_started()
            try:
                body = _canonical_json_bytes(result)
            finally:
                _record_read_phase("integrity", integrity_started)
        return _ExactJsonRead(result, body)

    def put_json(self, pathname: str, payload: Mapping[str, Any]) -> None:
        snapshot_match = CURRENT_SNAPSHOT_RE.fullmatch(pathname)
        canonical_mix = (
            snapshot_match.group(1)
            if snapshot_match
            else "phoenix1" if pathname == FROZEN_PHOENIX1_SNAPSHOT_KEY else None
        )
        if canonical_mix:
            if not _enabled(os.getenv(CANONICAL_SNAPSHOT_WRITE_ENV)):
                raise RuntimeError(
                    f"{CANONICAL_SNAPSHOT_WRITE_ENV} must be enabled before relational snapshot writes."
                )
            # Keep this opt-in during shadowing. The existing synchronizer still
            # supplies whole compatibility checkpoints; the importer turns each
            # one into content-hash-suppressed temporal relational revisions.
            from scripts.backfill_pumbility_supabase import _import_mix

            mix_key = canonical_mix
            manifest = {
                "schemaVersion": int(payload.get("schemaVersion") or 0),
                "mix": payload.get("mix"),
                "captureStartedAtUtc": payload.get("generatedAtUtc"),
                "captureCompletedAtUtc": payload.get("generatedAtUtc"),
                "players": len(payload.get("players", [])),
                "charts": len(payload.get("charts", [])),
                "scoreRows": len(payload.get("scores", [])),
                "source": "runtime-shadow-checkpoint",
            }
            with _connect(self.database_url) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        _assert_schema(cursor)
                    _import_mix(connection, mix_key, manifest, payload)
            return
        stored_payload = (
            _compact_staging_payload(payload)
            if STAGING_SNAPSHOT_RE.fullmatch(pathname)
            else payload
        )
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            self._put_json_row(cursor, pathname, stored_payload)

    def put_sync_checkpoint_players(
        self,
        pathname: str,
        player_checkpoints: Sequence[Mapping[str, Any]],
    ) -> None:
        if not STAGING_SNAPSHOT_RE.fullmatch(pathname):
            raise ValueError("A player checkpoint requires a supported staging pathname.")
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            for player_checkpoint in player_checkpoints:
                player = player_checkpoint.get("player")
                player_id = (
                    str(player.get("playerId") or "").strip()
                    if isinstance(player, Mapping)
                    else ""
                )
                if not player_id:
                    raise ValueError(
                        "A player checkpoint requires a private player identifier."
                    )
                self._put_json_row(
                    cursor,
                    _checkpoint_player_path(pathname, player_id),
                    player_checkpoint,
                )

    @property
    def typed_persistence_enabled(self) -> bool:
        return True

    def persist_typed_generation(
        self,
        *,
        job_external_key: str,
        mix_key: str,
        snapshot: Mapping[str, Any],
        config: Any,
        payload: Mapping[str, Any],
        baselines: Sequence[Mapping[str, Any]],
        contributions: Sequence[Mapping[str, Any]],
        chart_results: Sequence[Mapping[str, Any]],
        model_artifacts: tuple[
            dict[str, Any],
            dict[str, Any],
            bytes,
            list[dict[str, Any]],
            list[dict[str, Any]],
        ]
        | None = None,
    ) -> tuple[Any, Any | None]:
        """Persist the already-computed runtime generation before pointer publication."""
        from scripts.analyze_pumbility_supabase import (
            AnalysisOutput,
            _persist_analysis,
            _read_database_input,
            _sha256,
        )
        from scripts.populate_pumbility_production import _persist_model_generation
        from phoenix2_sync import sanitize_snapshot

        generated_at = _parse_timestamp(payload.get("generatedAtUtc"))
        if generated_at is None:
            raise ValueError("Typed runtime persistence requires a generation timestamp.")
        with _connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                _assert_schema(cursor)
                cursor.execute(
                    "select id from pumbility.jobs where external_key = %s",
                    (job_external_key,),
                )
                job_row = cursor.fetchone()
            if job_row is None:
                raise RuntimeError("The typed runtime job row is unavailable.")
            database_input = _read_database_input(connection, mix_key)
            runtime_snapshot = sanitize_snapshot(snapshot, mix=mix_key)
            # Relational reconstruction intentionally has no capture timestamp.
            # Compare the immutable entity content while retaining every other
            # canonical snapshot field in the source identity.
            runtime_snapshot["generatedAtUtc"] = str(
                database_input.snapshot.get("generatedAtUtc") or ""
            )
            runtime_source_hash = _sha256(runtime_snapshot)
            database_source_hash = _sha256(database_input.snapshot)
            if runtime_source_hash != database_source_hash:
                raise RuntimeError(
                    "The canonical snapshot changed before typed runtime persistence."
                )
            output = AnalysisOutput(
                database_input=database_input,
                config=config,
                started_at=generated_at,
                payload=dict(payload),
                baselines=[dict(row) for row in baselines],
                contributions=[dict(row) for row in contributions],
                chart_results=[dict(row) for row in chart_results],
                source_hash=database_source_hash,
                output_hash=_sha256(payload),
            )
            analysis_run_id = _persist_analysis(
                connection,
                output,
                run_key_prefix="runtime-analysis",
                job_id=job_row[0],
            )
        model_generation_id = None
        if model_artifacts is not None:
            with _connect(self.database_url) as connection:
                inputs = {
                    mix: _read_database_input(connection, mix)
                    for mix in ("phoenix1", "phoenix2")
                }
                model_generation_id = _persist_model_generation(
                    connection,
                    analysis_run_id=analysis_run_id,
                    inputs=inputs,
                    artifacts=model_artifacts,
                )
        return analysis_run_id, model_generation_id

    def put_json_bundle(self, payloads: Mapping[str, Mapping[str, Any]]) -> None:
        """Atomically replace a set of compatibility publication pointers."""
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            for pathname, payload in payloads.items():
                self._put_json_row(cursor, pathname, payload)

    def get_bytes(self, pathname: str) -> bytes | None:
        with _read_cursor(self.database_url) as cursor:
            fetch_started = _read_phase_started()
            try:
                cursor.execute(
                    """
                    select
                        (
                            select value
                            from pumbility.schema_metadata
                            where key = 'migration_version'
                        ),
                        artifact.object_key is not null,
                        artifact.sha256,
                        artifact.byte_size,
                        artifact.storage_object_path
                    from (values (1)) as required_row(present)
                    left join pumbility.artifacts as artifact
                        on artifact.object_key = %s
                        and artifact.storage_object_path is not null
                    """,
                    (pathname,),
                )
                metadata = cursor.fetchone()
            finally:
                _record_read_phase("fetch", fetch_started)
        schema_started = _read_phase_started()
        try:
            _validate_schema_value(metadata[0] if metadata else None)
        finally:
            _record_read_phase("schema", schema_started)
        if not metadata[1]:
            return None
        url = (
            f"{self.supabase_url}/storage/v1/object/authenticated/"
            f"{quote(self.bucket, safe='')}/{quote(str(metadata[4]), safe='/')}"
        )
        fetch_started = _read_phase_started()
        try:
            response = requests.get(
                url, headers=self._storage_headers(), timeout=self.timeout_seconds
            )
            if response.status_code == 404:
                raise ValueError(
                    f"Pumbility binary artifact {pathname!r} is missing from Storage."
                )
            response.raise_for_status()
            value = bytes(response.content)
        finally:
            _record_read_phase("fetch", fetch_started)
        integrity_started = _read_phase_started()
        try:
            digest_matches = hashlib.sha256(value).hexdigest() == str(metadata[2])
            byte_size_matches = len(value) == int(metadata[3])
        finally:
            _record_read_phase("integrity", integrity_started)
        if not digest_matches or not byte_size_matches:
            raise ValueError(f"Pumbility binary artifact {pathname!r} failed checksum validation.")
        return value

    def put_bytes(self, pathname: str, payload: bytes, *, content_type: str) -> None:
        url = (
            f"{self.supabase_url}/storage/v1/object/"
            f"{quote(self.bucket, safe='')}/{quote(pathname, safe='/')}"
        )
        headers = self._storage_headers(content_type=content_type)
        headers["x-upsert"] = "true"
        response = requests.post(
            url, headers=headers, data=payload, timeout=self.timeout_seconds
        )
        response.raise_for_status()
        digest = hashlib.sha256(payload).hexdigest()
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                """
                insert into pumbility.artifacts (
                    object_key, media_type, payload_json, storage_bucket,
                    storage_object_path, sha256, byte_size, validated_at, updated_at
                ) values (%s, %s, null, %s, %s, %s, %s, now(), now())
                on conflict (object_key) do update set
                    media_type = excluded.media_type,
                    payload_json = null,
                    storage_bucket = excluded.storage_bucket,
                    storage_object_path = excluded.storage_object_path,
                    sha256 = excluded.sha256,
                    byte_size = excluded.byte_size,
                    validated_at = excluded.validated_at,
                    updated_at = excluded.updated_at
                """,
                (pathname, content_type, self.bucket, pathname, digest, len(payload)),
            )

    def delete(self, pathnames: str | Sequence[str]) -> None:
        targets = [pathnames] if isinstance(pathnames, str) else list(pathnames)
        if not targets:
            return
        checkpoint_children: list[str] = []
        for pathname in targets:
            if STAGING_SNAPSHOT_RE.fullmatch(pathname):
                checkpoint_children.extend(
                    item.pathname for item in self.list(f"{pathname}.players/")
                )
        targets = list(dict.fromkeys([*targets, *checkpoint_children]))
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                "select object_key, storage_object_path from pumbility.artifacts where object_key = any(%s)",
                (targets,),
            )
            stored = [(str(row[0]), row[1]) for row in cursor.fetchall()]
        binary_paths = [str(path) for _, path in stored if path]
        if binary_paths:
            url = f"{self.supabase_url}/storage/v1/object/{quote(self.bucket, safe='')}"
            response = requests.delete(
                url,
                headers={**self._storage_headers(), "Content-Type": "application/json"},
                json={"prefixes": binary_paths},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                "delete from pumbility.artifacts where object_key = any(%s)", (targets,)
            )

    def list(self, prefix: str) -> list[StoredObject]:
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                """
                select object_key, updated_at
                from pumbility.artifacts
                where object_key like %s escape '\\'
                order by object_key
                """,
                (prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",),
            )
            rows = cursor.fetchall()
        return [StoredObject(str(row[0]), _parse_timestamp(row[1])) for row in rows]

    def enqueue_blob_mirror_event(
        self,
        operation: str,
        pathnames: str | Sequence[str],
        *,
        content_type: str | None = None,
    ) -> str:
        """Persist a reference-only mirror intent; artifact contents stay at rest."""
        from psycopg.types.json import Jsonb

        object_keys = [pathnames] if isinstance(pathnames, str) else list(pathnames)
        if not object_keys:
            raise ValueError("A Blob mirror event requires at least one object reference.")
        event_key = f"blob-mirror:{uuid.uuid4().hex}"
        payload: dict[str, Any] = {
            "operation": operation,
            "objectKeys": [str(value) for value in object_keys],
        }
        if content_type:
            payload["contentType"] = content_type
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                """
                insert into pumbility.outbox_events (
                    event_key, aggregate_type, aggregate_key, event_type, payload
                ) values (%s, 'artifact', 'vercel-rollback-mirror', 'blob_mirror', %s)
                """,
                (event_key, Jsonb(payload)),
            )
        return event_key

    def complete_blob_mirror_event(self, event_key: str) -> None:
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                """
                update pumbility.outbox_events
                set completed_at = now(), claimed_at = null, safe_error = null
                where event_key = %s and completed_at is null
                """,
                (event_key,),
            )

    def retry_blob_mirror_event(self, event_key: str) -> None:
        from psycopg.types.json import Jsonb

        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                """
                update pumbility.outbox_events
                set claimed_at = null,
                    available_at = now() + interval '1 minute',
                    safe_error = %s
                where event_key = %s and completed_at is null
                """,
                (Jsonb({"message": "The Vercel rollback mirror attempt failed."}), event_key),
            )

    def claim_blob_mirror_events(self, *, limit: int = 25) -> list[tuple[str, dict[str, Any]]]:
        """Lease pending mirror intents for idempotent operator replay."""
        bounded_limit = max(1, min(int(limit), 100))
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                """
                with pending as (
                    select id
                    from pumbility.outbox_events
                    where event_type = 'blob_mirror'
                      and completed_at is null
                      and available_at <= now()
                      and (claimed_at is null or claimed_at < now() - interval '15 minutes')
                    order by created_at
                    for update skip locked
                    limit %s
                )
                update pumbility.outbox_events event
                set claimed_at = now(), attempt = event.attempt + 1
                from pending
                where event.id = pending.id
                returning event.event_key, event.payload
                """,
                (bounded_limit,),
            )
            rows = cursor.fetchall()
        result: list[tuple[str, dict[str, Any]]] = []
        for event_key, raw_payload in rows:
            value = raw_payload if not isinstance(raw_payload, str) else json.loads(raw_payload)
            if isinstance(value, Mapping):
                result.append((str(event_key), dict(value)))
        return result


class PumbilityJobStore:
    """Compatibility adapter for current job payloads and active/latest pointers."""

    def __init__(self, *, database_url: str | None = None) -> None:
        self.database_url = _database_url(database_url)
        self.lease_owner = f"compat-{uuid.uuid4().hex}"

    def heartbeat(self, job_id: str) -> None:
        """Renew a lease while preserving the current stage and progress payload."""
        from psycopg.types.json import Jsonb

        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                """
                select id, status, lease_owner, stage, progress
                from pumbility.jobs
                where external_key = %s
                """,
                (job_id,),
            )
            current = cursor.fetchone()
            if current is None or current[1] != "running":
                raise RuntimeError("The Pumbility job is no longer running.")
            if str(current[2] or "") != self.lease_owner:
                raise RuntimeError("The Pumbility job lease is held by another worker.")
            progress = current[4] if isinstance(current[4], Mapping) else {}
            cursor.execute(
                "select pumbility.heartbeat_job(%s, %s, %s, %s, %s)",
                (
                    current[0],
                    self.lease_owner,
                    str(current[3] or "running"),
                    Jsonb(dict(progress)),
                    JOB_LEASE_SECONDS,
                ),
            )
            if cursor.fetchone()[0] is not True:
                raise RuntimeError("The Pumbility job lease expired before heartbeat.")
            cursor.execute(
                """
                update pumbility.jobs
                set payload = jsonb_set(payload, '{updatedAtUtc}', to_jsonb(clock_timestamp()), true)
                where id = %s
                """,
                (current[0],),
            )

    def start_lease_heartbeat(self, job_id: str) -> _ContinuousLeaseHeartbeat:
        return _ContinuousLeaseHeartbeat(lambda: self.heartbeat(job_id)).start()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with _read_cursor(self.database_url) as cursor:
            fetch_started = _read_phase_started()
            try:
                cursor.execute(
                    """
                    select
                        (
                            select value
                            from pumbility.schema_metadata
                            where key = 'migration_version'
                        ),
                        job.external_key is not null,
                        job.payload
                    from (values (1)) as required_row(present)
                    left join pumbility.jobs as job on job.external_key = %s
                    """,
                    (job_id,),
                )
                row = cursor.fetchone()
            finally:
                _record_read_phase("fetch", fetch_started)
        schema_started = _read_phase_started()
        try:
            _validate_schema_value(row[0] if row else None)
        finally:
            _record_read_phase("schema", schema_started)
        if not row[1]:
            return None
        decode_started = _read_phase_started()
        try:
            value = row[2] if not isinstance(row[2], str) else json.loads(row[2])
            return dict(value) if isinstance(value, Mapping) else None
        finally:
            _record_read_phase("decode", decode_started)

    def save(self, job: Mapping[str, Any]) -> dict[str, Any]:
        from psycopg.types.json import Jsonb

        value = dict(job)
        external_key = str(value["id"])
        requested_status = str(value.get("status") or "queued")
        kind = "player_recommendation" if value.get("playerKey") else "analysis"
        stage = str(value.get("stage") or "queued")
        mix_key = str(value.get("mix") or "phoenix2")
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                "select id, status, lease_owner from pumbility.jobs where external_key = %s for update",
                (external_key,),
            )
            current = cursor.fetchone()
            if requested_status == "queued":
                cursor.execute(
                    """
                    insert into pumbility.jobs (
                        external_key, kind, status, stage, mix_key, attempt,
                        payload, created_at, updated_at
                    ) values (
                        %s, %s, 'queued', %s, %s, %s, %s,
                        coalesce(%s::timestamptz, now()), coalesce(%s::timestamptz, now())
                    )
                    on conflict (external_key) do update set
                        stage = excluded.stage,
                        payload = excluded.payload,
                        updated_at = excluded.updated_at
                    """,
                    (
                        external_key,
                        kind,
                        stage,
                        mix_key,
                        int(value.get("attempt") or 0),
                        Jsonb(value),
                        value.get("createdAtUtc"),
                        value.get("updatedAtUtc"),
                    ),
                )
                return value

            if requested_status == "running":
                if current is None or current[1] != "running":
                    cursor.execute(
                        "select pumbility.claim_job(%s, %s, %s, %s, %s, %s, %s)",
                        (
                            external_key,
                            kind,
                            stage,
                            mix_key,
                            Jsonb(value),
                            self.lease_owner,
                            JOB_LEASE_SECONDS,
                        ),
                    )
                    claim = cursor.fetchone()[0]
                    if not isinstance(claim, Mapping) or not claim.get("claimed"):
                        raise RuntimeError("The Pumbility job lease is held by another worker.")
                    job_row = claim.get("job")
                    job_uuid = job_row.get("id") if isinstance(job_row, Mapping) else None
                else:
                    if str(current[2] or "") != self.lease_owner:
                        raise RuntimeError("The Pumbility job lease is held by another worker.")
                    job_uuid = current[0]
                    cursor.execute(
                        "select pumbility.heartbeat_job(%s, %s, %s, %s, %s)",
                        (
                            job_uuid,
                            self.lease_owner,
                            stage,
                            Jsonb(dict(value.get("progress") or {})),
                            JOB_LEASE_SECONDS,
                        ),
                    )
                    if cursor.fetchone()[0] is not True:
                        raise RuntimeError("The Pumbility job lease expired before heartbeat.")
                cursor.execute(
                    "update pumbility.jobs set payload = %s, updated_at = coalesce(%s::timestamptz, now()) where id = %s",
                    (Jsonb(value), value.get("updatedAtUtc"), job_uuid),
                )
                return value

            if requested_status in {"completed", "failed"} and current is not None and current[1] == "running":
                job_uuid = current[0]
                owns_lease = str(current[2] or "") == self.lease_owner
                if owns_lease:
                    safe_error = (
                        None
                        if requested_status == "completed"
                        else Jsonb({"message": "The job failed; see the compatibility payload."})
                    )
                    cursor.execute(
                        "select pumbility.complete_job(%s, %s, %s, %s, %s, %s::timestamptz)",
                        (
                            job_uuid,
                            self.lease_owner,
                            requested_status == "completed",
                            stage,
                            safe_error,
                            value.get("retryAllowedAtUtc"),
                        ),
                    )
                    if cursor.fetchone()[0] is not True:
                        raise RuntimeError("The Pumbility job could not be completed under its lease.")
                elif requested_status == "failed":
                    cursor.execute("select pumbility.cancel_job(%s, null)", (job_uuid,))
                else:
                    raise RuntimeError("The Pumbility job lease is held by another worker.")
                cursor.execute(
                    "update pumbility.jobs set payload = %s, stage = %s, updated_at = coalesce(%s::timestamptz, now()) where id = %s",
                    (Jsonb(value), stage, value.get("updatedAtUtc"), job_uuid),
                )
                return value

            cursor.execute(
                """
                update pumbility.jobs set
                    status = %s,
                    stage = %s,
                    payload = %s,
                    retry_at = %s::timestamptz,
                    completed_at = coalesce(%s::timestamptz, completed_at, now()),
                    updated_at = coalesce(%s::timestamptz, now())
                where external_key = %s and status <> 'running'
                """,
                (
                    requested_status,
                    stage,
                    Jsonb(value),
                    value.get("retryAllowedAtUtc"),
                    value.get("completedAtUtc"),
                    value.get("updatedAtUtc"),
                    external_key,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("The Pumbility job transition was rejected.")
        return value

    def _get_head(self, name: str) -> str | None:
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                "select j.external_key from pumbility.job_heads h join pumbility.jobs j on j.id = h.job_id where h.name = %s",
                (name,),
            )
            row = cursor.fetchone()
        return str(row[0]) if row else None

    def _set_head(self, name: str, job_id: str | None) -> None:
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            if job_id is None:
                cursor.execute("delete from pumbility.job_heads where name = %s", (name,))
                return
            cursor.execute(
                """
                insert into pumbility.job_heads (name, job_id, updated_at)
                select %s, id, now() from pumbility.jobs where external_key = %s
                on conflict (name) do update set job_id = excluded.job_id, updated_at = excluded.updated_at
                """,
                (name, job_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Cannot point {name!r} at missing job {job_id!r}.")

    def active_job_id(self) -> str | None:
        return self._get_head("active:analysis")

    def set_active_job_id(self, job_id: str | None) -> None:
        self._set_head("active:analysis", job_id)

    def latest_job_id(self, mix: Any = "phoenix2") -> str | None:
        from mix_registry import resolve_mix

        return self._get_head(f"latest:{resolve_mix(mix).key}")

    def set_latest_job_id(self, job_id: str, mix: Any = "phoenix2") -> None:
        from mix_registry import resolve_mix

        self._set_head(f"latest:{resolve_mix(mix).key}", job_id)


class ShadowJsonStore:
    """Read the legacy store while mirroring mutations into PostgreSQL/Storage."""

    def __init__(self, primary: Any, shadow: Any, *, strict: bool = False) -> None:
        self.primary = primary
        self.shadow = shadow
        self.strict = strict

    def _mirror(self, method: str, *args: Any, **kwargs: Any) -> None:
        if (
            method == "put_json"
            and args
            and (
                CURRENT_SNAPSHOT_RE.fullmatch(str(args[0]))
                or str(args[0]) == FROZEN_PHOENIX1_SNAPSHOT_KEY
            )
            and not _enabled(os.getenv(CANONICAL_SNAPSHOT_WRITE_ENV))
        ):
            return
        try:
            getattr(self.shadow, method)(*args, **kwargs)
        except Exception:
            if self.strict:
                raise

    def get_json(self, pathname: str) -> dict[str, Any] | None:
        return self.primary.get_json(pathname)

    def put_json(self, pathname: str, payload: Mapping[str, Any]) -> None:
        self.primary.put_json(pathname, payload)
        self._mirror("put_json", pathname, payload)

    def put_sync_checkpoint_players(
        self, pathname: str, player_checkpoints: Sequence[Mapping[str, Any]]
    ) -> None:
        writer = getattr(self.shadow, "put_sync_checkpoint_players", None)
        if writer is None:
            return
        try:
            writer(pathname, player_checkpoints)
        except Exception:
            if self.strict:
                raise

    @property
    def typed_persistence_enabled(self) -> bool:
        return _enabled(os.getenv(CANONICAL_SNAPSHOT_WRITE_ENV))

    def persist_typed_generation(self, **kwargs: Any) -> tuple[Any, Any | None]:
        if not self.typed_persistence_enabled:
            raise RuntimeError(
                f"{CANONICAL_SNAPSHOT_WRITE_ENV} must be enabled before typed persistence."
            )
        return self.shadow.persist_typed_generation(**kwargs)

    def put_json_bundle(self, payloads: Mapping[str, Mapping[str, Any]]) -> None:
        # Preserve the exact legacy store's sequential behavior while the
        # Supabase mirror receives one atomic transaction.
        for pathname, payload in payloads.items():
            self.primary.put_json(pathname, payload)
        if hasattr(self.shadow, "put_json_bundle"):
            self._mirror("put_json_bundle", payloads)
        else:
            for pathname, payload in payloads.items():
                self._mirror("put_json", pathname, payload)

    def get_bytes(self, pathname: str) -> bytes | None:
        return self.primary.get_bytes(pathname)

    def put_bytes(self, pathname: str, payload: bytes, *, content_type: str) -> None:
        self.primary.put_bytes(pathname, payload, content_type=content_type)
        self._mirror("put_bytes", pathname, payload, content_type=content_type)

    def delete(self, pathnames: str | Sequence[str]) -> None:
        self.primary.delete(pathnames)
        self._mirror("delete", pathnames)

    def list(self, prefix: str) -> list[Any]:
        return self.primary.list(prefix)


class ShadowJobStore:
    """Legacy-primary job store with fail-open shadow writes."""

    def __init__(self, primary: Any, shadow: Any, *, strict: bool = False) -> None:
        self.primary = primary
        self.shadow = shadow
        self.strict = strict

    def _mirror(self, method: str, *args: Any) -> None:
        try:
            getattr(self.shadow, method)(*args)
        except Exception:
            if self.strict:
                raise

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self.primary.get(job_id)

    def save(self, job: Mapping[str, Any]) -> dict[str, Any]:
        value = self.primary.save(job)
        self._mirror("save", value)
        return value

    def start_lease_heartbeat(self, job_id: str) -> _ContinuousLeaseHeartbeat:
        """Keep the Supabase shadow lease alive without changing the legacy primary."""
        return _ContinuousLeaseHeartbeat(
            lambda: self._mirror("heartbeat", job_id)
        ).start()

    def active_job_id(self) -> str | None:
        return self.primary.active_job_id()

    def set_active_job_id(self, job_id: str | None) -> None:
        self.primary.set_active_job_id(job_id)
        self._mirror("set_active_job_id", job_id)

    def latest_job_id(self, mix: Any = "phoenix2") -> str | None:
        return self.primary.latest_job_id(mix)

    def set_latest_job_id(self, job_id: str, mix: Any = "phoenix2") -> None:
        self.primary.set_latest_job_id(job_id, mix)
        try:
            self.shadow.set_latest_job_id(job_id, mix)
        except Exception:
            if self.strict:
                raise


def _timed_read(
    reader: Callable[..., Any], *args: Any
) -> tuple[Any, Exception | None, float, dict[str, float]]:
    previous_phases = getattr(_read_telemetry, "phases", None)
    phases: dict[str, float] = {}
    _read_telemetry.phases = phases
    started = time.perf_counter()
    try:
        try:
            value, error = reader(*args), None
        except Exception as caught:
            value, error = None, caught
        return value, error, (time.perf_counter() - started) * 1000, dict(phases)
    finally:
        if previous_phases is None:
            del _read_telemetry.phases
        else:
            _read_telemetry.phases = previous_phases


class CanaryJsonStore:
    """Dual-read one public domain and serve the candidate only after equality."""

    def __init__(self, authoritative: Any, candidate: Any, *, domain: str) -> None:
        self.authoritative = authoritative
        self.candidate = candidate
        self.domain = domain

    def _read(self, method: str, *args: Any) -> Any:
        overall_started = time.perf_counter()
        candidate_reader = getattr(self.candidate, method)
        if method == "get_json" and getattr(
            type(self.candidate), "_get_json_with_evidence", None
        ) is not None:
            candidate_reader = self.candidate._get_json_with_evidence
        with ThreadPoolExecutor(max_workers=2) as executor:
            authoritative_future = executor.submit(
                _timed_read, getattr(self.authoritative, method), *args
            )
            candidate_future = executor.submit(
                _timed_read, candidate_reader, *args
            )
            authoritative, authoritative_error, authoritative_ms, authoritative_phases = (
                authoritative_future.result()
            )
            candidate, candidate_error, candidate_ms, candidate_phases = (
                candidate_future.result()
            )

        def emit(
            outcome: str,
            *,
            comparison_ms: float | None = None,
            error: Exception | None = None,
            authority_error: Exception | None = None,
        ) -> None:
            _safe_rollout_event(
                operation=method,
                domain=self.domain,
                outcome=outcome,
                authoritative_ms=authoritative_ms,
                candidate_ms=candidate_ms,
                authoritative_phases=authoritative_phases,
                candidate_phases=candidate_phases,
                comparison_ms=comparison_ms,
                overall_ms=(time.perf_counter() - overall_started) * 1000,
                authoritative_error=authority_error,
                candidate_error=error,
            )

        if authoritative_error is not None:
            emit("authority-error", authority_error=authoritative_error)
            raise authoritative_error
        if candidate_error is not None:
            emit("candidate-error-fallback", error=candidate_error)
            return authoritative
        candidate_value = (
            candidate.value if isinstance(candidate, _ExactJsonRead) else candidate
        )
        comparison_started = time.perf_counter()
        try:
            matches = (
                _equivalent_to_exact_json(authoritative, candidate)
                if isinstance(candidate, _ExactJsonRead)
                else _equivalent(authoritative, candidate)
            )
        except Exception:
            emit(
                "comparison-error-fallback",
                comparison_ms=(time.perf_counter() - comparison_started) * 1000,
            )
            return authoritative
        comparison_ms = (time.perf_counter() - comparison_started) * 1000
        if not matches:
            emit("mismatch-fallback", comparison_ms=comparison_ms)
            return authoritative
        emit("candidate-served", comparison_ms=comparison_ms)
        return candidate_value

    def get_json(self, pathname: str) -> dict[str, Any] | None:
        return self._read("get_json", pathname)

    def get_bytes(self, pathname: str) -> bytes | None:
        return self._read("get_bytes", pathname)

    def put_json(self, pathname: str, payload: Mapping[str, Any]) -> None:
        self.authoritative.put_json(pathname, payload)

    def put_json_bundle(self, payloads: Mapping[str, Mapping[str, Any]]) -> None:
        writer = getattr(self.authoritative, "put_json_bundle", None)
        if writer is not None:
            writer(payloads)
            return
        for pathname, payload in payloads.items():
            self.authoritative.put_json(pathname, payload)

    def put_bytes(self, pathname: str, payload: bytes, *, content_type: str) -> None:
        self.authoritative.put_bytes(pathname, payload, content_type=content_type)

    def delete(self, pathnames: str | Sequence[str]) -> None:
        self.authoritative.delete(pathnames)

    def list(self, prefix: str) -> list[Any]:
        return self.authoritative.list(prefix)


class CanaryJobStore:
    """Dual-read job payloads while leaving all mutations on the authority."""

    def __init__(self, authoritative: Any, candidate: Any, *, domain: str) -> None:
        self.authoritative = authoritative
        self.candidate = candidate
        self.domain = domain

    def get(self, job_id: str) -> dict[str, Any] | None:
        overall_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=2) as executor:
            authoritative_future = executor.submit(
                _timed_read, self.authoritative.get, job_id
            )
            candidate_future = executor.submit(_timed_read, self.candidate.get, job_id)
            authoritative, authoritative_error, authoritative_ms, authoritative_phases = (
                authoritative_future.result()
            )
            candidate, candidate_error, candidate_ms, candidate_phases = (
                candidate_future.result()
            )

        def emit(
            outcome: str,
            *,
            comparison_ms: float | None = None,
            error: Exception | None = None,
            authority_error: Exception | None = None,
        ) -> None:
            _safe_rollout_event(
                operation="get-job",
                domain=self.domain,
                outcome=outcome,
                authoritative_ms=authoritative_ms,
                candidate_ms=candidate_ms,
                authoritative_phases=authoritative_phases,
                candidate_phases=candidate_phases,
                comparison_ms=comparison_ms,
                overall_ms=(time.perf_counter() - overall_started) * 1000,
                authoritative_error=authority_error,
                candidate_error=error,
            )

        if authoritative_error is not None:
            emit("authority-error", authority_error=authoritative_error)
            raise authoritative_error
        if candidate_error is not None:
            emit("candidate-error-fallback", error=candidate_error)
            return authoritative
        comparison_started = time.perf_counter()
        try:
            matches = _equivalent(authoritative, candidate)
        except Exception:
            emit(
                "comparison-error-fallback",
                comparison_ms=(time.perf_counter() - comparison_started) * 1000,
            )
            return authoritative
        comparison_ms = (time.perf_counter() - comparison_started) * 1000
        if not matches:
            emit("mismatch-fallback", comparison_ms=comparison_ms)
            return authoritative
        emit("candidate-served", comparison_ms=comparison_ms)
        return candidate

    def save(self, job: Mapping[str, Any]) -> dict[str, Any]:
        return self.authoritative.save(job)

    def start_lease_heartbeat(self, job_id: str) -> Any:
        return self.authoritative.start_lease_heartbeat(job_id)

    def active_job_id(self) -> str | None:
        return self.authoritative.active_job_id()

    def set_active_job_id(self, job_id: str | None) -> None:
        self.authoritative.set_active_job_id(job_id)

    def latest_job_id(self, mix: Any = "phoenix2") -> str | None:
        return self.authoritative.latest_job_id(mix)

    def set_latest_job_id(self, job_id: str, mix: Any = "phoenix2") -> None:
        self.authoritative.set_latest_job_id(job_id, mix)


class SupabasePrimaryJsonStore:
    """Supabase-primary store with independently gated Vercel mirror and fallback."""

    def __init__(
        self,
        primary: Any,
        legacy: Any,
        *,
        mirror_enabled: bool,
        fallback_enabled: bool,
    ) -> None:
        self.primary = primary
        self.legacy = legacy
        self.mirror_enabled = mirror_enabled
        self.fallback_enabled = fallback_enabled

    def _read(self, method: str, *args: Any) -> Any:
        started = time.perf_counter()
        try:
            value = getattr(self.primary, method)(*args)
            if value is not None or not self.fallback_enabled:
                return value
            outcome = "primary-missing-fallback"
        except Exception:
            if not self.fallback_enabled:
                raise
            outcome = "primary-error-fallback"
        primary_ms = (time.perf_counter() - started) * 1000
        fallback_started = time.perf_counter()
        value = getattr(self.legacy, method)(*args)
        fallback_ms = (time.perf_counter() - fallback_started) * 1000
        _safe_rollout_event(
            operation=method,
            domain="supabase-primary",
            outcome=outcome,
            authoritative_ms=primary_ms,
            candidate_ms=fallback_ms,
        )
        return value

    def _mirror(
        self,
        method: str,
        *args: Any,
        event_paths: str | Sequence[str],
        event_content_type: str | None = None,
        **kwargs: Any,
    ) -> None:
        if not self.mirror_enabled:
            return
        event_key = self.primary.enqueue_blob_mirror_event(
            method, event_paths, content_type=event_content_type
        )
        started = time.perf_counter()
        try:
            getattr(self.legacy, method)(*args, **kwargs)
        except Exception:
            try:
                self.primary.retry_blob_mirror_event(event_key)
            except Exception:
                _safe_rollout_event(
                    operation=method,
                    domain="vercel-mirror",
                    outcome="outbox-retry-error",
                    authoritative_ms=0.0,
                    candidate_ms=(time.perf_counter() - started) * 1000,
                )
            _safe_rollout_event(
                operation=method,
                domain="vercel-mirror",
                outcome="mirror-error",
                authoritative_ms=0.0,
                candidate_ms=(time.perf_counter() - started) * 1000,
            )
            return
        try:
            self.primary.complete_blob_mirror_event(event_key)
        except Exception:
            _safe_rollout_event(
                operation=method,
                domain="vercel-mirror",
                outcome="outbox-completion-error",
                authoritative_ms=0.0,
                candidate_ms=(time.perf_counter() - started) * 1000,
            )
            return
        _safe_rollout_event(
            operation=method,
            domain="vercel-mirror",
            outcome="mirror-complete",
            authoritative_ms=0.0,
            candidate_ms=(time.perf_counter() - started) * 1000,
        )

    def get_json(self, pathname: str) -> dict[str, Any] | None:
        return self._read("get_json", pathname)

    def get_bytes(self, pathname: str) -> bytes | None:
        return self._read("get_bytes", pathname)

    def put_json(self, pathname: str, payload: Mapping[str, Any]) -> None:
        self.primary.put_json(pathname, payload)
        self._mirror("put_json", pathname, payload, event_paths=pathname)

    def put_sync_checkpoint_players(
        self, pathname: str, player_checkpoints: Sequence[Mapping[str, Any]]
    ) -> None:
        self.primary.put_sync_checkpoint_players(pathname, player_checkpoints)

    @property
    def typed_persistence_enabled(self) -> bool:
        return True

    def persist_typed_generation(self, **kwargs: Any) -> tuple[Any, Any | None]:
        return self.primary.persist_typed_generation(**kwargs)

    def put_json_bundle(self, payloads: Mapping[str, Mapping[str, Any]]) -> None:
        self.primary.put_json_bundle(payloads)
        legacy_bundle = getattr(self.legacy, "put_json_bundle", None)
        if legacy_bundle is not None:
            self._mirror(
                "put_json_bundle", payloads, event_paths=list(payloads.keys())
            )
            return
        for pathname, payload in payloads.items():
            self._mirror("put_json", pathname, payload, event_paths=pathname)

    def put_bytes(self, pathname: str, payload: bytes, *, content_type: str) -> None:
        self.primary.put_bytes(pathname, payload, content_type=content_type)
        self._mirror(
            "put_bytes",
            pathname,
            payload,
            event_content_type=content_type,
            content_type=content_type,
            event_paths=pathname,
        )

    def delete(self, pathnames: str | Sequence[str]) -> None:
        self.primary.delete(pathnames)
        self._mirror("delete", pathnames, event_paths=pathnames)

    def list(self, prefix: str) -> list[Any]:
        return self._read("list", prefix)


def drain_blob_mirror_outbox(
    primary: PumbilityArtifactStore, legacy: Any, *, limit: int = 25
) -> tuple[int, int]:
    """Replay reference-only mirror events. Returns ``(completed, failed)``."""
    completed = 0
    failed = 0
    for event_key, payload in primary.claim_blob_mirror_events(limit=limit):
        operation = str(payload.get("operation") or "")
        raw_keys = payload.get("objectKeys")
        object_keys = (
            [str(value) for value in raw_keys]
            if isinstance(raw_keys, list) and all(isinstance(value, str) for value in raw_keys)
            else []
        )
        try:
            if not object_keys:
                raise ValueError("The Blob mirror event has no object references.")
            if operation == "put_json":
                for pathname in object_keys:
                    value = primary.get_json(pathname)
                    if value is None:
                        raise RuntimeError("A mirrored JSON artifact is unavailable.")
                    legacy.put_json(pathname, value)
            elif operation == "put_json_bundle":
                values: dict[str, Mapping[str, Any]] = {}
                for pathname in object_keys:
                    value = primary.get_json(pathname)
                    if value is None:
                        raise RuntimeError("A mirrored JSON artifact bundle is incomplete.")
                    values[pathname] = value
                bundle_writer = getattr(legacy, "put_json_bundle", None)
                if bundle_writer is not None:
                    bundle_writer(values)
                else:
                    for pathname, value in values.items():
                        legacy.put_json(pathname, value)
            elif operation == "put_bytes":
                content_type = str(payload.get("contentType") or "application/octet-stream")
                for pathname in object_keys:
                    value = primary.get_bytes(pathname)
                    if value is None:
                        raise RuntimeError("A mirrored binary artifact is unavailable.")
                    legacy.put_bytes(pathname, value, content_type=content_type)
            elif operation == "delete":
                legacy.delete(object_keys)
            else:
                raise ValueError("The Blob mirror event operation is unsupported.")
            primary.complete_blob_mirror_event(event_key)
            completed += 1
        except Exception:
            primary.retry_blob_mirror_event(event_key)
            failed += 1
    return completed, failed


def select_json_store(
    legacy_factory: Callable[[], Any], *, canary_domain: str | None = None
) -> Any:
    backend = configured_backend()
    canaries = configured_read_canaries()
    canary_enabled = _read_canary_enabled(canary_domain, canaries)
    if backend == "vercel":
        legacy = legacy_factory()
        if not canary_enabled:
            return legacy
        return CanaryJsonStore(legacy, PumbilityArtifactStore(), domain=str(canary_domain))
    supabase = PumbilityArtifactStore()
    if backend == "supabase":
        if canaries:
            raise RuntimeError(
                f"{READ_CANARY_ENV} must be empty when {BACKEND_ENV}=supabase."
            )
        mirror_enabled = _enabled(os.getenv(BLOB_MIRROR_ENV))
        fallback_enabled = _enabled(os.getenv(BLOB_READ_FALLBACK_ENV))
        if not mirror_enabled and not fallback_enabled:
            return supabase
        return SupabasePrimaryJsonStore(
            supabase,
            legacy_factory(),
            mirror_enabled=mirror_enabled,
            fallback_enabled=fallback_enabled,
        )
    legacy = legacy_factory()
    primary = (
        CanaryJsonStore(legacy, supabase, domain=str(canary_domain))
        if canary_enabled
        else legacy
    )
    return ShadowJsonStore(
        primary, supabase, strict=_enabled(os.getenv(SHADOW_STRICT_ENV))
    )


def select_job_store(
    legacy_factory: Callable[[], Any], *, canary_domain: str | None = None
) -> Any:
    backend = configured_backend()
    canaries = configured_read_canaries()
    canary_enabled = _read_canary_enabled(canary_domain, canaries)
    if backend == "vercel":
        legacy = legacy_factory()
        if not canary_enabled:
            return legacy
        return CanaryJobStore(legacy, PumbilityJobStore(), domain=str(canary_domain))
    supabase = PumbilityJobStore()
    if backend == "supabase":
        if canaries:
            raise RuntimeError(
                f"{READ_CANARY_ENV} must be empty when {BACKEND_ENV}=supabase."
            )
        return supabase
    legacy = legacy_factory()
    primary = (
        CanaryJobStore(legacy, supabase, domain=str(canary_domain))
        if canary_enabled
        else legacy
    )
    return ShadowJobStore(
        primary, supabase, strict=_enabled(os.getenv(SHADOW_STRICT_ENV))
    )
