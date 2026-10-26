"""Local schema-55 combined official D26+ Clearing, preserving schema 54 exactly."""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from statistics import median
from typing import Any, Mapping

from official_high_level_clearing import (COMBINED_METHOD, COMBINED_CAP_POLICY,
    P1_OBSERVED_CAP_POLICY, apply_combined_official_high_level_clearing)
from pumbility_average import average_difficulties
from sss_scoring import MODES, _composite, _derived, payload_sha256
from tier_difficulty import EVIDENCE_ORDER

SCHEMA_VERSION = 55
BASE_SCHEMA_VERSION = 54
NAMESPACE = "combinedOfficialClearing"
STAGE = "combinedOfficialClearingBaseline"


def _rank_pumbility(rows: list[dict], goal: str) -> None:
    groups = defaultdict(list)
    for row in rows:
        metric = row["pumbilityVariants"][goal]
        if metric["estimatedDifficulty"] is not None:
            groups[row["level"]].append(row)
    for group in groups.values():
        group.sort(key=lambda row: (row["pumbilityVariants"][goal]["estimatedDifficulty"],
            row["pumbilityVariants"][goal]["initialFit"]["estimatedDifficulty"],
            row.get("songName") or "", str(row["chartId"])))
        for rank, row in enumerate(group, 1):
            metric = row["pumbilityVariants"][goal]
            metric.update(levelRank=rank, levelComparisonCharts=len(group))
            metric["initialFit"].update(levelRank=rank, levelComparisonCharts=len(group))


def _refresh_general(row: dict) -> None:
    metric = copy.deepcopy(row["tierMetrics"]["pumbility"])
    scoring = row["scoringVariants"]["general"]
    clearing = row["tierMetrics"]["clearing"]
    value = average_difficulties(scoring["estimatedDifficulty"], clearing["estimatedDifficulty"])
    raw = average_difficulties(scoring["initialFit"]["estimatedDifficulty"], clearing["initialFit"]["estimatedDifficulty"])
    metric.update(_derived(value, row["level"]), aggregation="average", levelRank=None, levelComparisonCharts=None,
        scoringSupportCount=int(scoring.get("nContributors") or row.get("nContributors") or 0),
        clearingSupportCount=int(clearing.get("ratedClearCount") or 0),
        evidenceStatus="Unrated" if value is None else EVIDENCE_ORDER[min(
            EVIDENCE_ORDER.index(scoring["evidenceStatus"]), EVIDENCE_ORDER.index(clearing["evidenceStatus"]))])
    metric.setdefault("initialFit", {}).update(_derived(raw, row["level"]), levelRank=None, levelComparisonCharts=None)
    row["tierMetrics"]["pumbility"] = metric
    row["pumbilityVariants"]["general"] = copy.deepcopy(metric)


