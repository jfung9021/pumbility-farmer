export type BpmRange = { min: number; max: number };
type ChartBpm = { bpmMin?: number | null; bpmMax?: number | null };

export function chartBpmRange(chart: ChartBpm): BpmRange | null {
  const { bpmMin, bpmMax } = chart;
  if (typeof bpmMin !== "number" || typeof bpmMax !== "number"
    || !Number.isFinite(bpmMin) || !Number.isFinite(bpmMax)
    || bpmMin <= 0 || bpmMax < bpmMin) return null;
  return { min: bpmMin, max: bpmMax };
}

export function tierBpmBounds(charts: ChartBpm[]): BpmRange | null {
  const ranges = charts.map(chartBpmRange).filter((range): range is BpmRange => range !== null);
  if (!ranges.length) return null;
  const min = Math.floor(Math.min(...ranges.map((range) => range.min)));
  const max = Math.ceil(Math.max(...ranges.map((range) => range.max)));
  return { min, max: Math.max(min + 1, max) };
}

export function matchesTierBpm(chart: ChartBpm, selected: BpmRange | null): boolean {
  if (!selected) return true;
  const bpm = chartBpmRange(chart);
  return bpm !== null && bpm.min >= selected.min && bpm.max <= selected.max;
}

export function clampBpmRange(range: BpmRange, bounds: BpmRange): BpmRange {
  const min = Math.max(bounds.min, Math.min(range.min, bounds.max));
  return { min, max: Math.max(min, Math.min(range.max, bounds.max)) };
}

export function formatTierScore(score: number): string {
  return `${Math.floor(score / 1000)}k`;
}
