"""Official Phoenix maximum-score Scoring overlay; Clearing remains isolated."""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

import numpy as np

from official_cross_level_calibration import fit_cross_level_curve, inverse_chart_cost
from official_player_scoring import fit_cross_level_player_comparisons
from piu_misgrade_analyzer import difficulty_effect_band, relative_difficulty_group
from tier_difficulty import EVIDENCE_ORDER, pumbility_difficulty, refresh_pumbility_metrics

SCORING_METHOD_VERSION = 1
SCORING_METHOD = "official-cross-level-v1"
MINIMUM_OFFICIAL_LEVEL = 20
MODES = (("Single", "singles"), ("Double", "doubles"))
INTERVAL_METHOD = "conditional-player-bootstrap-fixed-fit-v1"


def _empty_metric() -> dict[str, Any]:
    return {"estimatedDifficulty": None, "difficultyDelta": None, "levelRank": None,
            "levelComparisonCharts": None, "effectBandRank": None, "effectBand": None,
            "evidenceStatus": "Unrated"}


def _set_metric(metric: dict[str, Any], difficulty: float, level: int, status: str) -> None:
    delta = difficulty - (level + .5)
    rank, band = difficulty_effect_band(delta)
    metric.update(estimatedDifficulty=difficulty, difficultyDelta=delta,
                  effectBandRank=rank, effectBand=band, evidenceStatus=status)


def _new_row(chart: Mapping[str, Any]) -> dict[str, Any]:
    mode = chart["type"]
    level = int(chart["level"])
    return {
        "chartId": str(chart.get("id", chart.get("chartId"))), "songName": chart.get("songName") or "",
        "type": mode, "mode": "Singles" if mode == "Single" else "Doubles", "level": level,
        "difficulty": chart.get("difficulty") or f"{'S' if mode == 'Single' else 'D'}{level}",
        "folder": f"{'S' if mode == 'Single' else 'D'}{level}",
        "imageUrl": chart.get("imageUrl"), "noteCount": chart.get("noteCount"),
        "stepArtist": chart.get("stepArtist"), "songType": chart.get("songType"), "bpmMin": chart.get("bpmMin"), "bpmMax": chart.get("bpmMax"), "bpmFocal": chart.get("bpmFocal"),
        **({"originalMix": chart["originalMix"]} if "originalMix" in chart else {}),
        "phoenix1Contributors": 0, "phoenix2Contributors": 0,
        "tierMetrics": {"clearing": {**_empty_metric(), "clearCount": 0, "ratedClearCount": 0,
                                      "missingSkillCount": 0}, "pumbility": _empty_metric()},
    }


def _rank_rows(rows: Sequence[dict[str, Any]]) -> None:
    """Preserve full precision and Clearing dictionaries while replacing score ranks."""
    measured = sorted((row for row in rows if row["difficultyDelta"] is not None),
                      key=lambda row: (row["difficultyDelta"], -row["nContributors"], row.get("songName") or "", row["chartId"]))
    folders = defaultdict(list)
    for rank, row in enumerate(measured, 1):
        row["modeRank"] = rank
        folders[int(row["level"])].append(row)
    for group in folders.values():
        deltas = [row["difficultyDelta"] for row in group]
        for rank, row in enumerate(group, 1):
            equal = [index + 1 for index, value in enumerate(deltas) if value == row["difficultyDelta"]]
            percentile = (float(np.mean(equal)) - .5) / len(group)
            relative_rank, relative = relative_difficulty_group(percentile)
            row.update(levelRank=rank, levelComparisonCharts=len(group), levelPercentile=percentile,
                       relativeGroupRank=relative_rank, relativeGroup=relative)
    composite_folders = defaultdict(list)
    for row in rows:
        if row["tierMetrics"]["pumbility"]["estimatedDifficulty"] is not None:
            composite_folders[int(row["level"])].append(row)
    for group in composite_folders.values():
        group.sort(key=lambda row: (row["tierMetrics"]["pumbility"]["estimatedDifficulty"], row.get("songName") or "", row["chartId"]))
        for rank, row in enumerate(group, 1):
            row["tierMetrics"]["pumbility"].update(levelRank=rank, levelComparisonCharts=len(group))


