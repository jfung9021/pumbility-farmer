import { readFile } from "node:fs/promises";
import path from "node:path";

import {
  chartMatchesRecommendationLevelRange,
  officialDifficulty,
  RECOMMENDATION_DISPLAY_COUNT,
  recommendationDifficultyOptions,
} from "./recommendation-filters.ts";
import type {
  ModeKey,
  PlayerRecommendationsResponse,
  RecommendationChart,
  RecommendationChartEstimate,
  RecommendationModeKey,
  RecommendationModeResult,
  RecommendationPlayer,
  RecommendationPlayersResponse,
  RecommendationScoreProgress,
  RecommendationTopScore,
} from "./types";


function boundedStandardMode(
  modeKey: Exclude<ModeKey, "coop">,
  mode: RecommendationModeResult,
): RecommendationModeResult {
  const bounded = (charts: RecommendationChart[] | undefined) => (charts ?? [])
    .filter((chart) => chartMatchesRecommendationLevelRange(
      chart,
      modeKey,
      mode.scoringRating,
    ));
  const filterCandidates = bounded(mode.filterCandidates);
  const topRecommendations = bounded(mode.topRecommendations)
    .slice(0, RECOMMENDATION_DISPLAY_COUNT);
  const maximumEstimatedDifficulty = typeof mode.scoringRating === "number"
    ? mode.scoringRating + RECOMMENDATION_UPPER_RADIUS
    : Number.NEGATIVE_INFINITY;
  return {
    ...mode,
    candidateCount: filterCandidates.filter(
      (chart) => chart.estimatedDifficulty <= maximumEstimatedDifficulty,
    ).length,
    filterCandidateCount: filterCandidates.length,
    filterCandidates,
    topRecommendations,
  };
}

function boundedRecommendationPlayer(
  player: PlayerRecommendationsResponse["player"],
): PlayerRecommendationsResponse["player"] {
  if (!player.modes.singles || !player.modes.doubles) return player;
  const singles = boundedStandardMode("singles", player.modes.singles);
  const doubles = boundedStandardMode("doubles", player.modes.doubles);
  const sourceCandidates = new Map(
    [
      ...(singles.filterCandidates ?? []),
      ...(doubles.filterCandidates ?? []),
    ].map((candidate) => [candidate.chartId, candidate] as const),
  );
  const sourceTopIds = new Set([
    ...singles.topRecommendations.map((candidate) => candidate.chartId),
    ...doubles.topRecommendations.map((candidate) => candidate.chartId),
  ]);
  const rawOverall = player.modes.overall;
  const overall = rawOverall
    ? {
        ...rawOverall,
        candidateCount: sourceTopIds.size,
        filterCandidateCount: (rawOverall.filterCandidates ?? []).filter(
          (candidate) => sourceCandidates.has(candidate.chartId),
        ).length,
        sourceRecommendationCounts: {
          singles: singles.topRecommendations.length,
          doubles: doubles.topRecommendations.length,
        },
        filterCandidates: (rawOverall.filterCandidates ?? []).flatMap((candidate) => {
          const source = sourceCandidates.get(candidate.chartId);
          return source
            ? [{ ...source, projectedGain: candidate.projectedGain }]
            : [];
        }),
        topRecommendations: rawOverall.topRecommendations.flatMap((candidate) => {
          const source = sourceCandidates.get(candidate.chartId);
          return source && sourceTopIds.has(candidate.chartId)
            ? [{ ...source, projectedGain: candidate.projectedGain }]
            : [];
        }).slice(0, RECOMMENDATION_DISPLAY_COUNT),
      }
    : undefined;
  return {
    ...player,
    modes: {
      ...player.modes,
      singles,
      doubles,
      ...(overall ? { overall } : {}),
    },
  };
}

function boundedRecommendationResponse(
  payload: PlayerRecommendationsResponse,
): PlayerRecommendationsResponse {
  return {
    ...payload,
    player: boundedRecommendationPlayer(payload.player),
  };
}


