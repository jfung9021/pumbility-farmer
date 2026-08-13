"""Incremental, privacy-minimized snapshot synchronization for supported mixes."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence

from mix_registry import DEFAULT_MIX_KEY, MixSpec, resolve_mix


# Compatibility constant for existing Phoenix 2 callers.
MIX = resolve_mix(DEFAULT_MIX_KEY).api_value
SNAPSHOT_SCHEMA_VERSION = 2
DEFAULT_WORKERS = 6
DEFAULT_CHECKPOINT_EVERY = 50
EMPTY_RECHECK_AFTER = timedelta(days=1)
INCREMENTAL_SCORE_LOOKBACK = timedelta(days=7)

CHART_FIELDS = (
    "id",
    "songName",
    "type",
    "level",
    "difficulty",
    "imageUrl",
    "noteCount",
    "stepArtist",
    "bpmMin",
    "bpmMax",
)
SCORE_FIELDS = (
    "playerId",
    "chartId",
    "pumbility",
    "score",
    "letterGrade",
    "plate",
    "recordedAt",
    "isBroken",
)

ProgressCallback = Callable[[int, int, str], None]
CheckpointCallback = Callable[[dict[str, Any]], None]
PlayerCheckpointCallback = Callable[[list[dict[str, Any]]], None]


class CollectionClient(Protocol):
    def fetch_page_collection(
        self,
        initial_path: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def incremental_recorded_after(
    value: object,
    *,
    lookback: timedelta = INCREMENTAL_SCORE_LOOKBACK,
) -> str | None:
    """Return an overlapping score watermark that tolerates delayed indexing."""
    parsed = parse_utc(value)
    if parsed is None:
        return None
    return isoformat_utc(parsed - max(lookback, timedelta(0)))


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def sanitize_chart(row: Mapping[str, Any]) -> dict[str, Any] | None:
    chart_id = row.get("id")
    if chart_id is None or str(chart_id).strip() == "":
        return None
    sanitized = {field: row.get(field) for field in CHART_FIELDS}
    sanitized["id"] = str(chart_id)
    bpm_min = _finite_number(row.get("bpmMin"))
    bpm_max = _finite_number(row.get("bpmMax"))
    sanitized["bpmMin"] = bpm_min if bpm_min is not None and bpm_min > 0 else None
    sanitized["bpmMax"] = bpm_max if bpm_max is not None and bpm_max > 0 else None
    if (
        sanitized["bpmMin"] is not None
        and sanitized["bpmMax"] is not None
        and sanitized["bpmMin"] > sanitized["bpmMax"]
    ):
        sanitized["bpmMin"], sanitized["bpmMax"] = (
            sanitized["bpmMax"],
            sanitized["bpmMin"],
        )
    return sanitized


def _song_bpm_range(row: Mapping[str, Any]) -> tuple[float | None, float | None]:
    bpm = row.get("bpm")
    if not isinstance(bpm, Mapping):
        return None, None
    bpm_min = _finite_number(bpm.get("min"))
    bpm_max = _finite_number(bpm.get("max"))
    bpm_min = bpm_min if bpm_min is not None and bpm_min > 0 else None
    bpm_max = bpm_max if bpm_max is not None and bpm_max > 0 else None
    if bpm_min is not None and bpm_max is not None and bpm_min > bpm_max:
        return bpm_max, bpm_min
    return bpm_min, bpm_max


def attach_song_bpm_metadata(
    charts: Sequence[Mapping[str, Any]],
    songs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Join the v2 song catalog's BPM range onto chart rows by song name."""
    bpm_by_song = {
        str(row.get("name") or ""): _song_bpm_range(row)
        for row in songs
        if str(row.get("name") or "").strip()
    }
    enriched: list[dict[str, Any]] = []
    for row in charts:
        bpm_min, bpm_max = bpm_by_song.get(
            str(row.get("songName") or ""), (None, None)
        )
        enriched.append({**row, "bpmMin": bpm_min, "bpmMax": bpm_max})
    return enriched


