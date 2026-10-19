import { hasLimitedData } from "./chart-evidence.ts";
import { truncateCoopEstimatedDifficulty, truncateEstimatedDifficulty } from "./format-difficulty.ts";
import { isSssScoringChart, isTargetBoundaryChart, scoringTargetForChart } from "./scoring-variants.ts";
import type { AnalysisPayload, ChartResult, ClearingTierMetric, ModeKey, TierMetricKey, TierMetricResult } from "./types";

export function clearingSkillPercentile(clearing: ClearingTierMetric | undefined) {
  if (!clearing) return null;
  if ("q20Skill" in clearing) return { label: "20th", value: clearing.q20Skill ?? null };
  if ("q10Skill" in clearing && !("q50Skill" in clearing) && !("q30Skill" in clearing)) {
    return { label: "10th", value: clearing.q10Skill ?? null };
  }
  return null;
}

export function clearingPercentileRange(clearing: ClearingTierMetric | undefined) {
  if (!clearing) return null;
  if (clearingSkillPercentile(clearing)) return null;
  if ("q0Skill" in clearing && "q50Skill" in clearing) {
    return { label: "0th–50th", lower: clearing.q0Skill ?? null, upper: clearing.q50Skill ?? null };
  }
  if ("q10Skill" in clearing && "q50Skill" in clearing) {
    return { label: "10th–50th", lower: clearing.q10Skill ?? null, upper: clearing.q50Skill ?? null };
  }
  if ("q10Skill" in clearing && "q30Skill" in clearing) {
    return { label: "10th–30th", lower: clearing.q10Skill ?? null, upper: clearing.q30Skill ?? null };
  }
  if ("q25Skill" in clearing && "q50Skill" in clearing) {
    return { label: "25th–50th", lower: clearing.q25Skill ?? null, upper: clearing.q50Skill ?? null };
  }
  return null;
}

export function hasQualifiedSubmittedClearing(clearing: ClearingTierMetric | undefined): boolean {
  return clearing?.method === "submitted-cross-level-clearing-v1"
    || clearing?.method === "submitted-cross-level-clearing-v2"
    || clearing?.method === "submitted-folder-centered-clearing-v1";
}

const unratedMetric: TierMetricResult = {
  estimatedDifficulty: null,
  difficultyDelta: null,
  levelRank: null,
  levelComparisonCharts: null,
  effectBandRank: null,
  effectBand: null,
  evidenceStatus: "Unrated",
};

export function selectedTierMetric(chart: ChartResult, metric: TierMetricKey): TierMetricResult {
  return metric === "scoring" ? chart : chart.tierMetrics?.[metric] ?? unratedMetric;
}

export function tierMetricAvailability(charts: ChartResult[], metric: TierMetricKey, mode: ModeKey) {
  if (metric === "scoring") return "available";
  if (mode === "coop") return "unsupported";
  return charts.some((chart) => chart.tierMetrics?.[metric]) ? "available" : "unavailable";
}

