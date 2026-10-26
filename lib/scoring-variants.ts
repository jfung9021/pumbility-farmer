import type { AnalysisPayload, ChartResult, ScoringGoal, ScoringGoalPolicy, ScoringVariant, TierMetricKey } from "./types";

export const DEFAULT_SCORING_GOAL: ScoringGoal = "sssplus";
export const SCORING_GOALS = ["general", "sssplus"] as const;
/** Immutable older generation contracts still contain these internal variants. */
export const HISTORICAL_SCORING_GOALS = ["general", "ss", "ssplus", "sss", "sssplus"] as const;
export const SS_SCORE_TARGET = 980000;
export const SSPLUS_SCORE_TARGET = 985000;
export const SSS_SCORE_TARGET = 990000;
export const SSSPLUS_SCORE_TARGET = 995000;
const TARGETS = {
  ss: { goal: "ss", label: "SS", score: SS_SCORE_TARGET },
  ssplus: { goal: "ssplus", label: "SS+", score: SSPLUS_SCORE_TARGET },
  sss: { goal: "sss", label: "SSS", score: SSS_SCORE_TARGET },
  sssplus: { goal: "sssplus", label: "SSS+", score: SSSPLUS_SCORE_TARGET },
} as const;
export const SCORING_TARGET_POLICIES: readonly ScoringGoalPolicy[] = [
  { key: "ss", grade: "SS", score: 980000, inclusive: true, policyVersion: "ss-target-policy-v1",
    modelMethod: "target-attainment-rasch-v1", boundaryMethod: "target-boundary-score-gap-v1",
    rareMethod: "target-rare-success-upper-band-v1", nearTargetScoreRange: [970000, 999999] },
  { key: "ssplus", grade: "SS+", score: 985000, inclusive: true, policyVersion: "ssplus-target-policy-v1",
    modelMethod: "target-attainment-rasch-v1", boundaryMethod: "target-boundary-score-gap-v1",
    rareMethod: "target-rare-success-upper-band-v1", nearTargetScoreRange: [975000, 999999] },
  { key: "sss", grade: "SSS", score: 990000, inclusive: true, policyVersion: "sss-historical-v1",
    modelMethod: "sss-target-rasch-v1", boundaryMethod: "sss-boundary-score-gap-v1",
    rareMethod: "sss-rare-success-upper-band-v1", nearTargetScoreRange: [980000, 999999] },
  { key: "sssplus", grade: "SSS+", score: 995000, inclusive: true, policyVersion: "sssplus-target-policy-v1",
    modelMethod: "target-attainment-rasch-v1", boundaryMethod: "target-boundary-score-gap-v1",
    rareMethod: "target-rare-success-upper-band-v1", nearTargetScoreRange: [985000, 999999] },
];
export const HISTORICAL_SSS_UNAVAILABLE = "SSS is unavailable in this analysis generation. Its charts remain Unrated; General is available separately.";

/** The selected goal owns its label, even while older analysis data is loading. */
export function scoringTargetForGoal(goal: ScoringGoal) {
  return TARGETS[goal === "general" ? "sssplus" : goal];
}

export function scoringGoalUnavailableMessage(goal: Exclude<ScoringGoal, "general">): string {
  const target = scoringTargetForGoal(goal);
  return `${target.label} is unavailable in this analysis generation. Its charts remain Unrated; General is available separately.`;
}

/** An unrated estimate is still a supported variant; do not infer availability from its point value. */
export function hasScoringGoalVariants(payload: AnalysisPayload | null | undefined, goal: ScoringGoal): boolean {
  if (!payload) return false;
  if (goal === "general") return true;
  if ([58, 59, 60].includes(payload.schemaVersion ?? 0) && payload.summary.method.availableScoringGoals) {
    return payload.summary.method.availableScoringGoals.includes(goal);
  }
  return payload.singles.some(chart => Boolean(chart.scoringVariants?.[goal]))
    || payload.doubles.some(chart => Boolean(chart.scoringVariants?.[goal]));
}

export function scoringTargetForPayload(payload: AnalysisPayload | null | undefined) {
  return [52, 53, 54, 55, 56, 57, 58, 59, 60].includes(payload?.schemaVersion ?? 0) || !payload
    ? { goal: "sssplus" as const, label: "SSS+", score: SSSPLUS_SCORE_TARGET }
    : { goal: "sss" as const, label: "SSS", score: SSS_SCORE_TARGET };
}

