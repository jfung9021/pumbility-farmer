"""JSON-only FastAPI routes for latest analysis data and async job status."""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from analysis_runtime import (
    PrivateBlobStore,
    RuntimeJobStore,
    read_latest_payload,
    update_job,
)
from api._shared import start_or_reuse_analysis
from api.cron import cron_authorized
from mix_registry import DEFAULT_MIX_KEY, resolve_mix


router = APIRouter()


@router.get("/api/analyze")
def get_analysis(
    job_id: str | None = Query(default=None, alias="jobId"),
    mix: str = Query(default=DEFAULT_MIX_KEY),
):
    try:
        mix_spec = resolve_mix(mix)
        if mix_spec.archived:
            if job_id and job_id.strip():
                return JSONResponse(
                    status_code=410,
                    content={
                        "outcome": "archived",
                        "error": f"{mix_spec.label} refresh jobs are no longer available.",
                        "archiveUrl": mix_spec.archive_url,
                    },
                )
            return RedirectResponse(url=mix_spec.archive_url or "/", status_code=307)
        if job_id and job_id.strip():
            job = RuntimeJobStore().get(job_id.strip())
            if job is None:
                return JSONResponse(
                    status_code=404,
                    content={"error": "Analysis job not found or its status has expired."},
                )
            if resolve_mix(job.get("mix")).key != mix_spec.key:
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Analysis job not found for {mix_spec.label}."},
                )
            return job
        payload = read_latest_payload(PrivateBlobStore(), mix_spec)
        if payload is None:
            return JSONResponse(
                status_code=404,
                content={"error": "No completed analysis is stored yet."},
            )
        return payload
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except (RuntimeError, json.JSONDecodeError):
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
def refresh_analysis(mix: str = Query(default=DEFAULT_MIX_KEY)):
    try:
        status, payload = start_or_reuse_analysis(mix=resolve_mix(mix))
        return JSONResponse(status_code=status, content=payload)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except RuntimeError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "The analysis job could not be started."},
        )


@router.post("/api/analyze/cancel")
def cancel_analysis(
    request: Request,
    job_id: str = Query(default="", alias="jobId"),
):
    secret = os.getenv("CRON_SECRET", "")
    if not cron_authorized(request.headers.get("authorization") or "", secret):
        return JSONResponse(
            status_code=401,
            content={"error": "Unauthorized analysis cancellation request."},
        )
    normalized_id = job_id.strip()
    if not normalized_id:
        return JSONResponse(status_code=400, content={"error": "A jobId is required."})
    try:
        jobs = RuntimeJobStore()
        job = jobs.get(normalized_id)
        if job is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Analysis job not found or its status has expired."},
            )
        if job.get("status") == "completed":
            return {"outcome": "completed", "job": job}
        cancelled = update_job(
            jobs,
            normalized_id,
            status="failed",
            error="The analysis job was cancelled by an operator.",
            retryAllowedAtUtc=None,
            cancelRequested=True,
            progress={
                "current": 0,
                "total": 0,
                "percent": 0,
                "message": "The analysis job was cancelled and will not be retried.",
            },
        )
        if jobs.active_job_id() == normalized_id:
            jobs.set_active_job_id(None)
        return {"outcome": "cancelled", "job": cancelled}
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "The analysis job could not be cancelled."},
        )
