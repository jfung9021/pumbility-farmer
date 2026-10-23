"""Public comparison artifacts derived only from validated official leaderboards.

Raw captures and merge provenance stay private. Artifacts expose official game
tags, opaque player keys, current chart metadata and maximum raw scores only.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from official_score_merge import merge_official_scores
from phoenix2_pumbility import grade_for_score, phoenix2_pumbility
from song_bpm_metadata import bpm_bucket_index, bpm_metadata_identity, enrich_chart_bpm
from skill_radar import validate_bpm_policy

ROOT = Path(__file__).resolve().parent
DEFAULT_COMPARISON_DIR = ROOT / ".local-data/piu-scores/official/comparison/current"
DEFAULT_CHART_METADATA = ROOT / ".local-data/piu-scores/phoenix2/current/charts.json"
DEFAULT_BLOB_PREFIX = "analysis/official-comparison/current"
BPM_BUCKET_LABELS = ("<150", "150–159", "160–169", "170–179", "180–189", "190–199", "200–209", "210–219", "220+")
MODES = {"Single": "singles", "Double": "doubles"}
PLAYER_KEY = re.compile(r"op_[0-9a-f]{64}\Z")
GENERATION = re.compile(r"[0-9a-f]{64}\Z")
SCHEMA_VERSION = 2
FOCAL_SCHEMA_VERSION = 3
MINIMUM_OFFICIAL_SCORES = 200
METHOD = {
    "version": "official-comparison-sg-perfect-pg-v2",
    "scope": "Current Singles and Doubles charts at official level 20 or above",
    "scorePolicy": "Highest raw official Phoenix/Phoenix2 score per deterministically matched player and chart",
    "plateAssumption": "SG below 1,000,000; PG at exactly 1,000,000",
    "gradePolicy": "Phoenix 2 grade derived from raw score",
    "aggregation": "Top five unique chart Pumbility values per mode/BPM bucket; missing slots count as zero",
    "emptyBuckets": "No official data; PG and radar score are null",
    "bpmPolicy": "Entire valid positive BPM interval must fit one bucket",
    "bpmBuckets": list(BPM_BUCKET_LABELS),
    "doublesDisplayOffset": -1,
    "radarMinimumDisplayPg": 10,
    "radarMaximumDisplayPg": 25,
    "estimated": True,
}
FOCAL_METHOD = {**METHOD, "version": "official-comparison-sg-perfect-pg-v3", "bpmPolicy": "focal-v1"}


def _encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def bpm_bucket(bpm_min: Any = None, bpm_max: Any = None, *, bpm_focal: Any = None, bpm_policy=None) -> int | None:
    """Whole-interval, lower-inclusive/upper-exclusive BPM classification."""
    if validate_bpm_policy(bpm_policy):
        return bpm_bucket_index(bpm_focal)
    low, high = _finite(bpm_min), _finite(bpm_max)
    if low is None or high is None or low <= 0 or high < low:
        return None

    def index(value: float) -> int:
        return 0 if value < 150 else min(8, int((value - 150) // 10) + 1)

    return index(low) if index(low) == index(high) else None


def aggregate_bpm_skill(charts: Sequence[Mapping[str, Any]], scores: Mapping[str, int], *, bpm_policy=None) -> list[dict[str, Any]]:
    """Pure top-five aggregation, using integer cents before PG inversion."""
    evidence: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    titles = {chart["chartId"]: str(chart.get("songName") or "").casefold() for chart in charts}
    for chart in charts:
        cid, mode = chart["chartId"], chart["mode"]
        score = scores.get(cid)
        bucket = bpm_bucket(chart.get("bpmMin"), chart.get("bpmMax"), bpm_focal=chart.get("bpmFocal"), bpm_policy=bpm_policy)
        if mode not in ("singles", "doubles") or type(score) is not int or not 0 <= score <= 1_000_000 or bucket is None:
            continue
        plate = "Perfect Game" if score == 1_000_000 else "Superb Game"
        value = phoenix2_pumbility("Single" if mode == "singles" else "Double", int(chart["level"]), grade_for_score(score), plate)
        # The formula already floors to cents; recover its exact integer value.
        evidence[(mode, bucket)][cid] = {"chartId": cid, "score": score,
                                        "pumbilityCents": int(round(value * 100)),
                                        "assumedPlate": "PG" if score == 1_000_000 else "SG"}
    result = []
    for mode in ("singles", "doubles"):
        for bucket in range(9):
            values = sorted(evidence[(mode, bucket)].values(),
                            key=lambda row: (-row["pumbilityCents"], titles[row["chartId"]], row["chartId"]))
            selected = values[:5]
            pg = radar_score = None
            if selected:
                total = sum(row["pumbilityCents"] for row in selected) / 100
                base = (total / 5) / (760 / 750)
                effective_level = base / 7.5 - 27 if base <= 375 else 23 + (base - 375) / 15
                offset = 1 if mode == "doubles" else 0
                raw_pg = max(0, effective_level + offset) if total > 0 else 0
                pg = max(0, raw_pg - offset)
                radar_score = max(0, min(100, 100 * (pg - 10) / 15))
            result.append({"mode": mode, "bucketIndex": bucket, "pg": pg, "radarScore": radar_score,
                           "recordedCount": len(values), "contributingCount": len(selected),
                           "contributors": selected})
    return result


def _chart_metadata(current: Mapping[str, Mapping[str, Any]], metadata: Sequence[Mapping[str, Any]], songs: Sequence[Mapping[str, Any]], *, bpm_policy=None) -> list[dict[str, Any]]:
    if validate_bpm_policy(bpm_policy):
        by_id = {str(row.get("id", row.get("chartId", ""))): row for row in metadata}
        by_song = defaultdict(list)
        for song in songs:
            by_song[str(song.get("name", song.get("songName", "")))].append(song)
        result = []
        for cid, chart in current.items():
            source = {**by_id.get(cid, {}), **chart}
            # Preserve available variant identity from the current chart or ID join.
            if not source.get("songType"):
                source.pop("songType", None)
            for field in ("bpmMin", "bpmMax", "bpmFocal"):
                if source.get(field) is None:
                    source[field] = by_id.get(cid, {}).get(field)
            candidates = by_song.get(str(source.get("songName") or ""), [])
            if source.get("songType"):
                candidates = [song for song in candidates
                    if song.get("songType", song.get("type")) == source["songType"]]
            if len(candidates) == 1:
                song = candidates[0]
                if not source.get("songType"):
                    source["songType"] = song.get("songType", song.get("type"))
                interval = song.get("bpm")
                if isinstance(interval, Mapping) and source.get("bpmMin") is None and source.get("bpmMax") is None:
                    source.update(bpmMin=interval.get("min"), bpmMax=interval.get("max"))
                if source.get("bpmFocal") is None:
                    source["bpmFocal"] = song.get("bpmFocal")
            resolved = enrich_chart_bpm(source, mix="Phoenix2")
            mode = MODES[chart["type"]]
            result.append({"chartId": cid, "songName": str(source.get("songName") or "Untitled chart"),
                "songType": resolved.get("songType"),
                "chartLabel": str(chart.get("difficulty") or ("S" if mode == "singles" else "D") + str(chart["level"])),
                "mode": mode, "level": chart["level"],
                **{field: _finite(resolved.get(field)) for field in ("bpmMin", "bpmMax", "bpmFocal")}})
        return sorted(result, key=lambda row: (row["songName"].casefold(), row["mode"], row["level"], row["chartId"]))
    by_id = {str(row.get("id", row.get("chartId", ""))): row for row in metadata}
    # Joining by exact song name also covers a new chart for an existing song.
    by_name: dict[str, tuple[Any, Any]] = {}
    for row in metadata:
        low, high = _finite(row.get("bpmMin")), _finite(row.get("bpmMax"))
        if low is not None and high is not None and low > 0 and high >= low:
            by_name[str(row.get("songName", ""))] = (row.get("bpmMin"), row.get("bpmMax"))
    for row in songs:
        bpm = row.get("bpm")
        if isinstance(bpm, Mapping):
            by_name[str(row.get("name", ""))] = (bpm.get("min"), bpm.get("max"))
    result = []
    for cid, chart in current.items():
        enriched = by_id.get(cid, {})
        name = str(chart.get("songName") or enriched.get("songName") or "Untitled chart")
        low, high = chart.get("bpmMin"), chart.get("bpmMax")
        if low is None or high is None:
            low, high = enriched.get("bpmMin"), enriched.get("bpmMax")
        if low is None or high is None:
            low, high = by_name.get(name, (None, None))
        mode = MODES[chart["type"]]
        result.append({"chartId": cid, "songName": name,
                       "chartLabel": str(chart.get("difficulty") or ("S" if mode == "singles" else "D") + str(chart["level"])),
                       "mode": mode, "level": chart["level"], "bpmMin": _finite(low), "bpmMax": _finite(high)})
    return sorted(result, key=lambda row: (row["songName"].casefold(), row["mode"], row["level"], row["chartId"]))


def build_official_comparison(manifest: Mapping[str, Any], *, chart_metadata: Sequence[Mapping[str, Any]] = (),
                              songs: Sequence[Mapping[str, Any]] = (), generated_at: datetime | None = None,
                              bpm_policy=None) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Build a compact public index and profiles without exposing raw identities."""
    focal = validate_bpm_policy(bpm_policy)
    identity = bpm_metadata_identity() if focal else {}
    method = FOCAL_METHOD if focal else METHOD
    schema_version = FOCAL_SCHEMA_VERSION if focal else SCHEMA_VERSION
    merged = merge_official_scores(manifest)
    charts = _chart_metadata(manifest["_currentCharts"], chart_metadata, songs, bpm_policy=focal)
    tags: dict[tuple[str, int], set[str]] = defaultdict(set)
    for mix, capture in manifest["_captures"].items():
        for board in capture["boards"].values():
            for row in board["entries"]:
                if row.get("gameTag"):
                    tags[(mix, row["playerId"])].add(row["gameTag"])
    scores: dict[str, dict[str, int]] = defaultdict(dict)
    names: dict[str, set[tuple[int, str]]] = defaultdict(set)
    for cid, board in merged["boards"].items():
        for canonical_key, score in board.items():
            key = "op_" + canonical_key.split(":", 1)[1]
            scores[key][cid] = score
            for observation in merged["_privateProvenance"][cid][canonical_key]["observations"]:
                mix = observation["mix"]
                for tag in tags[(mix, observation["playerId"])]:
                    names[key].add((0 if mix == "Phoenix2" else 1, tag))
    display_names = {key: min(names[key])[1] if names[key] else "Unnamed official player · " + key[3:11] for key in scores}
    players = sorted(({"playerKey": key, "displayName": display_names[key], "recordedScoreCount": len(player_scores)}
                      for key, player_scores in scores.items()), key=lambda row: (row["displayName"].casefold(), row["playerKey"]))
    generation_input = {"manifest": merged["manifestSha256"], "charts": charts, "method": method}
    if focal:
        generation_input["bpmMetadata"] = identity
    generation = hashlib.sha256(_encoded(generation_input)).hexdigest()
    now = generated_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Comparison generation timestamp must be timezone-aware.")
    index = {"schemaVersion": schema_version, **identity, "generation": generation, "generatedAtUtc": now.astimezone(timezone.utc).isoformat(),
             "sourceDates": merged["sourceDates"], "method": dict(method), "players": players, "charts": charts}
    profiles = {}
    for key, player_scores in scores.items():
        buckets = aggregate_bpm_skill(charts, player_scores, bpm_policy=focal)
        profiles[key] = {"schemaVersion": schema_version, **identity, "generation": generation, "playerKey": key, "displayName": display_names[key],
                         "buckets": buckets, "scores": dict(sorted(player_scores.items())), "recordedScoreCount": len(player_scores),
                         "radarEligibleScoreCount": sum(row["recordedCount"] for row in buckets)}
    return index, profiles


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_encoded(value))
    os.replace(temporary, path)


