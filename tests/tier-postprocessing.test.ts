import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { compressDemoPayload, compressDifficulty, recoverInitialFitPayload, compressionFactor } from "../lib/tier-postprocessing.ts";
import { demoPayload } from "../lib/demo-data.ts";
import { formatEstimatedDifficulty } from "../lib/format-difficulty.ts";
import type { AnalysisPayload } from "../lib/types.ts";

const fixture = JSON.parse(await readFile(new URL("./fixtures/midpoint-compressed-tier.json",import.meta.url),"utf8")) as AnalysisPayload;

test("midpoint processing preserves original calibration and reads historical generations",()=>{
  const p=validateLocalAnalysisPayload(fixture,"combined");
  assert.equal(p.schemaVersion,44);
  assert.equal(validateLocalAnalysisPayload(recoverInitialFitPayload(p),"combined").schemaVersion,41);
  for(const row of [...p.singles,...p.doubles]){
    assert.equal(row.estimatedDifficulty,compressDifficulty(row.initialFit!.estimatedDifficulty as number|null,row.level));
    assert.equal(row.tierMetrics!.pumbility.estimatedDifficulty,row.estimatedDifficulty===null||row.tierMetrics!.clearing.estimatedDifficulty===null?null:Math.max(row.estimatedDifficulty,row.tierMetrics!.clearing.estimatedDifficulty));
  }
});

test("wrong factor, stale values, interval tampering, missing fits and stale composites fail",()=>{
  for(const mutate of [
    (p:AnalysisPayload)=>{(p.summary.method.ratingPostprocessing as {factor:number}).factor=.25;},
    (p:AnalysisPayload)=>{p.singles[0].estimatedDifficulty=99;},
    (p:AnalysisPayload)=>{p.singles[0].difficultyCi95Low=99;},
    (p:AnalysisPayload)=>{delete p.singles[0].initialFit;},
    (p:AnalysisPayload)=>{p.singles[0].tierMetrics!.pumbility.estimatedDifficulty=99;},
    (p:AnalysisPayload)=>{[p.singles[0].modeRank,p.singles[1].modeRank]=[p.singles[1].modeRank,p.singles[0].modeRank];},
  ]){const p=structuredClone(fixture);mutate(p);assert.throws(()=>validateLocalAnalysisPayload(p,"combined"),/incompatible/);}
});

test("display truncates only after full precision processing and demo uses the same rule",()=>{
  assert.equal(formatEstimatedDifficulty(compressDifficulty(26.6,25)!),"26.0");
  assert.equal(formatEstimatedDifficulty(compressDifficulty(26.7,25)!),"26.1");
  assert.equal(compressDifficulty(null,25),null);
  const p=compressDemoPayload(demoPayload);
  assert.equal(p.singles[0].estimatedDifficulty,compressDifficulty(demoPayload.singles[0].estimatedDifficulty,p.singles[0].level,compressionFactor(p)));
  assert.equal(compressionFactor(p), .75);
  assert.deepEqual(compressDemoPayload(p), p);
  for (const row of [...p.singles, ...p.doubles]) {
    for (const metric of [row, row.tierMetrics!.clearing, row.tierMetrics!.pumbility]) {
      const initial = metric.initialFit!;
      assert.equal(metric.estimatedDifficulty, compressDifficulty(initial.estimatedDifficulty as number | null, row.level, .75));
    }
  }
  assert.equal(formatEstimatedDifficulty(compressDifficulty(26.6,25,.75)!), "26.3");
  const invalid = structuredClone(p);
  (invalid.summary.method.ratingPostprocessing as {version: number}).version = 1;
  assert.throws(() => recoverInitialFitPayload(invalid), /Incompatible/);
});
