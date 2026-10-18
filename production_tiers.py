"""Hosted publication of the approved source-aware Scoring/Clearing generation.

Fits are private, content-addressed, and checkpointed one cohort per invocation.
The public view omits historical rollback copies; the complete audit stays private.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

VERSION = "6.21.0-submitted-sssplus"
PREVIOUS_VERSION = "6.20.0-source-aware-sssplus"
PREFIX = "analysis/private/source-aware-tiers/"
SEED = 20261018
SAMPLES = 200
MODES = (("Single", "singles"), ("Double", "doubles"))


class TierAnalysisPending(RuntimeError):
    def __init__(self, sequence: str, completed: int, *, stage="target-fit", generation=None,
                 progress_ordinal=None):
        super().__init__(f"Tier analysis phase {stage} checkpointed; continuing tier analysis.")
        self.sequence, self.completed = sequence, completed
        self.stage, self.generation = stage, generation
        self.progress_ordinal = completed if progress_ordinal is None else progress_ordinal
        self.total_stages = 8 + 1 + len(FINISH_STAGES) + 2


def digest(value: Any) -> str:
    from canonical_json import canonical_json_sha256
    return canonical_json_sha256(value)


def model_code_identity() -> str:
    root = Path(__file__).resolve().parent
    return digest({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("sss_scoring_model.py", "official_player_scoring.py", "scoring_targets.py")})


FINISH_STAGES = ("centered-sss", "sssplus", "median", "spread", "combined-clearing",
                 "source-aware", "submitted-calibration")
STAGE_SCHEMAS = dict(zip(FINISH_STAGES, (51, 52, 53, 54, 55, 56, 57)))
GENERAL_FILES = ("scoring_percentile.py", "official_cross_level_scoring.py", "official_player_scoring.py",
                 "official_scoring_calibration.py", "cross_level_clearing.py", "cross_level_clearing_calibration.py",
                 "hybrid_tiers.py", "pumbility_average.py", "tier_postprocessing.py", "tier_difficulty.py",
                 "piu_recommendations.py", "piu_misgrade_analyzer.py", "player_skill_ratings.py",
                 "phoenix1_score_overrides.py", "official_score_merge.py", "official_scoring_sources.py",
                 "official_cross_level_calibration.py", "chart_source_exclusions.py", "pumbility_contract.py")
STAGE_FILES = {
    "centered-sss": ("sss_scoring.py", "refined_tiers.py", "sss_scoring_refinement.py", "sss_folder_centering.py"),
    "sssplus": ("sssplus_tiers.py", "sss_scoring_refinement.py", "target_tail_policy.py"),
    "median": ("sssplus_median_calibration.py", "sssplus_final_curve.py"),
    "spread": ("sssplus_spread.py", "sssplus_rare_continuous.py", "target_tail_policy.py"),
    "combined-clearing": ("combined_official_clearing.py", "official_high_level_clearing.py"),
    "source-aware": ("source_aware_tiers.py", "final_folder_width.py"),
    "submitted-calibration": ("submitted_sssplus_calibration.py",),
    "archive": ("tier_pipeline_checkpoints.py",),
    "public": ("production_tiers.py", "tier_public.py", "config/tier-public-fields.json"),
}
# General's numeric sources at the previously deployed, approved generation.
# This permits a one-time legacy cache adoption without trusting unrelated old
# artifacts after a future General methodology change.
LEGACY_GENERAL_CODE_IDENTITY = "cba011919588bcfff6db2a8db79878db23359896d6ed4020aa27750134d9577b"


def stage_code_identity(stage):
    root = Path(__file__).resolve().parent
    names = set(STAGE_FILES.get(stage, GENERAL_FILES)) | {"production_tiers.py", "canonical_json.py"}
    # Include every numeric dependency even when a helper is shared across stages.
    names |= set(GENERAL_FILES) | {n for values in STAGE_FILES.values() for n in values}
    return digest({name: hashlib.sha256((root/name).read_bytes()).hexdigest()
                   for name in sorted(names)})


def general_code_identity():
    root = Path(__file__).resolve().parent
    return digest({name: hashlib.sha256((root/name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                   for name in GENERAL_FILES})


def pipeline_code_identity():
    return digest({"fits": model_code_identity(), **{
        name: stage_code_identity(name) for name in (*FINISH_STAGES, "archive", "public")}})


def load_context(first, second, manifest, records=None):
    from chart_source_exclusions import load_exclusions
    from cross_level_clearing import current_clearing_catalog
    from official_score_merge import merge_official_scores
    from sss_scoring import prepare_target_observations
    rows = records or []
    catalog = current_clearing_catalog({key: [r for r in rows if r.get("type") == mode]
        for mode, key in MODES}, second)
    policy = load_exclusions(catalog)
    merged = merge_official_scores(manifest, exclusions=policy)
    first, second = policy.filter_snapshot(first, "phoenix1"), policy.filter_snapshot(second, "phoenix2")
    prepared = prepare_target_observations(first, second, merged, catalog)
    return first, second, catalog, policy, merged, prepared


def cohort(prepared, goal, source, mode):
    from sss_scoring import payload_sha256
    minimum = 20 if source == "official" else 16
    boards = {cid: board for cid, board in prepared[source]["boards"].items()
        if prepared["catalog"][cid]["type"] == mode and prepared["catalog"][cid]["level"] >= minimum}
    sources = {name: {cid: board for cid, board in entries.items() if cid in boards}
        for name, entries in prepared[source]["sourceBoards"].items()}
    identity = {"boards": payload_sha256(boards), "sourceBoards": payload_sha256(sources),
        "target": 995000 if goal == "sssplus" else 990000, "samples": SAMPLES, "seed": SEED}
    return boards, sources, identity


def fit_path(identity):
    return PREFIX + "fits/" + digest({"inputs": identity, "code": model_code_identity()}) + ".json"


def validate_fit(fit, identity):
    policy = fit.get("policy", {})
    boot = fit.get("diagnostics", {}).get("bootstrap", {})
    if (policy.get("modelVersion") != "target-rasch-observed-best-v1"
            or policy.get("scoreTarget") != identity["target"]
            or policy.get("bootstrapSamples") != SAMPLES
            or policy.get("minimumPlayerCharts") != 4
            or policy.get("weighting") != "each-player-total-one-inverse-observed-chart-count-v1"
            or policy.get("regularizationGrid") != [.01, .05, .2, 1.]
            or not fit.get("diagnostics", {}).get("converged")
            or boot.get("completed") != SAMPLES or boot.get("failed") != 0):
        raise ValueError("The target fit does not implement the approved converged 200-bootstrap model.")


def training_predictor(target):
    def predict(boards, entries):
        from collections import defaultdict
        from official_player_scoring import fit_cross_level_player_comparisons
        fitted = fit_cross_level_player_comparisons(boards, bootstrap_samples=0)
        effects = {cid: r["scoringChartCost"] for cid, r in fitted["charts"].items()
            if r.get("scoringMainComponent") and r.get("scoringChartCost") is not None}
        histories = defaultdict(list)
        for cid, board in boards.items():
            if cid in effects:
                for player, score in board.items():
                    histories[player].append(score + effects[cid])
        abilities = {p: sum(values) / len(values) for p, values in histories.items() if len(values) >= 3}
        return {(cid, p): 1 / (1 + math.exp(-max(-700, min(700,
            (abilities[p] - effects[cid] - target) / 10000))))
            for cid, p in entries if cid in effects and p in abilities}
    return predict


def _public_copy(value):
    if isinstance(value, dict):
        return {key: _public_copy(item) for key, item in value.items() if not key.endswith("Baseline")}
    if isinstance(value, list):
        return [_public_copy(item) for item in value]
    return value


def public_tiers(full, *, private_path, input_identity, full_sha256=None):
    """Retain all active values/explanations; rollback history is a private artifact."""
    from source_aware_tiers import validate_source_aware_tiers
    if full.get("schemaVersion") == 57:
        from submitted_sssplus_calibration import validate_submitted_sssplus_calibration
        validate_source_aware_tiers(validate_submitted_sssplus_calibration(full))
    else:
        validate_source_aware_tiers(full)
    result = _public_copy(full)
    version = VERSION if full.get("schemaVersion") == 57 else PREVIOUS_VERSION
    result["summary"]["scriptVersion"] = version
    publication = {"version": 1, "method": version, "fullArtifactPath": private_path,
        "fullArtifactSha256": full_sha256 or digest(full), "inputs": input_identity, "historyStorage": "private",
        "scoreFetches": 0, "recommendationFits": 0}
    result["summary"]["method"]["productionTierPublication"] = publication
    publication["publicPayloadSha256"] = public_digest(result)
    validate_public_tiers(result)
    return result


def public_digest(payload):
    # A shallow copy avoids duplicating the full aggregate while omitting this hash.
    publication = dict(payload["summary"]["method"]["productionTierPublication"])
    publication.pop("publicPayloadSha256", None)
    method = {**payload["summary"]["method"], "productionTierPublication": publication}
    return digest({**payload, "summary": {**payload["summary"], "method": method}})


def write_private_audit(store, path, full):
    """Compress the reversible history without allocating its entire JSON twice."""
    import gzip
    import io
    import tempfile
    with tempfile.TemporaryFile() as staged:
        with gzip.GzipFile(fileobj=staged, mode="wb", mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8") as writer:
                json.dump(full, writer, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        staged.seek(0)
        hasher = hashlib.sha256()
        with gzip.GzipFile(fileobj=staged, mode="rb") as reader:
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                hasher.update(chunk)
        staged.seek(0)
        store.put_bytes(path, staged.read(), content_type="application/octet-stream")
        return hasher.hexdigest()


def validate_public_tiers(payload):
    from chart_source_exclusions import load_exclusions
    schema = payload.get("schemaVersion")
    if schema not in (56, 57):
        raise ValueError("The hosted source-aware tier schema must be 56 or 57.")
    method = payload["summary"]["method"]
    publication = method.get("productionTierPublication", {})
    if (publication.get("version") != 1 or publication.get("method") != (VERSION if schema == 57 else PREVIOUS_VERSION)
            or publication.get("publicPayloadSha256") != public_digest(payload)):
        raise ValueError("The public source-aware tier artifact failed its publication identity.")
    if schema == 57 and method.get("submittedSssplusCalibration", {}).get("localOnly") is not False:
        raise ValueError("The hosted submitted SSS+ calibration must declare production use.")
    rows = payload["singles"] + payload["doubles"]
    policy = load_exclusions([{**r, "id": r["chartId"]} for r in rows])
    if method["sourceAwareTiers"]["sourcePolicy"]["effectivePolicySha256"] != policy.semantic_hash():
        raise ValueError("The published tiers use a different source exclusion policy.")
    for row in rows:
        clearing = row["tierMetrics"]["clearing"]["estimatedDifficulty"]
        for goal in ("general", "sss", "sssplus"):
            scoring = row["scoringVariants"][goal]["estimatedDifficulty"]
            actual = row["pumbilityVariants"][goal]["estimatedDifficulty"]
            expected = None if scoring is None or clearing is None else (scoring + clearing) / 2
            if actual != expected or any(v is not None and not math.isfinite(v) for v in (scoring, clearing, actual)):
                raise ValueError("Public Pumbility must average its finite final components.")
        if row["estimatedDifficulty"] != row["scoringVariants"]["general"]["estimatedDifficulty"]:
            raise ValueError("General Scoring compatibility fields differ.")


def _finish_stage(stage, result, context, fits, identities, generated_at_utc):
    from sss_scoring import apply_sss_variants, payload_sha256
    from refined_tiers import apply_refined_tiers
    from sss_folder_centering import apply_folder_centering
    from sssplus_tiers import apply_sssplus_tiers
    from sssplus_median_calibration import apply_median_calibration
    from sssplus_rare_continuous import build_rare_adjustments
    from sssplus_spread import apply_sssplus_spread
    from combined_official_clearing import apply_combined_official_clearing
    from official_high_level_clearing import P1_OBSERVED_CAP_POLICY
    from source_aware_tiers import apply_source_aware_tiers
    first, second, catalog, policy, merged, prepared = context
    hashes = merged.get("_releaseSourceHashes") or {
        "phoenix1": payload_sha256(first), "phoenix2": payload_sha256(second),
        "currentCatalog": payload_sha256(catalog), "officialManifest": merged["manifestSha256"]}
    if stage == "centered-sss":
        result = apply_sss_variants(result, prepared, fits["sss"], source_hashes=hashes, bootstrap_samples=SAMPLES, seed=SEED)
        result, _ = apply_refined_tiers(result, prepared, fits["sss"], merged["_releasePhoenix2Capture"])
        return apply_folder_centering(result)
    if stage == "sssplus":
        return apply_sssplus_tiers(result, prepared, fits["sssplus"], source_hashes=hashes)[0]
    if stage == "median":
        return apply_median_calibration(result, policy_version=3)
    if stage == "spread":
        ids = {mode: {str(r["chartId"]) for r in result[key] if r["level"] >= 20} for mode, key in MODES}
        boards = {mode: {cid: board for cid, board in merged["boards"].items() if cid in ids[mode]} for mode, _ in MODES}
        adjustments, rare = build_rare_adjustments(result, boards, fits["sssplus"]["official"])
        return apply_sssplus_spread(result, rare_adjustments=adjustments, rare_policy=rare)
    if stage == "combined-clearing":
        return apply_combined_official_clearing(result, merged, bootstrap_samples=1000, cap_policy=P1_OBSERVED_CAP_POLICY)[0]
    if stage == "source-aware":
        exclusions = {cid: {"rules": meta["sourceExclusions"], "capturedSourceStates": meta["capturedSourceStates"],
            "effectiveSourceStates": meta["sourceStates"]} for cid, meta in merged["charts"].items() if meta.get("sourceExclusions")}
        provenance = {**policy.public(), "manifestSha256": merged["manifestSha256"],
            "effectiveInputSha256": merged["effectiveInputSha256"], "sourceDates": merged["sourceDates"],
            "excludedCharts": exclusions, "targetFits": identities, "correctedSourceHashes": hashes}
        result = apply_source_aware_tiers(result, result, provenance)
        # Pin before schema57 records its schema56 predecessor hash. Mutating the
        # timestamp after that hash made timestamped background resumes invalid.
        result["generatedAtUtc"] = result["summary"]["generatedAtUtc"] = generated_at_utc
        return result
    if stage == "submitted-calibration":
        from submitted_sssplus_calibration import apply_submitted_sssplus_calibration
        submitted_identity = next(item["identity"] for item in identities
            if (item["goal"], item["source"], item["mode"]) == ("sssplus", "submitted", "Double"))
        return apply_submitted_sssplus_calibration(result, fits["sssplus"]["submitted"]["Double"],
            fit_identity=submitted_identity, local_only=False)
    raise ValueError("Unknown tier publication phase.")


def finish_generation(general, context, fits, identities, *, generated_at_utc=None):
    """Uninterrupted reference path, retained for exact parity verification."""
    created = generated_at_utc or datetime.now(timezone.utc).isoformat()
    result = general
    for stage in FINISH_STAGES:
        result = _finish_stage(stage, result, context, fits, identities, created)
    return result


def build_hosted_tiers(records, metadata, first, second, *, store, arguments, generated_at_utc=None,
                       on_stage_start=None, input_reference_sha256=None):
    """Advance one validated phase; never capture scores or fit recommendations."""
    from scoring_percentile import build_production_tier_payload
    from sss_scoring_model import fit_target_model
    from official_scoring_sources import canonical_scoring_manifest_sha256
    from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
    from tier_diagnostics import event, phase
    manifest = arguments["official_scoring_manifest"]
    context = load_context(first, second, manifest, records)
    p1, p2, catalog, policy, merged, prepared = context
    merged["_releasePhoenix2Capture"] = manifest["_captures"]["Phoenix2"]
    identity = {"version": VERSION, "phoenix1": digest(first), "phoenix2": digest(second),
        "catalog": digest(catalog), "officialManifest": canonical_scoring_manifest_sha256(manifest),
        "sourcePolicy": policy.semantic_hash(), "modelCode": model_code_identity(),
        "generationTimestamp": generated_at_utc, "pipelineCode": pipeline_code_identity()}
    generation = digest(identity)
    generation_prefix = PREFIX + "generations/" + generation + "/"
    public_path = generation_prefix + "public.json"
    cached = store.get_json(public_path)
    if cached is not None:
        validate_public_tiers(cached)
        return cached
    state_path = generation_prefix + "state.json"
    state = store.get_json(state_path)
    if state is None:
        state = {"version": 1, "generation": generation, "identity": identity, "stages": [],
                 "createdAtUtc": generated_at_utc or datetime.now(timezone.utc).isoformat()}
    if state.get("identity") != identity or state.get("version") != 1:
        raise ValueError("The tier generation state has incompatible inputs or code.")
    if input_reference_sha256 is not None:
        if state.get("pinnedInputsSha256") not in (None, input_reference_sha256):
            raise ValueError("The tier generation is bound to different pinned inputs.")
        state["pinnedInputsSha256"] = input_reference_sha256
    created = state["createdAtUtc"]
    general_identity = digest({key: value for key, value in identity.items()
        if key not in {"version", "generationTimestamp", "pipelineCode"}} | {
            "generalCode": general_code_identity(), "records": digest(records), "metadata": digest(metadata)})
    general_reference = state.get("general") or store.get_json(PREFIX + "general/" + general_identity + ".json")
    if general_reference is None:
        if on_stage_start: on_stage_start("general")
        general = None
        if general_code_identity() == LEGACY_GENERAL_CODE_IDENTITY:
            # The old tier-only path used a None timestamp. Pinning a new job time
            # must not cause the exact same approved General fit to be rebuilt.
            legacy = {key: value for key, value in identity.items() if key != "pipelineCode"}
            for version in (VERSION, PREVIOUS_VERSION):
                old = digest({**legacy, "version": version, "generationTimestamp": None})
                general = store.get_json(PREFIX + "generations/" + old + "/general.json")
                if general is not None:
                    break
        if general is None:
            with phase("general", generation=generation):
                general = build_production_tier_payload(records, metadata, p1, p2,
                    generated_at_utc=created, source_exclusions=policy, **arguments)
        general_reference = write_checkpoint(store, general, stage="general", identity=general_identity)
        store.put_json(PREFIX + "general/" + general_identity + ".json", general_reference)
        del general
    state["general"] = general_reference
    store.put_json(state_path, state)
    fits = {goal: {source: {} for source in ("official", "submitted")} for goal in ("sss", "sssplus")}
    identities, completed = [], 0
    for goal in fits:
        for source in fits[goal]:
            for mode, _ in MODES:
                boards, sources, fit_identity = cohort(prepared, goal, source, mode)
                path = fit_path(fit_identity)
                cached_fit = store.get_json(path)
                if cached_fit is None:
                    if on_stage_start: on_stage_start(f"target-fit-{goal}-{source}-{mode.lower()}")
                    fit = fit_target_model(boards, score_target=fit_identity["target"], bootstrap_samples=SAMPLES,
                        seed=SEED, source_boards=sources,
                        general_predictor=training_predictor(fit_identity["target"]) if source == "official" else None)
                    if source == "official":
                        fit["diagnostics"]["generalBenchmarkPolicy"] = {"method": "training-only-General-additive-score-cost",
                            "logisticScoreScale": 10000, "scaleSelection": "fixed-before-holdout", "heldoutRecordsUsedInFit": False}
                    else:
                        fit["diagnostics"]["validation"]["generalTargetBenchmarkReason"] = "Submitted General uses score profiles rather than additive score costs; no equivalent training-only target predictor."
                    validate_fit(fit, fit_identity)
                    store.put_json(path, {"identity": fit_identity, "fit": fit})
                    raise TierAnalysisPending(generation[:16] + "-fit-" + str(completed + 1), completed + 1,
                        stage="target-fit", generation=generation)
                if cached_fit["identity"] != fit_identity:
                    raise ValueError("The private target fit has stale inputs.")
                fit = cached_fit["fit"]
                validate_fit(fit, fit_identity)
                fits[goal][source][mode] = fit
                identities.append({"goal": goal, "source": source, "mode": mode, "identity": fit_identity, "reusedOriginal": False})
                completed += 1
    if "phaseInputs" not in state:
        if on_stage_start: on_stage_start("prepared-inputs")
        with phase("prepared-inputs", generation=generation):
            state["phaseInputs"] = _pin_phase_inputs(store, generation, context)
            state["fitReferences"] = [{**item, "path": fit_path(item["identity"]),
                "fitSha256": digest(fits[item["goal"]][item["source"]][item["mode"]])} for item in identities]
            store.put_json(state_path, state)
        raise TierAnalysisPending(generation[:16]+"-9", 8, stage="prepared-inputs",
                                  generation=generation, progress_ordinal=9)
    return _advance_finish(store, state, on_stage_start=on_stage_start)


def _advance_finish(store, state, *, on_stage_start=None):
    from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
    from tier_diagnostics import event, phase
    generation, identity, created = state["generation"], state["identity"], state["createdAtUtc"]
    state_path = PREFIX+"generations/"+generation+"/state.json"
    public_path = PREFIX+"generations/"+generation+"/public.json"
    cached = store.get_json(public_path)
    if cached is not None:
        validate_public_tiers(cached)
        return cached
    general_reference = state["general"]
    general_identity = general_reference["identity"]
    reference = general_reference
    if len(state["stages"]) > len(FINISH_STAGES):
        raise ValueError("The tier generation contains an unknown completed phase.")
    for index, saved in enumerate(state["stages"]):
        stage = FINISH_STAGES[index]
        expected = _stage_identity(generation, stage, reference["payloadSha256"], created)
        if (saved.get("stage") != stage or saved.get("identity") != expected
                or saved.get("predecessorSha256") != reference["payloadSha256"]
                or saved.get("schemaVersion") != STAGE_SCHEMAS[stage]):
            raise ValueError("The tier generation checkpoint chain is stale or corrupt.")
        reference = saved
    completed_stages = len(state["stages"])
    if completed_stages < len(FINISH_STAGES):
        stage = FINISH_STAGES[completed_stages]
        if on_stage_start: on_stage_start(stage)
        state["activeStage"] = stage
        store.put_json(state_path, state)
        with phase(stage, generation=generation):
            context, fits, identities = _load_phase_inputs(store, state, stage)
            predecessor = read_checkpoint(store, reference,
                stage="general" if completed_stages == 0 else FINISH_STAGES[completed_stages-1],
                identity=general_identity if completed_stages == 0 else reference["identity"])
            result = _finish_stage(stage, predecessor, context, fits, identities, created)
            if result.get("schemaVersion") != STAGE_SCHEMAS[stage]:
                raise ValueError("The tier transition produced an incompatible schema.")
            checkpoint = write_checkpoint(store, result, stage=stage,
                identity=_stage_identity(generation, stage, reference["payloadSha256"], created),
                predecessor=reference["payloadSha256"])
        state["stages"].append(checkpoint)
        state.pop("activeStage", None)
        store.put_json(state_path, state)
        event("tier_checkpoint_saved", generation=generation, stage=stage,
              artifact_bytes=checkpoint["compressedBytes"], output_hash=checkpoint["payloadSha256"])
        ordinal = 9 + completed_stages + 1
        raise TierAnalysisPending(generation[:16]+"-"+str(ordinal), 8, stage=stage,
                                  generation=generation, progress_ordinal=ordinal)
    if "archive" not in state:
        if on_stage_start: on_stage_start("archive")
        # The immutable, verified final-stage gzip already IS the complete audit.
        # Reuse its canonical content digest rather than serialize 400MB again.
        state["archive"] = reference
        store.put_json(state_path, state)
        ordinal = 9 + len(FINISH_STAGES) + 1
        raise TierAnalysisPending(generation[:16]+"-"+str(ordinal), 8, stage="archive",
                                  generation=generation, progress_ordinal=ordinal)
    state["activeStage"] = "public"
    if on_stage_start: on_stage_start("public")
    store.put_json(state_path, state)
    with phase("public", generation=generation):
        full = read_checkpoint(store, reference, stage="submitted-calibration", identity=reference["identity"])
        public = public_tiers(full, private_path=reference["path"], input_identity=identity,
                              full_sha256=reference["payloadSha256"])
        from tier_public import prepare_tier_publication, validate_tier_publication
        compact, details = prepare_tier_publication(public, generation)
        validate_tier_publication(public, compact)
        for path, artifact in details.items():
            store.put_json(path, artifact)
        validate_public_tiers(compact)
        store.put_json(public_path, compact)
    state["ready"] = {"path": public_path, "payloadSha256": digest(compact),
                      "archivePath": reference["path"], "archiveSha256": reference["payloadSha256"]}
    state.pop("activeStage", None)
    store.put_json(state_path, state)
    return compact


def _stage_identity(generation, stage, predecessor, created):
    return digest({"generation": generation, "stage": stage, "code": stage_code_identity(stage),
                   "predecessorSha256": predecessor, "createdAtUtc": created})


def _pin_phase_inputs(store, generation, context):
    from sss_scoring import payload_sha256
    from tier_pipeline_checkpoints import write_checkpoint
    first, second, catalog, policy, merged, prepared = context
    hashes = {"phoenix1": payload_sha256(first), "phoenix2": payload_sha256(second),
              "currentCatalog": payload_sha256(catalog), "officialManifest": merged["manifestSha256"]}
    official = {**merged, "_releaseSourceHashes": hashes}
    small = {key: value for key, value in official.items() if key in
        {"manifestSha256", "effectiveInputSha256", "sourceDates", "_releaseSourceHashes"}}
    small["charts"] = {cid: chart for cid, chart in merged.get("charts", {}).items() if chart.get("sourceExclusions")}
    payloads = {"model": {"prepared": prepared}, "official": {"merged": official},
                "provenance": {"policy": policy.public(), "merged": small}}
    return {name: write_checkpoint(store, payload, stage="phase-inputs-"+name,
                identity=digest({"generation": generation, "kind": name}))
            for name, payload in payloads.items()}


def _load_phase_inputs(store, state, stage):
    """Hydrate only the captured observations and fits this transition consumes."""
    from tier_pipeline_checkpoints import read_checkpoint
    def load(name):
        return read_checkpoint(store, state["phaseInputs"][name], stage="phase-inputs-"+name,
            identity=digest({"generation": state["generation"], "kind": name}))
    provenance = load("provenance")
    merged, prepared = provenance["merged"], {}
    if stage in ("centered-sss", "sssplus"):
        prepared = load("model")["prepared"]
    if stage in ("centered-sss", "spread", "combined-clearing"):
        merged = load("official")["merged"]
    fits, identities = {}, []
    for item in state["fitReferences"]:
        identities.append({key: item[key] for key in ("goal", "source", "mode", "identity", "reusedOriginal")})
        needed = ((stage == "centered-sss" and item["goal"] == "sss")
            or (stage == "sssplus" and item["goal"] == "sssplus")
            or (stage == "spread" and (item["goal"], item["source"]) == ("sssplus", "official"))
            or (stage == "submitted-calibration" and (item["goal"], item["source"], item["mode"]) == ("sssplus", "submitted", "Double")))
        if not needed:
            continue
        cached = store.get_json(item["path"])
        if (cached is None or cached.get("identity") != item["identity"]
                or digest(cached.get("fit")) != item["fitSha256"]):
            raise ValueError("A captured target fit changed during tier publication.")
        validate_fit(cached["fit"], item["identity"])
        fits.setdefault(item["goal"], {}).setdefault(item["source"], {})[item["mode"]] = cached["fit"]
    policy = SimpleNamespace(public=lambda: provenance["policy"])
    return (None, None, None, policy, merged, prepared), fits, identities


def pin_tier_publication_inputs(store, *, first, second, manifest, records, metadata, generated_at_utc):
    """Pin the captured inputs once; the durable job stores only this small ref."""
    from tier_pipeline_checkpoints import write_checkpoint
    payload = {"first": first, "second": second, "manifest": manifest, "records": records,
               "metadata": metadata, "generatedAtUtc": generated_at_utc, "pipelineCode": pipeline_code_identity()}
    return write_checkpoint(store, payload, stage="pinned-inputs", identity=digest(payload))


def advance_tier_publication(store, state, *, on_stage_start=None):
    """Worker adapter: one fit/finish phase, a compact result, and no promotion."""
    from tier_pipeline_checkpoints import read_checkpoint
    current_code = pipeline_code_identity()
    created = state["generatedAtUtc"]
    if state.get("generation") is not None:
        generation = state["generation"]
        captured = store.get_json(PREFIX+"generations/"+generation+"/state.json")
        if (captured is None or captured.get("generation") != generation or digest(captured["identity"]) != generation
                or captured["identity"].get("pipelineCode") != current_code
                or captured.get("createdAtUtc") != created
                or captured.get("pinnedInputsSha256") != state["inputs"]["payloadSha256"]):
            raise ValueError("The pinned tier generation has incompatible code, time or inputs.")
        if captured.get("phaseInputs"):
            try:
                candidate = _advance_finish(store, captured, on_stage_start=on_stage_start)
            except TierAnalysisPending as pending:
                return _pending_worker_result(state, pending)
            return _ready_worker_result(state, candidate)
    if on_stage_start: on_stage_start("loading-inputs")
    inputs = read_checkpoint(store, state["inputs"], stage="pinned-inputs", identity=state["inputs"]["identity"])
    created = state["generatedAtUtc"]
    if inputs.get("generatedAtUtc") != created:
        raise ValueError("The tier job creation time differs from its pinned inputs.")
    if inputs.get("pipelineCode") != current_code:
        raise ValueError("The pinned tier job uses different pipeline code; start a compatible generation.")
    try:
        candidate = build_hosted_tiers(inputs["records"], inputs["metadata"], inputs["first"], inputs["second"],
            store=store, generated_at_utc=created, on_stage_start=on_stage_start,
            input_reference_sha256=state["inputs"]["payloadSha256"],
            arguments={"official_scoring_manifest": inputs["manifest"],
                "cross_level_clearing": True, "midpoint_compression": True,
                "hybrid_lower_tiers": True, "average_pumbility": True})
    except TierAnalysisPending as pending:
        return _pending_worker_result(state, pending)
    return _ready_worker_result(state, candidate)


def _pending_worker_result(state, pending):
    updated = {**state, "generation": pending.generation, "sequence": pending.sequence,
               "progressOrdinal": pending.progress_ordinal, "stage": pending.stage}
    return {"done": False, "stage": pending.stage, "sequence": pending.sequence,
            "progressOrdinal": pending.progress_ordinal, "totalStages": pending.total_stages,
            "completedTargetFits": pending.completed, "state": updated}


def _ready_worker_result(state, candidate):
    publication = candidate["summary"]["method"]["productionTierPublication"]
    generation = digest(publication["inputs"])
    ready = {"done": True, "stage": "ready", "generation": generation,
             "progressOrdinal": 9+len(FINISH_STAGES)+2, "totalStages": 9+len(FINISH_STAGES)+2,
             "candidatePath": PREFIX+"generations/"+generation+"/public.json",
             "candidateSha256": digest(candidate), "archivePath": publication["fullArtifactPath"],
             "archiveSha256": publication["fullArtifactSha256"]}
    return {**ready, "state": {**state, **{key: ready[key] for key in
        ("generation", "stage", "progressOrdinal", "candidatePath", "candidateSha256", "archivePath", "archiveSha256")}}}
