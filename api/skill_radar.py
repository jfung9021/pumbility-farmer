"""Phoenix 2 Skill Radar; retained submitted evidence only, no score fetches."""
from __future__ import annotations

import json
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from analysis_runtime import PrivateBlobStore
from skill_radar_runtime import (PLAYER_KEY, SkillRadarIneligibleError,
    SkillRadarNotFoundError, read_skill_radar, read_skill_radar_players)
from skill_radar import validate_bpm_policy

router = APIRouter()


@router.get("/api/skill-radar/players")
def get_skill_radar_players(bpm_policy: str = Query(default="", alias="bpmPolicy")):
    headers = {"Cache-Control": "no-store"}
    try:
        policy = validate_bpm_policy(bpm_policy if isinstance(bpm_policy, str) else None)
        return JSONResponse(content=read_skill_radar_players(PrivateBlobStore(), bpm_policy=policy), headers=headers)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Unsupported BPM policy."}, headers=headers)
    except Exception:
        return JSONResponse(status_code=503, content={"error": "The Skill Radar player list is temporarily unavailable."}, headers=headers)


@router.get("/api/skill-radar")
def get_skill_radar(player_key: str = Query(default="", alias="playerKey"),
                    bpm_policy: str = Query(default="", alias="bpmPolicy")):
    headers = {"Cache-Control": "no-store"}
    if not PLAYER_KEY.fullmatch(player_key):
        return JSONResponse(status_code=400, content={"error": "A valid playerKey is required."}, headers=headers)
    try:
        policy = validate_bpm_policy(bpm_policy if isinstance(bpm_policy, str) else None)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Unsupported BPM policy."}, headers=headers)
    try:
        return JSONResponse(content=read_skill_radar(PrivateBlobStore(), player_key, bpm_policy=policy), headers=headers)
    except SkillRadarIneligibleError as error:
        return JSONResponse(status_code=403, content={"error": str(error)}, headers=headers)
    except SkillRadarNotFoundError as error:
        return JSONResponse(status_code=404, content={"error": str(error)}, headers=headers)
    except (RuntimeError, ValueError, json.JSONDecodeError):
        return JSONResponse(status_code=503, content={"error": "Skill Radar is temporarily unavailable."}, headers=headers)
    except Exception:
        return JSONResponse(status_code=500, content={"error": "Skill Radar could not be read."}, headers=headers)
