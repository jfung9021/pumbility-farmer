import { readFile } from "node:fs/promises";
import path from "node:path";

import type { AnalysisPayload } from "./types";
import { DEFAULT_MIX, isMixKey, MIXES, type MixKey } from "./mixes.ts";


const SECRET_PATTERN = /(?:piu_scores_live_|pst_live_)[0-9a-f]{16,}/i;
const FORBIDDEN_KEYS = new Set(["playerId", "username", "gameTag", "authorization", "apiKey", "token"]);

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

export function validateLocalAnalysisPayload(
  value: unknown,
  expectedMix: MixKey = DEFAULT_MIX,
): AnalysisPayload {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new LocalAnalysisValidationError("The local analysis payload must be an object.");
  }
  const payload = value as Partial<AnalysisPayload>;
  if (
    typeof payload.generatedAtUtc !== "string"
    || !payload.summary
    || typeof payload.summary !== "object"
    || !Array.isArray(payload.singles)
    || !Array.isArray(payload.doubles)
    || !Array.isArray(payload.relativeGroups)
    || !Array.isArray(payload.effectBands)
  ) {
    throw new LocalAnalysisValidationError("The local analysis payload has an invalid shape.");
  }
  const payloadMix = payload.mix;
  if (payloadMix === undefined && expectedMix !== DEFAULT_MIX) {
    throw new LocalAnalysisValidationError("The local aggregate has no Phoenix version metadata.");
  }
  if (payloadMix !== undefined && (
    !payloadMix
    || typeof payloadMix !== "object"
    || !isMixKey(payloadMix.key)
    || payloadMix.key !== expectedMix
    || payloadMix.apiValue !== MIXES[expectedMix].apiValue
    || payloadMix.label !== MIXES[expectedMix].label
  )) {
    throw new LocalAnalysisValidationError(
      `The local aggregate does not contain ${MIXES[expectedMix].label} data.`,
    );
  }
  if (containsForbiddenKey(payload)) {
    throw new LocalAnalysisValidationError("The local aggregate contains private player fields.");
  }
  return {
    ...payload,
    mix: payloadMix || MIXES[expectedMix],
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
