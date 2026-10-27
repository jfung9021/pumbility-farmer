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
WIDTH_VERSION = "6.24.0-level20-target-width"
RETAINED_VERSION = "6.25.0-retained-goals"
RETAINED_PRODUCER = "retained-goals-v1"
APPROVED_VERSION = "6.27.0-uniform-width"
APPROVED_SCHEMA = 162
PREFIX = "analysis/private/source-aware-tiers/"
SEED = 20261018
SAMPLES = 200
MODES = (("Single", "singles"), ("Double", "doubles"))
# Independent SS/SS+ work is retired. Keep its historical builders/validators
# for existing private artifacts without scheduling or attaching them to refreshes.
REQUIRED_REFRESH_GOALS = ()


class TierAnalysisPending(RuntimeError):
    def __init__(self, sequence: str, completed: int, *, stage="target-fit", generation=None,
                  progress_ordinal=None, fit_count=8):
        super().__init__(f"Tier analysis phase {stage} checkpointed; continuing tier analysis.")
        self.sequence, self.completed = sequence, completed
        self.stage, self.generation = stage, generation
        self.progress_ordinal = completed if progress_ordinal is None else progress_ordinal
        self.fit_count = fit_count
        self.total_stages = fit_count + 1 + len(RETAINED_FINISH_STAGES if fit_count == 4 else FINISH_STAGES) + 2


def digest(value: Any) -> str:
    from canonical_json import canonical_json_sha256
    return canonical_json_sha256(value)


def model_code_identity() -> str:
    from numeric_recipe import target_kernel_recipe, benchmark_recipe
    return digest({"target": target_kernel_recipe(), "benchmark": benchmark_recipe()})


FINISH_STAGES = ("centered-sss", "sssplus", "median", "spread", "combined-clearing",
                 "source-aware", "submitted-calibration")
RETAINED_FINISH_STAGES = ("official-clearing", *FINISH_STAGES[1:], "retained-width",
    "extended-clearing", "observed-clearing", "scoped-width", "folder-centering", "centered-maximum")
STAGE_SCHEMAS = dict(zip(FINISH_STAGES, (51, 52, 53, 54, 55, 56, 57)))
STAGE_SCHEMAS["official-clearing"] = 148
STAGE_SCHEMAS.update({"retained-width":61,"extended-clearing":158,"observed-clearing":159,
    "scoped-width":160,"folder-centering":161,"centered-maximum":162})
GENERAL_FILES = ("scoring_percentile.py", "official_cross_level_scoring.py", "official_player_scoring.py",
                 "official_scoring_calibration.py", "cross_level_clearing.py", "cross_level_clearing_calibration.py",
                 "hybrid_tiers.py", "pumbility_average.py", "tier_postprocessing.py", "tier_difficulty.py",
                 "piu_recommendations.py", "piu_misgrade_analyzer.py", "player_skill_ratings.py",
                 "phoenix1_score_overrides.py", "official_score_merge.py", "official_scoring_sources.py",
                 "official_cross_level_calibration.py", "chart_source_exclusions.py", "pumbility_contract.py",
                 "score_profile_cache.py", "tier_computation_cache.py")
