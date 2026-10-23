import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { readLocalSkillRadar, readLocalSkillRadarPlayers } from "../lib/local-skill-radar.ts";
import { radarAxisLabelLayout, radarPlayerFromUrl, radarPlayerUrl, radarPoint, radarScoreForLevel, validateSkillRadarResponse } from "../lib/skill-radar.ts";

const fixture = JSON.parse(await readFile(new URL("./fixtures/skill-radar-profile.json", import.meta.url), "utf8"));

function focalFixture() {
  const value = structuredClone(fixture);
  Object.assign(value, {methodVersion:7,bpmMetadataVersion:1,bpmPolicy:"focal-v1",bpmMetadataRevision:"a".repeat(64)});
  for (const bucket of value.buckets) for (const chart of bucket.suggestions) chart.bpmFocal = chart.bpmMin;
  return value;
}

test("focal radar method has an explicit strict contract and preserves full-range suggestions", () => {
  const value = focalFixture();
  const bucket = value.buckets.find((bucket: any) => bucket.bpmBucketId === "2" && bucket.mode === "singles");
  assert.ok(bucket);
  bucket.suggestions = [structuredClone(value.buckets.find((bucket: any) => bucket.suggestions.length).suggestions[0])];
  Object.assign(bucket.suggestions[0], {bpmMin:100,bpmMax:225,bpmFocal:169});
  assert.equal(validateSkillRadarResponse(value).methodVersion, 7);
  for (const mutate of [
    (profile: any) => {delete profile.bpmMetadataRevision;},
    (profile: any) => {profile.bpmMetadataRevision = "bad";},
    (profile: any) => {profile.bpmPolicy = "legacy";},
    (profile: any) => {profile.buckets.find((bucket: any) => bucket.bpmBucketId === "2" && bucket.suggestions.length).suggestions[0].bpmFocal = 170;},
    (profile: any) => {profile.buckets.find((bucket: any) => bucket.suggestions.length).suggestions[0].privateEvidence = [];},
  ]) {
    const broken = structuredClone(value); mutate(broken);
    assert.throws(() => validateSkillRadarResponse(broken), /invalid/);
  }
  const legacy = structuredClone(fixture);
  assert.throws(() => validateSkillRadarResponse(legacy, undefined, "focal-v1"), /invalid/);
  legacy.bpmPolicy = "focal-v1";
  assert.throws(() => validateSkillRadarResponse(legacy), /invalid/);
});

test("Python profile satisfies the public contract with all eighteen categories and exact mappings", () => {
  const profile = validateSkillRadarResponse(fixture, fixture.player.playerKey);
  assert.equal(profile.methodVersion, 6);
  assert.deepEqual(profile.reference, {grade:"SSS+", plate:"UG", minimumScore:995000});
  assert.equal(profile.bpmBuckets.length, 9);
  assert.equal(profile.buckets.length, 18);
  assert.equal(new Set(Object.values(profile.groups).flat()).size, 18);
  assert.equal(profile.groups.weaknesses.length, 3);
  for (const bucket of profile.buckets) {
    assert.equal(bucket.suggestedLevel, Math.max(16, Math.floor(bucket.rawEquivalentLevel + 1.5 + 1e-9)));
    assert.equal(bucket.displayEquivalentLevel, Math.max(0, bucket.rawEquivalentLevel - (bucket.mode === "doubles" ? 1 : 0)));
    assert.equal(bucket.radarScore, radarScoreForLevel(bucket.displayEquivalentLevel, profile.bpmBuckets.find(bin => bin.id === bucket.bpmBucketId)!, bucket.mode));
    assert.ok(bucket.suggestions.length <= 10);
    assert.ok(bucket.suggestions.every(chart => chart.level >= bucket.suggestedLevel && chart.level <= 30));
    assert.equal(bucket.contributingCharts.length, bucket.selectedCharts);
  }
});

