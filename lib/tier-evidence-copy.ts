import { formatEstimatedDifficulty } from "./format-difficulty.ts";
import { isSssScoringChart, isTargetBoundaryChart, isTargetRareChart, scoringTargetForChart } from "./scoring-variants.ts";
import { clearingRecordCount, hasLimitedTierData, hasQualifiedSubmittedClearing, scoringRecordCount, selectedTierMetric } from "./tier-metrics.ts";
import type { PumbilityAggregation } from "./pumbility-policy";
import type { ChartResult, TierMetricKey } from "./types";

export interface EvidenceCopyOptions {
  minimumScoreRecords?: number | null;
  pumbilityAggregation?: PumbilityAggregation | null;
}

function count(value: number | null): string {
  return value === null ? "an unknown number of" : value.toLocaleString();
}

function targetName(chart: ChartResult): string {
  const label = scoringTargetForChart(chart).label;
  return label === "SSS+" ? label : `${label} or higher`;
}

/** Boundary estimates use the returned board; other target estimates use eligible observations. */
function targetRecords(chart: ChartResult) {
  const records = scoringRecordCount(chart);
  const evidence = isTargetBoundaryChart(chart) ? chart.scoringRefinement?.fullBoard : chart.scoringRefinement?.modelEligible;
  const successes = evidence?.records === records ? evidence.successes : chart.scoringTargetSuccesses;
  const below = evidence?.records === records ? evidence.belowTarget : chart.scoringBelowTarget;
  const complete = records !== null && Number.isInteger(successes) && Number.isInteger(below)
    && successes! >= 0 && below! >= 0 && successes! + below! === records;
  return { records, successes: complete ? successes! : null, below: complete ? below! : null };
}

function scoringCountLine(chart: ChartResult): string {
  const records = scoringRecordCount(chart);
  if (!isSssScoringChart(chart)) return `Based on ${count(records)} usable player records.`;
  const target = targetRecords(chart);
  const kind = isTargetBoundaryChart(chart) ? "recorded best scores" : "usable player records";
  return `Based on ${count(records)} ${kind}${target.successes === null ? "." : `; ${target.successes.toLocaleString()} reached ${targetName(chart)}.`}`;
}

function clearingCountLine(chart: ChartResult, minimum: number | null | undefined): string {
  const clearing = chart.tierMetrics?.clearing;
  if (!clearing) return "Clearing records are unavailable.";
  const used = clearingRecordCount(chart);
  const qualified = hasQualifiedSubmittedClearing(clearing) || clearing.skillMetric === "official-clearer-ability"
    || clearing.method?.startsWith("official-d26-clearer-ability");
  if (qualified) {
    const requirement = hasQualifiedSubmittedClearing(clearing) && minimum
      ? ` (at least ${minimum} recorded charts in this mode)` : "";
    return `${count(used)} of ${clearing.clearCount.toLocaleString()} clearers had enough play history to include${requirement}.`;
  }
  return `Based on ${count(used)} clearers from ${clearing.clearCount.toLocaleString()} recorded clears.`;
}

function scoringLimitations(chart: ChartResult): string[] {
  const warnings: string[] = [];
  if (chart.estimatedDifficulty === null) {
    warnings.push(chart.scoringUnratedReason === "target-variant-unavailable"
      ? "This scoring target is unavailable in this analysis."
      : "There is not enough comparable data to rate this chart.");
  }
  if (isSssScoringChart(chart)) {
    const target = targetRecords(chart);
    if (target.records && target.successes === target.records) {
      warnings.push(`Everyone in the available records reached ${targetName(chart)}, so the estimate is less certain.`);
    } else if (target.records && target.successes === 0) {
      warnings.push(`No one in the available records reached ${targetName(chart)}, so the estimate is less certain.`);
    } else if (isTargetRareChart(chart) && chart.estimatedDifficulty !== null) {
      warnings.push(`Few players reached ${targetName(chart)}, so the estimate is less certain.`);
    } else if (chart.scoringProvisional && chart.estimatedDifficulty !== null) {
      warnings.push("The available comparisons are limited, so this estimate is less certain.");
    }
  } else if ((chart.scoringProvisional || chart.scoringProfileExtrapolated || chart.scoringExtrapolated) && chart.estimatedDifficulty !== null) {
    warnings.push("The available comparisons are limited, so this estimate is less certain.");
  }
  const capped = chart.scoringPossiblyTruncated || chart.scoringRefinement?.fullBoard.possiblyTruncated
    || chart.officialEvidence?.possiblyTruncated
    || Object.values(chart.scoringSourceStates ?? chart.officialScoringEvidence?.sourceStates ?? {}).some(source => source.possiblyTruncated);
  if (capped) warnings.push("The leaderboard may be incomplete; missing scores are unknown.");
  const comparability = chart.scoringComparability ?? chart.officialScoringEvidence?.comparability;
  if (chart.chartSourceExclusions) warnings.push("Scores from older, different chart versions are excluded.");
  else if (comparability === "changed" || comparability === "unknown") {
    warnings.push("Some older scores may come from a different chart version.");
  }
  return warnings;
}