def apply_combined_official_clearing(base: Mapping[str, Any], merged: Mapping[str, Any], *,
                                    bootstrap_samples: int = 1000,
                                    cap_policy: str = COMBINED_CAP_POLICY) -> tuple[dict, dict]:
    if base.get("schemaVersion") != BASE_SCHEMA_VERSION:
        raise ValueError("Combined official Clearing requires the schema-54 spread baseline.")
    reference = base.get("summary", {}).get("method", {}).get("sssplusScoring", {}).get("officialManifestSha256")
    if reference is not None and reference != merged.get("manifestSha256"):
        raise ValueError("Combined official Clearing must use the same pinned manifest as Scoring.")
    result = copy.deepcopy(base)
    from retained_tiers import scoring_goals
    goals = scoring_goals(base)
    previous = result["summary"]["method"].get("officialHighLevelClearing")
    for _, key in MODES:
        for row in result[key]:
            if STAGE in row:
                raise ValueError("Combined official Clearing cannot be applied twice.")
            row[STAGE] = {"clearing": copy.deepcopy(row["tierMetrics"]["clearing"]),
                "pumbility": copy.deepcopy(row["tierMetrics"]["pumbility"]),
                "pumbilityVariants": copy.deepcopy(row["pumbilityVariants"])}
    audit = apply_combined_official_high_level_clearing(result, merged, bootstrap_samples=bootstrap_samples, cap_policy=cap_policy)
    targets = [r for r in result["doubles"] if r["level"] >= 26]
    for row in targets:
        _refresh_general(row)
        for goal in goals[1:]:
            composite = _composite(row, row["scoringVariants"][goal])
            composite["intervalMethod"] = "conditional-fixed-combined-official-clearing-v1"
            row["pumbilityVariants"][goal] = composite
    # Only D26+ folders changed. Ranking these groups does not touch Scoring ranks.
    for goal in goals:
        _rank_pumbility(targets, goal)
    for row in targets:
        row["tierMetrics"]["pumbility"] = copy.deepcopy(row["pumbilityVariants"]["general"])
    result["schemaVersion"] = SCHEMA_VERSION
    result["summary"]["method"]["officialHighLevelClearing"] = audit
    result["summary"]["method"][NAMESPACE] = {
        "version": 2 if cap_policy == P1_OBSERVED_CAP_POLICY else 1, "method": COMBINED_METHOD, "localOnly": True,
        "baseSchemaVersion": BASE_SCHEMA_VERSION, "basePayloadSha256": payload_sha256(base),
        "previousOfficialHighLevelClearing": copy.deepcopy(previous),
        "previousOfficialHighLevelClearingPresent": "officialHighLevelClearing" in base["summary"]["method"],
        "manifestSha256": merged["manifestSha256"], "sourceDates": copy.deepcopy(merged["sourceDates"]),
        "mergePolicy": "full-tag-bidirectional-raw-max-v1", "mergeDiagnostics": copy.deepcopy(merged.get("diagnostics", {})),
        "scope": {"mode": "Double", "minimumLevel": 26, "historyMinimumLevel": 23},
        "uniqueClearPolicy": "one-merged-player-chart-clear-regardless-of-winning-source",
        "comparabilityPolicy": "current-chart-id-and-mode",
        "capPolicy": cap_policy, "compressionApplications": 1,
        "pumbility": {"aggregation": "average", "requiresBoth": True,
            "variants": list(goals), "componentValues": "unchanged-final-scoring-and-new-final-clearing",
            "intervals": "existing-scoring-intervals-conditional-on-fixed-new-clearing"},
        "modelRefits": 0, "dataFetches": 0}
    report = validate_combined_official_clearing(result, base)
    return result, report


def recover_combined_official_clearing(payload: Mapping[str, Any]) -> dict:
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("Combined official Clearing recovery requires schema 55.")
    result = copy.deepcopy(payload)
    marker = result["summary"]["method"].pop(NAMESPACE)
    result["schemaVersion"] = BASE_SCHEMA_VERSION
    if marker.get("previousOfficialHighLevelClearingPresent"):
        result["summary"]["method"]["officialHighLevelClearing"] = marker["previousOfficialHighLevelClearing"]
    else:
        result["summary"]["method"].pop("officialHighLevelClearing", None)
    for _, key in MODES:
        for row in result[key]:
            saved = row.pop(STAGE)
            row["tierMetrics"]["clearing"], row["tierMetrics"]["pumbility"] = saved["clearing"], saved["pumbility"]
            row["pumbilityVariants"] = saved["pumbilityVariants"]
    return result


