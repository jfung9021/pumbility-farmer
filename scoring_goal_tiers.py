"""Independent SS/SS+ artifacts over the unchanged historical tier pipeline.

Artifacts contain chart aggregates only. They are attached by exact semantic
input identity; no adjacent target's fitted effects or calibration are reused.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any, Mapping

from canonical_json import canonical_json_sha256 as digest
from scoring_targets import BACKFILL_GOALS, SCORING_GOALS, get_scoring_target, scoring_goal_policies
from sss_scoring import (MODES, _composite, _target_metric, fit_target_curve,
                         rank_variants, reference_anchors)
from tier_postprocessing import FIT_FIELDS

SCHEMA_VERSION = 58
SCRIPT_VERSION = "6.22.0-scoring-goals"
NAMESPACE = "scoringGoalBackfills"
METHOD = "independent-target-goal-overlays-v1"
ADDITIONS = (NAMESPACE, "scoringGoalPolicies", "availableScoringGoals")


def _need(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _validate_artifact_metadata(artifact, target):
    from production_tiers import SAMPLES, SEED
    method = artifact.get("method", {})
    _need(artifact.get("schemaVersion") == 1 and artifact.get("goal") == target.key
          and artifact.get("targetConfig") == target.public(), "Incorrect scoring-goal artifact policy.")
    _need(method.get("targetConfig") == target.public() and method.get("target") == target.score
          and method.get("bootstrapSamples") == SAMPLES and method.get("seed") == SEED
          and method.get("compressionApplications") == 1
          and method.get("method") == "target-median-anchor-refinement-centering-tail-v1",
          "Incorrect scoring-goal numerical recipe.")
    references = method.get("fitReferences", [])
    expected = {(source, mode) for source in ("official", "submitted") for mode, _ in MODES}
    _need(len(references) == 4 and {(entry.get("source"), entry.get("mode")) for entry in references} == expected,
          "Missing or duplicate scoring-goal fit references.")
    for entry in references:
        identity = entry.get("identity", {})
        _need(entry.get("goal") == target.key and identity.get("target") == target.score
              and identity.get("samples") == SAMPLES and identity.get("seed") == SEED
              and _hash(identity.get("boards")) and _hash(identity.get("sourceBoards")),
              "Stale scoring-goal fit reference.")


def _recompute(payload: dict, goal: str) -> None:
    for _, key in MODES:
        for row in payload.get(key, []):
            metric = row["scoringVariants"][goal]
            composite = _composite(row, metric)
            if metric["scoringMethod"] != get_scoring_target(goal).model_method:
                composite["intervalMethod"] = "unavailable-after-sss-refinement-v1"
            elif "scoringTailPolicy" in metric:
                composite["intervalMethod"] = "conditional-target-tail-fixed-clearing-and-calibration-v1"
            row["pumbilityVariants"][goal] = composite
        rank_variants(payload.get(key, []), goal=goal)


def _empty_rows(catalog: Mapping[str, Any]) -> dict:
    result = {"schemaVersion": SCHEMA_VERSION, "summary": {"method": {}}, "singles": [], "doubles": []}
    for cid, chart in sorted(catalog.items()):
        if chart.get("type") not in ("Single", "Double") or chart.get("level", 0) < 16:
            continue
        key = "singles" if chart["type"] == "Single" else "doubles"
        result[key].append({**copy.deepcopy(chart), "chartId": str(cid),
            "songName": chart.get("songName") or "", "scoringVariants": {}, "pumbilityVariants": {},
            "tierMetrics": {"clearing": {"estimatedDifficulty": None, "ratedClearCount": 0,
                "evidenceStatus": "Unrated", "initialFit": {"estimatedDifficulty": None}}}})
    return result


def build_goal_artifact(context, fits, fit_identities, generated_at_utc, *, goal: str) -> dict:
    """Build one target from four independent fits; never modify a legacy metric."""
    from production_tiers import SAMPLES, SEED, validate_fit
    from sss_folder_centering import centering_folders, shift_scoring
    from sss_scoring_refinement import apply_sss_refinement
    from target_tail_policy import apply_tail_policy

    _need(goal in BACKFILL_GOALS, "Only independently backfilled targets use this builder.")
    target = get_scoring_target(goal)
    first, second, _, policy, merged, prepared = context
    result = _empty_rows(prepared["catalog"])
    calibrations, diagnostics = {}, {}
    expected_cohorts = {(source, mode) for source in ("official", "submitted") for mode, _ in MODES}
    _need({(entry.get("source"), entry.get("mode")) for entry in fit_identities} == expected_cohorts
          and len(fit_identities) == 4, "Missing or duplicate target-fit provenance.")
    for entry in fit_identities:
        _need(entry.get("goal") == goal and entry["identity"].get("target") == target.score,
              "The backfill fit belongs to another target.")
    for source, minimum in (("submitted", 16), ("official", 20)):
        calibrations[source], diagnostics[source] = {}, {}
        for mode, key in MODES:
            fitted = fits[source][mode]
            validate_fit(fitted, {"target": target.score})
            anchors, exclusions = reference_anchors(prepared["catalog"], prepared[source]["charts"],
                                                    fitted, mode=mode, minimum_level=minimum)
            try:
                curve, error = fit_target_curve(anchors, minimum_level=minimum), None
            except ValueError as exc:
                curve, error = None, str(exc).replace("SSS calibration", target.grade + " calibration")
            calibrations[source][mode] = curve
            diagnostics[source][mode] = {"calibrationError": error, "referenceExclusions": exclusions,
                "referenceAnchors": copy.deepcopy(anchors), "model": copy.deepcopy(fitted["diagnostics"]),
                "modelPolicy": copy.deepcopy(fitted["policy"])}
            for row in result[key]:
                if (row["level"] >= 20) != (source == "official"):
                    continue
                cid = row["chartId"]
                estimate = fitted["charts"].get(cid)
                if estimate:
                    estimate = {**estimate, "fitConverged": bool(fitted["diagnostics"].get("converged"))}
                metric = _target_metric(row, source, estimate, prepared[source]["charts"].get(cid, {}),
                                        curve, error, target=target)
                row["scoringVariants"][goal] = metric
                row["pumbilityVariants"][goal] = _composite(row, metric)
    result["summary"]["method"][goal + "Scoring"] = {
        "version": 1, "method": target.model_method, "target": target.score,
        "targetConfig": target.public(), "calibration": calibrations, "diagnostics": diagnostics,
        "minimumPlayerCharts": 4, "minimumContributors": 5, "minimumReferenceContributors": 20,
        "minimumReferencePerOutcome": 5, "minimumReferenceCharts": 5,
        "bootstrapSamples": SAMPLES, "seed": SEED, "compressionApplications": 1,
        "sourceBoundary": {"submitted": [16, 19], "officialMinimum": 20}}
    _recompute(result, goal)
    result, _ = apply_sss_refinement(result, prepared, fits, seed=SEED, target=target)
    result["summary"]["method"][goal + "Refinement"]["firstPassSchemaVersion"] = SCHEMA_VERSION
    _recompute(result, goal)
    folders = centering_folders(result, goal=goal)
    for mode, key in MODES:
        for row in result[key]:
            if row["level"] >= 20:
                frozen = result["summary"]["method"][goal + "Refinement"]["ranges"]["official"][mode].get(str(row["level"]))
                shift_scoring(row["scoringVariants"][goal], row["level"], folders[mode][str(row["level"])], frozen)
    _recompute(result, goal)
    tail = apply_tail_policy(result, goal=goal)
    _recompute(result, goal)
    method = {**result["summary"]["method"][goal + "Scoring"],
        "method": "target-median-anchor-refinement-centering-tail-v1",
        "refinement": result["summary"]["method"][goal + "Refinement"],
        "folderCentering": {"version": 1, "method": "target-final-folder-median-centering-v1", "folders": folders},
        "tail": tail, "fitReferences": copy.deepcopy(fit_identities),
        "scoreFetches": 0, "recommendationFits": 0,
        "stageOrder": ["target-fit", "median-anchors", "frozen-range-refinement", "folder-centering",
                       "single-midpoint-compression", "target-tail-policy", "component-mean-and-ranks"]}
    return {"schemaVersion": 1, "goal": goal, "targetConfig": target.public(),
        "generatedAtUtc": generated_at_utc,
        "sourceHashes": {"phoenix1": digest(first), "phoenix2": digest(second),
                         "manifest": merged.get("manifestSha256"), "sourcePolicy": policy.semantic_hash()},
        "method": method,
        "charts": {row["chartId"]: row["scoringVariants"][goal] for _, key in MODES for row in result[key]}}


def _rows(payload):
    return [row for _, key in MODES for row in payload.get(key, [])]


def recover_goal_tiers(payload: Mapping[str, Any]) -> dict:
    _need(payload.get("schemaVersion") == SCHEMA_VERSION, "Goal recovery requires schema 58.")
    result = copy.deepcopy(payload)
    method = result["summary"]["method"]
    namespace = method.get(NAMESPACE, {})
    _need(namespace.get("version") == 1 and namespace.get("baseSchemaVersion") == 57,
          "Missing scoring-goal predecessor identity.")
    for row in _rows(result):
        for name in ("scoringVariants", "pumbilityVariants"):
            for goal in BACKFILL_GOALS:
                row[name].pop(goal, None)
    for key in ADDITIONS:
        method.pop(key, None)
    prior = namespace.get("baseScriptVersion")
    if prior is None:
        result["summary"].pop("scriptVersion", None)
    else:
        result["summary"]["scriptVersion"] = prior
    result["schemaVersion"] = 57
    return result


def _validate_metric(metric, row, target):
    value, level = metric.get("estimatedDifficulty"), row["level"]
    _need(metric.get("scoringTarget") == target.score and metric.get("scoringMethod") in
          (target.model_method, target.boundary_method, target.rare_method), "Incorrect scoring-goal model.")
    _need(metric.get("scoringSource") == ("official" if level >= 20 else "submitted"),
          "Incorrect scoring-goal source boundary.")
    counts = [metric.get(key) for key in ("nContributors", "scoringTargetSuccesses", "scoringBelowTarget")]
    _need(all(type(n) is int and n >= 0 for n in counts) and counts[0] == counts[1] + counts[2],
          "Incorrect scoring-goal support counts.")
    for key in ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"):
        _need(metric.get(key) is None or _finite(metric[key]), "Nonfinite scoring-goal value.")
    _need(metric.get("difficultyDelta") == (None if value is None else value-level-.5),
          "Incorrect scoring-goal delta.")
    low, high = metric.get("difficultyCi95Low"), metric.get("difficultyCi95High")
    _need(low is None or high is None or low <= high, "Reversed scoring-goal interval.")
    _need(value is not None or (metric.get("evidenceStatus") == "Unrated" and metric.get("scoringUnratedReason")),
          "Unavailable scoring-goal evidence needs an Unrated reason.")


def validate_goal_tiers(payload: Mapping[str, Any], *, full: bool = True) -> dict:
    """Validate the additive contract; historical validation remains unchanged."""
    base = recover_goal_tiers(payload)
    method = payload["summary"]["method"]
    namespace = method[NAMESPACE]
    goals = namespace.get("goals", {})
    _need(namespace.get("method") == METHOD and goals and set(goals) <= set(BACKFILL_GOALS),
          "Invalid scoring-goal overlay policy.")
    _need(method.get("scoringGoalPolicies") == scoring_goal_policies(), "Incorrect scoring-goal policies.")
    available = [goal for goal in SCORING_GOALS if goal not in BACKFILL_GOALS or goal in goals]
    _need(method.get("availableScoringGoals") == available, "Incorrect scoring-goal availability.")
    _need(isinstance(namespace.get("inputIdentity"), Mapping), "Missing scoring-goal input identity.")
    _need(_hash(namespace.get("basePayloadSha256")), "Missing scoring-goal baseline hash.")
    rows = _rows(payload)
    _need(len({str(row["chartId"]) for row in rows}) == len(rows), "Duplicate chart in scoring goals.")
    for goal, entry in goals.items():
        target = get_scoring_target(goal)
        _need(entry.get("targetConfig") == target.public() and entry.get("inputIdentity") == namespace["inputIdentity"],
              "Stale scoring-goal artifact identity.")
        _need(_hash(entry.get("artifactSha256")), "Missing scoring-goal artifact digest.")
        artifact = {**entry.get("artifact", {}), "charts": {}}
        _need(artifact.get("goal") == goal and artifact.get("targetConfig") == target.public()
              and artifact.get("inputIdentity") == namespace["inputIdentity"]
              and artifact.get("method") == entry.get("method"), "Incorrect scoring-goal artifact provenance.")
        _validate_artifact_metadata(artifact, target)
        for row in rows:
            _need(set(row["scoringVariants"]) == set(available) and set(row["pumbilityVariants"]) == set(available),
                  "Scoring-goal availability differs between charts.")
            metric = row["scoringVariants"][goal]
            _validate_metric(metric, row, target)
            artifact["charts"][str(row["chartId"])] = metric
        if full:
            _need(digest(artifact) == entry["artifactSha256"], "Scoring-goal chart evidence differs from its artifact.")
        expected = copy.deepcopy(payload)
        _recompute(expected, goal)
        for row, reference in zip(rows, _rows(expected)):
            _need(row["scoringVariants"][goal] == reference["scoringVariants"][goal]
                  and row["pumbilityVariants"][goal] == reference["pumbilityVariants"][goal],
                  "Scoring-goal ranks or Pumbility components differ.")
    if full:
        _need(digest(base) == namespace["basePayloadSha256"], "The scoring overlay changed a legacy field.")
        from submitted_sssplus_calibration import validate_submitted_sssplus_calibration
        from source_aware_tiers import validate_source_aware_tiers
        validate_source_aware_tiers(validate_submitted_sssplus_calibration(base))
    return base


def apply_goal_artifacts(base: Mapping[str, Any], artifacts: Mapping[str, Mapping[str, Any]], *,
                         expected_input_identity: Mapping[str, Any]) -> dict:
    """Join complete goals only when every consumed input belongs to this epoch."""
    _need(base.get("schemaVersion") == 57, "Scoring goals require a frozen schema-57 baseline.")
    _need(artifacts and set(artifacts) <= set(BACKFILL_GOALS), "Unknown or empty scoring-goal backfill.")
    result = copy.deepcopy(base)
    rows = _rows(result)
    chart_ids = {str(row["chartId"]) for row in rows}
    _need(len(chart_ids) == len(rows), "Duplicate baseline chart identity.")
    metadata = {}
    for goal, artifact in artifacts.items():
        target = get_scoring_target(goal)
        _need(artifact.get("schemaVersion") == 1 and artifact.get("goal") == goal
              and artifact.get("targetConfig") == target.public(), "Incorrect scoring-goal artifact policy.")
        _validate_artifact_metadata(artifact, target)
        _need(artifact.get("inputIdentity") == expected_input_identity, "The scoring-goal inputs changed.")
        _need(set(artifact.get("charts", {})) == chart_ids, "The scoring-goal chart population changed.")
        for row in rows:
            row["scoringVariants"][goal] = copy.deepcopy(artifact["charts"][str(row["chartId"])])
        _recompute(result, goal)
        metadata[goal] = {"targetConfig": target.public(), "artifactSha256": digest(artifact),
            "inputIdentity": copy.deepcopy(expected_input_identity), "method": copy.deepcopy(artifact["method"]),
            "artifact": {key: copy.deepcopy(value) for key, value in artifact.items() if key != "charts"}}
    result["schemaVersion"] = SCHEMA_VERSION
    result["summary"]["method"].update({
        "scoringGoalPolicies": scoring_goal_policies(),
        "availableScoringGoals": [goal for goal in SCORING_GOALS if goal not in BACKFILL_GOALS or goal in artifacts],
        NAMESPACE: {"version": 1, "method": METHOD, "baseSchemaVersion": 57,
            "basePayloadSha256": digest(base), "baseScriptVersion": base["summary"].get("scriptVersion"),
            "inputIdentity": copy.deepcopy(expected_input_identity), "goals": metadata}})
    validate_goal_tiers(result)
    return result
