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
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

VERSION = "6.21.0-submitted-sssplus"
PREVIOUS_VERSION = "6.20.0-source-aware-sssplus"
GOAL_VERSION = "6.22.0-scoring-goals"
TAIL_VERSION = "6.23.0-sss-bounded-tails"
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
    from numeric_recipe import target_kernel_recipe, benchmark_recipe
    return digest({"target": target_kernel_recipe(), "benchmark": benchmark_recipe()})


FINISH_STAGES = ("centered-sss", "sssplus", "median", "spread", "combined-clearing",
                 "source-aware", "submitted-calibration")
STAGE_SCHEMAS = dict(zip(FINISH_STAGES, (51, 52, 53, 54, 55, 56, 57)))
GENERAL_FILES = ("scoring_percentile.py", "official_cross_level_scoring.py", "official_player_scoring.py",
                 "official_scoring_calibration.py", "cross_level_clearing.py", "cross_level_clearing_calibration.py",
                 "hybrid_tiers.py", "pumbility_average.py", "tier_postprocessing.py", "tier_difficulty.py",
                 "piu_recommendations.py", "piu_misgrade_analyzer.py", "player_skill_ratings.py",
                 "phoenix1_score_overrides.py", "official_score_merge.py", "official_scoring_sources.py",
                 "official_cross_level_calibration.py", "chart_source_exclusions.py", "pumbility_contract.py",
                 "score_profile_cache.py", "tier_computation_cache.py")
STAGE_FILES = {
    "centered-sss": ("sss_scoring.py", "refined_tiers.py", "sss_scoring_refinement.py", "sss_folder_centering.py"),
    "sssplus": ("sssplus_tiers.py", "sss_scoring_refinement.py", "target_tail_policy.py"),
    "median": ("sssplus_median_calibration.py", "sssplus_final_curve.py"),
    "spread": ("sssplus_spread.py", "sssplus_rare_continuous.py", "target_tail_policy.py"),
    "combined-clearing": ("combined_official_clearing.py", "official_high_level_clearing.py"),
    "source-aware": ("source_aware_tiers.py", "final_folder_width.py"),
    "submitted-calibration": ("submitted_sssplus_calibration.py",),
    "archive": ("tier_pipeline_checkpoints.py",),
    "public": ("production_tiers.py", "tier_public.py", "config/tier-public-fields.json", "sss_tail_tiers.py"),
}
# General's numeric sources at the previously deployed, approved generation.
# This permits a one-time legacy cache adoption without trusting unrelated old
# artifacts after a future General methodology change.
LEGACY_GENERAL_CODE_IDENTITY = "cba011919588bcfff6db2a8db79878db23359896d6ed4020aa27750134d9577b"


def stage_code_identity(stage):
    root = Path(__file__).resolve().parent
    names = set(STAGE_FILES.get(stage, GENERAL_FILES)) | {
        "production_tiers.py", "canonical_json.py", "tier_computation_cache.py",
        "tier_pipeline_checkpoints.py"}
    # Include every numeric dependency even when a helper is shared across stages.
    names |= set(GENERAL_FILES) | {n for values in STAGE_FILES.values() for n in values}
    return digest({name: hashlib.sha256((root/name).read_bytes()).hexdigest()
                   for name in sorted(names)})


def general_code_identity():
    from numeric_recipe import general_producer_recipe
    return digest(general_producer_recipe())


