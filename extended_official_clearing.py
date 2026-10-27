"""Recoverable final official Clearing overlay for exactly S24 and D25."""
from __future__ import annotations
from retained_tiers import scoring_goals

import copy
import math
from statistics import median
from typing import Any, Mapping

import numpy as np

from chart_source_exclusions import canonical_hash
from combined_official_clearing import _rank_pumbility, _refresh_general
from final_folder_width import POLICY, width_parameters, width_value
from official_clearing_calibration import fit_clearing_spreads
from official_high_level_clearing import (
    P1_OBSERVED_CAP_POLICY, apply_combined_official_high_level_clearing,
)
from sss_scoring import MODES, _composite, _derived, payload_sha256
from tier_postprocessing import _rank_secondary, compress_difficulty, compression_factor

SCHEMA_VERSION = 58
BASE_SCHEMA_VERSION = 57
NAMESPACE = "extendedOfficialClearing"
STAGE = "extendedOfficialClearingBaseline"
METHOD = "official-clearer-ability-combined-v3"
INTERVAL_METHOD = "conditional-fixed-extended-official-clearing-and-width-v1"
TARGETS = [{"mode": "Single", "level": 24, "historyMinimumLevel": 22},
           {"mode": "Double", "level": 25, "historyMinimumLevel": 23}]
FIELDS = ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High")


def _target(row: Mapping[str, Any]) -> bool:
    return any(row.get("type") == t["mode"] and row.get("level") == t["level"] for t in TARGETS)


