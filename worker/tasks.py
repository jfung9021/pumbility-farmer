from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from time import perf_counter

from analysis_runtime import (
    ANALYSIS_CONTINUATION_FIELD,
    TYPED_CHECKPOINT_SCHEMA_VERSION,
    PrivateBlobStore,
    RuntimeJobStore,
    execute_analysis_job,
    safe_error,
    update_job,
)
from pumbility_contract import recommendation_blob_path
from phoenix2_sync import isoformat_utc, parse_utc, utc_now
from worker.celery import PLAYER_QUEUE_NAME, QUEUE_NAME, app


def _topology_worker_component(topic: str) -> str:
    return (
        "analysis-worker"
        if topic == QUEUE_NAME
        else "player-recommendations-worker"
    )


def _create_topology_effect_once(marker_path: str) -> bool:
    """Atomically create one isolated durable effect for an at-least-once task."""
    import os

    import psycopg

    from pumbility_store import PumbilityArtifactStore, _assert_schema
    from scripts.reconcile_pumbility_production import session_url_from_runtime

    database_url = session_url_from_runtime(
        os.environ.get("PUMBILITY_DATABASE_URL", "")
    )
    effect_path = f"{marker_path}.effect"
    with psycopg.connect(database_url, prepare_threshold=None) as connection:
        with connection.transaction(), connection.cursor() as cursor:
            _assert_schema(cursor)
            cursor.execute(
                "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (effect_path,),
            )
            cursor.execute(
                "select 1 from pumbility.artifacts where object_key = %s",
                (effect_path,),
            )
            if cursor.fetchone() is not None:
                return False
            PumbilityArtifactStore._put_json_row(
                cursor,
                effect_path,
                {"schemaVersion": 1, "effect": True},
            )
    return True


def PiuScoresClient(*args: Any, **kwargs: Any) -> Any:  # noqa: N802 - test seam
    from piu_misgrade_analyzer import PiuScoresClient as implementation

    return implementation(*args, **kwargs)


def refresh_one_player(*args: Any, **kwargs: Any) -> Any:
    from recommendation_refresh import refresh_player_recommendations as implementation

    return implementation(*args, **kwargs)


@app.task(queue=QUEUE_NAME, name="worker.tasks.refresh_analysis")
def refresh_analysis(job_id: str) -> dict[str, Any]:
    result = execute_analysis_job(job_id, yield_after_typed_checkpoint=True)
    continuation = result.pop(ANALYSIS_CONTINUATION_FIELD, None)
    if continuation in {
        "model",
        "snapshot",
        "database-analysis",
        "database-model",
        "publish",
    }:
        refresh_analysis.apply_async(
            args=[job_id],
            task_id=(
                f"{job_id}-{continuation}-checkpoint-"
                f"v{TYPED_CHECKPOINT_SCHEMA_VERSION}"
            ),
            queue=QUEUE_NAME,
        )
    return result


@app.task(
    queue=PLAYER_QUEUE_NAME,
    name="worker.tasks.refresh_player_recommendations",
)
def refresh_player_recommendations(job_id: str) -> dict[str, Any]:
    from piu_misgrade_analyzer import ApiError

    jobs = RuntimeJobStore()
    job = jobs.get(job_id)
    if job is None:
        raise RuntimeError("The queued player refresh status was not found.")
    if job.get("status") == "completed":
        return job
    started = perf_counter()
    started_at = utc_now()
    created_at = parse_utc(job.get("createdAtUtc"))
    queue_wait_ms = round(
        max(0.0, (started_at - created_at).total_seconds() * 1000), 3
    ) if created_at else None
    update_job(
        jobs,
        job_id,
        status="running",
        stage="syncing",
        progress={
            "current": 0,
            "total": 1,
            "percent": 10,
            "message": "Synchronizing this player's latest Phoenix 2 scores.",
        },
    )
    try:
        import os

        api_key = os.getenv("PIU_SCORES_API_KEY", "").strip()
        if not api_key:
            raise ApiError(
                "PIU_SCORES_API_KEY is not configured as a server-side environment variable."
            )
        timings: dict[str, float] = {}
        response = refresh_one_player(
            PrivateBlobStore(),
            PiuScoresClient(api_key=api_key),
            index_path=recommendation_blob_path(),
            player_key=str(job.get("playerKey") or ""),
            timings=timings,
        )
        completed = update_job(
            jobs,
            job_id,
            status="completed",
            stage="publishing",
            generatedAtUtc=response.get("generatedAtUtc"),
            modelGeneratedAtUtc=response.get("modelGeneratedAtUtc"),
            playerSyncedAtUtc=response.get("playerSyncedAtUtc"),
            durationMs=round((perf_counter() - started) * 1000, 3),
            queueWaitMs=queue_wait_ms,
            phaseDurationsMs=timings,
            retryAllowedAtUtc=None,
            error=None,
            progress={
                "current": 1,
                "total": 1,
                "percent": 100,
                "message": "Player recommendations refreshed.",
            },
        )
        print(json.dumps({
            "event": "player_recommendation_refresh",
            "status": "completed",
            "queueWaitMs": queue_wait_ms,
            "phaseDurationsMs": timings,
            "durationMs": completed.get("durationMs"),
        }, separators=(",", ":"), sort_keys=True))
        return completed
    except Exception as exc:
        failed = update_job(
            jobs,
            job_id,
            status="failed",
            stage="syncing",
            durationMs=round((perf_counter() - started) * 1000, 3),
            queueWaitMs=queue_wait_ms,
            retryAllowedAtUtc=isoformat_utc(utc_now() + timedelta(seconds=60)),
            error=safe_error(exc),
            progress={
                "current": 0,
                "total": 1,
                "percent": 0,
                "message": "The player refresh failed; cached recommendations remain available.",
            },
        )
        print(json.dumps({
            "event": "player_recommendation_refresh",
            "status": "failed",
            "queueWaitMs": queue_wait_ms,
            "durationMs": failed.get("durationMs"),
        }, separators=(",", ":"), sort_keys=True))
        return failed


