from __future__ import annotations

from worker.bootstrap import register_worker_boot

register_worker_boot("analysis-worker")

from worker import tasks
from worker.celery import app

__all__ = ["app", "tasks"]
