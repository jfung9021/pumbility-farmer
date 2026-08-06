#!/usr/bin/env python3
"""
PIU Phoenix 2 player-normalized chart misgrade analyzer.

Implements the requested exploratory metric:
  1. For each player, sort all valid Phoenix 2 best scores by per-score Pumbility.
  2. Define the player's baseline as the mean Pumbility of ranks 11 through 30.
  3. For each score in ranks 1 through 10, compute:
         residual = score_pumbility - baseline
         squared_residual = residual ** 2
  4. For each chart, average squared_residual across contributing players.
     Larger values are interpreted as easier / more favorably misgraded.
  5. Within each official folder, rank charts into ten equal-count bands:
         .0 = easiest, .9 = hardest.

The script deliberately does NOT consume PIU Scores' existing scoring-level or tier-list fields.
It uses only player best scores, the API-computed Phoenix 2 Pumbility value for each score,
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

Dependencies: Python 3.10+, requests, pandas, numpy, scipy.
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
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urljoin, urlparse

import numpy as np
import pandas as pd
import requests


DEFAULT_BASE_URL = "https://piuscores.arroweclip.se/"
TARGET_SINGLE_LEVELS = (20, 21, 22, 23)
TARGET_DOUBLE_LEVELS = (20, 21, 22, 23, 24)
TARGET_FOLDERS = tuple([f"S{x}" for x in TARGET_SINGLE_LEVELS] + [f"D{x}" for x in TARGET_DOUBLE_LEVELS])
KEY_RE = re.compile(r"^(?:piu_scores_live_|pst_live_)[0-9a-f]{64}$")
SCRIPT_VERSION = "1.0.2-exploratory"


class ApiError(RuntimeError):
    """A safe API failure that never contains the credential."""


@dataclass(frozen=True)
class AnalysisConfig:
    baseline_start_rank: int = 11
    baseline_end_rank: int = 30
    top_end_rank: int = 10
    min_contributors: int = 5
    published_contributors: int = 10
    shrinkage_k: float = 10.0
    bootstrap_samples: int = 500
    random_seed: int = 20260807

    @property
    def minimum_scores_per_player(self) -> int:
        return self.baseline_end_rank


class PiuScoresClient:
    """Minimal, conservative API v2 client with opaque-cursor paging."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 30.0,
        max_retries: int = 5,
        throttle_seconds: float = 0.12,
    ) -> None:
        if not KEY_RE.fullmatch(api_key.strip()):
            raise ApiError(
                "The API key does not match the expected PIU Scores tool-key shape. "
                "Use a piu_scores_live_... key via PIU_SCORES_API_KEY."
            )
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.throttle_seconds = max(0.0, throttle_seconds)
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
            try:
                response = self.session.get(
                    full_url,
                    params=params if attempt == 0 else None,
                    timeout=self.timeout_seconds,
                )
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
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = max(1.0, float(retry_after)) if retry_after else min(60.0, 2**attempt)
                except ValueError:
                    delay = min(60.0, 2**attempt)
                if attempt >= self.max_retries:
                    raise ApiError("PIU Scores rate limit persisted after retries (HTTP 429).")
                time.sleep(delay)
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
            if self.throttle_seconds:
                time.sleep(self.throttle_seconds)
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
    mix_label = "Phoenix 2" if mix == "Phoenix2" else mix
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
    if chart_type == "Single" and level in TARGET_SINGLE_LEVELS:
        return f"S{level}"
    if chart_type == "Double" and level in TARGET_DOUBLE_LEVELS:
        return f"D{level}"
    return None


def _bootstrap_mean_ci(values: np.ndarray, samples: int, rng: np.random.Generator) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return (math.nan, math.nan)
    if values.size == 1 or samples <= 0:
        value = float(values.mean())
        return (value, value)
    means = np.empty(samples, dtype=float)
    n = values.size
    # Chunked sampling avoids a very large temporary array for broad cohorts.
    for i in range(samples):
        means[i] = values[rng.integers(0, n, size=n)].mean()
    low, high = np.quantile(means, [0.025, 0.975])
    return float(low), float(high)


