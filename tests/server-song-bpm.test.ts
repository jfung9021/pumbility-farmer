import assert from "node:assert/strict";
import test from "node:test";
import { enrichBpmPayload, legacyBpmPayload } from "../lib/server-song-bpm.ts";

function signedBase() {
  return {
    generatedAtUtc:"2026-10-01T00:00:00Z",
    summary:{method:{productionTierPublication:{version:1,method:"existing-model",fullArtifactSha256:"b".repeat(64),publicPayloadSha256:"a".repeat(64)}}},
    singles:[{chartId:"trans",type:"Single",songName:"Transmission",bpmMin:100,bpmMax:225,estimatedDifficulty:24.125,score:997000,plate:"UG"}],
  };
}

test("derived TS metadata views retain the signed base identity without claiming its signature", () => {
  const source = signedBase();
  const saved = structuredClone(source);
  const result = enrichBpmPayload(source) as unknown as Record<string, any>;
  assert.deepEqual(source,saved);
  assert.equal(result.summary.method.productionTierPublication,undefined);
  assert.deepEqual(result.summary.method.baseProductionTierPublication,source.summary.method.productionTierPublication);
  assert.equal(result.bpmMetadata.basePublicPayloadSha256,"a".repeat(64));
  assert.deepEqual(result.summary.method.bpmMetadata,result.bpmMetadata);
  assert.equal(result.singles[0].bpmFocal,169);
  for (const key of ["chartId","estimatedDifficulty","score","plate"]) assert.equal(result.singles[0][key],(source.singles[0] as Record<string,unknown>)[key]);
  assert.deepEqual(enrichBpmPayload(result),result);
});

test("legacy TS projections also identify publication evidence as belonging to their base", () => {
  const source = signedBase();
  for (const input of [source,enrichBpmPayload(source)]) {
    const legacy = legacyBpmPayload(input) as unknown as Record<string,any>;
    assert.equal(legacy.summary.method.productionTierPublication,undefined);
    assert.deepEqual(legacy.summary.method.baseProductionTierPublication,source.summary.method.productionTierPublication);
    assert.equal(legacy.bpmMetadata,undefined);
    assert.equal(legacy.summary.method.bpmMetadata,undefined);
    assert.equal(legacy.singles[0].bpmFocal,undefined);
    assert.equal(legacy.singles[0].estimatedDifficulty,24.125);
    assert.equal(legacy.singles[0].score,997000);
  }
});
