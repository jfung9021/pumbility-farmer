import { checkFit, validateRanks } from "./sss-analysis-validation.ts";
import { FINAL_WIDTH_POLICY, finalWidthParameters, finalWidthRating } from "./source-aware-validation.ts";
import { spreadRating } from "./sssplus-spread-validation.ts";
import { targetTailRating } from "./sssplus-analysis-validation.ts";
import { compressDifficulty } from "./tier-postprocessing.ts";
import type { AnalysisPayload } from "./types";

type Obj = Record<string, any>;
const NS = "submittedSssplusCalibration";
const METHOD = "submitted-folder-centered-shared-q90-v1";
const FIELDS = ["estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"];
const TAIL = { version: 1, method: "target-midpoint-soft-tail-global-exceptions-v1", identityRadius: 1.5,
  ordinaryAsymptote: 1.9, exceptionThreshold: 2, exceptionLimit: 10, exceptionCeiling: 2.5 };
const INTERVAL = "conditional-submitted-shared-scale-width-tail-fixed-clearing-v1";
const POLICY = { version: 1, method: METHOD, scope: { source: "submitted", mode: "Double", levels: [16, 17, 18, 19] }, scoreTarget: 995000,
  initialResidualMultiplier: .8, residualQuantile: .9, minimumFolderCharts: 5, minimumContributors: 5, minimumPerOutcome: 5,
  minimumPositiveScale: 1e-8, compressionFactor: .5, compressionApplications: 1, asinhScale: .55,
  widthPolicy: FINAL_WIDTH_POLICY, tailPolicy: TAIL, newTailExceptions: false, intervalMethod: INTERVAL,
  meaning: "declared-folder-relative-scale-not-independent-between-folder-identification" };
const STAGES = ["cached-submitted-player-chart-effects", "folder-median-and-shared-q90-residual-scale",
  "single-midpoint-compression", "midpoint-asinh-spread", "final-folder-width-floor",
  "single-ordinary-tail-policy", "component-means-and-ranks"];
const HASH = /^[a-f0-9]{64}$/;

function need(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
function obj(value: unknown): Obj {
  need(value && typeof value === "object" && !Array.isArray(value), "Missing submitted SSS+ calibration object.");
  return value as Obj;
}
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function count(value: unknown): value is number { return Number.isInteger(value) && (value as number) >= 0; }
function same(a: any, b: any): boolean {
  if (typeof b === "number") return finite(a) && Math.abs(a - b) < 1e-8;
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => same(v, b[i]));
  return !!a && !!b && typeof a === "object" && typeof b === "object"
    && Object.keys(a).length === Object.keys(b).length && Object.keys(b).every(k => k in a && same(a[k], b[k]));
}
function quantile(values: number[], q: number): number {
  need(values.length && values.every(finite), "Invalid submitted residual population.");
  const x = values.toSorted((a, b) => a - b), p = q * (x.length - 1), i = Math.floor(p);
  return x[i] + (p - i) * (x[Math.ceil(p)] - x[i]);
}
function without(object: Obj, keys: string[]): Obj { return Object.fromEntries(Object.entries(object).filter(([key]) => !keys.includes(key))); }
function noModeRank(metric: Obj): Obj {
  const copy = without(metric, ["modeRank"]);
  if (copy.initialFit) copy.initialFit = without(copy.initialFit, ["modeRank"]);
  return copy;
}
function eligible(row: Obj, input: Obj, converged: boolean): boolean {
  const metric = obj(row.scoringVariants?.sssplus);
  return row.type === "Double" && row.level >= 16 && row.level <= 19 && converged && input.ratingEligible === true
    && input.calibratedComponent === true && metric.scoringFitConverged === true && finite(input.effect) && input.usableContributors >= 5
    && input.targetSuccesses >= 5 && input.belowTarget >= 5
    && metric.scoringSource === "submitted" && metric.scoringTarget === 995000
    && (finite(metric.estimatedDifficulty) || /calibration/i.test(metric.scoringUnratedReason ?? ""));
}

/** A folder's declared midpoint is a scale policy; adjacent fitted effect medians need not increase. */
export function submittedSssplusInitialRating(effect: number | null, level: number, center: number, sharedScale: number): number | null {
  if (effect === null) return null;
  need(finite(effect) && finite(center) && finite(sharedScale) && sharedScale > 0, "Invalid submitted shared scale or effect.");
  return level + .5 + .8 * (effect - center) / sharedScale;
}

