"""Deterministic target-only tail taper with a single global exception budget."""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

from sss_scoring import MODES, _derived

TAIL_METHOD = "target-midpoint-soft-tail-global-exceptions-v1"
TAIL_PARAMETERS = {"version": 1, "method": TAIL_METHOD, "identityRadius": 1.5,
    "ordinaryAsymptote": 1.9, "exceptionThreshold": 2., "exceptionLimit": 10,
    "exceptionCeiling": 2.5}
INTERVAL_METHOD = "conditional-target-tail-fixed-selection-and-calibration-v1"


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def tail_delta(delta: float, *, exceptional: bool = False) -> float:
    if not finite(delta):
        raise ValueError("Tail projection requires a finite delta.")
    if exceptional:
        return max(-2.5, min(2.5, delta))
    magnitude = abs(delta)
    if magnitude <= 1.5:
        return delta
    excess = magnitude - 1.5
    return math.copysign(1.5 + .4 * (excess / (.4 + excess)), delta)


def tail_value(value: float | None, level: int, *, exceptional: bool = False) -> float | None:
    return None if value is None else level + .5 + tail_delta(value - level - .5, exceptional=exceptional)


def _groups(payload: Mapping[str, Any], goal: str) -> dict[tuple[str, str, int], list[dict]]:
    groups = defaultdict(list)
    for mode, key in MODES:
        for row in payload.get(key, []):
            if finite(row["scoringVariants"][goal].get("estimatedDifficulty")):
                groups[("official" if row["level"] >= 20 else "submitted", mode, int(row["level"]))].append(row)
    return groups


def exception_evidence(row: Mapping[str, Any], *, goal: str, protected: bool) -> dict:
    metric, midpoint = row["scoringVariants"][goal], row["level"] + .5
    value = metric["estimatedDifficulty"]
    delta = value - midpoint
    refinement = metric.get("scoringRefinement", {})
    reasons = []
    if abs(delta) < 2:
        reasons.append("inside-two-point-boundary")
    if protected:
        reasons.append("protected-folder-median")
    if metric.get("scoringMethod") != "target-attainment-rasch-v1" or metric.get("scoringEstimateBranch") not in (None, "mixed"):
        reasons.append("not-ordinary-mixed-first-pass")
    if metric.get("nContributors", 0) < 20 or min(metric.get("scoringTargetSuccesses", 0), metric.get("scoringBelowTarget", 0)) < 5:
        reasons.append("insufficient-two-sided-player-support")
    if not metric.get("scoringFitConverged") or not metric.get("scoringMainComponent"):
        reasons.append("unconverged-or-disconnected-fit")
    if metric.get("scoringComparability") not in ("comparable", "same", "single-source-known"):
        reasons.append("incomparable-chart")
    if any(reason in metric.get("scoringProvisionalReasons", []) for reason in ("regularization-sensitive", "weak-target-information")):
        reasons.append("unstable-or-weak-information")
    if metric.get("scoringPossiblyTruncated") or refinement.get("fullBoard", {}).get("possiblyTruncated"):
        reasons.append("possibly-truncated-board")
    frozen = refinement.get("range")
    if not frozen or not frozen.get("supported") or frozen.get("kind") != "observed":
        reasons.append("borrowed-or-unsupported-local-range")
    if metric.get("scoringExtrapolated") or "unsupported-folder-anchor" in metric.get("scoringProvisionalReasons", []):
        reasons.append("unsupported-or-extrapolated-anchor")
    centering = metric.get("scoringFolderCentering")
    if row["level"] >= 20 and (not centering or centering.get("basis") != "folder"):
        reasons.append("borrowed-or-unavailable-folder-centering")
    low, high = metric.get("difficultyCi95Low"), metric.get("difficultyCi95High")
    conservative = None
    if not finite(low) or not finite(high) or low > high:
        reasons.append("unavailable-player-bootstrap-interval")
    else:
        conservative = low - midpoint if delta >= 0 else midpoint - high
        if conservative <= 2:
            reasons.append("interval-not-entirely-beyond-two")
    return {"exceptionEligible": not reasons, "rejectionReasons": reasons,
            "conservativeDistance": conservative, "protectedMedian": protected}