export function recommendationsForMode(
  payload: PlayerRecommendationsResponse,
  mode: RecommendationModeKey,
  difficulty = "",
): PlayerRecommendationsResponse {
  const boundedPayload = boundedRecommendationResponse(payload);
  const modePayload = boundedPayload.player.modes[mode];
  let selectedMode = modePayload;
  if (mode === "overall" && modePayload) {
    const difficultyOptions = recommendationDifficultyOptions(
      "overall",
      modePayload.filterCandidates ?? [],
    );
    const selectedDifficulty = difficulty
      ? difficultyOptions.find(
          (option) => option.toLocaleLowerCase() === difficulty.toLocaleLowerCase(),
        )
      : undefined;
    if (difficulty && !selectedDifficulty) {
      throw new RangeError("The requested Overall difficulty is unavailable.");
    }
    const { filterCandidates: allFilterCandidates, ...compactMode } = modePayload;
    selectedMode = {
      ...compactMode,
      difficultyOptions,
      ...(selectedDifficulty
        ? {
            filterCandidates: (allFilterCandidates ?? []).filter(
              (chart) => officialDifficulty(chart) === selectedDifficulty,
            ),
          }
        : {}),
    };
  }
  return {
    ...boundedPayload,
    player: {
      ...boundedPayload.player,
      modes: selectedMode ? { [mode]: selectedMode } : {},
    },
  };
}


export const LOCAL_RECOMMENDATIONS_PATH = path.join(
  process.cwd(),
  ".local-data",
  "piu-scores",
  "recommendations",
  "latest.json",
);

const FORBIDDEN_KEYS = new Set([
  "playerId",
  "userId",
  "gameTag",
  "email",
  "authorization",
  "apiKey",
  "token",
  "scores",
  "rawScore",
  "scoreId",
  "scoreDate",
]);
const TOP_SCORE_KEYS = new Set([
  "mode",
  "songName",
  "difficulty",
  "type",
  "level",
  "chartId",
  "imageUrl",
  "noteCount",
  "stepArtist",
  "bpmMin",
  "bpmMax",
  "estimatedDifficulty",
  "difficultyDelta",
  "difficultyCi95Low",
  "difficultyCi95High",
  "nContributors",
  "phoenix1Contributors",
  "phoenix2Contributors",
  "evidenceStatus",
  "pumbility",
  "grade",
  "plate",
  "plateCode",
]);
const OPTIONAL_TOP_SCORE_KEYS = new Set([
  "percentileScore",
  "percentileGrade",
  "percentilePlate",
  "percentilePlateCode",
  "percentileSupportCount",
]);
const COOP_TOP_SCORE_KEYS = new Set([
  ...[...TOP_SCORE_KEYS].filter((key) => key !== "pumbility"),
  "coopRating",
]);
const DEFAULT_DISPLAY_MINIMUM_OFFICIAL_LEVEL = 16;
const RECOMMENDATION_UPPER_RADIUS = 1.0;
const LOCAL_RECOMMENDATION_SCHEMA_VERSION = 25;

export type LocalRecommendationIndex = {
  schemaVersion?: number;
  generatedAtUtc: string;
  method: Record<string, unknown>;
  charts: RecommendationChartEstimate[];
  generationKey?: string;
  players: LocalRecommendationPlayerEntry[];
};

type LocalRecommendationPlayerEntry = RecommendationPlayer | {
  playerKey: string;
  username: string;
  displayName: string;
  eligibility: Record<ModeKey, boolean>;
  scoreProgress?: Partial<Record<ModeKey, RecommendationScoreProgress>>;
  shard: number;
};

export class LocalRecommendationsNotFoundError extends Error {}
export class LocalRecommendationsValidationError extends Error {}

function containsForbiddenKey(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsForbiddenKey);
  if (!value || typeof value !== "object") return false;
  return Object.entries(value).some(
    ([key, child]) => FORBIDDEN_KEYS.has(key) || containsForbiddenKey(child),
  );
}