def _legacy_general_code_identity():
    root = Path(__file__).resolve().parent
    return digest({name: hashlib.sha256((root/name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                   for name in GENERAL_FILES})


def pipeline_code_identity():
    return digest({"fits": model_code_identity(), **{
        name: stage_code_identity(name) for name in (*FINISH_STAGES, "archive", "public")}})


@lru_cache(maxsize=1)
def numerical_pipeline_recipe():
    """Completed-result identity; active recovery still pins pipeline_code_identity."""
    from numeric_recipe import general_producer_recipe, finish_producer_recipe, target_kernel_recipe, producer_dependencies
    from scoring_targets import SSS, SSSPLUS
    predecessor = digest(general_producer_recipe())
    stages = {}
    for stage in FINISH_STAGES:
        predecessor = digest({"predecessor": predecessor, "producer": finish_producer_recipe(stage)})
        stages[stage] = predecessor
    return {"version": 1, "general": general_producer_recipe(), "stages": stages,
            "targets": [SSS.public(), SSSPLUS.public()], "kernel": target_kernel_recipe(),
            "preparation": digest(producer_dependencies({
                "sss_scoring.py": {"prepare_target_observations"},
                "production_tiers.py": {"_cohort_observations", "_general_target_predictions"}})),
            "samples": SAMPLES, "seed": SEED}


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
    boards, sources = _cohort_observations(prepared, source, mode)
    identity = {"boards": payload_sha256(boards), "sourceBoards": payload_sha256(sources),
        "target": _scoring_target(goal).score, "samples": SAMPLES, "seed": SEED}
    return boards, sources, identity


def _cohort_observations(prepared, source, mode):
    minimum = 20 if source == "official" else 16
    boards = {cid: board for cid, board in prepared[source]["boards"].items()
        if prepared["catalog"][cid]["type"] == mode and prepared["catalog"][cid]["level"] >= minimum}
    sources = {name: {cid: board for cid, board in entries.items() if cid in boards}
        for name, entries in prepared[source]["sourceBoards"].items()}
    return boards, sources


def fit_path(identity):
    return PREFIX + "fits/" + digest({"inputs": identity, "code": model_code_identity()}) + ".json"


def _scoring_target(goal):
    from scoring_targets import get_scoring_target
    return get_scoring_target(goal)


def _cached_fit(store, boards, identity):
    """Narrow-key lookup with one explicit, reviewed legacy producer migration."""
    from numeric_recipe import legacy_outer_identities
    from tier_diagnostics import event
    path = fit_path(identity)
    cached = store.get_json(path)
    if cached is not None:
        return cached
    candidates = legacy_outer_identities()
    if not candidates:
        event("target_fit_cache_migration", stage="legacy-outer", cache_hit=False,
              outcome="producer-incompatible")
        return None
    outcome = "not-found"
    for legacy in candidates:
        old_path = PREFIX + "fits/" + digest({"inputs": identity, "code": legacy}) + ".json"
        old = store.get_json(old_path)
        if old is None:
            continue
        if old.get("identity") != identity:
            outcome = "input-mismatch"
            continue
        validate_fit(old["fit"], identity)
        if old["fit"].get("diagnostics", {}).get("validation", {}).get("seed") != identity["seed"]:
            outcome = "seed-mismatch"
            continue
        from target_fit_cache import adopt_compatible_components
        component = adopt_compatible_components(store, boards, old["fit"], score_target=identity["target"],
                                               bootstrap_samples=identity["samples"], seed=identity["seed"])
        store.put_json(path, old)
        event("target_fit_cache_migration", stage="legacy-outer", cache_hit=True, outcome="adopted")
        event("target_fit_cache_migration", stage="legacy-component", cache_hit=component,
              outcome="adopted" if component else "not-adopted")
        return old
    event("target_fit_cache_migration", stage="legacy-outer", cache_hit=False, outcome=outcome)
    return None


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


def training_predictor(target, store=None):
    def predict(boards, entries):
        from official_player_scoring import fit_cross_level_player_comparisons
        from numeric_recipe import benchmark_recipe
        from pumbility_store import PumbilityArtifactIntegrityError
        identity = {"recipe": benchmark_recipe(), "boards": digest(boards)}
        path = PREFIX + "training-general/" + digest(identity) + ".json"
        try:
            saved = store.get_json(path) if store is not None else None
        except (PumbilityArtifactIntegrityError, json.JSONDecodeError):
            saved = None
        fitted = (saved["fit"] if isinstance(saved, dict) and saved.get("identity") == identity
                  and saved.get("sha256") == digest(saved.get("fit")) else None)
        if fitted is None:
            fitted = fit_cross_level_player_comparisons(boards, bootstrap_samples=0)
            if store is not None:
                store.put_json(path, {"identity": identity, "fit": fitted, "sha256": digest(fitted)})
        return _general_target_predictions(boards, entries, fitted, target)
    return predict


def _general_target_predictions(boards, entries, fitted, target):
    from collections import defaultdict
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


def _public_copy(value):
    if isinstance(value, dict):
        return {key: _public_copy(item) for key, item in value.items() if not key.endswith("Baseline")}
    if isinstance(value, list):
        return [_public_copy(item) for item in value]
    return value


def public_tiers(full, *, private_path, input_identity, full_sha256=None, component_provenance=None,
                 computation_identity=None):
    """Retain all active values/explanations; rollback history is a private artifact."""
    from source_aware_tiers import validate_source_aware_tiers
    if full.get("schemaVersion") == 59:
        from sss_tail_tiers import validate_sss_tail_tiers
        validate_sss_tail_tiers(full)
    elif full.get("schemaVersion") == 58:
        from scoring_goal_tiers import validate_goal_tiers
        validate_goal_tiers(full)
    elif full.get("schemaVersion") == 57:
        from submitted_sssplus_calibration import validate_submitted_sssplus_calibration
        validate_source_aware_tiers(validate_submitted_sssplus_calibration(full))
    else:
        validate_source_aware_tiers(full)
    result = _public_copy(full)
    version = (TAIL_VERSION if full.get("schemaVersion") == 59 else GOAL_VERSION
               if full.get("schemaVersion") == 58 else VERSION if full.get("schemaVersion") == 57 else PREVIOUS_VERSION)
    result["summary"]["scriptVersion"] = version
    publication = {"version": 1, "method": version, "fullArtifactPath": private_path,
        "fullArtifactSha256": full_sha256 or digest(full), "inputs": input_identity, "historyStorage": "private",
        "scoreFetches": 0, "recommendationFits": 0}
    if component_provenance:
        publication["componentComputations"] = component_provenance
    if computation_identity is not None:
        publication.update(computedAtUtc=full.get("generatedAtUtc"), computedInputs=input_identity,
                           computedGeneration=digest(input_identity),
                           computationIdentity=digest(computation_identity), reusedComputation=False)
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
    if schema not in (56, 57, 58, 59):
        raise ValueError("The hosted source-aware tier schema must be 56, 57, 58 or 59.")
    method = payload["summary"]["method"]
    publication = method.get("productionTierPublication", {})
    expected_version = TAIL_VERSION if schema == 59 else GOAL_VERSION if schema == 58 else VERSION if schema == 57 else PREVIOUS_VERSION
    if (publication.get("version") != 1 or publication.get("method") != expected_version
            or publication.get("publicPayloadSha256") != public_digest(payload)):
        raise ValueError("The public source-aware tier artifact failed its publication identity.")
    if schema in (57, 58, 59) and method.get("submittedSssplusCalibration", {}).get("localOnly") is not False:
        raise ValueError("The hosted submitted SSS+ calibration must declare production use.")
    rows = payload["singles"] + payload["doubles"]
    policy = load_exclusions([{**r, "id": r["chartId"]} for r in rows])
    if method["sourceAwareTiers"]["sourcePolicy"]["effectivePolicySha256"] != policy.semantic_hash():
        raise ValueError("The published tiers use a different source exclusion policy.")
    goals = ("general", "sss", "sssplus")
    expanded = schema == 58 or schema == 59 and method.get("sssTailPolicy", {}).get("baseSchemaVersion") == 58
    if expanded:
        from scoring_targets import SCORING_GOALS, BACKFILL_GOALS, scoring_goal_policies
        goals = method.get("availableScoringGoals", [])
        if (method.get("scoringGoalPolicies") != scoring_goal_policies()
                or goals != [g for g in SCORING_GOALS if g in goals]
                or not set(("general", "sss", "sssplus")) <= set(goals)
                or not set(goals).intersection(BACKFILL_GOALS)):
            raise ValueError("The public scoring-goal policies or availability differ.")
    for row in rows:
        clearing = row["tierMetrics"]["clearing"]["estimatedDifficulty"]
        for goal in goals:
            if expanded and goal not in ("general", "sss", "sssplus"):
                metric = row["scoringVariants"][goal]
                target = _scoring_target(goal)
                if (metric.get("scoringTarget") != target.score or metric.get("scoringSource") != ("official" if row["level"] >= 20 else "submitted")
                        or metric.get("nContributors") != metric.get("scoringTargetSuccesses", -1) + metric.get("scoringBelowTarget", -1)):
                    raise ValueError("The public scoring goal has incorrect target or support counts.")
            scoring = row["scoringVariants"][goal]["estimatedDifficulty"]
            actual = row["pumbilityVariants"][goal]["estimatedDifficulty"]
            expected = None if scoring is None or clearing is None else (scoring + clearing) / 2
            if actual != expected or any(v is not None and not math.isfinite(v) for v in (scoring, clearing, actual)):
                raise ValueError("Public Pumbility must average its finite final components.")
        if row["estimatedDifficulty"] != row["scoringVariants"]["general"]["estimatedDifficulty"]:
            raise ValueError("General Scoring compatibility fields differ.")
    if schema == 59:
        _validate_public_sss_tail(payload)


def _validate_public_sss_tail(payload):
    """Replay bounded public points/intervals without exposing rollback trees."""
    from sss_tail_tiers import METHOD
    from target_tail_policy import TAIL_PARAMETERS, tail_value
    policy = payload["summary"]["method"].get("sssTailPolicy", {})
    tail = policy.get("tailPolicy", {})
    selected = tail.get("selectedChartIds")
    if (policy.get("version") != 1 or policy.get("method") != METHOD
            or policy.get("baseSchemaVersion") not in (57, 58)
            or not isinstance(policy.get("basePayloadSha256"), str)
            or len(policy["basePayloadSha256"]) != 64
            or any(c not in "0123456789abcdef" for c in policy["basePayloadSha256"])
            or any(tail.get(key) != value for key, value in TAIL_PARAMETERS.items())
            or tail.get("targetKey") != "sss" or not isinstance(selected, list)
            or any(not isinstance(cid, str) for cid in selected)
            or selected != sorted(set(selected)) or len(selected) > TAIL_PARAMETERS["exceptionLimit"]):
        raise ValueError("The public SSS tail policy differs.")
    rows = payload["singles"] + payload["doubles"]
    if not set(selected) <= {str(row["chartId"]) for row in rows}:
        raise ValueError("The public SSS tail selection contains an unknown chart.")
    pre, final, changed = [], [], 0
    for row in rows:
        metric, level = row["scoringVariants"]["sss"], row["level"]
        evidence = metric.get("scoringTailPolicy", {})
        exceptional = str(row["chartId"]) in selected
        for field, source in (("estimatedDifficulty", "preTailDifficulty"),
                              ("difficultyCi95Low", "preTailDifficultyCi95Low"),
                              ("difficultyCi95High", "preTailDifficultyCi95High")):
            value = evidence.get(source)
            if (source not in evidence or value is not None and
                    (type(value) not in (int, float) or not math.isfinite(value))
                    or metric.get(field) != tail_value(value, level, exceptional=exceptional)):
                raise ValueError("The public SSS tail estimate or interval differs from its input.")
        before, value = evidence["preTailDifficulty"], metric["estimatedDifficulty"]
        if exceptional and (before is None or abs(before-level-.5) < 2):
            raise ValueError("The public SSS exception is inside the ordinary boundary.")
        if value is not None:
            pre.append(before-level-.5)
            final.append(value-level-.5)
            changed += before != value
    counts = {"ratedCharts": len(final), "changedCharts": changed,
        "preTailAbsAbove1_5": sum(abs(d) > 1.5 for d in pre),
        "preTailAbsAtLeast2": sum(abs(d) >= 2 for d in pre),
        "finalAbsAbove1_5": sum(abs(d) > 1.5 for d in final),
        "finalAbsAtLeast2": sum(abs(d) >= 2 for d in final),
        "finalAbsAbove2_5": sum(abs(d) > 2.5 for d in final),
        "displayedAbsAtLeast2": sum(abs(math.trunc(row["scoringVariants"]["sss"]["estimatedDifficulty"]*10+1e-9)/10-row["level"]-.5) >= 2
            for row in rows if row["scoringVariants"]["sss"]["estimatedDifficulty"] is not None),
        "preTailMinDelta": min(pre, default=None), "preTailMaxDelta": max(pre, default=None),
        "finalMinDelta": min(final, default=None), "finalMaxDelta": max(final, default=None)}
    if tail.get("counts") != counts:
        raise ValueError("The public SSS tail population or counts differ.")


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
                       on_stage_start=None, input_reference_sha256=None, on_progress=None):
    """Advance one validated phase; never capture scores or fit recommendations."""
    from scoring_percentile import build_production_tier_payload
    from sss_scoring_model import fit_target_model
    from official_scoring_sources import canonical_scoring_manifest_sha256
    from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
    from tier_computation_cache import input_identity, read_index, write_index, TierComputationCacheError
    from tier_pipeline_checkpoints import CheckpointValidationError
    from pumbility_store import PumbilityArtifactIntegrityError
    from tier_diagnostics import event, phase
    manifest = arguments["official_scoring_manifest"]
    context = load_context(first, second, manifest, records)
    p1, p2, catalog, policy, merged, prepared = context
    merged["_releasePhoenix2Capture"] = manifest["_captures"]["Phoenix2"]
    identity = {"version": VERSION, "phoenix1": digest(first), "phoenix2": digest(second),
        "catalog": digest(catalog), "officialManifest": canonical_scoring_manifest_sha256(manifest),
        "sourcePolicy": policy.semantic_hash(), "modelCode": model_code_identity(),
        "generationTimestamp": generated_at_utc, "pipelineCode": pipeline_code_identity()}
    general_arguments = {key: value for key, value in arguments.items()
                         if key != "official_scoring_manifest"}
    if general_arguments:
        identity["generalArguments"] = digest(general_arguments)
    generation = digest(identity)
    generation_prefix = PREFIX + "generations/" + generation + "/"
    public_path = generation_prefix + "public.json"
    cached = store.get_json(public_path)
    if cached is not None:
        validate_public_tiers(cached)
        _report_progress(on_progress, generation, 9 + len(FINISH_STAGES) + 2, "public")
        return cached
    state_path = generation_prefix + "state.json"
    state = store.get_json(state_path)
    if state is None:
        state = {"version": 1, "generation": generation, "identity": identity, "stages": [],
                 "stageCodeIdentities": {stage: stage_code_identity(stage) for stage in FINISH_STAGES},
                 "createdAtUtc": generated_at_utc or datetime.now(timezone.utc).isoformat()}
    if state.get("identity") != identity or state.get("version") != 1:
        raise ValueError("The tier generation state has incompatible inputs or code.")
    if input_reference_sha256 is not None:
        if state.get("pinnedInputsSha256") not in (None, input_reference_sha256):
            raise ValueError("The tier generation is bound to different pinned inputs.")
        state["pinnedInputsSha256"] = input_reference_sha256
    created = state["createdAtUtc"]
    semantic_inputs = input_identity(first, second, records=records, metadata=metadata,
        catalog=catalog, manifest_sha256=identity["officialManifest"],
        policy=policy.public(), model_code=identity["modelCode"])
    if "generalArguments" in identity:
        semantic_inputs["generalArguments"] = identity["generalArguments"]
    computation_identity = {**semantic_inputs, "pipelineCode": identity["pipelineCode"],
                            "targetSamples": SAMPLES, "targetSeed": SEED}
    state["numericIdentity"] = {**{key: value for key, value in semantic_inputs.items() if key != "modelCode"},
                                "numericalRecipe": numerical_pipeline_recipe()}
    goal_inputs = goal_input_identity(first, second, catalog=catalog,
        manifest_sha256=identity["officialManifest"], policy=policy.public())
    goal_artifacts = matching_goal_artifacts(store, goal_inputs)
    state["goalInputIdentity"] = goal_inputs
    state["goalArtifacts"] = goal_artifacts
    if goal_artifacts:
        computation_identity["goalArtifacts"] = {goal: ref["payloadSha256"] for goal, ref in goal_artifacts.items()}
    state["computationIdentity"] = computation_identity
    if state.get("computationGeneration"):
        return _advance_finish(store, state, on_stage_start=on_stage_start, on_progress=on_progress)
    saved = None
    pipeline_lookup_failed = False
    try:
        saved = read_index(store, "pipeline", computation_identity)
        original = (_optional_computation(store, saved.get("generation"), computation_identity)
                    if saved is not None and saved.get("generation") != generation else None)
    except (TierComputationCacheError, CheckpointValidationError, PumbilityArtifactIntegrityError,
            json.JSONDecodeError):
        # Optional cross-run reuse is an optimization. Never hide storage/network
        # failures or failures from a pinned in-progress computation.
        original = None
        pipeline_lookup_failed = True
    if original is None and saved is None and not pipeline_lookup_failed:
        try:
            numeric = read_index(store, "numeric-pipeline", state["numericIdentity"])
            completed = (_completed_numeric_computation(store, numeric, state["numericIdentity"])
                         if numeric is not None else None)
        except (TierComputationCacheError, CheckpointValidationError, PumbilityArtifactIntegrityError,
                json.JSONDecodeError):
            completed = None
        if completed is not None:
            state.update(computationGeneration=completed["generation"], completedNumericReuse=True,
                computationCreatedAtUtc=completed["createdAtUtc"], general=completed["general"],
                stages=completed["stages"], phaseInputs=completed["phaseInputs"], fitsReady=True)
            store.put_json(state_path, state)
            return _advance_completed_numeric(store, state, verified_computation=completed,
                                             on_stage_start=on_stage_start, on_progress=on_progress)
    if original is not None:
        if original.get("phaseInputs"):
            # Borrow the original immutable computation, including its timestamps
            # and audit hashes. Only the enclosing run/publication is new.
            state["computationGeneration"] = original["generation"]
            state["computationCreatedAtUtc"] = original["createdAtUtc"]
            store.put_json(state_path, state)
            return _advance_finish(store, state, on_stage_start=on_stage_start, on_progress=on_progress,
                                   verified_computation=original)
    general_identity = digest({key: value for key, value in identity.items()
        if key not in {"version", "generationTimestamp", "pipelineCode"}} | {
            "generalCode": general_code_identity(), "records": digest(records), "metadata": digest(metadata)})
    general_reference = state.get("general")
    general_computation_identity = {**{k: v for k, v in semantic_inputs.items() if k != "modelCode"},
                                    "generalCode": general_code_identity()}
    general_cached = None
    if general_reference is None:
        try:
            general_cached = read_index(store, "general", general_computation_identity)
            if general_cached is None:
                from numeric_recipe import legacy_outer_identity
                legacy_code = legacy_outer_identity()
                if legacy_code is not None:
                    old_identity = {**semantic_inputs, "modelCode": legacy_code, "generalCode": general_code_identity()}
                    general_cached = read_index(store, "general", old_identity)
                    if general_cached is not None:
                        write_index(store, "general", general_computation_identity,
                                    reference=general_cached["reference"], provenance=general_cached["provenance"])
            if general_cached is not None:
                reference = general_cached.get("reference")
                if (not isinstance(reference, dict) or reference.get("stage") != "general"
                        or reference.get("schemaVersion") != 48
                        or not isinstance(general_cached.get("provenance"), dict)):
                    raise TierComputationCacheError("The cached General checkpoint is incompatible.")
                read_checkpoint(store, reference, stage="general", identity=reference.get("identity"))
        except (TierComputationCacheError, CheckpointValidationError):
            general_cached = None
    if general_reference is None and general_cached is not None:
        general_reference = general_cached.get("reference")
        if not isinstance(general_reference, dict) or general_reference.get("stage") != "general":
            raise ValueError("The cached General checkpoint is incompatible.")
        state.setdefault("componentComputations", {})["general"] = general_cached["provenance"]
    if general_reference is None:
        legacy_reference = store.get_json(PREFIX + "general/" + general_identity + ".json")
        if legacy_reference is not None:
            try:
                if (not isinstance(legacy_reference, dict) or legacy_reference.get("stage") != "general"
                        or legacy_reference.get("schemaVersion") != 48):
                    raise TierComputationCacheError("The cached General checkpoint is incompatible.")
                legacy_general = read_checkpoint(store, legacy_reference, stage="general",
                                                 identity=legacy_reference.get("identity"))
                general_reference = legacy_reference
                state.setdefault("componentComputations", {})["general"] = {
                    "computedAtUtc": legacy_general.get("generatedAtUtc"),
                    "checkpointSha256": legacy_reference["payloadSha256"], "legacy": True}
            except (TierComputationCacheError, CheckpointValidationError):
                pass
    if general_reference is None:
        _report_progress(on_progress, generation, 0, "general")
        if on_stage_start: on_stage_start("general")
        general = None
        if _legacy_general_code_identity() == LEGACY_GENERAL_CODE_IDENTITY:
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
                    generated_at_utc=created, source_exclusions=policy, cache_store=store, **arguments)
            provenance = {"computedAtUtc": general.get("generatedAtUtc", created),
                          "inputs": identity}
        else:
            provenance = {"computedAtUtc": general.get("generatedAtUtc"), "legacy": True}
        general_reference = write_checkpoint(store, general, stage="general", identity=general_identity)
        store.put_json(PREFIX + "general/" + general_identity + ".json", general_reference)
        provenance["checkpointSha256"] = general_reference["payloadSha256"]
        state.setdefault("componentComputations", {})["general"] = provenance
        write_index(store, "general", general_computation_identity,
                    reference=general_reference, provenance=provenance)
        del general
    state["general"] = general_reference
    store.put_json(state_path, state)
    if "preparedFitInputs" not in state:
        state["phaseInputs"] = _pin_phase_inputs(store, generation, context)
        state["preparedFitInputs"] = _pin_cohorts(store, generation, prepared, ("sss", "sssplus"))
        state["fitReferences"] = []
        store.put_json(state_path, state)
    return _advance_prepared_fits(store, state, on_stage_start=on_stage_start, on_progress=on_progress)


