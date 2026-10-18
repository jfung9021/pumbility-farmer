import type { AnalysisPayload } from "./types";

const serialized = new WeakMap<AnalysisPayload, string>();

/** Rollback snapshots stay in the validated local artifact, outside the browser view. */
export function tierBrowserJson(payload: AnalysisPayload): string {
  const cached = serialized.get(payload);
  if (cached !== undefined) return cached;
  const json = JSON.stringify(payload, (key, value) => key.endsWith("Baseline") ? undefined : value);
  serialized.set(payload, json);
  return json;
}
