"""Public combined Phoenix tier-list route."""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from analysis_runtime import PrivateBlobStore
from piu_recommendations import combined_tier_blob_path


router = APIRouter()


@router.get("/api/tier-list")
def get_combined_tier_list():
    try:
        payload = PrivateBlobStore(canary_domain="tier-list").get_json(
            combined_tier_blob_path()
        )
        if payload is None:
            return JSONResponse(
                status_code=404,
                content={"error": "The combined tier list has not been generated yet."},
            )
        return payload
    except (RuntimeError, json.JSONDecodeError):
        return JSONResponse(
            status_code=503,
            content={"error": "The combined tier-list service is temporarily unavailable."},
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "The combined tier list could not be read."},
        )
