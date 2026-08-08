"""Privacy-minimized recommendation index routes."""

from __future__ import annotations

import json
from typing import Mapping

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from analysis_runtime import PrivateBlobStore
from piu_recommendations import recommendation_blob_path, recommendation_shard_path


router = APIRouter()
PLAYER_LIST_CACHE_CONTROL = (
    "public, max-age=300, s-maxage=300, stale-while-revalidate=3600"
)
NO_STORE_CACHE_CONTROL = "no-store"


def _read_index() -> dict | None:
    return PrivateBlobStore().get_json(recommendation_blob_path())


def _player_eligibility(player: Mapping) -> dict[str, bool]:
    stored = player.get("eligibility")
    if isinstance(stored, Mapping):
        return {
            mode: bool(stored.get(mode))
            for mode in ("singles", "doubles")
            if mode in stored
        }
    modes = player.get("modes", {})
    if not isinstance(modes, Mapping):
        return {}
    return {
        mode: bool(details.get("eligible"))
        for mode, details in modes.items()
        if mode in {"singles", "doubles"} and isinstance(details, Mapping)
    }


def _read_player(store: PrivateBlobStore, payload: dict, player_key: str) -> dict | None:
    metadata = next(
        (row for row in payload.get("players", []) if row.get("playerKey") == player_key),
        None,
    )
    if metadata is None:
        return None
    if int(payload.get("storageSchemaVersion") or 0) < 2:
        return metadata
    generation_key = str(payload.get("generationKey") or "").strip()
    shard = metadata.get("shard")
    if not generation_key or not isinstance(shard, int):
        raise ValueError("The recommendation shard index is invalid.")
    shard_payload = store.get_json(recommendation_shard_path(generation_key, shard))
    if shard_payload is None:
        raise RuntimeError("The selected recommendation shard is unavailable.")
    return next(
        (
            row
            for row in shard_payload.get("players", [])
            if row.get("playerKey") == player_key
        ),
        None,
    )


@router.get("/api/recommendations/players")
def get_recommendation_players():
    try:
        payload = _read_index()
        if payload is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Recommendations have not been generated yet."},
                headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
            )
        players = []
        for player in payload.get("players", []):
            players.append(
                {
                    "playerKey": player.get("playerKey"),
                    "username": player.get("username"),
                    "displayName": player.get("displayName"),
                    "eligibility": _player_eligibility(player),
                }
            )
        return JSONResponse(
            content={
                "generatedAtUtc": payload.get("generatedAtUtc"),
                "method": payload.get("method", {}),
                "players": players,
            },
            headers={"Cache-Control": PLAYER_LIST_CACHE_CONTROL},
        )
    except (RuntimeError, json.JSONDecodeError):
        return JSONResponse(
            status_code=503,
            content={"error": "The recommendation service is temporarily unavailable."},
            headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "The recommendation player list could not be read."},
            headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
        )


@router.get("/api/recommendations")
def get_player_recommendations(
    player_key: str = Query(default="", alias="playerKey"),
):
    normalized_key = player_key.strip()
    if not normalized_key:
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
        player = _read_player(PrivateBlobStore(), payload, normalized_key)
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
