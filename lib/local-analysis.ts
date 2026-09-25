import { readFile } from "node:fs/promises";
import path from "node:path";

import type { AnalysisPayload } from "./types";
import { COMBINED_MIX, DEFAULT_MIX, isMixKey, MIXES, type MixKey } from "./mixes.ts";


const SECRET_PATTERN = /(?:piu_scores_live_|pst_live_)[0-9a-f]{16,}/i;
const FORBIDDEN_KEYS = new Set(["playerId", "username", "gameTag", "authorization", "apiKey", "token"]);
export const LOCAL_COMBINED_ANALYSIS_SCHEMA_VERSION = 26;
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
    const charts = mode === "Single" ? payload.singles : payload.doubles;
    if (charts?.some((chart) => chart.estimatedDifficulty != null
      && (!isFolderScale(chart.scoringDifficultyScale)
        || chart.scoringDifficultyScale !== modeScales[String(chart.level)]))) return false;
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
  if (
    expectedMix === "combined"
    && payload.schemaVersion !== LOCAL_COMBINED_ANALYSIS_SCHEMA_VERSION
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
          || payload.schemaVersion === LOCAL_COMBINED_ANALYSIS_SCHEMA_VERSION)
        && JSON.stringify(scoring.percentiles) === "[0.1,0.25,0.5,0.75,0.9]"
        && JSON.stringify(scoring.profileWeights) === "[1,1,1,1,2]"
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
        && (payload.schemaVersion === LOCAL_COMBINED_ANALYSIS_SCHEMA_VERSION
          ? payload.summary.method.localExperiment === undefined
          : payload.summary.method.localExperiment === "scoring-profile-level-scales")
        && hasValidFolderScales(payload)
      : payload.schemaVersion === LOCAL_COMBINED_ANALYSIS_SCHEMA_VERSION
        && scoring?.calibration === "original-residual-centering";
    if (scoring?.version !== 1 || scoring.population !== "combined" || !validScoring
      || tiers?.version !== 10 || clearing?.methodVersion !== 2
      || clearing.difficultyBasis !== "current-official-level"
      || clearing.requiredClearCount !== 50
      || !Array.isArray(clearing.ranks) || clearing.ranks.length !== 2
      || clearing.ranks[0] !== 1 || clearing.ranks[1] !== 50) {
      throw new LocalAnalysisValidationError("The local combined scoring or clearing-skill method is incompatible.");
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
