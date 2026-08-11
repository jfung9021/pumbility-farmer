"""Password-protected operator controls for manual Phoenix 2 refreshes."""

from __future__ import annotations

import hmac
import os
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from api._shared import start_or_reuse_analysis
from mix_registry import resolve_mix


router = APIRouter()


def _response(status_code: int, content: dict) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers={"Cache-Control": "no-store"},
    )


def jonathan_authorized(provided: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(provided, expected)


@router.post("/api/jonathan/refresh")
def refresh_from_jonathan(
    request: Request,
    mode: Literal["incremental", "full"] = Query(default="incremental"),
):
    password = os.getenv("JONATHAN_PASSWORD", "").strip()
    if not password:
        return _response(
            503,
            {"error": "The Jonathan refresh controls are not configured."},
        )
    if not jonathan_authorized(
        request.headers.get("x-jonathan-password", ""),
        password,
    ):
        return _response(
            401,
            {"error": "Unauthorized refresh request."},
        )

    full_sync = mode == "full"
    try:
        status, payload = start_or_reuse_analysis(
            mix=resolve_mix("phoenix2"),
            force_refresh=True,
            full_sync=full_sync,
            trigger="jonathan",
        )
        return _response(status, payload)
    except ValueError as exc:
        return _response(400, {"error": str(exc)})
    except RuntimeError as exc:
        return _response(503, {"error": str(exc)})
    except Exception:
        return _response(
            500,
            {"error": "The analysis job could not be started."},
        )
