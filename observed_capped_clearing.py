"""Local, recoverable observed-clearer estimates for capped S24/D25 boards."""
from __future__ import annotations
from retained_tiers import scoring_goals

import copy
import math
from datetime import datetime, timezone
from typing import Any, Mapping

from combined_official_clearing import _rank_pumbility, _refresh_general
from extended_official_clearing import _copy_overlay, _need, _same, _target
from final_folder_width import width_value
from official_high_level_clearing import _compress_metric
from sss_scoring import MODES, _composite, _derived, payload_sha256
from tier_postprocessing import _rank_secondary, compression_factor

SCHEMA_VERSION = 59
BASE_SCHEMA_VERSION = 58
NAMESPACE = "observedCappedClearing"
STAGE = "observedCappedClearingBaseline"
METHOD = "official-clearer-ability-observed-capped-v1"
CAP_POLICY = "observed-capped-s24-d25-fixed-reference-v1"
INTERVAL_METHOD = "conditional-scoring-fixed-observed-capped-clearing-v1"
FIELDS = ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High")


def _capped(row: Mapping[str, Any]) -> bool:
    return _target(row) and row["tierMetrics"]["clearing"].get("defaultedAtCap") is True


def _project(row: Mapping[str, Any], factor: float) -> dict:
    old = row["tierMetrics"]["clearing"]
    metric = copy.deepcopy(old)
    evidence = metric["clearingEvidence"]
    _need(evidence.get("sourceStates", {}).get("Phoenix2", {}).get("possiblyTruncated") is True,
          "Observed-capped Clearing requires a capped Phoenix 2 board.")
    metric.update(method=METHOD, observedAtCap=True, defaultedAtCap=False,
                  capDefaultDifficulty=None, capDefaultBasis=None, preRecenterCapDefaultDifficulty=None,
                  q20SkillCi95Low=None, q20SkillCi95High=None, extremeOutlier=False)
    evidence.update(capPolicy=CAP_POLICY, observedAtCap=True)
    metric["provisionalReasons"] = sorted((set(metric.get("provisionalReasons", []))
        - {"capped-board-folder-default"}) | {"capped-board-observed-clearers"})
    metric["uncertaintyReason"] = "The capped leaderboard omits clearers; a full-population interval is unavailable."
    metric["intervalMethod"] = "unavailable-capped-clearer-selection-v1"
    n, total = metric["ratedClearCount"], metric["clearCount"]
    _need(type(n) is int and type(total) is int and 0 <= n <= total,
          "Observed-capped Clearing requires valid observed and eligible counts.")
    weight = n / (n + 20) * (n / total if total else 0.)
    _need(_same(metric["shrinkageWeight"], weight), "Observed-capped Clearing support weight differs.")
    q, reference = metric.get("q20Skill"), metric.get("folderReferenceSkill")
    spread = metric.get("spreadCalibration") or {}
    width = old.get("finalFolderWidth")
    supported = (n > 0 and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        for v in (q, reference, spread.get("scale"), metric.get("recenteringShift"))) and width is not None)
    if not supported:
        metric.update(_derived(None, row["level"]), evidenceStatus="Unrated", rawDifficultyDelta=None,
            unrecenteredDifficulty=None, finalFolderWidth=None,
            unratedReason="No eligible observed clearer history." if not n else "The frozen folder reference is unavailable.")
        for field in FIELDS[1:]:
            metric[field] = None
        for side in ("Low", "High"):
            metric[f"difficultyDeltaCi95{side}"] = None
        evidence["unavailableReason"] = metric["unratedReason"]
        metric["initialFit"] = {**metric["initialFit"], **_derived(None, row["level"]),
            "difficultyCi95Low": None, "difficultyCi95High": None,
            "difficultyDeltaCi95Low": None, "difficultyDeltaCi95High": None}
        return metric
    delta = .7 * weight * (q - reference)
    unrecentered = row["level"] + .5 + spread["scale"] * delta
    initial = unrecentered + metric["recenteringShift"]
    metric.update(_derived(initial, row["level"]), rawDifficultyDelta=delta,
                  unrecenteredDifficulty=unrecentered, evidenceStatus="Provisional", unratedReason=None)
    metric.update(difficultyCi95Low=None, difficultyCi95High=None,
                  difficultyDeltaCi95Low=None, difficultyDeltaCi95High=None)
    _compress_metric(metric, row["level"], factor)
    inputs = {field: metric[field] for field in FIELDS}
    metric["finalFolderWidth"] = {**copy.deepcopy(width), "input": inputs}
    metric.update({field: width_value(inputs[field], width["parameters"]) for field in FIELDS})
    metric.update(_derived(metric["estimatedDifficulty"], row["level"]))
    return metric


