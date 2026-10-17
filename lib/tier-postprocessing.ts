import type { AnalysisPayload, ChartResult } from "./types";

export const POSTPROCESSED_SCHEMA = 44;
export const FIT_FIELDS = ["estimatedDifficulty", "difficultyDelta", "difficultyCi95Low", "difficultyCi95High",
  "difficultyDeltaCi95Low", "difficultyDeltaCi95High", "effectBand", "effectBandRank", "levelRank",
  "levelComparisonCharts", "modeRank", "levelPercentile", "relativeGroup", "relativeGroupRank"] as const;

export function compressionPolicy(baseAnalysisSchemaVersion: number) {
  return { version: 1, method: "official-midpoint-compression", factor: .5,
    reference: "current-official-level-plus-0.5", metrics: ["scoring", "clearing"], baseAnalysisSchemaVersion };
}

export function hasMidpointCompression(payload: AnalysisPayload | null | undefined): boolean {
  const p = payload?.summary.method.ratingPostprocessing as {baseAnalysisSchemaVersion?: number} | undefined;
  return payload?.schemaVersion === POSTPROCESSED_SCHEMA && !!p
    && [26, 41, 42, 43].includes(p.baseAnalysisSchemaVersion ?? 0)
    && samePolicy(p, compressionPolicy(p.baseAnalysisSchemaVersion!));
}

function samePolicy(a: object, b: object) {
  const entries = Object.entries(b);
  return Object.keys(a).length === entries.length && entries.every(([k, v]) =>
    JSON.stringify((a as Record<string, unknown>)[k]) === JSON.stringify(v));
}

export function compressDifficulty(value: number | null, level: number): number | null {
  if (value === null) return null;
  if (!Number.isFinite(value)) throw new Error("Invalid initial fitted difficulty.");
  return level + .5 + .5 * (value - level - .5);
}

function band(delta: number | null): [number | null, string | null] {
  if (delta === null) return [null, null];
  const rank = delta < -.5 ? 1 : delta < -.3 ? 2 : delta < -.1 ? 3 : delta <= .1 ? 4 : delta <= .3 ? 5 : delta <= .5 ? 6 : 7;
  return [rank, ["Overrated", "Very Easy", "Easy", "Medium", "Hard", "Very Hard", "Underrated"][rank - 1]];
}

function equal(a: unknown, b: unknown) {
  return typeof b === "number" ? typeof a === "number" && Number.isFinite(a) && Math.abs(a - b) < 1e-8 : a === b;
}

function metrics(row: ChartResult) {
  if (!row.tierMetrics) throw new Error("Missing processed tier metrics.");
  return [row, row.tierMetrics.clearing, row.tierMetrics.pumbility] as unknown as Array<Record<string, unknown>>;
}

/** Validate the final transformation, then expose the original contract to existing validators. */
export function recoverInitialFitPayload(payload: AnalysisPayload): AnalysisPayload {
  if (!hasMidpointCompression(payload)) throw new Error("Incompatible midpoint compression policy.");
  const raw = structuredClone(payload);
  raw.schemaVersion = (payload.summary.method.ratingPostprocessing as {baseAnalysisSchemaVersion:number}).baseAnalysisSchemaVersion;
  delete raw.summary.method.ratingPostprocessing;
  for (const mode of ["singles", "doubles"] as const) {
    for (let i = 0; i < payload[mode].length; i++) {
      const row = payload[mode][i], originals = metrics(raw[mode][i]);
      const actual = metrics(row);
      for (let j = 0; j < actual.length; j++) {
        const m = actual[j], fitted = m.initialFit as Record<string, unknown> | undefined;
        if (!fitted || typeof fitted !== "object" || !("estimatedDifficulty" in fitted)
          || Object.keys(fitted).some(k => !(FIT_FIELDS as readonly string[]).includes(k))) throw new Error("Missing initial fitted values.");
        const initial = fitted.estimatedDifficulty;
        if (!(initial === null || (typeof initial === "number" && Number.isFinite(initial)))) throw new Error("Invalid initial fitted value.");
        const value = compressDifficulty(initial, row.level), delta = value === null ? null : value - row.level - .5;
        const [rank, label] = band(delta);
        if (!equal(m.estimatedDifficulty, value) || !equal(m.difficultyDelta, delta)
          || m.effectBandRank !== rank || m.effectBand !== label) throw new Error("Invalid compressed tier value or band.");
        for (const side of ["Low", "High"]) {
          const key = `difficultyCi95${side}`;
          if (key in fitted) {
            const endpoint = fitted[key];
            if (!(endpoint === null || (typeof endpoint === "number" && Number.isFinite(endpoint)))) throw new Error("Invalid initial interval.");
            const transformed = compressDifficulty(endpoint, row.level);
            if (!equal(m[key], transformed)) throw new Error("Invalid compressed interval.");
            if (`difficultyDeltaCi95${side}` in fitted
              && !equal(m[`difficultyDeltaCi95${side}`], transformed === null ? null : transformed - row.level - .5)) throw new Error("Invalid compressed interval delta.");
          }
        }
        for (const key of FIT_FIELDS) delete originals[j][key];
        Object.assign(originals[j], fitted);
        delete originals[j].initialFit;
      }
      const [s, c, p] = actual;
      const expected = s.estimatedDifficulty === null || c.estimatedDifficulty === null ? null
        : Math.max(s.estimatedDifficulty as number, c.estimatedDifficulty as number);
      if (!equal(p.estimatedDifficulty, expected)) throw new Error("Invalid compressed Pumbility maximum.");
    }
    validateRanks(payload[mode]);
  }
  return raw;
}

