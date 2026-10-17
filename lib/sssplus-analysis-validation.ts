import { checkFit, validateRanks, validateSssAnalysisPayload } from "./sss-analysis-validation.ts";
import { validateRefinedAnalysisPayload } from "./refined-analysis-validation.ts";
import { validateCenteredAnalysisPayload } from "./sss-centering-validation.ts";
import { truncateEstimatedDifficulty } from "./format-difficulty.ts";
import type { AnalysisPayload, ChartResult, ScoringVariant, PumbilityTierMetric } from "./types";

type Obj = Record<string, unknown>;
const TARGET = 995000;
const METHOD = "target-midpoint-soft-tail-global-exceptions-v1";
const PREFIX = "sssplus";
const RANKS = ["levelRank", "levelComparisonCharts", "modeRank", "levelPercentile", "relativeGroup", "relativeGroupRank"];
const TARGET_METHODS = new Set(["target-attainment-rasch-v1", "target-boundary-score-gap-v1", "target-rare-success-upper-band-v1"]);
const TARGET_CONFIG = { key: "sssplus", grade: "SSS+", score: TARGET, inclusive: true, policyVersion: "sssplus-target-policy-v1",
  modelMethod: "target-attainment-rasch-v1", boundaryMethod: "target-boundary-score-gap-v1", rareMethod: "target-rare-success-upper-band-v1",
  nearTargetScoreRange: [985000, 999999] };
const TAIL_PARAMETERS = { version: 1, method: METHOD, identityRadius: 1.5, ordinaryAsymptote: 1.9, exceptionThreshold: 2, exceptionLimit: 10, exceptionCeiling: 2.5 };
const TAIL_INTERVAL = "conditional-target-tail-fixed-selection-and-calibration-v1";
function need(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
function obj(value: unknown): Obj { need(value && typeof value === "object" && !Array.isArray(value), "Missing SSS+ contract object."); return value as Obj; }
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function same(a: unknown, b: unknown): boolean {
  if (typeof b === "number") return finite(a) && Math.abs(a - b) < 1e-8;
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => same(v, b[i]));
  if (a && b && typeof a === "object" && typeof b === "object") {
    const aa = obj(a), bb = obj(b);
    return Object.keys(aa).length === Object.keys(bb).length && Object.keys(bb).every(k => k in aa && same(aa[k], bb[k]));
  }
  return false;
}
function median(values: number[]): number { const v = values.toSorted((a, b) => a - b), i = Math.floor(v.length / 2); return v.length % 2 ? v[i] : (v[i - 1] + v[i]) / 2; }

/** Versioned nonlinear stage; initialFit deliberately remains in model coordinates. */
export function targetTailRating(value: number | null, level: number, exceptional = false): number | null {
  if (value === null) return null;
  need(finite(value), "Nonfinite pre-tail target rating.");
  const m = level + .5, d = value - m, a = Math.abs(d);
  if (exceptional) return m + Math.max(-2.5, Math.min(2.5, d));
  if (a <= 1.5) return value;
  const excess = a - 1.5;
  return m + Math.sign(d) * (1.5 + .4 * excess / (.4 + excess));
}

/** Remove only the new overlay, retaining the immutable historical SSS generation. */
export function recoverSssPlusBaseline(payload: AnalysisPayload): AnalysisPayload {
  need(payload.schemaVersion === 52, "SSS+ recovery requires schema 52.");
  const base = structuredClone(payload); base.schemaVersion = 51;
  for (const key of Object.keys(base.summary.method)) if (key.startsWith(PREFIX)) delete base.summary.method[key];
  for (const key of ["singles", "doubles"] as const) for (const row of base[key]) {
    delete row.scoringVariants?.sssplus; delete row.pumbilityVariants?.sssplus;
    delete row.sssplusCenteringBaseline; delete row.sssplusTailBaseline; delete row.sssplusRefinementBaseline;
  }
  return base;
}

function withoutRanks(metric: Obj): Obj {
  const result = structuredClone(metric);
  for (const field of RANKS) { delete result[field]; if (result.initialFit) delete obj(result.initialFit)[field]; }
  return result;
}

