"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { RefreshMeta } from "../_components/refresh-meta";
import { GlobalRefreshNotice } from "../_components/global-refresh-notice";
import { ChartVideoLink } from "../_components/chart-video-link";
import { usePlayerProfile } from "../_components/player-profile-provider";
import { PlayerResult } from "../_components/player-result";
import "./tier-list.css";
import { BpmRangeFilter } from "../_components/bpm-range-filter";
import { ExplanatorySection } from "../_components/explanatory-section";
import { SiteHeader } from "../_components/site-header";
import { readJsonResponse } from "../../lib/api-response";
import { loadTierChartDetails } from "../../lib/tier-chart-details";
import { chartEvidenceLines, plainTierDataWarning, TIER_INTERVAL_TOOLTIP } from "../../lib/tier-evidence-copy";
import { loadWithGenerationRecovery, MODEL_DELAY_THRESHOLD_MS } from "../../lib/refresh-reliability";
import { demoPayload } from "../../lib/demo-data";
import { resolvePumbilityPolicy, type PumbilityAggregation } from "../../lib/pumbility-policy";
import { compressDemoPayload, hasMidpointCompression } from "../../lib/tier-postprocessing";
import {
  formatCoopEstimatedDifficulty,
  formatEstimatedDifficulty,
} from "../../lib/format-difficulty";
import { tierMetricFromSearchParams, tierModeFromSearchParams } from "../../lib/page-view-state";
import { DEFAULT_SCORING_GOAL, SCORING_GOALS, hasScoringGoalVariants, isSssScoringChart, projectScoringGoal, scoringGoalFromSearchParams, scoringGoalUrl, scoringTargetForChart, scoringTargetForGoal } from "../../lib/scoring-variants";
import { chartBpmRange, clampBpmRange, formatTierScore, matchesTierBpm, matchesTierLevel, tierBpmBounds, type BpmRange } from "../../lib/tier-filters";
import {
  clearingPercentileRange,
  clearingSkillPercentile,
  clearingMinimumScoreRecords,
  estimatedTierGroups,
  hasQualifiedSubmittedClearing,
  scoringRecordCount,
  selectedTierMetric,
  sortTierCharts,
  tierBandCharts,
  tierMetricAvailability,
} from "../../lib/tier-metrics";
import type {
  AnalysisPayload,
  ChartResult,
  ModeKey,
  ScoringGoal,
  TierMetricKey,
  TierPlayerScore,
  TierPlayerScoresResponse,
} from "../../lib/types";

type FilterState = {
  query: string;
  level: string;
  bpm: BpmRange | null;
};

type GroupingView = "tiers" | "estimated";
type LayoutView = "detailed" | "compact";

const initialFilter: FilterState = {
  query: "",
  level: "All",
  bpm: null,
};

const TierScoresContext = createContext<{
  selected: boolean;
  loading: boolean;
  unavailable: boolean;
  scores: Map<string, TierPlayerScore>;
}>({ selected: false, loading: false, unavailable: false, scores: new Map() });
const PumbilityPolicyContext = createContext<PumbilityAggregation | null>(null);
const RatingCompressionContext = createContext(false);
const TierDetailContext = createContext<{compact: boolean; generatedAtUtc: string; goal: ScoringGoal; select: (chart: ChartResult) => void; reload: (chartId: string) => Promise<string>}>({compact:false, generatedAtUtc:"", goal:"general", select:() => {}, reload:async () => {throw new Error("Reload the tier list to continue.");}});

function PersonalChartScore({ chartId }: { chartId: string }) {
  const { selected, loading, unavailable, scores } = useContext(TierScoresContext);
  if (!selected) return null;
  const result = scores.get(chartId);
  if (!result) return <span className="tier-player-score tier-score-status" aria-label={unavailable ? "Player score unavailable" : loading ? "Loading player score" : "No recorded score"}>{unavailable ? "Unavailable" : loading ? "Loading…" : "—"}</span>;
  return <PlayerResult className="tier-player-score" compact formattedScore={formatTierScore(result.score)} plate={result.plateCode} score={result.score} />;
}

const TierBrowseContext = createContext({ metric: "scoring" as TierMetricKey, showRating: false });
function chartBpmLabel(chart: ChartResult) {
  const bpm = chartBpmRange(chart);
  if (!bpm) return "— BPM";
  return `${bpm.min === bpm.max ? bpm.min : `${bpm.min}–${bpm.max}`} BPM`;
}

const groupTone = ["lime", "green", "mint", "slate", "orange", "rose", "red"];
const metricLabels: Record<TierMetricKey, string> = { scoring: "Scoring", clearing: "Clearing", pumbility: "Pumbility" };
const ClearingRecordMinimumContext = createContext<number | null>(null);
const metricTitles: Record<TierMetricKey, string> = {
  scoring: "Tier Lists",
  clearing: "Tier Lists",
  pumbility: "Tier Lists",
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

function LimitedDataWarning({ chart, metric, compact = false }: { chart: ChartResult; metric: TierMetricKey; compact?: boolean }) {
  const warning = plainTierDataWarning(chart, metric);
  if (!warning) return null;
  return <span aria-label={warning} role="img" title={warning} tabIndex={compact ? undefined : 0}
    className={`limited-data-warning${compact ? " compact-warning" : ""}`}>
    <b aria-hidden="true">!</b>{compact ? null : <span aria-hidden="true">Limited data</span>}
  </span>;
}

function ChartDetails({ chart, metric, headingId, detailsLoaded = false }: { chart: ChartResult; metric: TierMetricKey; headingId?: string; detailsLoaded?: boolean }) {
  const detailSource = useContext(TierDetailContext);
  const listOnly = detailSource.compact && !detailsLoaded;
  const pumbilityAggregation = useContext(PumbilityPolicyContext);
  const minimumScoreRecords = useContext(ClearingRecordMinimumContext);
  const selected = selectedTierMetric(chart, metric);
  const delta = selected.difficultyDelta;
  const isCoop = chart.type === "CoOp";
  const sssScoring = isSssScoringChart(chart);
  const target = scoringTargetForChart(chart);
  const crossScoring = chart.scoringMethod === "official-cross-level-v1";
  const profileScoring = "scoringScoreProfile" in chart || crossScoring || sssScoring;
  const clearing = chart.tierMetrics?.clearing;
  const crossClearing = hasQualifiedSubmittedClearing(clearing);
  const submittedProfile = chart.scoringMethod === "submitted-score-profile-v2";
  const officialEvidence = metric === "clearing" ? clearing?.clearingEvidence ?? chart.officialEvidence : chart.officialEvidence;
  const abilityClearing = clearing?.skillMetric === "official-clearer-ability";
  const clearingUnavailable = !crossClearing && crossScoring && !officialEvidence && clearing?.clearCount === 0
    && chart.level >= (chart.type === "Single" ? 23 : 25);
  const selectedOfficialEvidence = metric === "clearing" ? abilityClearing && Boolean(officialEvidence)
    : crossScoring ? Boolean(chart.officialScoringEvidence) : Boolean(officialEvidence);
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
          {sssScoring && metric !== "clearing" ? <span><b>{target.label} ≥{target.score.toLocaleString()}</b> · {chart.scoringSource === "official" ? "Combined official leaderboards" : "Submitted successful best scores"}{metric === "pumbility" ? " + Clearing" : ""}</span> : null}
          {selectedOfficialEvidence ? <span>{metric === "pumbility" && clearingUnavailable ? "Official Scoring; Clearing unavailable" : metric === "pumbility" && !abilityClearing ? "Official Scoring + player-submitted Clearing" : "Official PIUScores leaderboard"}</span> : metric === "clearing" ? <span>{clearingUnavailable ? "Clearing data unavailable" : "Player-submitted clears"}</span> : submittedProfile ? <span>{metric === "pumbility" ? "Player-submitted score profile + clears" : "Player-submitted score profile"}</span> : null}
          {selectedOfficialEvidence && selected.estimatedDifficulty != null && Math.floor(selected.estimatedDifficulty) !== chart.level ? <span>Estimate outside official level</span> : null}
          {metric === "scoring" ? <span><b>{scoringRecordCount(chart) ?? "Unknown"}</b> usable records</span> : null}
          {metric === "scoring" && !officialEvidence && !crossScoring && chart.phoenix1Contributors !== undefined && chart.phoenix2Contributors !== undefined ? (
            <span><b>{chart.phoenix1Contributors}/{chart.phoenix2Contributors}</b> P1/P2</span>
          ) : null}
          {!isCoop && selected.levelRank !== null && selected.levelComparisonCharts !== null ? (
            <span><b>#{selected.levelRank}</b> of {selected.levelComparisonCharts} in {chart.difficulty}</span>
          ) : null}
        </div>
        {listOnly ? <button type="button" onClick={() => detailSource.select(chart)}>Calculation and evidence</button> : <div className="chart-meta metric-details">
          {chartEvidenceLines(chart, metric, { minimumScoreRecords, pumbilityAggregation }).map((line) => <span key={line}>{line}</span>)}
        </div>}
      </div>
      {isCoop ? null : (
        <div className={`delta ${delta !== null && delta < 0 ? "delta-easy" : "delta-hard"}`}>
          <span>difference</span>
          <strong>{delta === null ? "-" : signed(delta)}</strong>
          {metric === "scoring" && chart.difficultyCi95Low !== null && chart.difficultyCi95High !== null && !officialEvidence ? (
            <small title={TIER_INTERVAL_TOOLTIP}>{formatEstimatedDifficulty(chart.difficultyCi95Low)}–{formatEstimatedDifficulty(chart.difficultyCi95High)} range</small>
          ) : null}
          {!listOnly && metric === "scoring" && !profileScoring && !officialEvidence ? <WhatIfDifficulty chart={chart} /> : null}
        </div>
      )}
    </>
  );
}