def select_exceptions(payload: Mapping[str, Any], *, goal: str = "sssplus") -> tuple[set[str], dict[str, dict]]:
    """Choose whole contiguous eligible outer blocks, with one shared ten-chart budget."""
    evidence, tails = {}, []
    for (_, _, level), rows in sorted(_groups(payload, goal).items()):
        ordered = sorted(rows, key=lambda row: (row["scoringVariants"][goal]["estimatedDifficulty"], str(row["chartId"])))
        measured = any(row["scoringVariants"][goal].get("scoringFolderCentering", {}).get("basis") == "folder" for row in rows)
        protected_values = set()
        if measured:
            protected_values = {ordered[(len(ordered) - 1) // 2]["scoringVariants"][goal]["estimatedDifficulty"],
                                ordered[len(ordered) // 2]["scoringVariants"][goal]["estimatedDifficulty"]}
        for row in ordered:
            cid = str(row["chartId"])
            evidence[cid] = exception_evidence(row, goal=goal,
                protected=row["scoringVariants"][goal]["estimatedDifficulty"] in protected_values)
        for side in (-1, 1):
            outer = [row for row in ordered if side * (row["scoringVariants"][goal]["estimatedDifficulty"] - level - .5) >= 2]
            outer.sort(key=lambda row: (-abs(row["scoringVariants"][goal]["estimatedDifficulty"] - level - .5), str(row["chartId"])))
            blocks, offset, blocked = [], 0, False
            while offset < len(outer):
                end = offset + 1
                value = outer[offset]["scoringVariants"][goal]["estimatedDifficulty"]
                while end < len(outer) and outer[end]["scoringVariants"][goal]["estimatedDifficulty"] == value:
                    end += 1
                block = outer[offset:end]
                if blocked or not all(evidence[str(row["chartId"])]["exceptionEligible"] for row in block):
                    for row in block:
                        if evidence[str(row["chartId"])]["exceptionEligible"]:
                            evidence[str(row["chartId"])]["rejectionReasons"].append("ineligible-outer-chart-blocks-tail")
                    blocked = True
                else:
                    blocks.append(block)
                offset = end
            if blocks:
                tails.append(blocks)
    selected, remaining = set(), 10
    while tails and remaining:
        def priority(blocks: list) -> tuple:
            block = blocks[0]
            return (-min(evidence[str(row["chartId"])]["conservativeDistance"] for row in block),
                    -min(row["scoringVariants"][goal]["nContributors"] for row in block),
                    min(str(row["chartId"]) for row in block))
        tails.sort(key=priority)
        blocks = tails.pop(0)
        block = blocks.pop(0)
        if len(block) > remaining:
            for group in [block, *blocks]:
                for row in group:
                    evidence[str(row["chartId"])]["rejectionReasons"].append("global-exception-budget")
            continue
        selected.update(str(row["chartId"]) for row in block)
        remaining -= len(block)
        if blocks:
            tails.append(blocks)
    for row_blocks in tails:
        for block in row_blocks:
            for row in block:
                evidence[str(row["chartId"])]["rejectionReasons"].append("global-exception-budget")
    return selected, evidence


def apply_tail_policy(payload: dict, *, goal: str = "sssplus") -> dict:
    selected, evidence = select_exceptions(payload, goal=goal)
    pre_values, final_values, changed = [], [], 0
    for _, key in MODES:
        for row in payload.get(key, []):
            metric, level, cid = row["scoringVariants"][goal], row["level"], str(row["chartId"])
            row[goal + "TailBaseline"] = {"scoring": copy.deepcopy(metric), "pumbility": copy.deepcopy(row["pumbilityVariants"][goal])}
            before = metric["estimatedDifficulty"]
            exceptional = cid in selected
            detail = {"version": 1, "method": TAIL_METHOD, "midpoint": level + .5,
                "preTailDifficulty": before, "preTailDifficultyCi95Low": metric.get("difficultyCi95Low"),
                "preTailDifficultyCi95High": metric.get("difficultyCi95High"),
                "ordinaryDifficulty": tail_value(before, level), "exceptionSelected": exceptional,
                **evidence.get(cid, {"exceptionEligible": False, "rejectionReasons": ["unrated"],
                                     "conservativeDistance": None, "protectedMedian": False})}
            for field in ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"):
                metric[field] = tail_value(metric.get(field), level, exceptional=exceptional)
            metric.update(_derived(metric["estimatedDifficulty"], level))
            for side in ("Low", "High"):
                endpoint = metric[f"difficultyCi95{side}"]
                metric[f"difficultyDeltaCi95{side}"] = None if endpoint is None else endpoint - level - .5
            detail.update(finalDifficulty=metric["estimatedDifficulty"], finalDifficultyCi95Low=metric["difficultyCi95Low"],
                          finalDifficultyCi95High=metric["difficultyCi95High"])
            metric["scoringTailPolicy"] = detail
            if before is not None:
                pre_values.append(before - level - .5)
                final_values.append(metric["estimatedDifficulty"] - level - .5)
                changed += before != metric["estimatedDifficulty"]
                if metric["difficultyCi95Low"] is not None:
                    metric["intervalMethod"] = INTERVAL_METHOD
    for (_, _, _), rows in _groups(payload, goal).items():
        values = [row["scoringVariants"][goal]["estimatedDifficulty"] for row in rows]
        for row in rows:
            row["scoringVariants"][goal]["scoringTailPolicy"]["finalFolderRange"] = {"lower": min(values), "upper": max(values)}
    counts = {"ratedCharts": len(final_values), "changedCharts": changed,
        "preTailAbsAbove1_5": sum(abs(delta) > 1.5 for delta in pre_values),
        "preTailAbsAtLeast2": sum(abs(delta) >= 2 for delta in pre_values),
        "finalAbsAbove1_5": sum(abs(delta) > 1.5 for delta in final_values),
        "finalAbsAtLeast2": sum(abs(delta) >= 2 for delta in final_values),
        "finalAbsAbove2_5": sum(abs(delta) > 2.5 for delta in final_values),
        "displayedAbsAtLeast2": sum(abs(math.trunc((row["scoringVariants"][goal]["estimatedDifficulty"] * 10 + 1e-9)) / 10 - row["level"] - .5) >= 2
            for _, key in MODES for row in payload.get(key, []) if finite(row["scoringVariants"][goal]["estimatedDifficulty"])),
        "preTailMinDelta": min(pre_values, default=None), "preTailMaxDelta": max(pre_values, default=None),
        "finalMinDelta": min(final_values, default=None), "finalMaxDelta": max(final_values, default=None)}
    return {**TAIL_PARAMETERS, "targetKey": goal, "selectedChartIds": sorted(selected), "counts": counts,
            "intervalMethod": INTERVAL_METHOD, "selection": "contiguous-eligible-outer-tie-blocks-global-conservative-distance-budget-v1"}
