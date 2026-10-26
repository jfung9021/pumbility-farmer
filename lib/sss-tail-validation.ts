import { HISTORICAL_SCORING_GOALS, SCORING_TARGET_POLICIES } from "./scoring-variants.ts";
import { checkFit, validateRanks } from "./sss-analysis-validation.ts";
import { targetTailRating, targetTailSelection } from "./sssplus-analysis-validation.ts";
import { truncateEstimatedDifficulty } from "./format-difficulty.ts";
import type { AnalysisPayload, ChartResult, ScoringVariant } from "./types";

type Obj = Record<string, any>;
const NS = "sssTailPolicy";
const METHOD = "sss-target-bounded-tails-v1";
const SCRIPT_VERSION = "6.23.0-sss-bounded-tails";
const TAIL_METHOD = "target-midpoint-soft-tail-global-exceptions-v1";
const TAIL_INTERVAL = "conditional-target-tail-fixed-selection-and-calibration-v1";
const PARAMETERS = { version: 1, method: TAIL_METHOD, identityRadius: 1.5, ordinaryAsymptote: 1.9,
  exceptionThreshold: 2, exceptionLimit: 10, exceptionCeiling: 2.5 };
const RANKS = ["levelRank", "levelComparisonCharts", "modeRank", "levelPercentile", "relativeGroup", "relativeGroupRank"];
const STATUSES = ["Unrated", "Insufficient", "Provisional", "Published"];
const METHODS = ["sss-target-rasch-v1", "sss-boundary-score-gap-v1", "sss-rare-success-upper-band-v1"];
const HASH = /^[a-f0-9]{64}$/;
function need(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
function obj(value: unknown): Obj {
  need(value !== null && typeof value === "object" && !Array.isArray(value), "Missing SSS tail object.");
  return value as Obj;
}
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function same(a: any, b: any): boolean {
  if (typeof b === "number") return finite(a) && Math.abs(a - b) < 1e-8;
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => same(v, b[i]));
  return !!a && !!b && typeof a === "object" && typeof b === "object"
    && Object.keys(a).length === Object.keys(b).length && Object.keys(b).every(k => k in a && same(a[k], b[k]));
}
function withoutRanks(metric: Obj): Obj {
  const copy = structuredClone(metric);
  for (const field of RANKS) { delete copy[field]; if (copy.initialFit) delete copy.initialFit[field]; }
  return copy;
}
function derived(value: number | null, level: number): Obj {
  const delta = value === null ? null : value - level - .5;
  const rank = delta === null ? null : delta < -.5 ? 1 : delta < -.3 ? 2 : delta < -.1 ? 3 : delta <= .1 ? 4 : delta <= .3 ? 5 : delta <= .5 ? 6 : 7;
  return { estimatedDifficulty: value, difficultyDelta: delta, effectBandRank: rank,
    effectBand: rank === null ? null : ["Overrated", "Very Easy", "Easy", "Medium", "Hard", "Very Hard", "Underrated"][rank - 1] };
}
function summary(payload: AnalysisPayload): { overlay: Obj; tail: Obj; selected: Set<string> } {
  need(payload.schemaVersion === 59 && Array.isArray(payload.singles) && Array.isArray(payload.doubles), "Invalid SSS tail generation.");
  const overlay = obj(payload.summary.method[NS]), tail = obj(overlay.tailPolicy);
  need(overlay.version === 1 && overlay.method === METHOD && [57, 58].includes(overlay.baseSchemaVersion)
    && (overlay.baseScriptVersion === null || typeof overlay.baseScriptVersion === "string") && HASH.test(overlay.basePayloadSha256), "SSS tail predecessor identity differs.");
  need(payload.summary.scriptVersion === SCRIPT_VERSION, "SSS tail script version differs.");
  for (const [key, value] of Object.entries(PARAMETERS)) need(tail[key] === value, "SSS tail parameters differ.");
  need(tail.targetKey === "sss" && tail.intervalMethod === TAIL_INTERVAL
    && tail.selection === "contiguous-eligible-outer-tie-blocks-global-conservative-distance-budget-v1"
    && Array.isArray(tail.selectedChartIds) && tail.selectedChartIds.every((id: unknown) => typeof id === "string")
    && same(tail.selectedChartIds, [...new Set(tail.selectedChartIds)].sort()) && tail.selectedChartIds.length <= 10,
  "SSS tail selection or global budget differs.");
  const goals = overlay.baseSchemaVersion === 58 ? payload.summary.method.availableScoringGoals : ["general", "sss", "sssplus"];
  need(Array.isArray(goals) && same(goals, HISTORICAL_SCORING_GOALS.filter(goal => goals.includes(goal)))
    && ["general", "sss", "sssplus"].every(goal => goals.includes(goal)), "SSS tail scoring-goal availability differs.");
  if (overlay.baseSchemaVersion === 58) need(goals.some(goal => goal === "ss" || goal === "ssplus")
    && same(payload.summary.method.scoringGoalPolicies, { version: 1, goals: SCORING_TARGET_POLICIES }), "SSS tail expanded-goal policy differs.");
  const keys = [...goals].sort(), rows = [...payload.singles, ...payload.doubles];
  need(new Set(rows.map(row => row.chartId)).size === rows.length, "SSS tail chart population is duplicated.");
  for (const row of rows) need(same(Object.keys(obj(row.scoringVariants)).sort(), keys)
    && same(Object.keys(obj(row.pumbilityVariants)).sort(), keys), "SSS tail variant population differs.");
  need(tail.selectedChartIds.every((id: string) => rows.some(row => row.chartId === id && finite(row.scoringVariants?.sss?.estimatedDifficulty))), "SSS tail selection contains an unavailable chart.");
  return { overlay, tail, selected: new Set(tail.selectedChartIds) };
}

