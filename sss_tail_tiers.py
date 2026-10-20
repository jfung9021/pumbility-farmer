"""Recoverable final SSS tail safeguard over immutable schema-57/58 results."""
from __future__ import annotations

import copy
import math
from statistics import median
from typing import Any, Mapping

from canonical_json import canonical_json_sha256 as digest
from scoring_targets import SSS
from sss_scoring import MODES, _composite, rank_variants
from target_tail_policy import TAIL_PARAMETERS, apply_tail_policy, finite

SCHEMA_VERSION = 59
SCRIPT_VERSION = "6.23.0-sss-bounded-tails"
NAMESPACE = "sssTailPolicy"
METHOD = "sss-target-bounded-tails-v1"
BASE_SCHEMAS = (57, 58)


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _rows(payload: Mapping[str, Any]) -> list[dict]:
    return [row for _, key in MODES for row in payload.get(key, [])]


def _validate_predecessor(payload: Mapping[str, Any]) -> None:
    if payload["schemaVersion"] == 58:
        from scoring_goal_tiers import validate_goal_tiers
        validate_goal_tiers(payload)
    else:
        from source_aware_tiers import validate_source_aware_tiers
        from submitted_sssplus_calibration import validate_submitted_sssplus_calibration
        validate_source_aware_tiers(validate_submitted_sssplus_calibration(payload))


def _apply(base: Mapping[str, Any]) -> dict:
    # Historical rollback trees are large and immutable. Copy only containers and
    # the active SSS components that this terminal stage can modify.
    result = dict(base)
    result["summary"] = copy.deepcopy(base["summary"])
    for _, key in MODES:
        result[key] = []
        for original in base.get(key, []):
            row = dict(original)
            for name in ("scoringVariants", "pumbilityVariants"):
                row[name] = dict(original[name])
                row[name]["sss"] = copy.deepcopy(original[name]["sss"])
            result[key].append(row)
    tail = apply_tail_policy(result, goal="sss", expected_model_method=SSS.model_method)
    for _, key in MODES:
        for row in result[key]:
            scoring = row["scoringVariants"]["sss"]
            composite = _composite(row, scoring)
            composite["intervalMethod"] = ("conditional-target-tail-fixed-clearing-and-calibration-v1"
                if scoring["scoringMethod"] == SSS.model_method
                else "unavailable-after-sss-refinement-v1")
            row["pumbilityVariants"]["sss"] = composite
        rank_variants(result[key], goal="sss")
    result["schemaVersion"] = SCHEMA_VERSION
    result["summary"]["scriptVersion"] = SCRIPT_VERSION
    result["summary"]["method"][NAMESPACE] = {
        "version": 1, "method": METHOD, "baseSchemaVersion": base["schemaVersion"],
        "baseScriptVersion": base["summary"].get("scriptVersion"),
        "basePayloadSha256": digest(base), "tailPolicy": tail}
    return result


def apply_sss_tail_tiers(payload: Mapping[str, Any]) -> dict:
    """Project final SSS once; public/general/other-target inputs stay frozen."""
    _need(payload.get("schemaVersion") in BASE_SCHEMAS,
          "SSS tail policy requires a frozen schema-57 or schema-58 predecessor.")
    _need(not payload.get("tierListView"), "SSS tail policy requires complete chart evidence.")
    _need(NAMESPACE not in payload["summary"]["method"]
          and all("sssTailBaseline" not in row and "scoringTailPolicy" not in row["scoringVariants"]["sss"]
                  for row in _rows(payload)), "SSS tail policy cannot be applied twice.")
    _need(len({str(row["chartId"]) for row in _rows(payload)}) == len(_rows(payload)),
          "Duplicate chart identity in SSS tail policy.")
    result = _apply(payload)
    validate_sss_tail_tiers(result, full=False)
    return result


