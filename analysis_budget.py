"""Invocation work deadline; leave time for checkpoint persistence and handoff."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import time


ANALYSIS_WORK_SECONDS = 600.0
_deadline: ContextVar[float | None] = ContextVar("analysis_work_deadline", default=None)


@contextmanager
def invocation_budget(work_seconds: float = ANALYSIS_WORK_SECONDS):
    token = _deadline.set(time.monotonic() + work_seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


def work_budget_remaining() -> float | None:
    deadline = _deadline.get()
    return None if deadline is None else deadline - time.monotonic()


def work_budget_available() -> bool:
    remaining = work_budget_remaining()
    return remaining is None or remaining > 0
