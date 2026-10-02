"""Public combined Phoenix tier-list route."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from analysis_runtime import PrivateBlobStore
from pumbility_contract import combined_tier_blob_path
from tier_scores import TIER_SCORE_MODES, TierScoresNotFoundError, read_tier_player_scores


router = APIRouter()


@router.get("/api/tier-list/scores")
def get_tier_player_scores(
    player_key: str = Query(default="", alias="playerKey"),
    mode: str = Query(default=""),
):
    player_key = player_key.strip()
    mode = mode.strip().lower()
    headers = {"Cache-Control": "no-store"}
    if not player_key or mode not in TIER_SCORE_MODES:
        return JSONResponse(status_code=400, headers=headers, content={
            "error": "A playerKey and mode of singles, doubles, or coop are required.",
        })
    try:
        payload = read_tier_player_scores(PrivateBlobStore(), player_key, mode)
        return JSONResponse(content=payload, headers=headers)
    except TierScoresNotFoundError as error:
        return JSONResponse(status_code=404, content={"error": str(error)}, headers=headers)
    except (RuntimeError, ValueError, json.JSONDecodeError):
        return JSONResponse(status_code=503, content={"error": "Player scores are temporarily unavailable."}, headers=headers)
    except Exception:
        return JSONResponse(status_code=500, content={"error": "The selected player's scores could not be read."}, headers=headers)


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
        return JSONResponse(content=payload)
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
