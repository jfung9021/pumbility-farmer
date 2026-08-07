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


def start_or_reuse_analysis() -> tuple[int, dict[str, Any]]:
    return request_refresh(
        PrivateBlobStore(),
        RuntimeJobStore(),
        enqueue_analysis,
    )
