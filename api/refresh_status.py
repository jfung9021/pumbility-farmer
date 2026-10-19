"""Small read-only publication and global refresh status for public pages."""

from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from phoenix2_sync import isoformat_utc, parse_utc, utc_now
from pumbility_contract import combined_tier_blob_path, recommendation_blob_path
from pumbility_store import _read_cursor, _validate_schema_value


router = APIRouter()
HEADERS = {"Cache-Control": "no-store"}
STATUSES = frozenset({"queued", "running", "completed", "failed"})
STAGES = frozenset({"queued", "discovering", "syncing", "analyzing", "publishing"})


def _timestamp(value: object) -> str | None:
    parsed = parse_utc(value) if isinstance(value, str) else None
    return isoformat_utc(parsed) if parsed is not None else None


def read_refresh_status() -> dict:
    """Project timestamps and safe job fields in one bounded read transaction."""
    database_url = os.getenv("PUMBILITY_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Publication status storage is unavailable.")
    with _read_cursor(database_url) as cursor:
        cursor.execute(
            """
            select
                (select value from pumbility.schema_metadata
                 where key = 'migration_version'),
                (select payload_json ->> 'generatedAtUtc'
                 from pumbility.artifacts where object_key = %s),
                (select coalesce(payload_json ->> 'modelGeneratedAtUtc',
                                 payload_json ->> 'generatedAtUtc')
                 from pumbility.artifacts where object_key = %s),
                job.status, job.stage,
                job.payload ->> 'updatedAtUtc',
                job.payload ->> 'completedAtUtc',
                case when job.status = 'failed' then
                    case when job.payload ->> 'error' =
                        'The typed analysis checkpoint made no progress after three resumptions.'
                    then 'analysis-no-progress' else 'refresh-failed' end
                else null end
            from (values (1)) as required_row(present)
            left join pumbility.job_heads as head on head.name = 'latest:phoenix2'
            left join pumbility.jobs as job on job.id = head.job_id
            """,
            (combined_tier_blob_path(), recommendation_blob_path()),
        )
        row = cursor.fetchone()
    _validate_schema_value(row[0] if row else None)
    latest = None
    if row[3] in STATUSES:
        latest = {
            "status": row[3],
            "stage": row[4] if row[4] in STAGES else None,
            "updatedAtUtc": _timestamp(row[5]),
            "completedAtUtc": _timestamp(row[6]),
            "failureCode": row[7] if row[7] in {"analysis-no-progress", "refresh-failed"} else None,
        }
    return {
        "checkedAtUtc": isoformat_utc(utc_now()),
        "tierGeneratedAtUtc": _timestamp(row[1]),
        "modelGeneratedAtUtc": _timestamp(row[2]),
        "latestRefresh": latest,
    }


@router.get("/api/refresh-status")
def get_refresh_status():
    try:
        return JSONResponse(content=read_refresh_status(), headers=HEADERS)
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "Refresh status is temporarily unavailable."},
            headers=HEADERS,
        )
