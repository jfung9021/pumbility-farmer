import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { compactTierPayload, tierChartDetails } from "../lib/tier-browser-payload.ts";
import { validateLevel20WidthAnalysisPayload, validateLevel20WidthPublicPayload, recoverLevel20WidthBaseline, level20WidthValue, LEVEL20_WIDTH_POLICY } from "../lib/level20-width-validation.ts";
import { validateSssTailAnalysisPayload } from "../lib/sss-tail-validation.ts";
import { validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { hasScoringGoalVariants, projectScoringGoal, scoringTargetForPayload } from "../lib/scoring-variants.ts";
import { hasLimitedTierData } from "../lib/tier-metrics.ts";
import type { AnalysisPayload, ScoringGoal } from "../lib/types";

type Obj=Record<string,any>;
const load=(name:string)=>JSON.parse(readFileSync(new URL(`./fixtures/${name}`,import.meta.url),"utf8"));
const GOALS=["ss","ssplus","sss","sssplus"] as const;
function fixture(schema:57|58=58) {
  const base=structuredClone(load("source-aware-width.json").candidate) as AnalysisPayload;
  const submitted=load("submitted-sssplus-calibration.json"),goals=load("scoring-goal-overlays.json");
  base.schemaVersion=57;base.summary.method.submittedSssplusCalibration=submitted.method;
  for(const saved of submitted.doubles) {
    const row=base.doubles.find(r=>r.chartId===saved.chartId)!;
    row.scoringVariants!.sssplus=saved.scoring;row.pumbilityVariants!.sssplus=saved.pumbility;
    if(saved.recovery)row.submittedSssplusCalibrationBaseline=saved.recovery;
  }
  if(schema===58) {
    base.schemaVersion=58;Object.assign(base.summary.method,goals.method);
    for(const key of ["singles","doubles"] as const)for(const saved of goals[key]) {
      const row=base[key].find(r=>r.chartId===saved.chartId)!;
      Object.assign(row.scoringVariants!,saved.scoringVariants);Object.assign(row.pumbilityVariants!,saved.pumbilityVariants);
    }
  }
  const legacy=structuredClone(base),tail=load("sss-tail-policy.json")[String(schema)];
  legacy.schemaVersion=59;legacy.summary.scriptVersion=tail.scriptVersion;legacy.summary.method.sssTailPolicy=tail.method;
  for(const key of ["singles","doubles"] as const)for(const saved of tail[key]) {
    const row=legacy[key].find(r=>r.chartId===saved.chartId)!;
    row.scoringVariants!.sss=saved.scoring;row.pumbilityVariants!.sss=saved.pumbility;row.sssTailBaseline=saved.recovery;
  }
  const candidate=structuredClone(legacy),width=load("level20-width-overlay.json")[String(schema)];
  candidate.schemaVersion=60;candidate.summary.scriptVersion=width.scriptVersion;candidate.summary.method.level20ScoringWidth=width.method;
  for(const key of ["singles","doubles"] as const)for(const saved of width[key]) {
    const row=candidate[key].find(r=>r.chartId===saved.chartId)!;
    for(const name of ["scoringVariants","pumbilityVariants"] as const)for(const [goal,patch] of Object.entries(saved[name]))Object.assign(obj(row[name])[goal],patch);
    row.level20WidthBaseline=saved.recovery;
  }
  return {base,legacy,candidate,validateBase:(p:AnalysisPayload)=>{
    assert.deepEqual(p,legacy);
    validateSssTailAnalysisPayload(p,prior=>assert.deepEqual(prior,base));
  }};
}
const obj=(v:unknown)=>v as Obj;

test("level20 width curve matches Python for interior, asymmetric tails, ties and sparse populations",()=>{
  const vectors=load("level20-scoring-width.json");
  assert.deepEqual(vectors.policy,LEVEL20_WIDTH_POLICY);
  for(const c of vectors.cases)for(const v of c.vectors)assert.ok(Math.abs(level20WidthValue(v.input,c.parameters)!-v.output)<1e-10,`${c.name}: ${v.input}`);
});

test("schema60 exactly recovers and validates schema59 with optional earlier goals",()=>{
  for(const schema of [57,58] as const) {
    const {candidate,legacy,validateBase}=fixture(schema),frozen=structuredClone(candidate);
    assert.equal(validateLevel20WidthAnalysisPayload(candidate,validateBase),candidate);
    assert.deepEqual(recoverLevel20WidthBaseline(candidate),legacy);
    assert.deepEqual(candidate,frozen);
    assert.equal(scoringTargetForPayload(candidate).goal,"sssplus");
    for(const goal of ["general","sss","sssplus"] as const)assert.equal(hasScoringGoalVariants(candidate,goal),true);
    for(const goal of ["ss","ssplus"] as const)assert.equal(hasScoringGoalVariants(candidate,goal),schema===58);
  }
});

test("schema60 browser lists validate and agree with lazy details for all selected goals",()=>{
  for(const schema of [57,58] as const) {
    const {candidate}=fixture(schema),compact=compactTierPayload(candidate);
    assert.equal(compactTierPayload(compact),compact);
    assert.equal(validateLevel20WidthPublicPayload(compact),compact);
    for(const mode of ["singles","doubles"] as const)for(const row of compact[mode]) {
      assert.equal(row.level20WidthBaseline,undefined);
      const details=tierChartDetails(candidate,row.chartId)!.chart;
      assert.equal(details.level20WidthBaseline,undefined);
      for(const goal of GOALS)if(row.scoringVariants?.[goal]) {
        const projected=projectScoringGoal(row,goal),detail=projectScoringGoal(details,goal);
        assert.equal(projected.estimatedDifficulty,detail.estimatedDifficulty);
        assert.equal(projected.tierMetrics!.pumbility.estimatedDifficulty,detail.tierMetrics!.pumbility.estimatedDifficulty);
        if(projected.scoringLevel20Width)assert.deepEqual(projected.scoringLevel20Width,detail.scoringLevel20Width);
      }
    }
  }
});

test("schema60 rejects changed outputs, policy, metadata, ranks, intervals and scope",()=>{
  const {candidate,validateBase}=fixture();
  const edits:Array<(p:AnalysisPayload)=>void>=[
    p=>{p.singles.find(r=>r.level===20)!.scoringVariants!.ss!.estimatedDifficulty!+=.1;},
    p=>{p.singles.find(r=>r.level===20)!.scoringVariants!.sss!.difficultyCi95High!+=.1;},
    p=>{p.singles.find(r=>r.level===20)!.pumbilityVariants!.sssplus!.estimatedDifficulty!+=.1;},
    p=>{p.singles[0].scoringVariants!.sss!.modeRank=999;},
    p=>{obj(p.summary.method.level20ScoringWidth).policy.targetCoreFraction=.8;},
    p=>{obj(p.summary.method.level20ScoringWidth).folders["Single:ss:20"].after.coreCharts++;},
    p=>{delete p.singles.find(r=>r.level===20)!.scoringVariants!.sss!.scoringLevel20Width;},
    p=>{p.singles.find(r=>r.level!==20)!.scoringVariants!.ss!.scoringLevel20Width=p.singles.find(r=>r.level===20)!.scoringVariants!.ss!.scoringLevel20Width;},
  ];
  for(const edit of edits) {
    const changed=structuredClone(candidate);edit(changed);
    assert.throws(()=>validateLevel20WidthAnalysisPayload(changed,validateBase));
    assert.throws(()=>compactTierPayload(changed));
  }
  const privacy=structuredClone(candidate);
  obj(privacy.singles.find(r=>r.level20WidthBaseline)!.level20WidthBaseline).playerId="private";
  assert.throws(()=>validateLocalAnalysisPayload(privacy,"combined"),/private/);
});

test("schema60 keeps selected goal limited-data warnings at nine and ten records",()=>{
  const {candidate}=fixture();
  for(const goal of GOALS)for(const count of [9,10]) {
    const row=structuredClone(candidate.singles[0]);
    Object.assign(row.scoringVariants![goal]!,{nContributors:count,scoringMethod:goal==="sss"?"sss-target-rasch-v1":"target-attainment-rasch-v1"});
    row.tierMetrics!.clearing.ratedClearCount=10;row.tierMetrics!.clearing.selectedCount=10;
    assert.equal(hasLimitedTierData(projectScoringGoal(row,goal),"scoring"),count===9);
  }
});
