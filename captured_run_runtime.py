"""Runtime adapters for one explicit recovery from verified captured inputs."""
from __future__ import annotations

import copy
from collections.abc import Mapping

from canonical_json import canonical_json_sha256 as digest


def captured_run_recovery_context(store, checkpoint):
    """Resolve existing pinned policy; never fit or fetch during preparation."""
    import analysis_runtime as runtime
    from production_tiers import PREFIX, numerical_pipeline_recipe
    from tier_pipeline_checkpoints import read_checkpoint

    job_id = str(checkpoint.get("jobId") or "")
    mix = runtime.resolve_mix(checkpoint.get("mix"))
    from piu_misgrade_analyzer import AnalysisConfig
    config = AnalysisConfig(**checkpoint.get("config", {}))
    if mix.key != "phoenix2" or runtime.resolve_mix(config.mix).key != mix.key:
        raise ValueError("The captured base analysis configuration has a different mix.")
    ref = checkpoint.get("combinedPreparation")
    if not isinstance(ref, Mapping) or ref.get("pathname") != runtime._combined_preparation_path(job_id, mix):
        raise ValueError("The source job has no pinned combined preparation.")
    prepared = store.get_json(ref["pathname"])
    if (not isinstance(prepared, Mapping) or digest(prepared) != ref.get("sha256")
            or prepared.get("jobId") != job_id or prepared.get("mix") != mix.key
            or prepared.get("snapshotReference") != checkpoint.get("snapshot")):
        raise ValueError("The captured combined preparation failed validation.")
    guard = prepared.get("sourceGuard")
    live = runtime._combined_preparation_guard(store)
    if not isinstance(guard, Mapping) or any(guard.get(key) != live.get(key) for key in ("flags", "sources")):
        raise ValueError("The captured source revisions or enabled policy changed.")
    progress = runtime._validated_tier_progress(checkpoint.get("tierProgress"))
    generation = progress["generation"]
    state = store.get_json(PREFIX + "generations/" + generation + "/state.json")
    if (not isinstance(state, Mapping) or state.get("generation") != generation
            or digest(state.get("identity")) != generation):
        raise ValueError("The captured tier input identity differs.")
    model_ref = state.get("phaseInputs", {}).get("model")
    policy_ref = state.get("phaseInputs", {}).get("provenance")
    if not isinstance(model_ref, Mapping) or not isinstance(policy_ref, Mapping):
        raise ValueError("The captured model/source policy inputs are missing.")
    model = read_checkpoint(store, model_ref, stage="phase-inputs-model",
                            identity=digest({"generation": generation, "kind": "model"}))
    provenance = read_checkpoint(store, policy_ref, stage="phase-inputs-provenance",
                                 identity=digest({"generation": generation, "kind": "provenance"}))
    arguments = runtime._official_tier_arguments(store)
    names = ("official_snapshot", "cross_level_clearing", "midpoint_compression", "hybrid_lower_tiers", "average_pumbility")
    arguments = {name: arguments.get(name) for name in names}
    if state["identity"].get("generalArguments") != digest(arguments):
        raise ValueError("The captured General policy arguments changed.")
    context = {"version": 1, "sourceGuard": guard, "arguments": arguments,
               "catalog": model["prepared"]["catalog"], "sourcePolicy": provenance["policy"],
               "originalProducerIdentity": state["identity"],
               "producerRecipe": numerical_pipeline_recipe(True),
               "sourceObjects": {}}
    if arguments["official_snapshot"] is not None:
        context["sourceObjects"][runtime.official_tier_snapshot_path()] = arguments["official_snapshot"]
    consent_reader = getattr(store, "captured_snapshot_consent_guard", None)
    if callable(consent_reader):
        snapshot = runtime._load_typed_checkpoint_snapshot(store, checkpoint=checkpoint,
            reference=checkpoint["snapshot"], mix_spec=mix)
        context["canonicalAdoptionGuard"] = consent_reader(snapshot["generatedAtUtc"])
    elif getattr(store, "typed_persistence_enabled", False):
        raise ValueError("The captured snapshot consent guard is unavailable.")
    from production_tiers import inventory_retained_fits
    context["fitInventory"], _ = inventory_retained_fits(store, generation)
    validate_captured_context(store, context)
    return context


