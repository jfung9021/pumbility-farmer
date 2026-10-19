import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  isOfficialPlayerKey,
  OFFICIAL_COMPARISON_METHOD,
  validateOfficialComparisonIndex,
  validateOfficialComparisonPlayer,
  type OfficialComparisonIndex,
  type OfficialComparisonPlayer,
} from "../lib/official-comparison.ts";
import {
  readLocalOfficialComparisonPlayer,
  OfficialComparisonGenerationError,
  OfficialComparisonNotFoundError,
} from "../lib/local-official-comparison.ts";

const playerKey = `op_${"a".repeat(64)}`;

function fixture(): { index: OfficialComparisonIndex; player: OfficialComparisonPlayer } {
  return {
    index: {
      schemaVersion: 2, generation: "generation_a", generatedAtUtc: "2026-10-19T00:00:00Z",
      sourceDates: { Phoenix: "2026-09-06T00:00:00Z", Phoenix2: "2026-10-11T00:00:00Z" },
      method: { version: OFFICIAL_COMPARISON_METHOD, plate: "SG below perfect; PG at perfect" },
      players: [{ playerKey, displayName: "PLAYER#0001" }],
      charts: [{ chartId: "chart-1", songName: "Alpha", chartLabel: "S20", mode: "singles", level: 20, bpmMin: 150, bpmMax: 150 }],
    },
    player: {
      schemaVersion: 2, generation: "generation_a", playerKey, displayName: "PLAYER#0001",
      buckets: (["singles", "doubles"] as const).flatMap(mode => Array.from({ length: 9 }, (_, bucketIndex) => ({
        mode, bucketIndex, pg: null, radarScore: null, recordedCount: 0, contributingCount: 0, contributors: [],
      }))),
      scores: { "chart-1": 0 }, recordedScoreCount: 1, radarEligibleScoreCount: 0,
    },
  };
}

test("comparison contract keeps unknown radar buckets and valid zero scores distinct", () => {
  const { index, player } = fixture();
  assert.equal(validateOfficialComparisonIndex(index), index);
  assert.equal(validateOfficialComparisonPlayer(player, index, playerKey).scores["chart-1"], 0);
  assert.equal(player.buckets[0].pg, null);
  player.buckets[1] = {
    ...player.buckets[1], pg: 0, radarScore: 0, recordedCount: 1, contributingCount: 1,
    contributors: [{ chartId: "chart-1", score: 0, pumbilityCents: 21337, assumedPlate: "SG" }],
  };
  player.radarEligibleScoreCount = 1;
  assert.doesNotThrow(() => validateOfficialComparisonPlayer(player, index, playerKey));
});

test("comparison rejects stale MG artifacts and validates the exact contributing records", () => {
  const { index, player } = fixture();
  assert.throws(() => validateOfficialComparisonIndex({ ...index, schemaVersion: 1 }), /catalog is invalid/);
  assert.throws(() => validateOfficialComparisonIndex({ ...index, method: { version: "official-comparison-mg-perfect-pg-v1" } }), /catalog is invalid/);
  assert.throws(() => validateOfficialComparisonPlayer({ ...player, schemaVersion: 1 }, index, playerKey), /current comparison catalog/);
  const bucket = {
    ...player.buckets[1], pg: 0, radarScore: 0, recordedCount: 1, contributingCount: 1,
    contributors: [{ chartId: "chart-1", score: 0, pumbilityCents: 21337, assumedPlate: "SG" as const }],
  };
  player.buckets[1] = bucket;
  assert.doesNotThrow(() => validateOfficialComparisonPlayer(player, index, playerKey));
  bucket.contributors[0].score = 999999;
  assert.throws(() => validateOfficialComparisonPlayer(player, index, playerKey), /contributing charts/);
  bucket.contributors[0].score = 0;
  bucket.contributingCount = 2;
  bucket.recordedCount = 2;
  bucket.contributors.push({ ...bucket.contributors[0] });
  assert.throws(() => validateOfficialComparisonPlayer(player, index, playerKey), /contributing charts/);
  bucket.contributors.pop();
  bucket.recordedCount = bucket.contributingCount = 1;
  player.scores["chart-1"] = bucket.contributors[0].score = 1000000;
  assert.throws(() => validateOfficialComparisonPlayer(player, index, playerKey), /contributing charts/);
  player.buckets[1] = { ...bucket, contributors: [{ ...bucket.contributors[0], assumedPlate: "PG" }] };
  assert.doesNotThrow(() => validateOfficialComparisonPlayer(player, index, playerKey));
  index.charts[0].bpmMax = 160;
  assert.throws(() => validateOfficialComparisonPlayer(player, index, playerKey), /does not match/);
});

test("comparison contracts reject wrong scope, generation, duplicate buckets and invented scores", () => {
  const { index, player } = fixture();
  assert.throws(() => validateOfficialComparisonIndex({ ...index, charts: [{ ...index.charts[0], level: 19 }] }), /chart catalog/);
  assert.throws(() => validateOfficialComparisonPlayer({ ...player, generation: "old" }, index, playerKey), /current comparison catalog/);
  assert.throws(() => validateOfficialComparisonPlayer({ ...player, buckets: [...player.buckets.slice(0, 17), player.buckets[0]] }, index, playerKey), /repeats/);
  assert.throws(() => validateOfficialComparisonPlayer({ ...player, scores: { missing: 995000 } }, index, playerKey), /score records/);
  assert.throws(() => validateOfficialComparisonPlayer({ ...player, buckets: player.buckets.map(bucket => ({ ...bucket, pg: 0, radarScore: 0 })) }, index, playerKey), /must be unknown/);
  assert.equal(isOfficialPlayerKey("../../private"), false);
});

test("local reader pins profile paths to the published generation and refuses stale requests", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "piu-comparison-"));
  const previousDirectory = process.env.PIU_LOCAL_OFFICIAL_COMPARISON_DIR;
  process.env.PIU_LOCAL_OFFICIAL_COMPARISON_DIR = directory;
  try {
    const { index, player } = fixture();
    const playerDirectory = path.join(directory, "generations", index.generation, "players");
    await mkdir(playerDirectory, { recursive: true });
    await writeFile(path.join(directory, "index.json"), JSON.stringify(index));
    await writeFile(path.join(playerDirectory, `${playerKey}.json`), JSON.stringify(player));
    assert.equal((await readLocalOfficialComparisonPlayer(playerKey, index.generation)).scores["chart-1"], 0);
    await assert.rejects(() => readLocalOfficialComparisonPlayer(playerKey, "stale"), OfficialComparisonGenerationError);
    await assert.rejects(() => readLocalOfficialComparisonPlayer(`op_${"b".repeat(64)}`), OfficialComparisonNotFoundError);
    await assert.rejects(() => readLocalOfficialComparisonPlayer("../private"), OfficialComparisonNotFoundError);
  } finally {
    if (previousDirectory === undefined) delete process.env.PIU_LOCAL_OFFICIAL_COMPARISON_DIR;
    else process.env.PIU_LOCAL_OFFICIAL_COMPARISON_DIR = previousDirectory;
    assert.ok(path.resolve(directory).startsWith(path.resolve(os.tmpdir()) + path.sep));
    await rm(directory, { recursive: true, force: true });
  }
});
