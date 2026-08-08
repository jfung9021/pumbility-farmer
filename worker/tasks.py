from __future__ import annotations

from typing import Any

from time import perf_counter

from analysis_runtime import (
    PrivateBlobStore,
    RuntimeJobStore,
    execute_analysis_job,
    safe_error,
    update_job,
)
from piu_misgrade_analyzer import ApiError, PiuScoresClient
from piu_recommendations import recommendation_blob_path
from recommendation_refresh import refresh_player_recommendations as refresh_one_player
from worker.celery import PLAYER_QUEUE_NAME, QUEUE_NAME, app


@app.task(queue=QUEUE_NAME, name="worker.tasks.refresh_analysis")
def refresh_analysis(job_id: str) -> dict[str, Any]:
    return execute_analysis_job(job_id)


@app.task(
    queue=PLAYER_QUEUE_NAME,
    name="worker.tasks.refresh_player_recommendations",
)
def refresh_player_recommendations(job_id: str) -> dict[str, Any]:
    jobs = RuntimeJobStore()
    job = jobs.get(job_id)
    if job is None:
        raise RuntimeError("The queued player refresh status was not found.")
    if job.get("status") == "completed":
        return job
    started = perf_counter()
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
        response = refresh_one_player(
            PrivateBlobStore(),
            PiuScoresClient(api_key=api_key),
            index_path=recommendation_blob_path(),
            player_key=str(job.get("playerKey") or ""),
        )
        return update_job(
            jobs,
            job_id,
            status="completed",
            stage="publishing",
            generatedAtUtc=response.get("generatedAtUtc"),
            modelGeneratedAtUtc=response.get("modelGeneratedAtUtc"),
            playerSyncedAtUtc=response.get("playerSyncedAtUtc"),
            durationMs=round((perf_counter() - started) * 1000, 3),
            retryAllowedAtUtc=None,
            error=None,
            progress={
                "current": 1,
                "total": 1,
                "percent": 100,
                "message": "Player recommendations refreshed.",
            },
        )
    except Exception as exc:
        return update_job(
            jobs,
            job_id,
            status="failed",
            stage="syncing",
            durationMs=round((perf_counter() - started) * 1000, 3),
            error=safe_error(exc),
            progress={
                "current": 0,
                "total": 1,
                "percent": 0,
                "message": "The player refresh failed; cached recommendations remain available.",
            },
        )