def _assign_decile_labels(
    frame: pd.DataFrame,
    metric_column: str,
    label_column: str,
) -> pd.DataFrame:
    result = frame.copy()
    result[label_column] = pd.Series(pd.NA, index=result.index, dtype="string")
    result[label_column + "Rank"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result[label_column + "Bucket"] = pd.Series(pd.NA, index=result.index, dtype="Int64")

    for folder, group in result.groupby("folder", sort=False, dropna=False):
        eligible = group[group[metric_column].notna()].copy()
        if eligible.empty:
            continue
        eligible = eligible.sort_values(
            [metric_column, "nTop10", "songName", "chartId"],
            ascending=[False, False, True, True],
            kind="mergesort",
        )
        count = len(eligible)
        for zero_index, row_index in enumerate(eligible.index):
            bucket = min(9, int(math.floor(zero_index * 10 / count)))
            result.at[row_index, label_column] = f"{folder}.{bucket}"
            result.at[row_index, label_column + "Rank"] = zero_index + 1
            result.at[row_index, label_column + "Bucket"] = bucket
    return result


def analyze_snapshot(
    players: Sequence[Mapping[str, Any]],
    charts: Sequence[Mapping[str, Any]],
    scores: Sequence[Mapping[str, Any]],
    config: AnalysisConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], pd.DataFrame]:
    """Return chart results, player baselines, run summary, and top-10 contributions."""
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
    if "difficulty" not in merged.columns:
        merged["difficulty"] = np.where(
            merged["type"].eq("Single"), "S" + merged["level"].astype(str),
            np.where(merged["type"].eq("Double"), "D" + merged["level"].astype(str), ""),
        )

    valid = merged[
        merged["pumbility"].notna()
        & (~merged["isBroken"])
        & merged["type"].isin(["Single", "Double"])
    ].copy()
    if valid.empty:
        raise ValueError("No valid Single/Double Phoenix 2 scores with Pumbility were found.")

    # API is already best-per-chart. This deterministic guard protects cached/manual inputs.
    valid = valid.sort_values(
        ["playerId", "chartId", "pumbility", "score", "recordedAt"],
        ascending=[True, True, False, False, False],
        kind="mergesort",
    ).drop_duplicates(["playerId", "chartId"], keep="first")

    player_counts = valid.groupby("playerId").size().rename("validScoreCount")
    eligible_player_ids = player_counts[player_counts >= config.minimum_scores_per_player].index
    valid = valid[valid["playerId"].isin(eligible_player_ids)].copy()
    if valid.empty:
        raise ValueError(
            f"No player had at least {config.minimum_scores_per_player} valid best scores, "
            "so ranks 11-30 cannot be defined."
        )

    valid = valid.sort_values(
        ["playerId", "pumbility", "score", "chartId"],
        ascending=[True, False, False, True],
        kind="mergesort",
    )
    valid["playerRank"] = valid.groupby("playerId", sort=False).cumcount() + 1

    baseline_slice = valid[
        valid["playerRank"].between(config.baseline_start_rank, config.baseline_end_rank)
    ]
    baselines = baseline_slice.groupby("playerId", sort=False)["pumbility"].agg(
        baselinePumbility="mean",
        baselineStd="std",
        baselineMin="min",
        baselineMax="max",
        baselineCount="count",
    )
    baselines = baselines.join(player_counts, how="left")
    baselines = baselines[baselines["baselineCount"] == (
        config.baseline_end_rank - config.baseline_start_rank + 1
    )].copy()

    # Hash identifiers in diagnostics. Aggregate outputs never expose names or raw player IDs.
    baselines["playerHash"] = [
        hashlib.sha256(pid.encode("utf-8")).hexdigest()[:16] for pid in baselines.index.astype(str)
    ]

    top = valid[
        valid["playerRank"].between(1, config.top_end_rank)
        & valid["playerId"].isin(baselines.index)
    ].copy()
    top = top.join(baselines[["baselinePumbility"]], on="playerId")
    top["residualPb"] = top["pumbility"] - top["baselinePumbility"]
    top["squaredResidualPb2"] = top["residualPb"] ** 2

    target_top = top[top["folder"].isin(TARGET_FOLDERS)].copy()
    target_catalog = chart_df[chart_df["folder"].isin(TARGET_FOLDERS)].copy()
    if target_catalog.empty:
        raise ValueError("The chart catalog contained none of the requested target folders.")

    # Denominators: eligible players who have any valid best score on the chart.
    target_valid = valid[valid["folder"].isin(TARGET_FOLDERS)].copy()
    scored_counts = target_valid.groupby("chartId")["playerId"].nunique().rename("nPlayersScored")

    rng = np.random.default_rng(config.random_seed)
    stat_rows: list[dict[str, Any]] = []
    for chart_id, group in target_top.groupby("chartId", sort=False):
        squared = group["squaredResidualPb2"].to_numpy(dtype=float)
        residual = group["residualPb"].to_numpy(dtype=float)
        ci_low, ci_high = _bootstrap_mean_ci(squared, config.bootstrap_samples, rng)
        stat_rows.append(
            {
                "chartId": str(chart_id),
                "nTop10": int(group["playerId"].nunique()),
                "misgradeRawPb2": float(np.mean(squared)),
                "misgradeRmsPb": float(math.sqrt(max(0.0, np.mean(squared)))),
                "meanResidualPb": float(np.mean(residual)),
                "medianResidualPb": float(np.median(residual)),
                "residualStdPb": float(np.std(residual, ddof=1)) if len(residual) > 1 else 0.0,
                "rawCi95LowPb2": ci_low,
                "rawCi95HighPb2": ci_high,
                "meanContributorBaselinePb": float(group["baselinePumbility"].mean()),
            }
        )
    stats = pd.DataFrame(stat_rows)
    if stats.empty:
        stats = pd.DataFrame(
            columns=[
                "chartId", "nTop10", "misgradeRawPb2", "misgradeRmsPb",
                "meanResidualPb", "medianResidualPb", "residualStdPb",
                "rawCi95LowPb2", "rawCi95HighPb2", "meanContributorBaselinePb",
            ]
        )

    result = target_catalog.merge(stats, on="chartId", how="left")
    result = result.merge(scored_counts, on="chartId", how="left")
    result["nTop10"] = result["nTop10"].fillna(0).astype(int)
    result["nPlayersScored"] = result["nPlayersScored"].fillna(0).astype(int)
    result["top10AppearanceRate"] = np.where(
        result["nPlayersScored"] > 0,
        result["nTop10"] / result["nPlayersScored"],
        np.nan,
    )

    # Empirical-Bayes-style reliability companion. The requested raw metric remains primary.
    level_prior = (
        target_top.groupby("folder", sort=False)["squaredResidualPb2"].mean().rename("folderPriorPb2")
    )
    result = result.merge(level_prior, on="folder", how="left")
    weight = result["nTop10"] / (result["nTop10"] + config.shrinkage_k)
    result["reliabilityWeight"] = weight
    result["misgradeShrunkPb2"] = np.where(
        result["misgradeRawPb2"].notna() & result["folderPriorPb2"].notna(),
        weight * result["misgradeRawPb2"] + (1.0 - weight) * result["folderPriorPb2"],
        np.nan,
    )
    result["misgradeShrunkRmsPb"] = np.sqrt(result["misgradeShrunkPb2"])

    result["evidenceStatus"] = np.select(
        [
            result["nTop10"] >= config.published_contributors,
            result["nTop10"] >= config.min_contributors,
            result["nTop10"] > 0,
        ],
        ["Published", "Provisional", "Insufficient"],
        default="Unrated",
    )

    result = _assign_decile_labels(result, "misgradeRawPb2", "requestedTier")
    result = _assign_decile_labels(result, "misgradeShrunkPb2", "reliabilityTier")

    # Within-folder percentiles: 1.0 is easiest, 0.0 is hardest among measured charts.
    result["easePercentileRaw"] = np.nan
    for folder, group in result.groupby("folder", sort=False):
        measured = group[group["misgradeRawPb2"].notna()]
        if measured.empty:
            continue
        ranks = measured["misgradeRawPb2"].rank(method="average", ascending=True)
        denom = max(1, len(measured) - 1)
        pct = (ranks - 1) / denom if len(measured) > 1 else pd.Series(0.5, index=measured.index)
        result.loc[measured.index, "easePercentileRaw"] = pct

    output_columns = [
        "folder", "requestedTier", "requestedTierRank", "requestedTierBucket",
        "reliabilityTier", "reliabilityTierRank", "reliabilityTierBucket",
        "songName", "difficulty", "type", "level", "chartId",
        "misgradeRawPb2", "misgradeRmsPb", "misgradeShrunkPb2", "misgradeShrunkRmsPb",
        "meanResidualPb", "medianResidualPb", "residualStdPb",
        "rawCi95LowPb2", "rawCi95HighPb2", "nTop10", "nPlayersScored",
        "top10AppearanceRate", "easePercentileRaw", "reliabilityWeight",
        "meanContributorBaselinePb", "evidenceStatus", "noteCount", "stepArtist",
    ]
    for col in output_columns:
        if col not in result.columns:
            result[col] = pd.NA
    result = result[output_columns].sort_values(
        ["folder", "requestedTierRank", "songName", "chartId"],
        na_position="last",
        kind="mergesort",
    )

    baseline_out = baselines.reset_index(drop=True)[
        ["playerHash", "validScoreCount", "baselinePumbility", "baselineStd",
         "baselineMin", "baselineMax", "baselineCount"]
    ].sort_values("playerHash")

    contribution_out = target_top[
        ["playerId", "chartId", "folder", "songName", "difficulty", "playerRank",
         "pumbility", "baselinePumbility", "residualPb", "squaredResidualPb2"]
    ].copy()
    contribution_out["playerHash"] = contribution_out["playerId"].map(
        lambda value: hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]
    )
    contribution_out = contribution_out.drop(columns=["playerId"])

    measured = result[result["misgradeRawPb2"].notna()]
    summary = {
        "scriptVersion": SCRIPT_VERSION,
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "method": {
            "baselineRanks": [config.baseline_start_rank, config.baseline_end_rank],
            "comparedRanks": [1, config.top_end_rank],
            "chartMetric": "mean((score_pumbility - player_baseline)^2)",
            "tierDirection": ".0 easiest; .9 hardest",
            "tierAssignment": "equal-count within each official folder, sorted by descending raw metric",
            "usesExistingPiuScoresTierList": False,
            "shrinkageK": config.shrinkage_k,
            "bootstrapSamples": config.bootstrap_samples,
        },
        "coverage": {
            "playersReturnedByCredential": len(players),
            "playersWithAtLeast30ValidScores": int(len(baselines)),
            "validBestScoreRowsAmongEligiblePlayers": int(len(valid)),
            "targetTop10Contributions": int(len(target_top)),
            "targetCatalogCharts": int(len(result)),
            "targetChartsWithAnyContribution": int(len(measured)),
            "targetChartsPublished": int((result["evidenceStatus"] == "Published").sum()),
            "targetChartsProvisional": int((result["evidenceStatus"] == "Provisional").sum()),
            "targetChartsInsufficient": int((result["evidenceStatus"] == "Insufficient").sum()),
            "targetChartsUnrated": int((result["evidenceStatus"] == "Unrated").sum()),
        },
        "folders": {},
    }
    for folder in TARGET_FOLDERS:
        subset = result[result["folder"] == folder]
        summary["folders"][folder] = {
            "catalogCharts": int(len(subset)),
            "measuredCharts": int(subset["misgradeRawPb2"].notna().sum()),
            "publishedCharts": int((subset["evidenceStatus"] == "Published").sum()),
            "medianContributors": float(subset.loc[subset["nTop10"] > 0, "nTop10"].median())
            if (subset["nTop10"] > 0).any()
            else None,
        }

    return result, baseline_out, summary, contribution_out


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
    if include_contributions:
        contributions.to_csv(output_dir / "top10_contributions_pseudonymous.csv", index=False, float_format="%.6f")

    # One easy-to-read CSV per folder.
    folder_dir = output_dir / "folders"
    folder_dir.mkdir(exist_ok=True)
    for folder in TARGET_FOLDERS:
        subset = chart_results[chart_results["folder"] == folder]
        subset.to_csv(folder_dir / f"{folder.lower()}_tiers.csv", index=False, float_format="%.6f")


