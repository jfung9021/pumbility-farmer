import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { resolvePumbilityPolicy } from "../lib/pumbility-policy.ts";
import { validateLocalAnalysisPayload } from "../lib/local-analysis.ts";

test("unmarked production maximum requires its deployed tier version", async () => {
  const payload = JSON.parse(await readFile(new URL("./fixtures/production-tier-maximum.json", import.meta.url), "utf8"));
  assert.equal(resolvePumbilityPolicy(payload)?.aggregation, "maximum");
  validateLocalAnalysisPayload(payload, "combined");
  payload.summary.method.tierMetrics.version = 10;
  assert.equal(resolvePumbilityPolicy(payload), null);
  assert.throws(() => validateLocalAnalysisPayload(payload, "combined"), /unrecognized Pumbility policy/);
});
