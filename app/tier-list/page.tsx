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
  clearingSkillPercentile,
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
  const profileScoring = "scoringScoreProfile" in chart;
  const clearing = chart.tierMetrics?.clearing;
  const officialEvidence = chart.officialEvidence;
  const abilityClearing = clearing?.skillMetric === "official-clearer-ability";
  const selectedOfficialEvidence = officialEvidence && (metric !== "clearing" || abilityClearing);
  const pointPercentile = clearingSkillPercentile(clearing);
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
          {selectedOfficialEvidence ? <span>{metric === "pumbility" && !abilityClearing ? "Official Scoring + player-submitted Clearing" : "Official PIUScores leaderboard"}</span> : metric === "clearing" ? <span>Player-submitted clears</span> : null}
          {selectedOfficialEvidence && selected.estimatedDifficulty != null && Math.floor(selected.estimatedDifficulty) !== chart.level ? <span>Estimate outside official level</span> : null}
          {metric === "scoring" ? <span><b>{chart.nContributors}</b> contributors</span> : null}
          {metric === "scoring" && !officialEvidence && chart.phoenix1Contributors !== undefined && chart.phoenix2Contributors !== undefined ? (
            <span><b>{chart.phoenix1Contributors}/{chart.phoenix2Contributors}</b> P1/P2</span>
          ) : null}
          {!isCoop && selected.levelRank !== null && selected.levelComparisonCharts !== null ? (
            <span><b>#{selected.levelRank}</b> of {selected.levelComparisonCharts} in {chart.difficulty}</span>
          ) : null}
        </div>
        {selectedOfficialEvidence ? (
          <div className="chart-meta metric-details">
            <span><b>{officialEvidence.rawRowCount}</b> leaderboard rows before validation</span>
            {officialEvidence.asOf ? <span>Official snapshot: <time dateTime={officialEvidence.asOf}>{officialEvidence.asOf.replace("T", " ").replace(/\.\d+Z$/, "Z").replace("Z", " UTC")}</time></span> : null}
            {officialEvidence.cutoffScore != null ? <span>Lowest returned score: <b>{officialEvidence.cutoffScore.toLocaleString()}</b></span> : null}
            {officialEvidence.possiblyTruncated ? <span>The leaderboard reached the 300-score limit. Scoring compares the observed players with their other same-level scores; players omitted by the cap remain unknown. {abilityClearing ? "Clearing uses the lowest uncapped estimate in this official-level folder." : "Clearing uses player-submitted clears and is unaffected by this cap."}</span> : null}
            {officialEvidence.unavailableReason ? <span>{officialEvidence.unavailableReason}</span> : null}
          </div>
        ) : null}
        {metric === "scoring" && officialEvidence ? (
          <div className="chart-meta metric-details">
            <span><b>{chart.scoringPlayerCount ?? 0}</b> players with comparisons, from <b>{chart.officialContributors ?? 0}</b> official leaderboard scores; every eligible player has equal weight.</span>
            {chart.scoringMeanGap != null ? <span>Average adjusted score gap: <b>{chart.scoringMeanGap.toLocaleString(undefined, { maximumFractionDigits: 0 })}</b> points. Positive means higher scoring difficulty than the level's midpoint.</span> : null}
            <span>Each player needs at least <b>{chart.scoringMinimumOtherCharts}</b> other charts at this official level. The target chart is excluded from their score baseline.</span>
            {chart.scoringGapStdDev != null ? <span>Player disagreement: <b>{chart.scoringGapStdDev.toLocaleString(undefined, { maximumFractionDigits: 0 })}</b> score points (standard deviation).</span> : null}
            {chart.difficultyCi95Low != null && chart.difficultyCi95High != null ? <span>95% player-bootstrap interval: <b>{chart.difficultyCi95Low.toFixed(2)}–{chart.difficultyCi95High.toFixed(2)}</b>. Conditional on fitted chart adjustments and scale; effort, selection and leaderboard-cap bias are not included.</span> : null}
            {chart.scoringDifficultyScale != null ? <span>Shared level coefficient: <b>{chart.scoringDifficultyScale.toFixed(3)}</b> per 10,000 points. The displayed estimate is the mean of personal difficulty estimates.</span> : null}
            {chart.scoringSpreadCalibration ? <span>Calibration: {chart.scoringSpreadCalibration.basis === "folder" ? "this official level" : chart.scoringSpreadCalibration.basis === "neighbor-folder" ? `borrowed from ${chart.type === "Single" ? "S" : "D"}${chart.scoringSpreadCalibration.referenceLevel}` : "conservative fallback"}. Narrow differences stay narrow; tails can cross official levels.</span> : null}
            {chart.scoringExtremeOutlier ? <span>Statistical scoring outlier: the average player gap passes this level's outer threshold. This label does not change the estimate.</span> : null}
            {chart.scoringProvisional && chart.estimatedDifficulty != null ? <span>Limited comparison data: this estimate is provisional or insufficiently supported.</span> : null}
            {(chart.scoringComponentCount ?? 1) > 1 ? <span>This level has disconnected comparison groups. This chart is anchored within its group of {chart.scoringComparisonCharts} charts; groups cannot be compared reliably.</span> : null}
            {chart.scoringUnratedReason ? <span>{chart.scoringUnratedReason}</span> : null}
          </div>
        ) : metric === "scoring" && profileScoring ? (
          <div className="chart-meta metric-details">
            {chart.scoringScoreProfile?.map((score, index) => <span key={index}>{[10, 25, 50, 75, 90][index]}th-percentile score: <b>{Math.round(score).toLocaleString()}</b>{index === 4 ? " (double weight)" : ""}</span>)}
            <span><b>{chart.nContributors}</b> unique successful players, equally weighted</span>
            {chart.scoringProfileMatchDifficulty != null ? <span>Profile match before calibration: <b>{chart.scoringProfileMatchDifficulty.toFixed(2)}</b></span> : null}
            {chart.scoringFolderReferenceDifficulty != null ? <span>Folder median match: <b>{chart.scoringFolderReferenceDifficulty.toFixed(2)}</b></span> : null}
            {chart.scoringDifficultyScale != null ? <span>Applied folder scale: <b>{chart.scoringDifficultyScale.toFixed(2)}</b></span> : null}
            {chart.scoringProfileRmse != null ? <span title="Weighted root mean squared score difference from the matched reference profile, with double weight on the 90th percentile; a larger gap means a poorer profile match">Profile match error: <b>{Math.round(chart.scoringProfileRmse).toLocaleString()}</b> score points</span> : null}
            {chart.scoringProfileExtrapolated ? <span>Raw profile match extends beyond the reference range; final difficulty uses folder centering and spread calibration.</span> : null}
            {chart.nContributors < 20 ? <span>Difficulty interval requires at least 20 players.</span> : null}
            {chart.estimatedDifficulty == null && chart.scoringScoreProfile && !officialEvidence ? <span>Insufficient mode reference data to estimate difficulty.</span> : null}
          </div>
        ) : null}
        {metric === "clearing" && clearing ? (
          <div className="chart-meta metric-details">
            {abilityClearing ? <>
              <span><b>{clearing.clearCount}</b> official leaderboard scores</span>
              <span><b>{clearing.ratedClearCount}/{chart.officialContributors ?? 0}</b> clearers with at least 25 other distinct S22+ / D23+ clears in this mode</span>
              <span><b>{clearing.missingSkillCount}</b> clearers have insufficient other-chart history</span>
              {clearing.q20Skill != null ? <span>20th-percentile clearer ability: <b>{clearing.q20Skill.toFixed(2)}</b></span> : null}
              {clearing.folderReferenceSkill != null ? <span>Folder median clearer ability: <b>{clearing.folderReferenceSkill.toFixed(2)}</b></span> : null}
              {!clearing.defaultedAtCap && clearing.shrinkageWeight != null ? <span>Evidence weight: <b>{(100 * clearing.shrinkageWeight).toFixed(1)}%</b>; the remainder pulls toward the folder midpoint.</span> : null}
              {!clearing.defaultedAtCap && clearing.spreadCalibration ? <span>Spread calibration: <b>{clearing.spreadCalibration.basis === "folder" ? "this official level" : clearing.spreadCalibration.basis === "neighbor-folder" ? `borrowed from ${chart.type === "Single" ? "S" : "D"}${clearing.spreadCalibration.referenceLevel}` : "conservative fallback"}</b>. Coefficient {clearing.spreadCalibration.scale.toFixed(3)}; the 90th-percentile reference spread targets ±0.45, and tails can cross official levels.</span> : null}
              {clearing.extremeOutlier ? <span>Statistical clearing outlier: the ability difference and its bootstrap interval pass the outlier threshold. This label does not change the estimate.</span> : null}
              {clearing.q20SkillCi95Low != null && clearing.q20SkillCi95High != null ? <span>Clearer ability 95% bootstrap interval: <b>{clearing.q20SkillCi95Low.toFixed(2)}–{clearing.q20SkillCi95High.toFixed(2)}</b> (fixed player histories; selection uncertainty excluded).</span> : !clearing.defaultedAtCap ? <span>At least 5 eligible clearers are needed for a bootstrap interval.</span> : null}
              {clearing.difficultyCi95Low != null && clearing.difficultyCi95High != null ? <span>Clearing difficulty 95% interval: <b>{clearing.difficultyCi95Low.toFixed(2)}–{clearing.difficultyCi95High.toFixed(2)}</b> (fixed histories, folder reference and scale).</span> : null}
              {!clearing.defaultedAtCap && clearing.ratedClearCount === 0 ? <span>No clearers have enough other-chart history; Clearing is Unrated.</span> : null}
              {clearing.defaultedAtCap ? <span>Cap default: <b>{chartGrade(chart, "clearing")}</b> ({clearing.capDefaultBasis === "folder-midpoint-no-uncapped-charts" ? "folder midpoint; no uncapped charts have a clearing estimate" : "lowest uncapped clearing estimate in this official-level folder"}).</span> : <span>Ability averages the 25 hardest other distinct official clears (25 required); leaderboard size only affects evidence strength.</span>}
            </> : <>
            <span><b>{clearing.ratedClearCount}/{clearing.clearCount}</b> clearers with available clearing skill (50+ unique clears in this mode)</span>
            {!pointPercentile ? <span><b>{clearing.selectedCount}</b> selected clearers</span> : null}
            <span><b>{clearing.missingSkillCount}</b> without clearing skill</span>
            {percentileRange?.lower != null && percentileRange.upper != null ? (
              <span>{percentileRange.label} percentile skill: <b>{percentileRange.lower.toFixed(2)}–{percentileRange.upper.toFixed(2)}</b> inclusive</span>
            ) : null}
            {pointPercentile?.value != null ? <span>{pointPercentile.label}-percentile skill: <b>{pointPercentile.value.toFixed(2)}</b></span> : null}
            {!pointPercentile && clearing.meanSkill != null ? <span>Selected mean skill: <b>{clearing.meanSkill.toFixed(2)}</b></span> : null}
            {clearing.folderReferenceSkill != null ? <span>Folder reference skill: <b>{clearing.folderReferenceSkill.toFixed(2)}</b></span> : null}
            </>}
          </div>
        ) : null}
        {metric === "pumbility" && pumbility ? (
          <div className="chart-meta metric-details">
            <span>Scoring: <b>{chartGrade(chart, "scoring")}</b></span>
            <span>Clearing: <b>{chartGrade(chart, "clearing")}</b></span>
            <span>Average: <b>{chartGrade(chart, "pumbility")}</b></span>
            <span><b>{pumbility.scoringSupportCount}</b> scoring contributors</span>
            <span><b>{pumbility.clearingSupportCount}</b> {abilityClearing ? "eligible official clearers" : `${pointPercentile ? "rated" : "selected"} clearers`}</span>
          </div>
        ) : null}
      </div>
      {isCoop ? null : (
        <div className={`delta ${delta !== null && delta < 0 ? "delta-easy" : "delta-hard"}`}>
          <span>difference</span>
          <strong>{delta === null ? "-" : signed(delta)}</strong>
          {metric === "scoring" && chart.difficultyCi95Low !== null && chart.difficultyCi95High !== null && !officialEvidence ? (
            <small title={profileScoring ? "95% bootstrap interval after calibration, with the mode reference curve, folder center, and selected folder scale held fixed; scale-selection uncertainty is excluded" : undefined}>{formatEstimatedDifficulty(chart.difficultyCi95Low)}-{formatEstimatedDifficulty(chart.difficultyCi95High)} CI</small>
          ) : null}
          {metric === "scoring" && !profileScoring && !officialEvidence ? <WhatIfDifficulty chart={chart} /> : null}
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
  const phoenix2Only = payload?.summary.method.sourceSelection === "phoenix2-only";
  const profileScoring = (payload?.summary.method.scoring as { calibration?: string } | undefined)?.calibration === "folder-scaled-score-profile";
  const officialTiers = payload?.summary.method.officialTiers;
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
  const clearingExample = modeCharts.find((chart) => chart.tierMetrics?.clearing && chart.tierMetrics.clearing.skillMetric !== "official-clearer-ability")?.tierMetrics?.clearing;
  const pointPercentile = clearingSkillPercentile(clearingExample);
  const clearingMethod = clearingPercentileRange(clearingExample);
  const tierMethod = payload?.summary.method.tierMetrics as {
    clearing?: { difficultyDeltaScale?: number };
  } | undefined;
  const clearingScale = tierMethod?.clearing?.difficultyDeltaScale ?? 1;
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
  const showOfficialMethod = activeMode !== "coop" && officialTiers?.enabled
    && filteredCharts.some((chart) => activeMetric === "clearing"
      ? chart.tierMetrics?.clearing.skillMetric === "official-clearer-ability" : chart.officialEvidence);
  const showSubmittedMethod = !showOfficialMethod || filteredCharts.some((chart) => activeMetric === "scoring"
    ? !chart.officialEvidence : chart.tierMetrics?.clearing.skillMetric !== "official-clearer-ability");
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
        {phoenix2Only ? <p className="metric-note">Local experiment: Phoenix 2 data only.</p> : null}
        {activeMode !== "coop" && officialTiers?.enabled ? <p className="metric-note">Scoring and Clearing use official PIUScores leaderboards for S25+ / D26+. Both use the original player-submitted methods through S24 / D25. Official Clearing uses S22+ / D23+ histories to estimate clearer ability; its 300-score default applies only at S25+ / D26+.</p> : null}
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

        {groupingView === "tiers" && activeMode !== "coop" ? (
          <p className="metric-note">Within each band, charts are sorted by estimated difficulty − (official level + 0.5), from smallest difference to largest.</p>
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
          {showOfficialMethod ? <>
          <p><b>How official clearer-ability estimates work (S25+ / D26+)</b> Each player's ability is the average official level + 0.5 of their 25 hardest other distinct clears, requiring at least 25. The chart being rated is excluded. Singles and Doubles are separate, and official S22+ / D23+ boards supply player histories.</p>
          <p>Each uncapped chart uses the linearly interpolated 20th percentile of its eligible clearers' abilities. The raw difficulty difference is 0.70 × evidence weight × (chart ability percentile − folder median ability percentile). The reference uses uncapped charts with usable player histories in the same official folder.</p>
          <p>Calibration uses the 90th percentile of absolute evidence-weighted offsets among uncapped charts with at least ten eligible clearers. The coefficient is 0.45 / that spread, expanding or compressing the reference distribution so roughly 90% falls within level + 0.05 to level + 0.95 (the displayed x.0–x.9 buckets). This applies separately to each official level's reference charts, not exactly 90% of all charts overall. A level needs five supported charts to fit its scale; otherwise it borrows from the nearest supported same-mode level, or keeps coefficient 1 if none exists. Zero spread also keeps coefficient 1. Tails are unbounded.</p>
          <p>Outlier labels are descriptive: the raw difference and its entire ability interval must pass three interquartile ranges, with at least 20 eligible clearers and half the observed players represented. Labels never gate or clip an estimate. A level crossing and limited evidence are shown separately.</p>
          <p>Evidence weight is n / (n + 20) × n / N, where n is eligible clearers and N is observed unique clearers. A small or incomplete sample pulls toward the folder midpoint, rather than making an unpopular chart harder. Fewer than 20 eligible clearers means limited data; none means Unrated. Bootstrap intervals describe the observed ability sample, with fixed player histories, and cannot account for player preferences or missing attempts.</p>
          <p>A full 300-score leaderboard may omit lower scores, so Clearing defaults to the lowest uncapped, nonempty chart estimate in the same mode and official-level folder. If none exists, it uses the folder midpoint (official level + 0.5), with that fallback identified in the chart details. Observed appearances on capped boards still support player histories. The capped chart itself receives a provisional default, not an estimate of its lower-tail ability.</p>
          </> : null}
          {showSubmittedMethod ? <>
          <p><b>Through S24 / D25</b> The original player-submitted clearing method applies.</p>
          <p><b>How clearing estimates work</b> A player's clearing skill is the average current official difficulty of their 50 hardest unique clears. At least 50 unique clears are required separately for Singles and Doubles. Repeat clears and charts cleared in both Phoenix versions count once.</p>
          <p>Each chart uses unique successful players from {phoenix2Only ? "Phoenix 2 only" : "either Phoenix version"}. {pointPercentile ? `We use the single ${pointPercentile.label}-percentile clearing skill, calculated with linear interpolation across all eligible clearers.` : `We average their clearing skills within the ${clearingMethod ? `${clearingMethod.label} percentile interval` : "published percentile interval"}, including both boundaries and ties.`} Players without available clearing skill (50+ unique clears in this mode) are excluded.</p>
          <p>The median chart in each official-level folder anchors at level + 0.5. Each skill point above or below the folder reference changes difficulty by {clearingScale} difficulty points. Estimates can cross official levels: an S20 can be 19.2. Calibration always uses the chart’s official-level folder. Sparse charts remain visible with evidence and limited-data labels; no usable estimate means Unrated.</p>
          <p>This estimates clearing difficulty from observed successful players, not pass probability or first-clear ability. Skill ratings can change after the recorded clear.</p>
          </> : null}
        </> : activeMetric === "pumbility" ? <>
          <p><b>How Pumbility estimates work</b> Pumbility is the arithmetic average of a chart’s scoring and clearing difficulty. Both components must be available. The average uses full-precision estimates before one-decimal display truncation, so displayed components may average slightly differently.</p>
          {showOfficialMethod ? <p>S25+ / D26+ combine official per-player Scoring with official clearer ability. Through S24 / D25, both components use the original player-submitted methods. Only the higher official Clearing tiers use the capped-leaderboard default. Official Scoring is limited with fewer than 10 players or provisional comparisons; Clearing is limited with fewer than 20 rated clearers from its respective source. Missing either component means Unrated.</p> : null}
          {showSubmittedMethod ? <p>Evidence follows the weaker component. Player-submitted components have limited data with fewer than 20 contributors or {pointPercentile ? "rated" : "selected"} clearers. Official Scoring uses its 10-player threshold and provisional-comparison flags. Each component's support is shown separately.</p> : null}
        </> : activeMode !== "coop" && profileScoring ? <>
          {showOfficialMethod ? <>
          <p><b>Official leaderboard scoring (S25+ / D26+)</b> Each player's best official Phoenix 2 scores are compared within the same mode and official level. A normal comparison needs three other charts. The baseline excludes the target score and adjusts for which charts the player played using shared players across the level. A lower-than-expected score means greater scoring difficulty. Missing leaderboard appearances never count as failures.</p>
          <p>Every player gets one equally weighted personal estimate per chart. One shared linear coefficient per level converts score gaps to difficulty; their full-precision mean is the chart estimate, with the median anchored at x.5. The coefficient is min(1, 0.45 / the 90th percentile of absolute chart offsets), using at least eight charts with ten or more eligible players and normal comparisons. This targets the central majority without stretching narrow distributions or limiting tails. Sparse levels borrow the nearest supported same-mode scale, or coefficient 0.4 without a donor. Outlier labels do not change estimates.</p>
          <p>Small folders, or folders with no normal comparison panel, can use fewer other charts and are labeled provisional. Disconnected groups have separate median anchors and cannot be compared reliably. Without any same-level comparison, a chart is Unrated. Intervals resample players 1,000 times with fitted adjustments and calibration held fixed; they do not capture effort, selection bias or hidden scores below the 300-score cap.</p>
          </> : null}
          {showSubmittedMethod ? <>
          {showOfficialMethod ? <p><b>Below S25 / D26</b> The player-submitted score population continues to apply.</p> : null}
          <p><b>How scoring estimates work</b> Each chart uses its 10th-, 25th-, 50th-, 75th-, and 90th-percentile scores among observed successful players, with linear interpolation and one equally weighted score per player. Phoenix 1 scores are normalized to the current chart note count; Phoenix 2 replaces overlapping records. Player skill, Pumbility, and top/recent play windows do not weight or select the scoring sample.</p>
          <p>The five scores are matched against a continuous reference curve, separately for Singles and Doubles. The 90th-percentile squared score difference has double weight; each other percentile has weight one. Final difficulty is official level + 0.5 + folder scale × (profile match − median profile match in the chart's official folder). Each folder's median anchors at level + 0.5. Matching another folder's typical profile does not force a chart to receive that folder's midpoint.</p>
          <p>Each mode and official level has its own spread scale, shown in chart details. Scales seek useful spreads while staying reasonably close to neighboring levels in the same mode; sparse folders borrow support from those neighbors. Narrow folders aim for about 1.0 grade across their middle 80%, while broader folders can retain wider spreads.</p>
          <p>Two-grade scoring moves should be rare: roughly 3–10 across Singles and Doubles is a guideline, with a soft penalty beyond ten, no minimum quota, and no hard cap. Limited-data charts count. Both directions count: for an S21, estimates of 23.0 or above and below 20.0 count. Pumbility remains the arithmetic average of scoring and clearing.</p>
          <p>References use folders with at least five charts having 20+ successful players each. Raw profile matches outside the reference range use linear extension and are marked in chart details before final calibration. Profile match error shows how closely a chart resembles its reference; lower is a closer fit.</p>
          <p>Limited data means fewer than 20 successful players. Confidence intervals use 1,000 bootstrap samples of the complete score profile, with the same final calibration. The reference curve, folder center, and selected folder scale are held fixed, so scale-selection uncertainty is excluded. Intervals describe the observed best-score population; charts played mainly by strong players can receive lower scoring estimates.</p>
          </> : null}
        </> : activeMode !== "coop" ? <>
          <p><b>How scoring estimates work</b> Scoring difficulty compares player performance within official-level folders using {phoenix2Only ? "Phoenix 2 observations only" : "Phoenix 1 and Phoenix 2 observations"}. Folder ranks, confidence intervals, and official-level What-if estimates describe the scoring model.</p>
          <p>Legacy and new Phoenix 2 charts share one scoring model within each mode. Source-specific normalization is preserved, and Phoenix 1 and Phoenix 2 observations have equal weight. Official level + 0.5 is the folder reference before shrinkage; the final median can differ slightly.</p>
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
