"""Dedicated Celery subscriber entrypoint for interactive player refreshes."""

from __future__ import annotations

from worker import tasks
from worker.celery import app

__all__ = ["app", "tasks"]
