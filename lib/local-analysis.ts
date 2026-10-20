import { readFile } from "node:fs/promises";
import path from "node:path";
import { cachedFileReader } from "./local-file-cache.ts";

import type { AnalysisPayload, CrossLevelClearingCurve, OfficialCrossLevelCurve, OfficialScoringSpread } from "./types";
import { COMBINED_MIX, DEFAULT_MIX, isMixKey, MIXES, type MixKey } from "./mixes.ts";
import { pumbilityReference as aggregatePumbility, resolvePumbilityPolicy } from "./pumbility-policy.ts";
import { HYBRID_INITIAL_SCHEMA, HYBRID_POSTPROCESSED_SCHEMA, POSTPROCESSED_SCHEMA, recoverInitialFitPayload } from "./tier-postprocessing.ts";
import { LOCAL_AVERAGE_PUMBILITY_SCHEMA, recoverMaximumPumbility } from "./pumbility-experiment.ts";
import { validateSssAnalysisPayload } from "./sss-analysis-validation.ts";
import { validateRefinedAnalysisPayload } from "./refined-analysis-validation.ts";
import { validateCenteredAnalysisPayload } from "./sss-centering-validation.ts";
import { validateSssPlusAnalysisPayload } from "./sssplus-analysis-validation.ts";
import { validateMedianAnalysisPayload } from "./sssplus-median-validation.ts";
import { validateSpreadAnalysisPayload } from "./sssplus-spread-validation.ts";
import { validateCombinedClearingPayload } from "./combined-clearing-validation.ts";
import { validateSourceAwarePayload } from "./source-aware-validation.ts";
import { validateSubmittedSssplusCalibrationPayload } from "./submitted-sssplus-calibration-validation.ts";
import { validateScoringGoalsPayload } from "./scoring-goals-validation.ts";
import { validateSssTailAnalysisPayload } from "./sss-tail-validation.ts";


const SECRET_PATTERN = /(?:piu_scores_live_|pst_live_)[0-9a-f]{16,}/i;
const FORBIDDEN_KEYS = new Set(["playerId", "username", "gameTag", "authorization", "apiKey", "token"]);
export const LOCAL_COMBINED_ANALYSIS_SCHEMA_VERSION = 26;
export const LOCAL_OFFICIAL_ANALYSIS_SCHEMA_VERSION = 41;
export const LOCAL_CROSS_LEVEL_ANALYSIS_SCHEMA_VERSION = 42;
export const LOCAL_CROSS_LEVEL_CLEARING_SCHEMA_VERSION = 43;
export const LOCAL_PERCENTILE_ANALYSIS_SCHEMA_VERSION = 25;

export const LEGACY_LOCAL_RESULTS_PATH = path.join(
  process.cwd(),
  ".local-data",
  "piu-scores",
  "analysis",
  "web_results.json",
);

export function localResultsPath(mix: MixKey = DEFAULT_MIX): string {
  return path.join(
    process.cwd(),
    ".local-data",
    "piu-scores",
    mix,
    "analysis",
    "web_results.json",
  );
}

export const DEFAULT_LOCAL_RESULTS_PATH = localResultsPath(DEFAULT_MIX);
export const COMBINED_LOCAL_RESULTS_PATH = path.join(
  process.cwd(),
  ".local-data",
  "piu-scores",
  "combined",
  "analysis",
  "web_results.json",
);
export const AVERAGE_PUMBILITY_LOCAL_RESULTS_PATH = path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "average", "web_results.json");
export const HYBRID_PUMBILITY_LOCAL_RESULTS_PATH = path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "hybrid", "web_results.json");
export const SSS_LOCAL_RESULTS_PATH = path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "sss", "web_results.json");
export const REFINED_SSS_LOCAL_RESULTS_PATH = path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "sss-refined", "web_results.json");
export const CENTERED_SSS_LOCAL_RESULTS_PATH = path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "sss-centered", "web_results.json");

export const SSSPLUS_LOCAL_RESULTS_PATH = path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "sssplus", "web_results.json");
export const SSSPLUS_MEDIAN_LOCAL_RESULTS_PATH = path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "sssplus-median", "web_results.json");
export const SSSPLUS_REFINED_ANCHORS_LOCAL_RESULTS_PATH = path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "sssplus-median-v2", "web_results.json");
export const SSSPLUS_STABLE_SCALE_LOCAL_RESULTS_PATH = path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "sssplus-median-v3", "web_results.json");
export const SSSPLUS_SPREAD_LOCAL_RESULTS_PATH = path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "sssplus-spread", "web_results.json");
export const SSSPLUS_COMBINED_CLEARING_LOCAL_RESULTS_PATH = path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "sssplus-spread-combined-clearing", "web_results.json");
export const SSSPLUS_P1_OBSERVED_CLEARING_LOCAL_RESULTS_PATH = path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "sssplus-spread-p1-observed-clearing", "web_results.json");
export const SUBMITTED_SSSPLUS_CALIBRATION_LOCAL_RESULTS_PATH = path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "submitted-sssplus-calibration", "web_results.json");

export function localCombinedResultsPath(
  environment: Readonly<Record<string, string | undefined>> = process.env,
): string {
  if (localAnalysisEnabled(environment) && environment.PIU_LOCAL_SCORING_VARIANT_EXPERIMENT === "submitted-sssplus-calibration") return SUBMITTED_SSSPLUS_CALIBRATION_LOCAL_RESULTS_PATH;
  if (localAnalysisEnabled(environment) && environment.PIU_LOCAL_SCORING_VARIANT_EXPERIMENT === "source-aware-width") return path.join(process.cwd(), ".local-data", "piu-scores", "combined", "experiments", "source-aware-width", "web_results.json");
  if (localAnalysisEnabled(environment) && environment.PIU_LOCAL_SCORING_VARIANT_EXPERIMENT === "sssplus-spread-p1-observed-clearing") return SSSPLUS_P1_OBSERVED_CLEARING_LOCAL_RESULTS_PATH;
  if (localAnalysisEnabled(environment) && environment.PIU_LOCAL_SCORING_VARIANT_EXPERIMENT === "sssplus-spread-combined-clearing") return SSSPLUS_COMBINED_CLEARING_LOCAL_RESULTS_PATH;
  if (localAnalysisEnabled(environment) && environment.PIU_LOCAL_SCORING_VARIANT_EXPERIMENT === "sssplus-spread") return SSSPLUS_SPREAD_LOCAL_RESULTS_PATH;
  if (localAnalysisEnabled(environment) && environment.PIU_LOCAL_SCORING_VARIANT_EXPERIMENT === "sssplus-median-v3") return SSSPLUS_STABLE_SCALE_LOCAL_RESULTS_PATH;
  if (localAnalysisEnabled(environment) && environment.PIU_LOCAL_SCORING_VARIANT_EXPERIMENT === "sssplus-median-v2") return SSSPLUS_REFINED_ANCHORS_LOCAL_RESULTS_PATH;
  if (localAnalysisEnabled(environment) && environment.PIU_LOCAL_SCORING_VARIANT_EXPERIMENT === "sssplus-median") return SSSPLUS_MEDIAN_LOCAL_RESULTS_PATH;
  if (localAnalysisEnabled(environment) && environment.PIU_LOCAL_SCORING_VARIANT_EXPERIMENT === "sssplus") return SSSPLUS_LOCAL_RESULTS_PATH;
  if (localAnalysisEnabled(environment) && environment.PIU_LOCAL_SCORING_VARIANT_EXPERIMENT === "sss-centered") return CENTERED_SSS_LOCAL_RESULTS_PATH;
  if (localAnalysisEnabled(environment) && environment.PIU_LOCAL_SCORING_VARIANT_EXPERIMENT === "sss-refined") return REFINED_SSS_LOCAL_RESULTS_PATH;
  if (localAnalysisEnabled(environment) && environment.PIU_LOCAL_SCORING_VARIANT_EXPERIMENT === "sss") return SSS_LOCAL_RESULTS_PATH;
  const experiment = localAnalysisEnabled(environment) ? environment.PIU_LOCAL_PUMBILITY_EXPERIMENT : undefined;
  return experiment === "hybrid" ? HYBRID_PUMBILITY_LOCAL_RESULTS_PATH
    : experiment === "average" ? AVERAGE_PUMBILITY_LOCAL_RESULTS_PATH : COMBINED_LOCAL_RESULTS_PATH;
}

export class LocalAnalysisNotFoundError extends Error {}
export class LocalAnalysisValidationError extends Error {}

export function localAnalysisEnabled(
  environment: Readonly<Record<string, string | undefined>> = process.env,
): boolean {
  return environment.PIU_LOCAL_ANALYSIS === "1";
}

function containsForbiddenKey(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsForbiddenKey);
  if (!value || typeof value !== "object") return false;
  return Object.entries(value).some(
    ([key, child]) => FORBIDDEN_KEYS.has(key) || containsForbiddenKey(child),
  );
}

function isFolderScale(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value)
    && value >= 0 && value <= 1
    && Math.abs(value * 100 - Math.round(value * 100)) < 1e-8;
}

