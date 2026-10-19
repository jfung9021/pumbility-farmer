import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { recoverScoringGoalsBaseline, validateScoringGoalsPayload } from "../lib/scoring-goals-validation.ts";
import { SCORING_TARGET_POLICIES, hasScoringGoalVariants } from "../lib/scoring-variants.ts";
import { compactTierPayload } from "../lib/tier-browser-payload.ts";
import { validateSubmittedSssplusCalibrationPayload } from "../lib/submitted-sssplus-calibration-validation.ts";
import { recoverSourceAwareBaseline, validateSourceAwarePayload } from "../lib/source-aware-validation.ts";
import type { AnalysisPayload } from "../lib/types.ts";

type Obj = Record<string, any>;
const historical = JSON.parse(readFileSync(new URL("./fixtures/sss-target-variants.json", import.meta.url), "utf8")) as AnalysisPayload;

/** Exercise the additive contract separately from the strict historical callback. */
function fixture(added: Array<"ss" | "ssplus"> = ["ss", "ssplus"]) {
  const base = structuredClone(historical);
  base.schemaVersion = 57;
  base.summary.scriptVersion = "preserved-test-generation";
  for (const row of [...base.singles, ...base.doubles]) {
    row.scoringVariants!.sssplus = { ...structuredClone(row.scoringVariants!.sss!), scoringTarget: 995000 };
    row.pumbilityVariants!.sssplus = structuredClone(row.pumbilityVariants!.sss!);
  }
  const candidate = structuredClone(base);
  candidate.schemaVersion = 58;
  candidate.summary.scriptVersion = "6.22.0-scoring-goals";
  const inputIdentity = { version: 1, syntheticInput: "fixed" };
  candidate.summary.method.scoringGoalPolicies = { version: 1, goals: structuredClone([...SCORING_TARGET_POLICIES]) };
  candidate.summary.method.availableScoringGoals = ["general", ...added, "sss", "sssplus"];
  candidate.summary.method.scoringGoalBackfills = { version: 1, method: "independent-target-goal-overlays-v1", baseSchemaVersion: 57,
    basePayloadSha256: "a".repeat(64), baseScriptVersion: base.summary.scriptVersion, inputIdentity,
    goals: Object.fromEntries(added.map(goal => {
      const targetConfig = SCORING_TARGET_POLICIES.find(p => p.key === goal)!;
      const method = { version: 1, method: "target-median-anchor-refinement-centering-tail-v1", target: targetConfig.score,
        targetConfig, bootstrapSamples: 200, minimumPlayerCharts: 4, minimumContributors: 5,
        minimumReferenceContributors: 20, minimumReferencePerOutcome: 5, minimumReferenceCharts: 5,
        scoreFetches: 0, recommendationFits: 0,
        compressionApplications: 1, sourceBoundary: { submitted: [16, 19], officialMinimum: 20 },
        fitReferences: ["official", "submitted"].flatMap(source => ["Single", "Double"].map(mode => ({
          goal, source, mode, identity: { target: targetConfig.score, samples: 200, boards: "c".repeat(64), sourceBoards: "d".repeat(64) },
        }))) };
      return [goal, { targetConfig, artifactSha256: "b".repeat(64), inputIdentity, method,
        artifact: { schemaVersion: 1, goal, targetConfig, generatedAtUtc: "2026-10-20T00:00:00Z", inputIdentity,
          method, sourceHashes: { phoenix1: "e".repeat(64), phoenix2: "f".repeat(64), manifest: "1".repeat(64), sourcePolicy: "2".repeat(64) } } }];
    })) };
  for (const row of [...candidate.singles, ...candidate.doubles]) for (const goal of added) {
    const scoring = structuredClone(row.scoringVariants!.sss!);
    scoring.scoringTarget = goal === "ss" ? 980000 : 985000;
    scoring.scoringMethod = "target-attainment-rasch-v1";
    row.scoringVariants![goal] = scoring;
    row.pumbilityVariants![goal] = structuredClone(row.pumbilityVariants!.sss!);
  }
  return { base, candidate };
}

test("expanded contract validates each advertised goal and delegates exact historical content", () => {
  for (const added of [["ss"], ["ssplus"], ["ss", "ssplus"]] as Array<Array<"ss" | "ssplus">>) {
    const { base, candidate } = fixture(added), frozen = structuredClone(candidate);
    let called = 0;
    assert.equal(validateScoringGoalsPayload(candidate, recovered => {
      called++;
      assert.deepEqual(recovered, base);
    }), candidate);
    assert.equal(called, 1);
    assert.deepEqual(recoverScoringGoalsBaseline(candidate), base);
    assert.deepEqual(candidate, frozen);
    assert.equal(hasScoringGoalVariants(candidate, "ss"), added.includes("ss"));
    assert.equal(hasScoringGoalVariants(candidate, "ssplus"), added.includes("ssplus"));
  }
});

