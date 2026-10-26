"""General/Clearing baseline for the current General and SSS+ producer.

The historical SSS branch is deliberately absent. Its old schema validators stay
available in their original modules for immutable historical artifacts.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from sss_scoring import MODES, general_scoring_view, payload_sha256

BASE_SCHEMA_VERSION = 148
PUBLIC_SCHEMA_VERSION = 61
VERSION = "6.25.0-retained-goals"
PRODUCER = "retained-goals-v1"
NAMESPACE = "retainedGoalProducer"
GOALS = ("general", "sssplus")


def is_retained(payload: Mapping[str, Any]) -> bool:
    marker = payload.get("summary", {}).get("method", {}).get(NAMESPACE)
    return isinstance(marker, Mapping) and marker.get("producer") == PRODUCER


def scoring_goals(payload: Mapping[str, Any]) -> tuple[str, ...]:
    return GOALS if is_retained(payload) else ("general", "sss", "sssplus")


def validate_marker(payload: Mapping[str, Any]) -> None:
    marker = payload.get("summary", {}).get("method", {}).get(NAMESPACE)
    if (not isinstance(marker, Mapping) or marker.get("version") != 1
            or marker.get("producer") != PRODUCER or marker.get("goals") != list(GOALS)
            or marker.get("targetCohorts") != 4 or marker.get("targetBootstrapSamples") != 200
            or marker.get("retiredTargetFits") != 0 or marker.get("baseSchemaVersion") != 48
            or marker.get("clearingBootstrapSamples") != 1000
            or not isinstance(marker.get("basePayloadSha256"), str)
            or len(marker["basePayloadSha256"]) != 64
            or any(value not in "0123456789abcdef" for value in marker["basePayloadSha256"])):
        raise ValueError("The retained-goal producer policy differs.")
    for _, key in MODES:
        for row in payload.get(key, []):
            for name in ("scoringVariants", "pumbilityVariants"):
                if set(row.get(name, {})) - set(GOALS):
                    raise ValueError("A retained-goal artifact contains a retired target.")


def apply_retained_baseline(base: Mapping[str, Any], capture: Mapping[str, Any], *,
                            bootstrap_samples: int = 1000) -> dict:
    """Extract exactly the Clearing/General work formerly bundled with SSS."""
    from official_high_level_clearing import apply_official_high_level_clearing
    from refined_tiers import _general_composite
    from tier_postprocessing import _rank_secondary

    if base.get("schemaVersion") != 48 or is_retained(base):
        raise ValueError("The retained baseline requires the unchanged General schema-48 input.")
    result = copy.deepcopy(base)
    marker = {"version": 1, "producer": PRODUCER, "goals": list(GOALS),
        "targetCohorts": 4, "targetBootstrapSamples": 200, "retiredTargetFits": 0,
        "baseSchemaVersion": 48, "basePayloadSha256": payload_sha256(base),
        "clearingBootstrapSamples": bootstrap_samples,
        "previousOfficialHighLevelClearingPresent": "officialHighLevelClearing" in base["summary"]["method"],
        "previousOfficialHighLevelClearing": copy.deepcopy(base["summary"]["method"].get("officialHighLevelClearing"))}
    result["summary"]["method"][NAMESPACE] = marker
    for _, key in MODES:
        for row in result[key]:
            row["scoringVariants"] = {"general": general_scoring_view(row)}
            row["pumbilityVariants"] = {"general": copy.deepcopy(row["tierMetrics"]["pumbility"])}
    result["summary"]["method"]["officialHighLevelClearing"] = apply_official_high_level_clearing(
        result, capture, bootstrap_samples=bootstrap_samples)
    for _, key in MODES:
        for row in result[key]:
            if key == "doubles" and row["level"] >= 26:
                _general_composite(row)
        _rank_secondary(result[key], "clearing")
        _rank_secondary(result[key], "pumbility")
        for row, before in zip(result[key], base[key]):
            row["pumbilityVariants"]["general"] = copy.deepcopy(row["tierMetrics"]["pumbility"])
            if row["tierMetrics"] != before["tierMetrics"]:
                row["retainedClearingBaseline"] = copy.deepcopy(before["tierMetrics"])
            else:
                row.pop("retainedClearingBaseline", None)
    result["schemaVersion"] = BASE_SCHEMA_VERSION
    validate_retained_baseline(result)
    return result


def recover_retained_baseline(payload: Mapping[str, Any]) -> dict:
    result = copy.deepcopy(payload)
    marker = result["summary"]["method"].pop(NAMESPACE)
    if marker["previousOfficialHighLevelClearingPresent"]:
        result["summary"]["method"]["officialHighLevelClearing"] = marker["previousOfficialHighLevelClearing"]
    else:
        result["summary"]["method"].pop("officialHighLevelClearing", None)
    result["schemaVersion"] = 48
    for _, key in MODES:
        for row in result.get(key, []):
            row.pop("scoringVariants", None)
            row.pop("pumbilityVariants", None)
            if "retainedClearingBaseline" in row:
                row["tierMetrics"] = row.pop("retainedClearingBaseline")
    return result


def validate_retained_baseline(payload: Mapping[str, Any]) -> dict:
    if payload.get("schemaVersion") != BASE_SCHEMA_VERSION:
        raise ValueError("The retained baseline schema differs.")
    validate_marker(payload)
    if payload_sha256(recover_retained_baseline(payload)) != payload["summary"]["method"][NAMESPACE].get("basePayloadSha256"):
        raise ValueError("The retained baseline cannot recover its exact General predecessor.")
    from pumbility_average import average_difficulties
    for _, key in MODES:
        for row in payload.get(key, []):
            if set(row.get("scoringVariants", {})) != {"general"} or set(row.get("pumbilityVariants", {})) != {"general"}:
                raise ValueError("The retained baseline may contain only General.")
            if row["scoringVariants"]["general"] != general_scoring_view(row):
                raise ValueError("The retained baseline changed General scoring.")
            clearing = row["tierMetrics"]["clearing"]
            composite = row["pumbilityVariants"]["general"]
            if (composite != row["tierMetrics"]["pumbility"]
                    or composite["estimatedDifficulty"] != average_difficulties(
                        row["estimatedDifficulty"], clearing["estimatedDifficulty"])):
                raise ValueError("The retained General Pumbility is not its component mean.")
    return dict(payload)