function clearingLimitations(chart: ChartResult): string[] {
  const clearing = chart.tierMetrics?.clearing;
  if (!clearing) return [];
  const warnings: string[] = [];
  if (clearing.estimatedDifficulty === null) warnings.push("There is not enough comparable clear history to rate this chart.");
  if (clearing.defaultedAtCap) {
    warnings.push("The leaderboard is incomplete, so Clearing uses a fallback for this official level.");
  } else if (clearing.clearingEvidence?.possiblyTruncated) {
    warnings.push("The leaderboard may be incomplete; missing clears are unknown.");
  }
  const reasons = clearing.provisionalReasons ?? [];
  if (clearing.comparability === "changed" || clearing.comparability === "unknown"
    || reasons.some(reason => reason.startsWith("chart-comparability-"))) {
    warnings.push("Some older clears may come from a different chart version.");
  }
  if (clearing.estimatedDifficulty !== null && (clearing.extrapolated
    || reasons.some(reason => ["outside-supported-anchors", "unsupported-official-folder"].includes(reason)))) {
    warnings.push("Few similar charts are available for comparison, so this estimate is less certain.");
  }
  return warnings;
}

/** Keep the warning icon's existing record-count rules while using everyday language. */
export function plainTierDataWarning(chart: ChartResult, metric: TierMetricKey): string | null {
  if (!hasLimitedTierData(chart, metric)) return null;
  const scoring = scoringRecordCount(chart);
  const clearing = clearingRecordCount(chart);
  const unknown = metric !== "clearing" && scoring === null || metric !== "scoring" && clearing === null;
  if (unknown) return "Limited data: the number of usable records is unavailable.";
  return metric === "pumbility"
    ? `Limited data: Scoring uses ${scoring} records and Clearing uses ${clearing} clearers. At least one uses fewer than 10.`
    : `Limited data: this rating uses ${metric === "scoring" ? scoring : clearing} ${metric === "scoring" ? "records" : "clearers"}, fewer than 10.`;
}

function grade(chart: ChartResult, metric: TierMetricKey): string {
  const value = selectedTierMetric(chart, metric).estimatedDifficulty;
  if (value === null) return "unavailable";
  return `${chart.type === "Single" ? "S" : "D"}${formatEstimatedDifficulty(value)}`;
}

/** Public explanations deliberately omit internal fit and calibration diagnostics. */
export function chartEvidenceLines(chart: ChartResult, metric: TierMetricKey, options: EvidenceCopyOptions = {}): string[] {
  const lines: string[] = [];
  if (metric === "pumbility") {
    const target = isSssScoringChart(chart) ? `${scoringTargetForChart(chart).label} Scoring` : "Scoring";
    lines.push(`${target}: ${grade(chart, "scoring")}. Clearing: ${grade(chart, "clearing")}.`);
    const aggregation = chart.tierMetrics?.pumbility.aggregation ?? options.pumbilityAggregation;
    lines.push(chart.tierMetrics?.pumbility.estimatedDifficulty == null
      ? "Pumbility needs both a Scoring and a Clearing rating."
      : aggregation === "average" ? `Pumbility uses their average, calculated before rounding: ${grade(chart, "pumbility")}.`
        : aggregation === "maximum" ? `Pumbility uses the higher rating: ${grade(chart, "pumbility")}.`
          : `Pumbility rating: ${grade(chart, "pumbility")}.`);
    lines.push(`Scoring: ${scoringCountLine(chart)}`);
    lines.push(`Clearing: ${clearingCountLine(chart, options.minimumScoreRecords)}`);
  } else if (metric === "clearing") {
    lines.push("Uses the play history of players who cleared this chart.");
    lines.push(clearingCountLine(chart, options.minimumScoreRecords));
  } else {
    lines.push(chart.scoringMethod === "submitted-score-profile-v2"
      ? "Compares players' score patterns with results on other charts."
      : "Compares players' scores with their results on other charts.");
    lines.push(scoringCountLine(chart));
  }
  const limitations = [...(metric !== "clearing" ? scoringLimitations(chart) : []),
    ...(metric !== "scoring" ? clearingLimitations(chart) : [])];
  const limited = plainTierDataWarning(chart, metric);
  if (limited) limitations.unshift(limited);
  if (limitations.length) lines.push([...new Set(limitations)].join(" "));
  return lines;
}

export const TIER_INTERVAL_TOOLTIP = "Estimated range from the available records. It does not include every source of uncertainty.";
