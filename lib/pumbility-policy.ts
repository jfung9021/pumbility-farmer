import type { AnalysisPayload } from "./types";

export type PumbilityAggregation = "average" | "maximum";

export interface PumbilityPolicy {
  aggregation: PumbilityAggregation;
  requiresRowMarker: boolean;
}

export const MAXIMUM_PUMBILITY_CALCULATION = "max(scoring difficulty, clearing difficulty) before rounding";
export const AVERAGE_PUMBILITY_CALCULATION = "(scoring difficulty + clearing difficulty) / 2 before rounding";

/** Resolve declared generations; tier-method 11 alone does not identify the rule. */
export function resolvePumbilityPolicy(payload: Partial<AnalysisPayload> | null | undefined): PumbilityPolicy | null {
  const tiers = payload?.summary?.method?.tierMetrics as {
    version?: number;
    pumbility?: { version?: number; aggregation?: string; requiresBoth?: boolean; calculation?: string };
  } | undefined;
  if (!tiers || (tiers.version !== 10 && tiers.version !== 11)) return null;
  const method = tiers.pumbility;
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

export function pumbilityReference(aggregation: PumbilityAggregation, scoring: number, clearing: number): number {
  return aggregation === "maximum" ? Math.max(scoring, clearing) : (scoring + clearing) / 2;
}
