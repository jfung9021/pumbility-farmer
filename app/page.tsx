"use client";

import { useEffect, useMemo, useState } from "react";
import { demoPayload } from "../lib/demo-data";
import type { AnalysisPayload, ChartResult, EvidenceStatus, ModeKey } from "../lib/types";

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
          <p>Relative scoring difficulty</p>
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
  const [runKey, setRunKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);

  useEffect(() => {
    const useDemo = new URLSearchParams(window.location.search).get("demo") === "1"
      || process.env.NEXT_PUBLIC_DEMO_MODE === "1";
    if (useDemo) {
      setPayload(demoPayload);
      setIsDemo(true);
      setLoading(false);
      return;
    }
    fetch("/api/analyze", { cache: "no-store" })
      .then(async (response) => {
        if (response.status === 404) return null;
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || "Could not load the latest analysis.");
        return body as AnalysisPayload;
      })
      .then(setPayload)
      .catch((error: Error) => setMessage(error.message))
      .finally(() => setLoading(false));
  }, []);

  const runAnalysis = async () => {
    setRunning(true);
    setMessage("Pulling shared scores and calculating separate mode rankings. This may take a few minutes.");
    try {
      const response = await fetch("/api/analyze", {
        method: "POST",
        headers: runKey ? { "X-Run-Secret": runKey } : undefined,
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Analysis failed.");
      setPayload(body as AnalysisPayload);
      setIsDemo(false);
      setMessage("Analysis complete. Both ranking sets have been refreshed.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Analysis failed.");
    } finally {
      setRunning(false);
    }
  };

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
          <details className="run-key">
            <summary>Run access</summary>
            <label>
              Optional run key
              <input value={runKey} onChange={(event) => setRunKey(event.target.value)} type="password" />
            </label>
          </details>
          <button className="run-button" disabled={running} onClick={runAnalysis} type="button">
            <span className={running ? "spinner" : "run-icon"}>{running ? "" : "↻"}</span>
            {running ? "Analyzing…" : "Run analysis"}
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
          <span className={running ? "status-live" : "status-dot"} />
          {loading ? "Loading the latest analysis…" : message || (payload
            ? `Last completed ${formatRunTime(payload.generatedAtUtc)}`
            : "No stored analysis yet. Run one to create the first ranking.")}
          {isDemo ? <b>Demo data</b> : null}
        </div>
      </section>

      <section className="dashboard" aria-busy={loading || running}>
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
            <h2>Relative scoring tiers</h2>
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
        <p>Negative difference = easier than the average official level. Results with fewer than 10 contributors are clearly labeled.</p>
      </footer>
    </main>
  );
}
