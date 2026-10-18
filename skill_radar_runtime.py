"""Read cached consented player evidence and build a bounded public radar."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import re
from threading import RLock
from typing import Mapping

from phoenix2_sync import merge_best_scores
from pumbility_contract import (combined_tier_blob_path, find_player_metadata,
    recommendation_blob_path, recommendation_phoenix2_shard_path, recommendation_player_state_path)
from skill_radar import (METHOD_VERSION, build_skill_radar, build_suggestion_catalog, content_digest)
from tier_scores import _sync_time, public_tier_scores

SKILL_METADATA_PATH = "analysis/private/skill-radar/phoenix2-metadata.json"
PLAYER_KEY = re.compile(r"^[a-f0-9]{20}$")
_cache = OrderedDict()
_catalog_cache = OrderedDict()
_cache_lock = RLock()


class SkillRadarNotFoundError(ValueError):
    pass


def skill_metadata(charts):
    """All chart levels, with no player state or history in the metadata object."""
    fields = ("id", "type", "level", "songName", "bpmMin", "bpmMax")
    return {"schemaVersion": 1, "charts": [{key: row.get(key) for key in fields}
        for row in charts if row.get("type") in ("Single", "Double")]}


def read_skill_radar(store, player_key):
    if not PLAYER_KEY.fullmatch(player_key):
        raise ValueError("A valid playerKey is required.")
    index = store.get_json(recommendation_blob_path())
    metadata = find_player_metadata(index or {}, player_key)
    if metadata is None:
        raise SkillRadarNotFoundError("The selected player was not found.")
    generation, player_id = str(index.get("generationKey") or ""), str(metadata.get("internalPlayerId") or "")
    shard_number = metadata.get("inputShard")
    if not generation or not player_id or isinstance(shard_number, bool) or not isinstance(shard_number, int) or shard_number < 0:
        raise SkillRadarNotFoundError("The selected player's score snapshot is unavailable.")
    shard = store.get_json(recommendation_phoenix2_shard_path(generation, shard_number))
    base = next((row for row in (shard or {}).get("players", [])
        if isinstance(row, Mapping) and str(row.get("playerId")) == player_id), None)
    live = store.get_json(recommendation_player_state_path(player_key))
    if live is not None and str(live.get("playerId")) != player_id:
        raise RuntimeError("The selected player's score state is invalid.")
    if base is None and live is None:
        raise SkillRadarNotFoundError("The selected player's scores are unavailable.")
    scores = [row for source in (base, live) for row in (source or {}).get("scores", []) if isinstance(row, Mapping)]
    loader = getattr(store, "read_skill_radar_metadata", None)
    all_metadata = loader() if callable(loader) else store.get_json(SKILL_METADATA_PATH)
    if not isinstance(all_metadata, Mapping) or not isinstance(all_metadata.get("charts"), list):
        raise RuntimeError("The all-level Phoenix 2 chart metadata is unavailable.")
    charts = all_metadata["charts"]
    revision_reader = getattr(store, "artifact_revision", None)
    tier_revision = revision_reader(combined_tier_blob_path()) if callable(revision_reader) else None
    catalog_key = content_digest({"method": METHOD_VERSION, "metadata": charts, "tier": tier_revision}) if tier_revision else None
    with _cache_lock:
        catalog = _catalog_cache.get(catalog_key) if catalog_key else None
    if catalog is None:
        tiers = store.get_json(combined_tier_blob_path())
        if tiers is None or not any("sssplus" in row.get("pumbilityVariants", {}) for mode in ("singles", "doubles") for row in tiers.get(mode, [])):
            raise RuntimeError("The SSS+ Pumbility analysis is unavailable.")
        catalog = build_suggestion_catalog(tiers, charts)
        if catalog_key:
            with _cache_lock:
                _catalog_cache[catalog_key] = catalog
                _catalog_cache.move_to_end(catalog_key)
                while len(_catalog_cache) > 8:
                    _catalog_cache.popitem(last=False)
    revision = content_digest({"method": METHOD_VERSION, "playerKey": player_key, "scores": scores,
        "metadata": catalog["metadataRevision"], "tier": catalog["tierIdentity"],
        "playerRevision": [(source or {}).get("lastSyncedAtUtc") for source in (base, live)],
        "tierGeneratedAtUtc": catalog["tierGeneratedAtUtc"], "displayName": metadata.get("displayName") or metadata.get("username")})
    with _cache_lock:
        cached = _cache.get(revision)
        if cached is not None:
            _cache.move_to_end(revision)
            return cached
    allowed = {str(row.get("id", row.get("chartId"))) for row in charts}
    personal = public_tier_scores(merge_best_scores([], scores, player_id=player_id), allowed)
    profile = build_skill_radar(charts, scores, catalog, player_key=player_key,
        display_name=str(metadata.get("displayName") or metadata.get("username") or "Player"),
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        player_synced_at_utc=_sync_time((base or {}).get("lastSyncedAtUtc"), (live or {}).get("lastSyncedAtUtc")),
        personal_scores=personal)
    with _cache_lock:
        _cache[revision] = profile
        _cache.move_to_end(revision)
        while len(_cache) > 64:
            _cache.popitem(last=False)
    return profile
