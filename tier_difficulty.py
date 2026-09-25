"""Clearing percentile estimates and the arithmetic Pumbility tier metric."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

import numpy as np

from piu_misgrade_analyzer import difficulty_effect_band


TIER_METRIC_VERSION = 2
EVIDENCE_ORDER = ("Unrated", "Insufficient", "Provisional", "Published")


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def inclusive_percentile_skill(ratings: Sequence[float]) -> dict[str, Any]:
    """Average actual observations inside the inclusive linear 10th–30th interval."""
    values = np.asarray(ratings, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return {"q10Skill": None, "q30Skill": None, "meanSkill": None, "selectedCount": 0}
    q10, q30 = np.quantile(values, [0.10, 0.30], method="linear")
    selected = values[(values >= q10) & (values <= q30)]
    return {
        "q10Skill": float(q10),
        "q30Skill": float(q30),
        "meanSkill": float(selected.mean()) if len(selected) else None,
        "selectedCount": int(len(selected)),
    }


def _empty_metric() -> dict[str, Any]:
    return {
        "estimatedDifficulty": None,
        "difficultyDelta": None,
        "levelRank": None,
        "levelComparisonCharts": None,
        "effectBandRank": None,
        "effectBand": None,
        "evidenceStatus": "Unrated",
    }


def _set_estimate(metric: dict[str, Any], estimate: float, midpoint: float) -> None:
    metric["estimatedDifficulty"] = estimate
    metric["difficultyDelta"] = estimate - midpoint
    metric["effectBandRank"], metric["effectBand"] = difficulty_effect_band(
        metric["difficultyDelta"]
    )


def assess_clearing_difficulty(
    mean_skill: float, official_level: int, references: Mapping[int, float]
) -> dict[str, Any]:
    """Probe the initial estimate's folder once, without changing either cohort."""
    reference = references[official_level]
    initial = official_level + 0.5 + mean_skill - reference
    # Match the display's 1e-9 tolerance in tenths, without rounding the rating.
    target_level = math.floor(initial + 1e-10)
    assessment_level = official_level
    estimate = initial
    status = "not-needed"
    if target_level != official_level:
        if target_level in references:
            assessment_level = target_level
            reference = references[target_level]
            estimate = target_level + 0.5 + mean_skill - reference
            status = "applied"
        else:
            status = "unavailable"
    return {
        "initialEstimatedDifficulty": initial,
        "estimatedDifficulty": estimate,
        "assessmentLevel": assessment_level,
        "folderReferenceSkill": reference,
        "reassessmentStatus": status,
    }