function isNullableFiniteNumber(value: unknown): boolean {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

function isNullableNonnegativeInteger(value: unknown): boolean {
  return value === null
    || (typeof value === "number" && Number.isInteger(value) && value >= 0);
}

function isRecommendationTopScore(value: unknown): value is RecommendationTopScore {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const score = value as Record<string, unknown>;
  const requiredKeys = score.type === "CoOp" ? COOP_TOP_SCORE_KEYS : TOP_SCORE_KEYS;
  return [...requiredKeys].every((key) => key in score)
    && Object.keys(score).every(
      (key) => requiredKeys.has(key) || OPTIONAL_TOP_SCORE_KEYS.has(key),
    )
    && (score.mode === "Singles" || score.mode === "Doubles" || score.mode === "Co-op")
    && (score.type === "Single" || score.type === "Double" || score.type === "CoOp")
    && ((score.mode === "Singles" && score.type === "Single")
      || (score.mode === "Doubles" && score.type === "Double")
      || (score.mode === "Co-op" && score.type === "CoOp"))
    && typeof score.songName === "string"
    && typeof score.difficulty === "string"
    && typeof score.level === "number"
    && Number.isInteger(score.level)
    && score.level > 0
    && typeof score.chartId === "string"
    && (score.imageUrl === null || typeof score.imageUrl === "string")
    && isNullableNonnegativeInteger(score.noteCount)
    && (score.stepArtist === null || typeof score.stepArtist === "string")
    && isNullableFiniteNumber(score.bpmMin)
    && isNullableFiniteNumber(score.bpmMax)
    && isNullableFiniteNumber(score.estimatedDifficulty)
    && isNullableFiniteNumber(score.difficultyDelta)
    && isNullableFiniteNumber(score.difficultyCi95Low)
    && isNullableFiniteNumber(score.difficultyCi95High)
    && isNullableNonnegativeInteger(score.nContributors)
    && isNullableNonnegativeInteger(score.phoenix1Contributors)
    && isNullableNonnegativeInteger(score.phoenix2Contributors)
    && (
      score.evidenceStatus === null
      || score.evidenceStatus === "Published"
      || score.evidenceStatus === "Provisional"
      || score.evidenceStatus === "Insufficient"
      || score.evidenceStatus === "Unrated"
    )
    && (score.type === "CoOp"
      ? typeof score.coopRating === "number"
        && Number.isFinite(score.coopRating)
        && score.coopRating >= 0
        && score.pumbility === undefined
      : typeof score.pumbility === "number"
        && Number.isFinite(score.pumbility)
        && score.pumbility >= 0)
    && (score.grade === null || typeof score.grade === "string")
    && (score.plate === null || typeof score.plate === "string")
    && (score.plateCode === null || typeof score.plateCode === "string");
}

function hasValidTopScores(modes: unknown): boolean {
  if (!modes || typeof modes !== "object" || Array.isArray(modes)) return false;
  return Object.entries(modes).every(([modeKey, value]) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const topScores = (value as Record<string, unknown>).topScores;
    return Array.isArray(topScores)
      && (modeKey === "coop" || topScores.length <= 50)
      && topScores.every((score) => isRecommendationTopScore(score)
        && (modeKey !== "singles" || score.type === "Single")
        && (modeKey !== "doubles" || score.type === "Double")
        && (modeKey !== "coop" || score.type === "CoOp"));
  });
}

export function validateLocalRecommendationIndex(value: unknown): LocalRecommendationIndex {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new LocalRecommendationsValidationError(
      "The local recommendation index must be an object.",
    );
  }
  const payload = value as {
    schemaVersion?: unknown;
    generatedAtUtc?: unknown;
    method?: unknown;
    charts?: unknown;
    players?: unknown;
  };
  if (payload.schemaVersion !== LOCAL_RECOMMENDATION_SCHEMA_VERSION) {
    throw new LocalRecommendationsValidationError(
      `The local recommendation index is incompatible. Regenerate schema ${LOCAL_RECOMMENDATION_SCHEMA_VERSION} recommendations.`,
    );
  }
  if (
    typeof payload.generatedAtUtc !== "string"
    || !payload.method
    || typeof payload.method !== "object"
    || !Array.isArray(payload.charts)
    || !Array.isArray(payload.players)
    || containsForbiddenKey(payload)
  ) {
    throw new LocalRecommendationsValidationError(
      "The local recommendation index has an invalid or private shape.",
    );
  }
  for (const chart of payload.charts) {
    if (
      !chart
      || typeof chart !== "object"
      || typeof chart.chartId !== "string"
      || typeof chart.songName !== "string"
      || (chart.type !== "Single" && chart.type !== "Double" && chart.type !== "CoOp")
      || typeof chart.level !== "number"
      || typeof chart.estimatedDifficulty !== "number"
      || !Number.isFinite(chart.estimatedDifficulty)
    ) {
      throw new LocalRecommendationsValidationError(
        "The local recommendation index contains an invalid chart estimate.",
      );
    }
  }
  for (const player of payload.players) {
    const record = player as Record<string, unknown>;
    if (
      !player
      || typeof player !== "object"
      || typeof record.playerKey !== "string"
      || typeof record.username !== "string"
      || typeof record.displayName !== "string"
      || (
        (!record.modes || typeof record.modes !== "object" || !hasValidTopScores(record.modes))
        && (
          !record.eligibility
          || typeof record.eligibility !== "object"
          || !Number.isInteger(record.shard)
        )
      )
    ) {
      throw new LocalRecommendationsValidationError(
        "The local recommendation index contains an invalid player.",
      );
    }
  }
  return payload as LocalRecommendationIndex;
}

