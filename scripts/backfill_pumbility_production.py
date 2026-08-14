"""Backfill one stable production Blob boundary into the hosted Pumbility schema.

This is intentionally separate from the loopback-only local importer. The command
accepts credentials only through environment variables, fingerprints the one
approved Supabase project and least-privilege login, takes a session advisory
lock, and leaves the existing Vercel backend authoritative.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis_runtime import VercelPrivateBlobStore  # noqa: E402
from pumbility_store import (  # noqa: E402
    EXPECTED_PUMBILITY_MIGRATION,
    PumbilityArtifactStore,
    _assert_schema,
)
from scripts.backfill_pumbility_supabase import (  # noqa: E402
    _canonical_bytes,
    _copy_rows,
    _digest,
    _import_mix,
    _import_reference_rows,
    _timestamp,
)
from scripts.capture_pumbility_migration_baseline import (  # noqa: E402
    COMBINED_TIER_POINTER,
    PHOENIX1_PRIVATE_SNAPSHOT,
    PHOENIX2_ANALYSIS_POINTER,
    PHOENIX2_PRIVATE_SNAPSHOT,
    RECOMMENDATION_POINTER,
    _exact_json_bytes,
    _read_active_pointers,
    _recommendation_index_path,
    _recommendation_input_shard_path,
    _recommendation_model_path,
    _recommendation_score_model_path,
    _required_production_bytes,
    _required_production_json,
)


EXPECTED_PROJECT_REF = "gsiyqhkcgegjrvqcqioc"
EXPECTED_HOST = "aws-1-us-east-2.pooler.supabase.com"
EXPECTED_DATABASE = "postgres"
EXPECTED_LOGIN = "pumbility_runtime_login"
DATABASE_URL_ENV = "PUMBILITY_PRODUCTION_DATABASE_URL"
CONFIRMATION_ENV = "PUMBILITY_PRODUCTION_CONFIRMATION"
SUPABASE_URL_ENV = "PUMBILITY_SUPABASE_URL"
SERVICE_KEY_ENV = "PUMBILITY_SUPABASE_SERVICE_ROLE_KEY"
CONFIRMATION = (
    f"BACKFILL {EXPECTED_PROJECT_REF} {EXPECTED_PUMBILITY_MIGRATION}"
)
LOCK_NAME = "pumbility:production-backfill"
MAX_BOUNDARY_ATTEMPTS = 3
MAX_CACHED_PLAYER_OBJECTS = 10_000
FROZEN_SCORE_BATCH_SIZE = 5_000
GENERATION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
OPERATOR_PHASE = "startup"


def validate_production_database_url(
    database_url: str, *, expected_project_ref: str
) -> None:
    parsed = urlparse(database_url)
    query = parse_qs(parsed.query)
    expected_user = f"{EXPECTED_LOGIN}.{expected_project_ref}"
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("The production database URL must use PostgreSQL.")
    if (parsed.hostname or "").casefold() != EXPECTED_HOST:
        raise ValueError("Refusing an unapproved production database host.")
    if parsed.port != 5432:
        raise ValueError("Production backfill requires the session pooler on port 5432.")
    if parsed.username != expected_user:
        raise ValueError("Production backfill requires the dedicated Pumbility login.")
    if parsed.path != f"/{EXPECTED_DATABASE}":
        raise ValueError("Refusing an unexpected production database name.")
    if query.get("sslmode") != ["require"]:
        raise ValueError("The production database URL must set sslmode=require.")
    if not parsed.password:
        raise ValueError("The production database URL is missing its password.")


def _snapshot_manifest(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": int(snapshot.get("schemaVersion") or 0),
        "mix": snapshot.get("mix"),
        "captureStartedAtUtc": snapshot.get("generatedAtUtc"),
        "captureCompletedAtUtc": snapshot.get("generatedAtUtc"),
        "players": len(snapshot.get("players", [])),
        "charts": len(snapshot.get("charts", [])),
        "scoreRows": len(snapshot.get("scores", [])),
        "source": "production-vercel-stable-boundary",
    }


def _read_stable_boundary(
    store: Any, *, max_attempts: int = MAX_BOUNDARY_ATTEMPTS
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
    if max_attempts < 1 or max_attempts > MAX_BOUNDARY_ATTEMPTS:
        raise ValueError("The production boundary attempt count is outside the safe bound.")
    for attempt in range(max_attempts):
        first_pointers = _read_active_pointers(store)
        phoenix1 = _required_production_json(
            store, PHOENIX1_PRIVATE_SNAPSHOT, "frozen Phoenix 1 private snapshot"
        )
        phoenix2_first = _required_production_json(
            store, PHOENIX2_PRIVATE_SNAPSHOT, "mutable Phoenix 2 private snapshot"
        )
        phoenix2_second = _required_production_json(
            store, PHOENIX2_PRIVATE_SNAPSHOT, "mutable Phoenix 2 private snapshot"
        )
        second_pointers = _read_active_pointers(store)
        if (
            _exact_json_bytes(phoenix2_first) == _exact_json_bytes(phoenix2_second)
            and _exact_json_bytes(first_pointers) == _exact_json_bytes(second_pointers)
        ):
            return first_pointers, phoenix1, phoenix2_first
        if attempt + 1 == max_attempts:
            break
    raise RuntimeError("The production publication boundary did not stabilize.")


def _assert_boundary_unchanged(
    store: Any,
    pointers: Mapping[str, Mapping[str, Any]],
    phoenix2: Mapping[str, Any],
) -> None:
    latest_snapshot = _required_production_json(
        store, PHOENIX2_PRIVATE_SNAPSHOT, "mutable Phoenix 2 private snapshot"
    )
    latest_pointers = _read_active_pointers(store)
    if (
        _exact_json_bytes(latest_snapshot) != _exact_json_bytes(phoenix2)
        or _exact_json_bytes(latest_pointers) != _exact_json_bytes(pointers)
    ):
        raise RuntimeError(
            "Production advanced during backfill; rerun the idempotent command before parity approval."
        )


def _assert_database_target(cursor: Any) -> None:
    cursor.execute(
        """
        select current_database(), current_user, value,
               r.rolcanlogin, r.rolsuper, r.rolbypassrls,
               pg_has_role(current_user, 'pumbility_worker', 'member')
        from pumbility.schema_metadata
        cross join pg_catalog.pg_roles r
        where key = 'migration_version' and r.rolname = current_user
        """
    )
    row = cursor.fetchone()
    expected = (
        EXPECTED_DATABASE,
        EXPECTED_LOGIN,
        EXPECTED_PUMBILITY_MIGRATION,
        True,
        False,
        False,
        True,
    )
    if row is None or tuple(row) != expected:
        raise RuntimeError("The hosted database fingerprint or role privileges do not match.")


def _claim_lock(cursor: Any) -> None:
    cursor.execute(
        "select pg_try_advisory_lock(hashtextextended(%s, 0))", (LOCK_NAME,)
    )
    if cursor.fetchone()[0] is not True:
        raise RuntimeError("Another Pumbility production backfill owns the operator lock.")
    cursor.execute("set application_name = 'pumbility-production-backfill'")
    cursor.execute("set lock_timeout = '10s'")
    # Keep database work below the hosted operator function's 800-second ceiling.
    cursor.execute("set statement_timeout = '12min'")
    cursor.execute("set idle_in_transaction_session_timeout = '60s'")


def _release_lock(cursor: Any) -> None:
    cursor.execute("select pg_advisory_unlock(hashtextextended(%s, 0))", (LOCK_NAME,))


def _recommendation_paths(index: Mapping[str, Any]) -> tuple[list[str], str]:
    generation = str(index.get("generationKey") or "")
    if not GENERATION_RE.fullmatch(generation):
        raise RuntimeError("The active recommendation generation key is invalid.")
    if int(index.get("storageSchemaVersion") or 0) != 3:
        raise RuntimeError("Production backfill requires the current schema-3 recommendation model.")
    shard_count = int(index.get("inputShardCount") or 0)
    if shard_count < 1 or shard_count > 1000:
        raise RuntimeError("The recommendation input shard count is outside the safe bound.")
    json_paths = [
        _recommendation_index_path(generation),
        _recommendation_model_path(generation),
    ]
    for shard in range(shard_count):
        json_paths.extend(
            (
                _recommendation_input_shard_path(generation, "phoenix1", shard),
                _recommendation_input_shard_path(generation, "phoenix2", shard),
            )
        )
    return json_paths, _recommendation_score_model_path(generation)


def _copy_cached_players(source: Any, target: PumbilityArtifactStore) -> int:
    copied = 0
    for prefix in (
        "analysis/private/recommendation-player-state/",
        "analysis/recommendations/players/",
    ):
        objects: Sequence[Any] = source.list(prefix)
        if len(objects) > MAX_CACHED_PLAYER_OBJECTS:
            raise RuntimeError("The cached-player object count is outside the safe bound.")
        source_paths = {str(item.pathname) for item in objects}
        target_paths = {str(item.pathname) for item in target.list(prefix)}
        stale_paths = sorted(target_paths - source_paths)
        if stale_paths:
            target.delete(stale_paths)
        for item in objects:
            pathname = str(item.pathname)
            payload = _required_production_json(source, pathname, "cached player artifact")
            target.put_json(pathname, payload)
            copied += 1
    return copied


def _copy_active_artifacts(
    source: Any,
    target: PumbilityArtifactStore,
    pointers: Mapping[str, Mapping[str, Any]],
) -> dict[str, int]:
    index = pointers["recommendations"]
    json_paths, numeric_path = _recommendation_paths(index)
    for pathname in json_paths:
        target.put_json(
            pathname,
            _required_production_json(source, pathname, "recommendation artifact"),
        )
    numeric = _required_production_bytes(
        source, numeric_path, "recommendation numeric model"
    )
    target.put_bytes(numeric_path, numeric, content_type="application/x-npz")
    phoenix1_public = json.loads(
        (PROJECT_ROOT / "public/data/phoenix1.json").read_text(encoding="utf-8")
    )
    target.put_json_bundle(
        {
            "analysis/phoenix1/latest.json": phoenix1_public,
            PHOENIX2_ANALYSIS_POINTER: pointers["phoenix2Analysis"],
            COMBINED_TIER_POINTER: pointers["combinedTier"],
            RECOMMENDATION_POINTER: index,
        }
    )
    return {
        "jsonArtifacts": len(json_paths) + 4,
        "binaryArtifacts": 1,
        "cachedPlayerArtifacts": _copy_cached_players(source, target),
    }


def _import_frozen_phoenix1(
    connection: Any,
    manifest: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> dict[str, int]:
    """Import the large frozen score set in restartable bounded transactions."""
    manifest_hash = hashlib.sha256(_canonical_bytes(manifest)).hexdigest()
    expected = (
        len(snapshot["players"]),
        len(snapshot["charts"]),
        len(snapshot["scores"]),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            select
              (select count(*) from pumbility.player_consents pc where pc.mix_id=m.id and pc.valid_to is null),
              (select count(*) from pumbility.charts c where c.mix_id=m.id and c.is_active),
              (select count(*) from pumbility.score_revisions sr where sr.mix_id=m.id and sr.valid_to is null)
            from pumbility.mixes m where m.mix_key='phoenix1'
            """
        )
        current = tuple(int(value) for value in cursor.fetchone())
    connection.commit()
    if current[0:2] == (0, 0) and current[2] == 0:
        metadata_only = dict(snapshot)
        metadata_only["scores"] = []
        with connection.transaction():
            _import_mix(connection, "phoenix1", manifest, metadata_only)
    elif current[0:2] != expected[0:2] or current[2] > expected[2]:
        raise RuntimeError("The frozen Phoenix 1 resume boundary is inconsistent.")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            select ds.id, m.id, r.id
            from pumbility.data_sources ds
            join pumbility.mixes m on m.data_source_id=ds.id and m.mix_key='phoenix1'
            join pumbility.sync_runs r on r.mix_id=m.id and r.content_hash=%s
            where ds.source_key='piu-scores'
            order by r.created_at desc limit 1
            """,
            (manifest_hash,),
        )
        identifiers = cursor.fetchone()
    connection.commit()
    if identifiers is None:
        raise RuntimeError("The frozen Phoenix 1 import run is unavailable for resume.")
    source_id, mix_id, sync_run_id = identifiers

    scores = snapshot["scores"]
    for start in range(0, len(scores), FROZEN_SCORE_BATCH_SIZE):
        batch = scores[start : start + FROZEN_SCORE_BATCH_SIZE]
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    create temporary table pumbility_import_score_batch (
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
                    "copy pumbility_import_score_batch from stdin",
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
                        for row in batch
                    ),
                )
                cursor.execute(
                    """
                    update pumbility.score_revisions sr set valid_to=now()
                    from pumbility_import_score_batch t
                    join pumbility.players p on p.data_source_id=%s and p.upstream_player_id=t.upstream_player_id
                    join pumbility.charts c on c.mix_id=%s and c.upstream_chart_id=t.upstream_chart_id
                    where sr.mix_id=%s and sr.player_id=p.id and sr.chart_id=c.id
                      and sr.valid_to is null and sr.row_hash<>t.row_hash
                    """,
                    (source_id, mix_id, mix_id),
                )
                cursor.execute(
                    """
                    insert into pumbility.score_revisions (
                        mix_id, player_id, chart_id, pumbility, score, letter_grade, plate,
                        recorded_at_raw, recorded_at, is_broken, payload, row_hash,
                        valid_from, source_sync_run_id
                    )
                    select %s, p.id, c.id, t.pumbility, t.score, t.letter_grade, t.plate,
                           t.recorded_at_raw, nullif(t.recorded_at,'')::timestamptz,
                           t.is_broken, '{}'::jsonb, t.row_hash, now(), %s
                    from pumbility_import_score_batch t
                    join pumbility.players p on p.data_source_id=%s and p.upstream_player_id=t.upstream_player_id
                    join pumbility.charts c on c.mix_id=%s and c.upstream_chart_id=t.upstream_chart_id
                    where not exists (
                        select 1 from pumbility.score_revisions current
                        where current.mix_id=%s and current.player_id=p.id
                          and current.chart_id=c.id and current.valid_to is null
                    )
                    """,
                    (mix_id, sync_run_id, source_id, mix_id, mix_id),
                )
        if start == 0 or start + len(batch) == len(scores) or start % 100_000 == 0:
            print(
                json.dumps(
                    {
                        "status": "importing",
                        "mix": "phoenix1",
                        "scoresProcessed": start + len(batch),
                        "scoresTotal": len(scores),
                    },
                    sort_keys=True,
                )
            )

    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "select count(*) from pumbility.score_revisions where mix_id=%s and valid_to is null",
                (mix_id,),
            )
            score_count = int(cursor.fetchone()[0])
            if score_count != expected[2]:
                raise RuntimeError("The frozen Phoenix 1 score count is incomplete after resume.")
            cursor.execute(
                "update pumbility.sync_runs set score_count=%s, updated_at=now() where id=%s",
                (score_count, sync_run_id),
            )
    return {"players": expected[0], "charts": expected[1], "scores": score_count}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-project-ref", required=True)
    parser.add_argument(
        "--database-url-env",
        default=DATABASE_URL_ENV,
        help="Environment variable holding the session-pooler URL; never pass the URL itself.",
    )
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    global OPERATOR_PHASE
    OPERATOR_PHASE = "validate-request"
    args = build_parser().parse_args(argv)
    if args.expected_project_ref != EXPECTED_PROJECT_REF:
        raise RuntimeError("The requested Supabase project is not the approved project.")
    database_url = os.getenv(args.database_url_env, "").strip()
    if not database_url:
        raise RuntimeError(f"{args.database_url_env} is not configured.")
    validate_production_database_url(
        database_url, expected_project_ref=args.expected_project_ref
    )
    if args.apply and os.getenv(CONFIRMATION_ENV, "") != CONFIRMATION:
        raise RuntimeError(f"{CONFIRMATION_ENV} does not match the required confirmation.")

    OPERATOR_PHASE = "source-boundary"
    source = VercelPrivateBlobStore()
    pointers, phoenix1, phoenix2 = _read_stable_boundary(source)
    counts = {
        "phoenix1": {
            "players": len(phoenix1.get("players", [])),
            "charts": len(phoenix1.get("charts", [])),
            "scores": len(phoenix1.get("scores", [])),
        },
        "phoenix2": {
            "players": len(phoenix2.get("players", [])),
            "charts": len(phoenix2.get("charts", [])),
            "scores": len(phoenix2.get("scores", [])),
        },
    }
    if not args.apply:
        OPERATOR_PHASE = "planned"
        print(json.dumps({"status": "planned", "mixes": counts}, sort_keys=True))
        return 0

    import psycopg

    results: dict[str, Any] = {}
    OPERATOR_PHASE = "database-target-and-lock"
    with psycopg.connect(database_url, prepare_threshold=None) as connection:
        with connection.cursor() as cursor:
            _assert_schema(cursor)
            _assert_database_target(cursor)
            _claim_lock(cursor)
        connection.commit()
        try:
            OPERATOR_PHASE = "phoenix1"
            results["phoenix1"] = _import_frozen_phoenix1(
                connection, _snapshot_manifest(phoenix1), phoenix1
            )
            print(json.dumps({"status": "stage-completed", "stage": "phoenix1"}, sort_keys=True))
            OPERATOR_PHASE = "phoenix2"
            with connection.transaction():
                results["phoenix2"] = _import_mix(
                    connection, "phoenix2", _snapshot_manifest(phoenix2), phoenix2
                )
            print(json.dumps({"status": "stage-completed", "stage": "phoenix2"}, sort_keys=True))
            OPERATOR_PHASE = "references"
            with connection.transaction():
                _, reference_counts = _import_reference_rows(connection)
            print(json.dumps({"status": "stage-completed", "stage": "references"}, sort_keys=True))
            OPERATOR_PHASE = "artifacts"
            target = PumbilityArtifactStore(database_url=database_url)
            artifact_counts = _copy_active_artifacts(source, target, pointers)
            print(json.dumps({"status": "stage-completed", "stage": "artifacts"}, sort_keys=True))
            OPERATOR_PHASE = "boundary"
            _assert_boundary_unchanged(source, pointers, phoenix2)
            print(json.dumps({"status": "stage-completed", "stage": "boundary"}, sort_keys=True))
        finally:
            with connection.cursor() as cursor:
                _release_lock(cursor)
            connection.commit()

    OPERATOR_PHASE = "completed"
    print(
        json.dumps(
            {
                "status": "completed",
                "mixes": results,
                "referenceRows": reference_counts,
                "artifacts": artifact_counts,
                "productionBackendChanged": False,
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
            "Pumbility production backfill failed safely; private details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