def _reference_anchors(mode: str, catalog: Mapping[str, Mapping[str, Any]],
                       chart_metadata: Mapping[str, Mapping[str, Any]], fitted: Mapping[str, Any]) -> tuple[list, dict]:
    pools, exclusions = defaultdict(list), {}
    levels = sorted({int(chart["level"]) for chart in catalog.values() if chart["type"] == mode and int(chart["level"]) >= 20})
    for level in levels:
        counts = defaultdict(int)
        for cid, chart in sorted(catalog.items()):
            if chart["type"] != mode or int(chart["level"]) != level:
                continue
            estimate = fitted["charts"].get(cid)
            metadata = chart_metadata.get(cid, {})
            if not estimate or not estimate["scoringMainComponent"]:
                counts["missingOrDisconnected"] += 1
            elif estimate["scoringPlayerCount"] < 10:
                counts["insufficientPlayers"] += 1
            elif not metadata.get("anchorEligible", False):
                counts["chartComparability"] += 1
            else:
                pools[level].append((cid, estimate["scoringChartCost"]))
        exclusions[str(level)] = {"catalogCharts": sum(chart["type"] == mode and int(chart["level"]) == level for chart in catalog.values()),
                                  "referenceCharts": len(pools[level]), "excluded": dict(counts)}
    anchors = [{"level": level, "cost": float(np.median([cost for _, cost in pool])),
                "referenceCharts": len(pool), "chartIds": [cid for cid, _ in pool]}
               for level, pool in sorted(pools.items()) if len(pool) >= 5]
    return anchors, exclusions


