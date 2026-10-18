"""Durable tier-only publication jobs; HTTP callers never own analysis work."""
from __future__ import annotations

from datetime import datetime, timezone
import re
import uuid
from typing import Any, Callable, Mapping

CONTINUATION_FIELD = "_tierPublicationContinuation"
MAX_IO_RETRIES = 2


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
              "completedTargetFits", "error", "exceptionClass", "attempt", "publication")
    return {key: job[key] for key in fields if key in job}


def request_tier_publication(enqueue: Callable[[str], None], *, store=None, jobs=None) -> tuple[int, dict[str, Any]]:
    from analysis_runtime import RuntimeJobStore
    from tier_release import require_tier_release_policy
    del store  # The trigger and status paths deliberately do not hydrate artifacts.
    require_tier_release_policy()
    jobs = jobs if jobs is not None else RuntimeJobStore()
    stamp = _timestamp()
    job = {"id": "tier-publication-" + uuid.uuid4().hex, "jobType": "tier-publication",
           "status": "queued", "stage": "pin-inputs", "createdAtUtc": stamp,
           "updatedAtUtc": stamp, "generatedAtUtc": stamp, "progressOrdinal": 0,
           "completedTargetFits": 0, "totalStages": 19, "attempt": 0, "error": None}
    current, created = jobs.request_tier_publication(job)
    if created:
        try:
            enqueue(str(current["id"]))
        except Exception:
            jobs.finish_tier_publication({**current, "status": "failed", "error": "queue-unavailable",
                                         "updatedAtUtc": _timestamp(), "completedAtUtc": _timestamp()})
            raise RuntimeError("The tier publication could not be queued.") from None
    return 202, {"outcome": "tier-publication-queued" if created else "tier-publication-active", **_public_status(current)}


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
            from production_tiers import advance_tier_publication
            def stage_started(stage):
                current.update(stage=stage, updatedAtUtc=_timestamp())
                jobs.save(current)
                event("tier-phase-start", job_id=job_id, stage=stage, sequence=current["progressOrdinal"])
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
            _stop_job_lease(heartbeat)
            heartbeat = None
            revision = store.artifact_revision(result["candidatePath"])
            if revision is None:
                raise ValueError("The validated tier candidate is unavailable.")
            if revision.get("sha256") != result["candidateSha256"]:
                raise TierPublicationConflict("The validated candidate content differs from its checkpoint.")
            publication = {"schemaVersion": 57, "candidatePath": result["candidatePath"],
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