export function recoverSubmittedSssplusCalibrationBaseline(payload: AnalysisPayload): AnalysisPayload {
  need(payload.schemaVersion === 57, "Submitted SSS+ recovery requires schema 57.");
  const base = structuredClone(payload) as unknown as Obj;
  base.schemaVersion = 56;
  delete base.summary.method[NS];
  for (const row of base.doubles) {
    const saved = row.submittedSssplusCalibrationBaseline;
    if (!saved) continue;
    need(same(Object.keys(saved).sort(), ["pumbility", "scoring"]), "Unexpected submitted recovery scope.");
    for (const [name, key] of [["scoring", "scoringVariants"], ["pumbility", "pumbilityVariants"]]) {
      const patch = obj(saved[name]), metric = obj(row[key]?.sssplus), changed = obj(patch.changed);
      need(Array.isArray(patch.added) && patch.added.every((k: unknown) => typeof k === "string")
        && new Set(patch.added).size === patch.added.length && patch.added.every((k: string) => !(k in changed)), "Invalid submitted metric recovery patch.");
      for (const key of patch.added) delete metric[key];
      Object.assign(metric, changed);
    }
    delete row.submittedSssplusCalibrationBaseline;
  }
  return base as AnalysisPayload;
}

/** Replay every numeric stage and delegate the exact schema-56 predecessor to its existing validator. */
export function validateSubmittedSssplusCalibrationPayload(payload: AnalysisPayload, validateBase: (p: AnalysisPayload) => unknown): AnalysisPayload {
  const base = recoverSubmittedSssplusCalibrationBaseline(payload);
  validateBase(base);
  const p = payload as unknown as Obj, b = base as unknown as Obj, m = obj(p.summary.method[NS]);
  need(m.version === 1 && m.method === METHOD && typeof m.localOnly === "boolean" && m.baseSchemaVersion === 56
    && HASH.test(m.basePayloadSha256) && HASH.test(m.fitSha256) && HASH.test(m.chartInputsSha256)
    && m.modelRefits === 0 && m.dataFetches === 0 && same(m.policy, POLICY) && same(m.stageOrder, STAGES), "Submitted SSS+ policy differs.");
  const source = obj(p.summary.method.sourceAwareTiers?.sourcePolicy), identity = obj(m.fitIdentity);
  const pinned = source.targetFits.filter((f: Obj) => f.goal === "sssplus" && f.source === "submitted" && f.mode === "Double");
  need(pinned.length === 1 && same(identity, pinned[0].identity) && identity.target === 995000 && identity.samples === 200
    && m.sourcePolicySemanticSha256 === source.effectivePolicySha256, "Submitted fit or source-policy identity differs.");
  need(same(p.singles, b.singles) && same(p.coop, b.coop), "Submitted calibration changed Singles or Co-op.");
  need(same(without(p, ["schemaVersion", "summary", "singles", "doubles", "coop"]), without(b, ["schemaVersion", "summary", "singles", "doubles", "coop"]))
    && same(without(p.summary, ["method"]), without(b.summary, ["method"]))
    && same(without(p.summary.method, [NS]), b.summary.method), "Submitted calibration changed protected generation fields.");
  const inputs = obj(m.chartInputs), diagnostics = obj(m.fitDiagnostics), folders = obj(m.folders), fitPolicy = obj(m.fitPolicy);
  need(fitPolicy.scoreTarget === 995000 && fitPolicy.bootstrapSamples === 200
    && fitPolicy.modelVersion === "target-rasch-observed-best-v1"
    && fitPolicy.weighting === "each-player-total-one-inverse-observed-chart-count-v1"
    && finite(fitPolicy.selectedRegularization) && fitPolicy.selectedRegularization > 0 && diagnostics.converged === true,
  "Submitted cached model policy differs.");
  const lower = b.doubles.filter((r: Obj) => r.level >= 16 && r.level <= 19);
  need(same(Object.keys(inputs).sort(), lower.map((r: Obj) => r.chartId).sort()), "Submitted chart input population differs.");
  for (const row of lower) {
    const input = obj(inputs[row.chartId]), metric = obj(row.scoringVariants.sssplus);
    need((input.effect === null || finite(input.effect)) && same(input.effect, metric.scoringChartEffect)
      && input.usableContributors === metric.nContributors && count(input.usableContributors)
      && input.targetSuccesses === metric.scoringTargetSuccesses && count(input.targetSuccesses)
      && input.belowTarget === metric.scoringBelowTarget && count(input.belowTarget)
      && input.targetSuccesses + input.belowTarget === input.usableContributors
      && input.calibratedComponent === metric.scoringMainComponent
      && typeof input.ratingEligible === "boolean" && Array.isArray(input.provisionalReasons)
      && count(input.bootstrapUsableSamples), "Submitted cached effects or support differ from their predecessor.");
    need((input.effectLow === null && input.effectHigh === null)
      || (finite(input.effectLow) && finite(input.effectHigh) && input.effectLow <= input.effectHigh), "Submitted effect interval is invalid.");
  }
  const groups = new Map<number, Obj[]>();
  for (const row of lower) if (eligible(row, inputs[row.chartId], diagnostics.converged === true)) {
    const group = groups.get(row.level) ?? []; group.push(row); groups.set(row.level, group);
  }
  for (const [level, rows] of groups) if (rows.length < 5) groups.delete(level);
  need(groups.size > 0 && same(Object.keys(folders).sort(), [...groups.keys()].map(String).sort()), "Submitted supported folder population differs.");
  const expectedFolders = new Map<number, { center: number; residualQ90: number; rows: Obj[] }>();
  for (const [level, rows] of groups) {
    const center = quantile(rows.map(r => inputs[r.chartId].effect), .5);
    const residualQ90 = quantile(rows.map(r => Math.abs(inputs[r.chartId].effect - center)), .9);
    expectedFolders.set(level, { center, residualQ90, rows });
    const folder = obj(folders[String(level)]);
    need(folder.level === level && same(folder.centerEffect, center) && same(folder.residualQ90, residualQ90)
      && folder.ratedCharts === rows.length && same(folder.populationChartIds, rows.map(r => r.chartId).sort()), "Submitted folder center or residual scale differs.");
  }
  const scale = quantile([...expectedFolders.values()].map(f => f.residualQ90), .5);
  need(scale > 1e-8 && same(m.sharedScale, scale), "Submitted shared residual scale differs.");
  const changed = [...groups.values()].flat().map(r => r.chartId).sort();
  need(same(m.modifiedChartIds, changed), "Submitted modified chart population differs.");
  const changedSet = new Set(changed), current = new Map<string, Obj>(p.doubles.map((r: Obj) => [r.chartId, r]));
  need(current.size === b.doubles.length && same(p.doubles.map((r: Obj) => r.chartId), b.doubles.map((r: Obj) => r.chartId)), "Submitted calibration changed the catalog.");
  for (const old of b.doubles) {
    const row = current.get(old.chartId)!, s = obj(row.scoringVariants.sssplus), oldS = obj(old.scoringVariants.sssplus);
    need(same(without(row, ["scoringVariants", "pumbilityVariants", "submittedSssplusCalibrationBaseline"]), without(old, ["scoringVariants", "pumbilityVariants"]))
      && same(without(row.scoringVariants, ["sssplus"]), without(old.scoringVariants, ["sssplus"]))
      && same(without(row.pumbilityVariants, ["sssplus"]), without(old.pumbilityVariants, ["sssplus"])), "Submitted calibration changed protected chart components.");
    if (!changedSet.has(row.chartId)) {
      need(same(noModeRank(s), noModeRank(oldS)) && same(row.pumbilityVariants.sssplus, old.pumbilityVariants.sssplus), "Submitted calibration changed an out-of-scope target estimate.");
      continue;
    }
    const input = inputs[row.chartId], folder = expectedFolders.get(row.level)!, detail = obj(s.scoringSubmittedCalibration);
    need(same(detail, { version: 1, method: METHOD, folder: String(row.level), effect: input.effect,
      effectCi95Low: input.effectLow, effectCi95High: input.effectHigh, initialCompressionApplications: 1 })
      && same(s.scoringChartEffect, input.effect) && same(s.scoringChartEffectCi95Low, input.effectLow)
      && same(s.scoringChartEffectCi95High, input.effectHigh), "Submitted effect provenance differs.");
    need(s.evidenceStatus === "Provisional" && s.scoringProvisional === true && s.scoringUnratedReason === null && s.scoringExtrapolated === false
      && s.intervalMethod === INTERVAL && s.intervalUnavailableReason === undefined
      && s.scoringTarget === 995000 && s.scoringSource === "submitted" && s.nContributors === input.usableContributors
      && s.scoringTargetSuccesses === input.targetSuccesses && s.scoringBelowTarget === input.belowTarget
      && same(s.scoringProvisionalReasons, [...new Set([...(oldS.scoringProvisionalReasons ?? []), ...input.provisionalReasons, "submitted-folder-relative-calibration"])].sort()), "Submitted evidence was promoted or support changed.");
    const points = folder.rows.map(r => ({ id: r.chartId, value: spreadRating(compressDifficulty(submittedSssplusInitialRating(inputs[r.chartId].effect, row.level, folder.center, scale), row.level), row.level) }));
    const width = finalWidthParameters(points, row.level), w = obj(s.finalFolderWidth);
    need(same(folders[String(row.level)].widthParameters, width) && same(w.parameters, width)
      && w.group === `Double:submitted-sssplus:${row.level}` && w.exceptionPreserved === false && w.initialCompressionApplications === 1,
    "Submitted final width population or policy differs.");
    for (const [field, effect] of FIELDS.map((field, i) => [field, input[["effect", "effectLow", "effectHigh"][i]]] as const)) {
      const initial = submittedSssplusInitialRating(effect, row.level, folder.center, scale);
      const compressed = compressDifficulty(initial, row.level), spread = spreadRating(compressed, row.level);
      const preTail = finalWidthRating(spread, width), final = targetTailRating(preTail, row.level);
      need(same(s.initialFit?.[field], initial) && same(w.input[field], spread) && same(s[field], final), "Submitted point or interval stage differs.");
      const spreadKey = field === "estimatedDifficulty" ? "Difficulty" : field === "difficultyCi95Low" ? "DifficultyCi95Low" : "DifficultyCi95High";
      need(same(s.scoringSpreadPolicy?.[`preSpread${spreadKey}`], compressed) && same(s.scoringSpreadPolicy?.[`spread${spreadKey}`], spread)
        && same(s.scoringTailPolicy?.[`preTail${spreadKey}`], preTail), "Submitted compression, spread or tail provenance differs.");
    }
    need(s.scoringSpreadPolicy?.scale === .55 && s.scoringSpreadPolicy?.midpoint === row.level + .5
      && s.scoringTailPolicy?.exceptionSelected === false, "Submitted tail exception, spread scale or midpoint differs.");
    checkFit(s, row.level); checkFit(s.initialFit, row.level);
    const c = row.tierMetrics.clearing, composite = obj(row.pumbilityVariants.sssplus);
    const mean = (score: number | null, clear: number | null) => score === null || clear === null ? null : (score + clear) / 2;
    need(same(composite.estimatedDifficulty, mean(s.estimatedDifficulty, c.estimatedDifficulty))
      && same(composite.initialFit.estimatedDifficulty, mean(s.initialFit.estimatedDifficulty, c.initialFit.estimatedDifficulty))
      && composite.aggregation === "average" && composite.scoringSupportCount === s.nContributors
      && composite.clearingSupportCount === c.ratedClearCount, "Submitted Pumbility is not the exact component mean.");
    const statuses = ["Unrated", "Insufficient", "Provisional", "Published"];
    need(composite.evidenceStatus === (composite.estimatedDifficulty === null ? "Unrated" : statuses[Math.min(statuses.indexOf(s.evidenceStatus), statuses.indexOf(c.evidenceStatus))]), "Submitted Pumbility evidence differs.");
    for (const side of ["Low", "High"]) {
      need(same(composite[`difficultyCi95${side}`], mean(s[`difficultyCi95${side}`], c.estimatedDifficulty))
        && same(composite.initialFit[`difficultyCi95${side}`], mean(s.initialFit[`difficultyCi95${side}`], c.initialFit.estimatedDifficulty)), "Submitted Pumbility conditional interval differs.");
    }
    checkFit(composite, row.level); checkFit(composite.initialFit, row.level);
  }
  for (const [level, folder] of expectedFolders) {
    const ordered = folder.rows.toSorted((a, b) => inputs[a.chartId].effect - inputs[b.chartId].effect);
    const values = ordered.map(r => current.get(r.chartId)!.scoringVariants.sssplus.estimatedDifficulty);
    need(values.every((value, i) => !i || value >= values[i - 1]) && same(quantile(values, .5), level + .5), "Submitted chart order or supported median differs.");
    ordered.forEach((row, i) => { if (i && inputs[row.chartId].effect === inputs[ordered[i - 1].chartId].effect) need(values[i] === values[i - 1], "Submitted tied chart effects were separated."); });
  }
  validateRanks(p.doubles.map((row: Obj) => ({ ...row, scoringVariants: { sss: row.scoringVariants.sssplus }, pumbilityVariants: { sss: row.pumbilityVariants.sssplus } })));
  return payload;
}