def sanitize_score(row: Mapping[str, Any], player_id: str | None = None) -> dict[str, Any] | None:
    effective_player_id = player_id if player_id is not None else row.get("playerId")
    chart_id = row.get("chartId")
    pumbility = _finite_number(row.get("pumbility"))
    if (
        effective_player_id is None
        or str(effective_player_id).strip() == ""
        or chart_id is None
        or str(chart_id).strip() == ""
        or pumbility is None
        or bool(row.get("isBroken", False))
    ):
        return None
    score = _finite_number(row.get("score"))
    sanitized = {
        "playerId": str(effective_player_id),
        "chartId": str(chart_id),
        "pumbility": pumbility,
        "score": score,
        "letterGrade": str(row.get("letterGrade") or "").strip() or None,
        "plate": str(row.get("plate") or "").strip() or None,
        "recordedAt": str(row.get("recordedAt") or ""),
        "isBroken": False,
    }
    return sanitized


def _score_priority(row: Mapping[str, Any]) -> tuple[float, float, str, int, str]:
    return (
        _finite_number(row.get("pumbility")) or -math.inf,
        _finite_number(row.get("score")) or -math.inf,
        str(row.get("recordedAt") or ""),
        int(bool(row.get("letterGrade"))) + int(bool(row.get("plate"))),
        str(row.get("chartId") or ""),
    )