const HISTORICAL_NAMES: Record<string, string> = {
  "target-attainment-rasch-v1": "sss-target-rasch-v1",
  "target-boundary-score-gap-v1": "sss-boundary-score-gap-v1",
  "target-rare-success-upper-band-v1": "sss-rare-success-upper-band-v1",
  "frozen-range-target-second-pass-v1": "frozen-range-sss-second-pass-v1",
  "target-final-folder-median-centering-v1": "sss-final-folder-median-centering-v1",
  "no-target": "no-sss", "all-target": "all-sss", "rare-target": "rare-sss",
};
function historicalNames(value: unknown): unknown {
  if (typeof value === "string") return HISTORICAL_NAMES[value] ?? value.replaceAll("SSS+", "SSS").replaceAll("sssplus", "sss");
  if (Array.isArray(value)) return value.map(historicalNames);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, historicalNames(v)]));
  return value;
}

/** Replay the same fixed-coordinate stages at 995000; historical stored values stay untouched. */
function centeredTargetView(payload: AnalysisPayload): AnalysisPayload {
  const view = recoverSssPlusBaseline(payload), methods = payload.summary.method;
  const normalized = (name: string) => obj(historicalNames(structuredClone(methods[name])));
  const model = normalized("sssplusScoring"), refinement = normalized("sssplusRefinement"), centering = normalized("sssplusFolderCentering");
  model.baseSchemaVersion = 48; delete model.targetConfig;
  refinement.noSssEndpointPolicy = refinement.noTargetEndpointPolicy; delete refinement.noTargetEndpointPolicy;
  refinement.firstPassSchemaVersion = 49;
  centering.baseSchemaVersion = 50; delete centering.targetKey;
  centering.population = "all-finite-final-sss-excluding-unrated";
  centering.pumbility = "mean-centered-sss-and-unchanged-clearing-no-composite-centering";
  view.summary.method.sssScoring = model;
  const pumbility = normalized("sssplusPumbility");
  pumbility.calculation = "(SSS scoring difficulty + unchanged clearing difficulty) / 2 before rounding";
  pumbility.componentValues = "after-midpoint-compression";
  view.summary.method.sssPumbility = pumbility;
  view.summary.method.sssRefinement = refinement;
  view.summary.method.sssFolderCentering = centering;
  view.summary.method.refinedTiers = { version: 1, method: "frozen-sss-second-pass-official-d26-clearing-v1", baseSchemaVersion: 49,
    basePayloadSha256: refinement.firstPassPayloadSha256, localOnly: true, pumbilityAggregation: "average", compressionApplications: 1,
    clearingDispatch: { mode: "Double", minimumLevel: 26 } };
  for (const key of ["singles", "doubles"] as const) for (let i = 0; i < view[key].length; i++) {
    const row = view[key][i], source = payload[key][i];
    need(source.sssplusTailBaseline && source.sssplusCenteringBaseline && source.sssplusRefinementBaseline, "Missing SSS+ stage baseline.");
    row.scoringVariants!.sss = historicalNames(structuredClone(source.sssplusTailBaseline.scoring)) as ScoringVariant;
    row.pumbilityVariants!.sss = historicalNames(structuredClone(source.sssplusTailBaseline.pumbility)) as PumbilityTierMetric;
    row.sssCenteringBaseline = historicalNames(structuredClone(source.sssplusCenteringBaseline)) as typeof row.sssCenteringBaseline;
    (row as unknown as Obj).refinementBaseline = historicalNames(structuredClone(source.sssplusRefinementBaseline));
  }
  return view;
}