def make_synthetic_snapshot(
    seed: int = 20260807,
    players_per_folder: int = 80,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, float]]:
    """Construct a controlled fixture where the intended easiest-to-hardest order is known."""
    rng = np.random.default_rng(seed)
    charts: list[dict[str, Any]] = []
    scores: list[dict[str, Any]] = []
    players: list[dict[str, Any]] = []
    true_signal: dict[str, float] = {}

    background_charts: list[dict[str, Any]] = []
    for i in range(40):
        chart_type = "Single" if i % 2 == 0 else "Double"
        level = 16 + (i % 4)
        chart_id = f"background-{i:02d}"
        row = {
            "id": chart_id,
            "mix": "Phoenix2",
            "originalMix": "Phoenix2",
            "songName": f"Synthetic Background {i + 1:02d}",
            "type": chart_type,
            "level": level,
            "difficulty": ("S" if chart_type == "Single" else "D") + str(level),
            "noteCount": 500 + i,
            "playerCount": 1,
            "stepArtist": "Synthetic",
            "scoringLevel": None,
        }
        charts.append(row)
        background_charts.append(row)

    residual_signals = np.linspace(28.0, 6.0, 10)
    target_charts: dict[str, list[dict[str, Any]]] = {}
    for folder in TARGET_FOLDERS:
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
                "noteCount": 850 + level * 10 + index,
                "playerCount": 1,
                "stepArtist": "Synthetic",
                "scoringLevel": None,
            }
            charts.append(row)
            rows.append(row)
            true_signal[chart_id] = float(signal)
        target_charts[folder] = rows

    for folder_index, folder in enumerate(TARGET_FOLDERS):
        for player_index in range(players_per_folder):
            player_id = f"synthetic-player-{folder_index:02d}-{player_index:04d}"
            players.append({"userId": player_id, "isPublic": False})
            base = float(rng.normal(345.0 + folder_index * 1.2, 8.0))

            # Ten target charts are designed to occupy the player's top ten.
            for chart_index, chart in enumerate(target_charts[folder]):
                pumbility = base + residual_signals[chart_index] + float(rng.normal(0.0, 0.65))
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

            # Twenty stable baseline scores become ranks 11-30.
            for bg in background_charts[:20]:
                pumbility = base + float(rng.normal(0.0, 0.45))
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

            # Twenty lower scores ensure a full, unambiguous ranking tail.
            for bg in background_charts[20:]:
                pumbility = base - 9.0 - abs(float(rng.normal(0.0, 1.2)))
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
    for folder in TARGET_FOLDERS:
        group = measured[measured["folder"] == folder].copy()
        if len(group) != 10:
            folder_results[folder] = {"passed": False, "reason": f"expected 10 charts, found {len(group)}"}
            all_ok = False
            continue
        correlation = float(group["trueResidualSignalPb"].corr(group["misgradeRawPb2"], method="spearman"))
        easiest = group.sort_values("misgradeRawPb2", ascending=False).iloc[0]
        hardest = group.sort_values("misgradeRawPb2", ascending=True).iloc[0]
        expected_easiest = group.sort_values("trueResidualSignalPb", ascending=False).iloc[0]
        expected_hardest = group.sort_values("trueResidualSignalPb", ascending=True).iloc[0]
        passed = (
            correlation >= 0.98
            and easiest["chartId"] == expected_easiest["chartId"]
            and hardest["chartId"] == expected_hardest["chartId"]
            and easiest["requestedTier"] == f"{folder}.0"
            and hardest["requestedTier"] == f"{folder}.9"
        )
        all_ok = all_ok and passed
        folder_results[folder] = {
            "passed": bool(passed),
            "spearmanCorrelation": correlation,
            "easiestChart": str(easiest["songName"]),
            "easiestTier": str(easiest["requestedTier"]),
            "hardestChart": str(hardest["songName"]),
            "hardestTier": str(hardest["requestedTier"]),
        }
    return {"passed": bool(all_ok), "folders": folder_results}


