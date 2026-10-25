"""Corrected display intervals and independently selected operational BPMs.

Only the compact generated catalog is read at runtime.  Exact song variants are
deliberate: a Full Song, Short Cut or remix never inherits an Arcade decision.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from mix_registry import resolve_mix


ROOT = Path(__file__).resolve().parent
RUNTIME_PATH = ROOT / "config" / "phoenix2-song-bpm-runtime.json"
BPM_METADATA_VERSION = 1
BPM_POLICY = "focal-v1"
SONG_TYPES = frozenset({"Arcade", "FullSong", "ShortCut", "Remix"})
SOURCE_FILENAMES = (
    "phoenix2-song-bpm.json",
    "phoenix2-song-bpm-overrides.json",
    "phoenix2-song-primary-bpm-research.json",
    "phoenix2-song-primary-bpm-overrides.json",
)


class BpmMetadataError(ValueError):
    """Invalid metadata or an unsafe song/variant match."""


def _number(value: Any, label: str) -> int | float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise BpmMetadataError(f"{label} must be a positive finite non-bool number")
    return int(value) if float(value).is_integer() else float(value)


def _interval(value: Any, label: str, *, nullable: bool = False) -> tuple[Any, Any]:
    if nullable and (value is None or value == {"min": None, "max": None}):
        return None, None
    if not isinstance(value, Mapping) or set(value) != {"min", "max"}:
        raise BpmMetadataError(f"{label} requires min and max")
    low = _number(value["min"], label + ".min")
    high = _number(value["max"], label + ".max")
    if low > high:
        raise BpmMetadataError(f"{label} endpoints are reversed")
    return low, high


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BpmMetadataError(f"{label} must be a nonempty exact string")
    return value


def _key(name: Any, song_type: Any) -> tuple[str, str]:
    name = _text(name, "songName")
    if not isinstance(song_type, str) or song_type not in SONG_TYPES:
        raise BpmMetadataError(f"Invalid songType {song_type!r} for {name!r}")
    return name, song_type


def _schema(document: Any, label: str, *, legacy_mix: bool = False) -> None:
    if (not isinstance(document, Mapping)
            or type(document.get("schemaVersion")) is not int
            or document.get("schemaVersion") != 1):
        raise BpmMetadataError(f"{label} schemaVersion must be 1")
    if document.get("mix") != "Phoenix2" and not (legacy_mix and "mix" not in document):
        raise BpmMetadataError(f"{label} mix must be Phoenix2")


def _unique_rows(rows: Any, label: str, name_key: str, type_key: str) -> dict:
    if not isinstance(rows, list):
        raise BpmMetadataError(f"{label} songs must be a list")
    result = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise BpmMetadataError(f"{label} contains a non-object song")
        key = _key(row.get(name_key), row.get(type_key))
        if key in result:
            raise BpmMetadataError(f"Duplicate {label} song {key!r}")
        result[key] = row
    return result


def _override_rows(document: Mapping, catalog: Mapping, label: str) -> dict:
    rows = document.get("songs")
    if not isinstance(rows, Mapping):
        raise BpmMetadataError(f"{label} songs must be an exact-name mapping")
    names = defaultdict(list)
    for key in catalog:
        names[key[0]].append(key)
    result = {}
    for name, row in rows.items():
        _text(name, label + " songName")
        if not isinstance(row, Mapping):
            raise BpmMetadataError(f"Invalid {label} for {name!r}")
        candidates = names.get(name, [])
        if row.get("songType") is not None:
            candidates = [key for key in candidates if key[1] == row["songType"]]
        if len(candidates) != 1:
            raise BpmMetadataError(f"Unknown or ambiguous {label} song {name!r}")
        if row.get("confirmedBy") != "user":
            raise BpmMetadataError(f"{label} requires explicit user confirmation: {name!r}")
        result[candidates[0]] = row
    return result


def _semantic_revision(songs: Iterable[Mapping]) -> str:
    semantic = {
        "mix": "Phoenix2", "bpmMetadataVersion": BPM_METADATA_VERSION,
        "bpmPolicy": BPM_POLICY,
        "songs": [
            {field: row[field] for field in ("songName", "songType", "bpmMin", "bpmMax", "bpmFocal")}
            for row in sorted(songs, key=lambda row: (row["songName"], row["songType"]))
        ],
    }
    encoded = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compile_runtime_catalog(
    catalog: Mapping, interval_overrides: Mapping, research: Mapping,
    focal_overrides: Mapping, *, source_hashes: Mapping | None = None,
) -> dict:
    """Validate all four authority documents and compile their effective values."""
    for document, label, legacy in (
        (catalog, "corrected catalog", False), (interval_overrides, "interval overrides", True),
        (research, "focal research", False), (focal_overrides, "focal overrides", False),
    ):
        _schema(document, label, legacy_mix=legacy)
    if source_hashes:
        for document, field, filename in (
            (catalog, "manualOverridesSha256", SOURCE_FILENAMES[1]),
            (research, "sourceCatalogSha256", SOURCE_FILENAMES[0]),
            (research, "manualPrimaryOverridesSha256", SOURCE_FILENAMES[3]),
        ):
            if document.get(field) is not None and document[field] != source_hashes.get(filename):
                raise BpmMetadataError(f"Authority hash {field} disagrees with {filename}")
    catalog_rows = _unique_rows(catalog.get("songs"), "corrected catalog", "name", "type")
    if not catalog_rows:
        raise BpmMetadataError("The corrected catalog is empty")
    researched = _unique_rows(research.get("songs"), "focal research", "songName", "songType")
    if set(researched) - set(catalog_rows):
        raise BpmMetadataError("Focal research references an unknown exact song variant")
    interval_rows = _override_rows(interval_overrides, catalog_rows, "interval override")
    focal_rows = _override_rows(focal_overrides, catalog_rows, "focal override")
    output = []
    for key, original in sorted(catalog_rows.items()):
        # Validate the original even when a higher-priority override exists.
        _interval(original.get("bpm"), f"catalog interval {key!r}")
        low, high = _interval(interval_rows.get(key, original).get("bpm"), f"effective interval {key!r}")
        row = researched.get(key)
        proposal = None
        direction = None
        if row is not None:
            if _interval(row.get("originalBpmRange"), f"research interval {key!r}") != (low, high):
                raise BpmMetadataError(f"Research interval disagrees with corrected interval for {key!r}")
            proposal = _number(row.get("recommendedPrimaryBpm"), f"research focal {key!r}")
            if not low <= proposal <= high:
                raise BpmMetadataError(f"Research focal outside interval for {key!r}")
            if row.get("selectionStatus") in {"user-directed", "user-preferred"}:
                stored_direction = row.get("userDirection")
                direction = _number(
                    stored_direction.get("recommendedPrimaryBpm") if isinstance(stored_direction, Mapping) else proposal,
                    f"retained user focal {key!r}",
                )
                if not low <= direction <= high:
                    raise BpmMetadataError(f"Retained user focal outside interval for {key!r}")
        if key in focal_rows:
            focal = _number(focal_rows[key].get("recommendedPrimaryBpm"), f"override focal {key!r}")
            focal_source, status = "user-override", "user-directed"
        elif direction is not None:
            focal, focal_source, status = direction, "retained-user-direction", row["selectionStatus"]
        elif proposal is not None:
            focal, focal_source, status = proposal, "researched-proposal", row.get("selectionStatus", "researched-proposal")
        elif low == high:
            focal, focal_source, status = low, "corrected-fixed-bpm", "fixed"
        else:
            raise BpmMetadataError(f"Missing focal decision for catalog range {key!r}")
        if not low <= focal <= high:
            raise BpmMetadataError(f"Effective focal outside interval for {key!r}")
        if low == high and focal != low:
            raise BpmMetadataError(f"Fixed BPM focal disagrees for {key!r}")
        output.append({
            "songName": key[0], "songType": key[1], "bpmMin": low, "bpmMax": high,
            "bpmFocal": focal, "selectionStatus": status,
            "intervalSource": "user-override" if key in interval_rows else "corrected-catalog",
            "focalSource": focal_source,
        })
    ranged = {key for key, row in zip(sorted(catalog_rows), output) if row["bpmMin"] != row["bpmMax"]}
    if set(researched) != ranged:
        raise BpmMetadataError("Focal research must cover exactly the corrected variable-tempo songs")
    return {
        "schemaVersion": 1, "mix": "Phoenix2", "bpmMetadataVersion": BPM_METADATA_VERSION,
        "bpmPolicy": BPM_POLICY, "bpmMetadataRevision": _semantic_revision(output),
        "sourceFiles": dict(source_hashes or {}),
        "summary": {"songEntries": len(output), "fixedBpmSongs": len(output) - len(ranged), "rangedBpmSongs": len(ranged)},
        "songs": output,
    }


def validate_runtime_catalog(document: Mapping) -> dict:
    """Fail closed on malformed or semantically stale runtime data."""
    _schema(document, "runtime catalog")
    if (type(document.get("bpmMetadataVersion")) is not int
            or document.get("bpmMetadataVersion") != BPM_METADATA_VERSION
            or document.get("bpmPolicy") != BPM_POLICY):
        raise BpmMetadataError("Unsupported runtime BPM metadata policy/version")
    rows = _unique_rows(document.get("songs"), "runtime catalog", "songName", "songType")
    if not rows:
        raise BpmMetadataError("The runtime catalog is empty")
    normalized = []
    for key, row in rows.items():
        low, high = _interval({"min": row.get("bpmMin"), "max": row.get("bpmMax")}, f"runtime interval {key!r}")
        focal = _number(row.get("bpmFocal"), f"runtime focal {key!r}")
        if not low <= focal <= high:
            raise BpmMetadataError(f"Runtime focal outside interval for {key!r}")
        normalized.append({**row, "bpmMin": low, "bpmMax": high, "bpmFocal": focal})
    if document.get("bpmMetadataRevision") != _semantic_revision(normalized):
        raise BpmMetadataError("Runtime BPM semantic revision does not match its values")
    return dict(document)


@lru_cache(maxsize=4)
def _read_runtime(path: str, modified_ns: int, size: int) -> dict:
    del modified_ns, size  # Stat values invalidate the cache after regeneration.
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BpmMetadataError(f"Cannot load BPM runtime catalog {path}: {error}") from error
    return validate_runtime_catalog(document)


def _runtime(path: str | Path | None = None) -> dict:
    selected = Path(path) if path is not None else RUNTIME_PATH
    try:
        stat = selected.stat()
    except OSError as error:
        raise BpmMetadataError(f"Missing BPM runtime catalog {selected}") from error
    return _read_runtime(str(selected.resolve()), stat.st_mtime_ns, stat.st_size)


def load_runtime_catalog(path: str | Path | None = None) -> dict:
    return deepcopy(_runtime(path))


def bpm_metadata_identity() -> dict:
    document = _runtime()
    return {key: document[key] for key in ("bpmMetadataVersion", "bpmPolicy", "bpmMetadataRevision")}


def _indexes(document: Mapping) -> tuple[dict, dict]:
    by_key = {(row["songName"], row["songType"]): row for row in document["songs"]}
    by_name = defaultdict(list)
    for key in by_key:
        by_name[key[0]].append(key)
    return by_key, by_name


def _resolve(name: str, song_type: str | None, indexes: tuple[dict, dict]) -> dict | None:
    by_key, by_name = indexes
    choices = by_name.get(name, [])
    if song_type is not None:
        _key(name, song_type)
        match = by_key.get((name, song_type))
        if match is None and choices:
            raise BpmMetadataError(f"Supplied songType {song_type!r} mismatches catalog song {name!r}")
        return match
    if len(choices) > 1:
        raise BpmMetadataError(f"Ambiguous song variant for {name!r}; songType is required")
    return by_key[choices[0]] if choices else None


def resolve_song_bpm(song_name: str, song_type: str | None = None, mix: Any = "Phoenix2", *, catalog: Mapping | None = None) -> dict | None:
    """Resolve one exact current-P2 song, recovering a unique legacy variant."""
    if resolve_mix(mix).api_value != "Phoenix2":
        return None
    document = _runtime() if catalog is None else validate_runtime_catalog(catalog)
    row = _resolve(_text(song_name, "songName"), song_type, _indexes(document))
    return dict(row) if row is not None else None


def _enrich_chart(chart: Mapping, indexes: tuple[dict, dict]) -> dict:
    result = dict(chart)
    name = chart.get("songName", chart.get("name"))
    song_type = chart.get("songType")
    if song_type is None and chart.get("type") in SONG_TYPES:
        song_type = chart["type"]
    if name is not None:
        resolved = _resolve(_text(name, "songName"), song_type, indexes)
        if resolved is not None:
            result.update({field: resolved[field] for field in ("songType", "bpmMin", "bpmMax", "bpmFocal")})
            return result
    elif song_type is not None:
        raise BpmMetadataError("Cannot resolve songType without an exact songName")
    if "bpmMin" in chart or "bpmMax" in chart:
        interval = {"min": chart.get("bpmMin"), "max": chart.get("bpmMax")}
    else:
        interval = chart.get("bpm")
    low, high = _interval(interval, "unmatched chart BPM", nullable=True)
    focal = chart.get("bpmFocal")
    if focal is not None:
        focal = _number(focal, "unmatched chart focal")
        if low is None or not low <= focal <= high:
            raise BpmMetadataError("Unmatched chart focal must be inside a valid display interval")
    elif low is not None and low == high:
        focal = low
    result.update({"bpmMin": low, "bpmMax": high, "bpmFocal": focal})
    if song_type is not None:
        result["songType"] = song_type
    return result


class BpmMetadataView:
    """One validated catalog and exact-variant lookup for an entire payload."""

    def __init__(self, mix: Any = "Phoenix2", *, catalog: Mapping | None = None):
        document = _runtime() if catalog is None else validate_runtime_catalog(catalog)
        self.identity = {key: document[key] for key in (
            "bpmMetadataVersion", "bpmPolicy", "bpmMetadataRevision")}
        self._indexes = _indexes(document) if resolve_mix(mix).api_value == "Phoenix2" else None

    def enrich_chart(self, chart: Mapping) -> dict:
        return _enrich_chart(chart, self._indexes) if self._indexes is not None else dict(chart)


def enrich_chart_bpm(chart: Mapping, mix: Any = "Phoenix2", *, catalog: Mapping | None = None) -> dict:
    return enrich_charts_bpm([chart], mix=mix, catalog=catalog)[0]


def enrich_charts_bpm(charts: Iterable[Mapping], mix: Any = "Phoenix2", *, catalog: Mapping | None = None) -> list[dict]:
    """Copy rows with full display ranges plus focal values, without rekeying IDs."""
    if resolve_mix(mix).api_value != "Phoenix2":
        return [dict(chart) for chart in charts]
    view = BpmMetadataView(mix, catalog=catalog)
    return [view.enrich_chart(chart) for chart in charts]


def bpm_bucket_index(value: Any) -> int | None:
    """Scalar half-open radar bins; invalid/unknown values have no category."""
    try:
        focal = _number(value, "focal BPM")
    except BpmMetadataError:
        return None
    if focal < 150:
        return 0
    if focal >= 220:
        return 8
    return 1 + int((focal - 150) // 10)
