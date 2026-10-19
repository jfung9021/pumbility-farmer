"""Small, privacy-preserving diagnostics for tier publication.

Exception messages and source lines are intentionally excluded: database and
HTTP exceptions can contain credentials or submitted player records.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import time
import traceback
from typing import Any, Iterator

LOGGER = logging.getLogger("tier_publication")
_FIELDS = frozenset({
    "request_id", "job_id", "generation", "stage", "sequence", "outcome",
    "duration_ms", "artifact_bytes", "schema_version", "exception_class",
    "source_hash", "output_hash", "rss_bytes", "peak_rss_bytes", "status_code",
    "attempt", "checkpoint", "cache_hit", "completed_fits", "stack",
    "tls_category", "cache_reads", "cache_writes", "cache_hits", "cache_misses",
    "cache_bypassed", "cache_failures", "cache_corruptions", "cache_shards", "read_duration_ms",
    "write_duration_ms",
})


def memory_usage() -> dict[str, int]:
    """Use process accounting where available without another dependency."""
    result: dict[str, int] = {}
    try:
        # Linux reports pages, not bytes, in statm.
        resident = int(Path("/proc/self/statm").read_text().split()[1])
        result["rss_bytes"] = resident * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        pass
    try:
        import resource
        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        result["peak_rss_bytes"] = peak * (1 if os.sys.platform == "darwin" else 1024)
    except (ImportError, OSError, ValueError):
        pass
    return result


def event(name: str, **fields: Any) -> dict[str, Any]:
    record = {"event": name, **memory_usage()}
    record.update({key: value for key, value in fields.items() if key in _FIELDS})
    LOGGER.warning(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return record


def report_exception(error: BaseException, **fields: Any) -> dict[str, Any]:
    locations = [{"file": Path(frame.filename).name,
                  "line": frame.lineno, "function": frame.name}
                 for frame in traceback.extract_tb(error.__traceback__)[-12:]]
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        fields = {**fields, "status_code": status}
    return event("tier_publication_failed", **{
        **fields, "exception_class": type(error).__name__, "stack": locations,
    })


@contextmanager
def phase(stage: str, **fields: Any) -> Iterator[None]:
    started = time.perf_counter()
    event("tier_stage_started", stage=stage, **fields)
    try:
        yield
    except BaseException as error:
        report_exception(error, stage=stage,
                         duration_ms=round((time.perf_counter()-started)*1000, 3), **fields)
        raise
    else:
        event("tier_stage_completed", stage=stage,
              duration_ms=round((time.perf_counter()-started)*1000, 3), **fields)