def _pin_cohorts(store, generation, prepared, goals):
    from tier_pipeline_checkpoints import write_checkpoint
    result = []
    for goal in goals:
        for source in ("official", "submitted"):
            for mode, _ in MODES:
                boards, sources, identity = cohort(prepared, goal, source, mode)
                reference = write_checkpoint(store, {"boards": boards, "sources": sources},
                    stage="cohort-inputs", identity=digest({"generation": generation, "goal": goal,
                        "source": source, "mode": mode, "identity": identity}))
                result.append({"goal": goal, "source": source, "mode": mode, "identity": identity,
                               "inputs": reference, "reusedOriginal": False})
    return result


def _advance_prepared_fits(store, state, *, on_stage_start=None, on_progress=None):
    from tier_pipeline_checkpoints import read_checkpoint
    from tier_computation_cache import write_index
    from target_fit_cache import load_or_fit_target
    generation = state["generation"]
    state_path = PREFIX + "generations/" + generation + "/state.json"
    references = state.setdefault("fitReferences", [])
    for item in state["preparedFitInputs"][len(references):]:
        identity, goal, source, mode = (item[k] for k in ("identity", "goal", "source", "mode"))
        path = fit_path(identity)
        cached = store.get_json(path)
        payload = None
        if cached is None:
            ref = item["inputs"]
            expected = digest({"generation": generation, "goal": goal, "source": source,
                               "mode": mode, "identity": identity})
            payload = read_checkpoint(store, ref, stage="cohort-inputs", identity=expected)
            cached = _cached_fit(store, payload["boards"], identity)
        computed = cached is None
        if computed:
            if on_stage_start: on_stage_start(f"target-fit-{goal}-{source}-{mode.lower()}")
            fit = load_or_fit_target(store, payload["boards"], score_target=identity["target"],
                bootstrap_samples=SAMPLES, seed=SEED, source_boards=payload["sources"],
                general_predictor=training_predictor(identity["target"], store) if source == "official" else None)
            if source == "official":
                fit["diagnostics"]["generalBenchmarkPolicy"] = {"method": "training-only-General-additive-score-cost",
                    "logisticScoreScale": 10000, "scaleSelection": "fixed-before-holdout", "heldoutRecordsUsedInFit": False}
            else:
                fit["diagnostics"]["validation"]["generalTargetBenchmarkReason"] = "Submitted General uses score profiles rather than additive score costs; no equivalent training-only target predictor."
            cached = {"identity": identity, "fit": fit}
            validate_fit(fit, identity)
            store.put_json(path, cached)
        if cached.get("identity") != identity:
            raise ValueError("The captured target fit has stale inputs.")
        validate_fit(cached["fit"], identity)
        references.append({**{k: item[k] for k in ("goal", "source", "mode", "identity", "reusedOriginal")},
                           "path": path, "fitSha256": digest(cached["fit"])})
        store.put_json(state_path, state)
        if computed:
            if on_progress:
                on_progress({"version": 1, "generation": generation, "ordinal": len(references),
                             "stage": "target-fit", "preparedContext": True})
            raise TierAnalysisPending(generation[:16] + "-fit-" + str(len(references)), len(references),
                                      stage="target-fit", generation=generation)
    if not state.get("fitsReady"):
        state["fitsReady"] = True
        store.put_json(state_path, state)
        write_index(store, "pipeline", state["computationIdentity"], generation=generation)
        _report_progress(on_progress, generation, 9, "prepared-inputs")
        raise TierAnalysisPending(generation[:16] + "-9", 8, stage="prepared-inputs",
                                  generation=generation, progress_ordinal=9)
    return _advance_finish(store, state, on_stage_start=on_stage_start, on_progress=on_progress)