def write_comparison_artifacts(index: Mapping[str, Any], profiles: Mapping[str, Mapping[str, Any]], output_dir: Path = DEFAULT_COMPARISON_DIR) -> None:
    """Write immutable generation profiles before atomically publishing the index."""
    generation = index["generation"]
    if not isinstance(generation, str) or not GENERATION.fullmatch(generation):
        raise ValueError("Invalid comparison generation.")
    if set(profiles) != {row.get("playerKey") for row in index.get("players", [])}:
        raise ValueError("The comparison generation must include every player profile.")
    for key, profile in profiles.items():
        if not PLAYER_KEY.fullmatch(key) or profile.get("generation") != generation or profile.get("playerKey") != key:
            raise ValueError("Invalid comparison player profile.")
        if profile.get("schemaVersion") != index.get("schemaVersion") or (index.get("schemaVersion") == FOCAL_SCHEMA_VERSION
                and any(profile.get(field) != index.get(field) for field in ("bpmMetadataVersion", "bpmPolicy", "bpmMetadataRevision"))):
            raise ValueError("The comparison profile BPM contract does not match its index.")
    for key, profile in profiles.items():
        _atomic_json(Path(output_dir) / "generations" / generation / "players" / f"{key}.json", profile)
    _atomic_json(Path(output_dir) / "index.json", index)


