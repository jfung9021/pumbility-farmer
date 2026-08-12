"use client";

import { useEffect, useMemo, useState, type KeyboardEvent } from "react";

import { ScoreSyncLink } from "../_components/score-sync-link";
import { SiteHeader } from "../_components/site-header";
import { readJsonResponse } from "../../lib/api-response";
import { hasLimitedData } from "../../lib/chart-evidence";
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


function signed(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
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
  const roundedPercent = Math.round(progress.percent);
  const ariaMinimum = atMaximum ? 0 : progress.threshold;
  const ariaMaximum = progress.nextThreshold ?? Math.max(progress.threshold, value);
  const ariaNow = Math.min(ariaMaximum, Math.max(ariaMinimum, value));
  const emblemLevel = String(progress.rungIndex).padStart(2, "0");
  return (
    <article className="pumbility-progress-panel">
      <div className={`pumbility-progress-heading${mode === "overall" ? "" : " no-emblem"}`}>
        {mode === "overall" ? (
          <img
            alt=""
            aria-hidden="true"
            className="pumbility-rank-emblem"
            height="88"
            src={`/images/phoenix2-ranks/pumbility_${emblemLevel}.webp`}
            width="88"
          />
        ) : null}
        <div>
          <span>{mode === "overall" ? "Rank progress" : "Skill title progress"}</span>
          <strong>{progress.label}</strong>
          <small>
            {progress.nextLabel
              ? `${pumbilityLabel(progress.remaining)} Pumbility to ${progress.nextLabel}`
              : `Maximum ${mode === "overall" ? "rank" : "skill title"} reached`}
          </small>
        </div>
        <b className="pumbility-progress-percent">{roundedPercent}%</b>
      </div>
      <div
        aria-label={`${progress.label} progress`}
        aria-valuemax={ariaMaximum}
        aria-valuemin={ariaMinimum}
        aria-valuenow={ariaNow}
        aria-valuetext={progress.nextLabel
          ? `${roundedPercent}%, ${pumbilityLabel(progress.remaining)} Pumbility to ${progress.nextLabel}`
          : `100%, maximum ${mode === "overall" ? "rank" : "skill title"} reached`}
        className="pumbility-progress"
        role="progressbar"
      >
        <b style={{ width: `${progress.percent}%` }} />
      </div>
      <div className="pumbility-progress-scale">
        <span><small>Start</small><b>{pumbilityLabel(progress.threshold)}</b></span>
        <span className="current"><small>Current</small><b>{pumbilityLabel(value)}</b></span>
        <span>
          <small>{progress.nextThreshold === null ? "End" : "Next"}</small>
          <b>{progress.nextThreshold === null
            ? "Maximum"
            : pumbilityLabel(progress.nextThreshold)}</b>
        </span>
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

function RecommendationCard({
  chart,
  rank,
}: {
  chart: RecommendationChart;
  rank: number;
}) {
  const bpm = formatBpm(chart.bpmMin, chart.bpmMax);
  const estimate = `${chart.type === "Single" ? "S" : "D"}${formatEstimatedDifficulty(chart.estimatedDifficulty)}`;
  const goal = chart.projectedGrade && chart.projectedPlateCode
    ? `Goal: ${chart.projectedGrade} ${chart.projectedPlateCode}`
    : null;
  return (
    <article className="recommendation-card">
      <span className="recommendation-rank">{String(rank).padStart(2, "0")}</span>
      <div className="chart-art recommendation-jacket" data-chart-type={chart.type} aria-hidden="true">
        {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <b>{chart.difficulty}</b>}
        <span className={`chart-difficulty-badge chart-difficulty-${chart.type.toLowerCase()}`}>
          {chart.level}
        </span>
      </div>
      <div className="recommendation-copy">
        <div className="recommendation-title">
          <h3>{chart.songName}</h3>
          {hasLimitedData(chart.nContributors) ? (
            <span
              aria-label="Limited data"
              className="limited-data-warning"
              title={`Limited data: ${chart.nContributors} unique player observations`}
            >
              <b aria-hidden="true">!</b>
              <span>Limited data</span>
            </span>
          ) : null}
        </div>
        <p>
          {chart.stepArtist || "Unknown step artist"}
          {bpm ? <> · {bpm}</> : null}
          <b> · {estimate} estimate</b>
        </p>
        <div className="recommendation-tags">
          <span>{chart.played ? `Current ${chart.existingPumbility?.toFixed(2)} PB` : "Unplayed in Phoenix 2"}</span>
        </div>
      </div>
      <div className="recommendation-value">
        <span>projected gain</span>
        <strong>{chart.projectedGain === null ? "-" : signed(chart.projectedGain)}</strong>
        {goal ? (
          <div className="recommendation-goal">
            <b>{goal}</b>
          </div>
        ) : null}
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
    setPlayerPayload(null);
    setLoadingPlayer(true);
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
        if (!cachedLoaded) setError(message);
      } finally {
        if (!controller.signal.aborted) {
          setLoadingPlayer(false);
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
  const modePumbility = mode?.currentTop50Pumbility ?? 0;
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
      <SiteHeader active="recommendations" />

      <section className="recommendations-hero">
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
          <div className="player-picker-meta">
            <ScoreSyncLink className="player-score-sync-link" />
          </div>
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
            <div className="recommendation-mode-row">
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
                <ProgressStat mode={activeMode} value={modePumbility} />
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
                    <h2 id="top-recommendations-title">
                      {mode.projectionAvailable === false
                        ? "Top 50 farmable charts"
                        : activeMode === "overall"
                          ? "Top 50 recommended charts"
                          : "Top 50 Pumbility opportunities"}
                    </h2>
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
        <p>The projected plate is the weighted median in Phoenix 2 order from Rough Game through Perfect Game. Expected Pumbility is then calculated once from the displayed projected score&apos;s grade, that median plate, and the chart&apos;s mode-specific formula. Projected gain is the deterministic top-50 change from that same displayed result.</p>
        <p>The visible skill rating and eligible-chart ceiling use top-20 average Pumbility. Both ratings are expressed as the continuous chart level where an S with Fair Game earns the selected window&apos;s average Pumbility. Phoenix 2 supplies a window once it is complete; otherwise a complete Phoenix 1 window is used, followed by partial Phoenix 2 only for the visible top-20 rating.</p>
        <p>Played status, existing chart Pumbility, and current top 50 use the Pumbility supplied by Phoenix 2 rather than recomputing historical results. Overall Pumbility is the best 50 values across both modes; Overall recommendations merge each mode&apos;s displayed top 50 and recalculate their deterministic gain against that shared pool. Projections are estimates, not guaranteed results.</p>
      </footer>
    </main>
  );
}