def _report_progress(callback, generation, ordinal, stage):
    if callback is not None:
        callback({"version": 1, "generation": generation, "ordinal": ordinal, "stage": stage})


def _captured_computation(store, generation, expected_identity=None):
    from tier_computation_cache import TierComputationCacheError
    if (not isinstance(generation, str) or len(generation) != 64
            or any(character not in "0123456789abcdef" for character in generation)):
        raise TierComputationCacheError("The tier computation generation reference is invalid.")
    state = store.get_json(PREFIX + "generations/" + generation + "/state.json")
    if (not isinstance(state, dict) or state.get("version") != 1
            or state.get("generation") != generation
            or not isinstance(state.get("identity"), dict)
            or digest(state["identity"]) != generation
            or state["identity"].get("pipelineCode") != pipeline_code_identity()
            or not isinstance(state.get("createdAtUtc"), str)
            or (expected_identity is not None and
                (state.get("computationIdentity") != expected_identity
                 or (state.get("computationGeneration") and not state.get("completedNumericReuse"))))):
        raise TierComputationCacheError("The captured tier computation has incompatible inputs or code.")
    if not state.get("computationGeneration"):
        _validate_stage_chain(state)
    _validate_publication_overlay_chain(state)
    return state


def _validate_stage_chain(state):
    from tier_computation_cache import TierComputationCacheError
    reference = state.get("general")
    stages = state.get("stages")
    if (not isinstance(reference, dict) or reference.get("stage") != "general"
            or not isinstance(reference.get("payloadSha256"), str)
            or not isinstance(stages, list) or len(stages) > len(FINISH_STAGES)):
        raise TierComputationCacheError("The tier generation checkpoint chain is stale or corrupt.")
    for index, saved in enumerate(stages):
        stage = FINISH_STAGES[index]
        expected = _stage_identity(state["generation"], stage, reference["payloadSha256"], state["createdAtUtc"])
        if (not isinstance(saved, dict) or saved.get("stage") != stage
                or saved.get("identity") != expected
                or saved.get("predecessorSha256") != reference["payloadSha256"]
                or saved.get("schemaVersion") != STAGE_SCHEMAS[stage]):
            raise TierComputationCacheError("The tier generation checkpoint chain is stale or corrupt.")
        reference = saved


