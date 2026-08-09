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
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from piu_misgrade_analyzer import (
    AnalysisConfig,
    DIFFICULTY_DELTA_SCALE,
    EFFECT_BANDS,
    MIN_TARGET_LEVEL,
    MODE_LABELS,
    MODE_TYPES,
    RELATIVE_GROUPS,
    SCRIPT_VERSION,
    _fit_level_calibration,
    _robust_location,
    apply_within_level_difficulty,
)
from phoenix2_pumbility import (
    PLATE_CODES,
    PlateProjectionModel,
    grade_for_score,
    phoenix2_pumbility,
)


RECOMMENDATION_SCHEMA_VERSION = 11
RECOMMENDATION_STORAGE_SCHEMA_VERSION = 2
RECOMMENDATION_SHARD_SIZE = 10
COMBINED_TIER_SCHEMA_VERSION = 1
RECOMMENDATION_RADIUS = 0.0
BASELINE_START_RANK = 11
BASELINE_END_RANK = 30
RECOMMENDATION_RATING_SCORE_COUNT = 10
PHOENIX2_RATING_SCORE_THRESHOLD = 10
TOP_PUMBILITY_COUNT = 50
TOP_RECOMMENDATION_COUNT = 50
MAX_RAW_SCORE = 1_000_000
SCORE_RESPONSE_MODEL_NAME = "population-crossfit-monotone-v2"
SCORE_PROJECTION_MODEL_NAME = "similar-skill-top100-q50-v1"
SCORE_RESPONSE_FOLDS = 5
SCORE_RESPONSE_GRID_STEP = 0.1
SCORE_RESPONSE_SMOOTHING_RADIUS = 8
SCORE_RESPONSE_MIN_SUPPORT = 5
PEER_SCORE_TOP_COUNT = 100
PEER_SCORE_QUANTILE = 0.50
PEER_SCORE_MIN_SUPPORT = 5
PEER_SCORE_INITIAL_RADIUS = 0.25
PEER_SCORE_MAX_RADIUS = 0.50
PEER_SCORE_RADIUS_STEP = 0.05
PEER_SCORE_WEIGHT_SIGMA = 0.25
PLAYER_KEY_NAMESPACE = "pumbility-farmer-recommendations-v1"
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
    "difficultyDelta",
    "difficultyCi95Low",
    "difficultyCi95High",
    "nContributors",
    "phoenix1Contributors",
    "phoenix2Contributors",
    "evidenceStatus",
)


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

    def __post_init__(self) -> None:
        size = len(self.player_keys)
        if len(self.ratings) != size or len(self.scores) != size:
            raise ValueError("A peer score cohort has inconsistent array lengths.")

    def to_payload(self) -> dict[str, Any]:
        return {
            "playerKeys": self.player_keys.tolist(),
            "ratings": self.ratings.tolist(),
            "scores": self.scores.tolist(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "_PeerScoreCohort":
        return cls(
            np.asarray(payload.get("playerKeys", []), dtype=np.str_),
            np.asarray(payload.get("ratings", []), dtype=float),
            np.asarray(payload.get("scores", []), dtype=float),
        )

    def predict(self, player_key: str, scoring_rating: float) -> tuple[float, int] | None:
        if not math.isfinite(scoring_rating) or not len(self.ratings):
            return None
        distances = np.abs(self.ratings - float(scoring_rating))
        eligible_player = self.player_keys != str(player_key)
        for radius in np.arange(
            PEER_SCORE_INITIAL_RADIUS,
            PEER_SCORE_MAX_RADIUS + PEER_SCORE_RADIUS_STEP / 2,
            PEER_SCORE_RADIUS_STEP,
        ):
            selected = eligible_player & (distances <= float(radius) + 1e-9)
            support = int(np.count_nonzero(selected))
            if support < PEER_SCORE_MIN_SUPPORT:
                continue
            selected_distances = distances[selected]
            selected_scores = self.scores[selected]
            weights = np.exp(
                -0.5 * (selected_distances / PEER_SCORE_WEIGHT_SIGMA) ** 2
            )
            order = np.argsort(selected_scores, kind="mergesort")
            ordered_scores = selected_scores[order]
            ordered_weights = weights[order]
            total_weight = float(ordered_weights.sum())
            if not math.isfinite(total_weight) or total_weight <= 0:
                return None
            positions = (
                np.cumsum(ordered_weights) - 0.5 * ordered_weights
            ) / total_weight
            score = float(
                np.interp(
                    PEER_SCORE_QUANTILE,
                    positions,
                    ordered_scores,
                    left=float(ordered_scores[0]),
                    right=float(ordered_scores[-1]),
                )
            )
            if math.isfinite(score):
                return score, support
            return None
        return None


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
        for key in cohort_keys:
            cohort = self.peer_cohorts[key]
            peer_player_keys.append(np.asarray(cohort.player_keys, dtype=np.str_))
            peer_ratings.append(np.asarray(cohort.ratings, dtype=float))
            peer_scores.append(np.asarray(cohort.scores, dtype=float))
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
                peer_cohorts: dict[str, _PeerScoreCohort] = {}
                present_peer_names = names & peer_names
                if present_peer_names and present_peer_names != peer_names:
                    raise ValueError("A stored peer score model is incomplete.")
                if present_peer_names:
                    cohort_keys = [
                        str(value) for value in arrays["peer_cohort_keys"].tolist()
                    ]
                    offsets = np.asarray(arrays["peer_cohort_offsets"], dtype=np.int64)
                    player_keys = np.asarray(arrays["peer_player_keys"], dtype=np.str_)
                    ratings = np.asarray(arrays["peer_ratings"], dtype=float)
                    scores = np.asarray(arrays["peer_scores"], dtype=float)
                    if (
                        len(offsets) != len(cohort_keys) + 1
                        or not len(offsets)
                        or int(offsets[0]) != 0
                        or np.any(np.diff(offsets) < 0)
                        or int(offsets[-1]) != len(player_keys)
                        or len(ratings) != len(player_keys)
                        or len(scores) != len(player_keys)
                    ):
                        raise ValueError("A stored peer score model is invalid.")
                    for index, key in enumerate(cohort_keys):
                        start = int(offsets[index])
                        end = int(offsets[index + 1])
                        peer_cohorts[key] = _PeerScoreCohort(
                            player_keys[start:end],
                            ratings[start:end],
                            scores[start:end],
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
                    score, support = peer_prediction
                    confidence = (
                        "high" if support >= 20 else "medium" if support >= 10 else "low"
                    )
                    return ScoreProjectionResult(
                        int(round(min(MAX_RAW_SCORE, max(0.0, score)))),
                        "peer-top100-q50",
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


def recommendation_blob_path() -> str:
    return "analysis/recommendations/latest.json"


def recommendation_generation_key(job_id: object) -> str:
    return hashlib.sha256(str(job_id).encode("utf-8")).hexdigest()[:20]


def recommendation_shard_prefix(generation_key: object | None = None) -> str:
    base = "analysis/recommendations/generations/"
    return base if generation_key is None else f"{base}{generation_key}/shards/"


def recommendation_shard_path(generation_key: object, shard: int) -> str:
    return f"{recommendation_shard_prefix(generation_key)}{int(shard):04d}.json"


def combined_tier_blob_path() -> str:
    return "analysis/combined/latest.json"


def phoenix1_snapshot_path() -> str:
    return "analysis/private/phoenix1.json"


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
        columns = ["playerId", "chartId", "mode", "source", "normalizedResidual"]
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
        frames.append(
            contribution[
                [
                    "playerId",
                    "chartId",
                    "mode",
                    "source",
                    "normalizedResidual",
                ]
            ]
        )
    columns = ["playerId", "chartId", "mode", "source", "normalizedResidual"]
    return (
        pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns),
        slopes,
    )


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
            location = _robust_location(values)
            std = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
            margin = 1.96 * std / math.sqrt(len(values)) if len(values) > 1 else 0.0
            counts = Counter(group["source"])
            stat_rows.append(
                {
                    "chartId": str(chart_id),
                    "nContributors": int(group["playerId"].nunique()),
                    "nPlayersScored": int(group["playerId"].nunique()),
                    "meanResidualPb": float(np.mean(values)),
                    "chartResidualPb": location,
                    "medianResidualPb": float(np.median(values)),
                    "residualStdPb": std,
                    "residualCi95LowPb": location - margin,
                    "residualCi95HighPb": location + margin,
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
        "averageDifficulty",
        "difficultyDelta",
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
    metadata = {
        "modes": mode_metadata,
        "sourceObservations": int(len(combined)),
        "phoenix1Observations": int((combined["source"] == "phoenix1").sum()),
        "phoenix2Observations": int((combined["source"] == "phoenix2").sum()),
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
        if int(chart.get("level") or 0) >= MIN_TARGET_LEVEL
    ]
    records.sort(
        key=lambda chart: (
            0 if chart.get("type") == "Single" else 1,
            (
                float(chart["difficultyDelta"])
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
                "extremelyEasyCharts": int(
                    (folder_subset["effectBandRank"] == 1).sum()
                ),
                "extremelyHardCharts": int(
                    (folder_subset["effectBandRank"] == 9).sum()
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
            },
            "folders": folders,
        }

    measured_count = sum(
        1 for chart in records if chart.get("difficultyDelta") is not None
    )
    summary = {
        "scriptVersion": f"{SCRIPT_VERSION}+combined-tier-v{COMBINED_TIER_SCHEMA_VERSION}",
        "generatedAtUtc": generated_at,
        "mix": dict(COMBINED_MIX),
        "method": {
            "catalog": "Phoenix 2 authoritative catalog",
            "overlapRule": "Phoenix 2 replaces Phoenix 1 for the same player and chart",
            "crossVersionNormalization": "version- and mode-specific Pumbility residuals converted to level units",
            "levelReference": "median measured chart residual within the exact mode and Phoenix 2 official level",
            "modeSeparation": "Singles and Doubles use independent baselines, calibration, and ranks",
            "difficultyDeltaScale": DIFFICULTY_DELTA_SCALE,
            "displayMinimumOfficialLevel": MIN_TARGET_LEVEL,
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
    return [
        {key: chart.get(key) for key in RECOMMENDATION_CHART_FIELDS}
        for chart in combined_charts
        if int(chart.get("level") or 0) >= MIN_TARGET_LEVEL
    ]


def build_manual_recommendation_mode(
    charts: Sequence[Mapping[str, Any]],
    chart_type: str,
    scoring_rating: float,
) -> dict[str, Any]:
    """Rank anonymous recommendations without inferring personal score gains."""
    candidates: list[dict[str, Any]] = []
    for chart in charts:
        if chart.get("type") != chart_type:
            continue
        level = float(chart.get("level", 0))
        estimate = float(chart.get("estimatedDifficulty", 0))
        if (
            level < MIN_TARGET_LEVEL
            or estimate > scoring_rating + RECOMMENDATION_RADIUS
        ):
            continue
        candidates.append(
            {
                **dict(chart),
                "distanceFromRating": round(estimate - scoring_rating, 6),
                "farmEdge": round(level + 0.5 - estimate, 6),
                "existingPumbility": None,
                "expectedPumbility": 0,
                "projectedGain": 0,
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
    candidates.sort(
        key=lambda row: (
            float(row["estimatedDifficulty"]),
            str(row.get("songName", "")).casefold(),
            str(row.get("chartId", "")),
        )
    )
    top_recommendations = sorted(
        candidates,
        key=lambda row: (
            -float(row["farmEdge"]),
            float(row["estimatedDifficulty"]),
            str(row.get("songName", "")).casefold(),
            str(row.get("chartId", "")),
        ),
    )[:TOP_RECOMMENDATION_COUNT]
    return {
        "eligible": True,
        "manual": True,
        "validScoreCount": 0,
        "scoringRating": round(scoring_rating, 3),
        "candidateRange": [
            None,
            round(scoring_rating + RECOMMENDATION_RADIUS, 3),
        ],
        "candidateCount": len(candidates),
        "projectionAvailable": False,
        "scoreProjectionModel": None,
        "candidates": candidates,
        "topRecommendations": top_recommendations,
    }


def _rating_bounds(score_count: int) -> tuple[int, int]:
    """Return the recommendation-only top-score window as zero-based bounds."""
    return 0, min(RECOMMENDATION_RATING_SCORE_COUNT, score_count)


def _rating_lookup(rows: pd.DataFrame) -> tuple[float | None, dict[str, float | None]]:
    """Return the full and leave-one-chart-out difficulty baselines in O(n)."""
    if rows.empty:
        return None, {}
    ordered = rows.sort_values(
        ["pumbility", "score", "chartId"],
        ascending=[False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    values = ordered["ratingDifficulty"].astype(float).to_numpy()
    prefix = np.concatenate(([0.0], np.cumsum(values)))

    def window_mean(count: int, removed: int | None = None) -> float | None:
        if count <= 0:
            return None
        start, end = _rating_bounds(count)
        if removed is None:
            return float((prefix[end] - prefix[start]) / (end - start))
        if removed < start:
            total = prefix[end + 1] - prefix[start + 1]
        elif removed >= end:
            total = prefix[end] - prefix[start]
        else:
            total = prefix[end + 1] - prefix[start] - values[removed]
        return float(total / (end - start))

    full = window_mean(len(ordered))
    leave_one_out = {
        str(chart_id): window_mean(len(ordered) - 1, index)
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
    if not phoenix1_scores.empty:
        return "phoenix1", phoenix1_scores
    return "phoenix2", phoenix2_scores


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

    # Each player has equal total influence within a mode.  A single prolific
    # player therefore cannot determine a region of the population surface.
    balanced = rows.copy()
    player_counts = balanced.groupby("playerId", sort=False)["chartId"].transform(
        "count"
    )
    balanced["modelWeight"] = 1.0 / player_counts.clip(lower=1).astype(float)
    p1_weight = float(
        balanced.loc[balanced["source"] == "phoenix1", "modelWeight"].sum()
    )
    p2_weight = float(
        balanced.loc[balanced["source"] == "phoenix2", "modelWeight"].sum()
    )
    if p1_weight > 0 and p2_weight > 0 and p1_weight > p2_weight:
        balanced.loc[balanced["source"] == "phoenix1", "modelWeight"] *= (
            p2_weight / p1_weight
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

    # Build the rating used by every outcome from chart difficulty only.  For a
    # target chart in the selected rating source, its chart is removed from the
    # rating window so the response cannot directly set its own predictor.
    def attach_rating_difficulty(scores: pd.DataFrame) -> pd.DataFrame:
        rows = scores.merge(
            catalog[["chartId", "type", "level"]],
            on="chartId",
            how="inner",
            validate="many_to_one",
        ).merge(
            estimates[["chartId", "estimatedDifficulty"]],
            on="chartId",
            how="left",
            validate="many_to_one",
        )
        rows["ratingDifficulty"] = rows["estimatedDifficulty"].where(
            rows["estimatedDifficulty"].notna(), rows["level"].astype(float) + 0.5
        )
        return rows

    rating_frames = {
        "phoenix1": attach_rating_difficulty(phoenix1_scores),
        "phoenix2": attach_rating_difficulty(phoenix2_scores),
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
    peer_top100_keys: set[tuple[str, str]] = set()
    all_player_modes = set(rating_groups["phoenix1"]) | set(
        rating_groups["phoenix2"]
    )
    for player_id, chart_type in all_player_modes:
        key = (player_id, chart_type)
        p2_group = rating_groups["phoenix2"].get(key, empty_rating_rows)
        p1_group = rating_groups["phoenix1"].get(key, empty_rating_rows)
        _, selected = _select_rating_scores(p1_group, p2_group)
        rating_lookups[(player_id, chart_type)] = _rating_lookup(selected)
        selected_top100 = selected.sort_values(
            ["pumbility", "score", "chartId"],
            ascending=[False, False, True],
            kind="mergesort",
        ).iloc[:PEER_SCORE_TOP_COUNT]
        peer_top100_keys.update(
            (player_id, str(chart_id))
            for chart_id in selected_top100["chartId"].astype(str)
        )

    ratings: list[float | None] = []
    for row in merged[["playerId", "type", "chartId"]].itertuples(index=False):
        full, leave_one_out = rating_lookups.get(
            (str(row.playerId), str(row.type)), (None, {})
        )
        ratings.append(leave_one_out.get(str(row.chartId), full))
    merged["scoringRating"] = pd.to_numeric(
        np.asarray(ratings, dtype=float), errors="coerce"
    )
    merged = merged[merged["scoringRating"].notna()].copy()
    merged["peerTop100"] = [
        (str(player_id), str(chart_id)) in peer_top100_keys
        for player_id, chart_id in merged[["playerId", "chartId"]].itertuples(
            index=False
        )
    ]

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
        peer_rows = calibrated_mode[calibrated_mode["peerTop100"]].copy()
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
            "peerTop100Rows": int(len(peer_rows)),
            "peerTop100Players": int(peer_rows["playerId"].nunique()),
            "peerTop100Charts": int(peer_rows["chartId"].nunique()),
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
        "minimumLocalSupport": SCORE_RESPONSE_MIN_SUPPORT,
        "supportNeighborhood": "plus or minus 0.5 rating and difficulty",
        "confidenceThresholds": {"high": 200, "medium": 50, "low": 5},
        "peerProjection": {
            "topPumbilityCount": PEER_SCORE_TOP_COUNT,
            "quantile": PEER_SCORE_QUANTILE,
            "minimumPeers": PEER_SCORE_MIN_SUPPORT,
            "initialRatingRadius": PEER_SCORE_INITIAL_RADIUS,
            "maximumRatingRadius": PEER_SCORE_MAX_RADIUS,
            "ratingRadiusStep": PEER_SCORE_RADIUS_STEP,
            "weightSigma": PEER_SCORE_WEIGHT_SIGMA,
            "confidenceThresholds": {"high": 20, "medium": 10, "low": 5},
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


def _rating_window(mode_scores: pd.DataFrame) -> tuple[pd.DataFrame, int, int, str]:
    """Return the highest-Pumbility rows used only for recommendations."""
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
    source_slopes = _source_level_slopes(charts, scores)
    return rebase_source_rows_to_catalog(
        charts,
        scores,
        phoenix2_catalog,
        source_slopes,
    )


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
            if estimate > scoring_rating + RECOMMENDATION_RADIUS:
                continue
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
            "candidateRange": [
                None,
                round(scoring_rating + RECOMMENDATION_RADIUS, 3),
            ],
            "candidateCount": len(candidates),
            "candidates": candidates,
            "topRecommendations": top,
        }
    return {"playerKey": public_player_key(player_id), "modes": modes}


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

    catalog_map = {
        str(row["chartId"]): row for row in catalog.to_dict(orient="records")
    }
    chart_map = {str(row["chartId"]): dict(row) for row in combined_charts}
    player_scores = scores[scores["playerId"] == str(player_id)].copy()
    player_phoenix1_scores = phoenix1_scores[
        phoenix1_scores["playerId"] == str(player_id)
    ].copy()
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
                    "At least one valid Phoenix 1 or Phoenix 2 score is required "
                    "in this mode."
                ),
                "projectionAvailable": False,
                "scoreProjectionModel": SCORE_PROJECTION_MODEL_NAME,
                **({"candidates": []} if include_candidates else {}),
                "topRecommendations": [],
            }
            continue

        rating_baseline, rating_start, rating_end, rating_label = _rating_window(
            rating_scores
        )
        rating_fallback_count = 0
        rating_difficulties: list[float] = []
        for row in rating_baseline.to_dict(orient="records"):
            chart_id = str(row["chartId"])
            catalog_chart = catalog_map[chart_id]
            estimate = chart_map.get(chart_id, {}).get("estimatedDifficulty")
            if estimate is None or not math.isfinite(float(estimate)):
                estimate = float(catalog_chart["level"]) + 0.5
                rating_fallback_count += 1
            rating_difficulties.append(float(estimate))
        scoring_rating = float(np.mean(rating_difficulties))

        if phoenix2_score_count:
            (
                projection_baseline,
                projection_start,
                projection_end,
                projection_label,
            ) = _baseline_window(mode_scores)
        else:
            projection_baseline = pd.DataFrame()
            projection_start, projection_end, projection_label = (
                rating_start,
                rating_end,
                rating_label,
            )
        baseline_pb = (
            float(projection_baseline["pumbility"].mean())
            if not projection_baseline.empty
            else None
        )
        slope = phoenix2_slopes.get(mode_key)

        current_values = [float(value) for value in mode_scores["pumbility"]]
        current_total = _top_total(current_values)
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
            if estimate > scoring_rating + RECOMMENDATION_RADIUS:
                continue
            farm_edge = float(chart["level"]) + 0.5 - estimate
            chart_id = str(chart["chartId"])
            existing = existing_by_chart.get(chart_id)
            projection = (
                score_response_model.predict(
                    str(player_id), mode_key, scoring_rating, estimate, chart_id
                )
                if score_response_model is not None
                else ScoreProjectionResult(None, "population-crossfit", 0, "unavailable")
            )
            projected_score = projection.score
            projected_grade = grade_for_score(projected_score)
            projected_plate: str | None = None
            projected_plate_probability: float | None = None
            plate_projection_source: str | None = None
            expected: float | None = None
            gain: float | None = None
            if projected_grade is not None:
                distribution = plate_model.distribution(
                    str(player_id), chart_type, projected_grade
                )
                projected_plate = distribution.most_likely
                projected_plate_probability = distribution.probabilities[projected_plate]
                plate_projection_source = distribution.source
                outcomes = [
                    (
                        probability,
                        phoenix2_pumbility(
                            chart_type,
                            int(chart["level"]),
                            projected_grade,
                            plate,
                        ),
                    )
                    for plate, probability in distribution.probabilities.items()
                ]
                expected = sum(probability * value for probability, value in outcomes)
                gain = sum(
                    probability
                    * _top50_marginal_gain(
                        value,
                        existing_pumbility=existing,
                        existing_in_top50=chart_id in top50_chart_ids,
                        current_score_count=len(mode_scores),
                        cutoff=top50_cutoff,
                    )
                    for probability, value in outcomes
                )
            candidates.append(
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
        candidates.sort(
            key=lambda row: (
                float(row["estimatedDifficulty"]),
                str(row["songName"]).casefold(),
                str(row["chartId"]),
            )
        )
        projection_available = any(
            row.get("projectedScore") is not None for row in candidates
        )
        if projection_available:
            top = sorted(
                candidates,
                key=_projected_gain_sort_key,
            )[:TOP_RECOMMENDATION_COUNT]
        else:
            top = sorted(
                candidates,
                key=lambda row: (
                    -float(row["farmEdge"]),
                    float(row["estimatedDifficulty"]),
                    str(row["songName"]).casefold(),
                    str(row["chartId"]),
                ),
            )[:TOP_RECOMMENDATION_COUNT]

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
            "ratingFallbackCharts": rating_fallback_count,
            "projectionAvailable": projection_available,
            "scoreProjectionModel": SCORE_PROJECTION_MODEL_NAME,
            "pumbilityPerLevel": (
                round(float(slope), 6) if slope is not None else None
            ),
            "currentTop50Pumbility": round(current_total, 3),
            "currentTop50CutoffPumbility": (
                round(top50_cutoff, 3) if top50_cutoff is not None else None
            ),
            "candidateRange": [
                None,
                round(scoring_rating + RECOMMENDATION_RADIUS, 3),
            ],
            "candidateCount": len(candidates),
            **({"candidates": candidates} if include_candidates else {}),
            "topRecommendations": top,
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
            include_candidates=False,
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
                    if mode in {"singles", "doubles"}
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
            "phoenix1RerateHandling": "Phoenix 1 Pumbility is shifted by its source slope times the Phoenix 2 minus Phoenix 1 level delta before ranking and normalization",
            "crossVersionNormalization": "Phoenix 1 scores rebased to Phoenix 2 levels, then version- and mode-specific Pumbility residuals converted to level units",
            "difficultyDeltaScale": DIFFICULTY_DELTA_SCALE,
            "pumbilityPerLevel": slopes,
            "scoreProjectionCoverage": score_projection_metadata,
            "scoreProjectionData": "matched Phoenix 1 + Phoenix 2 raw scores on the Phoenix 2 catalog, with Phoenix 2 precedence for overlapping player/chart rows",
            "baselineRanks": [BASELINE_START_RANK, BASELINE_END_RANK],
            "recommendationRatingRanks": [1, RECOMMENDATION_RATING_SCORE_COUNT],
            "phoenix2RatingScoreThreshold": PHOENIX2_RATING_SCORE_THRESHOLD,
            "ratingSource": "per mode, use Phoenix 2 at 10 valid scores; otherwise use Phoenix 1 when available, then available Phoenix 2 history",
            "shortHistoryBaseline": "within the selected rating source, use all available scores when fewer than 10 qualifying scores are available",
            "candidateUpperRadius": RECOMMENDATION_RADIUS,
            "candidateLowerBound": None,
            "topPumbilityCount": TOP_PUMBILITY_COUNT,
            "projection": "projected raw score converted with the official Phoenix 2 grade-and-plate Pumbility formula",
            "plateProjection": "hierarchical player, mode, and Phoenix 2 letter-grade distribution using Phoenix 2 observations plus a held-out-tuned capped Phoenix 1 prior and population smoothing",
            "phoenix1PlatePriorCap": plate_model.phoenix1_cap,
            "projectedGain": "probability-weighted change to the Phoenix 2 top-50 total; each plate outcome replaces the current chart PB and the number-50 chart only when it improves the retained top 50",
            "projectedGainTieBreak": "equal displayed projected gains are ordered by estimated difficulty from easiest to hardest, then expected Pumbility and chart name",
            "manualRanking": "farm edge at or below the requested scoring rating; no personal top-50 gain is inferred",
            "skillRatingCatalog": "all valid charts retained by the Phoenix 2 catalog, including levels below the display minimum",
            "currentStateSource": "Phoenix 2 only for played status, existing Pumbility, current top 50, and projected gain",
            "displayMinimumOfficialLevel": MIN_TARGET_LEVEL,
            "scoreProjection": "skill-distance-weighted median (50th-percentile) raw score from at least five other players of similar rating whose result on the exact chart ranked in their mode's top 100; the rating window expands from plus or minus 0.25 to 0.50 before falling back to the player-balanced population response surface",
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
        and int(chart.get("level") or 0) >= MIN_TARGET_LEVEL
    ]
    return payload
