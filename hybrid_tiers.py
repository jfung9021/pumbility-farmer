"""Local, explicitly versioned hybrid tiers built from pinned fitted components."""
from __future__ import annotations

import copy
import math
import re
from typing import Any, Mapping

from cross_level_clearing import apply_cross_level_clearing, current_clearing_catalog
from pumbility_contract import hybrid_clearing_method_identity, hybrid_tier_method_identity
from tier_postprocessing import finalize_tiers, initial_fit_payload, validate_postprocessing

CURRENT_METADATA = ("chartId", "type", "mode", "level", "difficulty", "folder", "songName",
                    "noteCount", "imageUrl", "stepArtist", "bpmMin", "bpmMax")


def retain_lower_submitted_scoring(payload: Mapping[str, Any], submitted: Mapping[str, Any]) -> dict[str, Any]:
    """Reuse the unchanged profile fit, with the overlay's current chart metadata."""
    result = initial_fit_payload(payload)
    source = initial_fit_payload(submitted)
    if source.get("schemaVersion") != 26 or result.get("schemaVersion") not in (42, 43):
        raise ValueError("Hybrid tiers require the submitted schema-26 fit and official schema-42/43 fit.")
    source_method = source.get("summary", {}).get("method", {})
    if source_method.get("scoring", {}).get("calibration") != "folder-scaled-score-profile":
        raise ValueError("Hybrid lower Scoring requires the existing submitted score-profile model.")
    for mode in ("singles", "doubles"):
        lower = {str(row["chartId"]): row for row in source.get(mode, [])}
        for index, row in enumerate(result.get(mode, [])):
            if row["level"] >= 20:
                continue
            original = lower.get(str(row["chartId"]))
            if original is None or original["level"] != row["level"] or original["type"] != row["type"]:
                raise ValueError("Pinned submitted lower Scoring must match the current chart catalog.")
            replacement = copy.deepcopy(original)
            replacement.update({key: copy.deepcopy(row[key]) for key in CURRENT_METADATA if key in row})
            replacement.update(scoringMethod="submitted-score-profile-v2", officialScoringContributors=0,
                               officialScoringEvidence=None, tierMetrics=copy.deepcopy(row["tierMetrics"]))
            result[mode][index] = replacement
    result["summary"]["method"]["hybridTiers"] = {**hybrid_tier_method_identity(), "submittedScoring": {
        "method": copy.deepcopy(source_method["scoring"]),
        "scoreProfileCalibration": copy.deepcopy(source_method.get("scoreProfileCalibration")),
        "confidenceIntervals": copy.deepcopy(source_method.get("confidenceIntervals"))}}
    result["summary"]["method"]["sourceSelection"] = "hybrid-submitted-below20-official-at20plus"
    return result