def _tail_overlay_identity(state, predecessor):
    return digest({"generation": state["generation"], "stage": "sss-tail", "version": TAIL_VERSION,
                   "predecessorSha256": predecessor["payloadSha256"]})


def _validate_publication_overlay_chain(state):
    from tier_computation_cache import TierComputationCacheError
    if not state.get("stages"):
        return
    reference = state["stages"][-1]
    if "goalOverlay" in state:
        saved = state["goalOverlay"]
        expected = digest({"generation": state["generation"], "artifacts": state.get("goalArtifacts")})
        if (not isinstance(saved, dict) or not state.get("goalArtifacts")
                or saved.get("stage") != "goal-overlays" or saved.get("schemaVersion") != 58
                or saved.get("identity") != expected
                or saved.get("predecessorSha256") != reference["payloadSha256"]):
            raise TierComputationCacheError("The scoring-goal overlay checkpoint chain differs.")
        reference = saved
    if "tailOverlay" in state:
        saved = state["tailOverlay"]
        if (not isinstance(saved, dict) or saved.get("stage") != "sss-tail" or saved.get("schemaVersion") != 59
                or saved.get("identity") != _tail_overlay_identity(state, reference)
                or saved.get("predecessorSha256") != reference["payloadSha256"]):
            raise TierComputationCacheError("The SSS tail overlay checkpoint chain differs.")
        reference = saved
    if "archive" in state and (not state.get("tailOverlay") or state["archive"] != reference):
        raise TierComputationCacheError("The tier archive does not reference its SSS tail overlay.")


def _publication_overlays(store, state, reference):
    """Persist each additive overlay so retries never taper an output twice."""
    from scoring_goal_tiers import apply_goal_artifacts
    from sss_tail_tiers import apply_sss_tail_tiers
    from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
    _validate_publication_overlay_chain(state)
    state_path = PREFIX + "generations/" + state["generation"] + "/state.json"
    if state.get("goalArtifacts"):
        if "goalOverlay" not in state:
            full = read_checkpoint(store, reference, stage=reference["stage"], identity=reference["identity"])
            artifacts = {goal: read_checkpoint(store, ref, stage="scoring-goal", identity=ref["identity"])
                         for goal, ref in state["goalArtifacts"].items()}
            full = apply_goal_artifacts(full, artifacts, expected_input_identity=state["goalInputIdentity"])
            state["goalOverlay"] = write_checkpoint(store, full, stage="goal-overlays",
                identity=digest({"generation": state["generation"], "artifacts": state["goalArtifacts"]}),
                predecessor=reference["payloadSha256"])
            store.put_json(state_path, state)
        reference = state["goalOverlay"]
    if "tailOverlay" not in state:
        full = read_checkpoint(store, reference, stage=reference["stage"], identity=reference["identity"])
        full = apply_sss_tail_tiers(full)
        state["tailOverlay"] = write_checkpoint(store, full, stage="sss-tail",
            identity=_tail_overlay_identity(state, reference), predecessor=reference["payloadSha256"])
        store.put_json(state_path, state)
    return state["tailOverlay"]


def resume_tier_generation(store, generation, *, generated_at_utc, on_progress=None,
                           on_stage_start=None):
    """Resume a pinned generation before reconstructing any raw source context."""
    state = _captured_computation(store, generation)
    if (state["createdAtUtc"] != generated_at_utc
            or not (state.get("phaseInputs") or state.get("computationGeneration"))):
        raise ValueError("The pinned tier generation has incompatible time or phase inputs.")
    if state.get("preparedFitInputs") and not state.get("fitsReady"):
        return _advance_prepared_fits(store, state, on_progress=on_progress, on_stage_start=on_stage_start)
    return _advance_finish(store, state, on_progress=on_progress, on_stage_start=on_stage_start)


def _optional_computation(store, generation, identity):
    from tier_computation_cache import TierComputationCacheError
    from tier_pipeline_checkpoints import verified_checkpoint_bytes
    original = _captured_computation(store, generation, identity)
    if not original.get("phaseInputs"):
        return None
    reference = original.get("tailOverlay") or original.get("goalOverlay") or (original["stages"][-1] if original["stages"] else original["general"])
    verified_checkpoint_bytes(store, reference, stage=reference["stage"], identity=reference["identity"])
    if original.get("ready"):
        public = store.get_json(PREFIX + "generations/" + generation + "/public.json")
        try:
            validate_public_tiers(public)
            publication = public["summary"]["method"]["productionTierPublication"]
            if (len(original["stages"]) != len(FINISH_STAGES)
                    or publication["inputs"] != original["identity"]
                    or digest(public) != original["ready"]["payloadSha256"]
                    or publication["fullArtifactPath"] != reference["path"]
                    or publication["fullArtifactSha256"] != reference["payloadSha256"]):
                raise ValueError("Publication reference differs.")
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise TierComputationCacheError("The cached tier publication is incompatible.") from error
    return original


