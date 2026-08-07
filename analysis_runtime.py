"""Durable analysis job coordination, status caching, and private Blob storage."""

from __future__ import annotations

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
    synchronize_phoenix2_snapshot,
    utc_now,
)
from piu_misgrade_analyzer import (
    AnalysisConfig,
    ApiError,
    PiuScoresClient,
    analyze_snapshot,
    build_web_payload,
    load_snapshot,
)


LATEST_BLOB_PATH = "analysis/latest.json"
CURRENT_SNAPSHOT_PATH = "analysis/private/phoenix2-current.json"
RUNS_PREFIX = "analysis/runs/"
STAGING_PREFIX = "analysis/staging/"
JOB_TTL_SECONDS = 24 * 60 * 60
FRESHNESS = timedelta(hours=1)
FAILED_RETRY_DELAY = timedelta(minutes=5)
STAGING_MAX_AGE = timedelta(hours=24)
RUN_RETENTION = 10


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
    def delete(self, pathnames: str | Sequence[str]) -> None: ...
    def list(self, prefix: str) -> list[BlobObject]: ...


class PrivateBlobStore:
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

    def delete(self, pathnames: str | Sequence[str]) -> None:
        from vercel.blob import BlobClient

        with BlobClient(token=self.token) as client:
            client.delete(pathnames)

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


class MemoryBlobStore:
    """Thread-safe test/local adapter with the same private JSON semantics."""

    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}
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

    def delete(self, pathnames: str | Sequence[str]) -> None:
        targets = [pathnames] if isinstance(pathnames, str) else list(pathnames)
        with self._lock:
            for pathname in targets:
                self.values.pop(pathname, None)
                self.uploaded.pop(pathname, None)

    def list(self, prefix: str) -> list[BlobObject]:
        with self._lock:
            return [
                BlobObject(pathname, self.uploaded.get(pathname))
                for pathname in sorted(self.values)
                if pathname.startswith(prefix)
            ]


class JobStore(Protocol):
    def get(self, job_id: str) -> dict[str, Any] | None: ...
    def save(self, job: Mapping[str, Any]) -> dict[str, Any]: ...
    def active_job_id(self) -> str | None: ...
    def set_active_job_id(self, job_id: str | None) -> None: ...
    def latest_job_id(self) -> str | None: ...
    def set_latest_job_id(self, job_id: str) -> None: ...


class RuntimeJobStore:
    JOB_KEY = "job:{}"
    ACTIVE_KEY = "active-job"
    LATEST_KEY = "latest-job"

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

    def latest_job_id(self) -> str | None:
        return self._get_id(self.LATEST_KEY)

    def set_latest_job_id(self, job_id: str) -> None:
        self.cache.set(self.LATEST_KEY, job_id, self._options("Latest analysis job"))


class MemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.active: str | None = None
        self.latest: str | None = None
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

    def latest_job_id(self) -> str | None:
        with self._lock:
            return self.latest

    def set_latest_job_id(self, job_id: str) -> None:
        with self._lock:
            self.latest = job_id


def new_job(job_id: str, now: datetime, *, attempt: int = 0) -> dict[str, Any]:
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


def deterministic_hourly_job_id(now: datetime, attempt: int = 0) -> str:
    base = now.astimezone(timezone.utc).strftime("analysis-%Y%m%dT%H")
    return base if attempt <= 0 else f"{base}-r{attempt}"


def _fresh_result(payload: Mapping[str, Any] | None, now: datetime) -> tuple[str, str] | None:
    generated = parse_utc(payload.get("generatedAtUtc")) if payload else None
    if generated is None:
        return None
    next_allowed = generated + FRESHNESS
    if now < next_allowed:
        return isoformat_utc(generated), isoformat_utc(next_allowed)
    return None


Enqueue = Callable[[str], None]


def request_refresh(
    blobs: JsonBlobStore,
    jobs: JobStore,
    enqueue: Enqueue,
    *,
    now: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    """Apply freshness, global-active, and failed-retry rules before enqueueing."""
    effective_now = now or utc_now()
    latest_payload = blobs.get_json(LATEST_BLOB_PATH)
    if fresh := _fresh_result(latest_payload, effective_now):
        generated_at, next_allowed_at = fresh
        return 200, {
            "outcome": "fresh",
            "generatedAtUtc": generated_at,
            "nextAllowedAtUtc": next_allowed_at,
        }

    active_id = jobs.active_job_id()
    active = jobs.get(active_id) if active_id else None
    if active and active.get("status") in {"queued", "running"}:
        return 202, {"outcome": "existing", "job": active}
    if active_id:
        jobs.set_active_job_id(None)

    latest_job_id = jobs.latest_job_id()
    previous = jobs.get(latest_job_id) if latest_job_id else None
    if previous and previous.get("status") == "failed":
        retry_at = parse_utc(previous.get("retryAllowedAtUtc"))
        if retry_at is not None and effective_now < retry_at:
            return 202, {"outcome": "existing", "job": previous}

    attempt = 0
    if previous:
        previous_base = deterministic_hourly_job_id(effective_now)
        if str(previous.get("id", "")).startswith(previous_base):
            attempt = int(previous.get("attempt") or 0) + 1
    job_id = deterministic_hourly_job_id(effective_now, attempt)
    while jobs.get(job_id) is not None:
        attempt += 1
        job_id = deterministic_hourly_job_id(effective_now, attempt)
    job = new_job(job_id, effective_now, attempt=attempt)
    jobs.save(job)
    jobs.set_latest_job_id(job_id)
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
) -> int:
    effective_now = now or utc_now()
    stale = [
        item.pathname
        for item in blobs.list(STAGING_PREFIX)
        if item.pathname != keep_path
        and item.uploaded_at is not None
        and effective_now - item.uploaded_at > STAGING_MAX_AGE
    ]
    if stale:
        blobs.delete(stale)
    return len(stale)


