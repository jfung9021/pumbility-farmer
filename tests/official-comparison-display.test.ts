import assert from "node:assert/strict";
import test from "node:test";
import type { OfficialComparisonChart } from "../lib/official-comparison.ts";
import {
  comparisonFolderLevels,
  comparisonRows,
  comparisonSearchParams,
  comparisonSummary,
  comparisonViewFromSearchParams,
  compareEstimatedBuckets,
  formatComparisonDelta,
  formatComparisonScore,
  formatEstimatedPgGap,
  scaleEstimatedPgComparison,
} from "../lib/official-comparison-display.ts";

function chart(chartId: string, songName: string, options: Partial<OfficialComparisonChart> = {}): OfficialComparisonChart {
  return { chartId, songName, chartLabel: "S20", mode: "singles", level: 20, bpmMin: 150, bpmMax: 150, ...options };
}

test("score comparison starts from the complete alphabetical folder, retaining charts without either score", () => {
  const charts = [
    chart("z", "Zebra"),
    chart("b", "Beta"),
    chart("a", "Alpha"),
    chart("d", "Other mode", { mode: "doubles", chartLabel: "D20" }),
    chart("l", "Other level", { level: 21, chartLabel: "S21" }),
  ];
  const rows = comparisonRows(charts, "singles", 20, { a: 998523 }, { a: 997100, z: 1000000 });
  assert.deepEqual(rows.map((row) => row.chart.songName), ["Alpha", "Beta", "Zebra"]);
  assert.deepEqual(rows.map(({ leftScore, rightScore, delta, winner }) => ({ leftScore, rightScore, delta, winner })), [
    { leftScore: 998523, rightScore: 997100, delta: 1423, winner: "left" },
    { leftScore: null, rightScore: null, delta: null, winner: null },
    { leftScore: null, rightScore: 1000000, delta: null, winner: null },
  ]);
  assert.equal(charts[0].chartId, "z", "Sorting must not mutate the catalog");
});

test("recorded zero scores participate in comparisons while missing entries never become losses", () => {
  const rows = comparisonRows([chart("zero", "A"), chart("tie", "B"), chart("missing", "C"), chart("right", "D")], "singles", 20,
    { zero: 0, tie: 1000000, missing: 900000, right: 999999 },
    { zero: 10, tie: 1000000, right: 1000000 });
  assert.equal(rows[0].leftScore, 0);
  assert.equal(rows[0].delta, -10);
  assert.equal(rows[1].winner, "tie");
  assert.equal(rows[2].winner, null);
  assert.deepEqual(comparisonSummary(rows), { charts: 4, leftRecorded: 4, rightRecorded: 3, leftWins: 0, rightWins: 2, ties: 1, comparable: 3 });
});

test("same-title charts remain distinct and use chart label then chart ID as stable tie breakers", () => {
  const rows = comparisonRows([chart("b", "Same", { chartLabel: "S20 B" }), chart("a2", "same"), chart("a1", "Same"), chart("x", "A")], "singles", 20);
  assert.deepEqual(rows.map((row) => row.chart.chartId), ["x", "a1", "a2", "b"]);
  assert.equal(rows.length, 4);
});

test("difficulty options derive from the catalog and title search retains unplayed matches", () => {
  const charts = [chart("a", "Alpha"), chart("b", "Beta"), chart("c", "Alpha long", { level: 22 }), chart("d", "Double", { mode: "doubles", level: 21 }), chart("e", "Alpha again")];
  assert.deepEqual(comparisonFolderLevels(charts, "singles"), [20, 22]);
  assert.deepEqual(comparisonFolderLevels(charts, "doubles"), [21]);
  assert.deepEqual(comparisonRows(charts, "singles", 20, {}, {}, " ALPHA ").map((row) => row.chart.chartId), ["a", "e"]);
});

test("URL state round-trips players, independent analysis and folder choices, and chart search", () => {
  const defaults = { left: "", right: "", mode: "singles", level: 20, bpm: 0, contributorMode: "singles", q: "" };
  assert.deepEqual(comparisonViewFromSearchParams(new URLSearchParams()), defaults);
  const view = { left: "op_left", right: "op_right", mode: "doubles" as const, level: 24, bpm: 5, contributorMode: "singles" as const, q: "Love & π + #1" };
  const params = comparisonSearchParams(view, new URLSearchParams("left=old&source=share"));
  assert.deepEqual(comparisonViewFromSearchParams(params), view);
  assert.equal(params.get("source"), "share");
  const swapped = { ...view, left: view.right, right: view.left };
  assert.deepEqual(comparisonViewFromSearchParams(comparisonSearchParams(swapped)), swapped);
  assert.equal(comparisonSearchParams({ ...view, left: "" }, params).has("left"), false);
  assert.deepEqual(comparisonViewFromSearchParams(new URLSearchParams("mode=coop&level=19")), defaults);
  assert.equal(comparisonViewFromSearchParams(new URLSearchParams("level=NaN")).level, 20);
  assert.equal(comparisonViewFromSearchParams(new URLSearchParams("level=20.5")).level, 20);
});

test("legacy links keep their folder and default to the first Singles analysis without a search", () => {
  assert.deepEqual(comparisonViewFromSearchParams(new URLSearchParams("left=op_a&right=op_b&mode=doubles&level=29")), {
    left: "op_a", right: "op_b", mode: "doubles", level: 29, bpm: 0, contributorMode: "singles", q: "",
  });
});

