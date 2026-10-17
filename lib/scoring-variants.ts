import type { AnalysisPayload, ChartResult, ScoringGoal, ScoringVariant, TierMetricKey } from "./types";

export const DEFAULT_SCORING_GOAL: ScoringGoal = "sssplus";
export const SSS_SCORE_TARGET = 990000;
export const SSSPLUS_SCORE_TARGET = 995000;
export const HISTORICAL_SSS_UNAVAILABLE = "SSS is unavailable in this analysis generation. Showing its existing General estimates.";

export function scoringTargetForPayload(payload: AnalysisPayload | null | undefined) {
  return [52, 53, 54, 55, 56].includes(payload?.schemaVersion ?? 0) || !payload
    ? { goal: "sssplus" as const, label: "SSS+", score: SSSPLUS_SCORE_TARGET }
    : { goal: "sss" as const, label: "SSS", score: SSS_SCORE_TARGET };
}

export function scoringTargetForChart(chart: ChartResult) {
  return chart.scoringTarget === SSSPLUS_SCORE_TARGET
    ? { goal: "sssplus" as const, label: "SSS+", score: SSSPLUS_SCORE_TARGET }
    : { goal: "sss" as const, label: "SSS", score: SSS_SCORE_TARGET };
}

export function scoringGoalFromSearchParams(params: URLSearchParams, payload?: AnalysisPayload | null): ScoringGoal {
  return params.get("scoring") === "general" ? "general" : scoringTargetForPayload(payload).goal;
}

/** Preserve player, filters represented by the URL, and unrelated navigation state. */
export function scoringGoalUrl(href: string, goal: ScoringGoal): URL {
  const url = new URL(href);
  url.searchParams.set("scoring", goal);
  return url;
}

export function hasSssScoringVariants(payload: AnalysisPayload | null | undefined): boolean {
  return [49, 50, 51, 52, 53, 54, 55, 56].includes(payload?.schemaVersion ?? 0);
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
  if (!chart.scoringVariants && goal === "sss") return chart;
  const target = chart.scoringVariants?.[goal] ?? unavailableTarget;
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
      ...unavailableTarget, aggregation: "average", scoringSupportCount: 0,
      clearingSupportCount: chart.tierMetrics.clearing.ratedClearCount,
    } };
  }
  return projected;
}