def refresh_hybrid_summary(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    for mode in ("singles", "doubles"):
        rows = payload.get(mode, [])
        info = summary.setdefault("modes", {}).setdefault(mode, {})
        info.update(catalogCharts=len(rows), measuredCharts=sum(r["estimatedDifficulty"] is not None for r in rows),
                    publishedCharts=sum(r["evidenceStatus"] == "Published" for r in rows))
        info["calibration"] = {"method": "submitted-score-profile-below20-official-cross-level-at20plus"}
        for folder, values in info.setdefault("folders", {}).items():
            group = [row for row in rows if row.get("folder") == folder]
            values.update(measuredCharts=sum(r["estimatedDifficulty"] is not None for r in group),
                          publishedCharts=sum(r["evidenceStatus"] == "Published" for r in group))
    all_rows = payload.get("singles", []) + payload.get("doubles", []) + payload.get("coop", [])
    summary.setdefault("coverage", {}).update(
        targetChartsMeasured=sum(r.get("estimatedDifficulty") is not None for r in all_rows),
        targetChartsPublished=sum(r.get("evidenceStatus") == "Published" for r in all_rows))


def build_local_hybrid_tiers(base: Mapping[str, Any], submitted: Mapping[str, Any],
                            phoenix1: Mapping[str, Any], phoenix2: Mapping[str, Any]) -> dict[str, Any]:
    if base.get("schemaVersion") != 44:
        raise ValueError("The local hybrid builder requires the frozen maximum schema-44 baseline.")
    validate_postprocessing(base)
    raw = retain_lower_submitted_scoring(base, submitted)
    candidate = apply_cross_level_clearing(raw, phoenix1, phoenix2, current_clearing_catalog(raw, phoenix2),
                                         hybrid_lower_tiers=True)
    refresh_hybrid_summary(candidate)
    candidate = finalize_tiers(candidate)
    candidate["summary"]["scriptVersion"] = str(base["summary"].get("scriptVersion", "")) + "+local-hybrid-v1"
    candidate["summary"]["tierArtifactProvenance"] = {
        "base": {"schemaVersion": base["schemaVersion"], "generatedAtUtc": base.get("generatedAtUtc"),
                 "scriptVersion": base["summary"].get("scriptVersion")},
        "operation": "local-hybrid-v1", "submittedFitGeneratedAtUtc": submitted.get("generatedAtUtc")}
    validate_hybrid_tiers(candidate)
    return candidate


def build_release_hybrid_tiers(base: Mapping[str, Any], submitted: Mapping[str, Any],
                              phoenix1: Mapping[str, Any], phoenix2: Mapping[str, Any]) -> dict[str, Any]:
    """Reuse a pinned official fit and rebuild only the changed submitted tier components."""
    from pumbility_average import apply_average_pumbility
    from pumbility_contract import HYBRID_TIER_SCRIPT_VERSION
    candidate = apply_average_pumbility(build_local_hybrid_tiers(base, submitted, phoenix1, phoenix2))
    candidate["summary"]["scriptVersion"] = HYBRID_TIER_SCRIPT_VERSION
    candidate["summary"]["tierArtifactProvenance"]["operation"] = "hybrid-average-release"
    candidate["summary"]["tierArtifactProvenance"]["producerScriptVersion"] = HYBRID_TIER_SCRIPT_VERSION
    validate_hybrid_tiers(candidate)
    return candidate


def validate_hybrid_tiers(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the versioned local fit and its per-range dispatch before publishing."""
    if payload.get("schemaVersion") == 48:
        from pumbility_average import recover_maximum_pumbility
        payload = recover_maximum_pumbility(payload)
    if payload.get("schemaVersion") not in (46, 47):
        raise ValueError("Hybrid tier validation requires schema 46, 47 or 48.")
    raw = validate_postprocessing(payload) if payload["schemaVersion"] == 47 else copy.deepcopy(payload)
    method = raw.get("summary", {}).get("method", {})
    hybrid, clearing = method.get("hybridTiers", {}), method.get("crossLevelClearing", {})
    if any(hybrid.get(key) != value for key, value in hybrid_tier_method_identity().items()):
        raise ValueError("Incompatible hybrid tier source policy.")
    submitted = hybrid.get("submittedScoring", {})
    if (submitted.get("method", {}).get("calibration") != "folder-scaled-score-profile"
            or not isinstance(submitted.get("scoreProfileCalibration"), Mapping)):
        raise ValueError("Hybrid tiers must retain submitted Scoring calibration.")
    official = method.get("officialCrossLevelScoring", {})
    if (any(official.get(key) != value for key, value in {
            "version": 1, "minimumOfficialLevel": 20, "source": "piuscores-official",
            "mergePolicy": "maximum-raw-score", "minimumOtherCharts": 3,
            "modelVersion": 1, "calibrationVersion": 1, "bootstrapSamples": 1000}.items())
            or not re.fullmatch(r"[0-9a-f]{64}", str(official.get("manifestSha256", "")))):
        raise ValueError("Incompatible official 20+ Scoring policy.")
    if (any(clearing.get(key) != value for key, value in hybrid_clearing_method_identity().items())
            or clearing.get("baseScoringSchemaVersion") != 42
            or method.get("tierMetrics", {}).get("version") != 12
            or method.get("tierMetrics", {}).get("clearing") != hybrid_clearing_method_identity()
            or set(clearing.get("inputHashes", {})) != {"phoenix1", "phoenix2", "currentCatalog"}
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in clearing["inputHashes"].values())):
        raise ValueError("Incompatible hybrid Clearing method or input identity.")
    for mode, key in (("Single", "singles"), ("Double", "doubles")):
        rows, curve = raw.get(key, []), clearing.get("calibration", {}).get(mode)
        if any(row["level"] >= 20 for row in rows):
            if (not curve or len(curve["anchors"]) < 3 or curve["anchors"][0]["level"] != 20
                    or any(a["level"] < 20 for a in curve["anchors"])):
                raise ValueError("Hybrid Clearing requires three increasing 20+ anchors including level 20.")
            from cross_level_clearing_calibration import fit_clearing_curve
            if fit_clearing_curve(curve["anchors"]) != curve:
                raise ValueError("Hybrid Clearing curve disagrees with its increasing median anchors.")
        for row in rows:
            lower = row["level"] < 20
            expected = "submitted-score-profile-v2" if lower else "official-cross-level-v1"
            metric = row["tierMetrics"]["clearing"]
            if row.get("scoringMethod") != expected or metric.get("method") != (
                    "submitted-folder-centered-clearing-v1" if lower else "submitted-cross-level-clearing-v2"):
                raise ValueError("A hybrid chart uses an incompatible range method.")
            if lower and metric["estimatedDifficulty"] is not None:
                reference = metric.get("folderReferenceSkill")
                expected_value = row["level"] + .5 + .70 * (metric["q10Skill"] - reference)
                if not math.isclose(metric["estimatedDifficulty"], expected_value, abs_tol=1e-9):
                    raise ValueError("Lower Clearing disagrees with its folder-centered calibration.")
    return raw
