"""Local final medians for every Singles/Doubles folder and rating view."""
from __future__ import annotations
from retained_tiers import scoring_goals

import copy
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median

from extended_official_clearing import _need
from sss_scoring import _derived, payload_sha256

SCHEMA_VERSION = 61
BASE_SCHEMA_VERSION = 60
NAMESPACE = "finalFolderCentering"
STAGE = "finalFolderCenteringBaseline"
METHOD = "final-all-rated-folder-median-centering-v1"
GOALS = ("general", "sss", "sssplus")
POINT_FIELDS = ("estimatedDifficulty", "difficultyDelta", "effectBand", "effectBandRank")
CI_FIELDS = tuple(f"{prefix}Ci95{side}" for side in ("Low", "High") for prefix in ("difficulty", "difficultyDelta"))
STATE_FIELDS = (*POINT_FIELDS, *CI_FIELDS, "aggregation", "intervalMethod", "capDefaultDifficulty", NAMESPACE)
CALCULATION = "(scoring difficulty + clearing difficulty) / 2 + per-folder median offset before rounding"


def _copy(base):
    result = dict(base)
    result["summary"] = {**base["summary"], "method": dict(base["summary"]["method"])}
    tiers = base["summary"]["method"].get("tierMetrics")
    if tiers is not None:
        result["summary"]["method"]["tierMetrics"] = {**tiers, "pumbility": dict(tiers["pumbility"])}
    for key in ("singles", "doubles"):
        result[key] = [{**r, "scoringVariants": {g: dict(v) for g, v in r["scoringVariants"].items()},
            "tierMetrics": {g: dict(v) for g, v in r["tierMetrics"].items()},
            "pumbilityVariants": {g: dict(v) for g, v in r["pumbilityVariants"].items()}} for r in base[key]]
    return result


def _state(metric):
    return {key: copy.deepcopy(metric[key]) for key in STATE_FIELDS if key in metric}


def _restore(metric, saved):
    for field in STATE_FIELDS:
        metric.pop(field, None)
    metric.update(saved)


def _mean(a, b):
    return None if a is None or b is None else (a+b)/2


def _location(values, level):
    values = [v for v in values if v is not None]
    center = median(values) if values else None
    return {"ratedCharts": len(values), "medianBefore": center, "offset": 0. if center is None else level+.5-center}


def _translate(metric, level, offset, *, value=None, composite=False):
    point = metric["estimatedDifficulty"]
    point = None if point is None else point + offset
    if composite:
        point = value
    metric.update(_derived(point, level))
    for side in ("Low", "High"):
        field = f"difficultyCi95{side}"
        if field in metric:
            metric[field] = None if metric[field] is None else metric[field] + offset
            metric[f"difficultyDeltaCi95{side}"] = None if metric[field] is None else metric[field]-level-.5
    metric[NAMESPACE] = {"version": 1, "offset": offset}
    if metric.get("defaultedAtCap"):
        metric["capDefaultDifficulty"] = point


