import { displayBpmRange, focalBpm, type SongBpm } from "./song-bpm.ts";
import phoenix2DebutCharts from "../config/phoenix2-debut-charts.json" with { type: "json" };

export type BpmRange = { min: number; max: number };
type ChartBpm = SongBpm;
const phoenix2DebutChartIds = new Set(phoenix2DebutCharts.chartIds);

export function matchesTierPhoenix2Debut(chart: { chartId: string; originalMix?: string }, selected: boolean): boolean {
  if (!selected) return true;
  if (chart.originalMix !== undefined) return chart.originalMix === "Phoenix2";
  return phoenix2DebutChartIds.has(chart.chartId);
}

export function matchesTierLevel(chart: { level: number; type: string }, selected: string): boolean {
  if (selected === "All") return true;
  if (selected === "20+") return chart.type !== "CoOp" && chart.level >= 20;
  return chart.level === Number(selected);
}

export function chartBpmRange(chart: ChartBpm): BpmRange | null {
  return displayBpmRange(chart);
}

export function tierBpmBounds(charts: ChartBpm[]): BpmRange | null {
  const bpms = charts.map(focalBpm).filter((bpm): bpm is number => bpm !== null);
  if (!bpms.length) return null;
  const min = Math.floor(Math.min(...bpms));
  const max = Math.ceil(Math.max(...bpms));
  return { min, max: Math.max(min + 1, max) };
}

export function matchesTierBpm(chart: ChartBpm, selected: BpmRange | null): boolean {
  if (!selected) return true;
  const bpm = focalBpm(chart);
  return bpm !== null && bpm >= selected.min && bpm <= selected.max;
}

export function clampBpmRange(range: BpmRange, bounds: BpmRange): BpmRange {
  const min = Math.max(bounds.min, Math.min(range.min, bounds.max));
  return { min, max: Math.max(min, Math.min(range.max, bounds.max)) };
}

export function formatTierScore(score: number): string {
  return `${Math.floor(score / 1000)}k`;
}
