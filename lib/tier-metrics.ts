import { hasLimitedData } from "./chart-evidence.ts";
import { truncateCoopEstimatedDifficulty, truncateEstimatedDifficulty } from "./format-difficulty.ts";
import type { ChartResult, ModeKey, TierMetricKey, TierMetricResult } from "./types";

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

export function hasLimitedTierData(chart: ChartResult, metric: TierMetricKey): boolean {
  if (metric === "scoring") return hasLimitedData(chart.nContributors);
  const clearing = chart.tierMetrics?.clearing;
  if (metric === "clearing") return hasLimitedData(clearing?.selectedCount ?? 0);
  const pumbility = chart.tierMetrics?.pumbility;
  return hasLimitedData(pumbility?.scoringSupportCount ?? 0)
    || hasLimitedData(pumbility?.clearingSupportCount ?? 0);
}

export function tierSupportLabel(chart: ChartResult, metric: TierMetricKey): string {
  if (metric === "scoring") return `${chart.nContributors} unique player observations`;
  if (metric === "clearing") return `${chart.tierMetrics?.clearing.selectedCount ?? 0} selected clearers`;
  const support = chart.tierMetrics?.pumbility;
  return `${support?.scoringSupportCount ?? 0} scoring contributors and ${support?.clearingSupportCount ?? 0} selected clearers`;
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
  return sortTierCharts(charts.filter((chart) => selectedTierMetric(chart, metric).effectBandRank === rank), metric);
}
