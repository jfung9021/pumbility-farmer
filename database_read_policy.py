"""Finite worker-only deadlines and retries for idempotent database reads."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import logging
from typing import Callable, Iterator, ParamSpec, TypeVar

import psycopg

from analysis_budget import work_budget_remaining


BACKGROUND_READ_TIMEOUT_MILLISECONDS = 30_000
BACKGROUND_READ_ATTEMPTS = 2
_background_reads: ContextVar[bool] = ContextVar("background_database_reads", default=False)
_LOGGER = logging.getLogger("pumbility.database")
_P = ParamSpec("_P")
_T = TypeVar("_T")


@contextmanager
def background_database_reads() -> Iterator[None]:
    """Opt one worker invocation into the background policy, then restore it."""
    token = _background_reads.set(True)
    try:
        yield
    finally:
        _background_reads.reset(token)


def is_statement_timeout_error(error: BaseException) -> bool:
    """Exclude explicit query cancellation and errors merely mentioning a timeout."""
    if not isinstance(error, psycopg.errors.QueryCanceled) or error.sqlstate != "57014":
        return False
    primary = error.diag.message_primary
    message = primary if primary is not None else str(error).partition("\n")[0]
    return message.strip().casefold() == "canceling statement due to statement timeout"


def read_statement_timeout_milliseconds(default: int | None) -> int | None:
    if not _background_reads.get():
        return default
    remaining = work_budget_remaining()
    limit = BACKGROUND_READ_TIMEOUT_MILLISECONDS
    if remaining is not None:
        # PostgreSQL treats zero as an unlimited deadline.
        limit = max(1, min(limit, int(remaining * 1000)))
    return limit


def retry_background_read(operation: str) -> Callable[[Callable[_P, _T]], Callable[_P, _T]]:
    """Replay a pure read only after its failed transaction has unwound."""
    def decorate(read: Callable[_P, _T]) -> Callable[_P, _T]:
        @wraps(read)
        def run(*args: _P.args, **kwargs: _P.kwargs) -> _T:
            attempts = BACKGROUND_READ_ATTEMPTS if _background_reads.get() else 1
            for attempt in range(1, attempts + 1):
                try:
                    return read(*args, **kwargs)
                except Exception as error:
                    if not is_statement_timeout_error(error):
                        raise
                    remaining = work_budget_remaining()
                    retrying = attempt < attempts and (remaining is None or remaining > 0)
                    _LOGGER.warning(json.dumps({
                        "event": "analysis_database_read_timeout",
                        "operation": operation,
                        "category": "statement-timeout",
                        "sqlstate": "57014",
                        "attempt": attempt,
                        "maxAttempts": attempts,
                        "retrying": retrying,
                    }, separators=(",", ":"), sort_keys=True))
                    if not retrying:
                        raise
            raise AssertionError("The database read attempt limit is invalid.")
        return run
    return decorate