def recover_sss_tail_tiers(payload: Mapping[str, Any]) -> dict:
    """Restore the exact preceding generation without changing its fields."""
    _need(payload.get("schemaVersion") == SCHEMA_VERSION, "SSS tail recovery requires schema 59.")
    namespace = payload["summary"]["method"].get(NAMESPACE, {})
    _need(namespace.get("version") == 1 and namespace.get("baseSchemaVersion") in BASE_SCHEMAS,
          "Missing SSS tail predecessor identity.")
    result = dict(payload)
    result["summary"] = copy.deepcopy(payload["summary"])
    result["summary"]["method"].pop(NAMESPACE)
    prior = namespace.get("baseScriptVersion")
    if prior is None:
        result["summary"].pop("scriptVersion", None)
    else:
        result["summary"]["scriptVersion"] = prior
    result["schemaVersion"] = namespace["baseSchemaVersion"]
    for _, key in MODES:
        result[key] = []
        for original in payload.get(key, []):
            row = dict(original)
            baseline = row.pop("sssTailBaseline", None)
            _need(isinstance(baseline, Mapping) and set(baseline) == {"scoring", "pumbility"},
                  "Missing SSS tail chart baseline.")
            for name, saved in (("scoringVariants", "scoring"), ("pumbilityVariants", "pumbility")):
                row[name] = dict(original[name])
                row[name]["sss"] = copy.deepcopy(baseline[saved])
            result[key].append(row)
    return result


def validate_sss_tail_tiers(payload: Mapping[str, Any], *, full: bool = True) -> dict:
    """Check exact recovery/replay; optionally validate the historical producer."""
    base = recover_sss_tail_tiers(payload)
    namespace = payload["summary"]["method"][NAMESPACE]
    _need(namespace.get("method") == METHOD and payload["summary"].get("scriptVersion") == SCRIPT_VERSION,
          "Incorrect SSS tail policy identity.")
    _need(digest(base) == namespace.get("basePayloadSha256"),
          "SSS tail policy changed a predecessor field.")
    _need(len({str(row["chartId"]) for row in _rows(base)}) == len(_rows(base)),
          "Duplicate chart identity in SSS tail policy.")
    _need(all("scoringTailPolicy" not in row["scoringVariants"]["sss"] for row in _rows(base)),
          "SSS tail predecessor already has a tail projection.")
    expected = _apply(base)
    _need(payload == expected, "SSS tail points, evidence, intervals, composites, ranks or policy differ.")
    tail = namespace["tailPolicy"]
    _need(tail["counts"]["finalAbsAtLeast2"] <= TAIL_PARAMETERS["exceptionLimit"]
          and tail["counts"]["displayedAbsAtLeast2"] <= TAIL_PARAMETERS["exceptionLimit"]
          and not tail["counts"]["finalAbsAbove2_5"], "SSS tail rarity budget was exceeded.")
    for row in _rows(payload):
        scoring = row["scoringVariants"]["sss"]
        low, high = scoring.get("difficultyCi95Low"), scoring.get("difficultyCi95High")
        _need(low is None or high is None or low <= high, "Reversed SSS tail interval.")
    for _, key in MODES:
        levels = {row["level"] for row in payload.get(key, [])}
        for level in levels:
            rows = [row for row in payload[key] if row["level"] == level
                    and finite(row["scoringVariants"]["sss"]["estimatedDifficulty"])]
            if not rows:
                continue
            if any(row["scoringVariants"]["sss"].get("scoringFolderCentering", {}).get("basis") == "folder" for row in rows):
                _need(math.isclose(median(row["scoringVariants"]["sss"]["estimatedDifficulty"] for row in rows),
                                   level + .5, abs_tol=1e-10), "SSS tail policy moved a supported folder median.")
            ordered = sorted(rows, key=lambda row: (row["sssTailBaseline"]["scoring"]["estimatedDifficulty"], str(row["chartId"])))
            _need(all(a["scoringVariants"]["sss"]["estimatedDifficulty"] <= b["scoringVariants"]["sss"]["estimatedDifficulty"]
                      for a, b in zip(ordered, ordered[1:])), "SSS tail policy inverted within-folder order.")
    if full:
        _validate_predecessor(base)
    return base
