"""Local schema-52 SSS+ overlay, preserving the complete schema-51 candidate."""
from __future__ import annotations

import copy
import math
from statistics import median
from typing import Any, Mapping

from official_cross_level_calibration import inverse_chart_cost
from scoring_targets import SSSPLUS
from sss_folder_centering import centering_folders, shift_scoring, validate_folder_centering
from sss_scoring import (MODES, RANK_FIELDS, _composite, _derived, _target_metric,
                         fit_target_curve, payload_sha256, rank_variants, reference_anchors)
from sss_scoring_refinement import apply_sss_refinement, boundary_estimate
from target_tail_policy import (INTERVAL_METHOD as TAIL_INTERVAL_METHOD, apply_tail_policy,
                                finite, tail_value)
from tier_postprocessing import compress_difficulty, compression_factor

SCHEMA_VERSION = 52
GOAL = SSSPLUS.key
METHOD = "sssplus-target-with-bounded-tails-v1"
NAMESPACES = ("sssplusScoring", "sssplusPumbility", "sssplusRefinement", "sssplusFolderCentering",
              "sssplusTailPolicy", "sssplusTiers")
ROW_STAGES = ("sssplusRefinementBaseline", "sssplusCenteringBaseline", "sssplusTailBaseline")


def apply_first_pass(base: Mapping[str, Any], prepared: Mapping[str, Any], fits: Mapping[str, Any], *,
                     source_hashes: Mapping[str, str], bootstrap_samples: int, seed: int) -> dict:
    result = copy.deepcopy(base)
    result["schemaVersion"] = SCHEMA_VERSION
    calibrations, diagnostics = {}, {}
    for source, minimum in (("submitted", 16), ("official", 20)):
        calibrations[source], diagnostics[source] = {}, {}
        for mode, key in MODES:
            fit = fits[source][mode]
            if fit.get("policy", {}).get("scoreTarget") != SSSPLUS.score:
                raise ValueError("SSS+ first pass cannot use a historical SSS fit.")
            anchors, exclusions = reference_anchors(prepared["catalog"], prepared[source]["charts"], fit,
                                                     mode=mode, minimum_level=minimum)
            try:
                curve, error = fit_target_curve(anchors, minimum_level=minimum), None
            except ValueError as exc:
                curve, error = None, str(exc).replace("SSS calibration", "SSS+ calibration")
            calibrations[source][mode] = curve
            diagnostics[source][mode] = {"calibrationError": error, "referenceExclusions": exclusions,
                "referenceAnchors": copy.deepcopy(anchors), "model": copy.deepcopy(fit["diagnostics"]),
                "modelPolicy": copy.deepcopy(fit["policy"])}
            for row in result.get(key, []):
                if (row["level"] >= 20) != (source == "official"):
                    continue
                cid = str(row["chartId"])
                estimate = fit["charts"].get(cid)
                if estimate:
                    estimate = {**estimate, "fitConverged": bool(fit["diagnostics"].get("converged"))}
                metric = _target_metric(row, source, estimate, prepared[source]["charts"].get(cid, {}),
                                        curve, error, target=SSSPLUS, factor=compression_factor(base))
                row["scoringVariants"][GOAL] = metric
                row["pumbilityVariants"][GOAL] = _composite(row, metric)
    for _, key in MODES:
        rank_variants(result.get(key, []), goal=GOAL)
    result["summary"]["method"]["sssplusScoring"] = {
        "version": 1, "method": SSSPLUS.model_method, "target": SSSPLUS.score,
        "targetConfig": SSSPLUS.public(), "meaning": "conditional-target-attainment-among-observed-best-scores",
        "baseSchemaVersion": base["schemaVersion"], "basePayloadSha256": payload_sha256(base), "sourceHashes": dict(source_hashes),
        "sourceDates": copy.deepcopy(prepared.get("sourceDates", {})), "officialManifestSha256": prepared.get("manifestSha256"),
        "minimumPlayerCharts": 4, "minimumContributors": 5, "minimumReferenceContributors": 20,
        "minimumReferencePerOutcome": 5, "minimumReferenceCharts": 5,
        "weighting": "one-total-weight-per-player-in-source-mode", "bootstrapSamples": bootstrap_samples, "seed": seed,
        "intervalMethod": "player-cluster-refit-fixed-calibration-v1",
        "intervalExclusions": ["anchor-uncertainty", "clearing-uncertainty", "effort", "selective-observation"],
        "sourceBoundary": {"submitted": [16, 19], "officialMinimum": 20},
        "compression": {"factor": compression_factor(base), "reference": "current-official-level-plus-0.5", "applications": 1},
        "calibration": calibrations, "diagnostics": diagnostics}
    result["summary"]["method"]["sssplusPumbility"] = {"version": 1, "aggregation": "average", "requiresBoth": True,
        "calculation": "(final SSS+ scoring difficulty + unchanged clearing difficulty) / 2 before rounding",
        "componentValues": "after-midpoint-compression-centering-and-target-tail-policy",
        "intervalMethod": "player-bootstrap-fixed-clearing-and-calibration-v1",
        "evidence": "weaker component status; separate scoring and clearing support counts"}
    return result


