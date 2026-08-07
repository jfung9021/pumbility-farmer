"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { demoPayload } from "../lib/demo-data";
import { readJsonResponse } from "../lib/api-response";
import type {
  AnalysisJobStatus,
  AnalysisPayload,
  AnalysisRefreshResponse,
  ChartResult,
  EvidenceStatus,
  ModeKey,
} from "../lib/types";

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

const groupTone = ["lime", "green", "mint", "cyan", "sky", "slate", "amber", "orange", "rose", "red"];

function signed(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
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

function TierSection({ rank, name, charts }: { rank: number; name: string; charts: ChartResult[] }) {
  return (
    <section className={`tier tier-${groupTone[rank - 1]}`} aria-labelledby={`tier-${rank}`}>
      <header className="tier-header">
        <div className="tier-rank">{String(rank).padStart(2, "0")}</div>
        <div>
          <p>Within-level scoring difficulty</p>
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
  const [payload, setPayload] = useState<AnalysisPayload | null>(null);
  const [activeMode, setActiveMode] = useState<ModeKey>("singles");
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
      const response = await fetch("/api/analyze", { cache: "no-store" });
      if (response.status === 404) {
        setPayload(null);
        return;
      }
      const latest = await readJsonResponse<AnalysisPayload>(response);
      setPayload(latest);
      setIsDemo(false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load the latest analysis.");
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const useDemo = new URLSearchParams(window.location.search).get("demo") === "1"
      || process.env.NEXT_PUBLIC_DEMO_MODE === "1";
    if (useDemo) {
      setPayload(demoPayload);
      setIsDemo(true);
      setLoading(false);
      return;
    }
    void loadLatest(true);
    const storedJobId = window.localStorage.getItem("analysisJobId");
    if (storedJobId) {
      fetch(`/api/analyze?jobId=${encodeURIComponent(storedJobId)}`, { cache: "no-store" })
        .then((response) => readJsonResponse<AnalysisJobStatus>(response))
        .then(setJob)
        .catch(() => window.localStorage.removeItem("analysisJobId"));
    }
  }, [loadLatest]);

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
    if (!job?.id || !jobIsActive) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetch(`/api/analyze?jobId=${encodeURIComponent(job.id)}`, {
          cache: "no-store",
        });
        const status = await readJsonResponse<AnalysisJobStatus>(response);
        if (cancelled) return;
        setJob(status);
        if (status.status === "completed") {
          setMessage("Analysis complete. Both ranking sets have been refreshed.");
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
    setMessage("Starting a background refresh…");
    try {
      const response = await fetch("/api/analyze", { method: "POST" });
      const body = await readJsonResponse<AnalysisRefreshResponse>(response);
      if (body.outcome === "fresh") {
        setMessage("The current rankings are still fresh; no new job was started.");
        await loadLatest();
        return;
      }
      setJob(body.job);
      window.localStorage.setItem("analysisJobId", body.job.id);
      setMessage(body.outcome === "existing" ? "Following the refresh already in progress." : null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Analysis failed.");
    }
  };

  const failedRetryMs = job?.status === "failed" && job.retryAllowedAtUtc && nowMs
    ? Math.max(0, new Date(job.retryAllowedAtUtc).getTime() - nowMs)
    : 0;
  const runDisabled = jobIsActive || failedRetryMs > 0;

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
      if (query && !`${chart.songName} ${chart.stepArtist || ""}`.toLocaleLowerCase().includes(query)) return false;
      return true;
    });
  }, [filter, modeCharts]);

  const updateFilter = (patch: Partial<FilterState>) => {
    setFilters((current) => ({
      ...current,
      [activeMode]: { ...current[activeMode], ...patch },
    }));
  };

  const statusText = loading
    ? "Loading the latest analysis…"
    : jobIsActive && job
      ? `${job.stage[0].toUpperCase()}${job.stage.slice(1)}: ${job.progress.message}`
      : job?.status === "failed"
        ? `Refresh failed: ${job.error || "The worker did not complete."}`
        : message || (payload
          ? `Last completed ${formatRunTime(payload.generatedAtUtc)}`
          : "No stored analysis yet. Run one to create the first ranking.");
  const buttonLabel = jobIsActive
    ? "Refreshing…"
    : failedRetryMs > 0
      ? `Retry in ${durationLabel(failedRetryMs)}`
      : "Refresh rankings";

  return (
    <main>
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <header className="site-header">
        <a className="brand" href="#top" aria-label="Pumbility Farmer home">
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
        <div className="eyebrow"><span /> Phoenix 2 score intelligence</div>
        <h1>Find the charts that<br /><em>give more back.</em></h1>
        <p>
          Player-normalized scoring difficulty across every level 20+ chart.
          Singles and Doubles are modeled, calibrated, and ranked independently.
        </p>
        <div className="run-status" aria-live="polite">
          <span className={jobIsActive ? "status-live" : "status-dot"} />
          <span>{statusText}</span>
          {isDemo ? <b>Demo data</b> : null}
        </div>
        <div className="refresh-meta" aria-live="polite">
          {payload && nowMs ? <span>Refresh age: <b>{refreshAge(payload.generatedAtUtc, nowMs)}</b></span> : null}
          {job?.status === "failed" && failedRetryMs > 0
            ? <span>Retry available in <b>{durationLabel(failedRetryMs)}</b></span>
            : null}
        </div>
        {jobIsActive && job ? (
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

      <section className="dashboard" aria-busy={loading || jobIsActive}>
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
          <div><span>Eligible players</span><strong>{modeSummary?.eligiblePlayers ?? 0}</strong><small>30+ {activeMode} scores</small></div>
          <div><span>Charts measured</span><strong>{modeSummary?.measuredCharts ?? 0}</strong><small>of {modeSummary?.catalogCharts ?? 0} level 20+</small></div>
          <div><span>Published charts</span><strong>{modeSummary?.publishedCharts ?? 0}</strong><small>10+ contributors</small></div>
          <div><span>Calibration</span><strong>{modeSummary?.pumbilityPerLevel.toFixed(1) ?? "—"}</strong><small>Pumbility per level</small></div>
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
            <h2>Within-level scoring tiers</h2>
          </div>
          <p><b>−</b> easier to score <span /> <b>+</b> harder to score</p>
        </div>

        <div className="tiers">
          {(payload?.relativeGroups || demoPayload.relativeGroups).map((group) => (
            <TierSection
              charts={filteredCharts.filter((chart) => chart.relativeGroupRank === group.rank)}
              key={group.rank}
              name={group.name}
              rank={group.rank}
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
        <p><b>How it works</b> Player skill is the mean Pumbility of ranks 11–30 within each mode. Only each player’s top 100 mode scores contribute to chart estimates.</p>
        <p>Every chart is compared only with charts of the same mode and official level. Tiers are within-level deciles; the numerical estimate is centered on the typical chart at that level.</p>
        <p>Results with fewer than 10 contributors are clearly labeled.</p>
      </footer>
    </main>
  );
}
