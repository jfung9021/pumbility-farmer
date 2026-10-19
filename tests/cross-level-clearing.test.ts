import assert from "node:assert/strict";
import test from "node:test";
import { CROSS_LEVEL_CLEARING_POLICY, validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { COMBINED_MIX } from "../lib/mixes.ts";
import { demoPayloads } from "../lib/demo-data.ts";
import { hasLimitedTierData, tierSupportLabel } from "../lib/tier-metrics.ts";
import type { AnalysisPayload, ChartResult } from "../lib/types.ts";
import { AVERAGE_PUMBILITY_CALCULATION, resolvePumbilityPolicy } from "../lib/pumbility-policy.ts";

function fixture(): AnalysisPayload {
  const policy = structuredClone(CROSS_LEVEL_CLEARING_POLICY);
  const anchors = [16, 17, 18].map((level) => ({ level, threshold: level + .2, difficulty: level + .5,
    referenceCharts: 5, chartIds: Array.from({ length: 5 }, (_, i) => `${level}-${i}`) }));
  const rows = anchors.flatMap((a) => a.chartIds.map((id) => ({
    ...structuredClone(demoPayloads.phoenix2.singles[0]), chartId: id, type: "Single", level: a.level,
    estimatedDifficulty: a.level + .25, evidenceStatus: "Published", nContributors: 30,
    tierMetrics: {
      clearing: { estimatedDifficulty: a.difficulty, difficultyDelta: 0, levelRank: 1, levelComparisonCharts: 5,
        effectBandRank: 4, effectBand: "Equivalent", evidenceStatus: "Published", clearCount: 30, ratedClearCount: 20,
        missingSkillCount: 10, q10Skill: a.threshold, folderReferenceSkill: a.threshold,
        method: "submitted-cross-level-clearing-v1", sourceCounts: {phoenix1: 30, phoenix2: 30},
        retainedSources: ["phoenix2"], comparability: "comparable", anchorEligible: true,
        qualificationExclusions: { recordRequirement: 10 }, extrapolated: false, provisionalReasons: [],
        supportedRange: [16.5, 18.5], difficultyCi95Low: null, difficultyCi95High: null },
      pumbility: { estimatedDifficulty: a.level + .375, difficultyDelta: -.125, levelRank: 1, levelComparisonCharts: 5,
        effectBandRank: 3, effectBand: "Easy", evidenceStatus: "Published", scoringSupportCount: 30, clearingSupportCount: 20 },
    },
  }))) as ChartResult[];
  return {
    ...structuredClone(demoPayloads.phoenix2), schemaVersion: 43,
    mix: COMBINED_MIX,
    singles: rows, doubles: [], coop: [],
    summary: { ...structuredClone(demoPayloads.phoenix2.summary), method: {
      scoring: {version: 1, population: "combined", calibration: "original-residual-centering"},
      tierMetrics: {version: 11, clearing: policy,
        pumbility: {calculation: AVERAGE_PUMBILITY_CALCULATION}},
      crossLevelClearing: { ...policy, baseScoringSchemaVersion: 26,
        inputHashes: {phoenix1: "a".repeat(64), phoenix2: "b".repeat(64), currentCatalog: "c".repeat(64)},
        sourceDates: {phoenix1: "2026-09-25T00:00:00Z", phoenix2: "2026-10-02T00:00:00Z"},
        calibration: {Single: {version: 1, anchors, tailPolicy: "boundary-linear", supportedRange: [16.5, 18.5]}, Double: null},
      },
    }},
  } as AnalysisPayload;
}

test("schema 43 validates top-30/100 Clearing independently of Scoring selection", () => {
  const p = fixture();
  assert.equal(resolvePumbilityPolicy(p)?.aggregation, "average");
  assert.equal(validateLocalAnalysisPayload(p, "combined").schemaVersion, 43);
  assert.equal(tierSupportLabel(p.singles[0], "clearing"), "20 rated clearers");
  assert.equal(hasLimitedTierData(p.singles[0], "clearing"), false);
  for (const change of [
    (q: AnalysisPayload) => { q.summary.method.crossLevelClearing!.skillMethod.minimumScoreRecords = 99 as 100; },
    (q: AnalysisPayload) => { delete (q.summary.method.crossLevelClearing as unknown as Record<string, unknown>).skillMethod; },
    (q: AnalysisPayload) => { q.singles[0].tierMetrics!.clearing.estimatedDifficulty = 16.6; },
    (q: AnalysisPayload) => { q.singles[0].tierMetrics!.clearing.defaultedAtCap = true; },
    (q: AnalysisPayload) => { q.singles[0].tierMetrics!.clearing.missingSkillCount = 0; },
    (q: AnalysisPayload) => { q.summary.method.crossLevelClearing!.calibration.Single!.anchors[1].threshold = 0; },
    (q: AnalysisPayload) => { q.singles[0].tierMetrics!.pumbility.estimatedDifficulty = 99; },
  ]) {
    const bad = structuredClone(p); change(bad);
    assert.throws(() => validateLocalAnalysisPayload(bad, "combined"), /incompatible/);
  }
});

test("legacy 200-record publications remain readable and mixed policy identities are rejected", () => {
  const p = fixture();
  const tiers = p.summary.method.tierMetrics as { clearing: { skillMethod: { version: number; minimumScoreRecords: number } } };
  p.summary.method.crossLevelClearing!.skillMethod.version = 1;
  p.summary.method.crossLevelClearing!.skillMethod.minimumScoreRecords = 200;
  Object.assign(tiers.clearing.skillMethod, { version: 1, minimumScoreRecords: 200 });
  assert.equal(validateLocalAnalysisPayload(p, "combined").schemaVersion, 43);
  for (const [version, minimumScoreRecords] of [[1, 100], [2, 200], [2, 99]] as const) {
    const bad = structuredClone(p);
    Object.assign(bad.summary.method.crossLevelClearing!.skillMethod, { version, minimumScoreRecords });
    Object.assign((bad.summary.method.tierMetrics as typeof tiers).clearing.skillMethod, { version, minimumScoreRecords });
    assert.throws(() => validateLocalAnalysisPayload(bad, "combined"), /incompatible/);
  }
});

test("sparse finite Clearing is Insufficient and missing evidence is Unrated", () => {
  const p = fixture();
  const row = structuredClone(p.singles[0]); row.chartId = "sparse";
  Object.assign(row.tierMetrics!.clearing, { clearCount: 4, ratedClearCount: 4, missingSkillCount: 0,
    sourceCounts: {phoenix1: 4, phoenix2: 0}, qualificationExclusions: {}, anchorEligible: false,
    evidenceStatus: "Insufficient", provisionalReasons: ["fewer-than-20-rated-clearers"] });
  row.tierMetrics!.pumbility.clearingSupportCount = 4;
  row.tierMetrics!.pumbility.evidenceStatus = "Insufficient";
  p.singles.push(row);
  assert.equal(validateLocalAnalysisPayload(p, "combined").singles.length, 16);
  assert.equal(hasLimitedTierData(row, "clearing"), true);
  Object.assign(row.tierMetrics!.clearing, {clearCount: 0, ratedClearCount: 0, missingSkillCount: 0,
    q10Skill: null, estimatedDifficulty: null, difficultyDelta: null, sourceCounts: {phoenix1: 0, phoenix2: 0},
    comparability: "no-evidence", evidenceStatus: "Unrated", provisionalReasons: []});
  Object.assign(row.tierMetrics!.pumbility, {estimatedDifficulty: null, clearingSupportCount: 0, evidenceStatus: "Unrated"});
  assert.equal(validateLocalAnalysisPayload(p, "combined").singles.length, 16);
});

test("current Pumbility takes the maximum and rejects stale averages", () => {
  const p = fixture();
  (p.summary.method.tierMetrics as Record<string, unknown>).pumbility = {version: 2, aggregation: "maximum", requiresBoth: true};
  p.singles[0].estimatedDifficulty = 17.123456789;
  for (const row of p.singles) {
    const composite = row.tierMetrics!.pumbility;
    composite.aggregation = "maximum";
    composite.estimatedDifficulty = Math.max(row.estimatedDifficulty!, row.tierMetrics!.clearing.estimatedDifficulty!);
    composite.difficultyDelta = composite.estimatedDifficulty - row.level - .5;
  }
  assert.equal(validateLocalAnalysisPayload(p, "combined").singles[0].tierMetrics!.pumbility.estimatedDifficulty, 17.123456789);
  assert.equal(p.singles[1].tierMetrics!.pumbility.estimatedDifficulty, 16.5);
  const stale = structuredClone(p);
  stale.singles[0].tierMetrics!.pumbility.estimatedDifficulty = (17.123456789 + 16.5) / 2;
  assert.throws(() => validateLocalAnalysisPayload(stale, "combined"), /incompatible/);
  p.singles[0].estimatedDifficulty = null;
  p.singles[0].tierMetrics!.pumbility.estimatedDifficulty = null;
  assert.equal(validateLocalAnalysisPayload(p, "combined").singles[0].tierMetrics!.pumbility.estimatedDifficulty, null);
});
