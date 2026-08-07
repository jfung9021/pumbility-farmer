from __future__ import annotations

from typing import Any

from analysis_runtime import execute_analysis_job
from worker.celery import QUEUE_NAME, app


@app.task(queue=QUEUE_NAME, name="worker.tasks.refresh_analysis")
def refresh_analysis(job_id: str) -> dict[str, Any]:
    return execute_analysis_job(job_id)
