import { focalBpm } from "./song-bpm.ts";

export type RadarMode = "singles" | "doubles";
export type RadarGroup = "weaknesses" | "strengths" | "everythingElse";

export const RADAR_REFERENCE = {grade:"SSS+", plate:"UG", minimumScore:995000} as const;
export const RADAR_SUGGESTION_LIMIT = 10;
export const RADAR_INITIAL_SUGGESTIONS = 5;

export interface RadarBpmBucket {
  id: string;
  label: string;
  min: number | null;
  max: number | null;
  radarScale: Record<RadarMode, {minimumAlignedLevel: number; maximumAlignedLevel: number}>;
}

export interface RadarSuggestion {
  chartId: string;
  songName: string;
  difficulty: string;
  type: "Single" | "Double";
  level: number;
  imageUrl: string | null;
  bpmMin: number | null;
  bpmMax: number | null;
  /** Required by focal methods; absent from the retained method 6 contract. */
  bpmFocal?: number | null;
  estimatedDifficulty: number;
  personalScore: number | null;
  personalPlate: string | null;
}

export interface RadarContribution {
  chartId: string;
  songName: string;
  difficulty: string;
  type: "Single" | "Double";
  level: number;
  imageUrl: string | null;
  pumbility: number;
  score: number | null;
  plate: string | null;
}

export interface RadarBucket {
  key: string;
  mode: RadarMode;
  bpmBucketId: string;
  label: string;
  rawEquivalentLevel: number;
  displayEquivalentLevel: number;
  radarScore: number;
  usableCharts: number;
  selectedCharts: number;
  needsPlays: boolean;
  suggestedLevel: number;
  group: RadarGroup;
  suggestions: RadarSuggestion[];
  contributingCharts: RadarContribution[];
}

export interface SkillRadarResponse {
  methodVersion: 6 | 7 | 8;
  bpmMetadataVersion?: 1;
  bpmPolicy?: "focal-v1";
  bpmMetadataRevision?: string;
  reference: typeof RADAR_REFERENCE;
  player: {playerKey: string; displayName: string};
  generatedAtUtc: string;
  tierGeneratedAtUtc: string;
  playerSyncedAtUtc?: string | null;
  revision?: string;
  bpmBuckets: RadarBpmBucket[];
  buckets: RadarBucket[];
  groups: Record<RadarGroup, string[]>;
}

export const RADAR_GROUPS = [
  {key:"weaknesses", label:"Work on your weaknesses"},
  {key:"strengths", label:"Strengthen your strengths"},
  {key:"everythingElse", label:"Work on everything else"},
] as const;

export const RADAR_MODES = {singles:{label:"Singles", prefix:"S", color:"#ff4a4a"}, doubles:{label:"Doubles", prefix:"D", color:"#39d96a"}} as const;
export const PLAYER_KEY_PATTERN = /^[a-f0-9]{20}$/;

export function radarScoreForLevel(alignedLevel: number, bucket: RadarBpmBucket, mode: RadarMode) {
  const {minimumAlignedLevel, maximumAlignedLevel} = bucket.radarScale[mode];
  return Math.max(0, Math.min(100, 100 * (alignedLevel - minimumAlignedLevel) / (maximumAlignedLevel - minimumAlignedLevel)));
}

export function radarPoint(index: number, score: number, radius = 132) {
  const angle = -Math.PI / 2 + index * 2 * Math.PI / 9;
  return {x:200 + Math.cos(angle) * radius * score / 100, y:200 + Math.sin(angle) * radius * score / 100};
}

export function radarAxisLabelLayout(index: number) {
  const anchor = radarPoint(index, 100, 152);
  const below = anchor.y > 200;
  return {
    x:anchor.x,
    labelY:anchor.y + (below ? 16 : -44),
    scoreY:anchor.y + (below ? 34 : -26),
    textAnchor:Math.abs(anchor.x - 200) < 1e-8 ? "middle" as const : anchor.x > 200 ? "start" as const : "end" as const,
  };
}

export function radarPlayerFromUrl(search: string): string {
  const value = new URLSearchParams(search).get("player") ?? "";
  return PLAYER_KEY_PATTERN.test(value) ? value : "";
}

export function radarPlayerUrl(href: string, playerKey: string): URL {
  const url = new URL(href);
  if (PLAYER_KEY_PATTERN.test(playerKey)) url.searchParams.set("player", playerKey);
  else url.searchParams.delete("player");
  return url;
}

const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const whole = (value: unknown): value is number => finite(value) && Number.isInteger(value) && value >= 0;
const optionalNumber = (value: unknown) => value === null || finite(value);
const exact = (value: Record<string, unknown>, keys: readonly string[], optional: readonly string[] = []) => keys.every(key => key in value) && Object.keys(value).every(key => keys.includes(key) || optional.includes(key));
const close = (a: number, b: number) => Math.abs(a - b) < 1e-8;

