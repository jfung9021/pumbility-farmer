import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { validateLocalAnalysisPayload, localCombinedResultsPath, SSSPLUS_MEDIAN_LOCAL_RESULTS_PATH, SSSPLUS_REFINED_ANCHORS_LOCAL_RESULTS_PATH, SSSPLUS_STABLE_SCALE_LOCAL_RESULTS_PATH } from "../lib/local-analysis.ts";
import { recoverMedianBaseline, validateMedianAnalysisPayload, validateFinalCurve, inverseFinalCost } from "../lib/sssplus-median-validation.ts";
import { validateSssPlusAnalysisPayload } from "../lib/sssplus-analysis-validation.ts";
import { sssCenteringFolders } from "../lib/sss-centering-validation.ts";
import { projectScoringGoal, scoringTargetForPayload, scoringGoalFromSearchParams } from "../lib/scoring-variants.ts";
import type { AnalysisPayload } from "../lib/types.ts";

const candidate=JSON.parse(readFileSync(new URL("./fixtures/sssplus-median-tiers.json",import.meta.url),"utf8")).candidate as AnalysisPayload;
const original=JSON.parse(readFileSync(new URL("./fixtures/sssplus-target-tiers.json",import.meta.url),"utf8")).candidate as AnalysisPayload;
const historical=JSON.parse(readFileSync(new URL("./fixtures/sssplus-target-tiers.json",import.meta.url),"utf8")).base as AnalysisPayload;
const refinedCandidate=JSON.parse(readFileSync(new URL("./fixtures/sssplus-refined-anchors.json",import.meta.url),"utf8")).candidate as AnalysisPayload;
const stableCandidate=JSON.parse(readFileSync(new URL("./fixtures/sssplus-stable-scale.json",import.meta.url),"utf8")).candidate as AnalysisPayload;
const validate=(p:AnalysisPayload)=>validateMedianAnalysisPayload(p,base=>validateSssPlusAnalysisPayload(base,old=>assert.deepEqual(old,historical)));
test("schema 53 recovers the exact historical candidate and validates every stage",()=>{
  assert.deepEqual(recoverMedianBaseline(candidate),original);
  assert.equal(validate(candidate),candidate);
});
test("schema 53 stays local, defaults to SSS+ and keeps General/Clearing projections",()=>{
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS:"1",PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sssplus-median"}),SSSPLUS_MEDIAN_LOCAL_RESULTS_PATH);
  assert.deepEqual(scoringTargetForPayload(candidate),{goal:"sssplus",label:"SSS+",score:995000});
  assert.equal(scoringGoalFromSearchParams(new URLSearchParams("scoring=sss"),candidate),"sss");
  const row=candidate.doubles.find(r=>r.level===22)!;
  assert.equal(projectScoringGoal(row,"sssplus").scoringMedianCalibration?.method,"sssplus-refined-median-calibration-v1");
  assert.equal(projectScoringGoal(row,"general"),row);
  assert.equal(projectScoringGoal(row,"sssplus","clearing"),row);
});
for(const [name,mutate] of [
  ["wrong target",(p:AnalysisPayload)=>{(p.summary.method.sssplusMedianCalibration as Record<string,unknown>).target=990000;}],
  ["anchor population",(p:AnalysisPayload)=>{const m=p.summary.method.sssplusMedianCalibration as any;m.populations.Double["22"].populationChartIds.pop();}],
  ["curve offset",(p:AnalysisPayload)=>{const m=p.summary.method.sssplusMedianCalibration as any;m.calibration.Double.offset+=.1;}],
  ["stale old shift",(p:AnalysisPayload)=>{p.doubles.find(r=>r.level===22)!.sssplusTailBaseline!.scoring.scoringFolderCentering!.finalShift+=1;}],
  ["latent coordinate",(p:AnalysisPayload)=>{p.doubles.find(r=>r.level===22)!.sssplusTailBaseline!.scoring.scoringMedianCalibration!.refinedLatent!+=.1;}],
  ["changed clearing",(p:AnalysisPayload)=>{p.doubles[0].tierMetrics!.clearing.estimatedDifficulty!+=.1;}],
  ["submitted value",(p:AnalysisPayload)=>{p.singles.find(r=>r.level<20)!.scoringVariants!.sssplus!.estimatedDifficulty!+=.1;}],
] as const)test(`schema 53 rejects ${name}`,()=>{
  const bad=structuredClone(candidate);mutate(bad);assert.throws(()=>validate(bad));
});
test("schema 53 checks privacy before recovering historical snapshots",()=>{
  const bad=structuredClone(candidate);
  Object.assign(bad.singles[0].sssplusMedianBaseline!.scoring,{gameTag:"private"});
  assert.throws(()=>validateLocalAnalysisPayload(bad,"combined"),/private/);
});

