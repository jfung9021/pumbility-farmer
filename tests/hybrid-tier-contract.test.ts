import assert from "node:assert/strict";
import test from "node:test";
import { validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { resolvePumbilityPolicy } from "../lib/pumbility-policy.ts";
import { hasMidpointCompression, recoverInitialFitPayload } from "../lib/tier-postprocessing.ts";
import type { AnalysisPayload } from "../lib/types.ts";
import { hybridTierFixture } from "./hybrid-tier-fixture.ts";

test("hybrid46 retains submitted lower Scoring and uses two declared Clearing paths", () => {
  const p = validateLocalAnalysisPayload(hybridTierFixture(false), "combined");
  assert.equal(resolvePumbilityPolicy(p)?.aggregation, "maximum");
  const lower = p.singles[0], upper = p.singles[5];
  assert.equal(lower.scoringMethod, "submitted-score-profile-v2");
  assert.equal(lower.tierMetrics!.clearing.method, "submitted-folder-centered-clearing-v1");
  assert.equal(lower.tierMetrics!.clearing.estimatedDifficulty, 19.36);
  assert.equal(upper.scoringMethod, "official-cross-level-v1");
  assert.equal(upper.estimatedDifficulty, null);
  assert.equal(upper.tierMetrics!.pumbility.estimatedDifficulty, null);
  assert.equal(upper.tierMetrics!.clearing.method, "submitted-cross-level-clearing-v2");
});

test("hybrid47 validates a single compression and recovers the declared raw generation", () => {
  const p = validateLocalAnalysisPayload(hybridTierFixture(), "combined");
  assert.equal(p.schemaVersion, 47);
  assert.equal(hasMidpointCompression(p), true);
  const recovered = recoverInitialFitPayload(p);
  assert.equal(recovered.schemaVersion, 46);
  assert.equal(recovered.singles[0].estimatedDifficulty, hybridTierFixture(false).singles[0].estimatedDifficulty);
  assert.equal(p.singles[0].estimatedDifficulty, 19.44);
});

test("hybrid rejects unsupported source/method declarations, stale formulas and missing official fallback", () => {
  const mutate: Array<(p: AnalysisPayload) => void> = [
    (p) => { p.summary.method.hybridTiers!.scoring.missingOfficialPolicy = "submitted-fallback" as "unrated"; },
    (p) => { p.summary.method.hybridTiers!.submittedScoring.method.percentiles = [.1, .5, .9]; },
    (p) => { p.summary.method.hybridTiers!.submittedScoring.confidenceIntervals.minimumPlayers = 5; },
    (p) => { p.summary.method.crossLevelClearing!.minimumCalibrationLevel = 19 as 20; },
    (p) => { p.summary.method.crossLevelClearing!.skillMethod.minimumScoreRecords = 199 as 200; },
    (p) => { p.summary.method.crossLevelClearing!.calibration.Single!.anchors[0].level = 19; },
    (p) => { p.summary.method.crossLevelClearing!.lowerReferenceSkills!.Single = {}; },
    (p) => { p.summary.method.crossLevelClearing!.inputHashes.phoenix1 = "invalid"; },
    (p) => { p.singles[0].scoringMethod = "official-cross-level-v1"; },
    (p) => { p.singles[0].estimatedDifficulty = 19.5; },
    (p) => { p.singles[0].tierMetrics!.clearing.estimatedDifficulty = 19.5; },
    (p) => { p.singles[0].tierMetrics!.clearing.folderReferenceSkill = 22; },
    (p) => { p.singles[0].tierMetrics!.pumbility.scoringSupportCount = 5; },
    (p) => { p.singles[5].estimatedDifficulty = 20.5; },
    (p) => { p.singles[0].evidenceStatus = "Provisional"; },
  ];
  for (const change of mutate) {
    const p = hybridTierFixture(false); change(p);
    assert.throws(() => validateLocalAnalysisPayload(p, "combined"), /incompatible/);
  }
});

test("hybrid lower unsupported reference stays unrated while preserving qualified q10 evidence", () => {
  const p = hybridTierFixture(false);
  p.singles = p.singles.filter((r) => r.level !== 19 || r.chartId === "synthetic-19-0");
  p.summary.method.crossLevelClearing!.lowerReferenceSkills!.Single = {};
  const row = p.singles[0], c = row.tierMetrics!.clearing, composite = row.tierMetrics!.pumbility;
  Object.assign(c, { estimatedDifficulty: null, difficultyDelta: null, folderReferenceSkill: null, evidenceStatus: "Unrated",
    unratedReason: "Fewer than five comparable folder reference charts with 20 qualified clearers." });
  Object.assign(composite, {estimatedDifficulty: null, difficultyDelta: null, evidenceStatus: "Unrated"});
  const result = validateLocalAnalysisPayload(p, "combined");
  assert.equal(result.singles[0].tierMetrics!.clearing.q10Skill, 20.8);
  assert.equal(result.singles[0].tierMetrics!.pumbility.estimatedDifficulty, null);
});