class ComparisonUnavailableError(RuntimeError):
    pass


class ComparisonPlayerNotFoundError(LookupError):
    pass


class ComparisonGenerationChangedError(RuntimeError):
    pass


def comparison_blob_prefix(bpm_policy=None) -> str:
    focal = validate_bpm_policy(bpm_policy)
    prefix = os.environ.get("OFFICIAL_COMPARISON_BLOB_PREFIX", DEFAULT_BLOB_PREFIX).strip().rstrip("/")
    return f"{prefix}/{focal}" if focal else prefix


def read_comparison_index(store: Any, *, prefix: str | None = None, bpm_policy=None) -> dict[str, Any]:
    focal = validate_bpm_policy(bpm_policy)
    payload = store.get_json(f"{prefix or comparison_blob_prefix(focal)}/index.json")
    if not isinstance(payload, dict) or payload.get("schemaVersion") != (FOCAL_SCHEMA_VERSION if focal else SCHEMA_VERSION) or not isinstance(payload.get("generation"), str) or not GENERATION.fullmatch(payload["generation"]):
        raise ComparisonUnavailableError("Official leaderboard comparison has not been generated yet.")
    if focal and (payload.get("bpmPolicy") != focal or payload.get("bpmMetadataVersion") != 1
            or not isinstance(payload.get("bpmMetadataRevision"), str) or not GENERATION.fullmatch(payload["bpmMetadataRevision"])):
        raise ComparisonUnavailableError("The official comparison BPM metadata is invalid.")
    players = payload.get("players")
    if not isinstance(players, list) or any(not isinstance(row, dict)
            or type(row.get("recordedScoreCount")) is not int or row["recordedScoreCount"] < 0 for row in players):
        raise ComparisonUnavailableError("Rebuild the official comparison player list with recorded score counts.")
    return {**payload, "players": [row for row in players if row["recordedScoreCount"] >= MINIMUM_OFFICIAL_SCORES]}


