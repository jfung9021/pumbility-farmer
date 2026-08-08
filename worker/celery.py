from __future__ import annotations

import os

from celery import Celery


QUEUE_NAME = os.getenv("CELERY_QUEUE_NAME", "analysis")
PLAYER_QUEUE_NAME = os.getenv(
    "CELERY_PLAYER_QUEUE_NAME", "player-recommendations"
)

app = Celery(
    "pumbility-analysis-worker",
    broker=os.getenv("CELERY_BROKER_URL", "vercel://"),
)
app.conf.update(
    accept_content=["json"],
    broker_transport_options={
        "use_task_id_as_idempotency_key": True,
        "retention": 24 * 60 * 60,
        "lease_duration": 800,
    },
    result_backend=None,
    result_serializer="json",
    task_acks_late=True,
    task_acks_on_failure_or_timeout=False,
    task_default_queue=QUEUE_NAME,
    task_ignore_result=True,
    task_reject_on_worker_lost=True,
    task_serializer="json",
)

if os.getenv("CELERY_TASK_ALWAYS_EAGER", "").strip().lower() in {"1", "true", "yes"}:
    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
