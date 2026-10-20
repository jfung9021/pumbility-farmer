import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { demoPayloads } from "../lib/demo-data.ts";
import { chartEvidenceLines, plainTierDataWarning, TIER_INTERVAL_TOOLTIP } from "../lib/tier-evidence-copy.ts";
import { scoringTargetForGoal } from "../lib/scoring-variants.ts";
import type { ChartResult, ScoringGoal } from "../lib/types";

function chart(): ChartResult {
  const row = structuredClone(demoPayloads.phoenix2.singles[0]);
  Object.assign(row, { nContributors: 12, estimatedDifficulty: 20.5, scoringProvisional: false });
  Object.assign(row.tierMetrics!.clearing, { method: "submitted-cross-level-clearing-v2", clearCount: 30,
    ratedClearCount: 12, selectedCount: 25, estimatedDifficulty: 20.6 });
  Object.assign(row.tierMetrics!.pumbility, { aggregation: "average", estimatedDifficulty: 20.55 });
  return row;
}

function target(goal: Exclude<ScoringGoal, "general">): ChartResult {
  const row = chart();
  Object.assign(row, { scoringMethod: "target-attainment-rasch-v1", scoringGoal: goal,
    scoringTarget: scoringTargetForGoal(goal).score, scoringTargetSuccesses: 2, scoringBelowTarget: 10,
    scoringRefinement: { version: 1, branch: "mixed", range: null,
      fullBoard: { records: 300, successes: 150, belowTarget: 150, highestScore: 999999, lowestScore: 0, possiblyTruncated: false },
      modelEligible: { records: 12, successes: 2, belowTarget: 10 } } });
  return row;
}

test("each target describes only records used by its selected calculation", () => {
  for (const goal of ["ss", "ssplus", "sss", "sssplus"] as const) {
    const row = target(goal);
    const copy = chartEvidenceLines(row, "scoring").join(" ");
    const label = scoringTargetForGoal(goal).label;
    assert.match(copy, /12 usable player records; 2 reached/);
    assert.ok(copy.includes(`${label}${goal === "sssplus" ? "." : " or higher."}`));
    assert.doesNotMatch(copy, /300|150|999,999|model|latent|bootstrap|asinh|frozen|offset|calibration/i);
  }
});

test("boundary estimates use complete-board counts and explain one-sided records", () => {
  for (const successes of [0, 20]) {
    const row = target("ss");
    row.scoringMethod = "target-boundary-score-gap-v1";
    row.nContributors = 2;
    Object.assign(row.scoringRefinement!.fullBoard, { records: 20, successes, belowTarget: 20 - successes });
    const copy = chartEvidenceLines(row, "scoring").join(" ");
    assert.match(copy, new RegExp(`20 recorded best scores; ${successes} reached SS or higher`));
    assert.match(copy, successes ? /Everyone in the available records/ : /No one in the available records/);
    assert.doesNotMatch(copy, /2 usable|2 recorded|150|300/);
    assert.equal(plainTierDataWarning(row, "scoring"), null);
  }
});

test("general and Co-op explanations use actual usable counts without model diagnostics", () => {
  const row = chart();
  row.scoringMethod = "official-cross-level-v1";
  row.scoringPlayerCount = 9;
  row.nContributors = 500;
  assert.match(chartEvidenceLines(row, "scoring").join(" "), /9 usable player records/);
  assert.doesNotMatch(chartEvidenceLines(row, "scoring").join(" "), /500/);
  assert.match(plainTierDataWarning(row, "scoring")!, /uses 9 records/);
  delete row.scoringMethod;
  row.type = "CoOp";
  assert.match(chartEvidenceLines(row, "scoring").join(" "), /500 usable player records/);
});

test("Clearing qualification follows the publication's requirement without raw technical reasons", () => {
  const row = chart();
  Object.assign(row.tierMetrics!.clearing, { clearCount: 6, ratedClearCount: 3, missingSkillCount: 3,
    provisionalReasons: ["outside-supported-anchors", "chart-comparability-changed"], extrapolated: true });
  for (const minimumScoreRecords of [100, 200]) {
    const copy = chartEvidenceLines(row, "clearing", { minimumScoreRecords }).join(" ");
    assert.match(copy, /3 of 6 clearers had enough play history to include/);
    assert.ok(copy.includes(`at least ${minimumScoreRecords} recorded charts in this mode`));
    assert.match(copy, /fewer than 10/);
    assert.match(copy, /different chart version/);
    assert.match(copy, /Few similar charts/);
    assert.doesNotMatch(copy, /outside-supported|percentile|anchor|piecewise|threshold|initial fit/i);
  }
  assert.doesNotMatch(chartEvidenceLines(row, "clearing").join(" "), /at least (100|200)/);
});

