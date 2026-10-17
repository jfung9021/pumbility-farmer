import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { COMBINED_LOCAL_RESULTS_PATH, HYBRID_PUMBILITY_LOCAL_RESULTS_PATH, localCombinedResultsPath, SSS_LOCAL_RESULTS_PATH, validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { applyAveragePumbility } from "../lib/pumbility-experiment.ts";
import { recoverSssGeneralPayload, validateSssAnalysisPayload } from "../lib/sss-analysis-validation.ts";
import { FIT_FIELDS } from "../lib/tier-postprocessing.ts";
import type { AnalysisPayload, ChartResult, ScoringVariant } from "../lib/types";

const targetFixture = JSON.parse(await readFile(new URL("./fixtures/sss-target-variants.json", import.meta.url), "utf8")) as AnalysisPayload;
const hybridFixture = JSON.parse(await readFile(new URL("./fixtures/hybrid-midpoint-tier.json", import.meta.url), "utf8")) as AnalysisPayload;
const base = recoverSssGeneralPayload(targetFixture);

function validate(value: AnalysisPayload) {
  return validateSssAnalysisPayload(value, recovered => assert.deepEqual(recovered, base));
}
function unavailableOverlay(): AnalysisPayload {
  const p = applyAveragePumbility(hybridFixture);
  p.schemaVersion = 49;
  p.summary.method.sssScoring = structuredClone(targetFixture.summary.method.sssScoring);
  p.summary.method.sssPumbility = structuredClone(targetFixture.summary.method.sssPumbility);
  const method = p.summary.method.sssScoring as { calibration: Record<string, Record<string, unknown>>; diagnostics: Record<string, Record<string, {calibrationError: string | null}>> };
  for (const source of ["submitted", "official"]) for (const mode of ["Single", "Double"]) {
    method.calibration[source][mode] = null;
    method.diagnostics[source][mode].calibrationError = "Target calibration is unavailable.";
  }
  for (const key of ["singles", "doubles"] as const) for (const row of p[key]) {
    const fields = [...FIT_FIELDS, "initialFit", "evidenceStatus", "nContributors", "nPlayersScored", "scoringMethod", "scoringUnratedReason", "scoringExtrapolated", "scoringProvisional"];
    const general = Object.fromEntries(fields.filter(k => k in row).map(k => [k, (row as unknown as Record<string, unknown>)[k]])) as unknown as ScoringVariant;
    const sss = { ...structuredClone(targetFixture.singles[0].scoringVariants!.sss!),
      ...Object.fromEntries(FIT_FIELDS.map(k => [k, null])), initialFit: Object.fromEntries(FIT_FIELDS.map(k => [k, null])),
      evidenceStatus: "Unrated", nContributors: 0, nPlayersScored: 0, scoringTargetSuccesses: 0, scoringBelowTarget: 0,
      scoringSource: row.level >= 20 ? "official" : "submitted", scoringComponent: null, scoringMainComponent: false,
      scoringChartEffect: null, scoringChartEffectCi95Low: null, scoringChartEffectCi95High: null,
      scoringInformation: null, scoringPossiblyTruncated: false, scoringSourceStates: {}, scoringFitConverged: false,
      scoringBootstrapValidReplicates: 0, scoringProvisionalReasons: [], scoringProvisional: true, scoringExtrapolated: false,
      scoringUnratedReason: "No observed successful best scores.", scoringComparability: "no-evidence" } as ScoringVariant;
    const composite = { ...structuredClone(targetFixture.singles[0].pumbilityVariants!.sss!),
      ...Object.fromEntries(FIT_FIELDS.map(k => [k, null])), initialFit: Object.fromEntries(FIT_FIELDS.map(k => [k, null])),
      evidenceStatus: "Unrated" as const, scoringSupportCount: 0, clearingSupportCount: row.tierMetrics!.clearing.ratedClearCount,
      unratedReason: sss.scoringUnratedReason };
    row.scoringVariants = {general, sss};
    row.pumbilityVariants = {general: structuredClone(row.tierMetrics!.pumbility), sss: composite};
  }
  return p;
}

test("Python-produced aggregate validates independent target curves, transformation, intervals and ranks", () => {
  const before = structuredClone(targetFixture);
  assert.equal(validate(targetFixture), targetFixture);
  assert.deepEqual(targetFixture, before);
  assert.equal(recoverSssGeneralPayload(targetFixture).schemaVersion, 48);
  assert.deepEqual(targetFixture.coop, base.coop);
});

test("schema49 delegates its complete General compatibility view to the historical validator", () => {
  const payload = unavailableOverlay();
  validateLocalAnalysisPayload(payload, "combined");
  assert.deepEqual(recoverSssGeneralPayload(payload), applyAveragePumbility(hybridFixture));
  assert.equal(payload.singles[0].scoringVariants!.sss!.estimatedDifficulty, null);
  assert.equal(payload.singles[0].pumbilityVariants!.sss!.estimatedDifficulty, null);
});

test("rejects changed General mirrors, target policies, source boundaries and calibration", () => {
  const mutations: Array<(p: AnalysisPayload) => void> = [
    p => { p.singles[0].scoringVariants!.general.nContributors++; },
    p => { p.singles[0].pumbilityVariants!.general.estimatedDifficulty! += .1; },
    p => { (p.summary.method.sssScoring as Record<string, unknown>).target = 995000; },
    p => { (p.summary.method.sssScoring as Record<string, unknown>).basePayloadSha256 = "invalid"; },
    p => { (p.summary.method.sssScoring as Record<string, unknown>).sourceHashes = {submitted: "invalid"}; },
    p => { p.singles[0].scoringVariants!.sss!.scoringSource = "official"; },
    p => { const m = p.summary.method.sssScoring as {compression: {applications: number}}; m.compression.applications = 2; },
    p => { const m = p.summary.method.sssScoring as {calibration: {submitted: {Single: {minimumAnchorLevel: number}}}}; m.calibration.submitted.Single.minimumAnchorLevel = 20; },
    p => { const m = p.summary.method.sssScoring as {calibration: {official: {Single: {anchors: Array<{cost: number}>}}}}; m.calibration.official.Single.anchors[1].cost = -1; },
  ];
  for (const mutate of mutations) {
    const bad = structuredClone(targetFixture); mutate(bad);
    assert.throws(() => validate(bad));
  }
});

test("rejects invalid finite values, compression, target evidence and bootstrap support", () => {
  const mutations: Array<(r: ChartResult) => void> = [
    r => { r.scoringVariants!.sss!.estimatedDifficulty = NaN; },
    r => { r.scoringVariants!.sss!.difficultyCi95Low = Infinity; },
    r => { r.scoringVariants!.sss!.estimatedDifficulty! += .1; },
    r => { r.scoringVariants!.sss!.scoringTargetSuccesses = 0; },
    r => { r.scoringVariants!.sss!.scoringMainComponent = false; },
    r => { r.scoringVariants!.sss!.scoringFitConverged = false; },
    r => { r.scoringVariants!.sss!.scoringBootstrapValidReplicates = 1; },
    r => { r.scoringVariants!.sss!.scoringChartEffectCi95Low! += .1; },
    r => { r.scoringVariants!.sss!.scoringPossiblyTruncated = true; },
    r => { r.scoringVariants!.sss!.scoringProvisional = false; },
    r => { r.scoringVariants!.sss!.scoringExtrapolated = false; },
    r => { r.scoringVariants!.sss!.levelRank!++; },
    r => { r.scoringVariants!.sss!.initialFit!.modeRank = 20; },
  ];
  for (const mutate of mutations) {
    const bad = structuredClone(targetFixture); mutate(bad.singles[0]);
    assert.throws(() => validate(bad));
  }
});

test("rejects stale Pumbility component means, intervals, evidence, counts and ranks", () => {
  const mutations: Array<(r: ChartResult) => void> = [
    r => { r.pumbilityVariants!.sss!.estimatedDifficulty! += .1; },
    r => { r.pumbilityVariants!.sss!.difficultyCi95Low! += .1; },
    r => { r.pumbilityVariants!.sss!.initialFit!.estimatedDifficulty = 0; },
    r => { r.pumbilityVariants!.sss!.evidenceStatus = "Published"; },
    r => { r.pumbilityVariants!.sss!.scoringSupportCount++; },
    r => { r.pumbilityVariants!.sss!.clearingSupportCount++; },
    r => { r.pumbilityVariants!.sss!.levelRank!++; },
  ];
  for (const mutate of mutations) {
    const bad = structuredClone(targetFixture); mutate(bad.singles[0]);
    assert.throws(() => validate(bad));
  }
});

test("the original overlay is privacy-checked before stripping or reading the General view", () => {
  for (const secret of [{playerId: "private"}, {gameTag: "private"}, {note: "pst_live_0123456789abcdef"}]) {
    const bad = unavailableOverlay();
    Object.assign(bad.singles[0].scoringVariants!.sss!, secret);
    assert.throws(() => validateLocalAnalysisPayload(bad, "combined"), /private|credential/);
  }
});

test("SSS candidate selection needs local mode, overrides other experiments, and broken49 never becomes General", () => {
  assert.equal(localCombinedResultsPath({PIU_LOCAL_SCORING_VARIANT_EXPERIMENT: "sss"}), COMBINED_LOCAL_RESULTS_PATH);
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS: "1", PIU_LOCAL_PUMBILITY_EXPERIMENT: "hybrid"}), HYBRID_PUMBILITY_LOCAL_RESULTS_PATH);
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS: "1", PIU_LOCAL_PUMBILITY_EXPERIMENT: "hybrid", PIU_LOCAL_SCORING_VARIANT_EXPERIMENT: "sss"}), SSS_LOCAL_RESULTS_PATH);
  const broken = unavailableOverlay();
  delete broken.singles[0].scoringVariants;
  assert.throws(() => validateLocalAnalysisPayload(broken, "combined"), /SSS aggregate is incompatible/);
});

