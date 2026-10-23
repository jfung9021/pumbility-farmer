import { readFile } from "node:fs/promises";
import path from "node:path";
import { cachedFileReader } from "./local-file-cache.ts";
import {
  isOfficialPlayerKey,
  MINIMUM_OFFICIAL_SCORES,
  validateOfficialComparisonIndex,
  validateOfficialComparisonPlayer,
  type OfficialComparisonIndex,
} from "./official-comparison.ts";

export class OfficialComparisonNotFoundError extends Error {}
export class OfficialComparisonGenerationError extends Error {}

export function officialComparisonDirectory(bpmPolicy?: string): string {
  const base = process.env.PIU_LOCAL_OFFICIAL_COMPARISON_DIR
    || path.join(process.cwd(), ".local-data", "piu-scores", "official", "comparison", "current");
  return bpmPolicy === "focal-v1" ? path.join(base, "focal-v1") : base;
}

async function readJson(filePath: string): Promise<unknown> {
  try {
    return JSON.parse(await readFile(filePath, "utf8"));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      throw new OfficialComparisonNotFoundError("Official comparison data is unavailable. Run npm run analyze:official-comparison.");
    }
    throw error;
  }
}

const readIndex = cachedFileReader(async filePath => validateOfficialComparisonIndex(await readJson(filePath)));

export async function readLocalOfficialComparisonIndex(bpmPolicy?: string): Promise<OfficialComparisonIndex> {
  try {
    const index = await readIndex(path.join(officialComparisonDirectory(bpmPolicy), "index.json"));
    if (index.schemaVersion !== (bpmPolicy === "focal-v1" ? 3 : 2)) throw new Error("The official comparison BPM policy does not match the requested contract.");
    return { ...index, players: index.players.filter(player => player.recordedScoreCount >= MINIMUM_OFFICIAL_SCORES) };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      throw new OfficialComparisonNotFoundError("Official comparison data is unavailable. Run npm run analyze:official-comparison.");
    }
    throw error;
  }
}

export async function readLocalOfficialComparisonPlayer(playerKey: string, generation?: string, bpmPolicy?: string) {
  if (!isOfficialPlayerKey(playerKey)) throw new OfficialComparisonNotFoundError("The selected official player was not found.");
  const index = await readLocalOfficialComparisonIndex(bpmPolicy);
  if (generation && generation !== index.generation) {
    throw new OfficialComparisonGenerationError("The official leaderboard snapshot changed. Reload the comparison.");
  }
  if (!index.players.some(player => player.playerKey === playerKey)) {
    throw new OfficialComparisonNotFoundError("The selected official player was not found.");
  }
  const payload = await readJson(path.join(officialComparisonDirectory(bpmPolicy), "generations", index.generation, "players", `${playerKey}.json`));
  return validateOfficialComparisonPlayer(payload, index, playerKey);
}
