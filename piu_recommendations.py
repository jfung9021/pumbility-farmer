"""Combined Phoenix chart estimates and player-specific recommendations.

Phoenix 1 and Phoenix 2 use different Pumbility scales.  This module therefore
normalizes player residuals within each version and mode before pooling chart
evidence.  Phoenix 2 is authoritative for the catalog and for every overlapping
player/chart score.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from pumbility_contract import (
    RECOMMENDATION_SCHEMA_VERSION,
    combined_tier_blob_path,
    phoenix1_snapshot_path,
    recommendation_blob_path,
    recommendation_generation_key,
    recommendation_shard_path,
    recommendation_shard_prefix,
)

from phoenix1_score_overrides import (
    convert_phoenix1_pumbility,
    convert_phoenix1_score,
    phoenix1_score_overrides_metadata,
)
from piu_misgrade_analyzer import (
    AnalysisConfig,
    DIFFICULTY_DELTA_SCALE,
    EFFECT_BANDS,
    FOLDER_RANGE_REFERENCE_CHARTS,
    MIN_TARGET_LEVEL,
    MODE_LABELS,
    MODE_TYPES,
    RELATIVE_GROUPS,
    SCRIPT_VERSION,
    _fit_level_calibration,
    apply_within_level_difficulty,
)
from phoenix2_pumbility import (
    GRADE_BANDS,
    PLATE_CODES,
    SKILL_RATING_REFERENCE_GRADE,
    SKILL_RATING_REFERENCE_MULTIPLIER,
    SKILL_RATING_REFERENCE_PLATE,
    PlateProjectionModel,
    grade_for_score,
    normalize_plate,
    phoenix2_coop_rating,
    phoenix2_pumbility,
    skill_rating_for_pumbility,
)


RECOMMENDATION_STORAGE_SCHEMA_VERSION = 2
RECOMMENDATION_SHARD_SIZE = 10
COMBINED_TIER_SCHEMA_VERSION = 5
RECOMMENDATION_RADIUS = 1.0
WHAT_IF_LEVEL_RADIUS = 3
BASELINE_START_RANK = 11
BASELINE_END_RANK = 30
RECOMMENDATION_RATING_SCORE_COUNT = 20
PHOENIX2_RATING_SCORE_THRESHOLD = RECOMMENDATION_RATING_SCORE_COUNT
PROJECTION_RATING_START_RANK = BASELINE_START_RANK
PROJECTION_RATING_END_RANK = BASELINE_END_RANK
PROJECTION_RATING_SCORE_THRESHOLD = PROJECTION_RATING_END_RANK
TOP_PUMBILITY_COUNT = 50
TOP_RECOMMENDATION_COUNT = 20
MAX_RAW_SCORE = 1_000_000
SCORE_RESPONSE_MODEL_NAME = "population-crossfit-monotone-v3"
SCORE_PROJECTION_MODEL_NAME = "similar-skill-pumbility-11-30-weighted-q50-v8"
COOP_SCORE_PROJECTION_MODEL_NAME = "estimated-difficulty-master-grade-ladder-v1"
COOP_SCORE_QUANTILE = 0.75
COOP_DIFFICULTY_MODEL_NAME = "conditional-q75-player-source-adjusted-log-miss-v2"
COOP_DIFFICULTY_REFERENCE_PERCENTILE = 0.50
COOP_ABILITY_SCORE_COUNT = 20
COOP_DIFFICULTY_EASIEST = 10
COOP_DIFFICULTY_MEDIAN = 17
COOP_DIFFICULTY_HARDEST = 25
COOP_MASTER_TITLE_RATING = 16_000.0
COOP_GOAL_PLATE = "Fair Game"
COOP_GOAL_GRADE_BANDS = (
    (12, "SSS+"),
    (13, "SS+"),
    (14, "SS"),
    (15, "S"),
    (17, "AAA+"),
    (18, "AAA"),
    (20, "AA+"),
    (24, "A+"),
    (25, "B"),
)
COOP_GOAL_SCORE_BY_GRADE = {
    grade: int(score) for score, grade, _ in GRADE_BANDS
}
SCORE_RESPONSE_FOLDS = 5
SCORE_RESPONSE_GRID_STEP = 0.1
SCORE_RESPONSE_SMOOTHING_RADIUS = 8
SCORE_RESPONSE_MIN_SUPPORT = 5
PEER_SCORE_QUANTILE = 0.50
PEER_SCORE_SUPPORT_TARGETS = (20, 10, 5)
PEER_SCORE_MIN_USABLE_SUPPORT = 5
PEER_SCORE_INITIAL_RADIUS = 0.2
PEER_SCORE_MAX_RADIUS = 0.5
PEER_SCORE_RADIUS_STEP = 0.1
PLAYER_KEY_NAMESPACE = "pumbility-farmer-recommendations-v1"
SOURCE_WEIGHTS = {"phoenix1": 1.0, "phoenix2": 2.0}
ABILITY_FULL_WEIGHT_RADIUS = 1.0
ABILITY_OUTSIDE_WEIGHT = 0.5
RECOMMENDATION_CHART_FIELDS = (
    "mode",
    "songName",
    "difficulty",
    "type",
    "level",
    "chartId",
    "imageUrl",
    "noteCount",
    "stepArtist",
    "bpmMin",
    "bpmMax",
    "estimatedDifficulty",
    "difficultyModelContinuous",
    "difficultyModelSignal",
    "difficultyDelta",
    "difficultyCi95Low",
    "difficultyCi95High",
    "nContributors",
    "phoenix1Contributors",
    "phoenix2Contributors",
    "evidenceStatus",
    "percentileScore",
    "percentileGrade",
    "percentilePlate",
    "percentilePlateCode",
    "percentileSupportCount",
)
TOP_SCORE_CHART_FIELDS = tuple(
    field
    for field in RECOMMENDATION_CHART_FIELDS
    if field
    not in {
        "difficultyModelContinuous",
        "difficultyModelSignal",
        "percentileScore",
        "percentileGrade",
        "percentilePlate",
        "percentilePlateCode",
        "percentileSupportCount",
    }
)


def _weighted_quantile(
    values: np.ndarray,
    weights: np.ndarray,
    quantile: float,
) -> float:
    """Return the lowest value whose positive cumulative weight reaches q."""
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    values = values[valid]
    weights = weights[valid]
    if not len(values):
        return math.nan
    order = np.argsort(values, kind="mergesort")
    values = values[order]
    weights = weights[order]
    threshold = min(1.0, max(0.0, float(quantile))) * float(weights.sum())
    index = int(np.searchsorted(np.cumsum(weights), threshold, side="left"))
    return float(values[min(index, len(values) - 1)])


def _weighted_robust_location(values: np.ndarray, weights: np.ndarray) -> float:
    """Return the existing Huber-style location with weighted pooling."""
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    values = values[valid]
    weights = weights[valid]
    if not len(values):
        return math.nan
    if len(values) < 3:
        return float(np.average(values, weights=weights))
    median = _weighted_quantile(values, weights, 0.5)
    mad = _weighted_quantile(np.abs(values - median), weights, 0.5)
    if not math.isfinite(mad) or mad <= 0:
        return float(np.average(values, weights=weights))
    limit = 2.5 * 1.4826 * mad
    return float(np.average(np.clip(values, median - limit, median + limit), weights=weights))


def _effective_sample_size(weights: np.ndarray) -> float:
    weights = np.asarray(weights, dtype=float)
    weights = weights[np.isfinite(weights) & (weights > 0)]
    if not len(weights):
        return 0.0
    weight_sum = float(weights.sum())
    square_sum = float(np.dot(weights, weights))
    return weight_sum * weight_sum / square_sum if square_sum > 0 else 0.0


def _observation_weight(source: object, player_ability: object, level: int) -> float:
    source_weight = SOURCE_WEIGHTS.get(str(source), 1.0)
    try:
        ability = float(player_ability)
    except (TypeError, ValueError):
        ability = math.nan
    ability_weight = (
        ABILITY_OUTSIDE_WEIGHT
        if math.isfinite(ability)
        and abs(ability - (float(level) + 0.5)) > ABILITY_FULL_WEIGHT_RADIUS
        else 1.0
    )
    return source_weight * ability_weight


@dataclass(frozen=True)
class ScoreProjectionResult:
    score: int | None
    source: str
    support_count: int
    confidence: str


@dataclass(frozen=True)
class _PeerScoreCohort:
    player_keys: np.ndarray
    ratings: np.ndarray
    scores: np.ndarray
    ranks: np.ndarray
    weights: np.ndarray | None = None

    def __post_init__(self) -> None:
        size = len(self.player_keys)
        weights = (
            np.ones(size, dtype=float)
            if self.weights is None
            else np.asarray(self.weights, dtype=float)
        )
        if (
            len(self.ratings) != size
            or len(self.scores) != size
            or len(self.ranks) != size
            or len(weights) != size
            or np.any(~np.isfinite(weights))
            or np.any(weights <= 0)
        ):
            raise ValueError("A peer score cohort has inconsistent array lengths.")
        object.__setattr__(self, "weights", weights)

    def to_payload(self) -> dict[str, Any]:
        return {
            "playerKeys": self.player_keys.tolist(),
            "ratings": self.ratings.tolist(),
            "scores": self.scores.tolist(),
            "ranks": self.ranks.tolist(),
            "weights": self.weights.tolist(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "_PeerScoreCohort":
        player_keys = np.asarray(payload.get("playerKeys", []), dtype=np.str_)
        raw_ranks = payload.get("ranks")
        if raw_ranks is None and len(player_keys):
            return cls(
                np.asarray([], dtype=np.str_),
                np.asarray([], dtype=float),
                np.asarray([], dtype=float),
                np.asarray([], dtype=np.int64),
            )
        return cls(
            player_keys,
            np.asarray(payload.get("ratings", []), dtype=float),
            np.asarray(payload.get("scores", []), dtype=float),
            np.asarray(raw_ranks if raw_ranks is not None else [], dtype=np.int64),
            np.asarray(payload.get("weights", np.ones(len(player_keys))), dtype=float),
        )

    def predict(
        self, player_key: str, scoring_rating: float
    ) -> tuple[float, int, str] | None:
        if not math.isfinite(scoring_rating) or not len(self.ratings):
            return None
        distances = np.abs(self.ratings - float(scoring_rating))
        eligible_player = self.player_keys != str(player_key)
        radius_count = int(
            round(
                (PEER_SCORE_MAX_RADIUS - PEER_SCORE_INITIAL_RADIUS)
                / PEER_SCORE_RADIUS_STEP
            )
        )
        selected = np.zeros(len(self.ratings), dtype=bool)
        support = 0
        for target_support in PEER_SCORE_SUPPORT_TARGETS:
            for step in range(radius_count + 1):
                radius = round(
                    PEER_SCORE_INITIAL_RADIUS + step * PEER_SCORE_RADIUS_STEP, 10
                )
                selected = eligible_player & (
                    distances <= float(radius) + 1e-9
                )
                support = int(np.count_nonzero(selected))
                if support >= target_support:
                    break
            if support >= target_support:
                break
        if support < PEER_SCORE_MIN_USABLE_SUPPORT:
            return None
        selected_scores = self.scores[selected]
        selected_weights = self.weights[selected]
        score = _weighted_quantile(
            selected_scores, selected_weights, PEER_SCORE_QUANTILE
        )
        return (score, support, "peer-all-q50") if math.isfinite(score) else None


def _peer_cohort_key(mode_key: str, chart_id: object) -> str:
    return f"{mode_key}\u001f{str(chart_id)}"


@dataclass(frozen=True)
class _ScoreSurface:
    rating_axis: np.ndarray
    difficulty_axis: np.ndarray
    score_grid: np.ndarray
    support_grid: np.ndarray

    def to_payload(self) -> dict[str, Any]:
        return {
            "ratingAxis": self.rating_axis.tolist(),
            "difficultyAxis": self.difficulty_axis.tolist(),
            "scoreGrid": self.score_grid.tolist(),
            "supportGrid": self.support_grid.tolist(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "_ScoreSurface":
        rating_axis = np.asarray(payload.get("ratingAxis", []), dtype=float)
        difficulty_axis = np.asarray(payload.get("difficultyAxis", []), dtype=float)
        score_grid = np.asarray(payload.get("scoreGrid", []), dtype=float)
        support_grid = np.asarray(payload.get("supportGrid", []), dtype=float)
        expected = (len(rating_axis), len(difficulty_axis))
        if (
            len(rating_axis) < 2
            or len(difficulty_axis) < 2
            or score_grid.shape != expected
            or support_grid.shape != expected
        ):
            raise ValueError("A stored score-response surface has an invalid shape.")
        return cls(rating_axis, difficulty_axis, score_grid, support_grid)

    def predict(self, rating: float, difficulty: float) -> tuple[float, int] | None:
        if (
            not math.isfinite(rating)
            or not math.isfinite(difficulty)
            or rating < float(self.rating_axis[0])
            or rating > float(self.rating_axis[-1])
            or difficulty < float(self.difficulty_axis[0])
            or difficulty > float(self.difficulty_axis[-1])
        ):
            return None

        def bounds(axis: np.ndarray, value: float) -> tuple[int, int, float]:
            high = int(np.searchsorted(axis, value, side="right"))
            high = min(max(high, 1), len(axis) - 1)
            low = high - 1
            width = float(axis[high] - axis[low])
            fraction = 0.0 if width <= 0 else (value - float(axis[low])) / width
            return low, high, min(1.0, max(0.0, fraction))

        r0, r1, rw = bounds(self.rating_axis, rating)
        d0, d1, dw = bounds(self.difficulty_axis, difficulty)

        def bilinear(grid: np.ndarray) -> float:
            low = float(grid[r0, d0]) * (1 - dw) + float(grid[r0, d1]) * dw
            high = float(grid[r1, d0]) * (1 - dw) + float(grid[r1, d1]) * dw
            return low * (1 - rw) + high * rw

        score = bilinear(self.score_grid)
        support = int(round(max(0.0, bilinear(self.support_grid))))
        if not math.isfinite(score) or support < SCORE_RESPONSE_MIN_SUPPORT:
            return None
        return score, support


@dataclass(frozen=True)
class ScoreResponseModel:
    full_surfaces: Mapping[str, _ScoreSurface]
    crossfit_surfaces: Mapping[int, Mapping[str, _ScoreSurface]]
    training_player_ids: frozenset[str]
    peer_cohorts: Mapping[str, _PeerScoreCohort] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "fullSurfaces": {
                mode: surface.to_payload()
                for mode, surface in self.full_surfaces.items()
            },
            "crossfitSurfaces": {
                str(fold): {
                    mode: surface.to_payload()
                    for mode, surface in surfaces.items()
                }
                for fold, surfaces in self.crossfit_surfaces.items()
            },
            "trainingPlayerIds": sorted(self.training_player_ids),
            "peerCohorts": {
                key: cohort.to_payload()
                for key, cohort in sorted(self.peer_cohorts.items())
            },
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ScoreResponseModel":
        raw_full = payload.get("fullSurfaces", {})
        raw_crossfit = payload.get("crossfitSurfaces", {})
        if not isinstance(raw_full, Mapping) or not isinstance(raw_crossfit, Mapping):
            raise ValueError("The stored score-response model is invalid.")
        full = {
            str(mode): _ScoreSurface.from_payload(surface)
            for mode, surface in raw_full.items()
            if isinstance(surface, Mapping)
        }
        crossfit: dict[int, dict[str, _ScoreSurface]] = {}
        for raw_fold, raw_surfaces in raw_crossfit.items():
            if not isinstance(raw_surfaces, Mapping):
                continue
            fold = int(raw_fold)
            crossfit[fold] = {
                str(mode): _ScoreSurface.from_payload(surface)
                for mode, surface in raw_surfaces.items()
                if isinstance(surface, Mapping)
            }
        return cls(
            full,
            crossfit,
            frozenset(str(value) for value in payload.get("trainingPlayerIds", [])),
            {
                str(key): _PeerScoreCohort.from_payload(cohort)
                for key, cohort in payload.get("peerCohorts", {}).items()
                if isinstance(cohort, Mapping)
            }
            if isinstance(payload.get("peerCohorts", {}), Mapping)
            else {},
        )

    def to_npz_bytes(self) -> bytes:
        """Serialize numeric surfaces compactly without executable pickle data."""
        arrays: dict[str, np.ndarray] = {
            "training_player_ids": np.asarray(
                sorted(self.training_player_ids), dtype=np.str_
            ),
            "crossfit_folds": np.asarray(
                sorted(self.crossfit_surfaces), dtype=np.int64
            ),
        }
        cohort_keys = sorted(self.peer_cohorts)
        cohort_offsets = [0]
        peer_player_keys: list[np.ndarray] = []
        peer_ratings: list[np.ndarray] = []
        peer_scores: list[np.ndarray] = []
        peer_ranks: list[np.ndarray] = []
        peer_weights: list[np.ndarray] = []
        for key in cohort_keys:
            cohort = self.peer_cohorts[key]
            peer_player_keys.append(np.asarray(cohort.player_keys, dtype=np.str_))
            peer_ratings.append(np.asarray(cohort.ratings, dtype=float))
            peer_scores.append(np.asarray(cohort.scores, dtype=float))
            peer_ranks.append(np.asarray(cohort.ranks, dtype=np.int64))
            peer_weights.append(np.asarray(cohort.weights, dtype=float))
            cohort_offsets.append(cohort_offsets[-1] + len(cohort.ratings))
        arrays.update(
            {
                "peer_cohort_keys": np.asarray(cohort_keys, dtype=np.str_),
                "peer_cohort_offsets": np.asarray(cohort_offsets, dtype=np.int64),
                "peer_player_keys": (
                    np.concatenate(peer_player_keys)
                    if peer_player_keys
                    else np.asarray([], dtype=np.str_)
                ),
                "peer_ratings": (
                    np.concatenate(peer_ratings)
                    if peer_ratings
                    else np.asarray([], dtype=float)
                ),
                "peer_scores": (
                    np.concatenate(peer_scores)
                    if peer_scores
                    else np.asarray([], dtype=float)
                ),
                "peer_ranks": (
                    np.concatenate(peer_ranks)
                    if peer_ranks
                    else np.asarray([], dtype=np.int64)
                ),
                "peer_weights": (
                    np.concatenate(peer_weights)
                    if peer_weights
                    else np.asarray([], dtype=float)
                ),
            }
        )
        for prefix, surfaces in (
            ("full", self.full_surfaces),
            *(
                (f"fold_{fold}", fold_surfaces)
                for fold, fold_surfaces in sorted(self.crossfit_surfaces.items())
            ),
        ):
            for mode, surface in sorted(surfaces.items()):
                base = f"{prefix}__{mode}"
                arrays[f"{base}__rating"] = surface.rating_axis
                arrays[f"{base}__difficulty"] = surface.difficulty_axis
                arrays[f"{base}__score"] = surface.score_grid
                arrays[f"{base}__support"] = surface.support_grid
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)
        return buffer.getvalue()

    @classmethod
    def from_npz_bytes(cls, payload: bytes) -> "ScoreResponseModel":
        """Restore a score model from the non-pickle compressed artifact."""
        try:
            with np.load(io.BytesIO(payload), allow_pickle=False) as arrays:
                names = set(arrays.files)
                training_ids = frozenset(
                    str(value) for value in arrays["training_player_ids"].tolist()
                )
                fold_ids = [int(value) for value in arrays["crossfit_folds"].tolist()]
                peer_names = {
                    "peer_cohort_keys",
                    "peer_cohort_offsets",
                    "peer_player_keys",
                    "peer_ratings",
                    "peer_scores",
                }
                peer_rank_name = "peer_ranks"
                peer_weight_name = "peer_weights"
                peer_cohorts: dict[str, _PeerScoreCohort] = {}
                present_peer_names = names & peer_names
                if present_peer_names and present_peer_names != peer_names:
                    raise ValueError("A stored peer score model is incomplete.")
                if peer_rank_name in names and present_peer_names != peer_names:
                    raise ValueError("A stored peer score model is incomplete.")
                if present_peer_names and peer_rank_name in names:
                    cohort_keys = [
                        str(value) for value in arrays["peer_cohort_keys"].tolist()
                    ]
                    offsets = np.asarray(arrays["peer_cohort_offsets"], dtype=np.int64)
                    player_keys = np.asarray(arrays["peer_player_keys"], dtype=np.str_)
                    ratings = np.asarray(arrays["peer_ratings"], dtype=float)
                    scores = np.asarray(arrays["peer_scores"], dtype=float)
                    ranks = np.asarray(arrays[peer_rank_name], dtype=np.int64)
                    weights = (
                        np.asarray(arrays[peer_weight_name], dtype=float)
                        if peer_weight_name in names
                        else np.ones(len(player_keys), dtype=float)
                    )
                    if (
                        len(offsets) != len(cohort_keys) + 1
                        or not len(offsets)
                        or int(offsets[0]) != 0
                        or np.any(np.diff(offsets) < 0)
                        or int(offsets[-1]) != len(player_keys)
                        or len(ratings) != len(player_keys)
                        or len(scores) != len(player_keys)
                        or len(ranks) != len(player_keys)
                        or len(weights) != len(player_keys)
                        or np.any(ranks < 1)
                        or np.any(~np.isfinite(weights))
                        or np.any(weights <= 0)
                    ):
                        raise ValueError("A stored peer score model is invalid.")
                    for index, key in enumerate(cohort_keys):
                        start = int(offsets[index])
                        end = int(offsets[index + 1])
                        peer_cohorts[key] = _PeerScoreCohort(
                            player_keys[start:end],
                            ratings[start:end],
                            scores[start:end],
                            ranks[start:end],
                            weights[start:end],
                        )
                surfaces: dict[str, dict[str, _ScoreSurface]] = {}
                for name in sorted(names):
                    if not name.endswith("__rating"):
                        continue
                    base = name.removesuffix("__rating")
                    prefix, mode = base.split("__", 1)
                    required = {
                        f"{base}__difficulty",
                        f"{base}__score",
                        f"{base}__support",
                    }
                    if not required.issubset(names):
                        raise ValueError("A stored score-response surface is incomplete.")
                    surface = _ScoreSurface(
                        np.asarray(arrays[name], dtype=float),
                        np.asarray(arrays[f"{base}__difficulty"], dtype=float),
                        np.asarray(arrays[f"{base}__score"], dtype=float),
                        np.asarray(arrays[f"{base}__support"], dtype=float),
                    )
                    expected = (len(surface.rating_axis), len(surface.difficulty_axis))
                    if (
                        len(surface.rating_axis) < 2
                        or len(surface.difficulty_axis) < 2
                        or surface.score_grid.shape != expected
                        or surface.support_grid.shape != expected
                    ):
                        raise ValueError(
                            "A stored score-response surface has an invalid shape."
                        )
                    surfaces.setdefault(prefix, {})[mode] = surface
        except (KeyError, OSError, ValueError) as exc:
            raise ValueError("The stored score-response model is invalid.") from exc
        full = surfaces.pop("full", {})
        crossfit: dict[int, Mapping[str, _ScoreSurface]] = {
            fold: {} for fold in fold_ids
        }
        for prefix, fold_surfaces in surfaces.items():
            if not prefix.startswith("fold_"):
                raise ValueError("The stored score-response model is invalid.")
            crossfit[int(prefix.removeprefix("fold_"))] = fold_surfaces
        return cls(full, crossfit, training_ids, peer_cohorts)

    def predict(
        self,
        player_id: str,
        mode_key: str,
        scoring_rating: float,
        estimated_difficulty: float,
        chart_id: str | None = None,
    ) -> ScoreProjectionResult:
        player_id = str(player_id)
        if chart_id is not None:
            cohort = self.peer_cohorts.get(_peer_cohort_key(mode_key, chart_id))
            if cohort is not None:
                peer_prediction = cohort.predict(
                    public_player_key(player_id), float(scoring_rating)
                )
                if peer_prediction is not None:
                    score, support, source = peer_prediction
                    confidence = (
                        "high"
                        if support >= 20
                        else "medium"
                        if support >= 10
                        else "low"
                        if support >= PEER_SCORE_MIN_USABLE_SUPPORT
                        else "limited"
                    )
                    return ScoreProjectionResult(
                        int(round(min(MAX_RAW_SCORE, max(0.0, score)))),
                        source,
                        support,
                        confidence,
                    )
        if player_id in self.training_player_ids:
            fold = _score_response_fold(player_id)
            surface = self.crossfit_surfaces.get(fold, {}).get(mode_key)
            source = "population-crossfit"
        else:
            surface = self.full_surfaces.get(mode_key)
            source = "population-full"
        if surface is None:
            return ScoreProjectionResult(None, source, 0, "unavailable")
        prediction = surface.predict(float(scoring_rating), float(estimated_difficulty))
        if prediction is None:
            return ScoreProjectionResult(None, source, 0, "unavailable")
        score, support = prediction
        confidence = "high" if support >= 200 else "medium" if support >= 50 else "low"
        return ScoreProjectionResult(
            int(round(min(MAX_RAW_SCORE, max(0.0, score)))),
            source,
            support,
            confidence,
        )


def _score_response_fold(player_id: object) -> int:
    digest = hashlib.sha256(str(player_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % SCORE_RESPONSE_FOLDS


def public_player_key(player_id: object) -> str:
    value = f"{PLAYER_KEY_NAMESPACE}:{player_id}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:20]


def _mode_key(chart_type: str) -> str:
    return "singles" if chart_type == "Single" else "doubles"


def _folder(chart_type: str, level: int) -> str:
    return f"{'S' if chart_type == 'Single' else 'D'}{level}"


COMBINED_MIX = {
    "key": "combined",
    "apiValue": "Phoenix+Phoenix2",
    "label": "Phoenix 1 + 2",
}


def _clean_snapshot_frames(
    snapshot: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    charts = pd.DataFrame(snapshot.get("charts", []))
    scores = pd.DataFrame(snapshot.get("scores", []))
    required_charts = {"id", "songName", "type", "level"}
    required_scores = {"playerId", "chartId", "pumbility", "isBroken"}
    if charts.empty or not required_charts.issubset(charts.columns):
        raise ValueError("A recommendation snapshot has an invalid chart catalog.")
    if scores.empty or not required_scores.issubset(scores.columns):
        raise ValueError("A recommendation snapshot has no usable score rows.")

    charts = charts.copy().rename(columns={"id": "chartId"})
    charts["chartId"] = charts["chartId"].astype(str)
    charts["level"] = pd.to_numeric(charts["level"], errors="coerce")
    charts = charts[
        charts["type"].isin(MODE_TYPES)
        & charts["level"].notna()
        & (charts["level"] > 0)
    ].copy()
    charts["level"] = charts["level"].astype(int)
    charts = charts.sort_values("chartId", kind="mergesort").drop_duplicates(
        "chartId", keep="last"
    )

    scores = scores.copy()
    scores["playerId"] = scores["playerId"].astype(str)
    scores["chartId"] = scores["chartId"].astype(str)
    scores["pumbility"] = pd.to_numeric(scores["pumbility"], errors="coerce")
    if "score" not in scores.columns:
        scores["score"] = np.nan
    scores["score"] = pd.to_numeric(scores["score"], errors="coerce")
    if "recordedAt" not in scores.columns:
        scores["recordedAt"] = ""
    scores["isBroken"] = scores["isBroken"].fillna(False).astype(bool)
    scores = scores[
        scores["pumbility"].notna()
        & (scores["pumbility"] > 0)
        & (~scores["isBroken"])
    ].copy()
    scores = scores.sort_values(
        ["playerId", "chartId", "pumbility", "score", "recordedAt"],
        ascending=[True, True, False, False, False],
        kind="mergesort",
    ).drop_duplicates(["playerId", "chartId"], keep="first")
    return charts, scores


def _apply_phoenix1_score_overrides(scores: pd.DataFrame) -> pd.DataFrame:
    """Convert the exceptional Phoenix 1 chart score and its Pumbility band."""
    result = scores.copy()
    original_scores = result["score"].copy()
    result["score"] = [
        convert_phoenix1_score(chart_id, raw_score)
        for chart_id, raw_score in zip(
            result["chartId"], original_scores, strict=True
        )
    ]
    result["pumbility"] = [
        convert_phoenix1_pumbility(chart_id, raw_score, pumbility)
        for chart_id, raw_score, pumbility in zip(
            result["chartId"],
            original_scores,
            result["pumbility"],
            strict=True,
        )
    ]
    return result


def retain_catalog_source_rows(
    charts: pd.DataFrame,
    scores: pd.DataFrame,
    allowed_chart_ids: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply the Phoenix 2 catalog allowlist before any source calculation."""
    retained_charts = charts[charts["chartId"].astype(str).isin(allowed_chart_ids)].copy()
    retained_ids = set(retained_charts["chartId"].astype(str))
    retained_scores = scores[scores["chartId"].astype(str).isin(retained_ids)].copy()
    return retained_charts, retained_scores


