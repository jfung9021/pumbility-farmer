import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { compactTierPayload, tierBrowserPayload, tierBrowserJson, tierChartDetails } from "../lib/tier-browser-payload.ts";
import { createTierChartDetailLoader } from "../lib/tier-chart-details.ts";
import { hasScoringGoalVariants, projectScoringGoal, SCORING_GOALS } from "../lib/scoring-variants.ts";
import { clearingMinimumScoreRecords, clearingPercentileRange, clearingSkillPercentile, estimatedTierGroups, hasLimitedTierData, selectedTierMetric, sortTierCharts, tierBandCharts, tierSupportLabel } from "../lib/tier-metrics.ts";
import { matchesTierBpm, matchesTierLevel, tierBpmBounds } from "../lib/tier-filters.ts";
import { formatDisplayBpm } from "../lib/song-bpm.ts";
import { resolvePumbilityPolicy } from "../lib/pumbility-policy.ts";
import { hasMidpointCompression } from "../lib/tier-postprocessing.ts";
import type { AnalysisPayload } from "../lib/types";

const source = JSON.parse(readFileSync(new URL("./fixtures/tier-public-source.json", import.meta.url), "utf8")) as AnalysisPayload;
const expected = JSON.parse(readFileSync(new URL("./fixtures/tier-public-list.json", import.meta.url), "utf8"));

test("browser DTO preserves all five goal/metric browse results while lazy details retain intervals and evidence", () => {
  const payload = structuredClone(source);
  payload.schemaVersion = 58;
  payload.summary.method.availableScoringGoals = [...SCORING_GOALS];
  payload.summary.method.crossLevelClearing = {version: 2, skillMethod: {minimumScoreRecords: 100},
    calibration: {audit: Array(1000).fill(0)}} as unknown as AnalysisPayload["summary"]["method"]["crossLevelClearing"];
  for (const row of payload.doubles) {
    Object.assign(row, {bpmFocal:155, songType:"Arcade"});
    for (const key of ["scoringVariants", "pumbilityVariants"] as const) {
      for (const goal of ["ss", "ssplus"] as const) row[key]![goal] = structuredClone(row[key]!.sssplus!);
    }
    Object.assign(row.scoringVariants!.ss!, {scoringMethod:"target-boundary-score-gap-v1", scoringTarget:980000});
    Object.assign(row.scoringVariants!.ssplus!, {scoringMethod:"target-attainment-rasch-v1", scoringTarget:985000});
  }
  const old = compactTierPayload(payload);
  const oldBefore = structuredClone(old), sourceBefore = structuredClone(payload);
  const browser = tierBrowserPayload(old);
  for (const goal of SCORING_GOALS) {
    assert.equal(hasScoringGoalVariants(browser, goal), hasScoringGoalVariants(old, goal));
    for (const metric of ["scoring", "clearing", "pumbility"] as const) {
      const a = old.doubles.map(row => projectScoringGoal(row, goal, metric));
      const b = browser.doubles.map(row => projectScoringGoal(row, goal, metric));
      const display = (rows: typeof a) => rows.map(row => ({id:row.chartId, song:row.songName,
        difficulty:row.difficulty, type:row.type, image:row.imageUrl, artist:row.stepArtist,
        point:selectedTierMetric(row, metric).estimatedDifficulty, bpm:formatDisplayBpm(row, false),
        warning:hasLimitedTierData(row, metric), support:tierSupportLabel(row, metric),
        pointPercentile:clearingSkillPercentile(row.tierMetrics?.clearing),
        percentileRange:clearingPercentileRange(row.tierMetrics?.clearing)}));
      assert.deepEqual(display(b), display(a));
      assert.deepEqual(sortTierCharts(b, metric).map(row => row.chartId), sortTierCharts(a, metric).map(row => row.chartId));
      assert.deepEqual(estimatedTierGroups(b, metric, "doubles").map(group => [group.value, group.charts.map(row => row.chartId)]),
        estimatedTierGroups(a, metric, "doubles").map(group => [group.value, group.charts.map(row => row.chartId)]));
      for (let rank = 1; rank <= 7; rank++) assert.deepEqual(tierBandCharts(b, metric, rank).map(row => row.chartId), tierBandCharts(a, metric, rank).map(row => row.chartId));
      for (const level of ["All", "18", "20+"]) for (const bpm of [null, {min:150,max:160}, {min:160,max:170}]) {
        const filtered = (rows: typeof a) => rows.filter(row => matchesTierLevel(row, level) && matchesTierBpm(row, bpm)
          && `${row.songName} ${row.stepArtist}`.toLocaleLowerCase().includes("example")).map(row => row.chartId);
        assert.deepEqual(filtered(b), filtered(a));
      }
      assert.deepEqual(tierBpmBounds(b), tierBpmBounds(a));
    }
  }
  assert.equal(clearingMinimumScoreRecords(browser), clearingMinimumScoreRecords(old));
  assert.deepEqual(resolvePumbilityPolicy(browser), resolvePumbilityPolicy(old));
  assert.equal(hasMidpointCompression(browser), hasMidpointCompression(old));
  assert.deepEqual(browser.tierListView, {...old.tierListView, browserVersion:1});
  assert.deepEqual(browser.summary.method.crossLevelClearing, {version:2,skillMethod:{minimumScoreRecords:100}});
  assert.equal(browser.doubles[0].scoringVariants!.ss!.difficultyCi95Low, undefined);
  assert.deepEqual(tierChartDetails(payload, "d18")?.chart.scoringVariants.ss.scoringRefinement,
    payload.doubles[0].scoringVariants!.ss!.scoringRefinement);
  assert.equal(tierChartDetails(payload, "d18")?.chart.scoringVariants.ss.difficultyCi95Low,
    payload.doubles[0].scoringVariants!.ss!.difficultyCi95Low);
  assert.ok(Buffer.byteLength(tierBrowserJson(payload)) < Buffer.byteLength(JSON.stringify(old))*.7);
  assert.equal(tierBrowserPayload(browser), browser);
  assert.deepEqual(payload, sourceBefore);
  assert.deepEqual(old, oldBefore);
});

