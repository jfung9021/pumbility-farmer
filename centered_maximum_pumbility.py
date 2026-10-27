"""Local Pumbility maximum followed by an independent final folder median shift."""
from __future__ import annotations
from retained_tiers import scoring_goals

from collections import defaultdict
from datetime import datetime, timezone

from extended_official_clearing import _need
from final_folder_centering import GOALS, _location
from sss_scoring import _derived, payload_sha256

SCHEMA_VERSION = 62
BASE_SCHEMA_VERSION = 61
NAMESPACE = "centeredMaximumPumbility"
STAGE = "centeredMaximumPumbilityBaseline"
METHOD = "folder-centered-maximum-pumbility-v1"
CALCULATION = "max(scoring difficulty, clearing difficulty) + per-folder median offset before rounding"
INTERVAL_METHOD = "conditional-scoring-fixed-clearing-and-folder-centering-v1"


def _maximum(scoring, clearing):
    return None if scoring is None or clearing is None else max(scoring, clearing)


def _copy(base):
    result = dict(base)
    method = dict(base["summary"]["method"])
    if method.get("tierMetrics") is not None:
        method["tierMetrics"] = {**method["tierMetrics"], "pumbility": dict(method["tierMetrics"]["pumbility"])}
    result["summary"] = {**base["summary"], "method": method}
    for key in ("singles", "doubles"):
        result[key] = [{**r, "pumbilityVariants": {g: dict(v) for g, v in r["pumbilityVariants"].items()},
            "tierMetrics": {**r["tierMetrics"], "pumbility": dict(r["tierMetrics"]["pumbility"])}} for r in base[key]]
    return result


def _calculate(base):
    result, folders = _copy(base), {}
    for key, mode in (("singles", "Single"), ("doubles", "Double")):
        groups = defaultdict(list)
        for original, row in zip(base[key], result[key]):
            _need(STAGE not in row, "Centered maximum cannot be applied twice.")
            row[STAGE] = {"pumbility": original["tierMetrics"]["pumbility"], "pumbilityVariants": original["pumbilityVariants"]}
            groups[row["level"]].append(row)
        folders[mode] = {}
        for level, rows in sorted(groups.items()):
            folders[mode][str(level)] = {}
            for goal in scoring_goals(base):
                record = _location([_maximum(r["scoringVariants"][goal]["estimatedDifficulty"],
                    r["tierMetrics"]["clearing"]["estimatedDifficulty"]) for r in rows], level)
                folders[mode][str(level)][goal] = record
                offset = record["offset"]
                for row in rows:
                    scoring, clearing = row["scoringVariants"][goal], row["tierMetrics"]["clearing"]
                    raw = _maximum(scoring["estimatedDifficulty"], clearing["estimatedDifficulty"])
                    metric = row["pumbilityVariants"][goal]
                    metric.update(_derived(None if raw is None else raw+offset, level), aggregation="centered-maximum",
                        intervalMethod=INTERVAL_METHOD, levelRank=None, levelComparisonCharts=None)
                    metric.pop("finalFolderCentering", None)
                    metric[NAMESPACE] = {"version": 1, "unshiftedDifficulty": raw, "offset": offset}
                    for side in ("Low", "High"):
                        endpoint = None if raw is None else _maximum(scoring.get(f"difficultyCi95{side}"), clearing["estimatedDifficulty"])
                        value = None if endpoint is None else endpoint+offset
                        metric[f"difficultyCi95{side}"] = value
                        metric[f"difficultyDeltaCi95{side}"] = None if value is None else value-level-.5
                ranked = sorted((r for r in rows if r["pumbilityVariants"][goal]["estimatedDifficulty"] is not None),
                    key=lambda r: (r["pumbilityVariants"][goal]["estimatedDifficulty"], r.get("songName") or "", str(r["chartId"])))
                for rank, row in enumerate(ranked, 1):
                    row["pumbilityVariants"][goal].update(levelRank=rank, levelComparisonCharts=len(ranked))
            for row in rows:
                row["tierMetrics"]["pumbility"] = dict(row["pumbilityVariants"]["general"])
    return result, folders


def _policy(base, folders):
    return {"version": 1, "method": METHOD, "localOnly": base["schemaVersion"] < 100, "baseSchemaVersion": base["schemaVersion"],
        "basePayloadSha256": payload_sha256(base), "aggregation": "centered-maximum", "requiresBoth": True,
        "calculation": CALCULATION, "scope": ["Single", "Double"], "goals": list(scoring_goals(base)),
        "population": "all-finite-component-pairs-including-provisional", "reference": "current-official-level-plus-0.5",
        "median": "middle-value-or-mean-of-middle-pair", "unrated": "excluded-and-preserved", "ties": "preserved",
        "filters": "frozen-full-official-folder", "components": "preserve-schema61-scoring-and-clearing-exactly",
        "rankPolicy": "rerank-final-pumbility-by-value-song-name-chart-id-preserve-initial-fits",
        "intervalMethod": INTERVAL_METHOD, "dataFetches": 0, "modelRefits": 0, "folders": folders}


