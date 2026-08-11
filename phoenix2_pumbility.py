"""Phoenix 2 Pumbility projection formula and player plate projection.

The mode-specific formula is regression-tested against all 50 cards in an
official Pumbility-page screenshot, including its exact displayed total.
Letter grades are always recomputed from raw score so Phoenix 1 observations
use Phoenix 2 boundaries. Existing Phoenix 2 Pumbility remains authoritative.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Sequence

from phoenix1_score_overrides import convert_phoenix1_score


GRADE_BANDS: tuple[tuple[int, str, int], ...] = (
    (995_000, "SSS+", 0),
    (990_000, "SSS", 1),
    (985_000, "SS+", 2),
    (980_000, "SS", 3),
    (975_000, "S+", 4),
    (970_000, "S", 5),
    (960_000, "AAA+", 7),
    (950_000, "AAA", 9),
    (940_000, "AA+", 11),
    (920_000, "AA", 14),
    (900_000, "A+", 17),
    (800_000, "A", 22),
    (700_000, "B", 30),
    (600_000, "C", 40),
    (500_000, "D", 50),
    (0, "F", 60),
)
SINGLE_GRADE_PENALTY_UNITS = {grade: units for _, grade, units in GRADE_BANDS}
DOUBLE_GRADE_PENALTY_UNITS = {
    **SINGLE_GRADE_PENALTY_UNITS,
    "AA": 13,
    "A+": 15,
    "A": 20,
    "B": 25,
    "C": 30,
    "D": 40,
    "F": 50,
}
GRADE_PENALTY_UNITS_BY_TYPE = {
    "Single": SINGLE_GRADE_PENALTY_UNITS,
    "Double": DOUBLE_GRADE_PENALTY_UNITS,
}

SINGLE_PLATE_BONUS_UNITS: dict[str, float] = {
    "Rough Game": 0,
    "Fair Game": 1,
    "Talented Game": 2,
    "Marvelous Game": 3,
    "Superb Game": 4,
    "Extreme Game": 7,
    "Ultimate Game": 8.5,
    "Perfect Game": 10,
}
DOUBLE_PLATE_BONUS_UNITS: dict[str, float] = {
    **SINGLE_PLATE_BONUS_UNITS,
    "Extreme Game": 6,
    "Ultimate Game": 8,
}
PLATE_BONUS_UNITS_BY_TYPE = {
    "Single": SINGLE_PLATE_BONUS_UNITS,
    "Double": DOUBLE_PLATE_BONUS_UNITS,
}
PLATE_CODES = {
    "Rough Game": "RG",
    "Fair Game": "FG",
    "Talented Game": "TG",
    "Marvelous Game": "MG",
    "Superb Game": "SG",
    "Extreme Game": "EG",
    "Ultimate Game": "UG",
    "Perfect Game": "PG",
}
PLATE_ALIASES = {
    **{name.casefold(): name for name in SINGLE_PLATE_BONUS_UNITS},
    **{code.casefold(): name for name, code in PLATE_CODES.items()},
}
PLATES = tuple(SINGLE_PLATE_BONUS_UNITS)
SKILL_RATING_REFERENCE_GRADE = "S"
SKILL_RATING_REFERENCE_PLATE = "Fair Game"
SKILL_RATING_REFERENCE_MULTIPLIER = (
    750
    - 5 * SINGLE_GRADE_PENALTY_UNITS[SKILL_RATING_REFERENCE_GRADE]
    + SINGLE_PLATE_BONUS_UNITS[SKILL_RATING_REFERENCE_PLATE]
) / 750

POPULATION_PRIOR_STRENGTH = 8.0
PLATE_LAPLACE_WEIGHT = 0.25
PHOENIX1_CAP_CANDIDATES = (5, 10, 20, 40, 80)
DEFAULT_PHOENIX1_CAP = 20


def grade_for_score(score: object) -> str | None:
    """Return the Phoenix 2 letter grade for a finite raw score."""
    if isinstance(score, bool):
        return None
    try:
        value = int(float(score))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    if value < 0:
        return None
    value = min(value, 1_000_000)
    return next(grade for threshold, grade, _ in GRADE_BANDS if value >= threshold)


def normalize_plate(value: object) -> str | None:
    if value is None:
        return None
    return PLATE_ALIASES.get(str(value).strip().casefold())


@lru_cache(maxsize=None)
def phoenix2_pumbility(
    chart_type: str,
    level: int,
    grade: str,
    plate: str,
) -> float:
    """Calculate projected Phoenix 2 chart Pumbility, truncated to two decimals."""
    if chart_type not in {"Single", "Double"}:
        raise ValueError(f"Unsupported chart type: {chart_type!r}")
    grade_penalties = GRADE_PENALTY_UNITS_BY_TYPE[chart_type]
    plate_bonuses = PLATE_BONUS_UNITS_BY_TYPE[chart_type]
    normalized_grade = str(grade).strip().upper()
    if normalized_grade not in grade_penalties:
        raise ValueError(f"Unsupported Phoenix 2 grade: {grade!r}")
    normalized_plate = normalize_plate(plate)
    if normalized_plate is None:
        raise ValueError(f"Unsupported Phoenix 2 plate: {plate!r}")
    effective_level = int(level) - (1 if chart_type == "Double" else 0)
    base = (
        7.5 * (effective_level + 27)
        if effective_level <= 23
        else 375.0 + 15.0 * (effective_level - 23)
    )
    multiplier = (
        750
        - 5 * grade_penalties[normalized_grade]
        + plate_bonuses[normalized_plate]
    ) / 750
    raw = max(0.0, base * multiplier)
    return math.floor((raw + 1e-9) * 100) / 100


def skill_rating_for_pumbility(chart_type: str, pumbility: object) -> float:
    """Invert average Pumbility to the continuous level earning S with FG."""
    if chart_type not in {"Single", "Double"}:
        raise ValueError(f"Unsupported chart type: {chart_type!r}")
    if isinstance(pumbility, bool):
        raise ValueError("Pumbility must be a finite non-negative number.")
    try:
        value = float(pumbility)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Pumbility must be a finite non-negative number.") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError("Pumbility must be a finite non-negative number.")

    base = value / SKILL_RATING_REFERENCE_MULTIPLIER
    effective_level = (
        base / 7.5 - 27.0
        if base <= 375.0
        else 23.0 + (base - 375.0) / 15.0
    )
    return effective_level + (1.0 if chart_type == "Double" else 0.0)


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _snapshot_observations(
    snapshot: Mapping[str, Any],
    catalog_types: Mapping[str, str],
    *,
    phoenix1: bool = False,
) -> tuple[list[tuple[str, str, str, str, str]], set[tuple[str, str]]]:
    observations: list[tuple[str, str, str, str, str]] = []
    score_keys: set[tuple[str, str]] = set()
    for raw in snapshot.get("scores", []):
        if not isinstance(raw, Mapping) or bool(raw.get("isBroken", False)):
            continue
        player_id = str(raw.get("playerId") or "").strip()
        chart_id = str(raw.get("chartId") or "").strip()
        if not player_id or chart_id not in catalog_types:
            continue
        score_keys.add((player_id, chart_id))
        score = (
            convert_phoenix1_score(chart_id, raw.get("score"))
            if phoenix1
            else raw.get("score")
        )
        grade = grade_for_score(score)
        plate = normalize_plate(raw.get("plate"))
        if grade is None or plate is None:
            continue
        observations.append(
            (player_id, chart_id, catalog_types[chart_id], grade, plate)
        )
    return observations, score_keys


def _scaled_counts(counts: Counter[str], cap: int) -> dict[str, float]:
    total = sum(counts.values())
    if total <= 0 or cap <= 0:
        return {}
    scale = min(1.0, cap / total)
    return {plate: count * scale for plate, count in counts.items()}


def _weighted_median_plate(probabilities: Mapping[str, float]) -> str:
    """Return the lower ordered plate whose cumulative probability reaches 50%."""
    cumulative = 0.0
    for plate in PLATES:
        cumulative += float(probabilities.get(plate, 0.0))
        if cumulative + 1e-12 >= 0.5:
            return plate
    return PLATES[-1]


@dataclass(frozen=True)
class PlateDistribution:
    probabilities: dict[str, float]
    median_plate: str
    source: str


class PlateProjectionModel:
    """Hierarchical plate model using P2 history plus a capped P1 prior."""

    def __init__(
        self,
        phoenix1_snapshot: Mapping[str, Any],
        phoenix2_snapshot: Mapping[str, Any],
    ) -> None:
        catalog_types = {
            str(row.get("id")): str(row.get("type"))
            for row in phoenix2_snapshot.get("charts", [])
            if isinstance(row, Mapping)
            and str(row.get("type")) in {"Single", "Double"}
        }
        p1_rows, _ = _snapshot_observations(
            phoenix1_snapshot, catalog_types, phoenix1=True
        )
        p2_rows, p2_keys = _snapshot_observations(phoenix2_snapshot, catalog_types)
        p1_rows = [row for row in p1_rows if (row[0], row[1]) not in p2_keys]

        self._p1 = self._group_player_counts(p1_rows)
        self._p2 = self._group_player_counts(p2_rows)
        self._population: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        self._mode_population: dict[str, Counter[str]] = defaultdict(Counter)
        self._distribution_cache: dict[tuple[str, str, str], PlateDistribution] = {}
        for row in (*p1_rows, *p2_rows):
            _, _, mode, grade, plate = row
            self._population[(mode, grade)][plate] += 1
            self._mode_population[mode][plate] += 1
        self.phoenix1_cap = self._select_phoenix1_cap()

    @staticmethod
    def _group_player_counts(
        rows: Sequence[tuple[str, str, str, str, str]],
    ) -> dict[tuple[str, str, str], Counter[str]]:
        grouped: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
        for player_id, _, mode, grade, plate in rows:
            grouped[(player_id, mode, grade)][plate] += 1
        return grouped

    def global_payload(self) -> dict[str, Any]:
        """Serialize population-only state for lightweight per-player inference."""
        return {
            "phoenix1Cap": self.phoenix1_cap,
            "population": {
                f"{mode}\u001f{grade}": dict(counts)
                for (mode, grade), counts in self._population.items()
            },
            "modePopulation": {
                mode: dict(counts) for mode, counts in self._mode_population.items()
            },
        }

    @classmethod
    def from_global_payload(
        cls,
        payload: Mapping[str, Any],
        phoenix1_snapshot: Mapping[str, Any],
        phoenix2_snapshot: Mapping[str, Any],
        catalog_types: Mapping[str, str],
    ) -> "PlateProjectionModel":
        """Restore global priors while deriving only the selected player's counts."""
        p1_rows, _ = _snapshot_observations(
            phoenix1_snapshot, catalog_types, phoenix1=True
        )
        p2_rows, p2_keys = _snapshot_observations(phoenix2_snapshot, catalog_types)
        p1_rows = [row for row in p1_rows if (row[0], row[1]) not in p2_keys]
        model = cls.__new__(cls)
        model._p1 = cls._group_player_counts(p1_rows)
        model._p2 = cls._group_player_counts(p2_rows)
        model._population = defaultdict(Counter)
        raw_population = payload.get("population", {})
        if not isinstance(raw_population, Mapping):
            raise ValueError("The stored plate population is invalid.")
        for raw_key, raw_counts in raw_population.items():
            parts = str(raw_key).split("\u001f", 1)
            if len(parts) != 2 or not isinstance(raw_counts, Mapping):
                raise ValueError("The stored plate population is invalid.")
            model._population[(parts[0], parts[1])] = Counter(
                {str(plate): int(count) for plate, count in raw_counts.items()}
            )
        model._mode_population = defaultdict(Counter)
        raw_modes = payload.get("modePopulation", {})
        if not isinstance(raw_modes, Mapping):
            raise ValueError("The stored plate mode population is invalid.")
        for mode, raw_counts in raw_modes.items():
            if not isinstance(raw_counts, Mapping):
                raise ValueError("The stored plate mode population is invalid.")
            model._mode_population[str(mode)] = Counter(
                {str(plate): int(count) for plate, count in raw_counts.items()}
            )
        cap = int(payload.get("phoenix1Cap") or DEFAULT_PHOENIX1_CAP)
        if cap not in PHOENIX1_CAP_CANDIDATES:
            raise ValueError("The stored Phoenix 1 plate-prior cap is invalid.")
        model.phoenix1_cap = cap
        model._distribution_cache = {}
        return model

    def _population_probabilities(self, mode: str, grade: str) -> dict[str, float]:
        counts = self._population.get((mode, grade)) or self._mode_population.get(mode)
        counts = counts or Counter()
        denominator = sum(counts.values()) + PLATE_LAPLACE_WEIGHT * len(PLATES)
        return {
            plate: (counts.get(plate, 0) + PLATE_LAPLACE_WEIGHT) / denominator
            for plate in PLATES
        }

    def _select_phoenix1_cap(self) -> int:
        if not self._p2:
            return DEFAULT_PHOENIX1_CAP
        best = (math.inf, DEFAULT_PHOENIX1_CAP)
        for cap in PHOENIX1_CAP_CANDIDATES:
            loss = 0.0
            observations = 0
            for key, targets in self._p2.items():
                _, mode, grade = key
                prior = self._population_probabilities(mode, grade)
                p1 = _scaled_counts(self._p1.get(key, Counter()), cap)
                denominator = POPULATION_PRIOR_STRENGTH + sum(p1.values())
                for plate, count in targets.items():
                    probability = (
                        POPULATION_PRIOR_STRENGTH * prior[plate]
                        + p1.get(plate, 0.0)
                    ) / denominator
                    loss -= count * math.log(max(probability, 1e-12))
                    observations += count
            score = loss / observations if observations else math.inf
            best = min(best, (score, cap))
        return best[1]

    def distribution(self, player_id: str, mode: str, grade: str) -> PlateDistribution:
        key = (str(player_id), mode, grade)
        cached = self._distribution_cache.get(key)
        if cached is not None:
            return cached
        population = self._population_probabilities(mode, grade)
        p1 = _scaled_counts(self._p1.get(key, Counter()), self.phoenix1_cap)
        p2 = self._p2.get(key, Counter())
        denominator = POPULATION_PRIOR_STRENGTH + sum(p1.values()) + sum(p2.values())
        probabilities = {
            plate: (
                POPULATION_PRIOR_STRENGTH * population[plate]
                + p1.get(plate, 0.0)
                + p2.get(plate, 0)
            ) / denominator
            for plate in PLATES
        }
        median_plate = _weighted_median_plate(probabilities)
        source = "phoenix2" if sum(p2.values()) else "phoenix1" if p1 else "population"
        result = PlateDistribution(probabilities, median_plate, source)
        self._distribution_cache[key] = result
        return result
