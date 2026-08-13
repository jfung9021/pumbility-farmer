"""Backfill one stable production Blob boundary into the hosted Pumbility schema.

This is intentionally separate from the loopback-only local importer. The command
accepts credentials only through environment variables, fingerprints the one
approved Supabase project and least-privilege login, takes a session advisory
lock, and leaves the existing Vercel backend authoritative.
"""

from __future__ import annotations

import argparse
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
    _import_mix,
    _import_reference_rows,
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
GENERATION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


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
    cursor.execute("set statement_timeout = '15min'")
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
        print(json.dumps({"status": "planned", "mixes": counts}, sort_keys=True))
        return 0

    import psycopg

    results: dict[str, Any] = {}
    with psycopg.connect(database_url, prepare_threshold=None) as connection:
        with connection.cursor() as cursor:
            _assert_schema(cursor)
            _assert_database_target(cursor)
            _claim_lock(cursor)
        connection.commit()
        try:
            for mix_key, snapshot in (("phoenix1", phoenix1), ("phoenix2", phoenix2)):
                with connection.transaction():
                    results[mix_key] = _import_mix(
                        connection, mix_key, _snapshot_manifest(snapshot), snapshot
                    )
            with connection.transaction():
                _, reference_counts = _import_reference_rows(connection)
            target = PumbilityArtifactStore(database_url=database_url)
            artifact_counts = _copy_active_artifacts(source, target, pointers)
            _assert_boundary_unchanged(source, pointers, phoenix2)
        finally:
            with connection.cursor() as cursor:
                _release_lock(cursor)
            connection.commit()

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
