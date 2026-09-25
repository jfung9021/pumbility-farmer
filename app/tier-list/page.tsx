"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { RefreshMeta } from "../_components/refresh-meta";
import { ChartVideoLink } from "../_components/chart-video-link";
import { SiteHeader } from "../_components/site-header";
import { readJsonResponse } from "../../lib/api-response";
import { demoPayload } from "../../lib/demo-data";
import {
  formatCoopEstimatedDifficulty,
  formatEstimatedDifficulty,
} from "../../lib/format-difficulty";
import { tierMetricFromSearchParams, tierModeFromSearchParams } from "../../lib/page-view-state";
import {
  clearingPercentileRange,
  estimatedTierGroups,
  hasLimitedTierData,
  selectedTierMetric,
  sortTierCharts,
  tierBandCharts,
  tierMetricAvailability,
  tierSupportLabel,
} from "../../lib/tier-metrics";
import type {
  AnalysisPayload,
  ChartResult,
  ModeKey,
  TierMetricKey,
} from "../../lib/types";

type FilterState = {
  query: string;
  level: string;
};

type GroupingView = "tiers" | "estimated";
type LayoutView = "detailed" | "compact";

const initialFilter: FilterState = {
  query: "",
  level: "All",
};

const groupTone = ["lime", "green", "mint", "slate", "orange", "rose", "red"];
const metricLabels: Record<TierMetricKey, string> = { scoring: "Scoring", clearing: "Clearing", pumbility: "Pumbility" };
const metricTitles: Record<TierMetricKey, string> = {
  scoring: "Scoring Difficulty Tier List",
  clearing: "Clearing Difficulty Tier List",
  pumbility: "Pumbility Tier List",
};

