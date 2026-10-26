import assert from "node:assert/strict";
import { compressionPolicy } from "../lib/tier-postprocessing.ts";
import { readFileSync } from "node:fs";
import test from "node:test";
import { finalWidthParameters, finalWidthRating, recoverSourceAwareBaseline, validateSourceAwarePayload } from "../lib/source-aware-validation.ts";
import { localCombinedResultsPath } from "../lib/local-analysis.ts";
import { scoringTargetForPayload, hasSssScoringVariants, projectScoringGoal } from "../lib/scoring-variants.ts";
import type { AnalysisPayload } from "../lib/types";

const fixture=JSON.parse(readFileSync(new URL("./fixtures/source-aware-width.json",import.meta.url),"utf8"));
const base=fixture.base as AnalysisPayload,candidate=fixture.candidate as AnalysisPayload;
const validate=(p:AnalysisPayload)=>validateSourceAwarePayload(p,b=>assert.deepEqual(b,base));

test("source-aware generation independently replays final widths and recovers schema 55",()=>{
  assert.deepEqual(recoverSourceAwareBaseline(candidate),base);
  assert.equal(validate(candidate),candidate);
  assert.equal(scoringTargetForPayload(candidate).goal,"sssplus");assert.equal(hasSssScoringVariants(candidate),true);
  const row=candidate.doubles.find(r=>r.scoringVariants!.sssplus!.finalFolderWidth)!;
  assert.deepEqual(projectScoringGoal(row,"sssplus").finalFolderWidth,row.scoringVariants!.sssplus!.finalFolderWidth);
});

test("final width is increasing and protects an asymmetric even median pair",()=>{
  const values=[26.2,26.45,26.55,26.57,26.6,26.8],p=finalWidthParameters(values.map((value,id)=>({id,value})),26);
  const out=values.map(v=>finalWidthRating(v,p)!);
  assert.ok(Math.abs((out[2]+out[3])/2-26.5)<1e-10);
  assert.deepEqual(out.toSorted((a,b)=>a-b),out);
  assert.ok(out.at(-1)!>26.9);
});

test("sparse and zero-spread inputs retain their existing scale",()=>{
  for(const values of [[26.1,26.2,26.3],[26.5,26.5,26.5,26.5]]){
    const p=finalWidthParameters(values.map((value,id)=>({id,value})),26);assert.equal(p.basis,"unavailable");
    assert.deepEqual(values.map(v=>finalWidthRating(v,p)),values);
  }
});

test("already wide folders retain distances and cross-level outliers",()=>{
  const values=[25.2,25.9,26.4,26.6,27.1,28.2],p=finalWidthParameters(values.map((value,id)=>({id,value})),26);
  assert.deepEqual(values.map(v=>finalWidthRating(v,p)),values);
});

test("four-chart local calibration stays provisional and cap defaults do not provide width",()=>{
  const p=finalWidthParameters([26.2,26.4,26.5,27.1].map((value,id)=>({id,value})),26);assert.equal(p.provisional,true);
  const capped=finalWidthParameters([26.2,26.4,26.5,27.1].map((value,id)=>({id,value,capDefault:id===0})),26);
  assert.equal(capped.basis,"unavailable");
});

test("source-aware validator rejects modified point, width population and component mean",()=>{
  for(const mutate of [
    (r:any)=>{r.scoringVariants.sssplus.estimatedDifficulty+=.1;},
    (r:any)=>{r.scoringVariants.sssplus.finalFolderWidth.parameters.upperReference+=.01;},
    (r:any)=>{r.pumbilityVariants.sssplus.estimatedDifficulty+=.1;},
  ]){
    const p=structuredClone(candidate),r=p.doubles.find(r=>r.scoringVariants!.sssplus!.finalFolderWidth)!;mutate(r);
    assert.throws(()=>validate(p),/width|mean|recovery/);
  }
});

test("semantic exclusions and target-fit identities cannot be silently changed",()=>{
  const p=structuredClone(candidate) as any;p.summary.method.sourceAwareTiers.sourcePolicy.effectivePolicySha256="0".repeat(64);
  assert.throws(()=>validate(p),/semantic/);
  const q=structuredClone(candidate) as any;q.summary.method.sourceAwareTiers.sourcePolicy.targetFits[0].identity.target=990;
  assert.throws(()=>validate(q),/identity/);
});

test("source-aware selector is isolated to enabled local analysis",()=>{
  const env={PIU_LOCAL_ANALYSIS:"1",PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"source-aware-width"};
  assert.match(localCombinedResultsPath(env),/source-aware-width/);
  assert.doesNotMatch(localCombinedResultsPath({...env,PIU_LOCAL_ANALYSIS:undefined}),/source-aware-width/);
});


test("source-aware Clearing validates the recorded 75% adjustment before final width",()=>{
  const p=structuredClone(candidate);
  p.summary.method.ratingPostprocessing=compressionPolicy(46,.75);
  for(const row of [...p.singles,...p.doubles]){
    const metric=row.tierMetrics!.clearing,w=metric.finalFolderWidth;
    if(!w)continue;
    for(const field of ["estimatedDifficulty","difficultyCi95Low","difficultyCi95High"] as const){
      const value=w.input[field];
      metric.initialFit![field]=value===null?null:row.level+.5+(value-row.level-.5)/.75;
    }
  }
  assert.equal(validate(p),p);
  p.summary.method.ratingPostprocessing=compressionPolicy(46,.5);
  assert.throws(()=>validate(p),/not compressed exactly once/);
});
