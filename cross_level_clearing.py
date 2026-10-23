"""Submitted-clearer thresholds and cross-level Clearing; Scoring is preserved."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

import numpy as np

from cross_level_clearing_calibration import clearing_difficulty, fit_clearing_curve
from phoenix2_sync import sanitize_score
from player_skill_ratings import clearing_tier_skill_for_levels
from pumbility_contract import (CLEARING_TIER_MINIMUM_SCORE_RECORDS,
                               CROSS_LEVEL_CLEARING_SCHEMA_VERSION, HYBRID_TIER_SCHEMA_VERSION,
                               cross_level_clearing_method_identity, hybrid_clearing_method_identity)
from tier_difficulty import EVIDENCE_ORDER, _empty_metric, _set_estimate, pumbility_difficulty, refresh_pumbility_metrics

CLEARING_METHOD = "submitted-cross-level-clearing-v1"
MODES = (("Single", "singles"), ("Double", "doubles"))


def current_clearing_catalog(payload: Mapping[str, Any], phoenix2: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Use the tier build's current metadata, retaining below-display history."""
    catalog = {str(c["id"]): dict(c) for c in phoenix2["charts"]}
    for key in ("singles", "doubles"):
        for row in payload.get(key, []):
            cid = str(row["chartId"])
            catalog[cid] = {**catalog.get(cid, {}), **{field: row.get(field) for field in (
                "type", "level", "noteCount", "songName", "songType", "difficulty", "imageUrl", "stepArtist", "bpmMin", "bpmMax", "bpmFocal")}, "id": cid}
    return [catalog[cid] for cid in sorted(catalog)]


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def clearing_input_identity(phoenix1: Mapping[str, Any], phoenix2: Mapping[str, Any],
                            current_charts: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    return {"phoenix1": _hash(phoenix1), "phoenix2": _hash(phoenix2),
            "currentCatalog": _hash(sorted(current_charts, key=lambda row: str(row.get("id", row.get("chartId")))))}


def prepare_clearing_evidence(phoenix1: Mapping[str, Any], phoenix2: Mapping[str, Any],
                              current_charts: Sequence[Mapping[str, Any]], *, exclusions=None) -> dict[str, Any]:
    catalog = {str(c.get("id", c.get("chartId"))): c for c in current_charts
               if c.get("type") in ("Single", "Double") and isinstance(c.get("level"), (int, float))
               and not isinstance(c.get("level"), bool) and math.isfinite(c["level"]) and c["level"] > 0}
    histories, by_source, excluded = defaultdict(set), {}, {}
    source_catalogs = {}
    for mix, snapshot in (("phoenix1", phoenix1), ("phoenix2", phoenix2)):
        source_catalogs[mix] = {str(c.get("id", c.get("chartId"))): c for c in snapshot.get("charts", [])}
        memberships, reasons = defaultdict(set), Counter()
        for raw in snapshot.get("scores", []):
            if exclusions is not None and exclusions.excludes(mix, str(raw.get("chartId"))):
                reasons["chartSourceExcluded"] += 1
                continue
            row = sanitize_score(raw)
            if row is None:
                reasons["invalidOrBroken"] += 1
                continue
            cid = row["chartId"]
            chart, origin = catalog.get(cid), source_catalogs[mix].get(cid)
            if not chart:
                reasons["outsideCurrentCatalog"] += 1
            elif not origin or origin.get("type") != chart["type"]:
                reasons["sourceModeMismatch"] += 1
            else:
                memberships[cid].add(row["playerId"])
                histories[row["playerId"], chart["type"]].add(cid)
        by_source[mix], excluded[mix] = memberships, dict(reasons)
    skills, qualifications = {}, {}
    for mode, _ in MODES:
        counts = Counter()
        for (player, chart_type), ids in histories.items():
            if chart_type != mode:
                continue
            skill = clearing_tier_skill_for_levels({cid: catalog[cid]["level"] for cid in ids}, record_count=len(ids))
            qualifications[player, mode] = ("recordRequirement" if len(ids) < CLEARING_TIER_MINIMUM_SCORE_RECORDS
                                          else "clearRequirement" if len(ids) < 30 else None)
            if skill is not None:
                skills[player, mode] = skill
                counts["qualifiedPlayers"] += 1
            else:
                counts[qualifications[player, mode]] += 1
            counts["observedPlayers"] += 1
        excluded[mode] = dict(counts)
    charts = {}
    for cid, chart in catalog.items():
        p1, p2 = by_source["phoenix1"][cid], by_source["phoenix2"][cid]
        players = p1 | p2
        values = [skills[player, chart["type"]] for player in sorted(players) if (player, chart["type"]) in skills]
        retained = (["phoenix1"] if p1 - p2 else []) + (["phoenix2"] if p2 else [])
        comparability = "comparable" if players else "no-evidence"
        for source in retained:
            count, current_count = source_catalogs[source][cid].get("noteCount"), chart.get("noteCount")
            known = all(isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value) and value > 0 for value in (count, current_count))
            if known and count != current_count:
                comparability = "changed"
            elif not known and comparability != "changed":
                comparability = "unknown"
        charts[cid] = {"clearCount": len(players), "ratedClearCount": len(values),
                       "missingSkillCount": len(players) - len(values),
                       "qualificationExclusions": dict(Counter(qualifications.get((player, chart["type"]))
                           for player in players if (player, chart["type"]) not in skills)),
                       "q10Skill": float(np.quantile(values, .1, method="linear")) if values else None,
                       "sourceCounts": {"phoenix1": len(p1), "phoenix2": len(p2)},
                       "retainedSources": retained, "comparability": comparability,
                       "anchorEligible": len(values) >= 20 and comparability == "comparable"}
    return {"catalog": catalog, "charts": charts, "diagnostics": excluded}


