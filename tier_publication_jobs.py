"""Durable tier-only publication jobs; HTTP callers never own analysis work."""
from __future__ import annotations

from datetime import datetime, timezone
import re
import uuid
from typing import Any, Callable, Mapping

CONTINUATION_FIELD = "_tierPublicationContinuation"
MAX_IO_RETRIES = 2
CURRENT_TIER_PRODUCER = "retained-goals-v1"
CURRENT_TIER_SCHEMA_VERSION = 61
TIER_PUBLICATION_STAGES = 15


def _retryable_io(error: Exception) -> bool:
    import requests
    import psycopg
    if isinstance(error, requests.exceptions.HTTPError):
        response = error.response
        status = response.status_code if response is not None else None
        return isinstance(status, int) and (status in (408, 429) or 500 <= status < 600)
    return isinstance(error, (requests.exceptions.RequestException, ConnectionError,
                              TimeoutError, psycopg.OperationalError))


class TierPublicationConflict(ValueError):
    """A pinned source, candidate, pointer, or generation lease changed."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_status(job: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("id", "jobType", "status", "stage", "createdAtUtc", "updatedAtUtc",
              "completedAtUtc", "generatedAtUtc", "progressOrdinal", "totalStages",
              "completedTargetFits", "error", "exceptionClass", "attempt", "publication",
              "operation", "goals", "backfill", "producer")
    return {key: job[key] for key in fields if key in job}


def request_tier_publication(enqueue: Callable[[str], None], *, store=None, jobs=None,
                             operation: str = "tier-publication", goals: tuple[str, ...] = ()) -> tuple[int, dict[str, Any]]:
    from analysis_runtime import RuntimeJobStore
    from tier_release import require_tier_release_policy
    del store  # The trigger and status paths deliberately do not hydrate artifacts.
    require_tier_release_policy()
    if operation not in {"tier-publication", "scoring-goal-backfill"}:
        raise ValueError("Unknown tier operation.")
    if operation == "scoring-goal-backfill":
        if not goals or len(set(goals)) != len(goals) or any(goal not in {"ss", "ssplus"} for goal in goals):
            raise ValueError("Backfill goals must be distinct SS or SS+ targets.")
    elif goals:
        raise ValueError("Tier publication does not accept backfill goals.")
    jobs = jobs if jobs is not None else RuntimeJobStore()
    stamp = _timestamp()
    job = {"id": "tier-publication-" + uuid.uuid4().hex, "jobType": "tier-publication",
           "status": "queued", "stage": "pin-inputs", "createdAtUtc": stamp,
           "updatedAtUtc": stamp, "generatedAtUtc": stamp, "progressOrdinal": 0,
           "completedTargetFits": 0, "totalStages": TIER_PUBLICATION_STAGES, "attempt": 0, "error": None,
           "producer": CURRENT_TIER_PRODUCER}
    if operation == "scoring-goal-backfill":
        job.update(operation=operation, goals=list(goals), totalStages=1 + len(goals) * 5)
    current, created = jobs.request_tier_publication(job)
    if (not created and operation == "tier-publication"
            and current.get("jobType") == "tier-publication"
            and current.get("operation", "tier-publication") == "tier-publication"
            and current.get("producer") != CURRENT_TIER_PRODUCER):
        # Existing queue deliveries can stay bound to their old deployment.
        # Cancel that immutable job through the normal store operation first;
        # its worker then loses the lease needed to advance or publish.
        cancelled_at = _timestamp()
        cancelled = jobs.save({**current, "status": "failed", "cancelRequested": True,
            "error": "retired-tier-producer-replaced", "retryAllowedAtUtc": None,
            "updatedAtUtc": cancelled_at, "completedAtUtc": cancelled_at})
        if (cancelled.get("id") != current["id"] or cancelled.get("status") != "failed"
                or cancelled.get("cancelRequested") is not True):
            raise RuntimeError("The retired tier publication could not be cancelled.")
        current, created = jobs.request_tier_publication(job)
    if created:
        try:
            enqueue(str(current["id"]))
        except Exception:
            jobs.finish_tier_publication({**current, "status": "failed", "error": "queue-unavailable",
                                         "updatedAtUtc": _timestamp(), "completedAtUtc": _timestamp()})
            raise RuntimeError("The tier publication could not be queued.") from None
    return 202, {"outcome": "tier-publication-queued" if created else "tier-publication-active", **_public_status(current)}


def request_scoring_goal_backfill(enqueue: Callable[[str], None], *, store=None, jobs=None,
                                   goals: tuple[str, ...] = ("ss", "ssplus")) -> tuple[int, dict[str, Any]]:
    return request_tier_publication(enqueue, store=store, jobs=jobs,
                                    operation="scoring-goal-backfill", goals=goals)


def get_tier_publication_status(job_id: str | None = None, *, jobs=None) -> dict[str, Any] | None:
    from analysis_runtime import RuntimeJobStore
    jobs = jobs if jobs is not None else RuntimeJobStore()
    if job_id is not None and not re.fullmatch(r"tier-publication-[a-f0-9]{32}", job_id):
        return None
    identity = job_id or jobs.tier_publication_job_id()
    job = jobs.get(identity) if identity else None
    return _public_status(job) if job and job.get("jobType") == "tier-publication" else None


def _pin_inputs(store, job):
    from production_tiers import pin_tier_publication_inputs
    from tier_release import source_aware_inputs
    expected = store.tier_publication_revisions()
    _, first, second, manifest, records, metadata = source_aware_inputs(store)
    if store.tier_publication_revisions() != expected:
        raise TierPublicationConflict("The sources changed while pinning tier inputs.")
    reference = pin_tier_publication_inputs(store, first=first, second=second, manifest=manifest,
        records=records, metadata=metadata, generated_at_utc=job["generatedAtUtc"])
    return {"inputs": reference, "generatedAtUtc": job["generatedAtUtc"]}, expected


def _advance_goal_backfills(store, current):
    """Cache independent goals for one immutable epoch; never promote pointers."""
    from production_tiers import advance_goal_backfill
    goals = current.get("goals")
    if not isinstance(goals, list) or not goals or len(set(goals)) != len(goals) or any(goal not in {"ss", "ssplus"} for goal in goals):
        raise ValueError("The pinned scoring-goal backfill is invalid.")
    completed = current.setdefault("goalArtifacts", {})
    goal = next((goal for goal in goals if goal not in completed), None)
    if goal is None:
        raise ValueError("The backfill has no pending goal.")
    prior_fits = 4 * len(completed)

    def progress(value):
        if not isinstance(value, Mapping):
            raise ValueError("The goal backfill progress is invalid.")
        current["stage"] = "backfill-" + goal

    result = advance_goal_backfill(store, pinned_inputs_reference=current["pipelineState"]["inputs"],
                                   goal=goal, on_progress=progress)
    if not isinstance(result, Mapping) or result.get("goal") != goal or not isinstance(result.get("done"), bool):
        raise ValueError("The goal backfill worker returned an incompatible result.")
    identity = result.get("inputIdentity")
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity):
        raise ValueError("The goal backfill input identity is invalid.")
    if current.get("backfillInputIdentity") not in (None, identity):
        raise TierPublicationConflict("The backfill goals use different pinned inputs.")
    current["backfillInputIdentity"] = identity
    count = result.get("completedTargetFits", 4 if result["done"] else 0)
    if type(count) is not int or not 0 <= count <= 4 or (result["done"] and count != 4):
        raise ValueError("The goal backfill fit count is invalid.")
    if prior_fits + count < current["completedTargetFits"]:
        raise ValueError("The goal backfill progress regressed.")
    current["completedTargetFits"] = prior_fits + count
    current["stage"] = "backfill-" + goal
    if result["done"]:
        reference = result.get("artifactReference")
        if (not isinstance(reference, Mapping) or not isinstance(reference.get("path"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", str(reference.get("sha256") or ""))):
            raise ValueError("The goal backfill did not produce a verified immutable artifact.")
        completed[goal] = dict(reference)
    current["progressOrdinal"] = max(current["progressOrdinal"],
        1 + 5 * len(completed) if result["done"] else 1 + 5 * len(completed) + count)
    done = len(completed) == len(goals)
    return {"done": done, "backfill": {"inputIdentity": identity,
            "goals": list(goals), "artifactReferences": dict(completed), "promoted": False}}


def execute_tier_publication_job(job_id: str, *, store=None, jobs=None) -> dict[str, Any]:
    from analysis_runtime import (PrivateBlobStore, RuntimeJobStore, _start_job_lease_heartbeat,
                                 _pulse_job_lease, _stop_job_lease)
    from tier_diagnostics import event, report_exception
    from time import perf_counter
    store = store if store is not None else PrivateBlobStore()
    jobs = jobs if jobs is not None else RuntimeJobStore()
    current = jobs.get(job_id)
    if not current or current.get("jobType") != "tier-publication":
        raise ValueError("The tier publication job was not found.")
    if current.get("status") in {"completed", "failed"}:
        return _public_status(current)
    current = {**current, "status": "running", "updatedAtUtc": _timestamp()}
    claimed = jobs.claim_tier_publication(current)
    if not claimed:
        return _public_status(jobs.get(job_id) or current)
    current = claimed
    heartbeat = None
    started = perf_counter()
    try:
        if (current.get("operation") != "scoring-goal-backfill"
                and current.get("producer") != CURRENT_TIER_PRODUCER):
            raise ValueError("This tier job uses a retired producer; start a current tier publication.")
        heartbeat = _start_job_lease_heartbeat(jobs, job_id)
        _pulse_job_lease(heartbeat)
        event("tier-phase-start", job_id=job_id, stage=current["stage"], sequence=current["progressOrdinal"])
        if "pipelineState" not in current:
            state, revisions = _pin_inputs(store, current)
            current.update(pipelineState=state, expectedRevisions=revisions, stage="prepare", progressOrdinal=1)
            result = {"done": False}
        else:
            if store.tier_publication_revisions() != current["expectedRevisions"]:
                raise TierPublicationConflict("The pinned publication sources changed.")
            def stage_started(stage):
                current.update(stage=stage, updatedAtUtc=_timestamp())
                jobs.save(current)
                event("tier-phase-start", job_id=job_id, stage=stage, sequence=current["progressOrdinal"])
            if current.get("operation") == "scoring-goal-backfill":
                stage_started("backfill")
                result = _advance_goal_backfills(store, current)
            else:
                from production_tiers import advance_tier_publication
                result = advance_tier_publication(store, current["pipelineState"], on_stage_start=stage_started)
                current["pipelineState"] = result.get("state", current["pipelineState"])
                current["stage"] = result.get("stage", current["stage"])
                current["progressOrdinal"] = max(current["progressOrdinal"], 1 + int(result.get("progressOrdinal", 0)))
                current["completedTargetFits"] = result.get("completedTargetFits", current["completedTargetFits"])
                if "totalStages" in result:
                    current["totalStages"] = 1 + int(result["totalStages"])
        _pulse_job_lease(heartbeat)
        current["updatedAtUtc"] = _timestamp()
        if result["done"]:
            if current.get("operation") == "scoring-goal-backfill":
                completed = {**current, "status": "completed", "stage": "cached", "error": None,
                             "completedAtUtc": _timestamp(), "backfill": result["backfill"]}
                # Source revisions and the active lease are checked in the same
                # transaction that completes this cache-only job. No pointers move.
                _stop_job_lease(heartbeat)
                heartbeat = None
                store.complete_scoring_goal_backfill(job_id=job_id, lease_owner=jobs.lease_owner,
                    expected_revisions=current["expectedRevisions"], completed_job=completed)
                event("scoring-goal-backfill-completed", job_id=job_id, stage="cached",
                      duration_ms=round((perf_counter()-started)*1000,3))
                return _public_status(completed)
            _stop_job_lease(heartbeat)
            heartbeat = None
            if (result.get("schemaVersion") != CURRENT_TIER_SCHEMA_VERSION
                    or result.get("producer") != CURRENT_TIER_PRODUCER):
                raise ValueError("Tier publication requires the current retained schema-61 producer.")
            from production_tiers import validate_current_tier_publication
            candidate = store.get_json(result["candidatePath"])
            if candidate is None:
                raise ValueError("The validated tier candidate is unavailable.")
            validate_current_tier_publication(candidate)
            revision = store.artifact_revision(result["candidatePath"])
            if revision is None:
                raise ValueError("The validated tier candidate is unavailable.")
            if revision.get("sha256") != result["candidateSha256"]:
                raise TierPublicationConflict("The validated candidate content differs from its checkpoint.")
            publication = {"schemaVersion": CURRENT_TIER_SCHEMA_VERSION, "producer": CURRENT_TIER_PRODUCER,
                           "candidatePath": result["candidatePath"],
                           "tierSha256": result["candidateSha256"], "scoreFetches": 0,
                           "recommendationFits": 0, "jobId": job_id}
            completed = {**current, "status": "completed", "stage": "published", "error": None,
                         "completedAtUtc": _timestamp(), "publication": publication}
            store.promote_tier_publication(job_id=job_id, lease_owner=jobs.lease_owner,
                candidate_path=result["candidatePath"], candidate_revision=revision,
                expected_revisions=current["expectedRevisions"], audit=publication,
                completed_job=completed)
            # Promotion and job completion are one transaction. A lost HTTP/queue
            # acknowledgement cannot cause a second pointer write on redelivery.
            event("tier-publication-completed", job_id=job_id, stage="published", duration_ms=round((perf_counter()-started)*1000,3))
            return _public_status(completed)
        current["continuationSequence"] = int(current.get("continuationSequence", 0)) + 1
        jobs.save(current)
        _stop_job_lease(heartbeat)
        heartbeat = None
        jobs.handoff_continuation(job_id, current)
        event("tier-phase-completed", job_id=job_id, stage=current["stage"], duration_ms=round((perf_counter()-started)*1000,3))
        return {**_public_status(current), "status": "queued", CONTINUATION_FIELD: current["continuationSequence"]}
    except Exception as error:
        report_exception(error, job_id=job_id, stage=current["stage"], duration_ms=round((perf_counter()-started)*1000,3))
        try:
            _stop_job_lease(heartbeat)
        except RuntimeError:
            pass  # The original phase failure remains the durable diagnostic.
        heartbeat = None
        # A deterministic validation/CAS error must not loop. Transient I/O is
        # retried only from the persisted phase, with a small durable budget.
        retry = _retryable_io(error)
        attempts = int(current.get("attempt", 0))
        failed = {**current, "status": "failed", "error": "tier-publication-failed",
                  "exceptionClass": type(error).__name__, "updatedAtUtc": _timestamp(),
                  "completedAtUtc": _timestamp(), "attempt": attempts + 1,
                  "continuationSequence": int(current.get("continuationSequence", 0)) + 1}
        if retry and attempts < MAX_IO_RETRIES:
            queued = jobs.retry_tier_publication(failed)
            return {**_public_status(queued), CONTINUATION_FIELD: int(queued.get("continuationSequence", 0))}
        jobs.finish_tier_publication(failed)
        return _public_status(failed)
    finally:
        _stop_job_lease(heartbeat)