def _calculate(base):
    result, folders = _copy(base), {}
    for key, mode in (("singles", "Single"), ("doubles", "Double")):
        groups = defaultdict(list)
        for row in result[key]:
            _need(STAGE not in row, "Final centering cannot be applied twice.")
            row[STAGE] = {"chart": _state(row), "clearing": _state(row["tierMetrics"]["clearing"]),
                "pumbility": _state(row["tierMetrics"]["pumbility"]),
                "scoringVariants": {g: _state(row["scoringVariants"][g]) for g in scoring_goals(base)},
                "pumbilityVariants": {g: _state(row["pumbilityVariants"][g]) for g in scoring_goals(base)}}
            groups[row["level"]].append(row)
        folders[mode] = {}
        for level, rows in sorted(groups.items()):
            scoring = {g: _location([r["scoringVariants"][g]["estimatedDifficulty"] for r in rows], level) for g in scoring_goals(base)}
            clearing = _location([r["tierMetrics"]["clearing"]["estimatedDifficulty"] for r in rows], level)
            for row in rows:
                _translate(row, level, scoring["general"]["offset"])
                for goal in scoring_goals(base):
                    _translate(row["scoringVariants"][goal], level, scoring[goal]["offset"])
                _translate(row["tierMetrics"]["clearing"], level, clearing["offset"])
            pumbility = {g: _location([_mean(r["scoringVariants"][g]["estimatedDifficulty"],
                r["tierMetrics"]["clearing"]["estimatedDifficulty"]) for r in rows], level) for g in scoring_goals(base)}
            for row in rows:
                for goal in scoring_goals(base):
                    metric = row["pumbilityVariants"][goal]
                    mean = _mean(row["scoringVariants"][goal]["estimatedDifficulty"], row["tierMetrics"]["clearing"]["estimatedDifficulty"])
                    offset = pumbility[goal]["offset"]
                    total = (scoring[goal]["offset"]+clearing["offset"])/2+offset
                    _translate(metric, level, total, value=None if mean is None else mean+offset, composite=True)
                    metric.update(aggregation="centered-average", intervalMethod="conditional-fixed-final-folder-centering-v1")
                    metric[NAMESPACE] = {"version": 1, "offset": offset, "componentMeanDifficulty": mean}
                row["tierMetrics"]["pumbility"] = dict(row["pumbilityVariants"]["general"])
            folders[mode][str(level)] = {"scoring": scoring, "clearing": clearing, "pumbility": pumbility}
    return result, folders


def _policy(base, folders):
    return {"version": 1, "method": METHOD, "localOnly": base["schemaVersion"] < 100, "baseSchemaVersion": base["schemaVersion"],
        "basePayloadSha256": payload_sha256(base), "scope": ["Single", "Double"],
        "population": "all-finite-current-folder-ratings-including-provisional", "reference": "current-official-level-plus-0.5",
        "median": "middle-value-or-mean-of-middle-pair", "minimumRatedCharts": 1,
        "unrated": "excluded-and-preserved", "ties": "preserved", "filters": "frozen-full-official-folder",
        "width": "unchanged", "evidence": "unchanged", "rankPolicy": "preserve-within-folder-order-and-fitted-ranks",
        "intervals": "translate-existing-conditional-intervals-no-new-evidence",
        "pumbility": "center-component-mean-independently", "calculation": CALCULATION,
        "dataFetches": 0, "modelRefits": 0, "folders": folders}


def apply_final_folder_centering(base):
    _need(base.get("schemaVersion") in (BASE_SCHEMA_VERSION, 160) and STAGE not in base
        and NAMESPACE not in base["summary"]["method"], "Final centering requires schema 60.")
    result, folders = _calculate(base)
    old_policy = base["summary"]["method"].get("tierMetrics", {}).get("pumbility")
    result[STAGE] = {"top": {k: base[k] for k in ("generatedAtUtc",) if k in base},
        "summary": {k: base["summary"][k] for k in ("generatedAtUtc",) if k in base["summary"]}, "pumbilityPolicy": old_policy}
    if old_policy is not None:
        result["summary"]["method"]["tierMetrics"]["pumbility"] = {**old_policy, "version": 5,
            "aggregation": "centered-average", "calculation": CALCULATION}
    result["summary"]["method"][NAMESPACE] = _policy(base, folders)
    result["schemaVersion"] = 161 if base["schemaVersion"] == 160 else SCHEMA_VERSION
    result["generatedAtUtc"] = result["summary"]["generatedAtUtc"] = (base["generatedAtUtc"] if base["schemaVersion"] >= 100 else datetime.now(timezone.utc).isoformat())
    validate_final_folder_centering(result, base)
    return result, centering_report(result, base)


