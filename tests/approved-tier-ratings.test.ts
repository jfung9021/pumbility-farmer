import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {resolvePumbilityPolicy,pumbilityReference} from "../lib/pumbility-policy.ts";
import {hasMidpointCompression} from "../lib/tier-postprocessing.ts";
import {scoringTargetForPayload,projectScoringGoal} from "../lib/scoring-variants.ts";
import {chartEvidenceLines} from "../lib/tier-evidence-copy.ts";
import {tierChartDetails,tierBrowserPayload} from "../lib/tier-browser-payload.ts";
import type {AnalysisPayload} from "../lib/types.ts";
const p=JSON.parse(readFileSync(new URL("./fixtures/approved-tier-ratings.json",import.meta.url),"utf8")).candidate as AnalysisPayload;
test("production schema 162 resolves the maximum policy and retains the SSS+ target",()=>{
  assert.equal(resolvePumbilityPolicy(p)?.aggregation,"centered-maximum");
  assert.equal(hasMidpointCompression(p),true);assert.equal(scoringTargetForPayload(p).goal,"sssplus");
  assert.equal(pumbilityReference("centered-maximum",25.1,25.3,-.2),25.1);
  assert.throws(()=>pumbilityReference("centered-maximum",25.1,25.3),/folder offset/);
});
test("production notes describe maximum centering and observed capped evidence in plain English",()=>{
  const chart=projectScoringGoal(p.singles.find(r=>r.tierMetrics?.clearing.observedAtCap&&r.tierMetrics.clearing.estimatedDifficulty!==null)!,"sssplus","pumbility");
  const lines=chartEvidenceLines(chart,"pumbility").join(" ");
  assert.match(lines,/starts with the higher rating, then centers this official level around S24.5/);
  assert.match(lines,/capped leaderboard.*remains provisional/);assert.doesNotMatch(lines,/uses their average/);
  assert.equal("centeredMaximumPumbilityBaseline" in tierChartDetails(p,chart.chartId)!.chart,false);
  assert.equal(resolvePumbilityPolicy(tierBrowserPayload(p))?.aggregation,"centered-maximum");
});
