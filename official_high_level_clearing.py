"""Clearing-only Phoenix 2 official overlay for current Doubles level 26+."""
from __future__ import annotations

import copy
import hashlib
import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

import numpy as np

from official_clearing_calibration import (
    calibrate_clearing_delta, clearing_calibration_identity, fit_clearing_spreads,
)
from official_tiers import (
    CLEARING_PERCENTILE, CLEARING_PRIOR_PLAYERS, CLEAR_ABILITY_METRIC,
    OFFICIAL_SOURCE, PLAYER_MINIMUM_OTHER_CLEARS, PLAYER_TOP_CLEARS,
    _board_entries, _empty_metric, _set_metric, official_player_ability,
)
from piu_misgrade_analyzer import difficulty_effect_band
from tier_difficulty import CLEARING_DIFFICULTY_DELTA_SCALE
from tier_postprocessing import compress_difficulty


METHOD = "official-d26-clearer-ability-v1"
COMBINED_METHOD = "official-d26-clearer-ability-combined-v2"
COMBINED_CAP_POLICY = "any-source-possibly-truncated-v1"
P1_OBSERVED_CAP_POLICY = "phoenix1-diagnostic-phoenix2-default-v1"
HISTORY_MINIMUM_LEVEL = 23
TARGET_MINIMUM_LEVEL = 26


def _chart_id(chart: Mapping[str, Any]) -> str:
    return str(chart.get("id", chart.get("chartId")))


def _is_double(chart: Mapping[str, Any], minimum: int) -> bool:
    level = chart.get("level")
    return (chart.get("type") == "Double" and not isinstance(level, bool)
            and isinstance(level, (int, float)) and math.isfinite(level)
            and level == int(level) and level >= minimum)


def _ability_interval(skills: Sequence[float], chart_id: str,
                      samples: int) -> tuple[float | None, float | None]:
    if len(skills) < 5 or samples == 0:
        return None, None
    seed = int.from_bytes(hashlib.sha256(chart_id.encode()).digest()[:8], "little")
    rng = np.random.default_rng(seed)
    draws = rng.choice(np.sort(skills), size=(samples, len(skills)), replace=True)
    percentiles = np.quantile(draws, CLEARING_PERCENTILE, axis=1, method="linear")
    low, high = np.quantile(percentiles, [.025, .975], method="linear")
    return float(low), float(high)


