import type { AnalysisPayload } from "./types";

export type PumbilityAggregation = "average" | "maximum" | "centered-maximum";

export interface PumbilityPolicy {
  aggregation: PumbilityAggregation;
  requiresRowMarker: boolean;
  downwardAdjustmentScale?: number;
}

export const MAXIMUM_PUMBILITY_CALCULATION = "max(scoring difficulty, clearing difficulty) before rounding";
export const AVERAGE_PUMBILITY_CALCULATION = "(scoring difficulty + clearing difficulty) / 2 before rounding";
export const CENTERED_MAXIMUM_CALCULATION = "max(scoring difficulty, clearing difficulty) + per-folder median offset before rounding";
export const HALF_DOWNWARD_PUMBILITY_CALCULATION = "max(scoring difficulty, clearing difficulty) + per-folder median offset with half downward adjustment before rounding";

/** Resolve declared generations; tier-method 11 alone does not identify the rule. */
export function resolvePumbilityPolicy(payload: Partial<AnalysisPayload> | null | undefined): PumbilityPolicy | null {
  if (payload?.schemaVersion === 162) {
    const marker = payload.summary?.method.centeredMaximumPumbility as {
      version?: number; method?: string; aggregation?: string;
      downwardAdjustmentScale?: number; upwardAdjustmentScale?: number;
    } | undefined;
    if (marker?.aggregation !== "centered-maximum") return null;
    if (marker.version === 1 && marker.method === "folder-centered-maximum-pumbility-v1") {
      return {aggregation:"centered-maximum",requiresRowMarker:true};
    }
    return marker.version === 2 && marker.method === "folder-centered-maximum-pumbility-v2"
      && marker.downwardAdjustmentScale === .5 && marker.upwardAdjustmentScale === 1
      ? {aggregation:"centered-maximum",requiresRowMarker:true,downwardAdjustmentScale:.5} : null;
  }
  if (payload && [49, 50, 51, 52, 53, 56, 57, 58, 59].includes(payload.schemaVersion ?? 0)) {
    const scoring = payload.summary?.method.sssScoring as { version?: number; baseSchemaVersion?: number } | undefined;
    return scoring?.version === 1 && scoring.baseSchemaVersion === 48
      ? resolvePumbilityPolicy({ ...payload, schemaVersion: 48 }) : null;
  }
  const tiers = payload?.summary?.method?.tierMetrics as {
    version?: number;
    pumbility?: { version?: number; aggregation?: string; requiresBoth?: boolean; calculation?: string };
  } | undefined;
  if (!tiers || ![10, 11, 12].includes(tiers.version ?? 0)) return null;
  const method = tiers.pumbility;
  if (tiers.version === 12) {
    const hybrid = payload?.summary?.method.hybridTiers as { version?: number } | undefined;
    if (hybrid?.version !== 1) return null;
    if ([46, 47].includes(payload?.schemaVersion ?? 0) && method?.version === 2
      && method.aggregation === "maximum" && method.requiresBoth === true
      && method.calculation === MAXIMUM_PUMBILITY_CALCULATION) return { aggregation: "maximum", requiresRowMarker: true };
    const experiment = payload?.summary?.method.pumbilityExperiment as { version?: number; aggregation?: string; baseAnalysisSchemaVersion?: number } | undefined;
    if (payload?.schemaVersion === 48 && method?.version === 4 && method.aggregation === "average" && method.requiresBoth === true
      && method.calculation === AVERAGE_PUMBILITY_CALCULATION && experiment?.version === 2
      && experiment.aggregation === "average" && experiment.baseAnalysisSchemaVersion === 47) return { aggregation: "average", requiresRowMarker: true };
    return null;
  }
  if (payload?.schemaVersion === 45 && method?.version === 3 && method.aggregation === "average" && method.requiresBoth === true
    && method.calculation === AVERAGE_PUMBILITY_CALCULATION) {
    const experiment = payload.summary?.method.pumbilityExperiment as { version?: number; aggregation?: string; baseAnalysisSchemaVersion?: number } | undefined;
    return experiment?.version === 1 && experiment.aggregation === "average" && experiment.baseAnalysisSchemaVersion === 44
      ? { aggregation: "average", requiresRowMarker: true } : null;
  }
  if (method?.version !== undefined || method?.aggregation !== undefined || method?.requiresBoth !== undefined) {
    return method.version === 2 && method.aggregation === "maximum" && method.requiresBoth === true
      && (method.calculation === undefined || method.calculation === MAXIMUM_PUMBILITY_CALCULATION)
      ? { aggregation: "maximum", requiresRowMarker: true } : null;
  }
  if (tiers.version === 10
    && (method?.calculation === undefined || method.calculation === AVERAGE_PUMBILITY_CALCULATION)) {
    return { aggregation: "average", requiresRowMarker: false };
  }
  if (tiers.version === 11 && payload?.schemaVersion === 41
    && method?.calculation === MAXIMUM_PUMBILITY_CALCULATION) {
    return { aggregation: "maximum", requiresRowMarker: false };
  }
  if (tiers.version === 11 && payload?.schemaVersion === 43
    && method?.calculation === AVERAGE_PUMBILITY_CALCULATION) {
    return { aggregation: "average", requiresRowMarker: false };
  }
  return null;
}

export function pumbilityReference(aggregation: PumbilityAggregation, scoring: number, clearing: number, folderOffset?: number): number {
  if (aggregation === "centered-maximum") {
    if (typeof folderOffset !== "number" || !Number.isFinite(folderOffset)) throw new Error("Centered Pumbility requires its folder offset.");
    return Math.max(scoring, clearing) + folderOffset;
  }
  return aggregation === "maximum" ? Math.max(scoring, clearing) : (scoring + clearing) / 2;
}