def read_comparison_profile(store: Any, player_key: str, generation: str | None = None, *, prefix: str | None = None, bpm_policy=None) -> dict[str, Any]:
    if not PLAYER_KEY.fullmatch(player_key):
        raise ValueError("Select a valid official leaderboard player.")
    focal = validate_bpm_policy(bpm_policy)
    artifact_prefix = prefix or comparison_blob_prefix(focal)
    index = read_comparison_index(store, prefix=artifact_prefix, bpm_policy=focal)
    if generation and generation != index["generation"]:
        raise ComparisonGenerationChangedError("The official leaderboard comparison changed. Reload the player list.")
    if not any(row.get("playerKey") == player_key for row in index.get("players", [])):
        raise ComparisonPlayerNotFoundError("The selected official leaderboard player was not found.")
    payload = store.get_json(f"{artifact_prefix}/generations/{index['generation']}/players/{player_key}.json")
    if not isinstance(payload, dict) or payload.get("schemaVersion") != (FOCAL_SCHEMA_VERSION if focal else SCHEMA_VERSION) or payload.get("generation") != index["generation"] or payload.get("playerKey") != player_key:
        raise ComparisonUnavailableError("The selected official leaderboard profile is unavailable.")
    if focal and any(payload.get(key) != index.get(key) for key in bpm_metadata_identity()):
        raise ComparisonUnavailableError("The official comparison BPM metadata revisions do not match.")
    return payload
