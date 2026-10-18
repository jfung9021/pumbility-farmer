import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { DEFAULT_SCORING_GOAL, hasScoringGoalVariants, hasSssScoringVariants, HISTORICAL_SSS_UNAVAILABLE, isSssScoringChart, projectScoringGoal, scoringGoalFromSearchParams, scoringGoalUnavailableMessage, scoringGoalUrl, scoringTargetForChart, scoringTargetForGoal, scoringTargetForPayload } from "../lib/scoring-variants.ts";
import { estimatedTierGroups, hasLimitedTierData, selectedTierMetric, sortTierCharts, tierBandCharts, tierSupportLabel } from "../lib/tier-metrics.ts";
import type { AnalysisPayload, ChartResult, ScoringVariant } from "../lib/types";

const fixture = JSON.parse(await readFile(new URL("./fixtures/hybrid-midpoint-tier.json", import.meta.url), "utf8")) as AnalysisPayload;

function variantChart(): ChartResult {
  const row = structuredClone(fixture.singles[0]);
  const sss: ScoringVariant = {
    estimatedDifficulty: 19.72, difficultyDelta: .22, levelRank: 4, levelComparisonCharts: 5,
    effectBandRank: 4, effectBand: "Hard", evidenceStatus: "Published", nContributors: 35, nPlayersScored: 35,
    difficultyCi95Low: 19.6, difficultyCi95High: 19.85, difficultyDeltaCi95Low: .1, difficultyDeltaCi95High: .35,
    initialFit: { estimatedDifficulty: 19.94 }, scoringMethod: "sss-target-rasch-v1", scoringTarget: 990000,
    scoringTargetSuccesses: 15, scoringBelowTarget: 20, scoringSource: "submitted", scoringUnratedReason: null,
    scoringProvisional: false, scoringExtrapolated: false, scoringChartEffect: .3, scoringInformation: 7,
  };
  row.scoringVariants = { general: { ...sss!, estimatedDifficulty: row.estimatedDifficulty }, sss };
  row.pumbilityVariants = {
    general: structuredClone(row.tierMetrics!.pumbility),
    sss: { ...structuredClone(row.tierMetrics!.pumbility), aggregation: "average", estimatedDifficulty: 19.61,
      difficultyDelta: .11, levelRank: 3, effectBandRank: 4, scoringSupportCount: 35, evidenceStatus: "Published" },
  };
  return row;
}

test("SSS+ default and explicit URL goals stay stable before and after every supported analysis loads", () => {
  assert.equal(DEFAULT_SCORING_GOAL, "sssplus");
  const historical = { ...fixture, schemaVersion: 51 };
  const current = { ...fixture, schemaVersion: 52 };
  for (const schemaVersion of [48, 49, 50, 51, 52, 53, 54, 55, 56, 57]) {
    for (const [query, expected] of [
      ["", "sssplus"], ["mode=doubles&metric=pumbility", "sssplus"], ["scoring=bad", "sssplus"],
      ["scoring=general", "general"], ["scoring=sss", "sss"], ["scoring=sssplus", "sssplus"],
    ] as const) {
      const params = new URLSearchParams(query);
      assert.equal(scoringGoalFromSearchParams(params), expected);
      assert.equal(scoringGoalFromSearchParams(params, { ...fixture, schemaVersion }), expected);
    }
  }
  assert.deepEqual(scoringTargetForPayload(current), {goal: "sssplus", label: "SSS+", score: 995000});
  assert.deepEqual(scoringTargetForPayload(historical), {goal: "sss", label: "SSS", score: 990000});
  assert.equal(scoringGoalFromSearchParams(new URLSearchParams("scoring=general")), "general");
  const original = new URL("http://localhost/tier-list?mode=doubles&metric=pumbility&player=abc#rankings-dashboard");
  const general = scoringGoalUrl(original.href, "general");
  assert.equal(general.searchParams.get("mode"), "doubles");
  assert.equal(general.searchParams.get("metric"), "pumbility");
  assert.equal(general.searchParams.get("player"), "abc");
  assert.equal(general.hash, original.hash);
  const sss = scoringGoalUrl(general.href, "sss");
  assert.equal(scoringGoalFromSearchParams(sss.searchParams, historical), "sss");
  const canonical = scoringGoalUrl(sss.href, scoringGoalFromSearchParams(sss.searchParams, current));
  assert.equal(canonical.searchParams.get("scoring"), "sss");
  assert.equal(canonical.searchParams.get("player"), "abc");
  assert.equal(canonical.hash, original.hash);
  // Back/forward reads URL state, rather than persisting a conflicting local preference.
  assert.equal(scoringGoalFromSearchParams(general.searchParams), "general");
  assert.equal(hasSssScoringVariants({ ...fixture, schemaVersion: 57 }), true);
  assert.equal(scoringTargetForPayload({ ...fixture, schemaVersion: 57 }).goal, "sssplus");
});

