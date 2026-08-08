"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { readJsonResponse } from "../../lib/api-response";
import type {
  ModeKey,
  PlayerRecommendationsResponse,
  PlayerRefreshJob,
  PlayerRefreshResponse,
  RecommendationChart,
  RecommendationPlayersResponse,
} from "../../lib/types";


function formatGeneratedAt(value: string | undefined): string {
  if (!value) return "Unknown generation time";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown generation time";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function signed(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function ratingLabel(mode: ModeKey, value: number): string {
  return `${mode === "singles" ? "S" : "D"}${value.toFixed(2)}`;
}

const GRADE_GOAL_SCORES: Record<string, number> = {
  "SSS+": 995_000,
  SSS: 990_000,
  "SS+": 985_000,
  SS: 980_000,
  "S+": 975_000,
  S: 970_000,
  "AAA+": 960_000,
  AAA: 950_000,
  "AA+": 940_000,
  AA: 920_000,
  "A+": 900_000,
  A: 800_000,
  B: 700_000,
  C: 600_000,
  D: 500_000,
  F: 0,
};

const PLATE_GOALS: Record<string, string> = {
  PG: "all Perfects",
  UG: "no Good, Bad, or Miss",
  EG: "no Bad or Miss",
  SG: "0 misses",
  MG: "<10 misses",
  TG: "<20 misses",
  FG: "<50 misses",
  RG: "50+ misses",
};

function recommendationGoal(chart: RecommendationChart): string | null {
  if (!chart.projectedGrade || !chart.projectedPlateCode) return null;
  const score = GRADE_GOAL_SCORES[chart.projectedGrade];
  const plateGoal = PLATE_GOALS[chart.projectedPlateCode];
  if (score === undefined || !plateGoal) return null;
  return `Goal: ${chart.projectedGrade} ${chart.projectedPlateCode} (${score.toLocaleString()}, ${plateGoal})`;
}

function RecommendationCard({
  chart,
  rank,
}: {
  chart: RecommendationChart;
  rank: number;
}) {
  return (
    <article className="recommendation-card">
      <span className="recommendation-rank">{String(rank).padStart(2, "0")}</span>
      <div className="recommendation-jacket" aria-hidden="true">
        {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <b>{chart.difficulty}</b>}
      </div>
      <div className="recommendation-copy">
        <div className="recommendation-title">
          <h3>{chart.songName}</h3>
          <span className={`evidence evidence-${chart.evidenceStatus.toLowerCase()}`}>
            {chart.evidenceStatus}
          </span>
        </div>
        <p>
          {chart.stepArtist || "Unknown step artist"}
          <b> · {chart.difficulty} official</b>
        </p>
        <div className="recommendation-tags">
          <span><b>{chart.type === "Single" ? "S" : "D"}{chart.estimatedDifficulty.toFixed(2)}</b> estimate</span>
          <span>{chart.played ? `Current ${chart.existingPumbility?.toFixed(2)} PB` : "Unplayed in Phoenix 2"}</span>
          {recommendationGoal(chart) ? <span><b>{recommendationGoal(chart)}</b></span> : null}
        </div>
      </div>
      <div className="recommendation-value">
        <span>projected gain</span>
        <strong>{chart.projectedGain === null ? "-" : signed(chart.projectedGain)}</strong>
      </div>
    </article>
  );
}