def _recompute_means(payload: dict) -> None:
    for _, key in MODES:
        for row in payload[key]:
            metric = row["scoringVariants"][GOAL]
            composite = _composite(row, metric)
            if metric["scoringMethod"] != SSSPLUS.model_method:
                composite["intervalMethod"] = "unavailable-after-sss-refinement-v1"
            elif "scoringTailPolicy" in metric:
                composite["intervalMethod"] = "conditional-target-tail-fixed-clearing-and-calibration-v1"
            row["pumbilityVariants"][GOAL] = composite
        rank_variants(payload[key], goal=GOAL)


def apply_centering(base: Mapping[str, Any]) -> dict:
    result = copy.deepcopy(base)
    folders = centering_folders(base, goal=GOAL)
    policy = {"version": 1, "method": "target-final-folder-median-centering-v1", "baseSchemaVersion": 52,
        "basePayloadSha256": payload_sha256(base), "localOnly": True, "targetKey": GOAL,
        "source": "official", "minimumLevel": 20, "minimumMixedFirstPassCharts": 5, "minimumFinalCharts": 5,
        "population": "all-finite-final-target-excluding-unrated", "reference": "current-official-level-plus-0.5",
        "median": "middle-value-or-mean-of-middle-pair", "borrow": "same-source-mode-offset-interpolation-or-nearest",
        "noDonor": "preserve-point-zero-offset", "processing": "frozen-second-pass-preview-then-initial-shift-then-single-compression",
        "compressionFactor": compression_factor(base), "compressionApplications": 1,
        "intervals": "translate-existing-conditional-intervals-fixed-centering-no-new-intervals",
        "pumbility": "mean-centered-target-and-unchanged-clearing-no-composite-centering", "folders": folders}
    result["summary"]["method"]["sssplusFolderCentering"] = policy
    for mode, key in MODES:
        for row in result[key]:
            metric = row["scoringVariants"][GOAL]
            row[GOAL + "CenteringBaseline"] = {"scoring": copy.deepcopy(metric),
                "pumbility": copy.deepcopy(row["pumbilityVariants"][GOAL])}
            if row["level"] >= 20:
                frozen = base["summary"]["method"]["sssplusRefinement"]["ranges"]["official"][mode].get(str(row["level"]))
                shift_scoring(metric, row["level"], folders[mode][str(row["level"])], frozen, compression_factor(base))
    _recompute_means(result)
    return result


