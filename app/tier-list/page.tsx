"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { readJsonResponse } from "../../lib/api-response";
import { demoPayload } from "../../lib/demo-data";
import {
  formatEstimatedDifficulty,
  truncateEstimatedDifficulty,
} from "../../lib/format-difficulty";
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

type GroupingView = "tiers" | "estimated";
type LayoutView = "detailed" | "compact";

const initialFilter: FilterState = {
  query: "",
  level: "All",
  evidence: "All",
  showUnrated: false,
};

const groupTone = ["lime", "green", "mint", "slate", "orange", "rose", "red"];

function signed(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function signedBoundary(value: number): string {
  return signed(value, Number.isInteger(value * 100) ? 2 : 3);
}

function effectRange(low: number | null, high: number | null): string {
  if (low === null) return `difference < ${signedBoundary(high ?? -0.5)}`;
  if (high === null) return `difference > ${signedBoundary(low)}`;
  return `${signedBoundary(low)} to ${signedBoundary(high)}`;
}

function chartGrade(chart: ChartResult): string {
  if (chart.estimatedDifficulty === null) return "-";
  const prefix = chart.type === "Single" ? "S" : "D";
  return `${prefix}${formatEstimatedDifficulty(chart.estimatedDifficulty)}`;
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
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
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
        <p>
          {chart.stepArtist || "Unknown step artist"}
          {chart.noteCount ? ` - ${chart.noteCount.toLocaleString()} notes` : ""}
        </p>
        <div className="chart-meta">
          <span><b>{chart.difficulty}</b> official</span>
          <span><b>{chartGrade(chart)}</b> estimated</span>
          <span><b>{chart.nContributors}</b> contributors</span>
          {chart.phoenix1Contributors !== undefined && chart.phoenix2Contributors !== undefined ? (
            <span><b>{chart.phoenix1Contributors}/{chart.phoenix2Contributors}</b> P1/P2</span>
          ) : null}
          {chart.levelRank !== null && chart.levelComparisonCharts !== null ? (
            <span><b>#{chart.levelRank}</b> of {chart.levelComparisonCharts} in {chart.difficulty}</span>
          ) : null}
        </div>
      </div>
      <div className={`delta ${delta !== null && delta < 0 ? "delta-easy" : "delta-hard"}`}>
        <span>difference</span>
        <strong>{delta === null ? "-" : signed(delta)}</strong>
        {chart.difficultyCi95Low !== null && chart.difficultyCi95High !== null ? (
          <small>{formatEstimatedDifficulty(chart.difficultyCi95Low)}-{formatEstimatedDifficulty(chart.difficultyCi95High)} CI</small>
        ) : null}
      </div>
    </article>
  );
}

function CompactChartCard({ chart }: { chart: ChartResult }) {
  return (
    <article className="compact-chart-card" title={`${chart.songName} (${chart.difficulty})`}>
      <div className="compact-jacket" aria-hidden="true">
        {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <span>{chart.difficulty}</span>}
      </div>
      <h3>{chart.songName}</h3>
    </article>
  );
}

function CompactChartGrid({ charts }: { charts: ChartResult[] }) {
  return (
    <div className="compact-chart-grid">
      {charts.length
        ? charts.map((chart) => <CompactChartCard chart={chart} key={chart.chartId} />)
        : <p className="empty-tier">No charts match the current filters.</p>}
    </div>
  );
}

function TierSection({ rank, name, range, charts, compact }: {
  rank: number;
  name: string;
  range: string;
  charts: ChartResult[];
  compact: boolean;
}) {
  if (compact) {
    return (
      <section className={`tier tier-${groupTone[rank - 1]} tier-compact`} aria-labelledby={`tier-${rank}`}>
        <div className="compact-tier-label"><h2 id={`tier-${rank}`}>{name}</h2></div>
        <CompactChartGrid charts={charts} />
      </section>
    );
  }
  return (
    <section className={`tier tier-${groupTone[rank - 1]}`} aria-labelledby={`tier-${rank}`}>
      <header className="tier-header">
        <div className="tier-rank">{String(rank).padStart(2, "0")}</div>
        <div><p>{range}</p><h2 id={`tier-${rank}`}>{name}</h2></div>
        <span className="tier-count">{charts.length} chart{charts.length === 1 ? "" : "s"}</span>
      </header>
      <div className="tier-list">
        {charts.length
          ? charts.map((chart) => <ChartCard chart={chart} key={chart.chartId} />)
          : <p className="empty-tier">No charts match the current filters.</p>}
      </div>
    </section>
  );
}

