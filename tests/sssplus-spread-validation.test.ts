import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { localCombinedResultsPath, SSSPLUS_SPREAD_LOCAL_RESULTS_PATH, SSSPLUS_COMBINED_CLEARING_LOCAL_RESULTS_PATH, validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { recoverSpreadBaseline, spreadRating, validateSpreadAnalysisPayload } from "../lib/sssplus-spread-validation.ts";
import { hasSssScoringVariants, projectScoringGoal, scoringGoalFromSearchParams, scoringTargetForPayload } from "../lib/scoring-variants.ts";
import type { AnalysisPayload } from "../lib/types.ts";

const fixture=JSON.parse(readFileSync(new URL("./fixtures/sssplus-spread.json",import.meta.url),"utf8"));
const base=fixture.base as AnalysisPayload,candidate=fixture.candidate as AnalysisPayload;
const validate=(p:AnalysisPayload)=>validateSpreadAnalysisPayload(p,frozen=>assert.deepEqual(frozen,base));
const policy=(p:AnalysisPayload)=>p.summary.method.sssplusSpreadPolicy as Record<string,any>;
const official=(p:AnalysisPayload)=>p.doubles.find(r=>r.level===20)!;

test("spread overlay recovers its exact predecessor and independently replays projection",()=>{
  assert.deepEqual(recoverSpreadBaseline(candidate),base);
  assert.equal(validate(candidate),candidate);
});
test("spread selector is local only; both new schemas default to SSS+",()=>{
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS:"1",PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sssplus-spread"}),SSSPLUS_SPREAD_LOCAL_RESULTS_PATH);
  assert.notEqual(localCombinedResultsPath({PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sssplus-spread"}),SSSPLUS_SPREAD_LOCAL_RESULTS_PATH);
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS:"1",PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sssplus-spread-combined-clearing"}),SSSPLUS_COMBINED_CLEARING_LOCAL_RESULTS_PATH);
  for(const schemaVersion of [54,55]) {
    const p={...candidate,schemaVersion};assert.equal(hasSssScoringVariants(p),true);
    assert.deepEqual(scoringTargetForPayload(p),{goal:"sssplus",label:"SSS+",score:995000});
    assert.equal(scoringGoalFromSearchParams(new URLSearchParams("scoring=sss"),p),"sss");
  }
});
test("spread projection preserves General and Clearing and exposes current target details",()=>{
  const row=official(candidate);
  assert.equal(projectScoringGoal(row,"general"),row);
  assert.equal(projectScoringGoal(row,"sssplus","clearing"),row);
  assert.equal(projectScoringGoal(row,"sssplus").scoringSpreadPolicy?.scale,.55);
});
test("asinh spread fixes midpoint, preserves order, and keeps distant estimates separated",()=>{
  assert.equal(spreadRating(null,20),null);assert.equal(spreadRating(20.5,20),20.5);
  const input=[-100,0,13.929,15.913,20,20.5,21,50,100],output=input.map(v=>spreadRating(v,20)!);
  assert.deepEqual(output,output.toSorted((a,b)=>a-b));
  assert.ok(spreadRating(15.913,20)!-spreadRating(13.929,20)!>.1);
  for(const x of [.01,.5,2,100]) {
    assert.ok(Math.abs(spreadRating(20.5+x,20)!-20.5)<x);
    assert.ok(Math.abs(spreadRating(20.5+x,20)!+spreadRating(20.5-x,20)!-41)<1e-12);
  }
  assert.throws(()=>spreadRating(20,20,0));assert.throws(()=>spreadRating(Infinity,20));
});
test("the overlay uses full precision target means and transforms conditional intervals",()=>{
  for(const row of candidate.doubles.filter(r=>r.level>=20)) {
    const s=row.scoringVariants!.sssplus!,c=row.tierMetrics!.clearing,p=row.pumbilityVariants!.sssplus!;
    assert.equal(p.estimatedDifficulty,(s.estimatedDifficulty!+c.estimatedDifficulty!)/2);
    assert.ok(s.difficultyCi95Low!<=s.difficultyCi95High!);
    assert.equal(s.initialFit!.estimatedDifficulty,row.sssplusTailBaseline!.scoring.initialFit!.estimatedDifficulty);
  }
});
test("tail counts start after the asinh spread stage",()=>{
  const rows=candidate.singles.concat(candidate.doubles).filter(r=>r.level>=20&&r.scoringVariants!.sssplus!.estimatedDifficulty!==null);
  const counts=policy(candidate).tailPolicy.counts;
  const before=rows.map(r=>r.scoringVariants!.sssplus!.scoringTailPolicy!.preTailDifficulty!-r.level-.5);
  assert.equal(counts.preTailMinDelta,Math.min(...before));
  assert.equal(counts.preTailMaxDelta,Math.max(...before));
  assert.equal(counts.preTailAbsAbove1_5,before.filter(d=>Math.abs(d)>1.5).length);
  assert.equal(counts.changedCharts,rows.filter(r=>r.scoringVariants!.sssplus!.scoringTailPolicy!.preTailDifficulty!==r.scoringVariants!.sssplus!.estimatedDifficulty).length);
});
for(const [name,edit] of [
  ["wrong numeric target",(p:AnalysisPayload)=>{policy(p).target=990000;}],
  ["unselected scale",(p:AnalysisPayload)=>{policy(p).scale=.5;}],
  ["second midpoint compression",(p:AnalysisPayload)=>{policy(p).compressionApplications=2;}],
  ["changed stage order",(p:AnalysisPayload)=>{policy(p).stageOrder.reverse();}],
  ["manufactured rare effect",(p:AnalysisPayload)=>{policy(p).rareAdjustments[official(p).chartId]={effect:1,metadata:{posteriorEffect:1}};}],
  ["changed prior policy",(p:AnalysisPayload)=>{policy(p).rarePolicy.minimumLocalCharts=1;}],
  ["changed lower curve",(p:AnalysisPayload)=>{policy(p).medianCalibration.calibration.Double.finalScale.lowerSlope*=2;}],
  ["changed anchor population",(p:AnalysisPayload)=>{policy(p).medianCalibration.populations.Double['20'].populationChartIds.pop();}],
  ["altered rating",(p:AnalysisPayload)=>{official(p).scoringVariants!.sssplus!.estimatedDifficulty!+=.01;}],
  ["altered interval",(p:AnalysisPayload)=>{official(p).scoringVariants!.sssplus!.difficultyCi95Low!+=.1;}],
  ["altered component mean",(p:AnalysisPayload)=>{official(p).pumbilityVariants!.sssplus!.estimatedDifficulty!+=.01;}],
  ["changed submitted rating",(p:AnalysisPayload)=>{p.singles.find(r=>r.level<20)!.scoringVariants!.sssplus!.estimatedDifficulty!+=.1;}],
  ["unreplayed boundary exception",(p:AnalysisPayload)=>{policy(p).boundaryCandidates[official(p).chartId]={eligible:true,strength:3};}],
  ["changed global budget",(p:AnalysisPayload)=>{policy(p).tailPolicy.exceptionLimit=11;}],
] as const)test(`spread rejects ${name}`,()=>{
  const bad=structuredClone(candidate);edit(bad);assert.throws(()=>validate(bad));
});
test("spread checks privacy before historical snapshot recovery",()=>{
  const bad=structuredClone(candidate);Object.assign(bad.doubles[0].sssplusSpreadBaseline!.scoring,{gameTag:"private-player"});
  assert.throws(()=>validateLocalAnalysisPayload(bad,"combined"),/private/);
});
