import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { REFINED_SSS_LOCAL_RESULTS_PATH, SSS_LOCAL_RESULTS_PATH, localCombinedResultsPath, validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { noSssBoundaryEndpoint, recoverRefinedFirstPass, validateRefinedAnalysisPayload } from "../lib/refined-analysis-validation.ts";
import { hasMidpointCompression } from "../lib/tier-postprocessing.ts";
import type { AnalysisPayload } from "../lib/types";

const baseline = JSON.parse(await readFile(new URL("./fixtures/sss-target-variants.json", import.meta.url), "utf8")) as AnalysisPayload;
// The small target-only fixture omits compatibility composite diagnostics.
for (const key of ["singles","doubles"] as const) for (const row of baseline[key]) {
  const p=row.tierMetrics!.pumbility, c=row.tierMetrics!.clearing;
  p.levelRank=1; p.levelComparisonCharts=1; c.levelRank=1; c.levelComparisonCharts=1;
  p.aggregation="average"; p.scoringSupportCount=row.nContributors; p.clearingSupportCount=c.ratedClearCount;
  p.initialFit={estimatedDifficulty:(Number(row.initialFit!.estimatedDifficulty)+Number(c.initialFit!.estimatedDifficulty))/2};
  row.pumbilityVariants!.general=structuredClone(p);
}
function candidate(): AnalysisPayload {
  const payload = structuredClone(baseline);
  payload.schemaVersion = 50;
  const hash="a".repeat(64);
  payload.summary.method.refinedTiers={version:1,method:"frozen-sss-second-pass-official-d26-clearing-v1",baseSchemaVersion:49,
    basePayloadSha256:hash,localOnly:true,clearingDispatch:{mode:"Double",minimumLevel:26},pumbilityAggregation:"average",compressionApplications:1};
  payload.summary.method.sssRefinement={version:1,method:"frozen-range-sss-second-pass-v1",scoreTarget:990000,firstPassPayloadSha256:hash,compressionApplications:1,
    rangePolicy:{minimumRatedCharts:5,edges:"actual-finite-first-pass-min-max",borrow:"same-source-mode-offset-interpolation-or-nearest",frozenBeforeSecondPass:true},
    ranges:{submitted:{Single:{},Double:{}},official:{Single:{},Double:{}}}};
  payload.summary.method.officialHighLevelClearing={version:1,method:"official-d26-clearer-ability-v1",source:"piuscores-official",mix:"Phoenix2",mode:"Double",
    minimumLevel:26,percentile:.2,difficultyDeltaScale:.7,compressionFactor:.5,
    playerSkill:{method:"leave-one-chart-out-top-official-levels",topCharts:25,minimumOtherCharts:25,historyMinimumLevel:23,levelMidpointOffset:.5,separateModes:true},uncertainty:{samples:1000}};
  for (const key of ["singles","doubles"] as const) for (const row of payload[key]) {
    row.scoringVariants!.sss!.scoringFirstPass=structuredClone(row.scoringVariants!.sss!);
    (row as unknown as Record<string,unknown>).refinementBaseline={sssPumbility:structuredClone(row.pumbilityVariants!.sss!)};
  }
  return payload;
}
const validate=(value:AnalysisPayload)=>validateRefinedAnalysisPayload(value,recovered=>assert.deepEqual(recovered,baseline));

test("refined generation restores the complete frozen first pass and validates both averages",()=>{
  const payload=candidate(), before=structuredClone(payload);
  assert.equal(validate(payload),payload);
  assert.deepEqual(payload,before);
  assert.deepEqual(recoverRefinedFirstPass(payload),baseline);
});

test("refined generation rejects changed references, compression and component means",()=>{
  const mutations:Array<(p:AnalysisPayload)=>void>=[
    p=>{p.singles[0].scoringVariants!.sss!.estimatedDifficulty!+=.1;},
    p=>{p.singles[0].pumbilityVariants!.sss!.estimatedDifficulty!+=.1;},
    p=>{p.singles[0].tierMetrics!.clearing.estimatedDifficulty!+=.1;},
    p=>{p.singles[0].scoringVariants!.sss!.scoringFirstPass!.estimatedDifficulty!+=.1;},
    p=>{(p.summary.method.refinedTiers as Record<string,unknown>).compressionApplications=2;},
    p=>{(p.summary.method.officialHighLevelClearing as Record<string,unknown>).mix="Phoenix";},
  ];
  for(const mutate of mutations){const payload=candidate();mutate(payload);assert.throws(()=>validate(payload));}
});

test("schema50 privacy checks run before frozen-view recovery",()=>{
  const payload=candidate();
  Object.assign(payload.singles[0].scoringVariants!.sss!.scoringFirstPass!,{gameTag:"private"});
  assert.throws(()=>validateLocalAnalysisPayload(payload,"combined"),/private/);
});

test("new candidate selection is local and preserves schema49 rollback selection",()=>{
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS:"1",PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sss-refined"}),REFINED_SSS_LOCAL_RESULTS_PATH);
  assert.notEqual(localCombinedResultsPath({PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sss-refined"}),REFINED_SSS_LOCAL_RESULTS_PATH);
  assert.equal(localCombinedResultsPath({PIU_LOCAL_ANALYSIS:"1",PIU_LOCAL_SCORING_VARIANT_EXPERIMENT:"sss"}),SSS_LOCAL_RESULTS_PATH);
  const payload=candidate();
  payload.summary.method.ratingPostprocessing={version:1,method:"official-midpoint-compression",factor:.5,reference:"current-official-level-plus-0.5",metrics:["scoring","clearing"],baseAnalysisSchemaVersion:46};
  assert.equal(hasMidpointCompression(payload),true);
});

test("no-SSS endpoint validates the top-three average, sparse boards, ties and the original policy",()=>{
  for (const scores of [[989999],[989999,985001],[989999,985001,980001],[989900,989900,980000]]) {
    const full={records:scores.length+2,highestScore:scores[0],lowestScore:950000,topScores:scores};
    // Sparse boards have no hidden extra scores; the three-score examples include lower records.
    if(scores.length<3) full.records=scores.length;
    const boundary={endpointPolicy:"mean-top-3-available-v1",endpointScoreCount:scores.length};
    assert.equal(noSssBoundaryEndpoint(full,boundary,2,2),scores.reduce((sum,n)=>sum+n,0)/scores.length);
    assert.equal(noSssBoundaryEndpoint(full,{},1,1),scores[0]);
  }
});

test("no-SSS endpoint rejects omitted top scores, bad order, cap violations and incorrect support",()=>{
  const full={records:10,highestScore:989999,lowestScore:950000,topScores:[989999,985001,980001]};
  const boundary={endpointPolicy:"mean-top-3-available-v1",endpointScoreCount:3};
  for (const bad of [{...full,topScores:[989999]}, {...full,topScores:[985001,989999,980001]},
    {...full,topScores:[990000,985001,980001]}, {...full,topScores:[989999,985001,NaN]},
    {...full,topScores:[989999,985001,940000]}, {...full,records:0}]) {
    assert.throws(()=>noSssBoundaryEndpoint(bad,boundary,2,2));
  }
  assert.throws(()=>noSssBoundaryEndpoint(full,{...boundary,endpointScoreCount:1},2,2));
  assert.throws(()=>noSssBoundaryEndpoint(full,boundary,2,1));
});
