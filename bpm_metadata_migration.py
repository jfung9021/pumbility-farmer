"""Pinned metadata-only BPM migration; never reconciles players or scores.

The dry-run plan covers every active Phoenix 2 chart.  Applying it requires the
same active roster, source revision and chart payloads, under the publication
lock.  Prepared release pointers may be switched in that same transaction.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence

from pumbility_store import (
    PumbilityArtifactStore, TIER_PUBLICATION_LOCK, _assert_schema,
    _canonical_json_bytes, _json_metadata, bump_tier_source_revision,
)
from song_bpm_metadata import (
    enrich_chart_bpm, load_runtime_catalog, validate_runtime_catalog,
)

METADATA_FIELDS = ("songType", "bpmMin", "bpmMax", "bpmFocal")
IDENTITY_FIELDS = ("bpmMetadataVersion", "bpmPolicy", "bpmMetadataRevision")
SOURCE_REVISION_PATH = "analysis/private/source-revisions/phoenix2.json"
BPM_REVISION_PATH = "analysis/private/bpm-metadata/phoenix2.json"


class BpmMigrationConflict(ValueError):
    """Pinned inputs differ from the prepared migration/release."""


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _identity(catalog: Mapping) -> dict:
    return {field: catalog[field] for field in IDENTITY_FIELDS}


def _metadata(payload: Mapping) -> dict:
    return {field: payload.get(field) for field in METADATA_FIELDS}


def _target_payload(payload: Mapping, catalog: Mapping) -> dict:
    enriched = enrich_chart_bpm(payload, mix="Phoenix2", catalog=catalog)
    if enriched.get("bpmFocal") is None:
        raise BpmMigrationConflict(f"No focal BPM decision for chart {payload.get('id')!r}")
    result = dict(payload)
    for field in METADATA_FIELDS:
        if field in enriched:
            result[field] = enriched[field]
    if {key: value for key, value in result.items() if key not in METADATA_FIELDS} != {
        key: value for key, value in payload.items() if key not in METADATA_FIELDS
    }:
        raise BpmMigrationConflict("The migration would modify non-BPM chart metadata")
    return result


def read_active_chart_rows(cursor: Any, *, lock: bool = False) -> list[dict]:
    """Read the complete active roster, detecting charts without a current revision."""
    cursor.execute("""select c.id::text,c.upstream_chart_id
        from pumbility.charts c join pumbility.mixes m on m.id=c.mix_id
        where m.mix_key=%s and c.is_active order by c.upstream_chart_id"""
        + (" for update of c" if lock else ""), ("phoenix2",))
    active = [(str(row[0]), str(row[1])) for row in cursor.fetchall()]
    cursor.execute("""select c.id::text,c.upstream_chart_id,cr.revision_number,
        cr.content_hash,cr.payload,cr.bpm_min,cr.bpm_max
        from pumbility.charts c join pumbility.mixes m on m.id=c.mix_id
        join pumbility.chart_revisions cr on cr.chart_id=c.id and cr.valid_to is null
        where m.mix_key=%s and c.is_active order by c.upstream_chart_id"""
        + (" for update of cr" if lock else ""), ("phoenix2",))
    records = [{"chartDbId": str(row[0]), "chartId": str(row[1]), "revisionNumber": int(row[2]),
                "contentHash": str(row[3]), "payload": dict(row[4]),
                "bpmMin": row[5], "bpmMax": row[6]} for row in cursor.fetchall()]
    if not active or active != [(row["chartDbId"], row["chartId"]) for row in records]:
        raise BpmMigrationConflict("The complete active Phoenix 2 roster has missing/duplicate current revisions")
    return records


def read_artifact_revisions(cursor: Any, paths: Sequence[str], *, lock: bool = False) -> dict:
    if not paths:
        return {}
    cursor.execute("""select object_key,sha256,byte_size,xmin::text
        from pumbility.artifacts where object_key=any(%s) order by object_key"""
        + (" for update" if lock else ""), (list(paths),))
    return {str(row[0]): {"sha256": str(row[1]), "byteSize": int(row[2]), "revision": str(row[3])}
            for row in cursor.fetchall()}


def _prepared_artifacts(prepared: Mapping | None, identity: Mapping) -> dict:
    result = {}
    for path, entry in sorted((prepared or {}).items()):
        if not isinstance(path, str) or not path.startswith("analysis/") or path in {SOURCE_REVISION_PATH, BPM_REVISION_PATH}:
            raise BpmMigrationConflict("Prepared artifact must be an explicit analysis pointer, not a reserved source token")
        if not isinstance(entry, Mapping) or not isinstance(entry.get("payload"), Mapping):
            raise BpmMigrationConflict("Prepared artifact requires a JSON object payload")
        payload = dict(entry["payload"])
        payload_identity = payload.get("bpmMetadata", payload)
        if not isinstance(payload_identity, Mapping) or {key: payload_identity.get(key) for key in IDENTITY_FIELDS} != dict(identity):
            raise BpmMigrationConflict(f"Prepared artifact {path!r} has a different BPM metadata identity")
        digest, size = _json_metadata(payload, pathname=path)
        if entry.get("sha256", digest) != digest:
            raise BpmMigrationConflict("Prepared artifact payload does not match its expected hash")
        expected = entry.get("expectedRevision")
        if expected is not None and (not isinstance(expected, Mapping) or set(expected) != {"sha256", "byteSize", "revision"}):
            raise BpmMigrationConflict("Prepared artifact expectedRevision is malformed")
        result[path] = {"payload": payload, "sha256": digest, "byteSize": size,
                        "expectedRevision": dict(expected) if expected is not None else None}
    return result


def build_migration_plan(records: Sequence[Mapping], *, source_revision: Mapping | None,
                         prepared_artifacts: Mapping | None = None, catalog: Mapping | None = None) -> dict:
    document = load_runtime_catalog() if catalog is None else validate_runtime_catalog(catalog)
    identity = _identity(document)
    rows = []
    seen_ids, seen_db_ids = set(), set()
    for record in sorted(records, key=lambda row: row["chartId"]):
        chart_id, db_id = record["chartId"], record["chartDbId"]
        if not chart_id or not db_id or chart_id in seen_ids or db_id in seen_db_ids:
            raise BpmMigrationConflict("Migration roster IDs must be nonempty and unique")
        seen_ids.add(chart_id)
        seen_db_ids.add(db_id)
        payload = dict(record["payload"])
        if payload.get("id") != chart_id:
            raise BpmMigrationConflict("Stored chart payload identity differs from its relational chart")
        old_hash = _digest(payload)
        if record["contentHash"] != old_hash:
            raise BpmMigrationConflict("Stored chart content_hash differs from canonical payload hash")
        target = _target_payload(payload, document)
        changed = (old_hash != _digest(target)
                   or (record.get("bpmMin"), record.get("bpmMax")) != (target["bpmMin"], target["bpmMax"]))
        rows.append({
            "chartDbId": db_id, "chartId": chart_id, "revisionNumber": int(record["revisionNumber"]),
            "expectedContentHash": old_hash, "beforePayloadSha256": old_hash,
            "afterPayloadSha256": _digest(target), "before": _metadata(payload), "after": _metadata(target),
            "typedBefore": {"bpmMin": record.get("bpmMin"), "bpmMax": record.get("bpmMax")},
            "changed": changed,
        })
    if not rows:
        raise BpmMigrationConflict("Cannot prepare an empty active-chart migration")
    result = {
        "schemaVersion": 1, "kind": "phoenix2-bpm-metadata-only", "mix": "Phoenix2",
        **identity, "sourceRevision": dict(source_revision) if source_revision is not None else None,
        "artifacts": _prepared_artifacts(prepared_artifacts, identity),
        "summary": {"activeCharts": len(rows), "changedCharts": sum(row["changed"] for row in rows)},
        "charts": rows,
    }
    result["planSha256"] = _digest(result)
    return result


def prepare_migration(connection: Any, *, prepared_artifacts: Mapping | None = None,
                      catalog: Mapping | None = None) -> dict:
    with connection.transaction(), connection.cursor() as cursor:
        _assert_schema(cursor)
        cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (TIER_PUBLICATION_LOCK,))
        records = read_active_chart_rows(cursor)
        paths = [SOURCE_REVISION_PATH, *(prepared_artifacts or {})]
        revisions = read_artifact_revisions(cursor, paths)
        for path, entry in (prepared_artifacts or {}).items():
            if revisions.get(path) != entry.get("expectedRevision"):
                raise BpmMigrationConflict("A prepared publication pointer changed before planning")
        return build_migration_plan(records, source_revision=revisions.get(SOURCE_REVISION_PATH),
                                    prepared_artifacts=prepared_artifacts, catalog=catalog)


def _validate_plan(plan: Mapping, catalog: Mapping) -> None:
    unsigned = {key: value for key, value in plan.items() if key != "planSha256"}
    if (plan.get("schemaVersion") != 1 or plan.get("kind") != "phoenix2-bpm-metadata-only"
            or plan.get("mix") != "Phoenix2" or plan.get("planSha256") != _digest(unsigned)):
        raise BpmMigrationConflict("The saved migration plan is invalid or was modified")
    if {key: plan.get(key) for key in IDENTITY_FIELDS} != _identity(catalog):
        raise BpmMigrationConflict("The saved plan targets a different compiled BPM metadata revision")
    _prepared_artifacts(plan.get("artifacts"), _identity(catalog))
    rows = plan.get("charts")
    if (not isinstance(rows, list) or not rows
            or len({row["chartId"] for row in rows}) != len(rows)
            or len({row["chartDbId"] for row in rows}) != len(rows)
            or plan.get("summary") != {"activeCharts": len(rows), "changedCharts": sum(row["changed"] for row in rows)}):
        raise BpmMigrationConflict("The saved plan does not pin a unique complete chart roster")


def apply_migration(connection: Any, plan: Mapping, *, catalog: Mapping | None = None) -> dict:
    document = load_runtime_catalog() if catalog is None else validate_runtime_catalog(catalog)
    _validate_plan(plan, document)
    with connection.transaction(), connection.cursor() as cursor:
        _assert_schema(cursor)
        cursor.execute("set local lock_timeout = '5000ms'")
        cursor.execute("set local statement_timeout = '180000ms'")
        cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (TIER_PUBLICATION_LOCK,))
        actual = read_active_chart_rows(cursor, lock=True)
        actual_by_id = {row["chartId"]: row for row in actual}
        expected_by_id = {row["chartId"]: row for row in plan["charts"]}
        if set(actual_by_id) != set(expected_by_id) or any(actual_by_id[key]["chartDbId"] != expected_by_id[key]["chartDbId"] for key in expected_by_id):
            raise BpmMigrationConflict("The active Phoenix 2 chart set changed since the dry run")
        artifacts = plan.get("artifacts", {})
        revisions = read_artifact_revisions(cursor, [SOURCE_REVISION_PATH, *artifacts], lock=True)
        # An identical rerun does not create another temporal revision or token.
        rows_done = all(
            _digest(actual_by_id[key]["payload"]) == saved["afterPayloadSha256"]
            and actual_by_id[key]["contentHash"] == saved["afterPayloadSha256"]
            and (actual_by_id[key].get("bpmMin"), actual_by_id[key].get("bpmMax")) == (saved["after"]["bpmMin"], saved["after"]["bpmMax"])
            for key, saved in expected_by_id.items()
        )
        artifacts_done = all(revisions.get(path, {}).get("sha256") == entry["sha256"] for path, entry in artifacts.items())
        if rows_done and artifacts_done:
            return {"changedCharts": 0, "promotedArtifacts": 0, "noop": True, **_identity(document)}
        if revisions.get(SOURCE_REVISION_PATH) != plan.get("sourceRevision"):
            raise BpmMigrationConflict("The relational source revision changed since the dry run")
        for path, entry in artifacts.items():
            if revisions.get(path) != entry["expectedRevision"]:
                raise BpmMigrationConflict("A release pointer changed since the dry run")
        changed = []
        for key, saved in expected_by_id.items():
            row = actual_by_id[key]
            if (row["revisionNumber"] != saved["revisionNumber"] or row["contentHash"] != saved["expectedContentHash"]
                    or _digest(row["payload"]) != saved["beforePayloadSha256"]
                    or {"bpmMin": row.get("bpmMin"), "bpmMax": row.get("bpmMax")} != saved["typedBefore"]):
                raise BpmMigrationConflict("A pinned chart revision/payload changed since the dry run")
            target = _target_payload(row["payload"], document)
            if _digest(target) != saved["afterPayloadSha256"] or _metadata(target) != saved["after"]:
                raise BpmMigrationConflict("A saved target disagrees with the compiled metadata resolver")
            if saved["changed"]:
                changed.append((row, target))
        for row, target in changed:
            # INSERT ... SELECT copies every existing non-BPM typed column and
            # source_sync_run_id.  It never modifies charts, players or scores.
            cursor.execute("""update pumbility.chart_revisions set valid_to=now()
                where chart_id=%s::uuid and revision_number=%s and content_hash=%s and valid_to is null""",
                (row["chartDbId"], row["revisionNumber"], row["contentHash"]))
            if cursor.rowcount != 1:
                raise BpmMigrationConflict("A pinned chart revision could not be closed")
            cursor.execute("""insert into pumbility.chart_revisions
                (chart_id,revision_number,level,difficulty,step_artist,image_url,note_count,
                 bpm_min,bpm_max,payload,content_hash,valid_from,source_sync_run_id)
                select chart_id,revision_number+1,level,difficulty,step_artist,image_url,note_count,
                    %s,%s,%s::jsonb,%s,now(),source_sync_run_id
                from pumbility.chart_revisions where chart_id=%s::uuid and revision_number=%s and content_hash=%s""",
                (target["bpmMin"], target["bpmMax"], _canonical_json_bytes(target).decode("utf-8"), _digest(target),
                 row["chartDbId"], row["revisionNumber"], row["contentHash"]))
            if cursor.rowcount != 1:
                raise BpmMigrationConflict("A metadata-only chart revision could not be inserted")
        if changed:
            bump_tier_source_revision(cursor, "phoenix2")
            PumbilityArtifactStore._put_json_row(cursor, BPM_REVISION_PATH, _identity(document))
        for path, entry in artifacts.items():
            PumbilityArtifactStore._put_json_row(cursor, path, entry["payload"])
        return {"changedCharts": len(changed), "promotedArtifacts": len(artifacts),
                "noop": not changed and not artifacts, **_identity(document)}


def stage_local_snapshot(source: Path, destination: Path, *, catalog: Mapping | None = None) -> dict:
    """Create an unpublished candidate while preserving players/scores byte-for-byte."""
    from scripts.capture_private_score_snapshot import validate_snapshot_directory
    source, destination = source.resolve(), destination.resolve()
    if destination == source or source in destination.parents or destination.exists():
        raise BpmMigrationConflict("Snapshot staging requires a separate, nonexistent candidate directory")
    original_manifest = validate_snapshot_directory(source, mix="Phoenix2")
    document = load_runtime_catalog() if catalog is None else validate_runtime_catalog(catalog)
    charts = json.loads((source / "charts.json").read_text(encoding="utf-8"))
    resolved = [_target_payload(row, document) for row in charts]
    destination.mkdir(parents=True)
    for filename in ("players.json", "scores.jsonl.gz"):
        shutil.copyfile(source / filename, destination / filename)
    (destination / "charts.json").write_text(json.dumps(resolved, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    hashes = {filename: hashlib.sha256((destination / filename).read_bytes()).hexdigest()
              for filename in ("players.json", "charts.json", "scores.jsonl.gz")}
    for filename in ("players.json", "scores.jsonl.gz"):
        if hashes[filename] != original_manifest["checksums"][filename]:
            raise BpmMigrationConflict("Snapshot staging changed retained player/score bytes")
    manifest = {**deepcopy(original_manifest), **_identity(document), "checksums": hashes,
                "bpmMetadataOverlay": {
                    "sourceManifestSha256": hashlib.sha256((source / "snapshot_manifest.json").read_bytes()).hexdigest(),
                    "changedCharts": sum(old != new for old, new in zip(charts, resolved)),
                    "playersAndScoresBytesPreserved": True,
                }}
    (destination / "snapshot_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    validate_snapshot_directory(destination, mix="Phoenix2")
    return manifest