function validateRanks(rows: ChartResult[]) {
  const measured = rows.filter(r => r.estimatedDifficulty !== null).sort((a,b) =>
    a.difficultyDelta! - b.difficultyDelta! || b.nContributors - a.nContributors || a.songName.localeCompare(b.songName, "en") || a.chartId.localeCompare(b.chartId, "en"));
  // Rank values must be a complete permutation; equal-valued ties retain producer ordering.
  const ranks = measured.map(r => r.modeRank).sort((a,b) => a! - b!);
  if (ranks.some((n,i) => n !== i + 1)) throw new Error("Invalid compressed mode ranks.");
  const byModeRank = measured.toSorted((a,b) => a.modeRank! - b.modeRank!);
  if (byModeRank.some((r,i) => i>0 && r.difficultyDelta! < byModeRank[i-1].difficultyDelta!)) throw new Error("Invalid compressed mode rank ordering.");
  for (const metric of [0,1,2]) {
    for (const level of new Set(rows.map(r => r.level))) {
      const group = rows.filter(r => r.level === level && metrics(r)[metric].estimatedDifficulty !== null);
      const ordered = group.toSorted((a,b) => Number(metrics(a)[metric].levelRank) - Number(metrics(b)[metric].levelRank));
      for (let i=0;i<ordered.length;i++) {
        const m = metrics(ordered[i])[metric];
        if (m.levelRank !== i+1 || m.levelComparisonCharts !== ordered.length
          || (i>0 && Number(m.estimatedDifficulty) < Number(metrics(ordered[i-1])[metric].estimatedDifficulty))) throw new Error("Invalid compressed folder ranks.");
      }
    }
  }
}

export function compressDemoPayload(payload: AnalysisPayload): AnalysisPayload {
  const result = structuredClone(payload);
  result.schemaVersion = POSTPROCESSED_SCHEMA;
  result.summary.method.ratingPostprocessing = compressionPolicy(26);
  for (const mode of ["singles", "doubles"] as const) for (const row of result[mode]) {
    for (const metric of metrics(row)) {
      const fitted = Object.fromEntries(FIT_FIELDS.filter(k => k in metric).map(k => [k, metric[k]]));
      metric.initialFit = fitted;
      const value = compressDifficulty(fitted.estimatedDifficulty as number | null, row.level);
      metric.estimatedDifficulty = value;
      metric.difficultyDelta = value === null ? null : value - row.level - .5;
      const [rank, label] = band(metric.difficultyDelta as number | null);
      metric.effectBandRank = rank; metric.effectBand = label;
      for (const side of ["Low", "High"]) if (`difficultyCi95${side}` in fitted) {
        const endpoint = compressDifficulty(fitted[`difficultyCi95${side}`] as number | null, row.level);
        metric[`difficultyCi95${side}`] = endpoint;
        if (`difficultyDeltaCi95${side}` in fitted) metric[`difficultyDeltaCi95${side}`] = endpoint === null ? null : endpoint - row.level - .5;
      }
    }
  }
  return result;
}