test("selected target labels do not change to an older payload target", () => {
  assert.deepEqual(scoringTargetForGoal("sssplus"), { goal: "sssplus", label: "SSS+", score: 995000 });
  assert.deepEqual(scoringTargetForGoal("sss"), { goal: "sss", label: "SSS", score: 990000 });
  assert.deepEqual(scoringTargetForGoal("general"), scoringTargetForGoal(DEFAULT_SCORING_GOAL));
  const params = new URLSearchParams("scoring=sssplus");
  for (const payload of [null, { ...fixture, schemaVersion: 48 }, { ...fixture, schemaVersion: 51 }, { ...fixture, schemaVersion: 56 }]) {
    assert.equal(scoringTargetForGoal(scoringGoalFromSearchParams(params, payload)).label, "SSS+");
  }
});

test("target availability uses actual variants including unrated ones, without substituting another goal", () => {
  const row = variantChart();
  const historical = { ...fixture, schemaVersion: 51, singles: [row], doubles: [] };
  assert.equal(hasScoringGoalVariants(null, "sssplus"), false);
  assert.equal(hasScoringGoalVariants(fixture, "sssplus"), false);
  assert.equal(hasScoringGoalVariants(historical, "general"), true);
  assert.equal(hasScoringGoalVariants(historical, "sss"), true);
  assert.equal(hasScoringGoalVariants(historical, "sssplus"), false);
  row.scoringVariants!.sssplus = { ...row.scoringVariants!.sss!, scoringTarget: 995000, estimatedDifficulty: null, evidenceStatus: "Unrated" };
  assert.equal(hasScoringGoalVariants(historical, "sssplus"), true);
  assert.match(scoringGoalUnavailableMessage("sssplus"), /^SSS\+ is unavailable.*Unrated/);
  assert.match(scoringGoalUnavailableMessage("sss"), /^SSS is unavailable.*Unrated/);
});

test("SSS+ projection uses only its own target variant and matching Pumbility", () => {
  const row = variantChart(), historical = structuredClone(row.scoringVariants!.sss!);
  row.scoringVariants!.sssplus = { ...historical, estimatedDifficulty: 20.2, scoringTarget: 995000,
    scoringMethod: "target-attainment-rasch-v1", scoringTargetSuccesses: 5, scoringBelowTarget: 30 };
  row.pumbilityVariants!.sssplus = { ...row.pumbilityVariants!.sss!, estimatedDifficulty: 20.01 };
  const original = structuredClone(row);
  const projected = projectScoringGoal(row, "sssplus", "pumbility");
  assert.deepEqual(row, original);
  assert.equal(projected.estimatedDifficulty, 20.2);
  assert.equal(projected.scoringGoal, "sssplus");
  assert.equal(projected.scoringTarget, 995000);
  assert.equal(isSssScoringChart(projected), true);
  assert.deepEqual(scoringTargetForChart(projected), {goal: "sssplus", label: "SSS+", score: 995000});
  assert.match(tierSupportLabel(projected, "scoring"), /5 at SSS\+, 30 below/);
  assert.equal(projected.tierMetrics!.pumbility.estimatedDifficulty, 20.01);
  assert.deepEqual(projected.tierMetrics!.clearing, row.tierMetrics!.clearing);
  assert.deepEqual(row.scoringVariants!.sss, historical);
});