def merge_best_scores(
    existing: Sequence[Mapping[str, Any]],
    incoming: Sequence[Mapping[str, Any]],
    *,
    player_id: str | None = None,
) -> list[dict[str, Any]]:
    """Merge deterministically by player/chart and retain the best valid row."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for source in (existing, incoming):
        for raw in source:
            row = sanitize_score(raw, player_id=player_id)
            if row is None:
                continue
            key = (row["playerId"], row["chartId"])
            current = best.get(key)
            if current is None or _score_priority(row) > _score_priority(current):
                best[key] = row
    return [best[key] for key in sorted(best)]


def sanitize_snapshot(
    snapshot: Mapping[str, Any] | None,
    *,
    mix: str | MixSpec | None = None,
) -> dict[str, Any]:
    source = snapshot if isinstance(snapshot, Mapping) else {}
    requested = resolve_mix(mix) if mix is not None else resolve_mix(source.get("mix"))
    source_mix = source.get("mix")
    if source_mix is not None and str(source_mix).strip():
        existing = resolve_mix(source_mix)
        if existing.key != requested.key:
            raise ValueError(
                f"Snapshot mix {existing.label} does not match requested mix {requested.label}."
            )
    charts = [
        sanitized
        for raw in source.get("charts", [])
        if isinstance(raw, Mapping) and (sanitized := sanitize_chart(raw)) is not None
    ]
    scores = merge_best_scores(
        [],
        [raw for raw in source.get("scores", []) if isinstance(raw, Mapping)],
    )
    players: list[dict[str, Any]] = []
    seen_players: set[str] = set()
    for raw in source.get("players", []):
        if not isinstance(raw, Mapping):
            continue
        raw_id = raw.get("playerId", raw.get("userId"))
        if raw_id is None or str(raw_id).strip() == "":
            continue
        player_id = str(raw_id)
        if player_id in seen_players:
            continue
        seen_players.add(player_id)
        players.append(
            {
                "playerId": player_id,
                "username": str(raw.get("username") or "").strip(),
                "lastSyncedAtUtc": str(raw.get("lastSyncedAtUtc") or ""),
                "lastScoreRecordedAtUtc": (
                    str(raw.get("lastScoreRecordedAtUtc"))
                    if raw.get("lastScoreRecordedAtUtc")
                    else None
                ),
            }
        )
    return {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "mix": requested.api_value,
        "generatedAtUtc": str(source.get("generatedAtUtc") or ""),
        "players": sorted(players, key=lambda row: row["playerId"]),
        "charts": sorted(charts, key=lambda row: row["id"]),
        "scores": scores,
    }


def _last_score_recorded_at(rows: Sequence[Mapping[str, Any]]) -> str | None:
    values = sorted(str(row.get("recordedAt") or "") for row in rows if row.get("recordedAt"))
    return values[-1] if values else None


def _staging_payload(
    *,
    job_id: str,
    created_at: str,
    updated_at: str,
    run_started_at: str,
    consented_player_ids: Sequence[str],
    completed_player_ids: set[str],
    snapshot: Mapping[str, Any],
    mix: MixSpec,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "jobId": job_id,
        "createdAtUtc": created_at,
        "updatedAtUtc": updated_at,
        "runStartedAtUtc": run_started_at,
        "consentedPlayerIds": sorted(consented_player_ids),
        "completedPlayerIds": sorted(completed_player_ids),
        "mix": mix.api_value,
        "snapshot": sanitize_snapshot(snapshot, mix=mix),
    }


def synchronize_mix_snapshot(
    client: CollectionClient,
    current_snapshot: Mapping[str, Any] | None,
    *,
    job_id: str,
    mix: str | MixSpec = DEFAULT_MIX_KEY,
    resume_staging: Mapping[str, Any] | None = None,
    workers: int = DEFAULT_WORKERS,
    checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
    empty_recheck_after: timedelta = EMPTY_RECHECK_AFTER,
    progress: ProgressCallback | None = None,
    checkpoint: CheckpointCallback | None = None,
    checkpoint_players: PlayerCheckpointCallback | None = None,
    now: Callable[[], datetime] = utc_now,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch consent and charts, then incrementally synchronize player scores."""
    mix_spec = resolve_mix(mix)
    run_started = now()
    run_started_iso = isoformat_utc(run_started)
    players_full = client.fetch_page_collection("api/v2/players", {"limit": 100})
    consented_profiles = {
        str(row["userId"]): str(row.get("username") or "").strip()
        for row in players_full
        if row.get("userId") is not None and str(row.get("userId")).strip()
    }
    consented_ids = sorted(
        {
            str(row["userId"])
            for row in players_full
            if row.get("userId") is not None and str(row.get("userId")).strip()
        }
    )
    if not consented_ids:
        from piu_misgrade_analyzer import ApiError

        raise ApiError(
            "The PIU Scores credential returned no consented players for this tool."
        )
    if progress:
        progress(0, len(consented_ids), f"Discovered {len(consented_ids):,} consented players.")

    songs_full = client.fetch_page_collection(
        "api/v2/songs", {"mix": mix_spec.api_value, "limit": 100}
    )
    charts_full = client.fetch_page_collection(
        "api/v2/charts", {"mix": mix_spec.api_value, "limit": 100}
    )
    charts = [
        sanitized
        for raw in attach_song_bpm_metadata(charts_full, songs_full)
        if (sanitized := sanitize_chart(raw)) is not None
    ]
    charts.sort(key=lambda row: row["id"])
    if not charts:
        from piu_misgrade_analyzer import ApiError

        raise ApiError(f"The {mix_spec.label} chart catalog was empty.")
    valid_chart_ids = {row["id"] for row in charts}

    try:
        source_schema = int((current_snapshot or {}).get("schemaVersion") or 1)
    except (TypeError, ValueError):
        source_schema = 1
    current = sanitize_snapshot(
        None if source_schema < SNAPSHOT_SCHEMA_VERSION else current_snapshot,
        mix=mix_spec,
    )
    resume = resume_staging if isinstance(resume_staging, Mapping) else {}
    resume_mix = resolve_mix(resume.get("mix") or mix_spec.api_value)
    if (
        resume.get("jobId") == job_id
        and isinstance(resume.get("snapshot"), Mapping)
        and resume_mix.key != mix_spec.key
    ):
        raise ValueError(
            f"Resume checkpoint mix {resume_mix.label} does not match requested mix "
            f"{mix_spec.label}."
        )
    resume_snapshot = resume.get("snapshot")
    try:
        resume_storage_schema = int(resume.get("storageSchemaVersion") or 0)
    except (TypeError, ValueError):
        resume_storage_schema = 0
    try:
        resume_schema = int(
            resume_snapshot.get("schemaVersion")
            if isinstance(resume_snapshot, Mapping)
            else 1
        )
    except (TypeError, ValueError):
        resume_schema = 1
    full_resume = (
        resume.get("jobId") == job_id
        and resume_mix.key == mix_spec.key
        and isinstance(resume_snapshot, Mapping)
        and resume_schema >= SNAPSHOT_SCHEMA_VERSION
    )
    delta_resume = (
        resume.get("jobId") == job_id
        and resume_mix.key == mix_spec.key
        and resume_storage_schema >= 2
        and isinstance(resume.get("playerCheckpoints"), list)
    )
    can_resume = full_resume or delta_resume
    working = sanitize_snapshot(
        resume.get("snapshot") if full_resume else current,
        mix=mix_spec,
    )
    consented_set = set(consented_ids)
    working_scores = [
        row
        for row in working["scores"]
        if row["playerId"] in consented_set and row["chartId"] in valid_chart_ids
    ]
    scores_by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in working_scores:
        scores_by_player[row["playerId"]].append(row)

    current_player_meta = {row["playerId"]: row for row in current["players"]}
    working_player_meta = {
        row["playerId"]: row
        for row in working["players"]
        if row["playerId"] in consented_set
    }
    if delta_resume:
        for raw_checkpoint in resume.get("playerCheckpoints", []):
            if not isinstance(raw_checkpoint, Mapping):
                continue
            raw_player = raw_checkpoint.get("player")
            if not isinstance(raw_player, Mapping):
                continue
            player_id = str(raw_player.get("playerId") or "")
            if player_id not in consented_set:
                continue
            raw_scores = raw_checkpoint.get("scores")
            scores_by_player[player_id] = [
                row
                for row in merge_best_scores(
                    [],
                    raw_scores if isinstance(raw_scores, list) else [],
                    player_id=player_id,
                )
                if row["chartId"] in valid_chart_ids
            ]
            working_player_meta[player_id] = {
                "playerId": player_id,
                "username": consented_profiles.get(
                    player_id, str(raw_player.get("username") or "").strip()
                ),
                "lastSyncedAtUtc": str(raw_player.get("lastSyncedAtUtc") or ""),
                "lastScoreRecordedAtUtc": _last_score_recorded_at(
                    scores_by_player[player_id]
                ),
            }
    completed = (
        {
            str(player_id)
            for player_id in resume.get("completedPlayerIds", [])
            if str(player_id) in consented_set
        }
        if can_resume
        else set()
    )
    created_at = (
        str(resume.get("createdAtUtc"))
        if can_resume and resume.get("createdAtUtc")
        else run_started_iso
    )
    boundary_iso = (
        str(resume.get("runStartedAtUtc"))
        if can_resume and resume.get("runStartedAtUtc")
        else run_started_iso
    )

    def build_working_snapshot() -> dict[str, Any]:
        players: list[dict[str, Any]] = []
        all_scores: list[dict[str, Any]] = []
        for player_id in consented_ids:
            rows = merge_best_scores([], scores_by_player.get(player_id, []), player_id=player_id)
            all_scores.extend(rows)
            metadata = working_player_meta.get(player_id) or current_player_meta.get(player_id) or {}
            players.append(
                {
                    "playerId": player_id,
                    "username": consented_profiles.get(
                        player_id, str(metadata.get("username") or "").strip()
                    ),
                    "lastSyncedAtUtc": str(metadata.get("lastSyncedAtUtc") or ""),
                    "lastScoreRecordedAtUtc": _last_score_recorded_at(rows),
                }
            )
        return {
            "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
            "mix": mix_spec.api_value,
            "generatedAtUtc": boundary_iso,
            "players": players,
            "charts": charts,
            "scores": all_scores,
        }

    pending_player_checkpoints: list[dict[str, Any]] = []

    def save_checkpoint() -> dict[str, Any]:
        if checkpoint_players is not None and pending_player_checkpoints:
            checkpoint_players(list(pending_player_checkpoints))
            pending_player_checkpoints.clear()
        payload = _staging_payload(
            job_id=job_id,
            created_at=created_at,
            updated_at=isoformat_utc(now()),
            run_started_at=boundary_iso,
            consented_player_ids=consented_ids,
            completed_player_ids=completed,
            snapshot=build_working_snapshot(),
            mix=mix_spec,
        )
        if checkpoint:
            checkpoint(payload)
        return payload

    def save_player_checkpoint(player_id: str) -> None:
        if checkpoint_players is None:
            return
        metadata = working_player_meta.get(player_id) or current_player_meta.get(player_id) or {}
        rows = merge_best_scores([], scores_by_player.get(player_id, []), player_id=player_id)
        pending_player_checkpoints.append(
            {
                "schemaVersion": 1,
                "player": {
                    "playerId": player_id,
                    "username": consented_profiles.get(
                        player_id, str(metadata.get("username") or "").strip()
                    ),
                    "lastSyncedAtUtc": str(metadata.get("lastSyncedAtUtc") or ""),
                    "lastScoreRecordedAtUtc": _last_score_recorded_at(rows),
                },
                "scores": rows,
            }
        )

    if full_resume and checkpoint_players is not None:
        for player_id in sorted(completed):
            save_player_checkpoint(player_id)

    completed_since_checkpoint = 0
    for player_id in consented_ids:
        if player_id in completed:
            continue
        previous = current_player_meta.get(player_id)
        if previous is None or scores_by_player.get(player_id):
            continue
        last_synced = parse_utc(previous.get("lastSyncedAtUtc"))
        if last_synced is not None and run_started - last_synced < empty_recheck_after:
            working_player_meta[player_id] = dict(previous)
            completed.add(player_id)
            save_player_checkpoint(player_id)
            completed_since_checkpoint += 1
            if progress:
                progress(
                    len(completed),
                    len(consented_ids),
                    f"Skipping recently checked players with no {mix_spec.label} scores.",
                )
            if checkpoint_every > 0 and completed_since_checkpoint >= checkpoint_every:
                save_checkpoint()
                completed_since_checkpoint = 0

    def fetch_player(player_id: str) -> tuple[str, list[dict[str, Any]]]:
        params: dict[str, Any] = {"mix": mix_spec.api_value, "limit": 100}
        previous = current_player_meta.get(player_id)
        if previous is not None and scores_by_player.get(player_id):
            recorded_after = incremental_recorded_after(
                previous.get("lastSyncedAtUtc")
            )
            if recorded_after:
                params["recordedAfter"] = recorded_after
        rows = client.fetch_page_collection(f"api/v2/players/{player_id}/scores", params)
        return player_id, rows

    remaining = [player_id for player_id in consented_ids if player_id not in completed]
    executor = ThreadPoolExecutor(
        max_workers=max(1, int(workers)), thread_name_prefix=mix_spec.slug
    )
    futures: dict[Future[tuple[str, list[dict[str, Any]]]], str] = {
        executor.submit(fetch_player, player_id): player_id for player_id in remaining
    }
    try:
        for future in as_completed(futures):
            player_id, incoming = future.result()
            scores_by_player[player_id] = merge_best_scores(
                scores_by_player.get(player_id, []), incoming, player_id=player_id
            )
            working_player_meta[player_id] = {
                "playerId": player_id,
                "lastSyncedAtUtc": boundary_iso,
                "lastScoreRecordedAtUtc": _last_score_recorded_at(scores_by_player[player_id]),
            }
            completed.add(player_id)
            save_player_checkpoint(player_id)
            completed_since_checkpoint += 1
            if progress:
                progress(
                    len(completed),
                    len(consented_ids),
                    f"Synchronized {len(completed):,} of {len(consented_ids):,} players.",
                )
            if checkpoint_every > 0 and completed_since_checkpoint >= checkpoint_every:
                save_checkpoint()
                completed_since_checkpoint = 0
    except Exception:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    final_snapshot = sanitize_snapshot(build_working_snapshot(), mix=mix_spec)
    final_snapshot["generatedAtUtc"] = boundary_iso
    final_staging = save_checkpoint()
    return final_snapshot, final_staging


