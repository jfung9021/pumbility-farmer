"""Recoverable submitted Double 16--19 SSS+ scale over the frozen schema 56.

This is a rating-scale calibration, not a player-model refit.  Measured folder
effects may overlap or reverse; nominal folder distances never divide by those
small gaps.  Public replay inputs contain chart aggregates, never player IDs.
"""
from __future__ import annotations

import copy
import json
import math
from collections import defaultdict
from statistics import median
from typing import Any, Mapping

import numpy as np

from final_folder_width import POLICY as WIDTH_POLICY, width_parameters, width_value
from sss_scoring import _composite, _derived, payload_sha256, rank_variants
from sssplus_spread import SCALE, spread_value
from target_tail_policy import TAIL_METHOD, TAIL_PARAMETERS, finite, tail_value
from tier_postprocessing import FIT_FIELDS, compress_difficulty

SCHEMA_VERSION = 57
BASE_SCHEMA_VERSION = 56
NAMESPACE = "submittedSssplusCalibration"
STAGE = "submittedSssplusCalibrationBaseline"
METHOD = "submitted-folder-centered-shared-q90-v1"
INTERVAL_METHOD = "conditional-submitted-shared-scale-width-tail-fixed-clearing-v1"
FIELDS = ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High")
INPUT_FIELDS = ("effect", "effectLow", "effectHigh", "ratingEligible", "calibratedComponent",
                "usableContributors", "targetSuccesses", "belowTarget", "bootstrapUsableSamples",
                "provisionalReasons")
STAGE_ORDER = ["cached-submitted-player-chart-effects", "folder-median-and-shared-q90-residual-scale",
               "single-midpoint-compression", "midpoint-asinh-spread", "final-folder-width-floor",
               "single-ordinary-tail-policy", "component-means-and-ranks"]
POLICY = {"version": 1, "method": METHOD,
          "scope": {"source": "submitted", "mode": "Double", "levels": [16, 17, 18, 19]},
          "scoreTarget": 995000, "initialResidualMultiplier": .8, "residualQuantile": .9,
          "minimumFolderCharts": 5, "minimumContributors": 5, "minimumPerOutcome": 5,
          "minimumPositiveScale": 1e-8, "compressionFactor": .5, "compressionApplications": 1,
          "asinhScale": SCALE, "widthPolicy": copy.deepcopy(WIDTH_POLICY),
          "tailPolicy": copy.deepcopy(TAIL_PARAMETERS),
          "newTailExceptions": False, "intervalMethod": INTERVAL_METHOD,
          "meaning": "declared-folder-relative-scale-not-independent-between-folder-identification"}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _lower(row: Mapping[str, Any]) -> bool:
    return row.get("type") == "Double" and row.get("level") in (16, 17, 18, 19)


def _copy_overlay(payload: Mapping[str, Any]) -> dict:
    """Copy only fields this overlay writes; large historical stages are shared."""
    result = dict(payload)
    result["summary"] = dict(payload["summary"])
    result["summary"]["method"] = dict(payload["summary"]["method"])
    result["doubles"] = []
    for old in payload.get("doubles", []):
        row = dict(old)
        for container in ("scoringVariants", "pumbilityVariants"):
            row[container] = dict(old[container])
            row[container]["sssplus"] = copy.deepcopy(old[container]["sssplus"])
        result["doubles"].append(row)
    return result


def _source_identity(base: Mapping[str, Any], identity: Mapping[str, Any] | None) -> tuple[dict, str]:
    source = base.get("summary", {}).get("method", {}).get("sourceAwareTiers", {}).get("sourcePolicy", {})
    matches = [item for item in source.get("targetFits", [])
               if (item.get("goal"), item.get("source"), item.get("mode")) == ("sssplus", "submitted", "Double")]
    if len(matches) != 1:
        raise ValueError("Submitted calibration requires one source-corrected submitted Double SSS+ fit identity.")
    expected = matches[0].get("identity", {})
    if identity is not None and _json(identity) != _json(expected):
        raise ValueError("Submitted calibration fit identity differs from the source-corrected cohort.")
    digest = source.get("effectivePolicySha256")
    for value in [expected.get("boards"), expected.get("sourceBoards"), digest]:
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("Submitted calibration requires complete source-corrected cohort hashes.")
    if (expected.get("target") != 995000 or expected.get("samples") != 200
            or ("seed" in expected and not isinstance(expected["seed"], int))):
        raise ValueError("Submitted calibration requires the cached 995000-target, 200-sample cohort.")
    return copy.deepcopy(expected), digest