def _completed_numeric_computation(store, index, identity):
    """Verify a completed producer without accepting its old active-worker code."""
    from tier_computation_cache import TierComputationCacheError
    from tier_pipeline_checkpoints import verified_checkpoint_bytes
    generation = index.get("generation")
    if not isinstance(generation, str) or len(generation) != 64 or any(c not in "0123456789abcdef" for c in generation):
        raise TierComputationCacheError("The completed numerical generation is invalid.")
    state = store.get_json(PREFIX + "generations/" + generation + "/state.json")
    if (not isinstance(state, dict) or state.get("version") != 1 or state.get("generation") != generation
            or digest(state.get("identity")) != generation or state.get("numericIdentity") != identity
            or state.get("computationGeneration") or len(state.get("stages", [])) != len(FINISH_STAGES)
            or not isinstance(state.get("stageCodeIdentities"), dict)
            or not isinstance(state.get("createdAtUtc"), str) or not state.get("phaseInputs")):
        raise TierComputationCacheError("The completed numerical producer is incompatible.")
    predecessor = state.get("general")
    if not isinstance(predecessor, dict) or predecessor.get("stage") != "general":
        raise TierComputationCacheError("The numerical predecessor chain is missing General.")
    for stage, reference in zip(FINISH_STAGES, state["stages"]):
        code = state["stageCodeIdentities"].get(stage)
        expected = digest({"generation": generation, "stage": stage, "code": code,
            "predecessorSha256": predecessor["payloadSha256"], "createdAtUtc": state["createdAtUtc"]})
        if (not isinstance(code, str) or reference.get("stage") != stage
                or reference.get("schemaVersion") != STAGE_SCHEMAS[stage]
                or reference.get("predecessorSha256") != predecessor["payloadSha256"]
                or reference.get("identity") != expected):
            raise TierComputationCacheError("The completed numerical predecessor chain differs.")
        predecessor = reference
    if index.get("reference") != predecessor:
        raise TierComputationCacheError("The completed numerical index differs from its producer.")
    verified_checkpoint_bytes(store, predecessor, stage=FINISH_STAGES[-1], identity=predecessor["identity"])
    return state


def _advance_completed_numeric(store, state, *, verified_computation=None,
                              on_stage_start=None, on_progress=None):
    """Project a verified completed raw57 under current goal/publication formats."""
    from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
    from tier_computation_cache import write_index
    original = verified_computation or _completed_numeric_computation(store,
        {"generation": state["computationGeneration"], "reference": state["stages"][-1]}, state["numericIdentity"])
    reference = original["stages"][-1]
    generation, state_path = state["generation"], PREFIX + "generations/" + state["generation"] + "/state.json"
    if "archive" not in state:
        if on_stage_start: on_stage_start("archive")
        state["archive"] = _publication_overlays(store, state, reference)
        store.put_json(state_path, state)
        ordinal = 9 + len(FINISH_STAGES) + 1
        _report_progress(on_progress, generation, ordinal, "archive")
        raise TierAnalysisPending(generation[:16] + "-" + str(ordinal), 8, stage="archive",
                                  generation=generation, progress_ordinal=ordinal)
    reference = state["archive"]
    if on_stage_start: on_stage_start("public")
    full = read_checkpoint(store, reference, stage=reference["stage"], identity=reference["identity"])
    public = public_tiers(full, private_path=reference["path"], input_identity=state["identity"],
        full_sha256=reference["payloadSha256"], component_provenance=original.get("componentComputations"),
        computation_identity=state["computationIdentity"])
    public["generatedAtUtc"] = public["summary"]["generatedAtUtc"] = state["createdAtUtc"]
    publication = public["summary"]["method"]["productionTierPublication"]
    publication.update(computedAtUtc=original["createdAtUtc"], computedInputs=original["identity"],
        computedGeneration=original["generation"], reusedComputation=True)
    publication["publicPayloadSha256"] = public_digest(public)
    from tier_public import prepare_tier_publication, validate_tier_publication
    compact, details = prepare_tier_publication(public, generation)
    validate_tier_publication(public, compact)
    _write_detail_shards(store, details)
    validate_public_tiers(compact)
    public_path = PREFIX + "generations/" + generation + "/public.json"
    store.put_json(public_path, compact)
    state["ready"] = {"path": public_path, "payloadSha256": digest(compact),
        "archivePath": reference["path"], "archiveSha256": reference["payloadSha256"]}
    store.put_json(state_path, state)
    write_index(store, "pipeline", state["computationIdentity"], generation=generation)
    _report_progress(on_progress, generation, 9 + len(FINISH_STAGES) + 2, "public")
    return compact


def _advance_reused_computation(store, state, *, on_stage_start=None, on_progress=None,
                               verified_computation=None):
    if state.get("completedNumericReuse"):
        return _advance_completed_numeric(store, state, verified_computation=verified_computation,
                                          on_stage_start=on_stage_start, on_progress=on_progress)
    from tier_pipeline_checkpoints import verified_checkpoint_bytes
    original = verified_computation or _captured_computation(
        store, state["computationGeneration"], state["computationIdentity"])
    verified_reference = ((original.get("tailOverlay") or original.get("goalOverlay") or original["stages"][-1])["blobSha256"]
                          if verified_computation and original["stages"] else None)
    if original["createdAtUtc"] != state.get("computationCreatedAtUtc"):
        raise ValueError("The reused tier computation time differs.")

    def progress(value):
        _report_progress(on_progress, state["generation"], value["ordinal"], value["stage"])

    try:
        computed = _advance_finish(store, original, on_stage_start=on_stage_start, on_progress=progress)
    except TierAnalysisPending as pending:
        raise TierAnalysisPending(state["generation"][:16] + "-" + str(pending.progress_ordinal),
            pending.completed, stage=pending.stage, generation=state["generation"],
            progress_ordinal=pending.progress_ordinal) from None
    original = _captured_computation(store, original["generation"], state["computationIdentity"])
    publication = computed["summary"]["method"]["productionTierPublication"]
    if (publication["inputs"] != original["identity"]
            or (original.get("ready") and digest(computed) != original["ready"]["payloadSha256"])):
        raise ValueError("The reused tier publication differs from its computed generation.")
    # The transition-validated canonical content is unchanged if its compressed
    # bytes match. Verify those bytes without replaying or inflating the full tree.
    reference = original.get("tailOverlay") or original.get("goalOverlay") or original["stages"][-1]
    if reference["blobSha256"] != verified_reference:
        verified_checkpoint_bytes(store, reference, stage=reference["stage"], identity=reference["identity"])
    compact = {**computed, "generatedAtUtc": state["createdAtUtc"],
               "summary": {**computed["summary"], "generatedAtUtc": state["createdAtUtc"],
                           "method": dict(computed["summary"]["method"])}}
    run_publication = {**publication, "inputs": state["identity"],
        "computedAtUtc": publication.get("computedAtUtc", computed["generatedAtUtc"]),
        "computedInputs": publication.get("computedInputs", original["identity"]),
        "computedGeneration": publication.get("computedGeneration", original["generation"]),
        "computationIdentity": digest(state["computationIdentity"]), "reusedComputation": True}
    compact["summary"]["method"]["productionTierPublication"] = run_publication
    run_publication["publicPayloadSha256"] = public_digest(compact)
    validate_public_tiers(compact)
    public_path = PREFIX + "generations/" + state["generation"] + "/public.json"
    store.put_json(public_path, compact)
    state["ready"] = {"path": public_path, "payloadSha256": digest(compact),
        "archivePath": publication["fullArtifactPath"], "archiveSha256": publication["fullArtifactSha256"]}
    store.put_json(PREFIX + "generations/" + state["generation"] + "/state.json", state)
    _report_progress(on_progress, state["generation"], 9 + len(FINISH_STAGES) + 2, "public")
    return compact


