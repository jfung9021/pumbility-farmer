"""Secured daily Vercel Cron route using the standard refresh rules."""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from api._shared import start_or_reuse_analysis
from mix_registry import DEFAULT_MIX_KEY, resolve_mix


router = APIRouter()


def cron_authorized(authorization: str, secret: str) -> bool:
    return bool(secret) and hmac.compare_digest(authorization, f"Bearer {secret}")


@router.get("/api/cron")
def run_cron(request: Request, mix: str = Query(default=DEFAULT_MIX_KEY)):
    secret = os.getenv("CRON_SECRET", "").strip()
    authorization = request.headers.get("Authorization", "")
    if not cron_authorized(authorization, secret):
        return JSONResponse(status_code=401, content={"error": "Unauthorized cron request."})
    try:
        status, payload = start_or_reuse_analysis(
            mix=resolve_mix(mix), trigger="cron"
        )
        return JSONResponse(status_code=status, content=payload)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except RuntimeError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "The scheduled analysis job could not be started."},
        )


@router.get("/api/cron/{mix}")
def run_mix_cron(request: Request, mix: str):
    return run_cron(request, mix)