def _new_metric(board: Mapping[str, Any] | None, skills: Sequence[float],
                observed: int, chart_id: str, capture_as_of: Any,
                bootstrap_samples: int, *, combined: bool = False,
                cap_policy: str = COMBINED_CAP_POLICY) -> dict[str, Any]:
    # A union's cardinality is not the cap of either source leaderboard.
    capped = bool(board and (any(state.get("possiblyTruncated") for state in board["sourceStates"].values())
                            if combined else (board.get("possiblyTruncated")
                             or int(board.get("rawRowCount") or 0) >= 300
                             or int(board.get("maxRank") or 0) >= 300)))
    reported_capped = capped
    if combined and cap_policy == P1_OBSERVED_CAP_POLICY:
        capped = bool(board and board["sourceStates"]["Phoenix2"].get("possiblyTruncated"))
    count = len(skills)
    coverage = count / observed if observed else 0.
    low, high = _ability_interval(skills, chart_id, bootstrap_samples) if not capped else (None, None)
    reason = ("Official Phoenix 2 leaderboard data is unavailable." if board is None else
              "The official Phoenix 2 leaderboard has no usable observations." if not observed else
              "No observed clearer has at least 25 other unique official D23+ clears." if not count and not capped else None)
    result = {
        **_empty_metric(), "method": COMBINED_METHOD if combined else METHOD, "skillMetric": CLEAR_ABILITY_METRIC,
        "clearCount": observed, "observedClearCount": observed, "ratedClearCount": count,
        "missingSkillCount": observed - count,
        "q20Skill": float(np.quantile(skills, CLEARING_PERCENTILE, method="linear")) if count else None,
        "q20SkillCi95Low": low, "q20SkillCi95High": high,
        "folderReferenceSkill": None, "abilityCoverage": coverage,
        "shrinkageWeight": count / (count + CLEARING_PRIOR_PLAYERS) * coverage,
        "defaultedAtCap": capped, "capDefaultDifficulty": None, "capDefaultBasis": None,
        "preRecenterCapDefaultDifficulty": None, "spreadCalibration": None,
        "extremeOutlier": False, "unrecenteredDifficulty": None, "recenteringShift": None,
        "difficultyCi95Low": None, "difficultyCi95High": None,
        "difficultyDeltaCi95Low": None, "difficultyDeltaCi95High": None,
        "provisionalReasons": [], "unratedReason": reason,
        "uncertaintyReason": ("Capped-board floor assignment has no conditional difficulty interval."
                              if capped else "Fewer than five eligible official clearers." if count < 5 else
                              "Conditional bootstrap disabled." if not bootstrap_samples else None),
        "clearingEvidence": {
            "source": OFFICIAL_SOURCE, "mix": "Phoenix2",
            "asOf": (board or {}).get("asOf") or capture_as_of,
            "rawRowCount": int((board or {}).get("rawRowCount") or 0),
            "maxRank": (board or {}).get("maxRank"),
            "cutoffScore": (board or {}).get("cutoffScore"),
            "possiblyTruncated": capped,
            "status": "missing" if board is None else "possibly-truncated" if capped else "empty" if not observed else "available",
            "unavailableReason": reason,
        },
    }
    if combined:
        reason = ("Official Phoenix 1 and Phoenix 2 leaderboard data is unavailable." if board is None else
                  "The combined official leaderboard has no usable observations." if not observed else reason)
        result["unratedReason"] = reason
        result["clearingEvidence"].update(
            mix="Phoenix+Phoenix2", asOf=None, sourceDates=copy.deepcopy(capture_as_of),
            sourceStates=copy.deepcopy((board or {}).get("sourceStates", {})),
            manifestSha256=(board or {}).get("manifestSha256"),
            rawRowCount=sum(int(state.get("rawRowCount") or 0)
                            for state in (board or {}).get("sourceStates", {}).values()),
            mergedPlayers=observed, maxRank=None,
            comparability=(board or {}).get("comparability", "no-evidence"),
            unavailableReason=reason,
        )
        if cap_policy == P1_OBSERVED_CAP_POLICY:
            result["clearingEvidence"].update(capPolicy=cap_policy,
                possiblyTruncated=reported_capped,
                status="missing" if board is None else "possibly-truncated" if reported_capped else "empty" if not observed else "available")
    return result


def _compress_metric(metric: dict[str, Any], level: int) -> None:
    midpoint = level + .5
    estimate = metric["estimatedDifficulty"]
    initial = {key: copy.deepcopy(metric[key]) for key in (
        "estimatedDifficulty", "difficultyDelta", "difficultyCi95Low", "difficultyCi95High",
        "difficultyDeltaCi95Low", "difficultyDeltaCi95High", "effectBand", "effectBandRank",
        "levelRank", "levelComparisonCharts",
    )}
    metric["initialFit"] = initial
    if estimate is None:
        return
    value = compress_difficulty(estimate, level)
    metric["estimatedDifficulty"] = value
    metric["difficultyDelta"] = value - midpoint
    metric["effectBandRank"], metric["effectBand"] = difficulty_effect_band(value - midpoint)
    if metric["defaultedAtCap"]:
        metric["capDefaultDifficulty"] = value
    for side in ("Low", "High"):
        endpoint = compress_difficulty(initial[f"difficultyCi95{side}"], level)
        metric[f"difficultyCi95{side}"] = endpoint
        metric[f"difficultyDeltaCi95{side}"] = None if endpoint is None else endpoint - midpoint