def apply_cross_level_scoring(base_payload: Mapping[str, Any], merged: Mapping[str, Any],
                              current_charts: Sequence[Mapping[str, Any]], *,
                              retain_submitted_below20: bool = False,
                              modes_to_fit: set[str] | None = None) -> dict[str, Any]:
    """Apply official-only Scoring, preserving each existing Clearing metric exactly."""
    from pumbility_contract import CROSS_LEVEL_TIER_SCHEMA_VERSION

    from tier_postprocessing import initial_fit_payload
    payload = initial_fit_payload(base_payload)
    submitted_method = copy.deepcopy(payload.get("summary", {}).get("method", {}))
    catalog = {str(chart.get("id", chart.get("chartId"))): chart for chart in current_charts}
    metadata = merged.get("charts", {})
    source_dates = dict(merged.get("sourceDates", {}))
    merged_boards = merged.get("boards", {})
    curves, mode_diagnostics = {}, {}
    all_target_rows = []
    for mode, key in MODES:
        rows = payload.setdefault(key, [])
        if modes_to_fit is not None and mode not in modes_to_fit:
            previous = payload["summary"]["method"]["officialCrossLevelScoring"]
            curves[mode] = copy.deepcopy(previous["calibration"][mode])
            mode_diagnostics[mode] = copy.deepcopy(previous["modes"][mode])
            all_target_rows.extend(r for r in rows if r["level"] >= 20)
            continue
        existing = {str(row["chartId"]) for row in rows}
        for cid, chart in sorted(catalog.items()):
            if chart["type"] == mode and int(chart["level"]) >= 20 and cid not in existing:
                rows.append(_new_row(chart))
        boards = {cid: scores for cid, scores in merged_boards.items() if cid in catalog
                  and catalog[cid]["type"] == mode and int(catalog[cid]["level"]) >= 20}
        fitted = fit_cross_level_player_comparisons(boards, seed_key=f"official-cross-level-v1:{mode}:{merged.get('manifestSha256', '')}")
        anchors, excluded = _reference_anchors(mode, catalog, metadata, fitted)
        if any(catalog[cid]["type"] == mode and int(catalog[cid]["level"]) >= 20 for cid in catalog):
            try:
                curve = fit_cross_level_curve(anchors)
            except ValueError as exc:
                raise ValueError(f"{mode} cross-level candidate is uncalibrated: {exc}") from exc
        else:
            curve = None
        curves[mode] = curve
        mode_diagnostics[mode] = {"eligiblePlayers": fitted["eligiblePlayers"], "componentCount": fitted["componentCount"],
                                  "componentSizes": fitted["componentSizes"], "anchorExclusionsByLevel": excluded}
        for row in rows:
            cid = str(row["chartId"])
            chart = catalog.get(cid)
            if chart and chart["type"] != mode:
                raise ValueError("A chart UUID cannot change between Singles and Doubles.")
            if chart:
                level = int(chart["level"])
                row.update(level=level, difficulty=chart.get("difficulty") or f"{'S' if mode == 'Single' else 'D'}{level}",
                           folder=f"{'S' if mode == 'Single' else 'D'}{level}")
                if "originalMix" in chart:
                    row["originalMix"] = chart["originalMix"]
            level = int(row["level"])
            if retain_submitted_below20 and level < 20:
                row.update(scoringMethod="submitted-score-profile-v2", officialScoringContributors=0,
                           officialScoringEvidence=None)
                row.setdefault("tierMetrics", {}).setdefault("pumbility", _empty_metric())
                continue
            for field in list(row):
                if field.startswith("scoring") or field in ("whatIfEstimates", "pumbilityPerLevel"):
                    row.pop(field, None)
            row.update(**_empty_metric(), averageDifficulty=level + .5, modeRank=None, levelPercentile=None,
                       relativeGroupRank=None, relativeGroup=None, nContributors=0, nPlayersScored=0,
                       difficultyCi95Low=None, difficultyCi95High=None, difficultyDeltaCi95Low=None, difficultyDeltaCi95High=None,
                       folderMeasuredCharts=0, folderRangeCompression=None,
                       scoringMethod=SCORING_METHOD, officialScoringContributors=0, officialScoringEvidence=None,
                       scoringPlayerCount=0, scoringMinimumOtherCharts=3, scoringComparisonCharts=0, scoringComponentCount=fitted["componentCount"],
                       scoringComponent=None, scoringMainComponent=False, scoringProvisional=True, scoringExtrapolated=False,
                       scoringChartCost=None, scoringChartCostCi95Low=None, scoringChartCostCi95High=None,
                       scoringMeanGap=None, scoringGapStdDev=None, scoringGapCi95Low=None, scoringGapCi95High=None,
                       scoringUnratedReason="Current official level is below 20." if level < 20 else "Official leaderboard data is unavailable.")
            row.setdefault("tierMetrics", {}).setdefault("clearing", _empty_metric())
            clearing = row["tierMetrics"]["clearing"]
            composite = {**_empty_metric(), "scoringSupportCount": 0,
                         "clearingSupportCount": int(clearing.get("ratedClearCount") or 0)}
            row["tierMetrics"]["pumbility"] = composite
            if chart and level >= 20:
                meta = metadata.get(cid, {})
                states = meta.get("sourceStates", {})
                counts = {mix: int(state.get("count", state.get("rawRowCount", 0)) or 0) for mix, state in states.items()}
                public_states = {mix: {field: state[field] for field in (
                    "status", "asOf", "rawRowCount", "maxRank", "cutoffScore", "possiblyTruncated")}
                    for mix, state in states.items()}
                merged_count = len(boards.get(cid, {}))
                estimate = fitted["charts"].get(cid)
                row["officialScoringEvidence"] = {"sourceDates": source_dates, "sourceCounts": counts,
                    "sourceStates": public_states,
                    "mergedPlayerCount": merged_count, "comparability": meta.get("comparability", "no-evidence"),
                    "component": estimate["scoringComponent"] if estimate else None, "anchorEligible": bool(meta.get("anchorEligible", False))}
                row.update(officialScoringContributors=merged_count, nPlayersScored=merged_count)
                if estimate:
                    row.update(estimate)
                    count = estimate["scoringPlayerCount"]
                    row["nContributors"] = count
                    composite["scoringSupportCount"] = count
                    if estimate["scoringMainComponent"] and count >= 5 and curve:
                        cost = estimate["scoringChartCost"]
                        difficulty = inverse_chart_cost(cost, curve)
                        extrapolated = cost < curve["anchors"][0]["cost"] or cost > curve["anchors"][-1]["cost"]
                        supported_folder = level in {anchor["level"] for anchor in curve["anchors"]}
                        provisional = count < 10 or bool(meta.get("provisional", True)) or extrapolated or not supported_folder
                        status = "Provisional" if provisional else "Published"
                        _set_metric(row, difficulty, level, status)
                        row.update(scoringUnratedReason=None, scoringProvisional=provisional, scoringExtrapolated=extrapolated)
                        for side in ("Low", "High"):
                            endpoint = row[f"scoringChartCostCi95{side}"]
                            if endpoint is not None:
                                value = inverse_chart_cost(endpoint, curve)
                                row[f"difficultyCi95{side}"] = value
                                row[f"difficultyDeltaCi95{side}"] = value - (level + .5)
                        if clearing.get("estimatedDifficulty") is not None:
                            clearing_status = clearing.get("evidenceStatus", "Unrated")
                            composite_status = EVIDENCE_ORDER[min(EVIDENCE_ORDER.index(status), EVIDENCE_ORDER.index(clearing_status))]
                            _set_metric(composite, pumbility_difficulty(difficulty, clearing["estimatedDifficulty"]), level, composite_status)
                    elif count:
                        row["evidenceStatus"] = "Unrated"
                    if row["estimatedDifficulty"] is None:
                        row["scoringChartCostCi95Low"] = row["scoringChartCostCi95High"] = None
                all_target_rows.append(row)
        _rank_rows(rows)
        for row in rows:
            row["folderMeasuredCharts"] = sum(other["level"] == row["level"] and other["estimatedDifficulty"] is not None for other in rows)
    summary = payload.setdefault("summary", {})
    method = summary.setdefault("method", {})
    method["sourceSelection"] = "official-cross-level-scoring-overlay"
    method["scoring"] = {"version": 1, "population": "official-max-per-player-chart",
                         "calibration": "official-cross-level-median-anchored-exponential", "minimumOfficialLevel": 20}
    method["officialCrossLevelScoring"] = {
        "version": 1, "minimumOfficialLevel": 20, "source": "piuscores-official", "mergePolicy": "maximum-raw-score",
        "minimumOtherCharts": 3, "modelVersion": 1, "calibrationVersion": 1, "bootstrapSamples": 1000,
        "sourceDates": source_dates, "manifestSha256": merged.get("manifestSha256"), "calibration": curves,
        "intervalMethod": INTERVAL_METHOD, "modes": mode_diagnostics,
    }
    if retain_submitted_below20:
        from pumbility_contract import hybrid_tier_method_identity
        method["sourceSelection"] = "hybrid-submitted-below20-official-at20plus"
        method["hybridTiers"] = {**hybrid_tier_method_identity(), "submittedScoring": {
            "method": submitted_method.get("scoring"),
            "scoreProfileCalibration": submitted_method.get("scoreProfileCalibration"),
            "confidenceIntervals": submitted_method.get("confidenceIntervals")}}
    if "officialTiers" in method:
        method["officialTiers"].pop("scoring", None)
    for field in ("scoreProfileCalibration", "whatIfEstimates"):
        method.pop(field, None)
    method["confidenceIntervals"] = {"scoring": INTERVAL_METHOD, "conditions": "fixed fitted effects, source capture and reference anchors; excludes model-fit and selection uncertainty"}
    method["levelReference"] = "median eligible reference-chart cost maps to current official level + 0.5"
    method["observationWeighting"] = "equal-player shared-chart comparisons across all current levels 20+ within each mode"
    for _, key in MODES:
        rows = payload[key]
        info = summary.setdefault("modes", {}).setdefault(key, {})
        info.update(catalogCharts=len(rows), measuredCharts=sum(row["estimatedDifficulty"] is not None for row in rows),
                    publishedCharts=sum(row["evidenceStatus"] == "Published" for row in rows),
                    officialScoringEligiblePlayers=mode_diagnostics["Single" if key == "singles" else "Double"]["eligiblePlayers"],
                    calibration={"method": "official-cross-level-median-anchored-exponential"}, pumbilityPerLevel=None)
        info.setdefault("sources", {})["officialScoringObservations"] = sum(row["officialScoringContributors"] for row in rows)
        info["folders"] = {}
        for level in sorted({int(row["level"]) for row in rows}):
            group = [row for row in rows if row["level"] == level]
            contributors = [row["nContributors"] for row in group if row["nContributors"]]
            info["folders"][f"{'S' if key == 'singles' else 'D'}{level}"] = {
                "catalogCharts": len(group), "measuredCharts": sum(row["estimatedDifficulty"] is not None for row in group),
                "publishedCharts": sum(row["evidenceStatus"] == "Published" for row in group),
                "medianContributors": float(np.median(contributors)) if contributors else None,
                "overratedCharts": sum(row["effectBandRank"] == 1 for row in group),
                "underratedCharts": sum(row["effectBandRank"] == 7 for row in group),
            }
    rows = payload.get("singles", []) + payload.get("doubles", [])
    all_rows = rows + payload.get("coop", [])
    summary.setdefault("coverage", {}).update(
        sourceObservations=sum(row["nContributors"] for row in rows),
        officialScoringObservations=sum(row["officialScoringContributors"] for row in rows),
        targetCatalogCharts=len(all_rows), targetChartsMeasured=sum(row.get("estimatedDifficulty") is not None for row in all_rows),
        targetChartsPublished=sum(row.get("evidenceStatus") == "Published" for row in all_rows))
    if "crossLevelClearing" in method:
        method["crossLevelClearing"]["baseScoringSchemaVersion"] = CROSS_LEVEL_TIER_SCHEMA_VERSION
        payload["schemaVersion"] = 43
    else:
        payload["schemaVersion"] = CROSS_LEVEL_TIER_SCHEMA_VERSION
    refresh_pumbility_metrics(payload)
    return payload
