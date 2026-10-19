import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { demoPayloads } from "../lib/demo-data.ts";
import { compactTierPayload } from "../lib/tier-browser-payload.ts";
import { projectScoringGoal, scoringTargetForGoal } from "../lib/scoring-variants.ts";
import { clearingMinimumScoreRecords, clearingRecordCount, hasLimitedTierData, scoringRecordCount, tierDataWarning, tierSupportLabel } from "../lib/tier-metrics.ts";
import type { AnalysisPayload, ChartResult, ScoringVariant } from "../lib/types";

function chart(): ChartResult {
  const row = structuredClone(demoPayloads.phoenix2.singles[0]);
  row.nContributors = 10;
  Object.assign(row.tierMetrics!.clearing, { selectedCount: 10, ratedClearCount: 10, clearCount: 10, evidenceStatus: "Published" });
  return row;
}

function target(row: ChartResult, records: number, boundary = false): ScoringVariant {
  return {
    estimatedDifficulty: 23.5, difficultyDelta: 0, levelRank: 1, levelComparisonCharts: 1,
    effectBand: "Equivalent", effectBandRank: 4, evidenceStatus: "Provisional", nContributors: boundary ? 2 : records,
    scoringMethod: boundary ? "target-boundary-score-gap-v1" : "target-attainment-rasch-v1", scoringTarget: 995000,
    scoringTargetSuccesses: 0, scoringBelowTarget: boundary ? 2 : records,
    ...(boundary ? { scoringRefinement: { version: 1, branch: "no-target", range: null,
      fullBoard: { records, successes: 0, belowTarget: records, highestScore: 994000, lowestScore: 980000, possiblyTruncated: false },
      modelEligible: { records: 2, successes: 0, belowTarget: 2 } } } : {}),
  } as ScoringVariant;
}

test("General Scoring warnings use the 9/10 valid-contributor boundary for S, D, and Co-op", () => {
  for (const type of ["Single", "Double", "CoOp"] as const) {
    const row = chart(); row.type = type;
    row.nPlayersScored = 500;
    row.nContributors = 9;
    assert.equal(hasLimitedTierData(row, "scoring"), true);
    assert.match(tierDataWarning(row, "scoring")!, /9 unique player observations/);
    row.nContributors = 10;
    row.evidenceStatus = "Provisional";
    assert.equal(hasLimitedTierData(row, "scoring"), false);
    assert.equal(tierDataWarning(row, "scoring"), null);
  }
});

test("Clearing warnings count qualified clearers rather than observed clears or metadata labels", () => {
  const row = chart();
  Object.assign(row.tierMetrics!.clearing, { method: "submitted-cross-level-clearing-v2", clearCount: 6, ratedClearCount: 3, selectedCount: 50 });
  assert.equal(clearingRecordCount(row), 3);
  assert.equal(hasLimitedTierData(row, "clearing"), true);
  assert.match(tierDataWarning(row, "clearing")!, /3 rated clearers/);
  row.tierMetrics!.clearing.ratedClearCount = 10;
  row.tierMetrics!.clearing.evidenceStatus = "Insufficient";
  assert.equal(hasLimitedTierData(row, "clearing"), false);
  delete row.tierMetrics!.clearing.method;
  row.tierMetrics!.clearing.selectedCount = 9;
  assert.equal(clearingRecordCount(row), 9);
  assert.equal(hasLimitedTierData(row, "clearing"), true);
});

test("official General uses only players with usable comparisons and reports unknown support honestly", () => {
  const row = chart();
  row.scoringMethod = "official-cross-level-v1";
  row.nContributors = 500;
  row.scoringPlayerCount = 9;
  assert.equal(scoringRecordCount(row), 9);
  assert.equal(hasLimitedTierData(row, "scoring"), true);
  row.scoringPlayerCount = 10;
  row.scoringProvisional = true;
  assert.equal(hasLimitedTierData(row, "scoring"), false);
  delete row.scoringPlayerCount;
  assert.equal(scoringRecordCount(row), null);
  assert.match(tierDataWarning(row, "scoring")!, /Unknown number.*valid-record count is unavailable/);
});

test("selected target boundary warnings use all complete-board records for zero and all successes", () => {
  for (const goal of ["ss", "ssplus", "sss", "sssplus"] as const) {
    for (const successes of ["none", "all"] as const) {
      const row = chart();
      row.scoringVariants = { general: target(row, 10), [goal]: target(row, 9, true) };
      const variant = row.scoringVariants[goal]!;
      variant.scoringTarget = scoringTargetForGoal(goal).score;
      if (successes === "all") Object.assign(variant.scoringRefinement!.fullBoard, { successes: 9, belowTarget: 0 });
      let selected = projectScoringGoal(row, goal);
      assert.equal(scoringRecordCount(selected), 9);
      assert.equal(hasLimitedTierData(selected, "scoring"), true);
      variant.scoringRefinement!.fullBoard.records = 10;
      selected = projectScoringGoal(row, goal);
      assert.equal(scoringRecordCount(selected), 10);
      assert.equal(hasLimitedTierData(selected, "scoring"), false);
    }
  }
});