def apply_centered_maximum_pumbility(base):
    _need(base.get("schemaVersion") in (BASE_SCHEMA_VERSION, 161) and STAGE not in base
        and NAMESPACE not in base["summary"]["method"], "Centered maximum requires schema 61.")
    result, folders = _calculate(base)
    old_policy = base["summary"]["method"].get("tierMetrics", {}).get("pumbility")
    result[STAGE] = {"top": {k: base[k] for k in ("generatedAtUtc",) if k in base},
        "summary": {k: base["summary"][k] for k in ("generatedAtUtc",) if k in base["summary"]}, "pumbilityPolicy": old_policy}
    if old_policy is not None:
        result["summary"]["method"]["tierMetrics"]["pumbility"] = {**old_policy, "version": 6,
            "aggregation": "centered-maximum", "calculation": CALCULATION}
    result["summary"]["method"][NAMESPACE] = _policy(base, folders)
    result["schemaVersion"] = 162 if base["schemaVersion"] == 161 else SCHEMA_VERSION
    result["generatedAtUtc"] = result["summary"]["generatedAtUtc"] = (base["generatedAtUtc"] if base["schemaVersion"] >= 100 else datetime.now(timezone.utc).isoformat())
    validate_centered_maximum_pumbility(result, base)
    return result, maximum_report(result, base)


def recover_centered_maximum_pumbility(payload):
    _need(payload.get("schemaVersion") in (SCHEMA_VERSION, 162), "Centered maximum recovery requires schema 62.")
    result = _copy(payload)
    saved = result.pop(STAGE)
    result.pop("generatedAtUtc", None)
    result.update(saved["top"])
    result["summary"].pop("generatedAtUtc", None)
    result["summary"].update(saved["summary"])
    result["summary"]["method"].pop(NAMESPACE)
    if saved["pumbilityPolicy"] is not None:
        result["summary"]["method"]["tierMetrics"]["pumbility"] = saved["pumbilityPolicy"]
    result["schemaVersion"] = 161 if payload["schemaVersion"] == 162 else BASE_SCHEMA_VERSION
    for key in ("singles", "doubles"):
        for row in result[key]:
            old = row.pop(STAGE)
            _need(set(old) == {"pumbility", "pumbilityVariants"}, "Centered maximum recovery fields differ.")
            row["tierMetrics"]["pumbility"], row["pumbilityVariants"] = old["pumbility"], old["pumbilityVariants"]
    return result


def validate_centered_maximum_pumbility(payload, original=None):
    base = recover_centered_maximum_pumbility(payload)
    _need(original is None or base == original, "Centered maximum baseline differs.")
    expected, folders = _calculate(base)
    _need(payload["summary"]["method"][NAMESPACE] == _policy(base, folders), "Centered maximum policy or population differs.")
    for key in ("singles", "doubles", "coop"):
        _need(payload.get(key) == expected.get(key), "Centered maximum changed components, evidence, ranks or values incorrectly.")
    method = dict(payload["summary"]["method"])
    method.pop(NAMESPACE)
    if method.get("tierMetrics") is not None:
        old = base["summary"]["method"]["tierMetrics"]["pumbility"]
        _need(method["tierMetrics"]["pumbility"] == {**old, "version": 6, "aggregation": "centered-maximum", "calculation": CALCULATION},
            "Centered maximum declared Pumbility policy differs.")
        method["tierMetrics"] = {**method["tierMetrics"], "pumbility": old}
    _need(method == base["summary"]["method"], "Centered maximum protected methods differ.")
    _need(payload["generatedAtUtc"] == payload["summary"]["generatedAtUtc"], "Centered maximum generation differs.")
    return base


def maximum_report(payload, base):
    folders, charts = [], []
    for key, mode in (("singles", "Single"), ("doubles", "Double")):
        for level, goals in payload["summary"]["method"][NAMESPACE]["folders"][mode].items():
            rows = [r for r in payload[key] if r["level"] == int(level)]
            for goal, record in goals.items():
                def counts(values):
                    values = [v for v in values if v is not None]
                    m = int(level)+.5
                    return {"below": sum(v < m-1e-8 for v in values), "at": sum(abs(v-m) <= 1e-8 for v in values), "above": sum(v > m+1e-8 for v in values)}
                old_values = [r[STAGE]["pumbilityVariants"][goal]["estimatedDifficulty"] for r in rows]
                raw_values = [r["pumbilityVariants"][goal][NAMESPACE]["unshiftedDifficulty"] for r in rows]
                values = [r["pumbilityVariants"][goal]["estimatedDifficulty"] for r in rows]
                folders.append({"mode": mode, "level": int(level), "goal": goal, **record,
                    "previous": counts(old_values), "unshiftedMaximum": counts(raw_values), "current": counts(values),
                    "finalMedian": None if not record["ratedCharts"] else int(level)+.5})
        for old, row in zip(base[key], payload[key]):
            goals = {}
            for goal in scoring_goals(base):
                before, after = old["pumbilityVariants"][goal], row["pumbilityVariants"][goal]
                a, b = before["estimatedDifficulty"], after["estimatedDifficulty"]
                goals[goal] = {"before": a, "after": b, "change": None if a is None or b is None else b-a,
                    "unshiftedMaximum": after[NAMESPACE]["unshiftedDifficulty"], "offset": after[NAMESPACE]["offset"],
                    "oldRank": before.get("levelRank"), "newRank": after.get("levelRank")}
            charts.append({"chartId": row["chartId"], "songName": row["songName"], "mode": mode, "level": row["level"], "goals": goals})
    return {"schemaVersion": SCHEMA_VERSION, "localOnly": base["schemaVersion"] < 100, "officialFolders": sum(len(v) for v in payload["summary"]["method"][NAMESPACE]["folders"].values()),
        "folderGoals": len(folders), "folders": folders, "charts": charts, "scoringPreserved": True, "clearingPreserved": True,
        "baseRecovered": True, "aggregation": "centered-maximum", "dataFetches": 0, "modelRefits": 0}