function signed(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function chartGrade(chart: ChartResult, metric: TierMetricKey): string {
  const estimate = selectedTierMetric(chart, metric).estimatedDifficulty;
  if (estimate === null) return "-";
  if (chart.type === "CoOp") {
    const continuous = chart.difficultyModelContinuous;
    return (typeof continuous === "number" && Number.isFinite(continuous)
      ? continuous
      : estimate).toFixed(1);
  }
  const prefix = chart.type === "Single" ? "S" : "D";
  return `${prefix}${formatEstimatedDifficulty(estimate)}`;
}

function chartCountLabel(chart: ChartResult): string {
  return chart.type === "CoOp" ? `${chart.level}x` : String(chart.level);
}

function LimitedDataWarning({ chart, metric, compact = false }: { chart: ChartResult; metric: TierMetricKey; compact?: boolean }) {
  if (!hasLimitedTierData(chart, metric)) return null;

  return (
    <span
      aria-label={`Limited data: ${tierSupportLabel(chart, metric)}`}
      className={`limited-data-warning${compact ? " compact-warning" : ""}`}
      role="img"
      title="Limited data"
    >
      <b aria-hidden="true">!</b>
      {compact ? null : <span>Limited data</span>}
    </span>
  );
}

function fallbackWhatIfEstimates(chart: ChartResult): NonNullable<ChartResult["whatIfEstimates"]> {
  const minimumLevel = Math.max(16, chart.level - 1);
  return Array.from({ length: chart.level + 1 - minimumLevel + 1 }, (_, offset) => minimumLevel + offset)
    .filter((level) => level !== chart.level)
    .map((level) => ({ level, estimatedDifficulty: null }));
}

function WhatIfDifficulty({ chart }: { chart: ChartResult }) {
  const [selectedLevel, setSelectedLevel] = useState<number | null>(null);
  const prefix = chart.type === "Single" ? "S" : "D";
  const estimates = chart.whatIfEstimates ?? fallbackWhatIfEstimates(chart);
  const selectedEstimate = selectedLevel === null
    ? null
    : estimates.find((estimate) => estimate.level === selectedLevel)?.estimatedDifficulty ?? null;

  return (
    <div className="what-if-control">
      <span>If</span>
      <select
        aria-label={`Hypothetical official difficulty for ${chart.songName}`}
        onChange={(event) => setSelectedLevel(event.target.value ? Number(event.target.value) : null)}
        value={selectedLevel ?? ""}
      >
        <option value="">{prefix}??</option>
        {estimates.map((estimate) => (
          <option
            disabled={estimate.estimatedDifficulty === null}
            key={estimate.level}
            value={estimate.level}
          >
            {prefix}{estimate.level}{estimate.estimatedDifficulty === null ? " — unavailable" : ""}
          </option>
        ))}
      </select>
      <span>then</span>
      <span className="what-if-result">
        {selectedEstimate === null ? "—" : `${prefix}${formatEstimatedDifficulty(selectedEstimate)}`}
      </span>
    </div>
  );
}

function ChartDetails({ chart, metric, headingId }: { chart: ChartResult; metric: TierMetricKey; headingId?: string }) {
  const selected = selectedTierMetric(chart, metric);
  const delta = selected.difficultyDelta;
  const isCoop = chart.type === "CoOp";
  const clearing = chart.tierMetrics?.clearing;
  const percentileRange = clearingPercentileRange(clearing);
  const pumbility = chart.tierMetrics?.pumbility;
  return (
    <>
      <div className="chart-copy">
        <div className="chart-heading">
          <h3 id={headingId}>{chart.songName}</h3>
          <LimitedDataWarning chart={chart} metric={metric} />
        </div>
        <p>
          {chart.stepArtist || "Unknown step artist"}
          {chart.noteCount ? ` - ${chart.noteCount.toLocaleString()} notes` : ""}
        </p>
        <div className="chart-meta">
          {isCoop
            ? <span><b>{chart.difficulty}</b> chart</span>
            : <span><b>{chart.difficulty}</b> official</span>}
          <span><b>{chartGrade(chart, metric)}</b> estimated</span>
          <span>{selected.evidenceStatus}</span>
          {metric === "scoring" ? <span><b>{chart.nContributors}</b> contributors</span> : null}
          {metric === "scoring" && chart.phoenix1Contributors !== undefined && chart.phoenix2Contributors !== undefined ? (
            <span><b>{chart.phoenix1Contributors}/{chart.phoenix2Contributors}</b> P1/P2</span>
          ) : null}
          {!isCoop && selected.levelRank !== null && selected.levelComparisonCharts !== null ? (
            <span><b>#{selected.levelRank}</b> of {selected.levelComparisonCharts} in {chart.difficulty}</span>
          ) : null}
        </div>
        {metric === "clearing" && clearing ? (
          <div className="chart-meta metric-details">
            <span><b>{clearing.ratedClearCount}/{clearing.clearCount}</b> clearers with usable skill</span>
            <span><b>{clearing.selectedCount}</b> selected clearers</span>
            <span><b>{clearing.missingSkillCount}</b> missing skill</span>
            {percentileRange?.lower != null && percentileRange.upper != null ? (
              <span>{percentileRange.label} percentile skill: <b>{percentileRange.lower.toFixed(2)}–{percentileRange.upper.toFixed(2)}</b> inclusive</span>
            ) : null}
            {clearing.meanSkill !== null ? <span>Selected mean skill: <b>{clearing.meanSkill.toFixed(2)}</b></span> : null}
            {clearing.folderReferenceSkill !== null ? <span>Folder reference skill: <b>{clearing.folderReferenceSkill.toFixed(2)}</b></span> : null}
          </div>
        ) : null}
        {metric === "pumbility" && pumbility ? (
          <div className="chart-meta metric-details">
            <span>Scoring: <b>{chartGrade(chart, "scoring")}</b></span>
            <span>Clearing: <b>{chartGrade(chart, "clearing")}</b></span>
            <span>Average: <b>{chartGrade(chart, "pumbility")}</b></span>
            <span><b>{pumbility.scoringSupportCount}</b> scoring contributors</span>
            <span><b>{pumbility.clearingSupportCount}</b> selected clearers</span>
          </div>
        ) : null}
      </div>
      {isCoop ? null : (
        <div className={`delta ${delta !== null && delta < 0 ? "delta-easy" : "delta-hard"}`}>
          <span>difference</span>
          <strong>{delta === null ? "-" : signed(delta)}</strong>
          {metric === "scoring" && chart.difficultyCi95Low !== null && chart.difficultyCi95High !== null ? (
            <small>{formatEstimatedDifficulty(chart.difficultyCi95Low)}-{formatEstimatedDifficulty(chart.difficultyCi95High)} CI</small>
          ) : null}
          {metric === "scoring" ? <WhatIfDifficulty chart={chart} /> : null}
        </div>
      )}
    </>
  );
}

function ChartCard({ chart, metric }: { chart: ChartResult; metric: TierMetricKey }) {
  return (
    <article className={`chart-card${chart.type === "CoOp" ? " chart-card-coop" : ""}`}>
      <div className="chart-art-rail">
        <div className="chart-art jacket" data-chart-type={chart.type} aria-hidden="true">
          {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <span>{chart.difficulty}</span>}
        </div>
        <ChartVideoLink
          chartId={chart.chartId}
          difficulty={chart.difficulty}
          songName={chart.songName}
          variant="tier"
        />
      </div>
      <ChartDetails chart={chart} metric={metric} />
    </article>
  );
}

function CompactChartCard({ chart, metric, onSelect }: { chart: ChartResult; metric: TierMetricKey; onSelect: (chart: ChartResult) => void }) {
  return (
    <article className="compact-chart-card">
      <button
        aria-label={`View details for ${chart.songName}, ${chart.difficulty}${hasLimitedTierData(chart, metric) ? ", limited data" : ""}`}
        className="compact-chart-button"
        onClick={() => onSelect(chart)}
        type="button"
      >
        <span className="chart-art compact-jacket" data-chart-type={chart.type}>
          {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <span>{chart.difficulty}</span>}
          <LimitedDataWarning chart={chart} metric={metric} compact />
          <span aria-hidden="true" className={`chart-difficulty-badge chart-difficulty-${chart.type.toLowerCase()}`}>
            {chartCountLabel(chart)}
          </span>
        </span>
      </button>
    </article>
  );
}

function CompactChartGrid({ charts, metric, onSelect }: { charts: ChartResult[]; metric: TierMetricKey; onSelect: (chart: ChartResult) => void }) {
  return (
    <div className="compact-chart-grid">
      {charts.length
        ? charts.map((chart) => <CompactChartCard chart={chart} metric={metric} key={chart.chartId} onSelect={onSelect} />)
        : <p className="empty-tier">No charts match the current filters.</p>}
    </div>
  );
}

function TierDivider({ headingId, label }: { headingId: string; label: string }) {
  return (
    <header className="tier-divider">
      <span aria-hidden="true" className="tier-divider-leading" />
      <h2 id={headingId}>{label}</h2>
      <span aria-hidden="true" className="tier-divider-trailing" />
    </header>
  );
}

function ChartDetailDialog({ chart, metric, onClose }: { chart: ChartResult; metric: TierMetricKey; onClose: () => void }) {
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
          <div className="chart-dialog-art-rail">
            <div className="chart-art jacket chart-dialog-jacket" data-chart-type={chart.type} aria-hidden="true">
              {chart.imageUrl ? <img src={chart.imageUrl} alt="" /> : <span>{chart.difficulty}</span>}
            </div>
            <ChartVideoLink
              chartId={chart.chartId}
              difficulty={chart.difficulty}
              songName={chart.songName}
              variant="dialog"
            />
          </div>
          <ChartDetails chart={chart} metric={metric} headingId="chart-detail-dialog-title" />
        </div>
      </div>
    </div>
  );
}

