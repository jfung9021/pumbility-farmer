import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { readLocalSkillRadar } from "../lib/local-skill-radar.ts";
import { radarPlayerFromUrl, radarPlayerUrl, radarPoint, validateSkillRadarResponse } from "../lib/skill-radar.ts";

const fixture = JSON.parse(await readFile(new URL("./fixtures/skill-radar-profile.json", import.meta.url), "utf8"));

test("Python profile satisfies the public contract with all eighteen categories and exact mappings", () => {
  const profile = validateSkillRadarResponse(fixture, fixture.player.playerKey);
  assert.equal(profile.bpmBuckets.length, 9);
  assert.equal(profile.buckets.length, 18);
  assert.equal(new Set(Object.values(profile.groups).flat()).size, 18);
  assert.equal(profile.groups.weaknesses.length, 3);
  for (const bucket of profile.buckets) {
    assert.equal(bucket.minimumSuggestedDifficulty, bucket.rawPg + 1.5);
    assert.equal(bucket.displayPg, Math.max(0, bucket.rawPg - (bucket.mode === "doubles" ? 1 : 0)));
    assert.ok(bucket.suggestions.every(chart => chart.estimatedDifficulty >= bucket.minimumSuggestedDifficulty));
  }
});

test("invalid profiles and private histories cannot become a zero radar", () => {
  const mutations = [
    (value: any) => { value.player.playerId = "private"; },
    (value: any) => { value.scores = []; },
    (value: any) => { value.buckets[0].rawPg = Number.NaN; },
    (value: any) => { value.buckets[0].radarScore = 101; },
    (value: any) => { value.buckets[0].minimumSuggestedDifficulty -= 1; },
    (value: any) => { value.groups.everythingElse.push(value.groups.weaknesses[0]); },
    (value: any) => { value.buckets[1].mode = value.buckets[0].mode; value.buckets[1].bpmBucketId = value.buckets[0].bpmBucketId; },
  ];
  for (const mutate of mutations) {
    const value = structuredClone(fixture); mutate(value);
    assert.throws(() => validateSkillRadarResponse(value), /invalid/);
  }
  assert.throws(() => validateSkillRadarResponse(fixture, "b".repeat(20)), /invalid/);
});

test("player links preserve unrelated state and support URL restoration", () => {
  const key = fixture.player.playerKey;
  const url = radarPlayerUrl("https://example.test/skill-radar?keep=1#profile", key);
  assert.equal(url.searchParams.get("keep"), "1");
  assert.equal(url.hash, "#profile");
  assert.equal(radarPlayerFromUrl(url.search), key);
  assert.equal(radarPlayerFromUrl("?player=../private"), "");
  assert.equal(radarPlayerUrl(url.href, "").searchParams.has("player"), false);
});

test("radar geometry draws zeros at the center and all nine full-scale points inside its view", () => {
  for (let index = 0; index < 9; index++) {
    assert.deepEqual(radarPoint(index, 0), {x:200, y:200});
    const point = radarPoint(index, 100);
    assert.ok(Math.abs(Math.hypot(point.x - 200, point.y - 200) - 132) < 1e-8);
    assert.ok(point.x >= 0 && point.x <= 400 && point.y >= 0 && point.y <= 400);
  }
});

test("local reader accepts the shared DTO, rejects traversal and fails on corrupt artifacts", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "piu-skill-radar-"));
  try {
    const key = fixture.player.playerKey, file = path.join(directory, `${key}.json`);
    await writeFile(file, JSON.stringify(fixture));
    assert.deepEqual(await readLocalSkillRadar(key, directory), fixture);
    await assert.rejects(readLocalSkillRadar("../private", directory), /valid playerKey/);
    await writeFile(file, JSON.stringify({...fixture, token:"private"}));
    await assert.rejects(readLocalSkillRadar(key, directory), /invalid/);
  } finally {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(tmpdir()));
    await rm(directory, {recursive:true, force:true});
  }
});
