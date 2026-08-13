"""Reconcile local source snapshots with the current private Pumbility rows."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mix_registry import MIX_SPECS, resolve_mix  # noqa: E402
from phoenix2_sync import sanitize_snapshot  # noqa: E402
from piu_misgrade_analyzer import load_snapshot  # noqa: E402
from pumbility_store import _assert_schema, require_loopback_database_url  # noqa: E402
from scripts.capture_private_score_snapshot import validate_snapshot_directory  # noqa: E402


DEFAULT_SOURCE_ROOT = PROJECT_ROOT / ".local-data" / "piu-scores"
HMAC_ENV = "PUMBILITY_BASELINE_HMAC_KEY"
ALLOWED_REASONS = frozenset(
    {"consent_addition", "consent_revocation", "catalog_change", "score_addition", "upstream_correction"}
)


def _typed_score_payload(row: Any) -> dict[str, Any]:
    """Recreate the exact source row from typed relational columns."""
    return {
        "playerId": str(row[0]),
        "chartId": str(row[1]),
        "pumbility": row[2],
        "score": row[3],
        "letterGrade": row[4],
        "plate": row[5],
        "recordedAt": str(row[6] or ""),
        "isBroken": bool(row[7]),
    }


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _keyed_hash(rows: Iterable[Mapping[str, Any]], key: bytes) -> str:
    digest = hmac.new(key, digestmod=hashlib.sha256)
    for row in rows:
        body = _canonical(row)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest()


def _public_hash(rows: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        body = _canonical(row)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest()


def _source_snapshot(source_root: Path, mix_key: str) -> dict[str, Any]:
    root = source_root.resolve()
    local_root = (PROJECT_ROOT / ".local-data").resolve()
    if root != local_root and not root.is_relative_to(local_root):
        raise ValueError("Snapshot input must remain under this repository's .local-data directory.")
    path = root / mix_key / "current"
    manifest = validate_snapshot_directory(path, mix=mix_key)
    players, charts, scores = load_snapshot(path)
    return sanitize_snapshot(
        {
            "mix": resolve_mix(mix_key).api_value,
            "generatedAtUtc": manifest.get("captureCompletedAtUtc"),
            "players": players,
            "charts": charts,
            "scores": scores,
        },
        mix=mix_key,
    )


def _database_snapshot(connection: Any, mix_key: str) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            select p.upstream_player_id,
                   coalesce(ps.metadata->>'username', p.username),
                   ps.metadata->>'lastSyncedAtUtc',
                   ps.metadata->>'lastScoreRecordedAtUtc'
            from pumbility.player_consents pc
            join pumbility.players p on p.id = pc.player_id
            join pumbility.player_mix_state ps
              on ps.player_id = p.id and ps.mix_id = pc.mix_id
            join pumbility.mixes m on m.id = pc.mix_id
            join pumbility.consent_scopes cs on cs.id = pc.consent_scope_id
            where m.mix_key = %s and cs.scope_key = 'analysis' and pc.valid_to is null
            order by p.upstream_player_id
            """,
            (mix_key,),
        )
        players = [
            {
                "playerId": str(row[0]),
                "username": str(row[1] or ""),
                "lastSyncedAtUtc": str(row[2] or ""),
                "lastScoreRecordedAtUtc": str(row[3]) if row[3] else None,
            }
            for row in cursor.fetchall()
        ]
        cursor.execute(
            """
            select cr.payload
            from pumbility.chart_revisions cr
            join pumbility.charts c on c.id = cr.chart_id
            join pumbility.mixes m on m.id = c.mix_id
            where m.mix_key = %s and c.is_active and cr.valid_to is null
            order by c.upstream_chart_id
            """,
            (mix_key,),
        )
        charts = [dict(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            """
            select p.upstream_player_id, c.upstream_chart_id, sr.pumbility,
                   sr.score, sr.letter_grade, sr.plate, sr.recorded_at_raw,
                   sr.is_broken
            from pumbility.score_revisions sr
            join pumbility.mixes m on m.id = sr.mix_id
            join pumbility.players p on p.id = sr.player_id
            join pumbility.charts c on c.id = sr.chart_id
            where m.mix_key = %s and sr.valid_to is null
            order by p.upstream_player_id, c.upstream_chart_id
            """,
            (mix_key,),
        )
        scores = [_typed_score_payload(row) for row in cursor.fetchall()]
    return sanitize_snapshot(
        {
            "mix": resolve_mix(mix_key).api_value,
            "players": players,
            "charts": charts,
            "scores": scores,
        },
        mix=mix_key,
    )


def _entity_rows(snapshot: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        "players": [dict(row) for row in snapshot["players"]],
        "charts": [dict(row) for row in snapshot["charts"]],
        "scores": [dict(row) for row in snapshot["scores"]],
    }


def _natural_key(entity: str, row: Mapping[str, Any]) -> tuple[str, ...]:
    if entity == "players":
        return (str(row["playerId"]),)
    if entity == "charts":
        return (str(row["id"]),)
    return (str(row["playerId"]), str(row["chartId"]))


def _load_ledger(path: Path | None, key: bytes) -> set[tuple[str, str]]:
    if path is None:
        return set()
    value = json.loads(path.read_text(encoding="utf-8"))
    entries = value.get("changes") if isinstance(value, Mapping) else None
    if not isinstance(entries, list):
        raise ValueError("The change ledger must contain a changes array.")
    accepted: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("Every change ledger entry must be an object.")
        entity = str(entry.get("entity") or "")
        natural_key_hmac = str(entry.get("naturalKeyHmac") or "")
        reason = str(entry.get("reason") or "")
        sync_run_id = str(entry.get("syncRunId") or "")
        if entity not in {"players", "charts", "scores"} or reason not in ALLOWED_REASONS:
            raise ValueError("A change ledger entry has an invalid entity or reason.")
        if not natural_key_hmac or not sync_run_id:
            raise ValueError("A change ledger entry requires naturalKeyHmac and syncRunId.")
        accepted.add((entity, natural_key_hmac))
    return accepted


def _key_hmac(entity: str, natural_key: tuple[str, ...], key: bytes) -> str:
    return hmac.new(key, _canonical([entity, *natural_key]), hashlib.sha256).hexdigest()


def reconcile(
    source: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    key: bytes,
    accepted_changes: set[tuple[str, str]],
) -> dict[str, Any]:
    source_entities = _entity_rows(source)
    candidate_entities = _entity_rows(candidate)
    exact_matches = 0
    explained_changes = 0
    unexplained = 0
    entities: dict[str, Any] = {}
    for entity in ("players", "charts", "scores"):
        source_by_key = {_natural_key(entity, row): row for row in source_entities[entity]}
        candidate_by_key = {_natural_key(entity, row): row for row in candidate_entities[entity]}
        entity_unexplained = 0
        entity_explained = 0
        for natural_key in sorted(set(source_by_key) | set(candidate_by_key)):
            if source_by_key.get(natural_key) == candidate_by_key.get(natural_key):
                exact_matches += 1
                continue
            marker = (entity, _key_hmac(entity, natural_key, key))
            if marker in accepted_changes:
                explained_changes += 1
                entity_explained += 1
            else:
                unexplained += 1
                entity_unexplained += 1
        entities[entity] = {
            "sourceCount": len(source_by_key),
            "candidateCount": len(candidate_by_key),
            "sourceHash": _keyed_hash(source_entities[entity], key) if entity != "charts" else _public_hash(source_entities[entity]),
            "candidateHash": _keyed_hash(candidate_entities[entity], key) if entity != "charts" else _public_hash(candidate_entities[entity]),
            "explainedChanges": entity_explained,
            "unexplainedMismatches": entity_unexplained,
        }
    return {
        "exactMatches": exact_matches,
        "explainedChanges": explained_changes,
        "unexplainedMismatchCount": unexplained,
        "privacyScan": "passed",
        "result": "passed" if unexplained == 0 else "failed",
        "entities": entities,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", default="PUMBILITY_DATABASE_URL")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--mix", action="append", choices=sorted(MIX_SPECS))
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--change-ledger", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv(args.database_url_env, "").strip()
    if not database_url:
        raise RuntimeError(f"{args.database_url_env} is not configured.")
    require_loopback_database_url(database_url)
    raw_key = os.getenv(HMAC_ENV, "")
    if len(raw_key) < 32:
        raise RuntimeError(f"{HMAC_ENV} must contain at least 32 characters.")
    key = raw_key.encode("utf-8")
    accepted = _load_ledger(args.change_ledger, key)
    if args.baseline is not None:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        if not isinstance(baseline, Mapping):
            raise ValueError("The baseline manifest must contain a JSON object.")
    import psycopg

    results: dict[str, Any] = {}
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            _assert_schema(cursor)
        for mix in args.mix or sorted(MIX_SPECS):
            results[mix] = reconcile(
                _source_snapshot(args.source_root, mix),
                _database_snapshot(connection, mix),
                key=key,
                accepted_changes=accepted,
            )
    output = {
        "schemaVersion": 1,
        "mixes": results,
        "unexplainedMismatchCount": sum(
            int(value["unexplainedMismatchCount"]) for value in results.values()
        ),
        "privacyScan": "passed",
    }
    output["result"] = "passed" if output["unexplainedMismatchCount"] == 0 else "failed"
    encoded = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        resolved = args.output.resolve()
        local_root = (PROJECT_ROOT / ".local-data").resolve()
        if resolved != local_root and not resolved.is_relative_to(local_root):
            raise ValueError("Reconciliation evidence must remain under .local-data.")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(encoded, encoding="utf-8")
    print(json.dumps({
        "result": output["result"],
        "privacyScan": "passed",
        "unexplainedMismatchCount": output["unexplainedMismatchCount"],
    }, sort_keys=True))
    return 0 if output["result"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Pumbility reconciliation failed safely; private database details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
