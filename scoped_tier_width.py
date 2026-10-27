"""Local final-width override for Scoring and Clearing on S24+ and D25+."""
from __future__ import annotations
from retained_tiers import scoring_goals

import copy
from datetime import datetime, timezone
from typing import Any, Mapping

from combined_official_clearing import _rank_pumbility, _refresh_general
from extended_official_clearing import _need
from sss_scoring import MODES, _composite, _derived, payload_sha256
from tier_postprocessing import FIT_FIELDS, _rank_secondary, compression_factor

SCHEMA_VERSION = 60
BASE_SCHEMA_VERSION = 59
NAMESPACE = "scopedTierWidth"
STAGE = "scopedTierWidthBaseline"
METHOD = "scoped-final-component-width-v1"
SCOPE = [{"mode": "Single", "minimumLevel": 24}, {"mode": "Double", "minimumLevel": 25}]


def _target(row):
    return row.get("type") == "Single" and row.get("level", 0) >= 24 or row.get("type") == "Double" and row.get("level", 0) >= 25


def _copy(base):
    result = dict(base)
    result["summary"] = dict(base["summary"])
    result["summary"]["method"] = dict(base["summary"]["method"])
    for _, key in MODES:
        result[key] = []
        for original in base[key]:
            if not _target(original):
                result[key].append(original)
                continue
            row = dict(original)
            for field in ("scoringVariants", "tierMetrics", "pumbilityVariants"):
                row[field] = copy.deepcopy(original[field])
            result[key].append(row)
    return result


def _widen(metric, level, ratio):
    midpoint = level + .5
    def scaled(value):
        return None if value is None else midpoint + ratio * (value-midpoint)
    metric.update(_derived(scaled(metric["estimatedDifficulty"]), level))
    for side in ("Low", "High"):
        field = f"difficultyCi95{side}"
        if field in metric:
            metric[field] = scaled(metric[field])
            metric[f"difficultyDeltaCi95{side}"] = None if metric[field] is None else metric[field]-midpoint
    if metric.get("defaultedAtCap"):
        metric["capDefaultDifficulty"] = metric["estimatedDifficulty"]


def _calculate(base):
    result = _copy(base)
    ratio = 1. / compression_factor(base)
    for _, key in MODES:
        rows = [r for r in result[key] if _target(r)]
        for row in rows:
            _need(STAGE not in row, "Scoped width cannot be applied twice.")
            row[STAGE] = {"chart": {field: copy.deepcopy(row[field]) for field in FIT_FIELDS if field in row},
                **{field: copy.deepcopy(row[field]) for field in ("scoringVariants", "tierMetrics", "pumbilityVariants")}}
            _widen(row, row["level"], ratio)
            for scoring in row["scoringVariants"].values():
                _widen(scoring, row["level"], ratio)
            _widen(row["tierMetrics"]["clearing"], row["level"], ratio)
            _widen(row["tierMetrics"]["pumbility"], row["level"], ratio)
            _refresh_general(row)
            for goal in scoring_goals(base)[1:]:
                row["pumbilityVariants"][goal] = _composite(row, row["scoringVariants"][goal])
                row["pumbilityVariants"][goal]["intervalMethod"] = "conditional-scoring-fixed-clearing-and-scoped-width-v1"
        _rank_secondary(rows, "clearing")
        for goal in scoring_goals(base):
            _rank_pumbility(rows, goal)
        for row in rows:
            row["tierMetrics"]["pumbility"] = copy.deepcopy(row["pumbilityVariants"]["general"])
    return result


def apply_scoped_tier_width(base: Mapping[str, Any]) -> tuple[dict, dict]:
    _need(base.get("schemaVersion") in (BASE_SCHEMA_VERSION, 159) and STAGE not in base
        and NAMESPACE not in base.get("summary", {}).get("method", {}), "Scoped width requires the schema-59 predecessor.")
    result = _calculate(base)
    result[STAGE] = {"top": {key: base[key] for key in ("generatedAtUtc",) if key in base},
        "summary": {key: base["summary"][key] for key in ("generatedAtUtc",) if key in base["summary"]}}
    result["schemaVersion"] = 160 if base["schemaVersion"] == 159 else SCHEMA_VERSION
    result["generatedAtUtc"] = result["summary"]["generatedAtUtc"] = (base["generatedAtUtc"] if base["schemaVersion"] >= 100 else datetime.now(timezone.utc).isoformat())
    result["summary"]["method"][NAMESPACE] = {"version": 1, "method": METHOD, "localOnly": base["schemaVersion"] < 100,
        "baseSchemaVersion": base["schemaVersion"], "basePayloadSha256": payload_sha256(base), "scope": copy.deepcopy(SCOPE),
        "previousFactor": compression_factor(base), "factor": 1., "ratio": 1./compression_factor(base),
        "components": ["scoring", "clearing"], "application": "final-component-distance-from-current-official-midpoint",
        "rankPolicy": "preserve-fitted-scoring-order-rerank-derived-folder-metrics", "pumbility": "full-precision-component-mean",
        "dataFetches": 0, "modelRefits": 0}
    validate_scoped_tier_width(result, base)
    return result, scoped_width_report(result, base)


