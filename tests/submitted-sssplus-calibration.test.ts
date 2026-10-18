import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { localCombinedResultsPath, SUBMITTED_SSSPLUS_CALIBRATION_LOCAL_RESULTS_PATH, validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { recoverSubmittedSssplusCalibrationBaseline, submittedSssplusInitialRating, validateSubmittedSssplusCalibrationPayload } from "../lib/submitted-sssplus-calibration-validation.ts";
import { recoverSourceAwareBaseline, validateSourceAwarePayload } from "../lib/source-aware-validation.ts";
import { resolvePumbilityPolicy } from "../lib/pumbility-policy.ts";
import { hasMidpointCompression } from "../lib/tier-postprocessing.ts";
import { applyAveragePumbility } from "../lib/pumbility-experiment.ts";
import { projectScoringGoal, scoringGoalFromSearchParams, scoringTargetForPayload } from "../lib/scoring-variants.ts";
import type { AnalysisPayload } from "../lib/types.ts";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/submitted-sssplus-calibration.json", import.meta.url), "utf8"));
const base = JSON.parse(readFileSync(new URL("./fixtures/source-aware-width.json", import.meta.url), "utf8")).candidate as AnalysisPayload;
// Python-generated target components share the existing immutable schema-56 fixture.
const candidate = structuredClone(base);
candidate.schemaVersion = 57;
candidate.summary.method.submittedSssplusCalibration = fixture.method;
for (const saved of fixture.doubles) {
  const row = candidate.doubles.find(r => r.chartId === saved.chartId)!;
  row.scoringVariants!.sssplus = saved.scoring;
  row.pumbilityVariants!.sssplus = saved.pumbility;
  if (saved.recovery) row.submittedSssplusCalibrationBaseline = saved.recovery;
}
const predecessor = recoverSourceAwareBaseline(base);
const validate = (p: AnalysisPayload) => validateSubmittedSssplusCalibrationPayload(p, recovered => {
  assert.deepEqual(recovered, base);
  validateSourceAwarePayload(recovered, previous => assert.deepEqual(previous, predecessor));
});
const policy = (p: AnalysisPayload) => p.summary.method.submittedSssplusCalibration as Record<string, any>;
const restored = (p: AnalysisPayload) => p.doubles.find(r => r.scoringVariants?.sssplus?.scoringSubmittedCalibration)!;

test("submitted calibration independently replays each stage and recovers the exact schema-56 predecessor", () => {
  assert.deepEqual(recoverSubmittedSssplusCalibrationBaseline(candidate), base);
  assert.equal(validate(candidate), candidate);
  assert.equal(policy(candidate).modelRefits, 0);
  assert.equal(policy(candidate).dataFetches, 0);
});

test("schema57 recognizes the preserved average and compression policies from a valid schema48 baseline", () => {
  const hybrid = JSON.parse(readFileSync(new URL("./fixtures/hybrid-midpoint-tier.json", import.meta.url), "utf8")) as AnalysisPayload;
  const average = applyAveragePumbility(hybrid);
  validateLocalAnalysisPayload(average, "combined");
  const projected = { ...average, schemaVersion: 57, summary: { ...average.summary, method: { ...average.summary.method,
    sssScoring: { version: 1, baseSchemaVersion: 48 } } } };
  assert.deepEqual(resolvePumbilityPolicy(projected), { aggregation: "average", requiresRowMarker: true });
  assert.equal(hasMidpointCompression(projected), true);
});

test("folder-relative map centers independently of reversed cross-folder effects", () => {
  assert.equal(submittedSssplusInitialRating(3, 16, 3, .7), 16.5);
  assert.equal(submittedSssplusInitialRating(2, 18, 2, .7), 18.5);
  assert.equal(submittedSssplusInitialRating(null, 18, 2, .7), null);
  assert.ok(submittedSssplusInitialRating(2.1, 18, 2, .7)! > 18.5);
  assert.throws(() => submittedSssplusInitialRating(2, 18, 2, 0), /scale/);
  assert.throws(() => submittedSssplusInitialRating(2, 18, 2, -1), /scale/);
});

test("submitted chart details receive a numeric official midpoint for the spread policy", () => {
  for (const row of candidate.doubles.filter(r => r.scoringVariants?.sssplus?.scoringSubmittedCalibration)) {
    const spread = row.scoringVariants!.sssplus!.scoringSpreadPolicy!;
    assert.equal(spread.midpoint, row.level + .5);
    assert.equal(spread.midpoint.toFixed(1), `${row.level}.5`);
  }
});

test("the same replay contract accepts a production calibration scope", () => {
  const production = structuredClone(candidate);
  policy(production).localOnly = false;
  assert.equal(validate(production), production);
});

test("schema57 remains local, defaults to SSS+ and projects restored submitted components", () => {
  const env = { PIU_LOCAL_ANALYSIS: "1", PIU_LOCAL_SCORING_VARIANT_EXPERIMENT: "submitted-sssplus-calibration" };
  assert.equal(localCombinedResultsPath(env), SUBMITTED_SSSPLUS_CALIBRATION_LOCAL_RESULTS_PATH);
  assert.notEqual(localCombinedResultsPath({ ...env, PIU_LOCAL_ANALYSIS: undefined }), SUBMITTED_SSSPLUS_CALIBRATION_LOCAL_RESULTS_PATH);
  assert.equal(scoringTargetForPayload(candidate).goal, "sssplus");
  assert.equal(scoringGoalFromSearchParams(new URLSearchParams(), candidate), "sssplus");
  assert.equal(scoringGoalFromSearchParams(new URLSearchParams("scoring=sss"), candidate), "sss");
  const row = restored(candidate), projected = projectScoringGoal(row, "sssplus", "pumbility");
  assert.ok(Number.isFinite(projected.estimatedDifficulty));
  assert.equal(projected.scoringSubmittedCalibration?.method, "submitted-folder-centered-shared-q90-v1");
  assert.deepEqual(projected.tierMetrics?.clearing, row.tierMetrics?.clearing);
  assert.equal(projected.tierMetrics?.pumbility.estimatedDifficulty,
    (row.scoringVariants!.sssplus!.estimatedDifficulty! + row.tierMetrics!.clearing.estimatedDifficulty!) / 2);
});

for (const [name, mutate] of [
  ["target", (p: AnalysisPayload) => { policy(p).policy.scoreTarget = 990000; }],
  ["shared scale", (p: AnalysisPayload) => { policy(p).sharedScale += .1; }],
  ["folder center", (p: AnalysisPayload) => { Object.values<any>(policy(p).folders)[0].centerEffect += .1; }],
  ["folder population", (p: AnalysisPayload) => { Object.values<any>(policy(p).folders)[0].populationChartIds.pop(); }],
  ["fit identity", (p: AnalysisPayload) => { policy(p).fitIdentity.target = 990000; }],
  ["cached effect", (p: AnalysisPayload) => { policy(p).chartInputs[restored(p).chartId].effect += .1; }],
  ["double compression", (p: AnalysisPayload) => { policy(p).policy.compressionApplications = 2; }],
  ["stage order", (p: AnalysisPayload) => { policy(p).stageOrder.reverse(); }],
  ["refit claim", (p: AnalysisPayload) => { policy(p).modelRefits = 1; }],
  ["point", (p: AnalysisPayload) => { restored(p).scoringVariants!.sssplus!.estimatedDifficulty! += .1; }],
  ["interval", (p: AnalysisPayload) => { restored(p).scoringVariants!.sssplus!.difficultyCi95Low! += .1; }],
  ["width donor", (p: AnalysisPayload) => { restored(p).scoringVariants!.sssplus!.finalFolderWidth!.parameters.lowerReference! += .1; }],
  ["missing spread midpoint", (p: AnalysisPayload) => { Reflect.deleteProperty(restored(p).scoringVariants!.sssplus!.scoringSpreadPolicy!, "midpoint"); }],
  ["incorrect spread midpoint", (p: AnalysisPayload) => { restored(p).scoringVariants!.sssplus!.scoringSpreadPolicy!.midpoint += 1; }],
  ["tail exception", (p: AnalysisPayload) => { restored(p).scoringVariants!.sssplus!.scoringTailPolicy!.exceptionSelected = true; }],
  ["evidence promotion", (p: AnalysisPayload) => { restored(p).scoringVariants!.sssplus!.evidenceStatus = "Published"; }],
  ["Pumbility mean", (p: AnalysisPayload) => { restored(p).pumbilityVariants!.sssplus!.estimatedDifficulty! += .1; }],
  ["protected General", (p: AnalysisPayload) => { restored(p).estimatedDifficulty! += .1; }],
  ["protected Clearing", (p: AnalysisPayload) => { restored(p).tierMetrics!.clearing.estimatedDifficulty! += .1; }],
] as const) test(`submitted calibration rejects altered ${name}`, () => {
  const changed = structuredClone(candidate);
  mutate(changed);
  assert.throws(() => validate(changed));
});

test("the local reader rejects private fields before recovering an earlier schema", () => {
  const changed = structuredClone(candidate);
  Object.assign(restored(changed).scoringVariants!.sssplus!, { gameTag: "private" });
  assert.throws(() => validateLocalAnalysisPayload(changed, "combined"), /private/);
});
