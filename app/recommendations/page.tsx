"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, type KeyboardEvent } from "react";

import { readJsonResponse } from "../../lib/api-response";
import { formatEstimatedDifficulty } from "../../lib/format-difficulty";
import { pumbilityProgress } from "../../lib/pumbility-progress";
import type {
  ModeKey,
  PlayerRecommendationsResponse,
  PlayerRefreshJob,
  PlayerRefreshResponse,
  RecommendationChart,
  RecommendationModeKey,
  RecommendationPlayersResponse,
} from "../../lib/types";


const RECOMMENDATION_MODES: RecommendationModeKey[] = [
  "overall",
  "singles",
  "doubles",
];


function formatGeneratedAt(value: string | undefined): string {
  if (!value) return "generation time unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "generation time unavailable";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function playerGenerationLabel(payload: PlayerRecommendationsResponse): string {
  const overall = payload.generatedAtUtc;
  if (payload.legacySnapshot) {
    return `Legacy snapshot generated ${formatGeneratedAt(overall)}`;
  }
  const scores = payload.playerSyncedAtUtc
    || payload.recommendationsGeneratedAtUtc
    || overall;
  const model = payload.modelGeneratedAtUtc || overall;
  return `Scores checked ${formatGeneratedAt(scores)} · model ${formatGeneratedAt(model)}`;
}

function signed(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function ratingLabel(mode: ModeKey, value: number): string {
  return `${mode === "singles" ? "S" : "D"}${value.toFixed(2)}`;
}

function pumbilityLabel(value: number): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function ProgressStat({
  mode,
  value,
}: {
  mode: RecommendationModeKey;
  value: number;
}) {
  const progress = pumbilityProgress(mode, value);
  const atMaximum = progress.nextThreshold === null;
  const ariaMinimum = atMaximum ? 0 : progress.threshold;
  const ariaMaximum = progress.nextThreshold ?? progress.threshold;
  const ariaNow = atMaximum
    ? progress.threshold
    : Math.min(ariaMaximum, Math.max(ariaMinimum, value));
  return (
    <article className="pumbility-progress-stat">
      <span>{mode === "overall" ? "Rank progress" : "Skill title progress"}</span>
      <strong>{progress.label}</strong>
      <small>
        {progress.nextLabel
          ? `${pumbilityLabel(progress.remaining)} Pumbility to ${progress.nextLabel}`
          : `Highest ${mode === "overall" ? "rank" : "skill title"} reached`}
      </small>
      <div
        aria-label={`${progress.label} progress`}
        aria-valuemax={ariaMaximum}
        aria-valuemin={ariaMinimum}
        aria-valuenow={ariaNow}
        className="pumbility-progress"
        role="progressbar"
      >
        <b style={{ width: `${progress.percent}%` }} />
      </div>
    </article>
  );
}

function formatBpm(minimum: number | null | undefined, maximum: number | null | undefined): string | null {
  const min = typeof minimum === "number" && Number.isFinite(minimum) && minimum > 0 ? minimum : null;
  const max = typeof maximum === "number" && Number.isFinite(maximum) && maximum > 0 ? maximum : null;
  if (min === null && max === null) return null;
  const low = (min ?? max) as number;
  const high = (max ?? min) as number;
  const format = (value: number) => new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 2,
  }).format(value);
  return low === high ? `${format(low)} BPM` : `${format(low)}–${format(high)} BPM`;
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

const PLATE_CRITERIA: Record<string, string> = {
  PG: "all Perfects",
  UG: "Perfects and Greats only",
  EG: "Perfects, Greats, and Goods only",
  SG: "0 misses",
  MG: "1–5 misses",
  TG: "6–10 misses",
  FG: "11–20 misses",
  RG: "21+ misses",
};

