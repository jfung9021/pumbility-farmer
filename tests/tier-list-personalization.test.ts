import assert from "node:assert/strict";
import test from "node:test";
import { chartBpmRange, clampBpmRange, formatTierScore, matchesTierBpm, matchesTierLevel, tierBpmBounds } from "../lib/tier-filters.ts";

test("Only 20+ selects current official Singles and Doubles levels while composing with BPM and search", () => {
  const charts = [
    { songName: "Pavane", type: "Double", level: 23, bpmMin: 160, bpmMax: 160, bpmFocal: 160 },
    { songName: "Low", type: "Single", level: 19, bpmMin: 160, bpmMax: 160, bpmFocal: 160 },
    { songName: "Other", type: "Single", level: 20, bpmMin: 200, bpmMax: 200, bpmFocal: 200 },
    { songName: "Pavane Co-op", type: "CoOp", level: 3, bpmMin: 160, bpmMax: 160, bpmFocal: 160 },
  ];
  assert.deepEqual(charts.filter((chart) => matchesTierLevel(chart, "20+")).map((chart) => chart.songName), ["Pavane", "Other"]);
  assert.deepEqual(charts.filter((chart) => matchesTierLevel(chart, "20+")
    && matchesTierBpm(chart, { min: 150, max: 170 }) && chart.songName.toLowerCase().includes("pavane"))
    .map((chart) => chart.songName), ["Pavane"]);
  assert.equal(matchesTierLevel(charts[0], "23"), true);
  assert.equal(matchesTierLevel(charts[1], "20"), false);
  assert.equal(matchesTierLevel(charts[3], "All"), true);
  assert.equal(matchesTierLevel(charts[3], "3"), true);
});

test("personal tier scores truncate thousands including a perfect score", () => {
  assert.equal(formatTierScore(996999), "996k");
  assert.equal(formatTierScore(923001), "923k");
  assert.equal(formatTierScore(999999), "999k");
  assert.equal(formatTierScore(1000000), "1000k");
  assert.equal(formatTierScore(0), "0k");
});

test("BPM selection contains the focal tempo inclusively while preserving the display range", () => {
  const chart = { bpmMin: 100, bpmMax: 225, bpmFocal: 169 };
  assert.equal(matchesTierBpm(chart, { min: 160, max: 170 }), true);
  assert.equal(matchesTierBpm(chart, { min: 169, max: 169 }), true);
  assert.equal(matchesTierBpm(chart, { min: 170, max: 180 }), false);
  assert.deepEqual(chartBpmRange(chart), { min: 100, max: 225 });
  assert.equal(matchesTierBpm({ bpmMin: 150, bpmMax: 170, bpmFocal: 169.999 }, { min: 150, max: 169.999 }), true);
  assert.equal(matchesTierBpm({ bpmMin: 150, bpmMax: 170, bpmFocal: 169.999 }, { min: 170, max: 180 }), false);
  for (const bpmFocal of [null, NaN, Infinity, 0, 99, 226]) {
    assert.equal(matchesTierBpm({ ...chart, bpmFocal }, { min: 0, max: 9999 }), false);
  }
  assert.equal(matchesTierBpm({ bpmMin: 120, bpmMax: 120 }, { min: 0, max: 9999 }), false);
});

test("unknown BPM remains visible for All and is excluded for an active range", () => {
  for (const chart of [{}, { bpmMin: null, bpmMax: null }, { bpmMin: 120 }, { bpmMin: 0, bpmMax: 0 }, { bpmMin: 130, bpmMax: 120 }, { bpmMin: NaN, bpmMax: 120 }]) {
    assert.equal(chartBpmRange(chart), null);
    assert.equal(matchesTierBpm(chart, null), true);
    assert.equal(matchesTierBpm(chart, { min: 0, max: 9999 }), false);
  }
});

test("slider bounds surround fractional data without rounding chart comparisons", () => {
  assert.deepEqual(tierBpmBounds([{ bpmMin: 40, bpmMax: 300, bpmFocal: 119.5 }, { bpmMin: 150, bpmMax: 320, bpmFocal: 170.25 }, {}]), { min: 119, max: 171 });
  assert.deepEqual(tierBpmBounds([{ bpmMin: 120, bpmMax: 120, bpmFocal: 120 }]), { min: 120, max: 121 });
  assert.equal(tierBpmBounds([{}]), null);
});

test("BPM bounds changes retain an ordered supported selection", () => {
  assert.deepEqual(clampBpmRange({ min: 100, max: 170 }, { min: 120, max: 160 }), { min: 120, max: 160 });
  assert.deepEqual(clampBpmRange({ min: 200, max: 220 }, { min: 100, max: 190 }), { min: 190, max: 190 });
});