@app.task(bind=True, name="worker.tasks.topology_queue_probe")
def topology_queue_probe(
    self: Any,
    label: str,
    topic: str,
    identity_sha256: str,
    force_redelivery: bool = False,
) -> dict[str, Any]:
    """Produce one isolated, idempotent queue effect for hosted qualification."""
    import os

    from celery.exceptions import Reject

    from topology_diagnostics import (
        emit_event,
        emit_worker_cold_start_once,
        queue_marker_path,
        require_diagnostic_environment,
        require_topic,
        SHA256_RE,
    )

    from analysis_runtime import VercelPrivateBlobStore

    expected_label, _connection_limit = require_diagnostic_environment(os.environ)
    normalized_topic = require_topic(topic)
    if label != expected_label:
        raise RuntimeError("The diagnostic queue label is invalid for this topology.")
    digest = identity_sha256.strip().casefold()
    if not SHA256_RE.fullmatch(digest):
        raise RuntimeError("The diagnostic queue identity is malformed.")
    marker_path = queue_marker_path(label, normalized_topic, digest)
    store = VercelPrivateBlobStore()
    marker = store.get_json(marker_path) or {}
    raw_attempts = marker.get("attempts")
    attempts = (
        [attempt for attempt in raw_attempts if isinstance(attempt, int) and attempt > 0]
        if isinstance(raw_attempts, list)
        else []
    )
    attempt = max(attempts, default=0) + 1
    attempts.append(attempt)
    emit_event(
        {
            "kind": "queue",
            "label": label,
            "topic": normalized_topic,
            "stage": "consumed",
            "identitySha256": digest,
            "attempt": attempt,
        }
    )
    if force_redelivery and attempt == 1:
        store.put_json(
            marker_path,
            {"schemaVersion": 1, "attempts": attempts, "effect": False},
        )
        raise Reject("Injected diagnostic redelivery.", requeue=True)

    effect_created = _create_topology_effect_once(marker_path)
    if effect_created:
        emit_event(
            {
                "kind": "queue",
                "label": label,
                "topic": normalized_topic,
                "stage": "durable-effect",
                "identitySha256": digest,
                "attempt": attempt,
            }
        )
    store.put_json(
        marker_path,
        {"schemaVersion": 1, "attempts": attempts, "effect": True},
    )
    emit_event(
        {
            "kind": "worker",
            "label": label,
            "component": (
                "analysis"
                if normalized_topic == QUEUE_NAME
                else "player-recommendations"
            ),
            "outcome": "succeeded",
            "count": 1,
            "isolatedDiagnostic": True,
        }
    )
    emit_worker_cold_start_once(
        label=label,
        component=_topology_worker_component(normalized_topic),
    )
    return {"status": "completed", "effectCreated": effect_created}


@app.task(bind=True, name="worker.tasks.topology_capacity_probe")
def topology_capacity_probe(
    self: Any,
    label: str,
    connection_limit: int,
) -> dict[str, Any]:
    """Measure dedicated-role database usage under real queue concurrency."""
    del self
    import os
    import psycopg

    from topology_diagnostics import emit_event, require_diagnostic_environment

    from scripts.reconcile_pumbility_production import session_url_from_runtime

    expected_label, expected_limit = require_diagnostic_environment(os.environ)
    if label != expected_label or connection_limit != expected_limit:
        raise RuntimeError("The diagnostic capacity scope is invalid.")
    try:
        with psycopg.connect(
            session_url_from_runtime(os.environ.get("PUMBILITY_DATABASE_URL", "")),
            prepare_threshold=None,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("select pg_sleep(1)")
                cursor.execute(
                    "select count(*) from pg_stat_activity where usename = current_user"
                )
                row = cursor.fetchone()
                active = int(row[0]) if row else 0
        emit_event(
            {
                "kind": "capacity",
                "label": label,
                "activeConnections": active,
                "connectionLimit": connection_limit,
                "connectionErrors": 0,
                "deadlineErrors": 0,
            }
        )
        return {"status": "completed", "sampled": True}
    except Exception:
        emit_event(
            {
                "kind": "capacity",
                "label": label,
                "activeConnections": 0,
                "connectionLimit": connection_limit,
                "connectionErrors": 1,
                "deadlineErrors": 0,
            }
        )
        raise