def _inputs(base: Mapping[str, Any], fit: Mapping[str, Any]) -> dict:
    return {str(row["chartId"]): {name: copy.deepcopy(fit.get("charts", {}).get(str(row["chartId"]), {}).get(name))
                                for name in INPUT_FIELDS}
            for row in base.get("doubles", []) if _lower(row)}


def _check_inputs(base: Mapping[str, Any], inputs: Mapping[str, Any], diagnostics: Mapping[str, Any]) -> None:
    rows = {str(row["chartId"]): row for row in base.get("doubles", []) if _lower(row)}
    if set(inputs) != set(rows) or not diagnostics.get("converged"):
        raise ValueError("Submitted calibration requires the complete converged lower Double cohort.")
    for cid, row in rows.items():
        value, metric = inputs[cid], row["scoringVariants"]["sssplus"]
        if set(value) != set(INPUT_FIELDS) or metric.get("scoringSource") != "submitted" or metric.get("scoringTarget") != 995000:
            raise ValueError("Submitted calibration source, target, or public fit inputs differ.")
        comparisons = {"effect": "scoringChartEffect", "usableContributors": "nContributors",
                       "targetSuccesses": "scoringTargetSuccesses", "belowTarget": "scoringBelowTarget",
                       "calibratedComponent": "scoringMainComponent"}
        for raw, public in comparisons.items():
            if value[raw] != metric.get(public):
                raise ValueError("Submitted calibration cached chart inputs differ from the source-corrected predecessor.")
        if value["effect"] is not None and not finite(value["effect"]):
            raise ValueError("Submitted chart effects must be finite or null.")
        low, high = value["effectLow"], value["effectHigh"]
        if (low is None) != (high is None) or (low is not None and (not finite(low) or not finite(high) or low > high)):
            raise ValueError("Submitted chart effect endpoints must be paired and ordered.")


def _eligible(row: Mapping[str, Any], value: Mapping[str, Any]) -> bool:
    metric = row["scoringVariants"]["sssplus"]
    previous_reason = str(metric.get("scoringUnratedReason") or "")
    previous_supported = finite(metric.get("estimatedDifficulty")) or "calibration" in previous_reason.lower()
    return (previous_supported and bool(value.get("ratingEligible")) and bool(value.get("calibratedComponent"))
            and bool(metric.get("scoringFitConverged")) and finite(value.get("effect"))
            and (value.get("usableContributors") or 0) >= POLICY["minimumContributors"]
            and min(value.get("targetSuccesses") or 0, value.get("belowTarget") or 0) >= POLICY["minimumPerOutcome"])


def _fit_policy(fit: Mapping[str, Any]) -> dict:
    return {name: copy.deepcopy(fit.get("policy", {}).get(name)) for name in
            ("scoreTarget", "bootstrapSamples", "selectedRegularization", "modelVersion", "weighting")}


def _fit_diagnostics(fit: Mapping[str, Any]) -> dict:
    return {name: copy.deepcopy(fit.get("diagnostics", {}).get(name)) for name in
            ("converged", "bootstrap", "inputPlayers", "eligiblePlayers", "observations", "fitObservations", "componentCount")}


def _initial(effect: float | None, level: int, center: float, scale: float) -> float | None:
    return None if effect is None else level + .5 + POLICY["initialResidualMultiplier"] * (effect - center) / scale