def recover_sssplus_baseline(payload: Mapping[str, Any]) -> dict:
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("SSS+ recovery requires schema 52.")
    result = copy.deepcopy(payload)
    result["schemaVersion"] = payload["summary"]["method"]["sssplusTiers"]["baseSchemaVersion"]
    for name in NAMESPACES:
        result["summary"]["method"].pop(name, None)
    for _, key in MODES:
        for row in result[key]:
            row["scoringVariants"].pop(GOAL)
            row["pumbilityVariants"].pop(GOAL)
            for stage in ROW_STAGES:
                row.pop(stage)
    return result


def apply_sssplus_tiers(base: Mapping[str, Any], prepared: Mapping[str, Any], fits: Mapping[str, Any], *,
                        source_hashes: Mapping[str, str], bootstrap_samples: int = 200,
                        seed: int = 20261018) -> tuple[dict, dict]:
    from retained_tiers import BASE_SCHEMA_VERSION, is_retained, validate_retained_baseline
    retained = is_retained(base)
    if base.get("schemaVersion") != (BASE_SCHEMA_VERSION if retained else 51):
        raise ValueError("SSS+ requires the frozen current centered schema-51 baseline.")
    (validate_retained_baseline if retained else validate_folder_centering)(base)
    first = apply_first_pass(base, prepared, fits, source_hashes=source_hashes, bootstrap_samples=bootstrap_samples, seed=seed)
    refined, refinement_audit = apply_sss_refinement(first, prepared, fits, seed=seed, target=SSSPLUS)
    for _, key in MODES:
        for row, original in zip(refined[key], first[key]):
            row[GOAL + "RefinementBaseline"] = {"sssPumbility": copy.deepcopy(original["pumbilityVariants"][GOAL])}
    _recompute_means(refined)
    candidate = apply_centering(refined)
    tail_policy = apply_tail_policy(candidate)
    candidate["summary"]["method"]["sssplusTailPolicy"] = tail_policy
    candidate["summary"]["method"]["sssplusTiers"] = {"version": 1, "method": METHOD, "baseSchemaVersion": base["schemaVersion"],
        "basePayloadSha256": payload_sha256(base), "localOnly": True, "targetConfig": SSSPLUS.public(),
        "stageOrder": ["target-fit", "median-anchors", "frozen-range-refinement", "folder-centering", "single-midpoint-compression", "target-tail-policy", "component-mean-and-ranks"],
        "compressionApplications": 1, "tailPolicyMethod": tail_policy["method"]}
    _recompute_means(candidate)
    validate_sssplus_tiers(candidate, base)
    report = {"schemaVersion": SCHEMA_VERSION, "targetConfig": SSSPLUS.public(),
        "basePayloadSha256": payload_sha256(base), "sourceHashes": dict(source_hashes),
        "dataFetches": 0, "targetCohortsRefitted": 4, "bootstrapSamplesPerCohort": bootstrap_samples,
        "coverage": {key: {"target": sum(row["scoringVariants"][GOAL]["estimatedDifficulty"] is not None for row in candidate[key]),
            "pumbility": sum(row["pumbilityVariants"][GOAL]["estimatedDifficulty"] is not None for row in candidate[key])} for _, key in MODES},
        "calibration": candidate["summary"]["method"]["sssplusScoring"]["calibration"],
        "diagnostics": candidate["summary"]["method"]["sssplusScoring"]["diagnostics"],
        "centering": candidate["summary"]["method"]["sssplusFolderCentering"]["folders"],
        "tail": tail_policy, "refinement": refinement_audit,
        "preservedSchema51Payload": True}
    return candidate, report


