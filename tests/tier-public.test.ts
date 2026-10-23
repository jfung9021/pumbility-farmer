import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { compactTierPayload, tierChartDetails } from "../lib/tier-browser-payload.ts";
import { createTierChartDetailLoader } from "../lib/tier-chart-details.ts";
import { projectScoringGoal } from "../lib/scoring-variants.ts";
import { estimatedTierGroups, sortTierCharts, tierBandCharts } from "../lib/tier-metrics.ts";
import type { AnalysisPayload } from "../lib/types";

const source = JSON.parse(readFileSync(new URL("./fixtures/tier-public-source.json", import.meta.url), "utf8")) as AnalysisPayload;
const expected = JSON.parse(readFileSync(new URL("./fixtures/tier-public-list.json", import.meta.url), "utf8"));

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
