import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { compactTierPayload, tierChartDetails } from "../lib/tier-browser-payload.ts";
import { hasMidpointCompression } from "../lib/tier-postprocessing.ts";
import { resolvePumbilityPolicy } from "../lib/pumbility-policy.ts";
import { validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { hasScoringGoalVariants, projectScoringGoal, scoringGoalFromSearchParams, scoringTargetForPayload } from "../lib/scoring-variants.ts";
import { hasLimitedTierData } from "../lib/tier-metrics.ts";
import { validateScoringGoalsPayload } from "../lib/scoring-goals-validation.ts";
import { validateSubmittedSssplusCalibrationPayload } from "../lib/submitted-sssplus-calibration-validation.ts";
import { recoverSourceAwareBaseline, validateSourceAwarePayload } from "../lib/source-aware-validation.ts";
import { recoverSssTailBaseline, validateSssTailAnalysisPayload, validateSssTailPublicPayload } from "../lib/sss-tail-validation.ts";
import type { AnalysisPayload, ScoringVariant } from "../lib/types";

type Obj = Record<string, any>;
function load(name: string) { return JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url), "utf8")); }
const sourceAware = load("source-aware-width.json").candidate as AnalysisPayload;
const submitted = load("submitted-sssplus-calibration.json"), additions = load("scoring-goal-overlays.json"), tail = load("sss-tail-policy.json");

function predecessors() {
  const base = structuredClone(sourceAware);
  base.schemaVersion = 57;
  base.summary.method.submittedSssplusCalibration = submitted.method;
  for (const saved of submitted.doubles) {
    const row = base.doubles.find(row => row.chartId === saved.chartId)!;
    row.scoringVariants!.sssplus = saved.scoring;
    row.pumbilityVariants!.sssplus = saved.pumbility;
    if (saved.recovery) row.submittedSssplusCalibrationBaseline = saved.recovery;
  }
  const expanded = structuredClone(base);
  expanded.schemaVersion = 58;
  Object.assign(expanded.summary.method, additions.method);
  for (const key of ["singles", "doubles"] as const) for (const saved of additions[key]) {
    const row = expanded[key].find(row => row.chartId === saved.chartId)!;
    Object.assign(row.scoringVariants!, saved.scoringVariants);
    Object.assign(row.pumbilityVariants!, saved.pumbilityVariants);
  }
  return { base, expanded };
}
function fixture(schema: 57 | 58 = 58) {
  const predecessorsBySchema = predecessors(), base = schema === 57 ? predecessorsBySchema.base : predecessorsBySchema.expanded;
  const candidate = structuredClone(base), overlay = tail[String(schema)];
  candidate.schemaVersion = 59;
  candidate.summary.scriptVersion = overlay.scriptVersion;
  candidate.summary.method.sssTailPolicy = overlay.method;
  for (const key of ["singles", "doubles"] as const) for (const saved of overlay[key]) {
    const row = candidate[key].find(row => row.chartId === saved.chartId)!;
    row.scoringVariants!.sss = saved.scoring;
    row.pumbilityVariants!.sss = saved.pumbility;
    row.sssTailBaseline = saved.recovery;
  }
  const strict57 = (p: AnalysisPayload) => {
    assert.deepEqual(p, predecessorsBySchema.base);
    validateSubmittedSssplusCalibrationPayload(p, prior => {
      assert.deepEqual(prior, sourceAware);
      validateSourceAwarePayload(prior, earlier => assert.deepEqual(earlier, recoverSourceAwareBaseline(sourceAware)));
    });
  };
  const validateBase = (p: AnalysisPayload) => {
    assert.deepEqual(p, base);
    return schema === 57 ? strict57(p) : validateScoringGoalsPayload(p, strict57);
  };
  return { base, candidate, validateBase };
}

test("Python SSS tail overlays replay and exactly restore both schema57 and58 predecessors", () => {
  for (const schema of [57, 58] as const) {
    const { base, candidate, validateBase } = fixture(schema), frozen = structuredClone(candidate);
    assert.equal(validateSssTailAnalysisPayload(candidate, validateBase), candidate);
    assert.deepEqual(recoverSssTailBaseline(candidate), base);
    assert.deepEqual(candidate, frozen);
    assert.equal(hasMidpointCompression(candidate), hasMidpointCompression(base));
    assert.deepEqual(resolvePumbilityPolicy(candidate), resolvePumbilityPolicy(base));
    assert.equal(scoringTargetForPayload(candidate).goal, "sssplus");
    assert.equal(scoringGoalFromSearchParams(new URLSearchParams(), candidate), "sssplus");
    for (const goal of ["general", "sss", "sssplus"] as const) assert.equal(hasScoringGoalVariants(candidate, goal), true);
    assert.equal(hasScoringGoalVariants(candidate, "ss"), schema === 58);
    assert.equal(hasScoringGoalVariants(candidate, "ssplus"), schema === 58);
  }
});