test("missing new target cannot substitute historical SSS or General in either list", () => {
  const row = variantChart();
  const projected = projectScoringGoal(row, "sssplus", "pumbility");
  assert.equal(projected.estimatedDifficulty, null);
  assert.equal(projected.difficultyCi95Low, null);
  assert.equal(projected.evidenceStatus, "Unrated");
  assert.equal(projected.initialFit, undefined);
  assert.equal(projected.modeRank, null);
  assert.equal(projected.scoringTarget, 995000);
  assert.equal(projected.tierMetrics!.pumbility.estimatedDifficulty, null);
  assert.equal(projected.tierMetrics!.pumbility.evidenceStatus, "Unrated");
  assert.equal(projectScoringGoal(row, "sssplus", "clearing"), row);
  const coop = { ...row, type: "CoOp" as const };
  assert.equal(projectScoringGoal(coop, "sssplus"), coop);
});

test("one projection supplies matching target values, ranks, intervals and components without mutating General", () => {
  const row = variantChart(), original = structuredClone(row);
  const projected = projectScoringGoal(row, "sss", "pumbility");
  assert.deepEqual(row, original);
  assert.equal(projectScoringGoal(row, "general"), row);
  assert.equal(projected.estimatedDifficulty, 19.72);
  assert.equal(projected.difficultyCi95Low, 19.6);
  assert.equal(projected.levelRank, 4);
  assert.equal(projected.scoringMethod, "sss-target-rasch-v1");
  assert.equal(projected.scoringScoreProfile, undefined);
  assert.equal(projected.officialScoringEvidence, undefined);
  assert.equal(projected.whatIfEstimates, undefined);
  assert.deepEqual(projected.tierMetrics!.clearing, row.tierMetrics!.clearing);
  assert.deepEqual(selectedTierMetric(projected, "pumbility"), row.pumbilityVariants!.sss!);
  assert.equal(tierBandCharts([projected], "scoring", 4)[0], projected);
  assert.equal(estimatedTierGroups([projected], "scoring", "singles")[0].value, 19.7);
  assert.match(tierSupportLabel(projected, "pumbility"), /35 target-fit contributors \(15 at SSS, 20 below\)/);
  assert.equal(hasLimitedTierData(projected, "scoring"), false);
  projected.evidenceStatus = "Provisional";
  assert.equal(hasLimitedTierData(projected, "scoring"), true);
});

test("missing SSS stays Unrated in both lists and cannot leak General values or ordering", () => {
  const rated = variantChart(), missing = variantChart();
  missing.chartId = "missing";
  Object.assign(missing.scoringVariants!.sss!, { estimatedDifficulty: null, difficultyDelta: null,
    difficultyCi95Low: null, difficultyCi95High: null, levelRank: null, levelComparisonCharts: null,
    effectBand: null, effectBandRank: null, evidenceStatus: "Unrated", scoringUnratedReason: "one-sided-outcomes" });
  Object.assign(missing.pumbilityVariants!.sss!, { estimatedDifficulty: null, difficultyDelta: null, evidenceStatus: "Unrated" });
  const rows = [missing, rated].map(row => projectScoringGoal(row, "sss"));
  assert.equal(rows[0].estimatedDifficulty, null);
  assert.equal(rows[0].difficultyCi95Low, null);
  assert.equal(rows[0].tierMetrics!.pumbility.estimatedDifficulty, null);
  assert.equal(rows[0].scoringUnratedReason, "one-sided-outcomes");
  assert.deepEqual(sortTierCharts(rows, "scoring").map(row => row.chartId), [rated.chartId, "missing"]);
  assert.equal(estimatedTierGroups(rows, "pumbility", "singles").flatMap(g => g.charts).length, 1);
});

