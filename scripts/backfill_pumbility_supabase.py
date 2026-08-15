"""Backfill validated local snapshots into the private Pumbility schema.

This command is deliberately local-only. Production backfill requires a separate,
reviewed runbook and must not be enabled by weakening this guard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mix_registry import MIX_SPECS, resolve_mix  # noqa: E402
from phoenix2_sync import sanitize_snapshot  # noqa: E402
from piu_misgrade_analyzer import load_snapshot  # noqa: E402
from piu_recommendations import public_player_key  # noqa: E402
from phoenix1_score_overrides import phoenix1_score_overrides_metadata  # noqa: E402
from pumbility_store import _assert_schema, require_loopback_database_url  # noqa: E402
from scripts.capture_private_score_snapshot import validate_snapshot_directory  # noqa: E402


DEFAULT_SOURCE_ROOT = PROJECT_ROOT / ".local-data" / "piu-scores"
RUNTIME_REFERENCE_ROOT = PROJECT_ROOT / "runtime_reference_data"
COMPATIBILITY_JSON_ARTIFACTS = {
    "analysis/phoenix1/latest.json": Path("phoenix1/analysis/web_results.json"),
    "analysis/phoenix2/latest.json": Path("phoenix2/analysis/web_results.json"),
    "analysis/combined/latest.json": Path("combined/analysis/web_results.json"),
}


def _reference_path(relative_path: str, *, project_root: Path = PROJECT_ROOT) -> Path:
    primary = project_root / relative_path
    if primary.is_file():
        return primary
    return project_root / RUNTIME_REFERENCE_ROOT.name / Path(relative_path).name


REFERENCE_JSON_ARTIFACTS = {
    "reference/phoenix1/public.json": _reference_path("public/data/phoenix1.json"),
    "reference/phoenix1/manifest.json": _reference_path(
        "public/data/phoenix1.manifest.json"
    ),
    "reference/phoenix1/rerates.json": _reference_path(
        "public/data/phoenix1-rerates.json"
    ),
    "reference/nevsister/videos.json": PROJECT_ROOT
    / "lib/data/nevsister-chart-videos.json",
    "reference/nevsister/overrides.json": PROJECT_ROOT
    / "lib/data/nevsister-chart-video-overrides.json",
}


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _source_directory(source_root: Path, mix_key: str) -> Path:
    root = source_root.resolve()
    expected_root = (PROJECT_ROOT / ".local-data").resolve()
    if root != expected_root and not root.is_relative_to(expected_root):
        raise ValueError("Snapshot input must remain under this repository's .local-data directory.")
    return root / mix_key / "current"


def _validated_snapshot(source_root: Path, mix_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    source_dir = _source_directory(source_root, mix_key)
    manifest = validate_snapshot_directory(source_dir, mix=mix_key)
    players, charts, scores = load_snapshot(source_dir)
    snapshot = sanitize_snapshot(
        {
            "mix": resolve_mix(mix_key).api_value,
            "generatedAtUtc": manifest.get("captureCompletedAtUtc"),
            "players": players,
            "charts": charts,
            "scores": scores,
        },
        mix=mix_key,
    )
    expected = (
        int(manifest["players"]),
        int(manifest["charts"]),
        int(manifest["scoreRows"]),
    )
    actual = (
        len(snapshot["players"]),
        len(snapshot["charts"]),
        len(snapshot["scores"]),
    )
    if actual != expected:
        raise ValueError(
            f"Sanitized {mix_key} counts {actual} do not match the validated manifest {expected}."
        )
    return manifest, snapshot


def _copy_rows(cursor: Any, statement: str, rows: Iterable[tuple[Any, ...]]) -> None:
    with cursor.copy(statement) as copy:
        for row in rows:
            copy.write_row(row)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _import_reference_rows(connection: Any) -> tuple[Any, dict[str, int]]:
    """Import immutable archive/reference provenance separately from live analysis."""
    from psycopg.types.json import Jsonb

    archive_path = REFERENCE_JSON_ARTIFACTS["reference/phoenix1/public.json"]
    archive_manifest_path = REFERENCE_JSON_ARTIFACTS[
        "reference/phoenix1/manifest.json"
    ]
    rerates_path = REFERENCE_JSON_ARTIFACTS["reference/phoenix1/rerates.json"]
    videos_path = REFERENCE_JSON_ARTIFACTS["reference/nevsister/videos.json"]
    overrides_path = REFERENCE_JSON_ARTIFACTS["reference/nevsister/overrides.json"]
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    archive_manifest = json.loads(archive_manifest_path.read_text(encoding="utf-8"))
    rerates = json.loads(rerates_path.read_text(encoding="utf-8"))["rerates"]
    videos = json.loads(videos_path.read_text(encoding="utf-8"))["charts"]
    video_overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
    archive_hash = _file_sha256(archive_path)
    archive_method = str(archive_manifest["methodologyVersion"])
    generated_at = str(archive.get("generatedAtUtc") or archive_manifest["frozenAtUtc"])
    analyzer_code_hash = _file_sha256(PROJECT_ROOT / "piu_misgrade_analyzer.py")
    override_code_hash = _file_sha256(PROJECT_ROOT / "phoenix1_score_overrides.py")
    with connection.cursor() as cursor:
        cursor.execute("select id from pumbility.mixes where mix_key = 'phoenix1'")
        phoenix1_id = cursor.fetchone()[0]
        cursor.execute(
            """
            insert into pumbility.methodologies (
                methodology_key, script_version, code_hash, configuration
            ) values ('phoenix1-public-archive', %s, %s, %s)
            on conflict (methodology_key, script_version, code_hash) do update set
                configuration = excluded.configuration
            returning id
            """,
            (
                archive_method,
                analyzer_code_hash,
                Jsonb({"source": "frozen-public-archive", "manifestSchemaVersion": archive_manifest.get("schemaVersion")}),
            ),
        )
        archive_methodology_id = cursor.fetchone()[0]
        cursor.execute(
            """
            insert into pumbility.analysis_runs (
                run_key, mix_id, methodology_id, kind, status, generated_at,
                source_hash, summary, output_hash, coverage,
                started_at, completed_at, validated_at
            ) values (
                %s, %s, %s, 'imported_public_archive', 'shadow', %s::timestamptz,
                %s, %s, %s, %s, %s::timestamptz, %s::timestamptz, %s::timestamptz
            )
            on conflict (run_key) do update set summary = excluded.summary
            returning id
            """,
            (
                f"imported:phoenix1-public:{archive_hash}",
                phoenix1_id,
                archive_methodology_id,
                generated_at,
                archive_hash,
                Jsonb(dict(archive.get("summary") or {})),
                archive_hash,
                Jsonb(
                    {
                        "catalogCharts": archive_manifest.get("catalogCharts"),
                        "measuredCharts": archive_manifest.get("measuredCharts"),
                        "selectedContributions": archive_manifest.get("selectedContributions"),
                    }
                ),
                generated_at,
                generated_at,
                generated_at,
            ),
        )
        archive_run_id = cursor.fetchone()[0]
        cursor.execute(
            """
            insert into pumbility.methodologies (
                methodology_key, script_version, code_hash, configuration
            ) values ('phoenix1-score-overrides', 'current-imported-code', %s, %s)
            on conflict (methodology_key, script_version, code_hash) do update set
                configuration = excluded.configuration
            returning id
            """,
            (override_code_hash, Jsonb({"source": "phoenix1_score_overrides.py"})),
        )
        override_methodology_id = cursor.fetchone()[0]
        override_rows = phoenix1_score_overrides_metadata()
        cursor.executemany(
            """
            insert into pumbility.score_overrides (
                mix_id, override_key, methodology_id, parameters, provenance, valid_from
            ) values (%s, %s, %s, %s, %s, %s::timestamptz)
            on conflict (mix_id, override_key, methodology_id) do update set
                parameters = excluded.parameters,
                provenance = excluded.provenance
            """,
            [
                (
                    phoenix1_id,
                    str(row["chartId"]),
                    override_methodology_id,
                    Jsonb(dict(row)),
                    Jsonb({"sourceFile": "phoenix1_score_overrides.py"}),
                    generated_at,
                )
                for row in override_rows
            ],
        )
        cursor.execute("select id from pumbility.mixes where mix_key = 'phoenix2'")
        phoenix2_id = cursor.fetchone()[0]
        cursor.executemany(
            """
            insert into pumbility.chart_rerates (
                source_chart_id, target_chart_id, source_level, target_level, provenance
            )
            select source.id, target.id, %s, %s, %s
            from pumbility.charts source
            join pumbility.charts target on target.upstream_chart_id = source.upstream_chart_id
            where source.mix_id = %s and target.mix_id = %s and source.upstream_chart_id = %s
            on conflict (source_chart_id, target_chart_id) do update set
                source_level = excluded.source_level,
                target_level = excluded.target_level,
                provenance = excluded.provenance
            """,
            [
                (
                    int(str(row["from"])[1:]),
                    int(str(row["to"])[1:]),
                    Jsonb(dict(row)),
                    phoenix1_id,
                    phoenix2_id,
                    str(row["chartId"]),
                )
                for row in rerates
            ],
        )
        note_by_chart = dict(video_overrides.get("notes") or {})
        video_rows = [
            (chart_id, video_id, False)
            for chart_id, video_id in videos.items()
        ] + [
            (chart_id, video_id, True)
            for chart_id, video_id in (video_overrides.get("charts") or {}).items()
        ]
        cursor.executemany(
            """
            insert into pumbility.chart_videos (
                chart_id, video_id, video_url, is_override, matching_method, notes, provenance
            )
            select c.id, %s, %s, %s, %s, %s, %s
            from pumbility.charts c
            where c.mix_id = %s and c.upstream_chart_id = %s
            on conflict (chart_id, video_id) do update set
                is_override = excluded.is_override,
                matching_method = excluded.matching_method,
                notes = excluded.notes,
                provenance = excluded.provenance
            """,
            [
                (
                    video_id,
                    f"https://www.youtube.com/watch?v={video_id}",
                    is_override,
                    "manual_override" if is_override else "validated_catalog",
                    note_by_chart.get(chart_id),
                    Jsonb({"source": "nevsister", "override": is_override}),
                    phoenix2_id,
                    chart_id,
                )
                for chart_id, video_id, is_override in video_rows
            ],
        )
        alias_rows = [
            (title, alias)
            for title, aliases in (video_overrides.get("aliases") or {}).items()
            for alias in aliases
        ]
        cursor.executemany(
            """
            insert into pumbility.song_aliases (song_id, alias, normalized_alias, provenance)
            select s.id, %s, lower(btrim(%s)), %s
            from pumbility.songs s
            where s.mix_id = %s and s.title = %s
            on conflict (song_id, normalized_alias) do update set
                alias = excluded.alias,
                provenance = excluded.provenance
            """,
            [
                (
                    alias,
                    alias,
                    Jsonb({"source": "nevsister-chart-video-overrides.json"}),
                    phoenix2_id,
                    title,
                )
                for title, alias in alias_rows
            ],
        )
    return archive_run_id, {
        "scoreOverrides": len(override_rows),
        "rerates": len(rerates),
        "videos": len(video_rows),
        "aliases": len(alias_rows),
    }


def _import_json_artifacts(connection: Any, source_root: Path, archive_run_id: Any) -> int:
    """Import bounded JSON payloads; intentionally exclude multi-GB legacy shards."""
    from psycopg.types.json import Jsonb

    artifacts = {
        **{
            object_key: source_root / relative_path
            for object_key, relative_path in COMPATIBILITY_JSON_ARTIFACTS.items()
        },
        **REFERENCE_JSON_ARTIFACTS,
    }
    rows: list[tuple[Any, ...]] = []
    for object_key, path in artifacts.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Bounded JSON artifact {path.name} must contain an object.")
        body = _canonical_bytes(payload)
        rows.append(
            (
                object_key,
                Jsonb(payload),
                hashlib.sha256(body).hexdigest(),
                len(body),
                Jsonb({"source": "local-backfill", "sourceFile": path.name}),
                archive_run_id if object_key == "reference/phoenix1/public.json" else None,
            )
        )
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            insert into pumbility.artifacts (
                object_key, media_type, payload_json, sha256, byte_size,
                metadata, analysis_run_id, validated_at, updated_at
            ) values (%s, 'application/json', %s, %s, %s, %s, %s, now(), now())
            on conflict (object_key) do update set
                media_type = excluded.media_type,
                payload_json = excluded.payload_json,
                storage_bucket = null,
                storage_object_path = null,
                sha256 = excluded.sha256,
                byte_size = excluded.byte_size,
                metadata = excluded.metadata,
                analysis_run_id = excluded.analysis_run_id,
                validated_at = excluded.validated_at,
                updated_at = excluded.updated_at
            """,
            rows,
        )
    return len(rows)