def rebase_source_rows_to_catalog(
    charts: pd.DataFrame,
    scores: pd.DataFrame,
    authoritative_catalog: pd.DataFrame,
    pumbility_per_level: Mapping[str, float],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply source scores to the authoritative catalog's type and level."""
    source_metadata = charts[["chartId", "type", "level"]].rename(
        columns={"type": "sourceType", "level": "sourceLevel"}
    )
    target_metadata = authoritative_catalog[["chartId", "type", "level"]].rename(
        columns={"type": "targetType", "level": "targetLevel"}
    )
    mapping = source_metadata.merge(
        target_metadata,
        on="chartId",
        how="inner",
        validate="one_to_one",
    )
    mapping["pumbilityPerLevel"] = mapping["sourceType"].map(pumbility_per_level)
    mapping = mapping[
        mapping["pumbilityPerLevel"].notna()
        & mapping["targetType"].isin(MODE_TYPES)
    ].copy()
    if mapping.empty:
        return target_metadata.iloc[0:0].rename(
            columns={"targetType": "type", "targetLevel": "level"}
        ), scores.iloc[0:0].copy()

    mapping["levelDelta"] = mapping["targetLevel"] - mapping["sourceLevel"]
    rebased_scores = scores.merge(
        mapping[["chartId", "levelDelta", "pumbilityPerLevel"]],
        on="chartId",
        how="inner",
        validate="many_to_one",
    )
    rebased_scores["pumbility"] = (
        rebased_scores["pumbility"]
        + rebased_scores["levelDelta"] * rebased_scores["pumbilityPerLevel"]
    )
    rebased_scores = rebased_scores[rebased_scores["pumbility"] > 0].drop(
        columns=["levelDelta", "pumbilityPerLevel"]
    )
    retained_ids = set(mapping["chartId"].astype(str))
    rebased_charts = authoritative_catalog[
        authoritative_catalog["chartId"].astype(str).isin(retained_ids)
    ].copy()
    return rebased_charts, rebased_scores


def _source_level_slopes(
    charts: pd.DataFrame,
    scores: pd.DataFrame,
) -> dict[str, float]:
    merged = scores.merge(
        charts[["chartId", "type", "level"]],
        on="chartId",
        how="inner",
        validate="many_to_one",
    )
    slopes: dict[str, float] = {}
    for chart_type in MODE_TYPES:
        mode = merged[merged["type"] == chart_type]
        if mode.empty:
            continue
        try:
            slope, _ = _fit_level_calibration(mode)
        except ValueError:
            continue
        slopes[chart_type] = slope
    return slopes


def _source_contributions(
    snapshot: Mapping[str, Any],
    source: str,
    *,
    contribution_fraction: float = 0.20,
    allowed_chart_ids: set[str] | None = None,
    authoritative_catalog: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    charts, scores = _clean_snapshot_frames(snapshot)
    if source == "phoenix1":
        scores = _apply_phoenix1_score_overrides(scores)
    if authoritative_catalog is not None:
        authoritative_ids = set(authoritative_catalog["chartId"].astype(str))
        charts, scores = retain_catalog_source_rows(charts, scores, authoritative_ids)
        source_slopes = _source_level_slopes(charts, scores)
        charts, scores = rebase_source_rows_to_catalog(
            charts,
            scores,
            authoritative_catalog,
            source_slopes,
        )
    if allowed_chart_ids is not None:
        charts, scores = retain_catalog_source_rows(charts, scores, allowed_chart_ids)
    if charts.empty or scores.empty:
        columns = [
            "playerId",
            "chartId",
            "mode",
            "source",
            "normalizedResidual",
            "chartType",
            "chartLevel",
            "sourceSlope",
            "score",
            "plate",
        ]
        return pd.DataFrame(columns=columns), {}
    merged = scores.merge(
        charts[["chartId", "type", "level"]],
        on="chartId",
        how="inner",
        validate="many_to_one",
    )
    frames: list[pd.DataFrame] = []
    slopes: dict[str, float] = {}
    for chart_type in MODE_TYPES:
        mode = merged[merged["type"] == chart_type].copy()
        if mode.empty:
            continue
        mode = mode.sort_values(
            ["playerId", "pumbility", "score", "chartId"],
            ascending=[True, False, False, True],
            kind="mergesort",
        )
        mode["playerRank"] = mode.groupby("playerId", sort=False).cumcount() + 1
        counts = mode.groupby("playerId", sort=False).size()
        baseline_rows = mode[
            mode["playerRank"].between(BASELINE_START_RANK, BASELINE_END_RANK)
        ]
        baselines = baseline_rows.groupby("playerId", sort=False)["pumbility"].agg(
            baselinePumbility="mean", baselineCount="count"
        )
        baselines["validScoreCount"] = baselines.index.map(counts)
        baselines = baselines[
            (baselines["baselineCount"] == BASELINE_END_RANK - BASELINE_START_RANK + 1)
            & (baselines["validScoreCount"] >= BASELINE_END_RANK)
            & (baselines["baselinePumbility"] > 0)
        ]
        if baselines.empty:
            continue

        try:
            slope, _ = _fit_level_calibration(mode)
        except ValueError:
            continue
        slopes[_mode_key(chart_type)] = slope

        eligible = mode[mode["playerId"].isin(baselines.index)].copy()
        eligible["validScoreCount"] = eligible["playerId"].map(counts).astype(int)
        eligible["rankLimit"] = np.ceil(
            eligible["validScoreCount"] * contribution_fraction
        ).astype(int)
        eligible["recordedAtTimestamp"] = pd.to_datetime(
            eligible["recordedAt"], errors="coerce", utc=True
        )
        eligible["recencyRank"] = pd.Series(pd.NA, index=eligible.index, dtype="Int64")
        dated = eligible[eligible["recordedAtTimestamp"].notna()].sort_values(
            ["playerId", "recordedAtTimestamp", "pumbility", "score", "chartId"],
            ascending=[True, False, False, False, True],
            kind="mergesort",
        )
        eligible.loc[dated.index, "recencyRank"] = pd.array(
            dated.groupby("playerId", sort=False).cumcount() + 1, dtype="Int64"
        )
        window = (
            (eligible["playerRank"] <= eligible["rankLimit"])
            | (
                eligible["recencyRank"].notna()
                & (eligible["recencyRank"] <= eligible["rankLimit"])
            )
        )
        union_count = window.groupby(eligible["playerId"], sort=False).transform("sum")
        selected = np.where(
            union_count < 100,
            eligible["playerRank"] <= 100,
            window,
        )
        contribution = eligible[selected].copy()
        contribution["baselinePumbility"] = contribution["playerId"].map(
            baselines["baselinePumbility"]
        )
        contribution["normalizedResidual"] = (
            contribution["pumbility"] - contribution["baselinePumbility"]
        ) / slope
        contribution["mode"] = MODE_LABELS[chart_type]
        contribution["source"] = source
        contribution["chartType"] = chart_type
        contribution["chartLevel"] = contribution["level"].astype(int)
        contribution["sourceSlope"] = slope
        if "plate" not in contribution.columns:
            contribution["plate"] = None
        frames.append(
            contribution[
                [
                    "playerId",
                    "chartId",
                    "mode",
                    "source",
                    "normalizedResidual",
                    "chartType",
                    "chartLevel",
                    "sourceSlope",
                    "score",
                    "plate",
                ]
            ]
        )
    columns = [
        "playerId",
        "chartId",
        "mode",
        "source",
        "normalizedResidual",
        "chartType",
        "chartLevel",
        "sourceSlope",
        "score",
        "plate",
    ]
    return (
        pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns),
        slopes,
    )


def _attach_contribution_weights(
    contributions: pd.DataFrame,
    phoenix1_snapshot: Mapping[str, Any],
    phoenix2_scores: pd.DataFrame,
    phoenix2_catalog: pd.DataFrame,
) -> pd.DataFrame:
    """Attach source and leave-one-chart-out ability weights to observations."""
    weighted = contributions.copy()
    if weighted.empty:
        weighted["playerAbility"] = pd.Series(dtype=float)
        weighted["sourceWeight"] = pd.Series(dtype=float)
        weighted["abilityWeight"] = pd.Series(dtype=float)
        weighted["observationWeight"] = pd.Series(dtype=float)
        return weighted

    _, phoenix1_rating_scores = _prepare_phoenix1_rating_frames(
        phoenix1_snapshot, phoenix2_catalog
    )

    def attach_mode(scores: pd.DataFrame) -> pd.DataFrame:
        return scores.merge(
            phoenix2_catalog[["chartId", "type"]],
            on="chartId",
            how="inner",
            validate="many_to_one",
        )

    source_frames = {
        "phoenix1": attach_mode(phoenix1_rating_scores),
        "phoenix2": attach_mode(phoenix2_scores),
    }
    lookups: dict[
        tuple[str, str, str], tuple[float | None, dict[str, float | None]]
    ] = {}
    for source, frame in source_frames.items():
        for (player_id, chart_type), group in frame.groupby(
            ["playerId", "type"], sort=False
        ):
            lookups[(source, str(player_id), str(chart_type))] = _rating_lookup(
                group, str(chart_type)
            )

    abilities: list[float | None] = []
    for row in weighted[
        ["source", "playerId", "chartType", "chartId"]
    ].itertuples(index=False):
        full, leave_one_out = lookups.get(
            (str(row.source), str(row.playerId), str(row.chartType)),
            (None, {}),
        )
        abilities.append(leave_one_out.get(str(row.chartId), full))
    weighted["playerAbility"] = pd.to_numeric(
        pd.Series(abilities, index=weighted.index), errors="coerce"
    )
    weighted["sourceWeight"] = weighted["source"].map(SOURCE_WEIGHTS).fillna(1.0)
    weighted["abilityWeight"] = np.where(
        weighted["playerAbility"].notna()
        & (
            (
                weighted["playerAbility"]
                - (weighted["chartLevel"].astype(float) + 0.5)
            ).abs()
            > ABILITY_FULL_WEIGHT_RADIUS
        ),
        ABILITY_OUTSIDE_WEIGHT,
        1.0,
    )
    weighted["observationWeight"] = (
        weighted["sourceWeight"] * weighted["abilityWeight"]
    )
    return weighted


def _weighted_residual_statistics(
    values: np.ndarray,
    weights: np.ndarray,
) -> dict[str, float]:
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    values = values[valid]
    weights = weights[valid]
    if not len(values):
        return {
            "mean": math.nan,
            "location": math.nan,
            "median": math.nan,
            "std": math.nan,
            "ciLow": math.nan,
            "ciHigh": math.nan,
            "effectiveSupport": 0.0,
        }
    mean = float(np.average(values, weights=weights))
    location = _weighted_robust_location(values, weights)
    effective_support = _effective_sample_size(weights)
    weight_sum = float(weights.sum())
    variance_denominator = weight_sum - float(np.dot(weights, weights)) / weight_sum
    variance = (
        float(np.dot(weights, (values - mean) ** 2) / variance_denominator)
        if variance_denominator > 0
        else 0.0
    )
    std = math.sqrt(max(0.0, variance))
    margin = (
        1.96 * std / math.sqrt(effective_support)
        if effective_support > 1.0
        else 0.0
    )
    return {
        "mean": mean,
        "location": location,
        "median": _weighted_quantile(values, weights, 0.5),
        "std": std,
        "ciLow": location - margin,
        "ciHigh": location + margin,
        "effectiveSupport": effective_support,
    }


def what_if_levels(level: int) -> list[int]:
    """Return the bounded alternative official levels shown for one chart."""
    current = int(level)
    return [
        candidate
        for candidate in range(
            max(MIN_TARGET_LEVEL, current - WHAT_IF_LEVEL_RADIUS),
            current + WHAT_IF_LEVEL_RADIUS + 1,
        )
        if candidate != current
    ]


def _what_if_residual_shift(
    observation: Mapping[str, Any],
    target_level: int,
) -> float:
    """Revalue one selected contribution while leaving its baseline frozen."""
    current_level = int(observation["chartLevel"])
    level_delta = float(target_level - current_level)
    if observation.get("source") != "phoenix2":
        return level_delta

    grade = grade_for_score(observation.get("score"))
    plate = normalize_plate(observation.get("plate"))
    slope = float(observation.get("sourceSlope") or math.nan)
    chart_type = str(observation.get("chartType") or "")
    if (
        grade is None
        or plate is None
        or chart_type not in MODE_TYPES
        or not math.isfinite(slope)
        or slope <= 0
    ):
        # The contribution is already normalized into level units. Retaining the
        # empirical one-level shift keeps the selected evidence set intact when
        # a historical Phoenix 2 row lacks its plate.
        return level_delta

    current_pumbility = phoenix2_pumbility(
        chart_type,
        current_level,
        grade,
        plate,
    )
    target_pumbility = phoenix2_pumbility(
        chart_type,
        target_level,
        grade,
        plate,
    )
    return (target_pumbility - current_pumbility) / slope


def build_chart_what_if_estimates(
    result: pd.DataFrame,
    observations: pd.DataFrame,
) -> pd.Series:
    """Calculate chart-only estimates against frozen target-folder references."""
    folder_models: dict[int, tuple[float, float]] = {}
    for level, group in result.groupby("level", sort=False):
        reference = pd.to_numeric(
            group["levelReferenceResidualPb"], errors="coerce"
        ).dropna()
        compression = pd.to_numeric(
            group["folderRangeCompression"], errors="coerce"
        ).dropna()
        if reference.empty or compression.empty:
            continue
        reference_value = float(reference.iloc[0])
        compression_value = float(compression.iloc[0])
        if math.isfinite(reference_value) and math.isfinite(compression_value):
            folder_models[int(level)] = (reference_value, compression_value)

    observations_by_chart = {
        str(chart_id): group.to_dict(orient="records")
        for chart_id, group in observations.groupby("chartId", sort=False)
    }
    estimates: list[list[dict[str, Any]]] = []
    for row in result.to_dict(orient="records"):
        current_level = int(row["level"])
        chart_observations = observations_by_chart.get(str(row["chartId"]), [])
        frozen_reliability = float(row.get("reliabilityWeight") or math.nan)
        shrinkage_k = float(row.get("shrinkageK") or math.nan)
        chart_estimates: list[dict[str, Any]] = []
        for target_level in what_if_levels(current_level):
            estimated_difficulty: float | None = None
            folder_model = folder_models.get(target_level)
            if chart_observations and folder_model:
                hypothetical_residuals = np.asarray(
                    [
                        float(observation["normalizedResidual"])
                        + _what_if_residual_shift(observation, target_level)
                        for observation in chart_observations
                    ],
                    dtype=float,
                )
                hypothetical_weights = np.asarray(
                    [
                        _observation_weight(
                            observation.get("source"),
                            observation.get("playerAbility"),
                            target_level,
                        )
                        for observation in chart_observations
                    ],
                    dtype=float,
                )
                chart_location = _weighted_robust_location(
                    hypothetical_residuals, hypothetical_weights
                )
                effective_support = _effective_sample_size(hypothetical_weights)
                reliability = (
                    effective_support / (effective_support + shrinkage_k)
                    if math.isfinite(shrinkage_k) and shrinkage_k > 0
                    else frozen_reliability
                )
                reference, compression = folder_model
                estimate = (
                    target_level
                    + 0.5
                    - DIFFICULTY_DELTA_SCALE
                    * reliability
                    * (chart_location - reference)
                    * compression
                )
                if math.isfinite(estimate):
                    estimated_difficulty = round(float(estimate), 6)
            chart_estimates.append(
                {
                    "level": target_level,
                    "estimatedDifficulty": estimated_difficulty,
                }
            )
        estimates.append(chart_estimates)
    return pd.Series(estimates, index=result.index, dtype=object)


def merge_source_contributions(
    phoenix1: pd.DataFrame,
    phoenix2: pd.DataFrame,
    *,
    authoritative_phoenix2_keys: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Merge source observations with unconditional Phoenix 2 precedence."""
    keys = ["playerId", "chartId", "mode"]
    key_source = (
        authoritative_phoenix2_keys
        if authoritative_phoenix2_keys is not None
        else phoenix2
    )
    if key_source.empty:
        return pd.concat([phoenix1, phoenix2], ignore_index=True)
    phoenix2_keys = pd.MultiIndex.from_frame(key_source[keys])
    if phoenix1.empty:
        return phoenix2.copy()
    phoenix1_keys = pd.MultiIndex.from_frame(phoenix1[keys])
    retained_phoenix1 = phoenix1[~phoenix1_keys.isin(phoenix2_keys)].copy()
    return pd.concat([retained_phoenix1, phoenix2], ignore_index=True)


def retain_phoenix2_catalog_contributions(
    contributions: pd.DataFrame,
    phoenix2_catalog: pd.DataFrame,
) -> pd.DataFrame:
    """Remove every observation whose chart is absent from Phoenix 2 charts.json."""
    valid_ids = set(phoenix2_catalog["chartId"].astype(str))
    return contributions[contributions["chartId"].astype(str).isin(valid_ids)].copy()


def _round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def coop_master_goal_for_estimated_difficulty(
    estimated_difficulty: object,
) -> tuple[int, str, str] | None:
    """Return the fixed-plate Master-title goal for one whole Co-op difficulty."""
    if isinstance(estimated_difficulty, bool):
        return None
    try:
        value = float(estimated_difficulty)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value):
        return None
    difficulty = _round_half_up(Decimal(str(value)))
    grade = next(
        (
            candidate_grade
            for maximum_difficulty, candidate_grade in COOP_GOAL_GRADE_BANDS
            if difficulty <= maximum_difficulty
        ),
        COOP_GOAL_GRADE_BANDS[-1][1],
    )
    return COOP_GOAL_SCORE_BY_GRADE[grade], grade, COOP_GOAL_PLATE


def _coop_catalog_rows(
    phoenix2_snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return the authoritative 2x-5x Phoenix 2 Co-op catalog."""
    by_id: dict[str, dict[str, Any]] = {}
    for raw in phoenix2_snapshot.get("charts", []):
        if not isinstance(raw, Mapping) or str(raw.get("type")) != "CoOp":
            continue
        chart_id = str(raw.get("id") or "").strip()
        try:
            player_count = int(raw.get("level"))
        except (TypeError, ValueError, OverflowError):
            continue
        if not chart_id or player_count not in {2, 3, 4, 5}:
            continue
        row = dict(raw)
        row.update(
            {
                "chartId": chart_id,
                "type": "CoOp",
                "level": player_count,
                "difficulty": f"{player_count}x",
            }
        )
        row.pop("id", None)
        by_id[chart_id] = row
    return [by_id[chart_id] for chart_id in sorted(by_id)]


def _coop_snapshot_observations(
    snapshot: Mapping[str, Any],
    allowed_chart_ids: set[str],
    *,
    source: str,
) -> list[dict[str, Any]]:
    """Clean raw Co-op scores without requiring a positive Pumbility value."""
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in snapshot.get("scores", []):
        if not isinstance(raw, Mapping) or bool(raw.get("isBroken", False)):
            continue
        player_id = str(raw.get("playerId") or "").strip()
        chart_id = str(raw.get("chartId") or "").strip()
        if not player_id or chart_id not in allowed_chart_ids:
            continue
        raw_score = raw.get("score")
        if source == "phoenix1":
            raw_score = convert_phoenix1_score(chart_id, raw_score)
        if isinstance(raw_score, bool):
            continue
        try:
            score = int(float(raw_score))
        except (TypeError, ValueError, OverflowError):
            continue
        plate = normalize_plate(raw.get("plate"))
        grade = grade_for_score(score)
        if score < 0 or grade is None or plate is None:
            continue
        score = min(score, MAX_RAW_SCORE)
        row = {
            "playerId": player_id,
            "chartId": chart_id,
            "score": score,
            "grade": grade,
            "plate": plate,
            "source": source,
            "recordedAt": str(raw.get("recordedAt") or ""),
        }
        key = (player_id, chart_id)
        current = by_key.get(key)
        priority = (
            score,
            phoenix2_coop_rating(grade, plate),
            row["recordedAt"],
        )
        current_priority = (
            (
                int(current["score"]),
                phoenix2_coop_rating(
                    str(current["grade"]), str(current["plate"])
                ),
                str(current["recordedAt"]),
            )
            if current is not None
            else None
        )
        if current_priority is None or priority > current_priority:
            by_key[key] = row
    return [by_key[key] for key in sorted(by_key)]


def build_coop_observations(
    phoenix1_snapshot: Mapping[str, Any] | None,
    phoenix2_snapshot: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return the P2 Co-op catalog and merged raw scores with P2 precedence."""
    catalog = _coop_catalog_rows(phoenix2_snapshot)
    allowed_ids = {str(row["chartId"]) for row in catalog}
    phoenix1 = _coop_snapshot_observations(
        phoenix1_snapshot or {}, allowed_ids, source="phoenix1"
    )
    phoenix2 = _coop_snapshot_observations(
        phoenix2_snapshot, allowed_ids, source="phoenix2"
    )
    phoenix2_keys = {
        (str(row["playerId"]), str(row["chartId"])) for row in phoenix2
    }
    merged = [
        row
        for row in phoenix1
        if (str(row["playerId"]), str(row["chartId"])) not in phoenix2_keys
    ]
    merged.extend(phoenix2)
    merged.sort(
        key=lambda row: (
            str(row["chartId"]),
            str(row["playerId"]),
            str(row["source"]),
        )
    )
    return catalog, merged


def _coop_player_ability_percentiles(
    snapshot: Mapping[str, Any] | None,
    *,
    source: str,
) -> dict[str, float]:
    """Return source-local 0..1 ability percentiles from top-20 S/D PBs."""
    if not snapshot:
        return {}
    try:
        charts, scores = _clean_snapshot_frames(snapshot)
    except ValueError:
        return {}
    if source == "phoenix1":
        scores = _apply_phoenix1_score_overrides(scores)
    scored = scores.merge(
        charts[["chartId", "type"]],
        on="chartId",
        how="inner",
        validate="many_to_one",
    )
    if scored.empty:
        return {}
    scored = scored.sort_values(
        ["playerId", "pumbility", "score", "chartId"],
        ascending=[True, False, False, True],
        kind="mergesort",
    )
    scored["abilityRank"] = scored.groupby("playerId", sort=False).cumcount() + 1
    top = scored[scored["abilityRank"] <= COOP_ABILITY_SCORE_COUNT]
    counts = top.groupby("playerId", sort=False).size()
    complete_players = counts[counts == COOP_ABILITY_SCORE_COUNT].index
    means = (
        top[top["playerId"].isin(complete_players)]
        .groupby("playerId", sort=False)["pumbility"]
        .mean()
    )
    if means.empty:
        return {}
    if len(means) == 1:
        return {str(means.index[0]): COOP_DIFFICULTY_REFERENCE_PERCENTILE}
    average_ranks = means.rank(method="average", ascending=True)
    denominator = float(len(means) - 1)
    return {
        str(player_id): float((average_ranks.loc[player_id] - 1.0) / denominator)
        for player_id in sorted(means.index.astype(str))
    }


def _coop_adjusted_difficulty_signals(
    observations: Sequence[Mapping[str, Any]],
    phoenix1_snapshot: Mapping[str, Any] | None,
    phoenix2_snapshot: Mapping[str, Any],
) -> tuple[dict[str, float], dict[str, int], dict[str, Any]]:
    """Estimate conditional chart q75s at median ability in Phoenix 2.

    The response is log miss-points so scores near the one-million ceiling retain
    useful separation.  Player ability and source effects are estimated after
    removing chart means.  The final chart signal is q25 adjusted log-miss,
    which supplies outlier resistance after adjustment and corresponds to q75
    score because the transformation reverses score ordering.  Raw chart scores
    are never trimmed.
    """
    chart_ids = sorted({str(row["chartId"]) for row in observations})
    if not chart_ids:
        return {}, {}, {
            "abilityCoverageObservations": 0,
            "abilitySameSourceObservations": 0,
            "abilityOppositeSourceObservations": 0,
            "abilityMedianFallbackObservations": 0,
            "difficultyFitObservations": 0,
            "difficultyResidualRefitIterations": 0,
            "abilityCoefficients": [0.0, 0.0, 0.0],
            "phoenix2SourceCoefficient": 0.0,
        }

    abilities = {
        "phoenix1": _coop_player_ability_percentiles(
            phoenix1_snapshot, source="phoenix1"
        ),
        "phoenix2": _coop_player_ability_percentiles(
            phoenix2_snapshot, source="phoenix2"
        ),
    }
    chart_index = {chart_id: index for index, chart_id in enumerate(chart_ids)}
    response: list[float] = []
    covariates: list[list[float]] = []
    groups: list[int] = []
    ability_sources: list[str] = []
    for row in observations:
        source = str(row["source"])
        player_id = str(row["playerId"])
        ability = abilities.get(source, {}).get(player_id)
        ability_source = "same-source"
        if ability is None or not math.isfinite(float(ability)):
            fallback_source = (
                "phoenix2" if source == "phoenix1" else "phoenix1"
            )
            ability = abilities.get(fallback_source, {}).get(player_id)
            ability_source = "opposite-source"
        has_ability = ability is not None and math.isfinite(float(ability))
        if not has_ability:
            ability_source = "median"
        centered = (
            float(ability) - COOP_DIFFICULTY_REFERENCE_PERCENTILE
            if has_ability
            else 0.0
        )
        score = min(MAX_RAW_SCORE, max(0, int(row["score"])))
        response.append(math.log1p(MAX_RAW_SCORE - score))
        covariates.append(
            [
                centered,
                centered * centered,
                centered * centered * centered,
                1.0 if source == "phoenix2" else 0.0,
            ]
        )
        groups.append(chart_index[str(row["chartId"])])
        ability_sources.append(ability_source)

    y = np.asarray(response, dtype=float)
    x = np.asarray(covariates, dtype=float)
    group_index = np.asarray(groups, dtype=int)
    def fit_within_chart() -> np.ndarray:
        centered_x: list[np.ndarray] = []
        centered_y: list[np.ndarray] = []
        for group in range(len(chart_ids)):
            selected = group_index == group
            if not np.any(selected):
                continue
            group_x = x[selected]
            group_y = y[selected]
            centered_x.append(group_x - group_x.mean(axis=0))
            centered_y.append(group_y - group_y.mean())
        if not centered_x:
            return np.zeros(x.shape[1], dtype=float)
        design = np.concatenate(centered_x, axis=0)
        target = np.concatenate(centered_y, axis=0)
        if not np.any(np.abs(design) > 0):
            return np.zeros(x.shape[1], dtype=float)
        coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
        return np.asarray(coefficients, dtype=float)

    coefficients = fit_within_chart()

    # Evaluate every original row at median ability and in Phoenix 2.  The
    # reference-source term is common to all charts but makes the signal's
    # interpretation explicit and stable as source coverage changes.
    reference_covariates = np.asarray([0.0, 0.0, 0.0, 1.0], dtype=float)
    adjusted_log_miss = y - x @ coefficients + float(
        reference_covariates @ coefficients
    )
    signals: dict[str, float] = {}
    model_support: dict[str, int] = {}
    for chart_id, group in chart_index.items():
        selected = group_index == group
        signals[chart_id] = float(
            np.quantile(adjusted_log_miss[selected], 0.25, method="linear")
        )
        model_support[chart_id] = int(np.count_nonzero(selected))

    ability_source_counts = Counter(ability_sources)
    coverage_count = int(len(y) - ability_source_counts.get("median", 0))
    return signals, model_support, {
        "abilityCoverageObservations": coverage_count,
        "abilitySameSourceObservations": int(
            ability_source_counts.get("same-source", 0)
        ),
        "abilityOppositeSourceObservations": int(
            ability_source_counts.get("opposite-source", 0)
        ),
        "abilityMedianFallbackObservations": int(
            ability_source_counts.get("median", 0)
        ),
        "difficultyFitObservations": int(len(y)),
        "difficultyResidualRefitIterations": 0,
        "abilityCoefficients": [round(float(value), 9) for value in coefficients[:3]],
        "phoenix2SourceCoefficient": round(float(coefficients[3]), 9),
    }


def _coop_continuous_estimated_difficulties(
    difficulty_signals: Mapping[str, float],
) -> dict[str, float]:
    """Piecewise-scale adjusted signals continuously through 10/17/25.

    The two middle observations form a median anchor for even-sized catalogs.
    The rest of the empirical distribution is not quantile-normalized.
    """
    finite_signals = {
        str(chart_id): float(signal)
        for chart_id, signal in difficulty_signals.items()
        if isinstance(signal, (int, float)) and math.isfinite(float(signal))
    }
    if not finite_signals:
        return {}
    ordered = sorted(finite_signals.values())
    easiest_signal = ordered[0]
    hardest_signal = ordered[-1]
    if easiest_signal == hardest_signal:
        return {
            chart_id: float(COOP_DIFFICULTY_MEDIAN)
            for chart_id in finite_signals
        }

    lower_median = ordered[(len(ordered) - 1) // 2]
    upper_median = ordered[len(ordered) // 2]

    def calibrated(signal: float) -> float:
        if signal <= lower_median:
            denominator = lower_median - easiest_signal
            continuous = (
                float(COOP_DIFFICULTY_MEDIAN)
                if denominator <= 0
                else COOP_DIFFICULTY_EASIEST
                + (COOP_DIFFICULTY_MEDIAN - COOP_DIFFICULTY_EASIEST)
                * (signal - easiest_signal)
                / denominator
            )
        elif signal <= upper_median:
            continuous = float(COOP_DIFFICULTY_MEDIAN)
        else:
            denominator = hardest_signal - upper_median
            continuous = (
                float(COOP_DIFFICULTY_MEDIAN)
                if denominator <= 0
                else COOP_DIFFICULTY_MEDIAN
                + (COOP_DIFFICULTY_HARDEST - COOP_DIFFICULTY_MEDIAN)
                * (signal - upper_median)
                / denominator
            )
        return float(
            max(
                COOP_DIFFICULTY_EASIEST,
                min(COOP_DIFFICULTY_HARDEST, continuous),
            )
        )

    return {
        chart_id: calibrated(signal)
        for chart_id, signal in finite_signals.items()
    }


def _coop_estimated_difficulties(
    difficulty_signals: Mapping[str, float],
) -> dict[str, int]:
    """Round continuous 10/17/25 calibration half up to whole tier buckets."""
    return {
        chart_id: _round_half_up(Decimal(str(continuous)))
        for chart_id, continuous in _coop_continuous_estimated_difficulties(
            difficulty_signals
        ).items()
    }


def build_coop_chart_results(
    phoenix1_snapshot: Mapping[str, Any] | None,
    phoenix2_snapshot: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build raw q75 projections and independently adjusted tier estimates."""
    catalog, observations = build_coop_observations(
        phoenix1_snapshot, phoenix2_snapshot
    )
    observations_by_chart: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        observations_by_chart.setdefault(str(observation["chartId"]), []).append(
            observation
        )

    percentile_rows: dict[str, dict[str, Any]] = {}
    for chart_id, rows in observations_by_chart.items():
        ordered = sorted(
            rows,
            key=lambda row: (
                int(row["score"]),
                phoenix2_coop_rating(str(row["grade"]), str(row["plate"])),
                str(row["playerId"]),
                str(row["source"]),
            ),
        )
        index = max(0, math.ceil(COOP_SCORE_QUANTILE * len(ordered)) - 1)
        percentile_rows[chart_id] = ordered[index]
    difficulty_signals, model_support, difficulty_metadata = (
        _coop_adjusted_difficulty_signals(
            observations,
            phoenix1_snapshot,
            phoenix2_snapshot,
        )
    )
    continuous_difficulties = _coop_continuous_estimated_difficulties(
        difficulty_signals
    )
    difficulties = {
        chart_id: _round_half_up(Decimal(str(continuous)))
        for chart_id, continuous in continuous_difficulties.items()
    }

    ranked_chart_ids = sorted(
        difficulties,
        key=lambda chart_id: (
            continuous_difficulties[chart_id],
            difficulty_signals[chart_id],
            -int(percentile_rows[chart_id]["score"]),
            chart_id,
        ),
    )
    rank_by_id = {
        chart_id: rank for rank, chart_id in enumerate(ranked_chart_ids, start=1)
    }
    output: list[dict[str, Any]] = []
    for chart in catalog:
        chart_id = str(chart["chartId"])
        rows = observations_by_chart.get(chart_id, [])
        percentile = percentile_rows.get(chart_id)
        counts = Counter(str(row["source"]) for row in rows)
        support = len(rows)
        estimated = difficulties.get(chart_id)
        evidence_status = (
            "Published"
            if support >= 10
            else "Provisional"
            if support >= 5
            else "Insufficient"
            if support > 0
            else "Unrated"
        )
        output.append(
            {
                "mode": "Co-op",
                "modeRank": rank_by_id.get(chart_id),
                "levelRank": rank_by_id.get(chart_id),
                "levelPercentile": (
                    round((rank_by_id[chart_id] - 0.5) / len(ranked_chart_ids), 6)
                    if chart_id in rank_by_id and ranked_chart_ids
                    else None
                ),
                "levelComparisonCharts": len(ranked_chart_ids),
                "folder": chart["difficulty"],
                "relativeGroupRank": None,
                "relativeGroup": None,
                "effectBandRank": None,
                "effectBand": None,
                "songName": chart.get("songName"),
                "difficulty": chart["difficulty"],
                "type": "CoOp",
                "level": int(chart["level"]),
                "chartId": chart_id,
                "imageUrl": chart.get("imageUrl"),
                "noteCount": chart.get("noteCount"),
                "stepArtist": chart.get("stepArtist"),
                "bpmMin": chart.get("bpmMin"),
                "bpmMax": chart.get("bpmMax"),
                "estimatedDifficulty": estimated,
                "difficultyModelContinuous": (
                    round(float(continuous_difficulties[chart_id]), 6)
                    if chart_id in continuous_difficulties
                    else None
                ),
                "difficultyModelSignal": (
                    round(float(difficulty_signals[chart_id]), 9)
                    if chart_id in difficulty_signals
                    else None
                ),
                "difficultyModelSupportCount": int(
                    model_support.get(chart_id, 0)
                ),
                "whatIfEstimates": None,
                "averageDifficulty": None,
                "difficultyDelta": None,
                "folderMeasuredCharts": None,
                "folderRangeCompression": None,
                "difficultyDeltaCi95Low": None,
                "difficultyDeltaCi95High": None,
                "difficultyCi95Low": None,
                "difficultyCi95High": None,
                "pumbilityPerLevel": None,
                "nContributors": support,
                "nPlayersScored": support,
                "phoenix1Contributors": int(counts.get("phoenix1", 0)),
                "phoenix2Contributors": int(counts.get("phoenix2", 0)),
                "evidenceStatus": evidence_status,
                "percentileScore": (
                    int(percentile["score"]) if percentile is not None else None
                ),
                "percentileGrade": (
                    str(percentile["grade"]) if percentile is not None else None
                ),
                "percentilePlate": (
                    str(percentile["plate"]) if percentile is not None else None
                ),
                "percentilePlateCode": (
                    PLATE_CODES[str(percentile["plate"])]
                    if percentile is not None
                    else None
                ),
                "percentileSupportCount": support,
            }
        )
    output.sort(
        key=lambda chart: (
            chart.get("estimatedDifficulty") is None,
            int(chart.get("estimatedDifficulty") or 0),
            str(chart.get("songName") or "").casefold(),
            str(chart["chartId"]),
        )
    )
    metadata = {
        "eligiblePlayers": len({str(row["playerId"]) for row in observations}),
        "phoenix1Observations": sum(
            1 for row in observations if row["source"] == "phoenix1"
        ),
        "phoenix2Observations": sum(
            1 for row in observations if row["source"] == "phoenix2"
        ),
        "catalogCharts": len(catalog),
        "measuredCharts": len(percentile_rows),
        "difficultyModel": COOP_DIFFICULTY_MODEL_NAME,
        "difficultyTransform": "log1p(1,000,000 - score)",
        "difficultyConditionalQuantile": COOP_SCORE_QUANTILE,
        "difficultyReferenceAbilityPercentile": COOP_DIFFICULTY_REFERENCE_PERCENTILE,
        "difficultyReferenceSource": "phoenix2",
        "difficultyCalibrationAnchors": {
            "easiest": COOP_DIFFICULTY_EASIEST,
            "median": COOP_DIFFICULTY_MEDIAN,
            "hardest": COOP_DIFFICULTY_HARDEST,
        },
        **difficulty_metadata,
    }
    return output, metadata


def build_combined_chart_results(
    phoenix1_snapshot: Mapping[str, Any],
    phoenix2_snapshot: Mapping[str, Any],
    *,
    bootstrap_samples: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, float], dict[str, Any]]:
    """Build Phoenix 2-catalog chart estimates from normalized two-version evidence."""
    del bootstrap_samples  # Normal-approximation intervals keep full refreshes bounded.
    phoenix2_catalog, phoenix2_scores = _clean_snapshot_frames(phoenix2_snapshot)
    phoenix2_chart_ids = set(phoenix2_catalog["chartId"].astype(str))
    phoenix1, _ = _source_contributions(
        phoenix1_snapshot,
        "phoenix1",
        authoritative_catalog=phoenix2_catalog,
    )
    phoenix2, phoenix2_slopes = _source_contributions(
        phoenix2_snapshot,
        "phoenix2",
        allowed_chart_ids=phoenix2_chart_ids,
    )
    type_by_chart = dict(zip(phoenix2_catalog["chartId"], phoenix2_catalog["type"]))
    phoenix2_score_keys = phoenix2_scores[["playerId", "chartId"]].copy()
    phoenix2_score_keys["mode"] = phoenix2_score_keys["chartId"].map(type_by_chart).map(
        MODE_LABELS
    )
    phoenix2_score_keys = phoenix2_score_keys[phoenix2_score_keys["mode"].notna()]
    combined = merge_source_contributions(
        phoenix1,
        phoenix2,
        authoritative_phoenix2_keys=phoenix2_score_keys,
    )

    catalog = phoenix2_catalog
    combined = retain_phoenix2_catalog_contributions(combined, catalog)
    combined = _attach_contribution_weights(
        combined,
        phoenix1_snapshot,
        phoenix2_scores,
        catalog,
    )
    rows: list[pd.DataFrame] = []
    config = AnalysisConfig(
        mix="phoenix2",
        min_contributors=5,
        published_contributors=10,
        bootstrap_samples=0,
    )
    mode_metadata: dict[str, dict[str, int]] = {}
    for chart_type in MODE_TYPES:
        mode_name = MODE_LABELS[chart_type]
        mode_key = _mode_key(chart_type)
        mode_catalog = catalog[catalog["type"] == chart_type].copy()
        if mode_catalog.empty:
            continue
        mode_catalog["folder"] = [
            _folder(chart_type, int(level)) for level in mode_catalog["level"]
        ]
        mode_observations = combined[combined["mode"] == mode_name]
        mode_metadata[mode_key] = {
            "eligiblePlayers": int(mode_observations["playerId"].nunique()),
            "phoenix1Observations": int(
                (mode_observations["source"] == "phoenix1").sum()
            ),
            "phoenix2Observations": int(
                (mode_observations["source"] == "phoenix2").sum()
            ),
        }
        stat_rows: list[dict[str, Any]] = []
        for chart_id, group in mode_observations.groupby("chartId", sort=False):
            values = group["normalizedResidual"].to_numpy(dtype=float)
            weights = group["observationWeight"].to_numpy(dtype=float)
            statistics = _weighted_residual_statistics(values, weights)
            counts = Counter(group["source"])
            stat_rows.append(
                {
                    "chartId": str(chart_id),
                    "nContributors": int(group["playerId"].nunique()),
                    "nPlayersScored": int(group["playerId"].nunique()),
                    "effectiveContributors": statistics["effectiveSupport"],
                    "meanResidualPb": statistics["mean"],
                    "chartResidualPb": statistics["location"],
                    "medianResidualPb": statistics["median"],
                    "residualStdPb": statistics["std"],
                    "residualCi95LowPb": statistics["ciLow"],
                    "residualCi95HighPb": statistics["ciHigh"],
                    "meanContributorBaselinePb": 0.0,
                    "phoenix1Contributors": int(counts.get("phoenix1", 0)),
                    "phoenix2Contributors": int(counts.get("phoenix2", 0)),
                }
            )
        stats = pd.DataFrame(stat_rows)
        if stats.empty:
            stats = pd.DataFrame(
                columns=[
                    "chartId",
                    "nContributors",
                    "nPlayersScored",
                    "effectiveContributors",
                    "meanResidualPb",
                    "chartResidualPb",
                    "medianResidualPb",
                    "residualStdPb",
                    "residualCi95LowPb",
                    "residualCi95HighPb",
                    "meanContributorBaselinePb",
                    "phoenix1Contributors",
                    "phoenix2Contributors",
                ]
            )
        result = mode_catalog.merge(stats, on="chartId", how="left")
        result["mode"] = mode_name
        result["effectiveContributors"] = result["effectiveContributors"].fillna(0.0)
        for column in (
            "nContributors",
            "nPlayersScored",
            "phoenix1Contributors",
            "phoenix2Contributors",
        ):
            result[column] = result[column].fillna(0).astype(int)
        result["contributionAppearanceRate"] = np.where(
            result["nPlayersScored"] > 0,
            result["nContributors"] / result["nPlayersScored"],
            np.nan,
        )
        result["evidenceStatus"] = np.select(
            [
                result["nContributors"] >= config.published_contributors,
                result["nContributors"] >= config.min_contributors,
                result["nContributors"] > 0,
            ],
            ["Published", "Provisional", "Insufficient"],
            default="Unrated",
        )
        result = apply_within_level_difficulty(result, 1.0, config)
        result["whatIfEstimates"] = build_chart_what_if_estimates(
            result,
            mode_observations,
        )
        rows.append(result)

    if not rows:
        raise ValueError("The combined recommendation catalog had no Single or Double charts.")
    output = pd.concat(rows, ignore_index=True)
    keep = [
        "mode",
        "modeRank",
        "levelRank",
        "levelPercentile",
        "levelComparisonCharts",
        "folder",
        "relativeGroupRank",
        "relativeGroup",
        "effectBandRank",
        "effectBand",
        "songName",
        "difficulty",
        "type",
        "level",
        "chartId",
        "imageUrl",
        "noteCount",
        "stepArtist",
        "bpmMin",
        "bpmMax",
        "estimatedDifficulty",
        "whatIfEstimates",
        "averageDifficulty",
        "difficultyDelta",
        "folderMeasuredCharts",
        "folderRangeCompression",
        "difficultyDeltaCi95Low",
        "difficultyDeltaCi95High",
        "difficultyCi95Low",
        "difficultyCi95High",
        "pumbilityPerLevel",
        "nContributors",
        "nPlayersScored",
        "phoenix1Contributors",
        "phoenix2Contributors",
        "evidenceStatus",
    ]
    for column in keep:
        if column not in output.columns:
            output[column] = pd.NA
    records = json.loads(output[keep].to_json(orient="records", double_precision=6))
    coop_records, coop_metadata = build_coop_chart_results(
        phoenix1_snapshot, phoenix2_snapshot
    )
    records.extend(coop_records)
    metadata = {
        "modes": {**mode_metadata, "coop": coop_metadata},
        "sourceObservations": int(len(combined)),
        "phoenix1Observations": int((combined["source"] == "phoenix1").sum()),
        "phoenix2Observations": int((combined["source"] == "phoenix2").sum()),
        "coopSourceObservations": int(
            coop_metadata["phoenix1Observations"]
            + coop_metadata["phoenix2Observations"]
        ),
    }
    return records, phoenix2_slopes, metadata


def build_combined_tier_payload(
    combined_charts: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Any],
    *,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the public single-tier-list payload from shared chart estimates."""
    generated_at = generated_at_utc or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    records = [
        dict(chart)
        for chart in combined_charts
        if chart.get("type") == "CoOp"
        or int(chart.get("level") or 0) >= MIN_TARGET_LEVEL
    ]
    records.sort(
        key=lambda chart: (
            {"Single": 0, "Double": 1, "CoOp": 2}.get(
                str(chart.get("type")), 3
            ),
            (
                float(chart["estimatedDifficulty"])
                if chart.get("type") == "CoOp"
                and isinstance(chart.get("estimatedDifficulty"), (int, float))
                and math.isfinite(float(chart["estimatedDifficulty"]))
                else float(chart["difficultyDelta"])
                if isinstance(chart.get("difficultyDelta"), (int, float))
                and math.isfinite(float(chart["difficultyDelta"]))
                else math.inf
            ),
            str(chart.get("songName") or "").casefold(),
            str(chart.get("chartId") or ""),
        )
    )
    chart_frame = pd.DataFrame(records)
    modes: dict[str, Any] = {}
    metadata_modes = metadata.get("modes", {})
    for chart_type in MODE_TYPES:
        mode_key = _mode_key(chart_type)
        subset = chart_frame[chart_frame["type"] == chart_type].copy()
        measured = subset[subset["difficultyDelta"].notna()]
        folders: dict[str, Any] = {}
        for folder in sorted(
            subset["folder"].dropna().unique(), key=lambda value: int(str(value)[1:])
        ):
            folder_subset = subset[subset["folder"] == folder]
            contributors = folder_subset.loc[
                folder_subset["nContributors"] > 0, "nContributors"
            ]
            folders[str(folder)] = {
                "catalogCharts": int(len(folder_subset)),
                "measuredCharts": int(folder_subset["difficultyDelta"].notna().sum()),
                "publishedCharts": int(
                    (folder_subset["evidenceStatus"] == "Published").sum()
                ),
                "medianContributors": (
                    float(contributors.median()) if not contributors.empty else None
                ),
                "rangeCompression": float(
                    folder_subset["folderRangeCompression"].iloc[0]
                ),
                "overratedCharts": int(
                    (folder_subset["effectBandRank"] == 1).sum()
                ),
                "underratedCharts": int(
                    (folder_subset["effectBandRank"] == 7).sum()
                ),
            }
        mode_meta = metadata_modes.get(mode_key, {})
        modes[mode_key] = {
            "eligiblePlayers": int(mode_meta.get("eligiblePlayers", 0)),
            "catalogCharts": int(len(subset)),
            "measuredCharts": int(len(measured)),
            "publishedCharts": int((subset["evidenceStatus"] == "Published").sum()),
            "pumbilityPerLevel": 1.0 if not measured.empty else None,
            "calibration": {
                "method": "version- and mode-normalized residuals in level units",
                "slope": 1.0,
            },
            "shrinkage": {
                "method": "mode-wide empirical Bayes variance ratio",
            },
            "sources": {
                "phoenix1Observations": int(mode_meta.get("phoenix1Observations", 0)),
                "phoenix2Observations": int(mode_meta.get("phoenix2Observations", 0)),
                "weights": {"phoenix1": 1, "phoenix2": 2},
            },
            "folders": folders,
        }

    coop_subset = chart_frame[chart_frame["type"] == "CoOp"].copy()
    coop_measured = coop_subset[coop_subset["estimatedDifficulty"].notna()]
    coop_meta = metadata_modes.get("coop", {})
    coop_folders: dict[str, Any] = {}
    for folder in ("2x", "3x", "4x", "5x"):
        folder_subset = coop_subset[coop_subset["difficulty"] == folder]
        if folder_subset.empty:
            continue
        contributors = folder_subset.loc[
            folder_subset["nContributors"] > 0, "nContributors"
        ]
        coop_folders[folder] = {
            "catalogCharts": int(len(folder_subset)),
            "measuredCharts": int(folder_subset["estimatedDifficulty"].notna().sum()),
            "publishedCharts": int(
                (folder_subset["evidenceStatus"] == "Published").sum()
            ),
            "medianContributors": (
                float(contributors.median()) if not contributors.empty else None
            ),
        }
    modes["coop"] = {
        "eligiblePlayers": int(coop_meta.get("eligiblePlayers", 0)),
        "catalogCharts": int(len(coop_subset)),
        "measuredCharts": int(len(coop_measured)),
        "publishedCharts": int(
            (coop_subset["evidenceStatus"] == "Published").sum()
        ),
        "calibration": {
            "method": "conditional q75 score difficulty from player/source-adjusted log miss points, piecewise-scaled through 10/17/25 anchors",
            "quantile": COOP_SCORE_QUANTILE,
            "medianDifficulty": COOP_DIFFICULTY_MEDIAN,
            "rounding": "round half up to the nearest whole number",
        },
        "difficultyModel": {
            key: coop_meta.get(key)
            for key in (
                "difficultyModel",
                "difficultyTransform",
                "difficultyConditionalQuantile",
                "difficultyReferenceAbilityPercentile",
                "difficultyReferenceSource",
                "difficultyCalibrationAnchors",
                "abilityCoverageObservations",
                "abilitySameSourceObservations",
                "abilityOppositeSourceObservations",
                "abilityMedianFallbackObservations",
                "difficultyFitObservations",
                "difficultyResidualRefitIterations",
                "abilityCoefficients",
                "phoenix2SourceCoefficient",
            )
        },
        "sources": {
            "phoenix1Observations": int(coop_meta.get("phoenix1Observations", 0)),
            "phoenix2Observations": int(coop_meta.get("phoenix2Observations", 0)),
            "weights": {"phoenix1": 1, "phoenix2": 1},
        },
        "folders": coop_folders,
    }

    measured_count = sum(
        1
        for chart in records
        if (
            chart.get("type") == "CoOp"
            and chart.get("estimatedDifficulty") is not None
        )
        or (
            chart.get("type") != "CoOp"
            and chart.get("difficultyDelta") is not None
        )
    )
    summary = {
        "scriptVersion": f"{SCRIPT_VERSION}+combined-tier-v{COMBINED_TIER_SCHEMA_VERSION}",
        "generatedAtUtc": generated_at,
        "mix": dict(COMBINED_MIX),
        "method": {
            "catalog": "Phoenix 2 authoritative catalog",
            "overlapRule": "Phoenix 2 replaces Phoenix 1 for the same player and chart",
            "crossVersionNormalization": "version- and mode-specific Pumbility residuals converted to level units",
            "observationWeighting": {
                "sourceWeights": {"phoenix1": 1, "phoenix2": 2},
                "playerAbility": "per-mode S+FG-equivalent rating from Pumbility ranks 11-30, leave-one-chart-out",
                "fullWeightRadius": ABILITY_FULL_WEIGHT_RADIUS,
                "outsideRadiusWeight": ABILITY_OUTSIDE_WEIGHT,
                "midpoint": "official chart level + 0.5",
                "missingAbilityWeight": 1,
            },
            "levelReference": "median measured chart residual within the exact mode and Phoenix 2 official level",
            "modeSeparation": "Singles and Doubles keep independent residual models; Co-op recommendation goals use the independent whole-number tier difficulty",
            "coop": {
                "scoreProjectionModel": COOP_SCORE_PROJECTION_MODEL_NAME,
                "goalTitle": "[CO-OP] Master",
                "goalTotalRating": COOP_MASTER_TITLE_RATING,
                "goalPlate": COOP_GOAL_PLATE,
                "goalGradeBands": [
                    {"maximumDifficulty": maximum, "grade": grade}
                    for maximum, grade in COOP_GOAL_GRADE_BANDS
                ],
                "rawEvidenceQuantile": COOP_SCORE_QUANTILE,
                "rawEvidenceSelection": "nearest-rank observed score-and-plate pair retained for analysis provenance",
                "difficultyModel": COOP_DIFFICULTY_MODEL_NAME,
                "difficultyResponse": "log1p(1,000,000 - score)",
                "playerAbility": "source-specific percentile of the player's mean top-20 Single and Double Pumbility",
                "sourceAdjustment": "Phoenix 2 indicator estimated after within-chart demeaning",
                "robustFit": "the conditional quantile supplies post-adjustment outlier resistance; residual refit iterations are disabled and raw scores are not trimmed",
                "difficultyStatistic": "25th percentile adjusted log miss at median player ability and Phoenix 2 source, equivalent to conditional 75th-percentile score",
                "difficultyRange": [10, 25],
                "difficultyMedian": COOP_DIFFICULTY_MEDIAN,
                "difficultyCalibration": "piecewise linear on each side of the empirical median; no target histogram",
                "difficultyRounding": "round half up to the nearest whole number",
                "chartPool": "all current Phoenix 2 2x, 3x, 4x, and 5x Co-op charts",
                "rating": "120 - 0.8 * grade penalty + 0.16 * plate units",
                "aggregation": "sum every unique Co-op chart rating; no top-50 limit",
            },
            "difficultyDeltaScale": DIFFICULTY_DELTA_SCALE,
            "effectBands": "seven fixed absolute bands with Overrated and Underrated beyond +/-0.5",
            "folderRangeNormalization": {
                "method": "one-sided expected-normal-maximum order-statistic compression",
                "referenceMeasuredCharts": FOLDER_RANGE_REFERENCE_CHARTS,
                "formula": "min(1, expectedNormalMax(reference) / expectedNormalMax(measured charts in folder))",
                "expandsFolders": False,
            },
            "phoenix1ScoreOverrides": phoenix1_score_overrides_metadata(),
            "displayMinimumOfficialLevel": MIN_TARGET_LEVEL,
            "whatIfEstimates": {
                "calculation": "chart-only weighted contribution revaluation against frozen target-folder models",
                "levelRadius": WHAT_IF_LEVEL_RADIUS,
                "minimumOfficialLevel": MIN_TARGET_LEVEL,
                "frozen": [
                    "player baselines",
                    "contribution selection",
                    "target-folder reference and range compression",
                    "ranks and tier membership",
                ],
                "recalculated": [
                    "ability distance weights against the hypothetical midpoint",
                    "weighted robust location",
                    "effective support and reliability shrinkage",
                ],
                "missingTargetReference": "unavailable",
            },
        },
        "coverage": {
            "sourceObservations": int(metadata.get("sourceObservations", 0)),
            "phoenix1Observations": int(metadata.get("phoenix1Observations", 0)),
            "phoenix2Observations": int(metadata.get("phoenix2Observations", 0)),
            "targetCatalogCharts": len(records),
            "targetChartsMeasured": measured_count,
            "targetChartsPublished": sum(
                1 for chart in records if chart.get("evidenceStatus") == "Published"
            ),
        },
        "modes": modes,
    }
    return {
        "schemaVersion": COMBINED_TIER_SCHEMA_VERSION,
        "generatedAtUtc": generated_at,
        "mix": dict(COMBINED_MIX),
        "summary": summary,
        "singles": [chart for chart in records if chart.get("type") == "Single"],
        "doubles": [chart for chart in records if chart.get("type") == "Double"],
        "coop": [chart for chart in records if chart.get("type") == "CoOp"],
        "relativeGroups": [
            {"rank": rank, "name": name}
            for rank, name in enumerate(RELATIVE_GROUPS, start=1)
        ],
        "effectBands": [
            {"rank": rank, "name": name, "low": low, "high": high}
            for rank, name, low, high in EFFECT_BANDS
        ],
    }


def _top_total(values: Sequence[float]) -> float:
    return float(sum(sorted((value for value in values if value > 0), reverse=True)[:TOP_PUMBILITY_COUNT]))


def _top50_marginal_gain(
    candidate_pumbility: float,
    *,
    existing_pumbility: float | None,
    existing_in_top50: bool,
    current_score_count: int,
    cutoff: float | None,
) -> float:
    """Return the exact top-50 total change for one candidate outcome."""
    retained = max(existing_pumbility or 0.0, candidate_pumbility)
    if existing_in_top50:
        return max(0.0, retained - float(existing_pumbility or 0.0))
    if current_score_count < TOP_PUMBILITY_COUNT:
        return retained
    return max(0.0, retained - float(cutoff or 0.0))


def _projected_gain_sort_key(row: Mapping[str, Any]) -> tuple[float, float, float, str, str]:
    """Rank gain first, then prefer easier charts for displayed-gain ties."""
    return (
        -float(row.get("projectedGain") or 0),
        float(row["estimatedDifficulty"]),
        -float(row.get("expectedPumbility") or 0),
        str(row.get("songName") or "").casefold(),
        str(row.get("chartId") or ""),
    )


def _recommendation_chart_rows(
    combined_charts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chart in combined_charts:
        fields = (
            RECOMMENDATION_CHART_FIELDS
            if chart.get("type") == "CoOp"
            else TOP_SCORE_CHART_FIELDS
        )
        rows.append({key: chart.get(key) for key in fields})
    return rows


def _json_safe_scalar(value: Any) -> Any:
    """Return one public payload scalar with missing pandas values normalized."""
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    return value.item() if isinstance(value, np.generic) else value


def _public_top_scores(
    ordered_scores: pd.DataFrame,
    catalog_by_id: Mapping[str, Mapping[str, Any]],
    chart_analysis_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Serialize the exact retained score rows without exposing private score data."""
    output: list[dict[str, Any]] = []
    for score in ordered_scores.iloc[:TOP_PUMBILITY_COUNT].to_dict(orient="records"):
        chart_id = str(score["chartId"])
        catalog_chart = catalog_by_id.get(chart_id, {})
        analysis_chart = chart_analysis_by_id.get(chart_id, {})
        chart_type = str(
            analysis_chart.get("type") or catalog_chart.get("type") or ""
        )
        level_value = analysis_chart.get("level") or catalog_chart.get("level")
        try:
            level = int(level_value)
        except (TypeError, ValueError, OverflowError):
            level = 0
        difficulty = analysis_chart.get("difficulty") or catalog_chart.get(
            "difficulty"
        )
        if not difficulty and chart_type in MODE_TYPES and level > 0:
            difficulty = _folder(chart_type, level)
        normalized_plate = normalize_plate(score.get("plate"))

        def chart_value(field: str) -> Any:
            if field in analysis_chart:
                return _json_safe_scalar(analysis_chart.get(field))
            return _json_safe_scalar(catalog_chart.get(field))

        row = {field: chart_value(field) for field in TOP_SCORE_CHART_FIELDS}
        row.update(
            {
                "mode": chart_value("mode") or MODE_LABELS.get(chart_type),
                "songName": chart_value("songName") or "Unknown chart",
                "difficulty": _json_safe_scalar(difficulty),
                "type": chart_type,
                "level": level,
                "chartId": chart_id,
                "pumbility": float(score["pumbility"]),
                "grade": grade_for_score(score.get("score")),
                "plate": normalized_plate,
                "plateCode": (
                    PLATE_CODES[normalized_plate]
                    if normalized_plate is not None
                    else None
                ),
            }
        )
        output.append(row)
    return output


def build_manual_recommendation_mode(
    charts: Sequence[Mapping[str, Any]],
    chart_type: str,
    scoring_rating: float,
) -> dict[str, Any]:
    """Rank anonymous recommendations without inferring personal score gains."""
    filter_candidates: list[dict[str, Any]] = []
    for chart in charts:
        if chart.get("type") != chart_type:
            continue
        level = float(chart.get("level", 0))
        estimate = float(chart.get("estimatedDifficulty", 0))
        if not math.isfinite(estimate):
            continue
        filter_candidates.append(
            {
                **dict(chart),
                "distanceFromRating": round(estimate - scoring_rating, 6),
                "farmEdge": round(level + 0.5 - estimate, 6),
                "existingPumbility": None,
                "expectedPumbility": None,
                "projectedGain": None,
                "projectedScore": None,
                "scoreProjectionSource": None,
                "scoreProjectionSupportCount": None,
                "scoreProjectionConfidence": "unavailable",
                "projectedGrade": None,
                "projectedPlate": None,
                "projectedPlateCode": None,
                "projectedPlateProbability": None,
                "plateProjectionSource": None,
                "played": False,
            }
        )
    ranked_candidates = sorted(
        filter_candidates,
        key=lambda row: (
            -float(row["farmEdge"]),
            float(row["estimatedDifficulty"]),
            str(row.get("songName", "")).casefold(),
            str(row.get("chartId", "")),
        ),
    )
    default_candidates = [
        row
        for row in ranked_candidates
        if int(row.get("level") or 0) >= MIN_TARGET_LEVEL
        and float(row["estimatedDifficulty"])
        <= scoring_rating + RECOMMENDATION_RADIUS
    ]
    top_recommendations = default_candidates[:TOP_RECOMMENDATION_COUNT]
    return {
        "eligible": True,
        "manual": True,
        "validScoreCount": 0,
        "scoringRating": round(scoring_rating, 3),
        "candidateRange": [
            None,
            round(scoring_rating + RECOMMENDATION_RADIUS, 3),
        ],
        "candidateCount": len(default_candidates),
        "filterCandidateCount": len(ranked_candidates),
        "projectionAvailable": False,
        "scoreProjectionModel": None,
        "topScores": [],
        "filterCandidates": ranked_candidates,
        "topRecommendations": top_recommendations,
    }


def _skill_rating_from_rows(
    rows: pd.DataFrame,
    chart_type: str,
    *,
    start_rank: int,
    end_rank: int,
) -> float | None:
    """Convert one ordered Pumbility window to its S+FG-equivalent level."""
    if rows.empty or start_rank < 1 or end_rank < start_rank:
        return None
    window = rows.iloc[start_rank - 1 : end_rank]
    if window.empty:
        return None
    values = pd.to_numeric(window["pumbility"], errors="coerce").dropna()
    if len(values) != len(window):
        return None
    return skill_rating_for_pumbility(chart_type, float(values.mean()))


def _rating_lookup(
    rows: pd.DataFrame,
    chart_type: str,
) -> tuple[float | None, dict[str, float | None]]:
    """Return full and leave-one-out ranks 11-30 Pumbility ratings in O(n)."""
    if len(rows) < PROJECTION_RATING_SCORE_THRESHOLD:
        return None, {}
    ordered = rows.sort_values(
        ["pumbility", "score", "chartId"],
        ascending=[False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    values = ordered["pumbility"].astype(float).to_numpy()
    prefix = np.concatenate(([0.0], np.cumsum(values)))
    start = PROJECTION_RATING_START_RANK - 1
    stop = PROJECTION_RATING_END_RANK

    def window_rating(count: int, removed: int | None = None) -> float | None:
        end = min(stop, count)
        expected_window_size = (
            PROJECTION_RATING_END_RANK - PROJECTION_RATING_START_RANK + 1
        )
        if end - start != expected_window_size:
            return None
        if removed is None:
            total = prefix[end] - prefix[start]
        else:
            if removed < start:
                total = prefix[end + 1] - prefix[start + 1]
            elif removed >= end:
                total = prefix[end] - prefix[start]
            else:
                total = prefix[end + 1] - prefix[start] - values[removed]
        return skill_rating_for_pumbility(
            chart_type, float(total / (end - start))
        )

    full = window_rating(len(ordered))
    leave_one_out = {
        str(chart_id): window_rating(len(ordered) - 1, index)
        for index, chart_id in enumerate(ordered["chartId"])
    }
    return full, leave_one_out


def _select_rating_scores(
    phoenix1_scores: pd.DataFrame,
    phoenix2_scores: pd.DataFrame,
) -> tuple[str, pd.DataFrame]:
    """Choose one mode's recommendation-rating source consistently."""
    if len(phoenix2_scores) >= PHOENIX2_RATING_SCORE_THRESHOLD:
        return "phoenix2", phoenix2_scores
    if len(phoenix1_scores) >= RECOMMENDATION_RATING_SCORE_COUNT:
        return "phoenix1", phoenix1_scores
    if not phoenix2_scores.empty:
        return "phoenix2", phoenix2_scores
    return "phoenix1", phoenix1_scores.iloc[0:0].copy()


def _select_projection_rating_scores(
    phoenix1_scores: pd.DataFrame,
    phoenix2_scores: pd.DataFrame,
) -> tuple[str | None, pd.DataFrame]:
    """Choose a complete ranks 11-30 source for score projection matching."""
    if len(phoenix2_scores) >= PROJECTION_RATING_SCORE_THRESHOLD:
        return "phoenix2", phoenix2_scores
    if len(phoenix1_scores) >= PROJECTION_RATING_SCORE_THRESHOLD:
        return "phoenix1", phoenix1_scores
    return None, phoenix2_scores.iloc[0:0].copy()


def _smooth_grid(values: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    result = np.apply_along_axis(
        lambda row: np.convolve(row, kernel, mode="same"), 1, values
    )
    return np.apply_along_axis(
        lambda column: np.convolve(column, kernel, mode="same"), 0, result
    )


def _weighted_isotonic(
    values: np.ndarray,
    weights: np.ndarray,
    *,
    increasing: bool,
) -> np.ndarray:
    """Pool adjacent violations without letting one noisy cell flatten an axis."""
    target = values.astype(float).copy()
    if not increasing:
        target = -target
    block_values: list[float] = []
    block_weights: list[float] = []
    block_starts: list[int] = []
    block_ends: list[int] = []
    for index, (value, weight) in enumerate(zip(target, weights, strict=True)):
        block_values.append(float(value))
        block_weights.append(max(float(weight), 1e-6))
        block_starts.append(index)
        block_ends.append(index + 1)
        while len(block_values) >= 2 and block_values[-2] > block_values[-1]:
            combined_weight = block_weights[-2] + block_weights[-1]
            combined_value = (
                block_values[-2] * block_weights[-2]
                + block_values[-1] * block_weights[-1]
            ) / combined_weight
            block_values[-2:] = [combined_value]
            block_weights[-2:] = [combined_weight]
            block_ends[-2:] = [block_ends[-1]]
            block_starts.pop()
    result = np.empty_like(target)
    for value, start, end in zip(
        block_values, block_starts, block_ends, strict=True
    ):
        result[start:end] = value
    return result if increasing else -result


def _project_monotone_score_grid(
    values: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """Alternately project onto rating-up and difficulty-down constraints."""
    result = values.astype(float).copy()
    stable_weights = np.maximum(weights.astype(float), 1e-6)
    for _ in range(12):
        previous = result.copy()
        for column in range(result.shape[1]):
            result[:, column] = _weighted_isotonic(
                result[:, column],
                stable_weights[:, column],
                increasing=True,
            )
        for row in range(result.shape[0]):
            result[row, :] = _weighted_isotonic(
                result[row, :],
                stable_weights[row, :],
                increasing=False,
            )
        if float(np.max(np.abs(result - previous))) < 0.01:
            break
    # Remove only floating-point remnants after weighted pooling.
    result = np.maximum.accumulate(result, axis=0)
    result = np.minimum.accumulate(result, axis=1)
    return np.clip(result, 0, MAX_RAW_SCORE)


def _calibrate_score_rows(rows: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Calibrate P1 to P2 without using rows outside the supplied train split."""
    calibrated = rows.copy()
    if calibrated.empty:
        calibrated["calibratedScore"] = pd.Series(dtype=float)
        return calibrated, 0.0
    centered_x = calibrated["estimatedDifficulty"] - calibrated.groupby(
        ["source", "playerId"], sort=False
    )["estimatedDifficulty"].transform("mean")
    centered_y = calibrated["score"] - calibrated.groupby(
        ["source", "playerId"], sort=False
    )["score"].transform("mean")
    denominator = float(np.dot(centered_x, centered_x))
    slope = (
        -float(np.dot(centered_x, centered_y) / denominator)
        if denominator > 0
        else 0.0
    )
    grouped = calibrated.groupby(["playerId", "source"], sort=False).agg(
        score=("score", "mean"), difficulty=("estimatedDifficulty", "mean")
    )
    grouped["intercept"] = grouped["score"] + max(0.0, slope) * grouped["difficulty"]
    pivot = grouped["intercept"].unstack("source")
    if {"phoenix1", "phoenix2"}.issubset(pivot.columns):
        dual = pivot.dropna(subset=["phoenix1", "phoenix2"])
        offset = (
            float((dual["phoenix2"] - dual["phoenix1"]).median())
            if not dual.empty
            else 0.0
        )
    else:
        offset = 0.0
    if not math.isfinite(offset):
        offset = 0.0
    calibrated["calibratedScore"] = calibrated["score"].astype(float)
    mask = calibrated["source"] == "phoenix1"
    calibrated.loc[mask, "calibratedScore"] += offset
    calibrated["calibratedScore"] = calibrated["calibratedScore"].clip(
        0, MAX_RAW_SCORE
    )
    return calibrated, offset


def _phoenix2_normalized_pumbility(
    chart_type: object,
    level: object,
    calibrated_score: object,
    plate: object,
) -> float | None:
    """Return comparable Phoenix 2 Pumbility for one joined score row."""
    grade = grade_for_score(calibrated_score)
    normalized_plate = normalize_plate(plate)
    if grade is None or normalized_plate is None:
        return None
    try:
        value = phoenix2_pumbility(
            str(chart_type), int(level), grade, normalized_plate
        )
    except (TypeError, ValueError, OverflowError):
        return None
    return float(value) if math.isfinite(value) else None


def _build_score_surface(rows: pd.DataFrame) -> _ScoreSurface | None:
    if len(rows) < SCORE_RESPONSE_MIN_SUPPORT:
        return None
    rating_min = math.floor((float(rows["scoringRating"].min()) - 0.5) * 10) / 10
    rating_max = math.ceil((float(rows["scoringRating"].max()) + 0.5) * 10) / 10
    difficulty_min = math.floor((float(rows["estimatedDifficulty"].min()) - 0.5) * 10) / 10
    difficulty_max = math.ceil((float(rows["estimatedDifficulty"].max()) + 0.5) * 10) / 10
    rating_axis = np.arange(
        rating_min, rating_max + SCORE_RESPONSE_GRID_STEP / 2, SCORE_RESPONSE_GRID_STEP
    )
    difficulty_axis = np.arange(
        difficulty_min,
        difficulty_max + SCORE_RESPONSE_GRID_STEP / 2,
        SCORE_RESPONSE_GRID_STEP,
    )
    if len(rating_axis) < 2 or len(difficulty_axis) < 2:
        return None

    # Balance prolific players first, then give each retained Phoenix 2 score
    # twice the influence of a Phoenix 1 score.
    balanced = rows.copy()
    player_counts = balanced.groupby("playerId", sort=False)["chartId"].transform(
        "count"
    )
    balanced["modelWeight"] = (
        balanced["source"].map(SOURCE_WEIGHTS).fillna(1.0)
        / player_counts.clip(lower=1).astype(float)
    )

    numerator = np.zeros((len(rating_axis), len(difficulty_axis)), dtype=float)
    denominator = np.zeros_like(numerator)
    raw_count = np.zeros_like(numerator)
    rating_index = np.rint(
        (balanced["scoringRating"].to_numpy(float) - rating_axis[0])
        / SCORE_RESPONSE_GRID_STEP
    ).astype(int)
    difficulty_index = np.rint(
        (balanced["estimatedDifficulty"].to_numpy(float) - difficulty_axis[0])
        / SCORE_RESPONSE_GRID_STEP
    ).astype(int)
    rating_index = np.clip(rating_index, 0, len(rating_axis) - 1)
    difficulty_index = np.clip(difficulty_index, 0, len(difficulty_axis) - 1)
    weights = balanced["modelWeight"].to_numpy(float)
    scores = balanced["calibratedScore"].to_numpy(float)
    np.add.at(numerator, (rating_index, difficulty_index), weights * scores)
    np.add.at(denominator, (rating_index, difficulty_index), weights)
    np.add.at(raw_count, (rating_index, difficulty_index), 1.0)

    radius = min(
        SCORE_RESPONSE_SMOOTHING_RADIUS,
        (len(rating_axis) - 1) // 2,
        (len(difficulty_axis) - 1) // 2,
    )
    offsets = np.arange(-radius, radius + 1, dtype=float)
    sigma = max(1.0, radius / 2.0)
    kernel = np.exp(-0.5 * (offsets / sigma) ** 2)
    smoothed_numerator = _smooth_grid(numerator, kernel)
    smoothed_denominator = _smooth_grid(denominator, kernel)
    global_mean = float(np.average(scores, weights=weights))
    score_grid = np.divide(
        smoothed_numerator,
        smoothed_denominator,
        out=np.full_like(smoothed_numerator, global_mean),
        where=smoothed_denominator > 0,
    )
    support_radius = min(5, radius)
    support_kernel = np.ones(2 * support_radius + 1, dtype=float)
    support_grid = _smooth_grid(raw_count, support_kernel)
    score_grid = _project_monotone_score_grid(score_grid, support_grid)
    return _ScoreSurface(rating_axis, difficulty_axis, score_grid, support_grid)


def fit_score_response_model(
    phoenix1_snapshot: Mapping[str, Any] | None,
    phoenix2_snapshot: Mapping[str, Any],
    combined_charts: Sequence[Mapping[str, Any]],
) -> tuple[ScoreResponseModel, dict[str, Any]]:
    """Fit P2-calibrated, player-balanced monotone population score surfaces."""
    catalog, phoenix2_scores = _clean_snapshot_frames(phoenix2_snapshot)
    estimates = pd.DataFrame(combined_charts)
    required = ["chartId", "type", "estimatedDifficulty"]
    if estimates.empty or not set(required).issubset(estimates.columns):
        return ScoreResponseModel({}, {}, frozenset()), {"modes": {}}
    estimates = estimates[required].copy()
    estimates["chartId"] = estimates["chartId"].astype(str)
    estimates["estimatedDifficulty"] = pd.to_numeric(
        estimates["estimatedDifficulty"], errors="coerce"
    )
    estimates = estimates.sort_values("chartId", kind="mergesort").drop_duplicates(
        "chartId", keep="last"
    )

    if phoenix1_snapshot is not None:
        phoenix1_charts, phoenix1_scores = _prepare_phoenix1_rating_frames(
            phoenix1_snapshot, catalog
        )
    else:
        phoenix1_scores = phoenix2_scores.iloc[0:0].copy()

    phoenix2_keys = pd.MultiIndex.from_frame(
        phoenix2_scores[["playerId", "chartId"]]
    )
    if not phoenix1_scores.empty:
        phoenix1_keys = pd.MultiIndex.from_frame(
            phoenix1_scores[["playerId", "chartId"]]
        )
        retained_phoenix1 = phoenix1_scores[~phoenix1_keys.isin(phoenix2_keys)].copy()
    else:
        retained_phoenix1 = phoenix1_scores.copy()
    overlap_rows_removed = int(len(phoenix1_scores) - len(retained_phoenix1))

    def attach_source(scores: pd.DataFrame, source: str) -> pd.DataFrame:
        rows = scores.merge(
            catalog[["chartId", "type", "level"]],
            on="chartId",
            how="inner",
            validate="many_to_one",
        ).merge(
            estimates[["chartId", "estimatedDifficulty"]],
            on="chartId",
            how="inner",
            validate="many_to_one",
        )
        rows["source"] = source
        return rows

    merged = pd.concat(
        [
            attach_source(retained_phoenix1, "phoenix1"),
            attach_source(phoenix2_scores, "phoenix2"),
        ],
        ignore_index=True,
    )
    merged = merged[
        merged["score"].notna()
        & merged["score"].between(0, MAX_RAW_SCORE)
        & merged["estimatedDifficulty"].notna()
    ].copy()

    # Projection matching uses the S+FG-equivalent rating from Pumbility ranks
    # 11-30. For a target chart in that source, remove the chart and promote the
    # next rank when available so an outcome cannot directly set its predictor.
    def attach_rating_mode(scores: pd.DataFrame) -> pd.DataFrame:
        return scores.merge(
            catalog[["chartId", "type", "level"]],
            on="chartId",
            how="inner",
            validate="many_to_one",
        )

    rating_frames = {
        "phoenix1": attach_rating_mode(phoenix1_scores),
        "phoenix2": attach_rating_mode(phoenix2_scores),
    }
    empty_rating_rows = rating_frames["phoenix2"].iloc[0:0]
    rating_groups = {
        source: {
            (str(player_id), str(chart_type)): group
            for (player_id, chart_type), group in frame.groupby(
                ["playerId", "type"], sort=False
            )
        }
        for source, frame in rating_frames.items()
    }
    rating_lookups: dict[tuple[str, str], tuple[float | None, dict[str, float | None]]] = {}
    all_player_modes = set(rating_groups["phoenix1"]) | set(
        rating_groups["phoenix2"]
    )
    for player_id, chart_type in all_player_modes:
        key = (player_id, chart_type)
        p2_group = rating_groups["phoenix2"].get(key, empty_rating_rows)
        p1_group = rating_groups["phoenix1"].get(key, empty_rating_rows)
        _, selected = _select_projection_rating_scores(p1_group, p2_group)
        rating_lookups[(player_id, chart_type)] = _rating_lookup(
            selected, chart_type
        )

    ratings: list[float | None] = []
    for row in merged[["playerId", "type", "chartId"]].itertuples(index=False):
        full, leave_one_out = rating_lookups.get(
            (str(row.playerId), str(row.type)), (None, {})
        )
        leave_one_out_rating = leave_one_out.get(str(row.chartId), full)
        # Exactly 30 scores cannot form a complete leave-one-out ranks 11-30
        # window. Keep the established score-projection threshold by using the
        # complete full window; difficulty weighting treats this LOO as missing.
        ratings.append(full if leave_one_out_rating is None else leave_one_out_rating)
    merged["scoringRating"] = pd.to_numeric(
        np.asarray(ratings, dtype=float), errors="coerce"
    )
    merged = merged[merged["scoringRating"].notna()].copy()

    merged["fold"] = merged["playerId"].map(_score_response_fold)

    full_surfaces: dict[str, _ScoreSurface] = {}
    crossfit_surfaces: dict[int, dict[str, _ScoreSurface]] = {
        fold: {} for fold in range(SCORE_RESPONSE_FOLDS)
    }
    peer_cohorts: dict[str, _PeerScoreCohort] = {}
    mode_metadata: dict[str, Any] = {}
    for chart_type in MODE_TYPES:
        mode_key = _mode_key(chart_type)
        mode = merged[merged["type"] == chart_type].copy()
        calibrated_mode, source_offset = _calibrate_score_rows(mode)
        surface = _build_score_surface(calibrated_mode)
        if surface is not None:
            full_surfaces[mode_key] = surface
        plates = (
            calibrated_mode["plate"]
            if "plate" in calibrated_mode.columns
            else pd.Series(None, index=calibrated_mode.index, dtype=object)
        )
        calibrated_mode["normalizedPumbility"] = [
            _phoenix2_normalized_pumbility(
                chart_type_value,
                level,
                score,
                plate,
            )
            for chart_type_value, level, score, plate in zip(
                calibrated_mode["type"],
                calibrated_mode["level"],
                calibrated_mode["calibratedScore"],
                plates,
                strict=True,
            )
        ]
        peer_rows = calibrated_mode[
            calibrated_mode["normalizedPumbility"].notna()
        ].copy()
        peer_rows = peer_rows.sort_values(
            [
                "playerId",
                "normalizedPumbility",
                "calibratedScore",
                "chartId",
            ],
            ascending=[True, False, False, True],
            kind="mergesort",
        )
        peer_rows["normalizedPumbilityRank"] = (
            peer_rows.groupby("playerId", sort=False).cumcount() + 1
        )
        for chart_id, cohort_rows in peer_rows.groupby("chartId", sort=False):
            ordered = cohort_rows.sort_values(
                ["scoringRating", "playerId"], kind="mergesort"
            )
            peer_cohorts[_peer_cohort_key(mode_key, chart_id)] = _PeerScoreCohort(
                np.asarray(
                    [public_player_key(value) for value in ordered["playerId"]],
                    dtype=np.str_,
                ),
                ordered["scoringRating"].to_numpy(float),
                ordered["calibratedScore"].to_numpy(float),
                ordered["normalizedPumbilityRank"].to_numpy(np.int64),
                ordered["source"].map(SOURCE_WEIGHTS).fillna(1.0).to_numpy(float),
            )
        fold_rows: dict[str, int] = {}
        for fold in range(SCORE_RESPONSE_FOLDS):
            training = mode[mode["fold"] != fold]
            fold_rows[str(fold)] = int(len(training))
            calibrated_training, _ = _calibrate_score_rows(training)
            fold_surface = _build_score_surface(calibrated_training)
            if fold_surface is not None:
                crossfit_surfaces[fold][mode_key] = fold_surface
        mode_metadata[mode_key] = {
            "rows": int(len(mode)),
            "players": int(mode["playerId"].nunique()),
            "sourceRows": {
                source: int((mode["source"] == source).sum())
                for source in ("phoenix1", "phoenix2")
            },
            "sourcePlayers": {
                source: int(
                    mode.loc[mode["source"] == source, "playerId"].nunique()
                )
                for source in ("phoenix1", "phoenix2")
            },
            "phoenix1ToPhoenix2ScoreOffset": round(source_offset, 3),
            "crossfitTrainingRows": fold_rows,
            "peerEligibleRows": int(len(peer_rows)),
            "peerEligiblePlayers": int(peer_rows["playerId"].nunique()),
            "peerEligibleCharts": int(peer_rows["chartId"].nunique()),
        }
    model = ScoreResponseModel(
        full_surfaces,
        crossfit_surfaces,
        frozenset(merged["playerId"].astype(str).unique()),
        peer_cohorts,
    )
    return model, {
        "model": SCORE_PROJECTION_MODEL_NAME,
        "populationFallbackModel": SCORE_RESPONSE_MODEL_NAME,
        "crossfitFolds": SCORE_RESPONSE_FOLDS,
        "personalRawScoreInput": False,
        "playerBalanced": True,
        "sourceWeights": {"phoenix1": 1, "phoenix2": 2},
        "abilityDistanceWeighting": False,
        "minimumLocalSupport": SCORE_RESPONSE_MIN_SUPPORT,
        "supportNeighborhood": "plus or minus 0.5 rating and difficulty",
        "projectionRating": {
            "ranks": [PROJECTION_RATING_START_RANK, PROJECTION_RATING_END_RANK],
            "minimumSourceScores": PROJECTION_RATING_SCORE_THRESHOLD,
            "referenceGrade": SKILL_RATING_REFERENCE_GRADE,
            "referencePlate": SKILL_RATING_REFERENCE_PLATE,
            "referenceMultiplier": SKILL_RATING_REFERENCE_MULTIPLIER,
            "leaveOneOut": "remove the target chart and promote rank 31 when available",
        },
        "confidenceThresholds": {"high": 200, "medium": 50, "low": 5},
        "peerProjection": {
            "quantile": PEER_SCORE_QUANTILE,
            "minimumPeers": PEER_SCORE_MIN_USABLE_SUPPORT,
            "minimumUsablePeers": PEER_SCORE_MIN_USABLE_SUPPORT,
            "supportTargets": list(PEER_SCORE_SUPPORT_TARGETS),
            "initialRatingRadius": PEER_SCORE_INITIAL_RADIUS,
            "maximumRatingRadius": PEER_SCORE_MAX_RADIUS,
            "ratingRadiusStep": PEER_SCORE_RADIUS_STEP,
            "radiusSearch": "repeat plus or minus 0.2 through 0.5 for support targets 20, 10, then 5; use the narrowest successful radius within each pass",
            "scoreNormalization": "joined Phoenix 1 + Phoenix 2 observations recomputed with the Phoenix 2 chart catalog and grade-and-plate Pumbility formula; Phoenix 2 replaces overlaps",
            "percentileWeighting": "Phoenix 1 = 1; Phoenix 2 = 2",
            "confidenceThresholds": {
                "high": 20,
                "medium": 10,
                "low": 5,
            },
        },
        "phoenix1Calibration": "mode-specific dual-source player intercept offset to the Phoenix 2 raw-score scale",
        "phoenix2OverlapRowsRemovedFromPhoenix1": overlap_rows_removed,
        "modes": mode_metadata,
    }


def fit_score_projection_model(
    phoenix1_snapshot: Mapping[str, Any] | None,
    phoenix2_snapshot: Mapping[str, Any],
    combined_charts: Sequence[Mapping[str, Any]],
) -> tuple[ScoreResponseModel, dict[str, Any]]:
    """Compatibility name for the population score response fitter."""
    return fit_score_response_model(phoenix1_snapshot, phoenix2_snapshot, combined_charts)


def fit_score_projection_slopes(
    phoenix2_snapshot: Mapping[str, Any],
    combined_charts: Sequence[Mapping[str, Any]],
    *,
    phoenix1_snapshot: Mapping[str, Any] | None = None,
) -> ScoreResponseModel:
    """Deprecated compatibility wrapper returning the response model."""
    model, _ = fit_score_response_model(
        phoenix1_snapshot,
        phoenix2_snapshot,
        combined_charts,
    )
    return model


def _baseline_window(mode_scores: pd.DataFrame) -> tuple[pd.DataFrame, int, int, str]:
    score_count = len(mode_scores)
    if score_count >= BASELINE_END_RANK:
        start = BASELINE_START_RANK
        end = BASELINE_END_RANK
        label = "ranks 11-30"
    else:
        start = 1
        end = max(1, math.ceil(score_count * 0.5))
        label = f"best 50% ({end} of {score_count})"
    return mode_scores.iloc[start - 1 : end].copy(), start, end, label


def _rating_window(
    mode_scores: pd.DataFrame,
) -> tuple[pd.DataFrame, int, int, str]:
    """Return the shared top-score Pumbility rows used for recommendations."""
    end = min(RECOMMENDATION_RATING_SCORE_COUNT, len(mode_scores))
    label = (
        f"top {RECOMMENDATION_RATING_SCORE_COUNT} scores"
        if end == RECOMMENDATION_RATING_SCORE_COUNT
        else f"all {end} available {'score' if end == 1 else 'scores'}"
    )
    return mode_scores.iloc[:end].copy(), 1, end, label


def _prepare_phoenix1_rating_frames(
    phoenix1_snapshot: Mapping[str, Any],
    phoenix2_catalog: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    charts, scores = _clean_snapshot_frames(phoenix1_snapshot)
    allowed_ids = set(phoenix2_catalog["chartId"].astype(str))
    charts, scores = retain_catalog_source_rows(charts, scores, allowed_ids)
    scores = _apply_phoenix1_score_overrides(scores)
    retained_ids = set(charts["chartId"].astype(str))
    rating_catalog = phoenix2_catalog[
        phoenix2_catalog["chartId"].astype(str).isin(retained_ids)
    ].copy()
    rating_rows = scores.merge(
        rating_catalog[["chartId", "type", "level"]],
        on="chartId",
        how="inner",
        validate="many_to_one",
    )
    plates = (
        rating_rows["plate"]
        if "plate" in rating_rows.columns
        else pd.Series(None, index=rating_rows.index, dtype=object)
    )
    rating_rows["pumbility"] = [
        _phoenix2_normalized_pumbility(chart_type, level, score, plate)
        for chart_type, level, score, plate in zip(
            rating_rows["type"],
            rating_rows["level"],
            rating_rows["score"],
            plates,
            strict=True,
        )
    ]
    rating_rows = rating_rows[
        rating_rows["pumbility"].notna() & (rating_rows["pumbility"] > 0)
    ].drop(columns=["type", "level"])
    return rating_catalog, rating_rows


def _build_player_recommendation_phoenix2_only(
    player_id: str,
    phoenix2_snapshot: Mapping[str, Any],
    combined_charts: Sequence[Mapping[str, Any]],
    phoenix2_slopes: Mapping[str, float],
    score_response_model: ScoreResponseModel | None = None,
) -> dict[str, Any]:
    catalog, scores = _clean_snapshot_frames(phoenix2_snapshot)
    catalog_map = {str(row["chartId"]): row for row in catalog.to_dict(orient="records")}
    chart_map = {str(row["chartId"]): dict(row) for row in combined_charts}
    player_scores = scores[scores["playerId"] == str(player_id)].copy()
    modes: dict[str, Any] = {}
    for chart_type in MODE_TYPES:
        mode_key = _mode_key(chart_type)
        mode_ids = set(catalog.loc[catalog["type"] == chart_type, "chartId"])
        mode_scores = player_scores[player_scores["chartId"].isin(mode_ids)].copy()
        mode_scores = mode_scores.sort_values(
            ["pumbility", "score", "chartId"],
            ascending=[False, False, True],
            kind="mergesort",
        )
        score_count = len(mode_scores)
        if score_count == 0:
            modes[mode_key] = {
                "eligible": False,
                "validScoreCount": 0,
                "requiredScoreCount": 1,
                "reason": "At least one positive Phoenix 2 score is required in this mode.",
                "candidates": [],
                "topRecommendations": [],
            }
            continue
        slope = phoenix2_slopes.get(mode_key)
        if slope is None or not math.isfinite(float(slope)) or float(slope) <= 0:
            modes[mode_key] = {
                "eligible": False,
                "validScoreCount": int(len(mode_scores)),
                "requiredScoreCount": BASELINE_END_RANK,
                "reason": "Phoenix 2 does not yet have enough calibration data for this mode.",
                "candidates": [],
                "topRecommendations": [],
            }
            continue

        if score_count >= BASELINE_END_RANK:
            baseline_start = BASELINE_START_RANK
            baseline_end = BASELINE_END_RANK
            baseline_label = "ranks 11–30"
        else:
            baseline_start = 1
            baseline_end = max(1, math.ceil(score_count * 0.5))
            baseline_label = f"best 50% ({baseline_end} of {score_count})"
        baseline = mode_scores.iloc[baseline_start - 1 : baseline_end].copy()
        baseline_pb = float(baseline["pumbility"].mean())
        fallback_count = 0
        baseline_difficulties: list[float] = []
        baseline_edges: list[float] = []
        for row in baseline.to_dict(orient="records"):
            chart_id = str(row["chartId"])
            catalog_chart = catalog_map[chart_id]
            estimate = chart_map.get(chart_id, {}).get("estimatedDifficulty")
            if estimate is None or not math.isfinite(float(estimate)):
                estimate = float(catalog_chart["level"]) + 0.5
                fallback_count += 1
            estimate = float(estimate)
            baseline_difficulties.append(estimate)
            baseline_edges.append(float(catalog_chart["level"]) + 0.5 - estimate)
        scoring_rating = float(np.mean(baseline_difficulties))
        baseline_edge = float(np.mean(baseline_edges))
        current_values = [float(value) for value in mode_scores["pumbility"]]
        current_total = _top_total(current_values)
        existing_by_chart = {
            str(row["chartId"]): float(row["pumbility"])
            for row in mode_scores.to_dict(orient="records")
        }

        candidates: list[dict[str, Any]] = []
        for chart in combined_charts:
            if chart.get("type") != chart_type:
                continue
            if int(chart.get("level") or 0) < MIN_TARGET_LEVEL:
                continue
            estimate = chart.get("estimatedDifficulty")
            if estimate is None or not math.isfinite(float(estimate)):
                continue
            estimate = float(estimate)
            farm_edge = float(chart["level"]) + 0.5 - estimate
            expected = max(0.0, baseline_pb + float(slope) * (farm_edge - baseline_edge))
            chart_id = str(chart["chartId"])
            existing = existing_by_chart.get(chart_id)
            projected = max(existing or 0.0, expected)
            projection = (
                score_response_model.predict(
                    str(player_id), mode_key, scoring_rating, estimate, chart_id
                )
                if score_response_model is not None
                else ScoreProjectionResult(None, "population-crossfit", 0, "unavailable")
            )
            projected_score = projection.score
            simulated = [
                projected if str(row["chartId"]) == chart_id else float(row["pumbility"])
                for row in mode_scores.to_dict(orient="records")
            ]
            if existing is None:
                simulated.append(projected)
            gain = max(0.0, _top_total(simulated) - current_total)
            candidates.append(
                {
                    **dict(chart),
                    "distanceFromRating": round(estimate - scoring_rating, 6),
                    "farmEdge": round(farm_edge, 6),
                    "existingPumbility": round(existing, 3) if existing is not None else None,
                    "expectedPumbility": round(expected, 3),
                    "projectedGain": round(gain, 3),
                    "projectedScore": projected_score,
                    "scoreProjectionSource": projection.source,
                    "scoreProjectionSupportCount": projection.support_count,
                    "scoreProjectionConfidence": projection.confidence,
                    "played": existing is not None,
                }
            )
        candidates.sort(
            key=lambda row: (
                float(row["estimatedDifficulty"]),
                str(row["songName"]).casefold(),
                str(row["chartId"]),
            )
        )
        top = sorted(
            candidates,
            key=_projected_gain_sort_key,
        )[:TOP_RECOMMENDATION_COUNT]
        modes[mode_key] = {
            "eligible": True,
            "validScoreCount": int(score_count),
            "baselineRanks": [baseline_start, baseline_end],
            "baselineLabel": baseline_label,
            "baselinePumbility": round(baseline_pb, 3),
            "scoringRating": round(scoring_rating, 3),
            "ratingFallbackCharts": fallback_count,
            "pumbilityPerLevel": round(float(slope), 6),
            "projectionAvailable": any(
                row.get("projectedScore") is not None for row in candidates
            ),
            "scoreProjectionModel": SCORE_PROJECTION_MODEL_NAME,
            "currentTop50Pumbility": round(current_total, 3),
            "candidateCount": len(candidates),
            "candidates": candidates,
            "topRecommendations": top,
        }
    return {"playerKey": public_player_key(player_id), "modes": modes}


def build_player_coop_mode(
    player_id: str,
    phoenix2_snapshot: Mapping[str, Any],
    combined_charts: Sequence[Mapping[str, Any]],
    *,
    include_candidates: bool = True,
) -> dict[str, Any]:
    """Build additive Co-op recommendations from the Master grade ladder."""
    catalog, phoenix2_observations = build_coop_observations(
        None, phoenix2_snapshot
    )
    catalog_by_id = {str(row["chartId"]): row for row in catalog}
    player_scores = [
        row
        for row in phoenix2_observations
        if str(row["playerId"]) == str(player_id)
    ]
    existing_by_chart = {
        str(row["chartId"]): phoenix2_coop_rating(
            str(row["grade"]), str(row["plate"])
        )
        for row in player_scores
    }
    current_rating = sum(existing_by_chart.values())
    analysis_by_id = {
        str(row["chartId"]): dict(row)
        for row in combined_charts
        if row.get("type") == "CoOp" and row.get("chartId") is not None
    }

    sortable_top_scores: list[tuple[int, dict[str, Any]]] = []
    for score in player_scores:
        chart_id = str(score["chartId"])
        chart = analysis_by_id.get(chart_id, catalog_by_id.get(chart_id, {}))
        plate = str(score["plate"])
        sortable_top_scores.append(
            (
                int(score["score"]),
                {
                    **{
                        field: _json_safe_scalar(chart.get(field))
                        for field in TOP_SCORE_CHART_FIELDS
                    },
                    "mode": "Co-op",
                    "songName": chart.get("songName") or "Unknown chart",
                    "difficulty": chart.get("difficulty")
                    or f"{int(chart.get('level') or 0)}x",
                    "type": "CoOp",
                    "level": int(chart.get("level") or 0),
                    "chartId": chart_id,
                    "grade": str(score["grade"]),
                    "plate": plate,
                    "plateCode": PLATE_CODES[plate],
                    "coopRating": round(existing_by_chart[chart_id], 2),
                },
            )
        )
    sortable_top_scores.sort(
        key=lambda item: (
            -float(item[1]["coopRating"]),
            -item[0],
            str(item[1].get("songName") or "").casefold(),
            str(item[1]["chartId"]),
        )
    )
    top_scores = [row for _, row in sortable_top_scores]

    candidates: list[dict[str, Any]] = []
    for chart_id, chart in analysis_by_id.items():
        estimate = chart.get("estimatedDifficulty")
        goal = coop_master_goal_for_estimated_difficulty(estimate)
        if (
            not isinstance(estimate, (int, float))
            or not math.isfinite(float(estimate))
            or goal is None
        ):
            continue
        score, grade, plate = goal
        expected = phoenix2_coop_rating(str(grade), plate)
        existing = existing_by_chart.get(chart_id)
        gain = max(0.0, expected - float(existing or 0.0))
        candidates.append(
            {
                **dict(chart),
                "distanceFromRating": 0.0,
                "farmEdge": round(25.0 - float(estimate), 6),
                "existingPumbility": None,
                "expectedPumbility": None,
                "existingCoopRating": (
                    round(existing, 2) if existing is not None else None
                ),
                "expectedCoopRating": round(expected, 2),
                "projectedGain": round(gain, 2),
                "projectedScore": score,
                "scoreProjectionSource": "estimated-difficulty-master-grade-ladder",
                "scoreProjectionSupportCount": None,
                "scoreProjectionConfidence": "high",
                "projectedGrade": str(grade),
                "projectedPlate": plate,
                "projectedPlateCode": PLATE_CODES[plate],
                "projectedPlateProbability": None,
                "plateProjectionSource": "fixed-fair-game",
                "played": existing is not None,
            }
        )
    candidates.sort(
        key=lambda row: (
            -float(row["projectedGain"]),
            (
                float(row["difficultyModelContinuous"])
                if isinstance(row.get("difficultyModelContinuous"), (int, float))
                and math.isfinite(float(row["difficultyModelContinuous"]))
                else float(row["estimatedDifficulty"])
            ),
            -float(row["expectedCoopRating"]),
            str(row.get("songName") or "").casefold(),
            str(row.get("chartId") or ""),
        )
    )
    top_recommendations = candidates[:TOP_RECOMMENDATION_COUNT]
    return {
        "eligible": True,
        "validScoreCount": len(player_scores),
        "requiredScoreCount": 0,
        "projectionAvailable": bool(candidates),
        "scoreProjectionModel": COOP_SCORE_PROJECTION_MODEL_NAME,
        "currentCoopRating": round(current_rating, 2),
        "candidateRange": [10, 25],
        "candidateCount": len(candidates),
        "filterCandidateCount": len(candidates),
        "topScores": top_scores,
        **({"filterCandidates": candidates} if include_candidates else {}),
        "topRecommendations": top_recommendations,
    }


def build_player_recommendation(
    player_id: str,
    phoenix2_snapshot: Mapping[str, Any],
    combined_charts: Sequence[Mapping[str, Any]],
    phoenix2_slopes: Mapping[str, float],
    score_response_model: ScoreResponseModel | None = None,
    *,
    phoenix1_snapshot: Mapping[str, Any] | None = None,
    prepared_phoenix2: tuple[pd.DataFrame, pd.DataFrame] | None = None,
    prepared_phoenix1: tuple[pd.DataFrame, pd.DataFrame] | None = None,
    plate_model: PlateProjectionModel | None = None,
    include_candidates: bool = True,
) -> dict[str, Any]:
    """Build recommendations with historical rating and current-state separation."""
    catalog, scores = prepared_phoenix2 or _clean_snapshot_frames(phoenix2_snapshot)
    if prepared_phoenix1 is not None:
        phoenix1_catalog, phoenix1_scores = prepared_phoenix1
    elif phoenix1_snapshot is not None:
        phoenix1_catalog, phoenix1_scores = _prepare_phoenix1_rating_frames(
            phoenix1_snapshot, catalog
        )
    else:
        phoenix1_catalog = catalog.iloc[0:0].copy()
        phoenix1_scores = scores.iloc[0:0].copy()

    if plate_model is None:
        plate_model = PlateProjectionModel(phoenix1_snapshot or {}, phoenix2_snapshot)

    player_scores = scores[scores["playerId"] == str(player_id)].copy()
    player_phoenix1_scores = phoenix1_scores[
        phoenix1_scores["playerId"] == str(player_id)
    ].copy()
    modes: dict[str, Any] = {}
    catalog_by_id = {
        str(row["chartId"]): row for row in catalog.to_dict(orient="records")
    }
    chart_analysis_by_id = {
        str(row["chartId"]): dict(row)
        for row in combined_charts
        if row.get("chartId") is not None
    }
    overall_source_rows: dict[str, list[dict[str, Any]]] = {
        "singles": [],
        "doubles": [],
    }
    overall_filter_rows: dict[str, list[dict[str, Any]]] = {
        "singles": [],
        "doubles": [],
    }

    chart_type_by_id = {
        str(row["chartId"]): str(row["type"])
        for row in catalog[["chartId", "type"]].to_dict(orient="records")
        if row.get("type") in MODE_TYPES
    }
    overall_scores = player_scores[
        player_scores["chartId"].astype(str).isin(set(chart_type_by_id))
    ].copy()
    overall_scores = overall_scores.sort_values(
        ["pumbility", "score", "chartId"],
        ascending=[False, False, True],
        kind="mergesort",
    )
    overall_values = [float(value) for value in overall_scores["pumbility"]]
    overall_current_total = _top_total(overall_values)
    overall_top_scores = _public_top_scores(
        overall_scores,
        catalog_by_id,
        chart_analysis_by_id,
    )
    overall_existing_by_chart = {
        str(row["chartId"]): float(row["pumbility"])
        for row in overall_scores.to_dict(orient="records")
    }
    overall_top50_chart_ids = set(
        overall_scores.iloc[:TOP_PUMBILITY_COUNT]["chartId"].astype(str)
    )
    overall_top50_cutoff = (
        float(overall_scores.iloc[TOP_PUMBILITY_COUNT - 1]["pumbility"])
        if len(overall_scores) >= TOP_PUMBILITY_COUNT
        else None
    )
    overall_top50_mode_counts = {"singles": 0, "doubles": 0}
    for chart_id in overall_scores.iloc[:TOP_PUMBILITY_COUNT]["chartId"].astype(str):
        chart_type = chart_type_by_id.get(chart_id)
        if chart_type in MODE_TYPES:
            overall_top50_mode_counts[_mode_key(chart_type)] += 1

    def public_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in row.items()
            if not str(key).startswith("_")
        }

    for chart_type in MODE_TYPES:
        mode_key = _mode_key(chart_type)
        mode_ids = set(catalog.loc[catalog["type"] == chart_type, "chartId"])
        mode_scores = player_scores[player_scores["chartId"].isin(mode_ids)].copy()
        mode_scores = mode_scores.sort_values(
            ["pumbility", "score", "chartId"],
            ascending=[False, False, True],
            kind="mergesort",
        )
        phoenix1_mode_ids = set(
            phoenix1_catalog.loc[phoenix1_catalog["type"] == chart_type, "chartId"]
        )
        phoenix1_mode_scores = player_phoenix1_scores[
            player_phoenix1_scores["chartId"].isin(phoenix1_mode_ids)
        ].copy()
        phoenix1_mode_scores = phoenix1_mode_scores.sort_values(
            ["pumbility", "score", "chartId"],
            ascending=[False, False, True],
            kind="mergesort",
        )

        phoenix2_score_count = len(mode_scores)
        current_values = [float(value) for value in mode_scores["pumbility"]]
        current_total = _top_total(current_values)
        top_scores = _public_top_scores(
            mode_scores,
            catalog_by_id,
            chart_analysis_by_id,
        )
        existing_by_chart = {
            str(row["chartId"]): float(row["pumbility"])
            for row in mode_scores.to_dict(orient="records")
        }
        top50_chart_ids = set(
            mode_scores.iloc[:TOP_PUMBILITY_COUNT]["chartId"].astype(str)
        )
        top50_cutoff = (
            float(mode_scores.iloc[TOP_PUMBILITY_COUNT - 1]["pumbility"])
            if len(mode_scores) >= TOP_PUMBILITY_COUNT
            else None
        )
        rating_source, rating_scores = _select_rating_scores(
            phoenix1_mode_scores, mode_scores
        )

        if rating_scores.empty:
            modes[mode_key] = {
                "eligible": False,
                "validScoreCount": int(phoenix2_score_count),
                "requiredScoreCount": 1,
                "phoenix2ScoreCount": int(phoenix2_score_count),
                "phoenix2ScoreThreshold": PHOENIX2_RATING_SCORE_THRESHOLD,
                "ratingSource": None,
                "reason": (
                    "At least one valid Phoenix 2 score or 20 valid Phoenix 1 "
                    "scores are required in this mode."
                ),
                "projectionAvailable": False,
                "scoreProjectionModel": SCORE_PROJECTION_MODEL_NAME,
                "currentTop50Pumbility": round(current_total, 3),
                "currentTop50CutoffPumbility": (
                    round(top50_cutoff, 3) if top50_cutoff is not None else None
                ),
                "currentTop50Count": min(
                    int(phoenix2_score_count), TOP_PUMBILITY_COUNT
                ),
                "topScores": top_scores,
                **({"filterCandidates": []} if include_candidates else {}),
                "topRecommendations": [],
            }
            continue

        rating_baseline, rating_start, rating_end, rating_label = _rating_window(
            rating_scores
        )
        scoring_rating = _skill_rating_from_rows(
            rating_baseline,
            chart_type,
            start_rank=1,
            end_rank=len(rating_baseline),
        )
        if scoring_rating is None:
            raise ValueError("A selected recommendation rating window is invalid.")

        projection_rating_source, projection_rating_scores = (
            _select_projection_rating_scores(phoenix1_mode_scores, mode_scores)
        )
        projection_rating, projection_rating_leave_one_out = _rating_lookup(
            projection_rating_scores, chart_type
        )
        projection_start = PROJECTION_RATING_START_RANK
        projection_end = PROJECTION_RATING_END_RANK
        projection_label = "ranks 11-30"
        projection_baseline = (
            projection_rating_scores.iloc[projection_start - 1 : projection_end]
            if projection_rating is not None
            else projection_rating_scores.iloc[0:0]
        )
        baseline_pb = (
            float(projection_baseline["pumbility"].mean())
            if not projection_baseline.empty
            else None
        )
        slope = phoenix2_slopes.get(mode_key)

        filter_candidates: list[dict[str, Any]] = []
        for chart in combined_charts:
            if chart.get("type") != chart_type:
                continue
            estimate = chart.get("estimatedDifficulty")
            if estimate is None or not math.isfinite(float(estimate)):
                continue
            estimate = float(estimate)
            farm_edge = float(chart["level"]) + 0.5 - estimate
            chart_id = str(chart["chartId"])
            existing = existing_by_chart.get(chart_id)
            candidate_projection_rating = projection_rating_leave_one_out.get(
                chart_id, projection_rating
            )
            if candidate_projection_rating is None:
                candidate_projection_rating = projection_rating
            projection = (
                score_response_model.predict(
                    str(player_id),
                    mode_key,
                    candidate_projection_rating,
                    estimate,
                    chart_id,
                )
                if score_response_model is not None
                and candidate_projection_rating is not None
                else ScoreProjectionResult(None, "population-crossfit", 0, "unavailable")
            )
            projected_score = projection.score
            projected_grade = grade_for_score(projected_score)
            projected_plate: str | None = None
            projected_plate_probability: float | None = None
            plate_projection_source: str | None = None
            expected: float | None = None
            gain: float | None = None
            overall_gain: float | None = None
            if projected_grade is not None:
                distribution = plate_model.distribution(
                    str(player_id), chart_type, projected_grade
                )
                projected_plate = distribution.median_plate
                projected_plate_probability = distribution.probabilities[projected_plate]
                plate_projection_source = distribution.source
                expected = phoenix2_pumbility(
                    chart_type,
                    int(chart["level"]),
                    projected_grade,
                    projected_plate,
                )
                gain = _top50_marginal_gain(
                    expected,
                    existing_pumbility=existing,
                    existing_in_top50=chart_id in top50_chart_ids,
                    current_score_count=len(mode_scores),
                    cutoff=top50_cutoff,
                )
                overall_existing = overall_existing_by_chart.get(chart_id)
                overall_gain = _top50_marginal_gain(
                    expected,
                    existing_pumbility=overall_existing,
                    existing_in_top50=chart_id in overall_top50_chart_ids,
                    current_score_count=len(overall_scores),
                    cutoff=overall_top50_cutoff,
                )
            filter_candidates.append(
                {
                    **dict(chart),
                    "distanceFromRating": round(estimate - scoring_rating, 6),
                    "farmEdge": round(farm_edge, 6),
                    "existingPumbility": (
                        round(existing, 3) if existing is not None else None
                    ),
                    "expectedPumbility": (
                        round(expected, 3) if expected is not None else None
                    ),
                    "projectedGain": round(gain, 3) if gain is not None else None,
                    "_overallProjectedGain": (
                        round(overall_gain, 3) if overall_gain is not None else None
                    ),
                    "projectedScore": projected_score,
                    "scoreProjectionSource": projection.source,
                    "scoreProjectionSupportCount": projection.support_count,
                    "scoreProjectionConfidence": projection.confidence,
                    "projectedGrade": projected_grade,
                    "projectedPlate": projected_plate,
                    "projectedPlateCode": (
                        PLATE_CODES[projected_plate] if projected_plate else None
                    ),
                    "projectedPlateProbability": (
                        round(projected_plate_probability, 6)
                        if projected_plate_probability is not None
                        else None
                    ),
                    "plateProjectionSource": plate_projection_source,
                    "played": existing is not None,
                }
            )
        projection_available = any(
            row.get("projectedScore") is not None for row in filter_candidates
        )
        if projection_available:
            ranked_filter_candidates = sorted(
                filter_candidates,
                key=_projected_gain_sort_key,
            )
        else:
            ranked_filter_candidates = sorted(
                filter_candidates,
                key=lambda row: (
                    -float(row["farmEdge"]),
                    float(row["estimatedDifficulty"]),
                    str(row["songName"]).casefold(),
                    str(row["chartId"]),
                ),
            )
        default_candidates = [
            row
            for row in ranked_filter_candidates
            if int(row.get("level") or 0) >= MIN_TARGET_LEVEL
            and float(row["estimatedDifficulty"])
            <= scoring_rating + RECOMMENDATION_RADIUS
        ]
        top = default_candidates[:TOP_RECOMMENDATION_COUNT]

        overall_source_rows[mode_key] = [dict(row) for row in top]
        overall_filter_rows[mode_key] = [
            dict(row) for row in ranked_filter_candidates
        ]

        modes[mode_key] = {
            "eligible": True,
            "validScoreCount": int(phoenix2_score_count),
            "phoenix2ScoreCount": int(phoenix2_score_count),
            "phoenix2ScoreThreshold": PHOENIX2_RATING_SCORE_THRESHOLD,
            "ratingSource": rating_source,
            "ratingSourceScoreCount": int(len(rating_scores)),
            "ratingBaselineRanks": [rating_start, rating_end],
            "ratingBaselineLabel": rating_label,
            "baselineRanks": [projection_start, projection_end],
            "baselineLabel": projection_label,
            "baselinePumbility": (
                round(baseline_pb, 3) if baseline_pb is not None else None
            ),
            "scoringRating": round(scoring_rating, 3),
            "projectionRating": (
                round(projection_rating, 3)
                if projection_rating is not None
                else None
            ),
            "projectionRatingSource": projection_rating_source,
            "projectionRatingSourceScoreCount": int(len(projection_rating_scores)),
            "projectionRatingRequiredScoreCount": PROJECTION_RATING_SCORE_THRESHOLD,
            "projectionRatingRanks": [projection_start, projection_end],
            "projectionRatingLabel": projection_label,
            "ratingReferenceGrade": SKILL_RATING_REFERENCE_GRADE,
            "ratingReferencePlate": SKILL_RATING_REFERENCE_PLATE,
            "ratingReferenceMultiplier": round(
                SKILL_RATING_REFERENCE_MULTIPLIER, 6
            ),
            "projectionAvailable": projection_available,
            "scoreProjectionModel": SCORE_PROJECTION_MODEL_NAME,
            "pumbilityPerLevel": (
                round(float(slope), 6) if slope is not None else None
            ),
            "currentTop50Pumbility": round(current_total, 3),
            "currentTop50CutoffPumbility": (
                round(top50_cutoff, 3) if top50_cutoff is not None else None
            ),
            "currentTop50Count": min(
                int(phoenix2_score_count), TOP_PUMBILITY_COUNT
            ),
            "topScores": top_scores,
            "candidateRange": [
                None,
                round(scoring_rating + RECOMMENDATION_RADIUS, 3),
            ],
            "candidateCount": len(default_candidates),
            "filterCandidateCount": len(ranked_filter_candidates),
            **(
                {
                    "filterCandidates": [
                        public_candidate(row) for row in ranked_filter_candidates
                    ]
                }
                if include_candidates
                else {}
            ),
            "topRecommendations": [public_candidate(row) for row in top],
        }

    modes["coop"] = build_player_coop_mode(
        str(player_id),
        phoenix2_snapshot,
        combined_charts,
        include_candidates=include_candidates,
    )

    source_mode_eligibility = {
        mode_key: bool(modes.get(mode_key, {}).get("eligible"))
        for mode_key in ("singles", "doubles")
    }
    source_recommendation_counts = {
        mode_key: len(overall_source_rows[mode_key])
        for mode_key in ("singles", "doubles")
    }
    overall_candidates: list[dict[str, Any]] = []
    for mode_key in ("singles", "doubles"):
        if not source_mode_eligibility[mode_key]:
            continue
        for source in overall_source_rows[mode_key]:
            row = public_candidate(source)
            row["projectedGain"] = source.get("_overallProjectedGain")
            overall_candidates.append(row)

    overall_projection_available = any(
        row.get("projectedScore") is not None for row in overall_candidates
    )
    if overall_projection_available:
        overall_top = sorted(
            overall_candidates,
            key=_projected_gain_sort_key,
        )[:TOP_RECOMMENDATION_COUNT]
    else:
        overall_top = sorted(
            overall_candidates,
            key=lambda row: (
                -float(row["farmEdge"]),
                float(row["estimatedDifficulty"]),
                str(row["songName"]).casefold(),
                str(row["chartId"]),
            ),
        )[:TOP_RECOMMENDATION_COUNT]

    overall_filter_candidates: list[dict[str, Any]] = []
    for mode_key in ("singles", "doubles"):
        if not source_mode_eligibility[mode_key]:
            continue
        for source in overall_filter_rows[mode_key]:
            row = public_candidate(source)
            row["projectedGain"] = source.get("_overallProjectedGain")
            overall_filter_candidates.append(row)
    if overall_projection_available:
        overall_filter_candidates.sort(key=_projected_gain_sort_key)
    else:
        overall_filter_candidates.sort(
            key=lambda row: (
                -float(row["farmEdge"]),
                float(row["estimatedDifficulty"]),
                str(row["songName"]).casefold(),
                str(row["chartId"]),
            )
        )

    overall_eligible = any(source_mode_eligibility.values())
    modes["overall"] = {
        "eligible": overall_eligible,
        "validScoreCount": int(len(overall_scores)),
        "phoenix2ScoreCount": int(len(overall_scores)),
        "requiredScoreCount": 1,
        "projectionAvailable": overall_projection_available,
        "scoreProjectionModel": SCORE_PROJECTION_MODEL_NAME,
        "currentTop50Pumbility": round(overall_current_total, 3),
        "currentTop50CutoffPumbility": (
            round(overall_top50_cutoff, 3)
            if overall_top50_cutoff is not None
            else None
        ),
        "currentTop50Count": min(len(overall_scores), TOP_PUMBILITY_COUNT),
        "topScores": overall_top_scores,
        "top50ModeCounts": overall_top50_mode_counts,
        "sourceModeEligibility": source_mode_eligibility,
        "sourceRecommendationCounts": source_recommendation_counts,
        "candidateCount": len(overall_candidates),
        "filterCandidateCount": len(overall_filter_candidates),
        **(
            {"filterCandidates": overall_filter_candidates}
            if include_candidates
            else {}
        ),
        "topRecommendations": overall_top,
        **(
            {}
            if overall_eligible
            else {
                "reason": (
                    "At least one mode needs a valid Phoenix 2 score or 20 valid "
                    "Phoenix 1 scores before Overall recommendations are available."
                )
            }
        ),
    }
    return {"playerKey": public_player_key(player_id), "modes": modes}


def build_recommendation_index(
    phoenix1_snapshot: Mapping[str, Any],
    phoenix2_snapshot: Mapping[str, Any],
    *,
    generated_at_utc: str | None = None,
    combined_charts: Sequence[Mapping[str, Any]] | None = None,
    phoenix2_slopes: Mapping[str, float] | None = None,
    generation_key: str | None = None,
    shard_writer: Callable[[int, Mapping[str, Any]], None] | None = None,
    shard_size: int = RECOMMENDATION_SHARD_SIZE,
) -> dict[str, Any]:
    if (generation_key is None) != (shard_writer is None):
        raise ValueError("Recommendation sharding requires a generation key and writer.")
    if shard_size <= 0:
        raise ValueError("Recommendation shard size must be positive.")
    if combined_charts is None or phoenix2_slopes is None:
        built_charts, built_slopes, _ = build_combined_chart_results(
            phoenix1_snapshot, phoenix2_snapshot
        )
        combined_charts = built_charts
        phoenix2_slopes = built_slopes
    charts_for_players = _recommendation_chart_rows(combined_charts)
    slopes = dict(phoenix2_slopes)
    score_response_model, score_projection_metadata = fit_score_response_model(
        phoenix1_snapshot,
        phoenix2_snapshot,
        charts_for_players,
    )
    prepared_phoenix2 = _clean_snapshot_frames(phoenix2_snapshot)
    prepared_phoenix1 = _prepare_phoenix1_rating_frames(
        phoenix1_snapshot, prepared_phoenix2[0]
    )
    plate_model = PlateProjectionModel(phoenix1_snapshot, phoenix2_snapshot)
    players = phoenix2_snapshot.get("players", [])
    named_players = [
        row
        for row in players
        if isinstance(row, Mapping)
        and str(row.get("playerId") or row.get("userId") or "").strip()
        and str(row.get("username") or "").strip()
    ]
    named_players.sort(
        key=lambda row: (
            str(row.get("username") or "").strip().casefold(),
            public_player_key(row.get("playerId") or row.get("userId")),
        )
    )
    username_counts = Counter(str(row["username"]).strip().casefold() for row in named_players)
    output_players: list[dict[str, Any]] = []
    shard_players: list[dict[str, Any]] = []
    shard_number = 0
    generated_at = generated_at_utc or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    for row in named_players:
        player_id = str(row.get("playerId") or row.get("userId"))
        username = str(row["username"]).strip()
        recommendation = build_player_recommendation(
            player_id,
            phoenix2_snapshot,
            charts_for_players,
            slopes,
            score_response_model,
            prepared_phoenix2=prepared_phoenix2,
            prepared_phoenix1=prepared_phoenix1,
            plate_model=plate_model,
            include_candidates=True,
        )
        suffix = recommendation["playerKey"][-4:]
        recommendation.update(
            {
                "username": username,
                "displayName": (
                    f"{username} · {suffix}"
                    if username_counts[username.casefold()] > 1
                    else username
                ),
            }
        )
        if shard_writer is None:
            output_players.append(recommendation)
            continue
        output_players.append(
            {
                "playerKey": recommendation["playerKey"],
                "username": recommendation["username"],
                "displayName": recommendation["displayName"],
                "eligibility": {
                    mode: bool(details.get("eligible"))
                    for mode, details in recommendation.get("modes", {}).items()
                    if mode in {"singles", "doubles", "coop"}
                },
                "shard": shard_number,
            }
        )
        shard_players.append(recommendation)
        if len(shard_players) >= shard_size:
            shard_writer(
                shard_number,
                {
                    "storageSchemaVersion": RECOMMENDATION_STORAGE_SCHEMA_VERSION,
                    "generationKey": generation_key,
                    "generatedAtUtc": generated_at,
                    "players": shard_players,
                },
            )
            shard_players = []
            shard_number += 1
    if shard_writer is not None and shard_players:
        shard_writer(
            shard_number,
            {
                "storageSchemaVersion": RECOMMENDATION_STORAGE_SCHEMA_VERSION,
                "generationKey": generation_key,
                "generatedAtUtc": generated_at,
                "players": shard_players,
            },
        )
        shard_number += 1
    payload = {
        "schemaVersion": RECOMMENDATION_SCHEMA_VERSION,
        "generatedAtUtc": generated_at,
        "method": {
            "catalog": "Phoenix 2 authoritative catalog",
            "overlapRule": "best Phoenix 2 score always replaces Phoenix 1 for the same player and chart",
            "phoenix1RerateHandling": "Phoenix 1 rating rows use current Phoenix 2 chart levels and recompute Pumbility from the raw score, Phoenix 2 grade boundaries, and recorded plate",
            "crossVersionNormalization": "chart-difficulty evidence uses version- and mode-normalized residuals; player ratings use Phoenix 2-formula Pumbility in both versions",
            "difficultyDeltaScale": DIFFICULTY_DELTA_SCALE,
            "folderRangeNormalization": {
                "method": "one-sided expected-normal-maximum order-statistic compression",
                "referenceMeasuredCharts": FOLDER_RANGE_REFERENCE_CHARTS,
                "formula": "min(1, expectedNormalMax(reference) / expectedNormalMax(measured charts in folder))",
                "expandsFolders": False,
            },
            "phoenix1ScoreOverrides": phoenix1_score_overrides_metadata(),
            "pumbilityPerLevel": slopes,
            "scoreProjectionCoverage": score_projection_metadata,
            "scoreProjectionData": "joined Phoenix 1 + Phoenix 2 scores normalized with the Phoenix 2 chart catalog and grade-and-plate Pumbility formula, with Phoenix 2 precedence for overlapping player/chart rows",
            "baselineRanks": [BASELINE_START_RANK, BASELINE_END_RANK],
            "recommendationRatingRanks": [1, RECOMMENDATION_RATING_SCORE_COUNT],
            "projectionRatingRanks": [
                PROJECTION_RATING_START_RANK,
                PROJECTION_RATING_END_RANK,
            ],
            "phoenix1RatingRanks": [1, RECOMMENDATION_RATING_SCORE_COUNT],
            "phoenix2RatingRanks": [1, RECOMMENDATION_RATING_SCORE_COUNT],
            "phoenix2RatingScoreThreshold": PHOENIX2_RATING_SCORE_THRESHOLD,
            "projectionRatingScoreThreshold": PROJECTION_RATING_SCORE_THRESHOLD,
            "ratingReference": "continuous chart level whose Phoenix 2 S with Fair Game Pumbility equals the selected window's average Pumbility",
            "ratingReferenceGrade": SKILL_RATING_REFERENCE_GRADE,
            "ratingReferencePlate": SKILL_RATING_REFERENCE_PLATE,
            "ratingReferenceMultiplier": SKILL_RATING_REFERENCE_MULTIPLIER,
            "ratingSource": "the displayed rating and recommendation ceiling use Phoenix 2 top 20 at 20 valid scores; otherwise Phoenix 1 top 20 when available, then partial Phoenix 2",
            "projectionRatingSource": "score projections use Phoenix 2 ranks 11-30 at 30 valid scores; otherwise Phoenix 1 ranks 11-30 when all 30 are available",
            "shortHistoryBaseline": "a mode without a complete 30-score Phoenix 2 or Phoenix 1 source keeps top-20 farm-edge recommendations but does not receive personal projected scores",
            "candidateUpperRadius": RECOMMENDATION_RADIUS,
            "candidateLowerBound": None,
            "topPumbilityCount": TOP_PUMBILITY_COUNT,
            "overallPumbility": "the highest 50 Phoenix 2 Pumbility values from the player's combined Single and Double scores",
            "overallRecommendations": "merge the displayed top 20 Single and top 20 Double recommendations, recalculate every projected gain against the shared Phoenix 2 S+D top 50, then retain the best 20; official-difficulty filters rank the full level-16+ recommendation catalog",
            "actualPumbilitySource": "upstream",
            "projection": "median projected raw score converted with the mode-specific Phoenix 2 projection formula and the weighted-median plate",
            "plateProjection": "weighted median of the ordered RG-to-PG hierarchical player, mode, and Phoenix 2 letter-grade distribution using Phoenix 2 observations plus a held-out-tuned capped Phoenix 1 prior and population smoothing",
            "plateProjectionStatistic": "weighted-median",
            "pumbilityProjectionStatistic": "median-score-median-plate",
            "phoenix1PlatePriorCap": plate_model.phoenix1_cap,
            "projectedGain": "deterministic change from the median-score and median-plate projected Pumbility to the active Phoenix 2 top-50 pool; Single and Double use their mode pool, while Overall uses the shared S+D pool; the projection replaces the current chart PB and the number-50 chart only when it improves the retained top 50",
            "projectedGainTieBreak": "equal displayed projected gains are ordered by estimated difficulty from easiest to hardest, then expected Pumbility and chart name",
            "manualRanking": "farm edge up to 1.0 estimated-difficulty point above the requested scoring rating; official-difficulty filters use all ranked level-16+ charts and no personal top-50 gain is inferred",
            "skillRatingCatalog": "all valid charts retained by the Phoenix 2 catalog, including levels below the display minimum",
            "currentStateSource": "Phoenix 2 only for played status, existing Pumbility, current top 50, and projected gain",
            "displayMinimumOfficialLevel": MIN_TARGET_LEVEL,
            "scoreProjection": "using each player's S+FG-equivalent ranks 11-30 Pumbility rating, take the Phoenix-source-weighted median raw score from all other players with a normalized result on the exact chart (Phoenix 1 = 1, Phoenix 2 = 2); search plus or minus 0.2 through 0.5 in 0.1 steps seeking 20 peers, repeat seeking 10, then repeat seeking five; use all peers within the narrowest successful radius and fall back below five peers to the player-balanced population response surface with the same source weights",
            "scoreProjectionModel": SCORE_PROJECTION_MODEL_NAME,
        },
        "players": output_players,
    }
    if shard_writer is not None:
        payload.update(
            {
                "storageSchemaVersion": RECOMMENDATION_STORAGE_SCHEMA_VERSION,
                "generationKey": generation_key,
                "shardCount": shard_number,
                "shardSize": shard_size,
            }
        )
        return payload
    payload["charts"] = [
        chart
        for chart in charts_for_players
        if isinstance(chart.get("estimatedDifficulty"), (int, float))
        and math.isfinite(float(chart["estimatedDifficulty"]))
    ]
    return payload