def print_result_summary(summary: Mapping[str, Any], output_dir: Path) -> None:
    coverage = summary.get("coverage", {})
    print("\nAnalysis complete", flush=True)
    print(f"  eligible players: {coverage.get('playersWithAtLeast30ValidScores', 0):,}")
    print(f"  target top-10 contributions: {coverage.get('targetTop10Contributions', 0):,}")
    print(f"  target charts measured: {coverage.get('targetChartsWithAnyContribution', 0):,}")
    print(f"  output: {output_dir.resolve()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate player-normalized Phoenix 2 chart misgrade tiers.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_analysis_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--output-dir", type=Path, required=True)
        p.add_argument("--min-contributors", type=int, default=5)
        p.add_argument("--published-contributors", type=int, default=10)
        p.add_argument("--shrinkage-k", type=float, default=10.0)
        p.add_argument("--bootstrap-samples", type=int, default=500)
        p.add_argument("--random-seed", type=int, default=20260807)
        p.add_argument(
            "--include-contributions",
            action="store_true",
            help="Write pseudonymous player-chart top-10 contribution rows for audit/debugging.",
        )

    live = sub.add_parser("live", help="Pull a live consented-player snapshot and analyze it.")
    add_analysis_options(live)
    live.add_argument("--base-url", default=DEFAULT_BASE_URL)
    live.add_argument("--mix", default="Phoenix2", help="PIU Scores mix identifier to analyze.")
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
    if args.min_contributors < 1:
        raise ValueError("--min-contributors must be at least 1")
    if args.published_contributors < args.min_contributors:
        raise ValueError("--published-contributors must be >= --min-contributors")
    if args.shrinkage_k < 0:
        raise ValueError("--shrinkage-k must be nonnegative")
    if args.bootstrap_samples < 0:
        raise ValueError("--bootstrap-samples must be nonnegative")
    return AnalysisConfig(
        min_contributors=args.min_contributors,
        published_contributors=args.published_contributors,
        shrinkage_k=args.shrinkage_k,
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
            mix = args.mix.strip()
            if not mix:
                raise ValueError("--mix must not be empty")
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
            print("Synthetic validation passed for all nine folders.", flush=True)

        print_result_summary(summary, output_dir)
        return 0
    except (ApiError, FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
