"""Connection reuse and bounded retries for repeatable private binary storage I/O.

Credentials are supplied per request, never kept on shared session defaults.
Each calling thread owns its session; all pools close when the transport is
released. A retry never changes the method, object key, headers, or body.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import errno
import random
import ssl
import threading
import time
from typing import Any, Iterator
import weakref

import requests

import tier_diagnostics

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_RESET_ERRNOS = frozenset({errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE, errno.ETIMEDOUT})
_MAX_ATTEMPTS = 3


def _exception_parts(error: BaseException) -> Iterator[Any]:
    """Inspect wrapped urllib3/ssl errors without logging their private text."""
    pending: list[Any] = [error]
    seen: set[int] = set()
    while pending and len(seen) < 32:
        part = pending.pop()
        if id(part) in seen:
            continue
        seen.add(id(part))
        yield part
        if isinstance(part, BaseException):
            pending.extend(part.args)
            pending.extend((part.__cause__, part.__context__, getattr(part, "reason", None)))


def classify_tls_error(error: BaseException) -> str:
    parts = tuple(_exception_parts(error))
    if any(isinstance(part, ssl.SSLCertVerificationError) for part in parts):
        return "certificate"
    text = " ".join(part for part in parts if isinstance(part, str)).upper()
    if any(marker in text for marker in (
        "CERTIFICATE_VERIFY_FAILED", "CERTIFICATE VERIFY FAILED", "HOSTNAME MISMATCH",
    )):
        return "certificate"
    if any(isinstance(part, ssl.SSLEOFError) for part in parts) or "UNEXPECTED_EOF_WHILE_READING" in text:
        return "eof"
    if any(isinstance(part, (ssl.SSLError, requests.exceptions.SSLError)) for part in parts):
        return "ssl_other"
    return "none"


def _retryable_error(error: requests.exceptions.RequestException) -> bool:
    tls = classify_tls_error(error)
    if tls == "certificate":
        return False
    if tls == "eof" or isinstance(error, requests.exceptions.Timeout):
        return True
    # Only known resets are retried; generic connection and SSL errors may be
    # permanent configuration/certificate problems and must remain visible.
    return any(
        isinstance(part, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError))
        or isinstance(part, OSError) and part.errno in _RESET_ERRNOS
        for part in _exception_parts(error)
    )


def _retry_delay(response: requests.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except (ValueError, TypeError):
                try:
                    when = parsedate_to_datetime(retry_after)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
    return 0.1 * 2 ** (attempt - 1) + random.uniform(0.0, 0.1)


def _close_sessions(sessions: list[requests.Session]) -> None:
    for session in sessions:
        session.close()


class PrivateStorageTransport:
    def __init__(self) -> None:
        self._local = threading.local()
        self._sessions: list[requests.Session] = []
        self._lock = threading.Lock()
        self._finalizer = weakref.finalize(self, _close_sessions, self._sessions)

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            with self._lock:
                self._sessions.append(session)
            self._local.session = session
        return session

    def request(self, method: str, url: str, *, timeout: float, **kwargs: Any) -> requests.Response:
        if method not in {"GET", "POST"}:
            raise ValueError("Private binary transport supports only GET and repeatable POST.")
        if method == "POST" and (
            kwargs.get("headers", {}).get("x-upsert") != "true"
            or not isinstance(kwargs.get("data"), bytes)
        ):
            raise ValueError("Retried private uploads require an upsert and immutable bytes.")
        if timeout <= 0:
            raise ValueError("Private binary request timeout must be positive.")
        started = time.monotonic()
        recovered_error: requests.exceptions.RequestException | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            response: requests.Response | None = None
            error: requests.exceptions.RequestException | None = None
            remaining = max(0.001, timeout - (time.monotonic() - started))
            try:
                response = self._session().request(method, url, timeout=remaining, **kwargs)
            except requests.exceptions.RequestException as caught:
                error = caught
            retryable = _retryable_error(error) if error is not None else response.status_code in _RETRY_STATUSES
            delay = _retry_delay(response, attempt) if retryable and attempt < _MAX_ATTEMPTS else 0.0
            retry = retryable and attempt < _MAX_ATTEMPTS and time.monotonic() - started + delay < timeout
            if retry:
                if error is not None:
                    recovered_error = error
                if response is not None:
                    response.close()
                time.sleep(delay)
                # Scheduling or backoff can consume the remaining budget.
                # Preserve the last failure instead of starting another socket.
                if time.monotonic() - started < timeout:
                    continue
            failed = error is not None or response.status_code >= 400
            if failed or attempt > 1:
                fields: dict[str, Any] = {
                    "stage": "storage_" + method.lower(), "outcome": "failed" if failed else "success",
                    "attempt": attempt, "duration_ms": round((time.monotonic() - started) * 1000, 3),
                }
                diagnostic_error = error if failed else recovered_error
                if diagnostic_error is not None:
                    fields.update(exception_class=type(diagnostic_error).__name__, tls_category=classify_tls_error(diagnostic_error))
                if response is not None:
                    fields["status_code"] = response.status_code
                tier_diagnostics.event("private_storage_request", **fields)
            if error is not None:
                raise error
            return response
        raise AssertionError("Unreachable private storage retry state.")
