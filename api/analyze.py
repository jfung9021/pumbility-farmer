"""JSON-only FastAPI routes for latest analysis data and async job status."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from analysis_runtime import LATEST_BLOB_PATH, PrivateBlobStore, RuntimeJobStore
from api._shared import start_or_reuse_analysis


router = APIRouter()


@router.get("/api/analyze")
def get_analysis(job_id: str | None = Query(default=None, alias="jobId")):
    try:
        if job_id and job_id.strip():
            job = RuntimeJobStore().get(job_id.strip())
            if job is None:
                return JSONResponse(
                    status_code=404,
                    content={"error": "Analysis job not found or its status has expired."},
                )
            return job
        payload = PrivateBlobStore().get_json(LATEST_BLOB_PATH)
        if payload is None:
            return JSONResponse(
                status_code=404,
                content={"error": "No completed analysis is stored yet."},
            )
        return payload
    except (RuntimeError, ValueError, json.JSONDecodeError):
        return JSONResponse(
            status_code=503,
            content={"error": "The latest analysis service is temporarily unavailable."},
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "The latest analysis could not be read."},
        )


@router.post("/api/analyze")
def refresh_analysis():
    try:
        status, payload = start_or_reuse_analysis()
        return JSONResponse(status_code=status, content=payload)
    except RuntimeError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "The analysis job could not be started."},
        )