test("refined anchor policy validates independently and preserves exact model baseline",()=>{
  assert.deepEqual(recoverMedianBaseline(refinedCandidate),original);
  assert.equal(validate(refinedCandidate),refinedCandidate);
  assert.equal((refinedCandidate.summary.method.sssplusMedianCalibration as any).version,2);
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS:"1",PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sssplus-median-v2"}),SSSPLUS_REFINED_ANCHORS_LOCAL_RESULTS_PATH);
  assert.notEqual(localCombinedResultsPath({PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sssplus-median-v2"}),SSSPLUS_REFINED_ANCHORS_LOCAL_RESULTS_PATH);
});

test("five corrected estimates can support an anchor with no two-sided first pass; four cannot",()=>{
  const view=structuredClone(original);
  for(const row of view.doubles) {
    row.scoringVariants!.sss=structuredClone(row.sssplusCenteringBaseline!.scoring);
    if(row.level===22)Object.assign(row.scoringVariants!.sss!.scoringFirstPass!,{estimatedDifficulty:null,evidenceStatus:"Unrated",scoringTargetSuccesses:0});
  }
  assert.notEqual(sssCenteringFolders(view).Double["22"].basis,"folder");
  assert.equal(sssCenteringFolders(view,false).Double["22"].basis,"folder");
  assert.equal(sssCenteringFolders(view,false).Double["22"].mixedFirstPassCount,0);
  view.doubles.find(r=>r.level===22)!.scoringVariants!.sss!.evidenceStatus="Unrated";
  assert.notEqual(sssCenteringFolders(view,false).Double["22"].basis,"folder");
});

test("refined anchor policy rejects stale support rules and unsupported versions",()=>{
  for(const edit of [
    (p:AnalysisPayload)=>{(p.summary.method.sssplusMedianCalibration as any).minimumMixedCharts=5;},
    (p:AnalysisPayload)=>{(p.summary.method.sssplusMedianCalibration as any).minimumFinalCharts=4;},
    (p:AnalysisPayload)=>{(p.summary.method.sssplusMedianCalibration as any).version=4;},
  ]) {const bad=structuredClone(refinedCandidate);edit(bad);assert.throws(()=>validate(bad));}
});

test("stable scale policy validates every stage and preserves the model baseline",()=>{
  assert.deepEqual(recoverMedianBaseline(stableCandidate),original);
  assert.equal(validate(stableCandidate),stableCandidate);
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS:"1",PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sssplus-median-v3"}),SSSPLUS_STABLE_SCALE_LOCAL_RESULTS_PATH);
  assert.notEqual(localCombinedResultsPath({PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sssplus-median-v3"}),SSSPLUS_STABLE_SCALE_LOCAL_RESULTS_PATH);
  assert.equal(projectScoringGoal(stableCandidate.doubles.find(r=>r.level===20)!,"sssplus").scoringMedianCalibration?.version,3);
});

test("stable lower curve uses the adjacent median increment and is continuous",()=>{
  const c=(stableCandidate.summary.method.sssplusMedianCalibration as any).calibration.Double;
  assert.equal(validateFinalCurve(c,3),c);
  const [a,b]=c.anchors, slope=(b.cost-a.cost)/(b.level-a.level);
  assert.equal(c.finalScale.lowerSlope,slope);
  assert.ok(c.offset<=2*a.cost-b.cost);
  assert.equal(inverseFinalCost(a.cost-2*slope,c),a.difficulty-2);
  assert.ok(Math.abs(inverseFinalCost(a.cost-1e-10,c)-a.difficulty)<1e-7);
  assert.ok(Math.abs(inverseFinalCost(a.cost+1e-10,c)-a.difficulty)<1e-7);
  const values=[-100,-1,a.cost-1e-6,a.cost,a.cost+1e-6,b.cost,100].map(x=>inverseFinalCost(x,c));
  assert.deepEqual(values,values.toSorted((a,b)=>a-b));
});

test("stable scale rejects old tails, altered margins, slopes, projections and intervals",()=>{
  for(const edit of [
    (p:AnalysisPayload)=>{(p.summary.method.sssplusMedianCalibration as any).calibration.Double.tailPolicy="tangent-linear";},
    (p:AnalysisPayload)=>{(p.summary.method.sssplusMedianCalibration as any).calibration.Double.finalScale.lowerSlope*=2;},
    (p:AnalysisPayload)=>{(p.summary.method.sssplusMedianCalibration as any).finalScalePolicy.minimumOffsetMarginAnchorGaps=.5;},
    (p:AnalysisPayload)=>{p.doubles.find(r=>r.level===20)!.sssplusTailBaseline!.scoring.estimatedDifficulty!+=.1;},
    (p:AnalysisPayload)=>{p.doubles.find(r=>r.level===20)!.scoringVariants!.sssplus!.difficultyCi95Low!+=.1;},
  ]) {const bad=structuredClone(stableCandidate);edit(bad);assert.throws(()=>validate(bad));}
});
