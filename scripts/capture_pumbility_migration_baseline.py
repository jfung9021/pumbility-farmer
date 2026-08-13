#!/usr/bin/env python3
"""Capture a privacy-safe Pumbility migration baseline.

The committed migration contract deliberately does not include raw player identifiers,
usernames, score rows, or per-player digests. Public artifacts use SHA-256. Private
datasets use HMAC-SHA256 with a key supplied only through the environment.

The production command uses the existing private Vercel adapter, but constructs it lazily
only after the operator explicitly selects that command. Its bounded reader accepts a
boundary only when all active pointers and two reads of the mutable Phoenix 2 snapshot
are exactly equal. Tests inject in-memory stores; importing this module never accesses a
credential or network.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = PROJECT_ROOT / ".local-data" / "piu-scores"
DEFAULT_EVIDENCE_ROOT = PROJECT_ROOT / ".local-data" / "pumbility-migration"
MANIFEST_SCHEMA_VERSION = 1
MINIMUM_HMAC_KEY_BYTES = 32
DEFAULT_PRODUCTION_ATTEMPTS = 3
MAXIMUM_PRODUCTION_ATTEMPTS = 10

PHOENIX2_ANALYSIS_POINTER = "analysis/phoenix2/latest.json"
COMBINED_TIER_POINTER = "analysis/combined/latest.json"
RECOMMENDATION_POINTER = "analysis/recommendations/latest.json"
PHOENIX1_PRIVATE_SNAPSHOT = "analysis/private/phoenix1.json"
PHOENIX2_PRIVATE_SNAPSHOT = "analysis/private/phoenix2-current.json"
BOUNDARY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
GENERATION_KEY_PATTERN = re.compile(r"^[0-9a-f]{20}$")

SECRET_PATTERN = re.compile(
    r"(?:piu_scores_live_|pst_live_)[0-9a-f]{16,}", re.IGNORECASE
)
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE
)
PUBLIC_PLAYER_KEY_PATTERN = re.compile(r"^[0-9a-f]{20}$", re.IGNORECASE)
FORBIDDEN_OUTPUT_KEYS = {
    "apiKey",
    "authorization",
    "displayName",
    "email",
    "gameTag",
    "internalPlayerId",
    "playerId",
    "rawScore",
    "score",
    "scores",
    "token",
    "userId",
    "username",
}
FORBIDDEN_PUBLIC_KEYS = FORBIDDEN_OUTPUT_KEYS | {
    "plateScores",
}
VOLATILE_ANALYSIS_KEYS = {
    "captureCompletedAtUtc",
    "captureStartedAtUtc",
    "completedAtUtc",
    "createdAtUtc",
    "generatedAtUtc",
    "modelGeneratedAtUtc",
    "playerSyncedAtUtc",
    "recommendationsGeneratedAtUtc",
    "sourceGeneratedAtUtc",
    "startedAtUtc",
    "updatedAtUtc",
}


class BaselineCaptureError(RuntimeError):
    """Raised when a safe and internally consistent baseline cannot be produced."""


class ProductionReadStore(Protocol):
    """Read-only seam shared by Vercel and the in-memory production-reader tests."""

    def get_json(self, pathname: str) -> dict[str, Any] | None: ...

    def get_bytes(self, pathname: str) -> bytes | None: ...


class _MovingProductionBoundary(RuntimeError):
    """Internal retry signal; all other production read failures fail immediately."""


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_value(value: Any) -> Any:
    """Normalize values for representation-independent logical hashing."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise BaselineCaptureError("A logical hash input contained a non-finite number.")
        # Numeric JSON spellings such as 1 and 1.0 are equivalent to the application.
        return {"$number": numeric.hex()}
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(child) for child in value]
    raise BaselineCaptureError(
        f"Unsupported logical hash value type: {value.__class__.__name__}."
    )


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    copied = [dict(row) for row in rows]
    return sorted(copied, key=canonical_bytes)


def public_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def private_hmac_sha256(value: Any, key: bytes) -> str:
    if len(key) < MINIMUM_HMAC_KEY_BYTES:
        raise BaselineCaptureError(
            f"The baseline HMAC key must contain at least {MINIMUM_HMAC_KEY_BYTES} bytes."
        )
    return hmac.new(key, canonical_bytes(value), hashlib.sha256).hexdigest()


def private_bytes_hmac_sha256(value: bytes, key: bytes) -> str:
    if len(key) < MINIMUM_HMAC_KEY_BYTES:
        raise BaselineCaptureError(
            f"The baseline HMAC key must contain at least {MINIMUM_HMAC_KEY_BYTES} bytes."
        )
    return hmac.new(key, value, hashlib.sha256).hexdigest()


