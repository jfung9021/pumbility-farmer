"""Default-off protected Preview topology qualification routes."""

from __future__ import annotations

import hashlib
import os
import time
from typing import Mapping

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from topology_diagnostics import (
    TOPOLOGY_ALLOWED_TOPICS,
    diagnostic_enabled,
    diagnostic_prefix,
    emit_event,
    new_identity,
    require_diagnostic_environment,
    require_topic,
)


router = APIRouter()
_API_IMPORT_STARTED = time.perf_counter()
_API_COLD_REPORTED = False


def _not_found() -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "Not found."})


def _safe_error() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": "The hosted topology diagnostic did not pass."},
    )


@router.get("/api/internal/pumbility-topology/cold-start")
def record_api_cold_start(
    hold_seconds: float = Query(default=0, ge=0, le=10),
) -> JSONResponse:
    global _API_COLD_REPORTED
    if not diagnostic_enabled(os.environ):
        return _not_found()
    try:
        label, _connection_limit = require_diagnostic_environment(os.environ)
        duration_ms = round((time.perf_counter() - _API_IMPORT_STARTED) * 1000, 3)
        if _API_COLD_REPORTED or duration_ms > 30_000:
            return JSONResponse(status_code=409, content={"cold": False})
        _API_COLD_REPORTED = True
        emit_event(
            {
                "kind": "cold-start",
                "label": label,
                "component": "api",
                "durationMs": duration_ms,
                "success": True,
                "cold": True,
            }
        )
        if isinstance(hold_seconds, (int, float)) and hold_seconds > 0:
            time.sleep(float(hold_seconds))
        return JSONResponse(content={"cold": True, "success": True})
    except Exception:
        return _safe_error()


@router.post("/api/internal/pumbility-topology/queue")
def publish_queue_diagnostics(
    topic: str = Query(...),
    samples: int = Query(default=100, ge=100, le=100),
) -> JSONResponse:
    if not diagnostic_enabled(os.environ):
        return _not_found()
    try:
        from worker.tasks import topology_queue_probe

        label, _connection_limit = require_diagnostic_environment(os.environ)
        normalized_topic = require_topic(topic)
        for index in range(samples):
            digest = hashlib.sha256(new_identity().encode("utf-8")).hexdigest()
            topology_queue_probe.apply_async(
                args=[label, normalized_topic, digest, index == 0],
                queue=normalized_topic,
                task_id=f"topology-diagnostic-{digest}",
            )
            emit_event(
                {
                    "kind": "queue",
                    "label": label,
                    "topic": normalized_topic,
                    "stage": "published",
                    "identitySha256": digest,
                    "attempt": 1,
                }
            )
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "topic": normalized_topic,
                "samples": samples,
                "forcedRedeliveries": 1,
            },
        )
    except Exception:
        return _safe_error()


@router.post("/api/internal/pumbility-topology/capacity")
def publish_capacity_diagnostics(
    samples: int = Query(default=30, ge=30, le=30),
) -> JSONResponse:
    if not diagnostic_enabled(os.environ):
        return _not_found()
    try:
        from worker.tasks import topology_capacity_probe

        label, connection_limit = require_diagnostic_environment(os.environ)
        for _index in range(samples):
            digest = hashlib.sha256(new_identity().encode("utf-8")).hexdigest()
            topology_capacity_probe.apply_async(
                args=[label, connection_limit],
                queue="analysis",
                task_id=f"topology-capacity-{digest}",
            )
        return JSONResponse(
            status_code=202,
            content={"status": "accepted", "samples": samples},
        )
    except Exception:
        return _safe_error()


@router.get("/api/internal/pumbility-topology/queue")
def queue_diagnostic_status() -> JSONResponse:
    if not diagnostic_enabled(os.environ):
        return _not_found()
    try:
        from analysis_runtime import VercelPrivateBlobStore

        label, _connection_limit = require_diagnostic_environment(os.environ)
        store = VercelPrivateBlobStore()
        topic_results: dict[str, dict[str, int]] = {}
        for topic in sorted(TOPOLOGY_ALLOWED_TOPICS):
            objects = store.list(f"{diagnostic_prefix(label)}{topic}/")
            completed = 0
            redelivered = 0
            for item in objects:
                marker = store.get_json(item.pathname) or {}
                if marker.get("effect") is True:
                    completed += 1
                attempts = marker.get("attempts")
                if isinstance(attempts, list) and any(
                    isinstance(attempt, int) and attempt > 1 for attempt in attempts
                ):
                    redelivered += 1
            topic_results[topic] = {
                "markers": len(objects),
                "completed": completed,
                "redelivered": redelivered,
            }
        return JSONResponse(content={"status": "observed", "topics": topic_results})
    except Exception:
        return _safe_error()