export async function readLocalRecommendationIndex(): Promise<LocalRecommendationIndex> {
  let raw: string;
  try {
    raw = await readFile(LOCAL_RECOMMENDATIONS_PATH, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      throw new LocalRecommendationsNotFoundError(
        "No local recommendations have been generated yet.",
      );
    }
    throw error;
  }
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new LocalRecommendationsValidationError(
      "The local recommendation index is not valid JSON.",
    );
  }
  return validateLocalRecommendationIndex(value);
}

export async function readLocalRecommendationPlayer(
  payload: LocalRecommendationIndex,
  playerKey: string,
): Promise<RecommendationPlayer | null> {
  const metadata = payload.players.find((row) => row.playerKey === playerKey);
  if (!metadata) return null;
  if ("modes" in metadata) return metadata;
  if (
    !payload.generationKey
    || !/^[a-f0-9]{20}$/.test(payload.generationKey)
    || !Number.isInteger(metadata.shard)
    || metadata.shard < 0
  ) {
    throw new LocalRecommendationsValidationError(
      "The local recommendation shard reference is invalid.",
    );
  }
  const shardPath = path.join(
    path.dirname(LOCAL_RECOMMENDATIONS_PATH),
    "generations",
    payload.generationKey,
    "shards",
    `${String(metadata.shard).padStart(4, "0")}.json`,
  );
  let value: unknown;
  try {
    value = JSON.parse(await readFile(shardPath, "utf8"));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      throw new LocalRecommendationsValidationError(
        "The selected local recommendation shard is missing.",
      );
    }
    throw error;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new LocalRecommendationsValidationError(
      "The selected local recommendation shard is invalid.",
    );
  }
  const shard = value as {
    generationKey?: unknown;
    players?: unknown;
  };
  if (
    shard.generationKey !== payload.generationKey
    || !Array.isArray(shard.players)
    || containsForbiddenKey(shard)
  ) {
    throw new LocalRecommendationsValidationError(
      "The selected local recommendation shard is invalid.",
    );
  }
  const player = shard.players.find(
    (row): row is RecommendationPlayer => Boolean(
      row
      && typeof row === "object"
      && (row as RecommendationPlayer).playerKey === playerKey
      && typeof (row as RecommendationPlayer).username === "string"
      && typeof (row as RecommendationPlayer).displayName === "string"
      && (row as RecommendationPlayer).modes
      && typeof (row as RecommendationPlayer).modes === "object",
    ),
  );
  if (!player) {
    throw new LocalRecommendationsValidationError(
      "The selected local recommendation is missing from its shard.",
    );
  }
  if (!hasValidTopScores(player.modes)) {
    throw new LocalRecommendationsValidationError(
      "The selected local recommendation contains invalid top scores.",
    );
  }
  return player;
}