function TierSection({ rank, name, charts, metric, compact, onSelect }: {
  rank: number;
  name: string;
  charts: ChartResult[];
  metric: TierMetricKey;
  compact: boolean;
  onSelect: (chart: ChartResult) => void;
}) {
  return (
    <section className={`tier tier-${groupTone[rank - 1]}${compact ? " tier-compact" : ""}`} aria-labelledby={`tier-${rank}`}>
      <TierDivider headingId={`tier-${rank}`} label={name} />
      {compact ? (
        <CompactChartGrid charts={charts} metric={metric} onSelect={onSelect} />
      ) : (
        <div className="tier-list">
          {charts.length
            ? charts.map((chart) => <ChartCard chart={chart} metric={metric} key={chart.chartId} />)
            : <p className="empty-tier">No charts match the current filters.</p>}
        </div>
      )}
    </section>
  );
}

function EstimatedDifficultySection({ charts, metric, compact, mode, value, onSelect }: {
  charts: ChartResult[];
  metric: TierMetricKey;
  compact: boolean;
  mode: ModeKey;
  value: number;
  onSelect: (chart: ChartResult) => void;
}) {
  const formatted = mode === "coop"
    ? formatCoopEstimatedDifficulty(value)
    : formatEstimatedDifficulty(value);
  const label = mode === "coop"
    ? `Co-op ${formatted}`
    : `${mode === "singles" ? "S" : "D"}${formatted}`;
  const sectionId = `estimated-${mode}-${formatted.replace(".", "-")}`;
  return (
    <section className={`tier tier-sky estimated-tier${compact ? " tier-compact" : ""}`} aria-labelledby={sectionId}>
      <TierDivider
        headingId={sectionId}
        label={label}
      />
      {compact ? (
        <CompactChartGrid charts={charts} metric={metric} onSelect={onSelect} />
      ) : (
        <div className="tier-list">
          {charts.map((chart) => <ChartCard chart={chart} metric={metric} key={chart.chartId} />)}
        </div>
      )}
    </section>
  );
}

