import { readFile } from "node:fs/promises";
import path from "node:path";

import type {
  ModeKey,
  PlayerRecommendationsResponse,
  RecommendationChart,
  RecommendationChartEstimate,
  RecommendationModeResult,
  RecommendationPlayer,
  RecommendationPlayersResponse,
} from "./types";


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
]);
const DEFAULT_DISPLAY_MINIMUM_OFFICIAL_LEVEL = 16;

export class LocalRecommendationsNotFoundError extends Error {}
export class LocalRecommendationsValidationError extends Error {}

function containsForbiddenKey(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsForbiddenKey);
  if (!value || typeof value !== "object") return false;
  return Object.entries(value).some(
    ([key, child]) => FORBIDDEN_KEYS.has(key) || containsForbiddenKey(child),
  );
}

export async function readLocalRecommendationIndex(): Promise<{
  generatedAtUtc: string;
  method: Record<string, unknown>;
  charts: RecommendationChartEstimate[];
  players: RecommendationPlayer[];
}> {
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
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new LocalRecommendationsValidationError(
      "The local recommendation index must be an object.",
    );
  }
  const payload = value as {
    generatedAtUtc?: unknown;
    method?: unknown;
    charts?: unknown;
    players?: unknown;
  };
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
      || (chart.type !== "Single" && chart.type !== "Double")
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
    if (
      !player
      || typeof player !== "object"
      || typeof player.playerKey !== "string"
      || typeof player.username !== "string"
      || typeof player.displayName !== "string"
      || !player.modes
      || typeof player.modes !== "object"
    ) {
      throw new LocalRecommendationsValidationError(
        "The local recommendation index contains an invalid player.",
      );
    }
  }
  return payload as {
    generatedAtUtc: string;
    method: Record<string, unknown>;
    charts: RecommendationChartEstimate[];
    players: RecommendationPlayer[];
  };
}

function manualMode(
  charts: RecommendationChartEstimate[],
  modeKey: ModeKey,
  scoringRating: number,
  minimumOfficialLevel: number,
): RecommendationModeResult {
  const chartType = modeKey === "singles" ? "Single" : "Double";
  const candidates: RecommendationChart[] = charts
    .filter(
      (chart) => chart.type === chartType
        && chart.level >= minimumOfficialLevel
        && chart.estimatedDifficulty <= scoringRating + 0.5 + Number.EPSILON,
    )
    .map((chart) => {
      const farmEdge = chart.level + 0.5 - chart.estimatedDifficulty;
      return {
        ...chart,
        distanceFromRating: Number((chart.estimatedDifficulty - scoringRating).toFixed(6)),
        farmEdge: Number(farmEdge.toFixed(6)),
        existingPumbility: null,
        expectedPumbility: 0,
        projectedGain: 0,
        projectedScore: null,
        played: false,
      };
    })
    .sort((left, right) =>
      left.estimatedDifficulty - right.estimatedDifficulty
      || left.songName.localeCompare(right.songName)
      || left.chartId.localeCompare(right.chartId),
    );
  const topRecommendations = [...candidates]
    .sort((left, right) =>
      right.farmEdge - left.farmEdge
      || left.estimatedDifficulty - right.estimatedDifficulty
      || left.songName.localeCompare(right.songName)
      || left.chartId.localeCompare(right.chartId),
    )
    .slice(0, 20);
  return {
    eligible: true,
    manual: true,
    validScoreCount: 0,
    scoringRating: Number(scoringRating.toFixed(3)),
    candidateRange: [
      null,
      Number((scoringRating + 0.5).toFixed(3)),
    ],
    candidateCount: candidates.length,
    candidates,
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
  return {
    generatedAtUtc: payload.generatedAtUtc,
    method: payload.method,
    player: {
      playerKey: "manual",
      username: "",
      displayName: `Manual ${scoringRating.toFixed(2)}`,
      manual: true,
      modes: {
        singles: manualMode(payload.charts, "singles", scoringRating, minimumOfficialLevel),
        doubles: manualMode(payload.charts, "doubles", scoringRating, minimumOfficialLevel),
      },
    },
  };
}

export function recommendationPlayerList(
  payload: Awaited<ReturnType<typeof readLocalRecommendationIndex>>,
): RecommendationPlayersResponse {
  return {
    generatedAtUtc: payload.generatedAtUtc,
    method: payload.method,
    players: payload.players.map((player) => ({
      playerKey: player.playerKey,
      username: player.username,
      displayName: player.displayName,
      eligibility: {
        singles: Boolean(player.modes.singles?.eligible),
        doubles: Boolean(player.modes.doubles?.eligible),
      },
    })),
  };
}

export function recommendationsForPlayer(
  payload: Awaited<ReturnType<typeof readLocalRecommendationIndex>>,
  playerKey: string,
): PlayerRecommendationsResponse | null {
  const player = payload.players.find((row) => row.playerKey === playerKey);
  return player
    ? {
        generatedAtUtc: payload.generatedAtUtc,
        method: payload.method,
        player,
      }
    : null;
}