test("Published SSS accepts each cohort's known comparability labels and rejects changed or unknown labels", () => {
  for (const source of ["submitted", "official"] as const) {
    const payload = structuredClone(targetFixture);
    const row = payload.singles.find(r => r.scoringVariants!.sss!.scoringSource === source)!;
    payload.singles = [row]; payload.doubles = [];
    const frozenBase = recoverSssGeneralPayload(payload), level = row.level;
    const scoring = row.scoringVariants!.sss!, composite = row.pumbilityVariants!.sss!;
    const method = payload.summary.method.sssScoring as { calibration: Record<string, {Single: {anchors: Array<{cost: number}>}}> };
    const point = (metric: Record<string, unknown>, value: number) => {
      const delta = value - level - .5;
      const rank = delta < -.5 ? 1 : delta < -.3 ? 2 : delta < -.1 ? 3 : delta <= .1 ? 4 : delta <= .3 ? 5 : delta <= .5 ? 6 : 7;
      Object.assign(metric, {estimatedDifficulty: value, difficultyDelta: delta, effectBandRank: rank,
        effectBand: ["Overrated", "Very Easy", "Easy", "Medium", "Hard", "Very Hard", "Underrated"][rank - 1]});
    };
    point(scoring as unknown as Record<string, unknown>, level + .5);
    point(scoring.initialFit!, level + .5);
    Object.assign(scoring, {scoringChartEffect: method.calibration[source].Single.anchors[0].cost,
      scoringExtrapolated: false, scoringProvisional: false, scoringProvisionalReasons: [], evidenceStatus: "Published",
      modeRank: 1, levelRank: 1, levelComparisonCharts: 1, levelPercentile: .5, relativeGroupRank: 6, relativeGroup: "50–60% percentile"});
    Object.assign(scoring.initialFit!, {modeRank: 1, levelRank: 1, levelComparisonCharts: 1,
      levelPercentile: .5, relativeGroupRank: 6, relativeGroup: "50–60% percentile"});
    point(composite as unknown as Record<string, unknown>, (scoring.estimatedDifficulty! + row.tierMetrics!.clearing.estimatedDifficulty!) / 2);
    point(composite.initialFit!, (scoring.initialFit!.estimatedDifficulty as number + (row.tierMetrics!.clearing.initialFit!.estimatedDifficulty as number)) / 2);
    Object.assign(composite, {evidenceStatus: "Published", levelRank: 1, levelComparisonCharts: 1});
    Object.assign(composite.initialFit!, {levelRank: 1, levelComparisonCharts: 1});
    const verify = () => validateSssAnalysisPayload(payload, recovered => assert.deepEqual(recovered, frozenBase));
    for (const label of source === "official" ? ["same", "single-source-known"] : ["comparable"]) {
      scoring.scoringComparability = label;
      verify();
    }
    for (const label of ["unknown", "changed", "single-source-unknown", ...(source === "official" ? ["comparable"] : ["same"])]) {
      scoring.scoringComparability = label;
      assert.throws(verify, /Published SSS retains weak evidence/);
    }
  }
});