function preTailMetric(row: ChartResult): ScoringVariant {
  need(row.sssplusTailBaseline, "Missing centered SSS+ pre-tail snapshot.");
  return row.sssplusTailBaseline.scoring;
}
function rowGroups(payload: AnalysisPayload): ChartResult[][] {
  const result = new Map<string, ChartResult[]>();
  for (const key of ["singles", "doubles"] as const) for (const row of payload[key]) if (finite(preTailMetric(row).estimatedDifficulty)) {
    const id = `${row.level >= 20 ? "official" : "submitted"}:${key}:${row.level}`;
    if (!result.has(id)) result.set(id, []);
    result.get(id)!.push(row);
  }
  return [...result.values()];
}
function evidence(row: ChartResult, protectedMedian: boolean): Obj {
  const s = preTailMetric(row), m = row.level + .5, d = s.estimatedDifficulty! - m;
  const reasons: string[] = [], frozen = s.scoringRefinement?.range;
  if (Math.abs(d) < 2) reasons.push("inside-two-point-boundary");
  if (protectedMedian) reasons.push("protected-folder-median");
  if (s.scoringMethod !== "target-attainment-rasch-v1" || ![undefined, "mixed"].includes(s.scoringEstimateBranch)) reasons.push("not-ordinary-mixed-first-pass");
  if (s.nContributors < 20 || Math.min(s.scoringTargetSuccesses ?? 0, s.scoringBelowTarget ?? 0) < 5) reasons.push("insufficient-two-sided-player-support");
  if (!s.scoringFitConverged || !s.scoringMainComponent) reasons.push("unconverged-or-disconnected-fit");
  if (!["comparable", "same", "single-source-known"].includes(s.scoringComparability ?? "")) reasons.push("incomparable-chart");
  if (s.scoringProvisionalReasons?.some(reason => ["regularization-sensitive", "weak-target-information"].includes(reason))) reasons.push("unstable-or-weak-information");
  if (s.scoringPossiblyTruncated || s.scoringRefinement?.fullBoard.possiblyTruncated) reasons.push("possibly-truncated-board");
  if (!frozen?.supported || frozen.kind !== "observed") reasons.push("borrowed-or-unsupported-local-range");
  if (s.scoringExtrapolated || s.scoringProvisionalReasons?.includes("unsupported-folder-anchor")) reasons.push("unsupported-or-extrapolated-anchor");
  if (row.level >= 20 && s.scoringFolderCentering?.basis !== "folder") reasons.push("borrowed-or-unavailable-folder-centering");
  const low = s.difficultyCi95Low, high = s.difficultyCi95High;
  let conservativeDistance: number | null = null;
  if (!finite(low) || !finite(high) || low > high) reasons.push("unavailable-player-bootstrap-interval");
  else { conservativeDistance = d >= 0 ? low - m : m - high; if (conservativeDistance <= 2) reasons.push("interval-not-entirely-beyond-two"); }
  return { exceptionEligible: reasons.length === 0, rejectionReasons: reasons, conservativeDistance, protectedMedian };
}

/** Replay whole tie blocks and a single budget across both modes, sources and sides. */
export function targetTailSelection(payload: AnalysisPayload): { selected: Set<string>; evidence: Map<string, Obj> } {
  const info = new Map<string, Obj>(), tails: ChartResult[][][] = [];
  for (const rows of rowGroups(payload)) {
    const ordered = rows.toSorted((a, b) => preTailMetric(a).estimatedDifficulty! - preTailMetric(b).estimatedDifficulty! || (a.chartId < b.chartId ? -1 : 1));
    const protectedValues = new Set<number>();
    if (rows.some(row => preTailMetric(row).scoringFolderCentering?.basis === "folder")) {
      protectedValues.add(preTailMetric(ordered[Math.floor((ordered.length - 1) / 2)]).estimatedDifficulty!);
      protectedValues.add(preTailMetric(ordered[Math.floor(ordered.length / 2)]).estimatedDifficulty!);
    }
    for (const row of ordered) info.set(row.chartId, evidence(row, protectedValues.has(preTailMetric(row).estimatedDifficulty!)));
    for (const side of [-1, 1]) {
      const outer = ordered.filter(row => side * (preTailMetric(row).estimatedDifficulty! - row.level - .5) >= 2)
        .sort((a, b) => Math.abs(preTailMetric(b).estimatedDifficulty! - b.level - .5) - Math.abs(preTailMetric(a).estimatedDifficulty! - a.level - .5) || (a.chartId < b.chartId ? -1 : 1));
      const blocks: ChartResult[][] = []; let offset = 0, blocked = false;
      while (offset < outer.length) {
        let end = offset + 1;
        while (end < outer.length && preTailMetric(outer[end]).estimatedDifficulty === preTailMetric(outer[offset]).estimatedDifficulty) end++;
        const block = outer.slice(offset, end);
        if (blocked || block.some(row => !info.get(row.chartId)!.exceptionEligible)) {
          for (const row of block) if (info.get(row.chartId)!.exceptionEligible) (info.get(row.chartId)!.rejectionReasons as string[]).push("ineligible-outer-chart-blocks-tail");
          blocked = true;
        } else blocks.push(block);
        offset = end;
      }
      if (blocks.length) tails.push(blocks);
    }
  }
  const selected = new Set<string>(); let remaining = 10;
  const close = (blocks: ChartResult[][]) => { for (const block of blocks) for (const row of block) (info.get(row.chartId)!.rejectionReasons as string[]).push("global-exception-budget"); };
  while (tails.length && remaining) {
    tails.sort((a, b) => {
      const conservative = (block: ChartResult[]) => Math.min(...block.map(row => Number(info.get(row.chartId)!.conservativeDistance)));
      const support = (block: ChartResult[]) => Math.min(...block.map(row => preTailMetric(row).nContributors));
      const id = (block: ChartResult[]) => block.map(row => row.chartId).sort()[0];
      return conservative(b[0]) - conservative(a[0]) || support(b[0]) - support(a[0]) || (id(a[0]) < id(b[0]) ? -1 : 1);
    });
    const blocks = tails.shift()!, block = blocks.shift()!;
    if (block.length > remaining) { close([block, ...blocks]); continue; }
    for (const row of block) selected.add(row.chartId);
    remaining -= block.length;
    if (blocks.length) tails.push(blocks);
  }
  for (const blocks of tails) close(blocks);
  return { selected, evidence: info };
}

