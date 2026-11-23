"""Independent Phoenix 2 mode results and their consolidated Overall view."""
from __future__ import annotations

import json

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from analysis_runtime import PrivateBlobStore
from pro_pumbility_runtime import (
    PLAYER_KEY, ProGenerationChangedError, ProPlayerNotFoundError,
    ProUnavailableError, read_pro_index, read_pro_profile,
)

router = APIRouter()
HEADERS = {"Cache-Control": "no-store"}


@router.get("/api/pro-recommendations/players")
def get_pro_players():
    try:
        return JSONResponse(content=read_pro_index(PrivateBlobStore()), headers=HEADERS)
    except (RuntimeError, ValueError, json.JSONDecodeError):
        return JSONResponse(status_code=503, content={"error": "Pro Farming data is temporarily unavailable."}, headers=HEADERS)


@router.get("/api/pro-recommendations")
def get_pro_recommendations(player_key: str = Query(default="", alias="playerKey"),
                            generation: str = Query(default=""), mode: str = Query(default="overall"),
                            scope: str = Query(default="mode-consolidated")):
    if mode not in ("overall", "singles", "doubles") or scope != "mode-consolidated":
        return JSONResponse(status_code=400, content={"error": "Choose Overall, Singles or Doubles for Pro Farming."}, headers=HEADERS)
    if not PLAYER_KEY.fullmatch(player_key):
        return JSONResponse(status_code=400, content={"error": "Select a valid player."}, headers=HEADERS)
    try:
        return JSONResponse(content=read_pro_profile(PrivateBlobStore(), player_key, generation), headers=HEADERS)
    except ProPlayerNotFoundError as error:
        return JSONResponse(status_code=404, content={"error": str(error)}, headers=HEADERS)
    except ProGenerationChangedError as error:
        return JSONResponse(status_code=409, content={"error": str(error)}, headers=HEADERS)
    except (ProUnavailableError, RuntimeError, ValueError, json.JSONDecodeError):
        return JSONResponse(status_code=503, content={"error": "The Pro Farming profile is temporarily unavailable."}, headers=HEADERS)
