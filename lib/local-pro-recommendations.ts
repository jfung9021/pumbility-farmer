import { readFile } from "node:fs/promises";
import path from "node:path";
import { isProPlayerKey, validateProPlayersResponse, validateProRecommendationsResponse } from "./pro-recommendations.ts";

export class ProRecommendationsUnavailableError extends Error {}
export class ProRecommendationsNotFoundError extends Error {}
export class ProRecommendationsGenerationError extends Error {}

export function proRecommendationsDirectory(): string {
  return process.env.PIU_LOCAL_PRO_RECOMMENDATIONS_DIR || path.join(process.cwd(), ".local-data", "piu-scores", "pro-modes", "current");
}

async function readJson(filePath: string): Promise<unknown> {
  try { return JSON.parse(await readFile(filePath, "utf8")); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      throw new ProRecommendationsUnavailableError("Pro Farming data is unavailable. Run npm run pro:refresh to capture current Phoenix 2 data.");
    }
    throw error;
  }
}

export async function readLocalProPlayers() {
  return validateProPlayersResponse(await readJson(path.join(proRecommendationsDirectory(), "index.json")));
}

export async function readLocalProRecommendations(playerKey: string, generation?: string) {
  if (!isProPlayerKey(playerKey)) throw new ProRecommendationsNotFoundError("Select a valid player.");
  const index = await readLocalProPlayers();
  if (generation && generation !== index.generation) throw new ProRecommendationsGenerationError("The Pro Farming capture changed. Reload the player list.");
  if (!index.players.some(player => player.playerKey === playerKey)) throw new ProRecommendationsNotFoundError("This player has no captured Pro Farming profile. Refresh the local Phoenix 2 data.");
  const payload = await readJson(path.join(proRecommendationsDirectory(), "generations", index.generation, "players", `${playerKey}.json`));
  return validateProRecommendationsResponse(payload, index, playerKey);
}