function hasValidFolderScales(payload: Partial<AnalysisPayload>): boolean {
  const calibration = payload.summary?.method.scoreProfileCalibration as {
    folderScales?: Record<string, unknown>;
  } | undefined;
  const scales = calibration?.folderScales;
  if (!scales || typeof scales !== "object" || Array.isArray(scales)) return false;
  for (const mode of ["Single", "Double"] as const) {
    const folders = scales[mode];
    if (!folders || typeof folders !== "object" || Array.isArray(folders)) return false;
    if (Object.entries(folders).some(([level, scale]) => !/^\d+$/.test(level) || !isFolderScale(scale))) return false;
    const modeScales = folders as Record<string, number>;
    const charts = (mode === "Single" ? payload.singles : payload.doubles)?.filter((chart) =>
      payload.schemaVersion !== LOCAL_OFFICIAL_ANALYSIS_SCHEMA_VERSION
      || chart.level < (mode === "Single" ? 23 : 25));
    if (charts?.some((chart) => chart.estimatedDifficulty != null
      && (!isFolderScale(chart.scoringDifficultyScale)
        || chart.scoringDifficultyScale !== modeScales[String(chart.level)]))) return false;
  }
  return true;
}

function isOfficialScale(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

function pumbilityReference(payload: Partial<AnalysisPayload>, scoring: number, clearing: number): number {
  return aggregatePumbility(resolvePumbilityPolicy(payload)!.aggregation, scoring, clearing);
}

function hasValidPumbilityMethod(payload: Partial<AnalysisPayload>): boolean {
  const policy = resolvePumbilityPolicy(payload);
  if (!policy) return false;
  const rows = [...(payload.singles ?? []), ...(payload.doubles ?? [])];
  return rows.every((row) => {
    const c = row.tierMetrics?.clearing.estimatedDifficulty;
    const p = row.tierMetrics?.pumbility;
    if (!p || (policy.requiresRowMarker ? p.aggregation !== policy.aggregation
      : p.aggregation !== undefined)) return false;
    if (row.estimatedDifficulty === null || c == null) return p.estimatedDifficulty === null
      && (payload.schemaVersion !== HYBRID_INITIAL_SCHEMA || p.evidenceStatus === "Unrated");
    if (payload.schemaVersion === HYBRID_INITIAL_SCHEMA) {
      const statuses = ["Unrated", "Insufficient", "Provisional", "Published"];
      const weakest = Math.min(statuses.indexOf(row.evidenceStatus), statuses.indexOf(row.tierMetrics!.clearing.evidenceStatus));
      if (weakest < 0 || p.evidenceStatus !== statuses[weakest]) return false;
    }
    return Number.isFinite(row.estimatedDifficulty) && Number.isFinite(c)
      && typeof p.estimatedDifficulty === "number"
      && Math.abs(p.estimatedDifficulty - aggregatePumbility(policy.aggregation, row.estimatedDifficulty, c)) < .000002;
  });
}

function isCentralRange(spread: OfficialScoringSpread | null | undefined, level: number,
  minimumCharts: number, fallbackScale: number, maximumScale: number | null): boolean {
  if (!spread || typeof spread.scale !== "number" || !Number.isFinite(spread.scale) || spread.scale <= 0
    || (maximumScale !== null && spread.scale > maximumScale)
    || !Number.isInteger(spread.supportedCharts) || spread.supportedCharts < 0
    || !["folder", "neighbor-folder", "fallback"].includes(spread.basis)) return false;
  if (spread.basis === "fallback") {
    return spread.referenceLevel === null && spread.referenceSpread === null && spread.scale === fallbackScale
      && spread.lowerFence === null && spread.upperFence === null && spread.supportedCharts < minimumCharts;
  }
  if (!Number.isInteger(spread.referenceLevel)
    || typeof spread.referenceSpread !== "number" || !Number.isFinite(spread.referenceSpread) || spread.referenceSpread < 0) return false;
  if (maximumScale === null && spread.referenceSpread > 0) {
    // Both chart diagnostics are rounded to six decimals. Check the product so
    // dividing by a small rounded spread cannot amplify serialization error.
    const roundingTolerance = .0000005 * (spread.scale + spread.referenceSpread) + 1e-12;
    if (Math.abs(spread.scale * spread.referenceSpread - .45) > roundingTolerance) return false;
  } else if (Math.abs(spread.scale - (spread.referenceSpread > 0
    ? Math.min(maximumScale ?? Infinity, .45 / spread.referenceSpread) : 1)) > .000003) return false;
  if (spread.basis === "neighbor-folder") {
    return spread.referenceLevel !== level && spread.supportedCharts < minimumCharts
      && spread.lowerFence === null && spread.upperFence === null;
  }
  return spread.referenceLevel === level && spread.supportedCharts >= minimumCharts
    && typeof spread.lowerFence === "number" && Number.isFinite(spread.lowerFence)
    && typeof spread.upperFence === "number" && Number.isFinite(spread.upperFence)
    && spread.lowerFence <= spread.upperFence;
}

function hasValidOfficialTiers(payload: Partial<AnalysisPayload>, validateScoring = true, validateClearing = true): boolean {
  const method = payload.summary?.method.officialTiers;
  if (!method || method.version !== 15 || method.enabled !== true
    || method.source !== "piuscores-official" || method.mix !== "Phoenix2"
    || (method.asOf !== null && typeof method.asOf !== "string")
    || method.minimumLevels?.Single !== 23 || method.minimumLevels?.Double !== 25
    || method.capPolicy !== "clearing-folder-minimum"
    || (validateScoring && (method.scoring?.metric !== "equal-player-score-gaps" || method.scoring.minimumOtherCharts !== 3
    || method.scoring.sparsePolicy !== "provisional-same-level" || method.scoring.aggregation !== "equal-player-mean"
    || method.scoring.calibration?.method !== "shared-linear-player-gaps"
    || method.scoring.calibration.version !== 6
    || method.scoring.calibration.minimumLevels?.Single !== 23 || method.scoring.calibration.minimumLevels?.Double !== 25
    || method.scoring.calibration.minimumSupportedCharts !== 8
    || method.scoring.calibration.outlierIqrMultiplier !== 2
    || method.scoring.calibration.referenceQuantile !== .90 || method.scoring.calibration.targetHalfWidth !== .45
    || method.scoring.calibration.maximumScale !== 1 || method.scoring.calibration.rangePolicy !== "unbounded"
    || method.scoring.calibration.minimumReferencePlayers !== 10 || method.scoring.calibration.outlierUse !== "diagnostic-only" ))
    || method.clearing?.skillMetric !== "official-clearer-ability"
    || method.clearing.minimumLevels?.Single !== 25 || method.clearing.minimumLevels?.Double !== 26
    || method.clearing.difficultyDeltaScale !== 0.70
    || method.clearing.normalization !== "folder-median-player-ability"
    || method.clearing.percentile !== 0.20
    || method.clearing.playerSkill?.method !== "leave-one-chart-out-top-official-levels"
    || method.clearing.playerSkill.topCharts !== 25 || method.clearing.playerSkill.minimumOtherCharts !== 25
    || method.clearing.playerSkill.historyMinimumLevels?.Single !== 22
    || method.clearing.playerSkill.historyMinimumLevels?.Double !== 23
    || method.clearing.shrinkage?.priorPlayers !== 20
    || method.clearing.calibration?.method !== "robust-folder-spread"
    || method.clearing.calibration.version !== 4
    || method.clearing.calibration.referenceQuantile !== .90 || method.clearing.calibration.targetHalfWidth !== .45
    || method.clearing.calibration.maximumScale !== null || method.clearing.calibration.rangePolicy !== "unbounded"
    || method.clearing.calibration.minimumSupportedCharts !== 5 || method.clearing.calibration.minimumReferencePlayers !== 10
    || method.clearing.calibration.outlierUse !== "diagnostic-only") return false;
  const scales = method.scoring?.calibration?.folderScales;
  for (const mode of ["Single", "Double"] as const) {
    const folders = scales?.[mode];
    if (validateScoring && (!folders || typeof folders !== "object" || Array.isArray(folders)
      || Object.entries(folders).some(([level, scale]) => !/^\d+$/.test(level) || !isOfficialScale(scale)))) return false;
    const modeCharts = (mode === "Single" ? payload.singles : payload.doubles) ?? [];
    for (const chart of modeCharts) {
      if (chart.level < method.minimumLevels[mode]) {
        if (chart.officialEvidence) return false;
        continue;
      }
      const evidence = chart.officialEvidence;
      const clearing = chart.tierMetrics?.clearing;
      const pumbility = chart.tierMetrics?.pumbility;
      if (!validateScoring && !evidence) {
        // Charts newly added to the current catalog have no frozen Clearing baseline.
        const emptyFields = ["estimatedDifficulty", "difficultyDelta", "levelRank", "levelComparisonCharts", "effectBandRank", "effectBand"] as const;
        const allowedFields = new Set([...emptyFields, "evidenceStatus", "clearCount", "ratedClearCount", "missingSkillCount"]);
        if (chart.scoringMethod !== "official-cross-level-v1" || !clearing || !pumbility
          || (chart.officialContributors !== undefined && chart.officialContributors !== 0)
          || clearing.clearCount !== 0 || clearing.ratedClearCount !== 0 || clearing.missingSkillCount !== 0
          || clearing.evidenceStatus !== "Unrated" || pumbility.evidenceStatus !== "Unrated"
          || emptyFields.some((field) => clearing[field] !== null || pumbility[field] !== null)
          || Object.keys(clearing).some((field) => !allowedFields.has(field))) return false;
        continue;
      }
      if (!evidence || evidence.source !== "piuscores-official"
        || (evidence.asOf !== null && typeof evidence.asOf !== "string")
        || !Number.isInteger(evidence.rawRowCount) || evidence.rawRowCount < 0
        || typeof evidence.possiblyTruncated !== "boolean"
        || !["available", "possibly-truncated", "missing"].includes(evidence.status)
        || (evidence.unavailableReason !== null && typeof evidence.unavailableReason !== "string")
        || ((evidence.rawRowCount >= 300 || (evidence.maxRank ?? 0) >= 300) && !evidence.possiblyTruncated)
        || evidence.possiblyTruncated !== (evidence.status === "possibly-truncated")
        || !clearing || !pumbility
        || !Number.isInteger(chart.officialContributors) || chart.officialContributors! < 0
        || (validateScoring && (!Number.isInteger(chart.scoringPlayerCount) || chart.scoringPlayerCount! < 0
        || chart.scoringPlayerCount! > chart.officialContributors!
        || pumbility.scoringSupportCount !== chart.scoringPlayerCount
        || chart.nContributors !== chart.scoringPlayerCount
        || (chart.scoringPlayerCount === 0 ? chart.scoringMeanGap !== null
          : !(typeof chart.scoringMeanGap === "number" && Number.isFinite(chart.scoringMeanGap)))
        || !Number.isInteger(chart.scoringMinimumOtherCharts) || chart.scoringMinimumOtherCharts! < 1 || chart.scoringMinimumOtherCharts! > 3
        || !Number.isInteger(chart.scoringComparisonCharts) || chart.scoringComparisonCharts! < 1
        || !Number.isInteger(chart.scoringComponentCount) || chart.scoringComponentCount! < 1
        || chart.scoringProvisional !== (chart.scoringMinimumOtherCharts! < 3 || chart.scoringComponentCount! > 1 || chart.scoringPlayerCount! < 10) ))
        || (validateClearing && (!("folderReferenceSkill" in clearing)
        || !Number.isInteger(clearing.ratedClearCount) || clearing.ratedClearCount < 0
        || pumbility.clearingSupportCount !== clearing.ratedClearCount))
        || chart.scoringScoreProfile != null) return false;
      if (validateScoring && chart.estimatedDifficulty != null && (!isOfficialScale(chart.scoringDifficultyScale)
        || Math.abs(chart.scoringDifficultyScale - folders[String(chart.level)]) > .000001)) return false;
      if (validateScoring && chart.scoringPlayerCount === 0 && [chart, pumbility].some((metric) =>
        metric.estimatedDifficulty !== null || metric.difficultyDelta !== null
        || metric.evidenceStatus !== "Unrated")) return false;
      if (validateScoring) {
      const scoreSpread = chart.scoringSpreadCalibration;
      if (chart.scoringPlayerCount === 0) {
        if (chart.scoringExtremeOutlier !== false || !chart.scoringUnratedReason) return false;
      } else {
        if (!scoreSpread || !isCentralRange(scoreSpread, chart.level, 8, .4, 1)
          || Math.abs(scoreSpread.scale - chart.scoringDifficultyScale!) > .000001
          || chart.scoringComparisonCharts! < 2 || chart.scoringUnratedReason !== null
          || typeof chart.estimatedDifficulty !== "number" || !Number.isFinite(chart.estimatedDifficulty)
          || typeof chart.scoringGapStdDev !== "number" || !Number.isFinite(chart.scoringGapStdDev) || chart.scoringGapStdDev < 0
          || (chart.scoringProvisional && chart.evidenceStatus === "Published")) return false;
        const raw = chart.scoringMeanGap! / 10000;
        const extreme = !chart.scoringProvisional && scoreSpread.basis === "folder" && chart.scoringPlayerCount! >= 10
          && (raw < scoreSpread.lowerFence! || raw > scoreSpread.upperFence!);
        if (chart.scoringExtremeOutlier !== extreme) return false;
        const delta = scoreSpread.scale * raw;
        if (Math.abs(chart.estimatedDifficulty - (chart.level + .5 + delta)) > .00003) return false;
      }
      for (const side of ["Low", "High"] as const) {
        const gap = chart[`scoringGapCi95${side}`];
        const interval = chart[`difficultyCi95${side}`];
        if (chart.scoringPlayerCount! < 5) {
          if (gap !== null || interval !== null) return false;
        } else if (typeof gap !== "number" || !Number.isFinite(gap)
          || typeof interval !== "number" || !Number.isFinite(interval)
          || Math.abs(interval - (chart.level + .5 + chart.scoringDifficultyScale! * gap / 10000)) > .00003) return false;
      }
      if ((chart.scoringGapCi95Low ?? 0) > (chart.scoringGapCi95High ?? 0)) return false;
      }
      if (!validateClearing) continue;
      if (chart.level < method.clearing.minimumLevels[mode]) {
        if (clearing.skillMetric != null || !("q10Skill" in clearing) || "q20Skill" in clearing
          || clearing.defaultedAtCap != null || clearing.spreadCalibration != null
          || !Number.isInteger(clearing.clearCount) || clearing.clearCount < clearing.ratedClearCount
          || clearing.missingSkillCount !== clearing.clearCount - clearing.ratedClearCount) return false;
        if (chart.estimatedDifficulty === null || clearing.estimatedDifficulty === null) {
          if (pumbility.estimatedDifficulty !== null) return false;
        } else if (typeof pumbility.estimatedDifficulty !== "number"
          || Math.abs(pumbility.estimatedDifficulty - pumbilityReference(payload, chart.estimatedDifficulty, clearing.estimatedDifficulty)) > .000002) return false;
        if (evidence.status === "missing" && !evidence.unavailableReason) return false;
        continue;
      }
      if (clearing.skillMetric !== "official-clearer-ability"
        || clearing.clearCount !== (evidence.possiblyTruncated ? 300 : chart.officialContributors)
        || clearing.ratedClearCount > chart.officialContributors!
        || clearing.missingSkillCount !== chart.officialContributors! - clearing.ratedClearCount
        || (clearing.ratedClearCount === 0 ? clearing.q20Skill !== null
          : !(typeof clearing.q20Skill === "number" && Number.isFinite(clearing.q20Skill)))
        || clearing.defaultedAtCap !== evidence.possiblyTruncated) return false;
      if (evidence.status === "missing") {
        if (!evidence.unavailableReason
          || (validateScoring ? [chart, clearing, pumbility] : [clearing]).some((metric) => metric.estimatedDifficulty !== null
            || metric.difficultyDelta !== null || metric.levelRank !== null
            || metric.effectBandRank !== null || metric.evidenceStatus !== "Unrated")) return false;
      }
      if (evidence.possiblyTruncated) {
        const uncapped = modeCharts.filter((other) => other.level === chart.level
          && other.officialEvidence?.status === "available"
          && (other.tierMetrics?.clearing.clearCount ?? 0) > 0
          && other.tierMetrics?.clearing.estimatedDifficulty != null)
          .map((other) => other.tierMetrics!.clearing.estimatedDifficulty!);
        const expected = uncapped.length ? Math.min(...uncapped) : chart.level + 0.5;
        if (clearing.estimatedDifficulty !== expected || clearing.capDefaultDifficulty !== expected
          || clearing.capDefaultBasis !== (uncapped.length ? "uncapped-folder-minimum" : "folder-midpoint-no-uncapped-charts")) return false;
      } else if (clearing.capDefaultDifficulty !== null || clearing.capDefaultBasis !== null) return false;
      const coverage = chart.officialContributors ? clearing.ratedClearCount / chart.officialContributors : 0;
      const shrinkage = clearing.ratedClearCount / (clearing.ratedClearCount + 20) * coverage;
      if (typeof clearing.abilityCoverage !== "number" || !Number.isFinite(clearing.abilityCoverage)
        || Math.abs(clearing.abilityCoverage - coverage) > 0.000001
        || typeof clearing.shrinkageWeight !== "number" || !Number.isFinite(clearing.shrinkageWeight)
        || Math.abs(clearing.shrinkageWeight - shrinkage) > 0.000001) return false;
      if (!evidence.possiblyTruncated && clearing.ratedClearCount === 0 && clearing.estimatedDifficulty !== null) return false;
      const spread = clearing.spreadCalibration;
      if (!spread || !isCentralRange(spread, chart.level, 5, 1, null)
        || typeof clearing.extremeOutlier !== "boolean") return false;
      if ((evidence.possiblyTruncated || clearing.estimatedDifficulty === null) && clearing.extremeOutlier) return false;
      if (!evidence.possiblyTruncated && clearing.estimatedDifficulty !== null) {
        if (typeof clearing.folderReferenceSkill !== "number" || !Number.isFinite(clearing.folderReferenceSkill)) return false;
        const factor = .70 * shrinkage;
        const raw = factor * (clearing.q20Skill! - clearing.folderReferenceSkill);
        const supported = spread.basis === "folder" && clearing.ratedClearCount >= 20 && coverage >= .5
          && clearing.q20SkillCi95Low != null && clearing.q20SkillCi95High != null;
        const extreme = supported && ((raw < spread.lowerFence!
          && factor * (clearing.q20SkillCi95High! - clearing.folderReferenceSkill) < spread.lowerFence!)
          || (raw > spread.upperFence! && factor * (clearing.q20SkillCi95Low! - clearing.folderReferenceSkill) > spread.upperFence!));
        if (clearing.extremeOutlier !== extreme) return false;
        const delta = spread.scale * raw;
        // Diagnostics are rounded to six decimals in the public artifact.
        if (Math.abs(clearing.estimatedDifficulty - (chart.level + .5 + delta)) > .00003) return false;
      }
      for (const side of ["Low", "High"] as const) {
        const ability = clearing[`q20SkillCi95${side}`];
        const interval = clearing[`difficultyCi95${side}`];
        if (evidence.possiblyTruncated || clearing.estimatedDifficulty === null || ability == null) {
          if (interval !== null) return false;
        } else if (typeof interval !== "number" || !Number.isFinite(interval)
          || Math.abs(interval - (chart.level + .5 + spread.scale * .70 * shrinkage * (ability - clearing.folderReferenceSkill!))) > .00003) return false;
      }
      if (validateScoring && chart.estimatedDifficulty !== null && chart.scoringPointsPerLevel !== 10000) return false;
      if (chart.officialContributors === 0 && evidence.status === "available"
        && (validateScoring ? [chart, clearing, pumbility] : [clearing]).some((metric) => metric.estimatedDifficulty !== null)) return false;
      if ((chart.estimatedDifficulty === null || clearing.estimatedDifficulty === null)
        && pumbility.estimatedDifficulty !== null) return false;
    }
  }
  return !(payload.coop ?? []).some((chart) => chart.officialEvidence);
}

function inverseOfficialCost(cost: number, curve: OfficialCrossLevelCurve): number {
  const anchors = curve.anchors;
  const first = anchors[0];
  const last = anchors[anchors.length - 1];
  const pair = cost <= first.cost ? anchors.slice(0, 2)
    : cost >= last.cost ? anchors.slice(-2)
      : anchors.slice(anchors.findIndex((anchor) => anchor.cost > cost) - 1,
        anchors.findIndex((anchor) => anchor.cost > cost) + 1);
  const [left, right] = pair;
  const growth = Math.log((right.cost - curve.offset) / (left.cost - curve.offset))
    / (right.difficulty - left.difficulty);
  if (cost < first.cost) return first.difficulty + (cost - first.cost) / ((first.cost - curve.offset) * growth);
  if (cost > last.cost) return last.difficulty + (cost - last.cost) / ((last.cost - curve.offset) * growth);
  return left.difficulty + Math.log((cost - curve.offset) / (left.cost - curve.offset)) / growth;
}

function validOfficialCurve(curve: OfficialCrossLevelCurve | null | undefined): boolean {
  if (!curve || curve.version !== 1 || curve.tailPolicy !== "tangent-linear"
    || curve.referenceMinimumPlayers !== 10 || curve.referenceMinimumCharts !== 5
    || !Number.isFinite(curve.offset) || !Array.isArray(curve.anchors) || curve.anchors.length < 3
    || !Array.isArray(curve.supportedRange) || curve.supportedRange.length !== 2
    || !curve.fit || !Number.isFinite(curve.fit.growth) || curve.fit.growth <= 0
    || !Number.isFinite(curve.fit.normalizedOffset) || !Number.isFinite(curve.fit.normalizedAmplitude)
    || curve.fit.normalizedAmplitude <= 0 || !Number.isFinite(curve.fit.normalizedMse)
    || curve.fit.normalizedMse < 0 || typeof curve.fit.boundHit !== "boolean") return false;
  return curve.anchors.every((anchor, index, anchors) => Number.isInteger(anchor.level) && anchor.level >= 20
    && anchor.difficulty === anchor.level + .5 && Number.isFinite(anchor.cost) && anchor.cost > curve.offset
    && Number.isInteger(anchor.referenceCharts) && anchor.referenceCharts >= 5
    && Array.isArray(anchor.chartIds) && anchor.chartIds.length === anchor.referenceCharts
    && anchor.chartIds.every((id) => typeof id === "string")
    && new Set(anchor.chartIds).size === anchor.chartIds.length
    && (index === 0 ? anchor.level === 20 : anchor.level > anchors[index - 1].level && anchor.cost > anchors[index - 1].cost))
    && curve.supportedRange[0] === curve.anchors[0].difficulty
    && curve.supportedRange[1] === curve.anchors[curve.anchors.length - 1].difficulty;
}

function hasValidCrossLevelScoring(payload: Partial<AnalysisPayload>, hybrid = false): boolean {
  const method = payload.summary?.method.officialCrossLevelScoring;
  const scoring = payload.summary?.method.scoring as Record<string, unknown> | undefined;
  if (!method || method.version !== 1 || method.minimumOfficialLevel !== 20
    || method.intervalMethod !== "conditional-player-bootstrap-fixed-fit-v1"
    || !/^[0-9a-f]{64}$/.test(method.manifestSha256)
    || !method.sourceDates || Object.keys(method.sourceDates).length !== 2
    || Object.values(method.sourceDates).some((date) => typeof date !== "string" || !Number.isFinite(Date.parse(date)))
    || !method.calibration || scoring?.version !== 1
    || scoring.population !== "official-max-per-player-chart"
    || scoring.calibration !== "official-cross-level-median-anchored-exponential"
    || scoring.minimumOfficialLevel !== 20) return false;
  for (const mode of ["Single", "Double"] as const) {
    const charts = ((mode === "Single" ? payload.singles : payload.doubles) ?? []).filter((chart) => !hybrid || chart.level >= 20);
    const curve = method.calibration[mode];
    if (charts.some((chart) => chart.level >= 20) ? !validOfficialCurve(curve) : curve !== null && !validOfficialCurve(curve)) return false;
    for (const chart of charts) {
      const pumbility = chart.tierMetrics?.pumbility;
      const clearing = chart.tierMetrics?.clearing;
      const count = chart.scoringPlayerCount;
      if (chart.scoringMethod !== "official-cross-level-v1" || !pumbility || !clearing
        || !Number.isInteger(count) || count! < 0 || chart.nContributors !== count
        || pumbility.scoringSupportCount !== count || pumbility.clearingSupportCount !== clearing.ratedClearCount
        || !Number.isInteger(chart.officialScoringContributors) || chart.officialScoringContributors! < count!
        || chart.scoringScoreProfile != null || chart.scoringDifficultyScale != null || chart.scoringSpreadCalibration != null) return false;
      if (chart.level < 20) {
        if (chart.officialScoringEvidence !== null || count !== 0 || chart.officialScoringContributors !== 0
          || chart.estimatedDifficulty !== null || chart.difficultyDelta !== null || chart.scoringChartCost !== null
          || chart.evidenceStatus !== "Unrated" || !chart.scoringUnratedReason || pumbility.estimatedDifficulty !== null) return false;
        continue;
      }
      const evidence = chart.officialScoringEvidence;
      if (!evidence || evidence.mergedPlayerCount !== chart.officialScoringContributors
        || !evidence.sourceDates || Object.keys(evidence.sourceDates).length !== Object.keys(method.sourceDates).length
        || Object.entries(method.sourceDates).some(([mix, date]) => evidence.sourceDates[mix] !== date)
        || !evidence.sourceCounts || Object.values(evidence.sourceCounts).some((value) => !Number.isInteger(value) || value < 0)
        || !evidence.sourceStates || (Object.keys(evidence.sourceStates).length !== 2
          && !(Object.keys(evidence.sourceStates).length === 0 && evidence.mergedPlayerCount === 0
            && evidence.comparability === "no-evidence" && Object.values(evidence.sourceCounts).every((count) => count === 0)))
        || (Object.keys(evidence.sourceStates).length > 0 && Object.keys(method.sourceDates).some((mix) => {
          const state = evidence.sourceStates[mix];
          return !state || !["available", "empty", "absent-from-catalog", "not-mirrored"].includes(state.status)
            || (state.asOf !== null && (typeof state.asOf !== "string" || !Number.isFinite(Date.parse(state.asOf))))
            || !Number.isInteger(state.rawRowCount) || state.rawRowCount < 0
            || state.rawRowCount !== evidence.sourceCounts[mix]
            || !Number.isInteger(state.maxRank) || state.maxRank < 0
            || (state.cutoffScore !== null && (!Number.isInteger(state.cutoffScore) || state.cutoffScore < 0 || state.cutoffScore > 1000000))
            || typeof state.possiblyTruncated !== "boolean"
            || (state.status !== "available" && (state.rawRowCount !== 0 || state.possiblyTruncated));
        }))
        || typeof evidence.comparability !== "string" || typeof evidence.anchorEligible !== "boolean"
        || !(evidence.component === null || Number.isInteger(evidence.component))
        || typeof chart.scoringProvisional !== "boolean") return false;
      if (chart.estimatedDifficulty === null) {
        if (!chart.scoringUnratedReason || chart.difficultyDelta !== null || chart.evidenceStatus !== "Unrated"
          || pumbility.estimatedDifficulty !== null) return false;
      } else {
        if (!curve || count! < 5 || chart.scoringMainComponent !== true || !Number.isFinite(chart.scoringChartCost)
          || !Number.isFinite(chart.estimatedDifficulty) || chart.scoringUnratedReason !== null
          || Math.abs(chart.estimatedDifficulty - inverseOfficialCost(chart.scoringChartCost!, curve)) > .00003
          || Math.abs(chart.difficultyDelta! - (chart.estimatedDifficulty - chart.level - .5)) > .000002
          || (chart.scoringProvisional && chart.evidenceStatus === "Published")) return false;
      }
      for (const side of ["Low", "High"] as const) {
        const cost = chart[`scoringChartCostCi95${side}`];
        const interval = chart[`difficultyCi95${side}`];
        if (chart.estimatedDifficulty === null || count! < 5) {
          if (cost !== null || interval !== null) return false;
        } else if (!curve || typeof cost !== "number" || !Number.isFinite(cost)
          || typeof interval !== "number" || !Number.isFinite(interval)
          || Math.abs(interval - inverseOfficialCost(cost, curve)) > .00003) return false;
      }
      if ((chart.difficultyCi95Low ?? 0) > (chart.difficultyCi95High ?? 0)) return false;
      if (chart.estimatedDifficulty === null || clearing.estimatedDifficulty === null) {
        if (pumbility.estimatedDifficulty !== null) return false;
      } else if (typeof pumbility.estimatedDifficulty !== "number"
        || Math.abs(pumbility.estimatedDifficulty - pumbilityReference(payload, chart.estimatedDifficulty, clearing.estimatedDifficulty)) > .000002) return false;
    }
  }
  return !(payload.coop ?? []).some((chart) => chart.scoringMethod || chart.officialScoringEvidence);
}

function inverseClearingThreshold(threshold: number, curve: CrossLevelClearingCurve): number {
  const a = curve.anchors;
  let i = a.findIndex((anchor) => anchor.threshold > threshold) - 1;
  if (threshold >= a[a.length - 1].threshold) i = a.length - 2;
  i = Math.max(0, i);
  return a[i].difficulty + (a[i + 1].level - a[i].level) * (threshold - a[i].threshold)
    / (a[i + 1].threshold - a[i].threshold);
}

export const CROSS_LEVEL_CLEARING_POLICY = {
  version: 1, source: "piuscores-submitted", minimumOfficialLevel: 16,
  skillMethod: { version: 2, topClearCount: 30, minimumScoreRecords: 100, minimumUniqueClears: 30,
    difficultyBasis: "current-official-level", scope: "same-mode-current-catalog", recordDeduplication: "account-chart-across-mixes" },
  percentile: .10, percentileMethod: "linear", calibrationMethod: "median-anchored-piecewise-linear",
  calibrationVersion: 1, referenceMinimumClearers: 20, referenceMinimumCharts: 5,
  minimumAnchors: 3, tailPolicy: "boundary-linear", intervalMethod: "none",
} as const;

export const HYBRID_CLEARING_POLICY = {
  ...CROSS_LEVEL_CLEARING_POLICY, version: 2, minimumCalibrationLevel: 20,
  calibrationMethod: "20plus-median-anchored-piecewise-linear-with-lower-folder-fallback",
  lowerLevelMethod: { method: "folder-centered-q10", difficultyDeltaScale: .70, referenceMinimumClearers: 20, referenceMinimumCharts: 5 },
} as const;

export const HYBRID_TIER_POLICY = {
  version: 1, minimumDisplayLevel: 16,
  scoring: { below20: "piuscores-submitted-score-profile-v2", atLeast20: "official-cross-level-v1", officialMinimumLevel: 20, missingOfficialPolicy: "unrated" },
  clearing: { below20: "submitted-folder-centered-clearing-v1", atLeast20: "submitted-cross-level-clearing-v2", minimumCalibrationLevel: 20 },
} as const;

export const SUBMITTED_PROFILE_POLICY_V2 = {
  version: 2, population: "combined", calibration: "folder-scaled-score-profile", percentiles: [.5, .75, .9], profileWeights: [1, 1, 2],
  percentileMethod: "linear interpolation", scoreUnit: 10000, referenceSmoothing: 4, minimumReferencePlayers: 20,
  minimumReferenceCharts: 5, fullReferenceWeightCharts: 20, folderCenter: "median-profile-match", scaleStep: .01, maximumScale: 1,
  preferredCentralWidth: 1, spreadQuantiles: [.1, .9], minimumSpreadCharts: 10, minimumSpreadMedianPlayers: 10,
  spreadReliabilityCharts: 30, spreadReliabilityPlayers: 20, neighborSmoothing: .05, rarityThreshold: 10, rarityPenalty: .05,
} as const;

function matchesClearingPolicy(value: unknown, expected: unknown): boolean {
  return expected !== null && typeof expected === "object"
    ? value !== null && typeof value === "object" && Object.entries(expected).every(([key, item]) =>
      matchesClearingPolicy((value as Record<string, unknown>)[key], item))
    : value === expected;
}

function hasValidCrossLevelClearing(payload: Partial<AnalysisPayload>, hybrid = false): boolean {
  const method = payload.summary?.method.crossLevelClearing;
  const tiers = payload.summary?.method.tierMetrics as { version?: number; clearing?: unknown } | undefined;
  const currentPolicy = hybrid ? HYBRID_CLEARING_POLICY : CROSS_LEVEL_CLEARING_POLICY;
  const legacy = method?.skillMethod?.version === 1 && method.skillMethod.minimumScoreRecords === 200;
  const policy = legacy ? { ...currentPolicy, skillMethod: { ...currentPolicy.skillMethod, version: 1, minimumScoreRecords: 200 } } : currentPolicy;
  if (!method || !matchesClearingPolicy(method, policy)
    || tiers?.version !== (hybrid ? 12 : 11) || !matchesClearingPolicy(tiers.clearing, policy)
    || ![26, 41, 42].includes(method.baseScoringSchemaVersion)
    || (hybrid && method.baseScoringSchemaVersion !== 42)
    || !method.inputHashes || Object.keys(method.inputHashes).sort().join() !== "currentCatalog,phoenix1,phoenix2"
    || Object.values(method.inputHashes).some((hash) => !/^[0-9a-f]{64}$/.test(hash))
    || !method.sourceDates || Object.keys(method.sourceDates).sort().join() !== "phoenix1,phoenix2"
    || Object.values(method.sourceDates).some((date) => date !== null && (typeof date !== "string" || !Number.isFinite(Date.parse(date))))
    || !method.calibration) return false;
  for (const mode of ["Single", "Double"] as const) {
    const rows = (mode === "Single" ? payload.singles : payload.doubles) ?? [];
    const curve = method.calibration[mode];
    const needsCurve = rows.some((row) => !hybrid || row.level >= 20);
    if (needsCurve && (!curve || curve.version !== 1 || curve.tailPolicy !== "boundary-linear"
      || !Array.isArray(curve.anchors) || curve.anchors.length < 3
      || !Array.isArray(curve.supportedRange) || curve.supportedRange.length !== 2)) return false;
    if (!needsCurve && curve !== null) return false;
    if (curve && curve.anchors.some((a, i, anchors) => !Number.isInteger(a.level) || a.level < (hybrid ? 20 : 16)
      || a.difficulty !== a.level + .5 || !Number.isFinite(a.threshold)
      || !Number.isInteger(a.referenceCharts) || a.referenceCharts < 5 || !Array.isArray(a.chartIds)
      || a.chartIds.length !== a.referenceCharts || new Set(a.chartIds).size !== a.chartIds.length
      || a.chartIds.some((id) => typeof id !== "string")
      || (i === 0 && hybrid && a.level !== 20)
      || (i > 0 && (a.level <= anchors[i - 1].level || a.threshold <= anchors[i - 1].threshold)))) return false;
    if (curve && (curve.supportedRange[0] !== curve.anchors[0].difficulty
      || curve.supportedRange[1] !== curve.anchors[curve.anchors.length - 1].difficulty)) return false;
    const lowerReferences = hybrid ? method.lowerReferenceSkills?.[mode] : {};
    if (hybrid && (!lowerReferences || typeof lowerReferences !== "object" || Array.isArray(lowerReferences)
      || Object.entries(lowerReferences).some(([level, a]) => !/^(16|17|18|19)$/.test(level) || a.level !== Number(level)
        || !Number.isFinite(a.threshold) || !Number.isInteger(a.referenceCharts) || a.referenceCharts < 5
        || !Array.isArray(a.chartIds) || a.chartIds.length !== a.referenceCharts || new Set(a.chartIds).size !== a.referenceCharts))) return false;
    for (const row of rows) {
      const c = row.tierMetrics?.clearing;
      const p = row.tierMetrics?.pumbility;
      const lower = hybrid && row.level < 20;
      const reference = lower ? lowerReferences?.[String(row.level)] : curve?.anchors.find((a) => a.level === row.level);
      if (!c || !p || c.method !== (lower ? "submitted-folder-centered-clearing-v1" : hybrid ? "submitted-cross-level-clearing-v2" : "submitted-cross-level-clearing-v1") || row.level < 16
        || !Number.isInteger(c.clearCount) || !Number.isInteger(c.ratedClearCount) || c.ratedClearCount < 0
        || c.clearCount < c.ratedClearCount || c.missingSkillCount !== c.clearCount - c.ratedClearCount
        || !c.sourceCounts || Object.keys(c.sourceCounts).sort().join() !== "phoenix1,phoenix2"
        || Object.values(c.sourceCounts).some((n) => !Number.isInteger(n) || n < 0)
        || c.clearCount > c.sourceCounts.phoenix1 + c.sourceCounts.phoenix2
        || c.clearCount < Math.max(c.sourceCounts.phoenix1, c.sourceCounts.phoenix2)
        || !["comparable", "changed", "unknown", "no-evidence"].includes(c.comparability ?? "")
        || !c.qualificationExclusions || Object.values(c.qualificationExclusions).some((n) => !Number.isInteger(n) || n < 0)
        || Object.values(c.qualificationExclusions).reduce((sum, n) => sum + n, 0) !== c.missingSkillCount
        || c.anchorEligible !== (c.ratedClearCount >= 20 && c.comparability === "comparable")
        || c.difficultyCi95Low !== null || c.difficultyCi95High !== null || c.q20Skill !== undefined
        || c.defaultedAtCap !== undefined || c.spreadCalibration !== undefined
        || !Array.isArray(c.provisionalReasons) || typeof c.extrapolated !== "boolean"
        || JSON.stringify(c.supportedRange) !== JSON.stringify(lower ? null : curve?.supportedRange)
        || p.clearingSupportCount !== c.ratedClearCount || p.scoringSupportCount !== row.nContributors) return false;
      if (c.folderReferenceSkill !== (reference?.threshold ?? null)) return false;
      if (c.ratedClearCount === 0 || (lower && !reference)) {
        if ((c.ratedClearCount === 0 ? c.q10Skill !== null : typeof c.q10Skill !== "number" || !Number.isFinite(c.q10Skill))
          || c.estimatedDifficulty !== null || c.difficultyDelta !== null
          || c.evidenceStatus !== "Unrated" || p.estimatedDifficulty !== null) return false;
        if (hybrid && (!c.unratedReason || c.extrapolated || c.provisionalReasons.length)) return false;
      } else {
        const expected = lower ? row.level + .5 + .70 * (c.q10Skill! - reference!.threshold)
          : inverseClearingThreshold(c.q10Skill!, curve!);
        if (typeof c.q10Skill !== "number" || !Number.isFinite(c.q10Skill)
          || typeof c.estimatedDifficulty !== "number" || !Number.isFinite(c.estimatedDifficulty)
          || Math.abs(c.estimatedDifficulty - expected) > .0000001
          || typeof c.difficultyDelta !== "number" || Math.abs(c.difficultyDelta - c.estimatedDifficulty + row.level + .5) > .0000001) return false;
        const outside = !lower && (c.q10Skill < curve!.anchors[0].threshold || c.q10Skill > curve!.anchors[curve!.anchors.length - 1].threshold);
        const reasons = [...(c.ratedClearCount < 20 ? ["fewer-than-20-rated-clearers"] : []),
          ...(c.comparability !== "comparable" ? [`chart-comparability-${c.comparability}`] : []),
          ...(!reference ? ["unsupported-official-folder"] : []), ...(outside ? ["outside-supported-anchors"] : [])];
        const status = c.ratedClearCount < 5 ? "Insufficient" : reasons.length ? "Provisional" : "Published";
        if ((hybrid && c.unratedReason !== null) || c.extrapolated !== outside || JSON.stringify(c.provisionalReasons) !== JSON.stringify(reasons)
          || c.evidenceStatus !== status) return false;
      }
      if (row.estimatedDifficulty === null || c.estimatedDifficulty === null) {
        if (p.estimatedDifficulty !== null) return false;
      } else if (typeof p.estimatedDifficulty !== "number"
        || Math.abs(p.estimatedDifficulty - pumbilityReference(payload, row.estimatedDifficulty, c.estimatedDifficulty)) > .0000001) return false;
    }
    for (const anchor of [...(curve?.anchors ?? []), ...Object.values(lowerReferences ?? {})]) {
      const references = rows.filter((row) => row.level === anchor.level && row.tierMetrics?.clearing.anchorEligible);
      if (references.length !== anchor.referenceCharts || anchor.chartIds.some((id) => !references.some((r) => r.chartId === id))) return false;
      const thresholds = references.map((row) => row.tierMetrics!.clearing.q10Skill!).sort((a, b) => a - b);
      const middle = Math.floor(thresholds.length / 2);
      const median = thresholds.length % 2 ? thresholds[middle] : (thresholds[middle - 1] + thresholds[middle]) / 2;
      if (Math.abs(median - anchor.threshold) > .0000001) return false;
    }
    if (hybrid) for (const level of new Set(rows.map((row) => row.level))) {
      const supported = rows.filter((row) => row.level === level && row.tierMetrics?.clearing.anchorEligible).length >= 5;
      const reference = level < 20 ? lowerReferences?.[String(level)] : curve?.anchors.find((anchor) => anchor.level === level);
      if (supported !== !!reference) return false;
    }
  }
  return !(payload.coop ?? []).some((row) => row.tierMetrics?.clearing.method?.startsWith("submitted-"));
}

function hasValidHybridScoring(payload: Partial<AnalysisPayload>): boolean {
  const hybrid = payload.summary?.method.hybridTiers;
  const submitted = hybrid?.submittedScoring;
  const calibration = submitted?.scoreProfileCalibration as {
    folderScales?: Record<string, Record<string, number>>;
    folderCenters?: Record<string, Record<string, number>>;
    curves?: Record<string, unknown>;
  } | undefined;
  if (!hybrid || !matchesClearingPolicy(hybrid, HYBRID_TIER_POLICY)
    || !submitted || !matchesClearingPolicy(submitted.method, SUBMITTED_PROFILE_POLICY_V2)
    || submitted.method.difficultyDeltaScale !== undefined || submitted.method.maxTwoGradeMoves !== undefined
    || !matchesClearingPolicy(submitted.confidenceIntervals, { samples: 1000, minimumPlayers: 20,
      method: "joint score-profile bootstrap with final affine calibration, holding the curve, folder center, and selected folder scale fixed; excludes scale-selection uncertainty",
      population: "observed successful-player best scores; excludes attempt and player-selection uncertainty" })
    || !calibration?.folderScales || !calibration.folderCenters || !calibration.curves
    || !hasValidCrossLevelScoring(payload, true)) return false;
  for (const mode of ["Single", "Double"] as const) {
    const rows = ((mode === "Single" ? payload.singles : payload.doubles) ?? []).filter((row) => row.level < 20);
    const scales = calibration.folderScales[mode], centers = calibration.folderCenters[mode];
    if (!scales || !centers || !(mode in calibration.curves)
      || Object.entries(scales).some(([level, scale]) => !/^\d+$/.test(level) || !isFolderScale(scale))
      || Object.values(centers).some((center) => !Number.isFinite(center))) return false;
    for (const row of rows) {
      const p = row.tierMetrics?.pumbility, c = row.tierMetrics?.clearing;
      if (row.level < 16 || row.scoringMethod !== "submitted-score-profile-v2"
        || row.officialScoringEvidence != null || row.officialEvidence != null
        || (row.officialScoringContributors != null && row.officialScoringContributors !== 0) || row.scoringChartCost != null
        || !p || !c || !Number.isInteger(row.nContributors) || row.nContributors < 0
        || row.nPlayersScored !== row.nContributors || p.scoringSupportCount !== row.nContributors) return false;
      const raw = row.scoringProfileMatchDifficulty;
      if (raw == null) {
        if (row.estimatedDifficulty !== null || row.difficultyDelta !== null || row.evidenceStatus !== "Unrated"
          || row.scoringDifficultyScale != null || p.estimatedDifficulty !== null) return false;
      } else {
        const scale = scales[String(row.level)], center = centers[String(row.level)];
        const expected = row.level + .5 + scale * (raw - center);
        if (!Number.isFinite(raw) || !Number.isFinite(center) || !isFolderScale(scale)
          || row.scoringDifficultyScale !== scale || !Number.isFinite(row.scoringFolderReferenceDifficulty)
          || Math.abs(row.scoringFolderReferenceDifficulty! - center) > .000002
          || !Number.isFinite(row.estimatedDifficulty) || Math.abs(row.estimatedDifficulty! - expected) > .000003
          || Math.abs(row.difficultyDelta! - expected + row.level + .5) > .000003
          || !Array.isArray(row.scoringScoreProfile) || row.scoringScoreProfile.length !== 3
          || row.scoringScoreProfile.some((score, i, values) => !Number.isFinite(score) || score < 0 || score > 1000000 || (i > 0 && score < values[i - 1]))
          || row.evidenceStatus !== (row.nContributors >= 10 ? "Published" : row.nContributors >= 5 ? "Provisional" : "Insufficient")) return false;
      }
      for (const side of ["Low", "High"] as const) {
        const rawEndpoint = row[`scoringProfileMatchCi95${side}`], endpoint = row[`difficultyCi95${side}`];
        if (rawEndpoint == null) {
          if (endpoint !== null || row[`difficultyDeltaCi95${side}`] !== null) return false;
        } else {
          const expected = row.level + .5 + scales[String(row.level)] * (rawEndpoint - centers[String(row.level)]);
          if (row.nContributors < 20 || !Number.isFinite(rawEndpoint) || !Number.isFinite(endpoint)
            || Math.abs(endpoint! - expected) > .000003
            || Math.abs(row[`difficultyDeltaCi95${side}`]! - expected + row.level + .5) > .000003) return false;
        }
      }
      if ((row.difficultyCi95Low ?? 0) > (row.difficultyCi95High ?? 0)) return false;
    }
  }
  return true;
}

export function validateLocalAnalysisPayload(
  value: unknown,
  expectedMix: MixKey | "combined" = DEFAULT_MIX,
): AnalysisPayload {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new LocalAnalysisValidationError("The local analysis payload must be an object.");
  }
  const payload = value as Partial<AnalysisPayload>;
  if (expectedMix === "combined" && payload.schemaVersion === 59) {
    try {
      if (containsForbiddenKey(payload) || SECRET_PATTERN.test(JSON.stringify(payload))) throw new Error("The SSS tail aggregate contains private fields or a credential-shaped value.");
      return validateSssTailAnalysisPayload(payload as AnalysisPayload, base => validateLocalAnalysisPayload(base, expectedMix));
    } catch (error) {
      throw new LocalAnalysisValidationError(`The SSS tail aggregate is incompatible: ${error instanceof Error ? error.message : "invalid contract"}`);
    }
  }
  if (expectedMix === "combined" && payload.schemaVersion === 58) {
    try {
      if (containsForbiddenKey(payload) || SECRET_PATTERN.test(JSON.stringify(payload))) throw new Error("The scoring-goal aggregate contains private fields or a credential-shaped value.");
      return validateScoringGoalsPayload(payload as AnalysisPayload, base => validateLocalAnalysisPayload(base, expectedMix));
    } catch (error) {
      throw new LocalAnalysisValidationError(`The scoring-goal aggregate is incompatible: ${error instanceof Error ? error.message : "invalid contract"}`);
    }
  }
  if (expectedMix === "combined" && payload.schemaVersion === 57) {
    try {
      if (containsForbiddenKey(payload) || SECRET_PATTERN.test(JSON.stringify(payload))) throw new Error("The submitted SSS+ aggregate contains private fields or a credential-shaped value.");
      return validateSubmittedSssplusCalibrationPayload(payload as AnalysisPayload, base => validateLocalAnalysisPayload(base, expectedMix));
    } catch (error) {
      throw new LocalAnalysisValidationError(`The submitted SSS+ aggregate is incompatible: ${error instanceof Error ? error.message : "invalid contract"}`);
    }
  }
  if (expectedMix === "combined" && payload.schemaVersion === 56) {
    try {
      if (containsForbiddenKey(payload) || SECRET_PATTERN.test(JSON.stringify(payload))) throw new Error("The source-aware aggregate contains private fields or a credential-shaped value.");
      return validateSourceAwarePayload(payload as AnalysisPayload, base => validateLocalAnalysisPayload(base, expectedMix));
    } catch (error) {
      throw new LocalAnalysisValidationError(`The source-aware aggregate is incompatible: ${error instanceof Error ? error.message : "invalid contract"}`);
    }
  }
  if (expectedMix === "combined" && payload.schemaVersion === 55) {
    try {
      if (containsForbiddenKey(payload) || SECRET_PATTERN.test(JSON.stringify(payload))) throw new Error("The combined Clearing aggregate contains private player fields or a credential-shaped value.");
      return validateCombinedClearingPayload(payload as AnalysisPayload, base => validateLocalAnalysisPayload(base, expectedMix));
    } catch (error) {
      throw new LocalAnalysisValidationError(`The combined Clearing aggregate is incompatible: ${error instanceof Error ? error.message : "invalid contract"}`);
    }
  }
  if (expectedMix === "combined" && payload.schemaVersion === 54) {
    try {
      if (containsForbiddenKey(payload) || SECRET_PATTERN.test(JSON.stringify(payload))) throw new Error("The spread aggregate contains private player fields or a credential-shaped value.");
      return validateSpreadAnalysisPayload(payload as AnalysisPayload, base => validateLocalAnalysisPayload(base, expectedMix));
    } catch (error) {
      throw new LocalAnalysisValidationError(`The spread aggregate is incompatible: ${error instanceof Error ? error.message : "invalid contract"}`);
    }
  }
  if (expectedMix === "combined" && payload.schemaVersion === 53) {
    try {
      if (containsForbiddenKey(payload) || SECRET_PATTERN.test(JSON.stringify(payload))) throw new Error("The final median aggregate contains private player fields or a credential-shaped value.");
      return validateMedianAnalysisPayload(payload as AnalysisPayload, base => validateLocalAnalysisPayload(base, expectedMix));
    } catch (error) {
      throw new LocalAnalysisValidationError(`The final median aggregate is incompatible: ${error instanceof Error ? error.message : "invalid contract"}`);
    }
  }
  if (expectedMix === "combined" && payload.schemaVersion === 52) {
    try {
      if (containsForbiddenKey(payload) || SECRET_PATTERN.test(JSON.stringify(payload))) throw new Error("The SSS+ aggregate contains private player fields or a credential-shaped value.");
      return validateSssPlusAnalysisPayload(payload as AnalysisPayload, base => validateLocalAnalysisPayload(base, expectedMix));
    } catch (error) {
      throw new LocalAnalysisValidationError(`The SSS+ aggregate is incompatible: ${error instanceof Error ? error.message : "invalid contract"}`);
    }
  }
  if (expectedMix === "combined" && payload.schemaVersion === 51) {
    try {
      if (containsForbiddenKey(payload) || SECRET_PATTERN.test(JSON.stringify(payload))) throw new Error("The centered aggregate contains private player fields or a credential-shaped value.");
      return validateCenteredAnalysisPayload(payload as AnalysisPayload, base => validateLocalAnalysisPayload(base, expectedMix));
    } catch (error) {
      throw new LocalAnalysisValidationError(`The centered aggregate is incompatible: ${error instanceof Error ? error.message : "invalid contract"}`);
    }
  }
  if (expectedMix === "combined" && payload.schemaVersion === 50) {
    try {
      if (containsForbiddenKey(payload) || SECRET_PATTERN.test(JSON.stringify(payload))) throw new Error("The refined aggregate contains private player fields or a credential-shaped value.");
      return validateRefinedAnalysisPayload(payload as AnalysisPayload, base => validateLocalAnalysisPayload(base, expectedMix));
    } catch (error) {
      throw new LocalAnalysisValidationError(`The refined aggregate is incompatible: ${error instanceof Error ? error.message : "invalid contract"}`);
    }
  }
  if (expectedMix === "combined" && payload.schemaVersion === 49) {
    try {
      if (containsForbiddenKey(payload) || SECRET_PATTERN.test(JSON.stringify(payload))) throw new Error("The SSS aggregate contains private player fields or a credential-shaped value.");
      return validateSssAnalysisPayload(payload as AnalysisPayload, base => validateLocalAnalysisPayload(base, expectedMix));
    } catch (error) {
      throw new LocalAnalysisValidationError(`The SSS aggregate is incompatible: ${error instanceof Error ? error.message : "invalid contract"}`);
    }
  }
  if (expectedMix === "combined" && [LOCAL_AVERAGE_PUMBILITY_SCHEMA, 48].includes(payload.schemaVersion ?? 0)) {
    try {
      validateLocalAnalysisPayload(recoverMaximumPumbility(payload as AnalysisPayload), expectedMix);
      return payload as AnalysisPayload;
    } catch (error) {
      throw new LocalAnalysisValidationError(`The local Pumbility experiment is incompatible: ${error instanceof Error ? error.message : "invalid contract"}`);
    }
  }
  if (expectedMix === "combined" && [POSTPROCESSED_SCHEMA, HYBRID_POSTPROCESSED_SCHEMA].includes(payload.schemaVersion ?? 0)) {
    try {
      const raw = recoverInitialFitPayload(payload as AnalysisPayload);
      validateLocalAnalysisPayload(raw, expectedMix);
      return payload as AnalysisPayload;
    } catch (error) {
      throw new LocalAnalysisValidationError(`The midpoint-compressed aggregate is incompatible: ${error instanceof Error ? error.message : "invalid contract"}`);
    }
  }
  if (
    expectedMix === "combined"
    && payload.schemaVersion !== LOCAL_COMBINED_ANALYSIS_SCHEMA_VERSION
    && payload.schemaVersion !== LOCAL_OFFICIAL_ANALYSIS_SCHEMA_VERSION
    && payload.schemaVersion !== LOCAL_CROSS_LEVEL_ANALYSIS_SCHEMA_VERSION
    && payload.schemaVersion !== LOCAL_CROSS_LEVEL_CLEARING_SCHEMA_VERSION
    && payload.schemaVersion !== HYBRID_INITIAL_SCHEMA
    && payload.schemaVersion !== LOCAL_PERCENTILE_ANALYSIS_SCHEMA_VERSION
  ) {
    throw new LocalAnalysisValidationError(
      "The local combined aggregate uses an unsupported schema.",
    );
  }
  if (
    typeof payload.generatedAtUtc !== "string"
    || !payload.summary
    || typeof payload.summary !== "object"
    || !Array.isArray(payload.singles)
    || !Array.isArray(payload.doubles)
    || (expectedMix === "combined" && !Array.isArray(payload.coop))
    || !Array.isArray(payload.relativeGroups)
    || !Array.isArray(payload.effectBands)
  ) {
    throw new LocalAnalysisValidationError("The local analysis payload has an invalid shape.");
  }
  if (expectedMix === "combined") {
    if (!resolvePumbilityPolicy(payload)) {
      throw new LocalAnalysisValidationError("The local combined scoring or clearing-skill method is incompatible: unrecognized Pumbility policy.");
    }
    const hybrid = payload.schemaVersion === HYBRID_INITIAL_SCHEMA;
    const newClearing = payload.schemaVersion === LOCAL_CROSS_LEVEL_CLEARING_SCHEMA_VERSION || hybrid;
    if (newClearing && !hasValidCrossLevelClearing(payload, hybrid)) {
      throw new LocalAnalysisValidationError("The cross-level Clearing method, evidence, or calibration is incompatible.");
    }
    const scoringPayload = newClearing ? { ...payload, schemaVersion: payload.summary.method.crossLevelClearing!.baseScoringSchemaVersion } : payload;
    const scoring = payload.summary.method?.scoring as {
      version?: number;
      population?: string;
      calibration?: string;
      percentiles?: number[];
      profileWeights?: number[];
      percentileMethod?: string;
      scoreUnit?: number;
      referenceSmoothing?: number;
      minimumReferencePlayers?: number;
      minimumReferenceCharts?: number;
      fullReferenceWeightCharts?: number;
      folderCenter?: string;
      difficultyDeltaScale?: number;
      maxTwoGradeMoves?: number;
      scaleStep?: number;
      maximumScale?: number;
      preferredCentralWidth?: number;
      spreadQuantiles?: number[];
      minimumSpreadCharts?: number;
      minimumSpreadMedianPlayers?: number;
      spreadReliabilityCharts?: number;
      spreadReliabilityPlayers?: number;
      neighborSmoothing?: number;
      rarityThreshold?: number;
      rarityPenalty?: number;
    } | undefined;
    const tiers = payload.summary.method?.tierMetrics as {
      version?: number;
      clearing?: { skillMethod?: {
        methodVersion?: number;
        difficultyBasis?: string;
        ranks?: number[];
        requiredClearCount?: number;
      } };
    } | undefined;
    const clearing = tiers?.clearing?.skillMethod;
    const crossLevel = scoringPayload.schemaVersion === LOCAL_CROSS_LEVEL_ANALYSIS_SCHEMA_VERSION;
    const validScoring = hybrid ? hasValidHybridScoring(payload) : crossLevel ? hasValidCrossLevelScoring(payload) : scoring?.calibration === "folder-scaled-score-profile"
      ? (scoringPayload.schemaVersion === LOCAL_PERCENTILE_ANALYSIS_SCHEMA_VERSION
          || scoringPayload.schemaVersion === LOCAL_COMBINED_ANALYSIS_SCHEMA_VERSION
          || scoringPayload.schemaVersion === LOCAL_OFFICIAL_ANALYSIS_SCHEMA_VERSION)
        && ((scoring.version === 1
          && JSON.stringify(scoring.percentiles) === "[0.1,0.25,0.5,0.75,0.9]"
          && JSON.stringify(scoring.profileWeights) === "[1,1,1,1,2]")
          || (scoring.version === 2
            && JSON.stringify(scoring.percentiles) === "[0.5,0.75,0.9]"
            && JSON.stringify(scoring.profileWeights) === "[1,1,2]"))
        && scoring.percentileMethod === "linear interpolation" && scoring.scoreUnit === 10000
        && scoring.referenceSmoothing === 4 && scoring.minimumReferencePlayers === 20
        && scoring.minimumReferenceCharts === 5 && scoring.fullReferenceWeightCharts === 20
        && scoring.folderCenter === "median-profile-match"
        && scoring.scaleStep === 0.01 && scoring.maximumScale === 1
        && scoring.preferredCentralWidth === 1.0 && JSON.stringify(scoring.spreadQuantiles) === "[0.1,0.9]"
        && scoring.minimumSpreadCharts === 10 && scoring.minimumSpreadMedianPlayers === 10
        && scoring.spreadReliabilityCharts === 30 && scoring.spreadReliabilityPlayers === 20
        && scoring.neighborSmoothing === 0.05 && scoring.rarityThreshold === 10 && scoring.rarityPenalty === 0.05
        && scoring.difficultyDeltaScale === undefined && scoring.maxTwoGradeMoves === undefined
        && (payload.schemaVersion !== LOCAL_PERCENTILE_ANALYSIS_SCHEMA_VERSION
          ? payload.summary.method.localExperiment === undefined
          : payload.summary.method.localExperiment === "scoring-profile-level-scales")
        && hasValidFolderScales(scoringPayload)
      : scoringPayload.schemaVersion === LOCAL_COMBINED_ANALYSIS_SCHEMA_VERSION
        && scoring?.version === 1
        && scoring?.calibration === "original-residual-centering";
    if ((!crossLevel && scoring?.population !== "combined") || !validScoring
      || (!newClearing && (![10, 11].includes(tiers?.version ?? 0) || clearing?.methodVersion !== 2
      || clearing.difficultyBasis !== "current-official-level"
      || clearing.requiredClearCount !== 50
      || !Array.isArray(clearing.ranks) || clearing.ranks.length !== 2
      || clearing.ranks[0] !== 1 || clearing.ranks[1] !== 50))) {
      throw new LocalAnalysisValidationError("The local combined scoring or clearing-skill method is incompatible.");
    }
    if ((scoringPayload.schemaVersion === LOCAL_OFFICIAL_ANALYSIS_SCHEMA_VERSION || (crossLevel && !newClearing))
      && !hasValidOfficialTiers(scoringPayload, !crossLevel, !newClearing)) {
      throw new LocalAnalysisValidationError("The local official tier source, calibration, or truncation policy is incompatible.");
    }
    if (!hasValidPumbilityMethod(payload)) {
      throw new LocalAnalysisValidationError("The Pumbility aggregation method or component values are incompatible.");
    }
  }
  const payloadMix = payload.mix;
  if (payloadMix === undefined && expectedMix !== DEFAULT_MIX) {
    throw new LocalAnalysisValidationError("The local aggregate has no Phoenix version metadata.");
  }
  const expectedInfo = expectedMix === "combined" ? COMBINED_MIX : MIXES[expectedMix];
  if (payloadMix !== undefined && (
    !payloadMix
    || typeof payloadMix !== "object"
    || payloadMix.key !== expectedMix
    || payloadMix.apiValue !== expectedInfo.apiValue
    || payloadMix.label !== expectedInfo.label
  )) {
    throw new LocalAnalysisValidationError(
      `The local aggregate does not contain ${expectedInfo.label} data.`,
    );
  }
  if (containsForbiddenKey(payload)) {
    throw new LocalAnalysisValidationError("The local aggregate contains private player fields.");
  }
  return {
    ...payload,
    mix: payloadMix || MIXES[DEFAULT_MIX],
  } as AnalysisPayload;
}