def synchronize_phoenix2_snapshot(
    client: CollectionClient,
    current_snapshot: Mapping[str, Any] | None,
    **kwargs: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compatibility wrapper for the original Phoenix 2-only API."""
    return synchronize_mix_snapshot(
        client,
        current_snapshot,
        mix=DEFAULT_MIX_KEY,
        **kwargs,
    )


def analyzer_input(
    snapshot: Mapping[str, Any],
    *,
    minimum_scores_per_mode: int = 30,
    eligible_only: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return deterministic analyzer rows, excluding empty/ineligible players."""
    clean = sanitize_snapshot(snapshot)
    chart_type = {str(row["id"]): str(row.get("type") or "") for row in clean["charts"]}
    counts: Counter[tuple[str, str]] = Counter()
    nonempty: set[str] = set()
    for row in clean["scores"]:
        mode = chart_type.get(row["chartId"])
        if mode not in {"Single", "Double"}:
            continue
        player_id = row["playerId"]
        nonempty.add(player_id)
        if float(row["pumbility"]) > 0:
            counts[(player_id, mode)] += 1
    if eligible_only:
        selected = {
            player_id
            for player_id in nonempty
            if counts[(player_id, "Single")] >= minimum_scores_per_mode
            or counts[(player_id, "Double")] >= minimum_scores_per_mode
        }
    else:
        selected = nonempty
    players = [{"userId": player_id} for player_id in sorted(selected)]
    scores = [row for row in clean["scores"] if row["playerId"] in selected]
    return players, clean["charts"], scores