test("Clearing and Co-op remain intact while absent historical targets are accurately Unrated", () => {
  const row = variantChart();
  assert.equal(projectScoringGoal(row, "sss", "clearing"), row);
  const coop = { ...row, type: "CoOp" as const };
  assert.equal(projectScoringGoal(coop, "sss"), coop);
  assert.equal(hasSssScoringVariants(fixture), false);
  assert.match(HISTORICAL_SSS_UNAVAILABLE, /remain Unrated/);
  const original = structuredClone(fixture.singles[0]);
  for (const goal of ["sss", "sssplus"] as const) {
    const projected = projectScoringGoal(fixture.singles[0], goal, "pumbility");
    assert.equal(projected.estimatedDifficulty, null);
    assert.equal(projected.scoringGoal, goal);
    assert.equal(projected.scoringTarget, goal === "sss" ? 990000 : 995000);
    assert.equal(projected.scoringUnratedReason, "target-variant-unavailable");
    assert.equal(projected.tierMetrics!.pumbility.estimatedDifficulty, null);
    assert.deepEqual(projected.tierMetrics!.clearing, original.tierMetrics!.clearing);
    assert.deepEqual(fixture.singles[0], original);
  }
  assert.equal(hasSssScoringVariants({ ...fixture, schemaVersion: 49 }), true);
  assert.equal(hasSssScoringVariants({ ...fixture, schemaVersion: 50 }), true);
});

test("boundary projection uses complete-board support and does not inherit a fit interval", () => {
  const row = variantChart();
  Object.assign(row.scoringVariants!.sss!, {
    scoringMethod: "sss-boundary-score-gap-v1", scoringEstimateBranch: "all-sss",
    evidenceStatus: "Provisional", difficultyCi95Low: null, difficultyCi95High: null,
    intervalUnavailableReason: "Second-pass rule has no formal interval",
    scoringRefinement: { version: 1, branch: "all-sss", range: null,
      fullBoard: { records: 300, successes: 300, belowTarget: 0, highestScore: 1000000, lowestScore: 990551, possiblyTruncated: true },
      modelEligible: { records: 230, successes: 230, belowTarget: 0 } },
  });
  Object.assign(row.pumbilityVariants!.sss!, { difficultyCi95Low: null, difficultyCi95High: null, evidenceStatus: "Provisional" });
  const projected = projectScoringGoal(row, "sss", "pumbility");
  assert.equal(isSssScoringChart(projected), true);
  assert.equal(projected.scoringEstimateBranch, "all-sss");
  assert.equal(projected.difficultyCi95Low, null);
  assert.equal(selectedTierMetric(projected, "pumbility").evidenceStatus, "Provisional");
  assert.match(tierSupportLabel(projected, "scoring"), /300 available best scores.*300 at SSS.*boundary estimate/);
  assert.equal(hasLimitedTierData(projected, "scoring"), true);
  assert.equal(hasLimitedTierData(projected, "pumbility"), true);
});

test("rare-success SSS projection preserves independent official Clearing provenance and support", () => {
  const row = variantChart();
  row.type = "Double";
  row.level = 26;
  Object.assign(row.scoringVariants!.sss!, { scoringMethod: "sss-rare-success-upper-band-v1", scoringEstimateBranch: "rare-sss",
    evidenceStatus: "Provisional", scoringTargetSuccesses: 1, scoringBelowTarget: 101, nContributors: 102 });
  Object.assign(row.tierMetrics!.clearing, {
    method: "official-d26-clearer-ability-v1", skillMetric: "official-clearer-ability",
    evidenceStatus: "Published", clearCount: 140, ratedClearCount: 80,
    clearingEvidence: { source: "piuscores-official", mix: "Phoenix2", asOf: "2026-10-11T17:25:07Z", rawRowCount: 140,
      maxRank: 140, possiblyTruncated: false, cutoffScore: 880000, status: "available", unavailableReason: null },
  });
  const projected = projectScoringGoal(row, "sss", "pumbility");
  assert.equal(isSssScoringChart(projected), true);
  assert.deepEqual(projected.tierMetrics!.clearing.clearingEvidence, row.tierMetrics!.clearing.clearingEvidence);
  assert.equal(projected.officialEvidence, undefined);
  assert.match(tierSupportLabel(projected, "pumbility"), /102 target-fit contributors.*1 at SSS.*80 eligible official clearers from 140 observed/);
  assert.match(tierSupportLabel(projected, "clearing"), /^80 eligible official clearers from 140 observed$/);
  assert.equal(hasLimitedTierData(projected, "scoring"), true);
  assert.equal(hasLimitedTierData(projected, "clearing"), false);
  projected.tierMetrics!.clearing.evidenceStatus = "Provisional";
  assert.equal(hasLimitedTierData(projected, "clearing"), true);
  assert.equal(hasLimitedTierData(row, "pumbility"), true);
});
