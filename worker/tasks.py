from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from time import perf_counter

from analysis_runtime import (
    PrivateBlobStore,
    RuntimeJobStore,
    execute_analysis_job,
    safe_error,
    update_job,
)
from pumbility_contract import recommendation_blob_path
from phoenix2_sync import isoformat_utc, parse_utc, utc_now
from worker.celery import PLAYER_QUEUE_NAME, QUEUE_NAME, app


def PiuScoresClient(*args: Any, **kwargs: Any) -> Any:  # noqa: N802 - test seam
    from piu_misgrade_analyzer import PiuScoresClient as implementation

    return implementation(*args, **kwargs)


def refresh_one_player(*args: Any, **kwargs: Any) -> Any:
    from recommendation_refresh import refresh_player_recommendations as implementation

    return implementation(*args, **kwargs)


@app.task(queue=QUEUE_NAME, name="worker.tasks.refresh_analysis")
def refresh_analysis(job_id: str) -> dict[str, Any]:
    return execute_analysis_job(job_id)


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