export function validateSssPlusAnalysisPayload(payload: AnalysisPayload, validateHistorical: (base: AnalysisPayload) => unknown): AnalysisPayload {
  need(payload.schemaVersion === 52 && Array.isArray(payload.singles) && Array.isArray(payload.doubles), "Invalid SSS+ generation.");
  validateHistorical(recoverSssPlusBaseline(payload));
  return validateTargetStages(payload);
}

function validateTargetStages(payload: AnalysisPayload): AnalysisPayload {
  // The target-specific intermediate replay and final tail contract are checked below.
  const method = obj(payload.summary.method.sssplusScoring);
  need(method.version === 1 && method.method === "target-attainment-rasch-v1" && method.target === TARGET && method.baseSchemaVersion === 51,
    "SSS+ numeric target or model identity differs.");
  need(same(method.targetConfig, TARGET_CONFIG), "SSS+ target configuration differs.");
  const integration = obj(payload.summary.method.sssplusTiers), refinement = obj(payload.summary.method.sssplusRefinement);
  need(same(integration, { version: 1, method: "sssplus-target-with-bounded-tails-v1", baseSchemaVersion: 51,
    basePayloadSha256: method.basePayloadSha256, localOnly: true, targetConfig: TARGET_CONFIG,
    stageOrder: ["target-fit", "median-anchors", "frozen-range-refinement", "folder-centering", "single-midpoint-compression", "target-tail-policy", "component-mean-and-ranks"],
    compressionApplications: 1, tailPolicyMethod: METHOD }), "SSS+ integration identity or stage order differs.");
  need(same(payload.summary.method.sssplusPumbility, { version: 1, aggregation: "average", requiresBoth: true,
    calculation: "(final SSS+ scoring difficulty + unchanged clearing difficulty) / 2 before rounding",
    componentValues: "after-midpoint-compression-centering-and-target-tail-policy", intervalMethod: "player-bootstrap-fixed-clearing-and-calibration-v1",
    evidence: "weaker component status; separate scoring and clearing support counts" }), "SSS+ Pumbility policy differs.");
  need(refinement.method === "frozen-range-target-second-pass-v1" && refinement.scoreTarget === TARGET && refinement.firstPassSchemaVersion === 52,
    "SSS+ refinement identity differs.");
  const centering = obj(payload.summary.method.sssplusFolderCentering);
  need(centering.method === "target-final-folder-median-centering-v1" && centering.baseSchemaVersion === 52 && centering.targetKey === "sssplus"
    && centering.population === "all-finite-final-target-excluding-unrated" && centering.pumbility === "mean-centered-target-and-unchanged-clearing-no-composite-centering",
    "SSS+ centering identity differs.");
  for (const source of ["submitted", "official"]) for (const mode of ["Single", "Double"]) {
    const diagnostic = obj(obj(obj(method.diagnostics)[source])[mode]);
    need(obj(diagnostic.modelPolicy).scoreTarget === TARGET, "SSS+ model policy contains a stale target.");
    const scale = obj(obj(obj(refinement.gapCalibration)[source])[mode]);
    need(same(obj(scale.policy).scoreRange, [985000, 999999]), "SSS+ gap calibration contains a stale score window.");
  }
  const centered = centeredTargetView(payload);
  validateCenteredAnalysisPayload(centered, refined => validateRefinedAnalysisPayload(refined,
    first => validateSssAnalysisPayload(first, () => undefined, TARGET), TARGET));
  validateTail(payload);
  return payload;
}