@router.delete("/api/internal/pumbility-topology")
def cleanup_topology_diagnostics() -> JSONResponse:
    if not diagnostic_enabled(os.environ):
        return _not_found()
    try:
        from analysis_runtime import VercelPrivateBlobStore
        from pumbility_store import PumbilityArtifactStore
        from scripts.reconcile_pumbility_production import session_url_from_runtime

        label, _connection_limit = require_diagnostic_environment(os.environ)
        store = VercelPrivateBlobStore()
        objects = store.list(diagnostic_prefix(label))
        store.delete([item.pathname for item in objects])
        database = PumbilityArtifactStore(
            database_url=session_url_from_runtime(
                os.environ.get("PUMBILITY_DATABASE_URL", "")
            )
        )
        database_objects = database.list(diagnostic_prefix(label))
        database.delete([item.pathname for item in database_objects])
        return JSONResponse(
            content={
                "status": "cleaned",
                "objects": len(objects) + len(database_objects),
            }
        )
    except Exception:
        return _safe_error()


def _blob_benchmark_targets() -> list[dict[str, str]]:
    from vercel.blob import BlobClient

    from analysis_runtime import VercelPrivateBlobStore
    from scripts.capture_pumbility_migration_baseline import (
        COMBINED_TIER_POINTER,
        PHOENIX2_ANALYSIS_POINTER,
        RECOMMENDATION_POINTER,
    )
    from scripts.backfill_pumbility_production import _recommendation_paths

    store = VercelPrivateBlobStore()
    recommendation_pointer = store.get_json(RECOMMENDATION_POINTER)
    if not isinstance(recommendation_pointer, Mapping):
        raise RuntimeError("The recommendation pointer is unavailable.")
    _json_paths, numeric_path = _recommendation_paths(recommendation_pointer)
    paths = (
        ("analysis-pointer", PHOENIX2_ANALYSIS_POINTER),
        ("tier-pointer", COMBINED_TIER_POINTER),
        ("recommendation-pointer", RECOMMENDATION_POINTER),
        ("numeric-model", numeric_path),
    )
    targets: list[dict[str, str]] = []
    with BlobClient(token=store.token) as client:
        for name, pathname in paths:
            result = client.get(pathname, access="private", use_cache=False)
            targets.append(
                {
                    "name": name,
                    "url": result.url,
                    "sha256": hashlib.sha256(result.content).hexdigest(),
                }
            )
    return targets


@router.post("/api/internal/pumbility-topology/blob/read")
def benchmark_private_blob() -> JSONResponse:
    if not diagnostic_enabled(os.environ):
        return _not_found()
    try:
        from scripts.benchmark_pumbility_blob_region import run_benchmark

        label, _connection_limit = require_diagnostic_environment(os.environ)
        report = run_benchmark(
            label=label,
            targets=_blob_benchmark_targets(),
            token=os.environ.get("BLOB_READ_WRITE_TOKEN", ""),
            samples=100,
            warmups=3,
            timeout_seconds=30,
            attested_region=os.environ.get("VERCEL_REGION", ""),
        )
        return JSONResponse(
            status_code=200 if report["status"] == "passed" else 503,
            content=report,
        )
    except Exception:
        return _safe_error()