def recover_scoped_tier_width(payload):
    _need(payload.get("schemaVersion") in (SCHEMA_VERSION, 160), "Scoped width recovery requires schema 60.")
    result = _copy(payload)
    saved = result.pop(STAGE)
    result.pop("generatedAtUtc", None)
    result.update(saved["top"])
    result["summary"].pop("generatedAtUtc", None)
    result["summary"].update(saved["summary"])
    result["summary"]["method"].pop(NAMESPACE)
    result["schemaVersion"] = 159 if payload["schemaVersion"] == 160 else BASE_SCHEMA_VERSION
    for _, key in MODES:
        for row in result[key]:
            if not _target(row):
                _need(STAGE not in row, "Scoped width recovery appeared outside its scope.")
                continue
            old = row.pop(STAGE)
            for field in FIT_FIELDS:
                row.pop(field, None)
            row.update(old["chart"])
            for field in ("scoringVariants", "tierMetrics", "pumbilityVariants"):
                row[field] = old[field]
    return result


def validate_scoped_tier_width(payload, original=None):
    base = recover_scoped_tier_width(payload)
    m = payload["summary"]["method"][NAMESPACE]
    _need(m == {"version": 1, "method": METHOD, "localOnly": base["schemaVersion"] < 100, "baseSchemaVersion": base["schemaVersion"],
        "basePayloadSha256": payload_sha256(base), "scope": SCOPE, "previousFactor": compression_factor(base), "factor": 1.,
        "ratio": 1./compression_factor(base), "components": ["scoring", "clearing"],
        "application": "final-component-distance-from-current-official-midpoint",
        "rankPolicy": "preserve-fitted-scoring-order-rerank-derived-folder-metrics", "pumbility": "full-precision-component-mean",
        "dataFetches": 0, "modelRefits": 0} and (original is None or base == original), "Scoped width policy or recovery differs.")
    expected = _calculate(base)
    for field in payload.keys() | expected.keys():
        if field in ("schemaVersion", "generatedAtUtc", "summary", STAGE):
            continue
        _need(payload.get(field) == expected.get(field), "Scoped width changed protected values or disagrees with its affine map.")
    _need(payload["generatedAtUtc"] == payload["summary"]["generatedAtUtc"], "Scoped width generation differs.")
    return base


def scoped_width_report(payload, base):
    charts = []
    for _, key in MODES:
        before = {r["chartId"]: r for r in base[key]}
        for row in payload[key]:
            if not _target(row):
                continue
            old = before[row["chartId"]]
            charts.append({"chartId": row["chartId"], "songName": row["songName"], "mode": row["type"], "level": row["level"],
                "clearing": {"before": old["tierMetrics"]["clearing"]["estimatedDifficulty"], "after": row["tierMetrics"]["clearing"]["estimatedDifficulty"]},
                "scoring": {goal: {"before": old["scoringVariants"][goal]["estimatedDifficulty"], "after": row["scoringVariants"][goal]["estimatedDifficulty"]} for goal in scoring_goals(base)},
                "pumbility": {goal: {"before": old["pumbilityVariants"][goal]["estimatedDifficulty"], "after": row["pumbilityVariants"][goal]["estimatedDifficulty"]} for goal in scoring_goals(base)}})
    return {"schemaVersion": SCHEMA_VERSION, "localOnly": base["schemaVersion"] < 100, "previousFactor": compression_factor(base), "factor": 1.,
        "scope": SCOPE, "components": ["scoring", "clearing"], "charts": charts, "affectedCharts": len(charts),
        "baseRecovered": True, "lowerFolderValuesPreserved": True, "dataFetches": 0, "modelRefits": 0}
