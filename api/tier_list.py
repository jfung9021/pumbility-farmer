"""Public combined Phoenix tier-list route."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from analysis_runtime import PrivateBlobStore
from pumbility_contract import combined_tier_blob_path
from tier_scores import TIER_SCORE_MODES, TierScoresNotFoundError, read_tier_player_scores
from tier_public import compact_tier_payload, detail_shard_path, tier_chart_details, tier_detail_generated_at
from bpm_metadata_overlay import select_bpm_payload, validate_bpm_policy
from song_bpm_metadata import bpm_metadata_identity


router = APIRouter()


@router.get("/api/tier-list/chart")
def get_tier_chart_details(
    chart_id: str = Query(default="", alias="chartId"),
    generated_at_utc: str = Query(default="", alias="generatedAtUtc"),
    bpm_policy: Annotated[str, Query(alias="bpmPolicy")] = "",
    bpm_metadata_revision: Annotated[str, Query(alias="bpmMetadataRevision")] = "",
):
    headers = {"Cache-Control": "no-store"}
    if not chart_id.strip() or not generated_at_utc.strip():
        return JSONResponse(status_code=400, content={"error": "A chartId and generatedAtUtc are required."}, headers=headers)
    try:
        focal = validate_bpm_policy(bpm_policy)
    except ValueError as error:
        return JSONResponse(status_code=400, content={"error": str(error)}, headers=headers)
    try:
        if focal and bpm_metadata_revision != bpm_metadata_identity()["bpmMetadataRevision"]:
            return JSONResponse(status_code=409, content={"error": "The BPM metadata has changed. Reload the tier list."}, headers=headers)
        store = PrivateBlobStore(canary_domain="tier-list")
        payload = store.get_json(combined_tier_blob_path())
        if payload is None:
            return JSONResponse(status_code=404, content={"error": "The tier list is unavailable."}, headers=headers)
        if generated_at_utc != payload.get("generatedAtUtc"):
            return JSONResponse(status_code=409, content={"error": "The tier list has changed. Reload it to view this chart."}, headers=headers)
        view = payload.get("tierListView", {})
        if view.get("kind") == "list":
            prefix = view.get("detailsPrefix", "")
            shard = store.get_json(detail_shard_path(prefix, chart_id))
            # Numeric fit time can precede newly assembled scoring-goal details.
            # The client still requests/receives the current list generation.
            details_at = tier_detail_generated_at(payload)
            if (not details_at or shard is None or shard.get("schemaVersion") != 1
                    or shard.get("generatedAtUtc") != details_at):
                raise RuntimeError("The current chart detail shard is unavailable.")
            chart = shard.get("charts", {}).get(chart_id)
            details = {"generatedAtUtc": generated_at_utc, "chart": chart} if chart else None
        else:
            details = tier_chart_details(payload, chart_id)
        return JSONResponse(content=select_bpm_payload(details, bpm_policy), headers=headers) if details else JSONResponse(status_code=404, content={"error": "The selected chart was not found."}, headers=headers)
    except (RuntimeError, ValueError, json.JSONDecodeError):
        return JSONResponse(status_code=503, content={"error": "Chart details are temporarily unavailable."}, headers=headers)
    except Exception:
        return JSONResponse(status_code=500, content={"error": "The selected chart could not be read."}, headers=headers)


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
def get_combined_tier_list(bpm_policy: Annotated[str, Query(alias="bpmPolicy")] = ""):
    try:
        focal = validate_bpm_policy(bpm_policy)
    except ValueError as error:
        return JSONResponse(status_code=400, content={"error": str(error)}, headers={"Cache-Control": "no-store"})
    try:
        payload = PrivateBlobStore(canary_domain="tier-list").get_json(
            combined_tier_blob_path()
        )
        if payload is None:
            return JSONResponse(
                status_code=404,
                content={"error": "The combined tier list has not been generated yet."},
            )
        selected = select_bpm_payload(payload, bpm_policy)
        return JSONResponse(content=compact_tier_payload(selected), headers={"Cache-Control": "no-store"})
    except (RuntimeError, ValueError, json.JSONDecodeError):
        return JSONResponse(
            status_code=503,
            content={"error": "The combined tier-list service is temporarily unavailable."},
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "The combined tier list could not be read."},
        )