/** Remove only the final SSS policy; all predecessor algorithms stay strict. */
export function recoverSssTailBaseline(payload: AnalysisPayload): AnalysisPayload {
  const { overlay } = summary(payload), base = structuredClone(payload);
  base.schemaVersion = overlay.baseSchemaVersion;
  if (overlay.baseScriptVersion === null) delete (base.summary as unknown as Obj).scriptVersion;
  else base.summary.scriptVersion = overlay.baseScriptVersion;
  delete base.summary.method[NS];
  for (const row of [...base.singles, ...base.doubles]) {
    const saved = obj(row.sssTailBaseline);
    need(same(Object.keys(saved).sort(), ["pumbility", "scoring"]), "SSS tail recovery scope differs.");
    row.scoringVariants!.sss = obj(saved.scoring) as ScoringVariant;
    row.pumbilityVariants!.sss = saved.pumbility;
    delete row.sssTailBaseline;
  }
  return base;
}

/** Adapt only the ordinary-model name for the unchanged global selection replay. */
function selectionView(payload: AnalysisPayload): AnalysisPayload {
  const view = { ...payload };
  for (const key of ["singles", "doubles"] as const) view[key] = payload[key].map(row => {
    const saved = obj(row.sssTailBaseline), scoring = obj(saved.scoring);
    return { ...row, sssplusTailBaseline: { ...saved, scoring: { ...scoring,
      scoringMethod: scoring.scoringMethod === "sss-target-rasch-v1" ? "target-attainment-rasch-v1" : scoring.scoringMethod,
    } } } as ChartResult;
  });
  return view;
}

function checkComposite(row: ChartResult, full: boolean) {
  const scoring = obj(row.scoringVariants?.sss), clearing = obj(row.tierMetrics?.clearing), composite = obj(row.pumbilityVariants?.sss);
  const value = scoring.estimatedDifficulty === null || clearing.estimatedDifficulty === null ? null : (scoring.estimatedDifficulty + clearing.estimatedDifficulty) / 2;
  const status = value === null ? "Unrated" : STATUSES[Math.min(STATUSES.indexOf(scoring.evidenceStatus), STATUSES.indexOf(clearing.evidenceStatus))];
  need(STATUSES.includes(scoring.evidenceStatus) && STATUSES.includes(clearing.evidenceStatus)
    && composite.aggregation === "average" && same(composite.estimatedDifficulty, value) && composite.evidenceStatus === status
    && composite.scoringSupportCount === scoring.nContributors && composite.clearingSupportCount === clearing.ratedClearCount,
  "SSS tail Pumbility mean, evidence or support differs.");
  checkFit(composite, row.level);
  for (const side of ["Low", "High"]) {
    const endpoint = scoring[`difficultyCi95${side}`], expected = value === null || endpoint === null ? null : (endpoint + clearing.estimatedDifficulty) / 2;
    need(same(composite[`difficultyCi95${side}`], expected), "SSS tail Pumbility interval differs.");
  }
  if (!full) return;
  const rawS = obj(scoring.initialFit), rawC = obj(clearing.initialFit), rawP = obj(composite.initialFit);
  const rawMean = rawS.estimatedDifficulty === null || rawC.estimatedDifficulty === null ? null : (rawS.estimatedDifficulty + rawC.estimatedDifficulty) / 2;
  need(same(rawP.estimatedDifficulty, rawMean), "SSS tail initial Pumbility differs.");
  checkFit(rawP, row.level);
  need(composite.unratedReason === (scoring.estimatedDifficulty === null ? scoring.scoringUnratedReason
    : clearing.estimatedDifficulty === null ? "Clearing difficulty is unavailable." : null), "SSS tail unavailable reason differs.");
  const interval = scoring.scoringMethod === "sss-target-rasch-v1"
    ? "conditional-target-tail-fixed-clearing-and-calibration-v1" : "unavailable-after-sss-refinement-v1";
  need(composite.intervalMethod === interval, "SSS tail composite interval policy differs.");
  for (const side of ["Low", "High"]) need(same(rawP[`difficultyCi95${side}`], value === null || rawS[`difficultyCi95${side}`] === null
    ? null : (rawS[`difficultyCi95${side}`] + rawC.estimatedDifficulty) / 2), "SSS tail initial Pumbility interval differs.");
}

