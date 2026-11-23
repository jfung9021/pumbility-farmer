"""Read generated, public-safe Pro Farming artifacts from private storage."""
from __future__ import annotations

import os
import re
from typing import Any

METHOD = "pro-pumbility-p50-modes-overall-top50-phoenix2-v6"
PLAYER_KEY = re.compile(r"(?:[0-9a-f]{20}|op_[0-9a-f]{64})\Z")
GENERATION = re.compile(r"[0-9a-f]{64}\Z")
DEFAULT_PREFIX = "analysis/pro-recommendations/modes/current"


class ProUnavailableError(RuntimeError):
    pass


class ProPlayerNotFoundError(LookupError):
    pass


class ProGenerationChangedError(RuntimeError):
    pass


def pro_blob_prefix() -> str:
    return os.environ.get("PRO_RECOMMENDATIONS_BLOB_PREFIX", DEFAULT_PREFIX).strip().rstrip("/")


def _validate_metadata(payload: Any) -> dict[str, Any]:
    if (not isinstance(payload, dict) or payload.get("schemaVersion") != 2
            or payload.get("method") != METHOD or payload.get("mix") != "Phoenix2"
            or payload.get("scope") != "mode-consolidated" or not isinstance(payload.get("generation"), str)
            or not GENERATION.fullmatch(payload["generation"])):
        raise ProUnavailableError("Pro Farming has no compatible Phoenix 2 capture. Generate the Pro Farming data first.")
    return payload


def read_pro_index(store: Any, *, prefix: str | None = None) -> dict[str, Any]:
    payload = _validate_metadata(store.get_json(f"{prefix or pro_blob_prefix()}/index.json"))
    players = payload.get("players")
    if not isinstance(players, list):
        raise ProUnavailableError("The Pro Farming player list is unavailable.")
    keys: set[str] = set()
    for player in players:
        key = player.get("playerKey") if isinstance(player, dict) else None
        if not isinstance(key, str) or not PLAYER_KEY.fullmatch(key) or key in keys:
            raise ProUnavailableError("The Pro Farming player list is invalid.")
        keys.add(key)
    return payload


def read_pro_profile(store: Any, player_key: str, generation: str | None = None,
                     *, prefix: str | None = None) -> dict[str, Any]:
    if not PLAYER_KEY.fullmatch(player_key):
        raise ValueError("Select a valid Pro Farming player.")
    root = prefix or pro_blob_prefix()
    index = read_pro_index(store, prefix=root)
    if generation and generation != index["generation"]:
        raise ProGenerationChangedError("The Pro Farming capture changed. Reload the player list.")
    if not any(player["playerKey"] == player_key for player in index["players"]):
        raise ProPlayerNotFoundError("This player has no captured Pro Farming profile.")
    payload = _validate_metadata(store.get_json(f"{root}/generations/{index['generation']}/players/{player_key}.json"))
    if (payload["generation"] != index["generation"] or payload.get("snapshotAt") != index.get("snapshotAt")
            or not isinstance(payload.get("player"), dict) or payload["player"].get("playerKey") != player_key):
        raise ProUnavailableError("The Pro Farming profile does not match the current capture.")
    return payload