function ChartCard({ chart, metric }: { chart: ChartResult; metric: TierMetricKey }) {
  const details = useContext(TierDetailContext);
  const { showRating } = useContext(TierBrowseContext);
  return <article className="tier-browse-row">
    <button className="tier-row-button" onClick={() => details.select(chart)} type="button" aria-label={`View details for ${chart.songName}, ${chart.difficulty}, ${chartBpmLabel(chart)}`}>
      <span className="chart-art compact-jacket" data-chart-type={chart.type} aria-hidden="true">
        {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <span>{chart.difficulty}</span>}
        <span className={`chart-difficulty-badge chart-difficulty-${chart.type.toLowerCase()}`}>{chartCountLabel(chart)}</span>
      </span>
      <span className="tier-row-copy"><span className="tier-song" title={chart.songName}>{chart.songName}</span><span className="tier-chart-bpm">{chartBpmLabel(chart)}</span>{showRating ? <span className="tier-chart-rating">{chartGrade(chart, metric)}</span> : null}</span>
    </button>
    <PersonalChartScore chartId={chart.chartId} />
  </article>;
}

function LazyChartDetails({chart, metric}: {chart: ChartResult; metric: TierMetricKey}) {
  const source = useContext(TierDetailContext);
  const [loaded, setLoaded] = useState<ChartResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const recoveryUsed = useRef(false);
  useEffect(() => {
    if (!source.compact) return;
    let cancelled = false;
    setLoaded(null); setError(null);
    loadWithGenerationRecovery(source.generatedAtUtc,
      generation => loadTierChartDetails(chart.chartId, generation),
      async () => {
        if (cancelled) throw new DOMException("Cancelled", "AbortError");
        if (recoveryUsed.current) throw new Error("The tier list changed again. Reload the page to view this chart.");
        recoveryUsed.current = true;
        return source.reload(chart.chartId);
      },
    ).then(({ value: response }) => {
      if (!cancelled) setLoaded(projectScoringGoal(response.chart, source.goal, metric));
    }).catch(error => { if (!cancelled) setError(error instanceof Error ? error.message : "Chart details could not be loaded."); });
    return () => { cancelled = true; };
  }, [attempt, chart.chartId, metric, source.compact, source.generatedAtUtc, source.goal]);
  if (!source.compact) return <ChartDetails chart={chart} metric={metric} headingId="chart-detail-dialog-title" detailsLoaded />;
  if (loaded) return <ChartDetails chart={loaded} metric={metric} headingId="chart-detail-dialog-title" detailsLoaded />;
  return <div className="chart-copy"><h3 id="chart-detail-dialog-title">{chart.songName}</h3><p role="status">{error || "Loading chart details..."}</p>{error ? <button type="button" onClick={() => setAttempt(value => value + 1)}>Retry</button> : null}</div>;
}

