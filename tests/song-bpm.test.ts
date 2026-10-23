import assert from "node:assert/strict";
import test from "node:test";
import { displayBpmRange, focalBpm, focalBpmBucket, formatDisplayBpm, requireFocalBpmMetadata } from "../lib/song-bpm.ts";

test("display BPM preserves complete corrected ranges and fractional precision independently of focal tempo", () => {
  assert.equal(formatDisplayBpm({bpmMin:100,bpmMax:225,bpmFocal:169}), "100–225 BPM");
  assert.equal(formatDisplayBpm({bpmMin:156.118,bpmMax:156.118,bpmFocal:156.118}, false), "156.118");
  assert.equal(formatDisplayBpm({bpmMin:81.5,bpmMax:81.5}), "81.5 BPM");
  assert.equal(formatDisplayBpm({}), "— BPM");
  assert.equal(displayBpmRange({bpmMin:225,bpmMax:100}), null);
  assert.equal(focalBpm({bpmMin:100,bpmMax:225}), null);
});

test("focal BPM categories keep exact half-open edges", () => {
  const cases = [[149.999,0],[150,1],[159.999,1],[160,2],[169,2],[169.999,2],[170,3],[219.999,7],[220,8],[240,8]];
  for (const [bpmFocal,bucket] of cases) assert.equal(focalBpmBucket({bpmMin:40,bpmMax:320,bpmFocal}), bucket);
  for (const bpmFocal of [null,NaN,Infinity,0,-1,321]) assert.equal(focalBpmBucket({bpmMin:40,bpmMax:320,bpmFocal}), null);
});

test("focal client requests require explicit versioned BPM metadata", () => {
  const metadata = {bpmMetadataVersion:1,bpmPolicy:"focal-v1",bpmMetadataRevision:"a".repeat(64)};
  assert.equal(requireFocalBpmMetadata(metadata), metadata);
  for (const value of [undefined,null,[],{}, {...metadata,bpmMetadataVersion:2}, {...metadata,bpmPolicy:"legacy"}, {...metadata,bpmMetadataRevision:"a".repeat(63)}, {...metadata,bpmMetadataRevision:"z".repeat(64)}]) {
    assert.throws(() => requireFocalBpmMetadata(value), /BPM metadata is unavailable or invalid/);
  }
});