export async function readLocalAnalysisPayload(
  mixOrResultsPath: MixKey | string = DEFAULT_MIX,
  explicitResultsPath?: string,
): Promise<AnalysisPayload> {
  const mix = isMixKey(mixOrResultsPath) ? mixOrResultsPath : DEFAULT_MIX;
  const resultsPath = explicitResultsPath
    ?? (isMixKey(mixOrResultsPath) ? localResultsPath(mix) : mixOrResultsPath);
  let raw: string;
  try {
    raw = await readFile(resultsPath, "utf8");
  } catch (error) {
    if (
      (error as NodeJS.ErrnoException).code === "ENOENT"
      && mix === DEFAULT_MIX
      && explicitResultsPath === undefined
      && isMixKey(mixOrResultsPath)
    ) {
      try {
        raw = await readFile(LEGACY_LOCAL_RESULTS_PATH, "utf8");
      } catch (legacyError) {
        if ((legacyError as NodeJS.ErrnoException).code === "ENOENT") {
          throw new LocalAnalysisNotFoundError("No local analysis has been generated yet.");
        }
        throw legacyError;
      }
    } else if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      throw new LocalAnalysisNotFoundError("No local analysis has been generated yet.");
    } else {
      throw error;
    }
  }
  if (SECRET_PATTERN.test(raw)) {
    throw new LocalAnalysisValidationError("The local aggregate contains a credential-shaped value.");
  }
  try {
    return validateLocalAnalysisPayload(JSON.parse(raw), mix);
  } catch (error) {
    if (error instanceof LocalAnalysisValidationError) throw error;
    throw new LocalAnalysisValidationError("The local analysis file is not valid JSON.");
  }
}

async function readValidatedCombinedAnalysisPayload(resultsPath: string): Promise<AnalysisPayload> {
  let raw: string;
  try {
    raw = await readFile(resultsPath, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      throw new LocalAnalysisNotFoundError(
        "No local combined tier list has been generated yet.",
      );
    }
    throw error;
  }
  if (SECRET_PATTERN.test(raw)) {
    throw new LocalAnalysisValidationError(
      "The local combined aggregate contains a credential-shaped value.",
    );
  }
  try {
    return validateLocalAnalysisPayload(JSON.parse(raw), "combined");
  } catch (error) {
    if (error instanceof LocalAnalysisValidationError) throw error;
    throw new LocalAnalysisValidationError(
      "The local combined analysis file is not valid JSON.",
    );
  }
}

const readCachedCombinedAnalysis = cachedFileReader(readValidatedCombinedAnalysisPayload);

export async function readLocalCombinedAnalysisPayload(): Promise<AnalysisPayload> {
  try {
    return await readCachedCombinedAnalysis(localCombinedResultsPath());
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      throw new LocalAnalysisNotFoundError("No local combined tier list exists. Run npm run analyze:recommendations.");
    }
    throw error;
  }
}
