"""Public official leaderboard comparison routes; raw captures stay private."""
from __future__ import annotations

import json

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from analysis_runtime import PrivateBlobStore
from official_player_comparison import (
    ComparisonGenerationChangedError, ComparisonPlayerNotFoundError, ComparisonUnavailableError,
    PLAYER_KEY, read_comparison_index, read_comparison_profile,
)
from skill_radar import validate_bpm_policy

router = APIRouter()
HEADERS = {"Cache-Control": "no-store"}


@router.get("/api/official-comparison/players")
def get_official_comparison_players(bpm_policy: str = Query(default="", alias="bpmPolicy")):
    try:
        policy = validate_bpm_policy(bpm_policy if isinstance(bpm_policy, str) else None)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Unsupported BPM policy."}, headers=HEADERS)
    try:
        return JSONResponse(content=read_comparison_index(PrivateBlobStore(), bpm_policy=policy), headers=HEADERS)
    except (RuntimeError, ValueError, json.JSONDecodeError):
        return JSONResponse(status_code=503, content={"error": "Official leaderboard comparison is temporarily unavailable."}, headers=HEADERS)


@router.get("/api/official-comparison")
def get_official_comparison(player_key: str = Query(default="", alias="playerKey"), generation: str = Query(default=""),
                            bpm_policy: str = Query(default="", alias="bpmPolicy")):
    if not PLAYER_KEY.fullmatch(player_key):
        return JSONResponse(status_code=400, content={"error": "Select a valid official leaderboard player."}, headers=HEADERS)
    try:
        policy = validate_bpm_policy(bpm_policy if isinstance(bpm_policy, str) else None)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Unsupported BPM policy."}, headers=HEADERS)
    try:
        return JSONResponse(content=read_comparison_profile(PrivateBlobStore(), player_key, generation, bpm_policy=policy), headers=HEADERS)
    except ComparisonPlayerNotFoundError as error:
        return JSONResponse(status_code=404, content={"error": str(error)}, headers=HEADERS)
    except ComparisonGenerationChangedError as error:
        return JSONResponse(status_code=409, content={"error": str(error)}, headers=HEADERS)
    except (ComparisonUnavailableError, RuntimeError, ValueError, json.JSONDecodeError):
        return JSONResponse(status_code=503, content={"error": "The selected official leaderboard profile is temporarily unavailable."}, headers=HEADERS)
