import type { AnalysisPayload } from "./types";
import fields from "../config/tier-public-fields.json" with { type: "json" };
import { validateSssTailPublicPayload } from "./sss-tail-validation.ts";

const serialized = new WeakMap<AnalysisPayload, string>();

type Row = Record<string, unknown>;
function pick(row: Row, keys: readonly string[]): Row {
  return Object.fromEntries(keys.filter(key => key in row).map(key => [key, row[key]]));
}
function metric(row: Row, kind: "chart" | "scoring" | "clearing" | "pumbility") {
  const result = pick(row, [...fields.metric, ...fields[kind]]);
  if (kind === "chart" || kind === "scoring") {
    delete result.scoringBoundaryRecords;
    if (row.scoringMethod === "sss-boundary-score-gap-v1" || row.scoringMethod === "target-boundary-score-gap-v1") {
      const refinement = row.scoringRefinement as { fullBoard?: { records?: unknown } } | undefined;
      const count = refinement?.fullBoard?.records;
      if (typeof count === "number" && Number.isSafeInteger(count) && count >= 0) result.scoringBoundaryRecords = count;
    }
  }
  if (row.scoringSpreadPolicy) result.scoringSpreadPolicy = pick(row.scoringSpreadPolicy as Row, ["midpoint"]);
  if (row.scoringTarget === 990000 && row.scoringTailPolicy) result.scoringTailPolicy = pick(row.scoringTailPolicy as Row,
    ["preTailDifficulty", "preTailDifficultyCi95Low", "preTailDifficultyCi95High"]);
  return result;
}
function chart(row: Row): Row {
  const result = metric(row, "chart");
  for (const [key, kind] of [["scoringVariants", "scoring"], ["pumbilityVariants", "pumbility"]] as const) {
    if (row[key]) result[key] = Object.fromEntries(Object.entries(row[key] as Row).map(([goal, value]) =>
      [goal, goal === "general" ? pick(value as Row, ["estimatedDifficulty"]) : metric(value as Row, kind)]));
  }
  if (row.tierMetrics) {
    const tiers = row.tierMetrics as Row;
    const projected = Object.fromEntries((["clearing", "pumbility"] as const).filter(key => tiers[key]).map(key => [key, metric(tiers[key] as Row, key)]));
    const evidence = (tiers.clearing as Row | undefined)?.clearingEvidence;
    if (evidence) projected.clearing.clearingEvidence = pick(evidence as Row, ["capPolicy"]);
    result.tierMetrics = projected;
  }
  for (const key of ["officialEvidence", "officialScoringEvidence"]) if (row[key]) result[key] = {available: true};
  return result;
}

/** Project only display/filter/order fields after the full local validator passes. */
export function compactTierPayload(payload: AnalysisPayload): AnalysisPayload {
  if (payload.tierListView?.kind === "list") return payload.schemaVersion === 59 ? validateSssTailPublicPayload(payload) : payload;
  const source = payload as unknown as Row;
  const result = pick(source, ["schemaVersion", "generatedAtUtc", "mix", "relativeGroups", "effectBands"]);
  const summary = payload.summary;
  const method = pick(summary.method, fields.method);
  for (const key of ["sssScoring", "sssRefinement"]) if (summary.method[key]) method[key] = pick(summary.method[key] as Row, ["version", "baseSchemaVersion"]);
  if (summary.method.submittedSssplusCalibration) method.submittedSssplusCalibration = pick(summary.method.submittedSssplusCalibration as Row, ["version", "localOnly"]);
  result.summary = {...pick(summary as unknown as Row, ["scriptVersion", "coverage", "modes"]), method};
  for (const mode of ["singles", "doubles", "coop"] as const) if (payload[mode]) result[mode] = payload[mode]!.map(row => chart(row as unknown as Row));
  result.tierListView = {version:1, kind:"list", detailsAvailable:true};
  const compact = result as unknown as AnalysisPayload;
  return compact.schemaVersion === 59 ? validateSssTailPublicPayload(compact) : compact;
}

/** Chart evidence retains the existing contract; only private rollback copies go. */
export function tierChartDetails(payload: AnalysisPayload, chartId: string) {
  const row = [...payload.singles, ...payload.doubles, ...(payload.coop ?? [])].find(row => row.chartId === chartId);
  return row ? {generatedAtUtc:payload.generatedAtUtc, chart:JSON.parse(JSON.stringify(row, (key, value) => key.endsWith("Baseline") ? undefined : value))} : null;
}

export function tierBrowserJson(payload: AnalysisPayload): string {
  const cached = serialized.get(payload);
  if (cached !== undefined) return cached;
  const json = JSON.stringify(compactTierPayload(payload));
  serialized.set(payload, json);
  return json;
}
