import type { OfficialComparisonBucket, OfficialComparisonChart } from "./official-comparison.ts";

export type ComparisonMode = "singles" | "doubles";

export type ComparisonView = {
  left: string;
  right: string;
  mode: ComparisonMode;
  level: number;
  bpm: number;
  contributorMode: ComparisonMode;
  q: string;
};

export type ComparisonRow = {
  chart: OfficialComparisonChart;
  leftScore: number | null;
  rightScore: number | null;
  delta: number | null;
  winner: "left" | "right" | "tie" | null;
};

export function comparisonViewFromSearchParams(params: URLSearchParams): ComparisonView {
  const level = Number(params.get("level"));
  const bpm = Number(params.get("bpm"));
  return {
    left: params.get("left") || "",
    right: params.get("right") || "",
    mode: params.get("mode") === "doubles" ? "doubles" : "singles",
    level: Number.isInteger(level) && level >= 20 && level <= 99 ? level : 20,
    bpm: Number.isInteger(bpm) && bpm >= 0 && bpm < 9 ? bpm : 0,
    contributorMode: params.get("contributorMode") === "doubles" ? "doubles" : "singles",
    q: params.get("q") || "",
  };
}

export function comparisonSearchParams(view: ComparisonView, existing = new URLSearchParams()): URLSearchParams {
  const params = new URLSearchParams(existing);
  for (const side of ["left", "right"] as const) {
    if (view[side]) params.set(side, view[side]);
    else params.delete(side);
  }
  params.set("mode", view.mode);
  params.set("level", String(view.level));
  params.set("bpm", String(view.bpm));
  params.set("contributorMode", view.contributorMode);
  if (view.q) params.set("q", view.q);
  else params.delete("q");
  return params;
}

export function comparisonFolderLevels(charts: OfficialComparisonChart[], mode: ComparisonMode): number[] {
  return [...new Set(charts.filter((chart) => chart.mode === mode).map((chart) => chart.level))]
    .sort((a, b) => a - b);
}

const titleOrder = new Intl.Collator("en", { sensitivity: "base", numeric: true });

function recordedScore(scores: Record<string, number> | undefined, chartId: string): number | null {
  const score = scores?.[chartId];
  return typeof score === "number" && Number.isFinite(score) ? score : null;
}

export function comparisonRows(
  charts: OfficialComparisonChart[],
  mode: ComparisonMode,
  level: number,
  leftScores?: Record<string, number>,
  rightScores?: Record<string, number>,
  query = "",
): ComparisonRow[] {
  const search = query.trim().toLocaleLowerCase();
  return charts
    .filter((chart) => chart.mode === mode && chart.level === level
      && (!search || `${chart.songName} ${chart.chartLabel}`.toLocaleLowerCase().includes(search)))
    .slice()
    .sort((a, b) => titleOrder.compare(a.songName, b.songName)
      || titleOrder.compare(a.chartLabel, b.chartLabel)
      || (a.chartId < b.chartId ? -1 : a.chartId > b.chartId ? 1 : 0))
    .map((chart) => {
      const leftScore = recordedScore(leftScores, chart.chartId);
      const rightScore = recordedScore(rightScores, chart.chartId);
      const delta = leftScore !== null && rightScore !== null ? leftScore - rightScore : null;
      return {
        chart,
        leftScore,
        rightScore,
        delta,
        winner: delta === null ? null : delta === 0 ? "tie" : delta > 0 ? "left" : "right",
      };
    });
}

export function comparisonSummary(rows: ComparisonRow[]) {
  return rows.reduce((summary, row) => ({
    charts: summary.charts + 1,
    leftRecorded: summary.leftRecorded + Number(row.leftScore !== null),
    rightRecorded: summary.rightRecorded + Number(row.rightScore !== null),
    leftWins: summary.leftWins + Number(row.winner === "left"),
    rightWins: summary.rightWins + Number(row.winner === "right"),
    ties: summary.ties + Number(row.winner === "tie"),
    comparable: summary.comparable + Number(row.delta !== null),
  }), { charts: 0, leftRecorded: 0, rightRecorded: 0, leftWins: 0, rightWins: 0, ties: 0, comparable: 0 });
}

export function formatComparisonScore(score: number | null): string {
  return score === null ? "--" : score.toLocaleString("en-US");
}

export function formatComparisonDelta(delta: number | null): string {
  if (delta === null) return "--";
  return delta > 0 ? `+${delta.toLocaleString("en-US")}` : delta.toLocaleString("en-US");
}

/** Compare the aligned, unrounded PG estimates, before the radar's visual clipping. */
export function compareEstimatedBuckets(
  left: Pick<OfficialComparisonBucket, "pg"> | undefined,
  right: Pick<OfficialComparisonBucket, "pg"> | undefined,
): { winner: "left" | "right" | "tie" | null; gap: number | null } {
  if (left?.pg === null || left?.pg === undefined || right?.pg === null || right?.pg === undefined) {
    return { winner: null, gap: null };
  }
  const difference = left.pg - right.pg;
  return { winner: difference === 0 ? "tie" : difference > 0 ? "left" : "right", gap: Math.abs(difference) };
}

export function formatEstimatedPgGap(gap: number): string {
  return gap > 0 && gap < 0.01 ? "<0.01 PG" : `${gap.toFixed(2)} PG`;
}

/** Each half of the comparison track represents exactly 1 PG. */
export function scaleEstimatedPgComparison(
  left: Pick<OfficialComparisonBucket, "pg"> | undefined,
  right: Pick<OfficialComparisonBucket, "pg"> | undefined,
) {
  const comparison = compareEstimatedBuckets(left, right);
  return {
    ...comparison,
    fraction: comparison.gap === null ? null : Math.min(comparison.gap, 1),
    capped: comparison.gap !== null && comparison.gap > 1,
  };
}