def validate_captured_context(store, context):
    import analysis_runtime as runtime
    from chart_source_exclusions import load_exclusions
    from production_tiers import numerical_pipeline_recipe

    if not isinstance(context, Mapping) or context.get("version") != 1:
        raise ValueError("The captured runtime policy is invalid.")
    if digest(context.get("arguments")) != context.get("originalProducerIdentity", {}).get("generalArguments"):
        raise ValueError("The captured General policy arguments changed.")
    live = runtime._combined_preparation_guard(store)
    if any(context["sourceGuard"].get(key) != live.get(key) for key in ("flags", "sources")):
        raise ValueError("The captured source revisions or enabled policy changed.")
    if context.get("producerRecipe") != numerical_pipeline_recipe(True):
        raise ValueError("The captured recovery producer changed after preparation.")
    catalog = context["catalog"]
    rows = list(catalog.values()) if isinstance(catalog, dict) else catalog
    policy = load_exclusions(rows)
    if policy.public() != context["sourcePolicy"]:
        raise ValueError("The captured source exclusion policy changed.")


class CapturedInputStore:
    """Read captured sources and existing numerical caches; delegate new writes."""
    def __init__(self, store, bundle, *, job_id=None, lease_owner=None):
        self.base = store
        self.captured_bundle = bundle
        self.job_id, self.lease_owner = job_id, lease_owner
        self.captured_publication_sources = None

    def get_json(self, path):
        objects = self.captured_bundle["sourceObjects"]
        if path in objects:
            return copy.deepcopy(objects[path])
        return self.base.get_json(path)

    def get_bytes(self, path):
        objects = self.captured_bundle.get("sourceBinaryObjects", {})
        if path in objects:
            return objects[path]
        return self.base.get_bytes(path)

    def put_json(self, path, value):
        if path in self.captured_bundle["sourceObjects"]:
            raise ValueError("A recovery cannot overwrite a captured input.")
        from analysis_runtime import current_snapshot_path
        if path == current_snapshot_path("phoenix2"):
            guard = self.captured_bundle["context"].get("canonicalAdoptionGuard")
            if not isinstance(guard, Mapping) or not self.job_id:
                raise ValueError("The captured canonical snapshot has no consent guard.")
            return self.base.put_json(path, value, captured_recovery_source={
                **guard, "jobId": self.job_id, "bundleId": self.captured_bundle["bundleId"]})
        return self.base.put_json(path, value)

    def put_bytes(self, path, value, **kwargs):
        if path in self.captured_bundle.get("sourceBinaryObjects", {}):
            raise ValueError("A recovery cannot overwrite a captured input.")
        return self.base.put_bytes(path, value, **kwargs)

    def publish_generation(self, payloads, **kwargs):
        validate_captured_context(self.base, self.captured_bundle["context"])
        if not self.job_id or not self.lease_owner or not self.captured_publication_sources:
            raise ValueError("The recovery publication has no active owner.")
        return self.base.publish_generation(payloads, **kwargs, captured_recovery={
            "jobId": self.job_id, "leaseOwner": self.lease_owner,
            "sourceRevisions": self.captured_publication_sources})

    def __getattr__(self, name):
        return getattr(self.base, name)


def capture_publication_sources(store):
    """Pin canonical score/consent revisions after adopting the saved snapshot."""
    import analysis_runtime as runtime
    validate_captured_context(store.base, store.captured_bundle["context"])
    revisions = store.base.tier_publication_revisions()
    required = {"analysis/private/source-revisions/phoenix1.json",
                "analysis/private/source-revisions/phoenix2.json", runtime.official_scoring_manifest_path()}
    selected = {key: value for key, value in revisions.items()
                if key in required or key.startswith("analysis/private/official/scoring/")}
    if not required <= selected.keys():
        raise ValueError("The recovery canonical source revisions are unavailable.")
    high = runtime.official_tier_snapshot_path()
    if high in store.captured_bundle["context"]["sourceGuard"]["sources"]:
        selected[high] = store.base.artifact_revision(high)
    return selected


