from __future__ import annotations

import os


QUEUE_NAME = os.getenv("CELERY_QUEUE_NAME", "analysis")
PLAYER_QUEUE_NAME = os.getenv(
    "CELERY_PLAYER_QUEUE_NAME", "player-recommendations"
)