STAGE_FILES = {
    "official-clearing": ("retained_tiers.py", "refined_tiers.py", "official_high_level_clearing.py"),
    "centered-sss": ("sss_scoring.py", "refined_tiers.py", "sss_scoring_refinement.py", "sss_folder_centering.py"),
    "sssplus": ("sssplus_tiers.py", "sss_scoring_refinement.py", "target_tail_policy.py"),
    "median": ("sssplus_median_calibration.py", "sssplus_final_curve.py"),
    "spread": ("sssplus_spread.py", "sssplus_rare_continuous.py", "target_tail_policy.py"),
    "combined-clearing": ("combined_official_clearing.py", "official_high_level_clearing.py"),
    "source-aware": ("source_aware_tiers.py", "final_folder_width.py"),
    "submitted-calibration": ("submitted_sssplus_calibration.py",),
    "retained-width": ("level20_scoring_width.py",),
    "extended-clearing": ("extended_official_clearing.py", "official_high_level_clearing.py"),
    "observed-clearing": ("observed_capped_clearing.py",),
    "scoped-width": ("scoped_tier_width.py",),
    "folder-centering": ("final_folder_centering.py",),
    "centered-maximum": ("centered_maximum_pumbility.py", "approved_tier_ratings.py"),
    "archive": ("tier_pipeline_checkpoints.py",),
    "public": ("production_tiers.py", "tier_public.py", "config/tier-public-fields.json", "sss_tail_tiers.py",
               "level20_scoring_width.py"),
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


def pipeline_code_identity(retained=False):
    stages = RETAINED_FINISH_STAGES if retained else FINISH_STAGES
    return digest({"fits": model_code_identity(), **{
        name: stage_code_identity(name) for name in (*stages, "archive", "public")},
        **({"producer": RETAINED_PRODUCER} if retained else {})})


def _retained_state(state):
    return state.get("identity", {}).get("producer") == RETAINED_PRODUCER


def _finish_stages(state):
    return RETAINED_FINISH_STAGES if _retained_state(state) else FINISH_STAGES


def _fit_count(state):
    return 4 if _retained_state(state) else 8


def _prepared_ordinal(state):
    return _fit_count(state) + 1


@lru_cache(maxsize=2)
def numerical_pipeline_recipe(retained=False):
    """Completed-result identity; active recovery still pins pipeline_code_identity."""
    from numeric_recipe import general_producer_recipe, finish_producer_recipe, target_kernel_recipe, producer_dependencies
    from scoring_targets import SSS, SSSPLUS
    predecessor = digest(general_producer_recipe())
    stages = {}
    for stage in (RETAINED_FINISH_STAGES if retained else FINISH_STAGES):
        predecessor = digest({"predecessor": predecessor, "producer": finish_producer_recipe(stage)})
        stages[stage] = predecessor
    return {"version": 1, "general": general_producer_recipe(), "stages": stages,
            "targets": [SSSPLUS.public()] if retained else [SSS.public(), SSSPLUS.public()], "kernel": target_kernel_recipe(),
            "preparation": digest(producer_dependencies({
                "sss_scoring.py": {"prepare_target_observations"},
                "production_tiers.py": {"_cohort_observations", "_general_target_predictions"}})),
            "samples": SAMPLES, "seed": SEED,
            **({"producer": RETAINED_PRODUCER} if retained else {})}


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
    if full.get("schemaVersion") == APPROVED_SCHEMA:
        from approved_tier_ratings import validate_approved_rating_chain
        validate_approved_rating_chain(full)
    elif full.get("schemaVersion") in (60, 61):
        from level20_scoring_width import validate_level20_scoring_width
        validate_level20_scoring_width(full)
    elif full.get("schemaVersion") == 59:
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
    from approved_tier_ratings import approved_publication_version
    version = (approved_publication_version(full) if full.get("schemaVersion") == APPROVED_SCHEMA else RETAINED_VERSION if full.get("schemaVersion") == 61 else WIDTH_VERSION if full.get("schemaVersion") == 60 else TAIL_VERSION
               if full.get("schemaVersion") == 59 else GOAL_VERSION
               if full.get("schemaVersion") == 58 else VERSION if full.get("schemaVersion") == 57 else PREVIOUS_VERSION)
    result["summary"]["scriptVersion"] = version
    if full.get("schemaVersion") in (61, APPROVED_SCHEMA):
        result["summary"]["method"]["availableScoringGoals"] = ["general", "sssplus"]
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
    if schema not in (56, 57, 58, 59, 60, 61, APPROVED_SCHEMA):
        raise ValueError("The hosted source-aware tier schema must be 56, 57, 58, 59 or 60.")
    method = payload["summary"]["method"]
    publication = method.get("productionTierPublication", {})
    from approved_tier_ratings import approved_publication_version
    expected_version = (approved_publication_version(payload) if schema == APPROVED_SCHEMA else RETAINED_VERSION if schema == 61 else WIDTH_VERSION if schema == 60 else TAIL_VERSION if schema == 59
                        else GOAL_VERSION if schema == 58 else VERSION if schema == 57 else PREVIOUS_VERSION)
    if (publication.get("version") != 1 or publication.get("method") != expected_version
            or publication.get("publicPayloadSha256") != public_digest(payload)):
        raise ValueError("The public source-aware tier artifact failed its publication identity.")
    if schema in (57, 58, 59, 60, 61, APPROVED_SCHEMA) and method.get("submittedSssplusCalibration", {}).get("localOnly") is not False:
        raise ValueError("The hosted submitted SSS+ calibration must declare production use.")
    rows = payload["singles"] + payload["doubles"]
    policy = load_exclusions([{**r, "id": r["chartId"]} for r in rows])
    if method["sourceAwareTiers"]["sourcePolicy"]["effectivePolicySha256"] != policy.semantic_hash():
        raise ValueError("The published tiers use a different source exclusion policy.")
    goals = ("general", "sss", "sssplus")
    if schema in (61, APPROVED_SCHEMA):
        from retained_tiers import GOALS, validate_marker
        validate_marker(payload)
        goals = GOALS
        if any(set(row.get(name, {})) != set(GOALS) for row in rows
               for name in ("scoringVariants", "pumbilityVariants")):
            raise ValueError("The retained publication is missing a current goal.")
    expanded = schema == 58 or schema in (59, 60) and method.get("sssTailPolicy", {}).get("baseSchemaVersion") == 58
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
            expected = None if scoring is None or clearing is None else (
                max(scoring, clearing) + method["centeredMaximumPumbility"]["folders"][row["type"]][str(row["level"])][goal]["offset"]
                if schema == APPROVED_SCHEMA else (scoring + clearing) / 2)
            if actual != expected or any(v is not None and not math.isfinite(v) for v in (scoring, clearing, actual)):
                raise ValueError("Public Pumbility must average its finite final components.")
        if row["estimatedDifficulty"] != row["scoringVariants"]["general"]["estimatedDifficulty"]:
            raise ValueError("General Scoring compatibility fields differ.")
    if schema == APPROVED_SCHEMA:
        from approved_tier_ratings import validate_public_approved_ratings
        validate_public_approved_ratings(payload)
    if schema in (60, 61):
        from level20_scoring_width import validate_public_level20_scoring_width
        validate_public_level20_scoring_width(payload)
    if schema in (59, 60):
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
    def predecessor_metric(row):
        metric = row["scoringVariants"]["sss"]
        # Schema 60 preserves the exact schema-59 public points as compact
        # evidence. Replay the older safeguard against those inputs, then let
        # the independent width validator check the current final values.
        width = metric.get("scoringLevel20Width")
        return ({**metric, **width["original"]}
                if payload.get("schemaVersion") == 60 and isinstance(width, Mapping) else metric)
    if not set(selected) <= {str(row["chartId"]) for row in rows}:
        raise ValueError("The public SSS tail selection contains an unknown chart.")
    pre, final, changed = [], [], 0
    for row in rows:
        metric, level = predecessor_metric(row), row["level"]
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
        "displayedAbsAtLeast2": sum(abs(math.trunc(predecessor_metric(row)["estimatedDifficulty"]*10+1e-9)/10-row["level"]-.5) >= 2
            for row in rows if predecessor_metric(row)["estimatedDifficulty"] is not None),
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
    if stage == "official-clearing":
        from retained_tiers import apply_retained_baseline
        return apply_retained_baseline(result, merged["_releasePhoenix2Capture"])
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
    if stage == "retained-width":
        from level20_scoring_width import apply_retained_level20_scoring_width
        return apply_retained_level20_scoring_width(result)
    if stage == "extended-clearing":
        from extended_official_clearing import apply_extended_official_clearing
        return apply_extended_official_clearing(result, merged, bootstrap_samples=1000, local_only=False)[0]
    if stage == "observed-clearing":
        from observed_capped_clearing import apply_observed_capped_clearing
        return apply_observed_capped_clearing(result)[0]
    if stage == "scoped-width":
        from scoped_tier_width import apply_scoped_tier_width
        return apply_scoped_tier_width(result)[0]
    if stage == "folder-centering":
        from final_folder_centering import apply_final_folder_centering
        return apply_final_folder_centering(result)[0]
    if stage == "centered-maximum":
        from centered_maximum_pumbility import apply_centered_maximum_pumbility
        return apply_centered_maximum_pumbility(result)[0]
    raise ValueError("Unknown tier publication phase.")


def finish_generation(general, context, fits, identities, *, generated_at_utc=None):
    """Uninterrupted reference path, retained for exact parity verification."""
    created = generated_at_utc or datetime.now(timezone.utc).isoformat()
    result = general
    for stage in FINISH_STAGES:
        result = _finish_stage(stage, result, context, fits, identities, created)
    return result


def build_retained_hosted_tiers(*args, **kwargs):
    """Current hosted entry point; legacy callers retain their original producer."""
    kwargs["on_progress"] = _retained_progress(kwargs.get("on_progress"))
    return build_hosted_tiers(*args, retained_goals=True, **kwargs)


def _retained_progress(callback):
    if callback is None:
        return None
    return lambda value: callback({**value, "producer": RETAINED_PRODUCER})


def _retained_dependencies(first, second, manifest, records):
    """Validate cheap numerical dependencies before preparing merged model rows."""
    from chart_source_exclusions import load_exclusions
    from cross_level_clearing import current_clearing_catalog
    from official_scoring_identity import scoring_source_numeric_identity
    # This validation includes the capture bytes, scope and all observations.
    # The omitted fetch/check envelope remains in the separate run identity.
    source_numeric = scoring_source_numeric_identity(manifest)
    catalog = current_clearing_catalog({key: [r for r in (records or []) if r.get("type") == mode]
        for mode, key in MODES}, second)
    return catalog, load_exclusions(catalog), source_numeric


def _index_preparation(store, state):
    """Only completed publications qualify for lookup before model preparation."""
    from tier_computation_cache import write_index
    descriptor = state.get("preparationDescriptor")
    if _retained_state(state) and descriptor and state.get("ready"):
        write_index(store, "preparation", descriptor,
            generation=(state["generation"] if state.get("completedNumericReuse")
                        else state.get("computationGeneration", state["generation"])),
            computationIdentity=state["computationIdentity"])


def build_hosted_tiers(records, metadata, first, second, *, store, arguments, generated_at_utc=None,
                       on_stage_start=None, input_reference_sha256=None, on_progress=None, retained_goals=False):
    """Advance one validated phase; never capture scores or fit recommendations."""
    from scoring_percentile import build_production_tier_payload
    from sss_scoring_model import fit_target_model
    from official_scoring_sources import canonical_scoring_manifest_sha256
    from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
    from tier_computation_cache import input_identity, preparation_descriptor, read_index, write_index, TierComputationCacheError
    from tier_pipeline_checkpoints import CheckpointValidationError
    from pumbility_store import PumbilityArtifactIntegrityError
    from tier_diagnostics import event, phase
    manifest = arguments["official_scoring_manifest"]
    context = None
    if retained_goals:
        catalog, policy, source_numeric = _retained_dependencies(first, second, manifest, records)
    else:
        context = load_context(first, second, manifest, records)
        p1, p2, catalog, policy, merged, prepared = context
        merged["_releasePhoenix2Capture"] = manifest["_captures"]["Phoenix2"]
    identity = {"version": RETAINED_VERSION if retained_goals else VERSION, "phoenix1": digest(first), "phoenix2": digest(second),
        "catalog": digest(catalog), "officialManifest": canonical_scoring_manifest_sha256(manifest),
        "sourcePolicy": policy.semantic_hash(), "modelCode": model_code_identity(),
        "generationTimestamp": generated_at_utc, "pipelineCode": pipeline_code_identity(retained_goals)}
    if retained_goals:
        identity["producer"] = RETAINED_PRODUCER
        identity["officialSourceNumeric"] = source_numeric
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
        _report_progress(on_progress, generation, (5 if retained_goals else 9) + len(FINISH_STAGES) + 2, "public")
        return cached
    state_path = generation_prefix + "state.json"
    state = store.get_json(state_path)
    if state is None:
        state = {"version": 1, "generation": generation, "identity": identity, "stages": [],
                 "stageCodeIdentities": {stage: stage_code_identity(stage) for stage in
                                        (RETAINED_FINISH_STAGES if retained_goals else FINISH_STAGES)},
                 "createdAtUtc": generated_at_utc or datetime.now(timezone.utc).isoformat()}
    if state.get("identity") != identity or state.get("version") != 1:
        raise ValueError("The tier generation state has incompatible inputs or code.")
    if input_reference_sha256 is not None:
        if state.get("pinnedInputsSha256") not in (None, input_reference_sha256):
            raise ValueError("The tier generation is bound to different pinned inputs.")
        state["pinnedInputsSha256"] = input_reference_sha256
    created = state["createdAtUtc"]
    semantic_inputs = input_identity(first, second, records=records, metadata=metadata,
        catalog=catalog, manifest_sha256=source_numeric if retained_goals else identity["officialManifest"],
        policy=policy.public(), model_code=identity["modelCode"])
    if retained_goals:
        semantic_inputs["version"] = 2
        semantic_inputs["officialSourceNumeric"] = semantic_inputs.pop("officialManifest")
        state["preparationDescriptor"] = preparation_descriptor(first, second,
            records=records, metadata=metadata, catalog=catalog, policy=policy.public(),
            general_arguments=general_arguments, source_numeric_hash=source_numeric,
            model_code=identity["modelCode"], pipeline_code=identity["pipelineCode"],
            producer=RETAINED_PRODUCER)
    if "generalArguments" in identity:
        semantic_inputs["generalArguments"] = identity["generalArguments"]
    computation_identity = {**semantic_inputs, "pipelineCode": identity["pipelineCode"],
                            "targetSamples": SAMPLES, "targetSeed": SEED}
    state["numericIdentity"] = {**{key: value for key, value in semantic_inputs.items() if key != "modelCode"},
                                 "numericalRecipe": numerical_pipeline_recipe(retained_goals)}
    if REQUIRED_REFRESH_GOALS:
        if context is None:
            context = load_context(first, second, manifest, records)
            p1, p2, catalog, policy, merged, prepared = context
        goal_inputs = goal_input_identity(first, second, catalog=catalog,
            manifest_sha256=identity["officialManifest"], policy=policy.public())
        goal_artifacts = matching_goal_artifacts(store, goal_inputs)
        state["goalInputIdentity"] = goal_inputs
        state["goalArtifacts"] = goal_artifacts
        state["goalSourceHashes"] = {"phoenix1": digest(p1), "phoenix2": digest(p2)}
        if goal_artifacts:
            computation_identity["goalArtifacts"] = {goal: ref["payloadSha256"] for goal, ref in goal_artifacts.items()}
    state["computationIdentity"] = computation_identity
    if state.get("computationGeneration") or state.get("fitsReady"):
        return _advance_finish(store, state, on_stage_start=on_stage_start, on_progress=on_progress)
    saved = None
    pipeline_lookup_failed = False
    try:
        if retained_goals:
            descriptor_index = read_index(store, "preparation", state["preparationDescriptor"])
            if descriptor_index is not None:
                if descriptor_index.get("computationIdentity") != computation_identity:
                    raise TierComputationCacheError("The preparation numerical identity differs.")
                candidate = _optional_computation(store, descriptor_index.get("generation"), computation_identity)
                if (candidate is None or not candidate.get("ready")
                        or candidate.get("preparationDescriptor") != state["preparationDescriptor"]):
                    raise TierComputationCacheError("The completed preparation producer differs.")
                saved = {"generation": candidate["generation"]}
        if saved is None:
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
    if context is None:
        context = load_context(first, second, manifest, records)
        p1, p2, prepared_catalog, prepared_policy, merged, prepared = context
        if digest(prepared_catalog) != digest(catalog) or prepared_policy.public() != policy.public():
            raise ValueError("The prepared numerical source dependencies changed.")
        merged["_releasePhoenix2Capture"] = manifest["_captures"]["Phoenix2"]
    general_identity = digest({key: value for key, value in identity.items()
        if key not in {"version", "generationTimestamp", "pipelineCode"}} | {
            "generalCode": general_code_identity(), "records": digest(records), "metadata": digest(metadata)})
    general_reference = state.get("general") or _preparation_receipt(
        store, generation, "general", stage="general", identity=general_identity)
    if general_reference is not None and "general" not in state:
        state.setdefault("componentComputations", {})["general"] = {
            "computedAtUtc": created, "checkpointSha256": general_reference["payloadSha256"],
            "recoveredPreparation": True}
    general_computation_identity = {**{k: v for k, v in semantic_inputs.items() if k != "modelCode"},
                                    "generalCode": general_code_identity()}
    if retained_goals:
        # General also merges official score observations. Keep their validated
        # semantic identity even though the capture check envelope is excluded.
        general_identity = digest(general_computation_identity)
        general_reference = state.get("general") or _preparation_receipt(
            store, generation, "general", stage="general", identity=general_identity)
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
            _check_preparation_budget("general")
            with phase("general", generation=generation):
                general = build_production_tier_payload(records, metadata, p1, p2,
                    generated_at_utc=created, source_exclusions=policy, cache_store=store, **arguments)
            provenance = {"computedAtUtc": general.get("generatedAtUtc", created),
                          "inputs": identity}
        else:
            provenance = {"computedAtUtc": general.get("generatedAtUtc"), "legacy": True}
        general_reference = write_checkpoint(store, general, stage="general", identity=general_identity,
            recovery_path=_preparation_receipt_path(generation, "general"))
        from tier_pipeline_checkpoints import verified_checkpoint_bytes
        verified_checkpoint_bytes(store, general_reference, stage="general", identity=general_identity)
        store.put_json(PREFIX + "general/" + general_identity + ".json", general_reference)
        provenance["checkpointSha256"] = general_reference["payloadSha256"]
        state.setdefault("componentComputations", {})["general"] = provenance
        write_index(store, "general", general_computation_identity,
                    reference=general_reference, provenance=provenance)
        del general
    state["general"] = general_reference
    state["preparationGeneralIdentity"] = general_identity
    store.put_json(state_path, state)
    if not state.get("preparationVersion") and not state.get("phaseInputs"):
        state["preparationVersion"] = 1
        store.put_json(state_path, state)
        _preparation_pending(store, state, on_progress, "general")
    return _advance_preparation(store, state, context=context,
        on_stage_start=on_stage_start, on_progress=on_progress)


PHASE_INPUT_NAMES = ("model", "official", "provenance")
PREPARATION_COHORTS = tuple((goal, source, mode) for goal in ("sss", "sssplus")
                          for source in ("official", "submitted") for mode, _ in MODES)


def _preparation_cohorts(state):
    return tuple(item for item in PREPARATION_COHORTS
                 if not _retained_state(state) or item[0] == "sssplus")


def _preparation_total(state):
    return 1 + len(PHASE_INPUT_NAMES) + len(_preparation_cohorts(state))


def _check_preparation_budget(unit):
    from analysis_budget import work_budget_available
    if not work_budget_available():
        raise RuntimeError("The analysis invocation exhausted its work budget before tier preparation " + unit + ".")


def _preparation_receipt_path(generation, unit):
    return PREFIX + "generations/" + generation + "/preparation/" + unit + ".json"


def _preparation_receipt(store, generation, unit, *, stage, identity):
    from tier_pipeline_checkpoints import verified_checkpoint_bytes, CheckpointValidationError
    reference = store.get_json(_preparation_receipt_path(generation, unit))
    if reference is None:
        return None
    try:
        verified_checkpoint_bytes(store, reference, stage=stage, identity=identity)
    except CheckpointValidationError:
        # An interrupted upload may leave only its candidate receipt. It is not
        # progress and can be retried; transport/storage failures still surface.
        return None
    return reference


def _preparation_ordinal(state):
    phases = state.get("phaseInputs", {})
    cohorts = state.get("preparedFitInputs", [])
    if not isinstance(phases, dict) or set(phases) - set(PHASE_INPUT_NAMES):
        raise ValueError("The tier preparation phase inputs are invalid.")
    general = state.get("general")
    if (not isinstance(general, dict) or general.get("stage") != "general"
            or (state.get("preparationGeneralIdentity") is not None
                and general.get("identity") != state["preparationGeneralIdentity"])):
        raise ValueError("The tier preparation General provenance differs.")
    for name, reference in phases.items():
        if (not isinstance(reference, dict) or reference.get("stage") != "phase-inputs-" + name
                or reference.get("identity") != digest({"generation": state["generation"], "kind": name})):
            raise ValueError("The tier preparation phase provenance differs.")
    if not isinstance(cohorts, list) or len(cohorts) > len(_preparation_cohorts(state)):
        raise ValueError("The tier preparation cohorts are invalid.")
    for item, expected in zip(cohorts, _preparation_cohorts(state)):
        if not isinstance(item, dict) or tuple(item.get(k) for k in ("goal", "source", "mode")) != expected:
            raise ValueError("The tier preparation cohort order differs.")
        reference = item.get("inputs", {})
        identity = digest({"generation": state["generation"], "goal": item["goal"],
            "source": item["source"], "mode": item["mode"], "identity": item["identity"]})
        if (not isinstance(reference, dict) or reference.get("stage") != "cohort-inputs"
                or reference.get("identity") != identity):
            raise ValueError("The tier preparation cohort provenance differs.")
    if cohorts and len(phases) != len(PHASE_INPUT_NAMES):
        raise ValueError("Tier cohorts require all captured phase inputs.")
    return int("general" in state) + len(phases) + len(cohorts)


def _preparation_progress(state, callback):
    if callback is not None:
        callback({"version": 1, "generation": state["generation"], "ordinal": 0,
            "stage": "general", "preparationOrdinal": _preparation_ordinal(state),
            "preparedContext": len(state.get("phaseInputs", {})) == len(PHASE_INPUT_NAMES)})


def _preparation_pending(store, state, callback, unit):
    from tier_diagnostics import event
    ordinal = _preparation_ordinal(state)
    _preparation_progress(state, callback)
    event("tier_preparation_committed", generation=state["generation"], stage=unit,
          preparation_ordinal=ordinal)
    raise TierAnalysisPending(state["generation"][:16] + "-prepare-" + str(ordinal), 0,
        stage="preparation-" + unit, generation=state["generation"], progress_ordinal=0,
        fit_count=_fit_count(state))


def _advance_preparation(store, state, *, context=None, on_stage_start=None, on_progress=None):
    """Commit one input unit, then hand off before starting any next unit or fit."""
    from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint, verified_checkpoint_bytes
    from tier_diagnostics import event
    generation = state["generation"]
    state_path = PREFIX + "generations/" + generation + "/state.json"
    _preparation_ordinal(state)
    if (state.get("preparationVersion") and not state.get("fitReferences")
            and not state.get("fitsReady") and _preparation_ordinal(state) < _preparation_total(state)):
        _preparation_progress(state, on_progress)
    phases = state.setdefault("phaseInputs", {})
    for name in PHASE_INPUT_NAMES:
        if name in phases:
            continue
        if context is None:
            raise ValueError("The tier preparation requires its uncaptured source context.")
        unit, stage = "phase-" + name, "phase-inputs-" + name
        identity = digest({"generation": generation, "kind": name})
        reference = _preparation_receipt(store, generation, unit, stage=stage, identity=identity)
        if reference is None:
            _check_preparation_budget(unit)
            if on_stage_start: on_stage_start(unit)
            event("tier_preparation_started", generation=generation, stage=unit)
            reference = write_checkpoint(store, _phase_input_payload(context, name),
                stage=stage, identity=identity, recovery_path=_preparation_receipt_path(generation, unit))
            verified_checkpoint_bytes(store, reference, stage=stage, identity=identity)
        phases[name] = reference
        state["preparationVersion"] = 1
        store.put_json(state_path, state)
        _preparation_pending(store, state, on_progress, unit)
    cohorts = state.setdefault("preparedFitInputs", [])
    if len(cohorts) < len(_preparation_cohorts(state)):
        _check_preparation_budget("cohort-" + str(len(cohorts) + 1))
        prepared = (context[-1] if context is not None else read_checkpoint(store, phases["model"],
            stage="phase-inputs-model", identity=digest({"generation": generation, "kind": "model"}))["prepared"])
        goal, source, mode = _preparation_cohorts(state)[len(cohorts)]
        boards, sources, fit_identity = cohort(prepared, goal, source, mode)
        unit = "cohort-" + str(len(cohorts) + 1)
        identity = digest({"generation": generation, "goal": goal, "source": source,
                           "mode": mode, "identity": fit_identity})
        reference = _preparation_receipt(store, generation, unit, stage="cohort-inputs", identity=identity)
        if reference is None:
            if on_stage_start: on_stage_start(unit)
            event("tier_preparation_started", generation=generation, stage=unit)
            reference = write_checkpoint(store, {"boards": boards, "sources": sources},
                stage="cohort-inputs", identity=identity,
                recovery_path=_preparation_receipt_path(generation, unit))
            verified_checkpoint_bytes(store, reference, stage="cohort-inputs", identity=identity)
        cohorts.append({"goal": goal, "source": source, "mode": mode, "identity": fit_identity,
                        "inputs": reference, "reusedOriginal": False})
        state.setdefault("fitReferences", [])
        state["preparationVersion"] = 1
        store.put_json(state_path, state)
        _preparation_pending(store, state, on_progress, unit)
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
    from target_fit_cache import load_or_fit_target, pending_fit_progress, TargetFitPending
    if _preparation_ordinal(state) != _preparation_total(state):
        raise ValueError("The captured tier preparation is incomplete.")
    generation = state["generation"]
    state_path = PREFIX + "generations/" + generation + "/state.json"
    references = state.setdefault("fitReferences", [])
    product_policy = {"diagnostics_policy": "product-only"} if _retained_state(state) else {}
    for item in state["preparedFitInputs"][len(references):]:
        identity, goal, source, mode = (item[k] for k in ("identity", "goal", "source", "mode"))
        path = fit_path(identity)
        if product_policy:
            path = path.replace("/fits/", "/fits/product-only/")
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
            inner = pending_fit_progress(store, payload["boards"], score_target=identity["target"],
                bootstrap_samples=SAMPLES, seed=SEED, source_boards=payload["sources"], **product_policy)
            if inner is not None:
                _report_inner_fit(on_progress, generation, len(references), inner)
            if on_stage_start: on_stage_start(f"target-fit-{goal}-{source}-{mode.lower()}")
            try:
                fit = load_or_fit_target(store, payload["boards"], score_target=identity["target"],
                    bootstrap_samples=SAMPLES, seed=SEED, source_boards=payload["sources"],
                    general_predictor=training_predictor(identity["target"], store)
                        if source == "official" and not product_policy else None, **product_policy)
            except TargetFitPending as pending:
                recovered = pending_fit_progress(store, payload["boards"], score_target=identity["target"],
                    bootstrap_samples=SAMPLES, seed=SEED, source_boards=payload["sources"], **product_policy)
                if (recovered is None or recovered["identity"] != pending.progress["identity"]
                        or recovered["ordinal"] < pending.progress["ordinal"]):
                    raise ValueError("The pending target fit has no matching durable progress.")
                _report_inner_fit(on_progress, generation, len(references), recovered)
                raise _pending_inner_fit(generation, len(references), recovered, fit_count=_fit_count(state)) from None
            if source == "official" and not product_policy:
                fit["diagnostics"]["generalBenchmarkPolicy"] = {"method": "training-only-General-additive-score-cost",
                    "logisticScoreScale": 10000, "scaleSelection": "fixed-before-holdout", "heldoutRecordsUsedInFit": False}
            elif not product_policy:
                fit["diagnostics"]["validation"]["generalTargetBenchmarkReason"] = "Submitted General uses score profiles rather than additive score costs; no equivalent training-only target predictor."
            cached = {"identity": identity, "fit": fit}
            validate_fit(fit, identity)
            store.put_json(path, cached)
        if product_policy:
            from target_fit_cache import _product_fit
            cached = {**cached, "fit": _product_fit(cached["fit"])}
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
                                      stage="target-fit", generation=generation, fit_count=_fit_count(state))
    if not state.get("fitsReady"):
        state["fitsReady"] = True
        store.put_json(state_path, state)
        write_index(store, "pipeline", state["computationIdentity"], generation=generation)
        ordinal = _prepared_ordinal(state)
        _report_progress(on_progress, generation, ordinal, "prepared-inputs")
        raise TierAnalysisPending(generation[:16] + "-" + str(ordinal), _fit_count(state), stage="prepared-inputs",
                                  generation=generation, progress_ordinal=ordinal, fit_count=_fit_count(state))
    return _advance_finish(store, state, on_stage_start=on_stage_start, on_progress=on_progress)


