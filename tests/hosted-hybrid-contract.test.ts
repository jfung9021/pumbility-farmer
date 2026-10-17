import assert from "node:assert/strict";
import test from "node:test";
import { readJsonResponse } from "../lib/api-response.ts";
import { AVERAGE_PUMBILITY_LOCAL_RESULTS_PATH, COMBINED_LOCAL_RESULTS_PATH, HYBRID_PUMBILITY_LOCAL_RESULTS_PATH,
  localAnalysisEnabled, localCombinedResultsPath, validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { applyAveragePumbility } from "../lib/pumbility-experiment.ts";
import { resolvePumbilityPolicy } from "../lib/pumbility-policy.ts";
import { hasMidpointCompression } from "../lib/tier-postprocessing.ts";
import type { AnalysisPayload } from "../lib/types.ts";
import { hybridTierFixture } from "./hybrid-tier-fixture.ts";

test("hosted48 response and declared policy work without local experiment flags", async () => {
  assert.equal(localAnalysisEnabled({}), false);
  const hosted = applyAveragePumbility(hybridTierFixture());
  const payload = await readJsonResponse<AnalysisPayload>(new Response(JSON.stringify(hosted), {status: 200}));
  assert.equal(payload.schemaVersion, 48);
  assert.equal(payload.mix.key, "combined");
  assert.equal(resolvePumbilityPolicy(payload)?.aggregation, "average");
  assert.equal(hasMidpointCompression(payload), true);
  assert.equal(validateLocalAnalysisPayload(payload, "combined").schemaVersion, 48);
  assert.equal(payload.singles[0].tierMetrics!.pumbility.estimatedDifficulty,
    (payload.singles[0].estimatedDifficulty! + payload.singles[0].tierMetrics!.clearing.estimatedDifficulty!) / 2);
});

test("local preview file selection cannot override the default without explicit local mode", () => {
  assert.equal(localCombinedResultsPath({}), COMBINED_LOCAL_RESULTS_PATH);
  assert.equal(localCombinedResultsPath({PIU_LOCAL_PUMBILITY_EXPERIMENT: "hybrid"}), COMBINED_LOCAL_RESULTS_PATH);
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS: "0", PIU_LOCAL_PUMBILITY_EXPERIMENT: "average"}), COMBINED_LOCAL_RESULTS_PATH);
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS: "1"}), COMBINED_LOCAL_RESULTS_PATH);
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS: "1", PIU_LOCAL_PUMBILITY_EXPERIMENT: "average"}), AVERAGE_PUMBILITY_LOCAL_RESULTS_PATH);
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS: "1", PIU_LOCAL_PUMBILITY_EXPERIMENT: "hybrid"}), HYBRID_PUMBILITY_LOCAL_RESULTS_PATH);
});
