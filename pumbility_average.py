"""Versioned full-precision Pumbility means, retaining the maximum generation."""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from typing import Any, Mapping

from piu_misgrade_analyzer import difficulty_effect_band

AVERAGE_CALCULATION = "(scoring difficulty + clearing difficulty) / 2 before rounding"
AVERAGE_SCHEMAS = {48: 47}


def average_policy(base_schema: int) -> dict[str, Any]:
    if base_schema != 47:
        raise ValueError("Pumbility averaging requires the schema-47 midpoint-compressed maximum baseline.")
    return {"version": 2, "aggregation": "average",
            "reference": "current-official-level-plus-0.5", "baseAnalysisSchemaVersion": base_schema,
            "componentValues": "after-midpoint-compression", "requiresBoth": True}


def average_difficulties(scoring: float | None, clearing: float | None) -> float | None:
    if scoring is None or clearing is None:
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in (scoring, clearing)):
        raise ValueError("Pumbility average inputs must be finite or null.")
    return (scoring + clearing) / 2


def _derived(value: float | None, level: int) -> dict[str, Any]:
    delta = None if value is None else value - level - .5
    rank, label = (None, None) if delta is None else difficulty_effect_band(delta)
    return {"estimatedDifficulty": value, "difficultyDelta": delta, "effectBandRank": rank, "effectBand": label}


def _same(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (float, int)) and not isinstance(expected, bool):
        return (isinstance(actual, (float, int)) and not isinstance(actual, bool)
                and math.isfinite(actual) and abs(actual - expected) < 1e-8)
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and actual.keys() == expected.keys()
                and all(_same(actual[key], value) for key, value in expected.items()))
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(_same(a, b) for a, b in zip(actual, expected)))
    return type(actual) is type(expected) and actual == expected


def apply_average_pumbility(payload: Mapping[str, Any]) -> dict[str, Any]:
    base = recover_maximum_pumbility(payload) if payload.get("schemaVersion") in AVERAGE_SCHEMAS else payload
    schema = base.get("schemaVersion")
    if schema != 47:
        raise ValueError("Pumbility averaging requires the schema-47 midpoint-compressed maximum baseline.")
    result = copy.deepcopy(base)
    method = result["summary"]["method"]
    tiers = method["tierMetrics"]
    if tiers["pumbility"].get("aggregation") != "maximum":
        raise ValueError("Pumbility averaging requires a maximum composite baseline.")
    result["schemaVersion"] = 48
    method["pumbilityExperiment"] = {**average_policy(schema), "baseMethod": copy.deepcopy(tiers["pumbility"])}
    tiers["pumbility"] = {"version": 4, "aggregation": "average", "requiresBoth": True,
        "calculation": AVERAGE_CALCULATION,
        "evidence": "weaker component status; separate scoring and clearing support counts"}
    for mode in ("singles", "doubles"):
        folders = defaultdict(list)
        for row in result.get(mode, []):
            composite, clearing = row["tierMetrics"]["pumbility"], row["tierMetrics"]["clearing"]
            composite["maximumBaseline"] = copy.deepcopy(composite)
            composite["aggregation"] = "average"
            value = average_difficulties(row["estimatedDifficulty"], clearing["estimatedDifficulty"])
            composite.update(_derived(value, row["level"]), levelRank=None, levelComparisonCharts=None)
            if "initialFit" in composite:
                composite["initialFit"].update(_derived(average_difficulties(
                    row["initialFit"]["estimatedDifficulty"], clearing["initialFit"]["estimatedDifficulty"]), row["level"]))
            if value is not None:
                folders[row["level"]].append(row)
        for group in folders.values():
            # Schema 48 uses the same portable Unicode code-point tie order in Python and TypeScript.
            group.sort(key=lambda row: (row["tierMetrics"]["pumbility"]["estimatedDifficulty"],
                row["tierMetrics"]["pumbility"].get("initialFit", {}).get("estimatedDifficulty") or 0,
                row.get("songName") or "", row["chartId"]))
            for rank, row in enumerate(group, 1):
                composite = row["tierMetrics"]["pumbility"]
                composite.update(levelRank=rank, levelComparisonCharts=len(group))
                if "initialFit" in composite:
                    composite["initialFit"].update(levelRank=rank, levelComparisonCharts=len(group))
    return result


def recover_maximum_pumbility(payload: Mapping[str, Any]) -> dict[str, Any]:
    schema = payload.get("schemaVersion")
    if schema not in AVERAGE_SCHEMAS:
        raise ValueError("Pumbility average recovery requires schema 48.")
    marker = payload.get("summary", {}).get("method", {}).get("pumbilityExperiment")
    if (not isinstance(marker, dict) or not marker.get("baseMethod")
            or not _same({key: value for key, value in marker.items() if key != "baseMethod"},
                         average_policy(AVERAGE_SCHEMAS[schema]))):
        raise ValueError("Incompatible Pumbility average policy.")
    base = copy.deepcopy(payload)
    base["schemaVersion"] = AVERAGE_SCHEMAS[schema]
    del base["summary"]["method"]["pumbilityExperiment"]
    base["summary"]["method"]["tierMetrics"]["pumbility"] = copy.deepcopy(marker["baseMethod"])
    for mode in ("singles", "doubles"):
        for row in base.get(mode, []):
            original = row["tierMetrics"]["pumbility"].get("maximumBaseline")
            if not isinstance(original, dict) or "maximumBaseline" in original:
                raise ValueError("Pumbility average is missing its maximum baseline.")
            row["tierMetrics"]["pumbility"] = copy.deepcopy(original)
    expected = apply_average_pumbility(base)
    if (not _same(payload["summary"]["method"], expected["summary"]["method"])
            or any(not _same(row["tierMetrics"]["pumbility"], correct["tierMetrics"]["pumbility"])
                   for mode in ("singles", "doubles")
                   for row, correct in zip(payload.get(mode, []), expected.get(mode, [])))):
        raise ValueError("Pumbility average values, support, evidence or ranks disagree with the component mean.")
    return base