def _calculate(base: Mapping[str, Any]) -> tuple[dict, list[str]]:
    result = _copy_overlay(base)
    changed = []
    for _, key in MODES:
        rows = [row for row in result[key] if _target(row)]
        for row in rows:
            _need(STAGE not in row, "Observed-capped Clearing cannot be applied twice.")
            row[STAGE] = {"clearing": copy.deepcopy(row["tierMetrics"]["clearing"]),
                          "pumbility": copy.deepcopy(row["tierMetrics"]["pumbility"]),
                          "pumbilityVariants": copy.deepcopy(row["pumbilityVariants"])}
            if not _capped(row):
                continue
            row["tierMetrics"]["clearing"] = _project(row, compression_factor(base))
            changed.append(str(row["chartId"]))
            _refresh_general(row)
            for goal in scoring_goals(base)[1:]:
                row["pumbilityVariants"][goal] = _composite(row, row["scoringVariants"][goal])
                row["pumbilityVariants"][goal]["intervalMethod"] = INTERVAL_METHOD
        _rank_secondary(rows, "clearing")
        for row in rows:
            metric = row["tierMetrics"]["clearing"]
            metric["initialFit"].update(levelRank=metric["levelRank"], levelComparisonCharts=metric["levelComparisonCharts"])
        for goal in scoring_goals(base):
            _rank_pumbility(rows, goal)
        for row in rows:
            row["tierMetrics"]["pumbility"] = copy.deepcopy(row["pumbilityVariants"]["general"])
    return result, sorted(changed)


def apply_observed_capped_clearing(base: Mapping[str, Any]) -> tuple[dict, dict]:
    _need(base.get("schemaVersion") in (BASE_SCHEMA_VERSION, 158)
          and NAMESPACE not in base.get("summary", {}).get("method", {}) and STAGE not in base,
          "Observed-capped Clearing requires a complete schema-58 baseline.")
    extension = base["summary"]["method"]["extendedOfficialClearing"]
    result, ids = _calculate(base)
    result[STAGE] = {"top": {key: base[key] for key in ("generatedAtUtc",) if key in base},
                     "summary": {key: base["summary"][key] for key in ("generatedAtUtc",) if key in base["summary"]}}
    generated = (base["generatedAtUtc"] if base["schemaVersion"] >= 100 else datetime.now(timezone.utc).isoformat())
    result["generatedAtUtc"] = result["summary"]["generatedAtUtc"] = generated
    result["schemaVersion"] = 159 if base["schemaVersion"] == 158 else SCHEMA_VERSION
    result["summary"]["method"][NAMESPACE] = {
        "version": 1, "method": METHOD, "localOnly": base["schemaVersion"] < 100, "baseSchemaVersion": base["schemaVersion"],
        "basePayloadSha256": payload_sha256(base), "capPolicy": CAP_POLICY,
        "scope": [{"mode": "Single", "level": 24}, {"mode": "Double", "level": 25}],
        "cappedChartIds": ids, "calibration": "frozen-schema58-reference-spread-location-and-width-v1",
        "compressionApplications": 1, "compressionFactor": compression_factor(base),
        "clearingManifestSha256": extension["clearingManifestSha256"],
        "effectiveInputSha256": extension["effectiveInputSha256"], "sourceDates": copy.deepcopy(extension["sourceDates"]),
        "intervalPolicy": "no-full-population-interval-for-capped-clearers", "dataFetches": 0, "modelRefits": 0}
    validate_observed_capped_clearing(result, base)
    return result, observed_capped_report(result, base)


def recover_observed_capped_clearing(payload: Mapping[str, Any]) -> dict:
    _need(payload.get("schemaVersion") in (SCHEMA_VERSION, 159), "Observed-capped recovery requires schema 59.")
    result = _copy_overlay(payload)
    saved = result.pop(STAGE)
    result.pop("generatedAtUtc", None)
    result.update(saved["top"])
    result["summary"].pop("generatedAtUtc", None)
    result["summary"].update(saved["summary"])
    result["summary"]["method"].pop(NAMESPACE)
    result["schemaVersion"] = 158 if payload["schemaVersion"] == 159 else BASE_SCHEMA_VERSION
    for _, key in MODES:
        for row in result[key]:
            if not _target(row):
                _need(STAGE not in row, "Observed-capped recovery appeared outside S24/D25.")
                continue
            old = row.pop(STAGE)
            _need(set(old) == {"clearing", "pumbility", "pumbilityVariants"}, "Observed-capped recovery fields differ.")
            row["tierMetrics"]["clearing"], row["tierMetrics"]["pumbility"] = old["clearing"], old["pumbility"]
            row["pumbilityVariants"] = old["pumbilityVariants"]
    return result


