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


def _platform_cron_diagnostic_request(request: Request) -> bool:
    from topology_diagnostics import cron_diagnostic_enabled

    return bool(
        cron_diagnostic_enabled(os.environ)
        and request.headers.get("user-agent", "").strip().casefold()
        == "vercel-cron/1.0"
    )


def _topology_cron_claim(*, create: bool) -> tuple[str, str, bool]:
    import psycopg

    from pumbility_store import PumbilityArtifactStore, _assert_schema
    from scripts.backfill_pumbility_production import _assert_database_target
    from topology_diagnostics import (
        cron_marker_path,
        require_cron_diagnostic_environment,
        require_runtime_database_url,
        validated_cron_correlation,
    )

    try:
        label, _connection_limit = require_cron_diagnostic_environment(os.environ)
        correlation = validated_cron_correlation(os.environ)
        marker_path = cron_marker_path(label, correlation)
        database_url = require_runtime_database_url(os.environ)
        with psycopg.connect(database_url, prepare_threshold=None) as connection:
            with connection.transaction(), connection.cursor() as cursor:
                _assert_schema(cursor)
                _assert_database_target(cursor)
                cursor.execute(
                    "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (marker_path,),
                )
                cursor.execute(
                    "select 1 from pumbility.artifacts where object_key = %s",
                    (marker_path,),
                )
                exists = cursor.fetchone() is not None
                if create and not exists:
                    PumbilityArtifactStore._put_json_row(
                        cursor,
                        marker_path,
                        {"schemaVersion": 1, "delivered": True},
                    )
        return label, correlation, exists
    except Exception:
        raise RuntimeError("The topology cron correlation could not be recorded.") from None


def _emit_topology_cron_route_event(
    request: Request, *, status: int, outcome: str
) -> bool:
    """Emit once only after a genuine request newly starts an HTTP-202 cycle.

    Returns True when a prior durable correlation proves this request is a replay.
    """
    from topology_diagnostics import emit_event

    if not _platform_cron_diagnostic_request(request):
        return False
    newly_started = status == 202 and outcome == "started"
    label, correlation, existed = _topology_cron_claim(create=newly_started)
    if not newly_started:
        return existed
    if existed:
        return True
    emit_event(
        {
            "kind": "cron",
            "label": label,
            "source": "route",
            "correlationSha256": correlation,
            "count": 1,
            "authorized": True,
        }
    )
    return False


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
        replay_suppressed = _emit_topology_cron_route_event(
            request,
            status=status,
            outcome=str(payload.get("outcome") or ""),
        )
        if replay_suppressed:
            payload = {**payload, "topologyReplaySuppressed": True}
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
