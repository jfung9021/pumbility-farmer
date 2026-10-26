"""Private verified saved-run inputs; preparing a bundle never changes a job.

Objects use immutable binary JSON copies outside abandoned runtime checkpoint
cleanup. Operator responses contain only the summary; loaded bundles are private
runtime inputs and must never be returned by a public route.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from canonical_json import canonical_json_sha256 as digest
from mix_registry import resolve_mix
from official_scoring_sources import canonical_scoring_manifest_sha256, hydrate_official_scoring_manifest
from pumbility_contract import (
    combined_tier_blob_path, official_scoring_manifest_path, phoenix1_snapshot_path,
    recommendation_blob_path,
)

PREFIX = "analysis/private/captured-run-recovery/"
VERSION = 1
_HASH = re.compile(r"[a-f0-9]{64}")
_JOB = re.compile(r"[A-Za-z0-9_-]{1,128}")


class CapturedRunInputError(ValueError):
    """Exact saved inputs are missing, incompatible, corrupt or changed."""


def _body(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _copy_object(store, value, *, binary=False):
    from analysis_budget import work_budget_available
    if not work_budget_available():
        raise CapturedRunInputError("The recovery input-copy work budget is exhausted.")
    body = value if binary else _body(value)
    sha = hashlib.sha256(body).hexdigest()
    path = f"{PREFIX}objects/{sha}.{'bin' if binary else 'json'}"
    reference = {"path": path, "sha256": sha, "byteSize": len(body), "kind": "bytes" if binary else "json"}
    existing = store.get_bytes(path)
    if existing is None:
        store.put_bytes(path, body, content_type="application/octet-stream")
    elif existing != body:
        raise CapturedRunInputError("The immutable recovery object differs.")
    _load_object(store, reference)
    return reference


def _load_object(store, reference):
    if not isinstance(reference, Mapping) or not _HASH.fullmatch(str(reference.get("sha256") or "")):
        raise CapturedRunInputError("The recovery object reference is invalid.")
    kind = reference.get("kind")
    if kind not in {"json", "bytes"} or reference.get("path") != f"{PREFIX}objects/{reference['sha256']}.{'json' if kind == 'json' else 'bin'}":
        raise CapturedRunInputError("The recovery object path is invalid.")
    body = store.get_bytes(reference["path"])
    if (not isinstance(body, bytes) or len(body) != reference.get("byteSize")
            or hashlib.sha256(body).hexdigest() != reference["sha256"]):
        raise CapturedRunInputError("The recovery object bytes failed validation.")
    if kind == "bytes":
        return body
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapturedRunInputError("The recovery object JSON is invalid.") from error
    if _body(value) != body:
        raise CapturedRunInputError("The recovery object canonical content differs.")
    return value


def _input_projection(checkpoint):
    return {key: checkpoint.get(key) for key in (
        "jobId", "mix", "snapshot", "config", "payload", "typedShards", "combinedPreparation")}


def _manifest_from_objects(raw, captures):
    by_path = {raw["captures"][mix]["path"]: value for mix, value in captures.items()}
    return hydrate_official_scoring_manifest(raw, lambda relative: by_path.get(relative))


def prepare_captured_run_inputs(store, jobs, *, source_job_id: str, expected_manifest_sha256: str,
                                context_loader: Callable, source_deployment: str | None = None,
                                now: datetime | None = None) -> dict[str, Any]:
    """Validate and export an existing run; perform no upstream or lifecycle work.

    ``context_loader(store, checkpoint)`` is a trusted runtime callback validating
    the original settings/policy/BPM/recipes. Its optional ``sourceObjects`` map
    supplies additional exact private inputs for the recovery store overlay.
    """
    from analysis_runtime import (
        _load_typed_checkpoint_shard, _load_typed_checkpoint_snapshot, _typed_manifest,
        _verified_snapshot_reference, typed_checkpoint_path,
    )
    from phoenix2_sync import sanitize_snapshot

    if not _JOB.fullmatch(str(source_job_id)) or not _HASH.fullmatch(str(expected_manifest_sha256)):
        raise CapturedRunInputError("The captured source identity is invalid.")
    job = jobs.get(source_job_id)
    marker = (job or {}).get("officialSourceCapture", {})
    if (not isinstance(job, Mapping) or job.get("mix") not in {"phoenix2", "Phoenix2"}
            or marker.get("status") != "completed" or marker.get("manifestSha256") != expected_manifest_sha256):
        raise CapturedRunInputError("The source job has no matching complete official capture.")
    spec = resolve_mix("phoenix2")
    revisions, originals, objects = {}, {}, {}
    revision_reader = getattr(store, "artifact_revision", None)

    def read(path):
        if path in originals:
            return originals[path]
        revisions[path] = revision_reader(path) if callable(revision_reader) else None
        value = store.get_json(path)
        if not isinstance(value, Mapping):
            raise CapturedRunInputError("A required captured input is unavailable.")
        originals[path] = dict(value)
        return originals[path]

    checkpoint_path = typed_checkpoint_path(source_job_id, spec)
    checkpoint = read(checkpoint_path)
    if checkpoint.get("jobId") != source_job_id or checkpoint.get("mix") != spec.key:
        raise CapturedRunInputError("The source checkpoint belongs to another run.")
    snapshot_reference = checkpoint.get("snapshot")
    if not isinstance(snapshot_reference, Mapping):
        raise CapturedRunInputError("The captured snapshot reference is missing.")
    snapshot_path = snapshot_reference.get("pathname")
    read(snapshot_path)
    _verified_snapshot_reference(store, checkpoint=checkpoint, reference=snapshot_reference, mix_spec=spec)
    second = _load_typed_checkpoint_snapshot(store, checkpoint=checkpoint, reference=snapshot_reference, mix_spec=spec)
    prepared_reference = checkpoint.get("combinedPreparation")
    if not isinstance(prepared_reference, Mapping):
        raise CapturedRunInputError("The source job has no pinned combined preparation.")
    from analysis_runtime import _combined_preparation_path
    if prepared_reference.get("pathname") != _combined_preparation_path(source_job_id, spec):
        raise CapturedRunInputError("The combined preparation path differs from its source job.")
    prepared = read(prepared_reference["pathname"])
    if (prepared_reference.get("sha256") != digest(prepared) or prepared.get("jobId") != source_job_id
            or prepared.get("mix") != spec.key or prepared.get("snapshotReference") != snapshot_reference
            or prepared.get("generatedAtUtc") != checkpoint.get("payload", {}).get("generatedAtUtc")):
        raise CapturedRunInputError("The source combined preparation failed validation.")
    if prepared.get("snapshotRevision") is not None and (
            not callable(revision_reader) or revision_reader(snapshot_path) != prepared["snapshotRevision"]):
        raise CapturedRunInputError("The pinned captured snapshot revision changed.")
    first = sanitize_snapshot(read(phoenix1_snapshot_path()), mix="phoenix1")
    expected_snapshots = prepared.get("snapshotHashes")
    if expected_snapshots != {"phoenix1": digest(first), "phoenix2": digest(second)}:
        raise CapturedRunInputError("The captured submitted source hashes differ.")
    raw_manifest = read(official_scoring_manifest_path())
    if canonical_scoring_manifest_sha256(raw_manifest) != expected_manifest_sha256:
        raise CapturedRunInputError("The exact captured official manifest cannot be reconstructed.")
    prefix = official_scoring_manifest_path().rsplit("/", 1)[0]
    captures = {mix: read(f"{prefix}/{ref['path']}") for mix, ref in raw_manifest["captures"].items()}
    manifest = _manifest_from_objects(raw_manifest, captures)
    if marker.get("sourceDates") != {mix: cap["asOf"] for mix, cap in captures.items()}:
        raise CapturedRunInputError("The captured official source epochs differ.")
    if marker.get("boardCounts") != {mix: len(cap["boards"]) for mix, cap in captures.items()}:
        raise CapturedRunInputError("The captured official source counts differ.")
    for path, expected in prepared.get("sourceGuard", {}).get("sources", {}).items():
        if path.startswith("analysis/") and callable(revision_reader) and revision_reader(path) != expected:
            raise CapturedRunInputError("A pinned source revision changed before input export.")

    typed = _typed_manifest(checkpoint)
    frames = {}
    for name, dataset in typed["datasets"].items():
        frames[name] = []
        for descriptor in dataset["shards"]:
            read(descriptor["pathname"])
            frames[name].extend(_load_typed_checkpoint_shard(store, checkpoint=checkpoint, dataset=name, descriptor=descriptor))
    config, payload = checkpoint.get("config"), checkpoint.get("payload")
    if not isinstance(config, Mapping) or not isinstance(payload, Mapping):
        raise CapturedRunInputError("The saved analysis configuration or base payload is unavailable.")
    context = context_loader(store, checkpoint)
    if not isinstance(context, Mapping):
        raise CapturedRunInputError("The captured runtime context is invalid.")
    for path, value in context.get("sourceObjects", {}).items():
        if not isinstance(path, str) or not path.startswith("analysis/") or read(path) != value:
            raise CapturedRunInputError("An additional pinned source differs from its captured context.")

    from production_tiers import PREFIX as TIER_PREFIX
    from tier_pipeline_checkpoints import read_checkpoint, verified_checkpoint_bytes
    generation = checkpoint.get("tierProgress", {}).get("generation")
    if not _HASH.fullmatch(str(generation or "")):
        raise CapturedRunInputError("The source tier generation is unavailable.")
    # The generation's progress may advance during preparation. Its immutable
    # input identity and individually verified references are preserved instead.
    state = store.get_json(f"{TIER_PREFIX}generations/{generation}/state.json")
    if (not isinstance(state, Mapping) or state.get("generation") != generation
            or digest(state.get("identity")) != generation
            or state["identity"].get("officialManifest") != expected_manifest_sha256
            or state["identity"].get("phoenix1") != digest(first)
            or state["identity"].get("phoenix2") != digest(second)):
        raise CapturedRunInputError("The source tier generation inputs differ.")
    references = []
    if isinstance(state.get("general"), Mapping):
        references.append(dict(state["general"]))
    for name, reference in state.get("phaseInputs", {}).items():
        if (reference.get("stage") != "phase-inputs-" + name
                or reference.get("identity") != digest({"generation": generation, "kind": name})):
            raise CapturedRunInputError("A source phase reference differs.")
        references.append(dict(reference))
    for item in state.get("preparedFitInputs", []):
        reference = item.get("inputs", {})
        expected = digest({"generation": generation, "goal": item["goal"],
                           "source": item["source"], "mode": item["mode"], "identity": item["identity"]})
        if reference.get("stage") != "cohort-inputs" or reference.get("identity") != expected:
            raise CapturedRunInputError("A source cohort reference differs.")
        references.append(dict(reference))
    binary_objects = {}
    for reference in references:
        read_checkpoint(store, reference, stage=reference["stage"], identity=reference["identity"])
        body = verified_checkpoint_bytes(store, reference, stage=reference["stage"], identity=reference["identity"])
        binary_objects[reference["path"]] = _copy_object(store, body, binary=True)
    for reference in state.get("fitReferences", []):
        fit = read(reference["path"])
        if fit.get("identity") != reference.get("identity") or digest(fit.get("fit")) != reference.get("fitSha256"):
            raise CapturedRunInputError("A preserved source fit failed validation.")

    # Source input fields stay fixed while old fit/progress counters may advance.
    if _input_projection(store.get_json(checkpoint_path) or {}) != _input_projection(checkpoint):
        raise CapturedRunInputError("The source input references changed during export.")
    for path, value in originals.items():
        if path == checkpoint_path:
            continue
        if callable(revision_reader):
            if revision_reader(path) != revisions[path]:
                raise CapturedRunInputError("A captured input revision changed during export.")
        elif digest(store.get_json(path)) != digest(value):
            raise CapturedRunInputError("A captured input changed during export.")
    _verified_snapshot_reference(store, checkpoint=checkpoint, reference=snapshot_reference, mix_spec=spec)
    if (jobs.get(source_job_id) or {}).get("officialSourceCapture") != marker:
        raise CapturedRunInputError("The source official capture identity changed during export.")

    for path, value in originals.items():
        if path != checkpoint_path:
            objects[path] = _copy_object(store, value)
    inputs = {"phoenix1": first, "phoenix2": second, "config": dict(config), "payload": dict(payload),
              "typedShards": typed, "typedFrames": frames, "eligiblePlayerCount": checkpoint.get("eligiblePlayerCount"),
              "combinedPreparation": prepared, "context": dict(context), "sourceCheckpoint": _input_projection(checkpoint)}
    inputs["sourceGeneration"] = {"generation": generation, "identity": state["identity"],
                                  "protectedReferences": references, "fitReferences": state.get("fitReferences", []),
                                  "preparedFitInputs": [{key: item[key] for key in ("goal", "source", "mode", "identity", "inputs")}
                                                        for item in state.get("preparedFitInputs", [])]}
    inputs_reference = _copy_object(store, inputs)
    rollback, serving_revisions = {}, {}
    for name, path in (("tiers", combined_tier_blob_path()), ("recommendations", recommendation_blob_path())):
        serving_revisions[path] = revision_reader(path) if callable(revision_reader) else None
        value = store.get_json(path)
        if value is not None:
            rollback[name] = _copy_object(store, value)
    for name, path in (("tiers", combined_tier_blob_path()), ("recommendations", recommendation_blob_path())):
        if callable(revision_reader):
            stable = revision_reader(path) == serving_revisions[path]
        else:
            current = store.get_json(path)
            stable = (current is None and name not in rollback) or (
                name in rollback and digest(current) == digest(_load_object(store, rollback[name])))
        if not stable:
            raise CapturedRunInputError("The serving generation changed during rollback preservation.")
    verified_at = now or datetime.now(timezone.utc)
    if verified_at.tzinfo is None:
        raise CapturedRunInputError("The bundle verification timestamp must be timezone-aware.")
    bundle = {"version": VERSION, "sourceJobId": source_job_id, "sourceDeployment": source_deployment,
              "officialManifestSha256": expected_manifest_sha256, "sourceSnapshotHashes": dict(expected_snapshots),
              "sourceDates": {mix: cap["asOf"] for mix, cap in captures.items()},
              "sourceFetchedAtUtc": {mix: cap["fetchedAtUtc"] for mix, cap in captures.items()},
              "boardCounts": {mix: len(cap["boards"]) for mix, cap in captures.items()},
              "copiedAtUtc": verified_at.astimezone(timezone.utc).isoformat(),
              "sourceRevisions": revisions, "servingRevisions": serving_revisions,
              "sourceObjects": objects, "sourceBinaryObjects": binary_objects,
              "inputs": inputs_reference, "rollback": rollback}
    body = _body(bundle)
    bundle_id = hashlib.sha256(body).hexdigest()
    path = f"{PREFIX}bundles/{bundle_id}.json"
    existing = store.get_bytes(path)
    if existing is None:
        store.put_bytes(path, body, content_type="application/octet-stream")
    elif existing != body:
        raise CapturedRunInputError("The immutable recovery bundle differs.")
    loaded = load_captured_run_inputs(store, bundle_id)
    return {"bundleId": bundle_id, "summary": captured_run_summary(loaded)}


def load_captured_run_inputs(store, bundle_id: str) -> dict[str, Any]:
    """Load only verified private copies; never read latest mutable sources."""
    if not _HASH.fullmatch(str(bundle_id)):
        raise CapturedRunInputError("The recovery bundle identity is invalid.")
    body = store.get_bytes(f"{PREFIX}bundles/{bundle_id}.json")
    if not isinstance(body, bytes) or hashlib.sha256(body).hexdigest() != bundle_id:
        raise CapturedRunInputError("The recovery bundle bytes failed validation.")
    try:
        bundle = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapturedRunInputError("The recovery bundle JSON is invalid.") from error
    if bundle.get("version") != VERSION or _body(bundle) != body:
        raise CapturedRunInputError("The recovery bundle schema differs.")
    inputs = _load_object(store, bundle["inputs"])
    source_objects = {path: _load_object(store, ref) for path, ref in bundle["sourceObjects"].items()}
    binary_objects = {path: _load_object(store, ref) for path, ref in bundle.get("sourceBinaryObjects", {}).items()}
    raw = source_objects[official_scoring_manifest_path()]
    if canonical_scoring_manifest_sha256(raw) != bundle["officialManifestSha256"]:
        raise CapturedRunInputError("The bundled official manifest hash differs.")
    prefix = official_scoring_manifest_path().rsplit("/", 1)[0]
    captures = {mix: source_objects[f"{prefix}/{ref['path']}"] for mix, ref in raw["captures"].items()}
    manifest = _manifest_from_objects(raw, captures)
    if ({mix: cap["asOf"] for mix, cap in captures.items()} != bundle["sourceDates"]
            or {mix: cap["fetchedAtUtc"] for mix, cap in captures.items()} != bundle["sourceFetchedAtUtc"]
            or {"phoenix1": digest(inputs["phoenix1"]), "phoenix2": digest(inputs["phoenix2"])} != bundle["sourceSnapshotHashes"]):
        raise CapturedRunInputError("The bundled source provenance differs.")
    rollback = {name: _load_object(store, ref) for name, ref in bundle.get("rollback", {}).items()}
    from analysis_runtime import _load_typed_checkpoint_snapshot, _load_typed_checkpoint_shard, _typed_manifest
    from tier_pipeline_checkpoints import read_checkpoint
    from phoenix2_sync import sanitize_snapshot
    class SavedObjects:
        def get_json(self, path):
            return source_objects.get(path)
        def get_bytes(self, path):
            return binary_objects.get(path)
    saved = SavedObjects()
    checkpoint = inputs["sourceCheckpoint"]
    if (checkpoint.get("jobId") != bundle["sourceJobId"] or checkpoint.get("mix") != "phoenix2"
            or checkpoint.get("config") != inputs["config"] or checkpoint.get("payload") != inputs["payload"]
            or checkpoint.get("typedShards") != inputs["typedShards"]):
        raise CapturedRunInputError("The bundled base analysis inputs differ.")
    second = _load_typed_checkpoint_snapshot(saved, checkpoint=checkpoint,
        reference=checkpoint["snapshot"], mix_spec=resolve_mix("phoenix2"))
    first = sanitize_snapshot(source_objects[phoenix1_snapshot_path()], mix="phoenix1")
    if first != inputs["phoenix1"] or second != inputs["phoenix2"]:
        raise CapturedRunInputError("The bundled submitted snapshots differ.")
    typed = _typed_manifest(checkpoint)
    for name, dataset in typed["datasets"].items():
        rows = []
        for descriptor in dataset["shards"]:
            rows.extend(_load_typed_checkpoint_shard(saved, checkpoint=checkpoint, dataset=name, descriptor=descriptor))
        if rows != inputs["typedFrames"][name]:
            raise CapturedRunInputError("The bundled typed analysis rows differ.")
    prepared = inputs["combinedPreparation"]
    if (digest(prepared) != checkpoint["combinedPreparation"]["sha256"]
            or prepared.get("snapshotHashes") != bundle["sourceSnapshotHashes"]):
        raise CapturedRunInputError("The bundled combined preparation differs.")
    generation = inputs["sourceGeneration"]
    if digest(generation["identity"]) != generation["generation"]:
        raise CapturedRunInputError("The bundled tier generation differs.")
    for reference in generation["protectedReferences"]:
        read_checkpoint(saved, reference, stage=reference["stage"], identity=reference["identity"])
    for item in generation.get("preparedFitInputs", []):
        reference = item["inputs"]
        expected = digest({"generation": generation["generation"], "goal": item["goal"],
                           "source": item["source"], "mode": item["mode"], "identity": item["identity"]})
        if reference.get("stage") != "cohort-inputs" or reference.get("identity") != expected:
            raise CapturedRunInputError("The bundled cohort input identity differs.")
        read_checkpoint(saved, reference, stage="cohort-inputs", identity=expected)
    for reference in generation["fitReferences"]:
        fit = source_objects[reference["path"]]
        if fit.get("identity") != reference.get("identity") or digest(fit.get("fit")) != reference.get("fitSha256"):
            raise CapturedRunInputError("The bundled fit reference differs.")
    return {**inputs, "bundleId": bundle_id, "sourceJobId": bundle["sourceJobId"],
            "officialManifest": manifest, "rawOfficialManifest": raw, "officialCaptures": captures,
            "sourceObjects": source_objects, "sourceBinaryObjects": binary_objects,
            "rollback": rollback, "sourceProvenance": bundle}


def captured_run_summary(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Safe operator projection, without player data or private object paths."""
    provenance = bundle["sourceProvenance"]
    summary = {"bundleId": bundle["bundleId"], "sourceJobId": bundle["sourceJobId"],
            "officialManifestSha256": provenance["officialManifestSha256"],
            "sourceDates": provenance["sourceDates"], "sourceFetchedAtUtc": provenance["sourceFetchedAtUtc"],
            "boardCounts": provenance["boardCounts"], "sourceSnapshotHashes": provenance["sourceSnapshotHashes"],
            "copiedAtUtc": provenance["copiedAtUtc"], "preservedObjectCount": len(provenance["sourceObjects"]) + len(provenance.get("sourceBinaryObjects", {})),
            "upstreamRefreshed": False}
    inventory = bundle.get("context", {}).get("fitInventory")
    if inventory is not None:
        summary["fitInventory"] = _safe_fit_inventory(inventory)
    return summary


