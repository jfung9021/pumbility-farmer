#!/usr/bin/env python3
"""
PIU Phoenix player-normalized chart scoring-difficulty analyzer.

Singles and Doubles are analyzed as completely independent populations:
  1. Rank each player's valid best scores within one mode by Pumbility.
  2. Define that mode's player baseline as the mean of ranks 11 through 30.
  3. Retain only ranks 1 through 100 from that player and mode.
  4. For every level-20+ chart in that set, compute the signed residual from the
     player's mode-specific baseline.
  5. Compare every chart only with measured charts in its exact mode and
     official level, using that folder's median residual as the reference.
  6. Calibrate residual Pumbility into continuous level units and anchor the
     typical official level L chart at L + 0.5. Negative differences are easier
     within that folder and positive differences are harder.

The script deliberately does NOT consume PIU Scores' existing scoring-level or tier-list fields.
It uses only player best scores, the API-computed Pumbility value for each score,
and the chart catalog for names/modes/official levels.

Authentication
--------------
Set the tool key only in an environment variable. It is never written to output or logs:

  export PIU_SCORES_API_KEY='piu_scores_live_...'
  python piu_misgrade_analyzer.py live --output-dir ./piu_run

A tool key can read only players who explicitly shared with that community tool.

Offline validation
------------------

  python piu_misgrade_analyzer.py synthetic --output-dir ./synthetic_demo

Dependencies: Python 3.12+, requests, pandas, numpy.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import random
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urljoin, urlparse

import numpy as np
import pandas as pd
import requests

from mix_registry import DEFAULT_MIX_KEY, resolve_mix


DEFAULT_BASE_URL = "https://piuscores.arroweclip.se/"
MIN_TARGET_LEVEL = 20
MODE_TYPES = ("Single", "Double")
MODE_LABELS = {"Single": "Singles", "Double": "Doubles"}
SYNTHETIC_FOLDERS = (
    "S20", "S21", "S22", "S23", "D20", "D21", "D22", "D23", "D24"
)
RELATIVE_GROUPS = tuple(
    ["Easiest 10%"]
    + [f"{start}–{start + 10}% percentile" for start in range(10, 90, 10)]
    + ["Hardest 10%"]
)
EFFECT_BANDS = (
    (1, "Extremely Easy", None, -0.75),
    (2, "Very Easy", -0.75, -0.5),
    (3, "Easy", -0.5, -0.25),
    (4, "Slightly Easy", -0.25, -0.1),
    (5, "Typical", -0.1, 0.1),
    (6, "Slightly Hard", 0.1, 0.25),
    (7, "Hard", 0.25, 0.5),
    (8, "Very Hard", 0.5, 0.75),
    (9, "Extremely Hard", 0.75, None),
)
DEFAULT_EMPIRICAL_SHRINKAGE_K = 5.0
CALIBRATION_SCORE_BIN = 2_500
CALIBRATION_MIN_SCORE = 900_000
DIFFICULTY_DELTA_SCALE = 0.4
SYNTHETIC_PUMBILITY_PER_LEVEL = 7.3
KEY_RE = re.compile(r"^(?:piu_scores_live_|pst_live_)[0-9a-f]{64}$")
SCRIPT_VERSION = "5.6.0-nine-bands-and-0.4-scale"


class ApiError(RuntimeError):
    """A safe API failure that never contains the credential."""


@dataclass(frozen=True)
class AnalysisConfig:
    mix: str = DEFAULT_MIX_KEY
    baseline_start_rank: int = 11
    baseline_end_rank: int = 30
    contribution_fraction: float = 0.20
    min_contributors: int = 5
    published_contributors: int = 10
    # None estimates the prior strength from the current mode's chart residuals.
    shrinkage_k: float | None = None
    # Primarily for controlled fixtures. Production runs estimate this from scores.
    pumbility_per_level: float | None = None
    bootstrap_samples: int = 500
    random_seed: int = 20260807

    @property
    def minimum_scores_per_player(self) -> int:
        return self.baseline_end_rank


class SharedRequestLimiter:
    """Thread-safe request-start pacing with a shared rate-limit backoff."""

    def __init__(
        self,
        interval_seconds: float = 0.125,
        *,
        monotonic: Any = time.monotonic,
        sleeper: Any = time.sleep,
    ) -> None:
        self.interval_seconds = max(0.0, float(interval_seconds))
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._next_start = 0.0
        self._blocked_until = 0.0

    def wait(self) -> None:
        while True:
            with self._lock:
                now = float(self._monotonic())
                ready_at = max(self._next_start, self._blocked_until)
                delay = ready_at - now
                if delay <= 0:
                    self._next_start = now + self.interval_seconds
                    return
            self._sleeper(delay)

    def block_for(self, delay_seconds: float) -> None:
        with self._lock:
            self._blocked_until = max(
                self._blocked_until,
                float(self._monotonic()) + max(0.0, float(delay_seconds)),
            )


class PiuScoresClient:
    """Minimal, conservative API v2 client with opaque-cursor paging."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 30.0,
        max_retries: int = 5,
        request_start_interval_seconds: float = 0.125,
        limiter: SharedRequestLimiter | None = None,
    ) -> None:
        if not KEY_RE.fullmatch(api_key.strip()):
            raise ApiError(
                "The API key does not match the expected PIU Scores tool-key shape. "
                "Use a piu_scores_live_... key via PIU_SCORES_API_KEY."
            )
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.limiter = limiter or SharedRequestLimiter(request_start_interval_seconds)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key.strip()}",
                "Accept": "application/json",
                "User-Agent": f"piu-misgrade-analyzer/{SCRIPT_VERSION}",
            }
        )
        self._allowed_netloc = urlparse(self.base_url).netloc
        self.request_count = 0
        self._request_count_lock = threading.Lock()

    @staticmethod
    def _retry_after_seconds(value: str | None, attempt: int) -> float:
        fallback = min(60.0, float(2**attempt))
        if not value:
            return fallback
        try:
            return max(1.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(1.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return fallback

    def _safe_url(self, url: str) -> str:
        full = urljoin(self.base_url, url)
        parsed = urlparse(full)
        if parsed.scheme != "https" or parsed.netloc != self._allowed_netloc:
            raise ApiError("The API returned a paging URL outside the configured PIU Scores host.")
        return full

    def _get_json(self, url: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        full_url = self._safe_url(url)
        last_message = "unknown error"
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                response = self.session.get(
                    full_url,
                    params=params if attempt == 0 else None,
                    timeout=self.timeout_seconds,
                )
                with self._request_count_lock:
                    self.request_count += 1
            except requests.RequestException as exc:
                last_message = exc.__class__.__name__
                if attempt >= self.max_retries:
                    raise ApiError(
                        "Could not reach the PIU Scores API. The credential was not printed or saved. "
                        f"Network error type: {last_message}."
                    ) from None
                time.sleep(min(8.0, 0.75 * (2**attempt)))
                continue

            if response.status_code == 429:
                delay = self._retry_after_seconds(response.headers.get("Retry-After"), attempt)
                if attempt >= self.max_retries:
                    raise ApiError("PIU Scores rate limit persisted after retries (HTTP 429).")
                self.limiter.block_for(delay)
                continue

            if response.status_code in (401, 403):
                raise ApiError(
                    f"PIU Scores rejected the credential or access grant (HTTP {response.status_code}). "
                    "The key may be invalid/expired, or no relevant player share may exist."
                )
            if response.status_code >= 500:
                if attempt >= self.max_retries:
                    raise ApiError(f"PIU Scores returned HTTP {response.status_code} after retries.")
                time.sleep(min(12.0, 0.75 * (2**attempt)))
                continue
            if response.status_code >= 400:
                detail = ""
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        detail = str(body.get("detail") or body.get("title") or "")
                except ValueError:
                    detail = ""
                suffix = f" Detail: {detail[:300]}" if detail else ""
                raise ApiError(f"PIU Scores returned HTTP {response.status_code}.{suffix}")

            try:
                payload = response.json()
            except ValueError:
                raise ApiError("PIU Scores returned a non-JSON response.") from None
            if not isinstance(payload, dict):
                raise ApiError("PIU Scores returned an unexpected JSON shape.")
            return payload

        raise ApiError(f"API request failed: {last_message}")

    def fetch_page_collection(
        self,
        initial_path: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Follow the API's `next` value exactly until null."""
        all_rows: list[dict[str, Any]] = []
        next_url: str | None = initial_path
        first = True
        seen: set[str] = set()
        while next_url:
            full = self._safe_url(next_url)
            canonical = full + ("?" + requests.compat.urlencode(params, doseq=True) if first and params else "")
            if canonical in seen:
                raise ApiError("The API returned a repeated cursor; paging was stopped to avoid a loop.")
            seen.add(canonical)
            payload = self._get_json(next_url, params=params if first else None)
            first = False
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise ApiError("Expected a paginated response containing a data array.")
            for row in rows:
                if isinstance(row, dict):
                    all_rows.append(row)
            raw_next = payload.get("next")
            if raw_next is not None and not isinstance(raw_next, str):
                raise ApiError("The API returned an invalid next cursor URL.")
            next_url = raw_next
        return all_rows


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _write_jsonl_gz(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on {path.name} line {line_number}: {exc}") from exc
            if isinstance(row, dict):
                rows.append(row)
    return rows


def pull_live_snapshot(
    client: PiuScoresClient,
    raw_dir: Path,
    mix: str = "Phoenix2",
    progress_every: int = 10,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Pull consented players, one mix's chart catalog, and all their best scores."""
    mix_spec = resolve_mix(mix)
    mix = mix_spec.api_value
    mix_label = mix_spec.label
    _mkdir_private(raw_dir)
    print("Reading players who shared with this tool...", flush=True)
    players_full = client.fetch_page_collection("api/v2/players", {"limit": 100})
    players = [
        {
            "userId": p.get("userId"),
            "isPublic": p.get("isPublic"),
        }
        for p in players_full
        if p.get("userId")
    ]
    if not players:
        raise ApiError(
            "The tool key returned zero readable players. A tool key only sees players who explicitly "
            "shared their PIU Scores data with that tool."
        )

    print(f"Reading the {mix_label} chart catalog...", flush=True)
    charts = client.fetch_page_collection(
        "api/v2/charts", {"mix": mix, "limit": 100}
    )
    if not charts:
        raise ApiError(f"The {mix_label} chart catalog was empty.")

    all_scores: list[dict[str, Any]] = []
    total_players = len(players)
    for index, player in enumerate(players, start=1):
        player_id = str(player["userId"])
        rows = client.fetch_page_collection(
            f"api/v2/players/{player_id}/scores",
            {"mix": mix, "limit": 100},
        )
        for row in rows:
            normalized = dict(row)
            normalized["playerId"] = player_id
            all_scores.append(normalized)
        if index == 1 or index == total_players or index % max(1, progress_every) == 0:
            print(
                f"  players {index:,}/{total_players:,}; best-score rows {len(all_scores):,}",
                flush=True,
            )

    _write_json(raw_dir / "players.json", players)
    _write_json(raw_dir / "charts.json", charts)
    _write_jsonl_gz(raw_dir / "scores.jsonl.gz", all_scores)
    _write_json(
        raw_dir / "snapshot_manifest.json",
        {
            "scriptVersion": SCRIPT_VERSION,
            "pulledAtUtc": datetime.now(timezone.utc).isoformat(),
            "baseUrl": client.base_url,
            "mix": mix,
            "players": len(players),
            "charts": len(charts),
            "scoreRows": len(all_scores),
            "httpRequests": client.request_count,
            "credentialStored": False,
            "note": "Player names/game tags were intentionally omitted from the cache.",
        },
    )
    if not all_scores:
        player_word = "player" if total_players == 1 else "players"
        raise ApiError(
            f"PIU Scores returned {total_players:,} shared {player_word}, but none exposed any "
            f"{mix_label} best-score rows to this credential. The analyzer needs individual score "
            f"rows to calculate each player's ranks 11-30 baseline. Import {mix_label} scores for at "
            "least one account, then share that account's score data with this exact community "
            "tool (or use that account's personal API token), and retry. A diagnostic snapshot "
            f"manifest was written to {raw_dir.resolve()}."
        )
    return players, charts, all_scores


def load_snapshot(raw_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    players_path = raw_dir / "players.json"
    charts_path = raw_dir / "charts.json"
    scores_path = raw_dir / "scores.jsonl.gz"
    missing = [str(p) for p in (players_path, charts_path, scores_path) if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing cached snapshot files: " + ", ".join(missing))
    players = _read_json(players_path)
    charts = _read_json(charts_path)
    scores = _read_jsonl_gz(scores_path)
    if not isinstance(players, list) or not isinstance(charts, list):
        raise ValueError("Cached players.json and charts.json must contain arrays.")
    return players, charts, scores


def folder_for(chart_type: str, level: int) -> str | None:
    if level < MIN_TARGET_LEVEL:
        return None
    if chart_type == "Single":
        return f"S{level}"
    if chart_type == "Double":
        return f"D{level}"
    return None


def _robust_location(values: np.ndarray) -> float:
    """Return a Huber-style chart location while preserving small samples."""
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return math.nan
    if values.size < 3:
        return float(values.mean())
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad <= 0 or not math.isfinite(mad):
        return float(values.mean())
    limit = 2.5 * 1.4826 * mad
    return float(np.clip(values, median - limit, median + limit).mean())


def _bootstrap_location_ci(
    values: np.ndarray,
    samples: int,
    rng: np.random.Generator,
) -> tuple[float, float]:
    """Bootstrap distinct player contributions within one chart."""
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return (math.nan, math.nan)
    if values.size == 1 or samples <= 0:
        value = _robust_location(values)
        return (value, value)
    locations = np.empty(samples, dtype=float)
    n = values.size
    for i in range(samples):
        locations[i] = _robust_location(values[rng.integers(0, n, size=n)])
    low, high = np.quantile(locations, [0.025, 0.975])
    return float(low), float(high)


def relative_difficulty_group(level_percentile: float) -> tuple[int, str]:
    """Return a decile group from a chart's midpoint percentile in its folder."""
    if not math.isfinite(level_percentile) or not 0.0 <= level_percentile <= 1.0:
        raise ValueError("A relative-difficulty group requires a percentile from 0 to 1.")
    rank = min(10, max(1, int(math.floor(level_percentile * 10.0)) + 1))
    return rank, RELATIVE_GROUPS[rank - 1]


def difficulty_effect_band(delta: float) -> tuple[int, str]:
    """Classify a level-unit effect without forcing a quota within each folder."""
    if not math.isfinite(delta):
        raise ValueError("A difficulty effect band requires a finite delta.")
    if delta <= -0.75:
        return (1, "Extremely Easy")
    if delta <= -0.5:
        return (2, "Very Easy")
    if delta <= -0.25:
        return (3, "Easy")
    if delta <= -0.1:
        return (4, "Slightly Easy")
    if delta < 0.1:
        return (5, "Typical")
    if delta < 0.25:
        return (6, "Slightly Hard")
    if delta < 0.5:
        return (7, "Hard")
    if delta < 0.75:
        return (8, "Very Hard")
    return (9, "Extremely Hard")


def _fit_level_calibration(
    mode_scores: pd.DataFrame,
    override: float | None = None,
) -> tuple[float, dict[str, Any]]:
    """Estimate Pumbility per level within player and narrow score bands."""
    if override is not None:
        slope = float(override)
        if not math.isfinite(slope) or slope <= 0:
            raise ValueError("Configured Pumbility-per-level must be positive and finite.")
        return slope, {
            "method": "configured override",
            "slope": slope,
            "observations": 0,
            "scoreBinWidth": CALIBRATION_SCORE_BIN,
        }

    required = {"playerId", "score", "level", "pumbility"}
    missing = required - set(mode_scores.columns)
    if missing:
        raise ValueError(f"Calibration data is missing fields: {sorted(missing)}")
    measured = mode_scores[
        mode_scores["score"].notna()
        & (mode_scores["score"] >= CALIBRATION_MIN_SCORE)
        & mode_scores["level"].notna()
        & mode_scores["pumbility"].notna()
        & (mode_scores["pumbility"] > 0)
    ].copy()
    measured["scoreBin"] = (
        np.floor(measured["score"].astype(float) / CALIBRATION_SCORE_BIN).astype(int)
    )
    keys = ["playerId", "scoreBin"]
    comparable = measured.groupby(keys, sort=False)["level"].transform("nunique") >= 2
    measured = measured[comparable].copy()
    if len(measured) < 30 or measured["level"].nunique() < 3:
        raise ValueError(
            "Pumbility-per-level calibration needs at least 30 comparable positive scores "
            "covering three levels within player/score bands."
        )
    x = measured["level"].astype(float) - measured.groupby(keys, sort=False)[
        "level"
    ].transform("mean")
    y = measured["pumbility"].astype(float) - measured.groupby(keys, sort=False)[
        "pumbility"
    ].transform("mean")
    denominator = float(np.dot(x, x))
    slope = float(np.dot(x, y) / denominator) if denominator > 0 else math.nan
    if not math.isfinite(slope) or slope <= 0:
        raise ValueError(
            f"Pumbility-per-level calibration must be positive and finite; got {slope:.3f}."
        )
    return slope, {
        "method": "within-player fixed effects and 2,500-point score bands",
        "slope": slope,
        "observations": int(len(measured)),
        "players": int(measured["playerId"].nunique()),
        "levels": int(measured["level"].nunique()),
        "scoreBinWidth": CALIBRATION_SCORE_BIN,
        "minimumScore": CALIBRATION_MIN_SCORE,
        "validation": "positive finite empirical slope",
    }


def _estimate_shrinkage_k(result: pd.DataFrame) -> tuple[float, dict[str, Any]]:
    """Estimate empirical-Bayes prior strength from mode-wide chart variance."""
    required = {"folder", "chartResidualPb", "residualStdPb", "nContributors"}
    if not required.issubset(result.columns):
        return DEFAULT_EMPIRICAL_SHRINKAGE_K, {
            "method": "versioned fallback for insufficient variance data",
            "k": DEFAULT_EMPIRICAL_SHRINKAGE_K,
        }
    measured = result[
        result["chartResidualPb"].notna() & (result["nContributors"] > 0)
    ].copy()
    repeated = measured[
        (measured["nContributors"] > 1) & measured["residualStdPb"].notna()
    ].copy()
    if len(measured) < 10 or len(repeated) < 3:
        return DEFAULT_EMPIRICAL_SHRINKAGE_K, {
            "method": "versioned fallback for insufficient variance data",
            "k": DEFAULT_EMPIRICAL_SHRINKAGE_K,
        }
    folder_center = measured.groupby("folder", sort=False)["chartResidualPb"].transform(
        "median"
    )
    centered = measured["chartResidualPb"] - folder_center
    observed_variance = float(centered.var(ddof=1))
    degrees = repeated["nContributors"] - 1
    within_variance = float(
        np.average(repeated["residualStdPb"].astype(float) ** 2, weights=degrees)
    )
    mean_noise = float(
        np.mean(
            repeated["residualStdPb"].astype(float) ** 2
            / repeated["nContributors"].astype(float)
        )
    )
    between_variance = max(observed_variance - mean_noise, 1e-6)
    raw_k = within_variance / between_variance
    if not math.isfinite(raw_k) or raw_k <= 0:
        raw_k = DEFAULT_EMPIRICAL_SHRINKAGE_K
    k = float(np.clip(raw_k, 0.5, 20.0))
    return k, {
        "method": "mode-wide empirical Bayes variance ratio",
        "k": k,
        "rawK": float(raw_k),
        "withinChartVariancePb": within_variance,
        "betweenChartVariancePb": between_variance,
        "charts": int(len(measured)),
        "chartsWithRepeatedContributors": int(len(repeated)),
    }


def _apply_chart_ranks_and_groups(result: pd.DataFrame) -> pd.DataFrame:
    result = result.copy()
    result["modeRank"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result["levelRank"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result["levelPercentile"] = np.nan
    result["levelComparisonCharts"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result["relativeGroupRank"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result["relativeGroup"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["effectBandRank"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result["effectBand"] = pd.Series(pd.NA, index=result.index, dtype="string")
    measured = result[result["difficultyDelta"].notna()].sort_values(
        ["difficultyDelta", "nContributors", "songName", "chartId"],
        ascending=[True, False, True, True],
        kind="mergesort",
    )
    if not measured.empty:
        result.loc[measured.index, "modeRank"] = pd.array(
            np.arange(1, len(measured) + 1), dtype="Int64"
        )
    for _, group in measured.groupby("folder", sort=False):
        count = len(group)
        result.loc[group.index, "levelRank"] = pd.array(
            np.arange(1, count + 1), dtype="Int64"
        )
        result.loc[group.index, "levelComparisonCharts"] = count
        tied_ranks = group["difficultyDelta"].rank(method="average", ascending=True)
        percentiles = (tied_ranks - 0.5) / count
        result.loc[group.index, "levelPercentile"] = percentiles
        for row_index, percentile in percentiles.items():
            group_rank, group_name = relative_difficulty_group(float(percentile))
            result.at[row_index, "relativeGroupRank"] = group_rank
            result.at[row_index, "relativeGroup"] = group_name
    for row_index, delta in result.loc[measured.index, "difficultyDelta"].items():
        band_rank, band_name = difficulty_effect_band(float(delta))
        result.at[row_index, "effectBandRank"] = band_rank
        result.at[row_index, "effectBand"] = band_name
    return result


def apply_within_level_difficulty(
    result: pd.DataFrame,
    pumbility_per_level: float,
    config: AnalysisConfig,
) -> pd.DataFrame:
    """Center estimates and tier categories within each exact mode-level folder."""
    result = result.copy()
    has_measurements = result.get("chartResidualPb", result["meanResidualPb"]).notna().any()
    if has_measurements and (
        not math.isfinite(pumbility_per_level) or pumbility_per_level <= 0
    ):
        raise ValueError("Pumbility-per-level calibration must be positive and finite.")

    chart_location = (
        result["chartResidualPb"]
        if "chartResidualPb" in result.columns
        else result["meanResidualPb"]
    )

    # The median represents a typical chart without allowing a sparse outlier to
    # shift the whole folder. Every reference is computed within the current mode.
    result["levelReferenceResidualPb"] = chart_location.groupby(
        result["folder"], sort=False
    ).transform("median")
    # Retain the established output field as a compatibility alias.
    result["expectedResidualPb"] = result["levelReferenceResidualPb"]
    result["rawEasePb"] = (
        chart_location - result["levelReferenceResidualPb"]
    )
    if config.shrinkage_k is None:
        shrinkage_k, _ = _estimate_shrinkage_k(result)
    else:
        shrinkage_k = float(config.shrinkage_k)
    weight = result["nContributors"] / (result["nContributors"] + shrinkage_k)
    result["shrinkageK"] = shrinkage_k
    result["reliabilityWeight"] = weight
    result["shrunkEasePb"] = np.where(
        chart_location.notna(), weight * result["rawEasePb"], np.nan
    )
    result["pumbilityPerLevel"] = pumbility_per_level
    result["averageDifficulty"] = result["level"].astype(float) + 0.5
    result["difficultyDelta"] = (
        -DIFFICULTY_DELTA_SCALE * result["shrunkEasePb"] / pumbility_per_level
    )
    result["estimatedDifficulty"] = (
        result["averageDifficulty"] + result["difficultyDelta"]
    )
    delta_ci_low = -DIFFICULTY_DELTA_SCALE * weight * (
        result["residualCi95HighPb"] - result["levelReferenceResidualPb"]
    ) / pumbility_per_level
    delta_ci_high = -DIFFICULTY_DELTA_SCALE * weight * (
        result["residualCi95LowPb"] - result["levelReferenceResidualPb"]
    ) / pumbility_per_level
    result["difficultyCi95Low"] = result["averageDifficulty"] + delta_ci_low
    result["difficultyCi95High"] = result["averageDifficulty"] + delta_ci_high
    result["difficultyDeltaCi95Low"] = delta_ci_low
    result["difficultyDeltaCi95High"] = delta_ci_high
    return _apply_chart_ranks_and_groups(result)


def analyze_snapshot(
    players: Sequence[Mapping[str, Any]],
    charts: Sequence[Mapping[str, Any]],
    scores: Sequence[Mapping[str, Any]],
    config: AnalysisConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], pd.DataFrame]:
    """Return chart results, mode-specific baselines, summary, and selected contributions."""
    if not math.isfinite(config.contribution_fraction) or not 0 < config.contribution_fraction <= 1:
        raise ValueError("Contribution fraction must be greater than 0 and at most 1.")
    chart_df = pd.DataFrame(charts)
    score_df = pd.DataFrame(scores)
    if chart_df.empty:
        raise ValueError("No chart rows were supplied.")
    if score_df.empty:
        raise ValueError("No score rows were supplied.")

    required_chart = {"id", "songName", "type", "level"}
    missing_chart = required_chart - set(chart_df.columns)
    if missing_chart:
        raise ValueError(f"Chart data is missing fields: {sorted(missing_chart)}")
    required_score = {"playerId", "chartId", "pumbility", "isBroken"}
    missing_score = required_score - set(score_df.columns)
    if missing_score:
        raise ValueError(f"Score data is missing fields: {sorted(missing_score)}")

    chart_df = chart_df.rename(columns={"id": "chartId"}).copy()
    chart_df["chartId"] = chart_df["chartId"].astype(str)
    chart_df["level"] = pd.to_numeric(chart_df["level"], errors="coerce").astype("Int64")
    chart_df["folder"] = [
        folder_for(str(t), int(l)) if pd.notna(l) else None
        for t, l in zip(chart_df["type"], chart_df["level"])
    ]

    score_df = score_df.copy()
    score_df["playerId"] = score_df["playerId"].astype(str)
    score_df["chartId"] = score_df["chartId"].astype(str)
    score_df["pumbility"] = pd.to_numeric(score_df["pumbility"], errors="coerce")
    if "score" not in score_df.columns:
        score_df["score"] = np.nan
    score_df["score"] = pd.to_numeric(score_df["score"], errors="coerce")
    score_df["isBroken"] = score_df["isBroken"].fillna(False).astype(bool)
    if "recordedAt" not in score_df.columns:
        score_df["recordedAt"] = ""

    merged = score_df.merge(
        chart_df[["chartId", "songName", "type", "level", "difficulty", "folder"]
                 if "difficulty" in chart_df.columns
                 else ["chartId", "songName", "type", "level", "folder"]],
        on="chartId",
        how="inner",
        validate="many_to_one",
    )
    if "difficulty" not in chart_df.columns:
        chart_df["difficulty"] = np.where(
            chart_df["type"].eq("Single"), "S" + chart_df["level"].astype(str),
            np.where(chart_df["type"].eq("Double"), "D" + chart_df["level"].astype(str), ""),
        )
    if "difficulty" not in merged.columns:
        merged = merged.merge(chart_df[["chartId", "difficulty"]], on="chartId", how="left")

    nonpositive_pumbility_rows = int(
        (
            merged["pumbility"].notna()
            & (merged["pumbility"] <= 0)
            & (~merged["isBroken"])
            & merged["type"].isin(MODE_TYPES)
        ).sum()
    )
    valid = merged[
        merged["pumbility"].notna()
        & (merged["pumbility"] > 0)
        & (~merged["isBroken"])
        & merged["type"].isin(MODE_TYPES)
    ].copy()
    if valid.empty:
        mix_label = resolve_mix(config.mix).label
        raise ValueError(
            f"No valid Single/Double {mix_label} scores with positive Pumbility were found."
        )

    # API is already best-per-chart. This deterministic guard protects cached/manual inputs.
    valid = valid.sort_values(
        ["playerId", "chartId", "pumbility", "score", "recordedAt"],
        ascending=[True, True, False, False, False],
        kind="mergesort",
    ).drop_duplicates(["playerId", "chartId"], keep="first")

    target_catalog = chart_df[chart_df["folder"].notna()].copy()
    if target_catalog.empty:
        raise ValueError("The chart catalog contained no Single or Double charts at level 20 or above.")

    rng = np.random.default_rng(config.random_seed)
    mode_results: list[pd.DataFrame] = []
    baseline_frames: list[pd.DataFrame] = []
    contribution_frames: list[pd.DataFrame] = []
    mode_diagnostics: dict[str, dict[str, Any]] = {}
    eligible_valid_rows = 0
    eligible_target_rows = 0
    fallback_player_mode_pairs = 0

    for chart_type in MODE_TYPES:
        mode_name = MODE_LABELS[chart_type]
        mode_valid = valid[valid["type"] == chart_type].copy()
        mode_valid = mode_valid.sort_values(
            ["playerId", "pumbility", "score", "chartId"],
            ascending=[True, False, False, True],
            kind="mergesort",
        )
        counts = mode_valid.groupby("playerId").size().rename("validScoreCount")
        mode_valid["playerRank"] = mode_valid.groupby("playerId", sort=False).cumcount() + 1
        mode_valid["recordedAtTimestamp"] = pd.to_datetime(
            mode_valid["recordedAt"], errors="coerce", utc=True
        )
        mode_valid["recencyRank"] = pd.Series(
            pd.NA, index=mode_valid.index, dtype="Int64"
        )
        dated = mode_valid[mode_valid["recordedAtTimestamp"].notna()].sort_values(
            ["playerId", "recordedAtTimestamp", "pumbility", "score", "chartId"],
            ascending=[True, False, False, False, True],
            kind="mergesort",
        )
        mode_valid.loc[dated.index, "recencyRank"] = pd.array(
            dated.groupby("playerId", sort=False).cumcount() + 1,
            dtype="Int64",
        )
        baseline_slice = mode_valid[
            mode_valid["playerRank"].between(
                config.baseline_start_rank, config.baseline_end_rank
            )
        ]
        baselines = baseline_slice.groupby("playerId", sort=False)["pumbility"].agg(
            baselinePumbility="mean",
            baselineStd="std",
            baselineMin="min",
            baselineMax="max",
            baselineCount="count",
        )
        baselines = baselines.join(counts, how="left")
        required_baseline_count = config.baseline_end_rank - config.baseline_start_rank + 1
        baselines = baselines[
            (baselines["baselineCount"] == required_baseline_count)
            & (baselines["validScoreCount"] >= config.minimum_scores_per_player)
            & baselines["baselinePumbility"].notna()
            & (baselines["baselinePumbility"] > 0)
        ].copy()
        eligible_ids = baselines.index
        eligible_valid = mode_valid[mode_valid["playerId"].isin(eligible_ids)].copy()
        eligible_valid["validScoreCount"] = (
            eligible_valid["playerId"].map(counts).astype(int)
        )
        eligible_valid["contributionRankLimit"] = np.ceil(
            eligible_valid["validScoreCount"] * config.contribution_fraction
        ).astype(int)
        eligible_valid["selectedByPumbility"] = (
            eligible_valid["playerRank"] <= eligible_valid["contributionRankLimit"]
        )
        eligible_valid["selectedByRecency"] = (
            eligible_valid["recencyRank"].notna()
            & (eligible_valid["recencyRank"] <= eligible_valid["contributionRankLimit"])
        )
        window_selected = (
            eligible_valid["selectedByPumbility"] | eligible_valid["selectedByRecency"]
        )
        eligible_valid["windowUnionCount"] = window_selected.groupby(
            eligible_valid["playerId"], sort=False
        ).transform("sum").astype(int)
        eligible_valid["usesTop100Fallback"] = eligible_valid["windowUnionCount"] < 100
        eligible_valid["selectedByTop100Fallback"] = (
            eligible_valid["usesTop100Fallback"] & (eligible_valid["playerRank"] <= 100)
        )
        eligible_valid["selectedForContribution"] = np.where(
            eligible_valid["usesTop100Fallback"],
            eligible_valid["selectedByTop100Fallback"],
            window_selected,
        )
        fallback_player_mode_pairs += int(
            eligible_valid.loc[eligible_valid["usesTop100Fallback"], "playerId"].nunique()
        )
        eligible_valid_rows += len(eligible_valid)
        eligible_target_rows += int(eligible_valid["folder"].notna().sum())

        baseline_export = baselines.reset_index().copy()
        baseline_export["mode"] = mode_name
        baseline_export["playerHash"] = baseline_export["playerId"].map(
            lambda pid: hashlib.sha256(str(pid).encode("utf-8")).hexdigest()[:16]
        )
        baseline_export = baseline_export.drop(columns=["playerId"])
        baseline_frames.append(baseline_export)

        contributing = eligible_valid[
            eligible_valid["selectedForContribution"]
        ].copy()
        contributing = contributing.join(baselines[["baselinePumbility"]], on="playerId")
        contributing["residualPb"] = (
            contributing["pumbility"] - contributing["baselinePumbility"]
        )
        target_contributions = contributing[contributing["folder"].notna()].copy()

        if target_contributions.empty:
            slope = float(config.pumbility_per_level or math.nan)
            calibration_diagnostics: dict[str, Any] = {
                "method": "unavailable: no qualifying target observations",
                "slope": None,
                "observations": 0,
            }
        else:
            slope, calibration_diagnostics = _fit_level_calibration(
                mode_valid, config.pumbility_per_level
            )

        if not target_contributions.empty:
            contribution_export = target_contributions[
                ["playerId", "chartId", "folder", "songName", "difficulty", "playerRank",
                 "recencyRank", "validScoreCount", "contributionRankLimit",
                 "windowUnionCount", "usesTop100Fallback", "selectedByPumbility",
                 "selectedByRecency", "selectedByTop100Fallback", "recordedAt", "pumbility",
                 "baselinePumbility", "residualPb"]
            ].copy()
            contribution_export["mode"] = mode_name
            contribution_export["playerHash"] = contribution_export["playerId"].map(
                lambda pid: hashlib.sha256(str(pid).encode("utf-8")).hexdigest()[:16]
            )
            contribution_frames.append(contribution_export.drop(columns=["playerId"]))

        scored_counts = (
            eligible_valid[eligible_valid["folder"].notna()]
            .groupby("chartId")["playerId"]
            .nunique()
            .rename("nPlayersScored")
        )
        stat_rows: list[dict[str, Any]] = []
        for chart_id, group in target_contributions.groupby("chartId", sort=False):
            residual = group["residualPb"].to_numpy(dtype=float)
            ci_low, ci_high = _bootstrap_location_ci(
                residual, config.bootstrap_samples, rng
            )
            stat_rows.append(
                {
                    "chartId": str(chart_id),
                    "nContributors": int(group["playerId"].nunique()),
                    "meanResidualPb": float(np.mean(residual)),
                    "chartResidualPb": _robust_location(residual),
                    "medianResidualPb": float(np.median(residual)),
                    "residualStdPb": float(np.std(residual, ddof=1)) if len(residual) > 1 else 0.0,
                    "residualCi95LowPb": ci_low,
                    "residualCi95HighPb": ci_high,
                    "meanContributorBaselinePb": float(group["baselinePumbility"].mean()),
                }
            )
        stats = pd.DataFrame(stat_rows)
        if stats.empty:
            stats = pd.DataFrame(columns=[
                "chartId", "nContributors", "meanResidualPb", "chartResidualPb", "medianResidualPb",
                "residualStdPb", "residualCi95LowPb", "residualCi95HighPb",
                "meanContributorBaselinePb",
            ])

        mode_catalog = target_catalog[target_catalog["type"] == chart_type].copy()
        if mode_catalog.empty:
            continue
        result = mode_catalog.merge(stats, on="chartId", how="left")
        result = result.merge(scored_counts, on="chartId", how="left")
        result["mode"] = mode_name
        result["nContributors"] = result["nContributors"].fillna(0).astype(int)
        result["nPlayersScored"] = result["nPlayersScored"].fillna(0).astype(int)
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
        if config.shrinkage_k is None:
            _, shrinkage_diagnostics = _estimate_shrinkage_k(result)
        else:
            shrinkage_diagnostics = {
                "method": "configured override",
                "k": float(config.shrinkage_k),
            }
        result = apply_within_level_difficulty(result, slope, config)
        mode_diagnostics[mode_name.lower()] = {
            "calibration": calibration_diagnostics,
            "shrinkage": shrinkage_diagnostics,
        }
        mode_results.append(result)

    if not any(not frame.empty for frame in baseline_frames):
        raise ValueError(
            f"No player had at least {config.minimum_scores_per_player} valid scores in either "
            "Singles or Doubles, so separate ranks 11-30 baselines cannot be defined."
        )

    result = pd.concat(mode_results, ignore_index=True)
    output_columns = [
        "mode", "modeRank", "levelRank", "levelPercentile", "levelComparisonCharts",
        "folder", "relativeGroupRank", "relativeGroup", "effectBandRank", "effectBand",
        "songName", "difficulty", "type", "level", "chartId", "imageUrl", "noteCount",
        "stepArtist", "estimatedDifficulty", "averageDifficulty", "difficultyDelta",
        "difficultyDeltaCi95Low", "difficultyDeltaCi95High",
        "difficultyCi95Low", "difficultyCi95High", "pumbilityPerLevel", "rawEasePb",
        "shrunkEasePb", "meanResidualPb", "medianResidualPb", "residualStdPb",
        "chartResidualPb", "shrinkageK",
        "residualCi95LowPb", "residualCi95HighPb", "levelReferenceResidualPb",
        "expectedResidualPb",
        "nContributors", "nPlayersScored", "contributionAppearanceRate", "reliabilityWeight",
        "meanContributorBaselinePb", "evidenceStatus",
    ]
    for col in output_columns:
        if col not in result.columns:
            result[col] = pd.NA
    result = result[output_columns].sort_values(
        ["mode", "modeRank", "folder", "songName", "chartId"],
        na_position="last",
        kind="mergesort",
    )

    baseline_out = pd.concat(baseline_frames, ignore_index=True)
    baseline_columns = [
        "mode", "playerHash", "validScoreCount", "baselinePumbility", "baselineStd",
        "baselineMin", "baselineMax", "baselineCount",
    ]
    baseline_out = baseline_out[baseline_columns].sort_values(["mode", "playerHash"])
    contribution_out = (
        pd.concat(contribution_frames, ignore_index=True)
        if contribution_frames
        else pd.DataFrame(columns=[
            "chartId", "folder", "songName", "difficulty", "playerRank",
            "recencyRank", "validScoreCount", "contributionRankLimit",
            "windowUnionCount", "usesTop100Fallback", "selectedByPumbility",
            "selectedByRecency", "selectedByTop100Fallback", "recordedAt", "pumbility",
            "baselinePumbility", "residualPb", "mode", "playerHash",
        ])
    )

    measured = result[result["difficultyDelta"].notna()]
    summary = {
        "scriptVersion": SCRIPT_VERSION,
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "mix": resolve_mix(config.mix).as_payload(),
        "method": {
            "baselineRanks": [config.baseline_start_rank, config.baseline_end_rank],
            "contributionSelection": {
                "method": "deduplicated union of Pumbility and recency windows",
                "windows": ["top Pumbility", "most recent recordedAt"],
                "fractionPerWindow": config.contribution_fraction,
                "rankLimitRounding": "ceiling",
                "missingTimestampRule": "eligible by Pumbility only",
                "fallback": "top 100 by Pumbility when the deduplicated union has fewer than 100 scores",
            },
            "chartMetric": "signed player-normalized Pumbility residual",
            "validScoreRule": "finite, non-broken, strictly positive Pumbility",
            "levelReference": "median measured chart residual within the exact mode and official level",
            "difficultyDelta": "-0.4 * shrunkEasePb / pumbilityPerLevel",
            "difficultyDeltaScale": DIFFICULTY_DELTA_SCALE,
            "negativeDeltaMeaning": "easier to score than the typical chart at that exact mode and level",
            "relativeGrouping": "midpoint-percentile deciles, labeled only as relative percentiles",
            "effectBands": "nine fixed level-unit thresholds; extreme means |difficultyDelta| >= 0.75",
            "modeSeparation": "Singles and Doubles use independent eligibility, baselines, calibration, and ranks",
            "usesExistingPiuScoresTierList": False,
            "shrinkage": "mode-wide empirical-Bayes variance ratio" if config.shrinkage_k is None else "configured override",
            "bootstrapSamples": config.bootstrap_samples,
            "bootstrapUnit": "distinct contributing players within each chart",
        },
        "coverage": {
            "playersReturnedByCredential": len(players),
            "nonpositivePumbilityRowsExcluded": nonpositive_pumbility_rows,
            "eligiblePlayerModePairs": int(len(baseline_out)),
            "validBestScoreRowsAmongEligiblePlayerModes": int(eligible_valid_rows),
            "positiveTargetRowsAmongEligiblePlayerModes": int(eligible_target_rows),
            "targetSelectedContributions": int(len(contribution_out)),
            "playerModePairsUsingTop100Fallback": fallback_player_mode_pairs,
            "positiveTargetRowsExcludedByContributionWindow": int(
                eligible_target_rows - len(contribution_out)
            ),
            "targetCatalogCharts": int(len(result)),
            "targetChartsMeasured": int(len(measured)),
            "targetChartsPublished": int((result["evidenceStatus"] == "Published").sum()),
            "targetChartsProvisional": int((result["evidenceStatus"] == "Provisional").sum()),
            "targetChartsInsufficient": int((result["evidenceStatus"] == "Insufficient").sum()),
            "targetChartsUnrated": int((result["evidenceStatus"] == "Unrated").sum()),
        },
        "modes": {},
    }
    for mode_name in MODE_LABELS.values():
        subset = result[result["mode"] == mode_name]
        mode_baselines = baseline_out[baseline_out["mode"] == mode_name]
        measured_subset = subset[subset["difficultyDelta"].notna()]
        folders: dict[str, Any] = {}
        for folder in sorted(subset["folder"].dropna().unique(), key=lambda value: int(value[1:])):
            folder_subset = subset[subset["folder"] == folder]
            contributors = folder_subset.loc[folder_subset["nContributors"] > 0, "nContributors"]
            folders[str(folder)] = {
                "catalogCharts": int(len(folder_subset)),
                "measuredCharts": int(folder_subset["difficultyDelta"].notna().sum()),
                "publishedCharts": int((folder_subset["evidenceStatus"] == "Published").sum()),
                "medianContributors": float(contributors.median()) if not contributors.empty else None,
                "extremelyEasyCharts": int((folder_subset["effectBandRank"] == 1).sum()),
                "extremelyHardCharts": int((folder_subset["effectBandRank"] == 9).sum()),
            }
        summary["modes"][mode_name.lower()] = {
            "eligiblePlayers": int(len(mode_baselines)),
            "catalogCharts": int(len(subset)),
            "measuredCharts": int(len(measured_subset)),
            "publishedCharts": int((subset["evidenceStatus"] == "Published").sum()),
            "pumbilityPerLevel": float(measured_subset["pumbilityPerLevel"].iloc[0])
            if not measured_subset.empty else None,
            "calibration": mode_diagnostics.get(mode_name.lower(), {}).get("calibration", {}),
            "shrinkage": mode_diagnostics.get(mode_name.lower(), {}).get("shrinkage", {}),
            "folders": folders,
        }

    return result, baseline_out, summary, contribution_out


def build_web_payload(
    chart_results: pd.DataFrame,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Convert analysis frames into a JSON-safe contract for the web UI."""
    records = json.loads(chart_results.to_json(orient="records", double_precision=6))
    return {
        "generatedAtUtc": summary.get("generatedAtUtc"),
        "mix": dict(summary.get("mix") or resolve_mix().as_payload()),
        "summary": dict(summary),
        "singles": [row for row in records if row.get("mode") == "Singles"],
        "doubles": [row for row in records if row.get("mode") == "Doubles"],
        "relativeGroups": [
            {"rank": rank, "name": name}
            for rank, name in enumerate(RELATIVE_GROUPS, start=1)
        ],
        "effectBands": [
            {"rank": rank, "name": name, "low": low, "high": high}
            for rank, name, low, high in EFFECT_BANDS
        ],
    }


def export_analysis(
    output_dir: Path,
    chart_results: pd.DataFrame,
    player_baselines: pd.DataFrame,
    summary: Mapping[str, Any],
    contributions: pd.DataFrame,
    include_contributions: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    chart_results.to_csv(output_dir / "chart_tiers.csv", index=False, float_format="%.6f")
    player_baselines.to_csv(output_dir / "player_baselines_pseudonymous.csv", index=False, float_format="%.6f")
    _write_json(output_dir / "analysis_summary.json", summary)
    _write_json(output_dir / "web_results.json", build_web_payload(chart_results, summary))
    if include_contributions:
        contributions.to_csv(
            output_dir / "selected_contributions_pseudonymous.csv",
            index=False,
            float_format="%.6f",
        )

    for mode_name in MODE_LABELS.values():
        subset = chart_results[chart_results["mode"] == mode_name]
        subset.to_csv(
            output_dir / f"{mode_name.lower()}_rankings.csv",
            index=False,
            float_format="%.6f",
        )

    # One easy-to-read CSV per official folder.
    folder_dir = output_dir / "folders"
    folder_dir.mkdir(exist_ok=True)
    folders = sorted(
        chart_results["folder"].dropna().unique(),
        key=lambda value: (value[0], int(value[1:])),
    )
    for folder in folders:
        subset = chart_results[chart_results["folder"] == folder]
        subset.to_csv(folder_dir / f"{folder.lower()}_tiers.csv", index=False, float_format="%.6f")


def make_synthetic_snapshot(
    seed: int = 20260807,
    players_per_folder: int = 80,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, float]]:
    """Construct a fixture with independent mode skill and known chart ease signals."""
    rng = np.random.default_rng(seed)
    charts: list[dict[str, Any]] = []
    scores: list[dict[str, Any]] = []
    players: list[dict[str, Any]] = []
    true_signal: dict[str, float] = {}

    background_charts: dict[str, list[dict[str, Any]]] = {"Single": [], "Double": []}
    for chart_type in MODE_TYPES:
        prefix = "S" if chart_type == "Single" else "D"
        for i in range(40):
            level = 16 + (i % 4)
            chart_id = f"background-{prefix.lower()}-{i:02d}"
            row = {
                "id": chart_id,
                "mix": "Phoenix2",
                "originalMix": "Phoenix2",
                "songName": f"Synthetic {prefix} Background {i + 1:02d}",
                "type": chart_type,
                "level": level,
                "difficulty": f"{prefix}{level}",
                "imageUrl": None,
                "noteCount": 500 + i,
                "playerCount": 1,
                "stepArtist": "Synthetic",
                "scoringLevel": None,
            }
            charts.append(row)
            background_charts[chart_type].append(row)

    # The signal spans a known two-level range around each folder's median.
    residual_signals = np.linspace(
        SYNTHETIC_PUMBILITY_PER_LEVEL,
        -SYNTHETIC_PUMBILITY_PER_LEVEL,
        10,
    )
    target_charts: dict[str, list[dict[str, Any]]] = {}
    for folder in SYNTHETIC_FOLDERS:
        chart_type = "Single" if folder.startswith("S") else "Double"
        level = int(folder[1:])
        rows: list[dict[str, Any]] = []
        for index, signal in enumerate(residual_signals):
            chart_id = f"{folder.lower()}-{index:02d}"
            row = {
                "id": chart_id,
                "mix": "Phoenix2",
                "originalMix": "Phoenix2",
                "songName": f"Synthetic {folder} Chart {chr(65 + index)}",
                "type": chart_type,
                "level": level,
                "difficulty": folder,
                "imageUrl": None,
                "noteCount": 850 + level * 10 + index,
                "playerCount": 1,
                "stepArtist": "Synthetic",
                "scoringLevel": None,
            }
            charts.append(row)
            rows.append(row)
            true_signal[chart_id] = float(signal)
        target_charts[folder] = rows

    for folder_index, folder in enumerate(SYNTHETIC_FOLDERS):
        for player_index in range(players_per_folder):
            player_id = f"synthetic-player-{folder_index:02d}-{player_index:04d}"
            players.append({"userId": player_id, "isPublic": False})
            base = float(rng.normal(345.0, 8.0))
            chart_type = "Single" if folder.startswith("S") else "Double"
            level = int(folder[1:])
            level_bonus = SYNTHETIC_PUMBILITY_PER_LEVEL * (level - MIN_TARGET_LEVEL)

            # Ten targets occupy the top ten. Their signed signals have a known order.
            for chart_index, chart in enumerate(target_charts[folder]):
                pumbility = (
                    base + level_bonus + 30.0 + residual_signals[chart_index]
                    + float(rng.normal(0.0, 0.65))
                )
                scores.append(
                    {
                        "playerId": player_id,
                        "chartId": chart["id"],
                        "recordedAt": "2026-08-07T00:00:00Z",
                        "source": "synthetic",
                        "score": int(970000 + rng.integers(0, 5000)),
                        "letterGrade": "S",
                        "plate": "Rough Game",
                        "isBroken": False,
                        "pumbility": round(pumbility, 2),
                    }
                )

            # Twenty stable same-mode scores become ranks 11-30.
            for bg in background_charts[chart_type][:20]:
                background_level = int(bg["level"])
                pumbility = (
                    base
                    + SYNTHETIC_PUMBILITY_PER_LEVEL
                    * (background_level - MIN_TARGET_LEVEL)
                    + float(rng.normal(0.0, 0.45))
                )
                scores.append(
                    {
                        "playerId": player_id,
                        "chartId": bg["id"],
                        "recordedAt": "2026-08-07T00:00:00Z",
                        "source": "synthetic",
                        "score": int(960000 + rng.integers(0, 10000)),
                        "letterGrade": "AAA+",
                        "plate": "Rough Game",
                        "isBroken": False,
                        "pumbility": round(pumbility, 2),
                    }
                )

            # Twenty lower same-mode scores exercise the contribution-window cutoff path.
            for bg in background_charts[chart_type][20:]:
                background_level = int(bg["level"])
                pumbility = (
                    base
                    + SYNTHETIC_PUMBILITY_PER_LEVEL
                    * (background_level - MIN_TARGET_LEVEL)
                    - 9.0
                    - abs(float(rng.normal(0.0, 1.2)))
                )
                scores.append(
                    {
                        "playerId": player_id,
                        "chartId": bg["id"],
                        "recordedAt": "2026-08-07T00:00:00Z",
                        "source": "synthetic",
                        "score": int(950000 + rng.integers(0, 10000)),
                        "letterGrade": "AAA",
                        "plate": "Rough Game",
                        "isBroken": False,
                        "pumbility": round(pumbility, 2),
                    }
                )

    return players, charts, scores, true_signal


def validate_synthetic(
    chart_results: pd.DataFrame,
    true_signal: Mapping[str, float],
) -> dict[str, Any]:
    measured = chart_results[chart_results["chartId"].isin(true_signal)].copy()
    measured["trueResidualSignalPb"] = measured["chartId"].map(true_signal)
    folder_results: dict[str, Any] = {}
    all_ok = True
    for folder in SYNTHETIC_FOLDERS:
        group = measured[measured["folder"] == folder].copy()
        if len(group) != 10:
            folder_results[folder] = {"passed": False, "reason": f"expected 10 charts, found {len(group)}"}
            all_ok = False
            continue
        true_ranks = group["trueResidualSignalPb"].rank(method="average")
        measured_ranks = group["difficultyDelta"].rank(method="average")
        correlation = float(true_ranks.corr(measured_ranks))
        easiest = group.sort_values("difficultyDelta", ascending=True).iloc[0]
        hardest = group.sort_values("difficultyDelta", ascending=False).iloc[0]
        expected_easiest = group.sort_values("trueResidualSignalPb", ascending=False).iloc[0]
        expected_hardest = group.sort_values("trueResidualSignalPb", ascending=True).iloc[0]
        passed = (correlation <= -0.98) and (
            easiest["chartId"] == expected_easiest["chartId"]
            and hardest["chartId"] == expected_hardest["chartId"]
            and float(easiest["difficultyDelta"]) <= -0.25
            and float(hardest["difficultyDelta"]) >= 0.25
            and int(easiest["effectBandRank"]) <= 3
            and int(hardest["effectBandRank"]) >= 7
        )
        if folder == "S20":
            passed = passed and float(easiest["estimatedDifficulty"]) < 20.5
        all_ok = all_ok and passed
        folder_results[folder] = {
            "passed": bool(passed),
            "spearmanCorrelation": correlation,
            "easiestChart": str(easiest["songName"]),
            "easiestGroup": str(easiest["relativeGroup"]),
            "easiestEffectBand": str(easiest["effectBand"]),
            "easiestEstimatedDifficulty": float(easiest["estimatedDifficulty"]),
            "hardestChart": str(hardest["songName"]),
            "hardestGroup": str(hardest["relativeGroup"]),
            "hardestEffectBand": str(hardest["effectBand"]),
        }
    return {"passed": bool(all_ok), "folders": folder_results}


def print_result_summary(summary: Mapping[str, Any], output_dir: Path) -> None:
    coverage = summary.get("coverage", {})
    print("\nAnalysis complete", flush=True)
    modes = summary.get("modes", {})
    print(f"  eligible Single players: {modes.get('singles', {}).get('eligiblePlayers', 0):,}")
    print(f"  eligible Double players: {modes.get('doubles', {}).get('eligiblePlayers', 0):,}")
    print(
        "  target selected contributions: "
        f"{coverage.get('targetSelectedContributions', 0):,}"
    )
    print(f"  target charts measured: {coverage.get('targetChartsMeasured', 0):,}")
    print(f"  output: {output_dir.resolve()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate separate Single/Double Phoenix scoring-difficulty rankings.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_analysis_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--output-dir", type=Path, required=True)
        p.add_argument(
            "--mix",
            default=DEFAULT_MIX_KEY,
            help="Supported mix key: phoenix1 or phoenix2.",
        )
        p.add_argument("--min-contributors", type=int, default=5)
        p.add_argument("--published-contributors", type=int, default=10)
        p.add_argument(
            "--shrinkage-k",
            type=float,
            default=None,
            help="Override empirical shrinkage with a fixed prior contributor count.",
        )
        p.add_argument(
            "--pumbility-per-level",
            type=float,
            default=None,
            help="Override score-controlled per-mode calibration (primarily for fixtures).",
        )
        p.add_argument(
            "--contribution-fraction",
            type=float,
            default=0.20,
            help=(
                "Fraction used for each of the top-Pumbility and most-recent "
                "per-player mode windows before deduplication."
            ),
        )
        p.add_argument("--bootstrap-samples", type=int, default=500)
        p.add_argument("--random-seed", type=int, default=20260807)
        p.add_argument(
            "--include-contributions",
            action="store_true",
            help="Write pseudonymous player-chart contribution rows for audit/debugging.",
        )

    live = sub.add_parser("live", help="Pull a live consented-player snapshot and analyze it.")
    add_analysis_options(live)
    live.add_argument("--base-url", default=DEFAULT_BASE_URL)
    live.add_argument("--api-key-env", default="PIU_SCORES_API_KEY")
    live.add_argument("--timeout-seconds", type=float, default=30.0)
    live.add_argument("--max-retries", type=int, default=5)
    live.add_argument("--throttle-seconds", type=float, default=0.12)

    cache = sub.add_parser("cache", help="Analyze an existing raw snapshot without network access.")
    add_analysis_options(cache)
    cache.add_argument("--raw-dir", type=Path, required=True)

    synthetic = sub.add_parser("synthetic", help="Run a controlled offline validation fixture.")
    add_analysis_options(synthetic)
    synthetic.add_argument("--players-per-folder", type=int, default=80)

    return parser


def config_from_args(args: argparse.Namespace) -> AnalysisConfig:
    mix_spec = resolve_mix(args.mix)
    if args.min_contributors < 1:
        raise ValueError("--min-contributors must be at least 1")
    if args.published_contributors < args.min_contributors:
        raise ValueError("--published-contributors must be >= --min-contributors")
    if args.shrinkage_k is not None and args.shrinkage_k < 0:
        raise ValueError("--shrinkage-k must be nonnegative")
    if args.pumbility_per_level is not None and args.pumbility_per_level <= 0:
        raise ValueError("--pumbility-per-level must be positive")
    if not math.isfinite(args.contribution_fraction) or not 0 < args.contribution_fraction <= 1:
        raise ValueError("--contribution-fraction must be greater than 0 and at most 1")
    if args.bootstrap_samples < 0:
        raise ValueError("--bootstrap-samples must be nonnegative")
    return AnalysisConfig(
        mix=mix_spec.key,
        min_contributors=args.min_contributors,
        published_contributors=args.published_contributors,
        shrinkage_k=args.shrinkage_k,
        pumbility_per_level=args.pumbility_per_level,
        contribution_fraction=args.contribution_fraction,
        bootstrap_samples=args.bootstrap_samples,
        random_seed=args.random_seed,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = config_from_args(args)
        output_dir: Path = args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        if args.command == "live":
            mix = resolve_mix(config.mix).api_value
            api_key = os.environ.get(args.api_key_env, "").strip()
            if not api_key:
                raise ApiError(
                    f"Environment variable {args.api_key_env} is empty. "
                    "Do not put the key on the command line or in source code."
                )
            client = PiuScoresClient(
                api_key=api_key,
                base_url=args.base_url,
                timeout_seconds=args.timeout_seconds,
                max_retries=args.max_retries,
                throttle_seconds=args.throttle_seconds,
            )
            players, charts, scores = pull_live_snapshot(client, output_dir / "raw", mix=mix)
            true_signal = None
        elif args.command == "cache":
            manifest_path = args.raw_dir / "snapshot_manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(manifest, Mapping) and manifest.get("mix"):
                    manifest_mix = resolve_mix(manifest.get("mix"))
                    requested_mix = resolve_mix(config.mix)
                    if manifest_mix.key != requested_mix.key:
                        raise ValueError(
                            f"Snapshot mix {manifest_mix.label} does not match requested mix "
                            f"{requested_mix.label}."
                        )
            players, charts, scores = load_snapshot(args.raw_dir)
            true_signal = None
        elif args.command == "synthetic":
            players, charts, scores, true_signal = make_synthetic_snapshot(
                seed=args.random_seed,
                players_per_folder=args.players_per_folder,
            )
            raw_dir = output_dir / "synthetic_raw"
            _mkdir_private(raw_dir)
            _write_json(raw_dir / "players.json", players)
            _write_json(raw_dir / "charts.json", charts)
            _write_jsonl_gz(raw_dir / "scores.jsonl.gz", scores)
        else:
            parser.error("Unknown command")
            return 2

        chart_results, baselines, summary, contributions = analyze_snapshot(
            players, charts, scores, config
        )
        export_analysis(
            output_dir,
            chart_results,
            baselines,
            summary,
            contributions,
            include_contributions=args.include_contributions,
        )

        if true_signal is not None:
            validation = validate_synthetic(chart_results, true_signal)
            _write_json(output_dir / "synthetic_validation.json", validation)
            if not validation["passed"]:
                raise RuntimeError("Synthetic validation did not recover the known ordering.")
            print("Synthetic mode-separated validation passed for all nine folders.", flush=True)

        print_result_summary(summary, output_dir)
        return 0
    except (ApiError, FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
