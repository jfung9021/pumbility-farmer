"""Recommendation-only rebuilds from pinned stored scores and tier annotations."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Callable, Mapping

from general_pumbility import METHOD
from phoenix2_sync import isoformat_utc, sanitize_snapshot, utc_now
from pumbility_contract import (
    MODEL_ARTIFACT_SCHEMA_VERSION,
    RECOMMENDATION_SCHEMA_VERSION,
    recommendation_blob_path,
    recommendation_generation_key,
    recommendation_index_path,
)
from recommendation_refresh import build_recommendation_model_artifacts


CHECKPOINT_VERSION = 1


def checkpoint_path(job_id: str) -> str:
    return f"analysis/private/recommendation-model-refreshes/{job_id}.json"


def _pinned_inputs(store: Any, boundary: Mapping[str, Any]) -> tuple[dict, dict, list[dict]]:
    paths = boundary["artifactPaths"]
    inputs = {name: store.get_json(paths[name]) for name in ("phoenix1", "phoenix2", "tiers")}
    if any(not isinstance(value, Mapping) for value in inputs.values()):
        raise RuntimeError("Published recommendation inputs are unavailable.")
    current = store.recommendation_refresh_boundary()
    if current != boundary:
        raise RuntimeError("Recommendation inputs changed while they were being pinned.")
    tiers = inputs["tiers"]
    annotations = [dict(row) for mode in ("singles", "doubles", "coop")
                   for row in tiers.get(mode, []) if isinstance(row, Mapping)]
    return (sanitize_snapshot(inputs["phoenix1"], mix="phoenix1"),
            sanitize_snapshot(inputs["phoenix2"], mix="phoenix2"), annotations)


def execute_recommendation_model_refresh(
    *, blob_store: Any, job_store: Any, job_id: str,
    now: Callable[[], datetime] = utc_now,
    lease_heartbeat: Any | None = None,
    yield_after_checkpoint: bool = False,
) -> dict[str, Any]:
    """Stage, register, then promote one model without any analysis or capture."""
    import analysis_runtime as runtime

    job = job_store.get(job_id)
    if job is None:
        raise RuntimeError("The recommendation-only job is unavailable.")
    if job.get("cancelRequested") or job.get("status") in {"completed", "failed", "cancelled"}:
        return job
    path = checkpoint_path(job_id)
    checkpoint = blob_store.get_json(path)
    if checkpoint is None:
        boundary = blob_store.recommendation_refresh_boundary()
        checkpoint = {
            "version": CHECKPOINT_VERSION, "jobId": job_id, "method": METHOD,
            "recommendationSchemaVersion": RECOMMENDATION_SCHEMA_VERSION,
            "modelArtifactSchemaVersion": MODEL_ARTIFACT_SCHEMA_VERSION,
            "generatedAtUtc": isoformat_utc(now()), "phase": "pinned", "boundary": boundary,
        }
        blob_store.put_json(path, checkpoint)
    if (checkpoint.get("version") != CHECKPOINT_VERSION or checkpoint.get("jobId") != job_id
            or checkpoint.get("method") != METHOD
            or checkpoint.get("recommendationSchemaVersion") != RECOMMENDATION_SCHEMA_VERSION
            or checkpoint.get("modelArtifactSchemaVersion") != MODEL_ARTIFACT_SCHEMA_VERSION
            or checkpoint.get("phase") not in {"pinned", "staged", "registered"}):
        raise ValueError("The recommendation-only checkpoint is incompatible.")
    boundary = checkpoint["boundary"]
    generation = recommendation_generation_key(job_id)
    generated_at = checkpoint["generatedAtUtc"]

    def progress(step: int, message: str, *, stage: str = "publishing") -> dict:
        return runtime.update_job(job_store, job_id, now=now(), status="running", stage=stage,
            progress={"current": step, "total": 3, "percent": step * 100 // 3,
                      "phase": "recommendation-model", "message": message})

    def continuation(name: str, step: int, message: str) -> dict:
        current = progress(step, message)
        runtime._stop_job_lease(lease_heartbeat)
        handoff = getattr(job_store, "handoff_continuation", None)
        if callable(handoff):
            handoff(job_id, current)
        return {**current, runtime.ANALYSIS_CONTINUATION_FIELD: name,
                runtime.ANALYSIS_CONTINUATION_SEQUENCE_FIELD: f"recommendations-only-{step}"}

    if checkpoint["phase"] == "pinned":
        progress(0, "Building the PIUScores recommendation model from stored inputs.", stage="analyzing")
        phoenix1, phoenix2, annotations = _pinned_inputs(blob_store, boundary)
        source_hashes, snapshot_hashes, input_hash = runtime._recommendation_source_identity(phoenix1, phoenix2, phoenix2_source_sha256=None)
        runtime._pulse_job_lease(lease_heartbeat)
        index, model, binary, p1_shards, p2_shards = build_recommendation_model_artifacts(
            phoenix1, phoenix2, combined_charts=annotations, phoenix2_slopes={},
            generation_key=generation, generated_at_utc=generated_at,
            consume_phoenix1_snapshot=True, component_store=blob_store,
        )
        context = {"publicationScope": "recommendations-only", "baseAnalysisRunId": boundary["analysisRunId"],
                   "annotationTierRevision": boundary["expectedRevisions"][boundary["artifactPaths"]["tiers"]]}
        index.update(context)
        model.update(context)
        manifest = runtime._recommendation_artifact_manifest(
            index=index, model=model, score_model_bytes=binary,
            phoenix1_shards=p1_shards, phoenix2_shards=p2_shards,
        )
        metadata = {
            "generationKey": generation, "modelArtifactSchemaVersion": MODEL_ARTIFACT_SCHEMA_VERSION,
            "playerCount": len(index["players"]), "phoenix1ShardCount": len(p1_shards),
            "phoenix2ShardCount": len(p2_shards), "sourceHashes": source_hashes,
            "snapshotHashes": snapshot_hashes, "inputSha256": input_hash,
            "outputSha256": runtime._canonical_json_sha256({"index": index, "model": model,
                "numericModelSha256": hashlib.sha256(binary).hexdigest()}),
            "artifactManifest": manifest,
        }
        runtime._pulse_job_lease(lease_heartbeat)
        runtime.publish_recommendation_model_artifacts(
            blob_store, index=index, model=model, score_model_bytes=binary,
            phoenix1_shards=p1_shards, phoenix2_shards=p2_shards,
            index_path=recommendation_blob_path(), publish_index=False, artifact_manifest=manifest,
        )
        checkpoint = {**checkpoint, "phase": "staged", "metadata": metadata}
        blob_store.put_json(path, checkpoint)
        if yield_after_checkpoint:
            return continuation("database-model", 1, "Recommendation artifacts staged; queued for model registration.")

    if checkpoint["phase"] == "staged":
        progress(1, "Registering the recommendation model against the published base analysis.")
        runtime._pulse_job_lease(lease_heartbeat)
        analysis_id, model_id = blob_store.persist_typed_generation(
            job_external_key=job_id, mix_key="phoenix2", snapshot={}, config=None,
            payload={"generatedAtUtc": generated_at}, phase="model",
            analysis_run_id=boundary["analysisRunId"], model_metadata=checkpoint["metadata"],
        )
        if str(analysis_id) != str(boundary["analysisRunId"]) or not model_id:
            raise RuntimeError("Recommendation registration returned an invalid typed model identity.")
        checkpoint = {**checkpoint, "phase": "registered", "modelGenerationId": str(model_id)}
        blob_store.put_json(path, checkpoint)
        if yield_after_checkpoint:
            return continuation("publish", 2, "Recommendation model registered; queued for pointer publication.")

    progress(2, "Publishing the recommendation model; tier lists and score inputs remain unchanged.")
    runtime._pulse_job_lease(lease_heartbeat)
    index = blob_store.get_json(recommendation_index_path(generation))
    expected = checkpoint["metadata"]["artifactManifest"]["sections"]["index"]
    if (not isinstance(index, Mapping) or index.get("generationKey") != generation
            or runtime._canonical_json_sha256(index) != expected["sha256"]):
        raise RuntimeError("The staged recommendation index failed validation.")
    current = job_store.get(job_id)
    if current is None:
        raise RuntimeError("The recommendation-only job is unavailable.")
    completed = {**current, "status": "completed", "stage": "publishing", "error": None,
                 "generatedAtUtc": generated_at, "updatedAtUtc": isoformat_utc(now()),
                 "completedAtUtc": isoformat_utc(now()), "retryAllowedAtUtc": None,
                 "progress": {"current": 3, "total": 3, "percent": 100,
                              "phase": "recommendation-model",
                              "message": "Recommendations refreshed; tier lists and stored score inputs were preserved."}}
    publish_args = {"job_id": job_id, "lease_owner": getattr(job_store, "lease_owner", ""),
                    "analysis_run_id": str(boundary["analysisRunId"]),
                    "model_generation_id": checkpoint["modelGenerationId"],
                    "expected_revisions": boundary["expectedRevisions"], "completed_job": completed}
    try:
        blob_store.publish_recommendation_refresh(index, **publish_args)
    except Exception:
        if not blob_store.publication_committed(generation_key=generation,
                analysis_run_id=publish_args["analysis_run_id"],
                model_generation_id=publish_args["model_generation_id"]):
            raise
    try:
        runtime._stop_job_lease(lease_heartbeat)
    except Exception:
        # Publication completed atomically; stopping its heartbeat is cleanup.
        pass
    return job_store.get(job_id) or completed
