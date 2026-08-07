#!/usr/bin/env python3
"""Capture a private, analysis-only Phoenix snapshot for local replay."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from phoenix2_sync import (  # noqa: E402
    CHART_FIELDS,
    SCORE_FIELDS,
    isoformat_utc,
    synchronize_phoenix2_snapshot,
)
from mix_registry import DEFAULT_MIX_KEY, MixSpec, resolve_mix  # noqa: E402
from piu_misgrade_analyzer import (  # noqa: E402
    ApiError,
    PiuScoresClient,
    SCRIPT_VERSION,
    load_snapshot,
)


DEFAULT_DATA_ROOT = PROJECT_ROOT / ".local-data" / "piu-scores"
PLAYER_FIELDS = ("userId", "lastSyncedAtUtc", "lastScoreRecordedAtUtc")
FORBIDDEN_KEYS = {
    "username",
    "gameTag",
    "email",
    "displayName",
    "authorization",
    "apiKey",
    "token",
}
SECRET_PATTERN = re.compile(r"(?:piu_scores_live_|pst_live_)[0-9a-f]{16,}", re.IGNORECASE)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _atomic_write_json(path: Path, payload: Any) -> None:
    _mkdir_private(path.parent)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def _atomic_write_jsonl_gz(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    _mkdir_private(path.parent)
    temporary = path.with_name(path.name + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _contains_secret(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_secret(key) or _contains_secret(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(item) for item in value)
    return isinstance(value, str) and SECRET_PATTERN.search(value) is not None


def _validate_allowed_fields(
    label: str,
    rows: Sequence[Mapping[str, Any]],
    allowed: Sequence[str],
) -> None:
    allowed_set = set(allowed)
    for index, row in enumerate(rows):
        unexpected = set(row) - allowed_set
        forbidden = set(row) & FORBIDDEN_KEYS
        if unexpected or forbidden:
            names = sorted(unexpected | forbidden)
            raise ValueError(f"{label} row {index} contains forbidden fields: {names}")
        if _contains_secret(row):
            raise ValueError(f"{label} row {index} contains a credential-shaped value.")


def validate_snapshot_rows(
    players: Sequence[Mapping[str, Any]],
    charts: Sequence[Mapping[str, Any]],
    scores: Sequence[Mapping[str, Any]],
    *,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> None:
    """Validate privacy, shape, uniqueness, and referential integrity."""
    mix_spec = resolve_mix(mix)
    if not players:
        raise ValueError("The snapshot contains no consented players.")
    if not charts:
        raise ValueError(f"The snapshot contains no {mix_spec.label} charts.")
    if not scores:
        raise ValueError(
            f"The snapshot contains no {mix_spec.label} scores visible to this credential."
        )

    _validate_allowed_fields("Player", players, PLAYER_FIELDS)
    _validate_allowed_fields("Chart", charts, CHART_FIELDS)
    _validate_allowed_fields("Score", scores, SCORE_FIELDS)

    player_ids: set[str] = set()
    for index, row in enumerate(players):
        player_id = str(row.get("userId") or "").strip()
        if not player_id:
            raise ValueError(f"Player row {index} has no userId.")
        if player_id in player_ids:
            raise ValueError(f"Player row {index} duplicates a userId.")
        player_ids.add(player_id)

    chart_ids: set[str] = set()
    for index, row in enumerate(charts):
        missing = {"id", "songName", "type", "level"} - set(row)
        if missing:
            raise ValueError(f"Chart row {index} is missing required fields: {sorted(missing)}")
        chart_id = str(row.get("id") or "").strip()
        if not chart_id:
            raise ValueError(f"Chart row {index} has no id.")
        if chart_id in chart_ids:
            raise ValueError(f"Chart row {index} duplicates an id.")
        chart_ids.add(chart_id)

    score_keys: set[tuple[str, str]] = set()
    for index, row in enumerate(scores):
        missing = {"playerId", "chartId", "pumbility", "isBroken"} - set(row)
        if missing:
            raise ValueError(f"Score row {index} is missing required fields: {sorted(missing)}")
        player_id = str(row.get("playerId") or "").strip()
        chart_id = str(row.get("chartId") or "").strip()
        if player_id not in player_ids:
            raise ValueError(f"Score row {index} refers to an unknown player.")
        if chart_id not in chart_ids:
            raise ValueError(f"Score row {index} refers to an unknown chart.")
        key = (player_id, chart_id)
        if key in score_keys:
            raise ValueError(f"Score row {index} duplicates a player/chart pair.")
        score_keys.add(key)
        try:
            pumbility = float(row.get("pumbility"))
        except (TypeError, ValueError):
            raise ValueError(f"Score row {index} has invalid Pumbility.") from None
        if not math.isfinite(pumbility):
            raise ValueError(f"Score row {index} has non-finite Pumbility.")
        raw_score = row.get("score")
        if raw_score is not None:
            try:
                numeric_score = float(raw_score)
            except (TypeError, ValueError):
                raise ValueError(f"Score row {index} has an invalid score.") from None
            if not math.isfinite(numeric_score):
                raise ValueError(f"Score row {index} has a non-finite score.")
        if bool(row.get("isBroken", False)):
            raise ValueError(f"Score row {index} is marked broken.")


def validate_snapshot_directory(
    path: Path,
    *,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> dict[str, Any]:
    """Validate a completed on-disk snapshot and its non-identifying manifest."""
    players, charts, scores = load_snapshot(path)
    validate_snapshot_rows(players, charts, scores, mix=mix)
    manifest_path = path / "snapshot_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("The snapshot manifest is missing.") from None
    except json.JSONDecodeError:
        raise ValueError("The snapshot manifest is not valid JSON.") from None
    mix_spec = resolve_mix(mix)
    if not isinstance(manifest, dict) or manifest.get("mix") != mix_spec.api_value:
        raise ValueError(
            f"The snapshot manifest does not describe {mix_spec.label} data."
        )
    expected_counts = {
        "players": len(players),
        "charts": len(charts),
        "scoreRows": len(scores),
    }
    for field, expected in expected_counts.items():
        if manifest.get(field) != expected:
            raise ValueError(f"The snapshot manifest has an incorrect {field} count.")
    if manifest.get("credentialStored") is not False or _contains_secret(manifest):
        raise ValueError("The snapshot manifest failed its credential-safety check.")
    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict):
        raise ValueError("The snapshot manifest has no checksums.")
    for name in ("players.json", "charts.json", "scores.jsonl.gz"):
        if checksums.get(name) != _sha256(path / name):
            raise ValueError(f"The snapshot checksum for {name} does not match.")
    return manifest


def _remove_tree_within(data_root: Path, target: Path) -> None:
    root = data_root.resolve()
    resolved = target.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError(f"Refusing to remove unsafe local-data path: {resolved}")
    if target.exists():
        shutil.rmtree(target)


def _load_staging(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _ensure_git_ignored(path: Path) -> None:
    try:
        relative = path.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        raise ValueError("The local snapshot directory must remain inside this repository.") from None
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", str(relative)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError(
            f"{relative} is not ignored by Git. Add .local-data/ to .gitignore before capturing."
        )


def _promote_candidate(data_root: Path, candidate: Path, current: Path) -> None:
    backup = data_root / "staging" / "previous"
    _remove_tree_within(data_root, backup)
    if current.exists():
        os.replace(current, backup)
    try:
        os.replace(candidate, current)
    except Exception:
        if backup.exists() and not current.exists():
            os.replace(backup, current)
        raise
    _remove_tree_within(data_root, backup)


def capture_private_snapshot(
    client: Any,
    data_root: Path,
    *,
    restart: bool = False,
    now: Callable[[], datetime] = utc_now,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> dict[str, Any]:
    """Capture, validate, and safely promote a complete API-visible snapshot."""
    mix_spec = resolve_mix(mix)
    if mix_spec.archived:
        raise ValueError(f"{mix_spec.label} is archived and cannot be captured again.")
    data_root = data_root.resolve()
    staging_dir = data_root / "staging"
    staging_path = staging_dir / "snapshot.json"
    current_dir = data_root / "current"
    _mkdir_private(staging_dir)
    if restart and staging_path.exists():
        staging_path.unlink()

    resume = _load_staging(staging_path)
    started = now()
    job_id = str(resume.get("jobId")) if resume and resume.get("jobId") else (
        f"local-{mix_spec.slug}-" + started.strftime("%Y%m%dT%H%M%SZ")
    )

    def checkpoint(payload: dict[str, Any]) -> None:
        _atomic_write_json(staging_path, payload)

    def progress(current: int, total: int, message: str) -> None:
        print(f"[{current:,}/{total:,}] {message}", flush=True)

    snapshot, _ = synchronize_phoenix2_snapshot(
        client,
        None,
        job_id=job_id,
        resume_staging=resume,
        progress=progress,
        checkpoint=checkpoint,
        now=now,
    )
    players = [
        {
            "userId": row["playerId"],
            "lastSyncedAtUtc": row.get("lastSyncedAtUtc", ""),
            "lastScoreRecordedAtUtc": row.get("lastScoreRecordedAtUtc"),
        }
        for row in snapshot["players"]
    ]
    charts = [dict(row) for row in snapshot["charts"]]
    scores = [dict(row) for row in snapshot["scores"]]
    validate_snapshot_rows(players, charts, scores, mix=mix_spec)

    candidate = staging_dir / f"candidate-{job_id}"
    _remove_tree_within(data_root, candidate)
    _mkdir_private(candidate)
    _atomic_write_json(candidate / "players.json", players)
    _atomic_write_json(candidate / "charts.json", charts)
    _atomic_write_jsonl_gz(candidate / "scores.jsonl.gz", scores)

    completed = now()
    manifest = {
        "schemaVersion": 1,
        "scriptVersion": SCRIPT_VERSION,
        "mix": mix_spec.api_value,
        "captureStartedAtUtc": isoformat_utc(started),
        "captureCompletedAtUtc": isoformat_utc(completed),
        "players": len(players),
        "charts": len(charts),
        "scoreRows": len(scores),
        "httpRequests": int(getattr(client, "request_count", 0)),
        "credentialStored": False,
        "checksums": {
            name: _sha256(candidate / name)
            for name in ("players.json", "charts.json", "scores.jsonl.gz")
        },
    }
    _atomic_write_json(candidate / "snapshot_manifest.json", manifest)
    validate_snapshot_directory(candidate, mix=mix_spec)
    _promote_candidate(data_root, candidate, current_dir)
    if staging_path.exists():
        staging_path.unlink()
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture all API-visible best scores for private local analysis."
    )
    parser.add_argument(
        "--mix",
        default=DEFAULT_MIX_KEY,
        help="Refreshable mix key (Phoenix 2 only).",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Discard an interrupted staging checkpoint and restart the full capture.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        mix_spec = resolve_mix(args.mix)
        if mix_spec.archived:
            raise ValueError(f"{mix_spec.label} is archived and cannot be captured again.")
        data_root = DEFAULT_DATA_ROOT / mix_spec.slug
        _ensure_git_ignored(data_root)
        api_key = os.getenv("PIU_SCORES_API_KEY", "").strip()
        if not api_key:
            raise ApiError(
                "PIU_SCORES_API_KEY is empty. Set it in the process environment, never in source code."
            )
        client = PiuScoresClient(api_key=api_key)
        manifest = capture_private_snapshot(
            client,
            data_root,
            restart=args.restart,
            mix=mix_spec,
        )
        print(
            f"{mix_spec.label} snapshot complete: "
            f"{manifest['players']:,} players, {manifest['charts']:,} charts, "
            f"{manifest['scoreRows']:,} best scores.",
            flush=True,
        )
        return 0
    except (ApiError, OSError, ValueError) as exc:
        print(f"Snapshot failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
