import assert from "node:assert/strict";
import test from "node:test";
import { chartBpmRange, clampBpmRange, formatTierScore, matchesTierBpm, tierBpmBounds } from "../lib/tier-filters.ts";

test("personal tier scores truncate thousands including a perfect score", () => {
  assert.equal(formatTierScore(996999), "996k");
  assert.equal(formatTierScore(923001), "923k");
  assert.equal(formatTierScore(999999), "999k");
  assert.equal(formatTierScore(1000000), "1000k");
  assert.equal(formatTierScore(0), "0k");
});

test("BPM selection contains the complete chart interval, inclusively", () => {
  const range = { min: 120, max: 130 };
  assert.equal(matchesTierBpm({ bpmMin: 120, bpmMax: 130 }, range), true);
  assert.equal(matchesTierBpm({ bpmMin: 125, bpmMax: 125 }, range), true);
  assert.equal(matchesTierBpm({ bpmMin: 110, bpmMax: 130 }, range), false);
  assert.equal(matchesTierBpm({ bpmMin: 120, bpmMax: 131 }, range), false);
  assert.equal(matchesTierBpm({ bpmMin: 119.999, bpmMax: 130 }, range), false);
  assert.equal(matchesTierBpm({ bpmMin: 120, bpmMax: 130.001 }, range), false);
  assert.equal(matchesTierBpm({ bpmMin: 150.5, bpmMax: 169.75 }, { min: 150, max: 170 }), true);
});

test("unknown BPM remains visible for All and is excluded for an active range", () => {
  for (const chart of [{}, { bpmMin: null, bpmMax: null }, { bpmMin: 120 }, { bpmMin: 0, bpmMax: 0 }, { bpmMin: 130, bpmMax: 120 }, { bpmMin: NaN, bpmMax: 120 }]) {
    assert.equal(chartBpmRange(chart), null);
    assert.equal(matchesTierBpm(chart, null), true);
    assert.equal(matchesTierBpm(chart, { min: 0, max: 9999 }), false);
  }
});

test("slider bounds surround fractional data without rounding chart comparisons", () => {
  assert.deepEqual(tierBpmBounds([{ bpmMin: 119.5, bpmMax: 130.1 }, { bpmMin: 150, bpmMax: 170.25 }, {}]), { min: 119, max: 171 });
  assert.deepEqual(tierBpmBounds([{ bpmMin: 120, bpmMax: 120 }]), { min: 120, max: 121 });
  assert.equal(tierBpmBounds([{}]), null);
});

test("BPM bounds changes retain an ordered supported selection", () => {
  assert.deepEqual(clampBpmRange({ min: 100, max: 170 }, { min: 120, max: 160 }), { min: 120, max: 160 });
  assert.deepEqual(clampBpmRange({ min: 200, max: 220 }, { min: 100, max: 190 }), { min: 190, max: 190 });
});
