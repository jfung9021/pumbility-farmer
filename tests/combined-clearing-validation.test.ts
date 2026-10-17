import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { recoverCombinedClearingBaseline, validateCombinedClearingPayload } from "../lib/combined-clearing-validation.ts";
import type { AnalysisPayload } from "../lib/types";

const fixture=JSON.parse(readFileSync(new URL("./fixtures/combined-official-clearing.json",import.meta.url),"utf8"));
const base:AnalysisPayload=fixture.base54??fixture.base;
const candidate:AnalysisPayload=fixture.candidate55??fixture.candidate;
const clone=()=>structuredClone(candidate);
function validate(p:AnalysisPayload) { return validateCombinedClearingPayload(p,b=>assert.deepEqual(b,base)); }

test("combined official Clearing recovers its frozen schema54 and replays calibration",()=>{
  assert.deepEqual(recoverCombinedClearingBaseline(candidate),base);
  assert.equal(validate(candidate),candidate);
});
test("combined official Clearing rejects changes to an unrelated Scoring component",()=>{
  const p=clone();p.doubles.find(r=>r.level>=26)!.scoringVariants!.sssplus!.estimatedDifficulty=42;
  assert.throws(()=>validate(p));
});
test("combined official Clearing rejects cap flags that disagree with individual sources",()=>{
  const p=clone(),row=p.doubles.find(r=>r.level>=26&&r.tierMetrics!.clearing.clearingEvidence?.sourceStates&&Object.keys(r.tierMetrics!.clearing.clearingEvidence.sourceStates).length)!;
  row.tierMetrics!.clearing.defaultedAtCap=!row.tierMetrics!.clearing.defaultedAtCap;
  assert.throws(()=>validate(p),/cap/);
});
test("combined official Clearing rejects a changed mean or extra compression",()=>{
  const p=clone(),row=p.doubles.find(r=>r.level>=26&&r.tierMetrics!.clearing.estimatedDifficulty!==null)!;
  row.pumbilityVariants!.sssplus!.estimatedDifficulty!+=.1;
  assert.throws(()=>validate(p),/Pumbility/);
  const q=clone(),c=q.doubles.find(r=>r.level>=26&&r.tierMetrics!.clearing.estimatedDifficulty!==null)!.tierMetrics!.clearing;
  c.estimatedDifficulty!+=.1;
  assert.throws(()=>validate(q),/compression/);
});
test("combined official Clearing rejects missing Phoenix1 provenance",()=>{
  const p=clone(),method=p.summary.method.combinedOfficialClearing as Record<string,any>;
  delete method.sourceDates.Phoenix;
  assert.throws(()=>validate(p),/source|Source|date/);
});