def validate_combined_official_clearing(payload: Mapping[str, Any], base: Mapping[str, Any] | None = None) -> dict:
    from retained_tiers import scoring_goals
    goals = scoring_goals(payload)
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("Combined official Clearing requires schema 55.")
    marker = payload.get("summary", {}).get("method", {}).get(NAMESPACE, {})
    policy = payload.get("summary", {}).get("method", {}).get("officialHighLevelClearing", {})
    cap_policy = P1_OBSERVED_CAP_POLICY if marker.get("version") == 2 else COMBINED_CAP_POLICY
    if (marker.get("version") not in (1, 2) or marker.get("method") != COMBINED_METHOD
            or marker.get("baseSchemaVersion") != BASE_SCHEMA_VERSION
            or marker.get("capPolicy") != cap_policy or policy.get("capPolicy") != cap_policy
            or marker.get("comparabilityPolicy") != "current-chart-id-and-mode"
            or marker.get("compressionApplications") != 1 or marker.get("dataFetches") != 0
            or policy.get("method") != COMBINED_METHOD or policy.get("mix") != "Phoenix+Phoenix2"
            or marker.get("manifestSha256") != policy.get("manifestSha256")
            or marker.get("sourceDates") != policy.get("sourceDates")):
        raise ValueError("Combined official Clearing policy is incompatible.")
    recovered = recover_combined_official_clearing(payload)
    if payload_sha256(recovered) != marker.get("basePayloadSha256") or (base is not None and recovered != base):
        raise ValueError("Combined official Clearing cannot recover its exact schema-54 baseline.")
    folders = defaultdict(list)
    for mode, key in MODES:
        if len(payload[key]) != len(recovered[key]):
            raise ValueError("Combined official Clearing changed the chart catalog.")
        for row, before in zip(payload[key], recovered[key]):
            # These explicit comparisons also prohibit new source metadata leaking into Scoring.
            if row["scoringVariants"] != before["scoringVariants"]:
                raise ValueError("Combined official Clearing changed Scoring.")
            if mode != "Double" or row["level"] < 26:
                clean = {k: v for k, v in row.items() if k != STAGE}
                if clean != before:
                    raise ValueError("Combined official Clearing changed a chart outside D26+.")
                continue
            clearing = row["tierMetrics"]["clearing"]
            evidence = clearing.get("clearingEvidence", {})
            if (clearing.get("method") != COMBINED_METHOD or evidence.get("source") != "piuscores-official"
                    or evidence.get("mix") != "Phoenix+Phoenix2"
                    or evidence.get("sourceDates") != marker["sourceDates"]):
                raise ValueError("Combined official Clearing lost its source provenance.")
            states = evidence.get("sourceStates", {})
            expected_cap = (bool(states.get("Phoenix2", {}).get("possiblyTruncated")) if cap_policy == P1_OBSERVED_CAP_POLICY
                            else any(s.get("possiblyTruncated") for s in states.values()))
            if states and clearing["defaultedAtCap"] != expected_cap:
                raise ValueError("Combined official Clearing cap differs from its source boards.")
            if cap_policy == P1_OBSERVED_CAP_POLICY and evidence.get("capPolicy") != cap_policy:
                raise ValueError("Combined official Clearing lost its effective cap policy.")
            value = clearing["estimatedDifficulty"]
            if value is not None:
                if not math.isfinite(value) or abs(value-(row["level"]+.5+.5*(clearing["initialFit"]["estimatedDifficulty"]-row["level"]-.5))) > 1e-8:
                    raise ValueError("Combined official Clearing must compress exactly once.")
                folders[row["level"]].append(value)
            for goal in goals:
                scoring, composite = row["scoringVariants"][goal], row["pumbilityVariants"][goal]
                expected = average_difficulties(scoring["estimatedDifficulty"], value)
                if composite["estimatedDifficulty"] != expected or composite.get("aggregation") != "average":
                    raise ValueError("Combined official Pumbility disagrees with its component mean.")
            if row["tierMetrics"]["pumbility"] != row["pumbilityVariants"]["general"]:
                raise ValueError("Combined official General Pumbility aliases disagree.")
    for level, values in folders.items():
        if abs(median(values)-level-.5) > 1e-8:
            raise ValueError("Combined official Clearing folder median is not level plus 0.5.")
    if payload.get("coop") != recovered.get("coop"):
        raise ValueError("Combined official Clearing changed Co-op.")
    return {"schemaVersion": SCHEMA_VERSION, "baseRecovered": True, "scoringPreserved": True,
            "targetCharts": sum(r["level"] >= 26 for r in payload["doubles"]),
            "ratedTargetCharts": sum(map(len, folders.values())), "folders": {str(k): {"count": len(v), "median": median(v)} for k, v in folders.items()},
            "manifestSha256": marker["manifestSha256"], "sourceDates": marker["sourceDates"]}