def _report_inner_fit(callback, generation, ordinal, progress, *, goal_units=None):
    if callback is not None:
        value = {"version": 1, "generation": generation, "ordinal": ordinal,
                 "stage": "target-fit", "preparedContext": True, "fitProgress": progress}
        if goal_units is not None:
            value.update(stage="scoring-goals", goalProgressOrdinal=goal_units)
        callback(value)


def _pending_inner_fit(generation, ordinal, progress, *, goal_units=None, fit_count=8):
    sequence = generation[:16] + "-inner-" + progress["identity"][:16] + "-" + str(progress["ordinal"])
    return TierAnalysisPending(sequence, min(ordinal, fit_count),
        stage="scoring-goals" if goal_units is not None else "target-fit",
        generation=generation, progress_ordinal=ordinal, fit_count=fit_count)


def _inventory_point_context(data, fit, context):
    from target_fit_cache import validated_point_context
    return validated_point_context(data, fit, context, SEED)


def _inventory_bootstrap(data, identity, saved):
    from target_fit_cache import validated_bootstrap_checkpoint
    return validated_bootstrap_checkpoint(data, identity, saved)


def inventory_retained_fits(store, generation):
    """Read-only inventory of exact 995000 work; private references stay separate.

    Historical generation receipts are verified against their own immutable
    identity, never accepted as a current producer state. Optional caches may be
    absent; corrupt/missing pinned prepared inputs fail closed. No optimizer,
    cache migration, progress write or point-context registration occurs here.
    """
    import target_fit_cache as cache
    import sss_scoring_model as model
    from numeric_recipe import legacy_component_code, legacy_outer_identities
    from pumbility_store import PumbilityArtifactIntegrityError
    from tier_pipeline_checkpoints import read_checkpoint
    if (not isinstance(generation, str) or len(generation) != 64
            or any(c not in "0123456789abcdef" for c in generation)):
        raise ValueError("The source tier generation reference is invalid.")
    state = store.get_json(PREFIX + "generations/" + generation + "/state.json")
    if (not isinstance(state, dict) or state.get("version") != 1 or state.get("generation") != generation
            or not isinstance(state.get("identity"), dict) or digest(state["identity"]) != generation):
        raise ValueError("The source tier generation identity differs.")
    _preparation_ordinal(state)
    items = [item for item in state.get("preparedFitInputs", []) if item["goal"] == "sssplus"]
    if [(item["source"], item["mode"]) for item in items] != [
            (source, mode) for source in ("official", "submitted") for mode, _ in MODES]:
        raise ValueError("The four retained target inputs are not completely captured.")
    summary, artifacts = [], []

    def optional(path):
        try:
            return store.get_json(path)
        except (PumbilityArtifactIntegrityError, json.JSONDecodeError):
            return None

    def retain(kind, path, component, record, *, adoption=False):
        # Hash exactly the object that was validated. A concurrent bootstrap
        # advance makes a later bundle copy reject/retry instead of blessing
        # unvalidated replacement bytes.
        artifacts.append({"kind": kind, "path": path, "sha256": digest(record),
                          "componentIdentity": digest(component), "adoptionRequired": adoption,
                          "source": item["source"], "mode": item["mode"],
                          **({"legacyProducerIdentity": state["identity"]["modelCode"]} if adoption else {})})

    legacy_producer = state["identity"].get("modelCode")
    old_component = legacy_component_code() if legacy_producer in legacy_outer_identities() else None
    for item in items:
        identity = item["identity"]
        expected = digest({"generation": generation, "goal": "sssplus", "source": item["source"],
                           "mode": item["mode"], "identity": identity})
        payload = read_checkpoint(store, item["inputs"], stage="cohort-inputs", identity=expected)
        from sss_scoring import payload_sha256
        if identity != {"boards": payload_sha256(payload["boards"]),
                "sourceBoards": payload_sha256(payload["sources"]), "target": 995000,
                "samples": SAMPLES, "seed": SEED}:
            raise ValueError("The captured retained target observations or policy differ.")
        data = model._prepare(payload["boards"], 995000)
        component = cache._identity(data, score_target=995000, bootstrap_samples=SAMPLES,
                                   seed=SEED, regularization_grid=model.REGULARIZATION_GRID)
        outer = main = bootstrap = point = adoption = False
        completed = 0
        warnings = []
        if not len(data["y"]):
            warnings.append("no-eligible-comparison-component")
        for code in dict.fromkeys([model_code_identity(), *legacy_outer_identities()]):
            path = PREFIX + "fits/" + digest({"inputs": identity, "code": code}) + ".json"
            for candidate in (path.replace("/fits/", "/fits/product-only/"), path):
                saved = optional(candidate)
                if not isinstance(saved, dict) or saved.get("identity") != identity:
                    continue
                try:
                    validate_fit(saved["fit"], identity)
                    if saved["fit"]["diagnostics"]["validation"].get("seed") != SEED:
                        continue
                except (KeyError, TypeError, ValueError):
                    warnings.append("invalid-optional-outer")
                    continue
                outer = True
                retain("outer", candidate, component, saved)
                break
            if outer:
                break
        candidates = [component]
        if old_component is not None and old_component != component["code"]:
            candidates.append({**component, "code": old_component})
        bootstrap_candidates = []
        for candidate in candidates:
            legacy = candidate != component
            saved = cache._read(store, "main", candidate)
            if saved is not None:
                try:
                    validate_fit(saved["fit"], identity)
                    valid = (saved["fit"]["diagnostics"]["validation"].get("seed") == SEED
                             and _inventory_point_context(data, saved["fit"], saved["context"]))
                except (KeyError, TypeError, ValueError):
                    valid = False
                if valid:
                    main, point, adoption = True, bool(len(data["y"])), legacy
                    retain("main", cache._path("main", candidate), component,
                        {"version": cache.VERSION, "identity": candidate, "payload": saved,
                         "payloadSha256": digest(saved)}, adoption=legacy)
                    break
            saved_bootstrap = cache._BootstrapCheckpoint(store, candidate).load()
            if saved_bootstrap is not None:
                if _inventory_bootstrap(data, candidate, saved_bootstrap):
                    bootstrap_candidates.append((candidate, saved_bootstrap))
                else:
                    warnings.append("invalid-optional-bootstrap")
        if not main and bootstrap_candidates:
            candidate, saved_bootstrap = max(bootstrap_candidates, key=lambda value: value[1]["completed"])
            bootstrap, completed, point, adoption = True, saved_bootstrap["completed"], True, candidate != component
            retain("bootstrap", cache._path("bootstrap", candidate), component,
                {"version": cache.VERSION, "identity": candidate, "payload": saved_bootstrap,
                 "payloadSha256": digest(saved_bootstrap)}, adoption=adoption)
        summary.append({"goal": "sssplus", "source": item["source"], "mode": item["mode"],
            "target": 995000, "samples": SAMPLES, "seed": SEED, "componentIdentity": digest(component),
            "fitObservations": len(data["y"]),
            "outerComplete": outer, "mainComplete": main, "bootstrapCheckpoint": bootstrap,
            "bootstrapCompleted": SAMPLES if main else completed, "pointContext": point,
            "adoptionRequired": adoption, "adoptable": adoption,
            "remainingInitialSolves": 0 if outer or main or bootstrap or not len(data["y"]) else 8,
            "remainingBootstrapAttempts": 0 if outer or main or not len(data["y"]) else SAMPLES - completed,
            "pointReplayPossible": bool(outer and not point), "warnings": sorted(set(warnings))})
    return {"version": 1, "generation": generation, "producer": RETAINED_PRODUCER,
            "cohorts": summary}, artifacts


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
            or state["identity"].get("pipelineCode") != pipeline_code_identity(_retained_state(state))
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
            or not isinstance(stages, list) or len(stages) > len(_finish_stages(state))):
        raise TierComputationCacheError("The tier generation checkpoint chain is stale or corrupt.")
    for index, saved in enumerate(stages):
        stage = _finish_stages(state)[index]
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