def _advance_finish(store, state, *, on_stage_start=None, on_progress=None, verified_computation=None):
    from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
    from tier_diagnostics import event, phase
    if state.get("preparedFitInputs") and not state.get("fitsReady"):
        return _advance_prepared_fits(store, state, on_stage_start=on_stage_start, on_progress=on_progress)
    generation, identity, created = state["generation"], state["identity"], state["createdAtUtc"]
    state_path = PREFIX+"generations/"+generation+"/state.json"
    public_path = PREFIX+"generations/"+generation+"/public.json"
    cached = store.get_json(public_path)
    if cached is not None:
        validate_public_tiers(cached)
        _report_progress(on_progress, generation, 9 + len(FINISH_STAGES) + 2, "public")
        return cached
    if state.get("computationGeneration"):
        return _advance_reused_computation(store, state, on_stage_start=on_stage_start,
                                          on_progress=on_progress, verified_computation=verified_computation)
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
    _report_progress(on_progress, generation,
        9 + completed_stages + int("archive" in state),
        "archive" if "archive" in state else (FINISH_STAGES[completed_stages-1] if completed_stages else "prepared-inputs"))
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
        if len(state["stages"]) == len(FINISH_STAGES) and state.get("numericIdentity"):
            from tier_computation_cache import write_index
            write_index(store, "numeric-pipeline", state["numericIdentity"], generation=generation,
                        reference=checkpoint)
        _report_progress(on_progress, generation, 9 + completed_stages + 1, stage)
        event("tier_checkpoint_saved", generation=generation, stage=stage,
              artifact_bytes=checkpoint["compressedBytes"], output_hash=checkpoint["payloadSha256"])
        ordinal = 9 + completed_stages + 1
        raise TierAnalysisPending(generation[:16]+"-"+str(ordinal), 8, stage=stage,
                                  generation=generation, progress_ordinal=ordinal)
    if "archive" not in state:
        if on_stage_start: on_stage_start("archive")
        state["archive"] = _publication_overlays(store, state, reference)
        store.put_json(state_path, state)
        ordinal = 9 + len(FINISH_STAGES) + 1
        _report_progress(on_progress, generation, ordinal, "archive")
        raise TierAnalysisPending(generation[:16]+"-"+str(ordinal), 8, stage="archive",
                                  generation=generation, progress_ordinal=ordinal)
    state["activeStage"] = "public"
    reference = state["archive"]
    if on_stage_start: on_stage_start("public")
    store.put_json(state_path, state)
    with phase("public", generation=generation):
        full = read_checkpoint(store, reference, stage=reference["stage"], identity=reference["identity"])
        public = public_tiers(full, private_path=reference["path"], input_identity=identity,
                              full_sha256=reference["payloadSha256"],
                              component_provenance=state.get("componentComputations"),
                              computation_identity=state.get("computationIdentity"))
        from tier_public import prepare_tier_publication, validate_tier_publication
        compact, details = prepare_tier_publication(public, generation)
        validate_tier_publication(public, compact)
        _write_detail_shards(store, details)
        validate_public_tiers(compact)
        store.put_json(public_path, compact)
    state["ready"] = {"path": public_path, "payloadSha256": digest(compact),
                      "archivePath": reference["path"], "archiveSha256": reference["payloadSha256"]}
    state.pop("activeStage", None)
    store.put_json(state_path, state)
    _report_progress(on_progress, generation, 9 + len(FINISH_STAGES) + 2, "public")
    return compact


def _write_detail_shards(store, details):
    """Join bounded immutable writes before the public generation commit marker."""
    if not details:
        return
    batch = getattr(store, "put_json_batch", None)
    if callable(batch):
        batch(details)
        return
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=min(4, len(details)), thread_name_prefix="tier-details") as executor:
        pending = [executor.submit(store.put_json, path, artifact) for path, artifact in details.items()]
        for future in as_completed(pending):
            future.result()


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
        if prepared:
            from target_fit_cache import hydrate_point_context
            boards, _, _ = cohort(prepared, item["goal"], item["source"], item["mode"])
            hydrate_point_context(store, boards, cached["fit"], score_target=item["identity"]["target"])
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
        if captured.get("phaseInputs") or captured.get("computationGeneration"):
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


def goal_input_identity(first, second, *, catalog, manifest_sha256, policy):
    """Bind goal artifacts to consumed sources, independently of General DTOs.

    Operator backfills reconstruct stripped chart records, whereas global jobs
    carry General/recommendation estimates. Both feed the same current catalog;
    those unrelated record/metadata fields are not inputs to target analysis.
    Pandas promotes nullable numeric columns to floats in the normal path.
    Normalize only equivalent integral catalog numbers for lookup, without
    changing the observations or any source snapshot/provenance hash.
    """
    from tier_computation_cache import snapshot_identity
    numeric_fields = {"level", "noteCount", "bpmMin", "bpmMax"}
    catalog_identity = [{key: (int(value) if key in numeric_fields
        and isinstance(value, float) and value.is_integer() else value)
        for key, value in row.items()} for row in catalog]
    return {"version": 1, "method": "independent-scoring-goal-inputs-v1",
            "phoenix1": snapshot_identity(first), "phoenix2": snapshot_identity(second),
            "catalog": digest(catalog_identity), "officialManifest": manifest_sha256,
            "sourcePolicy": digest(policy)}


def _goal_recipe(goal):
    from numeric_recipe import benchmark_recipe, producer_dependencies, target_kernel_recipe
    return {"target": _scoring_target(goal).public(), "kernel": target_kernel_recipe(),
            "benchmark": benchmark_recipe(),
            "calibration": digest(producer_dependencies({
                "scoring_goal_tiers.py": {"build_goal_artifact"},
                "sss_scoring.py": {"prepare_target_observations"},
                "production_tiers.py": {"load_context", "goal_input_identity",
                                        "_cohort_observations", "_general_target_predictions"}}))}


def _goal_index_path(inputs, goal):
    return PREFIX + "goal-artifacts/" + digest({"inputs": inputs, "recipe": _goal_recipe(goal)}) + ".json"