def recover_final_folder_centering(payload):
    _need(payload.get("schemaVersion") in (SCHEMA_VERSION, 161), "Final centering recovery requires schema 61.")
    result = _copy(payload)
    saved = result.pop(STAGE)
    result.pop("generatedAtUtc", None)
    result.update(saved["top"])
    result["summary"].pop("generatedAtUtc", None)
    result["summary"].update(saved["summary"])
    result["summary"]["method"].pop(NAMESPACE)
    if saved["pumbilityPolicy"] is not None:
        result["summary"]["method"]["tierMetrics"]["pumbility"] = saved["pumbilityPolicy"]
    result["schemaVersion"] = 160 if payload["schemaVersion"] == 161 else BASE_SCHEMA_VERSION
    for key in ("singles", "doubles"):
        for row in result[key]:
            old = row.pop(STAGE)
            _restore(row, old["chart"])
            for name in ("clearing", "pumbility"):
                _restore(row["tierMetrics"][name], old[name])
            for field in ("scoringVariants", "pumbilityVariants"):
                for goal in scoring_goals(payload):
                    _restore(row[field][goal], old[field][goal])
    return result


def validate_final_folder_centering(payload, original=None):
    base = recover_final_folder_centering(payload)
    _need(original is None or base == original, "Final centering baseline differs.")
    expected, folders = _calculate(base)
    _need(payload["summary"]["method"][NAMESPACE] == _policy(base, folders), "Final centering policy or population differs.")
    for key in ("singles", "doubles", "coop"):
        _need(payload.get(key) == expected.get(key), "Final centering changed evidence, ranks, width or ratings incorrectly.")
    method = dict(payload["summary"]["method"])
    method.pop(NAMESPACE)
    if method.get("tierMetrics") is not None:
        policy = {**base["summary"]["method"]["tierMetrics"]["pumbility"], "version": 5,
            "aggregation": "centered-average", "calculation": CALCULATION}
        _need(method["tierMetrics"]["pumbility"] == policy, "Final centering Pumbility declaration differs.")
        method["tierMetrics"] = {**method["tierMetrics"], "pumbility": base["summary"]["method"]["tierMetrics"]["pumbility"]}
    _need(method == base["summary"]["method"], "Final centering protected methods differ.")
    _need(payload["generatedAtUtc"] == payload["summary"]["generatedAtUtc"], "Final centering generation differs.")
    return base


def centering_report(payload, base):
    report = []
    for key, mode in (("singles", "Single"), ("doubles", "Double")):
        for level, record in payload["summary"]["method"][NAMESPACE]["folders"][mode].items():
            before = [r for r in base[key] if r["level"] == int(level)]
            after = [r for r in payload[key] if r["level"] == int(level)]
            for name in ("clearing", "scoring", "pumbility"):
                for goal in ((None,) if name == "clearing" else scoring_goals(base)):
                    def values(rows):
                        result = [(r["tierMetrics"]["clearing"] if name == "clearing" else r[name+"Variants"][goal])["estimatedDifficulty"] for r in rows]
                        return [v for v in result if v is not None]
                    old, new = values(before), values(after)
                    def counts(vals):
                        midpoint = int(level)+.5
                        return {"below": sum(v < midpoint-1e-8 for v in vals), "at": sum(abs(v-midpoint) <= 1e-8 for v in vals), "above": sum(v > midpoint+1e-8 for v in vals)}
                    center = median(old) if old else None
                    report.append({"mode": mode, "level": int(level), "metric": name, "goal": goal, "ratedCharts": len(new),
                        "medianBefore": center, "medianAfter": median(new) if new else None,
                        "totalOffset": 0. if center is None else int(level)+.5-center,
                        "before": counts(old), "after": counts(new)})
    return {"schemaVersion": SCHEMA_VERSION, "localOnly": base["schemaVersion"] < 100, "officialFolders": sum(len(v) for v in payload["summary"]["method"][NAMESPACE]["folders"].values()),
        "metricFolders": len(report), "folders": report, "widthsPreserved": True, "evidencePreserved": True, "baseRecovered": True,
        "pumbility": "centered-average", "dataFetches": 0, "modelRefits": 0}
