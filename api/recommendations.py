"""Privacy-minimized recommendation index routes."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Mapping

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from analysis_runtime import PrivateBlobStore, RuntimeJobStore, update_job
from phoenix2_sync import isoformat_utc, parse_utc, utc_now
from piu_recommendations import recommendation_blob_path, recommendation_shard_path
from recommendation_refresh import (
    PLAYER_REFRESH_FRESHNESS,
    cached_player_is_fresh,
    find_player_metadata,
    player_refresh_job_id,
    recommendation_player_path,
    with_staleness,
)
from worker.celery import PLAYER_QUEUE_NAME
from worker.tasks import refresh_player_recommendations


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


def _enqueue_player_refresh(job_id: str) -> None:
    refresh_player_recommendations.apply_async(
        args=[job_id],
        task_id=job_id,
        queue=PLAYER_QUEUE_NAME,
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
                "modelGeneratedAtUtc": payload.get(
                    "modelGeneratedAtUtc", payload.get("generatedAtUtc")
                ),
                "refreshSupported": int(payload.get("storageSchemaVersion") or 0) >= 3,
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
        store = PrivateBlobStore()
        if int(payload.get("storageSchemaVersion") or 0) >= 3:
            if find_player_metadata(payload, normalized_key) is None:
                player = None
            else:
                cached = store.get_json(recommendation_player_path(normalized_key))
                if cached is None:
                    return JSONResponse(
                        status_code=404,
                        content={
                            "error": "This player's recommendations have not been refreshed yet.",
                            "refreshRequired": True,
                        },
                        headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
                    )
                return JSONResponse(
                    content=with_staleness(cached, payload),
                    headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
                )
        else:
            player = _read_player(store, payload, normalized_key)
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


@router.post("/api/recommendations/refresh")
def start_player_recommendation_refresh(
    player_key: str = Query(default="", alias="playerKey"),
):
    normalized_key = player_key.strip()
    if not normalized_key:
        return JSONResponse(
            status_code=400,
            content={"error": "A playerKey is required."},
            headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
        )
    try:
        store = PrivateBlobStore()
        index = store.get_json(recommendation_blob_path())
        if index is None or int(index.get("storageSchemaVersion") or 0) < 3:
            return JSONResponse(
                status_code=503,
                content={"error": "The player-refresh model is not available yet."},
                headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
            )
        if find_player_metadata(index, normalized_key) is None:
            return JSONResponse(
                status_code=404,
                content={"error": "The selected recommendation player was not found."},
                headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
            )
        effective_now = utc_now()
        cached = store.get_json(recommendation_player_path(normalized_key))
        if cached_player_is_fresh(cached, index, now=effective_now):
            synced = cached.get("playerSyncedAtUtc")
            synced_at = parse_utc(synced) or effective_now
            return JSONResponse(
                content={
                    "outcome": "fresh",
                    "recommendation": with_staleness(cached, index),
                    "refreshEligibleAtUtc": isoformat_utc(
                        synced_at + PLAYER_REFRESH_FRESHNESS
                    ),
                },
                headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
            )
        jobs = RuntimeJobStore()
        job_id = player_refresh_job_id(normalized_key, effective_now)
        existing = jobs.get(job_id)
        if existing and existing.get("status") in {
            "queued",
            "running",
            "completed",
            "failed",
        }:
            return JSONResponse(
                status_code=202,
                content={"outcome": "existing", "job": existing},
                headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
            )
        stamp = isoformat_utc(effective_now)
        job = {
            "id": job_id,
            "kind": "player-recommendation-refresh",
            "playerKey": normalized_key,
            "status": "queued",
            "stage": "queued",
            "createdAtUtc": stamp,
            "updatedAtUtc": stamp,
            "startedAtUtc": None,
            "completedAtUtc": None,
            "generatedAtUtc": None,
            "retryAllowedAtUtc": None,
            "error": None,
            "progress": {
                "current": 0,
                "total": 1,
                "percent": 0,
                "message": "Waiting for the player recommendation worker.",
            },
        }
        jobs.save(job)
        try:
            _enqueue_player_refresh(job_id)
        except Exception as exc:
            failed = update_job(
                jobs,
                job_id,
                status="failed",
                error="The player refresh could not be queued. Please retry shortly.",
                retryAllowedAtUtc=isoformat_utc(effective_now + timedelta(seconds=60)),
            )
            raise RuntimeError(failed["error"]) from exc
        return JSONResponse(
            status_code=202,
            content={"outcome": "started", "job": jobs.get(job_id) or job},
            headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
        )
    except (RuntimeError, json.JSONDecodeError) as exc:
        return JSONResponse(
            status_code=503,
            content={"error": str(exc) or "The player refresh is temporarily unavailable."},
            headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "The player refresh could not be started."},
            headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
        )


@router.get("/api/recommendations/refresh")
def get_player_recommendation_refresh(
    job_id: str = Query(default="", alias="jobId"),
):
    normalized_id = job_id.strip()
    if not normalized_id:
        return JSONResponse(
            status_code=400,
            content={"error": "A jobId is required."},
            headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
        )
    try:
        job = RuntimeJobStore().get(normalized_id)
        if job is None or job.get("kind") != "player-recommendation-refresh":
            return JSONResponse(
                status_code=404,
                content={"error": "Player refresh job not found or expired."},
                headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
            )
        return JSONResponse(
            content=job,
            headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
        )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "The player refresh status is temporarily unavailable."},
            headers={"Cache-Control": NO_STORE_CACHE_CONTROL},
        )