test("local list projection matches Python publication DTO and preserves full-precision grouping", () => {
  const before = structuredClone(source);
  const actual = compactTierPayload(source);
  assert.deepEqual(actual, expected);
  assert.deepEqual(source, before);
  for (const goal of ["general", "sss", "sssplus"] as const) {
    const original = source.doubles.map(row => projectScoringGoal(row, goal, "pumbility"));
    const compact = actual.doubles.map(row => projectScoringGoal(row, goal, "pumbility"));
    assert.deepEqual(sortTierCharts(compact, "pumbility").map(row => row.chartId), sortTierCharts(original, "pumbility").map(row => row.chartId));
    assert.deepEqual(estimatedTierGroups(compact, "pumbility", "doubles").map(group => group.value), estimatedTierGroups(original, "pumbility", "doubles").map(group => group.value));
    assert.deepEqual(tierBandCharts(compact, "scoring", 2).map(row => row.chartId), tierBandCharts(original, "scoring", 2).map(row => row.chartId));
  }
  assert.deepEqual(tierChartDetails(source, "d18")?.chart.scoringVariants.sssplus.scoringRefinement, source.doubles[0].scoringVariants?.sssplus?.scoringRefinement);
  assert.equal(tierChartDetails(source, "missing"), null);
});

test("lazy evidence is deduplicated, retried after failures, and isolated by publication", async () => {
  const calls: string[] = [];
  let fail = true;
  const load = createTierChartDetailLoader((async (url: string) => {
    calls.push(url);
    if (fail) return Response.json({error:"Temporarily unavailable"}, {status:503});
    const params = new URL(url, "https://example.test").searchParams;
    return Response.json({generatedAtUtc:params.get("generatedAtUtc"), chart:source.doubles[0]});
  }) as typeof fetch);
  await assert.rejects(load("d18", source.generatedAtUtc), /Temporarily unavailable/);
  fail = false;
  const [a, b] = await Promise.all([load("d18", source.generatedAtUtc), load("d18", source.generatedAtUtc)]);
  assert.equal(a, b); assert.equal(calls.length, 2);
  await load("d18", source.generatedAtUtc); assert.equal(calls.length, 2);
  await load("d18", "next-generation"); assert.equal(calls.length, 3);
  const mismatch = createTierChartDetailLoader((async () => Response.json({generatedAtUtc:"wrong", chart:source.doubles[0]})) as typeof fetch);
  await assert.rejects(mismatch("d18", source.generatedAtUtc), /do not match/);
});

test("compact rows and lazy detail requests preserve and pin the BPM metadata revision", async () => {
  const payload = structuredClone(source);
  payload.bpmMetadata = {bpmMetadataVersion:1,bpmPolicy:"focal-v1",bpmMetadataRevision:"a".repeat(64)};
  Object.assign(payload.doubles[0], {bpmMin:100,bpmMax:225,bpmFocal:169,songType:"Arcade"});
  const compact = compactTierPayload(payload);
  assert.deepEqual(compact.bpmMetadata, payload.bpmMetadata);
  assert.equal(compact.doubles[0].bpmFocal, 169);
  assert.equal(compact.doubles[0].bpmMin, 100);
  assert.equal(compact.doubles[0].bpmMax, 225);
  assert.deepEqual(tierChartDetails(payload, "d18")?.bpmMetadata, payload.bpmMetadata);
  let calls = 0;
  const load = createTierChartDetailLoader((async (url: string) => {
    calls++;
    const params = new URL(url, "https://example.test").searchParams;
    assert.equal(params.get("bpmPolicy"), "focal-v1");
    return Response.json({...tierChartDetails(payload, "d18"), bpmMetadata:{...payload.bpmMetadata,bpmMetadataRevision:params.get("bpmMetadataRevision")}});
  }) as typeof fetch);
  await load("d18", payload.generatedAtUtc, "a".repeat(64));
  await load("d18", payload.generatedAtUtc, "b".repeat(64));
  assert.equal(calls, 2);
  const mismatch = createTierChartDetailLoader((async () => Response.json(tierChartDetails(payload, "d18"))) as typeof fetch);
  await assert.rejects(mismatch("d18",payload.generatedAtUtc,"b".repeat(64)), /do not match/);
});
