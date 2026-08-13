"""Opt-in PostgreSQL and Supabase Storage persistence adapters.

The default backend remains the existing Vercel stores.  This module is kept
free of imports from ``analysis_runtime`` so the runtime can select it lazily
without creating a circular dependency.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote, urlparse

import requests


BACKEND_ENV = "PUMBILITY_DATA_BACKEND"
DATABASE_URL_ENV = "PUMBILITY_DATABASE_URL"
SUPABASE_URL_ENV = "PUMBILITY_SUPABASE_URL"
SERVICE_KEY_ENV = "PUMBILITY_SUPABASE_SERVICE_ROLE_KEY"
STORAGE_BUCKET_ENV = "PUMBILITY_STORAGE_BUCKET"
SHADOW_STRICT_ENV = "PUMBILITY_SHADOW_STRICT"
CANONICAL_SNAPSHOT_WRITE_ENV = "PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED"
DEFAULT_BUCKET = "pumbility-artifacts"
EXPECTED_PUMBILITY_MIGRATION = "20260813010000"
VALID_BACKENDS = frozenset({"vercel", "shadow", "supabase"})
CURRENT_SNAPSHOT_RE = re.compile(r"^analysis/private/(phoenix1|phoenix2)-current\.json$")
FROZEN_PHOENIX1_SNAPSHOT_KEY = "analysis/private/phoenix1.json"
JOB_LEASE_SECONDS = 800
JOB_HEARTBEAT_INTERVAL_SECONDS = 60.0


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
    try:
        import psycopg
    except ImportError as error:  # pragma: no cover - environment diagnostic
        raise RuntimeError(
            "The Supabase backend requires psycopg. Run `uv sync --frozen`."
        ) from error
    # Transaction-pooled runtime connections must not create named prepared
    # statements that can be routed to another server connection.
    return psycopg.connect(database_url, prepare_threshold=None)


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
    cursor.execute(
        "select value from pumbility.schema_metadata where key = 'migration_version'"
    )
    row = cursor.fetchone()
    actual = str(row[0]) if row else "missing"
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
        snapshot_match = CURRENT_SNAPSHOT_RE.fullmatch(pathname)
        snapshot_mix = snapshot_match.group(1) if snapshot_match else None
        if pathname == FROZEN_PHOENIX1_SNAPSHOT_KEY:
            snapshot_mix = "phoenix1"
        if snapshot_mix:
            from scripts.reconcile_pumbility_supabase import _database_snapshot

            with _connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    _assert_schema(cursor)
                return _database_snapshot(connection, snapshot_mix)
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                "select payload_json, sha256, byte_size from pumbility.artifacts where object_key = %s and payload_json is not null",
                (pathname,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        value = row[0]
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, Mapping):
            raise ValueError(f"Pumbility artifact {pathname!r} is not a JSON object.")
        result = dict(value)
        body = _canonical_json_bytes(result)
        digest_matches = hashlib.sha256(body).hexdigest() == str(row[1])
        byte_size_matches = len(body) == int(row[2])
        if not digest_matches or not byte_size_matches:
            raise PumbilityArtifactIntegrityError(
                digest_matches=digest_matches,
                byte_size_matches=byte_size_matches,
            )
        return result

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
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            self._put_json_row(cursor, pathname, payload)

    def put_json_bundle(self, payloads: Mapping[str, Mapping[str, Any]]) -> None:
        """Atomically replace a set of compatibility publication pointers."""
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            for pathname, payload in payloads.items():
                self._put_json_row(cursor, pathname, payload)

    def get_bytes(self, pathname: str) -> bytes | None:
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                """
                select sha256, byte_size, storage_object_path
                from pumbility.artifacts
                where object_key = %s and storage_object_path is not null
                """,
                (pathname,),
            )
            metadata = cursor.fetchone()
        if metadata is None:
            return None
        url = (
            f"{self.supabase_url}/storage/v1/object/authenticated/"
            f"{quote(self.bucket, safe='')}/{quote(str(metadata[2]), safe='/')}"
        )
        response = requests.get(
            url, headers=self._storage_headers(), timeout=self.timeout_seconds
        )
        if response.status_code == 404:
            raise ValueError(f"Pumbility binary artifact {pathname!r} is missing from Storage.")
        response.raise_for_status()
        value = bytes(response.content)
        if hashlib.sha256(value).hexdigest() != str(metadata[0]) or len(value) != int(metadata[1]):
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
        with _connect(self.database_url) as connection, connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                "select payload from pumbility.jobs where external_key = %s", (job_id,)
            )
            row = cursor.fetchone()
        if row is None:
            return None
        value = row[0] if not isinstance(row[0], str) else json.loads(row[0])
        return dict(value) if isinstance(value, Mapping) else None

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


def select_json_store(legacy_factory: Callable[[], Any]) -> Any:
    backend = configured_backend()
    if backend == "vercel":
        return legacy_factory()
    supabase = PumbilityArtifactStore()
    if backend == "supabase":
        return supabase
    return ShadowJsonStore(
        legacy_factory(), supabase, strict=_enabled(os.getenv(SHADOW_STRICT_ENV))
    )


def select_job_store(legacy_factory: Callable[[], Any]) -> Any:
    backend = configured_backend()
    if backend == "vercel":
        return legacy_factory()
    supabase = PumbilityJobStore()
    if backend == "supabase":
        return supabase
    return ShadowJobStore(
        legacy_factory(), supabase, strict=_enabled(os.getenv(SHADOW_STRICT_ENV))
    )