class CapturedAdoptionPending(RuntimeError):
    """Durable new-job adoption receipts await another bounded invocation."""
    def __init__(self, sequence, completed, total):
        self.sequence, self.completed, self.total = sequence, completed, total
        super().__init__("Captured input adoption checkpointed for continuation.")


def adopt_captured_run_checkpoint(*, blob_store, job_store, job_id, mix_spec, bundle,
                                  checkpoint_path, lease_heartbeat, now, max_units=4):
    """Re-envelope bounded saved inputs, resuming verified new-job receipts."""
    import analysis_runtime as runtime
    from analysis_budget import work_budget_available
    from piu_misgrade_analyzer import AnalysisConfig
    from target_fit_cache import adopt_reviewed_retained_components
    from tier_pipeline_checkpoints import read_checkpoint

    config = AnalysisConfig(**bundle["config"])
    if (mix_spec.key != "phoenix2" or runtime.resolve_mix(config.mix).key != mix_spec.key
            or checkpoint_path != runtime.typed_checkpoint_path(job_id, mix_spec)):
        raise ValueError("The captured recovery mix is invalid.")
    if type(max_units) is not int or max_units < 1:
        raise ValueError("The captured adoption unit budget is invalid.")
    names = ("baselines", "contributions", "chartResults")
    existing = blob_store.get_json(checkpoint_path)
    if existing is not None:
        recovery = existing.get("capturedRecovery", {})
        if (existing.get("jobId") != job_id or existing.get("mix") != mix_spec.key
                or recovery.get("bundleId") != bundle["bundleId"]):
            raise ValueError("The existing captured adoption belongs to another input.")
        runtime._typed_manifest(existing)
        return existing
    source = bundle["sourceGeneration"]
    cohorts = [item for item in source.get("preparedFitInputs", []) if item["goal"] == "sssplus"]
    cohort_keys = [digest(item) for item in cohorts]
    shard_counts = {name: (len(bundle["typedFrames"][name]) + runtime.TYPED_CHECKPOINT_ROW_LIMIT - 1)
                         // runtime.TYPED_CHECKPOINT_ROW_LIMIT for name in names}
    total = len(cohorts) + sum(shard_counts.values()) + 1
    cursor_path = runtime.typed_checkpoint_shard_prefix(job_id, mix_spec) + "captured-adoption.json"
    identity = {"version": 1, "jobId": job_id, "mix": mix_spec.key, "bundleId": bundle["bundleId"],
                "sourceJobId": bundle["sourceJobId"], "configSha256": digest(bundle["config"]),
                "framesSha256": digest(bundle["typedFrames"]), "snapshotSha256": digest(bundle["phoenix2"]),
                "cohortKeys": cohort_keys, "rowLimit": runtime.TYPED_CHECKPOINT_ROW_LIMIT}
    cursor = blob_store.get_json(cursor_path)
    if cursor is None:
        cursor = {"identity": identity, "createdAtUtc": runtime.isoformat_utc(now()),
                  "fitAdoptions": [], "shards": {name: [] for name in names}, "receipts": {}, "snapshot": None}
    if (cursor.get("identity") != identity or not isinstance(cursor.get("fitAdoptions"), list)
            or cursor.get("fitAdoptions") != cohort_keys[:len(cursor.get("fitAdoptions", []))]
            or len(cursor.get("fitAdoptions", [])) > len(cohort_keys)
            or not isinstance(cursor.get("shards"), dict) or set(cursor["shards"]) != set(names)):
        raise ValueError("The captured adoption cursor inputs differ.")
    revision = getattr(blob_store, "artifact_revision", None)
    receipt_checkpoint = {"jobId": job_id, "mix": mix_spec.key}

    def save():
        blob_store.put_json(cursor_path, cursor)
        if blob_store.get_json(cursor_path) != cursor:
            raise ValueError("The captured adoption receipt changed during persistence.")

    def completed():
        return len(cursor["fitAdoptions"]) + sum(len(cursor["shards"][name]) for name in names) + int(cursor["snapshot"] is not None)

    def pending():
        save()
        count = completed()
        sequence = f"captured-adoption-{bundle['bundleId'][:16]}-{count}"
        raise CapturedAdoptionPending(sequence, count, total)

    units = 0
    def begin_unit():
        runtime._pulse_job_lease(lease_heartbeat)
        if units >= max_units or not work_budget_available():
            pending()

    # Validate each committed receipt before trusting its progress. Revision
    # tokens avoid downloading unchanged completed shards on a typed backend.
    runtime._pulse_job_lease(lease_heartbeat)
    if not work_budget_available():
        pending()
    earlier_complete = True
    for name in names:
        descriptors = cursor["shards"][name]
        if not isinstance(descriptors, list) or len(descriptors) > shard_counts[name] or (descriptors and not earlier_complete):
            raise ValueError("The captured adoption shard cursor is invalid.")
        for shard, descriptor in enumerate(descriptors):
            runtime._pulse_job_lease(lease_heartbeat)
            if not work_budget_available():
                pending()
            offset = shard * runtime.TYPED_CHECKPOINT_ROW_LIMIT
            part = bundle["typedFrames"][name][offset:offset + runtime.TYPED_CHECKPOINT_ROW_LIMIT]
            expected = {"pathname": runtime.typed_checkpoint_shard_path(job_id, name, shard, mix_spec),
                        "shard": shard, "rowCount": len(part), "sha256": digest(part)}
            if descriptor != expected:
                raise ValueError("The captured adoption shard identity differs.")
            receipt = cursor.get("receipts", {}).get(descriptor["pathname"])
            if callable(revision) and receipt is not None:
                if revision(descriptor["pathname"]) != receipt:
                    raise ValueError("The captured adoption shard revision changed.")
            else:
                runtime._load_typed_checkpoint_shard(blob_store, checkpoint=receipt_checkpoint,
                    dataset=name, descriptor=descriptor)
        earlier_complete = earlier_complete and len(descriptors) == shard_counts[name]
    if cursor["snapshot"] is not None:
        runtime._pulse_job_lease(lease_heartbeat)
        if not work_budget_available():
            pending()
        if completed() != total:
            raise ValueError("The captured snapshot precedes complete shard adoption.")
        snapshot_checkpoint = {**receipt_checkpoint, "snapshot": cursor["snapshot"]}
        runtime._verified_snapshot_reference(blob_store, checkpoint=snapshot_checkpoint,
            reference=cursor["snapshot"], mix_spec=mix_spec)
        runtime._load_typed_checkpoint_snapshot(blob_store, checkpoint=snapshot_checkpoint,
            reference=cursor["snapshot"], mix_spec=mix_spec)

    # A completed compatibility bridge is recorded independently of row copies.
    # The bridge itself remains idempotent if a process dies before its receipt.
    for item in cohorts[len(cursor["fitAdoptions"]):]:
        begin_unit()
        reference = item["inputs"]
        values = read_checkpoint(blob_store, reference, stage="cohort-inputs", identity=reference["identity"])
        adopt_reviewed_retained_components(blob_store, values["boards"],
            legacy_producer_identity=source["identity"].get("modelCode"))
        cursor["fitAdoptions"].append(digest(item))
        units += 1
        save()
    datasets = {}
    for name in names:
        rows = bundle["typedFrames"][name]
        descriptors = cursor["shards"][name]
        for shard in range(len(descriptors), shard_counts[name]):
            begin_unit()
            offset = shard * runtime.TYPED_CHECKPOINT_ROW_LIMIT
            part = rows[offset:offset + runtime.TYPED_CHECKPOINT_ROW_LIMIT]
            sha = digest(part)
            path = runtime.typed_checkpoint_shard_path(job_id, name, shard, mix_spec)
            descriptor = {"pathname": path, "shard": shard, "rowCount": len(part), "sha256": sha}
            # A killed invocation can leave a verified shard before its cursor
            # write. Adopt that orphan receipt instead of rewriting the rows.
            if blob_store.get_json(path) is None:
                blob_store.put_json(path, {"schemaVersion": runtime.TYPED_CHECKPOINT_SHARD_SCHEMA_VERSION,
                    "jobId": job_id, "mix": mix_spec.key, "dataset": name, "shard": shard,
                    "rowCount": len(part), "sha256": sha, "rows": part})
            runtime._load_typed_checkpoint_shard(blob_store, checkpoint=receipt_checkpoint,
                dataset=name, descriptor=descriptor)
            descriptors.append(descriptor)
            if callable(revision):
                cursor["receipts"][path] = revision(path)
            units += 1
            save()
        datasets[name] = {"rowCount": len(rows), "shardCount": len(descriptors),
                          "shards": descriptors, "sha256": digest(descriptors)}
    manifest = {"schemaVersion": runtime.TYPED_CHECKPOINT_SHARD_SCHEMA_VERSION,
                "rowLimit": runtime.TYPED_CHECKPOINT_ROW_LIMIT, "datasets": datasets, "sha256": digest(datasets)}
    if cursor["snapshot"] is None:
        begin_unit()
        path = runtime.typed_checkpoint_snapshot_path(job_id, mix_spec)
        if blob_store.get_json(path) is None:
            snapshot = runtime._write_typed_checkpoint_snapshot(blob_store, job_id=job_id,
                mix_spec=mix_spec, snapshot=bundle["phoenix2"])
        else:
            snapshot = {"pathname": path, "sha256": identity["snapshotSha256"]}
            runtime._load_typed_checkpoint_snapshot(blob_store, checkpoint=receipt_checkpoint,
                reference=snapshot, mix_spec=mix_spec)
            reader = getattr(blob_store, "typed_snapshot_receipt", None)
            if callable(reader):
                snapshot["artifactReceipt"] = reader(path)
                runtime._verified_snapshot_reference(blob_store, checkpoint=receipt_checkpoint,
                    reference=snapshot, mix_spec=mix_spec)
        cursor["snapshot"] = snapshot
        units += 1
        save()
    runtime._pulse_job_lease(lease_heartbeat)
    if not work_budget_available():
        pending()
    snapshot = cursor["snapshot"]
    timestamp = cursor["createdAtUtc"]
    payload = copy.deepcopy(bundle["payload"])
    payload["generatedAtUtc"] = timestamp
    if isinstance(payload.get("summary"), dict):
        payload["summary"]["generatedAtUtc"] = timestamp
    checkpoint = {"schemaVersion": runtime.TYPED_CHECKPOINT_SCHEMA_VERSION,
        "phase": runtime.TYPED_CHECKPOINT_ANALYSIS_PHASE, "jobId": job_id, "mix": mix_spec.key,
        "createdAtUtc": timestamp, "reanalyzeOnly": False,
        "eligiblePlayerCount": bundle["eligiblePlayerCount"],
        "snapshot": snapshot, "config": bundle["config"], "payload": payload,
        "typedShards": manifest, "combined": None, "combinedTier": None, "model": None,
        "capturedRecovery": {"version": 1, "bundleId": bundle["bundleId"],
            "sourceJobId": bundle["sourceJobId"], "sourceSnapshotHashes": bundle["sourceProvenance"]["sourceSnapshotHashes"],
            "upstreamRefreshed": False}}
    runtime._typed_manifest(checkpoint)
    blob_store.put_json(checkpoint_path, checkpoint)
    runtime._pulse_job_lease(lease_heartbeat)
    return checkpoint
