import assert from "node:assert/strict";
import test from "node:test";
import { chartBpmRange, clampBpmRange, formatTierScore, matchesTierBpm, matchesTierLevel, matchesTierPhoenix2Debut, tierBpmBounds } from "../lib/tier-filters.ts";
import phoenix2DebutCharts from "../config/phoenix2-debut-charts.json" with { type: "json" };

test("Phoenix 2 chart debuts use explicit introduction metadata before the legacy UUID catalog", () => {
  const catalogChartId = phoenix2DebutCharts.chartIds[0]!;
  assert.ok(catalogChartId);
  assert.equal(matchesTierPhoenix2Debut({ chartId: catalogChartId }, true), true);
  assert.equal(matchesTierPhoenix2Debut({ chartId: "unknown-chart", originalMix: "Phoenix2" }, true), true);
  assert.equal(matchesTierPhoenix2Debut({ chartId: "unknown-chart" }, true), false);
  for (const originalMix of ["Phoenix", "XX", "Rise", "Unknown", ""]) {
    assert.equal(matchesTierPhoenix2Debut({ chartId: catalogChartId, originalMix }, true), false);
  }
});

test("turning off the Phoenix 2 debut filter retains every chart regardless of metadata", () => {
  for (const chart of [
    { chartId: phoenix2DebutCharts.chartIds[0]! },
    { chartId: "unknown-chart" },
    { chartId: "older-chart", originalMix: "Phoenix" },
    { chartId: "new-chart", originalMix: "Phoenix2" },
    { chartId: "future-chart", originalMix: "Rise" },
  ]) assert.equal(matchesTierPhoenix2Debut(chart, false), true);
});

test("Phoenix 2 chart debuts compose with level, BPM, and search including new charts for older songs", () => {
  const charts = [
    { chartId: "new-pavane", originalMix: "Phoenix2", songName: "Pavane", type: "Double", level: 23, bpmFocal: 160, bpmMin: 160, bpmMax: 160 },
    { chartId: "old-pavane", originalMix: "XX", songName: "Pavane", type: "Double", level: 23, bpmFocal: 160, bpmMin: 160, bpmMax: 160 },
    { chartId: "low-pavane", originalMix: "Phoenix2", songName: "Pavane", type: "Single", level: 19, bpmFocal: 160, bpmMin: 160, bpmMax: 160 },
    { chartId: "fast-pavane", originalMix: "Phoenix2", songName: "Pavane", type: "Double", level: 23, bpmFocal: 200, bpmMin: 200, bpmMax: 200 },
    { chartId: phoenix2DebutCharts.chartIds[0]!, songName: "Other", type: "Double", level: 23, bpmFocal: 160, bpmMin: 160, bpmMax: 160 },
  ];
  const selected = (newPhoenix2Only: boolean) => charts.filter(chart => matchesTierPhoenix2Debut(chart, newPhoenix2Only)
    && matchesTierLevel(chart, "20+") && matchesTierBpm(chart, { min: 150, max: 170 })
    && chart.songName.toLowerCase().includes("pavane")).map(chart => chart.chartId);
  assert.deepEqual(selected(true), ["new-pavane"]);
  assert.deepEqual(selected(false), ["new-pavane", "old-pavane"]);
});

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