def _width_overlay_identity(state, predecessor):
    from numeric_recipe import source_recipe
    return digest({"generation": state["generation"], "stage": "level20-width", "version": WIDTH_VERSION,
                   "recipe": source_recipe("level20_scoring_width.py"),
                   "predecessorSha256": predecessor["payloadSha256"]})


def _latest_publication_reference(state):
    return (state.get("widthOverlay") or state.get("tailOverlay") or state.get("goalOverlay")
            or (state["stages"][-1] if state.get("stages") else state["general"]))


def _validate_publication_overlay_chain(state):
    from tier_computation_cache import TierComputationCacheError
    if not state.get("stages"):
        return
    reference = state["stages"][-1]
    if _retained_state(state):
        if reference.get("schemaVersion") == APPROVED_SCHEMA:
            if "archive" in state and state["archive"] != reference:
                raise TierComputationCacheError("The approved archive differs from its final rating checkpoint.")
            return
        if any(name in state for name in ("goalOverlay", "tailOverlay")):
            raise TierComputationCacheError("A retained producer contains a retired overlay.")
        saved = state.get("widthOverlay")
        if saved is not None:
            if (not isinstance(saved, dict) or saved.get("stage") != "level20-width"
                    or saved.get("schemaVersion") != 61
                    or saved.get("identity") != _width_overlay_identity(state, reference)
                    or saved.get("predecessorSha256") != reference["payloadSha256"]):
                raise TierComputationCacheError("The retained width checkpoint chain differs.")
            reference = saved
        if "archive" in state and (saved is None or state["archive"] != reference):
            raise TierComputationCacheError("The retained archive does not reference its final width.")
        return
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
    if "widthOverlay" in state:
        saved = state["widthOverlay"]
        if (not state.get("tailOverlay") or not isinstance(saved, dict)
                or saved.get("stage") != "level20-width" or saved.get("schemaVersion") != 60
                or saved.get("identity") != _width_overlay_identity(state, reference)
                or saved.get("predecessorSha256") != reference["payloadSha256"]):
            raise TierComputationCacheError("The level-20 width overlay checkpoint chain differs.")
        reference = saved
    if "archive" in state and (not state.get("tailOverlay") or state["archive"] != reference):
        raise TierComputationCacheError("The tier archive does not reference its final publication overlay.")