test("Pumbility explains the selected target and actual average or maximum with component support", () => {
  const row = target("ssplus");
  for (const aggregation of ["average", "maximum"] as const) {
    row.tierMetrics!.pumbility.aggregation = aggregation;
    const lines = chartEvidenceLines(row, "pumbility", { minimumScoreRecords: 100,
      pumbilityAggregation: aggregation === "average" ? "maximum" : "average" });
    assert.ok(lines.length <= 5);
    assert.match(lines[0], /^SS\+ Scoring: S20.5. Clearing: S20.6/);
    assert.match(lines[1], aggregation === "average" ? /uses their average, calculated before rounding/ : /uses the higher rating/);
    assert.match(lines[2], /12 usable player records; 2 reached SS\+ or higher/);
    assert.match(lines[3], /12 of 30 clearers/);
    assert.doesNotMatch(lines.join(" "), /25 clearers|300/);
  }
  row.tierMetrics!.pumbility.estimatedDifficulty = null;
  row.tierMetrics!.clearing.estimatedDifficulty = null;
  assert.match(chartEvidenceLines(row, "pumbility").join(" "), /needs both a Scoring and a Clearing rating/);
});

test("limited-data warning keeps the 9/10 threshold for each component and unknown counts", () => {
  const row = target("sss");
  for (const records of [9, 10]) {
    row.nContributors = records;
    assert.equal(plainTierDataWarning(row, "scoring") !== null, records < 10);
    assert.equal(plainTierDataWarning(row, "pumbility") !== null, records < 10);
  }
  row.tierMetrics!.clearing.ratedClearCount = 9;
  assert.match(plainTierDataWarning(row, "pumbility")!, /Clearing uses 9 clearers/);
  row.scoringMethod = "official-cross-level-v1";
  assert.match(plainTierDataWarning(row, "scoring")!, /number of usable records is unavailable/);
});

test("incomplete boards, exclusions, rare attainment and unrated states use everyday warnings", () => {
  const row = target("sssplus");
  row.scoringPossiblyTruncated = true;
  row.scoringMethod = "target-conditional-pooled-prior-v1";
  row.scoringComparability = "changed";
  Object.assign(row.tierMetrics!.clearing, { defaultedAtCap: true });
  let copy = chartEvidenceLines(row, "pumbility").join(" ");
  assert.match(copy, /Few players reached SSS\+/);
  assert.match(copy, /leaderboard may be incomplete/);
  assert.match(copy, /different chart version/);
  assert.match(copy, /Clearing uses a fallback/);
  row.estimatedDifficulty = null;
  row.scoringUnratedReason = "target-variant-unavailable";
  copy = chartEvidenceLines(row, "scoring").join(" ");
  assert.match(copy, /This scoring target is unavailable/);
  assert.doesNotMatch(copy, /target-variant-unavailable/);
});

test("the shared chart panel renders only concise copy and keeps lazy detail loading", () => {
  const page = readFileSync(new URL("../app/tier-list/page.tsx", import.meta.url), "utf8");
  const detail = page.slice(page.indexOf("function ChartDetails"), page.indexOf("function ChartCard"));
  assert.match(detail, /chartEvidenceLines\(chart, metric, \{ minimumScoreRecords, pumbilityAggregation \}\)/);
  assert.match(detail, /TIER_INTERVAL_TOOLTIP/);
  assert.match(page, /<ChartDetails chart=\{loaded\} metric=\{metric\}[^>]*detailsLoaded/);
  assert.doesNotMatch(detail, /latent|bootstrap|asinh|frozen|offset|exception|coefficient|percentile/i);
  assert.match(TIER_INTERVAL_TOOLTIP, /does not include every source of uncertainty/);
  assert.match(page, /const level20Width = Boolean\(payload\?\.summary.method.level20ScoringWidth\)/);
  assert.match(page, /showSssMethod && level20Width \? <p[^>]*>S20 and D20 ratings aim to keep most charts near their official level/);
});
