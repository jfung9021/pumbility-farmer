"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { SiteHeader } from "../_components/site-header";
import { readJsonResponse } from "../../lib/api-response";
import { hasLimitedData } from "../../lib/chart-evidence";
import { demoPayload } from "../../lib/demo-data";
import {
  formatEstimatedDifficulty,
  truncateEstimatedDifficulty,
} from "../../lib/format-difficulty";
import type {
  AnalysisPayload,
  ChartResult,
  ModeKey,
} from "../../lib/types";

type FilterState = {
  query: string;
  level: string;
  showUnrated: boolean;
};

type GroupingView = "tiers" | "estimated";
type LayoutView = "detailed" | "compact";

const initialFilter: FilterState = {
  query: "",
  level: "All",
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

function refreshAge(value: string, nowMs: number): string {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "unknown age";
  const elapsed = Math.max(0, nowMs - timestamp);
  if (elapsed < 60_000) return "just now";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}m ago`;
  return `${Math.floor(elapsed / 3_600_000)}h ago`;
}

function LimitedDataWarning({ chart, compact = false }: { chart: ChartResult; compact?: boolean }) {
  if (!hasLimitedData(chart.nContributors)) return null;

  return (
    <span
      aria-label={`Limited data: ${chart.nContributors} unique player observations`}
      className={`limited-data-warning${compact ? " compact-warning" : ""}`}
      role="img"
      title="Limited data"
    >
      <b aria-hidden="true">!</b>
      {compact ? null : <span>Limited data</span>}
    </span>
  );
}

function ChartDetails({ chart, headingId }: { chart: ChartResult; headingId?: string }) {
  const delta = chart.difficultyDelta;
  return (
    <>
      <div className="chart-copy">
        <div className="chart-heading">
          <h3 id={headingId}>{chart.songName}</h3>
          <LimitedDataWarning chart={chart} />
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
    </>
  );
}

function ChartCard({ chart }: { chart: ChartResult }) {
  return (
    <article className="chart-card">
      <div className="chart-art jacket" data-chart-type={chart.type} aria-hidden="true">
        {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <span>{chart.difficulty}</span>}
      </div>
      <ChartDetails chart={chart} />
    </article>
  );
}

function CompactChartCard({ chart, onSelect }: { chart: ChartResult; onSelect: (chart: ChartResult) => void }) {
  return (
    <article className="compact-chart-card">
      <button
        aria-label={`View details for ${chart.songName}, ${chart.difficulty}${hasLimitedData(chart.nContributors) ? ", limited data" : ""}`}
        className="compact-chart-button"
        onClick={() => onSelect(chart)}
        type="button"
      >
        <span className="chart-art compact-jacket" data-chart-type={chart.type}>
          {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <span>{chart.difficulty}</span>}
          <LimitedDataWarning chart={chart} compact />
          <span aria-hidden="true" className={`chart-difficulty-badge chart-difficulty-${chart.type.toLowerCase()}`}>
            {chart.level}
          </span>
        </span>
      </button>
    </article>
  );
}

function CompactChartGrid({ charts, onSelect }: { charts: ChartResult[]; onSelect: (chart: ChartResult) => void }) {
  return (
    <div className="compact-chart-grid">
      {charts.length
        ? charts.map((chart) => <CompactChartCard chart={chart} key={chart.chartId} onSelect={onSelect} />)
        : <p className="empty-tier">No charts match the current filters.</p>}
    </div>
  );
}

function ChartDetailDialog({ chart, onClose }: { chart: ChartResult; onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable?.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus();
    };
  }, [onClose]);

  return (
    <div
      className="chart-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        aria-labelledby="chart-detail-dialog-title"
        aria-modal="true"
        className="chart-dialog"
        ref={dialogRef}
        role="dialog"
      >
        <button aria-label="Close chart details" className="chart-dialog-close" onClick={onClose} ref={closeButtonRef} type="button">
          <span aria-hidden="true">&times;</span>
        </button>
        <div className="chart-dialog-body">
          <div className="chart-art jacket chart-dialog-jacket" data-chart-type={chart.type} aria-hidden="true">
            {chart.imageUrl ? <img src={chart.imageUrl} alt="" /> : <span>{chart.difficulty}</span>}
          </div>
          <ChartDetails chart={chart} headingId="chart-detail-dialog-title" />
        </div>
      </div>
    </div>
  );
}

function TierSection({ rank, name, range, charts, compact, onSelect }: {
  rank: number;
  name: string;
  range: string;
  charts: ChartResult[];
  compact: boolean;
  onSelect: (chart: ChartResult) => void;
}) {
  if (compact) {
    return (
      <section className={`tier tier-${groupTone[rank - 1]} tier-compact`} aria-labelledby={`tier-${rank}`}>
        <div className="compact-tier-label"><h2 id={`tier-${rank}`}>{name}</h2></div>
        <CompactChartGrid charts={charts} onSelect={onSelect} />
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

function EstimatedDifficultySection({ charts, compact, mode, value, onSelect }: {
  charts: ChartResult[];
  compact: boolean;
  mode: ModeKey;
  value: number;
  onSelect: (chart: ChartResult) => void;
}) {
  const formatted = formatEstimatedDifficulty(value);
  const label = `${mode === "singles" ? "S" : "D"}${formatted}`;
  const sectionId = `estimated-${mode}-${formatted.replace(".", "-")}`;
  if (compact) {
    return (
      <section className="tier tier-sky tier-compact estimated-tier" aria-labelledby={sectionId}>
        <div className="compact-tier-label"><h2 id={sectionId}>{label}</h2></div>
        <CompactChartGrid charts={charts} onSelect={onSelect} />
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
  const [groupingView, setGroupingView] = useState<GroupingView>("estimated");
  const [layoutView, setLayoutView] = useState<LayoutView>("compact");
  const [filters, setFilters] = useState<Record<ModeKey, FilterState>>({
    singles: { ...initialFilter },
    doubles: { ...initialFilter },
  });
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(0);
  const [selectedChart, setSelectedChart] = useState<ChartResult | null>(null);

  const loadLatest = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const response = await fetch("/api/tier-list", { cache: "no-store" });
      if (response.status === 404) {
        setPayload(null);
        setMessage("No stored combined analysis is available yet.");
        return false;
      }
      const latest = await readJsonResponse<AnalysisPayload>(response);
      if (latest.mix?.key !== "combined") {
        throw new Error("The server returned a version-specific tier list.");
      }
      setPayload(latest);
      setMessage(null);
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
      setMessage(null);
      setLoading(false);
      return;
    }
    void loadLatest(true);
  }, [loadLatest]);

  useEffect(() => {
    setNowMs(Date.now());
    const timer = window.setInterval(() => setNowMs(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const modeCharts = payload?.[activeMode] || [];
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
  const closeChartDialog = useCallback(() => setSelectedChart(null), []);

  return (
    <main>
      <SiteHeader active="tier-list" />

      <section className="hero" id="top">
        <h1>Scoring Difficulty Tier List</h1>
        <div className="refresh-meta" aria-live="polite">
          {payload && nowMs ? <span>Refresh age: <b>{refreshAge(payload.generatedAtUtc, nowMs)}</b></span> : null}
        </div>
        {loading || message ? <p className="tier-load-message" aria-live="polite">{loading ? "Loading tier list..." : message}</p> : null}
      </section>

      <section className="dashboard" aria-busy={loading} id="rankings-dashboard">
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
          <div className="filter-options-row">
            <label>
              <span>Official level</span>
              <select value={filter.level} onChange={(event) => updateFilter({ level: event.target.value })}>
                <option>All</option>
                {levels.map((level) => <option key={level} value={level}>{activeMode === "singles" ? "S" : "D"}{level}</option>)}
              </select>
            </label>
            <label className="unrated-toggle">
              <input checked={filter.showUnrated} onChange={(event) => updateFilter({ showUnrated: event.target.checked })} type="checkbox" />
              <span aria-hidden="true" /> Include unrated
            </label>
          </div>
        </div>

        <div className="results-controls">
          <div className="results-switchers">
            <div aria-label="Difficulty grouping" className="view-switcher" role="group">
              <button
                aria-pressed={groupingView === "estimated"}
                className={groupingView === "estimated" ? "active" : ""}
                onClick={() => setGroupingView("estimated")}
                type="button"
              >Estimated Difficulty</button>
              <button
                aria-pressed={groupingView === "tiers"}
                className={groupingView === "tiers" ? "active" : ""}
                onClick={() => setGroupingView("tiers")}
                type="button"
              >Tier Bands</button>
            </div>
            <div aria-label="Chart layout" className="view-switcher" role="group">
              <button
                aria-pressed={layoutView === "compact"}
                className={layoutView === "compact" ? "active" : ""}
                onClick={() => setLayoutView("compact")}
                type="button"
              >Compact</button>
              <button
                aria-pressed={layoutView === "detailed"}
                className={layoutView === "detailed" ? "active" : ""}
                onClick={() => setLayoutView("detailed")}
                type="button"
              >Detailed</button>
            </div>
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
                  onSelect={setSelectedChart}
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
                  onSelect={setSelectedChart}
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
                <CompactChartGrid charts={unratedCharts} onSelect={setSelectedChart} />
              </section>
            ) : (
              <section className="unrated-section">
                <header><div><p>No estimate</p><h2>Unrated</h2></div><span>{unratedCharts.length} charts</span></header>
                {unratedCharts.map((chart) => <ChartCard chart={chart} key={chart.chartId} />)}
              </section>
            )
          ) : null}
        </div>
      </section>
      {selectedChart ? <ChartDetailDialog chart={selectedChart} onClose={closeChartDialog} /> : null}
    </main>
  );
}