def _exact_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Encode decoded JSON for strict boundary comparisons without logical coercion."""
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BaselineCaptureError("A production artifact was not valid finite JSON.") from exc


def _validate_boundary_id(boundary_id: str) -> str:
    value = str(boundary_id or "").strip()
    if not BOUNDARY_ID_PATTERN.fullmatch(value):
        raise BaselineCaptureError(
            "The boundary identifier must be 1-80 letters, numbers, dots, underscores, "
            "or hyphens and must start with a letter or number."
        )
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BaselineCaptureError(f"Required baseline input is missing: {path}") from None
    except json.JSONDecodeError:
        raise BaselineCaptureError(f"Baseline input is not valid JSON: {path}") from None


def _read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise BaselineCaptureError(
                        f"{path} line {line_number} is not a JSON object."
                    )
                rows.append(value)
    except FileNotFoundError:
        raise BaselineCaptureError(f"Required baseline input is missing: {path}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineCaptureError(f"Could not read baseline score rows from {path}.") from exc
    return rows


def _walk_forbidden_keys(value: Any, *, public_payload: bool = False) -> None:
    forbidden = FORBIDDEN_PUBLIC_KEYS if public_payload else FORBIDDEN_OUTPUT_KEYS
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in forbidden:
                raise BaselineCaptureError(
                    f"Privacy scan rejected forbidden field {key!r}."
                )
            _walk_forbidden_keys(child, public_payload=public_payload)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _walk_forbidden_keys(child, public_payload=public_payload)
    elif isinstance(value, str) and SECRET_PATTERN.search(value):
        raise BaselineCaptureError("Privacy scan rejected a credential-shaped value.")


def privacy_scan_manifest(value: Any) -> None:
    """Reject raw/private field names and identifier-shaped output values."""
    _walk_forbidden_keys(value)

    def scan_strings(child: Any) -> None:
        if isinstance(child, Mapping):
            for nested in child.values():
                scan_strings(nested)
        elif isinstance(child, (list, tuple)):
            for nested in child:
                scan_strings(nested)
        elif isinstance(child, str):
            if UUID_PATTERN.fullmatch(child):
                raise BaselineCaptureError("Privacy scan rejected a UUID-shaped value.")
            if PUBLIC_PLAYER_KEY_PATTERN.fullmatch(child):
                raise BaselineCaptureError(
                    "Privacy scan rejected a public-player-key-shaped value."
                )

    scan_strings(value)


def validate_public_payload(value: Any, label: str) -> None:
    try:
        _walk_forbidden_keys(value, public_payload=True)
    except BaselineCaptureError as exc:
        raise BaselineCaptureError(f"Public artifact {label!r} is not privacy-safe: {exc}") from exc


def _semantic_analysis_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _semantic_analysis_payload(child)
            for key, child in value.items()
            if str(key) not in VOLATILE_ANALYSIS_KEYS
        }
    if isinstance(value, list):
        return [_semantic_analysis_payload(child) for child in value]
    return value


def _snapshot_contract(
    current_dir: Path,
    *,
    expected_mix: str,
    hmac_key: bytes,
) -> dict[str, Any]:
    manifest = _read_json(current_dir / "snapshot_manifest.json")
    players = _read_json(current_dir / "players.json")
    charts = _read_json(current_dir / "charts.json")
    scores = _read_jsonl_gz(current_dir / "scores.jsonl.gz")
    if not isinstance(manifest, dict):
        raise BaselineCaptureError(f"Snapshot manifest in {current_dir} is not an object.")
    if not isinstance(players, list) or not all(isinstance(row, dict) for row in players):
        raise BaselineCaptureError(f"Snapshot players in {current_dir} are invalid.")
    if not isinstance(charts, list) or not all(isinstance(row, dict) for row in charts):
        raise BaselineCaptureError(f"Snapshot charts in {current_dir} are invalid.")
    if manifest.get("mix") != expected_mix:
        raise BaselineCaptureError(
            f"Snapshot {current_dir} describes {manifest.get('mix')!r}, expected {expected_mix!r}."
        )

    player_ids = [str(row.get("userId") or "").strip() for row in players]
    chart_ids = [str(row.get("id") or "").strip() for row in charts]
    if any(not value for value in player_ids) or len(set(player_ids)) != len(player_ids):
        raise BaselineCaptureError(f"Snapshot {current_dir} has missing or duplicate players.")
    if any(not value for value in chart_ids) or len(set(chart_ids)) != len(chart_ids):
        raise BaselineCaptureError(f"Snapshot {current_dir} has missing or duplicate charts.")
    player_set = set(player_ids)
    chart_set = set(chart_ids)
    score_keys: set[tuple[str, str]] = set()
    for row in scores:
        player_id = str(row.get("playerId") or "").strip()
        chart_id = str(row.get("chartId") or "").strip()
        key = (player_id, chart_id)
        if player_id not in player_set or chart_id not in chart_set:
            raise BaselineCaptureError(f"Snapshot {current_dir} has an orphan score row.")
        if key in score_keys:
            raise BaselineCaptureError(f"Snapshot {current_dir} has a duplicate best-score key.")
        score_keys.add(key)
    expected_counts = {
        "playerRecords": len(players),
        "chartRecords": len(charts),
        "bestScoreRecords": len(scores),
    }
    source_counts = {
        "playerRecords": manifest.get("players"),
        "chartRecords": manifest.get("charts"),
        "bestScoreRecords": manifest.get("scoreRows"),
    }
    if source_counts != expected_counts:
        raise BaselineCaptureError(
            f"Snapshot {current_dir} counts do not match its source manifest."
        )

    chart_types = {str(row.get("id")): str(row.get("type") or "") for row in charts}
    mode_counts = {
        "singleBestScoreRecords": sum(
            1 for row in scores if chart_types.get(str(row.get("chartId"))) == "Single"
        ),
        "doubleBestScoreRecords": sum(
            1 for row in scores if chart_types.get(str(row.get("chartId"))) == "Double"
        ),
    }
    return {
        "mix": expected_mix,
        "snapshotSchemaVersion": manifest.get("schemaVersion"),
        "scriptVersion": manifest.get("scriptVersion"),
        "sourceBoundaryUtc": manifest.get("captureCompletedAtUtc"),
        "counts": {**expected_counts, **mode_counts},
        "logicalHashes": {
            "catalogSha256": public_sha256(canonical_rows(charts)),
            "consentSetHmacSha256": private_hmac_sha256(
                sorted(player_ids), hmac_key
            ),
            "playerRecordsHmacSha256": private_hmac_sha256(
                canonical_rows(players), hmac_key
            ),
            "bestScoreKeysHmacSha256": private_hmac_sha256(
                sorted(score_keys), hmac_key
            ),
            "bestScoreRecordsHmacSha256": private_hmac_sha256(
                canonical_rows(scores), hmac_key
            ),
        },
    }


def _snapshot_payload_contract(
    payload: Mapping[str, Any],
    *,
    expected_mix: str,
    hmac_key: bytes,
    label: str,
) -> dict[str, Any]:
    if payload.get("mix") != expected_mix:
        raise BaselineCaptureError(f"The {label} has unexpected mix metadata.")
    players = payload.get("players")
    charts = payload.get("charts")
    scores = payload.get("scores")
    if not isinstance(players, list) or not all(isinstance(row, Mapping) for row in players):
        raise BaselineCaptureError(f"The {label} has invalid player records.")
    if not isinstance(charts, list) or not all(isinstance(row, Mapping) for row in charts):
        raise BaselineCaptureError(f"The {label} has invalid chart records.")
    if not isinstance(scores, list) or not all(isinstance(row, Mapping) for row in scores):
        raise BaselineCaptureError(f"The {label} has invalid score records.")

    player_rows = [dict(row) for row in players]
    chart_rows = [dict(row) for row in charts]
    score_rows = [dict(row) for row in scores]
    player_ids = [
        str(row.get("playerId", row.get("userId")) or "").strip()
        for row in player_rows
    ]
    chart_ids = [str(row.get("id") or "").strip() for row in chart_rows]
    if any(not value for value in player_ids) or len(set(player_ids)) != len(player_ids):
        raise BaselineCaptureError(f"The {label} has missing or duplicate players.")
    if any(not value for value in chart_ids) or len(set(chart_ids)) != len(chart_ids):
        raise BaselineCaptureError(f"The {label} has missing or duplicate charts.")

    player_set = set(player_ids)
    chart_set = set(chart_ids)
    score_keys: set[tuple[str, str]] = set()
    for row in score_rows:
        player_id = str(row.get("playerId") or "").strip()
        chart_id = str(row.get("chartId") or "").strip()
        score_key = (player_id, chart_id)
        if player_id not in player_set or chart_id not in chart_set:
            raise BaselineCaptureError(f"The {label} has an orphan score record.")
        if score_key in score_keys:
            raise BaselineCaptureError(f"The {label} has a duplicate best-score key.")
        score_keys.add(score_key)

    chart_types = {str(row.get("id")): str(row.get("type") or "") for row in chart_rows}
    return {
        "mix": expected_mix,
        "snapshotSchemaVersion": payload.get("schemaVersion"),
        "scriptVersion": None,
        "sourceBoundaryUtc": payload.get("generatedAtUtc"),
        "counts": {
            "playerRecords": len(player_rows),
            "chartRecords": len(chart_rows),
            "bestScoreRecords": len(score_rows),
            "singleBestScoreRecords": sum(
                1
                for row in score_rows
                if chart_types.get(str(row.get("chartId"))) == "Single"
            ),
            "doubleBestScoreRecords": sum(
                1
                for row in score_rows
                if chart_types.get(str(row.get("chartId"))) == "Double"
            ),
        },
        "logicalHashes": {
            "catalogSha256": public_sha256(canonical_rows(chart_rows)),
            "consentSetHmacSha256": private_hmac_sha256(sorted(player_ids), hmac_key),
            "playerRecordsHmacSha256": private_hmac_sha256(
                canonical_rows(player_rows), hmac_key
            ),
            "bestScoreKeysHmacSha256": private_hmac_sha256(
                sorted(score_keys), hmac_key
            ),
            "bestScoreRecordsHmacSha256": private_hmac_sha256(
                canonical_rows(score_rows), hmac_key
            ),
        },
    }


def _analysis_payload_contract(payload: Mapping[str, Any], label: str) -> dict[str, Any]:
    validate_public_payload(payload, label)
    singles = payload.get("singles")
    doubles = payload.get("doubles")
    if not isinstance(singles, list) or not isinstance(doubles, list):
        raise BaselineCaptureError(f"Analysis artifact {label!r} has no mode arrays.")
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "schemaVersion": payload.get("schemaVersion"),
        "sourceBoundaryUtc": payload.get("generatedAtUtc"),
        "scriptVersion": summary.get("scriptVersion"),
        "counts": {
            "singleChartResults": len(singles),
            "doubleChartResults": len(doubles),
        },
        "exactSha256": public_sha256(payload),
        "semanticSha256": public_sha256(_semantic_analysis_payload(payload)),
    }


def _analysis_contract(path: Path, label: str) -> dict[str, Any]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise BaselineCaptureError(f"Analysis artifact {path} is not an object.")
    return _analysis_payload_contract(payload, label)


def _public_file_contract(path: Path, *, parse_json: bool = True) -> dict[str, Any]:
    payload = _read_json(path) if parse_json else None
    if parse_json:
        validate_public_payload(payload, path.name)
    return {
        "bytes": path.stat().st_size,
        "fileSha256": file_sha256(path),
        "logicalSha256": public_sha256(payload) if parse_json else None,
    }


def _reference_contracts(project_root: Path) -> dict[str, Any]:
    archive_path = project_root / "public" / "data" / "phoenix1.json"
    archive = _read_json(archive_path)
    validate_public_payload(archive, "frozen Phoenix 1 archive")
    manifest_path = project_root / "public" / "data" / "phoenix1.manifest.json"
    archive_manifest = _read_json(manifest_path)
    rerates_path = project_root / "public" / "data" / "phoenix1-rerates.json"
    rerates = _read_json(rerates_path)
    videos_path = project_root / "lib" / "data" / "nevsister-chart-videos.json"
    videos = _read_json(videos_path)
    overrides_path = project_root / "lib" / "data" / "nevsister-chart-video-overrides.json"
    overrides = _read_json(overrides_path)
    for label, value in (
        ("Phoenix 1 manifest", archive_manifest),
        ("Phoenix 1 rerates", rerates),
        ("chart videos", videos),
        ("chart video overrides", overrides),
    ):
        validate_public_payload(value, label)
    rerate_rows = rerates.get("rerates", []) if isinstance(rerates, dict) else []
    video_rows = videos.get("charts", {}) if isinstance(videos, dict) else {}
    aliases = overrides.get("aliases", {}) if isinstance(overrides, dict) else {}
    manual_charts = overrides.get("charts", {}) if isinstance(overrides, dict) else {}
    notes = overrides.get("notes", {}) if isinstance(overrides, dict) else {}
    return {
        "frozenPhoenix1Archive": {
            **_public_file_contract(archive_path),
            "methodologyVersion": archive_manifest.get("methodologyVersion"),
            "counts": {
                "singleChartResults": len(archive.get("singles", [])),
                "doubleChartResults": len(archive.get("doubles", [])),
            },
        },
        "frozenPhoenix1Manifest": _public_file_contract(manifest_path),
        "phoenix1Rerates": {
            **_public_file_contract(rerates_path),
            "counts": {
                "rerates": len(rerate_rows),
                "uprates": sum(
                    1 for row in rerate_rows if row.get("direction") == "uprated"
                ),
                "downrates": sum(
                    1 for row in rerate_rows if row.get("direction") == "downrated"
                ),
            },
        },
        "chartVideos": {
            **_public_file_contract(videos_path),
            "mappingCount": len(video_rows),
        },
        "chartVideoOverrides": {
            **_public_file_contract(overrides_path),
            "counts": {
                "aliases": len(aliases),
                "manualMappings": len(manual_charts),
                "notes": len(notes),
            },
        },
        "scoreOverrideSource": _public_file_contract(
            project_root / "phoenix1_score_overrides.py", parse_json=False
        ),
        "pumbilityFormulaSource": _public_file_contract(
            project_root / "phoenix2_pumbility.py", parse_json=False
        ),
    }


def _recommendation_contract(data_root: Path, hmac_key: bytes) -> dict[str, Any]:
    recommendation_root = data_root / "recommendations"
    latest_path = recommendation_root / "latest.json"
    payload = _read_json(latest_path)
    if not isinstance(payload, dict):
        raise BaselineCaptureError(f"Recommendation index {latest_path} is not an object.")
    players = payload.get("players")
    if not isinstance(players, list):
        raise BaselineCaptureError("The local recommendation index has no player array.")
    artifact_paths = sorted(
        str(path.relative_to(recommendation_root)).replace("\\", "/")
        for path in recommendation_root.rglob("*")
        if path.is_file() and path != latest_path
    )
    artifact_bytes = sum(
        path.stat().st_size
        for path in recommendation_root.rglob("*")
        if path.is_file() and path != latest_path
    )
    method = payload.get("method") if isinstance(payload.get("method"), dict) else {}
    return {
        "schemaVersion": payload.get("schemaVersion"),
        "storageSchemaVersion": payload.get("storageSchemaVersion"),
        "sourceBoundaryUtc": payload.get("modelGeneratedAtUtc", payload.get("generatedAtUtc")),
        "refreshSupported": bool(payload.get("refreshSupported", False)),
        "counts": {
            "publicPlayerEntries": len(players),
            "referencedArtifacts": len(artifact_paths),
            "referencedArtifactBytes": artifact_bytes,
        },
        "logicalHashes": {
            "methodSha256": public_sha256(method),
            "privateIndexHmacSha256": private_hmac_sha256(payload, hmac_key),
            "artifactInventoryHmacSha256": private_hmac_sha256(
                artifact_paths, hmac_key
            ),
        },
    }


def _code_contract(project_root: Path) -> dict[str, Any]:
    tracked = [
        "analysis_runtime.py",
        "mix_registry.py",
        "phoenix1_score_overrides.py",
        "phoenix2_pumbility.py",
        "phoenix2_sync.py",
        "piu_misgrade_analyzer.py",
        "piu_recommendations.py",
        "recommendation_refresh.py",
        "package-lock.json",
        "uv.lock",
    ]
    hashes = {
        name: file_sha256(project_root / name)
        for name in tracked
        if (project_root / name).is_file()
    }
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        commit = result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    return {
        "gitCommit": commit,
        "sourceFileHashesSha256": public_sha256(hashes),
        "trackedSourceFiles": len(hashes),
    }


def validate_baseline_manifest(manifest: Mapping[str, Any]) -> None:
    required = {
        "schemaVersion",
        "captureStatus",
        "boundary",
        "code",
        "datasets",
        "publicArtifacts",
        "derivedArtifacts",
        "contractCoverage",
        "privacy",
        "gate",
    }
    missing = required - set(manifest)
    if missing:
        raise BaselineCaptureError(
            f"Baseline manifest is missing required fields: {sorted(missing)}"
        )
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        raise BaselineCaptureError("Baseline manifest schema version is unsupported.")
    boundary = manifest.get("boundary")
    if not isinstance(boundary, Mapping) or not str(boundary.get("id") or "").strip():
        raise BaselineCaptureError("Baseline manifest has no boundary identifier.")
    privacy = manifest.get("privacy")
    if not isinstance(privacy, Mapping) or privacy.get("scanResult") != "passed":
        raise BaselineCaptureError("Baseline manifest has no passing privacy result.")
    privacy_scan_manifest(manifest)


def capture_local_baseline(
    *,
    project_root: Path,
    data_root: Path,
    boundary_id: str,
    hmac_key: bytes,
    captured_at_utc: str | None = None,
) -> dict[str, Any]:
    boundary_id = _validate_boundary_id(boundary_id)
    phoenix1 = _snapshot_contract(
        data_root / "phoenix1" / "current",
        expected_mix="Phoenix",
        hmac_key=hmac_key,
    )
    phoenix2 = _snapshot_contract(
        data_root / "phoenix2" / "current",
        expected_mix="Phoenix2",
        hmac_key=hmac_key,
    )
    derived = {
        "phoenix1Analysis": _analysis_contract(
            data_root / "phoenix1" / "analysis" / "web_results.json",
            "local Phoenix 1 analysis",
        ),
        "phoenix2Analysis": _analysis_contract(
            data_root / "phoenix2" / "analysis" / "web_results.json",
            "local Phoenix 2 analysis",
        ),
        "combinedTier": _analysis_contract(
            data_root / "combined" / "analysis" / "web_results.json",
            "local combined tier",
        ),
        "recommendationIndex": _recommendation_contract(data_root, hmac_key),
    }
    manifest: dict[str, Any] = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "captureStatus": "local-development-only",
        "boundary": {
            "id": boundary_id.strip(),
            "capturedAtUtc": captured_at_utc or utc_now_text(),
            "source": "local",
            "productionReady": False,
        },
        "code": _code_contract(project_root),
        "datasets": {
            "phoenix1Snapshot": phoenix1,
            "phoenix2Snapshot": phoenix2,
        },
        "publicArtifacts": _reference_contracts(project_root),
        "derivedArtifacts": derived,
        "contractCoverage": {
            "productionApiRoutes": 14,
            "standaloneLocalRouteHandlers": 6,
            "browserRoutes": 4,
            "productionCaptureImplemented": True,
        },
        "privacy": {
            "scanResult": "passed",
            "containsRawPlayerIdentifiers": False,
            "containsUsernames": False,
            "containsRawScoreRows": False,
            "containsPerPlayerDigests": False,
            "privateDigestAlgorithm": "HMAC-SHA256",
            "publicDigestAlgorithm": "SHA-256",
            "hmacKeyStored": False,
        },
        "gate": {
            "productionT0Captured": False,
            "generationConsistencyVerified": False,
            "readyForSchemaImplementation": False,
            "blockingReason": (
                "This is a local-development baseline. An authorized, generation-consistent "
                "production T0 capture is still required."
            ),
        },
    }
    validate_baseline_manifest(manifest)
    return manifest


def _required_production_json(
    store: ProductionReadStore, pathname: str, role: str
) -> dict[str, Any]:
    try:
        payload = store.get_json(pathname)
    except Exception as exc:
        raise BaselineCaptureError(f"Could not read the required {role} artifact.") from exc
    if not isinstance(payload, dict):
        raise BaselineCaptureError(f"The required {role} artifact is missing or invalid.")
    _exact_json_bytes(payload)
    return payload


def _required_production_bytes(
    store: ProductionReadStore, pathname: str, role: str
) -> bytes:
    try:
        payload = store.get_bytes(pathname)
    except Exception as exc:
        raise BaselineCaptureError(f"Could not read the required {role} artifact.") from exc
    if not isinstance(payload, bytes):
        raise BaselineCaptureError(f"The required {role} artifact is missing or invalid.")
    return payload


def _read_active_pointers(store: ProductionReadStore) -> dict[str, dict[str, Any]]:
    return {
        "phoenix2Analysis": _required_production_json(
            store, PHOENIX2_ANALYSIS_POINTER, "Phoenix 2 analysis pointer"
        ),
        "combinedTier": _required_production_json(
            store, COMBINED_TIER_POINTER, "combined-tier pointer"
        ),
        "recommendations": _required_production_json(
            store, RECOMMENDATION_POINTER, "recommendation pointer"
        ),
    }


def _bounded_artifact_count(value: Any, role: str) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise BaselineCaptureError(f"The {role} count is invalid.") from exc
    if count < 0 or count > 100_000:
        raise BaselineCaptureError(f"The {role} count is outside the safe capture bound.")
    return count


def _recommendation_index_path(generation: str) -> str:
    return f"analysis/recommendations/indexes/{generation}.json"


def _recommendation_model_path(generation: str) -> str:
    return f"analysis/recommendations/models/{generation}.json"


def _recommendation_score_model_path(generation: str) -> str:
    return f"analysis/recommendations/models/{generation}.npz"


def _recommendation_input_shard_path(
    generation: str, mix_key: str, shard: int
) -> str:
    return (
        f"analysis/private/recommendation-inputs/{generation}/{mix_key}/"
        f"{shard:04d}.json"
    )


def _legacy_recommendation_shard_path(generation: str, shard: int) -> str:
    return f"analysis/recommendations/generations/{generation}/shards/{shard:04d}.json"


def _artifact_hmac_record(
    role: str, payload: Mapping[str, Any] | bytes, hmac_key: bytes
) -> dict[str, Any]:
    if isinstance(payload, bytes):
        size = len(payload)
        digest = private_bytes_hmac_sha256(payload, hmac_key)
    else:
        body = _exact_json_bytes(payload)
        size = len(body)
        digest = private_hmac_sha256(payload, hmac_key)
    return {
        "role": role,
        "bytes": size,
        "contentHmacSha256": digest,
    }


def _read_recommendation_artifacts(
    store: ProductionReadStore,
    index: Mapping[str, Any],
    hmac_key: bytes,
) -> list[dict[str, Any]]:
    generation = str(index.get("generationKey") or "").strip()
    if not GENERATION_KEY_PATTERN.fullmatch(generation):
        raise BaselineCaptureError("The active recommendation generation identifier is invalid.")
    versioned = _required_production_json(
        store,
        _recommendation_index_path(generation),
        "versioned recommendation index",
    )
    if _exact_json_bytes(versioned) != _exact_json_bytes(index):
        raise BaselineCaptureError(
            "The active recommendation pointer does not match its versioned index."
        )
    records = [_artifact_hmac_record("versionedIndex", versioned, hmac_key)]
    storage_schema = _bounded_artifact_count(
        index.get("storageSchemaVersion"), "recommendation storage schema"
    )
    if storage_schema >= 3:
        expected_model_path = _recommendation_model_path(generation)
        if index.get("modelPath") != expected_model_path:
            raise BaselineCaptureError(
                "The active recommendation index references an unexpected model artifact."
            )
        model = _required_production_json(
            store, expected_model_path, "recommendation model metadata"
        )
        score_model = _required_production_bytes(
            store,
            _recommendation_score_model_path(generation),
            "recommendation numeric model",
        )
        records.extend(
            (
                _artifact_hmac_record("modelMetadata", model, hmac_key),
                _artifact_hmac_record("numericModel", score_model, hmac_key),
            )
        )
        shard_count = _bounded_artifact_count(
            index.get("inputShardCount"), "recommendation input shard"
        )
        for shard in range(shard_count):
            for mix_key, role in (
                ("phoenix1", "phoenix1InputShard"),
                ("phoenix2", "phoenix2InputShard"),
            ):
                payload = _required_production_json(
                    store,
                    _recommendation_input_shard_path(generation, mix_key, shard),
                    role,
                )
                records.append(_artifact_hmac_record(role, payload, hmac_key))
        return records
    if storage_schema == 2:
        shard_count = _bounded_artifact_count(
            index.get("shardCount"), "legacy recommendation shard"
        )
        for shard in range(shard_count):
            payload = _required_production_json(
                store,
                _legacy_recommendation_shard_path(generation, shard),
                "legacy recommendation shard",
            )
            records.append(_artifact_hmac_record("legacyPublicShard", payload, hmac_key))
        return records
    raise BaselineCaptureError("The active recommendation storage schema is unsupported.")


def _artifact_evidence_summary(
    records: Sequence[Mapping[str, Any]], hmac_key: bytes
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["role"]), []).append(dict(record))
    groups = {
        role: {
            "count": len(items),
            "bytes": sum(int(item["bytes"]) for item in items),
            "contentSetHmacSha256": private_hmac_sha256(
                sorted(str(item["contentHmacSha256"]) for item in items), hmac_key
            ),
        }
        for role, items in sorted(grouped.items())
    }
    return {
        "referencedArtifacts": len(records),
        "referencedArtifactBytes": sum(int(record["bytes"]) for record in records),
        "contentSetHmacSha256": private_hmac_sha256(
            canonical_rows(records), hmac_key
        ),
        "groups": groups,
    }


def _production_recommendation_contract(
    index: Mapping[str, Any],
    artifact_summary: Mapping[str, Any],
    hmac_key: bytes,
) -> dict[str, Any]:
    players = index.get("players")
    if not isinstance(players, list) or not all(isinstance(row, Mapping) for row in players):
        raise BaselineCaptureError("The active recommendation index has invalid player entries.")
    method = index.get("method") if isinstance(index.get("method"), Mapping) else {}
    return {
        "schemaVersion": index.get("schemaVersion"),
        "storageSchemaVersion": index.get("storageSchemaVersion"),
        "sourceBoundaryUtc": index.get("modelGeneratedAtUtc", index.get("generatedAtUtc")),
        "refreshSupported": bool(index.get("refreshSupported", False)),
        "counts": {
            "publicPlayerEntries": len(players),
            "referencedArtifacts": artifact_summary["referencedArtifacts"],
            "referencedArtifactBytes": artifact_summary["referencedArtifactBytes"],
        },
        "logicalHashes": {
            "methodSha256": public_sha256(method),
            "privateIndexHmacSha256": private_hmac_sha256(index, hmac_key),
            "artifactInventoryHmacSha256": artifact_summary["contentSetHmacSha256"],
        },
    }


def capture_production_baseline(
    *,
    store: ProductionReadStore,
    project_root: Path,
    boundary_id: str,
    hmac_key: bytes,
    max_attempts: int = DEFAULT_PRODUCTION_ATTEMPTS,
    captured_at_utc: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read one complete stable production publication boundary.

    Only an exact change to an active pointer or the mutable Phoenix 2 snapshot is
    retryable. Missing, malformed, or mismatched referenced artifacts fail immediately.
    """
    boundary_id = _validate_boundary_id(boundary_id)
    if max_attempts < 1 or max_attempts > MAXIMUM_PRODUCTION_ATTEMPTS:
        raise BaselineCaptureError(
            f"Production capture attempts must be between 1 and {MAXIMUM_PRODUCTION_ATTEMPTS}."
        )
    captured_at = captured_at_utc or utc_now_text()
    for attempt in range(1, max_attempts + 1):
        first_pointers = _read_active_pointers(store)
        phoenix1_snapshot = _required_production_json(
            store, PHOENIX1_PRIVATE_SNAPSHOT, "frozen Phoenix 1 private snapshot"
        )
        phoenix2_snapshot_first = _required_production_json(
            store, PHOENIX2_PRIVATE_SNAPSHOT, "mutable Phoenix 2 private snapshot"
        )
        artifact_records = _read_recommendation_artifacts(
            store, first_pointers["recommendations"], hmac_key
        )
        phoenix2_snapshot_second = _required_production_json(
            store, PHOENIX2_PRIVATE_SNAPSHOT, "mutable Phoenix 2 private snapshot"
        )
        second_pointers = _read_active_pointers(store)

        snapshot_stable = _exact_json_bytes(phoenix2_snapshot_first) == _exact_json_bytes(
            phoenix2_snapshot_second
        )
        pointers_stable = _exact_json_bytes(first_pointers) == _exact_json_bytes(
            second_pointers
        )
        if not snapshot_stable or not pointers_stable:
            if attempt < max_attempts:
                continue
            raise BaselineCaptureError(
                "The production publication boundary did not stabilize within the bounded "
                "attempt limit; no evidence was written. Retry after active publication ends."
            )

        phoenix1_contract = _snapshot_payload_contract(
            phoenix1_snapshot,
            expected_mix="Phoenix",
            hmac_key=hmac_key,
            label="frozen Phoenix 1 private snapshot",
        )
        phoenix2_contract = _snapshot_payload_contract(
            phoenix2_snapshot_first,
            expected_mix="Phoenix2",
            hmac_key=hmac_key,
            label="mutable Phoenix 2 private snapshot",
        )
        artifact_summary = _artifact_evidence_summary(artifact_records, hmac_key)
        manifest: dict[str, Any] = {
            "schemaVersion": MANIFEST_SCHEMA_VERSION,
            "captureStatus": "production-t0",
            "boundary": {
                "id": boundary_id,
                "capturedAtUtc": captured_at,
                "source": "production",
                "productionReady": True,
            },
            "code": _code_contract(project_root),
            "datasets": {
                "phoenix1Snapshot": phoenix1_contract,
                "phoenix2Snapshot": phoenix2_contract,
            },
            "publicArtifacts": _reference_contracts(project_root),
            "derivedArtifacts": {
                "phoenix1Analysis": _analysis_contract(
                    project_root / "public" / "data" / "phoenix1.json",
                    "frozen Phoenix 1 analysis",
                ),
                "phoenix2Analysis": _analysis_payload_contract(
                    first_pointers["phoenix2Analysis"], "production Phoenix 2 analysis"
                ),
                "combinedTier": _analysis_payload_contract(
                    first_pointers["combinedTier"], "production combined tier"
                ),
                "recommendationIndex": _production_recommendation_contract(
                    first_pointers["recommendations"], artifact_summary, hmac_key
                ),
            },
            "contractCoverage": {
                "productionApiRoutes": 14,
                "standaloneLocalRouteHandlers": 6,
                "browserRoutes": 4,
                "productionCaptureImplemented": True,
            },
            "privacy": {
                "scanResult": "passed",
                "containsRawPlayerIdentifiers": False,
                "containsUsernames": False,
                "containsRawScoreRows": False,
                "containsPerPlayerDigests": False,
                "privateDigestAlgorithm": "HMAC-SHA256",
                "publicDigestAlgorithm": "SHA-256",
                "hmacKeyStored": False,
            },
            "gate": {
                "productionT0Captured": True,
                "generationConsistencyVerified": True,
                "readyForSchemaImplementation": True,
                "blockingReason": "",
            },
        }
        private_evidence: dict[str, Any] = {
            "schemaVersion": 1,
            "evidenceType": "pumbility-production-baseline-private",
            "boundaryId": boundary_id,
            "capturedAtUtc": captured_at,
            "consistency": {
                "attemptsUsed": attempt,
                "activePointersMatched": True,
                "mutableSnapshotMatched": True,
                "activePointerSetHmacSha256": private_hmac_sha256(
                    first_pointers, hmac_key
                ),
                "mutableSnapshotHmacSha256": private_hmac_sha256(
                    phoenix2_snapshot_first, hmac_key
                ),
            },
            "datasetEvidence": {
                "phoenix1": {
                    "snapshotHmacSha256": private_hmac_sha256(
                        phoenix1_snapshot, hmac_key
                    ),
                    "logicalHashes": phoenix1_contract["logicalHashes"],
                },
                "phoenix2": {
                    "snapshotHmacSha256": private_hmac_sha256(
                        phoenix2_snapshot_first, hmac_key
                    ),
                    "logicalHashes": phoenix2_contract["logicalHashes"],
                },
            },
            "artifactEvidence": artifact_summary,
            "privacy": {
                "scanResult": "passed",
                "containsRawPlayerIdentifiers": False,
                "containsUsernames": False,
                "containsRawScoreRows": False,
                "containsStorageObjectLocations": False,
                "containsGenerationIdentifiers": False,
                "containsPerPlayerDigests": False,
                "privateDigestAlgorithm": "HMAC-SHA256",
                "hmacKeyStored": False,
            },
        }
        validate_baseline_manifest(manifest)
        privacy_scan_manifest(private_evidence)
        return manifest, private_evidence

    raise AssertionError("Unreachable bounded production capture loop.")