function manualMode(
  charts: RecommendationChartEstimate[],
  modeKey: ModeKey,
  scoringRating: number,
  minimumOfficialLevel: number,
): RecommendationModeResult {
  const chartType = modeKey === "singles"
    ? "Single"
    : modeKey === "doubles" ? "Double" : "CoOp";
  const maximumEstimatedDifficulty = scoringRating + RECOMMENDATION_UPPER_RADIUS;
  const filterCandidates: RecommendationChart[] = charts
    .filter(
      (chart) => chart.type === chartType
        && chartMatchesRecommendationLevelRange(chart, modeKey, scoringRating),
    )
    .map((chart) => {
      const farmEdge = chart.level + 0.5 - chart.estimatedDifficulty;
      return {
        ...chart,
        distanceFromRating: Number((chart.estimatedDifficulty - scoringRating).toFixed(6)),
        farmEdge: Number(farmEdge.toFixed(6)),
        existingPumbility: null,
        expectedPumbility: null,
        projectedGain: null,
        projectedScore: null,
        projectedGrade: null,
        projectedPlate: null,
        projectedPlateCode: null,
        projectedPlateProbability: null,
        plateProjectionSource: null,
        played: false,
      };
    })
    .sort((left, right) =>
      left.estimatedDifficulty - right.estimatedDifficulty
      || left.songName.localeCompare(right.songName)
      || left.chartId.localeCompare(right.chartId),
    );
  filterCandidates.sort((left, right) =>
    right.farmEdge - left.farmEdge
    || left.estimatedDifficulty - right.estimatedDifficulty
    || left.songName.localeCompare(right.songName)
    || left.chartId.localeCompare(right.chartId),
  );
  const defaultCandidates = filterCandidates.filter(
    (chart) => chart.level >= minimumOfficialLevel
      && chart.estimatedDifficulty <= maximumEstimatedDifficulty,
  );
  const topRecommendations = [...defaultCandidates]
    .sort((left, right) =>
      right.farmEdge - left.farmEdge
      || left.estimatedDifficulty - right.estimatedDifficulty
      || left.songName.localeCompare(right.songName)
      || left.chartId.localeCompare(right.chartId),
    )
    .slice(0, 50);
  return {
    eligible: true,
    manual: true,
    projectionAvailable: false,
    validScoreCount: 0,
    scoringRating: Number(scoringRating.toFixed(3)),
    candidateRange: [
      null,
      Number(maximumEstimatedDifficulty.toFixed(3)),
    ],
    candidateCount: defaultCandidates.length,
    filterCandidateCount: filterCandidates.length,
    filterCandidates,
    topScores: [],
    topRecommendations,
  };
}

function manualOverallMode(
  singles: RecommendationModeResult,
  doubles: RecommendationModeResult,
): RecommendationModeResult {
  const sourceRecommendations = [
    ...singles.topRecommendations,
    ...doubles.topRecommendations,
  ];
  const filterCandidates = [
    ...(singles.filterCandidates ?? []),
    ...(doubles.filterCandidates ?? []),
  ].sort((left, right) =>
    right.farmEdge - left.farmEdge
    || left.estimatedDifficulty - right.estimatedDifficulty
    || left.songName.localeCompare(right.songName)
    || left.chartId.localeCompare(right.chartId),
  );
  const topRecommendations = [...sourceRecommendations]
    .sort((left, right) =>
      right.farmEdge - left.farmEdge
      || left.estimatedDifficulty - right.estimatedDifficulty
      || left.songName.localeCompare(right.songName)
      || left.chartId.localeCompare(right.chartId),
    )
    .slice(0, 50);
  return {
    eligible: true,
    manual: true,
    validScoreCount: 0,
    projectionAvailable: false,
    currentTop50Pumbility: 0,
    currentTop50CutoffPumbility: null,
    currentTop50Count: 0,
    top50ModeCounts: { singles: 0, doubles: 0 },
    sourceModeEligibility: { singles: true, doubles: true },
    sourceRecommendationCounts: {
      singles: singles.topRecommendations.length,
      doubles: doubles.topRecommendations.length,
    },
    candidateCount: sourceRecommendations.length,
    filterCandidateCount: filterCandidates.length,
    filterCandidates,
    topScores: [],
    topRecommendations,
  };
}

