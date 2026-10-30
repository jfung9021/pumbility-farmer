"use client";

import Link from "next/link";
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
import { formatDisplayBpm, requireFocalBpmMetadata } from "../../lib/song-bpm";
import { loadTierChartDetails } from "../../lib/tier-chart-details";
import { chartEvidenceLines, plainTierDataWarning, TIER_INTERVAL_TOOLTIP } from "../../lib/tier-evidence-copy";
import { loadWithGenerationRecovery, MODEL_DELAY_THRESHOLD_MS } from "../../lib/refresh-reliability";
import { demoPayload } from "../../lib/demo-data";
import { resolvePumbilityPolicy, type PumbilityPolicy } from "../../lib/pumbility-policy";
import { compressDemoPayload, hasMidpointCompression } from "../../lib/tier-postprocessing";
import {
  formatCoopEstimatedDifficulty,
  formatEstimatedDifficulty,
} from "../../lib/format-difficulty";
import { tierMetricFromSearchParams, tierModeFromSearchParams } from "../../lib/page-view-state";
import { DEFAULT_SCORING_GOAL, SCORING_GOALS, hasScoringGoalVariants, isSssScoringChart, projectScoringGoal, scoringGoalFromSearchParams, scoringGoalUrl, scoringTargetForChart, scoringTargetForGoal } from "../../lib/scoring-variants";
import { clampBpmRange, formatTierScore, matchesTierBpm, matchesTierLevel, matchesTierPhoenix2Debut, tierBpmBounds, type BpmRange } from "../../lib/tier-filters";
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
  newPhoenix2Only: boolean;
};

type GroupingView = "tiers" | "estimated";
type LayoutView = "detailed" | "compact";

const initialFilter: FilterState = {
  query: "",
  level: "All",
  bpm: null,
  newPhoenix2Only: false,
};

const TierScoresContext = createContext<{
  selected: boolean;
  loading: boolean;
  unavailable: boolean;
  scores: Map<string, TierPlayerScore>;
}>({ selected: false, loading: false, unavailable: false, scores: new Map() });
const PumbilityPolicyContext = createContext<PumbilityPolicy | null>(null);
const RatingCompressionContext = createContext(false);
const TierDetailContext = createContext<{compact: boolean; generatedAtUtc: string; bpmMetadataRevision?: string; goal: ScoringGoal; select: (chart: ChartResult) => void; reload: (chartId: string) => Promise<{generatedAtUtc:string; bpmMetadataRevision?:string}>}>({compact:false, generatedAtUtc:"", goal:"general", select:() => {}, reload:async () => {throw new Error("Reload the tier list to continue.");}});

function PersonalChartScore({ chartId }: { chartId: string }) {
  const { selected, loading, unavailable, scores } = useContext(TierScoresContext);
  if (!selected) return null;
  const result = scores.get(chartId);
  if (!result) return <span className="tier-player-score tier-score-status" aria-label={unavailable ? "Player score unavailable" : loading ? "Loading player score" : "No recorded score"}>{unavailable ? "Unavailable" : loading ? "Loading…" : "—"}</span>;
  return <PlayerResult className="tier-player-score" compact formattedScore={formatTierScore(result.score)} plate={result.plateCode} score={result.score} />;
}

const TierBrowseContext = createContext({ metric: "scoring" as TierMetricKey, showRating: false });
function chartBpmLabel(chart: ChartResult) {
  return formatDisplayBpm(chart, false);
}