def _load_hmac_key(environment_name: str) -> bytes:
    value = os.getenv(environment_name, "")
    if not value:
        raise BaselineCaptureError(
            f"Set {environment_name} to a private value of at least "
            f"{MINIMUM_HMAC_KEY_BYTES} bytes before capturing a baseline."
        )
    key = value.encode("utf-8")
    if len(key) < MINIMUM_HMAC_KEY_BYTES:
        raise BaselineCaptureError(
            f"{environment_name} must contain at least {MINIMUM_HMAC_KEY_BYTES} bytes."
        )
    return key


def _write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(manifest), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _safe_output_label(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return f"<external-output>/{path.name}"


def _production_output_directory(
    *, project_root: Path, boundary_id: str, requested: Path | None
) -> Path:
    ignored_root = (project_root / ".local-data").resolve()
    output = (
        requested.resolve()
        if requested is not None
        else ignored_root / "pumbility-migration" / boundary_id
    )
    try:
        output.relative_to(ignored_root)
    except ValueError:
        raise BaselineCaptureError(
            "Production evidence must be written under the repository's ignored .local-data directory."
        ) from None
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture a privacy-safe Pumbility migration baseline."
    )
    subparsers = parser.add_subparsers(dest="source", required=True)
    local = subparsers.add_parser("local", help="Capture ignored local artifacts.")
    local.add_argument("--boundary-id", required=True)
    local.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    local.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    local.add_argument(
        "--hmac-key-env", default="PUMBILITY_BASELINE_HMAC_KEY"
    )
    local.add_argument("--output", type=Path)
    production = subparsers.add_parser(
        "production", help="Capture a stable production boundary with the private Vercel store."
    )
    production.add_argument("--boundary-id", required=True)
    production.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    production.add_argument(
        "--hmac-key-env", default="PUMBILITY_BASELINE_HMAC_KEY"
    )
    production.add_argument(
        "--max-attempts", type=int, default=DEFAULT_PRODUCTION_ATTEMPTS
    )
    production.add_argument("--output-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.source == "production":
            key = _load_hmac_key(args.hmac_key_env)
            project_root = args.project_root.resolve()
            output_dir = _production_output_directory(
                project_root=project_root,
                boundary_id=_validate_boundary_id(args.boundary_id),
                requested=args.output_dir,
            )
            # Deliberately lazy: local capture, module import, and tests neither construct
            # the Vercel adapter nor read credentials/network.
            try:
                from analysis_runtime import VercelPrivateBlobStore

                store = VercelPrivateBlobStore()
            except (ImportError, RuntimeError) as exc:
                raise BaselineCaptureError(
                    "The private Vercel production store is not configured. No evidence was written."
                ) from exc
            manifest, private_evidence = capture_production_baseline(
                store=store,
                project_root=project_root,
                boundary_id=args.boundary_id,
                hmac_key=key,
                max_attempts=args.max_attempts,
            )
            try:
                _write_manifest(output_dir / "private-evidence.json", private_evidence)
                _write_manifest(output_dir / "baseline-manifest.json", manifest)
            except OSError as exc:
                raise BaselineCaptureError(
                    "Could not write the ignored production evidence bundle."
                ) from exc
            summary = {
                "boundaryId": manifest["boundary"]["id"],
                "captureStatus": manifest["captureStatus"],
                "privacyScan": "passed",
                "generationConsistencyVerified": True,
            }
            privacy_scan_manifest(summary)
            print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
            return 0
        key = _load_hmac_key(args.hmac_key_env)
        manifest = capture_local_baseline(
            project_root=args.project_root.resolve(),
            data_root=args.data_root.resolve(),
            boundary_id=args.boundary_id,
            hmac_key=key,
        )
        output = args.output or (
            DEFAULT_EVIDENCE_ROOT / args.boundary_id / "baseline-manifest.json"
        )
        _write_manifest(output.resolve(), manifest)
        summary = {
            "boundaryId": manifest["boundary"]["id"],
            "captureStatus": manifest["captureStatus"],
            "manifestPath": _safe_output_label(output, args.project_root),
            "privacyScan": "passed",
            "productionReady": False,
        }
        privacy_scan_manifest(summary)
        print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
        return 0
    except BaselineCaptureError as exc:
        print(f"Baseline capture failed safely: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