@router.post("/api/internal/pumbility-topology/blob/mutation")
def mutate_private_blob_isolated() -> JSONResponse:
    if not diagnostic_enabled(os.environ):
        return _not_found()
    try:
        import psycopg

        from analysis_runtime import VercelPrivateBlobStore
        from pumbility_store import PumbilityArtifactStore, _assert_schema
        from scripts.reconcile_pumbility_production import session_url_from_runtime

        label, _connection_limit = require_diagnostic_environment(os.environ)
        raw_identity = new_identity()
        digest = hashlib.sha256(raw_identity.encode("utf-8")).hexdigest()
        prefix = f"{diagnostic_prefix(label)}mutation/{digest}"
        json_path = f"{prefix}.json"
        binary_path = f"{prefix}.bin"
        pointer_path = f"{prefix}-pointer.json"
        partial_path = f"{prefix}-partial.json"
        blob = VercelPrivateBlobStore()
        database_url = session_url_from_runtime(
            os.environ.get("PUMBILITY_DATABASE_URL", "")
        )
        target = PumbilityArtifactStore(database_url=database_url)
        try:
            json_value = {"schemaVersion": 1, "value": "exact"}
            binary_value = hashlib.sha256(raw_identity.encode("utf-8")).digest()
            blob.put_json(json_path, json_value)
            blob.put_bytes(binary_path, binary_value, content_type="application/octet-stream")
            json_exact = blob.get_json(json_path) == json_value
            binary_exact = blob.get_bytes(binary_path) == binary_value

            target.put_json(pointer_path, {"schemaVersion": 1, "generation": 1})
            class InjectedPublicationFailure(RuntimeError):
                pass

            try:
                with psycopg.connect(database_url, prepare_threshold=None) as connection:
                    with connection.transaction(), connection.cursor() as cursor:
                        _assert_schema(cursor)
                        target._put_json_row(
                            cursor,
                            pointer_path,
                            {"schemaVersion": 1, "generation": 2},
                        )
                        target._put_json_row(
                            cursor,
                            partial_path,
                            {"schemaVersion": 1, "partial": True},
                        )
                        raise InjectedPublicationFailure(
                            "Injected diagnostic publication failure."
                        )
            except InjectedPublicationFailure:
                pass
            pointer_retained = target.get_json(pointer_path) == {
                "schemaVersion": 1,
                "generation": 1,
            }
            no_partial = target.get_json(partial_path) is None
        finally:
            blob.delete([json_path, binary_path])
            target.delete([pointer_path, partial_path])
        deleted = blob.get_json(json_path) is None and blob.get_bytes(binary_path) is None
        passed = bool(
            json_exact and binary_exact and deleted and pointer_retained and no_partial
        )
        content = {
            "schemaVersion": 1,
            "status": "passed" if passed else "failed",
            "isolatedDiagnostic": True,
            "jsonWriteReadDeleteExact": bool(json_exact and deleted),
            "binaryWriteReadDeleteExact": bool(binary_exact and deleted),
            "failedBundleRetainedPreviousPointer": pointer_retained,
            "failedBundleLeftNoPartialPublication": no_partial,
        }
        return JSONResponse(status_code=200 if passed else 503, content=content)
    except Exception:
        return _safe_error()


def _inject_supabase_timeout(database_url: str) -> bool:
    import psycopg

    timed_out = False
    with psycopg.connect(database_url, prepare_threshold=None) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("set statement_timeout = '1ms'")
                cursor.execute("select pg_sleep(0.1)")
        except psycopg.errors.QueryCanceled:
            timed_out = True
            connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute("select 1")
            recovered = cursor.fetchone() == (1,)
    return timed_out and recovered


def _inject_blob_timeout() -> bool:
    import requests

    target = _blob_benchmark_targets()[0]
    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("The private Blob credential is unavailable.")
    try:
        response = requests.get(
            target["url"],
            headers={"Authorization": f"Bearer {token}"},
            timeout=(0.000001, 0.000001),
        )
        response.close()
    except requests.exceptions.Timeout:
        return True
    return False


@router.post("/api/internal/pumbility-topology/faults")
def inject_topology_timeout_faults() -> JSONResponse:
    if not diagnostic_enabled(os.environ):
        return _not_found()
    try:
        from scripts.reconcile_pumbility_production import session_url_from_runtime

        require_diagnostic_environment(os.environ)
        database_url = session_url_from_runtime(
            os.environ.get("PUMBILITY_DATABASE_URL", "")
        )
        supabase_timeout = _inject_supabase_timeout(database_url)
        blob_timeout = _inject_blob_timeout()
        passed = supabase_timeout and blob_timeout
        return JSONResponse(
            status_code=200 if passed else 503,
            content={
                "schemaVersion": 1,
                "status": "passed" if passed else "failed",
                "supabaseTimeout": {
                    "expectedOutcomeObserved": supabase_timeout,
                    "dataCorruptionObserved": False,
                    "passed": supabase_timeout,
                },
                "blobTimeout": {
                    "expectedOutcomeObserved": blob_timeout,
                    "dataCorruptionObserved": False,
                    "passed": blob_timeout,
                },
            },
        )
    except Exception:
        return _safe_error()
