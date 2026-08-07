import { MIXES } from "./mixes.ts";
import type { AnalysisPayload, ChartRerate, ChartResult } from "./types.ts";


export interface Phoenix1ReratePayload {
  schemaVersion: number;
  source: {
    workbook: string;
    worksheet: string;
    sha256: string;
  };
  phoenix1ArchiveSha256: string;
  rerates: Array<ChartRerate & { chartId: string }>;
}

function annotateCharts(
  charts: ChartResult[],
  rerates: ReadonlyMap<string, ChartRerate>,
): ChartResult[] {
  return charts.map((chart) => {
    const rerate = rerates.get(chart.chartId);
    return rerate ? { ...chart, phoenix2Rerate: rerate } : chart;
  });
}

export function applyPhoenix1Rerates(
  payload: AnalysisPayload,
  source: Phoenix1ReratePayload,
): AnalysisPayload {
  const archive = MIXES.phoenix1.archive;
  if (
    payload.mix.key !== "phoenix1"
    || !archive
    || source.schemaVersion !== 1
    || source.phoenix1ArchiveSha256 !== archive.sha256
    || !Array.isArray(source.rerates)
  ) {
    throw new Error("The Phoenix 1 rerate annotations do not match the archived dataset.");
  }

  const charts = new Map(
    [...payload.singles, ...payload.doubles].map((chart) => [chart.chartId, chart]),
  );
  const rerates = new Map<string, ChartRerate>();
  for (const row of source.rerates) {
    const chart = charts.get(row.chartId);
    if (
      !chart
      || rerates.has(row.chartId)
      || row.from !== chart.difficulty
      || !Number.isInteger(row.delta)
      || row.delta === 0
      || (row.delta > 0 ? "uprated" : "downrated") !== row.direction
    ) {
      throw new Error("The Phoenix 1 rerate annotations contain an invalid chart mapping.");
    }
    rerates.set(row.chartId, {
      from: row.from,
      to: row.to,
      delta: row.delta,
      direction: row.direction,
      sourceRow: row.sourceRow,
    });
  }

  return {
    ...payload,
    singles: annotateCharts(payload.singles, rerates),
    doubles: annotateCharts(payload.doubles, rerates),
  };
}
