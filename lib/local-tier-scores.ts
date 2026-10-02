import { readFile } from "node:fs/promises";
import path from "node:path";

import type { ModeKey, TierPlayerScore, TierPlayerScoresResponse } from "./types";

export const LOCAL_TIER_SCORES_PATH = path.join(
  process.cwd(), ".local-data", "piu-scores", "recommendations", "tier-scores.json",
);
const PLATE_CODES = new Set(["RG", "FG", "TG", "MG", "SG", "EG", "UG", "PG"]);

type LocalTierScorePlayer = Omit<TierPlayerScoresResponse, "mode">;
export interface LocalTierScoreArtifact {
  schemaVersion: 1;
  players: LocalTierScorePlayer[];
}

export class LocalTierScoresNotFoundError extends Error {}
export class LocalTierScoresValidationError extends Error {}

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => key in value);
}

function validScore(value: unknown): value is TierPlayerScore {
  return record(value)
    && exactKeys(value, ["chartId", "score", "plateCode"])
    && typeof value.chartId === "string" && value.chartId.trim().length > 0
    && typeof value.score === "number" && Number.isInteger(value.score)
    && value.score >= 0 && value.score <= 1_000_000
    && (value.plateCode === null || (typeof value.plateCode === "string" && PLATE_CODES.has(value.plateCode)));
}

export function validateLocalTierScores(value: unknown): LocalTierScoreArtifact {
  const playerKeys = new Set<string>();
  if (!record(value) || !exactKeys(value, ["schemaVersion", "players"])
    || value.schemaVersion !== 1 || !Array.isArray(value.players)
    || !value.players.every((player: unknown) => {
      if (!record(player) || !exactKeys(player, ["playerKey", "syncedAtUtc", "scores"])
        || typeof player.playerKey !== "string" || !/^[a-f0-9]{20}$/.test(player.playerKey)
        || playerKeys.has(player.playerKey)
        || !(player.syncedAtUtc === null || (typeof player.syncedAtUtc === "string" && Number.isFinite(Date.parse(player.syncedAtUtc))))
        || !Array.isArray(player.scores) || !player.scores.every(validScore)) return false;
      playerKeys.add(player.playerKey);
      return new Set(player.scores.map((score) => score.chartId)).size === player.scores.length;
    })) {
    throw new LocalTierScoresValidationError("The local tier scores have an invalid or private shape.");
  }
  return value as unknown as LocalTierScoreArtifact;
}

export async function readLocalTierScores(
  pathname = LOCAL_TIER_SCORES_PATH,
): Promise<LocalTierScoreArtifact> {
  let raw: string;
  try {
    raw = await readFile(pathname, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      throw new LocalTierScoresNotFoundError("No local tier scores exist. Run npm run analyze:recommendations.");
    }
    throw error;
  }
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new LocalTierScoresValidationError("The local tier scores are not valid JSON.");
  }
  return validateLocalTierScores(value);
}

export function localTierScoresForPlayer(
  artifact: LocalTierScoreArtifact,
  playerKey: string,
  mode: ModeKey,
  allowedChartIds: ReadonlySet<string>,
): TierPlayerScoresResponse | null {
  const player = artifact.players.find((row) => row.playerKey === playerKey);
  if (!player) return null;
  return {
    playerKey,
    mode,
    syncedAtUtc: player.syncedAtUtc,
    scores: player.scores.filter((score) => allowedChartIds.has(score.chartId))
      .map(({ chartId, score, plateCode }) => ({ chartId, score, plateCode }))
      .sort((left, right) => left.chartId.localeCompare(right.chartId)),
  };
}
