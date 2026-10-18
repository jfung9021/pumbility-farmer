export type RadarMode = "singles" | "doubles";
export type RadarGroup = "weaknesses" | "strengths" | "everythingElse";

export interface RadarBpmBucket {
  id: string;
  label: string;
  min: number | null;
  max: number | null;
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
  estimatedDifficulty: number;
  personalScore: number | null;
  personalPlate: string | null;
}

export interface RadarBucket {
  key: string;
  mode: RadarMode;
  bpmBucketId: string;
  label: string;
  rawPg: number;
  displayPg: number;
  radarScore: number;
  usableCharts: number;
  selectedCharts: number;
  needsPlays: boolean;
  minimumSuggestedDifficulty: number;
  group: RadarGroup;
  suggestions: RadarSuggestion[];
}

export interface SkillRadarResponse {
  methodVersion: 1;
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

export function radarPoint(index: number, score: number, radius = 132) {
  const angle = -Math.PI / 2 + index * 2 * Math.PI / 9;
  return {x:200 + Math.cos(angle) * radius * score / 100, y:200 + Math.sin(angle) * radius * score / 100};
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
export function validateSkillRadarResponse(value: unknown, expectedPlayerKey?: string): SkillRadarResponse {
  const invalid = () => { throw new Error("The Skill Radar data is unavailable or invalid."); };
  if (!record(value) || !exact(value, ["methodVersion", "player", "generatedAtUtc", "tierGeneratedAtUtc", "bpmBuckets", "buckets", "groups"], ["playerSyncedAtUtc", "revision"]) || value.methodVersion !== 1 || value.revision !== undefined && typeof value.revision !== "string") return invalid();
  if (!record(value.player) || !exact(value.player, ["playerKey", "displayName"]) || typeof value.player.playerKey !== "string" || !PLAYER_KEY_PATTERN.test(value.player.playerKey) || typeof value.player.displayName !== "string" || expectedPlayerKey && value.player.playerKey !== expectedPlayerKey) return invalid();
  for (const key of ["generatedAtUtc", "tierGeneratedAtUtc"]) if (typeof value[key] !== "string" || !Number.isFinite(Date.parse(value[key] as string))) return invalid();
  if (value.playerSyncedAtUtc !== undefined && value.playerSyncedAtUtc !== null && (typeof value.playerSyncedAtUtc !== "string" || !Number.isFinite(Date.parse(value.playerSyncedAtUtc)))) return invalid();
  if (!Array.isArray(value.bpmBuckets) || value.bpmBuckets.length !== 9 || !value.bpmBuckets.every(bin => record(bin) && exact(bin, ["id", "label", "min", "max"]) && typeof bin.id === "string" && typeof bin.label === "string" && optionalNumber(bin.min) && optionalNumber(bin.max))) return invalid();
  const binIds = new Set(value.bpmBuckets.map(bin => bin.id));
  if (binIds.size !== 9 || !Array.isArray(value.buckets) || value.buckets.length !== 18) return invalid();
  const keys = new Set<string>();
  for (const bucket of value.buckets) {
    if (!record(bucket) || !exact(bucket, ["key", "mode", "bpmBucketId", "label", "rawPg", "displayPg", "radarScore", "usableCharts", "selectedCharts", "needsPlays", "minimumSuggestedDifficulty", "group", "suggestions"]) || typeof bucket.key !== "string" || typeof bucket.bpmBucketId !== "string" || !binIds.has(bucket.bpmBucketId) || (bucket.mode !== "singles" && bucket.mode !== "doubles") || typeof bucket.label !== "string" || keys.has(bucket.key)) return invalid();
    keys.add(bucket.key);
    if (![bucket.rawPg, bucket.displayPg, bucket.radarScore, bucket.minimumSuggestedDifficulty].every(finite) || (bucket.rawPg as number) < 0 || !whole(bucket.usableCharts) || !whole(bucket.selectedCharts) || bucket.selectedCharts > 5 || bucket.usableCharts < bucket.selectedCharts || bucket.needsPlays !== (bucket.selectedCharts < 5)) return invalid();
    if (!close(bucket.displayPg as number, Math.max(0, (bucket.rawPg as number) - (bucket.mode === "doubles" ? 1 : 0))) || !close(bucket.radarScore as number, Math.max(0, Math.min(100, 100 * ((bucket.displayPg as number) - 10) / 15))) || !close(bucket.minimumSuggestedDifficulty as number, (bucket.rawPg as number) + 1.5)) return invalid();
    if (!RADAR_GROUPS.some(group => group.key === bucket.group) || !Array.isArray(bucket.suggestions) || bucket.suggestions.length > 5) return invalid();
    const ids = new Set<string>();
    let prior = -Infinity;
    for (const chart of bucket.suggestions) {
      if (!record(chart) || !exact(chart, ["chartId", "songName", "difficulty", "type", "level", "imageUrl", "bpmMin", "bpmMax", "estimatedDifficulty", "personalScore", "personalPlate"]) || typeof chart.chartId !== "string" || ids.has(chart.chartId) || typeof chart.songName !== "string" || typeof chart.difficulty !== "string" || chart.type !== (bucket.mode === "singles" ? "Single" : "Double") || !whole(chart.level) || !(chart.imageUrl === null || typeof chart.imageUrl === "string") || !optionalNumber(chart.bpmMin) || !optionalNumber(chart.bpmMax) || !finite(chart.estimatedDifficulty) || chart.estimatedDifficulty < (bucket.minimumSuggestedDifficulty as number) || chart.estimatedDifficulty < prior || !(chart.personalScore === null || whole(chart.personalScore) && chart.personalScore <= 1_000_000) || !(chart.personalPlate === null || ["RG", "FG", "TG", "MG", "SG", "EG", "UG", "PG"].includes(chart.personalPlate as string))) return invalid();
      ids.add(chart.chartId); prior = chart.estimatedDifficulty;
    }
  }
  if (!record(value.groups) || !exact(value.groups, RADAR_GROUPS.map(group => group.key))) return invalid();
  const partition = RADAR_GROUPS.flatMap(group => {
    const members = (value.groups as Record<string, unknown>)[group.key];
    if (!Array.isArray(members) || !members.every(key => typeof key === "string" && keys.has(key))) return invalid();
    if (group.key === "weaknesses" && members.length !== 3 || group.key === "strengths" && members.length > 3) return invalid();
    return members as string[];
  });
  if (partition.length !== 18 || new Set(partition).size !== 18 || value.buckets.some(bucket => !(value.groups as Record<string, string[]>)[bucket.group].includes(bucket.key))) return invalid();
  for (const binId of binIds) for (const mode of ["singles", "doubles"]) if (value.buckets.filter(bucket => bucket.bpmBucketId === binId && bucket.mode === mode).length !== 1) return invalid();
  return value as unknown as SkillRadarResponse;
}
