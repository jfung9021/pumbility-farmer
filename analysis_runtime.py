"""Durable analysis coordination and private artifact persistence."""

from __future__ import annotations

import atexit
import gc
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from phoenix2_sync import (
    analyzer_input,
    isoformat_utc,
    sanitize_snapshot,
    synchronize_mix_snapshot,
    synchronize_phoenix2_snapshot,
    utc_now,
)
from mix_registry import DEFAULT_MIX_KEY, MixSpec, resolve_mix
from pumbility_contract import (
    PLAYER_REFRESH_STORAGE_SCHEMA_VERSION,
    RECOMMENDATION_SCHEMA_VERSION,
    SCRIPT_VERSION,
    combined_tier_blob_path,
    phoenix1_snapshot_path,
    recommendation_blob_path,
    recommendation_generation_key,
    recommendation_index_path,
    recommendation_model_path,
    recommendation_phoenix1_shard_path,
    recommendation_phoenix2_shard_path,
    recommendation_shard_prefix,
    recommendation_score_model_path,
)


def PiuScoresClient(*args: Any, **kwargs: Any) -> Any:  # noqa: N802 - compatibility API
    from piu_misgrade_analyzer import PiuScoresClient as implementation

    return implementation(*args, **kwargs)


def analyze_snapshot(*args: Any, **kwargs: Any) -> Any:
    from piu_misgrade_analyzer import analyze_snapshot as implementation

    return implementation(*args, **kwargs)


def build_web_payload(*args: Any, **kwargs: Any) -> Any:
    from piu_misgrade_analyzer import build_web_payload as implementation

    return implementation(*args, **kwargs)


def load_snapshot(*args: Any, **kwargs: Any) -> Any:
    from piu_misgrade_analyzer import load_snapshot as implementation

    return implementation(*args, **kwargs)


def build_combined_chart_results(*args: Any, **kwargs: Any) -> Any:
    from piu_recommendations import build_combined_chart_results as implementation

    return implementation(*args, **kwargs)


def build_combined_tier_payload(*args: Any, **kwargs: Any) -> Any:
    from piu_recommendations import build_combined_tier_payload as implementation

    return implementation(*args, **kwargs)


def build_recommendation_model_artifacts(*args: Any, **kwargs: Any) -> Any:
    from recommendation_refresh import (
        build_recommendation_model_artifacts as implementation,
    )

    return implementation(*args, **kwargs)


def publish_recommendation_model_artifacts(*args: Any, **kwargs: Any) -> Any:
    from recommendation_refresh import (
        publish_recommendation_model_artifacts as implementation,
    )

    return implementation(*args, **kwargs)


LEGACY_LATEST_BLOB_PATH = "analysis/latest.json"


def latest_blob_path(mix: str | MixSpec = DEFAULT_MIX_KEY) -> str:
    spec = resolve_mix(mix)
    return f"analysis/{spec.slug}/latest.json"


def current_snapshot_path(mix: str | MixSpec = DEFAULT_MIX_KEY) -> str:
    spec = resolve_mix(mix)
    return f"analysis/private/{spec.slug}-current.json"


def runs_prefix(mix: str | MixSpec = DEFAULT_MIX_KEY) -> str:
    spec = resolve_mix(mix)
    return f"analysis/{spec.slug}/runs/"


def staging_prefix(mix: str | MixSpec = DEFAULT_MIX_KEY) -> str:
    spec = resolve_mix(mix)
    return f"analysis/{spec.slug}/staging/"


def typed_checkpoint_path(
    job_id: str, mix: str | MixSpec = DEFAULT_MIX_KEY
) -> str:
    spec = resolve_mix(mix)
    return f"analysis/private/runtime-checkpoints/{spec.slug}/{job_id}.json"


def typed_checkpoint_shard_prefix(
    job_id: str, mix: str | MixSpec = DEFAULT_MIX_KEY
) -> str:
    spec = resolve_mix(mix)
    return f"analysis/private/runtime-checkpoint-shards/{spec.slug}/{job_id}/"


