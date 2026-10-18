import { readFile } from "node:fs/promises";
import path from "node:path";
import { PLAYER_KEY_PATTERN, validateSkillRadarResponse } from "./skill-radar.ts";
import type { RecommendationPlayersResponse } from "./types";

export const LOCAL_SKILL_RADAR_DIRECTORY = path.join(process.cwd(), ".local-data", "piu-scores", "skill-radar", "players");

export class SkillRadarEligibilityError extends Error {}

export async function readLocalSkillRadarPlayers(file = path.join(path.dirname(LOCAL_SKILL_RADAR_DIRECTORY), "players.json")) {
  const value = JSON.parse(await readFile(file, "utf8"));
  if (!Array.isArray(value.players) || !value.players.every((player: {playerKey?: string; phoenix2ScoreCount?: number}) => PLAYER_KEY_PATTERN.test(player.playerKey ?? "") && Number.isInteger(player.phoenix2ScoreCount) && (player.phoenix2ScoreCount ?? 0) >= 100)) throw new Error("The Skill Radar player list is invalid.");
  return value as RecommendationPlayersResponse;
}

export async function readLocalSkillRadar(playerKey: string, directory = LOCAL_SKILL_RADAR_DIRECTORY, playersFile = path.join(path.dirname(directory), "players.json")) {
  if (!PLAYER_KEY_PATTERN.test(playerKey)) throw new RangeError("A valid playerKey is required.");
  const source = await readFile(path.join(directory, `${playerKey}.json`), "utf8");
  const players = await readLocalSkillRadarPlayers(playersFile);
  if (!players.players.some(player => player.playerKey === playerKey)) throw new SkillRadarEligibilityError("Skill Radar requires at least 100 submitted Phoenix 2 scores.");
  return validateSkillRadarResponse(JSON.parse(source), playerKey);
}