def _patch(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict:
    return {"changed": {key: copy.deepcopy(value) for key, value in old.items()
                        if key not in new or _json(value) != _json(new[key])},
            "added": sorted(set(new) - set(old))}


def _set_endpoints(metric: dict, values: Mapping[str, Any], level: int) -> None:
    metric.update(values)
    metric.update(_derived(values["estimatedDifficulty"], level))
    for side in ("Low", "High"):
        endpoint = values[f"difficultyCi95{side}"]
        metric[f"difficultyDeltaCi95{side}"] = None if endpoint is None else endpoint - level - .5


def _build(base: Mapping[str, Any], inputs: Mapping[str, Any], identity: Mapping[str, Any], fit_hash: str,
           fit_policy: Mapping[str, Any], diagnostics: Mapping[str, Any], *, local_only: bool = True) -> dict:
    if base.get("schemaVersion") != BASE_SCHEMA_VERSION or NAMESPACE in base.get("summary", {}).get("method", {}):
        raise ValueError("Submitted calibration requires the frozen schema-56 predecessor and cannot run twice.")
    identity, source_hash = _source_identity(base, identity)
    if fit_policy.get("scoreTarget") != 995000 or fit_policy.get("bootstrapSamples") != identity["samples"]:
        raise ValueError("Submitted calibration cached target or bootstrap policy differs.")
    _check_inputs(base, inputs, diagnostics)
    result = _copy_overlay(base)
    groups = defaultdict(list)
    for row in result["doubles"]:
        if _lower(row) and _eligible(row, inputs[str(row["chartId"])]):
            groups[int(row["level"])].append(row)
    supported = {level: rows for level, rows in groups.items() if len(rows) >= POLICY["minimumFolderCharts"]}
    folders = {}
    for level, rows in sorted(supported.items()):
        effects = [inputs[str(row["chartId"])]["effect"] for row in rows]
        center = float(median(effects))
        folders[str(level)] = {"level": level, "centerEffect": center, "ratedCharts": len(rows),
            "populationChartIds": sorted(str(row["chartId"]) for row in rows),
            "residualQ90": float(np.quantile([abs(effect-center) for effect in effects], .9, method="linear"))}
    scale = float(median(folder["residualQ90"] for folder in folders.values())) if folders else None
    if not finite(scale) or scale <= POLICY["minimumPositiveScale"]:
        raise ValueError("Submitted calibration needs supported folders and a finite positive shared residual scale.")
    modified = []
    for level, rows in sorted(supported.items()):
        folder = folders[str(level)]
        center = folder["centerEffect"]
        for row in rows:
            cid = str(row["chartId"])
            raw, metric = inputs[cid], row["scoringVariants"]["sssplus"]
            initial = {field: None for field in FIT_FIELDS}
            initial_values = {field: _initial(raw[name], level, center, scale) for field, name in
                              zip(FIELDS, ("effect", "effectLow", "effectHigh"))}
            _set_endpoints(initial, initial_values, level)
            metric["initialFit"] = initial
            compressed = {field: compress_difficulty(value, level) for field, value in initial_values.items()}
            spread = {field: spread_value(value, level) for field, value in compressed.items()}
            _set_endpoints(metric, spread, level)
            reasons = set(metric.get("scoringProvisionalReasons", [])) | set(raw.get("provisionalReasons") or [])
            reasons.add("submitted-folder-relative-calibration")
            metric.update(evidenceStatus="Provisional", scoringProvisional=True, scoringUnratedReason=None,
                scoringProvisionalReasons=sorted(reasons), scoringExtrapolated=False,
                scoringChartEffectCi95Low=raw["effectLow"], scoringChartEffectCi95High=raw["effectHigh"],
                scoringBootstrapValidReplicates=int(raw.get("bootstrapUsableSamples") or 0), intervalMethod=INTERVAL_METHOD)
            metric.pop("intervalUnavailableReason", None)
            metric["scoringSubmittedCalibration"] = {"version": 1, "method": METHOD, "folder": str(level),
                "effect": raw["effect"], "effectCi95Low": raw["effectLow"], "effectCi95High": raw["effectHigh"],
                "initialCompressionApplications": 1}
            metric["scoringSpreadPolicy"] = {"version": 1, "method": "midpoint-asinh-pre-tail-v1", "scale": SCALE,
                "midpoint": level+.5,
                **{"preSpread"+suffix: compressed[field] for field, suffix in
                   zip(FIELDS, ("Difficulty", "DifficultyCi95Low", "DifficultyCi95High"))},
                **{"spread"+suffix: spread[field] for field, suffix in
                   zip(FIELDS, ("Difficulty", "DifficultyCi95Low", "DifficultyCi95High"))}}
            modified.append(cid)
        params = width_parameters([{"id": row["chartId"], "value": row["scoringVariants"]["sssplus"]["estimatedDifficulty"]}
                                   for row in rows], level)
        folder["widthParameters"] = params
        ordered_effects = sorted(inputs[str(row["chartId"])]["effect"] for row in rows)
        central_effects = {ordered_effects[(len(rows)-1)//2], ordered_effects[len(rows)//2]}
        final_values = []
        for row in rows:
            metric = row["scoringVariants"]["sssplus"]
            snapshot = {field: metric[field] for field in FIELDS}
            before_tail = {field: width_value(value, params) for field, value in snapshot.items()}
            final = {field: tail_value(value, level) for field, value in before_tail.items()}
            _set_endpoints(metric, final, level)
            metric["finalFolderWidth"] = {"group": f"Double:submitted-sssplus:{level}", "input": snapshot,
                "parameters": copy.deepcopy(params), "exceptionPreserved": False, "initialCompressionApplications": 1}
            metric["scoringTailPolicy"] = {"version": 1, "method": TAIL_METHOD, "midpoint": level+.5,
                "exceptionSelected": False, "exceptionEligible": False,
                "rejectionReasons": ["submitted-calibration-no-new-exceptions"], "conservativeDistance": None,
                "protectedMedian": metric["scoringChartEffect"] in central_effects,
                "ordinaryDifficulty": final["estimatedDifficulty"],
                **{"preTail"+suffix: before_tail[field] for field, suffix in
                   zip(FIELDS, ("Difficulty", "DifficultyCi95Low", "DifficultyCi95High"))},
                **{"final"+suffix: final[field] for field, suffix in
                   zip(FIELDS, ("Difficulty", "DifficultyCi95Low", "DifficultyCi95High"))}}
            row["pumbilityVariants"]["sssplus"] = _composite(row, metric)
            row["pumbilityVariants"]["sssplus"]["intervalMethod"] = INTERVAL_METHOD
            final_values.append(metric["estimatedDifficulty"])
        for row in rows:
            row["scoringVariants"]["sssplus"]["scoringTailPolicy"]["finalFolderRange"] = {
                "lower": min(final_values), "upper": max(final_values)}
    rank_variants(result["doubles"], goal="sssplus")
    result["schemaVersion"] = SCHEMA_VERSION
    result["summary"]["method"][NAMESPACE] = {"version": 1, "method": METHOD, "localOnly": local_only,
        "baseSchemaVersion": BASE_SCHEMA_VERSION, "basePayloadSha256": payload_sha256(base),
        "policy": copy.deepcopy(POLICY), "fitIdentity": identity, "fitSha256": fit_hash,
        "sourcePolicySemanticSha256": source_hash, "fitPolicy": copy.deepcopy(fit_policy),
        "fitDiagnostics": copy.deepcopy(diagnostics), "chartInputs": copy.deepcopy(inputs),
        "chartInputsSha256": payload_sha256(inputs), "folders": folders, "sharedScale": scale,
        "modifiedChartIds": sorted(modified), "unsupportedFolders": sorted(level for level in groups if level not in supported),
        "modelRefits": 0, "dataFetches": 0, "compressionApplications": 1, "stageOrder": list(STAGE_ORDER)}
    for row, old in zip(result["doubles"], base.get("doubles", [])):
        patches = {name: _patch(old[container]["sssplus"], row[container]["sssplus"])
                   for name, container in (("scoring", "scoringVariants"), ("pumbility", "pumbilityVariants"))}
        if any(value["changed"] or value["added"] for value in patches.values()):
            row[STAGE] = patches
    return result


def apply_submitted_sssplus_calibration(payload: Mapping[str, Any], fit: Mapping[str, Any], *,
                                       fit_identity: Mapping[str, Any] | None = None,
                                       local_only: bool = True) -> dict:
    identity, _ = _source_identity(payload, fit_identity)
    result = _build(payload, _inputs(payload, fit), identity, payload_sha256(fit), _fit_policy(fit), _fit_diagnostics(fit), local_only=local_only)
    validate_submitted_sssplus_calibration(result, payload, fit=fit, fit_identity=identity)
    return result


def recover_submitted_sssplus_calibration(payload: Mapping[str, Any]) -> dict:
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("Submitted calibration recovery requires schema 57.")
    result = _copy_overlay(payload)
    result["schemaVersion"] = BASE_SCHEMA_VERSION
    result["summary"]["method"].pop(NAMESPACE)
    for row in result["doubles"]:
        patches = row.pop(STAGE, None)
        if patches is None:
            continue
        if set(patches) != {"scoring", "pumbility"}:
            raise ValueError("Submitted calibration recovery has unexpected component paths.")
        for name, container in (("scoring", "scoringVariants"), ("pumbility", "pumbilityVariants")):
            saved = patches[name]
            if set(saved) != {"changed", "added"}:
                raise ValueError("Submitted calibration recovery has an invalid patch.")
            metric = row[container]["sssplus"]
            for key in saved["added"]:
                metric.pop(key, None)
            metric.update(copy.deepcopy(saved["changed"]))
    return result


def validate_submitted_sssplus_calibration(payload: Mapping[str, Any], original: Mapping[str, Any] | None = None, *,
                                          fit: Mapping[str, Any] | None = None,
                                          fit_identity: Mapping[str, Any] | None = None) -> dict:
    base = recover_submitted_sssplus_calibration(payload)
    marker = payload["summary"]["method"][NAMESPACE]
    if (marker.get("policy") != POLICY or marker.get("stageOrder") != STAGE_ORDER or marker.get("modelRefits") != 0
            or marker.get("dataFetches") != 0 or not isinstance(marker.get("localOnly"), bool)):
        raise ValueError("Submitted calibration policy or stage order differs.")
    if payload_sha256(base) != marker.get("basePayloadSha256") or (original is not None and payload_sha256(original) != marker["basePayloadSha256"]):
        raise ValueError("Submitted calibration predecessor recovery differs.")
    identity, source_hash = _source_identity(base, marker.get("fitIdentity"))
    if fit_identity is not None and _json(identity) != _json(fit_identity):
        raise ValueError("Submitted calibration external fit identity differs.")
    if marker.get("sourcePolicySemanticSha256") != source_hash or payload_sha256(marker["chartInputs"]) != marker.get("chartInputsSha256"):
        raise ValueError("Submitted calibration source provenance or aggregate fit inputs differ.")
    if fit is not None and (payload_sha256(fit) != marker.get("fitSha256") or _json(_inputs(base, fit)) != _json(marker["chartInputs"])):
        raise ValueError("Submitted calibration cached fit or endpoints differ.")
    expected = _build(base, marker["chartInputs"], identity, marker["fitSha256"], marker["fitPolicy"], marker["fitDiagnostics"], local_only=marker["localOnly"])
    if _json(expected["summary"]["method"][NAMESPACE]) != _json(marker):
        raise ValueError("Submitted calibration folder parameters or declared provenance differ.")
    for row, replay in zip(payload.get("doubles", []), expected["doubles"]):
        for container in ("scoringVariants", "pumbilityVariants"):
            if _json(row[container]["sssplus"]) != _json(replay[container]["sssplus"]):
                raise ValueError("Submitted calibration points, evidence, intervals, component means or ranks differ.")
        if _json(row.get(STAGE)) != _json(replay.get(STAGE)):
            raise ValueError("Submitted calibration recovery patches differ.")
    for level, params in marker["folders"].items():
        ordered = sorted((row for row in payload["doubles"] if str(row["chartId"]) in params["populationChartIds"]),
                         key=lambda row: marker["chartInputs"][str(row["chartId"])]["effect"])
        values = [row["scoringVariants"]["sssplus"]["estimatedDifficulty"] for row in ordered]
        if not math.isclose(median(values), int(level)+.5, abs_tol=1e-10) or any(a > b for a, b in zip(values, values[1:])):
            raise ValueError("Submitted calibration moved the folder median or chart order.")
        for row in ordered:
            m = row["scoringVariants"]["sssplus"]
            low, high = m["difficultyCi95Low"], m["difficultyCi95High"]
            if (low is None) != (high is None) or (low is not None and low > high):
                raise ValueError("Submitted calibration intervals are not monotone.")
    return base