def _publication_overlays(store, state, reference, *, on_progress=None, on_stage_start=None):
    """Persist each additive overlay so retries never taper an output twice."""
    import gc
    from scoring_goal_tiers import apply_goal_artifacts
    from sss_tail_tiers import apply_sss_tail_tiers
    from level20_scoring_width import apply_level20_scoring_width
    from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
    _validate_publication_overlay_chain(state)
    if _retained_state(state):
        if reference.get("schemaVersion") == APPROVED_SCHEMA:
            return reference
        if "widthOverlay" not in state:
            from level20_scoring_width import apply_retained_level20_scoring_width
            full = read_checkpoint(store, reference, stage=reference["stage"], identity=reference["identity"])
            full = apply_retained_level20_scoring_width(full)
            state["widthOverlay"] = write_checkpoint(store, full, stage="level20-width",
                identity=_width_overlay_identity(state, reference), predecessor=reference["payloadSha256"])
            store.put_json(PREFIX + "generations/" + state["generation"] + "/state.json", state)
        return state["widthOverlay"]
    _advance_required_goals(store, state, on_progress=on_progress, on_stage_start=on_stage_start)
    state_path = PREFIX + "generations/" + state["generation"] + "/state.json"
    if REQUIRED_REFRESH_GOALS and state.get("goalArtifacts"):
        if "goalOverlay" not in state:
            full = read_checkpoint(store, reference, stage=reference["stage"], identity=reference["identity"])
            artifacts = {goal: read_checkpoint(store, ref, stage="scoring-goal", identity=ref["identity"])
                         for goal, ref in state["goalArtifacts"].items()}
            full = apply_goal_artifacts(full, artifacts, expected_input_identity=state["goalInputIdentity"])
            state["goalOverlay"] = write_checkpoint(store, full, stage="goal-overlays",
                identity=digest({"generation": state["generation"], "artifacts": state["goalArtifacts"]}),
                predecessor=reference["payloadSha256"])
            store.put_json(state_path, state)
            # The next read inflates the entire schema-58 checkpoint again.
            # Drop this decoded tree and its SS/SS+ inputs before that allocation.
            del full, artifacts
            gc.collect()
        reference = state["goalOverlay"]
    if "tailOverlay" not in state:
        full = read_checkpoint(store, reference, stage=reference["stage"], identity=reference["identity"])
        full = apply_sss_tail_tiers(full)
        state["tailOverlay"] = write_checkpoint(store, full, stage="sss-tail",
            identity=_tail_overlay_identity(state, reference), predecessor=reference["payloadSha256"])
        store.put_json(state_path, state)
        # Release the decoded schema-59 audit before inflating it for the next
        # additive stage, just as for the optional scoring-goal layer above.
        del full
        gc.collect()
    reference = state["tailOverlay"]
    if "widthOverlay" not in state:
        full = read_checkpoint(store, reference, stage=reference["stage"], identity=reference["identity"])
        full = apply_level20_scoring_width(full)
        state["widthOverlay"] = write_checkpoint(store, full, stage="level20-width",
            identity=_width_overlay_identity(state, reference), predecessor=reference["payloadSha256"])
        store.put_json(state_path, state)
    return state["widthOverlay"]


