import type { AnalysisPayload, ChartResult, PumbilityTierMetric } from "./types";

export const LOCAL_AVERAGE_PUMBILITY_SCHEMA = 45;
export const HYBRID_AVERAGE_PUMBILITY_SCHEMA = 48;
export const LOCAL_AVERAGE_PUMBILITY_CALCULATION = "(scoring difficulty + clearing difficulty) / 2 before rounding";
export const LOCAL_AVERAGE_PUMBILITY_POLICY = {
  version: 1, aggregation: "average", reference: "current-official-level-plus-0.5",
  baseAnalysisSchemaVersion: 44, componentValues: "after-midpoint-compression", requiresBoth: true,
};
export const HYBRID_AVERAGE_PUMBILITY_POLICY = {
  ...LOCAL_AVERAGE_PUMBILITY_POLICY, version: 2, baseAnalysisSchemaVersion: 47,
};

function derived(value: number | null, level: number) {
  const delta = value === null ? null : value - level - .5;
  const rank = delta === null ? null : delta < -.5 ? 1 : delta < -.3 ? 2 : delta < -.1 ? 3 : delta <= .1 ? 4 : delta <= .3 ? 5 : delta <= .5 ? 6 : 7;
  return { estimatedDifficulty: value, difficultyDelta: delta, effectBandRank: rank,
    effectBand: rank === null ? null : ["Overrated", "Very Easy", "Easy", "Medium", "Hard", "Very Hard", "Underrated"][rank - 1] };
}

export function averageDifficulties(scoring: number | null, clearing: number | null): number | null {
  if (scoring === null || clearing === null) return null;
  if (![scoring, clearing].every(Number.isFinite)) throw new Error("Invalid average inputs.");
  return (scoring + clearing) / 2;
}

function same(a: unknown, b: unknown): boolean {
  if (typeof b === "number") return typeof a === "number" && Number.isFinite(a) && Math.abs(a - b) < 1e-8;
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return a === b;
  const ak = Object.keys(a), bk = Object.keys(b);
  return ak.length === bk.length && bk.every(k => ak.includes(k)
    && same((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]));
}

// Python string ordering compares Unicode code points, including astral characters.
function compareCodePoints(left: string, right: string): number {
  const a = Array.from(left), b = Array.from(right);
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    const difference = a[i].codePointAt(0)! - b[i].codePointAt(0)!;
    if (difference) return difference;
  }
  return a.length - b.length;
}