def _need(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _same(actual: Any, expected: Any) -> bool:
    if isinstance(expected, float):
        return (not isinstance(actual, bool) and isinstance(actual, (int, float))
                and math.isfinite(actual) and math.isclose(actual, expected, rel_tol=0, abs_tol=1e-8))
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(_same(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _copy_overlay(base: Mapping[str, Any]) -> dict:
    """Copy mutable active targets, sharing immutable historical trees and other rows."""
    result = dict(base)
    result["summary"] = dict(base["summary"])
    result["summary"]["method"] = dict(base["summary"]["method"])
    for _, key in MODES:
        result[key] = []
        for old in base.get(key, []):
            if not _target(old):
                result[key].append(old)
                continue
            row = dict(old)
            row["tierMetrics"] = dict(old["tierMetrics"])
            row["tierMetrics"]["clearing"] = copy.deepcopy(old["tierMetrics"]["clearing"])
            row["tierMetrics"]["pumbility"] = copy.deepcopy(old["tierMetrics"]["pumbility"])
            row["pumbilityVariants"] = copy.deepcopy(old["pumbilityVariants"])
            result[key].append(row)
    return result


def _source_policy(policy: Any) -> None:
    _need(isinstance(policy, Mapping) and policy.get("version") == 1
          and _hash(policy.get("configSha256")) and _hash(policy.get("effectivePolicySha256"))
          and isinstance(policy.get("rules"), list), "Extended Clearing requires a pinned source exclusion policy.")
    semantic = {"version": 1, "rules": [
        {k: rule[k] for k in ("source", "chartId", "mode", "scope")}
        for rule in policy["rules"] if rule.get("enabled")]}
    _need(canonical_hash(semantic) == policy["effectivePolicySha256"], "Extended Clearing source exclusion identity differs.")


def _scoring_manifest(base: Mapping[str, Any]) -> str:
    method = base.get("summary", {}).get("method", {})
    value = (method.get("sourceAwareTiers", {}).get("sourcePolicy", {}).get("manifestSha256")
             or method.get("sssplusScoring", {}).get("officialManifestSha256"))
    _need(_hash(value), "Extended Clearing requires the frozen Scoring manifest identity.")
    return value


def _refresh_composites(rows: list[dict]) -> None:
    for row in rows:
        _refresh_general(row)
        for goal in row["scoringVariants"]:
            if goal == "general": continue
            row["pumbilityVariants"][goal] = _composite(row, row["scoringVariants"][goal])
            row["pumbilityVariants"][goal]["intervalMethod"] = INTERVAL_METHOD
    for goal in (rows[0]["scoringVariants"] if rows else ()):
        _rank_pumbility(rows, goal)
    for row in rows:
        row["tierMetrics"]["pumbility"] = copy.deepcopy(row["pumbilityVariants"]["general"])


def apply_extended_official_clearing(base: Mapping[str, Any], merged: Mapping[str, Any], *,
                                    bootstrap_samples: int = 1000, local_only: bool = True) -> tuple[dict, dict]:
    _need(base.get("schemaVersion") in (BASE_SCHEMA_VERSION, 61)
          and NAMESPACE not in base.get("summary", {}).get("method", {}),
          "Extended Clearing requires the frozen schema-57 predecessor and cannot run twice.")
    _need(type(local_only) is bool, "Extended Clearing local_only must be boolean.")
    _source_policy(merged.get("sourceExclusions"))
    _need(_hash(merged.get("manifestSha256")) and _hash(merged.get("effectiveInputSha256")),
          "Extended Clearing requires complete pinned official input hashes.")
    policy_hash = merged["sourceExclusions"]["effectivePolicySha256"]
    previous_policy = base["summary"]["method"].get("sourceAwareTiers", {}).get("sourcePolicy", {}).get("effectivePolicySha256")
    _need(previous_policy is None or previous_policy == policy_hash,
          "Extended Clearing cannot silently change the frozen source exclusion policy.")
    result = _copy_overlay(base)
    policies, folders = {}, {}
    for target, (_, key) in zip(TARGETS, MODES):
        mode, level, history = target["mode"], target["level"], target["historyMinimumLevel"]
        rows = [row for row in result[key] if _target(row)]
        for row in rows:
            _need(STAGE not in row, "Extended Clearing cannot be applied twice.")
            row[STAGE] = {"clearing": copy.deepcopy(row["tierMetrics"]["clearing"]),
                          "pumbility": copy.deepcopy(row["tierMetrics"]["pumbility"]),
                          "pumbilityVariants": copy.deepcopy(row["pumbilityVariants"])}
        audit = apply_combined_official_high_level_clearing(
            {key: rows, "summary": result["summary"]}, merged,
            bootstrap_samples=bootstrap_samples, cap_policy=P1_OBSERVED_CAP_POLICY,
            mode=mode, target_levels=[level], history_minimum_level=history, method=METHOD)
        policies[mode] = audit
        if not rows:
            continue
        params = width_parameters([
            {"id": row["chartId"], "value": row["tierMetrics"]["clearing"]["estimatedDifficulty"],
             "capDefault": row["tierMetrics"]["clearing"]["defaultedAtCap"]} for row in rows], level)
        for row in rows:
            metric = row["tierMetrics"]["clearing"]
            metric["historyMinimumLevel"] = history
            metric["clearingEvidence"].update(mode=mode, historyMinimumLevel=history,
                minimumOtherCharts=25, topCharts=25, effectiveInputSha256=merged["effectiveInputSha256"],
                sourcePolicySha256=policy_hash, manifestSha256=merged["manifestSha256"])
            if metric["estimatedDifficulty"] is None:
                continue
            snapshot = {field: metric[field] for field in FIELDS}
            metric.update({field: width_value(value, params) for field, value in snapshot.items()})
            metric["finalFolderWidth"] = {"group": f"{mode}:clearing:{level}", "input": snapshot,
                "parameters": copy.deepcopy(params), "exceptionPreserved": False, "initialCompressionApplications": 1}
            if params["provisional"]:
                if metric["evidenceStatus"] == "Published":
                    metric["evidenceStatus"] = "Provisional"
                metric["provisionalReasons"] = sorted(set(metric["provisionalReasons"]) | {"provisional-local-folder-width"})
            if metric["difficultyCi95Low"] is not None:
                metric["intervalMethod"] = POLICY["intervals"]
            metric.update(_derived(metric["estimatedDifficulty"], level))
            for side in ("Low", "High"):
                endpoint = metric[f"difficultyCi95{side}"]
                metric[f"difficultyDeltaCi95{side}"] = None if endpoint is None else endpoint-level-.5
            if metric["defaultedAtCap"]:
                metric["capDefaultDifficulty"] = metric["estimatedDifficulty"]
        _rank_secondary(rows, "clearing")
        _refresh_composites(rows)
        final = [row["tierMetrics"]["clearing"]["estimatedDifficulty"] for row in rows
                 if row["tierMetrics"]["clearing"]["estimatedDifficulty"] is not None]
        population = [row["tierMetrics"]["clearing"]["estimatedDifficulty"] for row in rows
                      if str(row["chartId"]) in params["populationChartIds"]]
        core = audit["folders"][str(level)]
        folders[f"{mode}:{level}"] = {"core": copy.deepcopy(core), "widthParameters": params,
            "coreAllFiniteMedian": core["finalMedian"], "finalAllFiniteMedian": median(final) if final else None,
            "finalWidthPopulationMedian": median(population) if population else None}
    result["schemaVersion"] = 158 if base["schemaVersion"] == 61 else SCHEMA_VERSION
    result["summary"]["method"][NAMESPACE] = {
        "version": 1, "method": METHOD, "localOnly": local_only,
        "baseSchemaVersion": base["schemaVersion"], "basePayloadSha256": payload_sha256(base),
        "targets": copy.deepcopy(TARGETS), "scoringManifestSha256": _scoring_manifest(base),
        "clearingManifestSha256": merged["manifestSha256"], "effectiveInputSha256": merged["effectiveInputSha256"],
        "sourcePolicy": copy.deepcopy(merged["sourceExclusions"]), "sourceDates": copy.deepcopy(merged["sourceDates"]),
        "catalogSha256": canonical_hash(merged["currentCatalog"]), "capPolicy": P1_OBSERVED_CAP_POLICY,
        "compressionApplications": 1, "compressionFactor": compression_factor(base), "widthPolicy": copy.deepcopy(POLICY),
        "policies": policies, "folders": folders, "modelRefits": 0, "dataFetches": 0}
    validate_extended_official_clearing(result, base)
    return result, extended_official_clearing_report(result, base)


def recover_extended_official_clearing(payload: Mapping[str, Any]) -> dict:
    _need(payload.get("schemaVersion") in (SCHEMA_VERSION, 158), "Extended Clearing recovery requires schema 58.")
    result = _copy_overlay(payload)
    _need(NAMESPACE in result["summary"]["method"], "Extended Clearing recovery metadata is missing.")
    result["schemaVersion"] = result["summary"]["method"].pop(NAMESPACE)["baseSchemaVersion"]
    for _, key in MODES:
        for row in result[key]:
            if not _target(row):
                _need(STAGE not in row, "Extended Clearing recovery appeared outside its target folders.")
                continue
            saved = row.pop(STAGE, None)
            _need(isinstance(saved, dict) and set(saved) == {"clearing", "pumbility", "pumbilityVariants"},
                  "Extended Clearing target recovery is missing.")
            row["tierMetrics"]["clearing"] = saved["clearing"]
            row["tierMetrics"]["pumbility"] = saved["pumbility"]
            row["pumbilityVariants"] = saved["pumbilityVariants"]
    return result


def validate_extended_official_clearing(payload: Mapping[str, Any], original: Mapping[str, Any] | None = None) -> dict:
    """Validate aggregate numeric provenance and return the exact schema-57 predecessor."""
    base = recover_extended_official_clearing(payload)
    marker = payload["summary"]["method"][NAMESPACE]
    _need(marker.get("version") == 1 and marker.get("method") == METHOD
          and type(marker.get("localOnly")) is bool and marker.get("baseSchemaVersion") == (61 if payload["schemaVersion"] == 158 else BASE_SCHEMA_VERSION)
          and marker.get("targets") == TARGETS and marker.get("capPolicy") == P1_OBSERVED_CAP_POLICY
          and marker.get("compressionApplications") == 1 and marker.get("compressionFactor") == compression_factor(base)
          and marker.get("widthPolicy") == POLICY and marker.get("modelRefits") == 0 and marker.get("dataFetches") == 0,
          "Extended Clearing policy differs.")
    _need(payload_sha256(base) == marker.get("basePayloadSha256") and (original is None or base == original),
          "Extended Clearing cannot recover its exact schema-57 baseline.")
    _need(marker.get("scoringManifestSha256") == _scoring_manifest(base)
          and all(_hash(marker.get(key)) for key in ("clearingManifestSha256", "effectiveInputSha256", "catalogSha256"))
          and set(marker.get("sourceDates", {})) == {"Phoenix", "Phoenix2"}, "Extended Clearing input identities differ.")
    _source_policy(marker.get("sourcePolicy"))
    previous_policy = base["summary"]["method"].get("sourceAwareTiers", {}).get("sourcePolicy", {}).get("effectivePolicySha256")
    _need(previous_policy is None or previous_policy == marker["sourcePolicy"]["effectivePolicySha256"],
          "Extended Clearing changed the frozen source exclusion policy.")
    _need(set(marker.get("policies", {})) == {"Single", "Double"}, "Extended Clearing mode policies are incomplete.")
    expected_folders = set()
    for target, (_, key) in zip(TARGETS, MODES):
        mode, level, history = target["mode"], target["level"], target["historyMinimumLevel"]
        rows = [row for row in payload[key] if _target(row)]
        policy = marker["policies"][mode]
        _need(policy.get("method") == METHOD and policy.get("mode") == mode and policy.get("minimumLevel") == level
              and policy.get("mix") == "Phoenix+Phoenix2" and policy.get("source") == "piuscores-official"
              and policy.get("capPolicy") == P1_OBSERVED_CAP_POLICY and policy.get("percentile") == .2
              and policy.get("difficultyDeltaScale") == .7 and policy.get("compressionFactor") == compression_factor(base)
              and policy.get("sourceDates") == marker["sourceDates"] and policy.get("manifestSha256") == marker["clearingManifestSha256"]
              and policy.get("targetCharts") == len(rows)
              and policy.get("playerSkill", {}).get("historyMinimumLevel") == history
              and policy.get("playerSkill", {}).get("topCharts") == 25
              and policy.get("playerSkill", {}).get("minimumOtherCharts") == 25,
              "Extended Clearing mode or history policy differs.")
        if not rows:
            _need(not policy.get("folders"), "Unexpected empty-mode Clearing folders.")
            continue
        group = f"{mode}:{level}"
        expected_folders.add(group)
        metrics = [row["tierMetrics"]["clearing"] for row in rows]
        measured = [m for m in metrics if not m["defaultedAtCap"] and m["q20Skill"] is not None]
        reference = float(np.median([m["q20Skill"] for m in measured])) if measured else None
        spreads = fit_clearing_spreads({(mode, level): {
            "deltas": [.7*m["shrinkageWeight"]*(m["q20Skill"]-reference) for m in measured],
            "players": [m["ratedClearCount"] for m in measured]}})[mode, level]
        uncapped = [level+.5+spreads["scale"]*.7*m["shrinkageWeight"]*(m["q20Skill"]-reference) for m in measured]
        floor = min(uncapped) if uncapped else level+.5
        finite = uncapped + [floor for m in metrics if m["defaultedAtCap"]]
        shift = level+.5-float(np.median(finite)) if finite else None
        expected_core = {
            "chartCount": len(rows), "measuredCharts": len(finite),
            "cappedCharts": sum(m["defaultedAtCap"] for m in metrics),
            "folderReferenceSkill": reference, "spreadCalibration": spreads,
            "capDefaultInitialDifficulty": floor,
            "capDefaultBasis": "uncapped-folder-minimum" if uncapped else "folder-midpoint-no-uncapped-charts",
            "preRecenterMedian": float(np.median(finite)) if finite else None, "recenteringShift": shift,
            "initialMedian": level+.5 if finite else None, "finalMedian": level+.5 if finite else None}
        _need(_same(policy.get("folders"), {str(level): expected_core}), "Extended Clearing core folder audit differs.")
        points = []
        for row, metric in zip(rows, metrics):
            evidence = metric.get("clearingEvidence", {})
            states = evidence.get("sourceStates", {})
            observed, count = metric.get("observedClearCount"), metric.get("ratedClearCount")
            _need(type(observed) is int and type(count) is int and 0 <= count <= observed
                  and metric.get("clearCount") == observed and metric.get("missingSkillCount") == observed-count,
                  "Extended Clearing observed or eligible counts differ.")
            weight = count/(count+20)*(count/observed if observed else 0.)
            _need(metric.get("method") == METHOD and metric.get("skillMetric") == "official-clearer-ability"
                  and metric.get("historyMinimumLevel") == history and evidence.get("mode") == mode
                  and evidence.get("historyMinimumLevel") == history and evidence.get("minimumOtherCharts") == 25
                  and evidence.get("topCharts") == 25 and evidence.get("source") == "piuscores-official"
                  and evidence.get("mix") == "Phoenix+Phoenix2" and evidence.get("sourceDates") == marker["sourceDates"]
                  and evidence.get("manifestSha256") == marker["clearingManifestSha256"]
                  and evidence.get("effectiveInputSha256") == marker["effectiveInputSha256"]
                  and evidence.get("sourcePolicySha256") == marker["sourcePolicy"]["effectivePolicySha256"]
                  and evidence.get("capPolicy") == P1_OBSERVED_CAP_POLICY
                  and metric.get("defaultedAtCap") == bool(states.get("Phoenix2", {}).get("possiblyTruncated"))
                  and _same(metric.get("abilityCoverage"), count/observed if observed else 0.)
                  and _same(metric.get("shrinkageWeight"), weight)
                  and ((metric.get("q20Skill") is None) == (count == 0))
                  and _same(metric.get("folderReferenceSkill"), reference) and _same(metric.get("spreadCalibration"), spreads),
                  "Extended Clearing source, support, cap or calibration differs.")
            value = (floor if metric["defaultedAtCap"] else None if metric["q20Skill"] is None
                     else level+.5+spreads["scale"]*.7*weight*(metric["q20Skill"]-reference))
            _need(_same(metric.get("unrecenteredDifficulty"), value) and _same(metric.get("recenteringShift"), shift),
                  "Extended Clearing core location differs.")
            initial = metric["initialFit"]
            _need(_same(initial.get("estimatedDifficulty"), None if value is None else value+shift),
                  "Extended Clearing core estimate differs.")
            if not metric["defaultedAtCap"] and value is not None:
                _need(_same(metric.get("rawDifficultyDelta"), .7*weight*(metric["q20Skill"]-reference)),
                      "Extended Clearing raw delta differs.")
            for side in ("Low", "High"):
                q = metric.get(f"q20SkillCi95{side}")
                expected = None if q is None else level+.5+spreads["scale"]*.7*weight*(q-reference)+shift
                _need((not metric["defaultedAtCap"] or q is None)
                      and _same(initial.get(f"difficultyCi95{side}"), expected), "Extended Clearing conditional interval differs.")
            compressed = {field: compress_difficulty(initial.get(field), level, compression_factor(base)) for field in FIELDS}
            if value is None:
                _need(metric.get("finalFolderWidth") is None and metric["estimatedDifficulty"] is None
                      and metric.get("evidenceStatus") == "Unrated", "Unrated Clearing acquired a width or estimate.")
                continue
            width = metric.get("finalFolderWidth", {})
            _need(width.get("group") == f"{mode}:clearing:{level}" and width.get("initialCompressionApplications") == 1
                  and width.get("exceptionPreserved") is False and _same(width.get("input"), compressed),
                  "Extended Clearing must compress exactly once before final width.")
            points.append({"id": row["chartId"], "value": compressed["estimatedDifficulty"], "capDefault": metric["defaultedAtCap"]})
        params = width_parameters(points, level)
        for row, metric in zip(rows, metrics):
            if metric["estimatedDifficulty"] is None:
                continue
            width = metric["finalFolderWidth"]
            _need(_same(width.get("parameters"), params), "Extended Clearing final width population differs.")
            for field in FIELDS:
                _need(_same(metric.get(field), width_value(width["input"][field], params)), "Extended Clearing final width or interval differs.")
            _need(_same({k: metric.get(k) for k in _derived(metric["estimatedDifficulty"], level)},
                        _derived(metric["estimatedDifficulty"], level)), "Extended Clearing derived fields differ.")
            if metric["defaultedAtCap"]:
                _need(_same(metric.get("capDefaultDifficulty"), metric["estimatedDifficulty"])
                      and metric.get("capDefaultBasis") == expected_core["capDefaultBasis"]
                      and _same(metric.get("preRecenterCapDefaultDifficulty"), floor)
                      and metric.get("difficultyCi95Low") is None and metric.get("difficultyCi95High") is None,
                      "Extended Clearing cap default differs.")
            count = metric["ratedClearCount"]
            status = ("Provisional" if uncapped and count >= 5 else "Insufficient") if metric["defaultedAtCap"] else (
                "Published" if count >= 10 else "Provisional" if count >= 5 else "Insufficient")
            reasons = []
            if metric["defaultedAtCap"]:
                reasons.append("capped-board-folder-default")
            else:
                if count < 10:
                    reasons.append("fewer-than-10-eligible-official-clearers")
                if spreads["basis"] != "folder":
                    reasons.append("borrowed-folder-scale" if spreads["basis"] == "neighbor-folder" else "fallback-folder-scale")
                    if status == "Published":
                        status = "Provisional"
            if metric["clearingEvidence"]["sourceStates"].get("Phoenix", {}).get("possiblyTruncated"):
                reasons.append("phoenix1-cap-ignored-for-estimation")
                if status == "Published":
                    status = "Provisional"
            comparability = metric["clearingEvidence"].get("comparability")
            if comparability not in ("same", "single-source-known", "no-evidence"):
                reasons.append("cross-version-note-count-"+str(comparability))
                if status == "Published":
                    status = "Provisional"
            if params["provisional"]:
                reasons.append("provisional-local-folder-width")
                if status == "Published":
                    status = "Provisional"
            _need(metric.get("evidenceStatus") == status and sorted(metric.get("provisionalReasons", [])) == sorted(reasons),
                  "Extended Clearing evidence status or provisional reasons differ.")
        folder = marker["folders"][group]
        final = [m["estimatedDifficulty"] for m in metrics if m["estimatedDifficulty"] is not None]
        population = [r["tierMetrics"]["clearing"]["estimatedDifficulty"] for r in rows if str(r["chartId"]) in params["populationChartIds"]]
        _need(_same(folder.get("widthParameters"), params) and _same(folder.get("core"), policy["folders"][str(level)])
              and _same(folder.get("coreAllFiniteMedian"), level+.5 if finite else None)
              and _same(folder.get("finalAllFiniteMedian"), median(final) if final else None)
              and _same(folder.get("finalWidthPopulationMedian"), median(population) if population else None),
              "Extended Clearing folder audit differs.")
        if params["basis"] == "folder":
            _need(_same(median(population), level+.5), "Extended Clearing width did not preserve its population median.")
        expected_rows = []
        for row in rows:
            clone = dict(row)
            clone["tierMetrics"] = dict(row["tierMetrics"])
            clone["tierMetrics"]["pumbility"] = copy.deepcopy(row[STAGE]["pumbility"])
            clone["pumbilityVariants"] = copy.deepcopy(row[STAGE]["pumbilityVariants"])
            clone["tierMetrics"]["clearing"] = copy.deepcopy(row["tierMetrics"]["clearing"])
            expected_rows.append(clone)
        _rank_secondary(expected_rows, "clearing")
        _refresh_composites(expected_rows)
        for row, expected in zip(rows, expected_rows):
            _need(_same(row["tierMetrics"], expected["tierMetrics"])
                  and _same(row["pumbilityVariants"], expected["pumbilityVariants"]),
                  "Extended Clearing ranks or Pumbility components differ.")
    _need(set(marker.get("folders", {})) == expected_folders, "Extended Clearing folder scope differs.")
    return base


def extended_official_clearing_report(payload: Mapping[str, Any], base: Mapping[str, Any]) -> dict:
    marker = payload["summary"]["method"][NAMESPACE]
    charts = []
    for _, key in MODES:
        old_rows = {str(r["chartId"]): r for r in base[key]}
        for row in payload[key]:
            if not _target(row):
                continue
            metric, old = row["tierMetrics"]["clearing"], old_rows[str(row["chartId"])]
            charts.append({"chartId": row["chartId"], "songName": row.get("songName"), "mode": row["type"], "level": row["level"],
                "beforeClearing": old["tierMetrics"]["clearing"]["estimatedDifficulty"], "afterClearing": metric["estimatedDifficulty"],
                "observedClearCount": metric["observedClearCount"], "ratedClearCount": metric["ratedClearCount"],
                "abilityCoverage": metric["abilityCoverage"], "defaultedAtCap": metric["defaultedAtCap"],
                "sourceStates": metric["clearingEvidence"]["sourceStates"], "evidenceStatus": metric["evidenceStatus"],
                "unratedReason": metric["unratedReason"], "spreadCalibration": metric["spreadCalibration"],
                "levelRank": metric["levelRank"], "provisionalReasons": metric["provisionalReasons"],
                "pumbility": {goal: {"before": old["pumbilityVariants"][goal]["estimatedDifficulty"],
                    "after": row["pumbilityVariants"][goal]["estimatedDifficulty"]} for goal in row["scoringVariants"]}})
    return {"schemaVersion": SCHEMA_VERSION, "baseSchemaVersion": BASE_SCHEMA_VERSION, "baseRecovered": True,
        "scoringPreserved": True, "targetCharts": len(charts),
        "ratedTargetCharts": sum(c["afterClearing"] is not None for c in charts), "charts": charts,
        "folders": copy.deepcopy(marker["folders"]), "scoringManifestSha256": marker["scoringManifestSha256"],
        "clearingManifestSha256": marker["clearingManifestSha256"], "effectiveInputSha256": marker["effectiveInputSha256"],
        "sourceDates": copy.deepcopy(marker["sourceDates"]), "modelRefits": 0, "dataFetches": 0,
        "unchangedScopeSha256": canonical_hash({key: [r for r in base[key] if not _target(r)] for _, key in MODES})}