export default function RecommendationsPage() {
  const [playersPayload, setPlayersPayload] = useState<RecommendationPlayersResponse | null>(null);
  const [playerPayload, setPlayerPayload] = useState<PlayerRecommendationsResponse | null>(null);
  const [selectedKey, setSelectedKey] = useState("");
  const [playerQuery, setPlayerQuery] = useState("");
  const [playerMenuOpen, setPlayerMenuOpen] = useState(false);
  const [activeMode, setActiveMode] = useState<ModeKey>("singles");
  const [loadingPlayers, setLoadingPlayers] = useState(true);
  const [loadingPlayer, setLoadingPlayer] = useState(false);
  const [refreshingPlayer, setRefreshingPlayer] = useState(false);
  const [refreshWarning, setRefreshWarning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/recommendations/players")
      .then((response) => readJsonResponse<RecommendationPlayersResponse>(response))
      .then((payload) => {
        if (cancelled) return;
        setPlayersPayload(payload);
        const params = new URLSearchParams(window.location.search);
        const requested = params.get("player") || "";
        const requestedPlayer = payload.players.find((player) => player.playerKey === requested);
        if (requestedPlayer) {
          setSelectedKey(requestedPlayer.playerKey);
          setPlayerQuery(requestedPlayer.displayName);
        }
      })
      .catch((caught) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "Could not load players.");
      })
      .finally(() => {
        if (!cancelled) setLoadingPlayers(false);
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!selectedKey) {
      setPlayerPayload(null);
      setRefreshWarning(null);
      return;
    }
    const controller = new AbortController();
    setPlayerPayload(null);
    setLoadingPlayer(true);
    setRefreshingPlayer(false);
    setRefreshWarning(null);
    setError(null);
    let cachedLoaded = false;

    const loadCached = async () => {
      const response = await fetch(
        `/api/recommendations?playerKey=${encodeURIComponent(selectedKey)}`,
        { cache: "no-store", signal: controller.signal },
      );
      if (response.status === 404) return null;
      const payload = await readJsonResponse<PlayerRecommendationsResponse>(response);
      cachedLoaded = true;
      setPlayerPayload(payload);
      setLoadingPlayer(false);
      return payload;
    };

    const waitForJob = async (initial: PlayerRefreshJob) => {
      let job = initial;
      while (!controller.signal.aborted && ["queued", "running"].includes(job.status)) {
        await new Promise<void>((resolve) => window.setTimeout(resolve, 1000));
        if (controller.signal.aborted) return;
        const response = await fetch(
          `/api/recommendations/refresh?jobId=${encodeURIComponent(job.id)}`,
          { cache: "no-store", signal: controller.signal },
        );
        job = await readJsonResponse<PlayerRefreshJob>(response);
      }
      if (job.status === "failed") {
        throw new Error(job.error || "Could not refresh this player's recommendations.");
      }
    };

    const refresh = async () => {
      if (playersPayload?.refreshSupported === false) return;
      setRefreshingPlayer(true);
      const response = await fetch(
        `/api/recommendations/refresh?playerKey=${encodeURIComponent(selectedKey)}`,
        { method: "POST", cache: "no-store", signal: controller.signal },
      );
      const started = await readJsonResponse<PlayerRefreshResponse>(response);
      if (started.outcome === "fresh") {
        cachedLoaded = true;
        setPlayerPayload(started.recommendation);
        return;
      }
      await waitForJob(started.job);
      const refreshed = await loadCached();
      if (!refreshed) throw new Error("The refreshed recommendations are unavailable.");
    };

    void (async () => {
      try {
        await loadCached();
        await refresh();
      } catch (caught) {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        const message = caught instanceof Error
          ? caught.message
          : "Could not refresh recommendations.";
        if (cachedLoaded) setRefreshWarning(message);
        else setError(message);
      } finally {
        if (!controller.signal.aborted) {
          setLoadingPlayer(false);
          setRefreshingPlayer(false);
        }
      }
    })();
    return () => controller.abort();
  }, [playersPayload?.refreshSupported, selectedKey]);

  const selectPlayer = (playerKey: string, inputValue = "") => {
    setSelectedKey(playerKey);
    setPlayerQuery(inputValue);
    setPlayerMenuOpen(false);
    const url = new URL(window.location.href);
    if (playerKey) url.searchParams.set("player", playerKey);
    else url.searchParams.delete("player");
    window.history.replaceState({}, "", url);
  };

  const mode = playerPayload?.player.modes[activeMode] || null;
  const phoenix2ScoreCount = mode?.phoenix2ScoreCount ?? mode?.validScoreCount ?? 0;
  const phoenix2ScoreThreshold = mode?.phoenix2ScoreThreshold ?? 50;
  const phoenix2ThresholdProgress = Math.min(phoenix2ScoreCount, phoenix2ScoreThreshold);
  const ratingSourceLabel = mode?.ratingSource === "phoenix1" ? "Phoenix 1" : "Phoenix 2";
  const handlePlayerInput = (value: string) => {
    setPlayerQuery(value);
    setPlayerMenuOpen(true);
    if (!selectedKey) return;
    setSelectedKey("");
    const url = new URL(window.location.href);
    url.searchParams.delete("player");
    window.history.replaceState({}, "", url);
  };

  const filteredPlayers = useMemo(() => {
    const players = playersPayload?.players || [];
    const normalized = selectedKey ? "" : playerQuery.trim().toLocaleLowerCase();
    if (!normalized) return players;
    return players.filter((player) =>
      `${player.displayName} ${player.username}`.toLocaleLowerCase().includes(normalized),
    );
  }, [playerQuery, playersPayload, selectedKey]);

  const hasSelection = Boolean(selectedKey);

  return (
    <main className="recommendations-page">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <header className="site-header">
        <Link className="brand" href="/" aria-label="Pumbility Farmer home">
          <span className="brand-mark">PF</span>
          <span>Pumbility <b>Farmer</b></span>
        </Link>
        <nav className="page-nav" aria-label="Primary navigation">
          <span>Recommendations</span>
          <Link href="/tier-list">Tier List</Link>
        </nav>
      </header>

      <section className="recommendations-hero">
        <p className="recommendations-intro">
          Choose a consented player. Skill ratings use Phoenix 1 until each mode reaches
          50 Phoenix 2 scores. Projected scores come from a population model of player rating
          and chart difficulty; played status and current value always use Phoenix 2.
        </p>

        <div className="player-picker">
          <label htmlFor="player-select">Phoenix 2 username</label>
          <div className="player-combobox">
            <input
              aria-controls="player-options"
              aria-expanded={playerMenuOpen}
              aria-haspopup="listbox"
              autoComplete="off"
              disabled={loadingPlayers || !playersPayload?.players.length}
              id="player-select"
              onBlur={() => setPlayerMenuOpen(false)}
              onChange={(event) => handlePlayerInput(event.target.value)}
              onClick={() => setPlayerMenuOpen(true)}
              onFocus={(event) => {
                event.currentTarget.select();
                setPlayerMenuOpen(true);
              }}
              placeholder="Type or select a player"
              role="combobox"
              type="text"
              value={playerQuery}
            />
            {playerMenuOpen ? (
              <div className="player-options" id="player-options" role="listbox">
                {filteredPlayers.map((player) => (
                  <button
                    aria-selected={selectedKey === player.playerKey}
                    key={player.playerKey}
                    onClick={() => selectPlayer(player.playerKey, player.displayName)}
                    onMouseDown={(event) => event.preventDefault()}
                    role="option"
                    type="button"
                  >
                    {player.displayName}
                  </button>
                ))}
                {!filteredPlayers.length ? <p>No matching usernames</p> : null}
              </div>
            ) : null}
          </div>
          <span>
            {playersPayload
              ? `${playersPayload.players.length.toLocaleString()} usernames · generated ${formatGeneratedAt(playersPayload.generatedAtUtc)}`
              : "Only usernames shared with this community tool are listed."}
          </span>
        </div>
        {error ? <div className="recommendation-notice error-notice">{error}</div> : null}
        {refreshWarning ? (
          <div className="recommendation-notice error-notice">
            Showing cached recommendations. Refresh failed: {refreshWarning}
          </div>
        ) : refreshingPlayer && playerPayload ? (
          <div className="recommendation-notice">Refreshing this player's Phoenix 2 scores…</div>
        ) : null}
      </section>

      <section className="recommendations-workspace" aria-busy={loadingPlayer}>
        {!hasSelection ? (
          <div className="recommendation-empty">
            <span>PF</span>
            <h2>Select a username</h2>
            <p>Your internal player ID and raw score history are never returned to the browser.</p>
          </div>
        ) : loadingPlayer && !playerPayload ? (
          <div className="recommendation-empty"><span className="spinner" /><h2>Calculating your route</h2></div>
        ) : playerPayload ? (
          <>
            <div className="recommendation-player-heading">
              <div>
                <p>SELECTED PLAYER</p>
                <h2>{playerPayload.player.displayName}</h2>
                <p>
                  Scores {formatGeneratedAt(playerPayload.playerSyncedAtUtc)} · model {formatGeneratedAt(playerPayload.modelGeneratedAtUtc)}
                </p>
              </div>
              <div className="recommendation-mode-tabs" role="tablist" aria-label="Recommendation mode">
                {(["singles", "doubles"] as ModeKey[]).map((modeKey) => (
                  <button
                    aria-selected={activeMode === modeKey}
                    className={activeMode === modeKey ? "active" : ""}
                    key={modeKey}
                    onClick={() => setActiveMode(modeKey)}
                    role="tab"
                    type="button"
                  >
                    <b>{modeKey === "singles" ? "S" : "D"}</b>
                    <span>{modeKey}</span>
                  </button>
                ))}
              </div>
            </div>

            {!mode?.eligible ? (
              <div className="recommendation-empty insufficient-state">
                <span>{mode?.validScoreCount ?? 0}/{mode?.requiredScoreCount ?? 1}</span>
                <h2>Not enough score data yet</h2>
                <p>{mode?.reason || "This mode cannot be rated yet."}</p>
              </div>
            ) : (
              <>
                <div className="recommendation-stats">
                  <article><span>Scoring rating</span><strong>{ratingLabel(activeMode, mode.scoringRating ?? 0)}</strong><small>{ratingSourceLabel} {mode.ratingBaselineLabel ?? mode.baselineLabel ?? "ranks 11-30"}</small></article>
                  <article className="rating-source-stat"><span>Phoenix 2 rating history</span><strong>{phoenix2ThresholdProgress}/{phoenix2ScoreThreshold}</strong><small>{mode.ratingSource === "phoenix1" ? "Using Phoenix 1 until this reaches 50" : "Using Phoenix 2 scores for skill rating"}</small><i><b style={{ width: `${Math.min(100, (phoenix2ThresholdProgress / phoenix2ScoreThreshold) * 100)}%` }} /></i></article>
                  <article><span>Eligible charts</span><strong>{mode.candidateCount ?? 0}</strong><small>At or below {mode.candidateRange?.[1].toFixed(2)}</small></article>
                  <article><span>Current top 50</span><strong>{mode.currentTop50Pumbility?.toFixed(2) ?? "-"}</strong><small>{mode.currentTop50CutoffPumbility === null || mode.currentTop50CutoffPumbility === undefined ? "Fewer than 50 Phoenix 2 charts" : `#50 cutoff ${mode.currentTop50CutoffPumbility.toFixed(2)}`}</small></article>
                </div>

                <section className="top-recommendations" aria-labelledby="top-recommendations-title">
                  <div className="recommendation-section-heading">
                    <div>
                      <p>{mode.projectionAvailable === false ? "HIGHEST FARM EDGE" : "MAXIMUM PROJECTED VALUE"}</p>
                      <h2 id="top-recommendations-title">
                        {mode.projectionAvailable === false
                          ? "Top 50 farmable charts"
                          : "Top 50 Pumbility opportunities"}
                      </h2>
                    </div>
                    <p>{mode.projectionAvailable === false
                      ? "Ranked by farm edge because the population score model is not available yet."
                      : "Projected gain uses the population-predicted score, every likely grade-plate outcome, and the player's Phoenix 2 top 50. Ties favor the easiest estimated difficulty."}</p>
                  </div>
                  <div className="recommendation-list">
                    {mode.topRecommendations.length ? mode.topRecommendations.map((chart, index) => (
                      <RecommendationCard chart={chart} key={chart.chartId} rank={index + 1} />
                    )) : <p className="no-recommendations">No nearby chart is projected to improve the current top 50.</p>}
                  </div>
                </section>
              </>
            )}
          </>
        ) : null}
      </section>

      <footer>
        <p><b>How the merge works</b> Phoenix 2 charts.json is a strict allowlist. When a player has a score in both versions, only their best Phoenix 2 score is used.</p>
        <p>Phoenix 1 scores are rebased to Phoenix 2 chart levels before each version is normalized and combined. Removed Phoenix 1 charts never enter this engine.</p>
        <p>Projected scores come from a player-balanced population response model. It learns how expected score changes with both scoring rating and continuous chart difficulty, so no player's raw-score average is used as their prediction baseline.</p>
        <p>Skill rating uses Phoenix 1 independently for Singles and Doubles until that mode reaches 50 valid Phoenix 2 scores.</p>
        <p>Played status, current top 50, and projected gain always use Phoenix 2. Projections are estimates, not guaranteed results.</p>
      </footer>
    </main>
  );
}
