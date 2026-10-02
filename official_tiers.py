"""Official-only high-level tier overlay, isolated from submitted-score models."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from official_scores import (is_official_target, is_official_history_chart, is_official_clearing_target,
                             OFFICIAL_HISTORY_MINIMUM_LEVELS, OFFICIAL_SCORING_MINIMUM_LEVELS,
                             OFFICIAL_CLEARING_MINIMUM_LEVELS)
from official_clearing_calibration import fit_clearing_spreads, calibrate_clearing_delta, clearing_calibration_identity
from official_scoring_calibration import fit_scoring_spreads, calibrate_scoring_delta, scoring_calibration_identity
from piu_misgrade_analyzer import _apply_chart_ranks_and_groups, difficulty_effect_band
from scoring_percentile import SCORE_UNIT
from official_player_scoring import fit_player_comparisons
from tier_difficulty import CLEARING_DIFFICULTY_DELTA_SCALE, EVIDENCE_ORDER


OFFICIAL_TIER_METHOD_VERSION = 14
OFFICIAL_SOURCE = "piuscores-official"
CLEAR_ABILITY_METRIC = "official-clearer-ability"
CLEARING_PERCENTILE = .20
PLAYER_TOP_CLEARS = 25
PLAYER_MINIMUM_OTHER_CLEARS = 25
CLEARING_PRIOR_PLAYERS = 20
MODES = (("Single", "singles"), ("Double", "doubles"))


def _empty_metric() -> dict[str, Any]:
    return {
        "estimatedDifficulty": None, "difficultyDelta": None,
        "levelRank": None, "levelComparisonCharts": None,
        "effectBandRank": None, "effectBand": None, "evidenceStatus": "Unrated",
    }


def _evidence(count: int) -> str:
    return "Published" if count >= 10 else "Provisional" if count >= 5 else "Insufficient"


def _set_metric(metric: dict[str, Any], estimate: float, level: int, count: int) -> None:
    metric["estimatedDifficulty"] = estimate
    metric["difficultyDelta"] = estimate - (level + .5)
    metric["effectBandRank"], metric["effectBand"] = difficulty_effect_band(metric["difficultyDelta"])
    metric["evidenceStatus"] = _evidence(count)


def _board_entries(board: Mapping[str, Any]) -> dict[str, float]:
    """Deduplicate positive observations, never infer an absent player to have failed."""
    entries: dict[str, float] = {}
    for entry in board.get("entries", []):
        player = str(entry.get("playerId") or "").strip()
        score = entry.get("score")
        if (not player or isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score) or not 0 <= score <= 1_000_000):
            raise ValueError("Official tier entries require an identity and finite valid score.")
        entries[player] = max(entries.get(player, -math.inf), float(score))
    return entries


def official_player_ability(history: Mapping[str, float], excluded_chart: str) -> float | None:
    """Require repeat accomplishments; the chart under evaluation never contributes."""
    levels = sorted((level for cid, level in history.items() if cid != excluded_chart), reverse=True)
    if len(levels) < PLAYER_MINIMUM_OTHER_CLEARS:
        return None
    return float(np.mean(levels[:PLAYER_TOP_CLEARS]))


def _ability_interval(skills: Sequence[float], chart_id: str) -> tuple[float | None, float | None]:
    """Conditional bootstrap of observed clearer ability, not a pass-rate interval."""
    if len(skills) < 5:
        return None, None
    ordered = np.sort(skills)
    seed = int.from_bytes(hashlib.sha256(chart_id.encode()).digest()[:8], "little")
    rng = np.random.default_rng(seed)
    samples = rng.choice(ordered, size=(1000, len(ordered)), replace=True)
    percentiles = np.quantile(samples, CLEARING_PERCENTILE, axis=1, method="linear")
    low, high = np.quantile(percentiles, [.025, .975], method="linear")
    return float(low), float(high)


def _folder_values(rows: Sequence[Mapping[str, Any]], field: str) -> dict[tuple[str, int], list[float]]:
    result: dict[tuple[str, int], list[float]] = defaultdict(list)
    for row in rows:
        value = row.get(field)
        if value is not None:
            result[(row["type"], int(row["level"]))].append(float(value))
    return result


def _by_mode(values: Mapping[tuple[str, int], Any]) -> dict[str, dict[str, Any]]:
    return {mode: {str(level): value for (typ, level), value in sorted(values.items()) if typ == mode}
            for mode, _ in MODES}


def _rank_metric(rows: Sequence[dict[str, Any]], metric_name: str) -> None:
    folders: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["tierMetrics"][metric_name]["estimatedDifficulty"] is not None:
            folders[(row["type"], int(row["level"]))].append(row)
    for charts in folders.values():
        charts.sort(key=lambda row: (row["tierMetrics"][metric_name]["estimatedDifficulty"],
                                     str(row.get("songName") or ""), row["chartId"]))
        for rank, row in enumerate(charts, 1):
            row["tierMetrics"][metric_name].update(levelRank=rank, levelComparisonCharts=len(charts))


def _refresh_summary(payload: dict[str, Any], targets: Sequence[Mapping[str, Any]],
                     official_players: Mapping[str, set[str]]) -> None:
    """Recount public observations; identities remain inside the calculation."""
    summary = payload.setdefault("summary", {})
    modes = summary.setdefault("modes", {})
    for mode, key in MODES:
        rows = payload.get(key, [])
        info = modes.setdefault(key, {})
        info.update(catalogCharts=len(rows),
                    measuredCharts=sum(row.get("estimatedDifficulty") is not None for row in rows),
                    publishedCharts=sum(row.get("evidenceStatus") == "Published" for row in rows),
                    officialEligiblePlayers=len(official_players[mode]),
                    eligiblePlayersBasis="submitted baseline population; official players counted separately")
        sources = info.setdefault("sources", {})
        for source in ("phoenix1", "phoenix2"):
            sources[f"{source}Observations"] = sum(int(row.get(f"{source}Contributors") or 0) for row in rows)
        sources["officialObservations"] = sum(int(row.get("officialContributors") or 0) for row in rows)
        folders = info.setdefault("folders", {})
        for level in sorted({int(row["level"]) for row in targets if row["type"] == mode}):
            charts = [row for row in rows if int(row["level"]) == level]
            contributors = [row["nContributors"] for row in charts if row["nContributors"] > 0]
            folders[f"{'S' if mode == 'Single' else 'D'}{level}"] = {
                "catalogCharts": len(charts),
                "measuredCharts": sum(row["estimatedDifficulty"] is not None for row in charts),
                "publishedCharts": sum(row["evidenceStatus"] == "Published" for row in charts),
                "medianContributors": float(np.median(contributors)) if contributors else None,
                "rangeCompression": 1.0,
                "overratedCharts": sum(row.get("effectBandRank") == 1 for row in charts),
                "underratedCharts": sum(row.get("effectBandRank") == 7 for row in charts),
            }
    rows = payload.get("singles", []) + payload.get("doubles", [])
    all_rows = rows + payload.get("coop", [])
    coverage = summary.setdefault("coverage", {})
    coverage.update(
        sourceObservations=sum(int(row.get("nContributors") or 0) for row in rows),
        officialObservations=sum(int(row.get("officialContributors") or 0) for row in rows),
        targetCatalogCharts=len(all_rows),
        targetChartsMeasured=sum(row.get("estimatedDifficulty") is not None for row in all_rows),
        targetChartsPublished=sum(row.get("evidenceStatus") == "Published" for row in all_rows),
    )
    for source in ("phoenix1", "phoenix2"):
        coverage[f"{source}Observations"] = sum(int(row.get(f"{source}Contributors") or 0) for row in rows)


def apply_official_tiers(
    base_payload: Mapping[str, Any],
    official_snapshot: Mapping[str, Any] | None,
    current_charts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Use official Scoring and Clearing at S25+/D26+.

    Full boards participate in Scoring and observed player histories. Their Clearing result
    is the lowest uncapped estimate in the same folder, or its midpoint when
    there is no uncapped estimate. Lower charts and recommendation inputs stay
    untouched; mode-wide ranking positions may move.
    """
    if official_snapshot is not None and (
        official_snapshot.get("source") != OFFICIAL_SOURCE
        or official_snapshot.get("mix") != "Phoenix2"
    ):
        raise ValueError("Official tiers require a piuscores-official Phoenix2 snapshot.")
    payload = copy.deepcopy(base_payload)
    catalog = {str(chart.get("id", chart.get("chartId"))): chart for chart in current_charts}
    target_catalog = {cid: chart for cid, chart in catalog.items() if is_official_target(chart)}
    history_catalog = {cid: chart for cid, chart in catalog.items() if is_official_history_chart(chart)}
    boards = {str(board["chartId"]): board for board in (official_snapshot or {}).get("boards", [])
              if str(board["chartId"]) in history_catalog or str(board["chartId"]) in target_catalog}
    entries = {cid: _board_entries(board) for cid, board in boards.items()}
    official_players = {mode: set() for mode, _ in MODES}
    histories: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for cid, players in entries.items():
        chart = catalog[cid]
        if cid in target_catalog:
            official_players[chart["type"]].update(players)
        if cid in history_catalog:
            for player in players:
                histories[(chart["type"], player)][cid] = int(chart["level"]) + .5

    targets: list[dict[str, Any]] = []
    submitted_clearings: dict[str, dict[str, Any]] = {}
    folders: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for _, key in MODES:
        for row in payload.get(key, []):
            cid = str(row["chartId"])
            chart = catalog.get(cid, row)
            if not is_official_target(chart):
                continue
            if row["type"] != chart["type"] or int(row["level"]) != int(chart["level"]):
                raise ValueError("Official tier base rows must match the current chart catalog.")
            targets.append(row)
            official_clearing = is_official_clearing_target(chart)
            if not official_clearing:
                submitted_clearings[cid] = copy.deepcopy(row["tierMetrics"]["clearing"])
            folders[(row["type"], int(row["level"]))].append(row)
            board = boards.get(cid)
            samples = entries.get(cid, {})
            skills = [skill for player in samples
                      if (skill := official_player_ability(histories[(row["type"], player)], cid)) is not None] if official_clearing else []
            capped = bool(board and (board.get("possiblyTruncated")
                                      or int(board.get("rawRowCount") or 0) >= 300
                                      or int(board.get("maxRank") or 0) >= 300))
            count = 300 if capped else len(samples)
            coverage = len(skills) / len(samples) if samples else 0.
            shrinkage = len(skills) / (len(skills) + CLEARING_PRIOR_PLAYERS) * coverage
            skill_interval = _ability_interval(skills, cid) if not capped else (None, None)
            reason = ("Official leaderboard data is unavailable." if board is None else
                      "The official leaderboard has no usable observations." if not samples else None)
            row["officialEvidence"] = {
                "source": OFFICIAL_SOURCE, "asOf": board.get("asOf") if board else None,
                "rawRowCount": int(board.get("rawRowCount") or 0) if board else 0,
                "maxRank": board.get("maxRank") if board else None,
                "cutoffScore": board.get("cutoffScore") if board else None,
                "possiblyTruncated": capped,
                "status": "missing" if board is None else "possibly-truncated" if capped else "available",
                "unavailableReason": reason,
            }
            for field in ("scoringPercentileScore", "scoringFolderReferenceScore",
                          "scoringPercentileCi95Low", "scoringPercentileCi95High"):
                row.pop(field, None)
            row.update(
                **_empty_metric(), averageDifficulty=int(row["level"]) + .5,
                modeRank=None, levelPercentile=None, relativeGroupRank=None, relativeGroup=None,
                nContributors=0, nPlayersScored=len(samples),
                phoenix1Contributors=0, phoenix2Contributors=0, officialContributors=len(samples),
                scoringPointsPerLevel=SCORE_UNIT, scoringDifficultyScale=None,
                scoringSpreadCalibration=None, scoringExtremeOutlier=False,
                scoringScoreProfile=None, scoringProfileMatchDifficulty=None,
                scoringProfileMatchCi95Low=None, scoringProfileMatchCi95High=None,
                scoringProfileRmse=None, scoringProfileExtrapolated=False,
                scoringFolderReferenceDifficulty=None,
                difficultyCi95Low=None, difficultyCi95High=None,
                difficultyDeltaCi95Low=None, difficultyDeltaCi95High=None,
                folderRangeCompression=1.0, folderMeasuredCharts=0,
                pumbilityPerLevel=None, whatIfEstimates=[],
                tierMetrics={
                    "clearing": {**_empty_metric(), "skillMetric": CLEAR_ABILITY_METRIC,
                                 "clearCount": count, "ratedClearCount": len(skills),
                                 "missingSkillCount": len(samples) - len(skills),
                                 "q20Skill": float(np.quantile(skills, CLEARING_PERCENTILE, method="linear")) if skills else None,
                                 "q20SkillCi95Low": skill_interval[0], "q20SkillCi95High": skill_interval[1],
                                 "folderReferenceSkill": None, "abilityCoverage": coverage,
                                 "shrinkageWeight": shrinkage, "defaultedAtCap": capped,
                                 "spreadCalibration": None, "extremeOutlier": False,
                                 "difficultyCi95Low": None, "difficultyCi95High": None,
                                 "capDefaultDifficulty": None, "capDefaultBasis": None},
                    "pumbility": {**_empty_metric(), "scoringSupportCount": 0,
                                  "clearingSupportCount": len(skills)},
                },
            )
            if not official_clearing:
                row["tierMetrics"]["clearing"] = submitted_clearings[cid]
                row["tierMetrics"]["pumbility"]["clearingSupportCount"] = submitted_clearings[cid]["ratedClearCount"]

    clearing_references = {}
    scale_folders = {}
    scoring_panels = {}
    for key, rows in folders.items():
        fit = fit_player_comparisons({row["chartId"]: entries.get(row["chartId"], {}) for row in rows},
                                     seed_key=f"{key[0]}:{key[1]}")
        scoring_panels[key] = {k: v for k, v in fit.items() if k not in ("charts", "personal")}
        for row in rows:
            # Remove old preview diagnostics when overlaying an existing artifact.
            for field in ("scoringBandMean", "scoringBandCount", "scoringBandStartRank", "scoringBandEndRank", "scoringFolderReferenceMean"):
                row.pop(field, None)
            row.update(fit["charts"][row["chartId"]])
            row["nContributors"] = row["scoringPlayerCount"]
            row["tierMetrics"]["pumbility"]["scoringSupportCount"] = row["scoringPlayerCount"]
        measured = [row for row in rows if row["scoringMeanGap"] is not None]
        scale_folders[key] = {
            "offsets": [row["scoringMeanGap"] / SCORE_UNIT for row in measured],
            "contributors": [row["scoringPlayerCount"] for row in measured],
            "eligible": [not row["scoringProvisional"] for row in measured],
        }
        abilities = [row["tierMetrics"]["clearing"]["q20Skill"] for row in rows
                     if is_official_clearing_target(row) and not row["tierMetrics"]["clearing"]["defaultedAtCap"]
                     and row["tierMetrics"]["clearing"]["q20Skill"] is not None]
        if abilities:
            clearing_references[key] = float(np.median(abilities))
    scoring_spreads = fit_scoring_spreads(scale_folders)
    scoring_scales = _by_mode({key: params["scale"] for key, params in scoring_spreads.items()})
    clearing_scale_folders = {}
    for key, rows in folders.items():
        if not is_official_clearing_target(rows[0]):
            continue
        measured = [row["tierMetrics"]["clearing"] for row in rows
                    if not row["tierMetrics"]["clearing"]["defaultedAtCap"]
                    and row["tierMetrics"]["clearing"]["q20Skill"] is not None]
        clearing_scale_folders[key] = {
            "deltas": [CLEARING_DIFFICULTY_DELTA_SCALE * c["shrinkageWeight"]
                       * (c["q20Skill"] - clearing_references[key]) for c in measured],
            "players": [c["ratedClearCount"] for c in measured],
        }
    clearing_spreads = fit_clearing_spreads(clearing_scale_folders)
    cap_floors = {}
    for key, rows in folders.items():
        mode, level = key
        midpoint = level + .5
        ability_reference = clearing_references.get(key)
        scale = scoring_scales[mode].get(str(level))
        for row in rows:
            row["scoringSpreadCalibration"] = scoring_spreads.get(key)
            row["folderMeasuredCharts"] = sum(item["scoringMeanGap"] is not None for item in rows)
            if row["scoringMeanGap"] is not None:
                row["scoringDifficultyScale"] = scale
                raw = row["scoringMeanGap"] / SCORE_UNIT
                delta, extreme = calibrate_scoring_delta(raw, scoring_spreads[key], row["scoringPlayerCount"],
                                                         not row["scoringProvisional"])
                row["scoringExtremeOutlier"] = extreme
                _set_metric(row, midpoint + delta, level, row["scoringPlayerCount"])
                if row["scoringProvisional"] and row["evidenceStatus"] == "Published":
                    row["evidenceStatus"] = "Provisional"
                for side in ("Low", "High"):
                    gap = row[f"scoringGapCi95{side}"]
                    if gap is not None:
                        row[f"difficultyDeltaCi95{side}"] = scale * gap / SCORE_UNIT
                        row[f"difficultyCi95{side}"] = midpoint + scale * gap / SCORE_UNIT
            clearing = row["tierMetrics"]["clearing"]
            if key not in clearing_spreads:
                continue
            clearing["folderReferenceSkill"] = ability_reference
            clearing["spreadCalibration"] = clearing_spreads[key]
            if clearing["q20Skill"] is not None and not clearing["defaultedAtCap"]:
                factor = CLEARING_DIFFICULTY_DELTA_SCALE * clearing["shrinkageWeight"]
                delta, extreme = calibrate_clearing_delta(factor * (clearing["q20Skill"] - ability_reference),
                    clearing_spreads[key], players=clearing["ratedClearCount"], coverage=clearing["abilityCoverage"],
                    interval_low=factor * (clearing["q20SkillCi95Low"] - ability_reference) if clearing["q20SkillCi95Low"] is not None else None,
                    interval_high=factor * (clearing["q20SkillCi95High"] - ability_reference) if clearing["q20SkillCi95High"] is not None else None)
                estimate = midpoint + delta
                clearing["extremeOutlier"] = extreme
                _set_metric(clearing, estimate, level, clearing["ratedClearCount"])
                for side in ("Low", "High"):
                    ability = clearing[f"q20SkillCi95{side}"]
                    if ability is not None:
                        clearing[f"difficultyCi95{side}"] = midpoint + clearing_spreads[key]["scale"] * factor * (ability - ability_reference)
        uncapped = [row["tierMetrics"]["clearing"]["estimatedDifficulty"] for row in rows
                    if is_official_clearing_target(row) and not row["tierMetrics"]["clearing"]["defaultedAtCap"]
                    and row["tierMetrics"]["clearing"]["estimatedDifficulty"] is not None]
        floor = min(uncapped) if uncapped else midpoint
        basis = "uncapped-folder-minimum" if uncapped else "folder-midpoint-no-uncapped-charts"
        if any(row["tierMetrics"]["clearing"].get("defaultedAtCap") for row in rows):
            cap_floors[key] = floor
        for row in rows:
            clearing = row["tierMetrics"]["clearing"]
            if clearing.get("defaultedAtCap"):
                _set_metric(clearing, floor, level, clearing["ratedClearCount"])
                clearing["evidenceStatus"] = "Provisional" if uncapped and clearing["ratedClearCount"] >= 5 else "Insufficient"
                clearing.update(capDefaultDifficulty=floor, capDefaultBasis=basis)
            if row["estimatedDifficulty"] is not None and clearing["estimatedDifficulty"] is not None:
                composite = row["tierMetrics"]["pumbility"]
                _set_metric(composite, (row["estimatedDifficulty"] + clearing["estimatedDifficulty"]) / 2,
                            level, row["scoringPlayerCount"])
                composite["evidenceStatus"] = EVIDENCE_ORDER[min(EVIDENCE_ORDER.index(row["evidenceStatus"]),
                                                                EVIDENCE_ORDER.index(clearing["evidenceStatus"]))]
    _rank_metric([row for row in targets if is_official_clearing_target(row)], "clearing")
    _rank_metric(targets, "pumbility")
    for mode, key in MODES:
        rows = [row for row in targets if row["type"] == mode]
        if rows:
            ranked = _apply_chart_ranks_and_groups(pd.DataFrame(rows))
            replacements = {row["chartId"]: row for row in json.loads(ranked.to_json(orient="records", double_precision=6))}
            for cid, row in replacements.items():
                if cid in submitted_clearings:
                    row["tierMetrics"]["clearing"] = submitted_clearings[cid]
            payload[key] = [replacements.get(row["chartId"], row) for row in payload[key]]
        measured = sorted((row for row in payload.get(key, []) if row.get("difficultyDelta") is not None),
                          key=lambda row: (row["difficultyDelta"], -int(row.get("nContributors") or 0),
                                           str(row.get("songName") or ""), row["chartId"]))
        for rank, row in enumerate(measured, 1):
            row["modeRank"] = rank

    method = payload.setdefault("summary", {}).setdefault("method", {})
    method["sourceSelection"] = "official-high-level-overlay"
    method["officialTiers"] = {
        "version": OFFICIAL_TIER_METHOD_VERSION, "enabled": True, "source": OFFICIAL_SOURCE,
        "mix": "Phoenix2", "asOf": (official_snapshot or {}).get("asOf") or next((board.get("asOf") for board in boards.values()), None),
        "minimumLevels": dict(OFFICIAL_SCORING_MINIMUM_LEVELS),
        "capPolicy": "clearing-folder-minimum",
        "targetCharts": len(targets), "availableBoards": sum(cid in target_catalog for cid in boards),
        "historyBoards": sum(cid in history_catalog for cid in boards),
        "possiblyTruncatedBoards": sum(row["officialEvidence"]["possiblyTruncated"] for row in targets),
        "scoring": {
            "metric": "equal-player-score-gaps", "minimumOtherCharts": 3,
            "sparsePolicy": "provisional-same-level", "aggregation": "equal-player-mean",
            "pointsPerLevel": SCORE_UNIT, "separateModesAndLevels": True,
            "population": "official players with at least three other same-level charts; smaller folders use provisional comparisons; no comparable history is Unrated",
            "folderPanels": _by_mode(scoring_panels),
            "calibration": {**scoring_calibration_identity(), "folderScales": scoring_scales,
                            "folderParameters": _by_mode(scoring_spreads)},
            "formula": "mean over players of (official level + 0.5 + folder scale * (mean of other chart scores adjusted for fitted chart effects - target score) / 10000)",
            "disconnectedPolicy": "separate component median anchors; provisional and not comparable across components",
            "confidenceIntervals": {"method": "1000 deterministic shared-player bootstrap samples",
                                    "minimumPlayers": 5, "conditions": "fixed fitted chart effects and folder scale; excludes model-fit uncertainty, effort, selection and leaderboard truncation bias"},
        },
        "clearing": {
            "minimumLevels": dict(OFFICIAL_CLEARING_MINIMUM_LEVELS),
            "skillMetric": CLEAR_ABILITY_METRIC,
            "difficultyDeltaScale": CLEARING_DIFFICULTY_DELTA_SCALE,
            "normalization": "folder-median-player-ability", "percentile": CLEARING_PERCENTILE,
            "playerSkill": {"method": "leave-one-chart-out-top-official-levels",
                            "topCharts": PLAYER_TOP_CLEARS, "minimumOtherCharts": PLAYER_MINIMUM_OTHER_CLEARS,
                            "levelMidpointOffset": .5, "separateModes": True,
                            "historyMinimumLevels": dict(OFFICIAL_HISTORY_MINIMUM_LEVELS)},
            "shrinkage": {"priorPlayers": CLEARING_PRIOR_PLAYERS,
                          "formula": "eligible players / (eligible players + 20) * eligible players / observed unique players"},
            "population": "official clearers with at least 25 other distinct S22+/D23+ clears in the same mode",
            "folderReferences": _by_mode(clearing_references), "capDefaults": _by_mode(cap_floors),
            "formula": "official level + 0.5 + calibrated(0.70 * shrinkage weight * (20th-percentile clearer ability - folder median ability))",
            "calibration": {**clearing_calibration_identity(), "folderParameters": _by_mode(clearing_spreads)},
            "uncertainty": {"method": "1000 deterministic player bootstrap samples of the 20th-percentile ability",
                            "minimumPlayers": 5, "conditions": "fixed player histories, folder reference and scale; excludes selection and calibration uncertainty"},
            "capDefault": "lowest uncapped nonempty chart estimate in the same official folder; midpoint if none",
        },
    }
    _refresh_summary(payload, targets, official_players)
    return payload