function recommendationGoal(chart: RecommendationChart): string | null {
  if (!chart.projectedGrade || !chart.projectedPlateCode) return null;
  const score = GRADE_GOAL_SCORES[chart.projectedGrade];
  const plateGoal = PLATE_CRITERIA[chart.projectedPlateCode];
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
  const bpm = formatBpm(chart.bpmMin, chart.bpmMax);
  return (
    <article className="recommendation-card">
      <span className="recommendation-rank">{String(rank).padStart(2, "0")}</span>
      <div className="chart-art recommendation-jacket" data-chart-type={chart.type} aria-hidden="true">
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
          {bpm ? <> · {bpm}</> : null}
          <b> · {chart.difficulty} official</b>
        </p>
        <div className="recommendation-tags">
          <span><b>{chart.type === "Single" ? "S" : "D"}{formatEstimatedDifficulty(chart.estimatedDifficulty)}</b> estimate</span>
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
  const [activeMode, setActiveMode] = useState<RecommendationModeKey>("overall");
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
      const deadline = Date.now() + 30_000;
      while (!controller.signal.aborted && ["queued", "running"].includes(job.status)) {
        if (Date.now() >= deadline) {
          throw new Error("The refresh took longer than 30 seconds. Please retry.");
        }
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
  const singlesMode = playerPayload?.player.modes.singles;
  const doublesMode = playerPayload?.player.modes.doubles;
  const modePumbility = mode?.currentTop50Pumbility ?? 0;
  const sourceRecommendationCounts = mode?.sourceRecommendationCounts;
  const sourceModeEligibility = mode?.sourceModeEligibility;
  const unavailableOverallModes = activeMode === "overall" && sourceModeEligibility
    ? (["singles", "doubles"] as ModeKey[]).filter(
        (modeKey) => !sourceModeEligibility[modeKey],
      )
    : [];

  const handleTabKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    currentIndex: number,
  ) => {
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % RECOMMENDATION_MODES.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + RECOMMENDATION_MODES.length)
        % RECOMMENDATION_MODES.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = RECOMMENDATION_MODES.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    const nextMode = RECOMMENDATION_MODES[nextIndex];
    setActiveMode(nextMode);
    window.requestAnimationFrame(() => {
      document.getElementById(`recommendation-tab-${nextMode}`)?.focus();
    });
  };
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
          Choose a consented player. Overall combines the best Single and Double opportunities,
          using each mode&apos;s own skill rating, and measures them against one shared Phoenix 2
          top-50 Pumbility total. Single and Double remain available as independent views.
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
        ) : hasSelection && playersPayload?.refreshSupported === false ? (
          <div className="recommendation-notice">
            Showing cached recommendations. Live score refresh is temporarily unavailable.
          </div>
        ) : null}
        {playerPayload?.stale ? (
          <div className="recommendation-notice">
            Showing the cached model from {formatGeneratedAt(
              playerPayload.modelGeneratedAtUtc || playerPayload.generatedAtUtc,
            )}.
            {playerPayload.currentModelGeneratedAtUtc
              ? ` The current model is ${formatGeneratedAt(playerPayload.currentModelGeneratedAtUtc)}.`
              : ""}
          </div>
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
                  {playerGenerationLabel(playerPayload)}
                </p>
              </div>
              <div className="recommendation-mode-tabs" role="tablist" aria-label="Recommendation mode">
                {RECOMMENDATION_MODES.map((modeKey, index) => (
                  <button
                    aria-controls="recommendation-panel"
                    aria-selected={activeMode === modeKey}
                    className={activeMode === modeKey ? "active" : ""}
                    id={`recommendation-tab-${modeKey}`}
                    key={modeKey}
                    onClick={() => setActiveMode(modeKey)}
                    onKeyDown={(event) => handleTabKeyDown(event, index)}
                    role="tab"
                    tabIndex={activeMode === modeKey ? 0 : -1}
                    type="button"
                  >
                    <b>{modeKey === "overall" ? "O" : modeKey === "singles" ? "S" : "D"}</b>
                    <span>{modeKey}</span>
                  </button>
                ))}
              </div>
            </div>

            <div
              aria-labelledby={`recommendation-tab-${activeMode}`}
              id="recommendation-panel"
              role="tabpanel"
            >
              {mode ? (
                <div className="recommendation-stats">
                  {activeMode === "overall" ? (
                    <article>
                      <span>Scoring ratings</span>
                      <strong>
                        {singlesMode?.eligible && singlesMode.scoringRating !== undefined
                          ? ratingLabel("singles", singlesMode.scoringRating)
                          : "S—"}
                        {" · "}
                        {doublesMode?.eligible && doublesMode.scoringRating !== undefined
                          ? ratingLabel("doubles", doublesMode.scoringRating)
                          : "D—"}
                      </strong>
                      <small>Each chart keeps its mode-specific rating and projection</small>
                    </article>
                  ) : (
                    <article>
                      <span>Scoring rating</span>
                      <strong>
                        {mode.scoringRating === undefined
                          ? "—"
                          : ratingLabel(activeMode, mode.scoringRating)}
                      </strong>
                      <small>
                        {mode.ratingSource === "phoenix1" ? "Phoenix 1" : "Phoenix 2"}{" "}
                        {mode.ratingBaselineLabel ?? mode.baselineLabel ?? "top 20 scores"}
                      </small>
                    </article>
                  )}
                  <ProgressStat mode={activeMode} value={modePumbility} />
                  {activeMode === "overall" ? (
                    <article>
                      <span>Recommendation pool</span>
                      <strong>{mode.candidateCount ?? 0}</strong>
                      <small>
                        {sourceRecommendationCounts?.singles ?? 0} Single · {sourceRecommendationCounts?.doubles ?? 0} Double
                      </small>
                    </article>
                  ) : (
                    <article>
                      <span>Eligible charts</span>
                      <strong>{mode.candidateCount ?? 0}</strong>
                      <small>
                        {mode.candidateRange?.[1] === undefined
                          ? "No rating-based chart ceiling"
                          : `At or below ${mode.candidateRange[1].toFixed(2)}`}
                      </small>
                    </article>
                  )}
                  <article>
                    <span>{activeMode === "overall" ? "Overall Pumbility" : "Current top 50"}</span>
                    <strong>{pumbilityLabel(modePumbility)}</strong>
                    <small>
                      {activeMode === "overall"
                        ? `${mode.top50ModeCounts?.singles ?? 0} Single · ${mode.top50ModeCounts?.doubles ?? 0} Double in the top 50`
                        : mode.currentTop50CutoffPumbility === null || mode.currentTop50CutoffPumbility === undefined
                          ? `${mode.currentTop50Count ?? mode.validScoreCount} of 50 Phoenix 2 charts`
                          : `#50 cutoff ${mode.currentTop50CutoffPumbility.toFixed(2)}`}
                    </small>
                  </article>
                </div>
              ) : null}

              {unavailableOverallModes.map((modeKey) => (
                <div className="recommendation-notice overall-source-notice" key={modeKey}>
                  <b>{modeKey === "singles" ? "Single" : "Double"} recommendations unavailable.</b>{" "}
                  {playerPayload.player.modes[modeKey].reason || "This mode cannot be rated yet."}{" "}
                  Existing {modeKey === "singles" ? "Single" : "Double"} Phoenix 2 scores still count toward Overall Pumbility.
                </div>
              ))}

              {activeMode === "overall" && !mode ? (
                <div className="recommendation-empty insufficient-state">
                  <span>O</span>
                  <h2>Overall is being prepared</h2>
                  <p>
                    This cached recommendation predates the Overall model. Single and Double
                    remain available while the latest analysis is published.
                  </p>
                </div>
              ) : !mode?.eligible ? (
                <div className="recommendation-empty insufficient-state">
                  <span>{mode?.validScoreCount ?? 0}/{mode?.requiredScoreCount ?? 1}</span>
                  <h2>Not enough score data yet</h2>
                  <p>{mode?.reason || "This mode cannot be rated yet."}</p>
                </div>
              ) : (
                <section className="top-recommendations" aria-labelledby="top-recommendations-title">
                  <div className="recommendation-section-heading">
                    <div>
                      <p>{mode.projectionAvailable === false ? "HIGHEST FARM EDGE" : "MAXIMUM PROJECTED VALUE"}</p>
                      <h2 id="top-recommendations-title">
                        {mode.projectionAvailable === false
                          ? "Top 50 farmable charts"
                          : activeMode === "overall"
                            ? "Top 50 overall opportunities"
                            : "Top 50 Pumbility opportunities"}
                      </h2>
                    </div>
                    <p>{mode.projectionAvailable === false
                      ? "Ranked by farm edge because a complete ranks 11–30 projection rating is not available."
                      : activeMode === "overall"
                        ? "The best 50 from each mode are combined, then reranked by projected gain to the shared Phoenix 2 S+D top 50."
                        : "Projected gain uses the motivated peer score, every likely grade-plate outcome, and the player's Phoenix 2 top 50. Ties favor the easiest estimated difficulty."}</p>
                  </div>
                  <div className="recommendation-list">
                    {mode.topRecommendations.length ? mode.topRecommendations.map((chart, index) => (
                      <RecommendationCard chart={chart} key={chart.chartId} rank={index + 1} />
                    )) : <p className="no-recommendations">No nearby chart is projected to improve the current top 50.</p>}
                  </div>
                </section>
              )}
            </div>
          </>
        ) : null}
      </section>

      <footer>
        <p><b>How the merge works</b> Phoenix 2 charts.json is a strict allowlist. When a player has a score in both versions, only their best Phoenix 2 score is used.</p>
        <p>Phoenix 1 scores are rebased to Phoenix 2 chart levels before each version is normalized and combined. Removed Phoenix 1 charts never enter this engine.</p>
        <p>Projected scores use the ranks 11–30 Pumbility rating and the unweighted median (50th percentile) from all other players with a normalized result on the exact chart. The search tries plus or minus 0.2 through 0.5 rating in 0.1 steps seeking 20 peers, repeats those radii seeking 10, then repeats seeking five. Every peer within the narrowest successful radius is used; below five peers, the player-balanced population model is used.</p>
        <p>The visible skill rating and eligible-chart ceiling use top-20 average Pumbility. Both ratings are expressed as the continuous chart level where an S with Fair Game earns the selected window&apos;s average Pumbility. Phoenix 2 supplies a window once it is complete; otherwise a complete Phoenix 1 window is used, followed by partial Phoenix 2 only for the visible top-20 rating.</p>
        <p>Played status, current top 50, and projected gain always use Phoenix 2. Overall Pumbility is the best 50 values across both modes; Overall recommendations merge each mode&apos;s displayed top 50 and recalculate their gain against that shared pool. Projections are estimates, not guaranteed results.</p>
      </footer>
    </main>
  );
}
