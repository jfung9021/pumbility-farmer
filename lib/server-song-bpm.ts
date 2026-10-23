/** Current-song metadata overlay for server readers; never imported by client UI. */
import runtime from "../config/phoenix2-song-bpm-runtime.json" with { type: "json" };
import { displayBpmRange, focalBpm } from "./song-bpm.ts";
import type { BpmMetadataIdentity } from "./types";

type Row = Record<string, unknown>;
const byName = new Map<string, typeof runtime.songs>();
for (const song of runtime.songs) {
  const rows = byName.get(song.songName) ?? [];
  rows.push(song);
  byName.set(song.songName, rows);
}

export const currentBpmMetadata: BpmMetadataIdentity = {
  bpmMetadataVersion: 1, bpmPolicy: "focal-v1", bpmMetadataRevision: runtime.bpmMetadataRevision,
};

/**
 * Python's publication hash includes Python numeric serialization. A JavaScript
 * projection cannot claim that signature after JSON.parse has erased 1 vs 1.0.
 * Retain the signed base as evidence, without labeling this derived view signed.
 */
function retainBasePublication(payload: Row): Row | undefined {
  const summary = payload.summary as Row | undefined;
  const method = summary?.method as Row | undefined;
  if (!method || typeof method !== "object") return undefined;
  const publication = method.productionTierPublication as Row | undefined;
  if (publication && typeof publication === "object" && "publicPayloadSha256" in publication) {
    method.baseProductionTierPublication = publication;
    delete method.productionTierPublication;
  }
  return method;
}

export function validateBpmPolicy(policy?: string | null): boolean {
  if (policy && policy !== "focal-v1") throw new RangeError("bpmPolicy must be focal-v1 when specified.");
  return policy === "focal-v1";
}

export function enrichBpmChart<T extends Row>(chart: T): T {
  const candidates = byName.get(String(chart.songName ?? "")) ?? [];
  const variant = typeof chart.songType === "string" ? chart.songType : undefined;
  const match = variant ? candidates.find(song => song.songType === variant)
    : candidates.length === 1 ? candidates[0] : undefined;
  if (candidates.length && !match) throw new Error("The chart's BPM song variant is ambiguous or mismatched.");
  if (match) return { ...chart, songType: match.songType, bpmMin: match.bpmMin, bpmMax: match.bpmMax, bpmFocal: match.bpmFocal };
  const range = displayBpmRange(chart);
  return { ...chart, bpmFocal: range?.min === range?.max && range ? range.min : focalBpm(chart) };
}

/** The base numerical generation stays intact; the revision pins its metadata view. */
export function enrichBpmPayload<T>(payload: T, mix: string = "phoenix2"): T {
  if (mix === "phoenix1" || mix === "Phoenix") return payload;
  function visit(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(visit);
    if (!value || typeof value !== "object") return value;
    const record = value as Row;
    const next = Object.fromEntries(Object.entries(record).map(([key, child]) => [key, key.endsWith("Baseline") ? child : visit(child)]));
    return next.songName && ["Single", "Double", "CoOp"].includes(String(next.type)) ? enrichBpmChart(next) : next;
  }
  const result = visit(payload) as Row;
  const original = payload as Row;
  const previous = original.bpmMetadata as Row | undefined;
  const method = retainBasePublication(result);
  const publication = method?.baseProductionTierPublication as Row | undefined;
  result.bpmMetadata = { ...currentBpmMetadata,
    ...(typeof original.generatedAtUtc === "string" ? { baseGeneratedAtUtc: previous?.baseGeneratedAtUtc ?? original.generatedAtUtc } : {}),
    ...(typeof previous?.basePublicPayloadSha256 === "string" || typeof publication?.publicPayloadSha256 === "string"
      ? {basePublicPayloadSha256: previous?.basePublicPayloadSha256 ?? publication?.publicPayloadSha256} : {}),
  };
  if (method) method.bpmMetadata = {...result.bpmMetadata as Row};
  return result as T;
}

export function legacyBpmPayload<T>(payload: T): T {
  const hidden = new Set(["bpmFocal", "songType", "bpmMetadata", "bpmMetadataVersion", "bpmPolicy", "bpmMetadataRevision"]);
  function visit(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(visit);
    if (!value || typeof value !== "object") return value;
    return Object.fromEntries(Object.entries(value).filter(([key]) => !hidden.has(key)).map(([key, child]) => [key, visit(child)]));
  }
  const result = visit(payload) as Row;
  retainBasePublication(result);
  return result as T;
}

export function selectBpmPayload<T>(payload: T, policy?: string | null, mix?: string): T {
  return validateBpmPolicy(policy) ? enrichBpmPayload(payload, mix) : legacyBpmPayload(payload);
}
