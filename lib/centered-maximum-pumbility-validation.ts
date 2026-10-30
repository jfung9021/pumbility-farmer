import { CENTERED_MAXIMUM_CALCULATION, HALF_DOWNWARD_PUMBILITY_CALCULATION, resolvePumbilityPolicy } from "./pumbility-policy.ts";
import type { AnalysisPayload } from "./types";

type Obj = Record<string, any>;
const NS = "centeredMaximumPumbility";
const INTERVAL = "conditional-scoring-fixed-clearing-and-folder-centering-v1";

function need(value: unknown, message: string): asserts value {
  if (!value) throw new Error(message);
}
function same(a: any, b: any): boolean {
  if (typeof b === "number") return typeof a === "number" && Number.isFinite(a) && Math.abs(a - b) < 1e-8;
  if (a === b) return true;
  return !!a && !!b && typeof a === "object" && typeof b === "object" && Array.isArray(a) === Array.isArray(b)
    && Object.keys(a).length === Object.keys(b).length && Object.keys(b).every(key => key in a && same(a[key], b[key]));
}
function maximum(s: number | null, c: number | null): number | null {
  need((s === null || Number.isFinite(s)) && (c === null || Number.isFinite(c)), "Centered maximum component is not finite.");
  return s === null || c === null ? null : Math.max(s, c);
}
function median(values: number[]): number | null {
  const sorted = values.toSorted((a, b) => a - b), middle = Math.floor(sorted.length / 2);
  return sorted.length === 0 ? null : sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}
function textCompare(a: string, b: string): number {
  const x = Array.from(a, c => c.codePointAt(0)!), y = Array.from(b, c => c.codePointAt(0)!);
  for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i] - y[i];
  return x.length - y.length;
}

/** Independently check a complete aggregate without requiring private rollback copies. */
export function validateCenteredMaximumPayload(payload: AnalysisPayload): AnalysisPayload {
  const p = payload as unknown as Obj, policy = p.summary?.method?.[NS];
  need(resolvePumbilityPolicy(payload)?.aggregation === "centered-maximum", "Centered maximum policy is unsupported.");
  const half = policy.version === 2;
  need(same(policy.scope, ["Single", "Double"]) && policy.requiresBoth === true
    && same(policy.goals, p.schemaVersion === 162 ? ["general", "sssplus"] : ["general", "sss", "sssplus"])
    && policy.population === "all-finite-component-pairs-including-provisional"
    && policy.reference === "current-official-level-plus-0.5" && policy.filters === "frozen-full-official-folder"
    && policy.intervalMethod === INTERVAL
    && policy.calculation === (half ? HALF_DOWNWARD_PUMBILITY_CALCULATION : CENTERED_MAXIMUM_CALCULATION),
  "Centered maximum population or calculation differs.");
  const folders: Obj = {};
  for (const [key, mode] of [["singles", "Single"], ["doubles", "Double"]]) {
    folders[mode] = {};
    for (const level of new Set<number>(p[key].map((row: Obj) => row.level))) {
      const rows: Obj[] = p[key].filter((row: Obj) => row.level === level);
      folders[mode][String(level)] = {};
      for (const goal of policy.goals) {
        const raw = rows.map(row => maximum(row.scoringVariants[goal].estimatedDifficulty, row.tierMetrics.clearing.estimatedDifficulty));
        const finite = raw.filter((value): value is number => value !== null), center = median(finite);
        const fullOffset = center === null ? 0 : level + .5 - center;
        const offset = half && fullOffset < 0 ? fullOffset * .5 : fullOffset;
        folders[mode][String(level)][goal] = {ratedCharts: finite.length, medianBefore: center,
          ...(half ? {fullOffset} : {}), offset};
        const ranked = rows.filter((_, i) => raw[i] !== null).toSorted((a, b) =>
          a.pumbilityVariants[goal].estimatedDifficulty - b.pumbilityVariants[goal].estimatedDifficulty
          || textCompare(a.songName || "", b.songName || "") || textCompare(String(a.chartId), String(b.chartId)));
        const ranks = new Map(ranked.map((row, i) => [row.chartId, i + 1]));
        for (let i = 0; i < rows.length; i++) {
          const row = rows[i], metric = row.pumbilityVariants[goal], scoring = row.scoringVariants[goal];
          const value = raw[i] === null ? null : raw[i]! + offset, delta = value === null ? null : value - level - .5;
          const band = delta === null ? null : delta < -.5 ? 1 : delta < -.3 ? 2 : delta < -.1 ? 3 : delta <= .1 ? 4 : delta <= .3 ? 5 : delta <= .5 ? 6 : 7;
          need(same(metric.estimatedDifficulty, value) && same(metric.difficultyDelta, delta)
            && metric.effectBandRank === band && metric.effectBand === (band === null ? null : ["Overrated", "Very Easy", "Easy", "Medium", "Hard", "Very Hard", "Underrated"][band - 1]),
          "Centered maximum point or band differs.");
          need(metric.aggregation === "centered-maximum" && metric.intervalMethod === INTERVAL
            && same(metric[NS], {version: policy.version, unshiftedDifficulty: raw[i], ...(half ? {fullOffset} : {}), offset})
            && metric.levelRank === (value === null ? null : ranks.get(row.chartId))
            && metric.levelComparisonCharts === (value === null ? null : ranked.length), "Centered maximum row offset or rank differs.");
          for (const side of ["Low", "High"]) {
            const endpoint = value === null ? null : maximum(scoring[`difficultyCi95${side}`] ?? null, row.tierMetrics.clearing.estimatedDifficulty);
            const expected = endpoint === null ? null : endpoint + offset;
            need(same(metric[`difficultyCi95${side}`], expected) && same(metric[`difficultyDeltaCi95${side}`], expected === null ? null : expected - level - .5),
              "Centered maximum conditional interval differs.");
          }
        }
      }
      for (const row of rows) need(same(row.tierMetrics.pumbility, row.pumbilityVariants.general), "Centered maximum General aliases differ.");
    }
  }
  need(same(policy.folders, folders), "Centered maximum folder population or offset differs.");
  const declared = p.summary.method.tierMetrics?.pumbility;
  if (declared) need(declared.version === (half ? 7 : 6) && declared.aggregation === "centered-maximum"
    && declared.calculation === policy.calculation, "Centered maximum declared calculation differs.");
  return payload;
}
