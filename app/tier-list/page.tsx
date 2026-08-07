"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { demoPayloads } from "../../lib/demo-data";
import { readJsonResponse } from "../../lib/api-response";
import {
  applyPhoenix1Rerates,
  type Phoenix1ReratePayload,
} from "../../lib/phoenix1-rerates";
import {
  archiveForMix,
  DEFAULT_MIX,
  MIXES,
  mixFromSearchParams,
  type MixKey,
} from "../../lib/mixes";
import type {
  AnalysisJobStatus,
  AnalysisPayload,
  AnalysisRefreshResponse,
  ChartResult,
  EvidenceStatus,
  ModeKey,
} from "../../lib/types";

const LOCAL_ANALYSIS = process.env.NEXT_PUBLIC_LOCAL_ANALYSIS === "1";

type FilterState = {
  query: string;
  level: string;
  evidence: "All" | EvidenceStatus;
  showUnrated: boolean;
};

const initialFilter: FilterState = {
  query: "",
  level: "All",
  evidence: "All",
  showUnrated: false,
};

const groupTone = ["lime", "green", "mint", "cyan", "slate", "amber", "orange", "rose", "red"];

function signed(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function signedBoundary(value: number): string {
  return signed(value, Number.isInteger(value * 100) ? 2 : 3);
}

function effectRange(low: number | null, high: number | null): string {
  if (low === null) return `difference ≤ ${signedBoundary(high ?? -1.0)}`;
  if (high === null) return `difference ≥ ${signedBoundary(low)}`;
  return `${signedBoundary(low)} to ${signedBoundary(high)}`;
}

function chartGrade(chart: ChartResult): string {
  if (chart.estimatedDifficulty === null) return "—";
  const prefix = chart.type === "Single" ? "S" : "D";
  return `${prefix}${chart.estimatedDifficulty.toFixed(1)}`;
}

function formatRunTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown run time";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function durationLabel(milliseconds: number): string {
  const seconds = Math.max(0, Math.ceil(milliseconds / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes < 60) return `${minutes}m ${remainingSeconds}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function refreshAge(value: string, nowMs: number): string {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "unknown age";
  const elapsed = Math.max(0, nowMs - timestamp);
  if (elapsed < 60_000) return "just now";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}m ago`;
  return `${Math.floor(elapsed / 3_600_000)}h ago`;
}

function ChartCard({ chart }: { chart: ChartResult }) {
  const delta = chart.difficultyDelta;
  const rerate = chart.phoenix2Rerate;
  return (
    <article className="chart-card">
      <div className="jacket" aria-hidden="true">
        {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <span>{chart.difficulty}</span>}
      </div>
      <div className="chart-copy">
        <div className="chart-heading">
          <h3>{chart.songName}</h3>
          <span className={`evidence evidence-${chart.evidenceStatus.toLowerCase()}`}>{chart.evidenceStatus}</span>
        </div>
        {rerate ? (
          <div
            aria-label={`Phoenix 2 ${rerate.direction} this chart from ${rerate.from} to ${rerate.to}`}
            className={`chart-rerate rerate-${rerate.direction}`}
            title={`Phoenix 2 ${rerate.direction}: ${rerate.from} → ${rerate.to}`}
          >
            <b>{rerate.direction === "uprated" ? "↑ Uprated" : "↓ Downrated"}</b>
            <span>in Phoenix 2 · {rerate.from} → {rerate.to}</span>
          </div>
        ) : null}
        <p>
          {chart.stepArtist || "Unknown step artist"}
          {chart.noteCount ? ` · ${chart.noteCount.toLocaleString()} notes` : ""}
        </p>
        <div className="chart-meta">
          <span><b>{chart.difficulty}</b> official</span>
          <span><b>{chartGrade(chart)}</b> estimated</span>
          <span><b>{chart.nContributors}</b> contributors</span>
          {chart.levelRank !== null && chart.levelComparisonCharts !== null ? (
            <span><b>#{chart.levelRank}</b> of {chart.levelComparisonCharts} in {chart.difficulty}</span>
          ) : null}
        </div>
      </div>
      <div className={`delta ${delta !== null && delta < 0 ? "delta-easy" : "delta-hard"}`}>
        <span>difference</span>
        <strong>{delta === null ? "—" : signed(delta)}</strong>
        {chart.difficultyCi95Low !== null && chart.difficultyCi95High !== null ? (
          <small>{chart.difficultyCi95Low.toFixed(1)}–{chart.difficultyCi95High.toFixed(1)} CI</small>
        ) : null}
      </div>
    </article>
  );
}

function TierSection({
  rank,
  name,
  range,
  charts,
}: {
  rank: number;
  name: string;
  range: string;
  charts: ChartResult[];
}) {
  return (
    <section className={`tier tier-${groupTone[rank - 1]}`} aria-labelledby={`tier-${rank}`}>
      <header className="tier-header">
        <div className="tier-rank">{String(rank).padStart(2, "0")}</div>
        <div>
          <p>{range}</p>
          <h2 id={`tier-${rank}`}>{name}</h2>
        </div>
        <span className="tier-count">{charts.length} chart{charts.length === 1 ? "" : "s"}</span>
      </header>
      <div className="tier-list">
        {charts.length ? charts.map((chart) => <ChartCard chart={chart} key={chart.chartId} />) : (
          <p className="empty-tier">No charts match the current filters.</p>
        )}
      </div>
    </section>
  );
}

export default function Home() {
  const [activeMix, setActiveMix] = useState<MixKey>(DEFAULT_MIX);
  const [payloads, setPayloads] = useState<Partial<Record<MixKey, AnalysisPayload>>>({});
  const [activeModes, setActiveModes] = useState<Record<MixKey, ModeKey>>({
    phoenix1: "singles",
    phoenix2: "singles",
  });
  const [filters, setFilters] = useState<Record<MixKey, Record<ModeKey, FilterState>>>({
    phoenix1: {
      singles: { ...initialFilter },
      doubles: { ...initialFilter },
    },
    phoenix2: {
      singles: { ...initialFilter },
      doubles: { ...initialFilter },
    },
  });
  const [loadingMix, setLoadingMix] = useState<MixKey | null>(DEFAULT_MIX);
  const [messages, setMessages] = useState<Partial<Record<MixKey, string | null>>>({});
  const [isDemo, setIsDemo] = useState(false);
  const [jobs, setJobs] = useState<Partial<Record<MixKey, AnalysisJobStatus | null>>>({});
  const [nowMs, setNowMs] = useState(0);
  const [tabVisible, setTabVisible] = useState(true);

  const loadLatest = useCallback(async (mix: MixKey, showLoading = false) => {
    if (showLoading) setLoadingMix(mix);
    try {
      const archive = archiveForMix(mix, LOCAL_ANALYSIS);
      const reratesArchive = MIXES[mix].archive;
      const [response, reratesResponse] = await Promise.all([
        fetch(archive?.url ?? `/api/analyze?mix=${mix}`, {
          cache: archive ? "force-cache" : "no-store",
        }),
        reratesArchive
          ? fetch(reratesArchive.reratesUrl, { cache: "force-cache" })
          : Promise.resolve(null),
      ]);
      if (response.status === 404) {
        setPayloads((current) => ({ ...current, [mix]: undefined }));
        return false;
      }
      let latest = await readJsonResponse<AnalysisPayload>(response);
      if (latest.mix && latest.mix.key !== mix) {
        throw new Error(`The server returned ${latest.mix.label} data for ${MIXES[mix].label}.`);
      }
      if (reratesResponse) {
        const rerates = await readJsonResponse<Phoenix1ReratePayload>(reratesResponse);
        latest = applyPhoenix1Rerates(latest, rerates);
      }
      setPayloads((current) => ({ ...current, [mix]: latest }));
      setIsDemo(false);
      return true;
    } catch (error) {
      setMessages((current) => ({
        ...current,
        [mix]: error instanceof Error ? error.message : "Could not load the latest analysis.",
      }));
      return false;
    } finally {
      if (showLoading) setLoadingMix((current) => current === mix ? null : current);
    }
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initialMix = mixFromSearchParams(params);
    setActiveMix(initialMix);
    const useDemo = params.get("demo") === "1"
      || process.env.NEXT_PUBLIC_DEMO_MODE === "1";
    if (useDemo) {
      setPayloads(demoPayloads);
      setIsDemo(true);
      setLoadingMix(null);
      return;
    }
    void loadLatest(initialMix, true);
    if (LOCAL_ANALYSIS || MIXES[initialMix].archive) {
      window.localStorage.removeItem(`analysisJobId:${initialMix}`);
      return;
    }
  }, [loadLatest]);

  useEffect(() => {
    if (LOCAL_ANALYSIS || isDemo || MIXES[activeMix].archive) {
      window.localStorage.removeItem(`analysisJobId:${activeMix}`);
      return;
    }
    const storageKey = `analysisJobId:${activeMix}`;
    const legacyJobId = activeMix === DEFAULT_MIX
      ? window.localStorage.getItem("analysisJobId")
      : null;
    const storedJobId = window.localStorage.getItem(storageKey) || legacyJobId;
    if (!storedJobId) return;
    fetch(
      `/api/analyze?mix=${activeMix}&jobId=${encodeURIComponent(storedJobId)}`,
      { cache: "no-store" },
    )
      .then((response) => readJsonResponse<AnalysisJobStatus>(response))
      .then((status) => setJobs((current) => ({ ...current, [activeMix]: status })))
      .catch(() => window.localStorage.removeItem(storageKey));
  }, [activeMix, isDemo]);

  useEffect(() => {
    setNowMs(Date.now());
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    const onVisibility = () => setTabVisible(document.visibilityState === "visible");
    onVisibility();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  const payload = payloads[activeMix] || null;
  const job = jobs[activeMix] || null;
  const activeMode = activeModes[activeMix];
  const loading = loadingMix === activeMix;
  const message = messages[activeMix] || null;
  const archive = archiveForMix(activeMix, LOCAL_ANALYSIS);
  const jobIsActive = job?.status === "queued" || job?.status === "running";
  useEffect(() => {
    if (LOCAL_ANALYSIS || MIXES[activeMix].archive || !job?.id || !jobIsActive) return;
    const pollingMix = activeMix;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetch(
          `/api/analyze?mix=${pollingMix}&jobId=${encodeURIComponent(job.id)}`,
          { cache: "no-store" },
        );
        const status = await readJsonResponse<AnalysisJobStatus>(response);
        if (cancelled) return;
        setJobs((current) => ({ ...current, [pollingMix]: status }));
        if (status.status === "completed") {
          setMessages((current) => ({
            ...current,
            [pollingMix]: `${MIXES[pollingMix].label} analysis complete.`,
          }));
          window.localStorage.removeItem(`analysisJobId:${pollingMix}`);
          if (pollingMix === DEFAULT_MIX) window.localStorage.removeItem("analysisJobId");
          await loadLatest(pollingMix);
          return;
        }
        if (status.status === "failed") return;
      } catch (error) {
        if (!cancelled) {
          setMessages((current) => ({
            ...current,
            [pollingMix]: error instanceof Error
              ? error.message
              : "Could not read analysis progress.",
          }));
        }
      }
      if (!cancelled) timer = window.setTimeout(poll, tabVisible ? 2000 : 10_000);
    };
    timer = window.setTimeout(poll, 0);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [activeMix, job?.id, jobIsActive, loadLatest, tabVisible]);

  const selectMix = (mix: MixKey) => {
    if (mix === activeMix) return;
    setActiveMix(mix);
    const url = new URL(window.location.href);
    if (mix === DEFAULT_MIX) url.searchParams.delete("mix");
    else url.searchParams.set("mix", mix);
    window.history.replaceState({}, "", url);
    if (!isDemo) void loadLatest(mix, true);
  };

  const runAnalysis = async () => {
    if (archive) return;
    if (LOCAL_ANALYSIS) {
      setMessages((current) => ({
        ...current,
        [activeMix]: `Reloading the latest local ${MIXES[activeMix].label} analysis…`,
      }));
      const loaded = await loadLatest(activeMix, true);
      if (loaded) {
        setMessages((current) => ({
          ...current,
          [activeMix]: "Local analysis reloaded from disk.",
        }));
      }
      return;
    }
    setMessages((current) => ({
      ...current,
      [activeMix]: `Starting a ${MIXES[activeMix].label} background refresh…`,
    }));
    try {
      const response = await fetch(`/api/analyze?mix=${activeMix}`, { method: "POST" });
      const body = await readJsonResponse<AnalysisRefreshResponse>(response);
      if (body.outcome === "fresh") {
        setMessages((current) => ({
          ...current,
          [activeMix]: "The current rankings are still fresh; no new job was started.",
        }));
        await loadLatest(activeMix);
        return;
      }
      if (body.outcome === "busy") throw new Error(body.error);
      setJobs((current) => ({ ...current, [activeMix]: body.job }));
      window.localStorage.setItem(`analysisJobId:${activeMix}`, body.job.id);
      setMessages((current) => ({
        ...current,
        [activeMix]: body.outcome === "existing"
          ? "Following the refresh already in progress."
          : null,
      }));
    } catch (error) {
      setMessages((current) => ({
        ...current,
        [activeMix]: error instanceof Error ? error.message : "Analysis failed.",
      }));
    }
  };

  const failedRetryMs = job?.status === "failed" && job.retryAllowedAtUtc && nowMs
    ? Math.max(0, new Date(job.retryAllowedAtUtc).getTime() - nowMs)
    : 0;
  const runDisabled = Boolean(archive) || (LOCAL_ANALYSIS ? loading : jobIsActive || failedRetryMs > 0);

  const modeCharts = payload?.[activeMode] || [];
  const modeSummary = payload?.summary.modes[activeMode];
  const filter = filters[activeMix][activeMode];
  const levels = useMemo(
    () => [...new Set(modeCharts.map((chart) => chart.level))].sort((a, b) => a - b),
    [modeCharts],
  );
  const filteredCharts = useMemo(() => {
    const query = filter.query.trim().toLocaleLowerCase();
    return modeCharts.filter((chart) => {
      if (!filter.showUnrated && chart.difficultyDelta === null) return false;
      if (filter.level !== "All" && chart.level !== Number(filter.level)) return false;
      if (filter.evidence !== "All" && chart.evidenceStatus !== filter.evidence) return false;
      if (query && !`${chart.songName} ${chart.stepArtist || ""}`.toLocaleLowerCase().includes(query)) return false;
      return true;
    });
  }, [filter, modeCharts]);

  const updateFilter = (patch: Partial<FilterState>) => {
    setFilters((current) => ({
      ...current,
      [activeMix]: {
        ...current[activeMix],
        [activeMode]: { ...current[activeMix][activeMode], ...patch },
      },
    }));
  };

  const setActiveMode = (mode: ModeKey) => {
    setActiveModes((current) => ({ ...current, [activeMix]: mode }));
  };

  const statusText = loading
    ? "Loading the latest analysis…"
    : archive
      ? message || (payload
        ? `Archived snapshot generated ${formatRunTime(payload.generatedAtUtc)}`
        : "The archived Phoenix 1 snapshot could not be loaded.")
    : LOCAL_ANALYSIS
      ? message || (payload
        ? `Local results generated ${formatRunTime(payload.generatedAtUtc)}`
        : `No local ${MIXES[activeMix].label} results yet. Run npm run analyze:${activeMix}, then reload.`)
      : jobIsActive && job
      ? `${job.stage[0].toUpperCase()}${job.stage.slice(1)}: ${job.progress.message}`
      : job?.status === "failed"
        ? `Refresh failed: ${job.error || "The worker did not complete."}`
        : message || (payload
          ? `Last completed ${formatRunTime(payload.generatedAtUtc)}`
          : "No stored analysis yet. Run one to create the first ranking.");
  const buttonLabel = jobIsActive
    ? "Refreshing…"
    : archive
      ? "Archived snapshot"
    : LOCAL_ANALYSIS
      ? "Reload local results"
      : failedRetryMs > 0
      ? `Retry in ${durationLabel(failedRetryMs)}`
      : "Refresh rankings";

  return (
    <main>
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <header className="site-header">
        <a className="brand" href="/" aria-label="Pumbility Farmer home">
          <span className="brand-mark">PF</span>
          <span>Pumbility <b>Farmer</b></span>
        </a>
        <div className="run-area">
          <button className="run-button" disabled={runDisabled} onClick={runAnalysis} type="button">
            <span className={jobIsActive ? "spinner" : "run-icon"}>{jobIsActive ? "" : "↻"}</span>
            {buttonLabel}
          </button>
        </div>
      </header>

      <section className="hero" id="top">
        <div className="mix-switcher" role="tablist" aria-label="Pump It Up version">
          {(["phoenix1", "phoenix2"] as MixKey[]).map((mix) => (
            <button
              aria-controls="rankings-dashboard"
              aria-selected={activeMix === mix}
              className={activeMix === mix ? "active" : ""}
              key={mix}
              onClick={() => selectMix(mix)}
              role="tab"
              type="button"
            >
              {MIXES[mix].label}
            </button>
          ))}
        </div>
        <div className="run-status" aria-live="polite">
          <span className={jobIsActive ? "status-live" : "status-dot"} />
          <span>{statusText}</span>
          {isDemo ? <b>Demo data</b> : null}
          {archive && !isDemo ? <b>Frozen archive</b> : null}
          {LOCAL_ANALYSIS && !archive && !isDemo ? <b>Local snapshot</b> : null}
        </div>
        <div className="refresh-meta" aria-live="polite">
          {archive
            ? <span>Frozen: <b>{formatRunTime(archive.frozenAtUtc)}</b></span>
            : payload && nowMs
              ? <span>Refresh age: <b>{refreshAge(payload.generatedAtUtc, nowMs)}</b></span>
              : null}
          {!archive && !LOCAL_ANALYSIS && job?.status === "failed" && failedRetryMs > 0
            ? <span>Retry available in <b>{durationLabel(failedRetryMs)}</b></span>
            : null}
        </div>
        {!LOCAL_ANALYSIS && jobIsActive && job ? (
          <div className="job-progress" aria-label={`${job.progress.percent}% complete`}>
            <div style={{ width: `${Math.max(0, Math.min(100, job.progress.percent))}%` }} />
            <span>
              {job.progress.total > 0
                ? `${job.progress.current.toLocaleString()} / ${job.progress.total.toLocaleString()} players`
                : "Preparing player synchronization"}
              <b>{job.progress.percent}%</b>
            </span>
          </div>
        ) : null}
      </section>

      <section className="dashboard" aria-busy={loading || jobIsActive} id="rankings-dashboard">
        <div className="mode-tabs" role="tablist" aria-label="Chart mode">
          {(["singles", "doubles"] as ModeKey[]).map((mode) => (
            <button
              aria-selected={activeMode === mode}
              className={activeMode === mode ? "active" : ""}
              key={mode}
              onClick={() => setActiveMode(mode)}
              role="tab"
              type="button"
            >
              <span className="mode-letter">{mode === "singles" ? "S" : "D"}</span>
              <span><b>{mode}</b><small>Independent ranking</small></span>
            </button>
          ))}
        </div>

        <div className="stats-grid">
          <div><span>Eligible players</span><strong>{modeSummary?.eligiblePlayers ?? 0}</strong><small>30+ positive-Pumbility {activeMode} scores</small></div>
          <div><span>Charts measured</span><strong>{modeSummary?.measuredCharts ?? 0}</strong><small>of {modeSummary?.catalogCharts ?? 0} level 20+</small></div>
          <div><span>Published charts</span><strong>{modeSummary?.publishedCharts ?? 0}</strong><small>10+ contributors</small></div>
          <div><span>Calibration</span><strong>{modeSummary?.pumbilityPerLevel?.toFixed(1) ?? "—"}</strong><small>Pumbility per level</small></div>
        </div>

        <div className="filter-bar">
          <label className="search-field">
            <span>⌕</span>
            <input
              aria-label="Search songs or step artists"
              onChange={(event) => updateFilter({ query: event.target.value })}
              placeholder="Search songs or step artists"
              type="search"
              value={filter.query}
            />
          </label>
          <label>
            <span>Official level</span>
            <select value={filter.level} onChange={(event) => updateFilter({ level: event.target.value })}>
              <option>All</option>
              {levels.map((level) => <option key={level} value={level}>{activeMode === "singles" ? "S" : "D"}{level}</option>)}
            </select>
          </label>
          <label>
            <span>Evidence</span>
            <select value={filter.evidence} onChange={(event) => updateFilter({ evidence: event.target.value as FilterState["evidence"] })}>
              {(["All", "Published", "Provisional", "Insufficient", "Unrated"] as const).map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label className="unrated-toggle">
            <input checked={filter.showUnrated} onChange={(event) => updateFilter({ showUnrated: event.target.checked })} type="checkbox" />
            <span /> Include unrated
          </label>
        </div>

        <div className="results-heading">
          <div>
            <p>{activeMode} · easiest first</p>
            <h2>Magnitude-based scoring tiers</h2>
          </div>
          <p><b>−</b> easier to score <span /> <b>+</b> harder to score</p>
        </div>

        <div className="tiers">
          {(payload?.effectBands || demoPayloads[activeMix].effectBands).map((group) => (
            <TierSection
              charts={filteredCharts.filter((chart) => chart.effectBandRank === group.rank)}
              key={group.rank}
              name={group.name}
              rank={group.rank}
              range={effectRange(group.low, group.high)}
            />
          ))}
          {filter.showUnrated ? (
            <section className="unrated-section">
              <header><div><p>Awaiting evidence</p><h2>Unrated</h2></div><span>{filteredCharts.filter((chart) => chart.difficultyDelta === null).length} charts</span></header>
              {filteredCharts.filter((chart) => chart.difficultyDelta === null).map((chart) => <ChartCard chart={chart} key={chart.chartId} />)}
            </section>
          ) : null}
        </div>
      </section>

      <footer>
        <p><b>How it works</b> Player skill is the mean positive Pumbility of ranks 11–30 within each mode. Chart estimates use the deduplicated union of each player’s top 20% by Pumbility and most recent 20%; when that produces fewer than 100 scores, the player’s top 100 by Pumbility are used instead.</p>
        <p>Every chart is compared only with charts of the same mode and official level. Extreme tiers require a measured difference of at least half a level; relative percentiles are retained only for within-folder rank.</p>
        <p>Results with fewer than 10 contributors are clearly labeled.</p>
      </footer>
    </main>
  );
}