test("old methods and incorrect reference metadata are rejected", () => {
  for (const mutate of [
    (value: any) => {value.methodVersion = 1;},
    (value: any) => {value.methodVersion = 2;},
    (value: any) => {value.methodVersion = 3;},
    (value: any) => {value.methodVersion = 4;},
    (value: any) => {value.methodVersion = 5;},
    (value: any) => {delete value.reference;},
    (value: any) => {value.reference.plate = "PG";},
    (value: any) => {value.reference.minimumScore = 1_000_000;},
    (value: any) => {value.reference.extra = "private";},
  ]) {
    const value = structuredClone(fixture); mutate(value);
    assert.throws(() => validateSkillRadarResponse(value), /invalid/);
  }
});

test("all nine BPM ceilings preserve Singles calibration and restore independent Doubles bounds", () => {
  const profile = validateSkillRadarResponse(fixture);
  assert.deepEqual(profile.bpmBuckets.map(bin => bin.radarScale.singles.maximumAlignedLevel), [22.834, 22.9735, 22.4215, 23.113, 23.113, 23.968, 24.7, 24.25, 25]);
  assert.deepEqual(profile.bpmBuckets.map(bin => bin.radarScale.doubles.maximumAlignedLevel), [23.8, 23.95, 23.65, 23.95, 24.1, 24.55, 25, 25, 25]);
  for (const bin of profile.bpmBuckets) {
    for (const mode of ["singles", "doubles"] as const) {
      const {minimumAlignedLevel: lower, maximumAlignedLevel: upper} = bin.radarScale[mode];
      assert.equal(lower, 10);
      const offset = mode === "doubles" ? 1 : 0;
      const raw = upper + offset;
      assert.ok(Math.abs(radarScoreForLevel(raw - offset, bin, mode) - 100) < 1e-8);
      assert.equal(radarScoreForLevel(lower, bin, mode), 0);
      assert.ok(Math.abs(radarScoreForLevel((lower + upper) / 2, bin, mode) - 50) < 1e-8);
      assert.equal(radarScoreForLevel(upper + 1, bin, mode), 100);
      assert.equal(radarScoreForLevel(lower - 1, bin, mode), 0);
    }
  }
  assert.ok(radarScoreForLevel(22.97, profile.bpmBuckets[1], "singles") < 100);
  assert.ok(radarScoreForLevel(20, profile.bpmBuckets[0], "singles") > radarScoreForLevel(20, profile.bpmBuckets[0], "doubles"));
});

test("220+ BPM remains unchanged while only Singles uses the latest requested targets", () => {
  const profile = validateSkillRadarResponse(fixture);
  const unchanged = profile.bpmBuckets[8];
  for (const level of [0, 10, 18.1234567, 24.95, 25, 26]) {
    for (const mode of ["singles", "doubles"] as const) {
      assert.equal(radarScoreForLevel(level, unchanged, mode), Math.max(0, Math.min(100, 100 * (level - 10) / 15)));
    }
  }
  const previousCeilings = [23.8, 23.95, 23.65, 23.95, 24.1, 24.55, 25, 25, 25];
  const targets = [93, 93, 91, 94, 93, 96, 98, 95, 100];
  for (const [index, bin] of profile.bpmBuckets.entries()) {
    const level = 18.1234567;
    const previousScore = (alignedLevel: number) => 100 * (alignedLevel - 10) / (previousCeilings[index] - 10);
    assert.ok(Math.abs(previousScore(bin.radarScale.singles.maximumAlignedLevel) - targets[index]) < 1e-8);
    assert.ok(Math.abs(radarScoreForLevel(level, bin, "singles") - previousScore(level) / (targets[index] / 100)) < 1e-8);
    assert.equal(radarScoreForLevel(level, bin, "doubles"), previousScore(level));
  }
});