def matching_goal_artifacts(store, inputs):
    """Optional results attach only to exactly matching source/policy inputs."""
    from scoring_targets import BACKFILL_GOALS
    from tier_pipeline_checkpoints import read_checkpoint, CheckpointValidationError
    from pumbility_store import PumbilityArtifactIntegrityError
    from tier_diagnostics import event
    result = {}
    for goal in BACKFILL_GOALS:
        outcome = "invalid-checkpoint"
        try:
            index = store.get_json(_goal_index_path(inputs, goal))
            if index is None:
                outcome = "absent-index"
            elif isinstance(index, dict):
                recipe = _goal_recipe(goal)
                if index.get("inputIdentity") != inputs:
                    outcome = "identity-mismatch"
                elif index.get("recipe") != recipe:
                    outcome = "recipe-mismatch"
                else:
                    ref = index["reference"]
                    if not isinstance(ref, dict):
                        raise TypeError("Invalid scoring-goal checkpoint reference.")
                    artifact = read_checkpoint(store, ref, stage="scoring-goal", identity=digest({"inputs": inputs, "recipe": recipe}))
                    if artifact.get("inputIdentity") != inputs:
                        outcome = "identity-mismatch"
                    elif artifact.get("targetConfig") == _scoring_target(goal).public():
                        result[goal] = ref
                        outcome = "hit"
        except (KeyError, TypeError, CheckpointValidationError, PumbilityArtifactIntegrityError, json.JSONDecodeError):
            pass
        event("scoring_goal_artifact_lookup", stage="scoring-goal", goal=goal, outcome=outcome)
    return result


def advance_goal_backfill(store, *, pinned_inputs_reference, goal, on_progress=None, max_fit_workers=1):
    """Compute at most one missing cohort; save artifacts without moving pointers."""
    from scoring_targets import BACKFILL_GOALS
    from scoring_goal_tiers import build_goal_artifact
    from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
    from official_scoring_sources import canonical_scoring_manifest_sha256
    from target_fit_cache import load_or_fit_target, hydrate_point_context
    if goal not in BACKFILL_GOALS:
        raise ValueError("Only independent SS/SS+ goal backfills are supported.")
    if type(max_fit_workers) is not int or max_fit_workers not in (1, 2):
        raise ValueError("Goal fitting permits one worker by default or two explicitly opted-in workers.")
    recipe = _goal_recipe(goal)
    task_identity = {"pinnedInputs": pinned_inputs_reference["payloadSha256"], "recipe": recipe}
    task_path = PREFIX + "goal-jobs/" + digest(task_identity) + ".json"
    state = store.get_json(task_path)
    if state is None:
        captured = read_checkpoint(store, pinned_inputs_reference, stage="pinned-inputs", identity=pinned_inputs_reference["identity"])
        context = load_context(captured["first"], captured["second"], captured["manifest"], captured["records"])
        first, second, catalog, policy, merged, prepared = context
        inputs = goal_input_identity(captured["first"], captured["second"], catalog=catalog,
            manifest_sha256=canonical_scoring_manifest_sha256(captured["manifest"]), policy=policy.public())
        generation = digest(task_identity)
        # Raw captures are already pinned. Retain their exact filtered hashes,
        # not duplicate snapshot/provenance trees beside prepared observations.
        context_ref = write_checkpoint(store, {"catalog": catalog,
            "policy": policy.public(), "policySemanticSha256": policy.semantic_hash(),
            "sourceHashes": {"phoenix1": digest(first), "phoenix2": digest(second)},
            "merged": {"manifestSha256": merged.get("manifestSha256")}, "prepared": prepared},
            stage="goal-context", identity=generation)
        state = {"version": 1, "identity": task_identity, "inputIdentity": inputs,
            "context": context_ref, "createdAtUtc": captured["generatedAtUtc"],
            "cohorts": _pin_cohorts(store, generation, prepared, (goal,)), "fits": []}
        store.put_json(task_path, state)
    if state.get("identity") != task_identity or state.get("version") != 1:
        raise ValueError("The pinned scoring-goal backfill changed code or inputs.")
    inputs = state["inputIdentity"]
    ready = matching_goal_artifacts(store, inputs).get(goal)
    if ready is not None:
        return {"done": True, "goal": goal, "inputIdentity": digest(inputs), "completedTargetFits": 4,
            "artifactReference": {"path": ready["path"], "sha256": ready["payloadSha256"]}}
    while len(state["fits"]) < len(state["cohorts"]):
        tasks = state["cohorts"][len(state["fits"]):len(state["fits"])+max_fit_workers]
        if max_fit_workers == 1:
            completed = [_build_goal_fit(store, tasks[0], digest(task_identity))]
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="goal-fit") as executor:
                completed = list(executor.map(lambda item: _build_goal_fit(store, item, digest(task_identity)), tasks))
        # Only the coordinator mutates durable progress, in original cohort order.
        for reference, computed in completed:
            state["fits"].append(reference)
        store.put_json(task_path, state)
        if any(computed for _, computed in completed):
            progress = {"done": False, "goal": goal, "inputIdentity": digest(inputs),
                        "completedTargetFits": len(state["fits"]), "sequence": digest(task_identity)[:16] + "-" + str(len(state["fits"]))}
            if on_progress: on_progress(progress)
            return progress
    captured = read_checkpoint(store, state["context"], stage="goal-context", identity=digest(task_identity))
    context = (None, None, captured["catalog"],
               SimpleNamespace(public=lambda: captured["policy"],
                               semantic_hash=lambda: captured["policySemanticSha256"]),
               captured["merged"], captured["prepared"])
    fits = {"official": {}, "submitted": {}}
    for item in state["fits"]:
        cached = store.get_json(item["path"])
        if cached is None or cached.get("identity") != item["identity"] or digest(cached.get("fit")) != item["fitSha256"]:
            raise ValueError("A pinned scoring-goal fit changed during calibration.")
        boards, _, _ = cohort(captured["prepared"], goal, item["source"], item["mode"])
        hydrate_point_context(store, boards, cached["fit"], score_target=item["identity"]["target"])
        fits[item["source"]][item["mode"]] = cached["fit"]
    artifact = build_goal_artifact(context, fits, state["fits"], state["createdAtUtc"], goal=goal,
                                   source_hashes=captured["sourceHashes"])
    artifact["inputIdentity"] = inputs
    reference = write_checkpoint(store, artifact, stage="scoring-goal", identity=digest({"inputs": inputs, "recipe": recipe}))
    store.put_json(_goal_index_path(inputs, goal), {"version": 1, "recipe": recipe,
                   "inputIdentity": inputs, "reference": reference})
    return {"done": True, "goal": goal, "inputIdentity": digest(inputs), "completedTargetFits": 4,
            "artifactReference": {"path": reference["path"], "sha256": reference["payloadSha256"]}}


def _build_goal_fit(store, item, generation):
    """A captured child writes immutable numeric outputs, never publication state."""
    from tier_pipeline_checkpoints import read_checkpoint
    from target_fit_cache import load_or_fit_target
    identity = item["identity"]
    expected = digest({"generation": generation, "goal": item["goal"], "source": item["source"],
                       "mode": item["mode"], "identity": identity})
    payload = read_checkpoint(store, item["inputs"], stage="cohort-inputs", identity=expected)
    cached = _cached_fit(store, payload["boards"], identity)
    computed = cached is None
    if computed:
        fit = load_or_fit_target(store, payload["boards"], score_target=identity["target"],
            bootstrap_samples=SAMPLES, seed=SEED, source_boards=payload["sources"],
            general_predictor=training_predictor(identity["target"], store) if item["source"] == "official" else None)
        validate_fit(fit, identity)
        cached = {"identity": identity, "fit": fit}
        store.put_json(fit_path(identity), cached)
    if cached.get("identity") != identity:
        raise ValueError("The scoring-goal fit has stale input identity.")
    validate_fit(cached["fit"], identity)
    return ({**{k: item[k] for k in ("goal", "source", "mode", "identity", "reusedOriginal")},
             "path": fit_path(identity), "fitSha256": digest(cached["fit"])}, computed)