def _safe_fit_inventory(inventory):
    """Explicit aggregate projection; never expose private artifact references."""
    if not isinstance(inventory, Mapping) or not _HASH.fullmatch(str(inventory.get("generation") or "")):
        raise CapturedRunInputError("The retained fit inventory identity is invalid.")
    producer = inventory.get("producer")
    if not isinstance(producer, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", producer):
        raise CapturedRunInputError("The retained fit inventory producer is invalid.")
    cohorts = inventory.get("cohorts")
    if not isinstance(cohorts, list) or len(cohorts) > 4:
        raise CapturedRunInputError("The retained fit inventory cohorts are invalid.")
    flags = {"outerComplete", "mainComplete", "bootstrapCheckpoint", "pointContext",
             "adoptionRequired", "pointReplayPossible"}
    counts = {"target", "samples", "seed", "bootstrapCompleted", "remainingInitialSolves", "remainingBootstrapAttempts"}
    rows = []
    for row in cohorts:
        if (not isinstance(row, Mapping) or row.get("goal") != "sssplus"
                or row.get("source") not in {"official", "submitted"}
                or row.get("mode") not in {"Single", "Double", "singles", "doubles"}):
            raise CapturedRunInputError("The retained fit inventory cohort is invalid.")
        safe = {key: row[key] for key in ("goal", "source", "mode")}
        for key in flags:
            if key in row:
                if type(row[key]) is not bool:
                    raise CapturedRunInputError("The retained fit inventory flag is invalid.")
                safe[key] = row[key]
        for key in counts:
            if key in row:
                if type(row[key]) is not int or row[key] < 0:
                    raise CapturedRunInputError("The retained fit inventory count is invalid.")
                safe[key] = row[key]
        if row.get("componentIdentity") is not None:
            if not _HASH.fullmatch(str(row["componentIdentity"])):
                raise CapturedRunInputError("The retained fit component identity is invalid.")
            safe["componentIdentity"] = row["componentIdentity"]
        warnings = row.get("warnings", [])
        if not isinstance(warnings, list) or any(not isinstance(value, str) or not re.fullmatch(r"[a-z0-9_-]{1,80}", value) for value in warnings):
            raise CapturedRunInputError("The retained fit inventory warning is invalid.")
        safe["warnings"] = list(warnings)
        rows.append(safe)
    return {"version": 1, "generation": inventory["generation"], "producer": producer, "cohorts": rows}