def resume_tier_generation(store, generation, *, generated_at_utc, on_progress=None,
                           on_stage_start=None):
    """Resume a pinned generation before reconstructing any raw source context."""
    state = _captured_computation(store, generation)
    if _retained_state(state):
        on_progress = _retained_progress(on_progress)
    if (state["createdAtUtc"] != generated_at_utc
            or not (state.get("phaseInputs") or state.get("computationGeneration"))):
        raise ValueError("The pinned tier generation has incompatible time or phase inputs.")
    if state.get("preparationVersion") and not state.get("fitsReady"):
        return _advance_preparation(store, state, on_progress=on_progress, on_stage_start=on_stage_start)
    if state.get("preparedFitInputs") and not state.get("fitsReady"):
        if _preparation_ordinal(state) < _preparation_total(state):
            return _advance_preparation(store, state, on_progress=on_progress, on_stage_start=on_stage_start)
        return _advance_prepared_fits(store, state, on_progress=on_progress, on_stage_start=on_stage_start)
    return _advance_finish(store, state, on_progress=on_progress, on_stage_start=on_stage_start)


def _optional_computation(store, generation, identity):
    from tier_computation_cache import TierComputationCacheError
    from tier_pipeline_checkpoints import verified_checkpoint_bytes
    original = _captured_computation(store, generation, identity)
    if not original.get("phaseInputs"):
        return None
    reference = _latest_publication_reference(original)
    verified_checkpoint_bytes(store, reference, stage=reference["stage"], identity=reference["identity"])
    if original.get("ready"):
        public = store.get_json(PREFIX + "generations/" + generation + "/public.json")
        try:
            validate_public_tiers(public)
            publication = public["summary"]["method"]["productionTierPublication"]
            if (len(original["stages"]) != len(_finish_stages(original))
                    or publication["inputs"] != original["identity"]
                    or digest(public) != original["ready"]["payloadSha256"]
                    or publication["fullArtifactPath"] != reference["path"]
                    or publication["fullArtifactSha256"] != reference["payloadSha256"]
                    or (_retained_state(original)
                        and publication.get("computationIdentity") != digest(identity))):
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
            or state.get("computationGeneration") or len(state.get("stages", [])) != len(_finish_stages(state))
            or not isinstance(state.get("stageCodeIdentities"), dict)
            or not isinstance(state.get("createdAtUtc"), str) or not state.get("phaseInputs")):
        raise TierComputationCacheError("The completed numerical producer is incompatible.")
    predecessor = state.get("general")
    if not isinstance(predecessor, dict) or predecessor.get("stage") != "general":
        raise TierComputationCacheError("The numerical predecessor chain is missing General.")
    for stage, reference in zip(_finish_stages(state), state["stages"]):
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
    verified_checkpoint_bytes(store, predecessor, stage=_finish_stages(state)[-1], identity=predecessor["identity"])
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
        state["archive"] = _publication_overlays(store, state, reference,
            on_progress=on_progress, on_stage_start=on_stage_start)
        store.put_json(state_path, state)
        ordinal = _prepared_ordinal(state) + len(_finish_stages(state)) + 1
        _report_progress(on_progress, generation, ordinal, "archive")
        raise TierAnalysisPending(generation[:16] + "-" + str(ordinal), _fit_count(state), stage="archive",
                                  generation=generation, progress_ordinal=ordinal, fit_count=_fit_count(state))
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
    _index_preparation(store, state)
    _report_progress(on_progress, generation, _prepared_ordinal(state) + len(_finish_stages(state)) + 2, "public")
    return compact


def _advance_reused_computation(store, state, *, on_stage_start=None, on_progress=None,
                               verified_computation=None):
    if state.get("completedNumericReuse"):
        return _advance_completed_numeric(store, state, verified_computation=verified_computation,
                                          on_stage_start=on_stage_start, on_progress=on_progress)
    from tier_pipeline_checkpoints import verified_checkpoint_bytes
    original = verified_computation or _captured_computation(
        store, state["computationGeneration"], state["computationIdentity"])
    verified_reference = (_latest_publication_reference(original)["blobSha256"]
                          if verified_computation and original["stages"] else None)
    if original["createdAtUtc"] != state.get("computationCreatedAtUtc"):
        raise ValueError("The reused tier computation time differs.")

    def progress(value):
        if on_progress:
            on_progress({**value, "generation": state["generation"]})

    try:
        computed = _advance_finish(store, original, on_stage_start=on_stage_start, on_progress=progress)
    except TierAnalysisPending as pending:
        sequence = state["generation"][:16] + pending.sequence[len(original["generation"][:16]):]
        raise TierAnalysisPending(sequence,
            pending.completed, stage=pending.stage, generation=state["generation"],
            progress_ordinal=pending.progress_ordinal, fit_count=pending.fit_count) from None
    original = _captured_computation(store, original["generation"], state["computationIdentity"])
    publication = computed["summary"]["method"]["productionTierPublication"]
    if (publication["inputs"] != original["identity"]
            or (original.get("ready") and digest(computed) != original["ready"]["payloadSha256"])):
        raise ValueError("The reused tier publication differs from its computed generation.")
    # The transition-validated canonical content is unchanged if its compressed
    # bytes match. Verify those bytes without replaying or inflating the full tree.
    reference = _latest_publication_reference(original)
    if reference["blobSha256"] != verified_reference:
        verified_checkpoint_bytes(store, reference, stage=reference["stage"], identity=reference["identity"])
    compact = {**computed, "generatedAtUtc": state["createdAtUtc"],
               "summary": {**computed["summary"], "generatedAtUtc": state["createdAtUtc"],
                           "method": dict(computed["summary"]["method"])}}
    if computed.get("tierListView", {}).get("kind") == "list":
        from tier_public import tier_detail_generated_at
        details_at = tier_detail_generated_at(computed)
        if details_at is None:
            raise ValueError("The reused chart detail generation is unavailable.")
        compact["tierListView"] = {**computed["tierListView"], "detailsGeneratedAtUtc": details_at}
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
    _index_preparation(store, state)
    _report_progress(on_progress, state["generation"], _prepared_ordinal(state) + len(_finish_stages(state)) + 2, "public")
    return compact