export function scoringTargetForChart(chart: ChartResult) {
  return Object.values(TARGETS).find(target => target.score === chart.scoringTarget) ?? TARGETS.sss;
}

export function scoringGoalFromSearchParams(params: URLSearchParams, _payload?: AnalysisPayload | null): ScoringGoal {
  const goal = params.get("scoring");
  return SCORING_GOALS.find(candidate => candidate === goal) ?? DEFAULT_SCORING_GOAL;
}

/** Preserve player, filters represented by the URL, and unrelated navigation state. */
export function scoringGoalUrl(href: string, goal: ScoringGoal): URL {
  const url = new URL(href);
  url.searchParams.set("scoring", SCORING_GOALS.find(candidate => candidate === goal) ?? DEFAULT_SCORING_GOAL);
  return url;
}

export function hasSssScoringVariants(payload: AnalysisPayload | null | undefined): boolean {
  return [49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60].includes(payload?.schemaVersion ?? 0);
}

export function isSssScoringChart(chart: ChartResult): boolean {
  return chart.scoringMethod === "sss-target-rasch-v1"
    || chart.scoringMethod === "sss-boundary-score-gap-v1"
    || chart.scoringMethod === "sss-rare-success-upper-band-v1"
    || chart.scoringMethod === "target-attainment-rasch-v1"
    || chart.scoringMethod === "target-boundary-score-gap-v1"
    || chart.scoringMethod === "target-rare-success-upper-band-v1"
    || chart.scoringMethod === "target-conditional-pooled-prior-v1";
}

export function isTargetBoundaryChart(chart: ChartResult): boolean {
  return chart.scoringMethod === "sss-boundary-score-gap-v1" || chart.scoringMethod === "target-boundary-score-gap-v1";
}

export function isTargetRareChart(chart: ChartResult): boolean {
  return chart.scoringMethod === "sss-rare-success-upper-band-v1" || chart.scoringMethod === "target-rare-success-upper-band-v1" || chart.scoringMethod === "target-conditional-pooled-prior-v1";
}

const unavailableTarget: ScoringVariant = {
  estimatedDifficulty: null, difficultyDelta: null, levelRank: null, levelComparisonCharts: null,
  modeRank: null, levelPercentile: null, relativeGroup: null, relativeGroupRank: null,
  effectBandRank: null, effectBand: null, evidenceStatus: "Unrated", nContributors: 0,
  difficultyCi95Low: null, difficultyCi95High: null, difficultyDeltaCi95Low: null, difficultyDeltaCi95High: null,
  scoringTarget: 995000, scoringMethod: "target-attainment-rasch-v1", scoringUnratedReason: "target-variant-unavailable",
};

/**
 * Project once before filtering, grouping, cards and details. Compatibility fields
 * remain General in the stored payload; Clearing and Co-op are never projected.
 */
export function projectScoringGoal(chart: ChartResult, goal: ScoringGoal, metric: TierMetricKey = "scoring"): ChartResult {
  if (goal === "general" || metric === "clearing" || chart.type === "CoOp") return chart;
  const unavailable = { ...unavailableTarget, scoringTarget: scoringTargetForGoal(goal).score };
  const target = chart.scoringVariants?.[goal] ?? unavailable;
  const projected = { ...chart };
  // General-only model metadata must not describe the selected target estimate.
  for (const key of Object.keys(projected)) {
    if (key.startsWith("scoring") && key !== "scoringVariants") delete (projected as unknown as Record<string, unknown>)[key];
  }
  delete projected.officialEvidence;
  delete projected.officialContributors;
  delete projected.officialScoringEvidence;
  delete projected.officialScoringContributors;
  delete projected.phoenix1Contributors;
  delete projected.phoenix2Contributors;
  delete projected.whatIfEstimates;
  delete projected.initialFit;
  delete projected.intervalMethod;
  delete projected.intervalUnavailableReason;
  Object.assign(projected, target, { scoringGoal: goal });
  if (chart.tierMetrics) {
    projected.tierMetrics = { ...chart.tierMetrics, pumbility: chart.pumbilityVariants?.[goal] ?? {
      ...unavailable, aggregation: "average", scoringSupportCount: 0,
      clearingSupportCount: chart.tierMetrics.clearing.ratedClearCount,
    } };
  }
  return projected;
}
