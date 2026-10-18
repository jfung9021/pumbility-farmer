import { readFile } from "node:fs/promises";
import path from "node:path";
import { PLAYER_KEY_PATTERN, validateSkillRadarResponse } from "./skill-radar.ts";

export const LOCAL_SKILL_RADAR_DIRECTORY = path.join(process.cwd(), ".local-data", "piu-scores", "skill-radar", "players");

export async function readLocalSkillRadar(playerKey: string, directory = LOCAL_SKILL_RADAR_DIRECTORY) {
  if (!PLAYER_KEY_PATTERN.test(playerKey)) throw new RangeError("A valid playerKey is required.");
  const source = await readFile(path.join(directory, `${playerKey}.json`), "utf8");
  return validateSkillRadarResponse(JSON.parse(source), playerKey);
}
