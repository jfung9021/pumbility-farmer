import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { CENTERED_SSS_LOCAL_RESULTS_PATH, REFINED_SSS_LOCAL_RESULTS_PATH, localCombinedResultsPath, validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { recoverUncenteredSss, sssCenteringFolders, validateCenteredAnalysisPayload } from "../lib/sss-centering-validation.ts";
import { hasSssScoringVariants, projectScoringGoal } from "../lib/scoring-variants.ts";
import { hasMidpointCompression } from "../lib/tier-postprocessing.ts";
import type { AnalysisPayload } from "../lib/types";

const fixture=JSON.parse(await readFile(new URL("./fixtures/sss-folder-centered.json",import.meta.url),"utf8")) as {base:AnalysisPayload;centered:AnalysisPayload};
const validate=(payload:AnalysisPayload)=>validateCenteredAnalysisPayload(payload,base=>assert.deepEqual(base,fixture.base));

test("Python centered points validate odd and even medians, sparse donors and original recovery",()=>{
  const payload=structuredClone(fixture.centered),before=structuredClone(payload);
  assert.equal(validate(payload),payload);
  assert.deepEqual(payload,before);
  assert.deepEqual(recoverUncenteredSss(payload),fixture.base);
  const folders=sssCenteringFolders(fixture.base);
  assert.ok(Math.abs(folders.Single["20"].finalShift-.3)<1e-10);
  assert.ok(Math.abs(folders.Single["21"].finalShift-.15)<1e-10);
  assert.ok(Math.abs(folders.Double["21"].finalShift+.1)<1e-10);
  assert.deepEqual(folders.Single["21"].donorLevels,[20,22]);
});

test("centered validation rejects stale points, intervals, Pumbility, donors and populations",()=>{
  const mutations:Array<(p:AnalysisPayload)=>void>=[
    p=>{p.singles[1].scoringVariants!.sss!.estimatedDifficulty!+=.1;},
    p=>{p.singles[1].scoringVariants!.sss!.initialFit!.estimatedDifficulty=20.5;},
    p=>{p.singles[1].scoringVariants!.sss!.difficultyCi95Low!+=.1;},
    p=>{p.singles[1].pumbilityVariants!.sss!.estimatedDifficulty!+=.1;},
    p=>{p.singles[1].scoringVariants!.sss!.scoringFolderCentering!.finalShift=0;},
    p=>{const m=p.summary.method.sssFolderCentering as {folders:Record<string,Record<string,{donorLevels:number[]}>>};m.folders.Single["21"].donorLevels=[20];},
    p=>{const m=p.summary.method.sssFolderCentering as {folders:Record<string,Record<string,{populationChartIds:string[]}>>};m.folders.Single["20"].populationChartIds.pop();},
    p=>{p.singles[1].tierMetrics!.clearing.estimatedDifficulty!+=.1;},
  ];
  for(const mutate of mutations){const bad=structuredClone(fixture.centered);mutate(bad);assert.throws(()=>validate(bad));}
});

test("centering keeps below20 values and General/Clearing projections while exposing SSS normalization",()=>{
  const old=fixture.base.singles[0],below=fixture.centered.singles[0];
  assert.deepEqual(projectScoringGoal(below,"sss").estimatedDifficulty,old.scoringVariants!.sss!.estimatedDifficulty);
  const row=fixture.centered.singles[1],projected=projectScoringGoal(row,"sss","pumbility");
  assert.equal(hasSssScoringVariants(fixture.centered),true);
  assert.equal(projected.scoringFolderCentering!.basis,"folder");
  assert.equal(projectScoringGoal(row,"general"),row);
  assert.equal(projectScoringGoal(row,"sss","clearing"),row);
  assert.deepEqual(projected.tierMetrics!.clearing,row.tierMetrics!.clearing);
  assert.deepEqual(projected.tierMetrics!.pumbility,row.pumbilityVariants!.sss!);
});

test("schema51 privacy is checked before any baseline snapshots are stripped",()=>{
  const bad=structuredClone(fixture.centered);
  Object.assign(bad.singles[0].sssCenteringBaseline!.scoring,{gameTag:"private"});
  assert.throws(()=>validateLocalAnalysisPayload(bad,"combined"),/private/);
});

test("centered local selection preserves refined rollback and compression recognition",()=>{
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS:"1",PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sss-centered"}),CENTERED_SSS_LOCAL_RESULTS_PATH);
  assert.notEqual(localCombinedResultsPath({PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sss-centered"}),CENTERED_SSS_LOCAL_RESULTS_PATH);
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS:"1",PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sss-refined"}),REFINED_SSS_LOCAL_RESULTS_PATH);
  const p=structuredClone(fixture.centered);
  p.summary.method.ratingPostprocessing={version:1,method:"official-midpoint-compression",factor:.5,reference:"current-official-level-plus-0.5",metrics:["scoring","clearing"],baseAnalysisSchemaVersion:46};
  assert.equal(hasMidpointCompression(p),true);
});