function validateTailValues(payload: AnalysisPayload, selected: Set<string>) {
  const pre: number[] = [], post: number[] = [];
  let changed = 0, displayed = 0;
  for (const row of [...payload.singles, ...payload.doubles]) {
    const scoring = obj(row.scoringVariants?.sss), detail = obj(scoring.scoringTailPolicy), midpoint = row.level + .5;
    need(scoring.scoringTarget === 990000 && METHODS.includes(scoring.scoringMethod)
      && scoring.scoringSource === (row.level >= 20 ? "official" : "submitted"), "SSS tail target or source differs.");
    need(Number.isSafeInteger(scoring.nContributors) && scoring.nContributors >= 0
      && Number.isSafeInteger(scoring.scoringTargetSuccesses) && scoring.scoringTargetSuccesses >= 0
      && Number.isSafeInteger(scoring.scoringBelowTarget) && scoring.scoringBelowTarget >= 0
      && scoring.nContributors === scoring.scoringTargetSuccesses + scoring.scoringBelowTarget, "SSS tail support totals differ.");
    const before = detail.preTailDifficulty, exceptional = selected.has(row.chartId);
    const value = targetTailRating(before, row.level, exceptional);
    need(same(scoring.estimatedDifficulty, value), "SSS tail point estimate differs.");
    for (const side of ["Low", "High"]) need(same(scoring[`difficultyCi95${side}`],
      targetTailRating(detail[`preTailDifficultyCi95${side}`], row.level, exceptional)), "SSS tail interval transform differs.");
    checkFit(scoring, row.level);
    checkComposite(row, false);
    if (value !== null) {
      pre.push(before - midpoint); post.push(value - midpoint);
      if (before !== value) changed++;
      if (Math.abs(truncateEstimatedDifficulty(value) - midpoint) >= 2) displayed++;
      need(Math.abs(value - midpoint) <= 2.5, "SSS tail ceiling exceeded.");
      need(!exceptional || Math.abs(before - midpoint) >= 2, "SSS tail exception is inside the threshold.");
    }
  }
  const counts = { ratedCharts: post.length, changedCharts: changed, preTailAbsAbove1_5: pre.filter(d => Math.abs(d) > 1.5).length,
    preTailAbsAtLeast2: pre.filter(d => Math.abs(d) >= 2).length, finalAbsAbove1_5: post.filter(d => Math.abs(d) > 1.5).length,
    finalAbsAtLeast2: post.filter(d => Math.abs(d) >= 2).length, finalAbsAbove2_5: post.filter(d => Math.abs(d) > 2.5).length,
    displayedAbsAtLeast2: displayed, preTailMinDelta: pre.length ? Math.min(...pre) : null, preTailMaxDelta: pre.length ? Math.max(...pre) : null,
    finalMinDelta: post.length ? Math.min(...post) : null, finalMaxDelta: post.length ? Math.max(...post) : null };
  need(same(obj(payload.summary.method[NS]).tailPolicy.counts, counts) && counts.finalAbsAtLeast2 <= 10 && displayed <= 10,
    "SSS tail aggregate counts or outlier budget differs.");
}