def _import_mix(connection: Any, mix_key: str, manifest: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, int]:
    from psycopg.types.json import Jsonb

    spec = resolve_mix(mix_key)
    manifest_hash = hashlib.sha256(_canonical_bytes(manifest)).hexdigest()
    run_key = f"local:{mix_key}:{manifest_hash}"
    with connection.cursor() as cursor:
        cursor.execute(
            """
            insert into pumbility.data_sources (source_key, display_name, is_frozen, metadata)
            values ('piu-scores', 'PIU Scores', false, '{}'::jsonb)
            on conflict (source_key) do update set display_name = excluded.display_name
            returning id
            """
        )
        source_id = cursor.fetchone()[0]
        cursor.execute(
            """
            insert into pumbility.mixes (
                data_source_id, mix_key, upstream_value, display_name, archived
            ) values (%s, %s, %s, %s, %s)
            on conflict (mix_key) do update set
                upstream_value = excluded.upstream_value,
                display_name = excluded.display_name,
                archived = excluded.archived
            returning id
            """,
            (source_id, spec.key, spec.api_value, spec.label, spec.archived),
        )
        mix_id = cursor.fetchone()[0]
        cursor.execute(
            """
            insert into pumbility.consent_scopes (scope_key, description)
            values ('analysis', 'PIU Scores data shared for Pumbility analysis')
            on conflict (scope_key) do update set description = excluded.description
            returning id
            """
        )
        consent_scope_id = cursor.fetchone()[0]
        cursor.execute(
            """
            insert into pumbility.sync_runs (
                mix_id, run_key, kind, status, source_schema_version, source_manifest,
                capture_started_at, capture_completed_at, player_count, chart_count,
                score_count, content_hash
            ) values (
                %s, %s, 'import', 'ready', %s, %s,
                coalesce(%s::timestamptz, now()),
                coalesce(%s::timestamptz, now()),
                %s, %s, %s, %s
            )
            on conflict (run_key) do update set source_manifest = excluded.source_manifest
            returning id
            """,
            (
                mix_id,
                run_key,
                int(manifest.get("schemaVersion") or 0),
                Jsonb(dict(manifest)),
                manifest.get("captureStartedAtUtc"),
                manifest.get("captureCompletedAtUtc"),
                len(snapshot["players"]),
                len(snapshot["charts"]),
                len(snapshot["scores"]),
                manifest_hash,
            ),
        )
        sync_run_id = cursor.fetchone()[0]

        cursor.execute(
            """
            create temporary table pumbility_import_players (
                upstream_player_id text primary key,
                public_key text not null,
                username text not null,
                last_synced_at text,
                last_score_recorded_at text,
                payload text not null
            ) on commit drop
            """
        )
        cursor.execute(
            """
            create temporary table pumbility_import_charts (
                upstream_chart_id text primary key,
                source_song_key text not null,
                title text not null,
                chart_type text not null,
                level integer,
                difficulty text,
                step_artist text,
                image_url text,
                note_count integer,
                bpm_min double precision,
                bpm_max double precision,
                content_hash text not null,
                payload text not null
            ) on commit drop
            """
        )
        cursor.execute(
            """
            create temporary table pumbility_import_scores (
                upstream_player_id text not null,
                upstream_chart_id text not null,
                pumbility double precision not null,
                score double precision,
                letter_grade text,
                plate text,
                recorded_at_raw text not null,
                recorded_at text,
                is_broken boolean not null,
                row_hash text not null,
                primary key (upstream_player_id, upstream_chart_id)
            ) on commit drop
            """
        )

        _copy_rows(
            cursor,
            "copy pumbility_import_players from stdin",
            (
                (
                    row["playerId"],
                    public_player_key(row["playerId"]),
                    row.get("username") or "",
                    _timestamp(row.get("lastSyncedAtUtc")),
                    _timestamp(row.get("lastScoreRecordedAtUtc")),
                    json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True),
                )
                for row in snapshot["players"]
            ),
        )
        _copy_rows(
            cursor,
            "copy pumbility_import_charts from stdin",
            (
                (
                    row["id"],
                    str(row.get("songName") or ""),
                    str(row.get("songName") or ""),
                    str(row.get("type") or ""),
                    int(row["level"]) if row.get("level") is not None else None,
                    row.get("difficulty"),
                    row.get("stepArtist"),
                    row.get("imageUrl"),
                    int(row["noteCount"]) if row.get("noteCount") is not None else None,
                    row.get("bpmMin"),
                    row.get("bpmMax"),
                    _digest(row),
                    json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True),
                )
                for row in snapshot["charts"]
            ),
        )
        _copy_rows(
            cursor,
            "copy pumbility_import_scores from stdin",
            (
                (
                    row["playerId"],
                    row["chartId"],
                    row["pumbility"],
                    row.get("score"),
                    row.get("letterGrade"),
                    row.get("plate"),
                    str(row.get("recordedAt") or ""),
                    _timestamp(row.get("recordedAt")),
                    bool(row.get("isBroken", False)),
                    _digest(row),
                )
                for row in snapshot["scores"]
            ),
        )

        cursor.execute(
            """
            insert into pumbility.players (
                data_source_id, upstream_player_id, public_key, username, is_active,
                last_synced_at, last_score_recorded_at, metadata
            )
            select %s, upstream_player_id, public_key, username, true,
                   nullif(last_synced_at, '')::timestamptz,
                   nullif(last_score_recorded_at, '')::timestamptz,
                   payload::jsonb
            from pumbility_import_players
            on conflict (data_source_id, upstream_player_id) do update set
                public_key = excluded.public_key,
                username = excluded.username,
                is_active = true,
                last_synced_at = excluded.last_synced_at,
                last_score_recorded_at = excluded.last_score_recorded_at,
                metadata = excluded.metadata,
                updated_at = now()
            """,
            (source_id,),
        )
        cursor.execute(
            """
            insert into pumbility.player_mix_state (
                player_id, mix_id, last_synced_at, last_score_recorded_at,
                content_hash, metadata, updated_at
            )
            select p.id, %s,
                   nullif(t.last_synced_at, '')::timestamptz,
                   nullif(t.last_score_recorded_at, '')::timestamptz,
                   encode(sha256(convert_to(t.payload, 'UTF8')), 'hex'),
                   t.payload::jsonb, now()
            from pumbility_import_players t
            join pumbility.players p on p.data_source_id = %s
              and p.upstream_player_id = t.upstream_player_id
            on conflict (player_id, mix_id) do update set
                last_synced_at = excluded.last_synced_at,
                last_score_recorded_at = excluded.last_score_recorded_at,
                content_hash = excluded.content_hash,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (mix_id, source_id),
        )
        cursor.execute(
            """
            update pumbility.player_consents c
            set valid_to = now(), status = 'revoked'
            where c.mix_id = %s and c.consent_scope_id = %s and c.valid_to is null
              and not exists (
                select 1 from pumbility_import_players t
                join pumbility.players p on p.data_source_id = %s
                  and p.upstream_player_id = t.upstream_player_id
                where p.id = c.player_id
              )
            """,
            (mix_id, consent_scope_id, source_id),
        )
        cursor.execute(
            """
            insert into pumbility.player_consents (
                player_id, mix_id, consent_scope_id, status, valid_from,
                source_sync_run_id
            )
            select p.id, %s, %s, 'granted', now(), %s
            from pumbility_import_players t
            join pumbility.players p on p.data_source_id = %s
              and p.upstream_player_id = t.upstream_player_id
            where not exists (
                select 1 from pumbility.player_consents c
                where c.player_id = p.id and c.mix_id = %s
                  and c.consent_scope_id = %s and c.valid_to is null
            )
            """,
            (
                mix_id,
                consent_scope_id,
                sync_run_id,
                source_id,
                mix_id,
                consent_scope_id,
            ),
        )
        cursor.execute(
            """
            insert into pumbility.songs (mix_id, source_song_key, title, metadata)
            select %s, source_song_key, max(title), '{}'::jsonb
            from pumbility_import_charts group by source_song_key
            on conflict (mix_id, source_song_key) do update set
                title = excluded.title, updated_at = now()
            """,
            (mix_id,),
        )
        cursor.execute(
            """
            update pumbility.charts c set is_active = false, updated_at = now()
            where c.mix_id = %s and c.is_active
              and not exists (
                select 1 from pumbility_import_charts t
                where t.upstream_chart_id = c.upstream_chart_id
              )
            """,
            (mix_id,),
        )
        cursor.execute(
            """
            insert into pumbility.charts (
                mix_id, upstream_chart_id, song_id, chart_type, is_active
            )
            select %s, t.upstream_chart_id, s.id, t.chart_type, true
            from pumbility_import_charts t
            join pumbility.songs s on s.mix_id = %s and s.source_song_key = t.source_song_key
            on conflict (mix_id, upstream_chart_id) do update set
                song_id = excluded.song_id,
                chart_type = excluded.chart_type,
                is_active = true,
                updated_at = now()
            """,
            (mix_id, mix_id),
        )
        cursor.execute(
            """
            update pumbility.chart_revisions r
            set valid_to = now()
            from pumbility.charts c, pumbility_import_charts t
            where r.chart_id = c.id and c.mix_id = %s
              and c.upstream_chart_id = t.upstream_chart_id
              and r.valid_to is null and r.content_hash <> t.content_hash
            """,
            (mix_id,),
        )
        cursor.execute(
            """
            insert into pumbility.chart_revisions (
                chart_id, revision_number, level, difficulty, step_artist, image_url,
                note_count, bpm_min, bpm_max, payload, content_hash, valid_from,
                source_sync_run_id
            )
            select c.id,
                   coalesce((select max(old.revision_number) + 1 from pumbility.chart_revisions old where old.chart_id = c.id), 1),
                   t.level, t.difficulty, t.step_artist, t.image_url, t.note_count,
                   t.bpm_min, t.bpm_max, t.payload::jsonb, t.content_hash, now(), %s
            from pumbility_import_charts t
            join pumbility.charts c on c.mix_id = %s and c.upstream_chart_id = t.upstream_chart_id
            where not exists (
                select 1 from pumbility.chart_revisions current
                where current.chart_id = c.id and current.valid_to is null
            )
            """,
            (sync_run_id, mix_id),
        )
        cursor.execute(
            """
            update pumbility.score_revisions r
            set valid_to = now()
            where r.mix_id = %s and r.valid_to is null and (
                not exists (
                    select 1 from pumbility_import_scores t
                    join pumbility.players p on p.data_source_id = %s
                      and p.upstream_player_id = t.upstream_player_id
                    join pumbility.charts c on c.mix_id = %s
                      and c.upstream_chart_id = t.upstream_chart_id
                    where p.id = r.player_id and c.id = r.chart_id
                )
                or exists (
                    select 1 from pumbility_import_scores t
                    join pumbility.players p on p.data_source_id = %s
                      and p.upstream_player_id = t.upstream_player_id
                    join pumbility.charts c on c.mix_id = %s
                      and c.upstream_chart_id = t.upstream_chart_id
                    where p.id = r.player_id and c.id = r.chart_id and t.row_hash <> r.row_hash
                )
            )
            """,
            (mix_id, source_id, mix_id, source_id, mix_id),
        )
        cursor.execute(
            """
            insert into pumbility.score_revisions (
                mix_id, player_id, chart_id, pumbility, score, letter_grade, plate,
                recorded_at_raw, recorded_at, is_broken, payload, row_hash,
                valid_from, source_sync_run_id
            )
            select %s, p.id, c.id, t.pumbility, t.score, t.letter_grade, t.plate,
                   t.recorded_at_raw, nullif(t.recorded_at, '')::timestamptz,
                   t.is_broken, '{}'::jsonb, t.row_hash, now(), %s
            from pumbility_import_scores t
            join pumbility.players p on p.data_source_id = %s
              and p.upstream_player_id = t.upstream_player_id
            join pumbility.charts c on c.mix_id = %s
              and c.upstream_chart_id = t.upstream_chart_id
            where not exists (
                select 1 from pumbility.score_revisions current
                where current.mix_id = %s and current.player_id = p.id
                  and current.chart_id = c.id and current.valid_to is null
            )
            """,
            (
                mix_id,
                sync_run_id,
                source_id,
                mix_id,
                mix_id,
            ),
        )
        cursor.execute(
            """
            select
              (select count(*) from pumbility.player_consents where mix_id = %s and valid_to is null),
              (select count(*) from pumbility.charts where mix_id = %s and is_active),
              (select count(*) from pumbility.score_revisions where mix_id = %s and valid_to is null)
            """,
            (mix_id, mix_id, mix_id),
        )
        counts = tuple(int(value) for value in cursor.fetchone())
    expected = (
        len(snapshot["players"]),
        len(snapshot["charts"]),
        len(snapshot["scores"]),
    )
    if counts != expected:
        raise RuntimeError(f"Imported {mix_key} counts {counts} do not match source counts {expected}.")
    return {"players": counts[0], "charts": counts[1], "scores": counts[2]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env",
        default="PUMBILITY_DATABASE_URL",
        help="Environment variable containing the local direct PostgreSQL URL.",
    )
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument(
        "--mix",
        action="append",
        choices=sorted(MIX_SPECS),
        help="Mix to import; repeat for both. Defaults to both mixes.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mixes = args.mix or sorted(MIX_SPECS)
    loaded = {mix: _validated_snapshot(args.source_root, mix) for mix in mixes}
    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "validated",
                    "mixes": {
                        mix: {
                            "players": len(snapshot["players"]),
                            "charts": len(snapshot["charts"]),
                            "scores": len(snapshot["scores"]),
                        }
                        for mix, (_, snapshot) in loaded.items()
                    },
                },
                sort_keys=True,
            )
        )
        return 0
    database_url = os.getenv(args.database_url_env, "").strip()
    if not database_url:
        raise RuntimeError(f"{args.database_url_env} is not configured.")
    require_loopback_database_url(database_url)
    import psycopg

    results: dict[str, dict[str, int]] = {}
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            _assert_schema(cursor)
        connection.commit()
        for mix, (manifest, snapshot) in loaded.items():
            with connection.transaction():
                results[mix] = _import_mix(connection, mix, manifest, snapshot)
        with connection.transaction():
            archive_run_id, reference_counts = _import_reference_rows(connection)
            artifact_count = _import_json_artifacts(
                connection, args.source_root.resolve(), archive_run_id
            )
    print(
        json.dumps(
            {
                "status": "completed",
                "mixes": results,
                "boundedJsonArtifacts": artifact_count,
                "referenceRows": reference_counts,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Pumbility backfill failed safely; private database details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
