/** Public, client-safe contracts for the official leaderboard comparison. */
export const OFFICIAL_BPM_LABELS = [
  "<150", "150–159", "160–169", "170–179", "180–189",
  "190–199", "200–209", "210–219", "220+",
] as const;

export type OfficialComparisonMode = "singles" | "doubles";
export const OFFICIAL_COMPARISON_METHOD = "official-comparison-sg-perfect-pg-v2";

export interface OfficialComparisonChart {
  chartId: string;
  songName: string;
  chartLabel: string;
  mode: OfficialComparisonMode;
  level: number;
  bpmMin: number | null;
  bpmMax: number | null;
}

export interface OfficialComparisonContributor {
  chartId: string;
  score: number;
  pumbilityCents: number;
  assumedPlate: "SG" | "PG";
}

export interface OfficialComparisonBucket {
  mode: OfficialComparisonMode;
  bucketIndex: number;
  /** Doubles already includes the shared radar's visual alignment. */
  pg: number | null;
  radarScore: number | null;
  recordedCount: number;
  contributingCount: number;
  contributors: OfficialComparisonContributor[];
}

export interface OfficialComparisonIndex {
  schemaVersion: 2;
  generation: string;
  generatedAtUtc: string;
  sourceDates: { Phoenix: string; Phoenix2: string };
  method: Record<string, unknown>;
  players: { playerKey: string; displayName: string }[];
  charts: OfficialComparisonChart[];
}

export interface OfficialComparisonPlayer {
  schemaVersion: 2;
  generation: string;
  playerKey: string;
  displayName: string;
  buckets: OfficialComparisonBucket[];
  scores: Record<string, number>;
  recordedScoreCount: number;
  radarEligibleScoreCount: number;
}

export class OfficialComparisonValidationError extends Error {}

export function isOfficialPlayerKey(value: string): boolean {
  return /^op_[a-f0-9]{64}$/.test(value);
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function count(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function mode(value: unknown): value is OfficialComparisonMode {
  return value === "singles" || value === "doubles";
}

function invalid(message: string): never {
  throw new OfficialComparisonValidationError(message);
}

export function validateOfficialComparisonIndex(value: unknown): OfficialComparisonIndex {
  if (!object(value) || value.schemaVersion !== 2 || !text(value.generation)
    || !/^[a-zA-Z0-9_-]+$/.test(value.generation) || !text(value.generatedAtUtc)
    || !object(value.sourceDates) || !text(value.sourceDates.Phoenix) || !text(value.sourceDates.Phoenix2)
    || !object(value.method) || value.method.version !== OFFICIAL_COMPARISON_METHOD
    || !Array.isArray(value.players) || !Array.isArray(value.charts)) {
    invalid("The official comparison catalog is invalid. Rebuild the comparison artifacts.");
  }
  const playerKeys = new Set<string>();
  for (const player of value.players) {
    if (!object(player) || !text(player.playerKey) || !isOfficialPlayerKey(player.playerKey)
      || !text(player.displayName) || playerKeys.has(player.playerKey)) {
      invalid("The official comparison player roster is invalid.");
    }
    playerKeys.add(player.playerKey);
  }
  const chartIds = new Set<string>();
  for (const chart of value.charts) {
    if (!object(chart) || !text(chart.chartId) || chartIds.has(chart.chartId)
      || !text(chart.songName) || !text(chart.chartLabel) || !mode(chart.mode)
      || typeof chart.level !== "number" || !Number.isFinite(chart.level) || chart.level < 20
      || ![chart.bpmMin, chart.bpmMax].every(bpm => bpm === null || (typeof bpm === "number" && Number.isFinite(bpm)))) {
      invalid("The official comparison chart catalog is invalid.");
    }
    chartIds.add(chart.chartId);
  }
  return value as unknown as OfficialComparisonIndex;
}

export function validateOfficialComparisonPlayer(
  value: unknown,
  index: OfficialComparisonIndex,
  playerKey: string,
): OfficialComparisonPlayer {
  if (!object(value) || value.schemaVersion !== 2 || value.generation !== index.generation
    || value.playerKey !== playerKey || !text(value.displayName)
    || !index.players.some(player => player.playerKey === playerKey)
    || !Array.isArray(value.buckets) || value.buckets.length !== 18 || !object(value.scores)
    || !count(value.recordedScoreCount) || !count(value.radarEligibleScoreCount)) {
    invalid("The official player profile does not match the current comparison catalog.");
  }
  const chartsById = new Map(index.charts.map(chart => [chart.chartId, chart]));
  const seen = new Set<string>();
  for (const bucket of value.buckets) {
    if (!object(bucket) || !mode(bucket.mode) || !count(bucket.bucketIndex) || bucket.bucketIndex > 8
      || !count(bucket.recordedCount) || !count(bucket.contributingCount)
      || bucket.contributingCount !== Math.min(5, bucket.recordedCount)
      || !Array.isArray(bucket.contributors) || bucket.contributors.length !== bucket.contributingCount) {
      invalid("The official player radar has invalid coverage.");
    }
    const key = `${bucket.mode}:${bucket.bucketIndex}`;
    if (seen.has(key)) invalid("The official player radar repeats a BPM bucket.");
    seen.add(key);
    if (bucket.recordedCount === 0) {
      if (bucket.pg !== null || bucket.radarScore !== null) invalid("An empty official radar bucket must be unknown.");
    } else if (typeof bucket.pg !== "number" || !Number.isFinite(bucket.pg) || bucket.pg < 0
      || typeof bucket.radarScore !== "number" || !Number.isFinite(bucket.radarScore)
      || bucket.radarScore < 0 || bucket.radarScore > 100) {
      invalid("The official player radar has an invalid estimate.");
    }
    const contributorIds = new Set<string>();
    let previousCents = Infinity;
    for (const contributor of bucket.contributors) {
      if (!object(contributor) || !text(contributor.chartId) || contributorIds.has(contributor.chartId)
        || !count(contributor.score) || contributor.score > 1_000_000
        || contributor.score !== value.scores[contributor.chartId]
        || !count(contributor.pumbilityCents) || contributor.pumbilityCents > previousCents
        || contributor.assumedPlate !== (contributor.score === 1_000_000 ? "PG" : "SG")) {
        invalid("The official radar contributing charts are invalid.");
      }
      const chart = chartsById.get(contributor.chartId);
      const low = chart?.bpmMin;
      const high = chart?.bpmMax;
      const bucketFor = (bpm: number) => bpm < 150 ? 0 : Math.min(8, Math.floor((bpm - 150) / 10) + 1);
      if (!chart || chart.mode !== bucket.mode || low == null || high == null || low <= 0 || high < low
        || bucketFor(low) !== bucket.bucketIndex || bucketFor(high) !== bucket.bucketIndex) {
        invalid("A contributing chart does not match its official BPM bucket.");
      }
      contributorIds.add(contributor.chartId);
      previousCents = contributor.pumbilityCents;
    }
  }
  const scores = Object.entries(value.scores);
  if (scores.some(([id, score]) => !chartsById.has(id) || !count(score) || score > 1_000_000)
    || scores.length !== value.recordedScoreCount || value.radarEligibleScoreCount > value.recordedScoreCount) {
    invalid("The official player score records are invalid.");
  }
  return value as unknown as OfficialComparisonPlayer;
}