def _run_path(payload: Mapping[str, Any], job_id: str) -> str:
    generated = parse_utc(payload.get("generatedAtUtc")) or utc_now()
    stamp = generated.strftime("%Y%m%dT%H%M%S%fZ")
    return f"{RUNS_PREFIX}{stamp}-{job_id}.json"


def publish_success(
    blobs: JsonBlobStore,
    *,
    job_id: str,
    snapshot: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> None:
    """Publish immutable aggregate, then promote snapshot/latest and enforce retention."""
    blobs.put_json(_run_path(payload, job_id), payload)
    blobs.put_json(CURRENT_SNAPSHOT_PATH, sanitize_snapshot(snapshot))
    blobs.put_json(LATEST_BLOB_PATH, payload)
    runs = sorted(blobs.list(RUNS_PREFIX), key=lambda item: item.pathname, reverse=True)
    stale = [item.pathname for item in runs[RUN_RETENTION:]]
    if stale:
        blobs.delete(stale)


_SECRET_PATTERN = re.compile(r"(?:piu_scores_live_|pst_live_)[0-9a-f]{16,}", re.IGNORECASE)


def safe_error(exc: BaseException) -> str:
    if isinstance(exc, (ApiError, FileNotFoundError, ValueError)):
        message = str(exc).strip() or "The analysis could not be completed."
        return _SECRET_PATTERN.sub("[credential redacted]", message)[:500]
    return "The analysis failed unexpectedly. Please retry after the cooldown."


def _snapshot_from_raw_dir(raw_dir: Path, timestamp: datetime) -> dict[str, Any]:
    players, charts, scores = load_snapshot(raw_dir)
    player_ids = sorted(
        {
            str(row.get("userId"))
            for row in players
            if row.get("userId") is not None and str(row.get("userId")).strip()
        }
    )
    stamp = isoformat_utc(timestamp)
    return sanitize_snapshot(
        {
            "schemaVersion": 1,
            "mix": "Phoenix2",
            "generatedAtUtc": stamp,
            "players": [
                {"playerId": player_id, "lastSyncedAtUtc": stamp}
                for player_id in player_ids
            ],
            "charts": charts,
            "scores": scores,
        }
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
    if existing.get("status") == "completed":
        return existing

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
            "message": "Reading the consented-player list and Phoenix 2 catalog.",
        },
    )
    staging_path = f"{STAGING_PREFIX}{job_id}.json"

    try:
        cleanup_abandoned_staging(blob_store, now=now(), keep_path=staging_path)
        current = blob_store.get_json(CURRENT_SNAPSHOT_PATH)
        resume = blob_store.get_json(staging_path)
        raw_dir_setting = os.getenv("PIU_ANALYSIS_RAW_DIR", "").strip()
        if raw_dir_setting:
            snapshot = _snapshot_from_raw_dir(Path(raw_dir_setting), now())
            staging = {
                "schemaVersion": 1,
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

            snapshot, _ = synchronize_phoenix2_snapshot(
                effective_client,
                current,
                job_id=job_id,
                resume_staging=resume,
                workers=6,
                checkpoint_every=50,
                progress=on_progress,
                checkpoint=lambda value: blob_store.put_json(staging_path, value),
                now=now,
            )

        config = AnalysisConfig(
            bootstrap_samples=int(os.getenv("ANALYSIS_BOOTSTRAP_SAMPLES", "500"))
        )
        players, charts, scores = analyzer_input(
            snapshot,
            minimum_scores_per_mode=config.minimum_scores_per_player,
            eligible_only=True,
        )
        update_job(
            job_store,
            job_id,
            status="running",
            stage="analyzing",
            progress={
                "current": len(players),
                "total": len(players),
                "percent": 100,
                "message": f"Analyzing {len(players):,} eligible players in separate modes.",
            },
        )
        chart_results, _, summary, _ = analyze_snapshot(players, charts, scores, config)
        payload = build_web_payload(chart_results, summary)
        update_job(
            job_store,
            job_id,
            status="running",
            stage="publishing",
            progress={
                "current": 1,
                "total": 1,
                "percent": 100,
                "message": "Publishing the private snapshot and refreshed rankings.",
            },
        )
        publish_success(
            blob_store,
            job_id=job_id,
            snapshot=snapshot,
            payload=payload,
        )
        blob_store.delete(staging_path)
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
        job_store.set_active_job_id(None)
        return completed
    except Exception as exc:
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
            job_store.set_active_job_id(None)
        return failed
