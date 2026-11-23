import { gradeForScore } from "./score-appearance.ts";

export const PRO_RECOMMENDATION_METHOD = "pro-pumbility-p50-modes-overall-top50-phoenix2-v6";
export type ProModeKey = "singles" | "doubles";
export type ProModeView = "overall" | ProModeKey;
export type ProRecommendationStatus = "ready" | "identity-unresolved" | "ineligible" | "insufficient-data" | "unavailable";
export interface ProPlayerSummary {
  playerKey: string; displayName: string; officialDisplayName: string | null;
  overallRank: number | null; overallPumbility: number | null;
  status: ProRecommendationStatus; eligible?: boolean;
  modes: Record<ProModeKey, { status: ProRecommendationStatus; modePumbility: number | null }>;
}
interface ProMetadata {
  schemaVersion: 2; method: typeof PRO_RECOMMENDATION_METHOD; mix: "Phoenix2"; scope: "mode-consolidated";
  generation: string; snapshotAt: string; fetchedAtUtc: string; generatedAtUtc: string;
}
export interface ProPlayersResponse extends ProMetadata { players: ProPlayerSummary[] }
export interface ProTopScore {
  chartId: string; songName: string; chartLabel: string; type: "Single" | "Double"; level: number;
  score: number | null; grade: string | null; plate: string | null; plateEstimated: boolean; pumbility: number;
}
export interface ProBaseline {
  mode: ProModeView; source: "piuscores" | "official-leaderboard" | "mixed"; estimated: boolean;
  scoreCount: number; top50Pumbility: number; cutoffPumbility: number | null;
  syncedAtUtc: string; snapshotAt: string; fallbackReason: string | null; top50: ProTopScore[];
}
export interface ProRecommendationChart {
  chartId: string; songName: string; chartLabel: string; type: "Single" | "Double"; level: number; sourceMode: ProModeKey;
  imageUrl?: string | null; stepArtist?: string | null; bpmMin?: number | null; bpmMax?: number | null;
  rawPercentileScore: number; gradeBoundaryScore: number; targetScore: number; targetGrade: string;
  assumedPlate: "MG" | "SG" | "PG"; expectedPumbility: number; projectedGain: number;
  currentScore: number | null; currentPumbility: number | null; currentPlate: string | null; currentPlateEstimated: boolean;
  peerScoreCount: number; cohortPlayerCount: number; boardPossiblyTruncated: boolean; limitedData: boolean;
}
export interface ProModeResult {
  mode: ProModeKey; generation: string; status: ProRecommendationStatus; message: string | null;
  modePumbility: number | null; baseline: ProBaseline | null;
  cohort: { minimumPlayers: 10; upwardWindow: 100 | 150 | 200; backfillCount: number; playerCount: number;
    minimumPumbility: number | null; maximumPumbility: number | null; limitedData: boolean } | null;
  recommendations: ProRecommendationChart[]; candidateCount: number; unobservedChartCount: number;
}
export interface ProOverallRecommendationChart extends ProRecommendationChart {
  modeProjectedGain: number; replacedPumbility: number;
}
export interface ProRecommendationsResponse extends ProMetadata {
  status: ProRecommendationStatus; message: string | null; eligible: boolean;
  player: Omit<ProPlayerSummary, "status" | "eligible" | "modes">;
  modes: Record<ProModeKey, ProModeResult>;
  overall: { kind: "overall-top50"; generation: string; sourceGenerations: Record<ProModeKey, string>; baseline: ProBaseline | null;
    availableModes: ProModeKey[]; unavailableModes: ProModeKey[]; recommendations: ProOverallRecommendationChart[] };
}
export class ProRecommendationValidationError extends Error {}
const MODES: ProModeKey[] = ["singles", "doubles"];
const STATUSES = new Set<unknown>(["ready", "identity-unresolved", "ineligible", "insufficient-data", "unavailable"]);
const object = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === "string" && v.length > 0;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
const count = (v: unknown): v is number => finite(v) && Number.isInteger(v);
const date = (v: unknown): v is string => text(v) && /(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const nullable = <T>(v: unknown, check: (v: unknown) => v is T): v is T | null => v === null || check(v);
export const isProPlayerKey = (v: unknown): v is string => typeof v === "string" && /^(?:[0-9a-f]{20}|op_[0-9a-f]{64})$/.test(v);
function invalid(message = "The Pro mode data is invalid. Run npm run pro:refresh to build the current Phoenix 2 mode datasets."): never {
  throw new ProRecommendationValidationError(message);
}
function metadata(v: unknown): asserts v is Record<string, unknown> {
  if (!object(v) || v.schemaVersion !== 2 || v.method !== PRO_RECOMMENDATION_METHOD || v.mix !== "Phoenix2"
    || v.scope !== "mode-consolidated" || !text(v.generation) || !/^[0-9a-f]{64}$/.test(v.generation)
    || !date(v.snapshotAt) || !date(v.fetchedAtUtc) || !date(v.generatedAtUtc)) invalid();
}
function player(v: unknown): v is Record<string, unknown> {
  return object(v) && isProPlayerKey(v.playerKey) && text(v.displayName) && nullable(v.officialDisplayName, text)
    && nullable(v.overallRank, count) && (v.overallRank === null || Number(v.overallRank) > 0) && nullable(v.overallPumbility, finite);
}
export function validateProPlayersResponse(v: unknown): ProPlayersResponse {
  metadata(v);
  if (!Array.isArray(v.players)) invalid();
  const seen = new Set<string>();
  for (const row of v.players) {
    if (!player(row) || !STATUSES.has(row.status) || seen.has(String(row.playerKey)) || !object(row.modes)) invalid();
    for (const mode of MODES) {
      const state = row.modes[mode];
      if (!object(state) || !STATUSES.has(state.status) || !nullable(state.modePumbility, finite)) invalid();
    }
    seen.add(String(row.playerKey));
  }
  return v as unknown as ProPlayersResponse;
}
function baseline(v: unknown, mode: ProModeView): void {
  if (!object(v) || v.mode !== mode || !["piuscores", "official-leaderboard", "mixed"].includes(String(v.source))
    || typeof v.estimated !== "boolean" || !count(v.scoreCount) || !finite(v.top50Pumbility)
    || !nullable(v.cutoffPumbility, finite) || !date(v.snapshotAt) || !date(v.syncedAtUtc)
    || !nullable(v.fallbackReason, text) || !Array.isArray(v.top50) || v.top50.length !== Math.min(50, v.scoreCount)) invalid();
  const ids = new Set<string>(); let total = 0; let previous = Infinity;
  for (const row of v.top50) {
    if (!object(row) || !text(row.chartId) || ids.has(row.chartId) || !text(row.songName) || !text(row.chartLabel)
      || (mode === "overall" ? !["Single", "Double"].includes(String(row.type)) : row.type !== (mode === "singles" ? "Single" : "Double")) || !count(row.level)
      || !nullable(row.score, finite) || !nullable(row.grade, text) || !nullable(row.plate, text)
      || typeof row.plateEstimated !== "boolean" || !finite(row.pumbility) || row.pumbility > previous) invalid();
    ids.add(row.chartId); previous = row.pumbility; total += Math.round(row.pumbility * 100);
  }
  if (Math.abs(total / 100 - v.top50Pumbility) > .001 || (v.top50.length === 50 ? v.cutoffPumbility !== previous : v.cutoffPumbility !== null)) invalid();
}
function modeResult(v: unknown, mode: ProModeKey, generation: unknown): asserts v is Record<string, unknown> {
  if (!object(v) || v.mode !== mode || v.generation !== generation || !STATUSES.has(v.status)
    || !nullable(v.message, text) || !nullable(v.modePumbility, finite) || !Array.isArray(v.recommendations)
    || !count(v.candidateCount) || !count(v.unobservedChartCount)) invalid();
  if (v.baseline !== null) baseline(v.baseline, mode);
  const cohort = v.cohort;
  if (cohort !== null && (!object(cohort) || cohort.minimumPlayers !== 10 || ![100,150,200].includes(Number(cohort.upwardWindow))
    || !count(cohort.playerCount) || !count(cohort.backfillCount) || cohort.backfillCount > cohort.playerCount
    || !nullable(cohort.minimumPumbility, finite) || !nullable(cohort.maximumPumbility, finite) || typeof cohort.limitedData !== "boolean")) invalid();
  if (v.status === "ready" && (!object(v.baseline) || Number(v.baseline.scoreCount) < 50 || v.baseline.cutoffPumbility === null
    || v.modePumbility !== v.baseline.top50Pumbility || !object(cohort))) invalid();
  if (v.status !== "ready" && v.recommendations.length) invalid();
  const ids = new Set<string>();
  for (const row of v.recommendations) {
    if (!object(row) || !text(row.chartId) || ids.has(row.chartId) || !text(row.songName) || !text(row.chartLabel)
      || row.sourceMode !== mode || row.type !== (mode === "singles" ? "Single" : "Double") || !count(row.level)
      || row.level < (mode === "singles" ? 24 : 25) || !finite(row.rawPercentileScore) || row.rawPercentileScore > 1_000_000
      || !count(row.gradeBoundaryScore) || !count(row.targetScore) || row.targetScore > 1_000_000
      || !text(row.targetGrade) || gradeForScore(row.rawPercentileScore) !== row.targetGrade || !finite(row.expectedPumbility)
      || !finite(row.projectedGain) || row.projectedGain <= 0 || !nullable(row.currentScore, finite)
      || !nullable(row.currentPumbility, finite) || !nullable(row.currentPlate, text) || typeof row.currentPlateEstimated !== "boolean"
      || !count(row.peerScoreCount) || !count(row.cohortPlayerCount) || row.peerScoreCount > row.cohortPlayerCount
      || !object(cohort) || row.cohortPlayerCount !== cohort.playerCount || typeof row.boardPossiblyTruncated !== "boolean"
      || typeof row.limitedData !== "boolean") invalid();
    const plate = row.rawPercentileScore === 1_000_000 ? "PG" : row.rawPercentileScore > 995_000 ? "SG" : "MG";
    if (row.assumedPlate !== plate || (plate === "PG" && row.targetScore !== 1_000_000)) invalid();
    ids.add(row.chartId);
  }
}
export function validateProRecommendationsResponse(v: unknown, index?: ProPlayersResponse, playerKey?: string): ProRecommendationsResponse {
  metadata(v);
  if (!player(v.player) || !STATUSES.has(v.status) || !nullable(v.message, text) || typeof v.eligible !== "boolean" || !object(v.modes)
    || !object(v.overall) || "baseline" in v || "cohort" in v || "recommendations" in v) invalid();
  const selected = v.player;
  if (playerKey && selected.playerKey !== playerKey) invalid("The Pro player does not match the selection.");
  if (index && (index.generation !== v.generation || index.snapshotAt !== v.snapshotAt || !index.players.some(row => row.playerKey === selected.playerKey))) invalid("The Pro mode capture changed. Reload the player list.");
  if (v.eligible && (!count(selected.overallRank) || selected.overallRank < 1 || selected.overallRank > 300)) invalid();
  const sourceRows = new Map<string, Record<string, unknown>>(); const available: string[] = []; const unavailable: string[] = [];
  for (const mode of MODES) {
    const result = v.modes[mode]; modeResult(result, mode, v.generation);
    (result.status === "ready" ? available : unavailable).push(mode);
    for (const row of result.recommendations as Record<string, unknown>[]) {
      if (sourceRows.has(String(row.chartId))) invalid();
      sourceRows.set(String(row.chartId), row);
    }
  }
  const overall = v.overall;
  if (overall.kind !== "overall-top50" || overall.generation !== v.generation || !object(overall.sourceGenerations)
    || MODES.some(mode => (overall.sourceGenerations as Record<string,unknown>)[mode] !== v.generation)
    || JSON.stringify(overall.availableModes) !== JSON.stringify(available) || JSON.stringify(overall.unavailableModes) !== JSON.stringify(unavailable)
    || !Array.isArray(overall.recommendations)) invalid();
  const modeBaselines = MODES.map(mode => (v.modes as Record<string,Record<string,unknown>>)[mode].baseline).filter(object);
  if (overall.baseline !== null) baseline(overall.baseline, "overall");
  if ((modeBaselines.length > 0) !== (overall.baseline !== null)) invalid();
  const combinedTop50 = modeBaselines.flatMap(base => base.top50 as Record<string,unknown>[])
    .sort((a,b) => Number(b.pumbility)-Number(a.pumbility) || (String(a.chartId)<String(b.chartId)?-1:String(a.chartId)>String(b.chartId)?1:0)).slice(0,50);
  if (object(overall.baseline) && (overall.baseline.scoreCount !== modeBaselines.reduce((n,base)=>n+Number(base.scoreCount),0)
    || JSON.stringify(overall.baseline.top50) !== JSON.stringify(combinedTop50))) invalid("Overall must reconstruct the combined Top 50.");
  const cutoff = object(overall.baseline) ? overall.baseline.cutoffPumbility : null;
  const topValues = new Map(combinedTop50.map(row=>[String(row.chartId),Number(row.pumbility)]));
  const expected = new Map<string, {gain:number;replacement:number}>();
  if (finite(cutoff)) for (const [id,source] of sourceRows) {
    const replacement = topValues.get(id) ?? cutoff;
    const gain = Math.max(0,Math.round(Number(source.expectedPumbility)*100)-Math.round(replacement*100))/100;
    if (gain > 0) expected.set(id,{gain,replacement});
  }
  if (overall.recommendations.length !== expected.size) invalid("Overall must include exactly the targets that improve its Top 50.");
  const seen = new Set<string>(); let lastGain = Infinity;
  for (const row of overall.recommendations) {
    if (!object(row) || !text(row.chartId) || seen.has(row.chartId)) invalid();
    const source = sourceRows.get(row.chartId);
    const estimate = expected.get(row.chartId);
    if (!source || !estimate || Object.keys(source).length+2 !== Object.keys(row).length
      || Object.keys(source).some(key => key !== "projectedGain" && source[key] !== row[key])
      || row.modeProjectedGain !== source.projectedGain || row.replacedPumbility !== estimate.replacement || row.projectedGain !== estimate.gain
      || !finite(row.projectedGain) || row.projectedGain > lastGain) invalid("Overall must preserve mode targets and calculate gains against its combined Top 50.");
    lastGain = row.projectedGain; seen.add(row.chartId);
  }
  return v as unknown as ProRecommendationsResponse;
}