test("expanded validation rejects stale policies, partial variants, mixed sources and mismatched composites", () => {
  const { candidate } = fixture();
  const mutations: Array<(p: AnalysisPayload) => void> = [
    p => { p.summary.method.scoringGoalPolicies!.goals[0].score = 985000; },
    p => { p.summary.method.availableScoringGoals = ["general", "sss", "sssplus"]; },
    p => { delete p.singles[0].scoringVariants!.ss; },
    p => { delete p.doubles[0].pumbilityVariants!.ssplus; },
    p => { p.singles[0].scoringVariants!.ss!.scoringTarget = 995000; },
    p => { p.singles[0].scoringVariants!.ss!.scoringSource = "official"; },
    p => { p.singles[0].scoringVariants!.ss!.estimatedDifficulty = Number.NaN; },
    p => { p.singles[0].scoringVariants!.ss!.nContributors = -1; },
    p => { p.singles[0].pumbilityVariants!.ss!.estimatedDifficulty! += .1; },
    p => { p.singles[0].pumbilityVariants!.ss!.scoringSupportCount++; },
    p => { p.singles[0].scoringVariants!.ss!.levelRank = 900; },
    p => { (p.summary.method.scoringGoalBackfills as Obj).goals.ss.inputIdentity = { changed: true }; },
    p => { (p.summary.method.scoringGoalBackfills as Obj).goals.ss.targetConfig.score = 985000; },
    p => { (p.summary.method.scoringGoalBackfills as Obj).goals.ss.artifact.goal = "sss"; },
    p => { (p.summary.method.scoringGoalBackfills as Obj).goals.ss.artifact.inputIdentity = {}; },
    p => { (p.summary.method.scoringGoalBackfills as Obj).goals.ss.method.fitReferences.pop(); },
  ];
  for (const mutate of mutations) {
    const changed = structuredClone(candidate);
    mutate(changed);
    assert.throws(() => validateScoringGoalsPayload(changed, () => assert.fail("invalid overlay reached historical validation")));
  }
});

test("strict historical validator failures cannot be hidden by an expanded overlay", () => {
  const { candidate } = fixture();
  assert.throws(() => validateScoringGoalsPayload(candidate, () => { throw new Error("historical failure"); }), /historical failure/);
});

test("compact expanded payload retains policy and availability without private goal diagnostics", () => {
  const { candidate } = fixture();
  const compact = compactTierPayload(candidate);
  assert.deepEqual(compact.summary.method.scoringGoalPolicies, candidate.summary.method.scoringGoalPolicies);
  assert.deepEqual(compact.summary.method.availableScoringGoals, candidate.summary.method.availableScoringGoals);
  assert.equal(compact.summary.method.scoringGoalBackfills, undefined);
  for (const goal of ["ss", "ssplus"] as const) {
    assert.equal(compact.singles[0].scoringVariants![goal]!.estimatedDifficulty, candidate.singles[0].scoringVariants![goal]!.estimatedDifficulty);
    assert.equal(compact.singles[0].pumbilityVariants![goal]!.estimatedDifficulty, candidate.singles[0].pumbilityVariants![goal]!.estimatedDifficulty);
  }
});

function pythonFixture() {
  const sourceAware = JSON.parse(readFileSync(new URL("./fixtures/source-aware-width.json", import.meta.url), "utf8")).candidate as AnalysisPayload;
  const submitted = JSON.parse(readFileSync(new URL("./fixtures/submitted-sssplus-calibration.json", import.meta.url), "utf8"));
  const additions = JSON.parse(readFileSync(new URL("./fixtures/scoring-goal-overlays.json", import.meta.url), "utf8"));
  const base = structuredClone(sourceAware);
  base.schemaVersion = 57;
  base.summary.method.submittedSssplusCalibration = submitted.method;
  for (const saved of submitted.doubles) {
    const row = base.doubles.find(row => row.chartId === saved.chartId)!;
    row.scoringVariants!.sssplus = saved.scoring;
    row.pumbilityVariants!.sssplus = saved.pumbility;
    if (saved.recovery) row.submittedSssplusCalibrationBaseline = saved.recovery;
  }
  const candidate = structuredClone(base);
  candidate.schemaVersion = 58;
  Object.assign(candidate.summary.method, additions.method);
  for (const mode of ["singles", "doubles"] as const) for (const added of additions[mode]) {
    const row = candidate[mode].find(row => row.chartId === added.chartId)!;
    Object.assign(row.scoringVariants!, added.scoringVariants);
    Object.assign(row.pumbilityVariants!, added.pumbilityVariants);
  }
  const predecessor = recoverSourceAwareBaseline(sourceAware);
  const strict57 = (recovered: AnalysisPayload) => {
    assert.deepEqual(recovered, base);
    validateSubmittedSssplusCalibrationPayload(recovered, restored56 => {
      assert.deepEqual(restored56, sourceAware);
      validateSourceAwarePayload(restored56, previous => assert.deepEqual(previous, predecessor));
    });
  };
  return { base, candidate, strict57 };
}

test("Python-built target overlays pass TS validation and preserve the strict schema57 predecessor", () => {
  const { base, candidate, strict57 } = pythonFixture();
  const frozen = structuredClone(candidate);
  assert.equal(validateScoringGoalsPayload(candidate, strict57), candidate);
  assert.deepEqual(recoverScoringGoalsBaseline(candidate), base);
  assert.deepEqual(candidate, frozen);
  assert.equal(hasScoringGoalVariants(candidate, "ss"), true);
  assert.equal(hasScoringGoalVariants(candidate, "ssplus"), true);
});

test("Python-built overlays reject selected-goal evidence and availability tampering", () => {
  const { candidate, strict57 } = pythonFixture();
  for (const mutate of [
    (p: AnalysisPayload) => { p.singles[0].scoringVariants!.ss!.scoringTargetSuccesses!++; },
    (p: AnalysisPayload) => { p.doubles[0].pumbilityVariants!.ssplus!.estimatedDifficulty! += .25; },
    (p: AnalysisPayload) => { p.summary.method.availableScoringGoals = ["general", "sss", "sssplus"]; },
  ]) {
    const changed = structuredClone(candidate);
    mutate(changed);
    assert.throws(() => validateScoringGoalsPayload(changed, strict57));
  }
});