export function recommendationsForRating(
  payload: Awaited<ReturnType<typeof readLocalRecommendationIndex>>,
  scoringRating: number,
): PlayerRecommendationsResponse {
  const configuredMinimum = payload.method.displayMinimumOfficialLevel;
  const minimumOfficialLevel = typeof configuredMinimum === "number"
    && Number.isFinite(configuredMinimum)
    ? configuredMinimum
    : DEFAULT_DISPLAY_MINIMUM_OFFICIAL_LEVEL;
  const singles = manualMode(
    payload.charts,
    "singles",
    scoringRating,
    minimumOfficialLevel,
  );
  const doubles = manualMode(
    payload.charts,
    "doubles",
    scoringRating,
    minimumOfficialLevel,
  );
  const coop: RecommendationModeResult = {
    eligible: false,
    manual: true,
    projectionAvailable: false,
    validScoreCount: 0,
    reason: "Co-op recommendations require a selected player.",
    filterCandidates: [],
    topScores: [],
    topRecommendations: [],
  };
  return {
    generatedAtUtc: payload.generatedAtUtc,
    modelGeneratedAtUtc: payload.generatedAtUtc,
    method: payload.method,
    player: {
      playerKey: "manual",
      username: "",
      displayName: `Manual ${scoringRating.toFixed(2)}`,
      manual: true,
      modes: {
        overall: manualOverallMode(singles, doubles),
        singles,
        doubles,
        coop,
      },
    },
  };
}

export function recommendationPlayerList(
  payload: Awaited<ReturnType<typeof readLocalRecommendationIndex>>,
): RecommendationPlayersResponse {
  return {
    generatedAtUtc: payload.generatedAtUtc,
    modelGeneratedAtUtc: payload.generatedAtUtc,
    refreshSupported: false,
    method: payload.method,
    players: payload.players.map((player) => ({
      playerKey: player.playerKey,
      username: player.username,
      displayName: player.displayName,
      eligibility: {
        singles: "modes" in player
          ? Boolean(player.modes.singles?.eligible)
          : Boolean(player.eligibility.singles),
        doubles: "modes" in player
          ? Boolean(player.modes.doubles?.eligible)
          : Boolean(player.eligibility.doubles),
        coop: "modes" in player
          ? Boolean(player.modes.coop?.eligible)
          : Boolean(player.eligibility.coop),
      },
      scoreProgress: "modes" in player
        ? {
            singles: {
              validScoreCount: player.modes.singles?.projectionAvailable
                ? player.modes.singles?.projectionRatingSourceScoreCount ?? 0
                : player.modes.singles?.phoenix2ScoreCount
                  ?? player.modes.singles?.validScoreCount
                  ?? 0,
              requiredScoreCount:
                player.modes.singles?.projectionRatingRequiredScoreCount
                ?? player.modes.singles?.requiredScoreCount
                ?? 30,
            },
            doubles: {
              validScoreCount: player.modes.doubles?.projectionAvailable
                ? player.modes.doubles?.projectionRatingSourceScoreCount ?? 0
                : player.modes.doubles?.phoenix2ScoreCount
                  ?? player.modes.doubles?.validScoreCount
                  ?? 0,
              requiredScoreCount:
                player.modes.doubles?.projectionRatingRequiredScoreCount
                ?? player.modes.doubles?.requiredScoreCount
                ?? 30,
            },
          }
        : player.scoreProgress,
    })),
  };
}

export function recommendationsForPlayer(
  payload: Awaited<ReturnType<typeof readLocalRecommendationIndex>>,
  playerKey: string,
  loadedPlayer?: RecommendationPlayer | null,
): PlayerRecommendationsResponse | null {
  const indexedPlayer = payload.players.find((row) => row.playerKey === playerKey);
  const player = loadedPlayer ?? (
    indexedPlayer && "modes" in indexedPlayer ? indexedPlayer : null
  );
  return player
    ? boundedRecommendationResponse({
        generatedAtUtc: payload.generatedAtUtc,
        recommendationsGeneratedAtUtc: payload.generatedAtUtc,
        modelGeneratedAtUtc: payload.generatedAtUtc,
        playerSyncedAtUtc: payload.generatedAtUtc,
        legacySnapshot: true,
        method: payload.method,
        player,
      })
    : null;
}
