from __future__ import annotations

from typing import Any

from analysis_runtime import PrivateBlobStore, RuntimeJobStore, request_refresh
from worker.celery import QUEUE_NAME
from worker.tasks import refresh_analysis


def enqueue_analysis(job_id: str) -> None:
    refresh_analysis.apply_async(
        args=[job_id],
        task_id=job_id,
        queue=QUEUE_NAME,
    )


def start_or_reuse_analysis(
    *,
    force_refresh: bool = False,
    deterministic_job_id: str | None = None,
    full_sync: bool = False,
    trigger: str = "manual",
) -> tuple[int, dict[str, Any]]:
    return request_refresh(
        PrivateBlobStore(),
        RuntimeJobStore(),
        enqueue_analysis,
        force_refresh=force_refresh,
        deterministic_job_id=deterministic_job_id,
        full_sync=full_sync,
        trigger=trigger,
    )