function EstimatedDifficultySection({ charts, compact, mode, value }: {
  charts: ChartResult[];
  compact: boolean;
  mode: ModeKey;
  value: number;
}) {
  const formatted = formatEstimatedDifficulty(value);
  const label = `${mode === "singles" ? "S" : "D"}${formatted}`;
  const sectionId = `estimated-${mode}-${formatted.replace(".", "-")}`;
  if (compact) {
    return (
      <section className="tier tier-sky tier-compact estimated-tier" aria-labelledby={sectionId}>
        <div className="compact-tier-label"><h2 id={sectionId}>{label}</h2></div>
        <CompactChartGrid charts={charts} />
      </section>
    );
  }
  return (
    <section className="tier tier-sky estimated-tier" aria-labelledby={sectionId}>
      <header className="tier-header">
        <div className="tier-rank">{formatted}</div>
        <div><p>Estimated scoring difficulty</p><h2 id={sectionId}>{label}</h2></div>
        <span className="tier-count">{charts.length} chart{charts.length === 1 ? "" : "s"}</span>
      </header>
      <div className="tier-list">
        {charts.map((chart) => <ChartCard chart={chart} key={chart.chartId} />)}
      </div>
    </section>
  );
}

export default function TierListPage() {
  const [payload, setPayload] = useState<AnalysisPayload | null>(null);
  const [activeMode, setActiveMode] = useState<ModeKey>("singles");
  const [groupingView, setGroupingView] = useState<GroupingView>("tiers");
  const [layoutView, setLayoutView] = useState<LayoutView>("detailed");
  const [filters, setFilters] = useState<Record<ModeKey, FilterState>>({
    singles: { ...initialFilter },
    doubles: { ...initialFilter },
  });
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);
  const [job, setJob] = useState<AnalysisJobStatus | null>(null);
  const [nowMs, setNowMs] = useState(0);
  const [tabVisible, setTabVisible] = useState(true);

  const loadLatest = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const response = await fetch("/api/tier-list", { cache: "no-store" });
      if (response.status === 404) {
        setPayload(null);
        return false;
      }
      const latest = await readJsonResponse<AnalysisPayload>(response);
      if (latest.mix?.key !== "combined") {
        throw new Error("The server returned a version-specific tier list.");
      }
      setPayload(latest);
      setIsDemo(false);
      return true;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load the combined tier list.");
      return false;
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const useDemo = params.get("demo") === "1" || process.env.NEXT_PUBLIC_DEMO_MODE === "1";
    if (useDemo) {
      setPayload(demoPayload);
      setIsDemo(true);
      setLoading(false);
      return;
    }
    void loadLatest(true);
  }, [loadLatest]);

  useEffect(() => {
    const storageKey = "analysisJobId:phoenix2";
    if (LOCAL_ANALYSIS || isDemo) {
      window.localStorage.removeItem(storageKey);
      return;
    }
    const storedJobId = window.localStorage.getItem(storageKey)
      || window.localStorage.getItem("analysisJobId");
    if (!storedJobId) return;
    fetch(`/api/analyze?mix=phoenix2&jobId=${encodeURIComponent(storedJobId)}`, { cache: "no-store" })
      .then((response) => readJsonResponse<AnalysisJobStatus>(response))
      .then(setJob)
      .catch(() => window.localStorage.removeItem(storageKey));
  }, [isDemo]);

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

  const jobIsActive = job?.status === "queued" || job?.status === "running";
  useEffect(() => {
    if (LOCAL_ANALYSIS || !job?.id || !jobIsActive) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetch(
          `/api/analyze?mix=phoenix2&jobId=${encodeURIComponent(job.id)}`,
          { cache: "no-store" },
        );
        const status = await readJsonResponse<AnalysisJobStatus>(response);
        if (cancelled) return;
        setJob(status);
        if (status.status === "completed") {
          setMessage("Combined Phoenix analysis complete.");
          window.localStorage.removeItem("analysisJobId:phoenix2");
          window.localStorage.removeItem("analysisJobId");
          await loadLatest();
          return;
        }
        if (status.status === "failed") return;
      } catch (error) {
        if (!cancelled) {
          setMessage(error instanceof Error ? error.message : "Could not read analysis progress.");
        }
      }
      if (!cancelled) timer = window.setTimeout(poll, tabVisible ? 2000 : 10_000);
    };
    timer = window.setTimeout(poll, 0);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [job?.id, jobIsActive, loadLatest, tabVisible]);

  const runAnalysis = async () => {
    if (LOCAL_ANALYSIS) {
      setMessage("Reloading the latest local combined analysis...");
      if (await loadLatest(true)) setMessage("Local combined analysis reloaded from disk.");
      return;
    }
    setMessage("Starting a Phoenix 2 refresh and combined analysis...");
    try {
      const response = await fetch("/api/analyze?mix=phoenix2", { method: "POST" });
      const body = await readJsonResponse<AnalysisRefreshResponse>(response);
      if (body.outcome === "fresh") {
        setMessage("The current rankings are still fresh; no new job was started.");
        await loadLatest();
        return;
      }
      if (body.outcome === "busy") throw new Error(body.error);
      setJob(body.job);
      window.localStorage.setItem("analysisJobId:phoenix2", body.job.id);
      setMessage(body.outcome === "existing" ? "Following the refresh already in progress." : null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Analysis failed.");
    }
  };

  const failedRetryMs = job?.status === "failed" && job.retryAllowedAtUtc && nowMs
    ? Math.max(0, new Date(job.retryAllowedAtUtc).getTime() - nowMs)
    : 0;
  const runDisabled = LOCAL_ANALYSIS ? loading : jobIsActive || failedRetryMs > 0;
  const modeCharts = payload?.[activeMode] || [];
  const modeSummary = payload?.summary.modes[activeMode];
  const filter = filters[activeMode];
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
      return !query || `${chart.songName} ${chart.stepArtist || ""}`.toLocaleLowerCase().includes(query);
    });
  }, [filter, modeCharts]);
  const estimatedGroups = useMemo(() => {
    const groups = new Map<number, ChartResult[]>();
    for (const chart of filteredCharts) {
      if (chart.estimatedDifficulty === null) continue;
      const bucket = truncateEstimatedDifficulty(chart.estimatedDifficulty);
      const charts = groups.get(bucket) ?? [];
      charts.push(chart);
      groups.set(bucket, charts);
    }
    return [...groups.entries()]
      .sort(([left], [right]) => left - right)
      .map(([value, charts]) => ({
        value,
        charts: charts.sort((left, right) =>
          (left.estimatedDifficulty ?? 0) - (right.estimatedDifficulty ?? 0)
          || left.songName.localeCompare(right.songName)),
      }));
  }, [filteredCharts]);
  const unratedCharts = useMemo(
    () => filteredCharts.filter((chart) => chart.difficultyDelta === null),
    [filteredCharts],
  );
  const updateFilter = (patch: Partial<FilterState>) => {
    setFilters((current) => ({
      ...current,
      [activeMode]: { ...current[activeMode], ...patch },
    }));
  };

  const statusText = loading
    ? "Loading the combined tier list..."
    : LOCAL_ANALYSIS
      ? message || (payload
        ? `Local results generated ${formatRunTime(payload.generatedAtUtc)}`
        : "No local combined results yet. Run npm run analyze:recommendations, then reload.")
      : jobIsActive && job
        ? `${job.stage[0].toUpperCase()}${job.stage.slice(1)}: ${job.progress.message}`
        : job?.status === "failed"
          ? `Refresh failed: ${job.error || "The worker did not complete."}`
          : message || (payload
            ? `Last completed ${formatRunTime(payload.generatedAtUtc)}`
            : "No stored combined analysis yet. Run one to create the first ranking.");
  const buttonLabel = jobIsActive
    ? "Refreshing..."
    : LOCAL_ANALYSIS
      ? "Reload local results"
      : failedRetryMs > 0
        ? `Retry in ${durationLabel(failedRetryMs)}`
        : "Refresh rankings";

  return (
    <main>
      <header className="site-header">
        <a className="brand" href="/" aria-label="Pumbility Farmer home">
          <span className="brand-mark">PF</span>
          <span>Pumbility <b>Farmer</b></span>
        </a>
        <div className="header-actions tier-header-actions">
          <nav className="page-nav" aria-label="Primary navigation">
            <Link href="/recommendations">Recommendations</Link>
            <span>Tier List</span>
          </nav>
          <div className="run-area">
            <button aria-label={buttonLabel} className="run-button" disabled={runDisabled} onClick={runAnalysis} type="button">
              <span className={jobIsActive ? "spinner" : "run-icon"} aria-hidden="true">
                {jobIsActive ? "" : "\u21bb"}
              </span>
              <span className="run-button-label">{buttonLabel}</span>
            </button>
          </div>
        </div>
      </header>

      <section className="hero" id="top">
        <p className="home-eyebrow">PHOENIX 1 + PHOENIX 2 EVIDENCE</p>
        <h1>Combined scoring tier list</h1>
        <div className="run-status" aria-live="polite">
          <span className={jobIsActive ? "status-live" : "status-dot"} />
          <span>{statusText}</span>
          {isDemo ? <b>Demo data</b> : null}
          {LOCAL_ANALYSIS && !isDemo ? <b>Local snapshot</b> : null}
        </div>
        <div className="refresh-meta" aria-live="polite">
          {payload && nowMs ? <span>Refresh age: <b>{refreshAge(payload.generatedAtUtc, nowMs)}</b></span> : null}
          {!LOCAL_ANALYSIS && job?.status === "failed" && failedRetryMs > 0
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
          <div><span>Eligible players</span><strong>{modeSummary?.eligiblePlayers ?? 0}</strong><small>normalized P1 + P2 histories</small></div>
          <div><span>Charts measured</span><strong>{modeSummary?.measuredCharts ?? 0}</strong><small>of {modeSummary?.catalogCharts ?? 0} current charts</small></div>
          <div><span>Published charts</span><strong>{modeSummary?.publishedCharts ?? 0}</strong><small>10+ contributors</small></div>
          <div><span>Evidence scale</span><strong>Level</strong><small>version-normalized residuals</small></div>
        </div>

        <div className="filter-bar">
          <label className="search-field">
            <span aria-hidden="true">{"\u2315"}</span>
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
            <p>{activeMode} - easiest first</p>
            <h2>{groupingView === "tiers" ? "Magnitude-based scoring tiers" : "Estimated-difficulty groups"}</h2>
          </div>
          <div className="results-controls">
            <div className="results-switchers">
              <div className="view-switcher" role="group" aria-label="Group charts by">
                <button
                  aria-pressed={groupingView === "tiers"}
                  className={groupingView === "tiers" ? "active" : ""}
                  onClick={() => setGroupingView("tiers")}
                  type="button"
                >Tier bands</button>
                <button
                  aria-pressed={groupingView === "estimated"}
                  className={groupingView === "estimated" ? "active" : ""}
                  onClick={() => setGroupingView("estimated")}
                  type="button"
                >Estimated difficulty</button>
              </div>
              <div className="view-switcher layout-switcher" role="group" aria-label="Chart layout">
                <button
                  aria-pressed={layoutView === "detailed"}
                  className={layoutView === "detailed" ? "active" : ""}
                  onClick={() => setLayoutView("detailed")}
                  type="button"
                >Detailed</button>
                <button
                  aria-pressed={layoutView === "compact"}
                  className={layoutView === "compact" ? "active" : ""}
                  onClick={() => setLayoutView("compact")}
                  type="button"
                >Compact</button>
              </div>
            </div>
            {groupingView === "tiers"
              ? <p className="results-legend"><b>-</b> easier to score <span /> <b>+</b> harder to score</p>
              : <p className="results-legend">Grouped by truncated one-decimal estimate</p>}
          </div>
        </div>

        <div className="tiers">
          {groupingView === "tiers"
            ? (payload?.effectBands || demoPayload.effectBands).map((group) => (
                <TierSection
                  charts={filteredCharts.filter((chart) => chart.effectBandRank === group.rank)}
                  compact={layoutView === "compact"}
                  key={group.rank}
                  name={group.name}
                  rank={group.rank}
                  range={effectRange(group.low, group.high)}
                />
              ))
            : estimatedGroups.map((group) => (
                <EstimatedDifficultySection
                  charts={group.charts}
                  compact={layoutView === "compact"}
                  key={group.value}
                  mode={activeMode}
                  value={group.value}
                />
              ))}
          {groupingView === "estimated" && estimatedGroups.length === 0 ? (
            <section className="unrated-section">
              <header><div><p>Current filters</p><h2>No estimated charts</h2></div><span>0 charts</span></header>
            </section>
          ) : null}
          {filter.showUnrated ? (
            layoutView === "compact" ? (
              <section className="tier tier-compact unrated-section" aria-labelledby="unrated-compact">
                <div className="compact-tier-label"><h2 id="unrated-compact">Unrated</h2></div>
                <CompactChartGrid charts={unratedCharts} />
              </section>
            ) : (
              <section className="unrated-section">
                <header><div><p>Awaiting evidence</p><h2>Unrated</h2></div><span>{unratedCharts.length} charts</span></header>
                {unratedCharts.map((chart) => <ChartCard chart={chart} key={chart.chartId} />)}
              </section>
            )
          ) : null}
        </div>
      </section>

      <footer>
        <p><b>How it works</b> Phoenix 1 and Phoenix 2 player residuals are normalized within version and mode, then combined against the current Phoenix 2 catalog.</p>
        <p>A Phoenix 2 score replaces the same player's Phoenix 1 score for the same chart. Removed Phoenix 1 charts are excluded.</p>
        <p>Every chart is compared only with charts of the same mode and current official level. Results with fewer than 10 contributors remain labeled.</p>
      </footer>
    </main>
  );
}