function validRecordCount(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

export function scoringRecordCount(chart: ChartResult): number | null {
  if (isTargetBoundaryChart(chart)) return validRecordCount(chart.scoringRefinement?.fullBoard.records)
    ?? validRecordCount(chart.scoringBoundaryRecords) ?? validRecordCount(chart.nContributors);
  if (isSssScoringChart(chart)) return validRecordCount(chart.nContributors);
  if (chart.officialEvidence || chart.officialScoringEvidence || chart.scoringMethod === "official-cross-level-v1") {
    return validRecordCount(chart.scoringPlayerCount);
  }
  return validRecordCount(chart.nContributors);
}

export function clearingRecordCount(chart: ChartResult): number | null {
  const clearing = chart.tierMetrics?.clearing;
  return validRecordCount(hasQualifiedSubmittedClearing(clearing) || clearing?.skillMetric === "official-clearer-ability"
    || clearing?.method?.startsWith("official-d26-clearer-ability") || clearingSkillPercentile(clearing)
    ? clearing?.ratedClearCount : clearing?.selectedCount);
}

export function hasLimitedTierData(chart: ChartResult, metric: TierMetricKey): boolean {
  const limited = (count: number | null) => count === null || hasLimitedData(count);
  return metric === "scoring" ? limited(scoringRecordCount(chart))
    : metric === "clearing" ? limited(clearingRecordCount(chart))
      : limited(scoringRecordCount(chart)) || limited(clearingRecordCount(chart));
}

/** Read the requirement belonging to this publication, including older generations. */
export function clearingMinimumScoreRecords(payload: AnalysisPayload | null | undefined): number | null {
  const count = payload?.summary.method.crossLevelClearing?.skillMethod.minimumScoreRecords;
  return count === 100 || count === 200 ? count : null;
}

export function tierSupportLabel(chart: ChartResult, metric: TierMetricKey): string {
  const clearingMetric = chart.tierMetrics?.clearing;
  const scoringCount = scoringRecordCount(chart);
  const clearingCount = clearingRecordCount(chart);
  const number = (count: number | null) => count === null ? "Unknown number of" : String(count);
  const officialClearing = clearingMetric?.skillMetric === "official-clearer-ability"
    || clearingMetric?.method?.startsWith("official-d26-clearer-ability");
  const ratedClearing = officialClearing || hasQualifiedSubmittedClearing(clearingMetric) || clearingSkillPercentile(clearingMetric);
  const officialClearerLabel = `${number(clearingCount)} eligible official clearers from ${clearingMetric?.clearCount ?? 0} observed`;
  if (metric === "clearing" && clearingMetric?.method?.startsWith("official-d26-clearer-ability")) return officialClearerLabel;
  if (isSssScoringChart(chart) && metric !== "clearing") {
    const board = chart.scoringRefinement?.fullBoard;
    const target = scoringTargetForChart(chart);
    const scoring = isTargetBoundaryChart(chart)
      ? `${number(scoringCount)} ${board || chart.scoringBoundaryRecords !== undefined ? "available best scores" : "reported contributors (complete-board count unavailable)"}${board ? ` (${board.successes} at ${target.label}, ${board.belowTarget} below)` : ""}; boundary estimate`
      : `${number(scoringCount)} target-fit contributors (${chart.scoringTargetSuccesses ?? 0} at ${target.label}, ${chart.scoringBelowTarget ?? 0} below)`;
    return metric === "scoring" ? scoring : `${scoring} and ${officialClearing ? officialClearerLabel : `${number(clearingCount)} ${ratedClearing ? "rated" : "selected"} clearers`}`;
  }
  if (chart.officialEvidence || chart.scoringMethod === "official-cross-level-v1") {
    const scoring = chart.scoringMethod === "official-cross-level-v1"
      ? `${number(scoringCount)} players with cross-level official comparisons`
      : `${number(scoringCount)} players with same-level comparisons`;
    const clearingMetric = chart.tierMetrics?.clearing;
    const clearing = clearingMetric?.skillMetric === "official-clearer-ability"
      ? `${number(clearingCount)} eligible official clearers`
      : hasQualifiedSubmittedClearing(clearingMetric) || clearingSkillPercentile(clearingMetric)
        ? `${number(clearingCount)} rated submitted clearers`
        : `${number(clearingCount)} selected submitted clearers`;
    return metric === "scoring" ? scoring : metric === "clearing" ? clearing : `${scoring} and ${clearing}`;
  }
  if (metric === "scoring") return `${number(scoringCount)} unique player observations`;
  const clearing = chart.tierMetrics?.clearing;
  const pointPercentile = clearingSkillPercentile(clearing);
  const rated = hasQualifiedSubmittedClearing(clearing) || pointPercentile;
  if (metric === "clearing" && rated) return `${number(clearingCount)} rated clearers`;
  if (metric === "clearing") return `${number(clearingCount)} selected clearers`;
  return `${number(scoringCount)} scoring contributors and ${number(clearingCount)} ${rated ? "rated" : "selected"} clearers`;
}

export function tierDataWarning(chart: ChartResult, metric: TierMetricKey): string | null {
  if (!hasLimitedTierData(chart, metric)) return null;
  const missing = metric !== "clearing" && scoringRecordCount(chart) === null
    || metric !== "scoring" && clearingRecordCount(chart) === null;
  return `Limited data: ${tierSupportLabel(chart, metric)}. ${missing
    ? "A valid-record count is unavailable."
    : selectedTierMetric(chart, metric).estimatedDifficulty === null ? "No rating is available for this evidence."
      : metric === "pumbility" ? "At least one component uses fewer than 10 valid records." : "This rating uses fewer than 10 valid records."}`;
}

export function sortTierCharts(charts: ChartResult[], metric: TierMetricKey): ChartResult[] {
  return [...charts].sort((left, right) =>
    (selectedTierMetric(left, metric).estimatedDifficulty ?? Infinity)
      - (selectedTierMetric(right, metric).estimatedDifficulty ?? Infinity)
    || left.songName.localeCompare(right.songName)
    || left.chartId.localeCompare(right.chartId));
}

export function estimatedTierGroups(charts: ChartResult[], metric: TierMetricKey, mode: ModeKey) {
  const groups = new Map<number, ChartResult[]>();
  for (const chart of sortTierCharts(charts, metric)) {
    const estimate = selectedTierMetric(chart, metric).estimatedDifficulty;
    if (estimate === null) continue;
    const value = mode === "coop"
      ? truncateCoopEstimatedDifficulty(estimate)
      : truncateEstimatedDifficulty(estimate);
    const group = groups.get(value) ?? [];
    group.push(chart);
    groups.set(value, group);
  }
  return [...groups.entries()].sort(([left], [right]) => left - right)
    .map(([value, group]) => ({ value, charts: group }));
}

export function tierBandCharts(charts: ChartResult[], metric: TierMetricKey, rank: number) {
  const difference = (chart: ChartResult) => {
    const estimate = selectedTierMetric(chart, metric).estimatedDifficulty;
    return estimate === null ? Infinity : estimate - (chart.level + 0.5);
  };
  return charts.filter((chart) => selectedTierMetric(chart, metric).effectBandRank === rank)
    .sort((left, right) => difference(left) - difference(right)
      || left.songName.localeCompare(right.songName)
      || left.chartId.localeCompare(right.chartId));
}