/** Reject histories/private fields and malformed profiles rather than drawing zeros. */
export function validateSkillRadarResponse(value: unknown, expectedPlayerKey?: string, expectedBpmPolicy?: "focal-v1"): SkillRadarResponse {
  const invalid = () => { throw new Error("The Skill Radar data is unavailable or invalid."); };
  if (!record(value) || (value.methodVersion !== 6 && value.methodVersion !== 7 && value.methodVersion !== 8)) return invalid();
  const focal = value.methodVersion !== 6;
  const groupLimit = value.methodVersion === 8 ? 5 : 3;
  if (expectedBpmPolicy === "focal-v1" && !focal) return invalid();
  const metadataKeys = focal ? ["bpmMetadataVersion", "bpmPolicy", "bpmMetadataRevision"] : [];
  if (!exact(value, ["methodVersion", "reference", "player", "generatedAtUtc", "tierGeneratedAtUtc", "bpmBuckets", "buckets", "groups", ...metadataKeys], ["playerSyncedAtUtc", "revision"]) || value.revision !== undefined && typeof value.revision !== "string") return invalid();
  if (focal && (value.bpmMetadataVersion !== 1 || value.bpmPolicy !== "focal-v1" || typeof value.bpmMetadataRevision !== "string" || !/^[a-f0-9]{64}$/.test(value.bpmMetadataRevision))) return invalid();
  if (!record(value.reference) || !exact(value.reference, ["grade", "plate", "minimumScore"]) || value.reference.grade !== RADAR_REFERENCE.grade || value.reference.plate !== RADAR_REFERENCE.plate || value.reference.minimumScore !== RADAR_REFERENCE.minimumScore) return invalid();
  if (!record(value.player) || !exact(value.player, ["playerKey", "displayName"]) || typeof value.player.playerKey !== "string" || !PLAYER_KEY_PATTERN.test(value.player.playerKey) || typeof value.player.displayName !== "string" || expectedPlayerKey && value.player.playerKey !== expectedPlayerKey) return invalid();
  for (const key of ["generatedAtUtc", "tierGeneratedAtUtc"]) if (typeof value[key] !== "string" || !Number.isFinite(Date.parse(value[key] as string))) return invalid();
  if (value.playerSyncedAtUtc !== undefined && value.playerSyncedAtUtc !== null && (typeof value.playerSyncedAtUtc !== "string" || !Number.isFinite(Date.parse(value.playerSyncedAtUtc)))) return invalid();
  if (!Array.isArray(value.bpmBuckets) || value.bpmBuckets.length !== 9 || !value.bpmBuckets.every(bin => {
    if (!record(bin) || !exact(bin, ["id", "label", "min", "max", "radarScale"]) || typeof bin.id !== "string" || typeof bin.label !== "string" || !optionalNumber(bin.min) || !optionalNumber(bin.max) || !record(bin.radarScale) || !exact(bin.radarScale, ["singles", "doubles"])) return false;
    const scales = bin.radarScale;
    return ["singles", "doubles"].every(mode => {
      const scale = scales[mode];
      return record(scale) && exact(scale, ["minimumAlignedLevel", "maximumAlignedLevel"]) && scale.minimumAlignedLevel === 10 && finite(scale.maximumAlignedLevel) && scale.maximumAlignedLevel > 10 && scale.maximumAlignedLevel <= 25 && !(finite(bin.min) && bin.min >= 220 && scale.maximumAlignedLevel !== 25);
    });
  })) return invalid();
  const bins = new Map((value.bpmBuckets as RadarBpmBucket[]).map(bin => [bin.id, bin]));
  const binIds = new Set(bins.keys());
  if (binIds.size !== 9 || !Array.isArray(value.buckets) || value.buckets.length !== 18) return invalid();
  const keys = new Set<string>();
  for (const bucket of value.buckets) {
    if (!record(bucket) || !exact(bucket, ["key", "mode", "bpmBucketId", "label", "rawEquivalentLevel", "displayEquivalentLevel", "radarScore", "usableCharts", "selectedCharts", "needsPlays", "suggestedLevel", "group", "suggestions", "contributingCharts"]) || typeof bucket.key !== "string" || typeof bucket.bpmBucketId !== "string" || !binIds.has(bucket.bpmBucketId) || (bucket.mode !== "singles" && bucket.mode !== "doubles") || typeof bucket.label !== "string" || keys.has(bucket.key)) return invalid();
    keys.add(bucket.key);
    if (![bucket.rawEquivalentLevel, bucket.displayEquivalentLevel, bucket.radarScore, bucket.suggestedLevel].every(finite) || (bucket.rawEquivalentLevel as number) < 0 || !whole(bucket.usableCharts) || !whole(bucket.selectedCharts) || bucket.selectedCharts > 5 || bucket.usableCharts < bucket.selectedCharts || bucket.needsPlays !== (bucket.selectedCharts < 5)) return invalid();
    if (!close(bucket.displayEquivalentLevel as number, Math.max(0, (bucket.rawEquivalentLevel as number) - (bucket.mode === "doubles" ? 1 : 0))) || !close(bucket.radarScore as number, radarScoreForLevel(bucket.displayEquivalentLevel as number, bins.get(bucket.bpmBucketId)!, bucket.mode)) || bucket.suggestedLevel !== Math.max(16, Math.floor((bucket.rawEquivalentLevel as number) + 1.5 + 1e-9))) return invalid();
    if (!RADAR_GROUPS.some(group => group.key === bucket.group) || !Array.isArray(bucket.suggestions) || bucket.suggestions.length > RADAR_SUGGESTION_LIMIT) return invalid();
    if (!Array.isArray(bucket.contributingCharts) || bucket.contributingCharts.length !== bucket.selectedCharts) return invalid();
    const contributionIds = new Set<string>();
    let contributionCents = 0;
    for (const chart of bucket.contributingCharts) {
      if (!record(chart) || !exact(chart, ["chartId", "songName", "difficulty", "type", "level", "imageUrl", "pumbility", "score", "plate"]) || typeof chart.chartId !== "string" || contributionIds.has(chart.chartId) || typeof chart.songName !== "string" || chart.type !== (bucket.mode === "singles" ? "Single" : "Double") || !whole(chart.level) || chart.level < 1 || chart.level > 30 || chart.difficulty !== `${bucket.mode === "singles" ? "S" : "D"}${chart.level}` || !(chart.imageUrl === null || typeof chart.imageUrl === "string") || !finite(chart.pumbility) || chart.pumbility < 0 || !(chart.score === null || whole(chart.score) && chart.score <= 1_000_000) || !(chart.plate === null || ["RG", "FG", "TG", "MG", "SG", "EG", "UG", "PG"].includes(chart.plate as string))) return invalid();
      contributionIds.add(chart.chartId);
      contributionCents += Math.round(chart.pumbility * 100);
    }
    const referenceMultiplier = (bucket.mode === "doubles" ? 758 : 758.5) / 750;
    const count = bucket.contributingCharts.length;
    const base = count > 0 ? contributionCents / 100 / count / referenceMultiplier : 0;
    const contributedEquivalentLevel = contributionCents > 0 ? Math.max(0, (base <= 375 ? base / 7.5 - 27 : 23 + (base - 375) / 15) + (bucket.mode === "doubles" ? 1 : 0)) : 0;
    if (!close(bucket.rawEquivalentLevel as number, contributedEquivalentLevel)) return invalid();
    const ids = new Set<string>();
    let prior = -Infinity;
    for (const chart of bucket.suggestions) {
      if (!record(chart) || !exact(chart, ["chartId", "songName", "difficulty", "type", "level", "imageUrl", "bpmMin", "bpmMax", "estimatedDifficulty", "personalScore", "personalPlate", ...(focal ? ["bpmFocal"] : [])]) || typeof chart.chartId !== "string" || ids.has(chart.chartId) || typeof chart.songName !== "string" || typeof chart.difficulty !== "string" || chart.type !== (bucket.mode === "singles" ? "Single" : "Double") || !whole(chart.level) || chart.level > 30 || !(chart.imageUrl === null || typeof chart.imageUrl === "string") || !optionalNumber(chart.bpmMin) || !optionalNumber(chart.bpmMax) || !finite(chart.estimatedDifficulty) || chart.level < (bucket.suggestedLevel as number) || chart.estimatedDifficulty < prior || !(chart.personalScore === null || whole(chart.personalScore) && chart.personalScore <= 1_000_000) || !(chart.personalPlate === null || ["RG", "FG", "TG", "MG", "SG", "EG", "UG", "PG"].includes(chart.personalPlate as string))) return invalid();
      if (focal) {
        const bpm = focalBpm(chart as unknown as RadarSuggestion);
        const bin = bins.get(bucket.bpmBucketId)!;
        if (bpm === null || bin.min !== null && bpm < bin.min || bin.max !== null && bpm >= bin.max) return invalid();
      }
      ids.add(chart.chartId); prior = chart.estimatedDifficulty;
    }
  }
  if (!record(value.groups) || !exact(value.groups, RADAR_GROUPS.map(group => group.key))) return invalid();
  const partition = RADAR_GROUPS.flatMap(group => {
    const members = (value.groups as Record<string, unknown>)[group.key];
    if (!Array.isArray(members) || !members.every(key => typeof key === "string" && keys.has(key))) return invalid();
    if (group.key === "weaknesses" && members.length !== groupLimit || group.key === "strengths" && members.length > groupLimit) return invalid();
    return members as string[];
  });
  if (partition.length !== 18 || new Set(partition).size !== 18 || value.buckets.some(bucket => !(value.groups as Record<string, string[]>)[bucket.group].includes(bucket.key))) return invalid();
  for (const binId of binIds) for (const mode of ["singles", "doubles"]) if (value.buckets.filter(bucket => bucket.bpmBucketId === binId && bucket.mode === mode).length !== 1) return invalid();
  return value as unknown as SkillRadarResponse;
}
