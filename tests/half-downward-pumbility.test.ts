import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { validateCenteredMaximumPayload } from "../lib/centered-maximum-pumbility-validation.ts";
import { pumbilityReference, resolvePumbilityPolicy } from "../lib/pumbility-policy.ts";
import { SCORING_GOALS, projectScoringGoal, scoringGoalFromSearchParams, scoringGoalUrl } from "../lib/scoring-variants.ts";
import { chartEvidenceLines } from "../lib/tier-evidence-copy.ts";
import { tierBrowserPayload, tierChartDetails } from "../lib/tier-browser-payload.ts";
import { gradeForScore } from "../lib/score-appearance.ts";
import type { AnalysisPayload } from "../lib/types.ts";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/half-downward-pumbility.json", import.meta.url), "utf8"));
const candidate = fixture.candidate as AnalysisPayload;
const legacy = JSON.parse(readFileSync(new URL("./fixtures/approved-tier-ratings.json", import.meta.url), "utf8")).candidate as AnalysisPayload;

test("independent TypeScript calculation validates half downward and immutable full-offset generations", () => {
  assert.equal(validateCenteredMaximumPayload(candidate), candidate);
  assert.equal(validateCenteredMaximumPayload(legacy), legacy);
  assert.equal(resolvePumbilityPolicy(candidate)?.downwardAdjustmentScale, .5);
  assert.equal(resolvePumbilityPolicy(legacy)?.downwardAdjustmentScale, undefined);
  let negativeGroups = 0;
  for (const key of ["singles", "doubles"] as const) for (let i = 0; i < candidate[key].length; i++) {
    const row = candidate[key][i], old = fixture.base[key][i];
    assert.deepEqual(row.scoringVariants, old.scoringVariants);
    assert.deepEqual(row.tierMetrics!.clearing, old.tierMetrics.clearing);
    for (const goal of SCORING_GOALS) {
      const metric = row.pumbilityVariants![goal]!, marker = metric.centeredMaximumPumbility!;
      const scoring = row.scoringVariants![goal]!.estimatedDifficulty, clearing = row.tierMetrics!.clearing.estimatedDifficulty;
      assert.equal(marker.offset, marker.fullOffset! < 0 ? marker.fullOffset! * .5 : marker.fullOffset);
      assert.equal(metric.estimatedDifficulty, scoring === null || clearing === null ? null
        : pumbilityReference("centered-maximum", scoring, clearing, marker.offset));
      assert.equal(metric.evidenceStatus, old.pumbilityVariants[goal].evidenceStatus);
      assert.equal(metric.scoringSupportCount, old.pumbilityVariants[goal].scoringSupportCount);
      assert.equal(metric.clearingSupportCount, old.pumbilityVariants[goal].clearingSupportCount);
      if (marker.fullOffset! < 0) negativeGroups++;
    }
  }
  assert.ok(negativeGroups > 0, "fixture must exercise actual downward adjustments");
  assert.deepEqual(candidate.coop, fixture.base.coop);
});

test("browser policy and lazy chart notes retain the half adjustment without promising an exact midpoint", () => {
  const browser = tierBrowserPayload(candidate), policy = resolvePumbilityPolicy(browser)!;
  assert.equal(policy.downwardAdjustmentScale, .5);
  const full = candidate.singles.find(row => row.level === 24)!;
  const detail = tierChartDetails(candidate, full.chartId)!.chart;
  assert.equal("centeredMaximumPumbilityBaseline" in detail, false);
  const selected = projectScoringGoal(detail, "sssplus", "pumbility");
  assert.match(chartEvidenceLines(selected, "pumbility")[1], /half of any downward adjustment toward this official level's midpoint/);
  assert.doesNotMatch(chartEvidenceLines(selected, "pumbility")[1], /centers this official level around/);
  const compact = projectScoringGoal(browser.singles.find(row => row.chartId === full.chartId)!, "sssplus", "pumbility");
  assert.match(chartEvidenceLines(compact, "pumbility", {
    pumbilityAggregation: policy.aggregation, pumbilityDownwardAdjustmentScale: policy.downwardAdjustmentScale,
  })[1], /half of any downward adjustment/);
  assert.match(chartEvidenceLines(projectScoringGoal(legacy.singles[0], "sssplus", "pumbility"), "pumbility")[1], /centers this official level around/);
});

test("standalone SSS difficulty stays out of choices, URLs and public variants while actual SSS grades remain", () => {
  assert.deepEqual(SCORING_GOALS, ["general", "sssplus"]);
  assert.equal(scoringGoalFromSearchParams(new URLSearchParams("scoring=sss")), "sssplus");
  assert.equal(scoringGoalUrl("https://example.test/tier-list?scoring=sss", "sss").searchParams.get("scoring"), "sssplus");
  const historical = structuredClone(candidate);
  for (const row of [...historical.singles, ...historical.doubles]) {
    row.scoringVariants!.sss = structuredClone(row.scoringVariants!.sssplus!);
    row.pumbilityVariants!.sss = structuredClone(row.pumbilityVariants!.sssplus!);
  }
  const browser = tierBrowserPayload(historical);
  for (const row of [...browser.singles, ...browser.doubles]) {
    assert.deepEqual(Object.keys(row.scoringVariants!).sort(), ["general", "sssplus"]);
    assert.deepEqual(Object.keys(row.pumbilityVariants!).sort(), ["general", "sssplus"]);
    const detail = tierChartDetails(historical, row.chartId)!.chart;
    assert.deepEqual(Object.keys(detail.scoringVariants).sort(), ["general", "sssplus"]);
    assert.deepEqual(Object.keys(detail.pumbilityVariants).sort(), ["general", "sssplus"]);
  }
  assert.equal(gradeForScore(990000), "SSS");
});

for (const [name, mutate] of [
  ["point", (p: any) => p.singles[0].pumbilityVariants.sssplus.estimatedDifficulty += .1],
  ["applied offset", (p: any) => p.singles[0].pumbilityVariants.sssplus.centeredMaximumPumbility.offset -= .1],
  ["full offset", (p: any) => p.singles[0].pumbilityVariants.sssplus.centeredMaximumPumbility.fullOffset -= .1],
  ["folder offset", (p: any) => p.summary.method.centeredMaximumPumbility.folders.Single[24].sssplus.offset -= .1],
  ["population", (p: any) => p.summary.method.centeredMaximumPumbility.population = "published-only"],
  ["scale", (p: any) => p.summary.method.centeredMaximumPumbility.downwardAdjustmentScale = 1],
  ["upward scale", (p: any) => p.summary.method.centeredMaximumPumbility.upwardAdjustmentScale = .5],
  ["method", (p: any) => p.summary.method.centeredMaximumPumbility.method = "folder-centered-maximum-pumbility-v1"],
  ["interval", (p: any) => p.singles[0].pumbilityVariants.sssplus.difficultyCi95Low += .1],
  ["rank", (p: any) => p.singles[0].pumbilityVariants.sssplus.levelRank = 999],
] as const) test(`independent half-adjustment validation rejects changed ${name}`, () => {
  const altered = structuredClone(candidate);
  mutate(altered);
  assert.throws(() => validateCenteredMaximumPayload(altered));
});