export default function TierListPage() {
  const [payload, setPayload] = useState<AnalysisPayload | null>(null);
  const [activeMode, setActiveMode] = useState<ModeKey>("singles");
  const [activeMetric, setActiveMetric] = useState<TierMetricKey>("scoring");
  const [groupingView, setGroupingView] = useState<GroupingView>("estimated");
  const [layoutView, setLayoutView] = useState<LayoutView>("compact");
  const [filters, setFilters] = useState<Record<ModeKey, FilterState>>({
    singles: { ...initialFilter },
    doubles: { ...initialFilter },
    coop: { ...initialFilter },
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
    const applyModeFromUrl = () => {
      const params = new URLSearchParams(window.location.search);
      setActiveMode(tierModeFromSearchParams(params));
      setActiveMetric(tierMetricFromSearchParams(params));
      setSelectedChart(null);
    };
    applyModeFromUrl();
    window.addEventListener("popstate", applyModeFromUrl);
    return () => window.removeEventListener("popstate", applyModeFromUrl);
  }, []);

  useEffect(() => {
    setNowMs(Date.now());
    const timer = window.setInterval(() => setNowMs(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const modeCharts = payload?.[activeMode] || [];
  const clearingMethod = clearingPercentileRange(modeCharts.find((chart) => chart.tierMetrics?.clearing)?.tierMetrics?.clearing);
  const metricAvailability = tierMetricAvailability(modeCharts, activeMetric, activeMode);
  const filter = filters[activeMode];
  const levels = useMemo(
    () => [...new Set(modeCharts.map((chart) => chart.level))].sort((a, b) => a - b),
    [modeCharts],
  );
  const filteredCharts = useMemo(() => {
    const query = filter.query.trim().toLocaleLowerCase();
    return modeCharts.filter((chart) => {
      if (filter.level !== "All" && chart.level !== Number(filter.level)) return false;
      return !query || `${chart.songName} ${chart.stepArtist || ""}`.toLocaleLowerCase().includes(query);
    });
  }, [filter, modeCharts]);
  const estimatedGroups = useMemo(
    () => estimatedTierGroups(filteredCharts, activeMetric, activeMode),
    [activeMode, activeMetric, filteredCharts],
  );
  const unratedCharts = useMemo(
    () => sortTierCharts(filteredCharts.filter((chart) => selectedTierMetric(chart, activeMetric).estimatedDifficulty === null), activeMetric),
    [activeMetric, filteredCharts],
  );
  const updateFilter = (patch: Partial<FilterState>) => {
    setFilters((current) => ({
      ...current,
      [activeMode]: { ...current[activeMode], ...patch },
    }));
  };
  const closeChartDialog = useCallback(() => setSelectedChart(null), []);
  const selectMode = useCallback((mode: ModeKey) => {
    setActiveMode(mode);
    setSelectedChart(null);
    const url = new URL(window.location.href);
    if (url.searchParams.get("mode") === mode) return;
    url.searchParams.set("mode", mode);
    window.history.pushState({}, "", url);
  }, []);
  const selectMetric = useCallback((metric: TierMetricKey) => {
    setActiveMetric(metric);
    setSelectedChart(null);
    const url = new URL(window.location.href);
    if (url.searchParams.get("metric") === metric) return;
    url.searchParams.set("metric", metric);
    window.history.pushState({}, "", url);
  }, []);

  return (
    <main className="tier-list-page">
      <SiteHeader active="tier-list" />

      <section className="hero page-title-hero" id="top">
        <h1>{metricTitles[activeMetric]}</h1>
        <RefreshMeta
          generatedAtUtc={payload?.generatedAtUtc}
          label="Tier list updated"
          loading={loading}
          loadingLabel="Loading tier list..."
          nowMs={nowMs}
        />
        {message ? <p className="tier-load-message" aria-live="polite">{message}</p> : null}
      </section>

      <section className="dashboard" aria-busy={loading} id="rankings-dashboard">
        <div className="view-switcher metric-switcher" role="group" aria-label="Tier list metric">
          {(["scoring", "clearing", "pumbility"] as TierMetricKey[]).map((metric) => (
            <button
              aria-pressed={activeMetric === metric}
              className={activeMetric === metric ? "active" : ""}
              key={metric}
              onClick={() => selectMetric(metric)}
              type="button"
            >{metricLabels[metric]}</button>
          ))}
        </div>
        <div className="mode-tabs" role="tablist" aria-label="Chart mode">
          {(["singles", "doubles", "coop"] as ModeKey[]).map((mode) => (
            <button
              aria-selected={activeMode === mode}
              className={activeMode === mode ? "active" : ""}
              disabled={mode === "coop" && activeMetric !== "scoring"}
              key={mode}
              onClick={() => selectMode(mode)}
              role="tab"
              type="button"
            >
              <span className="mode-letter">{mode === "singles" ? "S" : mode === "doubles" ? "D" : "C"}</span>
              <span>
                <b>{mode === "coop" ? "Co-op" : mode}</b>
              </span>
            </button>
          ))}
        </div>
        {activeMetric !== "scoring" ? <p className="metric-note">{metricLabels[activeMetric]} estimates are available for Singles and Doubles. Co-op uses a separate scoring scale.</p> : null}

        {metricAvailability !== "available" ? (
          <p className="metric-unavailable" role="status">
            {metricAvailability === "unsupported"
              ? `${metricLabels[activeMetric]} difficulty is unavailable for Co-op. Select Singles or Doubles, or switch to Scoring.`
              : loading ? "Loading tier list..." : `${metricLabels[activeMetric]} estimates are not available in this analysis yet.`}
          </p>
        ) : <>
        <div className="filter-bar">
          <label className="search-field">
            <span>Search songs or step artists</span>
            <input
              aria-label="Search songs or step artists"
              onChange={(event) => updateFilter({ query: event.target.value })}
              placeholder="Sorceress Elise"
              type="search"
              value={filter.query}
            />
          </label>
          <label className="level-field">
            <span>{activeMode === "coop" ? "Players" : "Official level"}</span>
            <select value={filter.level} onChange={(event) => updateFilter({ level: event.target.value })}>
              <option>All</option>
              {levels.map((level) => (
                <option key={level} value={level}>
                  {activeMode === "coop" ? `${level}x` : `${activeMode === "singles" ? "S" : "D"}${level}`}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="results-controls">
          <div className="results-switchers">
            <div
              aria-label="Difficulty grouping"
              className={`view-switcher${activeMode === "coop" ? " single-option" : ""}`}
              role="group"
            >
              <button
                aria-pressed={activeMode === "coop" || groupingView === "estimated"}
                className={activeMode === "coop" || groupingView === "estimated" ? "active" : ""}
                onClick={() => setGroupingView("estimated")}
                type="button"
              ><span>Estimated<br />Difficulty</span></button>
              {activeMode === "coop" ? null : (
                <button
                  aria-pressed={groupingView === "tiers"}
                  className={groupingView === "tiers" ? "active" : ""}
                  onClick={() => setGroupingView("tiers")}
                  type="button"
                >Tier Bands</button>
              )}
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
          {groupingView === "tiers" && activeMode !== "coop"
            ? (payload?.effectBands || demoPayload.effectBands).map((group) => (
                <TierSection
                  charts={tierBandCharts(filteredCharts, activeMetric, group.rank)}
                  metric={activeMetric}
                  compact={layoutView === "compact"}
                  key={group.rank}
                  name={group.name}
                  onSelect={setSelectedChart}
                  rank={group.rank}
                />
              ))
            : estimatedGroups.map((group) => (
                <EstimatedDifficultySection
                  charts={group.charts}
                  compact={layoutView === "compact"}
                  key={group.value}
                  mode={activeMode}
                  metric={activeMetric}
                  onSelect={setSelectedChart}
                  value={group.value}
                />
              ))}
          {(groupingView === "estimated" || activeMode === "coop") && estimatedGroups.length === 0 ? (
            <p className="empty-tier">No estimated charts match the current filters.</p>
          ) : null}
          <section className={`tier unrated-section${layoutView === "compact" ? " tier-compact" : ""}`} aria-labelledby="unrated-charts">
            <TierDivider headingId="unrated-charts" label="Unrated" />
            {layoutView === "compact" ? (
              <CompactChartGrid charts={unratedCharts} metric={activeMetric} onSelect={setSelectedChart} />
            ) : (
              <div className="tier-list">
                {unratedCharts.map((chart) => <ChartCard chart={chart} metric={activeMetric} key={chart.chartId} />)}
              </div>
            )}
          </section>
        </div>
        </>}
      </section>
      <footer>
        {activeMetric === "clearing" ? <>
          <p><b>How clearing estimates work</b> Each chart uses unique successful players from either Phoenix version. We average their current mode-specific skill ratings within the {clearingMethod ? `${clearingMethod.label} percentile interval` : "published percentile interval"}, including both boundaries and ties. Players without a usable skill rating are excluded from that average.</p>
          <p>The median chart in each official-level folder anchors at level + 0.5. Each skill point above or below the folder reference changes difficulty by one point. Estimates can cross official levels: an S20 can be 19.2. Calibration always uses the chart’s official-level folder. Sparse charts remain visible with evidence and limited-data labels; no selected players means Unrated.</p>
          <p>This estimates clearing difficulty from observed successful players, not pass probability or first-clear ability. Skill ratings can change after the recorded clear.</p>
        </> : activeMetric === "pumbility" ? <>
          <p><b>How Pumbility estimates work</b> Pumbility is the arithmetic average of a chart’s scoring and clearing difficulty. Both components must be available. The average uses full-precision estimates before one-decimal display truncation, so displayed components may average slightly differently.</p>
          <p>Evidence follows the weaker component. Limited data means fewer than 20 scoring contributors or fewer than 20 selected clearers; these supports are shown separately.</p>
        </> : activeMode !== "coop" ? <>
          <p><b>How scoring estimates work</b> Scoring difficulty compares player performance within official-level folders using Phoenix 1 and Phoenix 2 observations. Folder ranks, confidence intervals, and official-level What-if estimates describe the scoring model.</p>
          <p>Limited data means fewer than 20 scoring contributors. Select Clearing for difficulty based on the skill of successful players, or Pumbility for the average of both estimates.</p>
        </> : <>
        <p><b>How Co-op estimates work</b> Co-op charts share one 2x-5x tier list. Miss points are adjusted for player strength and Phoenix source using all observations, then a conditional 75th-percentile score is estimated for a median-strength Phoenix 2 player. The conditional quantile provides outlier robustness; raw scores and residuals are not trimmed.</p>
        <p>The resulting chart order anchors the easiest chart at continuous difficulty 10, the median chart at 16, and the hardest chart at 24.9, then truncates the published difficulty to a whole-number range from 10 through 24. This preserves the observed ordering without forcing a normal distribution. Co-op recommendation letter-grade goals are assigned from these whole-number difficulties.</p>
        </>}
      </footer>
      {selectedChart ? <ChartDetailDialog chart={selectedChart} metric={activeMetric} onClose={closeChartDialog} /> : null}
    </main>
  );
}
