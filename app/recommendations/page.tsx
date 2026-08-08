"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { readJsonResponse } from "../../lib/api-response";
import type {
  ModeKey,
  PlayerRecommendationsResponse,
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

function plateSourceLabel(chart: RecommendationChart): string {
  if (chart.plateProjectionSource === "phoenix2") return "Phoenix 2 history";
  if (chart.plateProjectionSource === "phoenix1") return "Phoenix 1 prior";
  return "population model";
}

function scoreProjectionEvidenceLabel(chart: RecommendationChart): string | null {
  if (chart.projectedScore === null || chart.scoreProjectionConfidence === "unavailable") return null;
  const support = chart.scoreProjectionSupportCount;
  const confidence = chart.scoreProjectionConfidence;
  if (support !== null && support !== undefined && confidence) {
    return `Population model · ${support.toLocaleString()} nearby scores · ${confidence} confidence`;
  }
  if (support !== null && support !== undefined) {
    return `Population model · ${support.toLocaleString()} nearby scores`;
  }
  if (confidence) return `Population model · ${confidence} confidence`;
  return "Population score model";
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
        <p>{chart.stepArtist || "Unknown step artist"}</p>
        <div className="recommendation-tags">
          <span><b>{chart.difficulty}</b> official</span>
          <span><b>{chart.type === "Single" ? "S" : "D"}{chart.estimatedDifficulty.toFixed(2)}</b> estimate</span>
          <span>{chart.played ? `Current ${chart.existingPumbility?.toFixed(2)} PB` : "Unplayed in Phoenix 2"}</span>
          {chart.projectedScore !== null ? (
            <span><b>{chart.projectedScore.toLocaleString()}</b> projected score</span>
          ) : null}
          {scoreProjectionEvidenceLabel(chart) ? (
            <span>{scoreProjectionEvidenceLabel(chart)}</span>
          ) : null}
          {chart.projectedGrade && chart.projectedPlateCode ? (
            <span>
              <b>{chart.projectedGrade} {chart.projectedPlateCode}</b> most likely
              {chart.projectedPlateProbability !== null
                ? ` (${(chart.projectedPlateProbability * 100).toFixed(0)}%)`
                : ""}
              {` from ${plateSourceLabel(chart)}`}
            </span>
          ) : null}
        </div>
      </div>
      <div className="recommendation-value">
        <span>projected gain</span>
        <strong>{chart.projectedGain === null ? "-" : signed(chart.projectedGain)}</strong>
        <small>
          {chart.expectedPumbility === null
            ? "Score projection unavailable"
            : `${chart.expectedPumbility.toFixed(2)} formula expected`}
        </small>
      </div>
    </article>
  );
}

function CandidateRow({ chart }: { chart: RecommendationChart }) {
  return (
    <article className="candidate-row">
      <div className="candidate-jacket" aria-hidden="true">
        {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <span>{chart.difficulty}</span>}
      </div>
      <div className="candidate-copy">
        <div><h3>{chart.songName}</h3><span>{chart.evidenceStatus}</span></div>
        <p>
          {chart.difficulty} official · {chart.estimatedDifficulty.toFixed(2)} estimated · {chart.nContributors} contributors
          {chart.projectedScore !== null ? ` · ${chart.projectedScore.toLocaleString()} projected score` : ""}
          {scoreProjectionEvidenceLabel(chart) ? ` · ${scoreProjectionEvidenceLabel(chart)}` : ""}
          {chart.projectedGrade && chart.projectedPlateCode
            ? ` · ${chart.projectedGrade} ${chart.projectedPlateCode} most likely${chart.projectedPlateProbability === null ? "" : ` (${(chart.projectedPlateProbability * 100).toFixed(0)}%)`} from ${plateSourceLabel(chart)}`
            : ""}
        </p>
      </div>
      <div className="candidate-metric"><span>from rating</span><b>{signed(chart.distanceFromRating)}</b></div>
      <div className="candidate-metric">
        <span>formula expected</span>
        <b>{chart.expectedPumbility === null ? "-" : chart.expectedPumbility.toFixed(2)}</b>
      </div>
      <div className="candidate-metric candidate-gain"><span>gain</span><b>{chart.projectedGain === null ? "-" : signed(chart.projectedGain)}</b></div>
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
  const [query, setQuery] = useState("");
  const [loadingPlayers, setLoadingPlayers] = useState(true);
  const [loadingPlayer, setLoadingPlayer] = useState(false);
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
      return;
    }
    const controller = new AbortController();
    setLoadingPlayer(true);
    setError(null);
    fetch(`/api/recommendations?playerKey=${encodeURIComponent(selectedKey)}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then((response) => readJsonResponse<PlayerRecommendationsResponse>(response))
      .then(setPlayerPayload)
      .catch((caught) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setError(caught instanceof Error ? caught.message : "Could not load recommendations.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingPlayer(false);
      });
    return () => controller.abort();
  }, [selectedKey]);

  const selectPlayer = (playerKey: string, inputValue = "") => {
    setSelectedKey(playerKey);
    setPlayerQuery(inputValue);
    setPlayerMenuOpen(false);
    setQuery("");
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
    setQuery("");
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

  const filteredCandidates = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    const rows = mode?.candidates || [];
    if (!normalized) return rows;
    return rows.filter((chart) =>
      `${chart.songName} ${chart.stepArtist || ""} ${chart.difficulty}`
        .toLocaleLowerCase()
        .includes(normalized),
    );
  }, [mode, query]);
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
              </div>
              <div className="recommendation-mode-tabs" role="tablist" aria-label="Recommendation mode">
                {(["singles", "doubles"] as ModeKey[]).map((modeKey) => (
                  <button
                    aria-selected={activeMode === modeKey}
                    className={activeMode === modeKey ? "active" : ""}
                    key={modeKey}
                    onClick={() => { setActiveMode(modeKey); setQuery(""); }}
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
                          ? "Top 20 farmable charts"
                          : "Top 20 Pumbility opportunities"}
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

                <section className="all-candidates" aria-labelledby="all-candidates-title">
                  <div className="recommendation-section-heading candidate-heading">
                    <div><p>FULL MATCHING SET</p><h2 id="all-candidates-title">All charts up to your rating</h2></div>
                    <label className="candidate-search">
                      <span>⌕</span>
                      <input
                        aria-label="Search nearby charts"
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="Search songs, artists, or levels"
                        type="search"
                        value={query}
                      />
                    </label>
                  </div>
                  <div className="candidate-list">
                    {filteredCandidates.map((chart) => <CandidateRow chart={chart} key={chart.chartId} />)}
                    {!filteredCandidates.length ? <p className="no-recommendations">No charts match this search.</p> : null}
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
