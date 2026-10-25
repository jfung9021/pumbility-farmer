"""Invocation work deadline; leave time for checkpoint persistence and handoff."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import time


ANALYSIS_WORK_SECONDS = 600.0
ANALYSIS_CHECKPOINT_RESERVE_SECONDS = 150.0
ANALYSIS_IO_SECONDS = ANALYSIS_WORK_SECONDS + ANALYSIS_CHECKPOINT_RESERVE_SECONDS
_deadline: ContextVar[float | None] = ContextVar("analysis_work_deadline", default=None)
_io_deadline: ContextVar[float | None] = ContextVar("analysis_io_deadline", default=None)


@contextmanager
def invocation_budget(work_seconds: float = ANALYSIS_WORK_SECONDS):
    started = time.monotonic()
    work_deadline = started + work_seconds
    io_deadline = started + min(
        work_seconds + ANALYSIS_CHECKPOINT_RESERVE_SECONDS, ANALYSIS_IO_SECONDS
    )
    outer_work = _deadline.get()
    outer_io = _io_deadline.get()
    if outer_work is not None:
        work_deadline = min(work_deadline, outer_work)
    if outer_io is not None:
        io_deadline = min(io_deadline, outer_io)
    token = _deadline.set(work_deadline)
    io_token = _io_deadline.set(io_deadline)
    try:
        yield
    finally:
        _io_deadline.reset(io_token)
        _deadline.reset(token)


def work_budget_remaining() -> float | None:
    deadline = _deadline.get()
    return None if deadline is None else deadline - time.monotonic()


def work_budget_available() -> bool:
    remaining = work_budget_remaining()
    return remaining is None or remaining > 0


def io_budget_remaining() -> float | None:
    """Keep checkpoint/recovery I/O bounded within the callback's reserved time."""
    deadline = _io_deadline.get()
    return None if deadline is None else deadline - time.monotonic()
