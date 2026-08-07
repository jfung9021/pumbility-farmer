"""Privacy-minimized recommendation index routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from analysis_runtime import PrivateBlobStore
from piu_recommendations import recommendation_blob_path


router = APIRouter()


def _read_index() -> dict | None:
    return PrivateBlobStore().get_json(recommendation_blob_path())


@router.get("/api/recommendations/players")
def get_recommendation_players():
    try:
        payload = _read_index()
        if payload is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Recommendations have not been generated yet."},
            )
        players = []
        for player in payload.get("players", []):
            modes = player.get("modes", {})
            players.append(
                {
                    "playerKey": player.get("playerKey"),
                    "username": player.get("username"),
                    "displayName": player.get("displayName"),
                    "eligibility": {
                        mode: bool(details.get("eligible"))
                        for mode, details in modes.items()
                        if mode in {"singles", "doubles"}
                    },
                }
            )
        return {
            "generatedAtUtc": payload.get("generatedAtUtc"),
            "method": payload.get("method", {}),
            "players": players,
        }
    except (RuntimeError, json.JSONDecodeError):
        return JSONResponse(
            status_code=503,
            content={"error": "The recommendation service is temporarily unavailable."},
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "The recommendation player list could not be read."},
        )


@router.get("/api/recommendations")
def get_player_recommendations(
    player_key: str = Query(default="", alias="playerKey"),
):
    if not player_key.strip():
        return JSONResponse(
            status_code=400,
            content={"error": "A playerKey is required."},
        )
    try:
        payload = _read_index()
        if payload is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Recommendations have not been generated yet."},
            )
        player = next(
            (
                row
                for row in payload.get("players", [])
                if row.get("playerKey") == player_key.strip()
            ),
            None,
        )
        if player is None:
            return JSONResponse(
                status_code=404,
                content={"error": "The selected recommendation player was not found."},
            )
        return {
            "generatedAtUtc": payload.get("generatedAtUtc"),
            "method": payload.get("method", {}),
            "player": player,
        }
    except (RuntimeError, json.JSONDecodeError):
        return JSONResponse(
            status_code=503,
            content={"error": "The recommendation service is temporarily unavailable."},
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "The selected recommendations could not be read."},
        )