test("invalid analysis selections fall back without discarding valid folder or search state", () => {
  for (const bpm of ["-1", "9", "1.5", "NaN", "Infinity"]) {
    const view = comparisonViewFromSearchParams(new URLSearchParams({ bpm, contributorMode: "coop", mode: "doubles", level: "25", q: "Alpha" }));
    assert.equal(view.bpm, 0);
    assert.equal(view.contributorMode, "singles");
    assert.equal(view.mode, "doubles");
    assert.equal(view.level, 25);
    assert.equal(view.q, "Alpha");
  }
  assert.equal(comparisonViewFromSearchParams(new URLSearchParams("bpm=8&contributorMode=doubles")).bpm, 8);
});

test("changing analysis preserves the score folder, and clearing search removes stale query text", () => {
  const folder = comparisonViewFromSearchParams(new URLSearchParams("mode=singles&level=20&q=old&source=share"));
  const params = comparisonSearchParams({ ...folder, bpm: 5, contributorMode: "doubles", q: "" }, new URLSearchParams("q=old&source=share"));
  assert.deepEqual(comparisonViewFromSearchParams(params), { ...folder, bpm: 5, contributorMode: "doubles", q: "" });
  assert.equal(params.has("q"), false);
  assert.equal(params.get("source"), "share");
  const changedFolder = comparisonViewFromSearchParams(comparisonSearchParams({ ...comparisonViewFromSearchParams(params), mode: "doubles", level: 29 }));
  assert.equal(changedFolder.bpm, 5);
  assert.equal(changedFolder.contributorMode, "doubles");
});

test("score and difference labels preserve full scores and literal missing-score hyphens", () => {
  assert.equal(formatComparisonScore(null), "--");
  assert.equal(formatComparisonScore(0), "0");
  assert.equal(formatComparisonScore(1000000), "1,000,000");
  assert.equal(formatComparisonDelta(null), "--");
  assert.equal(formatComparisonDelta(1423), "+1,423");
  assert.equal(formatComparisonDelta(-1423), "-1,423");
  assert.equal(formatComparisonDelta(0), "0");
});

test("BPM winners use full aligned PG precision, including differences hidden by rounding and radar clipping", () => {
  const left = { pg: 26.001 };
  const right = { pg: 26.002 };
  const outcome = compareEstimatedBuckets(left, right);
  assert.equal(outcome.winner, "right");
  assert.equal(formatEstimatedPgGap(outcome.gap!), "<0.01 PG");
  assert.equal(compareEstimatedBuckets({ pg: 18.5 }, { pg: 18.5 }).winner, "tie");
  assert.deepEqual(compareEstimatedBuckets({ pg: 18.5 }, { pg: 17.25 }), { winner: "left", gap: 1.25 });
  assert.equal(formatEstimatedPgGap(1.25), "1.25 PG");
});

test("an unrecorded BPM estimate never becomes a loss or a tie", () => {
  assert.deepEqual(compareEstimatedBuckets(undefined, { pg: 20 }), { winner: null, gap: null });
  assert.deepEqual(compareEstimatedBuckets({ pg: null }, { pg: 20 }), { winner: null, gap: null });
  assert.deepEqual(compareEstimatedBuckets({ pg: null }, { pg: null }), { winner: null, gap: null });
  assert.deepEqual(compareEstimatedBuckets({ pg: 20 }, undefined), { winner: null, gap: null });
});

test("arrows use a fixed linear 1 PG half-scale and mirror when players swap", () => {
  for (const [gap, fraction, capped] of [
    [0.001, 0.001, false], [0.02, 0.02, false], [0.25, 0.25, false],
    [0.5, 0.5, false], [1, 1, false], [1.25, 1, true],
  ] as const) {
    const left = { pg: 20 + gap };
    const right = { pg: 20 };
    const forward = scaleEstimatedPgComparison(left, right);
    const reverse = scaleEstimatedPgComparison(right, left);
    assert.equal(forward.winner, "left");
    assert.equal(reverse.winner, "right");
    assert.ok(Math.abs(forward.fraction! - fraction) < 1e-12);
    assert.equal(reverse.fraction, forward.fraction);
    assert.equal(forward.capped, capped);
    assert.equal(reverse.capped, capped);
    assert.ok(Math.abs(forward.gap! - gap) < 1e-12, "Capping the arrow must not cap its numeric gap");
  }
});

test("ties and missing estimates stay distinct on the arrow scale", () => {
  assert.deepEqual(scaleEstimatedPgComparison({ pg: 20 }, { pg: 20 }), { winner: "tie", gap: 0, fraction: 0, capped: false });
  for (const left of [undefined, { pg: null }]) {
    assert.deepEqual(scaleEstimatedPgComparison(left, { pg: 20 }), { winner: null, gap: null, fraction: null, capped: false });
    assert.deepEqual(scaleEstimatedPgComparison({ pg: 20 }, left), { winner: null, gap: null, fraction: null, capped: false });
  }
});

test("arrow geometry uses unrounded PG precision and caps only above exactly 1 PG", () => {
  const tiny = scaleEstimatedPgComparison({ pg: 26.001 }, { pg: 26.002 });
  assert.ok(Math.abs(tiny.fraction! - 0.001) < 1e-12);
  assert.equal(formatEstimatedPgGap(tiny.gap!), "<0.01 PG");
  assert.equal(scaleEstimatedPgComparison({ pg: 20.9999 }, { pg: 20 }).capped, false);
  assert.equal(scaleEstimatedPgComparison({ pg: 21 }, { pg: 20 }).capped, false);
  assert.equal(scaleEstimatedPgComparison({ pg: 21.0001 }, { pg: 20 }).capped, true);
});