test("SSS tail rejects changed points, intervals, selection, evidence, ranks and protected predecessors", () => {
  const { candidate, validateBase } = fixture();
  const edits: Array<(p: AnalysisPayload) => void> = [
    p => { p.singles[0].scoringVariants!.sss!.estimatedDifficulty! += .1; },
    p => { p.singles[0].scoringVariants!.sss!.difficultyCi95Low! += .1; },
    p => { p.singles[0].pumbilityVariants!.sss!.estimatedDifficulty! += .1; },
    p => { p.singles[0].scoringVariants!.sss!.levelRank = 999; },
    p => { p.singles[0].scoringVariants!.sss!.scoringTailPolicy!.exceptionSelected = true; },
    p => { p.singles[0].scoringVariants!.sss!.scoringTailPolicy!.rejectionReasons = []; },
    p => { p.singles[0].sssTailBaseline!.scoring.initialFit!.estimatedDifficulty = 999; },
    p => { p.singles[0].scoringVariants!.general.nContributors++; },
    p => { p.singles[0].scoringVariants!.ss!.nContributors++; },
    p => { (p.summary.method.sssTailPolicy!.tailPolicy as Obj).selectedChartIds = [p.singles[0].chartId]; },
    p => { (p.summary.method.sssTailPolicy!.tailPolicy as Obj).exceptionLimit = 11; },
    p => { p.summary.method.sssTailPolicy!.baseSchemaVersion = 57; },
  ];
  for (const edit of edits) {
    const changed = structuredClone(candidate); edit(changed);
    assert.throws(() => validateSssTailAnalysisPayload(changed, validateBase));
  }
  assert.throws(() => validateSssTailAnalysisPayload(candidate, () => { throw new Error("historical failure"); }), /historical failure/);
});

test("historical SSS tail lists replay while lazy details retain only current public goals", () => {
  for (const schema of [57, 58] as const) {
    const { candidate } = fixture(schema), compact = compactTierPayload(candidate);
    assert.equal(validateSssTailPublicPayload(compact), compact);
    assert.deepEqual(compact.summary.method.sssTailPolicy, candidate.summary.method.sssTailPolicy);
    for (const key of ["singles", "doubles"] as const) for (const row of compact[key]) {
      const details = tierChartDetails(candidate, row.chartId)!.chart;
      assert.equal(details.sssTailBaseline, undefined);
      assert.equal(row.sssTailBaseline, undefined);
      assert.deepEqual(Object.keys(row.scoringVariants!.sss!.scoringTailPolicy!).sort(),
        ["preTailDifficulty", "preTailDifficultyCi95Low", "preTailDifficultyCi95High"].sort());
      const historical = candidate[key].find(chart => chart.chartId === row.chartId)!;
      assert.equal(row.scoringVariants!.sss!.estimatedDifficulty, historical.scoringVariants!.sss!.estimatedDifficulty);
      assert.equal(row.pumbilityVariants!.sss!.estimatedDifficulty, historical.pumbilityVariants!.sss!.estimatedDifficulty);
      assert.equal(row.scoringVariants!.sss!.difficultyCi95Low, historical.scoringVariants!.sss!.difficultyCi95Low);
      assert.equal(row.scoringVariants!.sss!.difficultyCi95High, historical.scoringVariants!.sss!.difficultyCi95High);
      assert.equal(details.scoringVariants.sss, undefined);
      assert.equal(details.pumbilityVariants.sss, undefined);
      assert.equal(row.scoringVariants!.sssplus!.estimatedDifficulty, details.scoringVariants.sssplus.estimatedDifficulty);
      assert.equal(row.pumbilityVariants!.sssplus!.estimatedDifficulty, details.pumbilityVariants.sssplus.estimatedDifficulty);
    }
    for (const edit of [
      (p: AnalysisPayload) => { p.singles[0].scoringVariants!.sss!.estimatedDifficulty! += .1; },
      (p: AnalysisPayload) => { p.singles[0].scoringVariants!.sss!.difficultyCi95Low! += .1; },
      (p: AnalysisPayload) => { p.singles[0].scoringVariants!.sss!.levelRank = 999; },
      (p: AnalysisPayload) => { p.singles[0].pumbilityVariants!.sss!.levelRank = 999; },
      (p: AnalysisPayload) => { (p.summary.method.sssTailPolicy!.tailPolicy as Obj).counts.changedCharts++; },
    ]) {
      const changed = structuredClone(compact); edit(changed);
      assert.throws(() => validateSssTailPublicPayload(changed));
    }
  }
});

test("SSS tail projection preserves selected-goal warnings at nine and ten valid records", () => {
  const { candidate } = fixture();
  for (const count of [9, 10]) {
    const row = structuredClone(candidate.singles[0]);
    Object.assign(row.scoringVariants!.sss!, { nContributors: count, scoringMethod: "sss-target-rasch-v1" });
    row.tierMetrics!.clearing.ratedClearCount = 10;
    row.tierMetrics!.clearing.selectedCount = 10;
    row.pumbilityVariants!.sss!.scoringSupportCount = count;
    row.pumbilityVariants!.sss!.clearingSupportCount = 10;
    const projected = projectScoringGoal(row, "sss");
    assert.equal(hasLimitedTierData(projected, "scoring"), count === 9);
    assert.equal(hasLimitedTierData(projected, "pumbility"), count === 9);
    assert.equal(projectScoringGoal(row, "sss", "clearing"), row);
    const other = projectScoringGoal(row, "sssplus");
    assert.equal(other.scoringTarget, 995000);
  }
});

test("schema59 privacy validation checks tail baselines before recovering historical data", () => {
  const { candidate } = fixture();
  Object.assign(candidate.singles[0].sssTailBaseline!.scoring as ScoringVariant & Obj, { gameTag: "private" });
  assert.throws(() => validateLocalAnalysisPayload(candidate, "combined"), /private/);
});
