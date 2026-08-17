from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from time import perf_counter

from analysis_runtime import (
    ANALYSIS_CONTINUATION_FIELD,
    ANALYSIS_CONTINUATION_SEQUENCE_FIELD,
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
    from scripts.backfill_pumbility_production import _assert_database_target
    from topology_diagnostics import require_runtime_database_url

    database_url = require_runtime_database_url(os.environ)
    effect_path = f"{marker_path}.effect"
    with psycopg.connect(database_url, prepare_threshold=None) as connection:
        with connection.transaction(), connection.cursor() as cursor:
            _assert_schema(cursor)
            _assert_database_target(cursor)
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


def _begin_topology_worker_invocation(topic: str) -> Any | None:
    """Claim a real process-first invocation only in an enabled Preview topology."""
    import os

    from topology_diagnostics import diagnostic_enabled
    from worker.bootstrap import claim_first_worker_invocation

    if not diagnostic_enabled(os.environ):
        return None
    return claim_first_worker_invocation(_topology_worker_component(topic))


def _complete_topology_worker_invocation(
    claim: Any | None, *, label: str | None = None
) -> None:
    if claim is None:
        return
    import os

    from analysis_runtime import VercelPrivateBlobStore
    from topology_diagnostics import (
        cold_marker_path,
        emit_cold_start,
        require_diagnostic_environment,
    )

    if label is None:
        label, _connection_limit = require_diagnostic_environment(os.environ)

    duration_ms = round((perf_counter() - float(claim.started_at)) * 1000, 3)
    VercelPrivateBlobStore().put_json(
        cold_marker_path(label, str(claim.component), str(claim.identity_sha256)),
        {
            "schemaVersion": 1,
            "component": str(claim.component),
            "durationMs": duration_ms,
            "success": True,
            "cold": True,
        },
    )
    emit_cold_start(
        label=label,
        component=str(claim.component),
        duration_ms=duration_ms,
    )


def _complete_topology_worker_invocation_best_effort(claim: Any | None) -> None:
    """Never let optional diagnostic timing change an ordinary worker outcome."""
    try:
        _complete_topology_worker_invocation(claim)
    except Exception:
        # No marker means the aggregate remains below 30 and fails closed.
        pass


def _emit_topology_worker_event(*, label: str, topic: str, outcome: str) -> None:
    from topology_diagnostics import emit_event

    emit_event(
        {
            "kind": "worker",
            "label": label,
            "component": (
                "analysis" if topic == QUEUE_NAME else "player-recommendations"
            ),
            "outcome": outcome,
            "count": 1,
            "isolatedDiagnostic": True,
        }
    )


def PiuScoresClient(*args: Any, **kwargs: Any) -> Any:  # noqa: N802 - test seam
    from piu_misgrade_analyzer import PiuScoresClient as implementation

    return implementation(*args, **kwargs)


def refresh_one_player(*args: Any, **kwargs: Any) -> Any:
    from recommendation_refresh import refresh_player_recommendations as implementation

    return implementation(*args, **kwargs)


@app.task(queue=QUEUE_NAME, name="worker.tasks.refresh_analysis")
def refresh_analysis(job_id: str) -> dict[str, Any]:
    cold_claim = _begin_topology_worker_invocation(QUEUE_NAME)
    result = execute_analysis_job(job_id, yield_after_typed_checkpoint=True)
    continuation = result.pop(ANALYSIS_CONTINUATION_FIELD, None)
    sequence = result.pop(ANALYSIS_CONTINUATION_SEQUENCE_FIELD, None)
    if continuation in {
        "combined",
        "model-prepare",
        "model-fit-singles",
        "model-fit-doubles",
        "model-assemble-overall",
        "model",
        "snapshot",
        "database-analysis",
        "database-model",
        "publish",
    }:
        refresh_analysis.apply_async(
            args=[job_id],
            task_id=(
                f"{job_id}-{continuation}"
                f"{'-' + str(sequence) if sequence is not None else ''}"
                "-checkpoint-"
                f"v{TYPED_CHECKPOINT_SCHEMA_VERSION}"
            ),
            queue=QUEUE_NAME,
        )
    _complete_topology_worker_invocation_best_effort(cold_claim)
    return result


@app.task(
    queue=PLAYER_QUEUE_NAME,
    name="worker.tasks.refresh_player_recommendations",
)
def refresh_player_recommendations(job_id: str) -> dict[str, Any]:
    from piu_misgrade_analyzer import ApiError

    cold_claim = _begin_topology_worker_invocation(PLAYER_QUEUE_NAME)
    jobs = RuntimeJobStore()
    job = jobs.get(job_id)
    if job is None:
        raise RuntimeError("The queued player refresh status was not found.")
    if job.get("status") == "completed":
        _complete_topology_worker_invocation_best_effort(cold_claim)
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
        _complete_topology_worker_invocation_best_effort(cold_claim)
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


def _run_topology_queue_probe(
    label: str,
    topic: str,
    identity_sha256: str,
    force_redelivery: bool = False,
) -> dict[str, Any]:
    """Produce one isolated, idempotent queue effect for hosted qualification."""
    import os

    from topology_diagnostics import (
        emit_event,
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
        # The hosted Vercel Celery adapter acknowledges ``Reject(requeue=True)``
        # instead of returning the lease to the queue. Terminating before the
        # acknowledgement exercises the platform's real lease-redelivery path.
        _terminate_topology_worker_process(76)
        raise RuntimeError("The topology queue termination returned unexpectedly.")

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
    _emit_topology_worker_event(
        label=label, topic=normalized_topic, outcome="succeeded"
    )
    return {"status": "completed", "effectCreated": effect_created}


@app.task(bind=True, name="worker.tasks.topology_queue_probe")
def topology_queue_probe(
    self: Any,
    label: str,
    topic: str,
    identity_sha256: str,
    force_redelivery: bool = False,
) -> dict[str, Any]:
    del self
    cold_claim = _begin_topology_worker_invocation(topic)
    try:
        result = _run_topology_queue_probe(
            label, topic, identity_sha256, force_redelivery
        )
        _complete_topology_worker_invocation(cold_claim, label=label)
        return result
    except Exception:
        try:
            import os

            from topology_diagnostics import (
                require_diagnostic_environment,
                require_topic,
            )

            expected_label, _connection_limit = require_diagnostic_environment(os.environ)
            if label == expected_label:
                _emit_topology_worker_event(
                    label=label, topic=require_topic(topic), outcome="failed"
                )
        except Exception:
            pass
        raise RuntimeError("The hosted topology queue diagnostic failed.") from None


@app.task(bind=True, name="worker.tasks.topology_capacity_probe")
def topology_capacity_probe(
    self: Any,
    label: str,
    connection_limit: int,
) -> dict[str, Any]:
    """Measure dedicated-role database usage under real queue concurrency."""
    del self
    import os

    from pumbility_store import _assert_schema, _read_connect
    from scripts.backfill_pumbility_production import _assert_database_target
    from topology_diagnostics import (
        emit_event,
        require_diagnostic_environment,
        require_runtime_database_url,
    )

    expected_label, expected_limit = require_diagnostic_environment(os.environ)
    if label != expected_label or connection_limit != expected_limit:
        raise RuntimeError("The hosted topology capacity diagnostic failed.") from None
    cold_claim = _begin_topology_worker_invocation(QUEUE_NAME)
    try:
        database_url = require_runtime_database_url(os.environ)
        with _read_connect(database_url) as connection:
            with connection.transaction(), connection.cursor() as cursor:
                _assert_schema(cursor)
                _assert_database_target(cursor)
                cursor.execute("set local statement_timeout = '10s'")
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
        _complete_topology_worker_invocation(cold_claim, label=label)
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
        _emit_topology_worker_event(label=label, topic=QUEUE_NAME, outcome="failed")
        raise RuntimeError("The hosted topology capacity diagnostic failed.") from None


def _terminate_topology_worker_process(exit_code: int) -> None:
    """Terminate before queue acknowledgement to prove genuine crash redelivery."""
    import os

    os._exit(exit_code)


@app.task(bind=True, name="worker.tasks.topology_action_probe")
def topology_action_probe(
    self: Any,
    label: str,
    topic: str,
    identity_sha256: str,
    action: str,
) -> dict[str, Any]:
    """Execute one default-off diagnostic inside the selected worker topology."""
    del self
    import os

    from analysis_runtime import VercelPrivateBlobStore
    from topology_diagnostics import (
        SHA256_RE,
        action_result_path,
        require_action,
        require_diagnostic_environment,
        require_topic,
    )

    cold_claim = _begin_topology_worker_invocation(topic)
    try:
        expected_label, _connection_limit = require_diagnostic_environment(os.environ)
        normalized_topic = require_topic(topic)
        normalized_action = require_action(action)
        digest = str(identity_sha256 or "").strip().casefold()
        if label != expected_label or not SHA256_RE.fullmatch(digest):
            raise RuntimeError("The topology action scope is invalid.")
        result_path = action_result_path(label, digest)
        store = VercelPrivateBlobStore()
        prior = store.get_json(result_path) or {}
        if prior.get("status") == "passed":
            return {"status": "completed", "effectCreated": False}

        if normalized_action == "worker-crash":
            if prior.get("status") != "crash-injected":
                store.put_json(
                    result_path,
                    {
                        "schemaVersion": 1,
                        "action": normalized_action,
                        "status": "crash-injected",
                        "attempts": 1,
                    },
                )
                _terminate_topology_worker_process(75)
                raise RuntimeError("The topology worker termination returned unexpectedly.")
            effect_created = _create_topology_effect_once(result_path)
            result: dict[str, Any] = {
                "schemaVersion": 1,
                "action": normalized_action,
                "status": "passed",
                "attempts": 2,
                "crashObserved": True,
                "redeliveryRecovered": True,
                "exactlyOnceEffect": True,
            }
        else:
            from api.topology import (
                _run_private_blob_benchmark,
                _run_private_blob_mutation,
                _run_timeout_faults,
            )

            runners = {
                "blob-read": _run_private_blob_benchmark,
                "blob-mutation": _run_private_blob_mutation,
                "timeout-faults": _run_timeout_faults,
            }
            result = dict(runners[normalized_action]())
            effect_created = _create_topology_effect_once(result_path)
            result = {
                "schemaVersion": 1,
                "action": normalized_action,
                "status": "passed",
                "result": result,
                "exactlyOnceEffect": True,
            }

        store.put_json(result_path, result)
        _emit_topology_worker_event(
            label=label, topic=normalized_topic, outcome="succeeded"
        )
        _complete_topology_worker_invocation(cold_claim, label=label)
        return {"status": "completed", "effectCreated": effect_created}
    except BaseException as error:
        # A real os._exit() never returns and cannot be caught. Tests substitute a
        # BaseException sentinel to verify that the crash branch does not sanitize
        # away the intentional process termination.
        if not isinstance(error, Exception):
            raise
        try:
            normalized_topic = require_topic(topic)
            normalized_action = require_action(action)
            digest = str(identity_sha256 or "").strip().casefold()
            if label == expected_label and SHA256_RE.fullmatch(digest):
                VercelPrivateBlobStore().put_json(
                    action_result_path(label, digest),
                    {
                        "schemaVersion": 1,
                        "action": normalized_action,
                        "status": "failed",
                        "error": "diagnostic-failed",
                    },
                )
                _emit_topology_worker_event(
                    label=label, topic=normalized_topic, outcome="failed"
                )
        except Exception:
            pass
        raise RuntimeError("The hosted topology action diagnostic failed.") from None