/** Verify compact list values without requiring private predecessor snapshots. */
export function validateSssTailPublicPayload(payload: AnalysisPayload): AnalysisPayload {
  const { selected } = summary(payload);
  validateTailValues(payload, selected);
  for (const rows of [payload.singles, payload.doubles]) {
    // Compact rows omit raw fitted coordinates. Final Scoring ranks remain
    // fully replayable; tied composite means may use private initial means.
    validateRanks(rows.map(row => ({ ...row,
      scoringVariants: { sss: { ...row.scoringVariants!.sss, initialFit: row.scoringVariants!.sss } },
      pumbilityVariants: { sss: { estimatedDifficulty: null, initialFit: { estimatedDifficulty: null } } },
    })));
    for (const level of new Set(rows.map(row => row.level))) {
      const ranked = rows.filter(row => row.level === level && row.pumbilityVariants!.sss!.estimatedDifficulty !== null)
        .toSorted((a, b) => Number(a.pumbilityVariants!.sss!.levelRank) - Number(b.pumbilityVariants!.sss!.levelRank));
      ranked.forEach((row, index) => {
        const metric = obj(row.pumbilityVariants!.sss);
        need(metric.levelRank === index + 1 && metric.levelComparisonCharts === ranked.length
          && ["modeRank", "levelPercentile", "relativeGroup", "relativeGroupRank"].every(key => metric[key] === null)
          && (!index || metric.estimatedDifficulty >= ranked[index - 1].pumbilityVariants!.sss!.estimatedDifficulty!),
        "SSS tail compact composite rank differs.");
      });
    }
  }
  return payload;
}

/** Replay the final policy and validate the exact schema57/58 predecessor. */
export function validateSssTailAnalysisPayload(payload: AnalysisPayload, validateBase: (base: AnalysisPayload) => unknown): AnalysisPayload {
  const { tail, selected } = summary(payload);
  const base = recoverSssTailBaseline(payload);
  validateBase(base);
  const replay = targetTailSelection(selectionView(payload));
  need(same([...selected].sort(), [...replay.selected].sort()), "SSS tail exception selection differs.");
  const groups = new Map<string, ChartResult[]>();
  for (const row of [...payload.singles, ...payload.doubles]) if (finite(row.sssTailBaseline!.scoring.estimatedDifficulty)) {
    const id = `${row.type}:${row.level}`, rows = groups.get(id) ?? []; rows.push(row); groups.set(id, rows);
  }
  for (const rows of [payload.singles, payload.doubles]) {
    for (const row of rows) {
      const scoring = obj(row.scoringVariants?.sss), before = obj(row.sssTailBaseline!.scoring), midpoint = row.level + .5;
      const exceptional = selected.has(row.chartId), value = targetTailRating(before.estimatedDifficulty, row.level, exceptional);
      const low = targetTailRating(before.difficultyCi95Low, row.level, exceptional), high = targetTailRating(before.difficultyCi95High, row.level, exceptional);
      const details: Obj = { version: 1, method: TAIL_METHOD, midpoint, preTailDifficulty: before.estimatedDifficulty,
        preTailDifficultyCi95Low: before.difficultyCi95Low, preTailDifficultyCi95High: before.difficultyCi95High,
        ordinaryDifficulty: targetTailRating(before.estimatedDifficulty, row.level), exceptionSelected: exceptional,
        ...(replay.evidence.get(row.chartId) ?? { exceptionEligible: false, rejectionReasons: ["unrated"], conservativeDistance: null, protectedMedian: false }),
        finalDifficulty: value, finalDifficultyCi95Low: low, finalDifficultyCi95High: high };
      const group = groups.get(`${row.type}:${row.level}`);
      if (group?.includes(row)) {
        const values = group.map(r => targetTailRating(r.sssTailBaseline!.scoring.estimatedDifficulty, r.level, selected.has(r.chartId))!);
        details.finalFolderRange = { lower: Math.min(...values), upper: Math.max(...values) };
      }
      const expected: Obj = { ...structuredClone(before), ...derived(value, row.level), difficultyCi95Low: low, difficultyCi95High: high,
        difficultyDeltaCi95Low: low === null ? null : low - midpoint, difficultyDeltaCi95High: high === null ? null : high - midpoint,
        scoringTailPolicy: details };
      if (low !== null) expected.intervalMethod = TAIL_INTERVAL;
      need(same(withoutRanks(scoring), withoutRanks(expected)), "SSS tail values, evidence, intervals or preserved model differ.");
      checkFit(obj(scoring.initialFit), row.level);
      checkComposite(row, true);
    }
    validateRanks(rows as unknown as Obj[]);
  }
  validateTailValues(payload, selected);
  need(same(tail, { ...PARAMETERS, targetKey: "sss", selectedChartIds: [...selected].sort(), counts: tail.counts,
    intervalMethod: TAIL_INTERVAL, selection: "contiguous-eligible-outer-tie-blocks-global-conservative-distance-budget-v1" }), "SSS tail summary scope differs.");
  return payload;
}
