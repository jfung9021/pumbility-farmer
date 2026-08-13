"""Durable analysis job coordination, status caching, and private Blob storage."""

from __future__ import annotations

import gc
import json
import os
import re
import threading
from dataclasses import dataclass
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
from piu_misgrade_analyzer import (
    AnalysisConfig,
    ApiError,
    PiuScoresClient,
    SCRIPT_VERSION,
    analyze_snapshot,
    build_web_payload,
    load_snapshot,
)
from piu_recommendations import (
    build_combined_chart_results,
    build_combined_tier_payload,
    combined_tier_blob_path,
    phoenix1_snapshot_path,
    recommendation_blob_path,
    recommendation_generation_key,
    recommendation_shard_prefix,
)
from recommendation_refresh import (
    build_recommendation_model_artifacts,
    player_refresh_enabled,
    publish_recommendation_model_artifacts,
    recommendation_index_path,
)


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


class VercelPrivateBlobStore:
    """Minimal private-only Vercel Blob adapter."""

    def __init__(self, token: str | None = None) -> None:
        self.token = (token if token is not None else os.getenv("BLOB_READ_WRITE_TOKEN", "")).strip()
        if not self.token:
            raise RuntimeError("BLOB_READ_WRITE_TOKEN is not configured for the private analysis store.")

    def get_json(self, pathname: str) -> dict[str, Any] | None:
        from vercel.blob import BlobClient
        from vercel.blob.errors import BlobNotFoundError

        try:
            with BlobClient(token=self.token) as client:
                result = client.get(pathname, access="private", use_cache=False)
        except BlobNotFoundError:
            return None
        value = json.loads(result.content.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Private Blob object {pathname!r} did not contain a JSON object.")
        return value

    def get_bytes(self, pathname: str) -> bytes | None:
        from vercel.blob import BlobClient
        from vercel.blob.errors import BlobNotFoundError

        try:
            with BlobClient(token=self.token) as client:
                result = client.get(pathname, access="private", use_cache=False)
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
    """Select the configured private persistence backend.

    ``PUMBILITY_DATA_BACKEND`` defaults to ``vercel``.  ``shadow`` keeps all
    reads on Vercel and mirrors writes to Supabase; ``supabase`` selects the
    new store directly.  Keeping selection at the existing constructor makes
    this migration transparent to every current route, worker, and test seam.
    """

    def __new__(cls, token: str | None = None) -> JsonBlobStore:
        from pumbility_store import select_json_store

        return select_json_store(lambda: VercelPrivateBlobStore(token=token))


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
    """Select Vercel Runtime Cache, Supabase, or legacy-primary shadow jobs."""

    def __new__(cls) -> JobStore:
        from pumbility_store import select_job_store

        return select_job_store(VercelRuntimeJobStore)


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


def _run_path(
    payload: Mapping[str, Any],
    job_id: str,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> str:
    generated = parse_utc(payload.get("generatedAtUtc")) or utc_now()
    stamp = generated.strftime("%Y%m%dT%H%M%S%fZ")
    return f"{runs_prefix(mix)}{stamp}-{job_id}.json"


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
) -> None:
    """Publish derived artifacts, optionally promote the snapshot, and enforce retention."""
    mix_spec = resolve_mix(mix)
    if mix_spec.archived:
        raise ValueError(f"{mix_spec.label} is archived and cannot be published.")
    blobs.put_json(_run_path(payload, job_id, mix_spec), payload)
    publication_pointers: dict[str, Mapping[str, Any]] = {}
    if publish_snapshot:
        blobs.put_json(
            current_snapshot_path(mix_spec),
            sanitize_snapshot(snapshot, mix=mix_spec),
        )
    if recommendations is not None:
        generation_key = str(recommendations.get("generationKey") or "").strip()
        storage_schema = int(recommendations.get("storageSchemaVersion") or 0)
        promote = storage_schema < 3 or player_refresh_enabled(recommendations)
        if promote:
            previous_recommendations = blobs.get_json(recommendation_blob_path())
            if previous_recommendations is not None:
                previous_generation = str(
                    previous_recommendations.get("generationKey") or ""
                ).strip()
                if previous_generation:
                    blobs.put_json(
                        recommendation_index_path(previous_generation),
                        previous_recommendations,
                    )
            publication_pointers[recommendation_blob_path()] = recommendations
        if (
            storage_schema == 2
            and generation_key
            and os.getenv("PLAYER_RECOMMENDATION_PRUNE_LEGACY", "").strip().lower()
            in {"1", "true", "yes", "on"}
        ):
            keep_prefix = recommendation_shard_prefix(generation_key)
            stale_recommendations = [
                item.pathname
                for item in blobs.list(recommendation_shard_prefix())
                if not item.pathname.startswith(keep_prefix)
            ]
            if stale_recommendations:
                blobs.delete(stale_recommendations)
        elif storage_schema >= 3 and generation_key:
            _cleanup_recommendation_generations(blobs, generation_key)
            _cleanup_revoked_player_artifacts(blobs, recommendations)
            if promote and os.getenv(
                "PLAYER_RECOMMENDATION_PRUNE_LEGACY", ""
            ).strip().lower() in {"1", "true", "yes", "on"}:
                legacy_paths = [
                    item.pathname for item in blobs.list(recommendation_shard_prefix())
                ]
                if legacy_paths:
                    blobs.delete(legacy_paths)
    if combined_tier is not None:
        publication_pointers[combined_tier_blob_path()] = combined_tier
    publication_pointers[latest_blob_path(mix_spec)] = payload
    bundle_writer = getattr(blobs, "put_json_bundle", None)
    if callable(bundle_writer):
        bundle_writer(publication_pointers)
    else:
        for pathname, pointer_payload in publication_pointers.items():
            blobs.put_json(pathname, pointer_payload)
    runs = sorted(
        blobs.list(runs_prefix(mix_spec)), key=lambda item: item.pathname, reverse=True
    )
    stale = [item.pathname for item in runs[RUN_RETENTION:]]
    if stale:
        blobs.delete(stale)


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
        stored_index = blobs.get_json(item.pathname)
        generation = Path(item.pathname).stem
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
    if stale:
        blobs.delete(sorted(set(stale)))


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
    if stale:
        blobs.delete(stale)


_SECRET_PATTERN = re.compile(r"(?:piu_scores_live_|pst_live_)[0-9a-f]{16,}", re.IGNORECASE)


def safe_error(exc: BaseException) -> str:
    if isinstance(exc, (ApiError, FileNotFoundError, ValueError)):
        message = str(exc).strip() or "The analysis could not be completed."
        return _SECRET_PATTERN.sub("[credential redacted]", message)[:500]
    return "The analysis failed unexpectedly. Please retry after the cooldown."


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
) -> dict[str, Any]:
    """Run one idempotent, checkpointed refresh in a queue worker."""
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
    staging_path = f"{staging_prefix(mix_spec)}{job_id}.json"
    lease_heartbeat: Any | None = None

    try:
        lease_heartbeat = _start_job_lease_heartbeat(job_store, job_id)
        _pulse_job_lease(lease_heartbeat)
        cleanup_abandoned_staging(
            blob_store, now=now(), keep_path=staging_path, mix=mix_spec
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
        chart_results, _, summary, _ = analyze_snapshot(players, charts, scores, config)
        _pulse_job_lease(lease_heartbeat)
        payload = build_web_payload(chart_results, summary)
        del chart_results, players, charts, scores
        gc.collect()
        recommendation_payload: dict[str, Any] | None = None
        combined_tier_payload: dict[str, Any] | None = None
        frozen_phoenix1 = blob_store.get_json(phoenix1_snapshot_path())
        if frozen_phoenix1 is not None:
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
            del frozen_phoenix1
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
        publish_success(
            blob_store,
            job_id=job_id,
            snapshot=snapshot,
            payload=payload,
            recommendations=recommendation_payload,
            combined_tier=combined_tier_payload,
            publish_snapshot=not reanalyze_only,
            mix=mix_spec,
        )
        blob_store.delete(staging_path)
        _pulse_job_lease(lease_heartbeat)
        _stop_job_lease(lease_heartbeat)
        lease_heartbeat = None
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
        if job_store.active_job_id() == job_id:
            job_store.set_active_job_id(None)
        return completed
    except Exception as exc:
        if lease_heartbeat is not None:
            try:
                _stop_job_lease(lease_heartbeat)
            except Exception as heartbeat_error:
                exc = heartbeat_error
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