def _advance_finish(store, state, *, on_stage_start=None, on_progress=None, verified_computation=None):
    from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
    from tier_diagnostics import event, phase
    if (state.get("preparationVersion") or state.get("preparedFitInputs")) and not state.get("fitsReady"):
        return _advance_preparation(store, state, on_stage_start=on_stage_start, on_progress=on_progress)
    generation, identity, created = state["generation"], state["identity"], state["createdAtUtc"]
    state_path = PREFIX+"generations/"+generation+"/state.json"
    public_path = PREFIX+"generations/"+generation+"/public.json"
    cached = store.get_json(public_path)
    if cached is not None:
        validate_public_tiers(cached)
        _report_progress(on_progress, generation, _prepared_ordinal(state) + len(_finish_stages(state)) + 2, "public")
        return cached
    if state.get("computationGeneration"):
        return _advance_reused_computation(store, state, on_stage_start=on_stage_start,
                                          on_progress=on_progress, verified_computation=verified_computation)
    general_reference = state["general"]
    general_identity = general_reference["identity"]
    reference = general_reference
    if len(state["stages"]) > len(_finish_stages(state)):
        raise ValueError("The tier generation contains an unknown completed phase.")
    for index, saved in enumerate(state["stages"]):
        stage = _finish_stages(state)[index]
        expected = _stage_identity(generation, stage, reference["payloadSha256"], created)
        if (saved.get("stage") != stage or saved.get("identity") != expected
                or saved.get("predecessorSha256") != reference["payloadSha256"]
                or saved.get("schemaVersion") != STAGE_SCHEMAS[stage]):
            raise ValueError("The tier generation checkpoint chain is stale or corrupt.")
        reference = saved
    completed_stages = len(state["stages"])
    if not state.get("requiredGoalJobs") or "archive" in state:
        _report_progress(on_progress, generation,
            _prepared_ordinal(state) + completed_stages + int("archive" in state),
            "archive" if "archive" in state else (_finish_stages(state)[completed_stages-1] if completed_stages else "prepared-inputs"))
    if completed_stages < len(_finish_stages(state)):
        stage = _finish_stages(state)[completed_stages]
        if on_stage_start: on_stage_start(stage)
        state["activeStage"] = stage
        store.put_json(state_path, state)
        with phase(stage, generation=generation):
            context, fits, identities = _load_phase_inputs(store, state, stage)
            predecessor = read_checkpoint(store, reference,
                stage="general" if completed_stages == 0 else _finish_stages(state)[completed_stages-1],
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
        if len(state["stages"]) == len(_finish_stages(state)) and state.get("numericIdentity"):
            from tier_computation_cache import write_index
            write_index(store, "numeric-pipeline", state["numericIdentity"], generation=generation,
                        reference=checkpoint)
        _report_progress(on_progress, generation, _prepared_ordinal(state) + completed_stages + 1, stage)
        event("tier_checkpoint_saved", generation=generation, stage=stage,
              artifact_bytes=checkpoint["compressedBytes"], output_hash=checkpoint["payloadSha256"])
        ordinal = _prepared_ordinal(state) + completed_stages + 1
        raise TierAnalysisPending(generation[:16]+"-"+str(ordinal), _fit_count(state), stage=stage,
                                  generation=generation, progress_ordinal=ordinal, fit_count=_fit_count(state))
    if "archive" not in state:
        if on_stage_start: on_stage_start("archive")
        state["archive"] = _publication_overlays(store, state, reference,
            on_progress=on_progress, on_stage_start=on_stage_start)
        store.put_json(state_path, state)
        ordinal = _prepared_ordinal(state) + len(_finish_stages(state)) + 1
        _report_progress(on_progress, generation, ordinal, "archive")
        raise TierAnalysisPending(generation[:16]+"-"+str(ordinal), _fit_count(state), stage="archive",
                                  generation=generation, progress_ordinal=ordinal, fit_count=_fit_count(state))
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
    _index_preparation(store, state)
    _report_progress(on_progress, generation, _prepared_ordinal(state) + len(_finish_stages(state)) + 2, "public")
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
    from tier_pipeline_checkpoints import write_checkpoint
    return {name: write_checkpoint(store, _phase_input_payload(context, name), stage="phase-inputs-"+name,
                identity=digest({"generation": generation, "kind": name}))
            for name in PHASE_INPUT_NAMES}


def _phase_input_payload(context, name):
    from sss_scoring import payload_sha256
    first, second, catalog, policy, merged, prepared = context
    if name == "model":
        return {"prepared": prepared}
    hashes = {"phoenix1": payload_sha256(first), "phoenix2": payload_sha256(second),
              "currentCatalog": payload_sha256(catalog), "officialManifest": merged["manifestSha256"]}
    official = {**merged, "_releaseSourceHashes": hashes}
    small = {key: value for key, value in official.items() if key in
        {"manifestSha256", "effectiveInputSha256", "sourceDates", "_releaseSourceHashes"}}
    small["charts"] = {cid: chart for cid, chart in merged.get("charts", {}).items() if chart.get("sourceExclusions")}
    return {"official": {"merged": official},
            "provenance": {"policy": policy.public(), "merged": small}}[name]


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
    if stage in ("official-clearing", "centered-sss", "spread", "combined-clearing", "extended-clearing"):
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
               "metadata": metadata, "generatedAtUtc": generated_at_utc,
               "producer": RETAINED_PRODUCER, "pipelineCode": pipeline_code_identity(True)}
    return write_checkpoint(store, payload, stage="pinned-inputs", identity=digest(payload))


