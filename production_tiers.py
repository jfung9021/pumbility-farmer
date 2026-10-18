"""Hosted publication of the approved source-aware Scoring/Clearing generation.

Fits are private, content-addressed, and checkpointed one cohort per invocation.
The public view omits historical rollback copies; the complete audit stays private.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

VERSION = "6.21.0-submitted-sssplus"
PREVIOUS_VERSION = "6.20.0-source-aware-sssplus"
PREFIX = "analysis/private/source-aware-tiers/"
SEED = 20261018
SAMPLES = 200
MODES = (("Single", "singles"), ("Double", "doubles"))


class TierAnalysisPending(RuntimeError):
    def __init__(self, sequence: str, completed: int):
        super().__init__(f"Scoring model {completed}/8 checkpointed; continuing tier analysis.")
        self.sequence, self.completed = sequence, completed


def digest(value: Any) -> str:
    from canonical_json import canonical_json_sha256
    return canonical_json_sha256(value)


def model_code_identity() -> str:
    root = Path(__file__).resolve().parent
    return digest({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("sss_scoring_model.py", "official_player_scoring.py", "scoring_targets.py")})


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
        store.put_bytes(path, staged.read(), content_type="application/gzip")
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


def finish_generation(general, context, fits, identities):
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
    hashes = {"phoenix1": payload_sha256(first), "phoenix2": payload_sha256(second),
        "currentCatalog": payload_sha256(catalog), "officialManifest": merged["manifestSha256"]}
    result = apply_sss_variants(general, prepared, fits["sss"], source_hashes=hashes, bootstrap_samples=SAMPLES, seed=SEED)
    result, _ = apply_refined_tiers(result, prepared, fits["sss"], merged["_releasePhoenix2Capture"])
    result = apply_folder_centering(result)
    result, _ = apply_sssplus_tiers(result, prepared, fits["sssplus"], source_hashes=hashes)
    result = apply_median_calibration(result, policy_version=3)
    boards = {mode: {cid: board for cid, board in merged["boards"].items()
        if cid in {str(r["chartId"]) for r in result[key] if r["level"] >= 20}} for mode, key in MODES}
    adjustments, rare = build_rare_adjustments(result, boards, fits["sssplus"]["official"])
    result = apply_sssplus_spread(result, rare_adjustments=adjustments, rare_policy=rare)
    result, _ = apply_combined_official_clearing(result, merged, bootstrap_samples=1000, cap_policy=P1_OBSERVED_CAP_POLICY)
    exclusions = {cid: {"rules": meta["sourceExclusions"], "capturedSourceStates": meta["capturedSourceStates"],
        "effectiveSourceStates": meta["sourceStates"]} for cid, meta in merged["charts"].items() if meta.get("sourceExclusions")}
    provenance = {**policy.public(), "manifestSha256": merged["manifestSha256"],
        "effectiveInputSha256": merged["effectiveInputSha256"], "sourceDates": merged["sourceDates"],
        "excludedCharts": exclusions, "targetFits": identities, "correctedSourceHashes": hashes}
    result = apply_source_aware_tiers(result, result, provenance)
    from submitted_sssplus_calibration import apply_submitted_sssplus_calibration
    submitted_identity = next(item["identity"] for item in identities
        if (item["goal"], item["source"], item["mode"]) == ("sssplus", "submitted", "Double"))
    return apply_submitted_sssplus_calibration(result, fits["sssplus"]["submitted"]["Double"],
        fit_identity=submitted_identity, local_only=False)


def build_hosted_tiers(records, metadata, first, second, *, store, arguments, generated_at_utc=None):
    """Resume cached fits; never fetch scores or rebuild recommendation models."""
    from scoring_percentile import build_production_tier_payload
    from sss_scoring_model import fit_target_model
    from official_scoring_sources import canonical_scoring_manifest_sha256
    manifest = arguments["official_scoring_manifest"]
    context = load_context(first, second, manifest, records)
    p1, p2, catalog, policy, merged, prepared = context
    merged["_releasePhoenix2Capture"] = manifest["_captures"]["Phoenix2"]
    identity = {"version": VERSION, "phoenix1": digest(first), "phoenix2": digest(second),
        "catalog": digest(catalog), "officialManifest": canonical_scoring_manifest_sha256(manifest),
        "sourcePolicy": policy.semantic_hash(), "modelCode": model_code_identity(),
        "generationTimestamp": generated_at_utc}
    generation = digest(identity)
    public_path = PREFIX + "generations/" + generation + "/public.json"
    cached = store.get_json(public_path)
    if cached is not None:
        validate_public_tiers(cached)
        return cached
    general_path = PREFIX + "generations/" + generation + "/general.json"
    general = store.get_json(general_path)
    if general is None:
        # This release changes the final submitted SSS+ scale only. The previous
        # generation's General fit has the same exact source and model identities.
        previous = digest({**identity, "version": PREVIOUS_VERSION})
        general = store.get_json(PREFIX + "generations/" + previous + "/general.json")
    if general is None:
        general = build_production_tier_payload(records, metadata, p1, p2,
            generated_at_utc=generated_at_utc, source_exclusions=policy, **arguments)
        store.put_json(general_path, general)
    fits = {goal: {source: {} for source in ("official", "submitted")} for goal in ("sss", "sssplus")}
    identities, completed = [], 0
    for goal in fits:
        for source in fits[goal]:
            for mode, _ in MODES:
                boards, sources, fit_identity = cohort(prepared, goal, source, mode)
                path = fit_path(fit_identity)
                cached_fit = store.get_json(path)
                if cached_fit is None:
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
                    raise TierAnalysisPending(generation[:16] + "-" + str(completed + 1), completed + 1)
                if cached_fit["identity"] != fit_identity:
                    raise ValueError("The private target fit has stale inputs.")
                fit = cached_fit["fit"]
                validate_fit(fit, fit_identity)
                fits[goal][source][mode] = fit
                identities.append({"goal": goal, "source": source, "mode": mode, "identity": fit_identity, "reusedOriginal": False})
                completed += 1
    full = finish_generation(general, context, fits, identities)
    if generated_at_utc is not None:
        full["generatedAtUtc"] = full["summary"]["generatedAtUtc"] = generated_at_utc
    full_path = PREFIX + "generations/" + generation + "/full.json.gz"
    full_sha = write_private_audit(store, full_path, full)
    public = public_tiers(full, private_path=full_path, input_identity=identity, full_sha256=full_sha)
    store.put_json(public_path, public)
    return public
