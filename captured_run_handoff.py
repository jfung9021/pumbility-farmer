"""Explicit, idempotent saved-input recovery and cancellation/drain admission.

No network capture or numerical work belongs in this operator coordinator.
The old deployment's publication code cannot be retroactively fenced; the
replacement stays unqueued until the configured old invocation bound elapses.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

PREFIX = "analysis/private/captured-run-handoffs/"
HEAD = "active:captured-recovery"
INVOCATION_BOUND_SECONDS = 800
IDENTIFIER = re.compile(r"^[a-f0-9]{64}$")
JOB = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class CapturedRecoveryConflict(RuntimeError):
    """The expected source, admission owner, or publication changed."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def intent_path(intent_id: str) -> str:
    if not isinstance(intent_id, str) or not IDENTIFIER.fullmatch(intent_id):
        raise ValueError("A valid recovery intent ID is required.")
    return PREFIX + intent_id + ".json"


def summary(intent: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("intentId", "state", "sourceJobId", "sourceDeploymentId", "bundleId", "replacementJobId",
              "createdAtUtc", "cancelledAtUtc", "drainNotBeforeUtc", "admittedAtUtc", "expectedServingRevision",
              "finalServingRevision", "blocker", "inventory")
    return {key: intent[key] for key in fields if key in intent}


def prepare_recovery(store: Any, jobs: Any, request: Mapping[str, Any], *, context_loader: Callable,
                     prepare_inputs: Callable | None = None) -> dict[str, Any]:
    allowed = {"sourceJobId", "expectedManifestSha256", "sourceDeploymentId", "expectedHead",
               "expectedServingRevision", "idempotencyKey"}
    if set(request) != allowed:
        raise ValueError("Recovery preparation requires the documented identity fields only.")
    for field in ("sourceJobId", "sourceDeploymentId", "expectedHead", "idempotencyKey"):
        if not isinstance(request[field], str) or not JOB.fullmatch(request[field]):
            raise ValueError("Invalid recovery identity.")
    for field in ("expectedManifestSha256", "expectedServingRevision"):
        if not isinstance(request[field], str) or not IDENTIFIER.fullmatch(request[field]):
            raise ValueError("Invalid recovery content identity.")
    if request["expectedHead"] != request["sourceJobId"]:
        raise ValueError("The expected active head must identify the captured source job.")
    intent_id = digest({"operation": "captured-run-recovery-v1", "key": request["idempotencyKey"]})
    request_hash = digest(dict(request))
    existing = jobs.captured_recovery_status(intent_id)
    if existing is not None:
        if existing.get("requestHash") != request_hash:
            raise CapturedRecoveryConflict("The recovery idempotency key already names different inputs.")
        return summary(existing)
    source = jobs.get(request["sourceJobId"])
    if not source or source.get("status") not in {"queued", "running", "completed"}:
        raise CapturedRecoveryConflict("The captured source is unavailable for preparation.")
    active = jobs.active_job_id()
    if active != request["expectedHead"] and not (active is None and source.get("status") == "completed"):
        raise CapturedRecoveryConflict("The active source job changed before preparation.")
    if jobs.captured_recovery_serving_revision() != request["expectedServingRevision"]:
        raise CapturedRecoveryConflict("Serving revisions changed before preparation.")
    if prepare_inputs is None:
        from captured_run_inputs import prepare_captured_run_inputs
        prepare_inputs = prepare_captured_run_inputs
    prepared = prepare_inputs(store, jobs, source_job_id=request["sourceJobId"],
        expected_manifest_sha256=request["expectedManifestSha256"], context_loader=context_loader,
        source_deployment=request["sourceDeploymentId"])
    bundle_id = prepared.get("bundleId")
    if not isinstance(bundle_id, str) or not IDENTIFIER.fullmatch(bundle_id):
        raise ValueError("The verified recovery bundle identity is invalid.")
    intent = {"version": 1, "intentId": intent_id, "requestHash": request_hash,
        "sourceJobId": request["sourceJobId"], "sourceDeploymentId": request["sourceDeploymentId"],
        "bundleId": bundle_id, "expectedHead": request["expectedHead"],
        "expectedServingRevision": request["expectedServingRevision"],
        "replacementJobId": "analysis-captured-" + intent_id[:32], "state": "prepared",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(), "inventory": prepared.get("summary", {})}
    return summary(jobs.captured_recovery_prepare(intent))


def execute_recovery(store: Any, jobs: Any, intent_id: str, enqueue: Callable[[str], None], *,
                     load_inputs: Callable | None = None) -> dict[str, Any]:
    intent_path(intent_id)
    intent = jobs.captured_recovery_status(intent_id)
    if intent is None:
        raise ValueError("The recovery intent does not exist.")
    if intent["state"] in {"blocked", "aborted"}:
        return summary(intent)
    if load_inputs is None:
        from captured_run_inputs import load_captured_run_inputs
        load_inputs = load_captured_run_inputs
    load_inputs(store, intent["bundleId"])
    if intent["state"] == "prepared":
        intent = jobs.captured_recovery_begin(intent_id)
    if intent["state"] == "draining":
        intent = jobs.captured_recovery_admit(intent_id)
    if intent["state"] == "admitted":
        # At-least-once delivery uses the same job/task ID. Lost enqueue replies
        # can be retried; the ordinary atomic job claim prevents duplicate work.
        enqueue(intent["replacementJobId"])
    return summary(intent)


def _read_intent(cursor: Any, intent_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    cursor.execute("select payload_json from pumbility.artifacts where object_key=%s" + (" for update" if lock else ""),
                   (intent_path(intent_id),))
    row = cursor.fetchone()
    return dict(row[0]) if row is not None and isinstance(row[0], Mapping) else None


def _write_intent(cursor: Any, intent: Mapping[str, Any]) -> None:
    from pumbility_store import PumbilityArtifactStore
    PumbilityArtifactStore._put_json_row(cursor, intent_path(intent["intentId"]), intent)


def _transaction(jobs: Any, operation: Callable) -> Any:
    from pumbility_store import _assert_schema, _connect
    with _connect(jobs.database_url) as connection, connection.transaction(), connection.cursor() as cursor:
        cursor.execute("set local lock_timeout = '5000ms'")
        cursor.execute("set local statement_timeout = '30000ms'")
        _assert_schema(cursor)
        cursor.execute("select pg_advisory_xact_lock(hashtextextended('pumbility:captured-run-handoff',0))")
        return operation(cursor)


def _revision(cursor: Any) -> str:
    from pumbility_store import _tier_publication_revisions
    return digest(_tier_publication_revisions(cursor))


def store_status(jobs: Any, intent_id: str) -> dict[str, Any] | None:
    from pumbility_store import _read_cursor, _assert_schema
    with _read_cursor(jobs.database_url) as cursor:
        _assert_schema(cursor)
        return _read_intent(cursor, intent_id)


def store_prepare(jobs: Any, intent: Mapping[str, Any]) -> dict[str, Any]:
    def prepare(cursor):
        existing = _read_intent(cursor, intent["intentId"], lock=True)
        if existing is not None:
            if existing.get("requestHash") != intent["requestHash"]:
                raise CapturedRecoveryConflict("The recovery key already names different inputs.")
            return existing
        _write_intent(cursor, intent)
        return dict(intent)
    return _transaction(jobs, prepare)


def store_begin(jobs: Any, intent_id: str) -> dict[str, Any]:
    from psycopg.types.json import Jsonb
    def begin(cursor):
        intent = _read_intent(cursor, intent_id, lock=True)
        if not intent:
            raise ValueError("The recovery intent does not exist.")
        if intent["state"] != "prepared":
            return intent
        cursor.execute("select id,status,stage,payload from pumbility.jobs where external_key=%s for update",
                       (intent["sourceJobId"],))
        source = cursor.fetchone()
        if source is not None and source[1] == "completed":
            intent = {**intent, "state": "source-completed", "finalServingRevision": _revision(cursor)}
            _write_intent(cursor, intent)
            return intent
        if source is None or source[1] not in {"queued", "running"} or source[2] == "publishing":
            raise CapturedRecoveryConflict("The source job is terminal or publishing; cancellation was not performed.")
        cursor.execute("select name,job_id from pumbility.job_heads where name=any(%s) order by name for update",
                       (["active:analysis", HEAD, "active:tier-publication"],))
        heads = dict(cursor.fetchall())
        if heads.get("active:analysis") != source[0] or HEAD in heads or "active:tier-publication" in heads:
            raise CapturedRecoveryConflict("Another admission owner won; cancellation was not performed.")
        if _revision(cursor) != intent["expectedServingRevision"]:
            raise CapturedRecoveryConflict("Serving revisions changed; cancellation was not performed.")
        cursor.execute("select clock_timestamp()")
        stamp = cursor.fetchone()[0]
        intent = {**intent, "state": "draining", "cancelledAtUtc": stamp.isoformat(),
                  "drainNotBeforeUtc": (stamp + timedelta(seconds=INVOCATION_BOUND_SECONDS)).isoformat()}
        cursor.execute("select pumbility.cancel_job(%s,null)", (source[0],))
        cancelled = {**dict(source[3]), "status": "failed", "cancelRequested": True,
            "error": "The operator replaced this run using verified captured inputs.",
            "retryAllowedAtUtc": None, "completedAtUtc": stamp.isoformat(), "updatedAtUtc": stamp.isoformat()}
        cursor.execute("update pumbility.jobs set payload=%s where id=%s and cancellation_requested_at is not null",
                       (Jsonb(cancelled), source[0]))
        if cursor.rowcount != 1:
            raise CapturedRecoveryConflict("The source cancellation was not recorded.")
        replacement = {"id": intent["replacementJobId"], "status": "queued", "stage": "discovering", "mix": "phoenix2",
            "attempt": 0, "createdAtUtc": stamp.isoformat(), "updatedAtUtc": stamp.isoformat(), "startedAtUtc": None,
            "completedAtUtc": None, "generatedAtUtc": None, "error": None, "retryAllowedAtUtc": None,
            "fullSync": False, "reanalyzeOnly": False, "trigger": "captured-recovery", "refreshOfficialSources": False,
            "capturedRunBundleId": intent["bundleId"], "capturedRecoveryIntentId": intent_id,
            "capturedSourceJobId": intent["sourceJobId"],
            "progress": {"current": 0, "total": 0, "percent": 0, "message": "Waiting for the cancelled worker to drain."}}
        cursor.execute("""insert into pumbility.jobs(external_key,kind,status,stage,mix_key,attempt,payload,created_at,updated_at)
            values(%s,'analysis','queued','discovering','phoenix2',0,%s,%s,%s) returning id""",
            (replacement["id"], Jsonb(replacement), stamp, stamp))
        replacement_uuid = cursor.fetchone()[0]
        for head in (HEAD, "active:analysis", "latest:phoenix2"):
            cursor.execute("""insert into pumbility.job_heads(name,job_id,updated_at) values(%s,%s,%s)
                on conflict(name) do update set job_id=excluded.job_id,updated_at=excluded.updated_at""",
                (head, replacement_uuid, stamp))
        _write_intent(cursor, intent)
        return intent
    return _transaction(jobs, begin)


def store_admit(jobs: Any, intent_id: str, *, abort_revision: str | None = None) -> dict[str, Any]:
    def admit(cursor):
        intent = _read_intent(cursor, intent_id, lock=True)
        if not intent:
            raise ValueError("The recovery intent does not exist.")
        if intent["state"] not in {"draining", "blocked"}:
            return intent
        cursor.execute("select status,lease_owner,lease_expires_at,cancellation_requested_at from pumbility.jobs where external_key=%s for update",
                       (intent["sourceJobId"],))
        source = cursor.fetchone()
        cursor.execute("select clock_timestamp()")
        stamp = cursor.fetchone()[0]
        deadline = datetime.fromisoformat(intent["drainNotBeforeUtc"])
        if stamp < deadline:
            return intent
        if (source is None or source[0] not in {"failed", "cancelled"} or source[1] is not None
                or (source[2] is not None and source[2] > stamp) or source[3] is None):
            raise CapturedRecoveryConflict("The old callback's terminal ownership could not be established.")
        cursor.execute("select j.external_key from pumbility.job_heads h join pumbility.jobs j on j.id=h.job_id where h.name=any(%s) order by h.name for update of h",
                       ([HEAD, "active:analysis"],))
        heads = cursor.fetchall()
        if len(heads) != 2 or any(row[0] != intent["replacementJobId"] for row in heads):
            raise CapturedRecoveryConflict("The recovery reservation no longer owns global admission.")
        revision = _revision(cursor)
        intent = {**intent, "finalServingRevision": revision}
        if abort_revision is not None:
            if revision != abort_revision:
                raise CapturedRecoveryConflict("Serving revisions changed before the safe abort.")
            cursor.execute("""update pumbility.jobs set status='failed',completed_at=clock_timestamp(),
                cancellation_requested_at=clock_timestamp(),
                payload=payload || '{"status":"failed","cancelRequested":true}'::jsonb
                where external_key=%s and status='queued' and lease_owner is null""", (intent["replacementJobId"],))
            if cursor.rowcount != 1:
                raise CapturedRecoveryConflict("The waiting replacement could not be safely aborted.")
            cursor.execute("delete from pumbility.job_heads where name=any(%s) and job_id=(select id from pumbility.jobs where external_key=%s)",
                           ([HEAD, "active:analysis"], intent["replacementJobId"]))
            intent = {**intent, "state": "aborted"}
        elif revision != intent["expectedServingRevision"]:
            intent = {**intent, "state": "blocked", "blocker": "serving-revisions-changed-during-drain"}
        elif intent["state"] == "draining":
            intent = {**intent, "state": "admitted", "admittedAtUtc": stamp.isoformat()}
        _write_intent(cursor, intent)
        return intent
    return _transaction(jobs, admit)


def store_admission(jobs: Any, job_id: str | None = None) -> dict[str, Any] | None:
    from pumbility_store import _read_cursor, _assert_schema
    with _read_cursor(jobs.database_url) as cursor:
        _assert_schema(cursor)
        cursor.execute("select j.payload,j.status from pumbility.job_heads h join pumbility.jobs j on j.id=h.job_id where h.name=%s", (HEAD,))
        row = cursor.fetchone()
        if row is None:
            return None
        payload = dict(row[0])
        intent = _read_intent(cursor, payload.get("capturedRecoveryIntentId", ""))
        if not intent:
            raise CapturedRecoveryConflict("Recovery admission metadata is missing.")
        if intent["state"] == "admitted" and (job_id == payload["id"] or row[1] in {"completed", "failed", "cancelled"}):
            return None
        return {**summary(intent), "blocked": True}


def guard_head(cursor: Any, name: str, job_id: str | None) -> None:
    if name not in {"active:analysis", "latest:phoenix2"}:
        return
    # Lock even when HEAD is absent: a row lock alone cannot fence an ordinary
    # trigger already waiting behind a handoff's uncommitted head insertion.
    cursor.execute("select pg_advisory_xact_lock(hashtextextended('pumbility:captured-run-handoff',0))")
    cursor.execute("select j.external_key,j.status,j.payload from pumbility.job_heads h join pumbility.jobs j on j.id=h.job_id where h.name=%s for update of h", (HEAD,))
    row = cursor.fetchone()
    if row is not None and row[1] in {"completed", "failed", "cancelled"}:
        intent = _read_intent(cursor, dict(row[2]).get("capturedRecoveryIntentId", ""))
        if intent is not None and intent["state"] == "admitted":
            cursor.execute("delete from pumbility.job_heads where name=%s and job_id=(select id from pumbility.jobs where external_key=%s)", (HEAD, row[0]))
            return
    if row is not None and job_id != row[0] and not (job_id is None and row[1] in {"completed", "failed", "cancelled"}):
        raise CapturedRecoveryConflict("Global analysis admission is reserved for captured-input recovery.")