def advance_tier_publication(store, state, *, on_stage_start=None):
    """Worker adapter: one fit/finish phase, a compact result, and no promotion."""
    from tier_pipeline_checkpoints import read_checkpoint
    current_code = pipeline_code_identity(True)
    created = state["generatedAtUtc"]
    if state.get("generation") is not None:
        generation = state["generation"]
        captured = store.get_json(PREFIX+"generations/"+generation+"/state.json")
        if (captured is None or captured.get("generation") != generation or digest(captured["identity"]) != generation
                or captured["identity"].get("pipelineCode") != current_code
                or not _retained_state(captured)
                or captured.get("createdAtUtc") != created
                or captured.get("pinnedInputsSha256") != state["inputs"]["payloadSha256"]):
            raise ValueError("The pinned tier generation has incompatible code, time or inputs.")
        if (len(captured.get("phaseInputs", {})) == len(PHASE_INPUT_NAMES)
                or captured.get("computationGeneration")):
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
    if inputs.get("producer") != RETAINED_PRODUCER:
        raise ValueError("The pinned tier job uses a retired producer; start a current generation.")
    if inputs.get("pipelineCode") != current_code:
        raise ValueError("The pinned tier job uses different pipeline code; start a compatible generation.")
    try:
        candidate = build_retained_hosted_tiers(inputs["records"], inputs["metadata"], inputs["first"], inputs["second"],
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


def validate_current_tier_publication(candidate):
    """The tier-only worker can promote only the current retained producer."""
    method = candidate.get("summary", {}).get("method", {})
    publication = method.get("productionTierPublication", {})
    if (candidate.get("schemaVersion") != APPROVED_SCHEMA
            or method.get("retainedGoalProducer", {}).get("producer") != RETAINED_PRODUCER
            or publication.get("method") != APPROVED_VERSION
            or publication.get("inputs", {}).get("producer") != RETAINED_PRODUCER):
        raise ValueError("Tier publication requires the approved schema-162 retained producer.")
    return publication


def _ready_worker_result(state, candidate):
    publication = validate_current_tier_publication(candidate)
    generation = digest(publication["inputs"])
    total = 4 + 1 + len(RETAINED_FINISH_STAGES) + 2
    ready = {"done": True, "stage": "ready", "generation": generation,
             "producer": RETAINED_PRODUCER, "schemaVersion": APPROVED_SCHEMA,
             "completedTargetFits": 4,
             "progressOrdinal": total, "totalStages": total,
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


def _advance_required_goals(store, state, *, on_progress=None, on_stage_start=None):
    """Complete one durable SS/SS+ work unit before allowing publication.

    Reuse the generation's immutable prepared observations. Each continuation
    pins one cohort, fits one cohort, or assembles one goal artifact; raw score
    snapshots are never reconstructed here.
    """
    if not REQUIRED_REFRESH_GOALS:
        return
    from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
    from tier_computation_cache import write_index
    from target_fit_cache import pending_fit_progress, TargetFitPending

    artifacts = state.setdefault("goalArtifacts", {})
    jobs = state.setdefault("requiredGoalJobs", {})
    generation = state["generation"]
    state_path = PREFIX + "generations/" + generation + "/state.json"
    ordinal = 9 + len(FINISH_STAGES)

    def progress(inner=None):
        units = sum(9 if goal in artifacts else len(jobs.get(goal, {}).get("cohorts", []))
                    + len(jobs.get(goal, {}).get("fits", [])) for goal in REQUIRED_REFRESH_GOALS)
        if on_progress and units:
            value = {"version": 1, "generation": generation, "ordinal": ordinal,
                "stage": "scoring-goals", "goalProgressOrdinal": units}
            if inner is not None:
                value["fitProgress"] = inner
            on_progress(value)
        return units

    def committed():
        store.put_json(state_path, state)
        units = progress()
        raise TierAnalysisPending(generation[:16] + "-goals-" + str(units), 8,
            stage="scoring-goals", generation=generation, progress_ordinal=ordinal)

    def phase_input(name):
        reference = state["phaseInputs"][name]
        producer = state.get("computationGeneration", generation)
        return read_checkpoint(store, reference, stage="phase-inputs-" + name,
            identity=digest({"generation": producer, "kind": name}))

    for goal in REQUIRED_REFRESH_GOALS:
        if goal in artifacts:
            continue
        recipe = _goal_recipe(goal)
        job = jobs.setdefault(goal, {"recipe": recipe, "cohorts": [], "fits": []})
        if job.get("recipe") != recipe:
            raise ValueError("The required scoring-goal recipe changed during analysis.")
        fit_generation = digest({"generation": generation, "goal": goal, "recipe": recipe})
        cohorts, fits = job["cohorts"], job["fits"]
        expected = [(source, mode) for source in ("official", "submitted") for mode, _ in MODES]
        if (len(cohorts) > 4 or len(fits) > len(cohorts)
                or [(item.get("source"), item.get("mode")) for item in cohorts] != expected[:len(cohorts)]
                or any(item.get("goal") != goal for item in cohorts)):
            raise ValueError("The required scoring-goal cohort progress is invalid.")
        inner = None
        if len(cohorts) == 4 and len(fits) < 4:
            item = cohorts[len(fits)]
            cached = store.get_json(fit_path(item["identity"]))
            if cached is not None:
                if cached.get("identity") != item["identity"]:
                    raise ValueError("The recovered scoring-goal fit has stale input identity.")
                validate_fit(cached["fit"], item["identity"])
                fits.append({**{key: item[key] for key in
                    ("goal", "source", "mode", "identity", "reusedOriginal")},
                    "path": fit_path(item["identity"]), "fitSha256": digest(cached["fit"])})
                # Recover the fit/manifest crash gap before the runtime audits
                # the older cohort count. No numerical solve is repeated.
                committed()
            payload = read_checkpoint(store, item["inputs"], stage="cohort-inputs",
                identity=digest({"generation": fit_generation, "goal": goal,
                    "source": item["source"], "mode": item["mode"], "identity": item["identity"]}))
            inner = pending_fit_progress(store, payload["boards"], score_target=item["identity"]["target"],
                bootstrap_samples=SAMPLES, seed=SEED, source_boards=payload["sources"])
        progress(inner)
        if on_stage_start:
            on_stage_start("scoring-goals")
        _check_preparation_budget("scoring-goals")
        if len(cohorts) < 4:
            prepared = phase_input("model")["prepared"]
            source, mode = expected[len(cohorts)]
            boards, sources, identity = cohort(prepared, goal, source, mode)
            reference = write_checkpoint(store, {"boards": boards, "sources": sources},
                stage="cohort-inputs", identity=digest({"generation": fit_generation, "goal": goal,
                    "source": source, "mode": mode, "identity": identity}))
            cohorts.append({"goal": goal, "source": source, "mode": mode, "identity": identity,
                            "inputs": reference, "reusedOriginal": False})
            committed()
        if len(fits) < 4:
            try:
                reference, _ = _build_goal_fit(store, cohorts[len(fits)], fit_generation)
            except TargetFitPending as pending:
                recovered = pending_fit_progress(store, payload["boards"], score_target=item["identity"]["target"],
                    bootstrap_samples=SAMPLES, seed=SEED, source_boards=payload["sources"])
                if (recovered is None or recovered["identity"] != pending.progress["identity"]
                        or recovered["ordinal"] < pending.progress["ordinal"]):
                    raise ValueError("The pending scoring-goal fit has no matching durable progress.")
                units = progress(recovered)
                raise _pending_inner_fit(generation, ordinal, recovered, goal_units=units) from None
            fits.append(reference)
            committed()
        prepared = phase_input("model")["prepared"]
        provenance = phase_input("provenance")
        reference = _store_goal_artifact(store, goal=goal, inputs=state["goalInputIdentity"],
            prepared=prepared, policy=provenance["policy"],
            policy_semantic_sha256=state["identity"]["sourcePolicy"],
            source_hashes=state["goalSourceHashes"],
            manifest_sha256=provenance["merged"]["manifestSha256"],
            fit_references=fits, created_at_utc=state["createdAtUtc"])
        artifacts[goal] = reference
        state["computationIdentity"]["goalArtifacts"] = {
            key: value["payloadSha256"] for key, value in artifacts.items()}
        if all(key in artifacts for key in REQUIRED_REFRESH_GOALS):
            write_index(store, "pipeline", state["computationIdentity"], generation=generation)
        committed()


def _store_goal_artifact(store, *, goal, inputs, prepared, policy, policy_semantic_sha256,
                         source_hashes, manifest_sha256, fit_references, created_at_utc):
    """Share the exact operator-backfill recipe with normal refreshes."""
    from scoring_goal_tiers import build_goal_artifact
    from target_fit_cache import hydrate_point_context
    from tier_pipeline_checkpoints import write_checkpoint
    fits = {"official": {}, "submitted": {}}
    for item in fit_references:
        cached = store.get_json(item["path"])
        if cached is None or cached.get("identity") != item["identity"] or digest(cached.get("fit")) != item["fitSha256"]:
            raise ValueError("A pinned scoring-goal fit changed during calibration.")
        boards, _, _ = cohort(prepared, goal, item["source"], item["mode"])
        hydrate_point_context(store, boards, cached["fit"], score_target=item["identity"]["target"])
        fits[item["source"]][item["mode"]] = cached["fit"]
    context = (None, None, None,
        SimpleNamespace(public=lambda: policy, semantic_hash=lambda: policy_semantic_sha256),
        {"manifestSha256": manifest_sha256}, prepared)
    artifact = build_goal_artifact(context, fits, fit_references, created_at_utc, goal=goal,
                                   source_hashes=source_hashes)
    artifact["inputIdentity"] = inputs
    recipe = _goal_recipe(goal)
    reference = write_checkpoint(store, artifact, stage="scoring-goal", identity=digest({"inputs": inputs, "recipe": recipe}))
    store.put_json(_goal_index_path(inputs, goal), {"version": 1, "recipe": recipe,
                   "inputIdentity": inputs, "reference": reference})
    return reference


def advance_goal_backfill(store, *, pinned_inputs_reference, goal, on_progress=None, max_fit_workers=1):
    """Compute at most one missing cohort; save artifacts without moving pointers."""
    from scoring_targets import BACKFILL_GOALS
    from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint
    from official_scoring_sources import canonical_scoring_manifest_sha256
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
    reference = _store_goal_artifact(store, goal=goal, inputs=inputs, prepared=captured["prepared"],
        policy=captured["policy"], policy_semantic_sha256=captured["policySemanticSha256"],
        source_hashes=captured["sourceHashes"], manifest_sha256=captured["merged"].get("manifestSha256"),
        fit_references=state["fits"], created_at_utc=state["createdAtUtc"])
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
