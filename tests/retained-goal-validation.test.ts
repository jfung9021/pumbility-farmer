import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { compactTierPayload, tierChartDetails } from "../lib/tier-browser-payload.ts";
import { validateRetainedAnalysisPayload, validateLevel20WidthPublicPayload } from "../lib/level20-width-validation.ts";
import { validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { hasScoringGoalVariants, projectScoringGoal } from "../lib/scoring-variants.ts";
import type { AnalysisPayload } from "../lib/types";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/retained-goal-tiers.json", import.meta.url), "utf8"));
const load = (key: "full" | "compact") => structuredClone(fixture[key]) as AnalysisPayload;

test("retained producer validates full and compact data and keeps lazy details consistent", () => {
  const full = load("full"), frozen = structuredClone(full);
  assert.equal(validateRetainedAnalysisPayload(full), full);
  assert.equal(validateLocalAnalysisPayload(full, "combined"), full);
  const compact = compactTierPayload(full);
  const expected = load("compact");
  // Python's stored list has its own envelope hash; JS keeps its source hash.
  (expected.summary.method.productionTierPublication as any).publicPayloadSha256 =
    (compact.summary.method.productionTierPublication as any).publicPayloadSha256;
  assert.deepEqual(compact, expected);
  assert.equal(validateLevel20WidthPublicPayload(compact), compact);
  for (const row of [...compact.singles, ...compact.doubles]) {
    const detail = tierChartDetails(full, row.chartId)!.chart;
    for (const goal of ["general", "sssplus"] as const) {
      assert.equal(hasScoringGoalVariants(compact, goal), true);
      assert.equal(projectScoringGoal(row, goal).estimatedDifficulty, projectScoringGoal(detail, goal).estimatedDifficulty);
      assert.equal(projectScoringGoal(row, goal).tierMetrics!.pumbility.estimatedDifficulty,
        projectScoringGoal(detail, goal).tierMetrics!.pumbility.estimatedDifficulty);
    }
    assert.deepEqual(Object.keys(detail.scoringVariants).sort(), ["general", "sssplus"]);
  }
  assert.deepEqual(full, frozen);
});

test("retained payload rejects changed producer, support, means, uncertainty, goals and width policy", () => {
  const edits: Array<(p: any) => void> = [
    p => { p.summary.method.retainedGoalProducer.targetBootstrapSamples = 20; },
    p => { p.summary.method.retainedGoalProducer.retiredTargetFits = 4; },
    p => { p.summary.method.availableScoringGoals.push("sss"); },
    p => { p.singles[0].scoringVariants.sss = p.singles[0].scoringVariants.sssplus; },
    p => { p.singles[0].scoringVariants.sssplus.nContributors++; },
    p => { p.singles[0].pumbilityVariants.sssplus.estimatedDifficulty += .1; },
    p => { p.singles[0].tierMetrics.pumbility.estimatedDifficulty += .1; },
    p => { p.singles[0].scoringVariants.sssplus.difficultyDeltaCi95High += .1; },
    p => { p.summary.method.level20ScoringWidth.policy.goals.push("sss"); },
  ];
  for (const key of ["full", "compact"] as const) for (const edit of edits) {
    const changed = load(key); edit(changed);
    assert.throws(() => key === "full" ? validateRetainedAnalysisPayload(changed) : validateLevel20WidthPublicPayload(changed));
  }
});