/** Derive a separate local candidate; never mutate the maximum source artifact. */
export function applyAveragePumbility(payload: AnalysisPayload): AnalysisPayload {
  const base = [LOCAL_AVERAGE_PUMBILITY_SCHEMA, HYBRID_AVERAGE_PUMBILITY_SCHEMA].includes(payload.schemaVersion ?? 0)
    ? recoverMaximumPumbility(payload) : payload;
  if (base.schemaVersion !== 44 && base.schemaVersion !== 47) throw new Error("The local experiment requires a midpoint-compressed baseline.");
  const hybrid = base.schemaVersion === 47;
  const compareText = hybrid ? compareCodePoints : (a: string, b: string) => a.localeCompare(b, "en");
  const result = structuredClone(base);
  const tiers = result.summary.method.tierMetrics as { pumbility: Record<string, unknown> };
  if (tiers.pumbility.aggregation !== "maximum") throw new Error("The local experiment requires a maximum baseline.");
  result.schemaVersion = hybrid ? HYBRID_AVERAGE_PUMBILITY_SCHEMA : LOCAL_AVERAGE_PUMBILITY_SCHEMA;
  result.summary.method.pumbilityExperiment = { ...(hybrid ? HYBRID_AVERAGE_PUMBILITY_POLICY : LOCAL_AVERAGE_PUMBILITY_POLICY), baseMethod: structuredClone(tiers.pumbility) };
  tiers.pumbility = { version: hybrid ? 4 : 3, aggregation: "average", requiresBoth: true,
    calculation: LOCAL_AVERAGE_PUMBILITY_CALCULATION,
    evidence: "weaker component status; separate scoring and clearing support counts" };
  for (const mode of ["singles", "doubles"] as const) {
    for (const row of result[mode]) {
      const p = row.tierMetrics!.pumbility, c = row.tierMetrics!.clearing;
      p.maximumBaseline = structuredClone(p);
      p.aggregation = "average";
      Object.assign(p, derived(averageDifficulties(row.estimatedDifficulty, c.estimatedDifficulty), row.level));
      p.levelRank = null; p.levelComparisonCharts = null;
      if (p.initialFit) {
        Object.assign(p.initialFit, derived(averageDifficulties(
          row.initialFit!.estimatedDifficulty as number | null,
          c.initialFit!.estimatedDifficulty as number | null), row.level));
      }
    }
    for (const level of new Set(result[mode].map(r => r.level))) {
      const group = result[mode].filter(r => r.level === level && r.tierMetrics!.pumbility.estimatedDifficulty !== null);
      group.sort((a, b) => a.tierMetrics!.pumbility.estimatedDifficulty! - b.tierMetrics!.pumbility.estimatedDifficulty!
        || Number(a.tierMetrics!.pumbility.initialFit?.estimatedDifficulty) - Number(b.tierMetrics!.pumbility.initialFit?.estimatedDifficulty)
        || compareText(a.songName, b.songName) || compareText(a.chartId, b.chartId));
      group.forEach((r, i) => {
        const p = r.tierMetrics!.pumbility;
        p.levelRank = i + 1; p.levelComparisonCharts = group.length;
        if (p.initialFit) { p.initialFit.levelRank = i + 1; p.initialFit.levelComparisonCharts = group.length; }
      });
    }
  }
  return result;
}

/** Validate the derived candidate and expose its unchanged production contract. */
export function recoverMaximumPumbility(payload: AnalysisPayload): AnalysisPayload {
  const marker = payload.summary?.method.pumbilityExperiment as (typeof LOCAL_AVERAGE_PUMBILITY_POLICY & { baseMethod: Record<string, unknown> }) | undefined;
  const policy = payload.schemaVersion === HYBRID_AVERAGE_PUMBILITY_SCHEMA
    ? HYBRID_AVERAGE_PUMBILITY_POLICY : LOCAL_AVERAGE_PUMBILITY_POLICY;
  if (![LOCAL_AVERAGE_PUMBILITY_SCHEMA, HYBRID_AVERAGE_PUMBILITY_SCHEMA].includes(payload.schemaVersion ?? 0) || !marker
    || !same(Object.fromEntries(Object.entries(marker).filter(([k]) => k !== "baseMethod")), policy)
    || !marker.baseMethod) throw new Error("Invalid local Pumbility experiment policy.");
  const base = structuredClone(payload);
  base.schemaVersion = policy.baseAnalysisSchemaVersion;
  delete base.summary.method.pumbilityExperiment;
  (base.summary.method.tierMetrics as { pumbility: unknown }).pumbility = structuredClone(marker.baseMethod);
  for (const mode of ["singles", "doubles"] as const) for (const row of base[mode]) {
    const original = row.tierMetrics?.pumbility.maximumBaseline;
    if (!original || original.maximumBaseline) throw new Error("Missing local experiment maximum baseline.");
    row.tierMetrics!.pumbility = structuredClone(original);
  }
  const expected = applyAveragePumbility(base);
  if (!same(payload.summary.method, expected.summary.method)
    || ["singles", "doubles"].some(mode => (payload[mode as "singles"] as ChartResult[]).some((row, i) =>
      !same(row.tierMetrics?.pumbility, expected[mode as "singles"][i]?.tierMetrics?.pumbility)))) {
    throw new Error("Invalid average Pumbility values or ranks.");
  }
  return base;
}