function derived(value: number | null, level: number): Obj {
  const d = value === null ? null : value - level - .5;
  const rank = d === null ? null : d < -.5 ? 1 : d < -.3 ? 2 : d < -.1 ? 3 : d <= .1 ? 4 : d <= .3 ? 5 : d <= .5 ? 6 : 7;
  return { estimatedDifficulty: value, difficultyDelta: d, effectBandRank: rank,
    effectBand: rank === null ? null : ["Overrated", "Very Easy", "Easy", "Medium", "Hard", "Very Hard", "Underrated"][rank - 1] };
}

export function validateTail(payload: AnalysisPayload) {
  const { selected, evidence: info } = targetTailSelection(payload), groups = rowGroups(payload);
  const finalRanges = new Map<string, Obj>();
  for (const rows of groups) {
    const values = rows.map(row => targetTailRating(preTailMetric(row).estimatedDifficulty, row.level, selected.has(row.chartId))!);
    for (const row of rows) finalRanges.set(row.chartId, { lower: Math.min(...values), upper: Math.max(...values) });
  }
  const pre: number[] = [], post: number[] = []; let changed = 0, displayed = 0;
  const statuses = ["Unrated", "Insufficient", "Provisional", "Published"];
  for (const key of ["singles", "doubles"] as const) {
    for (const row of payload[key]) {
      need(same(Object.keys(row.scoringVariants ?? {}).sort(), ["general", "sss", "sssplus"])
        && same(Object.keys(row.pumbilityVariants ?? {}).sort(), ["general", "sss", "sssplus"]), "SSS+ variant keys differ.");
      const s = obj(row.scoringVariants!.sssplus), p = obj(row.pumbilityVariants!.sssplus), before = obj(preTailMetric(row)), level = row.level, midpoint = level + .5;
      need(s.scoringTarget === TARGET && TARGET_METHODS.has(String(s.scoringMethod)), "SSS+ target or method differs.");
      const exceptional = selected.has(row.chartId), value = targetTailRating(before.estimatedDifficulty as number | null, level, exceptional);
      const low = targetTailRating(before.difficultyCi95Low as number | null, level, exceptional), high = targetTailRating(before.difficultyCi95High as number | null, level, exceptional);
      const details: Obj = { version: 1, method: METHOD, midpoint, preTailDifficulty: before.estimatedDifficulty,
        preTailDifficultyCi95Low: before.difficultyCi95Low, preTailDifficultyCi95High: before.difficultyCi95High,
        ordinaryDifficulty: targetTailRating(before.estimatedDifficulty as number | null, level), exceptionSelected: exceptional,
        ...(info.get(row.chartId) ?? { exceptionEligible: false, rejectionReasons: ["unrated"], conservativeDistance: null, protectedMedian: false }),
        finalDifficulty: value, finalDifficultyCi95Low: low, finalDifficultyCi95High: high };
      if (finalRanges.has(row.chartId)) details.finalFolderRange = finalRanges.get(row.chartId);
      const expected = structuredClone(before);
      Object.assign(expected, derived(value, level), { difficultyCi95Low: low, difficultyCi95High: high,
        difficultyDeltaCi95Low: low === null ? null : low - midpoint, difficultyDeltaCi95High: high === null ? null : high - midpoint,
        scoringTailPolicy: details });
      if (low !== null) expected.intervalMethod = TAIL_INTERVAL;
      need(same(withoutRanks(s), withoutRanks(expected)), "SSS+ tail value, interval, selection, diagnostics or preserved fit differs.");
      checkFit(s, level); checkFit(obj(s.initialFit), level);
      const c = obj(row.tierMetrics!.clearing), rawS = obj(s.initialFit), rawC = obj(c.initialFit), rawP = obj(p.initialFit);
      const mean = value === null || c.estimatedDifficulty === null ? null : (value + Number(c.estimatedDifficulty)) / 2;
      const rawMean = rawS.estimatedDifficulty === null || rawC.estimatedDifficulty === null ? null : (Number(rawS.estimatedDifficulty) + Number(rawC.estimatedDifficulty)) / 2;
      const status = mean === null ? "Unrated" : statuses[Math.min(statuses.indexOf(String(s.evidenceStatus)), statuses.indexOf(String(c.evidenceStatus)))];
      const intervalMethod = s.scoringMethod !== "target-attainment-rasch-v1" ? "unavailable-after-sss-refinement-v1" : "conditional-target-tail-fixed-clearing-and-calibration-v1";
      need(same(p.estimatedDifficulty, mean) && same(rawP.estimatedDifficulty, rawMean) && p.aggregation === "average" && p.evidenceStatus === status
        && p.scoringSupportCount === s.nContributors && p.clearingSupportCount === c.ratedClearCount && p.intervalMethod === intervalMethod,
        "SSS+ Pumbility mean, evidence, support or interval method differs.");
      need(p.unratedReason === (value === null ? s.scoringUnratedReason : c.estimatedDifficulty === null ? "Clearing difficulty is unavailable." : null), "SSS+ Pumbility unavailable reason differs.");
      for (const side of ["Low", "High"]) {
        const field = `difficultyCi95${side}`;
        need(same(p[field], mean === null || s[field] === null ? null : (Number(s[field]) + Number(c.estimatedDifficulty)) / 2)
          && same(rawP[field], rawMean === null || rawS[field] === null ? null : (Number(rawS[field]) + Number(rawC.estimatedDifficulty)) / 2), "SSS+ Pumbility interval differs.");
      }
      checkFit(p, level); checkFit(rawP, level);
      if (value !== null) {
        pre.push(Number(before.estimatedDifficulty) - midpoint); post.push(value - midpoint);
        if (before.estimatedDifficulty !== value) changed++;
        if (Math.abs(truncateEstimatedDifficulty(value) - midpoint) >= 2) displayed++;
        need(Math.abs(value - midpoint) <= 2.5, "SSS+ rating exceeds its absolute tail ceiling.");
      }
    }
    const ranked = structuredClone(payload[key]);
    for (const row of ranked) { row.scoringVariants!.sss = row.scoringVariants!.sssplus; row.pumbilityVariants!.sss = row.pumbilityVariants!.sssplus; }
    validateRanks(ranked as unknown as Obj[]);
  }
  for (const rows of groups) {
    const ordered = rows.toSorted((a, b) => preTailMetric(a).estimatedDifficulty! - preTailMetric(b).estimatedDifficulty!);
    for (let i = 1; i < ordered.length; i++) need(ordered[i].scoringVariants!.sssplus!.estimatedDifficulty! >= ordered[i - 1].scoringVariants!.sssplus!.estimatedDifficulty!, "SSS+ tail policy inverted folder ordering.");
    if (rows.some(row => preTailMetric(row).scoringFolderCentering?.basis === "folder")) need(same(median(rows.map(row => row.scoringVariants!.sssplus!.estimatedDifficulty!)), rows[0].level + .5), "SSS+ tail policy shifted a supported folder median.");
  }
  const counts = { ratedCharts: post.length, changedCharts: changed, preTailAbsAbove1_5: pre.filter(d => Math.abs(d) > 1.5).length,
    preTailAbsAtLeast2: pre.filter(d => Math.abs(d) >= 2).length, finalAbsAbove1_5: post.filter(d => Math.abs(d) > 1.5).length,
    finalAbsAtLeast2: post.filter(d => Math.abs(d) >= 2).length, finalAbsAbove2_5: post.filter(d => Math.abs(d) > 2.5).length,
    displayedAbsAtLeast2: displayed, preTailMinDelta: pre.length ? Math.min(...pre) : null, preTailMaxDelta: pre.length ? Math.max(...pre) : null,
    finalMinDelta: post.length ? Math.min(...post) : null, finalMaxDelta: post.length ? Math.max(...post) : null };
  need(same(payload.summary.method.sssplusTailPolicy, { ...TAIL_PARAMETERS, targetKey: "sssplus", selectedChartIds: [...selected].sort(), counts,
    intervalMethod: TAIL_INTERVAL, selection: "contiguous-eligible-outer-tie-blocks-global-conservative-distance-budget-v1" }), "SSS+ tail policy, global budget or aggregate counts differ.");
  need(counts.finalAbsAtLeast2 <= 10 && displayed <= 10 && selected.size <= 10 && counts.finalAbsAbove2_5 === 0, "SSS+ exact or displayed outlier budget exceeded.");
}