test("scale metadata is required, strictly bounded and used for score reconstruction", () => {
  for (const mutate of [
    (value: any) => {delete value.bpmBuckets[0].radarScale;},
    (value: any) => {value.bpmBuckets[0].radarScale = null;},
    (value: any) => {value.bpmBuckets[0].radarScale = {minimumAlignedLevel:10, maximumAlignedLevel:22.834};},
    (value: any) => {delete value.bpmBuckets[0].radarScale.doubles;},
    (value: any) => {delete value.bpmBuckets[0].radarScale.singles.maximumAlignedLevel;},
    (value: any) => {value.bpmBuckets[0].radarScale.singles.minimumAlignedLevel = 9;},
    (value: any) => {value.bpmBuckets[0].radarScale.doubles.minimumAlignedLevel = Number.NaN;},
    (value: any) => {value.bpmBuckets[0].radarScale.singles.maximumAlignedLevel = Number.NaN;},
    (value: any) => {value.bpmBuckets[0].radarScale.doubles.maximumAlignedLevel = Infinity;},
    (value: any) => {value.bpmBuckets[0].radarScale.singles.maximumAlignedLevel = 10;},
    (value: any) => {value.bpmBuckets[0].radarScale.doubles.maximumAlignedLevel = 25.1;},
    (value: any) => {value.bpmBuckets[0].radarScale.extra = "private";},
    (value: any) => {value.bpmBuckets[0].radarScale.singles.extra = "private";},
    (value: any) => {value.bpmBuckets[8].radarScale.singles.maximumAlignedLevel = 24;},
    (value: any) => {value.bpmBuckets[8].radarScale.doubles.maximumAlignedLevel = 24;},
    (value: any) => {value.bpmBuckets[1].radarScale.singles.maximumAlignedLevel = 24;},
  ]) {
    const value = structuredClone(fixture); mutate(value);
    assert.throws(() => validateSkillRadarResponse(value), /invalid/);
  }
  for (const index of [1, 6, 7, 8]) {
    const value = structuredClone(fixture);
    const bin = value.bpmBuckets[index];
    bin.radarScale.singles.maximumAlignedLevel = 24;
    bin.radarScale.doubles.maximumAlignedLevel = 24;
    for (const bucket of value.buckets.filter((bucket: any) => bucket.bpmBucketId === bin.id)) {
      bucket.radarScore = radarScoreForLevel(bucket.displayEquivalentLevel, bin, bucket.mode);
    }
    if (index === 8) assert.throws(() => validateSkillRadarResponse(value), /invalid/);
    else assert.doesNotThrow(() => validateSkillRadarResponse(value));
  }
});

test("Doubles ratings must reconstruct from Doubles bounds rather than the Singles scale", () => {
  const changed = structuredClone(fixture);
  const bucket = changed.buckets.find((bucket: any) => bucket.key === "doubles:1");
  const bin = changed.bpmBuckets.find((bin: any) => bin.id === bucket.bpmBucketId);
  bucket.radarScore = radarScoreForLevel(bucket.displayEquivalentLevel, bin, "singles");
  assert.throws(() => validateSkillRadarResponse(changed), /invalid/);
});

test("mode-specific UG references reconstruct genuine PG performances without changing plates", () => {
  const profile = validateSkillRadarResponse(fixture);
  const single = profile.buckets.find(bucket => bucket.key === "singles:1")!;
  const double = profile.buckets.find(bucket => bucket.key === "doubles:1")!;
  assert.ok(single.rawEquivalentLevel > 18);
  assert.ok(double.rawEquivalentLevel > 19);
  assert.notEqual(single.displayEquivalentLevel, double.displayEquivalentLevel);
  assert.ok(single.contributingCharts.every(chart => chart.plate === "PG"));
  assert.ok(double.contributingCharts.every(chart => chart.plate === "PG"));
  const changed = structuredClone(fixture);
  const wrong = changed.buckets.find((bucket: any) => bucket.key === "doubles:1");
  wrong.rawEquivalentLevel = single.rawEquivalentLevel + 1;
  wrong.displayEquivalentLevel = single.displayEquivalentLevel;
  wrong.radarScore = single.radarScore;
  assert.throws(() => validateSkillRadarResponse(changed), /invalid/);
});