def apply_cross_level_clearing(base_payload: Mapping[str, Any], phoenix1: Mapping[str, Any],
                               phoenix2: Mapping[str, Any], current_charts: Sequence[Mapping[str, Any]],
                               *, source_dates: Mapping[str, str | None] | None = None,
                               hybrid_lower_tiers: bool = False) -> dict[str, Any]:
    prepared = prepare_clearing_evidence(phoenix1, phoenix2, current_charts)
    from tier_postprocessing import initial_fit_payload
    payload = initial_fit_payload(base_payload)
    method = payload.setdefault("summary", {}).setdefault("method", {})
    previous = method.get("crossLevelClearing", {})
    original_schema = previous.get("baseScoringSchemaVersion", payload["schemaVersion"])
    if original_schema not in (26, 41, 42):
        raise ValueError("Unsupported Scoring schema for Clearing overlay.")
    curves, diagnostics, lower_references = {}, {}, {}
    for mode, key in MODES:
        rows = payload.get(key, [])
        pools = defaultdict(list)
        for row in rows:
            evidence = prepared["charts"].get(row["chartId"], {})
            chart = prepared["catalog"].get(row["chartId"])
            if chart and (chart["type"] != mode or int(chart["level"]) != int(row["level"])):
                raise ValueError("Clearing overlay requires current chart modes and levels to match Scoring metadata.")
            if row["level"] >= 16 and evidence.get("anchorEligible"):
                pools[int(row["level"])].append((row["chartId"], evidence["q10Skill"]))
        folder_anchors = [{"level": level, "threshold": float(np.median([value for _, value in pool])),
                    "referenceCharts": len(pool), "chartIds": [cid for cid, _ in sorted(pool)]}
                   for level, pool in sorted(pools.items()) if len(pool) >= 5]
        anchors = [a for a in folder_anchors if a["level"] >= 20] if hybrid_lower_tiers else folder_anchors
        has_upper = any(row["level"] >= 20 for row in rows)
        if hybrid_lower_tiers and has_upper and not any(a["level"] == 20 for a in anchors):
            raise ValueError(f"{mode} hybrid Clearing calibration requires a supported level-20 anchor.")
        curve = fit_clearing_curve(anchors) if (has_upper if hybrid_lower_tiers else rows) else None
        curves[mode] = curve
        lower_references[mode] = {str(a["level"]): a for a in folder_anchors if a["level"] < 20}
        diagnostics[mode] = {"qualification": prepared["diagnostics"][mode],
                             "referenceChartsByLevel": {str(level): len(pool) for level, pool in sorted(pools.items())}}
        for row in rows:
            evidence = copy.deepcopy(prepared["charts"].get(row["chartId"], {
                "clearCount": 0, "ratedClearCount": 0, "missingSkillCount": 0, "q10Skill": None,
                "sourceCounts": {"phoenix1": 0, "phoenix2": 0}, "retainedSources": [],
                "comparability": "no-evidence", "anchorEligible": False, "qualificationExclusions": {}}))
            level, threshold, count = int(row["level"]), evidence["q10Skill"], evidence["ratedClearCount"]
            lower = hybrid_lower_tiers and level < 20
            row_method = ("submitted-folder-centered-clearing-v1" if lower else
                          "submitted-cross-level-clearing-v2" if hybrid_lower_tiers else CLEARING_METHOD)
            reference = next((a["threshold"] for a in folder_anchors if a["level"] == level), None)
            clearing = {**_empty_metric(), **evidence, "method": row_method,
                        "folderReferenceSkill": reference,
                        "supportedRange": curve["supportedRange"] if curve and not lower else None,
                        "extrapolated": False, "provisionalReasons": [],
                        "difficultyCi95Low": None, "difficultyCi95High": None}
            can_rate = reference is not None if lower else curve is not None
            if threshold is not None and can_rate and level >= 16:
                estimate = level + .5 + .70 * (threshold - reference) if lower else clearing_difficulty(threshold, curve)
                reasons = clearing["provisionalReasons"]
                if count < 20:
                    reasons.append("fewer-than-20-rated-clearers")
                if evidence["comparability"] != "comparable":
                    reasons.append("chart-comparability-" + evidence["comparability"])
                if clearing["folderReferenceSkill"] is None:
                    reasons.append("unsupported-official-folder")
                if not lower and (threshold < anchors[0]["threshold"] or threshold > anchors[-1]["threshold"]):
                    clearing["extrapolated"] = True
                    reasons.append("outside-supported-anchors")
                _set_estimate(clearing, estimate, level + .5)
                clearing["evidenceStatus"] = "Insufficient" if count < 5 else "Provisional" if reasons else "Published"
            if hybrid_lower_tiers:
                clearing["unratedReason"] = (None if clearing["estimatedDifficulty"] is not None else
                    "No qualified clearer skill evidence." if threshold is None else
                    "Fewer than five comparable folder reference charts with 20 qualified clearers." if lower else
                    "The 20+ Clearing calibration is unavailable.")
            metrics = row.setdefault("tierMetrics", {})
            metrics["clearing"] = clearing
            composite = {**_empty_metric(), "scoringSupportCount": int(row.get("nContributors") or 0),
                         "clearingSupportCount": count}
            scoring = row.get("estimatedDifficulty")
            if scoring is not None and clearing["estimatedDifficulty"] is not None:
                _set_estimate(composite, pumbility_difficulty(scoring, clearing["estimatedDifficulty"]), level + .5)
                composite["evidenceStatus"] = EVIDENCE_ORDER[min(EVIDENCE_ORDER.index(row["evidenceStatus"]),
                                                                 EVIDENCE_ORDER.index(clearing["evidenceStatus"]))]
            metrics["pumbility"] = composite
        for name in ("clearing", "pumbility"):
            folders = defaultdict(list)
            for row in rows:
                if row["tierMetrics"][name]["estimatedDifficulty"] is not None:
                    folders[row["level"]].append(row)
            for group in folders.values():
                group.sort(key=lambda r: (r["tierMetrics"][name]["estimatedDifficulty"], r.get("songName", ""), r["chartId"]))
                for rank, row in enumerate(group, 1):
                    row["tierMetrics"][name].update(levelRank=rank, levelComparisonCharts=len(group))
    identity = hybrid_clearing_method_identity() if hybrid_lower_tiers else cross_level_clearing_method_identity()
    method["crossLevelClearing"] = {**identity, "baseScoringSchemaVersion": original_schema,
        "inputHashes": clearing_input_identity(phoenix1, phoenix2, current_charts),
        "sourceDates": dict(source_dates or {mix: source.get("lastSyncedAtUtc") or source.get("generatedAtUtc") or None
            for mix, source in (("phoenix1", phoenix1), ("phoenix2", phoenix2))}),
        "calibration": curves, "modes": diagnostics, "sourceExclusions": {
            mix: prepared["diagnostics"][mix] for mix in ("phoenix1", "phoenix2")}}
    if hybrid_lower_tiers:
        method["crossLevelClearing"]["lowerReferenceSkills"] = lower_references
    tiers = method.setdefault("tierMetrics", {})
    tiers["version"] = 12 if hybrid_lower_tiers else 11
    tiers["clearing"] = identity
    payload["schemaVersion"] = HYBRID_TIER_SCHEMA_VERSION if hybrid_lower_tiers else CROSS_LEVEL_CLEARING_SCHEMA_VERSION
    refresh_pumbility_metrics(payload)
    return payload