def build_tier_metrics(
    charts: Sequence[Mapping[str, Any]],
    clearers_by_chart: Mapping[str, set[str]],
    player_mode_skills: Mapping[tuple[str, str], float],
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Return public aggregates using frozen official-folder references and one probe.

    Input scoring estimates must retain full precision until this calculation is
    complete. Player identities and skill samples never enter returned metrics.
    """
    metrics: list[dict[str, Any]] = []
    folders: dict[tuple[str, int], list[int]] = defaultdict(list)
    for index, chart in enumerate(charts):
        chart_type = str(chart["type"])
        if chart_type not in ("Single", "Double"):
            raise ValueError("Clearing metrics require Single or Double charts.")
        clearers = clearers_by_chart.get(str(chart["chartId"]), set())
        ratings = [
            rating
            for player in sorted(clearers)
            if (rating := _finite(player_mode_skills.get((player, chart_type)))) is not None
        ]
        clearing = {
            **_empty_metric(),
            "clearCount": len(clearers),
            "ratedClearCount": len(ratings),
            "missingSkillCount": len(clearers) - len(ratings),
            **inclusive_percentile_skill(ratings),
            "folderReferenceSkill": None,
            "initialEstimatedDifficulty": None,
            "assessmentLevel": None,
            "reassessmentStatus": None,
        }
        metrics.append({
            "clearing": clearing,
            "pumbility": {
                **_empty_metric(),
                "scoringSupportCount": int(chart.get("nContributors") or 0),
                "clearingSupportCount": clearing["selectedCount"],
            },
        })
        folders[(chart_type, int(chart["level"]))].append(index)

    references: dict[str, float] = {}
    mode_references: dict[str, dict[int, float]] = defaultdict(dict)
    for (chart_type, level), indices in folders.items():
        measurable = [
            index for index in indices
            if metrics[index]["clearing"]["meanSkill"] is not None
        ]
        if not measurable:
            continue
        reference = float(np.median([
            metrics[index]["clearing"]["meanSkill"] for index in measurable
        ]))
        references[f"{'S' if chart_type == 'Single' else 'D'}{level}"] = reference
        mode_references[chart_type][level] = reference

    # All references must be frozen before probing any chart against another level.
    for (chart_type, level), indices in folders.items():
        reference = mode_references[chart_type].get(level)
        if reference is None:
            continue
        midpoint = float(level) + 0.5
        for index in indices:
            clearing = metrics[index]["clearing"]
            clearing["folderReferenceSkill"] = reference
            if clearing["meanSkill"] is None:
                continue
            clearing.update(assess_clearing_difficulty(
                clearing["meanSkill"], level, mode_references[chart_type]
            ))
            estimate = clearing["estimatedDifficulty"]
            _set_estimate(clearing, estimate, midpoint)
            count = clearing["selectedCount"]
            clearing["evidenceStatus"] = (
                "Published" if count >= 10 else "Provisional" if count >= 5 else "Insufficient"
            )
            scoring = _finite(charts[index].get("estimatedDifficulty"))
            if scoring is not None:
                pumbility = metrics[index]["pumbility"]
                _set_estimate(pumbility, (scoring + estimate) / 2.0, midpoint)
                scoring_status = str(charts[index].get("evidenceStatus") or "Unrated")
                pumbility["evidenceStatus"] = EVIDENCE_ORDER[min(
                    EVIDENCE_ORDER.index(scoring_status),
                    EVIDENCE_ORDER.index(clearing["evidenceStatus"]),
                )]

        for name in ("clearing", "pumbility"):
            ranked = sorted(
                (index for index in indices if metrics[index][name]["estimatedDifficulty"] is not None),
                key=lambda index: (
                    metrics[index][name]["estimatedDifficulty"],
                    str(charts[index].get("songName") or ""),
                    str(charts[index]["chartId"]),
                ),
            )
            for rank, index in enumerate(ranked, start=1):
                metrics[index][name]["levelRank"] = rank
                metrics[index][name]["levelComparisonCharts"] = len(ranked)
    return metrics, references


def tier_metric_method(folder_references: Mapping[str, float]) -> dict[str, Any]:
    return {
        "version": TIER_METRIC_VERSION,
        "clearing": {
            "skillRating": "current mode-specific scoringRating from top-20 selected-source Pumbility",
            "clearPopulation": "unique nonbroken current-catalog clearers across both Phoenix versions",
            "percentiles": [0.10, 0.30],
            "percentileMethod": "linear, inclusive boundaries and ties",
            "calibration": "assessment level + 0.5 + mean selected skill - frozen official-folder median selected skill",
            "initialCalibration": "official level + 0.5 + mean selected skill - official-folder median selected skill",
            "reassessment": {
                "method": "one reassessment against the initial estimate's integer level",
                "referencePopulation": "original official-level folders; incoming charts are not inserted",
                "boundaryTolerance": 1e-10,
                "missingReference": "retain initial estimate",
                "finalMedianAnchored": False,
            },
            "folderReferenceSkills": dict(folder_references),
            "evidenceMinimumSelected": {"Published": 10, "Provisional": 5, "Insufficient": 1},
        },
        "pumbility": {
            "calculation": "(scoring difficulty + clearing difficulty) / 2 before rounding",
            "evidence": "weaker component status; separate scoring and clearing support counts",
        },
        "limitedDataSupportThreshold": 20,
    }