function ArtworkMetadata({ chart }: { chart: ChartResult }) {
  return <span className="tier-art-meta" aria-hidden="true">
    <span className="tier-art-bpm">{formatDisplayBpm(chart, false)}</span>
    <span className={`chart-difficulty-badge chart-difficulty-${chart.type.toLowerCase()}`}>{chartCountLabel(chart)}</span>
  </span>;
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
  const pumbilityPolicy = useContext(PumbilityPolicyContext);
  const pumbilityAggregation = pumbilityPolicy?.aggregation;
  const pumbilityDownwardAdjustmentScale = pumbilityPolicy?.downwardAdjustmentScale;
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
          <span>{chartBpmLabel(chart)}</span>
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
          {chartEvidenceLines(chart, metric, { minimumScoreRecords, pumbilityAggregation, pumbilityDownwardAdjustmentScale }).map((line) => <span key={line}>{line}</span>)}
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
        <ArtworkMetadata chart={chart} />
      </span>
      <span className="tier-row-copy"><span className="tier-song" title={chart.songName}>{chart.songName}</span>{showRating ? <span className="tier-chart-rating">{chartGrade(chart, metric)}</span> : null}</span>
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
    loadWithGenerationRecovery({generatedAtUtc:source.generatedAtUtc, bpmMetadataRevision:source.bpmMetadataRevision},
      generation => loadTierChartDetails(chart.chartId, generation.generatedAtUtc, generation.bpmMetadataRevision),
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
  }, [attempt, chart.chartId, metric, source.compact, source.generatedAtUtc, source.bpmMetadataRevision, source.goal, source.reload]);
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
        <span className="tier-art-title"><span className="tier-song">{chart.songName}</span></span>
        <ArtworkMetadata chart={chart} />
      </span>
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
              <ArtworkMetadata chart={chart} />
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

  const loadLatest = useCallback(async (showLoading = false, fresh = false) => {
    if (showLoading) setLoading(true);
    try {
      const response = await fetch(`/api/tier-list?bpmPolicy=focal-v1${fresh ? "&fresh=1" : ""}`, { cache: fresh ? "no-store" : "default" });
      if (response.status === 404) {
        setPayload(null);
        setMessage("No stored combined analysis is available yet.");
        return null;
      }
      const latest = await readJsonResponse<AnalysisPayload>(response);
      requireFocalBpmMetadata(latest.bpmMetadata);
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
    const latest = await loadLatest(false, true);
    if (!latest) throw new Error("The latest tier list could not be loaded. Reload the page to try again.");
    const chart = [...latest.singles, ...latest.doubles, ...(latest.coop ?? [])].find(row => row.chartId === chartId);
    if (!chart) {
      setSelectedChart(current => current?.chartId === chartId ? null : current);
      setMessage("This chart is no longer in the latest tier list.");
      throw new Error("This chart is no longer in the latest tier list.");
    }
    setSelectedChart(current => current?.chartId === chartId ? chart : current);
    return {generatedAtUtc:latest.generatedAtUtc, bpmMetadataRevision:latest.bpmMetadata?.bpmMetadataRevision};
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
      if (params.has("scoring") && params.get("scoring") !== selectedGoal) {
        window.history.replaceState(window.history.state, "", scoringGoalUrl(window.location.href, selectedGoal));
      }
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
  const extraFilterCount = Number(Boolean(selectedBpm)) + Number(filter.newPhoenix2Only);
  const levels = useMemo(
    () => [...new Set(modeCharts.map((chart) => chart.level))].sort((a, b) => a - b),
    [modeCharts],
  );
  const filteredCharts = useMemo(() => {
    const query = filter.query.trim().toLocaleLowerCase();
    return modeCharts.filter((chart) => {
      if (!matchesTierPhoenix2Debut(chart, filter.newPhoenix2Only)) return false;
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
    <PumbilityPolicyContext.Provider value={pumbilityPolicy}>
    <ClearingRecordMinimumContext.Provider value={minimumScoreRecords}>
    <RatingCompressionContext.Provider value={hasMidpointCompression(payload) || localAveragePumbility}>
    <TierDetailContext.Provider value={{compact:payload?.tierListView?.kind === "list", generatedAtUtc:payload?.generatedAtUtc ?? "", bpmMetadataRevision:payload?.bpmMetadata?.bpmMetadataRevision, goal:scoringGoal, select:setSelectedChart, reload:reloadSelectedChart}}>
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
          <button aria-expanded={filtersOpen} aria-controls="tier-more-filters" className="tier-toolbar-button" onClick={() => setFiltersOpen(value => !value)} type="button">Filters{extraFilterCount ? ` · ${extraFilterCount}` : ""}</button>
          <button aria-expanded={displayOpen} aria-controls="tier-display-settings" className="tier-toolbar-button" onClick={() => setDisplayOpen(value => !value)} type="button">Display</button>
        </div>
        <div className="tier-filter-disclosure" hidden={!filtersOpen} id="tier-more-filters">
          <label className="tier-phoenix2-toggle">
            <input checked={filter.newPhoenix2Only} onChange={(event) => updateFilter({ newPhoenix2Only: event.target.checked })} type="checkbox" />
            <span>New Phoenix 2 charts only<small>Includes new charts for older songs.</small></span>
          </label>
          <BpmRangeFilter bounds={bpmBounds} onChange={(bpm) => updateFilter({ bpm })} selected={selectedBpm} />
        </div>
        <div className="tier-display-disclosure" hidden={!displayOpen} id="tier-display-settings">
          <label>Group by<select aria-label="Difficulty grouping" disabled={activeMode === "coop"} onChange={event => setGroupingView(event.target.value as GroupingView)} value={activeMode === "coop" ? "estimated" : groupingView}><option value="estimated">Difficulty</option><option value="tiers">Tier bands</option></select></label>
          <label>Chart layout<select aria-label="Chart layout" onChange={event => setLayoutView(event.target.value as LayoutView)} value={layoutView}><option value="compact">Named grid</option><option value="detailed">Readable list</option></select></label>
        </div>
        <div className="tier-results-context"><span aria-live="polite">{filteredCharts.length} charts · {activeMode === "coop" ? "Co-op" : activeMode === "singles" ? "Singles" : "Doubles"} · {metricLabels[activeMetric]}{showScoringGoal ? ` · ${scoringGoal === "general" ? "General" : target.label}` : ""}</span>{filter.query || filter.level !== "All" || selectedBpm || filter.newPhoenix2Only ? <button onClick={() => updateFilter({...initialFilter})} type="button">Clear filters</button> : null}</div>
        <div className="tier-active-filters" aria-label="Applied filters">
          {filter.newPhoenix2Only ? <button aria-label="Remove new Phoenix 2 charts filter" onClick={() => updateFilter({newPhoenix2Only: false})} type="button">New Phoenix 2 charts only ×</button> : null}
          {filter.query ? <button aria-label="Remove search filter" onClick={() => updateFilter({query: ""})} type="button">Search: {filter.query} ×</button> : null}
          {filter.level !== "All" ? <button aria-label="Remove level filter" onClick={() => updateFilter({level: "All"})} type="button">{filter.level === "20+" ? "Only 20+" : activeMode === "coop" ? `${filter.level} players` : `${activeMode === "singles" ? "S" : "D"}${filter.level}`} ×</button> : null}
          {selectedBpm ? <button aria-label="Remove main BPM filter" onClick={() => updateFilter({bpm: null})} type="button">Main BPM {selectedBpm.min}–{selectedBpm.max} ×</button> : null}
        </div>
        {selectedPlayerKey ? <div className="tier-score-freshness"><RefreshMeta generatedAtUtc={activePlayerScores?.syncedAtUtc} label="Player scores synced" loading={loadingScores} loadingLabel="Loading player scores..." nowMs={nowMs} />{scoresError ? <span role="status">{scoresError}</span> : null}</div> : null}

        {groupingView === "tiers" && activeMode !== "coop" ? (
          <p className="tier-about-link"><Link href={profile.profileHref("/about#tier-bands")}>How tier bands work ↗</Link></p>
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
        <p className="tier-about-link">
          <Link href={profile.profileHref("/about#tier-lists")}>About Tier Lists ↗</Link>
          {" · "}
          <Link href={profile.profileHref(`/about#${activeMode === "coop" ? "coop" : activeMetric}`)}>How {activeMode === "coop" ? "Co-op" : metricLabels[activeMetric]} estimates work ↗</Link>
        </p>
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