def validate_observed_capped_clearing(payload: Mapping[str, Any], original: Mapping[str, Any] | None = None) -> dict:
    base = recover_observed_capped_clearing(payload)
    marker = payload["summary"]["method"][NAMESPACE]
    _need(marker.get("version") == 1 and marker.get("method") == METHOD and marker.get("localOnly") is (payload["schemaVersion"] < 100)
          and marker.get("baseSchemaVersion") == (158 if payload["schemaVersion"] == 159 else BASE_SCHEMA_VERSION) and marker.get("capPolicy") == CAP_POLICY
          and marker.get("scope") == [{"mode": "Single", "level": 24}, {"mode": "Double", "level": 25}]
          and marker.get("calibration") == "frozen-schema58-reference-spread-location-and-width-v1"
          and marker.get("intervalPolicy") == "no-full-population-interval-for-capped-clearers"
          and marker.get("compressionApplications") == 1 and marker.get("compressionFactor") == compression_factor(base)
          and marker.get("dataFetches") == 0 and marker.get("modelRefits") == 0, "Observed-capped Clearing policy differs.")
    _need(payload_sha256(base) == marker.get("basePayloadSha256") and (original is None or base == original),
          "Observed-capped Clearing cannot recover its exact baseline.")
    extension = base["summary"]["method"]["extendedOfficialClearing"]
    _need(all(marker.get(key) == extension.get(key) for key in
          ("clearingManifestSha256", "effectiveInputSha256", "sourceDates")), "Observed-capped Clearing inputs changed.")
    expected, ids = _calculate(base)
    _need(marker.get("cappedChartIds") == ids, "Observed-capped Clearing target selection differs.")
    for key in ("singles", "doubles", "coop"):
        _need(payload.get(key) == expected.get(key), "Observed-capped Clearing changed protected values, evidence or ranks.")
    _need({k:v for k,v in payload.items() if k not in ("schemaVersion", "summary", "generatedAtUtc", STAGE)}
          == {k:v for k,v in expected.items() if k not in ("schemaVersion", "summary", "generatedAtUtc", STAGE)},
          "Observed-capped Clearing changed protected payload fields.")
    _need(payload["summary"]["generatedAtUtc"] == payload["generatedAtUtc"], "Observed-capped Clearing generation differs.")
    return base


def observed_capped_report(payload: Mapping[str, Any], base: Mapping[str, Any]) -> dict:
    charts, folders = [], {}
    for mode, key in MODES:
        old_rows = {row["chartId"]: row for row in base[key]}
        for row in payload[key]:
            old = old_rows[row["chartId"]]
            if not _capped(old):
                continue
            before, after = old["tierMetrics"]["clearing"], row["tierMetrics"]["clearing"]
            a, b = before["estimatedDifficulty"], after["estimatedDifficulty"]
            charts.append({"chartId": row["chartId"], "songName": row["songName"], "mode": mode, "level": row["level"],
                "fallbackDifficulty": a, "observedDifficulty": b, "change": None if a is None or b is None else b-a,
                "ratedClearCount": after["ratedClearCount"], "observedClearCount": after["clearCount"],
                "q20Skill": after["q20Skill"], "evidenceStatus": after["evidenceStatus"], "unratedReason": after["unratedReason"],
                "pumbility": {goal: {"before": old["pumbilityVariants"][goal]["estimatedDifficulty"],
                    "after": row["pumbilityVariants"][goal]["estimatedDifficulty"]} for goal in scoring_goals(base)}})
        level = 24 if mode == "Single" else 25
        def buckets(rows):
            counts = {}
            for row in rows:
                v = row["tierMetrics"]["clearing"]["estimatedDifficulty"]
                if row["level"] != level or v is None:
                    continue
                label = f"{math.trunc(v*10+1e-9)/10:.1f}"
                counts[label] = counts.get(label, 0)+1
            return dict(sorted(counts.items()))
        folders[f"{mode}:{level}"] = {"beforeBuckets": buckets(base[key]), "afterBuckets": buckets(payload[key])}
    return {"schemaVersion": SCHEMA_VERSION, "localOnly": base["schemaVersion"] < 100, "changedCharts": len(charts), "charts": charts,
        "folders": folders, "baseRecovered": True, "scoringPreserved": True, "uncappedNumericRatingsPreserved": True,
        "otherFoldersPreserved": True, "calibrationRefits": 0, "dataFetches": 0,
        "sourceDates": payload["summary"]["method"][NAMESPACE]["sourceDates"]}
