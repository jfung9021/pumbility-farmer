import { readFile } from "node:fs/promises";
import path from "node:path";

import type { AnalysisPayload, OfficialScoringSpread } from "./types";
import { COMBINED_MIX, DEFAULT_MIX, isMixKey, MIXES, type MixKey } from "./mixes.ts";


const SECRET_PATTERN = /(?:piu_scores_live_|pst_live_)[0-9a-f]{16,}/i;
const FORBIDDEN_KEYS = new Set(["playerId", "username", "gameTag", "authorization", "apiKey", "token"]);
export const LOCAL_COMBINED_ANALYSIS_SCHEMA_VERSION = 26;
export const LOCAL_OFFICIAL_ANALYSIS_SCHEMA_VERSION = 41;
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

function hasValidOfficialTiers(payload: Partial<AnalysisPayload>): boolean {
  const method = payload.summary?.method.officialTiers;
  if (!method || method.version !== 15 || method.enabled !== true
    || method.source !== "piuscores-official" || method.mix !== "Phoenix2"
    || (method.asOf !== null && typeof method.asOf !== "string")
    || method.minimumLevels?.Single !== 23 || method.minimumLevels?.Double !== 25
    || method.capPolicy !== "clearing-folder-minimum"
    || method.scoring?.metric !== "equal-player-score-gaps" || method.scoring.minimumOtherCharts !== 3
    || method.scoring.sparsePolicy !== "provisional-same-level" || method.scoring.aggregation !== "equal-player-mean"
    || method.scoring.calibration?.method !== "shared-linear-player-gaps"
    || method.scoring.calibration.version !== 6
    || method.scoring.calibration.minimumLevels?.Single !== 23 || method.scoring.calibration.minimumLevels?.Double !== 25
    || method.scoring.calibration.minimumSupportedCharts !== 8
    || method.scoring.calibration.outlierIqrMultiplier !== 2
    || method.scoring.calibration.referenceQuantile !== .90 || method.scoring.calibration.targetHalfWidth !== .45
    || method.scoring.calibration.maximumScale !== 1 || method.scoring.calibration.rangePolicy !== "unbounded"
    || method.scoring.calibration.minimumReferencePlayers !== 10 || method.scoring.calibration.outlierUse !== "diagnostic-only"
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
  const scales = method.scoring.calibration?.folderScales;
  for (const mode of ["Single", "Double"] as const) {
    const folders = scales?.[mode];
    if (!folders || typeof folders !== "object" || Array.isArray(folders)
      || Object.entries(folders).some(([level, scale]) => !/^\d+$/.test(level) || !isOfficialScale(scale))) return false;
    const modeCharts = (mode === "Single" ? payload.singles : payload.doubles) ?? [];
    for (const chart of modeCharts) {
      if (chart.level < method.minimumLevels[mode]) {
        if (chart.officialEvidence) return false;
        continue;
      }
      const evidence = chart.officialEvidence;
      const clearing = chart.tierMetrics?.clearing;
      const pumbility = chart.tierMetrics?.pumbility;
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
        || !Number.isInteger(chart.scoringPlayerCount) || chart.scoringPlayerCount! < 0
        || chart.scoringPlayerCount! > chart.officialContributors!
        || pumbility.scoringSupportCount !== chart.scoringPlayerCount
        || chart.nContributors !== chart.scoringPlayerCount
        || (chart.scoringPlayerCount === 0 ? chart.scoringMeanGap !== null
          : !(typeof chart.scoringMeanGap === "number" && Number.isFinite(chart.scoringMeanGap)))
        || !Number.isInteger(chart.scoringMinimumOtherCharts) || chart.scoringMinimumOtherCharts! < 1 || chart.scoringMinimumOtherCharts! > 3
        || !Number.isInteger(chart.scoringComparisonCharts) || chart.scoringComparisonCharts! < 1
        || !Number.isInteger(chart.scoringComponentCount) || chart.scoringComponentCount! < 1
        || chart.scoringProvisional !== (chart.scoringMinimumOtherCharts! < 3 || chart.scoringComponentCount! > 1 || chart.scoringPlayerCount! < 10)
        || !("folderReferenceSkill" in clearing)
        || !Number.isInteger(clearing.ratedClearCount) || clearing.ratedClearCount < 0
        || pumbility.clearingSupportCount !== clearing.ratedClearCount
        || chart.scoringScoreProfile != null) return false;
      if (chart.estimatedDifficulty != null && (!isOfficialScale(chart.scoringDifficultyScale)
        || Math.abs(chart.scoringDifficultyScale - folders[String(chart.level)]) > .000001)) return false;
      if (chart.scoringPlayerCount === 0 && [chart, pumbility].some((metric) =>
        metric.estimatedDifficulty !== null || metric.difficultyDelta !== null
        || metric.evidenceStatus !== "Unrated")) return false;
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
      if (chart.level < method.clearing.minimumLevels[mode]) {
        if (clearing.skillMetric != null || !("q10Skill" in clearing) || "q20Skill" in clearing
          || clearing.defaultedAtCap != null || clearing.spreadCalibration != null
          || !Number.isInteger(clearing.clearCount) || clearing.clearCount < clearing.ratedClearCount
          || clearing.missingSkillCount !== clearing.clearCount - clearing.ratedClearCount) return false;
        if (chart.estimatedDifficulty === null || clearing.estimatedDifficulty === null) {
          if (pumbility.estimatedDifficulty !== null) return false;
        } else if (typeof pumbility.estimatedDifficulty !== "number"
          || Math.abs(pumbility.estimatedDifficulty - Math.max(chart.estimatedDifficulty, clearing.estimatedDifficulty)) > .000002) return false;
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
          || [chart, clearing, pumbility].some((metric) => metric.estimatedDifficulty !== null
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
      if (chart.estimatedDifficulty !== null && chart.scoringPointsPerLevel !== 10000) return false;
      if (chart.officialContributors === 0 && evidence.status === "available"
        && [chart, clearing, pumbility].some((metric) => metric.estimatedDifficulty !== null)) return false;
      if ((chart.estimatedDifficulty === null || clearing.estimatedDifficulty === null)
        && pumbility.estimatedDifficulty !== null) return false;
    }
  }
  return !(payload.coop ?? []).some((chart) => chart.officialEvidence);
}

export function validateLocalAnalysisPayload(
  value: unknown,
  expectedMix: MixKey | "combined" = DEFAULT_MIX,
): AnalysisPayload {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new LocalAnalysisValidationError("The local analysis payload must be an object.");
  }
  const payload = value as Partial<AnalysisPayload>;
  if (
    expectedMix === "combined"
    && payload.schemaVersion !== LOCAL_COMBINED_ANALYSIS_SCHEMA_VERSION
    && payload.schemaVersion !== LOCAL_OFFICIAL_ANALYSIS_SCHEMA_VERSION
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
    const validScoring = scoring?.calibration === "folder-scaled-score-profile"
      ? (payload.schemaVersion === LOCAL_PERCENTILE_ANALYSIS_SCHEMA_VERSION
          || payload.schemaVersion === LOCAL_COMBINED_ANALYSIS_SCHEMA_VERSION
          || payload.schemaVersion === LOCAL_OFFICIAL_ANALYSIS_SCHEMA_VERSION)
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
        && hasValidFolderScales(payload)
      : payload.schemaVersion === LOCAL_COMBINED_ANALYSIS_SCHEMA_VERSION
        && scoring?.version === 1
        && scoring?.calibration === "original-residual-centering";
    if (scoring?.population !== "combined" || !validScoring
      || tiers?.version !== 11 || clearing?.methodVersion !== 2
      || clearing.difficultyBasis !== "current-official-level"
      || clearing.requiredClearCount !== 50
      || !Array.isArray(clearing.ranks) || clearing.ranks.length !== 2
      || clearing.ranks[0] !== 1 || clearing.ranks[1] !== 50) {
      throw new LocalAnalysisValidationError("The local combined scoring or clearing-skill method is incompatible.");
    }
    if (payload.schemaVersion === LOCAL_OFFICIAL_ANALYSIS_SCHEMA_VERSION && !hasValidOfficialTiers(payload)) {
      throw new LocalAnalysisValidationError("The local official tier source, calibration, or truncation policy is incompatible.");
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

export async function readLocalCombinedAnalysisPayload(): Promise<AnalysisPayload> {
  let raw: string;
  try {
    raw = await readFile(COMBINED_LOCAL_RESULTS_PATH, "utf8");
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