test("sparse buckets average their available contributions rather than five slots", () => {
  const profile = validateSkillRadarResponse(fixture);
  const sparse = profile.buckets.find(bucket => bucket.key === "singles:4")!;
  assert.equal(sparse.selectedCharts, 3);
  assert.equal(sparse.needsPlays, true);
  assert.ok(Math.abs(sparse.rawEquivalentLevel - 24) < 1e-8);
  const one = profile.buckets.find(bucket => bucket.key === "doubles:4")!;
  assert.equal(one.selectedCharts, 1);
  assert.equal(one.needsPlays, true);
  assert.ok(Math.abs(one.rawEquivalentLevel - 25) < 1e-8);
  const empty = profile.buckets.find(bucket => bucket.selectedCharts === 0)!;
  assert.equal(empty.rawEquivalentLevel, 0);
  assert.equal(empty.radarScore, 0);
  const changed = structuredClone(fixture);
  changed.buckets.find((bucket: any) => bucket.key === "singles:4").needsPlays = false;
  assert.throws(() => validateSkillRadarResponse(changed), /invalid/);
});

test("recommendations allow up to ten sorted charts from the target and higher folders", () => {
  const value = structuredClone(fixture);
  const bucket = value.buckets.find((bucket: any) => bucket.mode === "singles" && bucket.suggestedLevel === 19);
  const template = bucket.suggestions[0];
  for (const count of [0, 5, 6, 9, 10]) {
    bucket.suggestions = Array.from({length:count}, (_, index) => ({...template, chartId:`practice-${index}`, level:index === 0 ? 20 : 19, difficulty:index === 0 ? "S20" : "S19", estimatedDifficulty:18.8 + index/10}));
    assert.equal(validateSkillRadarResponse(value).buckets.find(bucket => bucket.key === "singles:1")!.suggestions.length, count);
  }
  bucket.suggestions.push({...bucket.suggestions[9], chartId:"eleventh"});
  assert.throws(() => validateSkillRadarResponse(value), /invalid/);
  bucket.suggestions.pop();
  bucket.suggestions[0].level = 18;
  assert.throws(() => validateSkillRadarResponse(value), /invalid/);
  bucket.suggestions[0].level = 31;
  assert.throws(() => validateSkillRadarResponse(value), /invalid/);
  bucket.suggestions[0].level = 20;
  bucket.suggestions[0].estimatedDifficulty = 25;
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
    (value: any) => { value.buckets[0].rawEquivalentLevel = Number.NaN; },
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
    for (const [y, width, height] of [[label.labelY, 110, 22], [label.scoreY, 108, 17], [label.scoreY + 18, 108, 17]]) {
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

test("focal local reader pins pointer, manifest, roster and profile to one complete generation", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "piu-focal-radar-"));
  try {
    const directory = path.join(root,"players");
    const profile = focalFixture();
    const key = profile.player.playerKey;
    const identity = {bpmMetadataVersion:1,bpmPolicy:"focal-v1",bpmMetadataRevision:profile.bpmMetadataRevision};
    const generation = "b".repeat(64);
    const focalRoot = path.join(root,"focal-v1");
    const target = path.join(focalRoot,"generations",generation);
    await mkdir(path.join(target,"players"),{recursive:true});
    await writeFile(path.join(focalRoot,"current.json"),JSON.stringify({generation,...identity}));
    await writeFile(path.join(target,"manifest.json"),JSON.stringify({generation,...identity,methodVersion:7,profileVersion:12,players:[key]}));
    await writeFile(path.join(target,"players.json"),JSON.stringify({generation,...identity,generatedAtUtc:profile.generatedAtUtc,players:[{...profile.player,phoenix2ScoreCount:100}]}));
    await writeFile(path.join(target,"players",`${key}.json`),JSON.stringify(profile));
    assert.equal((await readLocalSkillRadar(key,directory,undefined,"focal-v1")).methodVersion,7);
    assert.equal((await readLocalSkillRadarPlayers(path.join(root,"players.json"),"focal-v1")).players.length,1);
    await writeFile(path.join(target,"players",`${key}.json`),JSON.stringify({...profile,bpmMetadataRevision:"c".repeat(64)}));
    await assert.rejects(readLocalSkillRadar(key,directory,undefined,"focal-v1"),/does not match/);
    await writeFile(path.join(focalRoot,"current.json"),JSON.stringify({generation:"../private",...identity}));
    await assert.rejects(readLocalSkillRadar(key,directory,undefined,"focal-v1"),/generation is invalid/);
  } finally {
    assert.equal(path.dirname(path.resolve(root)),path.resolve(tmpdir()));
    await rm(root,{recursive:true,force:true});
  }
});