def apply_official_high_level_clearing(
    payload: dict[str, Any], phoenix2_capture: Mapping[str, Any],
    current_catalog: Sequence[Mapping[str, Any]] | None = None, *,
    bootstrap_samples: int = 1000,
) -> dict[str, Any]:
    return _apply_official_high_level_clearing(payload, phoenix2_capture, current_catalog,
                                              bootstrap_samples=bootstrap_samples)


def _apply_official_high_level_clearing(
    payload: dict[str, Any], phoenix2_capture: Mapping[str, Any],
    current_catalog: Sequence[Mapping[str, Any]] | None = None, *,
    bootstrap_samples: int = 1000, combined: bool = False,
    cap_policy: str = COMBINED_CAP_POLICY,
) -> dict[str, Any]:
    """Mutate only target Clearing components and return public policy/audit metadata.

    The caller owns schema dispatch, Pumbility recalculation and summary installation.
    Player histories and all calibration are held fixed in the q20 bootstrap.
    """
    if (phoenix2_capture.get("source") != OFFICIAL_SOURCE
            or phoenix2_capture.get("mix") != ("Phoenix+Phoenix2" if combined else "Phoenix2")):
        raise ValueError("Official high-level Clearing requires a Phoenix2 piuscores-official capture.")
    if type(bootstrap_samples) is not int or bootstrap_samples < 0:
        raise ValueError("bootstrap_samples must be a nonnegative integer.")
    if cap_policy not in (COMBINED_CAP_POLICY, P1_OBSERVED_CAP_POLICY) or (not combined and cap_policy != COMBINED_CAP_POLICY):
        raise ValueError("Unsupported official Clearing cap policy.")
    catalog = {_chart_id(chart): chart for chart in
               (current_catalog if current_catalog is not None else phoenix2_capture.get("catalog", []))}
    if not catalog:
        raise ValueError("Official high-level Clearing requires the current chart catalog.")
    raw_boards = phoenix2_capture.get("boards", {})
    iterable = raw_boards.values() if isinstance(raw_boards, Mapping) else raw_boards
    boards = {str(board["chartId"]): board for board in iterable
              if str(board["chartId"]) in catalog
              and _is_double(catalog[str(board["chartId"])], HISTORY_MINIMUM_LEVEL)}
    entries = {}
    histories: dict[str, dict[str, float]] = defaultdict(dict)
    for chart_id, board in boards.items():
        if any(entry.get("isSupplemented") is True for entry in board.get("entries", [])):
            raise ValueError("Official Clearing cannot use supplemented leaderboard records.")
        players = entries[chart_id] = _board_entries(board)
        for player in players:
            histories[player][chart_id] = int(catalog[chart_id]["level"]) + .5

    folders: dict[int, list[dict[str, Any]]] = defaultdict(list)
    targets = []
    for row in payload.get("doubles", []):
        chart_id = str(row["chartId"])
        chart = catalog.get(chart_id)
        if chart is None:
            if _is_double(row, TARGET_MINIMUM_LEVEL):
                raise ValueError("A D26+ target is absent from the current official chart catalog.")
            continue
        if not _is_double(chart, TARGET_MINIMUM_LEVEL):
            continue
        if row.get("type") != chart["type"] or int(row["level"]) != int(chart["level"]):
            raise ValueError("Official Clearing rows must match the current official chart catalog.")
        samples = entries.get(chart_id, {})
        skills = [ability for player in samples
                  if (ability := official_player_ability(histories[player], chart_id)) is not None]
        row["tierMetrics"]["clearing"] = _new_metric(
            boards.get(chart_id), skills, len(samples), chart_id,
            phoenix2_capture.get("sourceDates") if combined else phoenix2_capture.get("asOf"),
            bootstrap_samples, combined=combined, cap_policy=cap_policy,
        )
        targets.append(row)
        folders[int(chart["level"])].append(row)

    references = {}
    scale_folders = {}
    for level, rows in sorted(folders.items()):
        measured = [row["tierMetrics"]["clearing"] for row in rows
                    if not row["tierMetrics"]["clearing"]["defaultedAtCap"]
                    and row["tierMetrics"]["clearing"]["q20Skill"] is not None]
        reference = float(np.median([m["q20Skill"] for m in measured])) if measured else None
        references[level] = reference
        scale_folders[("Double", level)] = {
            "deltas": [CLEARING_DIFFICULTY_DELTA_SCALE * m["shrinkageWeight"] * (m["q20Skill"] - reference)
                       for m in measured],
            "players": [m["ratedClearCount"] for m in measured],
        }
    spreads = fit_clearing_spreads(scale_folders)
    folder_audits = {}
    for level, rows in sorted(folders.items()):
        midpoint, reference = level + .5, references[level]
        parameters = spreads[("Double", level)]
        for row in rows:
            metric = row["tierMetrics"]["clearing"]
            metric["folderReferenceSkill"] = reference
            metric["spreadCalibration"] = copy.deepcopy(parameters)
            if metric["q20Skill"] is None or metric["defaultedAtCap"]:
                continue
            factor = CLEARING_DIFFICULTY_DELTA_SCALE * metric["shrinkageWeight"]
            bounds = [None if metric[f"q20SkillCi95{side}"] is None else
                      factor * (metric[f"q20SkillCi95{side}"] - reference) for side in ("Low", "High")]
            raw_delta = factor * (metric["q20Skill"] - reference)
            delta, extreme = calibrate_clearing_delta(
                raw_delta, parameters, players=metric["ratedClearCount"],
                coverage=metric["abilityCoverage"], interval_low=bounds[0], interval_high=bounds[1],
            )
            metric["rawDifficultyDelta"] = raw_delta
            metric["extremeOutlier"] = extreme
            _set_metric(metric, midpoint + delta, level, metric["ratedClearCount"])
            if metric["ratedClearCount"] < 10:
                metric["provisionalReasons"].append("fewer-than-10-eligible-official-clearers")
            if parameters["basis"] != "folder":
                metric["provisionalReasons"].append("borrowed-folder-scale" if parameters["basis"] == "neighbor-folder" else "fallback-folder-scale")
                if metric["evidenceStatus"] == "Published":
                    metric["evidenceStatus"] = "Provisional"
            for side, bound in zip(("Low", "High"), bounds):
                if bound is not None:
                    metric[f"difficultyCi95{side}"] = midpoint + parameters["scale"] * bound

        uncapped = [row["tierMetrics"]["clearing"]["estimatedDifficulty"] for row in rows
                    if not row["tierMetrics"]["clearing"]["defaultedAtCap"]
                    and row["tierMetrics"]["clearing"]["estimatedDifficulty"] is not None]
        floor = min(uncapped) if uncapped else midpoint
        cap_basis = "uncapped-folder-minimum" if uncapped else "folder-midpoint-no-uncapped-charts"
        for row in rows:
            metric = row["tierMetrics"]["clearing"]
            if metric["defaultedAtCap"]:
                _set_metric(metric, floor, level, metric["ratedClearCount"])
                metric["evidenceStatus"] = "Provisional" if uncapped and metric["ratedClearCount"] >= 5 else "Insufficient"
                metric.update(capDefaultBasis=cap_basis, preRecenterCapDefaultDifficulty=floor, unratedReason=None)
                metric["provisionalReasons"].append("capped-board-folder-default")
        finite = [row["tierMetrics"]["clearing"]["estimatedDifficulty"] for row in rows
                  if row["tierMetrics"]["clearing"]["estimatedDifficulty"] is not None]
        before_median = float(np.median(finite)) if finite else None
        shift = midpoint - before_median if finite else None
        for row in rows:
            metric = row["tierMetrics"]["clearing"]
            estimate = metric["estimatedDifficulty"]
            metric["unrecenteredDifficulty"] = estimate
            metric["recenteringShift"] = shift
            if estimate is not None:
                recentered = estimate + shift
                metric["estimatedDifficulty"] = recentered
                metric["difficultyDelta"] = recentered - midpoint
                metric["effectBandRank"], metric["effectBand"] = difficulty_effect_band(recentered - midpoint)
                for side in ("Low", "High"):
                    endpoint = metric[f"difficultyCi95{side}"]
                    if endpoint is not None:
                        metric[f"difficultyCi95{side}"] = endpoint + shift
                        metric[f"difficultyDeltaCi95{side}"] = endpoint + shift - midpoint
            _compress_metric(metric, level)
        ordered = sorted((row for row in rows if row["tierMetrics"]["clearing"]["estimatedDifficulty"] is not None),
                         key=lambda row: (row["tierMetrics"]["clearing"]["estimatedDifficulty"],
                                          str(row.get("songName") or ""), str(row["chartId"])))
        for rank, row in enumerate(ordered, 1):
            metric = row["tierMetrics"]["clearing"]
            metric.update(levelRank=rank, levelComparisonCharts=len(ordered))
            metric["initialFit"].update(levelRank=rank, levelComparisonCharts=len(ordered))
        folder_audits[str(level)] = {
            "chartCount": len(rows), "measuredCharts": len(finite),
            "cappedCharts": sum(row["tierMetrics"]["clearing"]["defaultedAtCap"] for row in rows),
            "folderReferenceSkill": reference, "spreadCalibration": copy.deepcopy(parameters),
            "capDefaultInitialDifficulty": floor, "capDefaultBasis": cap_basis,
            "preRecenterMedian": before_median, "recenteringShift": shift,
            "initialMedian": float(np.median([row["tierMetrics"]["clearing"]["initialFit"]["estimatedDifficulty"] for row in ordered])) if ordered else None,
            "finalMedian": float(np.median([row["tierMetrics"]["clearing"]["estimatedDifficulty"] for row in ordered])) if ordered else None,
        }
    audit = {
        "version": 2 if combined else 1, "method": COMBINED_METHOD if combined else METHOD,
        "source": OFFICIAL_SOURCE, "mix": "Phoenix+Phoenix2" if combined else "Phoenix2",
        "asOf": phoenix2_capture.get("asOf"), "minimumLevel": TARGET_MINIMUM_LEVEL,
        "mode": "Double", "targetCharts": len(targets), "availableBoards": sum(str(row["chartId"]) in boards for row in targets),
        "historyBoards": len(boards), "percentile": CLEARING_PERCENTILE,
        "difficultyDeltaScale": CLEARING_DIFFICULTY_DELTA_SCALE,
        "playerSkill": {"method": "leave-one-chart-out-top-official-levels", "topCharts": PLAYER_TOP_CLEARS,
                        "minimumOtherCharts": PLAYER_MINIMUM_OTHER_CLEARS, "historyMinimumLevel": HISTORY_MINIMUM_LEVEL,
                        "levelMidpointOffset": .5, "separateModes": True},
        "shrinkage": {"priorPlayers": CLEARING_PRIOR_PLAYERS, "formula": "n/(n+20)*n/N"},
        "calibration": clearing_calibration_identity(), "folders": folder_audits,
        "recentering": "actual-finite-folder-median-to-current-level-plus-0.5-before-compression",
        "compressionFactor": .5,
        "uncertainty": {"method": "deterministic-conditional-q20-player-bootstrap", "samples": bootstrap_samples,
                        "minimumPlayers": 5, "conditions": "fixed player histories, folder reference, spread and recentering; excludes selection and calibration uncertainty",
                        "cappedDifficultyIntervals": "unavailable-floor-assignment"},
    }
    if combined:
        audit.update(sourceDates=copy.deepcopy(phoenix2_capture["sourceDates"]),
                     manifestSha256=phoenix2_capture["manifestSha256"],
                     mergePolicy="full-tag-bidirectional-raw-max-v1",
                     capPolicy=cap_policy)
    return audit


