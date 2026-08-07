"""Secured daily Vercel Cron route using the standard refresh rules."""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api._shared import start_or_reuse_analysis


router = APIRouter()


def cron_authorized(authorization: str, secret: str) -> bool:
    return bool(secret) and hmac.compare_digest(authorization, f"Bearer {secret}")


@router.get("/api/cron")
def run_cron(request: Request):
    secret = os.getenv("CRON_SECRET", "").strip()
    authorization = request.headers.get("Authorization", "")
    if not cron_authorized(authorization, secret):
        return JSONResponse(status_code=401, content={"error": "Unauthorized cron request."})
    try:
        status, payload = start_or_reuse_analysis()
        return JSONResponse(status_code=status, content=payload)
    except RuntimeError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "The scheduled analysis job could not be started."},
        )
