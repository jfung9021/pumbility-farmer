"""Privacy-minimized recommendation index routes."""

from __future__ import annotations

import json
import math

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from analysis_runtime import PrivateBlobStore
from piu_recommendations import recommendation_blob_path


router = APIRouter()


def _read_index() -> dict | None:
    return PrivateBlobStore().get_json(recommendation_blob_path())


def _manual_mode(charts: list[dict], chart_type: str, scoring_rating: float) -> dict:
    candidates = []
    for chart in charts:
        if chart.get("type") != chart_type:
            continue
        level = float(chart.get("level", 0))
        estimate = float(chart.get("estimatedDifficulty", 0))
        if level < 20 or estimate > scoring_rating + 0.5 + 1e-9:
            continue
        candidate = {
            **chart,
            "distanceFromRating": round(estimate - scoring_rating, 6),
            "farmEdge": round(level + 0.5 - estimate, 6),
            "existingPumbility": None,
            "expectedPumbility": 0,
            "projectedGain": 0,
            "projectedScore": None,
            "played": False,
        }
        candidates.append(candidate)
    candidates.sort(
        key=lambda row: (
            float(row["estimatedDifficulty"]),
            str(row.get("songName", "")).casefold(),
            str(row.get("chartId", "")),
        )
    )
    top_recommendations = sorted(
        candidates,
        key=lambda row: (
            -float(row["farmEdge"]),
            float(row["estimatedDifficulty"]),
            str(row.get("songName", "")).casefold(),
            str(row.get("chartId", "")),
        ),
    )[:20]
    return {
        "eligible": True,
        "manual": True,
        "validScoreCount": 0,
        "scoringRating": round(scoring_rating, 3),
        "candidateRange": [None, round(scoring_rating + 0.5, 3)],
        "candidateCount": len(candidates),
        "candidates": candidates,
        "topRecommendations": top_recommendations,
    }


def _manual_recommendations(payload: dict, scoring_rating: float) -> dict:
    return {
        "generatedAtUtc": payload.get("generatedAtUtc"),
        "method": payload.get("method", {}),
        "player": {
            "playerKey": "manual",
            "username": "",
            "displayName": f"Manual {scoring_rating:.2f}",
            "manual": True,
            "modes": {
                "singles": _manual_mode(payload.get("charts", []), "Single", scoring_rating),
                "doubles": _manual_mode(payload.get("charts", []), "Double", scoring_rating),
            },
        },
    }


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
    rating_value: str = Query(default="", alias="rating"),
):
    try:
        rating = float(rating_value) if rating_value.strip() else math.nan
    except ValueError:
        rating = math.nan
    valid_rating = math.isfinite(rating) and 1 <= rating <= 40
    if not player_key.strip() and not valid_rating:
        return JSONResponse(
            status_code=400,
            content={"error": "A playerKey or a skill rating from 1 to 40 is required."},
        )
    try:
        payload = _read_index()
        if payload is None:
            return JSONResponse(
                status_code=404,
                content={"error": "Recommendations have not been generated yet."},
            )
        if not player_key.strip():
            return _manual_recommendations(payload, rating)
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
