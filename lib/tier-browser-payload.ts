import type { AnalysisPayload } from "./types";
import fields from "../config/tier-public-fields.json" with { type: "json" };
import { validateSssTailPublicPayload } from "./sss-tail-validation.ts";
import { validateLevel20WidthPublicPayload } from "./level20-width-validation.ts";
import { SCORING_GOALS } from "./scoring-variants.ts";

const serialized = new WeakMap<AnalysisPayload, string>();

type Row = Record<string, unknown>;
function pick(row: Row, keys: readonly string[]): Row {
  return Object.fromEntries(keys.filter(key => key in row).map(key => [key, row[key]]));
}
function publicGoalVariants(row: Row): Row {
  let result = row;
  for (const key of ["scoringVariants", "pumbilityVariants"]) {
    const variants = row[key] as Row | undefined;
    if (!variants) continue;
    const retained = Object.entries(variants).filter(([goal]) => SCORING_GOALS.some(candidate => candidate === goal));
    if (retained.length !== Object.keys(variants).length) result = {...result, [key]:Object.fromEntries(retained)};
  }
  return result;
}

/** Retire public lists only after immutable historical validation has completed. */
function publicTierGoals(payload: AnalysisPayload): AnalysisPayload {
  let result = payload;
  for (const mode of ["singles", "doubles", "coop"] as const) if (payload[mode]) {
    const rows = payload[mode]!;
    const retained = rows.map(row => publicGoalVariants(row as unknown as Row));
    if (retained.some((row, index) => row !== rows[index] as unknown as Row)) {
      result = {...result, [mode]:retained};
    }
  }
  const method = payload.summary.method;
  const available = method.availableScoringGoals;
  const policies = method.scoringGoalPolicies;
  const retainedGoals = available?.filter(goal => SCORING_GOALS.some(candidate => candidate === goal));
  const retainedPolicies = policies?.goals.filter(policy => SCORING_GOALS.some(candidate => candidate === policy.key));
  if (retainedGoals?.length !== available?.length || retainedPolicies?.length !== policies?.goals.length) {
    result = {...result, summary:{...result.summary, method:{...method,
      ...(retainedGoals ? {availableScoringGoals:retainedGoals} : {}),
      ...(retainedPolicies ? {scoringGoalPolicies:{...policies!, goals:retainedPolicies}} : {}),
    }}};
  }
  return result;
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
  if (row.scoringLevel20Width) result.scoringLevel20Width = pick(row.scoringLevel20Width as Row,
    ["version", "method", "group", "source", "original", "input", "final"]);
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
  if (payload.tierListView?.kind === "list") return payload.schemaVersion === 60 ? validateLevel20WidthPublicPayload(payload)
    : payload.schemaVersion === 59 ? validateSssTailPublicPayload(payload) : payload;
  const source = payload as unknown as Row;
  const result = pick(source, ["schemaVersion", "generatedAtUtc", "mix", "relativeGroups", "effectBands", "bpmMetadata"]);
  const summary = payload.summary;
  const method = pick(summary.method, fields.method);
  for (const key of ["sssScoring", "sssRefinement"]) if (summary.method[key]) method[key] = pick(summary.method[key] as Row, ["version", "baseSchemaVersion"]);
  if (summary.method.submittedSssplusCalibration) method.submittedSssplusCalibration = pick(summary.method.submittedSssplusCalibration as Row, ["version", "localOnly"]);
  result.summary = {...pick(summary as unknown as Row, ["scriptVersion", "coverage", "modes"]), method};
  for (const mode of ["singles", "doubles", "coop"] as const) if (payload[mode]) result[mode] = payload[mode]!.map(row => chart(row as unknown as Row));
  result.tierListView = {version:1, kind:"list", detailsAvailable:true};
  const compact = result as unknown as AnalysisPayload;
  return compact.schemaVersion === 60 ? validateLevel20WidthPublicPayload(compact)
    : compact.schemaVersion === 59 ? validateSssTailPublicPayload(compact) : compact;
}

/** Chart evidence retains the existing contract; only private rollback copies go. */
export function tierChartDetails(payload: AnalysisPayload, chartId: string) {
  const row = [...payload.singles, ...payload.doubles, ...(payload.coop ?? [])].find(row => row.chartId === chartId);
  return row ? {generatedAtUtc:payload.generatedAtUtc, ...(payload.bpmMetadata ? {bpmMetadata:payload.bpmMetadata} : {}), chart:JSON.parse(JSON.stringify(publicGoalVariants(row as unknown as Row), (key, value) => key.endsWith("Baseline") ? undefined : value))} : null;
}

/** Response-only DTO; strict publication validation runs before this projection. */
export function tierBrowserPayload(payload: AnalysisPayload): AnalysisPayload {
  if ((payload.tierListView as unknown as Row | undefined)?.browserVersion === 1) return publicTierGoals(payload);
  const compact = compactTierPayload(payload);
  const source = compact as unknown as Row;
  const result = { ...source };
  const method = pick(compact.summary.method, fields.browserMethod);
  for (const [key, keys] of Object.entries(fields.browserMethodFields)) {
    if (method[key]) method[key] = pick(method[key] as Row, keys);
  }
  // JavaScript cannot reproduce Python's canonical numeric encoding after
  // JSON.parse; preserve the base signature without signing a derived view.
  const publication = method.productionTierPublication as Row | undefined;
  if (publication && "publicPayloadSha256" in publication) {
    method.baseProductionTierPublication = publication;
    delete method.productionTierPublication;
  }
  result.summary = { ...compact.summary, method };
  const browserMetric = (row: Row, kind: "chart" | "scoring" | "clearing" | "pumbility") =>
    pick(row, [...fields.browserMetric, ...fields[kind]]);
  for (const mode of ["singles", "doubles", "coop"] as const) if (compact[mode]) {
    result[mode] = compact[mode]!.map(chart => {
      const row = chart as unknown as Row;
      const projected = browserMetric(row, "chart");
      for (const [key, kind] of [["scoringVariants", "scoring"], ["pumbilityVariants", "pumbility"]] as const) {
        if (row[key]) projected[key] = Object.fromEntries(Object.entries(row[key] as Row).map(([goal, value]) =>
          [goal, goal === "general" ? pick(value as Row, ["estimatedDifficulty"]) : browserMetric(value as Row, kind)]));
      }
      if (row.tierMetrics) {
        const tiers = row.tierMetrics as Row;
        const metrics = Object.fromEntries((["clearing", "pumbility"] as const).filter(key => tiers[key])
          .map(key => [key, browserMetric(tiers[key] as Row, key)]));
        const evidence = (tiers.clearing as Row | undefined)?.clearingEvidence;
        if (evidence) metrics.clearing.clearingEvidence = pick(evidence as Row, ["capPolicy"]);
        projected.tierMetrics = metrics;
      }
      for (const key of ["officialEvidence", "officialScoringEvidence"]) if (row[key]) projected[key] = {available: true};
      return projected;
    });
  }
  result.tierListView = {...compact.tierListView, browserVersion: 1};
  return publicTierGoals(result as unknown as AnalysisPayload);
}

export function tierBrowserJson(payload: AnalysisPayload): string {
  const cached = serialized.get(payload);
  if (cached !== undefined) return cached;
  const json = JSON.stringify(tierBrowserPayload(payload));
  serialized.set(payload, json);
  return json;
}