def apply_combined_official_high_level_clearing(
    payload: dict[str, Any], merged: Mapping[str, Any],
    current_catalog: Sequence[Mapping[str, Any]] | None = None, *,
    bootstrap_samples: int = 1000, cap_policy: str = COMBINED_CAP_POLICY,
) -> dict[str, Any]:
    """Reuse legacy numeric clearing calibration over the validated official union.

    The input is merge_official_scores output, never submitted observations.
    Current chart UUID and mode identify binary clears. Note-count differences
    affect score normalization, not the existence of an official clear.
    """
    if (not isinstance(merged.get("boards"), Mapping) or not isinstance(merged.get("charts"), Mapping)
            or not isinstance(merged.get("manifestSha256"), str)
            or len(merged["manifestSha256"]) != 64
            or set(merged.get("sourceDates", {})) != {"Phoenix", "Phoenix2"}):
        raise ValueError("Combined official Clearing requires a validated official score merge.")
    catalog = merged.get("currentCatalog")
    if not isinstance(catalog, list) or not catalog:
        raise ValueError("Combined official Clearing requires the current official catalog.")
    indexed = {_chart_id(chart): chart for chart in catalog}
    if current_catalog is not None:
        for chart in current_catalog:
            if _is_double(chart, HISTORY_MINIMUM_LEVEL):
                expected = indexed.get(_chart_id(chart))
                if expected is None or any(chart.get(k) != expected.get(k) for k in ("type", "level")):
                    raise ValueError("Combined official Clearing catalog differs from the pinned current catalog.")
    boards = {}
    comparability_diagnostics = defaultdict(list)
    for cid, chart in indexed.items():
        if not _is_double(chart, HISTORY_MINIMUM_LEVEL):
            continue
        metadata = merged["charts"].get(cid)
        observations = merged["boards"].get(cid, {})
        if not isinstance(metadata, Mapping):
            raise ValueError("A current official history chart lacks comparability metadata.")
        source_states = metadata.get("sourceStates", {})
        if set(source_states) != {"Phoenix", "Phoenix2"}:
            raise ValueError("Combined official Clearing requires both source board states.")
        comparability_diagnostics[metadata.get("comparability", "unknown")].append(cid)
        if not observations and not any(s.get("status") in ("available", "empty") for s in source_states.values()):
            continue
        boards[cid] = {"chartId": cid, "entries": [
            {"playerId": player, "score": score, "isSupplemented": False}
            for player, score in observations.items()],
            "rawRowCount": len(observations), "maxRank": None,
            "cutoffScore": min(observations.values(), default=None), "asOf": None,
            "sourceStates": copy.deepcopy(source_states), "comparability": metadata["comparability"],
            "manifestSha256": merged["manifestSha256"]}
    capture = {"source": OFFICIAL_SOURCE, "mix": "Phoenix+Phoenix2", "catalog": catalog,
               "boards": boards, "asOf": None, "sourceDates": merged["sourceDates"],
               "manifestSha256": merged["manifestSha256"]}
    audit = _apply_official_high_level_clearing(payload, capture, catalog,
                                               bootstrap_samples=bootstrap_samples, combined=True, cap_policy=cap_policy)
    for row in payload.get("doubles", []):
        cid = str(row["chartId"])
        if row["level"] >= TARGET_MINIMUM_LEVEL:
            metric = row["tierMetrics"]["clearing"]
            metadata = merged["charts"][cid]
            metric["clearingEvidence"].update(comparabilityPolicy="current-chart-id-and-mode")
            if cap_policy == P1_OBSERVED_CAP_POLICY and metric["clearingEvidence"].get("sourceStates", {}).get("Phoenix", {}).get("possiblyTruncated"):
                metric["provisionalReasons"].append("phoenix1-cap-ignored-for-estimation")
                if metric["evidenceStatus"] == "Published":
                    metric["evidenceStatus"] = "Provisional"
            if metadata.get("comparability") not in ("same", "single-source-known", "no-evidence"):
                metric["provisionalReasons"].append("cross-version-note-count-" + metadata.get("comparability", "unknown"))
                if metric["evidenceStatus"] == "Published":
                    metric["evidenceStatus"] = "Provisional"
    audit.update(comparabilityPolicy="current-chart-id-and-mode",
        historyComparabilityDiagnostics={key: sorted(ids) for key, ids in sorted(comparability_diagnostics.items())})
    return audit
