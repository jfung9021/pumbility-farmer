import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import {
  LocalTierScoresNotFoundError, LocalTierScoresValidationError,
  localTierScoresForPlayer, readLocalTierScores, validateLocalTierScores,
} from "../lib/local-tier-scores.ts";

const playerKey = "a".repeat(20);
const fixture = () => ({
  schemaVersion: 1,
  players: [{
    playerKey, syncedAtUtc: "2026-10-03T00:00:00Z",
    scores: [
      { chartId: "single", score: 996_999, plateCode: "MG" },
      { chartId: "double", score: 1_000_000, plateCode: "PG" },
      { chartId: "coop", score: 0, plateCode: null },
    ],
  }],
});

test("local tier score projection restricts current chart IDs and preserves zero and missing plate", () => {
  const artifact = validateLocalTierScores(fixture());
  assert.deepEqual(localTierScoresForPlayer(artifact, playerKey, "coop", new Set(["coop"])), {
    playerKey, mode: "coop", syncedAtUtc: "2026-10-03T00:00:00Z",
    scores: [{ chartId: "coop", score: 0, plateCode: null }],
  });
  assert.equal(localTierScoresForPlayer(artifact, "missing", "singles", new Set()), null);
  assert.deepEqual(localTierScoresForPlayer(artifact, playerKey, "singles", new Set())?.scores, []);
});

test("local tier scores reject private fields, duplicate keys, and invalid scores", () => {
  for (const mutate of [
    (value: any) => { value.token = "private"; },
    (value: any) => { value.players[0].playerId = "private"; },
    (value: any) => { value.players[0].scores[0].recordedAt = "private"; },
    (value: any) => { value.players.push(value.players[0]); },
    (value: any) => { value.players[0].scores.push(value.players[0].scores[0]); },
    (value: any) => { value.players[0].scores[0].score = 1_000_001; },
    (value: any) => { value.players[0].scores[0].score = 996_999.5; },
    (value: any) => { value.players[0].scores[0].plateCode = "unknown"; },
  ]) {
    const value = fixture();
    mutate(value);
    assert.throws(() => validateLocalTierScores(value), LocalTierScoresValidationError);
  }
});

test("local tier artifacts can return more than 50 chart scores", () => {
  const value = fixture();
  value.players[0].scores = Array.from({ length: 65 }, (_, index) => ({ chartId: `s${index}`, score: 923_999, plateCode: "FG" }));
  const artifact = validateLocalTierScores(value);
  assert.equal(localTierScoresForPlayer(artifact, playerKey, "singles", new Set(value.players[0].scores.map((row) => row.chartId)))?.scores.length, 65);
});

test("local tier artifact reader reports absent and malformed artifacts", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "tier-scores-"));
  const pathname = path.join(directory, "scores.json");
  try {
    await assert.rejects(readLocalTierScores(pathname), LocalTierScoresNotFoundError);
    await writeFile(pathname, "{");
    await assert.rejects(readLocalTierScores(pathname), LocalTierScoresValidationError);
    await writeFile(pathname, JSON.stringify(fixture()));
    assert.deepEqual(await readLocalTierScores(pathname), fixture());
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
