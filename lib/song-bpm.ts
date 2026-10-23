import type { BpmMetadataIdentity } from "./types";

/** Display intervals and numerical focal tempos have deliberately separate uses. */
export type SongBpm = { bpmMin?: number | null; bpmMax?: number | null; bpmFocal?: number | null };
export type DisplayBpmRange = { min: number; max: number };

export function positiveBpm(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

export function displayBpmRange(chart: SongBpm): DisplayBpmRange | null {
  return positiveBpm(chart.bpmMin) && positiveBpm(chart.bpmMax) && chart.bpmMax >= chart.bpmMin
    ? { min: chart.bpmMin, max: chart.bpmMax } : null;
}

export function focalBpm(chart: SongBpm): number | null {
  const range = displayBpmRange(chart);
  return range && positiveBpm(chart.bpmFocal) && chart.bpmFocal >= range.min && chart.bpmFocal <= range.max
    ? chart.bpmFocal : null;
}

/** String conversion preserves all configured decimal precision without locale grouping. */
export function formatDisplayBpm(chart: SongBpm, suffix = true): string {
  const range = displayBpmRange(chart);
  const label = range ? range.min === range.max ? String(range.min) : `${range.min}–${range.max}` : "—";
  return suffix ? `${label} BPM` : label;
}

/** Radar categories are half-open; tier slider selections are inclusive. */
export function focalBpmBucket(chart: SongBpm): number | null {
  const bpm = focalBpm(chart);
  return bpm === null ? null : bpm < 150 ? 0 : Math.min(8, Math.floor((bpm - 150) / 10) + 1);
}

/** A focal request must never silently accept an older response contract. */
export function requireFocalBpmMetadata(value: unknown): BpmMetadataIdentity {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("The BPM metadata is unavailable or invalid. Reload this page.");
  const metadata = value as Record<string, unknown>;
  if (metadata.bpmMetadataVersion !== 1 || metadata.bpmPolicy !== "focal-v1"
    || typeof metadata.bpmMetadataRevision !== "string" || !/^[a-f0-9]{64}$/.test(metadata.bpmMetadataRevision)) {
    throw new Error("The BPM metadata is unavailable or invalid. Reload this page.");
  }
  return value as BpmMetadataIdentity;
}
