import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { recoverCombinedClearingBaseline, validateCombinedClearingPayload } from "../lib/combined-clearing-validation.ts";
import { localCombinedResultsPath, SSSPLUS_P1_OBSERVED_CLEARING_LOCAL_RESULTS_PATH } from "../lib/local-analysis.ts";
import type { AnalysisPayload } from "../lib/types";

const fixture=JSON.parse(readFileSync(new URL("./fixtures/p1-observed-clearing.json",import.meta.url),"utf8"));
const base=fixture.base as AnalysisPayload,candidate=fixture.candidate as AnalysisPayload;
const rows=(p:AnalysisPayload)=>p.doubles.filter(r=>r.level>=26);
const validate=(p:AnalysisPayload)=>validateCombinedClearingPayload(p,b=>assert.deepEqual(b,base));

test("Phoenix1 diagnostic cap policy recovers its predecessor and validates independently",()=>{
  assert.deepEqual(recoverCombinedClearingBaseline(candidate),base);
  assert.equal(validate(candidate),candidate);
  for(const row of rows(candidate))assert.deepEqual(row.scoringVariants,base.doubles.find(r=>r.chartId===row.chartId)!.scoringVariants);
});
test("Phoenix1-capped charts retain distinct estimates on both sides of the folder midpoint",()=>{
  const charts=rows(candidate),values=charts.map(r=>r.tierMetrics!.clearing.estimatedDifficulty!).toSorted((a,b)=>a-b);
  assert.equal(values[Math.floor(values.length/2)],26.5);
  assert.ok(values[0]<26.5&&values.at(-1)!>26.5);
  assert.ok(new Set(values).size>1);
  for(const r of charts) {
    const c=r.tierMetrics!.clearing,e=c.clearingEvidence!;
    assert.equal(e.sourceStates!.Phoenix.possiblyTruncated,true);
    assert.equal(e.possiblyTruncated,true);
    assert.equal(c.defaultedAtCap,false);
    assert.notEqual(c.difficultyCi95Low,null);
    assert.notEqual(c.q20SkillCi95Low,null);
    assert.equal(c.evidenceStatus,"Provisional");
  }
});
test("Phoenix1 diagnostic policy rejects a manufactured shared cap fallback",()=>{
  const p=structuredClone(candidate);rows(p)[0].tierMetrics!.clearing.defaultedAtCap=true;
  assert.throws(()=>validate(p),/cap/);
});
test("Phoenix1 diagnostic policy preserves raw cap provenance",()=>{
  const p=structuredClone(candidate);rows(p)[0].tierMetrics!.clearing.clearingEvidence!.possiblyTruncated=false;
  assert.throws(()=>validate(p),/cap/);
});
test("Phoenix2 cap continues to require the existing fallback",()=>{
  const p=structuredClone(candidate);rows(p)[0].tierMetrics!.clearing.clearingEvidence!.sourceStates!.Phoenix2.possiblyTruncated=true;
  assert.throws(()=>validate(p),/cap/);
});
test("Phoenix1 diagnostic policy rejects a stale policy identifier",()=>{
  const p=structuredClone(candidate);(p.summary.method.combinedOfficialClearing as Record<string,any>).capPolicy="any-source-possibly-truncated-v1";
  assert.throws(()=>validate(p),/policy/);
});
test("Phoenix1 diagnostic candidate uses an isolated local selector",()=>{
  const environment={PIU_LOCAL_ANALYSIS:"1",PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sssplus-spread-p1-observed-clearing"};
  assert.equal(localCombinedResultsPath(environment),SSSPLUS_P1_OBSERVED_CLEARING_LOCAL_RESULTS_PATH);
  assert.notEqual(localCombinedResultsPath({...environment,PIU_LOCAL_ANALYSIS:undefined}),SSSPLUS_P1_OBSERVED_CLEARING_LOCAL_RESULTS_PATH);
});