test("ordinary target fits use contributors and composites check each selected component", () => {
  const row = chart();
  row.scoringVariants = { general: target(row, 10), sssplus: target(row, 9) };
  row.pumbilityVariants = { general: row.tierMetrics!.pumbility, sssplus: { ...row.tierMetrics!.pumbility, scoringSupportCount: 500, clearingSupportCount: 500 } };
  let selected = projectScoringGoal(row, "sssplus", "pumbility");
  assert.equal(hasLimitedTierData(selected, "pumbility"), true);
  assert.match(tierDataWarning(selected, "pumbility")!, /9 target-fit contributors.*at least one component/i);
  row.scoringVariants.sssplus!.nContributors = 10;
  selected = projectScoringGoal(row, "sssplus", "pumbility");
  assert.equal(hasLimitedTierData(selected, "pumbility"), false);
  row.tierMetrics!.clearing.selectedCount = 9;
  selected = projectScoringGoal(row, "sssplus", "pumbility");
  assert.equal(hasLimitedTierData(selected, "pumbility"), true);
  assert.equal(hasLimitedTierData(selected, "scoring"), false);
  assert.match(tierSupportLabel(selected, "pumbility"), /9 selected clearers/);
});

test("compact and detail payloads choose identical boundary support for each selected goal", () => {
  for (const count of [9, 10]) {
    const row = chart();
    row.scoringVariants = { general: target(row, 10), ss: { ...target(row, count, true), scoringTarget: 980000 },
      ssplus: { ...target(row, count, true), scoringTarget: 985000 },
      sss: { ...target(row, count, true), scoringTarget: 990000 }, sssplus: target(row, count, true) };
    const payload = { ...structuredClone(demoPayloads.phoenix2), singles: [row], doubles: [], coop: [] };
    const compact = compactTierPayload(payload);
    for (const goal of ["ss", "ssplus", "sss", "sssplus"] as const) {
      const full = projectScoringGoal(row, goal);
      const list = projectScoringGoal(compact.singles[0], goal);
      assert.equal(list.scoringRefinement, undefined);
      assert.equal(list.scoringBoundaryRecords, count);
      assert.equal(scoringRecordCount(full), scoringRecordCount(list));
      assert.equal(hasLimitedTierData(full, "scoring"), hasLimitedTierData(list, "scoring"));
      assert.equal(hasLimitedTierData(full, "pumbility"), hasLimitedTierData(list, "pumbility"));
      assert.match(tierSupportLabel(list, "scoring"), new RegExp(`^${count} available best scores`));
    }
    assert.doesNotMatch(JSON.stringify(compact), /highestScore|lowestScore|topScores|fullBoard/);
  }
});

test("unrated and missing counts never imply high support", () => {
  const row = chart();
  row.estimatedDifficulty = null; row.evidenceStatus = "Unrated"; row.nContributors = 0;
  assert.match(tierDataWarning(row, "scoring")!, /0 unique player observations/);
  assert.match(tierDataWarning(row, "scoring")!, /No rating is available/);
  Object.assign(row.tierMetrics!.clearing, { method: "official-d26-clearer-ability-combined-v2", ratedClearCount: 9 });
  assert.equal(hasLimitedTierData(row, "clearing"), true);
  row.tierMetrics!.clearing.ratedClearCount = 10;
  assert.equal(hasLimitedTierData(row, "clearing"), false);
});

test("record requirement copy follows the published 100 or legacy 200 threshold", () => {
  const payload = structuredClone(demoPayloads.phoenix2);
  const metadata = { skillMethod: { version: 2, minimumScoreRecords: 100 } };
  payload.summary.method.crossLevelClearing = metadata as AnalysisPayload["summary"]["method"]["crossLevelClearing"];
  assert.equal(clearingMinimumScoreRecords(payload), 100);
  metadata.skillMethod.version = 1; metadata.skillMethod.minimumScoreRecords = 200;
  assert.equal(clearingMinimumScoreRecords(payload), 200);
  metadata.skillMethod.minimumScoreRecords = 99;
  assert.equal(clearingMinimumScoreRecords(payload), null);
  assert.equal(clearingMinimumScoreRecords(null), null);
});

test("both tier layouts and the shared chart detail expose accessible count warnings and metadata copy", () => {
  const page = readFileSync(new URL("../app/tier-list/page.tsx", import.meta.url), "utf8");
  assert.match(page, /aria-label=\{warning\} role="img" title=\{warning\}/);
  assert.match(page, /<LimitedDataWarning chart=\{chart\} metric=\{metric\} \/>/);
  assert.match(page, /<LimitedDataWarning chart=\{chart\} metric=\{metric\} compact \/>/);
  assert.match(page, /<ChartDetails chart=\{loaded\} metric=\{metric\}[^>]*detailsLoaded/);
  assert.equal((page.match(/<CompactChartGrid[^>]*metric=\{/g) ?? []).length, 3);
  assert.match(page, /same-mode \$\{minimumScoreRecords\}-record requirement/);
  assert.doesNotMatch(page, /200-record|200 valid records|200 distinct valid/);
});