def validate_sssplus_tiers(payload: Mapping[str, Any], frozen: Mapping[str, Any] | None = None) -> dict:
    base = recover_sssplus_baseline(payload)
    if frozen is not None and base != frozen:
        raise ValueError("SSS+ overlay changed a frozen schema-51 field.")
    from retained_tiers import BASE_SCHEMA_VERSION, is_retained, validate_retained_baseline
    retained = is_retained(base)
    (validate_retained_baseline if retained else validate_folder_centering)(base)
    method = payload["summary"]["method"]
    scoring = method["sssplusScoring"]
    if (scoring.get("targetConfig") != SSSPLUS.public() or scoring.get("target") != 995000
            or scoring.get("method") != SSSPLUS.model_method
            or scoring.get("baseSchemaVersion") != (BASE_SCHEMA_VERSION if retained else 51)
            or scoring.get("basePayloadSha256") != payload_sha256(base)
            or method["sssplusTiers"].get("basePayloadSha256") != payload_sha256(base)):
        raise ValueError("SSS+ target identity or baseline differs.")
    pre_tail = copy.deepcopy(payload)
    for _, key in MODES:
        for row in pre_tail[key]:
            snapshot = row[GOAL + "TailBaseline"]
            row["scoringVariants"][GOAL] = snapshot["scoring"]
            row["pumbilityVariants"][GOAL] = snapshot["pumbility"]
    expected_tail = apply_tail_policy(pre_tail)
    if expected_tail != method["sssplusTailPolicy"]:
        raise ValueError("SSS+ tail selection, policy, population or counts differ.")
    _recompute_means(pre_tail)
    for _, key in MODES:
        for row, expected in zip(payload[key], pre_tail[key]):
            if row["scoringVariants"][GOAL] != expected["scoringVariants"][GOAL] or row["pumbilityVariants"][GOAL] != expected["pumbilityVariants"][GOAL]:
                raise ValueError("SSS+ tail points, intervals, composites or ranks differ.")
            metric, level = row["scoringVariants"][GOAL], row["level"]
            before = row[GOAL + "TailBaseline"]["scoring"]
            if metric["scoringTarget"] != 995000 or metric["nContributors"] != metric["scoringTargetSuccesses"] + metric["scoringBelowTarget"]:
                raise ValueError("SSS+ target or support counts differ.")
            if metric["scoringSource"] != ("official" if level >= 20 else "submitted"):
                raise ValueError("SSS+ source boundary differs.")
            for field in ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"):
                if before[field] != compress_difficulty(before["initialFit"][field], level, compression_factor(payload)):
                    raise ValueError("SSS+ pre-tail metric was not compressed exactly once.")
            if metric["estimatedDifficulty"] is None:
                if metric["evidenceStatus"] != "Unrated" or not metric.get("scoringUnratedReason"):
                    raise ValueError("Unavailable SSS+ value lacks its Unrated reason.")
            elif abs(metric["estimatedDifficulty"] - level - .5) > 2.5:
                raise ValueError("SSS+ point exceeds the absolute tail ceiling.")
    counts = expected_tail["counts"]
    if counts["finalAbsAtLeast2"] > 10 or counts["displayedAbsAtLeast2"] > 10 or counts["finalAbsAbove2_5"]:
        raise ValueError("SSS+ tail rarity budget was exceeded.")
    for mode, key in MODES:
        for level, folder in method["sssplusFolderCentering"]["folders"][mode].items():
            rows = [row for row in payload[key] if row["level"] == int(level) and finite(row["scoringVariants"][GOAL]["estimatedDifficulty"])]
            if rows and folder["basis"] == "folder" and not math.isclose(median(row["scoringVariants"][GOAL]["estimatedDifficulty"] for row in rows), int(level) + .5, abs_tol=1e-10):
                raise ValueError("SSS+ tail policy moved a supported folder's median.")
            ordered = sorted(rows, key=lambda row: (row[GOAL + "TailBaseline"]["scoring"]["estimatedDifficulty"], str(row["chartId"])))
            if any(a["scoringVariants"][GOAL]["estimatedDifficulty"] > b["scoringVariants"][GOAL]["estimatedDifficulty"] for a, b in zip(ordered, ordered[1:])):
                raise ValueError("SSS+ tail policy inverted within-folder ordering.")
    return base
