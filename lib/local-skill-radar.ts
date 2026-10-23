import { readFile } from "node:fs/promises";
import path from "node:path";
import { PLAYER_KEY_PATTERN, validateSkillRadarResponse } from "./skill-radar.ts";
import type { RecommendationPlayersResponse } from "./types";

export const LOCAL_SKILL_RADAR_DIRECTORY = path.join(process.cwd(), ".local-data", "piu-scores", "skill-radar", "players");

export class SkillRadarEligibilityError extends Error {}

type BpmGeneration = {generation: string; bpmMetadataVersion: 1; bpmPolicy: "focal-v1"; bpmMetadataRevision: string};
const validIdentity = (value: Record<string, unknown>) => value.bpmMetadataVersion === 1 && value.bpmPolicy === "focal-v1" && typeof value.bpmMetadataRevision === "string" && /^[a-f0-9]{64}$/.test(value.bpmMetadataRevision);

async function focalGeneration(directory: string): Promise<{directory: string; pointer: BpmGeneration; players: string[]; methodVersion: 7 | 8}> {
  const root = path.join(path.dirname(directory), "focal-v1");
  const pointer = JSON.parse(await readFile(path.join(root, "current.json"), "utf8"));
  if (!pointer || !validIdentity(pointer) || typeof pointer.generation !== "string" || !/^[a-f0-9]{64}$/.test(pointer.generation)) throw new Error("The Skill Radar generation is invalid.");
  const resolved = path.join(root, "generations", pointer.generation);
  const manifest = JSON.parse(await readFile(path.join(resolved, "manifest.json"), "utf8"));
  if (!manifest || !validIdentity(manifest) || manifest.bpmMetadataRevision !== pointer.bpmMetadataRevision || manifest.generation !== pointer.generation || !(manifest.methodVersion === 7 && manifest.profileVersion === 12 || manifest.methodVersion === 8 && manifest.profileVersion === 13) || !Array.isArray(manifest.players) || !manifest.players.every((key: unknown) => typeof key === "string" && PLAYER_KEY_PATTERN.test(key)) || new Set(manifest.players).size !== manifest.players.length) throw new Error("The Skill Radar generation metadata is inconsistent.");
  return {directory:resolved, pointer, players:manifest.players, methodVersion:manifest.methodVersion};
}

function assertRosterGeneration(value: Record<string, unknown>, generation: Awaited<ReturnType<typeof focalGeneration>>) {
  const players = value.players as {playerKey: string}[];
  if (!validIdentity(value) || value.bpmMetadataRevision !== generation.pointer.bpmMetadataRevision || value.generation !== generation.pointer.generation || players.length !== generation.players.length || players.some(player => !generation.players.includes(player.playerKey))) throw new Error("The Skill Radar roster does not match its generation.");
}

export async function readLocalSkillRadarPlayers(file = path.join(path.dirname(LOCAL_SKILL_RADAR_DIRECTORY), "players.json"), bpmPolicy?: string) {
  const generation = bpmPolicy === "focal-v1" ? await focalGeneration(path.join(path.dirname(file), "players")) : null;
  if (generation) {
    file = path.join(generation.directory, "players.json");
  }
  const value = JSON.parse(await readFile(file, "utf8"));
  if (!Array.isArray(value.players) || !value.players.every((player: {playerKey?: string; phoenix2ScoreCount?: number}) => PLAYER_KEY_PATTERN.test(player.playerKey ?? "") && Number.isInteger(player.phoenix2ScoreCount) && (player.phoenix2ScoreCount ?? 0) >= 100)) throw new Error("The Skill Radar player list is invalid.");
  if (generation) assertRosterGeneration(value, generation);
  return value as RecommendationPlayersResponse;
}

export async function readLocalSkillRadar(playerKey: string, directory = LOCAL_SKILL_RADAR_DIRECTORY, playersFile = path.join(path.dirname(directory), "players.json"), bpmPolicy?: string) {
  if (!PLAYER_KEY_PATTERN.test(playerKey)) throw new RangeError("A valid playerKey is required.");
  const generation = bpmPolicy === "focal-v1" ? await focalGeneration(directory) : null;
  if (generation) {
    directory = path.join(generation.directory, "players");
    playersFile = path.join(generation.directory, "players.json");
  }
  const source = await readFile(path.join(directory, `${playerKey}.json`), "utf8");
  const players = await readLocalSkillRadarPlayers(playersFile);
  if (generation) assertRosterGeneration(players as unknown as Record<string, unknown>, generation);
  if (!players.players.some(player => player.playerKey === playerKey)) throw new SkillRadarEligibilityError("Skill Radar requires at least 100 submitted Phoenix 2 scores.");
  const profile = validateSkillRadarResponse(JSON.parse(source), playerKey);
  if (profile.methodVersion !== (generation?.methodVersion ?? 6) || generation && profile.bpmMetadataRevision !== generation.pointer.bpmMetadataRevision) throw new Error("The Skill Radar profile does not match its generation.");
  return profile;
}
