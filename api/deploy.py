"""Signed deployment webhook that reanalyzes the stored snapshot per promotion."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Mapping

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from analysis_runtime import deterministic_deployment_job_id
from api._shared import start_or_reuse_analysis
from mix_registry import DEFAULT_MIX_KEY, resolve_mix


router = APIRouter()


def webhook_authorized(raw_body: bytes, signature: str, secret: str) -> bool:
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha1).hexdigest()
    return hmac.compare_digest(signature, expected)


def deployment_details(payload: Mapping[str, Any]) -> tuple[str, str] | None:
    if payload.get("type") != "deployment.promoted":
        return None
    event_payload = payload.get("payload")
    if not isinstance(event_payload, Mapping):
        return None
    deployment = event_payload.get("deployment")
    project = event_payload.get("project")
    if not isinstance(deployment, Mapping) or not isinstance(project, Mapping):
        return None
    deployment_id = str(deployment.get("id") or "").strip()
    project_id = str(project.get("id") or "").strip()
    if not deployment_id or not project_id:
        return None
    return deployment_id, project_id


@router.post("/api/deploy")
async def deployment_promoted(
    request: Request,
    mix: str = Query(default=DEFAULT_MIX_KEY),
):
    raw_body = await request.body()
    secret = os.getenv("VERCEL_DEPLOY_WEBHOOK_SECRET", "").strip()
    if not webhook_authorized(
        raw_body, request.headers.get("x-vercel-signature", ""), secret
    ):
        return JSONResponse(status_code=401, content={"error": "Unauthorized webhook."})
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "Invalid webhook JSON."})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"error": "Invalid webhook payload."})

    details = deployment_details(payload)
    if details is None:
        return JSONResponse(status_code=202, content={"outcome": "ignored"})
    deployment_id, project_id = details
    expected_project = os.getenv("VERCEL_PROJECT_ID", "").strip()
    if expected_project and not hmac.compare_digest(project_id, expected_project):
        return JSONResponse(status_code=202, content={"outcome": "ignored"})

    try:
        mix_spec = resolve_mix(mix)
        job_id = deterministic_deployment_job_id(deployment_id, mix=mix_spec)
        mix_kwargs = {} if mix_spec.key == DEFAULT_MIX_KEY else {"mix": mix_spec}
        status, result = start_or_reuse_analysis(
            force_refresh=True,
            deterministic_job_id=job_id,
            reanalyze_only=True,
            trigger="deployment",
            **mix_kwargs,
        )
        job = result.get("job") if isinstance(result, dict) else None
        if isinstance(job, dict) and job.get("id") != job_id:
            return JSONResponse(
                status_code=503,
                content={"error": "Another refresh is active; retry this deployment event."},
            )
        if isinstance(job, dict) and job.get("status") == "failed":
            return JSONResponse(
                status_code=503,
                content={"error": "The deployment refresh is waiting for its retry window."},
            )
        return JSONResponse(status_code=status, content=result)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except RuntimeError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "The deployment refresh could not be scheduled."},
        )
