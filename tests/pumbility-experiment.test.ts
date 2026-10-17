import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import type { AnalysisPayload } from "../lib/types";
import { applyAveragePumbility, averageDifficulties, recoverMaximumPumbility } from "../lib/pumbility-experiment.ts";
import { validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { resolvePumbilityPolicy } from "../lib/pumbility-policy.ts";
import { hasLimitedTierData, hasQualifiedSubmittedClearing, tierSupportLabel } from "../lib/tier-metrics.ts";

const fixture = JSON.parse(await readFile(new URL("./fixtures/midpoint-compressed-tier.json", import.meta.url), "utf8")) as AnalysisPayload;
const hybridFixture = JSON.parse(await readFile(new URL("./fixtures/hybrid-midpoint-tier.json", import.meta.url), "utf8")) as AnalysisPayload;

test("averages full-precision difficulties with mixed signs, equal values, and missing components", () => {
  assert.equal(averageDifficulties(20.9, 23), 21.95);
  assert.equal(averageDifficulties(23.013, 24.217), (23.013 + 24.217) / 2);
  assert.equal(averageDifficulties(21.5, 21.5), 21.5);
  assert.equal(averageDifficulties(null, 23), null);
  assert.equal(averageDifficulties(23, null), null);
  assert.throws(() => averageDifficulties(NaN, 23), /Invalid/);
});

test("local candidate validates and exactly preserves the maximum baseline and other metrics", () => {
  const before = structuredClone(fixture);
  const candidate = applyAveragePumbility(validateLocalAnalysisPayload(fixture, "combined"));
  validateLocalAnalysisPayload(candidate, "combined");
  assert.deepEqual(fixture, before);
  assert.deepEqual(recoverMaximumPumbility(candidate), fixture);
  assert.deepEqual(applyAveragePumbility(candidate), candidate);
  assert.equal(resolvePumbilityPolicy(candidate)?.aggregation, "average");
  assert.equal(resolvePumbilityPolicy(fixture)?.aggregation, "maximum");
  assert.deepEqual(candidate.coop, fixture.coop);
  for (const mode of ["singles", "doubles"] as const) for (const [i, row] of candidate[mode].entries()) {
    const original = fixture[mode][i], c = row.tierMetrics!.clearing, p = row.tierMetrics!.pumbility;
    assert.deepEqual(c, original.tierMetrics!.clearing);
    assert.deepEqual({ ...row, tierMetrics: undefined }, { ...original, tierMetrics: undefined });
    assert.equal(p.evidenceStatus, original.tierMetrics!.pumbility.evidenceStatus);
    assert.equal(p.scoringSupportCount, original.tierMetrics!.pumbility.scoringSupportCount);
    if (row.estimatedDifficulty === null || c.estimatedDifficulty === null) assert.equal(p.estimatedDifficulty, null);
    else {
      assert.ok(Math.abs(p.estimatedDifficulty! - (row.estimatedDifficulty + c.estimatedDifficulty) / 2) < 1e-12);
      assert.ok(p.estimatedDifficulty! <= p.maximumBaseline!.estimatedDifficulty! + 1e-12);
    }
  }
});

test("recomputes folder ranks and rejects corrupted values, policies, ranks, or baselines", () => {
  const candidate = applyAveragePumbility(fixture);
  for (const mode of ["singles", "doubles"] as const) {
    for (const level of new Set(candidate[mode].map(r => r.level))) {
      const group = candidate[mode].filter(r => r.level === level && r.tierMetrics!.pumbility.estimatedDifficulty !== null)
        .sort((a,b) => a.tierMetrics!.pumbility.levelRank! - b.tierMetrics!.pumbility.levelRank!);
      group.forEach((row, i) => {
        assert.equal(row.tierMetrics!.pumbility.levelRank, i + 1);
        assert.equal(row.tierMetrics!.pumbility.levelComparisonCharts, group.length);
        if (i) assert.ok(row.tierMetrics!.pumbility.estimatedDifficulty! >= group[i-1].tierMetrics!.pumbility.estimatedDifficulty!);
      });
    }
  }
  for (const kind of ["value", "rank", "policy", "baseline"]) {
    const bad = structuredClone(candidate);
    const row = [...bad.singles, ...bad.doubles].find(r => r.tierMetrics!.pumbility.estimatedDifficulty !== null)!;
    if (kind === "value") row.tierMetrics!.pumbility.estimatedDifficulty! += .1;
    if (kind === "rank") row.tierMetrics!.pumbility.levelRank! += 1;
    if (kind === "policy") (bad.summary.method.pumbilityExperiment as Record<string, unknown>).reference = "other";
    if (kind === "baseline") row.tierMetrics!.pumbility.maximumBaseline!.estimatedDifficulty! += .1;
    assert.throws(() => validateLocalAnalysisPayload(bad, "combined"), /incompatible/);
  }
});

test("hybrid submitted Clearing retains weak evidence even with plentiful clearers", () => {
  const row = structuredClone(fixture.singles[0]);
  row.scoringMethod = "submitted-score-profile-v2";
  row.nContributors = 50;
  const c = row.tierMetrics!.clearing;
  Object.assign(c, { method: "submitted-folder-centered-clearing-v1", ratedClearCount: 50,
    q10Skill: 20, evidenceStatus: "Provisional", provisionalReasons: ["chart-comparability-unknown"] });
  delete c.q20Skill; delete c.q50Skill; delete c.q30Skill;
  Object.assign(row.tierMetrics!.pumbility, {scoringSupportCount: 50, clearingSupportCount: 50});
  assert.equal(hasQualifiedSubmittedClearing(c), true);
  assert.equal(hasLimitedTierData(row, "scoring"), false);
  assert.equal(hasLimitedTierData(row, "clearing"), true);
  assert.equal(hasLimitedTierData(row, "pumbility"), true);
  assert.match(tierSupportLabel(row, "clearing"), /50 rated/);
  row.scoringMethod = "official-cross-level-v1";
  Object.assign(row, {scoringPlayerCount: 50, scoringProvisional: false});
  c.method = "submitted-cross-level-clearing-v2";
  assert.equal(hasLimitedTierData(row, "clearing"), true);
  c.evidenceStatus = "Published";
  assert.equal(hasLimitedTierData(row, "pumbility"), false);
});

test("hybrid average restores lower-tier coverage without compressing or changing either component", () => {
  const base = validateLocalAnalysisPayload(hybridFixture, "combined");
  const candidate = applyAveragePumbility(base);
  assert.equal(candidate.schemaVersion, 48);
  assert.equal(resolvePumbilityPolicy(candidate)?.aggregation, "average");
  validateLocalAnalysisPayload(candidate, "combined");
  assert.deepEqual(recoverMaximumPumbility(candidate), base);
  assert.deepEqual(applyAveragePumbility(candidate), candidate);
  for (const mode of ["singles", "doubles"] as const) for (const [i, row] of candidate[mode].entries()) {
    const c = row.tierMetrics!.clearing, p = row.tierMetrics!.pumbility;
    assert.deepEqual(c, base[mode][i].tierMetrics!.clearing);
    assert.deepEqual({ ...row, tierMetrics: undefined }, { ...base[mode][i], tierMetrics: undefined });
    if (row.level < 20) {
      assert.equal(row.scoringMethod, "submitted-score-profile-v2");
      assert.equal(p.estimatedDifficulty, (row.estimatedDifficulty! + c.estimatedDifficulty!) / 2);
      assert.equal(p.initialFit!.estimatedDifficulty,
        (Number(row.initialFit!.estimatedDifficulty) + Number(c.initialFit!.estimatedDifficulty)) / 2);
    } else {
      // Upper official evidence is intentionally missing in this synthetic fixture.
      assert.equal(row.estimatedDifficulty, null);
      assert.equal(p.estimatedDifficulty, null);
    }
  }
  for (const kind of ["policy", "value", "range"]) {
    const bad = structuredClone(candidate);
    if (kind === "policy") (bad.summary.method.pumbilityExperiment as Record<string, unknown>).baseAnalysisSchemaVersion = 44;
    if (kind === "value") bad.singles[0].tierMetrics!.pumbility.estimatedDifficulty! += .1;
    if (kind === "range") bad.singles[0].scoringMethod = "official-cross-level-v1";
    assert.throws(() => validateLocalAnalysisPayload(bad, "combined"), /incompatible/);
  }
});

test("schema 48 ties use Python Unicode code-point ordering for names and chart IDs", () => {
  const pairs = [
    ["a", "id"], ["A", "id"], ["é", "id"], ["Ω", "id"], ["\uE000", "id"], ["\u{10000}", "id"], ["😀", "id"],
    ["A", "é"], ["A", "A"], ["A", "a"], ["A", "\uE000"], ["A", "\u{10000}"], ["A", "😀"],
  ];
  const base = structuredClone(hybridFixture);
  const example = base.singles.find(row => row.level < 20)!;
  base.singles = pairs.map(([songName, chartId]) => {
    const row = structuredClone(example);
    Object.assign(row, {songName, chartId, estimatedDifficulty: 19.5});
    row.initialFit!.estimatedDifficulty = 19.5;
    row.tierMetrics!.clearing.estimatedDifficulty = 19.5;
    row.tierMetrics!.clearing.initialFit!.estimatedDifficulty = 19.5;
    return row;
  });
  const ordered = applyAveragePumbility(base).singles
    .sort((a, b) => a.tierMetrics!.pumbility.levelRank! - b.tierMetrics!.pumbility.levelRank!)
    .map(row => [row.songName, row.chartId]);
  // Expected order from Python sorted(pairs), whose string comparison uses code points.
  assert.deepEqual(ordered, [
    ["A", "A"], ["A", "a"], ["A", "id"], ["A", "é"], ["A", "\uE000"], ["A", "\u{10000}"], ["A", "😀"],
    ["a", "id"], ["é", "id"], ["Ω", "id"], ["\uE000", "id"], ["\u{10000}", "id"], ["😀", "id"],
  ]);
  const legacy = structuredClone(base);
  legacy.schemaVersion = 44;
  const legacyOrdered = applyAveragePumbility(legacy).singles
    .sort((a, b) => a.tierMetrics!.pumbility.levelRank! - b.tierMetrics!.pumbility.levelRank!)
    .map(row => [row.songName, row.chartId]);
  assert.deepEqual(legacyOrdered, [...pairs].sort((a, b) => a[0].localeCompare(b[0], "en") || a[1].localeCompare(b[1], "en")));
});
