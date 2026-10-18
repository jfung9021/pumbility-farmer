import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { readLocalSkillRadar, readLocalSkillRadarPlayers } from "../lib/local-skill-radar.ts";
import { radarAxisLabelLayout, radarPlayerFromUrl, radarPlayerUrl, radarPoint, validateSkillRadarResponse } from "../lib/skill-radar.ts";

const fixture = JSON.parse(await readFile(new URL("./fixtures/skill-radar-profile.json", import.meta.url), "utf8"));

test("Python profile satisfies the public contract with all eighteen categories and exact mappings", () => {
  const profile = validateSkillRadarResponse(fixture, fixture.player.playerKey);
  assert.equal(profile.bpmBuckets.length, 9);
  assert.equal(profile.buckets.length, 18);
  assert.equal(new Set(Object.values(profile.groups).flat()).size, 18);
  assert.equal(profile.groups.weaknesses.length, 3);
  for (const bucket of profile.buckets) {
    assert.equal(bucket.suggestedLevel, Math.max(16, Math.floor(bucket.rawPg + 1.5 + 1e-9)));
    assert.equal(bucket.displayPg, Math.max(0, bucket.rawPg - (bucket.mode === "doubles" ? 1 : 0)));
    assert.ok(bucket.suggestions.every(chart => chart.level === bucket.suggestedLevel));
    assert.equal(bucket.contributingCharts.length, bucket.selectedCharts);
  }
});

test("recommendations allow ten charts from the official target folder even below its calculated rating", () => {
  const value = structuredClone(fixture);
  const bucket = value.buckets.find((bucket: any) => bucket.mode === "singles" && bucket.suggestedLevel === 19);
  bucket.suggestions = Array.from({length:10}, (_, index) => ({...bucket.suggestions[0], chartId:`practice-${index}`, estimatedDifficulty:18.8}));
  assert.equal(validateSkillRadarResponse(value).buckets.find(bucket => bucket.key === "singles:1")!.suggestions.length, 10);
  bucket.suggestions.push({...bucket.suggestions[0], chartId:"eleventh"});
  assert.throws(() => validateSkillRadarResponse(value), /invalid/);
  bucket.suggestions.pop();
  bucket.suggestions[0].level = 20;
  assert.throws(() => validateSkillRadarResponse(value), /invalid/);
});

test("contributions cannot disagree with the rating, duplicate charts, or disclose histories", () => {
  const mutations = [
    (bucket: any) => {bucket.contributingCharts[0].pumbility += 1;},
    (bucket: any) => {bucket.contributingCharts[1].chartId = bucket.contributingCharts[0].chartId;},
    (bucket: any) => {bucket.contributingCharts[0].recordedAt = "private";},
    (bucket: any) => {bucket.contributingCharts[0].playerId = "private";},
    (bucket: any) => {bucket.contributingCharts[0].type = "Double";},
    (bucket: any) => {bucket.contributingCharts.push({...bucket.contributingCharts[0], chartId:"sixth"});},
    (bucket: any) => {delete bucket.contributingCharts;},
  ];
  for (const mutate of mutations) {
    const value = structuredClone(fixture);
    mutate(value.buckets.find((bucket: any) => bucket.mode === "singles" && bucket.selectedCharts === 5));
    assert.throws(() => validateSkillRadarResponse(value), /invalid/);
  }
});

test("invalid profiles and private histories cannot become a zero radar", () => {
  const mutations = [
    (value: any) => { value.player.playerId = "private"; },
    (value: any) => { value.scores = []; },
    (value: any) => { value.buckets[0].rawPg = Number.NaN; },
    (value: any) => { value.buckets[0].radarScore = 101; },
    (value: any) => { value.buckets[0].suggestedLevel -= 1; },
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

test("stacked BPM labels clear the full-scale plot and remain inside the padded view", () => {
  const blocks: Array<{axis: number; left: number; right: number; top: number; bottom: number}> = [];
  for (let index = 0; index < 9; index++) {
    const label = radarAxisLabelLayout(index);
    for (const [y, width, height] of [[label.labelY, 110, 22], [label.scoreY, 100, 17], [label.scoreY + 18, 100, 17]]) {
      const left = label.x - (label.textAnchor === "end" ? width : label.textAnchor === "middle" ? width / 2 : 0);
      const right = left + width, top = y - height / 2, bottom = y + height / 2;
      const nearestX = Math.max(left, Math.min(200, right)), nearestY = Math.max(top, Math.min(200, bottom));
      assert.ok(Math.hypot(nearestX - 200, nearestY - 200) > 132, `axis ${index} overlaps the plot`);
      assert.ok(left + 70 >= 0 && right + 70 <= 540 && top + 50 >= 0 && bottom + 50 <= 500, `axis ${index} leaves the viewport`);
      assert.ok(right < 205 || left > 225 || bottom < 52 || top > 64, `axis ${index} overlaps the outer-ring 100 label`);
      blocks.push({axis:index, left, right, top, bottom});
    }
  }
  for (let first = 0; first < blocks.length; first++) for (let second = first + 1; second < blocks.length; second++) {
    const a = blocks[first], b = blocks[second];
    if (a.axis !== b.axis) assert.ok(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom, `axes ${a.axis} and ${b.axis} have overlapping labels`);
  }
});

test("local reader accepts the shared DTO, rejects traversal and fails on corrupt artifacts", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "piu-skill-radar-"));
  try {
    const key = fixture.player.playerKey, file = path.join(directory, `${key}.json`);
    const roster = path.join(directory, "players.json");
    const players = {generatedAtUtc:fixture.generatedAtUtc, method:{minimumPhoenix2Scores:100}, players:[{...fixture.player, username:"PLAYER", phoenix2ScoreCount:100, eligibility:{singles:true,doubles:true,coop:true}}]};
    await writeFile(roster, JSON.stringify(players));
    await writeFile(file, JSON.stringify(fixture));
    assert.deepEqual(await readLocalSkillRadar(key, directory, roster), fixture);
    await assert.rejects(readLocalSkillRadar("../private", directory, roster), /valid playerKey/);
    await writeFile(roster, JSON.stringify({...players, players:[]}));
    await assert.rejects(readLocalSkillRadar(key, directory, roster), /100 submitted Phoenix 2/);
    await writeFile(roster, JSON.stringify({...players, players:[{...players.players[0],phoenix2ScoreCount:99}]}));
    await assert.rejects(readLocalSkillRadarPlayers(roster), /invalid/);
    await writeFile(roster, JSON.stringify(players));
    await writeFile(file, JSON.stringify({...fixture, token:"private"}));
    await assert.rejects(readLocalSkillRadar(key, directory, roster), /invalid/);
  } finally {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(tmpdir()));
    await rm(directory, {recursive:true, force:true});
  }
});