def typed_checkpoint_shard_root_prefix(
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> str:
    spec = resolve_mix(mix)
    return f"analysis/private/runtime-checkpoint-shards/{spec.slug}/"


def typed_checkpoint_shard_path(
    job_id: str,
    dataset: str,
    shard: int,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> str:
    if dataset not in {"baselines", "contributions", "chartResults"}:
        raise ValueError("The typed checkpoint dataset is invalid.")
    if shard < 0:
        raise ValueError("The typed checkpoint shard number must be nonnegative.")
    return f"{typed_checkpoint_shard_prefix(job_id, mix)}{dataset}/{shard:06d}.json"


def typed_checkpoint_snapshot_path(
    job_id: str, mix: str | MixSpec = DEFAULT_MIX_KEY
) -> str:
    return f"{typed_checkpoint_shard_prefix(job_id, mix)}snapshot.json"


# Backward-compatible constants refer to the default Phoenix 2 dataset.
LATEST_BLOB_PATH = latest_blob_path()
CURRENT_SNAPSHOT_PATH = current_snapshot_path()
RUNS_PREFIX = runs_prefix()
STAGING_PREFIX = staging_prefix()
JOB_TTL_SECONDS = 24 * 60 * 60
# Temporarily disabled: successful runs do not impose a manual-refresh cooldown.
FRESHNESS = timedelta(0)
FAILED_RETRY_DELAY = timedelta(minutes=5)
ACTIVE_JOB_STALE_AFTER = timedelta(minutes=5)
STAGING_MAX_AGE = timedelta(hours=24)
TYPED_CHECKPOINT_SCHEMA_VERSION = 4
TYPED_CHECKPOINT_SHARD_SCHEMA_VERSION = 1
TYPED_CHECKPOINT_ROW_LIMIT = 5_000
TYPED_CHECKPOINT_ANALYSIS_PHASE = "analysis"
TYPED_CHECKPOINT_MODEL_PHASE = "model"
TYPED_CHECKPOINT_SNAPSHOT_PHASE = "snapshot"
TYPED_CHECKPOINT_DATABASE_SHARDS_PHASE = "database-analysis-shards"
TYPED_CHECKPOINT_DATABASE_ANALYSIS_PHASE = "database-analysis"
TYPED_CHECKPOINT_DATABASE_MODEL_PHASE = "database-model"
TYPED_CHECKPOINT_DATABASE_POINTERS_PHASE = "database-pointers"
MAX_PUBLICATION_ATTEMPTS = 5
ANALYSIS_CONTINUATION_FIELD = "_analysisContinuation"
ANALYSIS_CONTINUATION_SEQUENCE_FIELD = "_analysisContinuationSequence"
RUN_RETENTION = 10
RECOMMENDATION_GENERATION_MIN_RETENTION = 2
RECOMMENDATION_GENERATION_MAX_AGE = timedelta(hours=48)
BLOB_DELETE_BATCH_SIZE = 100


def parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class BlobObject:
    pathname: str
    uploaded_at: datetime | None = None


class JsonBlobStore(Protocol):
    def get_json(self, pathname: str) -> dict[str, Any] | None: ...
    def put_json(self, pathname: str, payload: Mapping[str, Any]) -> None: ...
    def get_bytes(self, pathname: str) -> bytes | None: ...
    def put_bytes(self, pathname: str, payload: bytes, *, content_type: str) -> None: ...
    def delete(self, pathnames: str | Sequence[str]) -> None: ...
    def list(self, prefix: str) -> list[BlobObject]: ...


@dataclass
class _CachedBlobClient:
    client: Any
    client_type: type[Any]
    users: int = 0
    retired: bool = False


_blob_client_cache: dict[str, _CachedBlobClient] = {}
_blob_client_cache_lock = threading.RLock()


def _close_blob_client(entry: _CachedBlobClient) -> None:
    close = getattr(entry.client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # Closing a retired keep-alive client must not mask a completed read.
            pass


def _blob_client_is_closed(client: Any) -> bool:
    return bool(getattr(client, "_closed", False))


def _closed_blob_client_error(client: Any, error: Exception) -> bool:
    if _blob_client_is_closed(client):
        return True
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "client is closed",
            "client has been closed",
            "connection pool was closed",
        )
    )


def _acquire_blob_client(token: str) -> _CachedBlobClient:
    from vercel.blob import BlobClient

    retired: _CachedBlobClient | None = None
    with _blob_client_cache_lock:
        entry = _blob_client_cache.get(token)
        if entry is not None and (
            entry.client_type is not BlobClient or _blob_client_is_closed(entry.client)
        ):
            _blob_client_cache.pop(token, None)
            entry.retired = True
            if entry.users == 0:
                retired = entry
            entry = None
        if entry is None:
            entry = _CachedBlobClient(BlobClient(token=token), BlobClient)
            _blob_client_cache[token] = entry
        entry.users += 1
    if retired is not None:
        _close_blob_client(retired)
    return entry


def _release_blob_client(
    token: str, entry: _CachedBlobClient, *, retire: bool = False
) -> None:
    should_close = False
    with _blob_client_cache_lock:
        if retire and _blob_client_cache.get(token) is entry:
            _blob_client_cache.pop(token, None)
            entry.retired = True
        entry.users -= 1
        should_close = entry.retired and entry.users == 0
    if should_close:
        _close_blob_client(entry)


def _read_with_blob_client(token: str, operation: Callable[[Any], Any]) -> Any:
    """Use a shared keep-alive client and retry once if that client was closed."""
    for attempt in range(2):
        entry = _acquire_blob_client(token)
        retire = False
        try:
            return operation(entry.client)
        except Exception as error:
            retire = _closed_blob_client_error(entry.client, error)
            if not retire or attempt:
                raise
        finally:
            _release_blob_client(token, entry, retire=retire)
    raise AssertionError("A closed Blob client retry did not return or raise.")


def _close_cached_blob_clients() -> None:
    close_now: list[_CachedBlobClient] = []
    with _blob_client_cache_lock:
        for entry in _blob_client_cache.values():
            entry.retired = True
            if entry.users == 0:
                close_now.append(entry)
        _blob_client_cache.clear()
    for entry in close_now:
        _close_blob_client(entry)


atexit.register(_close_cached_blob_clients)


class VercelPrivateBlobStore:
    """Minimal private-only Vercel Blob adapter."""

    def __init__(self, token: str | None = None) -> None:
        self.token = (token if token is not None else os.getenv("BLOB_READ_WRITE_TOKEN", "")).strip()
        if not self.token:
            raise RuntimeError("BLOB_READ_WRITE_TOKEN is not configured for the private analysis store.")

    def get_json(self, pathname: str) -> dict[str, Any] | None:
        from vercel.blob.errors import BlobNotFoundError

        try:
            result = _read_with_blob_client(
                self.token,
                lambda client: client.get(
                    pathname, access="private", use_cache=False
                ),
            )
        except BlobNotFoundError:
            return None
        value = json.loads(result.content.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Private Blob object {pathname!r} did not contain a JSON object.")
        return value

    def get_bytes(self, pathname: str) -> bytes | None:
        from vercel.blob.errors import BlobNotFoundError

        try:
            result = _read_with_blob_client(
                self.token,
                lambda client: client.get(
                    pathname, access="private", use_cache=False
                ),
            )
        except BlobNotFoundError:
            return None
        return bytes(result.content)

    def put_json(self, pathname: str, payload: Mapping[str, Any]) -> None:
        from vercel.blob import BlobClient

        body = json.dumps(
            dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        with BlobClient(token=self.token) as client:
            client.put(
                pathname,
                body,
                access="private",
                content_type="application/json",
                add_random_suffix=False,
                overwrite=True,
                cache_control_max_age=60,
            )

    def put_bytes(
        self, pathname: str, payload: bytes, *, content_type: str
    ) -> None:
        from vercel.blob import BlobClient

        with BlobClient(token=self.token) as client:
            client.put(
                pathname,
                payload,
                access="private",
                content_type=content_type,
                add_random_suffix=False,
                overwrite=True,
                cache_control_max_age=60,
            )

    def delete(self, pathnames: str | Sequence[str]) -> None:
        from vercel.blob import BlobClient

        normalized = [pathnames] if isinstance(pathnames, str) else list(pathnames)
        if not normalized:
            return
        with BlobClient(token=self.token) as client:
            for start in range(0, len(normalized), BLOB_DELETE_BATCH_SIZE):
                client.delete(normalized[start : start + BLOB_DELETE_BATCH_SIZE])

    def list(self, prefix: str) -> list[BlobObject]:
        from vercel.blob import BlobClient

        objects: list[BlobObject] = []
        with BlobClient(token=self.token) as client:
            for item in client.iter_objects(prefix=prefix):
                raw_path = getattr(item, "pathname", None)
                if raw_path is None and isinstance(item, Mapping):
                    raw_path = item.get("pathname")
                raw_uploaded = getattr(item, "uploaded_at", None)
                if raw_uploaded is None:
                    raw_uploaded = getattr(item, "uploadedAt", None)
                if raw_uploaded is None and isinstance(item, Mapping):
                    raw_uploaded = item.get("uploadedAt", item.get("uploaded_at"))
                uploaded = raw_uploaded if isinstance(raw_uploaded, datetime) else parse_utc(raw_uploaded)
                if raw_path:
                    objects.append(BlobObject(str(raw_path), uploaded))
        return sorted(objects, key=lambda item: item.pathname)


class PrivateBlobStore:
    """Construct the private Supabase artifact store used by the live runtime.

    The historical name remains as an internal compatibility seam for the
    analysis coordinator. Provider selection is intentionally no longer
    configurable: production artifacts are always read from and written to
    Supabase/PostgreSQL or private Supabase Storage.
    """

    def __new__(
        cls, token: str | None = None, *, canary_domain: str | None = None
    ) -> JsonBlobStore:
        if token is not None:
            raise RuntimeError(
                "PrivateBlobStore no longer accepts a Vercel Blob token; "
                "use the explicit legacy adapter only from archived tooling."
            )
        del canary_domain
        from pumbility_store import PumbilityArtifactStore

        return PumbilityArtifactStore()


class MemoryBlobStore:
    """Thread-safe test/local adapter with the same private JSON semantics."""

    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}
        self.binary_values: dict[str, bytes] = {}
        self.uploaded: dict[str, datetime] = {}
        self._lock = threading.RLock()

    def get_json(self, pathname: str) -> dict[str, Any] | None:
        with self._lock:
            value = self.values.get(pathname)
            return json.loads(json.dumps(value)) if value is not None else None

    def put_json(self, pathname: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            self.values[pathname] = json.loads(json.dumps(dict(payload)))
            self.uploaded[pathname] = utc_now()

    def put_json_bundle(self, payloads: Mapping[str, Mapping[str, Any]]) -> None:
        with self._lock:
            prepared = {
                pathname: json.loads(json.dumps(dict(payload)))
                for pathname, payload in payloads.items()
            }
            timestamp = utc_now()
            self.values.update(prepared)
            self.uploaded.update({pathname: timestamp for pathname in prepared})

    def get_bytes(self, pathname: str) -> bytes | None:
        with self._lock:
            value = self.binary_values.get(pathname)
            return bytes(value) if value is not None else None

    def put_bytes(
        self, pathname: str, payload: bytes, *, content_type: str
    ) -> None:
        del content_type
        with self._lock:
            self.binary_values[pathname] = bytes(payload)
            self.uploaded[pathname] = utc_now()

    def delete(self, pathnames: str | Sequence[str]) -> None:
        targets = [pathnames] if isinstance(pathnames, str) else list(pathnames)
        with self._lock:
            for pathname in targets:
                self.values.pop(pathname, None)
                self.binary_values.pop(pathname, None)
                self.uploaded.pop(pathname, None)

    def list(self, prefix: str) -> list[BlobObject]:
        with self._lock:
            return [
                BlobObject(pathname, self.uploaded.get(pathname))
                for pathname in sorted(set(self.values) | set(self.binary_values))
                if pathname.startswith(prefix)
            ]


class JobStore(Protocol):
    def get(self, job_id: str) -> dict[str, Any] | None: ...
    def save(self, job: Mapping[str, Any]) -> dict[str, Any]: ...
    def requeue(self, job_id: str, job: Mapping[str, Any]) -> dict[str, Any]: ...
    def active_job_id(self) -> str | None: ...
    def set_active_job_id(self, job_id: str | None) -> None: ...
    def latest_job_id(self, mix: str | MixSpec = DEFAULT_MIX_KEY) -> str | None: ...
    def set_latest_job_id(
        self, job_id: str, mix: str | MixSpec = DEFAULT_MIX_KEY
    ) -> None: ...


def _start_job_lease_heartbeat(store: JobStore, job_id: str) -> Any | None:
    """Start an optional backend lease heartbeat without affecting legacy stores."""
    factory = getattr(store, "start_lease_heartbeat", None)
    return factory(job_id) if callable(factory) else None


def _pulse_job_lease(heartbeat: Any | None) -> None:
    if heartbeat is not None:
        heartbeat.pulse()


def _stop_job_lease(heartbeat: Any | None) -> None:
    if heartbeat is not None:
        heartbeat.stop()


class VercelRuntimeJobStore:
    JOB_KEY = "job:{}"
    ACTIVE_KEY = "active-job"
    LATEST_KEY = "latest-job:{}"

    def __init__(self) -> None:
        from vercel.cache import RuntimeCache

        self.cache = RuntimeCache(namespace="pumbility-analysis", strict=False)

    @staticmethod
    def _options(name: str) -> dict[str, Any]:
        return {"ttl": JOB_TTL_SECONDS, "tags": ["analysis-jobs"], "name": name}

    def get(self, job_id: str) -> dict[str, Any] | None:
        value = self.cache.get(self.JOB_KEY.format(job_id))
        return dict(value) if isinstance(value, dict) else None

    def save(self, job: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(job)
        job_id = str(value["id"])
        self.cache.set(
            self.JOB_KEY.format(job_id),
            value,
            self._options(f"Analysis job {job_id}"),
        )
        return value

    def requeue(self, job_id: str, job: Mapping[str, Any]) -> dict[str, Any]:
        existing = self.get(job_id)
        if existing is None or existing.get("status") != "failed":
            raise RuntimeError("The failed analysis job could not be requeued.")
        return self.save({**dict(job), "id": job_id, "status": "queued"})

    def _get_id(self, key: str) -> str | None:
        value = self.cache.get(key)
        return value if isinstance(value, str) and value else None

    def active_job_id(self) -> str | None:
        return self._get_id(self.ACTIVE_KEY)

    def set_active_job_id(self, job_id: str | None) -> None:
        if job_id:
            self.cache.set(self.ACTIVE_KEY, job_id, self._options("Active analysis job"))
        else:
            self.cache.delete(self.ACTIVE_KEY)

    def latest_job_id(self, mix: str | MixSpec = DEFAULT_MIX_KEY) -> str | None:
        return self._get_id(self.LATEST_KEY.format(resolve_mix(mix).slug))

    def set_latest_job_id(
        self, job_id: str, mix: str | MixSpec = DEFAULT_MIX_KEY
    ) -> None:
        spec = resolve_mix(mix)
        self.cache.set(
            self.LATEST_KEY.format(spec.slug),
            job_id,
            self._options(f"Latest {spec.label} analysis job"),
        )


class RuntimeJobStore:
    """Construct the Supabase-backed durable job store for the live runtime."""

    def __new__(cls, *, canary_domain: str | None = None) -> JobStore:
        del canary_domain
        from pumbility_store import PumbilityJobStore

        return PumbilityJobStore()


class MemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.active: str | None = None
        self.latest: dict[str, str] = {}
        self._lock = threading.RLock()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self.jobs.get(job_id)
            return json.loads(json.dumps(value)) if value is not None else None

    def save(self, job: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            value = json.loads(json.dumps(dict(job)))
            self.jobs[str(value["id"])] = value
            return json.loads(json.dumps(value))

    def requeue(self, job_id: str, job: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            existing = self.jobs.get(job_id)
            if existing is None or existing.get("status") != "failed":
                raise RuntimeError("The failed analysis job could not be requeued.")
            value = json.loads(json.dumps({**dict(job), "id": job_id, "status": "queued"}))
            self.jobs[job_id] = value
            return json.loads(json.dumps(value))

    def active_job_id(self) -> str | None:
        with self._lock:
            return self.active

    def set_active_job_id(self, job_id: str | None) -> None:
        with self._lock:
            self.active = job_id

    def latest_job_id(self, mix: str | MixSpec = DEFAULT_MIX_KEY) -> str | None:
        with self._lock:
            return self.latest.get(resolve_mix(mix).key)

    def set_latest_job_id(
        self, job_id: str, mix: str | MixSpec = DEFAULT_MIX_KEY
    ) -> None:
        with self._lock:
            self.latest[resolve_mix(mix).key] = job_id


def new_job(
    job_id: str,
    now: datetime,
    *,
    attempt: int = 0,
    full_sync: bool = False,
    reanalyze_only: bool = False,
    trigger: str = "manual",
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> dict[str, Any]:
    mix_spec = resolve_mix(mix)
    timestamp = isoformat_utc(now)
    return {
        "id": job_id,
        "status": "queued",
        "stage": "discovering",
        "progress": {
            "current": 0,
            "total": 0,
            "percent": 0,
            "message": "Waiting for the analysis worker.",
        },
        "createdAtUtc": timestamp,
        "updatedAtUtc": timestamp,
        "startedAtUtc": None,
        "completedAtUtc": None,
        "generatedAtUtc": None,
        "retryAllowedAtUtc": None,
        "error": None,
        "attempt": attempt,
        "fullSync": full_sync,
        "reanalyzeOnly": reanalyze_only,
        "trigger": trigger,
        "mix": mix_spec.key,
    }


def update_job(
    store: JobStore,
    job_id: str,
    *,
    now: datetime | None = None,
    **updates: Any,
) -> dict[str, Any]:
    job = store.get(job_id)
    if job is None:
        raise RuntimeError("The analysis job status expired before the worker completed.")
    effective_now = now or utc_now()
    job.update(updates)
    job["updatedAtUtc"] = isoformat_utc(effective_now)
    if job.get("status") == "running" and not job.get("startedAtUtc"):
        job["startedAtUtc"] = isoformat_utc(effective_now)
    if job.get("status") in {"completed", "failed"} and not job.get("completedAtUtc"):
        job["completedAtUtc"] = isoformat_utc(effective_now)
    return store.save(job)


def deterministic_hourly_job_id(
    now: datetime,
    attempt: int = 0,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> str:
    spec = resolve_mix(mix)
    prefix = "analysis" if spec.key == DEFAULT_MIX_KEY else f"analysis-{spec.slug}"
    base = now.astimezone(timezone.utc).strftime(f"{prefix}-%Y%m%dT%H")
    return base if attempt <= 0 else f"{base}-r{attempt}"


def deterministic_deployment_job_id(
    deployment_id: str,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]", "-", deployment_id.strip())[:80]
    if not normalized:
        raise ValueError("A deployment refresh requires a deployment ID.")
    spec = resolve_mix(mix)
    suffix = "" if spec.key == DEFAULT_MIX_KEY else f"-{spec.slug}"
    return f"analysis-deploy-{normalized}{suffix}"


def _fresh_result(payload: Mapping[str, Any] | None, now: datetime) -> tuple[str, str] | None:
    if FRESHNESS <= timedelta(0):
        return None
    summary = payload.get("summary") if payload else None
    if not isinstance(summary, Mapping) or summary.get("scriptVersion") != SCRIPT_VERSION:
        return None
    generated = parse_utc(payload.get("generatedAtUtc")) if payload else None
    if generated is None:
        return None
    next_allowed = generated + FRESHNESS
    if now < next_allowed:
        return isoformat_utc(generated), isoformat_utc(next_allowed)
    return None


def read_latest_payload(
    blobs: JsonBlobStore,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> dict[str, Any] | None:
    """Read a mix-scoped aggregate, with a read-only legacy fallback for Phoenix 2."""
    spec = resolve_mix(mix)
    payload = blobs.get_json(latest_blob_path(spec))
    if payload is None and spec.key == DEFAULT_MIX_KEY:
        payload = blobs.get_json(LEGACY_LATEST_BLOB_PATH)
    if payload is None:
        return None
    payload_mix = payload.get("mix")
    if isinstance(payload_mix, Mapping):
        actual = resolve_mix(payload_mix.get("key") or payload_mix.get("apiValue"))
        if actual.key != spec.key:
            raise ValueError(
                f"Stored aggregate mix {actual.label} does not match requested mix {spec.label}."
            )
    elif payload_mix is not None:
        raise ValueError("Stored aggregate mix metadata is invalid.")
    normalized = dict(payload)
    normalized["mix"] = spec.as_payload()
    summary = normalized.get("summary")
    if isinstance(summary, Mapping):
        normalized["summary"] = {**summary, "mix": spec.as_payload()}
    return normalized


Enqueue = Callable[[str], None]


def request_refresh(
    blobs: JsonBlobStore,
    jobs: JobStore,
    enqueue: Enqueue,
    *,
    now: datetime | None = None,
    force_refresh: bool = False,
    deterministic_job_id: str | None = None,
    full_sync: bool = False,
    reanalyze_only: bool = False,
    trigger: str = "manual",
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> tuple[int, dict[str, Any]]:
    """Apply freshness, global-active, and failed-retry rules before enqueueing."""
    if full_sync and reanalyze_only:
        raise ValueError("A refresh cannot be both a full sync and reanalysis-only.")
    mix_spec = resolve_mix(mix)
    if mix_spec.archived:
        return 409, {
            "outcome": "archived",
            "error": f"{mix_spec.label} is an archived snapshot and cannot be refreshed.",
            "archiveUrl": mix_spec.archive_url,
        }
    effective_now = now or utc_now()
    active_id = jobs.active_job_id()
    active = jobs.get(active_id) if active_id else None
    if active and active.get("status") in {"queued", "running"}:
        last_heartbeat = parse_utc(active.get("updatedAtUtc"))
        if (
            last_heartbeat is None
            or effective_now - last_heartbeat > ACTIVE_JOB_STALE_AFTER
        ):
            update_job(
                jobs,
                str(active["id"]),
                now=effective_now,
                status="failed",
                error="The analysis worker stopped reporting progress.",
                retryAllowedAtUtc=isoformat_utc(effective_now),
                progress={
                    "current": 0,
                    "total": 0,
                    "percent": 0,
                    "message": "The stale analysis worker was released for a safe retry.",
                },
            )
            jobs.set_active_job_id(None)
            active_id = None
            active = None
    if active and active.get("status") in {"queued", "running"}:
        active_mix = resolve_mix(active.get("mix")).key
        if active_mix == mix_spec.key:
            return 202, {"outcome": "existing", "job": active}
        return 409, {
            "outcome": "busy",
            "activeMix": active_mix,
            "error": (
                f"{resolve_mix(active_mix).label} is currently refreshing. "
                f"Retry {mix_spec.label} after it finishes."
            ),
        }
    if active_id:
        jobs.set_active_job_id(None)

    deterministic_existing = jobs.get(deterministic_job_id) if deterministic_job_id else None
    if deterministic_existing and deterministic_existing.get("status") in {
        "queued", "running", "completed"
    }:
        return 202, {"outcome": "existing", "job": deterministic_existing}

    latest_payload = read_latest_payload(blobs, mix_spec)
    if not force_refresh and (fresh := _fresh_result(latest_payload, effective_now)):
        generated_at, next_allowed_at = fresh
        return 200, {
            "outcome": "fresh",
            "generatedAtUtc": generated_at,
            "nextAllowedAtUtc": next_allowed_at,
        }

    latest_job_id = jobs.latest_job_id(mix_spec)
    previous = deterministic_existing or (jobs.get(latest_job_id) if latest_job_id else None)
    if previous and previous.get("status") == "failed":
        retry_at = parse_utc(previous.get("retryAllowedAtUtc"))
        if retry_at is not None and effective_now < retry_at:
            return 202, {"outcome": "existing", "job": previous}
        previous_job_id = str(previous.get("id") or "").strip()
        checkpoint = (
            blobs.get_json(typed_checkpoint_path(previous_job_id, mix_spec))
            if previous_job_id
            else None
        )
        checkpoint_phase = (
            str(checkpoint.get("phase") or "")
            if isinstance(checkpoint, Mapping)
            else ""
        )
        publication_attempts = (
            int(checkpoint.get("publicationAttempts") or 0)
            if isinstance(checkpoint, Mapping)
            else MAX_PUBLICATION_ATTEMPTS
        )
        requeue = getattr(jobs, "requeue", None)
        if (
            checkpoint_phase
            in {
                TYPED_CHECKPOINT_DATABASE_MODEL_PHASE,
                TYPED_CHECKPOINT_DATABASE_POINTERS_PHASE,
            }
            and publication_attempts < MAX_PUBLICATION_ATTEMPTS
            and callable(requeue)
        ):
            resumed = {
                **previous,
                "status": "queued",
                "stage": "publishing",
                "updatedAtUtc": isoformat_utc(effective_now),
                "completedAtUtc": None,
                "retryAllowedAtUtc": None,
                "error": None,
                "progress": {
                    "current": 1,
                    "total": 1,
                    "percent": 100,
                    "message": "Waiting to resume the validated pointer publication.",
                },
            }
            resumed = requeue(previous_job_id, resumed)
            jobs.set_latest_job_id(previous_job_id, mix_spec)
            jobs.set_active_job_id(previous_job_id)
            try:
                enqueue(previous_job_id)
            except Exception as exc:
                failed = update_job(
                    jobs,
                    previous_job_id,
                    now=effective_now,
                    status="failed",
                    error=(
                        "The analysis publication could not be queued. "
                        "Please try again in five minutes."
                    ),
                    retryAllowedAtUtc=isoformat_utc(
                        effective_now + FAILED_RETRY_DELAY
                    ),
                    progress={
                        "current": 0,
                        "total": 1,
                        "percent": 0,
                        "message": "Publication queue submission failed.",
                    },
                )
                jobs.set_active_job_id(None)
                raise RuntimeError(failed["error"]) from exc
            return 202, {"outcome": "resumed", "job": resumed}

    attempt = int(previous.get("attempt") or 0) + 1 if deterministic_existing else 0
    if previous and not deterministic_job_id:
        previous_base = deterministic_hourly_job_id(effective_now, mix=mix_spec)
        if str(previous.get("id", "")).startswith(previous_base):
            attempt = int(previous.get("attempt") or 0) + 1
    job_id = deterministic_job_id or deterministic_hourly_job_id(
        effective_now, attempt, mix=mix_spec
    )
    while not deterministic_job_id and jobs.get(job_id) is not None:
        attempt += 1
        job_id = deterministic_hourly_job_id(effective_now, attempt, mix=mix_spec)
    job = new_job(
        job_id,
        effective_now,
        attempt=attempt,
        full_sync=full_sync,
        reanalyze_only=reanalyze_only,
        trigger=trigger,
        mix=mix_spec,
    )
    jobs.save(job)
    jobs.set_latest_job_id(job_id, mix_spec)
    jobs.set_active_job_id(job_id)
    try:
        enqueue(job_id)
    except Exception as exc:
        failed = update_job(
            jobs,
            job_id,
            now=effective_now,
            status="failed",
            error="The analysis job could not be queued. Please try again in five minutes.",
            retryAllowedAtUtc=isoformat_utc(effective_now + FAILED_RETRY_DELAY),
            progress={
                "current": 0,
                "total": 0,
                "percent": 0,
                "message": "Queue submission failed.",
            },
        )
        jobs.set_active_job_id(None)
        raise RuntimeError(failed["error"]) from exc
    return 202, {"outcome": "started", "job": jobs.get(job_id) or job}


def cleanup_abandoned_staging(
    blobs: JsonBlobStore,
    *,
    now: datetime | None = None,
    keep_path: str | None = None,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> int:
    effective_now = now or utc_now()
    stale = [
        item.pathname
        for item in blobs.list(staging_prefix(mix))
        if item.pathname != keep_path
        and item.uploaded_at is not None
        and effective_now - item.uploaded_at > STAGING_MAX_AGE
    ]
    if stale:
        blobs.delete(stale)
    return len(stale)


def cleanup_abandoned_typed_checkpoints(
    blobs: JsonBlobStore,
    *,
    now: datetime | None = None,
    keep_job_id: str | None = None,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> int:
    """Bound deletion of private typed manifests/shards left by old failed jobs."""
    effective_now = now or utc_now()
    spec = resolve_mix(mix)
    keep_shard_prefix = (
        typed_checkpoint_shard_prefix(keep_job_id, spec) if keep_job_id else None
    )
    candidates = [
        *blobs.list(f"analysis/private/runtime-checkpoints/{spec.slug}/"),
        *blobs.list(typed_checkpoint_shard_root_prefix(spec)),
    ]
    stale = [
        item.pathname
        for item in candidates
        if (keep_shard_prefix is None or not item.pathname.startswith(keep_shard_prefix))
        and (
            keep_job_id is None
            or item.pathname != typed_checkpoint_path(keep_job_id, spec)
        )
        and item.uploaded_at is not None
        and effective_now - item.uploaded_at > STAGING_MAX_AGE
    ]
    for offset in range(0, len(stale), BLOB_DELETE_BATCH_SIZE):
        blobs.delete(stale[offset : offset + BLOB_DELETE_BATCH_SIZE])
    return len(stale)


def _run_path(
    payload: Mapping[str, Any],
    job_id: str,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> str:
    generated = parse_utc(payload.get("generatedAtUtc")) or utc_now()
    stamp = generated.strftime("%Y%m%dT%H%M%S%fZ")
    return f"{runs_prefix(mix)}{stamp}-{job_id}.json"


_PUBLICATION_LOGGER = logging.getLogger("pumbility.publication")
_PUBLICATION_PHASES = frozenset(
    {"publish-preflight", "pointer-commit", "post-publish-cleanup"}
)


def _publication_error_fields(error: BaseException | None) -> dict[str, str]:
    if error is None:
        return {"exceptionClass": "none", "category": "none", "sqlstate": "none"}
    normalized = str(error).casefold()
    category = next(
        (
            label
            for label, markers in (
                ("timeout", ("timeout", "timed out")),
                ("connection", ("connection", "server closed", "network")),
                ("integrity", ("checksum", "validation", "conflict")),
                ("lease", ("lease",)),
            )
            if any(marker in normalized for marker in markers)
        ),
        "other",
    )
    sqlstate = str(getattr(error, "sqlstate", None) or "none")
    if not re.fullmatch(r"[A-Za-z0-9]{5}|none", sqlstate):
        sqlstate = "other"
    return {
        "exceptionClass": re.sub(r"[^A-Za-z0-9_]", "", type(error).__name__)
        or "Exception",
        "category": category,
        "sqlstate": sqlstate,
    }


def _publication_event(
    phase: str,
    outcome: str,
    started: float,
    *,
    byte_count: int = 0,
    item_count: int = 0,
    warning_count: int = 0,
    error: BaseException | None = None,
) -> None:
    if phase not in _PUBLICATION_PHASES:
        raise ValueError("Unsupported publication telemetry phase.")
    event = {
        "event": "analysis_publication",
        "phase": phase,
        "outcome": outcome,
        "durationMs": round((time.perf_counter() - started) * 1000, 3),
        "byteCount": int(byte_count),
        "itemCount": int(item_count),
        "warningCount": int(warning_count),
        **_publication_error_fields(error),
    }
    _PUBLICATION_LOGGER.warning(
        json.dumps(event, separators=(",", ":"), sort_keys=True)
    )


def _publication_byte_count(payloads: Mapping[str, Mapping[str, Any]]) -> int:
    return sum(
        len(
            json.dumps(
                dict(payload),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        for payload in payloads.values()
    )


def _validate_typed_publication(
    *,
    job_id: str,
    payload: Mapping[str, Any],
    recommendations: Mapping[str, Any] | None,
    combined_tier: Mapping[str, Any] | None,
    analysis_run_id: str | None,
    model_generation_id: str | None,
) -> str:
    if recommendations is None or combined_tier is None:
        raise ValueError("Typed publication requires all public compatibility payloads.")
    generation = str(recommendations.get("generationKey") or "").strip()
    generated_at = str(payload.get("generatedAtUtc") or "").strip()
    recommendation_generated_at = str(
        recommendations.get("modelGeneratedAtUtc")
        or recommendations.get("generatedAtUtc")
        or ""
    ).strip()
    tier_generated_at = str(combined_tier.get("generatedAtUtc") or "").strip()
    if (
        not analysis_run_id
        or not model_generation_id
        or generation != recommendation_generation_key(job_id)
        or int(recommendations.get("schemaVersion") or 0)
        != RECOMMENDATION_SCHEMA_VERSION
        or int(recommendations.get("storageSchemaVersion") or 0)
        != PLAYER_REFRESH_STORAGE_SCHEMA_VERSION
        or recommendations.get("refreshSupported") is not True
        or parse_utc(generated_at) is None
        or recommendation_generated_at != generated_at
        or tier_generated_at != generated_at
    ):
        raise ValueError("The typed publication preflight contract is invalid.")
    return generation


def publish_success(
    blobs: JsonBlobStore,
    *,
    job_id: str,
    snapshot: Mapping[str, Any],
    payload: Mapping[str, Any],
    recommendations: Mapping[str, Any] | None = None,
    combined_tier: Mapping[str, Any] | None = None,
    publish_snapshot: bool = True,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
    analysis_run_id: str | None = None,
    model_generation_id: str | None = None,
    defer_cleanup: bool = False,
) -> int:
    """Promote derived pointers atomically; optionally defer noncritical cleanup."""
    mix_spec = resolve_mix(mix)
    if mix_spec.archived:
        raise ValueError(f"{mix_spec.label} is archived and cannot be published.")
    preflight_started = time.perf_counter()
    typed_publication = analysis_run_id is not None or model_generation_id is not None
    generation_key = ""
    try:
        if typed_publication:
            generation_key = _validate_typed_publication(
                job_id=job_id,
                payload=payload,
                recommendations=recommendations,
                combined_tier=combined_tier,
                analysis_run_id=analysis_run_id,
                model_generation_id=model_generation_id,
            )
        elif recommendations is not None:
            generation_key = str(recommendations.get("generationKey") or "").strip()
    except Exception as error:
        _publication_event("publish-preflight", "failed", preflight_started, error=error)
        raise
    _publication_event("publish-preflight", "completed", preflight_started)

    if publish_snapshot:
        blobs.put_json(
            current_snapshot_path(mix_spec),
            sanitize_snapshot(snapshot, mix=mix_spec),
        )
    publication_pointers: dict[str, Mapping[str, Any]] = {
        _run_path(payload, job_id, mix_spec): payload
    }
    if recommendations is not None:
        publication_pointers[recommendation_blob_path()] = recommendations
        if not typed_publication:
            previous_recommendations = blobs.get_json(recommendation_blob_path())
            if previous_recommendations is not None:
                previous_generation = str(
                    previous_recommendations.get("generationKey") or ""
                ).strip()
                if previous_generation:
                    publication_pointers[
                        recommendation_index_path(previous_generation)
                    ] = previous_recommendations
    if combined_tier is not None:
        publication_pointers[combined_tier_blob_path()] = combined_tier
    publication_pointers[latest_blob_path(mix_spec)] = payload
    byte_count = _publication_byte_count(publication_pointers)
    commit_started = time.perf_counter()
    try:
        generation_publisher = getattr(blobs, "publish_generation", None)
        if typed_publication and callable(generation_publisher):
            generation_publisher(
                publication_pointers,
                generation_key=generation_key,
                analysis_run_id=str(analysis_run_id),
                model_generation_id=str(model_generation_id),
            )
        else:
            bundle_writer = getattr(blobs, "put_json_bundle", None)
            if callable(bundle_writer):
                bundle_writer(publication_pointers)
            else:
                for pathname, pointer_payload in publication_pointers.items():
                    blobs.put_json(pathname, pointer_payload)
    except Exception as error:
        committed_reader = getattr(blobs, "publication_committed", None)
        if typed_publication and callable(committed_reader):
            try:
                if committed_reader(
                    generation_key=generation_key,
                    analysis_run_id=str(analysis_run_id),
                    model_generation_id=str(model_generation_id),
                ):
                    _publication_event(
                        "pointer-commit",
                        "resolved-ambiguous-commit",
                        commit_started,
                        byte_count=byte_count,
                        item_count=len(publication_pointers),
                        error=error,
                    )
                else:
                    raise error
            except Exception as verification_error:
                _publication_event(
                    "pointer-commit",
                    "failed",
                    commit_started,
                    byte_count=byte_count,
                    item_count=len(publication_pointers),
                    error=verification_error,
                )
                raise error
        else:
            _publication_event(
                "pointer-commit",
                "failed",
                commit_started,
                byte_count=byte_count,
                item_count=len(publication_pointers),
                error=error,
            )
            raise
    else:
        _publication_event(
            "pointer-commit",
            "completed",
            commit_started,
            byte_count=byte_count,
            item_count=len(publication_pointers),
        )

    if defer_cleanup:
        return 0
    return _post_publish_cleanup(
        blobs,
        recommendations=recommendations,
        mix=mix_spec,
    )


def _delete_cleanup_batches(
    blobs: JsonBlobStore,
    paths: Sequence[str],
    *,
    retain_typed_references: bool = False,
) -> None:
    deleter = (
        getattr(blobs, "delete_unreferenced", None)
        if retain_typed_references
        else None
    )
    if not callable(deleter):
        deleter = blobs.delete
    unique_paths = sorted(set(paths))
    for offset in range(0, len(unique_paths), BLOB_DELETE_BATCH_SIZE):
        deleter(unique_paths[offset : offset + BLOB_DELETE_BATCH_SIZE])


def _post_publish_cleanup(
    blobs: JsonBlobStore,
    *,
    recommendations: Mapping[str, Any] | None,
    mix: str | MixSpec,
    checkpoint_paths: Sequence[str] = (),
) -> int:
    """Run bounded, idempotent maintenance without changing publication success."""
    started = time.perf_counter()
    warnings: list[BaseException] = []
    if recommendations is not None:
        generation_key = str(recommendations.get("generationKey") or "").strip()
        storage_schema = int(recommendations.get("storageSchemaVersion") or 0)
        operations: list[Callable[[], None]] = []
        if storage_schema >= 3 and generation_key:
            operations.extend(
                (
                    lambda: _cleanup_recommendation_generations(blobs, generation_key),
                    lambda: _cleanup_revoked_player_artifacts(blobs, recommendations),
                )
            )
        if (
            generation_key
            and os.getenv("PLAYER_RECOMMENDATION_PRUNE_LEGACY", "").strip().lower()
            in {"1", "true", "yes", "on"}
        ):
            operations.append(
                lambda: _delete_cleanup_batches(
                    blobs,
                    [
                        item.pathname
                        for item in blobs.list(recommendation_shard_prefix())
                        if storage_schema >= 3
                        or not item.pathname.startswith(
                            recommendation_shard_prefix(generation_key)
                        )
                    ],
                )
            )
        for operation in operations:
            try:
                operation()
            except Exception as error:
                warnings.append(error)
                _publication_event(
                    "post-publish-cleanup", "warning", started, error=error
                )
    try:
        runs = sorted(
            blobs.list(runs_prefix(mix)), key=lambda item: item.pathname, reverse=True
        )
        _delete_cleanup_batches(blobs, [item.pathname for item in runs[RUN_RETENTION:]])
    except Exception as error:
        warnings.append(error)
        _publication_event("post-publish-cleanup", "warning", started, error=error)
    if checkpoint_paths:
        try:
            _delete_cleanup_batches(blobs, checkpoint_paths)
        except Exception as error:
            warnings.append(error)
            _publication_event("post-publish-cleanup", "warning", started, error=error)
    _publication_event(
        "post-publish-cleanup",
        "completed-with-warnings" if warnings else "completed",
        started,
        warning_count=len(warnings),
    )
    return len(warnings)


def _cleanup_recommendation_generations(
    blobs: JsonBlobStore, current_generation: str, *, now: datetime | None = None
) -> None:
    effective_now = now or utc_now()
    indexes = sorted(
        blobs.list("analysis/recommendations/indexes/"),
        key=lambda item: item.uploaded_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    keep = {current_generation}
    v3_indexes: list[BlobObject] = []
    for item in indexes:
        generation = Path(item.pathname).stem
        try:
            stored_index = blobs.get_json(item.pathname)
        except Exception:
            # Retain unreadable historical indexes for operator repair. They must
            # never block promotion of a fully validated current generation.
            keep.add(generation)
            continue
        if int((stored_index or {}).get("storageSchemaVersion") or 0) >= 3:
            v3_indexes.append(item)
        else:
            # Legacy schema-2 generations remain rollback-safe until explicit pruning.
            keep.add(generation)
    keep.update(
        Path(item.pathname).stem
        for item in v3_indexes[:RECOMMENDATION_GENERATION_MIN_RETENTION]
    )
    keep.update(
        Path(item.pathname).stem
        for item in v3_indexes
        if item.uploaded_at is not None
        and effective_now - item.uploaded_at <= RECOMMENDATION_GENERATION_MAX_AGE
    )
    stale: list[str] = []
    for item in blobs.list("analysis/recommendations/models/"):
        if Path(item.pathname).stem not in keep:
            stale.append(item.pathname)
    for item in blobs.list("analysis/private/recommendation-inputs/"):
        parts = item.pathname.split("/")
        generation = parts[3] if len(parts) > 3 else ""
        if generation not in keep:
            stale.append(item.pathname)
    for item in indexes:
        if Path(item.pathname).stem not in keep:
            stale.append(item.pathname)
    _delete_cleanup_batches(blobs, stale, retain_typed_references=True)


def _cleanup_revoked_player_artifacts(
    blobs: JsonBlobStore, recommendations: Mapping[str, Any]
) -> None:
    allowed = {
        str(row.get("playerKey"))
        for row in recommendations.get("players", [])
        if isinstance(row, Mapping) and row.get("playerKey")
    }
    stale: list[str] = []
    for prefix in (
        "analysis/private/recommendation-player-state/",
        "analysis/recommendations/players/",
    ):
        for item in blobs.list(prefix):
            if Path(item.pathname).stem not in allowed:
                stale.append(item.pathname)
    _delete_cleanup_batches(blobs, stale)


_SECRET_PATTERN = re.compile(r"(?:piu_scores_live_|pst_live_)[0-9a-f]{16,}", re.IGNORECASE)


def safe_error(exc: BaseException) -> str:
    from piu_misgrade_analyzer import ApiError

    if isinstance(exc, (ApiError, FileNotFoundError, ValueError)):
        message = str(exc).strip() or "The analysis could not be completed."
        return _SECRET_PATTERN.sub("[credential redacted]", message)[:500]
    return "The analysis failed unexpectedly. Please retry after the cooldown."


def _checkpoint_records(value: Any, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(row, Mapping) for row in value):
        raise ValueError(f"The typed analysis checkpoint has an invalid {field} field.")
    return [dict(row) for row in value]


def _canonical_json_sha256(value: Any) -> str:
    body = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _write_typed_frame_shards(
    *,
    blob_store: JsonBlobStore,
    job_store: JobStore,
    job_id: str,
    mix_spec: MixSpec,
    frames: Mapping[str, Any],
    lease_heartbeat: Any | None,
) -> dict[str, Any]:
    """Write bounded, deterministic typed rows and return a compact manifest."""
    from scripts.analyze_pumbility_supabase import _frame_records

    ordered_datasets = ("baselines", "contributions", "chartResults")
    total_shards = sum(
        (len(frames[name]) + TYPED_CHECKPOINT_ROW_LIMIT - 1)
        // TYPED_CHECKPOINT_ROW_LIMIT
        for name in ordered_datasets
    )
    completed_shards = 0
    datasets: dict[str, Any] = {}
    for dataset in ordered_datasets:
        frame = frames[dataset]
        descriptors: list[dict[str, Any]] = []
        for shard, offset in enumerate(
            range(0, len(frame), TYPED_CHECKPOINT_ROW_LIMIT)
        ):
            rows = _frame_records(
                frame.iloc[offset : offset + TYPED_CHECKPOINT_ROW_LIMIT]
            )
            digest = _canonical_json_sha256(rows)
            pathname = typed_checkpoint_shard_path(
                job_id, dataset, shard, mix_spec
            )
            blob_store.put_json(
                pathname,
                {
                    "schemaVersion": TYPED_CHECKPOINT_SHARD_SCHEMA_VERSION,
                    "jobId": job_id,
                    "mix": mix_spec.key,
                    "dataset": dataset,
                    "shard": shard,
                    "rowCount": len(rows),
                    "sha256": digest,
                    "rows": rows,
                },
            )
            descriptors.append(
                {
                    "pathname": pathname,
                    "shard": shard,
                    "rowCount": len(rows),
                    "sha256": digest,
                }
            )
            completed_shards += 1
            percent = (
                int((completed_shards / total_shards) * 100)
                if total_shards
                else 100
            )
            update_job(
                job_store,
                job_id,
                status="running",
                stage="publishing",
                progress={
                    "current": completed_shards,
                    "total": total_shards,
                    "percent": percent,
                    "message": (
                        f"Checkpointing typed analysis rows "
                        f"({completed_shards:,}/{total_shards:,} shards)."
                    ),
                },
            )
            _pulse_job_lease(lease_heartbeat)
            del rows
        dataset_manifest = {
            "rowCount": int(len(frame)),
            "shardCount": len(descriptors),
            "shards": descriptors,
        }
        dataset_manifest["sha256"] = _canonical_json_sha256(descriptors)
        datasets[dataset] = dataset_manifest
    manifest = {
        "schemaVersion": TYPED_CHECKPOINT_SHARD_SCHEMA_VERSION,
        "rowLimit": TYPED_CHECKPOINT_ROW_LIMIT,
        "datasets": datasets,
    }
    manifest["sha256"] = _canonical_json_sha256(datasets)
    return manifest


def _typed_manifest(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    raw = checkpoint.get("typedShards")
    if not isinstance(raw, Mapping):
        raise ValueError("The typed analysis checkpoint has no shard manifest.")
    manifest = dict(raw)
    if (
        int(manifest.get("schemaVersion") or 0)
        != TYPED_CHECKPOINT_SHARD_SCHEMA_VERSION
        or int(manifest.get("rowLimit") or 0) != TYPED_CHECKPOINT_ROW_LIMIT
        or not isinstance(manifest.get("datasets"), Mapping)
    ):
        raise ValueError("The typed analysis checkpoint shard manifest is invalid.")
    datasets = dict(manifest["datasets"])
    if manifest.get("sha256") != _canonical_json_sha256(datasets):
        raise ValueError("The typed analysis checkpoint manifest failed validation.")
    expected_names = {"baselines", "contributions", "chartResults"}
    if set(datasets) != expected_names:
        raise ValueError("The typed analysis checkpoint datasets are invalid.")
    for dataset, raw_dataset in datasets.items():
        if not isinstance(raw_dataset, Mapping):
            raise ValueError("The typed analysis checkpoint dataset is invalid.")
        dataset_manifest = dict(raw_dataset)
        shards = dataset_manifest.get("shards")
        if not isinstance(shards, list) or not all(
            isinstance(item, Mapping) for item in shards
        ):
            raise ValueError("The typed analysis checkpoint shard list is invalid.")
        descriptors = [dict(item) for item in shards]
        if (
            int(dataset_manifest.get("rowCount") or 0)
            != sum(int(item.get("rowCount") or 0) for item in descriptors)
            or int(dataset_manifest.get("shardCount") or 0) != len(descriptors)
            or dataset_manifest.get("sha256")
            != _canonical_json_sha256(descriptors)
        ):
            raise ValueError("The typed analysis checkpoint shard counts are invalid.")
        for expected_shard, descriptor in enumerate(descriptors):
            expected_path = typed_checkpoint_shard_path(
                str(checkpoint.get("jobId") or ""),
                dataset,
                expected_shard,
                str(checkpoint.get("mix") or DEFAULT_MIX_KEY),
            )
            if (
                int(descriptor.get("shard") or 0) != expected_shard
                or descriptor.get("pathname") != expected_path
                or int(descriptor.get("rowCount") or 0) < 0
                or int(descriptor.get("rowCount") or 0)
                > TYPED_CHECKPOINT_ROW_LIMIT
                or not re.fullmatch(r"[0-9a-f]{64}", str(descriptor.get("sha256") or ""))
            ):
                raise ValueError("The typed analysis checkpoint shard descriptor is invalid.")
        datasets[dataset] = {**dataset_manifest, "shards": descriptors}
    return {**manifest, "datasets": datasets}


def _load_typed_checkpoint_shard(
    blob_store: JsonBlobStore,
    *,
    checkpoint: Mapping[str, Any],
    dataset: str,
    descriptor: Mapping[str, Any],
) -> list[dict[str, Any]]:
    payload = blob_store.get_json(str(descriptor["pathname"]))
    if payload is None:
        raise RuntimeError("A typed analysis checkpoint shard is unavailable.")
    rows = _checkpoint_records(payload.get("rows"), field=f"{dataset} shard")
    digest = _canonical_json_sha256(rows)
    if (
        int(payload.get("schemaVersion") or 0)
        != TYPED_CHECKPOINT_SHARD_SCHEMA_VERSION
        or payload.get("jobId") != checkpoint.get("jobId")
        or payload.get("mix") != checkpoint.get("mix")
        or payload.get("dataset") != dataset
        or int(payload.get("shard") or 0) != int(descriptor.get("shard") or 0)
        or int(payload.get("rowCount") or 0) != len(rows)
        or int(descriptor.get("rowCount") or 0) != len(rows)
        or payload.get("sha256") != digest
        or descriptor.get("sha256") != digest
    ):
        raise ValueError("A typed analysis checkpoint shard failed count/hash validation.")
    return rows


def _typed_checkpoint_shard_paths(manifest: Mapping[str, Any]) -> list[str]:
    return [
        str(descriptor["pathname"])
        for dataset in manifest["datasets"].values()
        for descriptor in dataset["shards"]
    ]


def _write_typed_checkpoint_snapshot(
    blob_store: JsonBlobStore,
    *,
    job_id: str,
    mix_spec: MixSpec,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    value = sanitize_snapshot(snapshot, mix=mix_spec)
    digest = _canonical_json_sha256(value)
    pathname = typed_checkpoint_snapshot_path(job_id, mix_spec)
    blob_store.put_json(
        pathname,
        {
            "schemaVersion": TYPED_CHECKPOINT_SHARD_SCHEMA_VERSION,
            "jobId": job_id,
            "mix": mix_spec.key,
            "sha256": digest,
            "snapshot": value,
        },
    )
    return {"pathname": pathname, "sha256": digest}


def _load_typed_checkpoint_snapshot(
    blob_store: JsonBlobStore,
    *,
    checkpoint: Mapping[str, Any],
    reference: Mapping[str, Any],
    mix_spec: MixSpec,
) -> dict[str, Any]:
    expected_path = typed_checkpoint_snapshot_path(
        str(checkpoint.get("jobId") or ""), mix_spec
    )
    if reference.get("pathname") != expected_path:
        raise ValueError("The typed analysis checkpoint snapshot reference is invalid.")
    payload = blob_store.get_json(expected_path)
    raw_snapshot = payload.get("snapshot") if isinstance(payload, Mapping) else None
    if not isinstance(raw_snapshot, Mapping):
        raise RuntimeError("The typed analysis checkpoint snapshot is unavailable.")
    snapshot = sanitize_snapshot(raw_snapshot, mix=mix_spec)
    digest = _canonical_json_sha256(snapshot)
    if (
        int(payload.get("schemaVersion") or 0)
        != TYPED_CHECKPOINT_SHARD_SCHEMA_VERSION
        or payload.get("jobId") != checkpoint.get("jobId")
        or payload.get("mix") != mix_spec.key
        or payload.get("sha256") != digest
        or reference.get("sha256") != digest
    ):
        raise ValueError("The typed analysis checkpoint snapshot failed validation.")
    return snapshot


def _database_cursor_token(checkpoint: Mapping[str, Any]) -> str:
    cursor = checkpoint.get("databaseCursor")
    if isinstance(cursor, Mapping):
        return "{phase}:{dataset}:{shard}".format(
            phase=str(checkpoint.get("phase") or ""),
            dataset=int(cursor.get("dataset") or 0),
            shard=int(cursor.get("shard") or 0),
        )
    return str(checkpoint.get("phase") or "")


def _audit_checkpoint_resume(
    blob_store: JsonBlobStore,
    checkpoint_path: str,
    checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail a repeatedly resumed checkpoint that never advances its durable token."""
    value = dict(checkpoint)
    if str(value.get("phase") or "") in {
        TYPED_CHECKPOINT_DATABASE_MODEL_PHASE,
        TYPED_CHECKPOINT_DATABASE_POINTERS_PHASE,
    }:
        attempts = int(value.get("publicationAttempts") or 0) + 1
        if attempts > MAX_PUBLICATION_ATTEMPTS:
            raise ValueError(
                "The typed publication checkpoint exhausted its automatic retries."
            )
        value["publicationAttempts"] = attempts
        blob_store.put_json(checkpoint_path, value)
        return value
    token = _database_cursor_token(value)
    raw_audit = value.get("resumeAudit")
    audit = dict(raw_audit) if isinstance(raw_audit, Mapping) else {}
    observations = (
        int(audit.get("observations") or 0) + 1
        if audit.get("token") == token
        else 1
    )
    if observations >= 3:
        raise ValueError(
            "The typed analysis checkpoint made no progress after three resumptions."
        )
    value["resumeAudit"] = {"token": token, "observations": observations}
    blob_store.put_json(checkpoint_path, value)
    return value


def _committed_model_shard_paths(
    blobs: JsonBlobStore,
    *,
    generation: str,
    phoenix1_count: int,
    phoenix2_count: int,
) -> tuple[list[str], list[str]]:
    """Validate a committed generation without downloading its private shards."""
    expected_phoenix1_paths = {
        recommendation_phoenix1_shard_path(generation, shard)
        for shard in range(phoenix1_count)
    }
    expected_phoenix2_paths = {
        recommendation_phoenix2_shard_path(generation, shard)
        for shard in range(phoenix2_count)
    }
    phoenix1_prefix = recommendation_phoenix1_shard_path(generation, 0).rsplit(
        "/", 1
    )[0] + "/"
    phoenix2_prefix = recommendation_phoenix2_shard_path(generation, 0).rsplit(
        "/", 1
    )[0] + "/"
    actual_phoenix1_paths = {
        item.pathname for item in blobs.list(phoenix1_prefix)
    }
    actual_phoenix2_paths = {
        item.pathname for item in blobs.list(phoenix2_prefix)
    }
    if (
        actual_phoenix1_paths != expected_phoenix1_paths
        or actual_phoenix2_paths != expected_phoenix2_paths
    ):
        raise RuntimeError(
            "A typed analysis checkpoint model input generation is incomplete."
        )
    return sorted(actual_phoenix1_paths), sorted(actual_phoenix2_paths)


def _recover_published_model_checkpoint(
    blobs: JsonBlobStore,
    *,
    job_id: str,
    snapshot: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Recover model output whose generation commit marker preceded a redelivery."""
    generation = recommendation_generation_key(job_id)
    index = blobs.get_json(recommendation_index_path(generation))
    if index is None:
        return None
    generated_at = payload.get("generatedAtUtc")
    raw_shard_count = index.get("inputShardCount")
    if (
        index.get("generationKey") != generation
        or index.get("modelGeneratedAtUtc") != generated_at
        or index.get("modelPath") != recommendation_model_path(generation)
        or not isinstance(raw_shard_count, int)
        or isinstance(raw_shard_count, bool)
        or raw_shard_count < 0
    ):
        raise ValueError("The durable recommendation model commit marker is invalid.")

    model = blobs.get_json(recommendation_model_path(generation))
    score_model = blobs.get_bytes(recommendation_score_model_path(generation))
    if (
        model is None
        or score_model is None
        or model.get("generationKey") != generation
        or model.get("generatedAtUtc") != generated_at
    ):
        raise RuntimeError("The durable recommendation model generation is incomplete.")

    phoenix1_paths, phoenix2_paths = _committed_model_shard_paths(
        blobs,
        generation=generation,
        phoenix1_count=raw_shard_count,
        phoenix2_count=raw_shard_count,
    )

    frozen_phoenix1 = blobs.get_json(phoenix1_snapshot_path())
    if frozen_phoenix1 is None:
        raise RuntimeError(
            "The Phoenix 1 snapshot for the durable recommendation model is unavailable."
        )
    phoenix1_snapshot = sanitize_snapshot(frozen_phoenix1, mix="phoenix1")
    combined_charts, _, combined_metadata = build_combined_chart_results(
        phoenix1_snapshot, snapshot
    )
    combined_tier = build_combined_tier_payload(
        combined_charts,
        combined_metadata,
        generated_at_utc=generated_at,
    )
    metadata = {
        "generationKey": generation,
        "phoenix1ShardCount": raw_shard_count,
        "phoenix2ShardCount": raw_shard_count,
        "indexSha256": _canonical_json_sha256(index),
        "modelSha256": _canonical_json_sha256(model),
        "scoreModelSha256": hashlib.sha256(score_model).hexdigest(),
        "phoenix1ShardsSha256": _canonical_json_sha256(phoenix1_paths),
        "phoenix2ShardsSha256": _canonical_json_sha256(phoenix2_paths),
    }
    return dict(combined_tier), dict(index), metadata


def _load_checkpoint_model_artifacts(
    blobs: JsonBlobStore, metadata: Mapping[str, Any] | None
) -> tuple[
    dict[str, Any] | None,
    tuple[
        dict[str, Any],
        dict[str, Any],
        bytes,
        int,
        int,
    ]
    | None,
]:
    if metadata is None:
        return None, None
    generation = str(metadata.get("generationKey") or "").strip()
    phoenix1_count = int(metadata.get("phoenix1ShardCount") or 0)
    phoenix2_count = int(metadata.get("phoenix2ShardCount") or 0)
    if not generation or phoenix1_count < 0 or phoenix2_count < 0:
        raise ValueError("The typed analysis checkpoint has invalid model metadata.")
    index = blobs.get_json(recommendation_index_path(generation))
    model = blobs.get_json(recommendation_model_path(generation))
    score_model = blobs.get_bytes(recommendation_score_model_path(generation))
    if (
        index is None
        or model is None
        or score_model is None
    ):
        raise RuntimeError("A typed analysis checkpoint model artifact is unavailable.")
    raw_index_shard_count = index.get("inputShardCount")
    if (
        raw_index_shard_count != phoenix1_count
        or raw_index_shard_count != phoenix2_count
    ):
        raise RuntimeError(
            "A typed analysis checkpoint model input generation is incomplete."
        )
    _committed_model_shard_paths(
        blobs,
        generation=generation,
        phoenix1_count=phoenix1_count,
        phoenix2_count=phoenix2_count,
    )
    if (
        metadata.get("indexSha256") != _canonical_json_sha256(index)
        or metadata.get("modelSha256") != _canonical_json_sha256(model)
        or metadata.get("scoreModelSha256")
        != hashlib.sha256(score_model).hexdigest()
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(metadata.get("phoenix1ShardsSha256") or "")
        )
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(metadata.get("phoenix2ShardsSha256") or "")
        )
    ):
        raise ValueError("A typed analysis checkpoint model artifact failed validation.")
    return dict(index), (
        dict(index),
        dict(model),
        score_model,
        phoenix1_count,
        phoenix2_count,
    )


def _load_checkpoint_recommendation_index(
    blobs: JsonBlobStore, metadata: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Validate only the committed index needed by the final pointer continuation."""
    if metadata is None:
        return None
    generation = str(metadata.get("generationKey") or "").strip()
    phoenix1_count = int(metadata.get("phoenix1ShardCount") or 0)
    phoenix2_count = int(metadata.get("phoenix2ShardCount") or 0)
    if not generation or phoenix1_count < 0 or phoenix2_count < 0:
        raise ValueError("The typed analysis checkpoint has invalid model metadata.")
    index = blobs.get_json(recommendation_index_path(generation))
    if index is None:
        raise RuntimeError("A typed analysis checkpoint recommendation index is unavailable.")
    if (
        index.get("generationKey") != generation
        or index.get("inputShardCount") != phoenix1_count
        or index.get("inputShardCount") != phoenix2_count
        or metadata.get("indexSha256") != _canonical_json_sha256(index)
    ):
        raise ValueError("A typed analysis checkpoint recommendation index failed validation.")
    return dict(index)


def _build_analysis_model_artifacts(
    *,
    blob_store: JsonBlobStore,
    job_store: JobStore,
    job_id: str,
    snapshot: Mapping[str, Any],
    payload: Mapping[str, Any],
    eligible_player_count: int,
    lease_heartbeat: Any | None,
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    tuple[
        dict[str, Any],
        dict[str, Any],
        bytes,
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]
    | None,
]:
    recommendation_payload: dict[str, Any] | None = None
    combined_tier_payload: dict[str, Any] | None = None
    recommendation_model_artifacts: tuple[
        dict[str, Any],
        dict[str, Any],
        bytes,
        list[dict[str, Any]],
        list[dict[str, Any]],
    ] | None = None
    frozen_phoenix1 = blob_store.get_json(phoenix1_snapshot_path())
    if frozen_phoenix1 is None:
        return (
            combined_tier_payload,
            recommendation_payload,
            recommendation_model_artifacts,
        )

    update_job(
        job_store,
        job_id,
        status="running",
        stage="analyzing",
        progress={
            "current": eligible_player_count,
            "total": eligible_player_count,
            "percent": 100,
            "message": "Combining Phoenix 1 and Phoenix 2 recommendation evidence.",
        },
    )
    phoenix1_snapshot = sanitize_snapshot(frozen_phoenix1, mix="phoenix1")
    combined_charts, combined_slopes, combined_metadata = (
        build_combined_chart_results(phoenix1_snapshot, snapshot)
    )
    combined_tier_payload = build_combined_tier_payload(
        combined_charts,
        combined_metadata,
        generated_at_utc=payload.get("generatedAtUtc"),
    )
    recommendation_generation = recommendation_generation_key(job_id)
    (
        recommendation_payload,
        recommendation_model,
        recommendation_score_model,
        recommendation_phoenix1_shards,
        recommendation_phoenix2_shards,
    ) = build_recommendation_model_artifacts(
        phoenix1_snapshot,
        snapshot,
        generated_at_utc=payload.get("generatedAtUtc"),
        combined_charts=combined_charts,
        phoenix2_slopes=combined_slopes,
        generation_key=recommendation_generation,
    )
    recommendation_model_artifacts = (
        recommendation_payload,
        recommendation_model,
        recommendation_score_model,
        recommendation_phoenix1_shards,
        recommendation_phoenix2_shards,
    )
    publish_recommendation_model_artifacts(
        blob_store,
        index=recommendation_payload,
        model=recommendation_model,
        score_model_bytes=recommendation_score_model,
        phoenix1_shards=recommendation_phoenix1_shards,
        phoenix2_shards=recommendation_phoenix2_shards,
        index_path=recommendation_blob_path(),
        publish_index=False,
    )
    _pulse_job_lease(lease_heartbeat)
    return (
        combined_tier_payload,
        recommendation_payload,
        recommendation_model_artifacts,
    )


def _checkpoint_continuation(
    *,
    job_store: JobStore,
    job_id: str,
    continuation: str,
    stage: str,
    message: str,
    lease_heartbeat: Any | None,
    sequence: str | None = None,
) -> dict[str, Any]:
    current = update_job(
        job_store,
        job_id,
        status="running",
        stage=stage,
        progress={
            "current": 1,
            "total": 1,
            "percent": 100,
            "message": message,
        },
    )
    _stop_job_lease(lease_heartbeat)
    handoff = getattr(job_store, "handoff_continuation", None)
    if callable(handoff):
        handoff(job_id, current)
    result = {**current, ANALYSIS_CONTINUATION_FIELD: continuation}
    if sequence is not None:
        result[ANALYSIS_CONTINUATION_SEQUENCE_FIELD] = sequence
    return result


def _resume_typed_analysis_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    blob_store: JsonBlobStore,
    job_store: JobStore,
    job_id: str,
    mix_spec: MixSpec,
    staging_path: str,
    checkpoint_path: str,
    lease_heartbeat: Any | None,
    yield_after_checkpoint: bool = False,
) -> dict[str, Any]:
    from piu_misgrade_analyzer import AnalysisConfig

    if (
        int(checkpoint.get("schemaVersion") or 0) != TYPED_CHECKPOINT_SCHEMA_VERSION
        or str(checkpoint.get("jobId") or "") != job_id
        or resolve_mix(checkpoint.get("mix")).key != mix_spec.key
    ):
        raise ValueError("The typed analysis checkpoint identity is invalid.")
    raw_snapshot_reference = checkpoint.get("snapshot")
    raw_config = checkpoint.get("config")
    raw_payload = checkpoint.get("payload")
    raw_combined_tier = checkpoint.get("combinedTier")
    raw_model = checkpoint.get("model")
    checkpoint_phase = str(checkpoint.get("phase") or "")
    eligible_player_count = checkpoint.get("eligiblePlayerCount")
    checkpoint_phases = {
        TYPED_CHECKPOINT_ANALYSIS_PHASE,
        TYPED_CHECKPOINT_MODEL_PHASE,
        TYPED_CHECKPOINT_SNAPSHOT_PHASE,
        TYPED_CHECKPOINT_DATABASE_SHARDS_PHASE,
        TYPED_CHECKPOINT_DATABASE_ANALYSIS_PHASE,
        TYPED_CHECKPOINT_DATABASE_MODEL_PHASE,
        TYPED_CHECKPOINT_DATABASE_POINTERS_PHASE,
    }
    if (
        not isinstance(raw_snapshot_reference, Mapping)
        or not isinstance(raw_config, Mapping)
        or not isinstance(raw_payload, Mapping)
        or checkpoint_phase not in checkpoint_phases
        or not isinstance(eligible_player_count, int)
        or eligible_player_count < 0
        or (raw_combined_tier is not None and not isinstance(raw_combined_tier, Mapping))
        or (raw_model is not None and not isinstance(raw_model, Mapping))
    ):
        raise ValueError("The typed analysis checkpoint payload is invalid.")
    snapshot = _load_typed_checkpoint_snapshot(
        blob_store,
        checkpoint=checkpoint,
        reference=raw_snapshot_reference,
        mix_spec=mix_spec,
    )
    config = AnalysisConfig(**dict(raw_config))
    if resolve_mix(config.mix).key != mix_spec.key:
        raise ValueError("The typed analysis checkpoint configuration is invalid.")
    payload = dict(raw_payload)
    if parse_utc(payload.get("generatedAtUtc")) is None:
        raise ValueError("The typed analysis checkpoint timestamp is invalid.")
    manifest = _typed_manifest(checkpoint)
    recovered_model_checkpoint = False
    if checkpoint_phase == TYPED_CHECKPOINT_ANALYSIS_PHASE and raw_model is None:
        recovered_model = _recover_published_model_checkpoint(
            blob_store,
            job_id=job_id,
            snapshot=snapshot,
            payload=payload,
        )
        if recovered_model is not None:
            recovered_combined_tier, _, recovered_metadata = recovered_model
            checkpoint = {
                **checkpoint,
                "phase": TYPED_CHECKPOINT_MODEL_PHASE,
                "combinedTier": recovered_combined_tier,
                "model": recovered_metadata,
            }
            blob_store.put_json(checkpoint_path, checkpoint)
            _pulse_job_lease(lease_heartbeat)
            checkpoint_phase = TYPED_CHECKPOINT_MODEL_PHASE
            raw_combined_tier = recovered_combined_tier
            raw_model = recovered_metadata
            recovered_model_checkpoint = True
    if not recovered_model_checkpoint:
        checkpoint = _audit_checkpoint_resume(
            blob_store, checkpoint_path, checkpoint
        )
    elif yield_after_checkpoint:
        return _checkpoint_continuation(
            job_store=job_store,
            job_id=job_id,
            continuation="snapshot",
            stage="publishing",
            message=(
                "Recovered the durable recommendation model; queued for snapshot "
                "persistence."
            ),
            lease_heartbeat=lease_heartbeat,
        )

    if checkpoint_phase == TYPED_CHECKPOINT_ANALYSIS_PHASE:
        (
            combined_tier_payload,
            recommendation_payload,
            model_artifacts,
        ) = _build_analysis_model_artifacts(
            blob_store=blob_store,
            job_store=job_store,
            job_id=job_id,
            snapshot=snapshot,
            payload=payload,
            eligible_player_count=eligible_player_count,
            lease_heartbeat=lease_heartbeat,
        )
        model_checkpoint = None
        if model_artifacts is not None:
            model_checkpoint = {
                "generationKey": model_artifacts[0].get("generationKey"),
                "phoenix1ShardCount": len(model_artifacts[3]),
                "phoenix2ShardCount": len(model_artifacts[4]),
                "indexSha256": _canonical_json_sha256(model_artifacts[0]),
                "modelSha256": _canonical_json_sha256(model_artifacts[1]),
                "scoreModelSha256": hashlib.sha256(model_artifacts[2]).hexdigest(),
                "phoenix1ShardsSha256": _canonical_json_sha256(
                    [_canonical_json_sha256(value) for value in model_artifacts[3]]
                ),
                "phoenix2ShardsSha256": _canonical_json_sha256(
                    [_canonical_json_sha256(value) for value in model_artifacts[4]]
                ),
            }
        checkpoint = {
            **checkpoint,
            "phase": TYPED_CHECKPOINT_MODEL_PHASE,
            "combinedTier": combined_tier_payload,
            "model": model_checkpoint,
        }
        blob_store.put_json(checkpoint_path, checkpoint)
        _pulse_job_lease(lease_heartbeat)
        checkpoint_phase = TYPED_CHECKPOINT_MODEL_PHASE
        raw_combined_tier = combined_tier_payload
        raw_model = model_checkpoint
        if yield_after_checkpoint:
            return _checkpoint_continuation(
                job_store=job_store,
                job_id=job_id,
                continuation="snapshot",
                stage="publishing",
                message="Recommendation model checkpointed; queued for snapshot persistence.",
                lease_heartbeat=lease_heartbeat,
            )

    if checkpoint_phase == TYPED_CHECKPOINT_MODEL_PHASE:
        update_job(
            job_store,
            job_id,
            status="running",
            stage="publishing",
            progress={
                "current": 1,
                "total": 1,
                "percent": 100,
                "message": "Persisting the canonical private snapshot.",
            },
        )
        if not bool(checkpoint.get("reanalyzeOnly")):
            blob_store.put_json(current_snapshot_path(mix_spec), snapshot)
        checkpoint = {
            **checkpoint,
            "phase": TYPED_CHECKPOINT_SNAPSHOT_PHASE,
        }
        blob_store.put_json(checkpoint_path, checkpoint)
        _pulse_job_lease(lease_heartbeat)
        checkpoint_phase = TYPED_CHECKPOINT_SNAPSHOT_PHASE
        if yield_after_checkpoint:
            return _checkpoint_continuation(
                job_store=job_store,
                job_id=job_id,
                continuation="database-analysis",
                stage="publishing",
                message="Canonical snapshot checkpointed; queued for typed analysis persistence.",
                lease_heartbeat=lease_heartbeat,
            )

    typed_publisher = getattr(blob_store, "persist_typed_generation", None)
    if not callable(typed_publisher):
        raise RuntimeError("Typed Pumbility persistence is not available.")
    typed_kwargs = {
        "job_external_key": job_id,
        "mix_key": mix_spec.key,
        "snapshot": snapshot,
        "config": config,
        "payload": payload,
        "analysis_manifest": manifest,
    }

    if checkpoint_phase == TYPED_CHECKPOINT_SNAPSHOT_PHASE:
        update_job(
            job_store,
            job_id,
            status="running",
            stage="publishing",
            progress={
                "current": 1,
                "total": 1,
                "percent": 100,
                "message": "Preparing resumable typed analysis output.",
            },
        )
        analysis_run_id, _ = typed_publisher(
            **typed_kwargs,
            model_artifacts=None,
            phase="analysis-start",
        )
        if analysis_run_id is None:
            raise RuntimeError("Typed analysis persistence returned no generation identity.")
        checkpoint = {
            **checkpoint,
            "phase": TYPED_CHECKPOINT_DATABASE_SHARDS_PHASE,
            "analysisRunId": str(analysis_run_id),
            "databaseCursor": {"dataset": 0, "shard": 0},
        }
        blob_store.put_json(checkpoint_path, checkpoint)
        _pulse_job_lease(lease_heartbeat)
        checkpoint_phase = TYPED_CHECKPOINT_DATABASE_SHARDS_PHASE
        if yield_after_checkpoint:
            return _checkpoint_continuation(
                job_store=job_store,
                job_id=job_id,
                continuation="database-analysis",
                stage="publishing",
                message="Typed analysis generation prepared; queued for bounded row persistence.",
                lease_heartbeat=lease_heartbeat,
                sequence="start",
            )

    analysis_run_id = checkpoint.get("analysisRunId")
    if checkpoint_phase in {
        TYPED_CHECKPOINT_DATABASE_SHARDS_PHASE,
        TYPED_CHECKPOINT_DATABASE_ANALYSIS_PHASE,
        TYPED_CHECKPOINT_DATABASE_MODEL_PHASE,
        TYPED_CHECKPOINT_DATABASE_POINTERS_PHASE,
    } and (not isinstance(analysis_run_id, str) or not analysis_run_id.strip()):
        raise ValueError("The typed analysis checkpoint has no analysis generation identity.")

    if checkpoint_phase == TYPED_CHECKPOINT_DATABASE_SHARDS_PHASE:
        ordered_datasets = ("baselines", "contributions", "chartResults")
        raw_cursor = checkpoint.get("databaseCursor")
        if not isinstance(raw_cursor, Mapping):
            raise ValueError("The typed analysis checkpoint has no database cursor.")
        dataset_index = int(raw_cursor.get("dataset") or 0)
        shard_index = int(raw_cursor.get("shard") or 0)
        if dataset_index < 0 or shard_index < 0 or dataset_index > len(ordered_datasets):
            raise ValueError("The typed analysis checkpoint database cursor is invalid.")
        total_shards = sum(
            int(manifest["datasets"][name]["shardCount"])
            for name in ordered_datasets
        )
        while dataset_index < len(ordered_datasets):
            dataset = ordered_datasets[dataset_index]
            descriptors = manifest["datasets"][dataset]["shards"]
            if shard_index >= len(descriptors):
                dataset_index += 1
                shard_index = 0
                continue
            descriptor = descriptors[shard_index]
            rows = _load_typed_checkpoint_shard(
                blob_store,
                checkpoint=checkpoint,
                dataset=dataset,
                descriptor=descriptor,
            )
            completed_before = sum(
                int(manifest["datasets"][name]["shardCount"])
                for name in ordered_datasets[:dataset_index]
            ) + shard_index
            update_job(
                job_store,
                job_id,
                status="running",
                stage="publishing",
                progress={
                    "current": completed_before,
                    "total": total_shards,
                    "percent": (
                        int((completed_before / total_shards) * 100)
                        if total_shards
                        else 100
                    ),
                    "message": (
                        f"Persisting typed analysis shard "
                        f"{completed_before + 1:,}/{total_shards:,}."
                    ),
                },
            )
            typed_publisher(
                **typed_kwargs,
                model_artifacts=None,
                phase="analysis-chunk",
                analysis_run_id=analysis_run_id,
                analysis_dataset=dataset,
                analysis_rows=rows,
                analysis_chunk_sha256=str(descriptor["sha256"]),
            )
            del rows
            shard_index += 1
            checkpoint = {
                **checkpoint,
                "databaseCursor": {
                    "dataset": dataset_index,
                    "shard": shard_index,
                },
            }
            blob_store.put_json(checkpoint_path, checkpoint)
            _pulse_job_lease(lease_heartbeat)
            if yield_after_checkpoint:
                return _checkpoint_continuation(
                    job_store=job_store,
                    job_id=job_id,
                    continuation="database-analysis",
                    stage="publishing",
                    message=(
                        f"Typed analysis shard {completed_before + 1:,}/"
                        f"{total_shards:,} persisted; continuation queued."
                    ),
                    lease_heartbeat=lease_heartbeat,
                    sequence=f"{completed_before + 1:06d}",
                )
        typed_publisher(
            **typed_kwargs,
            model_artifacts=None,
            phase="analysis-finish",
            analysis_run_id=analysis_run_id,
        )
        checkpoint = {
            **checkpoint,
            "phase": TYPED_CHECKPOINT_DATABASE_ANALYSIS_PHASE,
            "databaseCursor": {
                "dataset": len(ordered_datasets),
                "shard": 0,
            },
        }
        blob_store.put_json(checkpoint_path, checkpoint)
        _pulse_job_lease(lease_heartbeat)
        checkpoint_phase = TYPED_CHECKPOINT_DATABASE_ANALYSIS_PHASE
        if yield_after_checkpoint:
            return _checkpoint_continuation(
                job_store=job_store,
                job_id=job_id,
                continuation="database-model",
                stage="publishing",
                message="Typed analysis validated; queued for model persistence.",
                lease_heartbeat=lease_heartbeat,
            )

    recommendation_payload: dict[str, Any] | None = None
    if checkpoint_phase == TYPED_CHECKPOINT_DATABASE_ANALYSIS_PHASE:
        recommendation_payload, model_artifacts = _load_checkpoint_model_artifacts(
            blob_store, dict(raw_model) if raw_model is not None else None
        )
        update_job(
            job_store,
            job_id,
            status="running",
            stage="publishing",
            progress={
                "current": 1,
                "total": 1,
                "percent": 100,
                "message": "Persisting the typed recommendation model.",
            },
        )
        _, model_generation_id = typed_publisher(
            **typed_kwargs,
            model_artifacts=model_artifacts,
            phase="model",
            analysis_run_id=analysis_run_id,
        )
        checkpoint = {
            **checkpoint,
            "phase": TYPED_CHECKPOINT_DATABASE_MODEL_PHASE,
            "modelGenerationId": (
                str(model_generation_id) if model_generation_id is not None else None
            ),
        }
        blob_store.put_json(checkpoint_path, checkpoint)
        _pulse_job_lease(lease_heartbeat)
        checkpoint_phase = TYPED_CHECKPOINT_DATABASE_MODEL_PHASE
        if yield_after_checkpoint:
            return _checkpoint_continuation(
                job_store=job_store,
                job_id=job_id,
                continuation="publish",
                stage="publishing",
                message="Typed model checkpointed; queued for atomic pointer publication.",
                lease_heartbeat=lease_heartbeat,
            )

    if recommendation_payload is None:
        recommendation_payload = _load_checkpoint_recommendation_index(
            blob_store, dict(raw_model) if raw_model is not None else None
        )
    model_generation_id = checkpoint.get("modelGenerationId")
    if not isinstance(model_generation_id, str) or not model_generation_id.strip():
        raise ValueError("The typed analysis checkpoint has no model generation identity.")
    combined_tier_payload = (
        dict(raw_combined_tier) if raw_combined_tier is not None else None
    )
    if checkpoint_phase != TYPED_CHECKPOINT_DATABASE_POINTERS_PHASE:
        update_job(
            job_store,
            job_id,
            status="running",
            stage="publishing",
            progress={
                "current": 1,
                "total": 1,
                "percent": 100,
                "message": "Committing validated public ranking pointers.",
            },
        )
        _pulse_job_lease(lease_heartbeat)
        publish_success(
            blob_store,
            job_id=job_id,
            snapshot=snapshot,
            payload=payload,
            recommendations=recommendation_payload,
            combined_tier=combined_tier_payload,
            publish_snapshot=False,
            mix=mix_spec,
            analysis_run_id=str(analysis_run_id),
            model_generation_id=model_generation_id,
            defer_cleanup=True,
        )
        checkpoint = {
            **checkpoint,
            "phase": TYPED_CHECKPOINT_DATABASE_POINTERS_PHASE,
        }
        blob_store.put_json(checkpoint_path, checkpoint)
        _pulse_job_lease(lease_heartbeat)
        checkpoint_phase = TYPED_CHECKPOINT_DATABASE_POINTERS_PHASE
    else:
        committed_reader = getattr(blob_store, "publication_committed", None)
        if callable(committed_reader) and not committed_reader(
            generation_key=str(recommendation_payload.get("generationKey") or ""),
            analysis_run_id=str(analysis_run_id),
            model_generation_id=model_generation_id,
        ):
            raise RuntimeError("The durable pointer publication could not be confirmed.")

    completed = update_job(
        job_store,
        job_id,
        status="completed",
        stage="publishing",
        generatedAtUtc=payload.get("generatedAtUtc"),
        retryAllowedAtUtc=None,
        error=None,
        progress={
            "current": 1,
            "total": 1,
            "percent": 100,
            "message": "Rankings refreshed successfully.",
        },
    )
    try:
        if job_store.active_job_id() == job_id:
            job_store.set_active_job_id(None)
    except Exception as active_head_error:
        _publication_event(
            "post-publish-cleanup",
            "active-head-warning",
            time.perf_counter(),
            error=active_head_error,
        )
    try:
        _stop_job_lease(lease_heartbeat)
    except Exception as heartbeat_error:
        _publication_event(
            "post-publish-cleanup",
            "heartbeat-warning",
            time.perf_counter(),
            error=heartbeat_error,
        )
    _post_publish_cleanup(
        blob_store,
        recommendations=recommendation_payload,
        mix=mix_spec,
        checkpoint_paths=[
            staging_path,
            checkpoint_path,
            typed_checkpoint_snapshot_path(job_id, mix_spec),
            *_typed_checkpoint_shard_paths(manifest),
        ],
    )
    return completed


def _snapshot_from_raw_dir(
    raw_dir: Path,
    timestamp: datetime,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> dict[str, Any]:
    mix_spec = resolve_mix(mix)
    manifest_path = raw_dir / "snapshot_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(manifest, Mapping) and manifest.get("mix"):
            manifest_mix = resolve_mix(manifest.get("mix"))
            if manifest_mix.key != mix_spec.key:
                raise ValueError(
                    f"Raw snapshot mix {manifest_mix.label} does not match requested mix "
                    f"{mix_spec.label}."
                )
    players, charts, scores = load_snapshot(raw_dir)
    player_metadata = {
        str(row.get("userId")): str(row.get("username") or "").strip()
        for row in players
        if row.get("userId") is not None and str(row.get("userId")).strip()
    }
    stamp = isoformat_utc(timestamp)
    return sanitize_snapshot(
        {
            "schemaVersion": 1,
            "mix": mix_spec.api_value,
            "generatedAtUtc": stamp,
            "players": [
                {
                    "playerId": player_id,
                    "username": player_metadata[player_id],
                    "lastSyncedAtUtc": stamp,
                }
                for player_id in sorted(player_metadata)
            ],
            "charts": charts,
            "scores": scores,
        },
        mix=mix_spec,
    )


def execute_analysis_job(
    job_id: str,
    *,
    blobs: JsonBlobStore | None = None,
    jobs: JobStore | None = None,
    client: Any | None = None,
    now: Callable[[], datetime] = utc_now,
    yield_after_typed_checkpoint: bool = False,
) -> dict[str, Any]:
    """Run one idempotent, checkpointed refresh in a queue worker."""
    from piu_misgrade_analyzer import AnalysisConfig, ApiError

    blob_store = blobs or PrivateBlobStore()
    job_store = jobs or RuntimeJobStore()
    existing = job_store.get(job_id)
    if existing is None:
        raise RuntimeError("The queued analysis job status was not found.")
    if existing.get("status") == "completed" or existing.get("cancelRequested"):
        return existing
    mix_spec = resolve_mix(existing.get("mix"))
    if mix_spec.archived:
        archived = update_job(
            job_store,
            job_id,
            status="failed",
            error=f"{mix_spec.label} is archived and cannot be refreshed.",
            retryAllowedAtUtc=None,
            progress={
                "current": 0,
                "total": 0,
                "percent": 0,
                "message": "Archived snapshots cannot be refreshed.",
            },
        )
        if job_store.active_job_id() == job_id:
            job_store.set_active_job_id(None)
        return archived

    active_id = job_store.active_job_id()
    if active_id and active_id != job_id:
        other = job_store.get(active_id)
        if other and other.get("status") in {"queued", "running"}:
            return update_job(
                job_store,
                job_id,
                status="failed",
                error="Another analysis refresh is already active.",
                retryAllowedAtUtc=isoformat_utc(now() + FAILED_RETRY_DELAY),
            )
    job_store.set_active_job_id(job_id)
    try:
        update_job(
            job_store,
            job_id,
            status="running",
            stage="discovering",
            error=None,
            progress={
                "current": 0,
                "total": 0,
                "percent": 0,
                "message": (
                    f"Loading the stored {mix_spec.label} snapshot for model reanalysis."
                    if existing.get("reanalyzeOnly")
                    else f"Reading the consented-player list and {mix_spec.label} catalog."
                ),
            },
        )
    except RuntimeError:
        current = job_store.get(job_id)
        if current is not None and current.get("status") == "running":
            # At-least-once delivery may overlap the worker already holding this
            # job's lease. A duplicate delivery is an acknowledged no-op.
            return current
        raise
    staging_path = f"{staging_prefix(mix_spec)}{job_id}.json"
    checkpoint_path = typed_checkpoint_path(job_id, mix_spec)
    typed_persistence_enabled = bool(
        getattr(blob_store, "typed_persistence_enabled", False)
    )
    lease_heartbeat: Any | None = None

    try:
        lease_heartbeat = _start_job_lease_heartbeat(job_store, job_id)
        _pulse_job_lease(lease_heartbeat)
        cleanup_abandoned_staging(
            blob_store, now=now(), keep_path=staging_path, mix=mix_spec
        )
        cleanup_abandoned_typed_checkpoints(
            blob_store, now=now(), keep_job_id=job_id, mix=mix_spec
        )
        if typed_persistence_enabled:
            typed_checkpoint = blob_store.get_json(checkpoint_path)
            if typed_checkpoint is not None:
                return _resume_typed_analysis_checkpoint(
                    typed_checkpoint,
                    blob_store=blob_store,
                    job_store=job_store,
                    job_id=job_id,
                    mix_spec=mix_spec,
                    staging_path=staging_path,
                    checkpoint_path=checkpoint_path,
                    lease_heartbeat=lease_heartbeat,
                    yield_after_checkpoint=yield_after_typed_checkpoint,
                )
        current = blob_store.get_json(current_snapshot_path(mix_spec))
        reanalyze_only = bool(existing.get("reanalyzeOnly"))
        resume = None if reanalyze_only else blob_store.get_json(staging_path)
        raw_dir_setting = os.getenv(
            f"PIU_ANALYSIS_RAW_DIR_{mix_spec.key.upper()}",
            os.getenv("PIU_ANALYSIS_RAW_DIR", ""),
        ).strip()
        if reanalyze_only:
            if current is None:
                raise ValueError(
                    f"No stored {mix_spec.label} snapshot is available for model reanalysis."
                )
            snapshot = sanitize_snapshot(current, mix=mix_spec)
        elif raw_dir_setting:
            snapshot = _snapshot_from_raw_dir(
                Path(raw_dir_setting), now(), mix=mix_spec
            )
            staging = {
                "schemaVersion": 1,
                "mix": mix_spec.api_value,
                "jobId": job_id,
                "createdAtUtc": isoformat_utc(now()),
                "updatedAtUtc": isoformat_utc(now()),
                "runStartedAtUtc": snapshot["generatedAtUtc"],
                "consentedPlayerIds": [row["playerId"] for row in snapshot["players"]],
                "completedPlayerIds": [row["playerId"] for row in snapshot["players"]],
                "snapshot": snapshot,
            }
            blob_store.put_json(staging_path, staging)
        else:
            effective_client = client
            if effective_client is None:
                api_key = os.getenv("PIU_SCORES_API_KEY", "").strip()
                if not api_key:
                    raise ApiError(
                        "PIU_SCORES_API_KEY is not configured as a server-side environment variable."
                    )
                effective_client = PiuScoresClient(api_key=api_key)

            def on_progress(current_count: int, total: int, message: str) -> None:
                percent = int((current_count / total) * 100) if total else 0
                update_job(
                    job_store,
                    job_id,
                    status="running",
                    stage="syncing" if total else "discovering",
                    progress={
                        "current": current_count,
                        "total": total,
                        "percent": percent,
                        "message": message,
                    },
                )

            synchronize = (
                synchronize_phoenix2_snapshot
                if mix_spec.key == DEFAULT_MIX_KEY
                else synchronize_mix_snapshot
            )
            sync_kwargs: dict[str, Any] = {}
            if mix_spec.key != DEFAULT_MIX_KEY:
                sync_kwargs["mix"] = mix_spec
            player_checkpoint_writer = getattr(
                blob_store, "put_sync_checkpoint_players", None
            )
            if callable(player_checkpoint_writer):
                sync_kwargs["checkpoint_players"] = lambda value: player_checkpoint_writer(
                    staging_path, value
                )
            snapshot, _ = synchronize(
                effective_client,
                None if existing.get("fullSync") else current,
                job_id=job_id,
                resume_staging=resume,
                workers=6,
                checkpoint_every=50,
                progress=on_progress,
                checkpoint=lambda value: blob_store.put_json(staging_path, value),
                now=now,
                **sync_kwargs,
            )

        _pulse_job_lease(lease_heartbeat)
        config = AnalysisConfig(
            mix=mix_spec.key,
            bootstrap_samples=int(os.getenv("ANALYSIS_BOOTSTRAP_SAMPLES", "500"))
        )
        players, charts, scores = analyzer_input(
            snapshot,
            minimum_scores_per_mode=config.minimum_scores_per_player,
            eligible_only=True,
        )
        eligible_player_count = len(players)
        update_job(
            job_store,
            job_id,
            status="running",
            stage="analyzing",
            progress={
                "current": eligible_player_count,
                "total": eligible_player_count,
                "percent": 100,
                "message": f"Analyzing {eligible_player_count:,} eligible players in separate modes.",
            },
        )
        chart_results, baseline_frame, summary, contribution_frame = analyze_snapshot(
            players, charts, scores, config
        )
        _pulse_job_lease(lease_heartbeat)
        payload = build_web_payload(chart_results, summary)
        if typed_persistence_enabled:
            typed_shards = _write_typed_frame_shards(
                blob_store=blob_store,
                job_store=job_store,
                job_id=job_id,
                mix_spec=mix_spec,
                frames={
                    "baselines": baseline_frame,
                    "contributions": contribution_frame,
                    "chartResults": chart_results,
                },
                lease_heartbeat=lease_heartbeat,
            )
            typed_snapshot = _write_typed_checkpoint_snapshot(
                blob_store,
                job_id=job_id,
                mix_spec=mix_spec,
                snapshot=snapshot,
            )
            _pulse_job_lease(lease_heartbeat)
        else:
            typed_shards = None
            typed_snapshot = None
        del chart_results, baseline_frame, contribution_frame, players, charts, scores
        gc.collect()
        if typed_persistence_enabled:
            checkpoint = {
                "schemaVersion": TYPED_CHECKPOINT_SCHEMA_VERSION,
                "phase": TYPED_CHECKPOINT_ANALYSIS_PHASE,
                "jobId": job_id,
                "mix": mix_spec.key,
                "createdAtUtc": isoformat_utc(now()),
                "reanalyzeOnly": reanalyze_only,
                "eligiblePlayerCount": eligible_player_count,
                "snapshot": typed_snapshot,
                "config": asdict(config),
                "payload": payload,
                "typedShards": typed_shards,
                "combinedTier": None,
                "model": None,
            }
            blob_store.put_json(checkpoint_path, checkpoint)
            _pulse_job_lease(lease_heartbeat)
            if yield_after_typed_checkpoint:
                return _checkpoint_continuation(
                    job_store=job_store,
                    job_id=job_id,
                    continuation="model",
                    stage="analyzing",
                    message="Base analysis checkpointed; queued for recommendation modeling.",
                    lease_heartbeat=lease_heartbeat,
                )
            return _resume_typed_analysis_checkpoint(
                checkpoint,
                blob_store=blob_store,
                job_store=job_store,
                job_id=job_id,
                mix_spec=mix_spec,
                staging_path=staging_path,
                checkpoint_path=checkpoint_path,
                lease_heartbeat=lease_heartbeat,
            )

        (
            combined_tier_payload,
            recommendation_payload,
            recommendation_model_artifacts,
        ) = _build_analysis_model_artifacts(
            blob_store=blob_store,
            job_store=job_store,
            job_id=job_id,
            snapshot=snapshot,
            payload=payload,
            eligible_player_count=eligible_player_count,
            lease_heartbeat=lease_heartbeat,
        )

        update_job(
            job_store,
            job_id,
            status="running",
            stage="publishing",
            progress={
                "current": eligible_player_count,
                "total": eligible_player_count,
                "percent": 100,
                "message": "Publishing the private snapshot and refreshed rankings.",
            },
        )
        _pulse_job_lease(lease_heartbeat)
        publish_snapshot = not reanalyze_only
        publish_success(
            blob_store,
            job_id=job_id,
            snapshot=snapshot,
            payload=payload,
            recommendations=recommendation_payload,
            combined_tier=combined_tier_payload,
            publish_snapshot=publish_snapshot,
            mix=mix_spec,
            defer_cleanup=True,
        )
        completed = update_job(
            job_store,
            job_id,
            status="completed",
            stage="publishing",
            generatedAtUtc=payload.get("generatedAtUtc"),
            retryAllowedAtUtc=None,
            error=None,
            progress={
                "current": 1,
                "total": 1,
                "percent": 100,
                "message": "Rankings refreshed successfully.",
            },
        )
        try:
            if job_store.active_job_id() == job_id:
                job_store.set_active_job_id(None)
        except Exception as active_head_error:
            _publication_event(
                "post-publish-cleanup",
                "active-head-warning",
                time.perf_counter(),
                error=active_head_error,
            )
        try:
            _stop_job_lease(lease_heartbeat)
        except Exception as heartbeat_error:
            _publication_event(
                "post-publish-cleanup",
                "heartbeat-warning",
                time.perf_counter(),
                error=heartbeat_error,
            )
        lease_heartbeat = None
        _post_publish_cleanup(
            blob_store,
            recommendations=recommendation_payload,
            mix=mix_spec,
            checkpoint_paths=[staging_path, checkpoint_path],
        )
        return completed
    except Exception as exc:
        if lease_heartbeat is not None:
            try:
                _stop_job_lease(lease_heartbeat)
            except Exception as heartbeat_error:
                _PUBLICATION_LOGGER.warning(
                    json.dumps(
                        {
                            "event": "analysis_lease_stop",
                            "outcome": "secondary-failure",
                            **_publication_error_fields(heartbeat_error),
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
            lease_heartbeat = None
        failed_at = now()
        message = safe_error(exc)
        try:
            failed = update_job(
                job_store,
                job_id,
                now=failed_at,
                status="failed",
                error=message,
                retryAllowedAtUtc=isoformat_utc(failed_at + FAILED_RETRY_DELAY),
                progress={
                    "current": 0,
                    "total": 0,
                    "percent": 0,
                    "message": "Analysis failed; staging data was kept for a worker retry.",
                },
            )
        finally:
            if job_store.active_job_id() == job_id:
                job_store.set_active_job_id(None)
        return failed
