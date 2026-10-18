"""Password-protected operator controls for manual Phoenix 2 refreshes."""

from __future__ import annotations

import hmac
import os
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from api._shared import start_or_reuse_analysis
from mix_registry import resolve_mix
from tier_diagnostics import report_exception


router = APIRouter()


def _response(status_code: int, content: dict) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers={"Cache-Control": "no-store"},
    )


def jonathan_authorized(provided: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(provided, expected)


def _tier_fit_denial(request: Request):
    password = os.getenv("JONATHAN_PASSWORD", "").strip()
    if not password:
        return _response(503, {"error": "The Jonathan refresh controls are not configured."})
    if not jonathan_authorized(request.headers.get("x-jonathan-password", ""), password):
        return _response(401, {"error": "Unauthorized refresh request."})
    return None


@router.get("/api/jonathan/tier-fits")
def get_tier_fit_status(request: Request):
    denial = _tier_fit_denial(request)
    if denial is not None:
        return denial
    try:
        from analysis_runtime import PrivateBlobStore
        from tier_release import source_aware_fit_status
        return _response(200, source_aware_fit_status(PrivateBlobStore()))
    except (ValueError, RuntimeError):
        return _response(503, {"error": "The cached tier-fit sources could not be inspected."})


@router.post("/api/jonathan/tier-fits")
def cache_tier_fit(request: Request, payload: dict):
    denial = _tier_fit_denial(request)
    if denial is not None:
        return denial
    try:
        from analysis_runtime import PrivateBlobStore
        from tier_release import seed_source_aware_fit
        return _response(200, seed_source_aware_fit(PrivateBlobStore(), payload))
    except ValueError as error:
        return _response(400, {"error": str(error)})
    except RuntimeError:
        return _response(503, {"error": "The target fit could not be cached."})


@router.post("/api/jonathan/refresh")
def refresh_from_jonathan(
    request: Request,
    mode: Literal["incremental", "full", "reanalyze", "official", "official-status", "tiers"] = Query(default="incremental"),
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
        if mode == "tiers":
            from tier_publication_jobs import request_tier_publication
            status, payload = request_tier_publication(enqueue_tier_publication)
            return _response(status, payload)
        if mode == "official-status":
            from analysis_runtime import PrivateBlobStore
            from pumbility_contract import official_scoring_manifest_path

            manifest = PrivateBlobStore().get_json(official_scoring_manifest_path())
            if manifest is None:
                return _response(200, {"outcome": "official-sources-missing"})
            return _response(200, {
                "outcome": "official-sources-ready",
                "sources": {mix: {key: ref[key] for key in ("asOf", "fetchedAtUtc")}
                            for mix, ref in manifest["captures"].items()},
            })
        if mode == "official":
            if os.getenv("PIU_OFFICIAL_CROSS_LEVEL_SCORING", "") != "1":
                return _response(409, {"error": "Official cross-level Scoring is not enabled."})
            from analysis_runtime import PrivateBlobStore, PiuScoresClient
            from official_scoring_sources import refresh_hosted_official_scoring_sources

            result = refresh_hosted_official_scoring_sources(
                PiuScoresClient(api_key=os.getenv("PIU_SCORES_API_KEY", "")), PrivateBlobStore(),
            )
            return _response(200, {"outcome": "official-sources-refreshed", **result})
        status, payload = start_or_reuse_analysis(
            mix=resolve_mix("phoenix2"),
            force_refresh=True,
            full_sync=full_sync,
            reanalyze_only=mode == "reanalyze",
            trigger="jonathan",
        )
        return _response(status, payload)
    except ValueError as exc:
        report_exception(exc, stage="operator-trigger", status_code=400)
        return _response(400, {"error": str(exc)})
    except RuntimeError as exc:
        from tier_release import TierReleaseBusyError
        report_exception(exc, stage="operator-trigger", status_code=503)
        if isinstance(exc, TierReleaseBusyError):
            return _response(409, {"error": str(exc)})
        return _response(503, {"error": str(exc)})
    except Exception as error:
        request_id = uuid4().hex
        report_exception(error, request_id=request_id, stage="operator-trigger", status_code=500)
        return _response(
            500,
            {"error": "The analysis job could not be started.", "requestId": request_id},
        )


def enqueue_tier_publication(job_id: str) -> None:
    from worker.tasks import publish_tiers
    from worker.constants import QUEUE_NAME
    publish_tiers.apply_async(args=[job_id], task_id=job_id, queue=QUEUE_NAME)


@router.get("/api/jonathan/tier-publication")
def tier_publication_status(request: Request, jobId: str | None = Query(default=None)):
    denial = _tier_fit_denial(request)
    if denial is not None:
        return denial
    try:
        from tier_publication_jobs import get_tier_publication_status
        status = get_tier_publication_status(jobId)
        if status is None:
            return _response(404, {"error": "Tier publication job not found."})
        return _response(200, status)
    except Exception as error:
        request_id = uuid4().hex
        report_exception(error, request_id=request_id, stage="operator-status", status_code=503)
        return _response(503, {"error": "Tier publication status is unavailable.", "requestId": request_id})


@router.get("/api/jonathan/tier-storage")
def tier_storage_status(request: Request):
    denial = _tier_fit_denial(request)
    if denial is not None:
        return denial
    try:
        from analysis_runtime import PrivateBlobStore
        return _response(200, PrivateBlobStore().tier_storage_status())
    except Exception as error:
        request_id = uuid4().hex
        report_exception(error, request_id=request_id, stage="storage-preflight", status_code=503)
        return _response(503, {"error": "Tier storage status is unavailable.", "requestId": request_id})