function CompactChartCard({ chart, onSelect }: { chart: ChartResult; metric?: TierMetricKey; onSelect: (chart: ChartResult) => void }) {
  const { metric, showRating } = useContext(TierBrowseContext);
  return <article className="compact-chart-card">
    <button aria-label={`View details for ${chart.songName}, ${chart.difficulty}, ${chartBpmLabel(chart)}`} className="compact-chart-button" onClick={() => onSelect(chart)} title={chart.songName} type="button">
      <span className="chart-art compact-jacket" data-chart-type={chart.type} aria-hidden="true">
        {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <span>{chart.difficulty}</span>}
        <span className={`chart-difficulty-badge chart-difficulty-${chart.type.toLowerCase()}`}>{chartCountLabel(chart)}</span>
      </span>
      <span className="tier-song">{chart.songName}</span>
      <span className="tier-chart-bpm">{chartBpmLabel(chart)}</span>
      {showRating ? <span className="tier-chart-rating">{chartGrade(chart, metric)}</span> : null}
    </button>
    <PersonalChartScore chartId={chart.chartId} />
  </article>;
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
            <PersonalChartScore chartId={chart.chartId} />
            <ChartVideoLink
              chartId={chart.chartId}
              difficulty={chart.difficulty}
              songName={chart.songName}
              variant="dialog"
            />
          </div>
          <LazyChartDetails chart={chart} metric={metric} />
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
  const phoenix2Only = payload?.summary.method.sourceSelection === "phoenix2-only";
  const profileScoring = (payload?.summary.method.scoring as { calibration?: string } | undefined)?.calibration === "folder-scaled-score-profile";
  const upperProfileScoring = (payload?.summary.method.scoring as { version?: number } | undefined)?.version === 2;
  const officialTiers = payload?.summary.method.officialTiers;
  const crossLevelScoring = payload?.summary.method.officialCrossLevelScoring;
  const crossLevelClearing = payload?.summary.method.crossLevelClearing;
  const minimumScoreRecords = clearingMinimumScoreRecords(payload);
  const hybridTiers = (payload?.summary.method.hybridTiers as { version?: number } | undefined)?.version === 1;
  const pumbilityPolicy = resolvePumbilityPolicy(payload);
  const maximumPumbility = pumbilityPolicy?.aggregation === "maximum";
  const localAveragePumbility = payload?.schemaVersion === 45 && pumbilityPolicy?.aggregation === "average";
  const adjustedAveragePumbility = [45, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60].includes(payload?.schemaVersion ?? 0) && pumbilityPolicy?.aggregation === "average";
  const [activeMode, setActiveMode] = useState<ModeKey>("singles");
  const [activeMetric, setActiveMetric] = useState<TierMetricKey>("scoring");
  const [scoringGoal, setScoringGoal] = useState<ScoringGoal>(DEFAULT_SCORING_GOAL);
  const target = scoringTargetForGoal(scoringGoal);
  const showScoringGoal = activeMode !== "coop" && activeMetric !== "clearing";
  const sssAvailable = hasScoringGoalVariants(payload, scoringGoal);
  const showSssMethod = showScoringGoal && scoringGoal === target.goal && sssAvailable;
  const historicalGoal = scoringGoal === "sss" || scoringGoal === "sssplus";
  const independentTarget = (scoringGoal === "ss" || scoringGoal === "ssplus") && [58, 59, 60].includes(payload?.schemaVersion ?? 0);
  const refinedSss = independentTarget || historicalGoal && [50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60].includes(payload?.schemaVersion ?? 0);
  const medianCalibrated = scoringGoal === "sssplus" && [53, 54, 55, 56, 57, 58, 59, 60].includes(payload?.schemaVersion ?? 0);
  const spreadCalibrated = scoringGoal === "sssplus" && [54, 55, 56, 57, 58, 59, 60].includes(payload?.schemaVersion ?? 0);
  const centeredSss = independentTarget || historicalGoal && [51, 52, 53, 54, 55, 56, 57, 58, 59, 60].includes(payload?.schemaVersion ?? 0);
  const tailPolicy = independentTarget || scoringGoal === "sss" && [59, 60].includes(payload?.schemaVersion ?? 0)
    || scoringGoal === "sssplus" && [52, 53, 54, 55, 56, 57, 58, 59, 60].includes(payload?.schemaVersion ?? 0);
  const level20Width = Boolean(payload?.summary.method.level20ScoringWidth);
  const submittedSssplusCalibrated = scoringGoal === "sssplus" && [57, 58, 59, 60].includes(payload?.schemaVersion ?? 0);
  const topThreeNoSss = scoringGoal !== "sss" && tailPolicy || (payload?.summary.method.sssRefinement as { version?: number } | undefined)?.version === 2;
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
  const profile = usePlayerProfile();
  const selectedPlayerKey = profile.playerKey;
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [playerScores, setPlayerScores] = useState<TierPlayerScoresResponse | null>(null);
  const [loadingScores, setLoadingScores] = useState(false);
  const [scoresError, setScoresError] = useState<string | null>(null);

  useEffect(() => { setSelectedChart(null); }, [selectedPlayerKey]);

  useEffect(() => {
    setPlayerScores(null);
    setScoresError(null);
    if (!selectedPlayerKey) { setLoadingScores(false); return; }
    const controller = new AbortController();
    setLoadingScores(true);
    const params = new URLSearchParams({ playerKey: selectedPlayerKey, mode: activeMode });
    fetch(`/api/tier-list/scores?${params}`, { signal: controller.signal, cache: "no-store" })
      .then((response) => readJsonResponse<TierPlayerScoresResponse>(response))
      .then((result) => {
        if (controller.signal.aborted) return;
        if (result.playerKey !== selectedPlayerKey || result.mode !== activeMode) throw new Error("The server returned scores for another player or mode.");
        setPlayerScores(result);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setScoresError(error instanceof Error ? error.message : "Could not load player scores.");
      })
      .finally(() => { if (!controller.signal.aborted) setLoadingScores(false); });
    return () => controller.abort();
  }, [activeMode, selectedPlayerKey]);

  const activePlayerScores = playerScores?.playerKey === selectedPlayerKey && playerScores.mode === activeMode ? playerScores : null;
  const scoreContext = useMemo(() => ({
    selected: Boolean(selectedPlayerKey),
    loading: loadingScores || Boolean(selectedPlayerKey && !activePlayerScores && !scoresError),
    unavailable: Boolean(scoresError),
    scores: new Map(activePlayerScores?.scores.map((score) => [score.chartId, score]) || []),
  }), [activePlayerScores, loadingScores, scoresError, selectedPlayerKey]);

  const loadLatest = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const response = await fetch("/api/tier-list", { cache: "no-store" });
      if (response.status === 404) {
        setPayload(null);
        setMessage("No stored combined analysis is available yet.");
        return null;
      }
      const latest = await readJsonResponse<AnalysisPayload>(response);
      if (latest.mix?.key !== "combined") {
        throw new Error("The server returned a version-specific tier list.");
      }
      setPayload(latest);
      setMessage(null);
      return latest;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load the combined tier list.");
      return null;
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  const reloadSelectedChart = useCallback(async (chartId: string) => {
    const latest = await loadLatest();
    if (!latest) throw new Error("The latest tier list could not be loaded. Reload the page to try again.");
    const chart = [...latest.singles, ...latest.doubles, ...(latest.coop ?? [])].find(row => row.chartId === chartId);
    if (!chart) {
      setSelectedChart(current => current?.chartId === chartId ? null : current);
      setMessage("This chart is no longer in the latest tier list.");
      throw new Error("This chart is no longer in the latest tier list.");
    }
    setSelectedChart(current => current?.chartId === chartId ? chart : current);
    return latest.generatedAtUtc;
  }, [loadLatest]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const useDemo = params.get("demo") === "1" || process.env.NEXT_PUBLIC_DEMO_MODE === "1";
    if (useDemo) {
      setPayload(compressDemoPayload(demoPayload));
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
      const selectedGoal = scoringGoalFromSearchParams(params);
      setScoringGoal(selectedGoal);
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

  const modeCharts = useMemo(() => (payload?.[activeMode] || [])
    .map(chart => projectScoringGoal(chart, scoringGoal, activeMetric)), [activeMetric, activeMode, payload, scoringGoal]);
  const clearingExample = modeCharts.find((chart) => chart.tierMetrics?.clearing && chart.tierMetrics.clearing.skillMetric !== "official-clearer-ability")?.tierMetrics?.clearing;
  const pointPercentile = clearingSkillPercentile(clearingExample);
  const clearingMethod = clearingPercentileRange(clearingExample);
  const tierMethod = payload?.summary.method.tierMetrics as {
    clearing?: { difficultyDeltaScale?: number };
  } | undefined;
  const clearingScale = tierMethod?.clearing?.difficultyDeltaScale ?? 1;
  const metricAvailability = tierMetricAvailability(modeCharts, activeMetric, activeMode);
  const goalUnavailable = !loading && Boolean(payload) && showScoringGoal && scoringGoal !== "general" && !sssAvailable;
  const filter = filters[activeMode];
  const bpmBounds = useMemo(() => tierBpmBounds(modeCharts), [modeCharts]);
  const selectedBpm = useMemo(() => filter.bpm && bpmBounds ? clampBpmRange(filter.bpm, bpmBounds) : null, [bpmBounds, filter.bpm]);
  const levels = useMemo(
    () => [...new Set(modeCharts.map((chart) => chart.level))].sort((a, b) => a - b),
    [modeCharts],
  );
  const filteredCharts = useMemo(() => {
    const query = filter.query.trim().toLocaleLowerCase();
    return modeCharts.filter((chart) => {
      if (!matchesTierLevel(chart, filter.level)) return false;
      if (!matchesTierBpm(chart, selectedBpm)) return false;
      return !query || `${chart.songName} ${chart.stepArtist || ""}`.toLocaleLowerCase().includes(query);
    });
  }, [filter, modeCharts, selectedBpm]);
  const estimatedGroups = useMemo(
    () => estimatedTierGroups(filteredCharts, activeMetric, activeMode),
    [activeMode, activeMetric, filteredCharts],
  );
  const officialD26Clearing = modeCharts.some(chart => ["official-d26-clearer-ability-v1", "official-d26-clearer-ability-combined-v2"].includes(chart.tierMetrics?.clearing.method ?? ""));
  const combinedD26Clearing = modeCharts.some(chart => chart.tierMetrics?.clearing.method === "official-d26-clearer-ability-combined-v2");
  const p1ObservedD26Clearing = modeCharts.some(chart => chart.tierMetrics?.clearing.clearingEvidence?.capPolicy === "phoenix1-diagnostic-phoenix2-default-v1");
  const showOfficialMethod = activeMode !== "coop" && ((officialTiers?.enabled
    && filteredCharts.some((chart) => activeMetric === "clearing"
      ? chart.tierMetrics?.clearing.skillMetric === "official-clearer-ability" : chart.officialScoringEvidence || chart.officialEvidence))
    || filteredCharts.some(chart => ["official-d26-clearer-ability-v1", "official-d26-clearer-ability-combined-v2"].includes(chart.tierMetrics?.clearing.method ?? "")));
  const showSubmittedMethod = !showOfficialMethod || filteredCharts.some((chart) => activeMetric === "scoring"
    ? chart.scoringMethod === "submitted-score-profile-v2" || !crossLevelScoring && !chart.officialEvidence : chart.tierMetrics?.clearing.skillMetric !== "official-clearer-ability");
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
  const selectScoringGoal = useCallback((goal: ScoringGoal) => {
    setScoringGoal(goal);
    setSelectedChart(null);
    const url = scoringGoalUrl(window.location.href, goal);
    if (window.location.href !== url.href) window.history.pushState({}, "", url);
  }, []);

  return (
    <TierBrowseContext.Provider value={{metric: activeMetric, showRating: groupingView === "tiers" && activeMode !== "coop"}}>
    <TierScoresContext.Provider value={scoreContext}>
    <PumbilityPolicyContext.Provider value={pumbilityPolicy?.aggregation ?? null}>
    <ClearingRecordMinimumContext.Provider value={minimumScoreRecords}>
    <RatingCompressionContext.Provider value={hasMidpointCompression(payload) || localAveragePumbility}>
    <TierDetailContext.Provider value={{compact:payload?.tierListView?.kind === "list", generatedAtUtc:payload?.generatedAtUtc ?? "", goal:scoringGoal, select:setSelectedChart, reload:reloadSelectedChart}}>
    <main className="tier-list-page">
      <SiteHeader active="tier-list" />

      <section className="hero page-title-hero" id="top">
        <h1>{metricTitles[activeMetric]}</h1>
        <RefreshMeta
          delayedAfterMs={MODEL_DELAY_THRESHOLD_MS}
          generatedAtUtc={payload?.generatedAtUtc}
          label="Tier lists updated"
          loading={loading}
          loadingLabel="Loading tier list..."
          nowMs={nowMs}
        />
        <GlobalRefreshNotice surface="tier" nowMs={nowMs} publishedAtUtc={payload?.generatedAtUtc} />
        {message ? <p className="tier-load-message" aria-live="polite">{message}</p> : null}
      </section>

      <section className="dashboard" aria-busy={loading} id="rankings-dashboard">
        <div className="tier-primary-controls">
          <div className="tier-mode-control" role="group" aria-label="Chart mode">
            {(["singles", "doubles", "coop"] as ModeKey[]).map(mode => <button aria-pressed={activeMode === mode} className={activeMode === mode ? "active" : ""} disabled={mode === "coop" && activeMetric !== "scoring"} key={mode} onClick={() => selectMode(mode)} type="button">{mode === "coop" ? "Co-op" : mode === "singles" ? "Singles" : "Doubles"}</button>)}
          </div>
          <label>Metric<select aria-label="Tier list metric" value={activeMetric} onChange={event => selectMetric(event.target.value as TierMetricKey)}>{(["scoring", "clearing", "pumbility"] as TierMetricKey[]).map(metric => <option key={metric} value={metric}>{metricLabels[metric]}</option>)}</select></label>
          {showScoringGoal ? <label>Goal<select aria-label="Scoring goal" value={scoringGoal} onChange={event => selectScoringGoal(event.target.value as ScoringGoal)}>{SCORING_GOALS.map(goal => <option key={goal} value={goal}>{goal === "general" ? "General" : scoringTargetForGoal(goal).label}</option>)}</select></label> : null}
        </div>

        {goalUnavailable ? <p className="metric-unavailable" role="status">{target.label} is unavailable in this analysis. <button type="button" onClick={() => selectScoringGoal("general")}>View General</button></p> : metricAvailability !== "available" ? (
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
              {activeMode !== "coop" ? <option value="20+">Only 20+</option> : null}
              {levels.map((level) => (
                <option key={level} value={level}>
                  {activeMode === "coop" ? `${level}x` : `${activeMode === "singles" ? "S" : "D"}${level}`}
                </option>
              ))}
            </select>
          </label>
          <button aria-expanded={filtersOpen} aria-controls="tier-more-filters" className="tier-toolbar-button" onClick={() => setFiltersOpen(value => !value)} type="button">Filters{selectedBpm ? " · 1" : ""}</button>
          <button aria-expanded={displayOpen} aria-controls="tier-display-settings" className="tier-toolbar-button" onClick={() => setDisplayOpen(value => !value)} type="button">Display</button>
        </div>
        <div className="tier-filter-disclosure" hidden={!filtersOpen} id="tier-more-filters"><BpmRangeFilter bounds={bpmBounds} onChange={(bpm) => updateFilter({ bpm })} selected={selectedBpm} /></div>
        <div className="tier-display-disclosure" hidden={!displayOpen} id="tier-display-settings">
          <label>Group by<select aria-label="Difficulty grouping" disabled={activeMode === "coop"} onChange={event => setGroupingView(event.target.value as GroupingView)} value={activeMode === "coop" ? "estimated" : groupingView}><option value="estimated">Difficulty</option><option value="tiers">Tier bands</option></select></label>
          <label>Chart layout<select aria-label="Chart layout" onChange={event => setLayoutView(event.target.value as LayoutView)} value={layoutView}><option value="compact">Named grid</option><option value="detailed">Readable list</option></select></label>
        </div>
        <div className="tier-results-context"><span aria-live="polite">{filteredCharts.length} charts · {activeMode === "coop" ? "Co-op" : activeMode === "singles" ? "Singles" : "Doubles"} · {metricLabels[activeMetric]}{showScoringGoal ? ` · ${scoringGoal === "general" ? "General" : target.label}` : ""}</span>{filter.query || filter.level !== "All" || selectedBpm ? <button onClick={() => updateFilter({...initialFilter})} type="button">Clear filters</button> : null}</div>
        <div className="tier-active-filters" aria-label="Applied filters">
          {filter.query ? <button aria-label="Remove search filter" onClick={() => updateFilter({query: ""})} type="button">Search: {filter.query} ×</button> : null}
          {filter.level !== "All" ? <button aria-label="Remove level filter" onClick={() => updateFilter({level: "All"})} type="button">{filter.level === "20+" ? "Only 20+" : activeMode === "coop" ? `${filter.level} players` : `${activeMode === "singles" ? "S" : "D"}${filter.level}`} ×</button> : null}
          {selectedBpm ? <button aria-label="Remove BPM filter" onClick={() => updateFilter({bpm: null})} type="button">{selectedBpm.min}–{selectedBpm.max} BPM ×</button> : null}
        </div>
        {selectedPlayerKey ? <div className="tier-score-freshness"><RefreshMeta generatedAtUtc={activePlayerScores?.syncedAtUtc} label="Player scores synced" loading={loadingScores} loadingLabel="Loading player scores..." nowMs={nowMs} />{scoresError ? <span role="status">{scoresError}</span> : null}</div> : null}

        {groupingView === "tiers" && activeMode !== "coop" ? (
          <ExplanatorySection title="About tier bands"><p>Within each band, charts are sorted by estimated difficulty − (official level + 0.5), from smallest difference to largest.</p></ExplanatorySection>
        ) : null}
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
            <p className="empty-tier">No charts match the current filters.</p>
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
        <ExplanatorySection title="About this tier list">
        {(hasMidpointCompression(payload) || localAveragePumbility) && activeMode !== "coop" ? <p className="metric-note">After the initial fit, Scoring and Clearing distances from the official level + 0.5 are halved. Pumbility {localAveragePumbility ? "averages the adjusted components in this local experiment" : adjustedAveragePumbility ? "averages the adjusted components" : "takes the higher adjusted component"}. Chart details summarize the records used and any data limitations.</p> : null}
        {localAveragePumbility && activeMetric === "pumbility" && activeMode !== "coop" ? <p className="metric-note"><b>Local experiment:</b> Pumbility = (Scoring + Clearing) / 2, using full precision. Both components are required.</p> : null}
        {phoenix2Only ? <p className="metric-note">Local experiment: Phoenix 2 data only.</p> : null}
        {showSssMethod ? <p className="metric-note"><b>{target.label} ≥{target.score.toLocaleString()}:</b> Scoring compares target attainment in recorded best scores, adjusting for the players observed on each chart. Levels 16–19 use submitted scores; 20+ use merged official leaderboards. Missing records are unknown. Pumbility averages this {target.label} estimate with the selected Clearing estimate; both are required.</p> : activeMode !== "coop" && hybridTiers ? <p className="metric-note">General Scoring uses submitted PIUScores score profiles for levels 16–19 and combined official Phoenix 1 / Phoenix 2 scores for 20+. Submitted Clearing uses full-history top-30 unique-clear skill with {minimumScoreRecords === null ? "the published requirement for valid records per mode" : `${minimumScoreRecords} valid records per mode`}. Its cross-level curve uses only 20+ anchors; levels 16–19 use their folder median and scale 0.70. Pumbility requires both displayed components.</p> : activeMode !== "coop" && crossLevelClearing && activeMetric !== "scoring" ? <p className="metric-note">Clearing uses submitted PIUScores records, top-30 unique-clear skill, and {minimumScoreRecords === null ? "the published record requirement per mode" : `a ${minimumScoreRecords}-record requirement per mode`}. Median reference thresholds anchor at official level + 0.5 across levels 16+. Scoring keeps its existing method; Pumbility requires both displayed components.</p> : activeMode !== "coop" && crossLevelScoring ? <p className="metric-note">Scoring combines official Phoenix 1 and Phoenix 2 scores across current levels 20+, keeping each matched player's higher score. Median reference chart costs anchor at official level + 0.5. Scoring and Pumbility below level 20 are Unrated. {crossLevelClearing ? "Clearing uses the submitted cross-level method." : "Clearing retains its existing sources and method."}</p> : activeMode !== "coop" && officialTiers?.enabled ? <p className="metric-note">Scoring uses official PIUScores per-player comparisons for S23+ / D25+. Clearing uses the original player-submitted method through S24 / D25, and official clearer ability for S25+ / D26+, using S22+ / D23+ histories. The 300-score Clearing default applies only to those higher official Clearing tiers.</p> : null}
        {showSssMethod && refinedSss ? <p className="metric-note">{spreadCalibrated ? <>At official level 20+, one-sided boards use their score gap to {target.score.toLocaleString()}, while rare-{target.label} charts use continuous conditional estimates with frozen player abilities and a folder prior. These corrected estimates remain provisional and have no formal interval.</> : <>{target.label} uses two passes: freeze the current rated range, then extend one-sided boards using their score gap to {target.score.toLocaleString()} and move well-supported rare-{target.label} charts toward its upper edge. These corrected estimates are provisional and have no formal interval.</>}</p> : null}
        {showSssMethod && medianCalibrated ? <p className="metric-note"><b>Final median calibration:</b> at official level 20+, each supported folder anchors its median refined chart effect at level + 0.5. The final exponential curve recalibrates spacing across levels, followed by a residual median offset, one 50% midpoint adjustment{spreadCalibrated ? ", a scale-0.55 continuous spread adjustment" : ""} and the tail policy. Derived anchors remain provisional. SSS+ Pumbility averages final Scoring with selected Clearing.</p> : null}
        {showSssMethod && centeredSss && !medianCalibrated ? <p className="metric-note"><b>{target.label} folder centering:</b> At official level 20+, supported folders have a final median of level + 0.5. Sparse folders borrow an offset within the same mode. Folder centering preserves ordering and spacing. {target.label} Pumbility averages the {tailPolicy ? "final post-policy" : "centered"} Scoring and existing Clearing values.</p> : null}
        {showSssMethod && level20Width ? <p className="metric-note">S20 and D20 ratings aim to keep most charts near their official level, while allowing unusually easy or hard charts outside it.</p> : showSssMethod && tailPolicy ? <p className="metric-note"><b>{spreadCalibrated ? "Spread and tails" : "Tail limits"}:</b> {spreadCalibrated ? "A smooth asinh curve moves distant charts toward the midpoint and retains continuous separation. The ordinary tail safeguard follows; evidence-based exceptions share a global budget of ten charts." : "Distances within ±1.5 of the official midpoint are unchanged; larger distances taper smoothly toward ±1.9. At most ten strongly supported exceptions across both modes and all levels may reach ±2 to ±2.5."} Supported folder medians remain at level + 0.5. Chart details summarize the records used and any data limitations.</p> : null}
        {officialD26Clearing && activeMetric !== "scoring" ? <p className="metric-note"><b>D26+ Clearing:</b> {combinedD26Clearing ? "Combined Phoenix 1 and Phoenix 2" : "Phoenix 2"} official clearer ability replaces the submitted method at D26 and above. It averages each player's 25 hardest other D23+ clears and uses the chart's 20th-percentile ability. Folder medians are recentered at level + 0.5 before midpoint adjustment.</p> : null}
        </ExplanatorySection>
        <ExplanatorySection title={`How ${activeMode === "coop" ? "Co-op" : metricLabels[activeMetric]} estimates work`}>
        {[56, 57].includes(payload?.schemaVersion ?? 0) && activeMode !== "coop" ? <p>Configured chart-version exclusions apply before score merging, player histories and fitting. Final SSS+ Scoring at level 20+ and official D26+ Clearing spread expands only narrow sides in displayed difficulty units, preserving supported folder medians and allowing cross-level outliers. Four-chart folder calibration remains provisional.</p> : null}
        {submittedSssplusCalibrated && activeMode === "doubles" && activeMetric !== "clearing" ? <p><b>Submitted D16–D19 SSS+ calibration:</b> the shared-player fit retains all eligible submitted Double 16+ histories. Each eligible lower folder is centered on its median chart effect at level + 0.5, using the median of the folders' 90th-percentile absolute effect residuals as one common scale. The initial scale multiplier is 0.8, followed by one 50% midpoint adjustment, the scale-0.55 asinh spread, fixed final width and the ordinary tail safeguard. No new fit or score pull is performed. These folder-relative estimates remain provisional; they measure observed best-score attainment and cannot separate practice effort from chart difficulty.</p> : null}
        {activeMetric === "clearing" ? <>
          {officialD26Clearing ? <>
          <p><b>Official {combinedD26Clearing ? "Phoenix 1 + Phoenix 2" : "Phoenix 2"} Clearing (D26+)</b> Player ability averages the current official midpoints of the 25 hardest other unique D23+ clears, requiring 25 other clears and excluding the target. Each directly estimated chart uses the interpolated 20th-percentile ability. The evidence weight is n / (n + 20) × n / N, with n eligible and N observed clearers; its raw offset is 0.70 × that weight × (chart threshold − folder median threshold).</p>
          {combinedD26Clearing ? <p>Official histories and target boards combine Phoenix 1 and Phoenix 2 by full game tag, retaining the higher score for each player and chart. {p1ObservedD26Clearing ? "Phoenix 1 cap flags are informational: those charts use their individual observed clearer skills, contribute to folder calibration and can receive conditional bootstrap intervals. Only Phoenix 2 cap flags trigger the existing fallback. Intervals describe the observed records and exclude selection uncertainty." : "Any source marked possibly truncated triggers the capped-board default; a merged count of 300 or more is not itself evidence of truncation."} Source cutoffs and dates remain visible in the chart evidence.</p> : null}
          <p>Robust folder spread calibration uses at least five charts with ten eligible clearers, targeting a 90th-percentile absolute offset of 0.45; sparse folders borrow a supported same-mode scale. Boards subject to the active cap fallback use the folder's lowest directly estimated difficulty, or its midpoint if none exists. Finite estimates are then recentered to an actual folder median of level + 0.5 and compressed once by 50%. Conditional bootstrap intervals use fixed player histories and fixed folder calibration. Cap-default assignments have no formal interval. All Singles and Doubles through D25 retain submitted Clearing.</p>
          </> : null}
          {crossLevelClearing ? <>
          <p><b>{hybridTiers ? "Submitted Clearing" : "Submitted cross-level Clearing"}</b> Each player's skill averages the current official levels of their 30 hardest unique clears. Players need {minimumScoreRecords === null ? "the published minimum number of" : minimumScoreRecords} distinct valid score records in the same mode across both Phoenix versions; duplicates count once and history below level 16 counts. Personal ratings retain their existing definition.</p>
          <p>Each submitted-method chart uses the linearly interpolated 10th percentile of qualified clearers' skills. Reference charts need at least 20 qualified clearers and comparable metadata. Each supported official folder needs five reference charts; its median threshold maps to official level + 0.5. {hybridTiers ? "The cross-level piecewise-linear curve is fitted only to level-20+ anchors and applies to 20+ charts, with linear boundary extrapolation. Below level 20, initial difficulty is the official midpoint + 0.70 × (chart threshold − eligible folder median threshold). No supported folder reference means Unrated. All levels still contribute to player skill and qualification." : "A single piecewise-linear curve per mode maps all charts across levels, with linear boundary extrapolation."}</p>
          <p>Below 20 qualified clearers, unsupported folders, changed or unknown chart metadata, and extrapolation receive weaker evidence. No usable skills means Unrated. These are successful best-record observations, not an attempt log: missing records are unknown and no pass probability or confidence interval is inferred.</p>
          <p>Submitted source snapshots: {Object.entries(crossLevelClearing.sourceDates).map(([mix, date]) => `${mix === "phoenix1" ? "Phoenix 1" : "Phoenix 2"}: ${date?.slice(0, 10) ?? "date unavailable"}`).join("; ")}.</p>
          </> : null}
          {!crossLevelClearing && showOfficialMethod ? <>
          <p><b>How official clearer-ability estimates work (S25+ / D26+)</b> Each player's ability is the average official level + 0.5 of their 25 hardest other distinct clears, requiring at least 25. The chart being rated is excluded. Singles and Doubles are separate, and official S22+ / D23+ boards supply player histories.</p>
          <p>Each uncapped chart uses the linearly interpolated 20th percentile of its eligible clearers' abilities. The raw difficulty difference is 0.70 × evidence weight × (chart ability percentile − folder median ability percentile). The reference uses uncapped charts with usable player histories in the same official folder.</p>
          <p>Calibration uses the 90th percentile of absolute evidence-weighted offsets among uncapped charts with at least ten eligible clearers. The coefficient is 0.45 / that spread, expanding or compressing the reference distribution so roughly 90% falls within level + 0.05 to level + 0.95 (the displayed x.0–x.9 buckets). This applies separately to each official level's reference charts, not exactly 90% of all charts overall. A level needs five supported charts to fit its scale; otherwise it borrows from the nearest supported same-mode level, or keeps coefficient 1 if none exists. Zero spread also keeps coefficient 1. Tails are unbounded.</p>
          <p>Outlier labels are descriptive: the raw difference and its entire ability interval must pass three interquartile ranges, with at least 20 eligible clearers and half the observed players represented. Labels never gate or clip an estimate. A level crossing and limited evidence are shown separately.</p>
          <p>Evidence weight is n / (n + 20) × n / N, where n is eligible clearers and N is observed unique clearers. A small or incomplete sample pulls toward the folder midpoint, rather than making an unpopular chart harder. Fewer than 20 eligible clearers means Insufficient evidence; none means Unrated. The record warning appears below 10 valid records. Bootstrap intervals describe the observed ability sample, with fixed player histories, and cannot account for player preferences or missing attempts.</p>
          <p>A full 300-score leaderboard may omit lower scores, so Clearing defaults to the lowest uncapped, nonempty chart estimate in the same mode and official-level folder. If none exists, it uses the folder midpoint (official level + 0.5), with that fallback identified in the chart details. Observed appearances on capped boards still support player histories. The capped chart itself receives a provisional default, not an estimate of its lower-tail ability.</p>
          </> : null}
          {!crossLevelClearing && showSubmittedMethod ? <>
          <p><b>Through S24 / D25</b> The original player-submitted clearing method applies.</p>
          <p><b>How clearing estimates work</b> A player's clearing skill is the average current official difficulty of their 50 hardest unique clears. At least 50 unique clears are required separately for Singles and Doubles. Repeat clears and charts cleared in both Phoenix versions count once.</p>
          <p>Each chart uses unique successful players from {phoenix2Only ? "Phoenix 2 only" : "either Phoenix version"}. {pointPercentile ? `We use the single ${pointPercentile.label}-percentile clearing skill, calculated with linear interpolation across all eligible clearers.` : `We average their clearing skills within the ${clearingMethod ? `${clearingMethod.label} percentile interval` : "published percentile interval"}, including both boundaries and ties.`} Players without available clearing skill (50+ unique clears in this mode) are excluded.</p>
          <p>The median chart in each official-level folder anchors at level + 0.5. Each skill point above or below the folder reference changes difficulty by {clearingScale} difficulty points. Estimates can cross official levels: an S20 can be 19.2. Calibration always uses the chart’s official-level folder. Sparse charts remain visible with evidence and limited-data labels; no usable estimate means Unrated.</p>
          <p>This estimates clearing difficulty from observed successful players, not pass probability or first-clear ability. Skill ratings can change after the recorded clear.</p>
          </> : null}
        </> : activeMetric === "pumbility" ? <>
          <p><b>How Pumbility estimates work</b> {adjustedAveragePumbility ? `${localAveragePumbility ? "This local experiment averages" : "Pumbility averages"} a chart’s Scoring and Clearing difficulties after their midpoint adjustment. Both components must be available. Calculations use full precision before one-decimal display truncation; there is no additional compression.` : maximumPumbility ? "Pumbility takes the higher of a chart’s scoring and clearing difficulty. Both components must be available. The maximum uses full-precision estimates before one-decimal display truncation." : pumbilityPolicy?.aggregation === "average" ? "This legacy generation uses the arithmetic average of a chart’s scoring and clearing difficulty. Both components must be available; calculations use full precision before display truncation." : "Pumbility requires both Scoring and Clearing estimates and a recognized aggregation policy."}</p>
          {showSssMethod ? <p>{target.label} Pumbility averages the selected {target.label} ≥{target.score.toLocaleString()} Scoring estimate{tailPolicy ? " after its tail policy" : ""} with the selected Clearing estimate. Levels 16–19 use the submitted target fit and folder-centered Clearing; 20+ use the independent official target fit. {officialD26Clearing ? `D26+ uses ${combinedD26Clearing ? "combined Phoenix 1 and Phoenix 2" : "Phoenix 2"} official clearer ability; other charts use submitted Clearing with 20+ calibration anchors.` : "Clearing at 20+ uses only 20+ calibration anchors."} Both components are required, and the weaker evidence determines the composite evidence. Missing {target.label} is not replaced with General{tailPolicy && scoringGoal !== "sss" ? " or historical SSS" : ""}.</p> : hybridTiers ? <p>Levels 16–19 combine the submitted score-profile Scoring estimate with submitted folder-centered Clearing. Current S20+ / D20+ combine official cross-level Scoring with {officialD26Clearing ? `${combinedD26Clearing ? "Combined Phoenix 1 and Phoenix 2" : "Phoenix 2"} official clearer ability at D26+, and submitted Clearing calibrated using only 20+ anchors elsewhere` : "submitted Clearing calibrated using only 20+ anchors"}. The component values agree with their respective tabs. Evidence follows the weaker component; extrapolated estimates remain provisional.</p> : crossLevelScoring ? <p>Current S20+ / D20+ use combined official cross-level Scoring. Below level 20, Scoring and Pumbility are Unrated. {crossLevelClearing ? "Clearing uses the submitted cross-level method." : "Clearing retains its existing submitted or official source."} Evidence follows the weaker component; extrapolated estimates are provisional.</p> : showOfficialMethod ? <p>S23+ / D25+ use official per-player Scoring. Through S24 / D25, that combines with the original player-submitted Clearing estimate. S25+ / D26+ combine it with official clearer ability; only these higher Clearing tiers use the capped-leaderboard default. The record warning appears when either displayed component uses fewer than 10 valid records; provisional evidence is shown separately. Missing either component means Unrated.</p> : null}
          {showSssMethod ? <p>The record warning follows the selected {target.label} and Clearing evidence and appears below 10 valid records in either displayed component. {refinedSss ? "Boundary and rare-success corrections are provisional and have no formal component or composite interval; unsupported calibration remains Unrated." : "One-sided target evidence is Unrated; provisional fits remain marked."} Each component's support is shown separately.</p> : showSubmittedMethod ? <p>Evidence follows the weaker component. The record warning appears below 10 contributors or {pointPercentile ? "rated" : "selected"} clearers in either displayed component. Provisional-comparison flags are shown separately. Each component's support is shown separately.</p> : null}
        </> : showSssMethod ? <>
          <p><b>How {target.label} Scoring estimates work</b> The target is a successful recorded best score of at least {target.score.toLocaleString()}, inclusive. A regularized logistic model fits separate player and chart effects. Players need four observed eligible charts. Their total observation weight is balanced so longer recorded histories do not dominate. Missing scores and broken records do not add failures.</p>
          <p>Submitted and official cohorts are fitted and calibrated independently for Singles and Doubles. The submitted fit uses valid 16+ histories and supplies levels 16–19; the official fit uses merged official 20+ scores and supplies 20+. Official Phoenix versions are matched by exact full game tag, keeping the higher score. Submitted and official identities are never joined by display name.</p>
          <p>{submittedSssplusCalibrated && activeMode === "doubles" ? "Submitted D16–D19 centers eligible chart effects within each folder and uses one common residual scale. Other cohorts retain their own supported median anchors at official level + 0.5, exponential segments and labeled boundary tangent extrapolation." : "Each source uses its own supported folder medians as anchors at official level + 0.5, with exponential segments and labeled boundary tangent extrapolation."} Reference charts for those exponential anchors need at least 20 usable contributors, at least five observed outcomes on each side of the target, and comparable metadata. Each reference folder needs five charts; at least three increasing anchors including the source's lowest level are required. {target.label} initial estimates and intervals receive the fixed 50% midpoint adjustment once.</p>
          <p>First-pass charts need both observed outcomes, five usable contributors, a calibrated comparison component and a stable converged fit. {refinedSss ? `The second pass classifies the complete available board before the history filter: no-${target.label} boards extend its frozen upper edge using the shortfall of ${topThreeNoSss ? "the average of the best three available scores (or fewer when fewer exist)" : "the highest score"} to ${target.score.toLocaleString()}, and all-${target.label} boards extend the lower edge from the lowest returned score's surplus. A held-out mixed-chart calibration converts score gaps into latent ${target.label} units. Sparse folders borrow range offsets within the same source and mode; unsupported calibration stays Unrated.` : "One-sided outcomes remain Unrated."} Sparse outcomes, weak information, truncated boards, comparability issues and extrapolation receive provisional evidence.</p>
          {refinedSss ? spreadCalibrated ? <p>At official level 20+, rare-{target.label} charts use continuous conditional estimates from observed player abilities and outcomes, with a folder prior. Prior spread is estimated within the same source and mode; unsupported estimates fall back to the frozen model's regularization. These effects replace the preliminary upper-band allocation before final folder calibration. Player abilities remain fixed, and the new conditional estimates have no formal interval. Submitted levels below 20 retain their existing refinement.</p> : <p>For one to four {target.label} results with matching full-board and usable evidence, a conditional chart-only fit supplies ordering within the highest 5% of the frozen numeric range. The correction blends by success count, usable support and success rate: one {target.label} among 20+ usable players gets full weight, while one among two does not. Player abilities, first-pass ranges and anchors stay fixed. Both boundary and rare-success corrections receive the midpoint adjustment once and have no formal interval.</p> : null}
          {centeredSss && !medianCalibrated ? <p>Final official {target.label} folders need five finite mixed first-pass charts with a positive range and five rated final charts to supply their own centering offset. All finite final estimates, including boundary and rare-success results, set the median at official level + 0.5. Sparse folders interpolate or borrow the nearest offset within their source and mode; their own medians need not equal that midpoint. The fixed offset is applied before the single 50% compression. Original fit values and frozen edges remain visible; effective ranges and existing intervals move by the same amount. {tailPolicy ? "The tail policy follows without recentering or changing player abilities and anchors. " : ""}Pumbility is averaged afterward and receives no independent centering.</p> : null}
          {tailPolicy ? spreadCalibrated ? <p>At official level 20+, the scale-0.55 asinh adjustment follows the single midpoint compression. It is a rating-scale policy that encourages central concentration without imposing normality or a fixed count per bucket. Rare-success charts use continuous conditional estimates instead of upper-band rank allocation. A bounded exception path can retain stronger outliers, including qualifying capped one-sided boards with explicit support and sensitivity checks. Exceptions share a global budget of ten, with chart ordering, ties and supported folder medians protected. Capped boards remain provisional and their score surplus does not establish population attainment.</p> : <p>After centering, distances up to ±1.5 from the current official midpoint remain unchanged. Larger ordinary distances taper toward ±1.9. An exception requires a supported, uncapped mixed-outcome first pass with 20 usable players, five outcomes on each side, supported local calibration, no extrapolation, a stable fit and a bootstrap interval wholly beyond ±2. Only eligible outer tails can qualify, with ties and median-defining charts protected; a global budget allows at most ten charts across both modes and all levels. Selected exceptions retain at most ±2.5. No one-sided, rare-corrected, capped or unsupported chart earns an exception from its score gap alone. This is a rating-scale policy, not added statistical evidence.</p> : null}
          <p>95% intervals resample whole players and refit the target model with calibration fixed{tailPolicy ? ", then transform their endpoints with the selected chart's fixed tail map and exception decision" : ""}. They exclude anchor uncertainty, effort, selective observation and missing attempts. This measures target attainment in observed best records, not a probability of succeeding on one attempt. General preserves the existing Scoring algorithm and has its own Pumbility list.</p>
        </> : activeMode !== "coop" && crossLevelScoring ? <>
          {hybridTiers && filteredCharts.some(chart => chart.level < 20) ? <>
          <p><b>Submitted score-profile Scoring (levels 16–19)</b> The existing PIUScores player-submitted model uses the 50th-, 75th-, and 90th-percentile successful scores, with one equally weighted score per player. Phoenix 1 scores are normalized to the current chart note count; Phoenix 2 replaces overlapping records. Player skill does not select or weight this sample.</p>
          <p>The profile matches the existing continuous mode reference curve, with double weight on the 90th-percentile squared difference. Initial difficulty is official level + 0.5 + the existing folder scale × (profile match − folder median profile match). The same fixed 50% midpoint adjustment is then applied once. The record warning appears below 10 players; provisional model evidence is shown separately. Intervals resample the complete profile 1,000 times with reference curve, folder median and folder scale fixed.</p>
          </> : null}
          <p><b>Official cross-level Scoring (S20+ / D20+)</b> Official Phoenix 1 and Phoenix 2 leaderboards are combined by chart ID and exact full game tag. Each matched player/chart contributes its higher raw official score. Unresolved identities stay separate; renamed tags may not match. Submitted and supplemented scores are excluded.</p>
          <p>Singles and Doubles are fitted separately across all eligible levels. Players need at least four distinct eligible charts; shared-player comparisons separate player strength from chart cost. Missing appearances never count as failures. Only the largest connected comparison group has a common calibrated coordinate; disconnected charts are Unrated.</p>
          <p>The median fitted cost among comparable reference charts in each supported official folder maps to level + 0.5. Each reference chart has at least ten contributing players, and each anchor needs five charts. Exponential segments join the exact median anchors; the curve is inverted after averaging score-point evidence. Score-point differences per level can grow at higher levels. The estimate is not an average of individually transformed player ratings.</p>
          <p>Unsupported folders and tangent extensions outside the supported anchors are provisional. Five to nine contributing players also give provisional evidence; fewer than five are Unrated. Charts with changed or unknown note counts cannot set anchors. Chart costs and calibrated difficulty use different units.</p>
          <p>95% conditional intervals resample player evidence 1,000 times with fitted effects and median anchors fixed. They exclude model-fit, identity, chart-comparability, effort, selection and truncated-board uncertainty. Official scores represent observed best scores, and each mix has its own source date. {hybridTiers ? "Levels 16–19 retain the submitted score-profile estimate. Missing official evidence at 20+ remains Unrated and never selects that lower-level fallback." : "Current levels below 20 are excluded and Unrated for Scoring."} {crossLevelClearing ? "Clearing uses submitted records; Co-op is unchanged." : "Clearing and Co-op retain their existing methods."}</p>
        </> : activeMode !== "coop" && profileScoring ? <>
          {showOfficialMethod ? <>
          <p><b>Official leaderboard scoring (S23+ / D25+)</b> Each player's best official Phoenix 2 scores are compared within the same mode and official level. A normal comparison needs three other charts. The baseline excludes the target score and adjusts for which charts the player played using shared players across the level. A lower-than-expected score means greater scoring difficulty. Missing leaderboard appearances never count as failures.</p>
          <p>Every player gets one equally weighted personal estimate per chart. One shared linear coefficient per level converts score gaps to difficulty; their full-precision mean is the chart estimate, with the median anchored at x.5. The coefficient is min(1, 0.45 / the 90th percentile of absolute chart offsets), using at least eight charts with ten or more eligible players and normal comparisons. This targets the central majority without stretching narrow distributions or limiting tails. Sparse levels borrow the nearest supported same-mode scale, or coefficient 0.4 without a donor. Outlier labels do not change estimates.</p>
          <p>Small folders, or folders with no normal comparison panel, can use fewer other charts and are labeled provisional. Disconnected groups have separate median anchors and cannot be compared reliably. Without any same-level comparison, a chart is Unrated. Intervals resample players 1,000 times with fitted adjustments and calibration held fixed; they do not capture effort, selection bias or hidden scores below the 300-score cap.</p>
          </> : null}
          {showSubmittedMethod ? <>
          {showOfficialMethod ? <p><b>Below S23 / D25</b> The player-submitted score population continues to apply.</p> : null}
          <p><b>How scoring estimates work</b> Each chart uses its {upperProfileScoring ? "50th-, 75th-, and 90th-percentile" : "10th-, 25th-, 50th-, 75th-, and 90th-percentile"} scores among observed successful players, with linear interpolation and one equally weighted score per player. Phoenix 1 scores are normalized to the current chart note count; Phoenix 2 replaces overlapping records. Player skill, Pumbility, and top/recent play windows do not weight or select the scoring sample.</p>
          <p>The {upperProfileScoring ? "three" : "five"} scores are matched against a continuous reference curve, separately for Singles and Doubles. The 90th-percentile squared score difference has double weight; each other percentile has weight one. Final difficulty is official level + 0.5 + folder scale × (profile match − median profile match in the chart's official folder). Each folder's median anchors at level + 0.5. Matching another folder's typical profile does not force a chart to receive that folder's midpoint.</p>
          <p>Each mode and official level has its own spread scale. Scales seek useful spreads while staying reasonably close to neighboring levels in the same mode; sparse folders borrow support from those neighbors. Narrow folders aim for about 1.0 grade across their middle 80%, while broader folders can retain wider spreads.</p>
          <p>Two-grade scoring moves should be rare: roughly 3–10 across Singles and Doubles is a guideline, with a soft penalty beyond ten, no minimum quota, and no hard cap. Limited-data charts count. Both directions count: for an S21, estimates of 23.0 or above and below 20.0 count. Pumbility uses the higher of scoring and clearing for current generations; historical generations retain their recorded aggregation method.</p>
          <p>References use folders with at least five charts having 20+ successful players each. Raw profile matches outside the reference range use linear extension; these estimates are less certain.</p>
          <p>The record warning means fewer than 10 successful players. Confidence intervals use 1,000 bootstrap samples of the complete score profile, with the same final calibration. The reference curve, folder center, and selected folder scale are held fixed, so scale-selection uncertainty is excluded. Intervals describe the observed best-score population; charts played mainly by strong players can receive lower scoring estimates.</p>
          </> : null}
        </> : activeMode !== "coop" ? <>
          <p><b>How scoring estimates work</b> Scoring difficulty compares player performance within official-level folders using {phoenix2Only ? "Phoenix 2 observations only" : "Phoenix 1 and Phoenix 2 observations"}. Folder ranks, confidence intervals, and official-level What-if estimates describe the scoring model.</p>
          <p>Legacy and new Phoenix 2 charts share one scoring model within each mode. Source-specific normalization is preserved, and Phoenix 1 and Phoenix 2 observations have equal weight. Official level + 0.5 is the folder reference before shrinkage; the final median can differ slightly.</p>
          <p>The record warning means fewer than 10 scoring contributors. Select Clearing for difficulty based on the skill of successful players, or Pumbility for the higher of both estimates in current generations.</p>
        </> : <>
        <p><b>How Co-op estimates work</b> Co-op charts share one 2x-5x tier list. Miss points are adjusted for player strength and Phoenix source using all observations, then a conditional 75th-percentile score is estimated for a median-strength Phoenix 2 player. The conditional quantile provides outlier robustness; raw scores and residuals are not trimmed.</p>
        <p>The resulting chart order anchors the easiest chart at continuous difficulty 10, the median chart at 16, and the hardest chart at 24.9, then truncates the published difficulty to a whole-number range from 10 through 24. This preserves the observed ordering without forcing a normal distribution. Co-op recommendation letter-grade goals are assigned from these whole-number difficulties.</p>
        </>}
        </ExplanatorySection>
      </footer>
      {selectedChart ? <ChartDetailDialog chart={selectedChart} metric={activeMetric} onClose={closeChartDialog} /> : null}
    </main>
    </TierDetailContext.Provider>
    </RatingCompressionContext.Provider>
    </ClearingRecordMinimumContext.Provider>
    </PumbilityPolicyContext.Provider>
    </TierScoresContext.Provider>
    </TierBrowseContext.Provider>
  );
}
