import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { SSSPLUS_LOCAL_RESULTS_PATH, CENTERED_SSS_LOCAL_RESULTS_PATH, localCombinedResultsPath, validateLocalAnalysisPayload } from "../lib/local-analysis.ts";
import { recoverSssPlusBaseline, targetTailRating, targetTailSelection, validateSssPlusAnalysisPayload } from "../lib/sssplus-analysis-validation.ts";
import { noSssBoundaryEndpoint } from "../lib/refined-analysis-validation.ts";
import type { AnalysisPayload, ChartResult } from "../lib/types";

const fixture = JSON.parse(await readFile(new URL("./fixtures/sssplus-target-tiers.json", import.meta.url), "utf8")) as { base: AnalysisPayload; candidate: AnalysisPayload };
const validate = (p: AnalysisPayload) => validateSssPlusAnalysisPayload(p, base => assert.deepEqual(base, fixture.base));

test("Python SSS+ fixture replays target calibration, refinement, centering, nonlinear intervals and means", () => {
  const p = structuredClone(fixture.candidate), before = structuredClone(p);
  assert.equal(validate(p), p);
  assert.deepEqual(p, before);
  assert.deepEqual(recoverSssPlusBaseline(p), fixture.base);
  const extreme = p.singles.find(row => Math.abs(row.scoringVariants!.sssplus!.scoringTailPolicy!.preTailDifficulty! - row.level - .5) > 2)!;
  assert.ok(Math.abs(extreme.scoringVariants!.sssplus!.estimatedDifficulty! - extreme.level - .5) < 1.9);
});

test("SSS+ rejects stale thresholds, fits, donors, post-tail values, intervals and component means", () => {
  const edits: Array<(p: AnalysisPayload) => void> = [
    p => { (p.summary.method.sssplusScoring as { target: number }).target = 990000; },
    p => { p.singles[0].scoringVariants!.sssplus!.scoringTarget = 990000; },
    p => { p.singles[0].scoringVariants!.sssplus!.estimatedDifficulty! += .1; },
    p => { p.singles[0].scoringVariants!.sssplus!.difficultyCi95Low! += .1; },
    p => { p.singles[0].pumbilityVariants!.sssplus!.estimatedDifficulty! += .1; },
    p => { p.singles[0].sssplusTailBaseline!.scoring.initialFit!.estimatedDifficulty = 100; },
    p => { p.singles[0].scoringVariants!.sssplus!.scoringTailPolicy!.exceptionSelected = true; },
    p => { (p.summary.method.sssplusTailPolicy as { selectedChartIds: string[] }).selectedChartIds = [p.singles[0].chartId]; },
    p => { p.singles.find(row => row.level >= 20)!.sssplusTailBaseline!.scoring.scoringFolderCentering!.finalShift += .1; },
    p => { p.singles[0].scoringVariants!.general.nContributors++; },
  ];
  for (const edit of edits) { const p = structuredClone(fixture.candidate); edit(p); assert.throws(() => validate(p)); }
});

test("schema52 privacy checks include every new snapshot before recovery", () => {
  const p = structuredClone(fixture.candidate);
  Object.assign(p.singles[0].sssplusTailBaseline!.scoring, { gameTag: "private" });
  assert.throws(() => validateLocalAnalysisPayload(p, "combined"), /private/);
});

test("target tail has the planned identity region, smooth join, signs and finite asymptote", () => {
  assert.equal(targetTailRating(null, 20), null);
  for (const delta of [-1.5, -1, 0, 1, 1.5]) assert.equal(targetTailRating(20.5 + delta, 20), 20.5 + delta);
  const deltas = [-1e300, -10, -2, -1.500001, -1.5, -1, 0, 1, 1.5, 1.500001, 2, 10, 1e300];
  const points = deltas.map(d => targetTailRating(20.5 + d, 20)!);
  points.forEach((value, i) => {
    assert.ok(Number.isFinite(value));
    assert.ok(Math.abs(value - 20.5) <= 1.9 + 1e-12);
    if (i) assert.ok(value >= points[i - 1]);
  });
  for (const delta of [.0001, 1, 1.5, 2, 20]) {
    assert.ok(Math.abs(targetTailRating(20.5 + delta, 20)! + targetTailRating(20.5 - delta, 20)! - 41) < 1e-12);
  }
  assert.ok(Math.abs(targetTailRating(22.000001, 20)! - 22.000001) < 1e-10);
  assert.equal(targetTailRating(100, 20, true), 23);
  assert.equal(targetTailRating(-100, 20, true), 18);
});

test("SSS+ local selection is explicit, isolated and keeps the schema51 rollback", () => {
  assert.equal(localCombinedResultsPath({ PIU_LOCAL_ANALYSIS: "1", PIU_LOCAL_SCORING_VARIANT_EXPERIMENT: "sssplus" }), SSSPLUS_LOCAL_RESULTS_PATH);
  assert.notEqual(localCombinedResultsPath({ PIU_LOCAL_SCORING_VARIANT_EXPERIMENT: "sssplus" }), SSSPLUS_LOCAL_RESULTS_PATH);
  assert.equal(localCombinedResultsPath({ PIU_LOCAL_ANALYSIS: "1", PIU_LOCAL_SCORING_VARIANT_EXPERIMENT: "sss-centered" }), CENTERED_SSS_LOCAL_RESULTS_PATH);
});

function candidate(id: string, level: number, delta: number, overrides: Record<string, unknown> = {}): ChartResult {
  const midpoint = level + .5;
  const metric = { estimatedDifficulty: midpoint + delta, scoringMethod: "target-attainment-rasch-v1", nContributors: 100,
    scoringTargetSuccesses: 50, scoringBelowTarget: 50, scoringFitConverged: true, scoringMainComponent: true,
    scoringComparability: "same", scoringProvisionalReasons: [], scoringPossiblyTruncated: false, scoringExtrapolated: false,
    scoringRefinement: { fullBoard: { possiblyTruncated: false }, range: { supported: true, kind: "observed" } },
    scoringFolderCentering: level >= 20 ? { basis: "folder" } : undefined,
    difficultyCi95Low: midpoint + delta - .05, difficultyCi95High: midpoint + delta + .05, ...overrides };
  return { chartId: id, level, sssplusTailBaseline: { scoring: metric } } as unknown as ChartResult;
}
function payload(singles: ChartResult[], doubles: ChartResult[] = []): AnalysisPayload { return { singles, doubles } as AnalysisPayload; }

test("tail exceptions share one ten-chart budget across modes, sources, folders and signs", () => {
  const singles: ChartResult[] = [], doubles: ChartResult[] = [];
  for (const [rows, level, prefix] of [[singles, 16, "ss"], [singles, 20, "so"], [doubles, 16, "ds"], [doubles, 20, "do"]] as const) {
    rows.push(candidate(prefix + "lo", level, -2.4), candidate(prefix + "mid", level, 0), candidate(prefix + "hi", level, 2.4));
  }
  singles.push(candidate("s21lo", 21, -2.45), candidate("s21mid", 21, 0), candidate("s21hi", 21, 2.45));
  doubles.push(candidate("d21lo", 21, -2.45), candidate("d21mid", 21, 0), candidate("d21hi", 21, 2.45));
  const selected = targetTailSelection(payload(singles, doubles));
  assert.equal(selected.selected.size, 10);
  assert.ok([...selected.selected].some(id => id.startsWith("ds")));
  assert.ok([...selected.selected].some(id => id.startsWith("so")));
  assert.ok(selected.selected.has("s21lo") && selected.selected.has("d21hi"));
  assert.ok([...selected.evidence.values()].some(e => (e.rejectionReasons as string[]).includes("global-exception-budget")));
});

test("tail selection keeps tied blocks whole, closes blocked tails and protects median ties", () => {
  const tied = Array.from({ length: 11 }, (_, i) => candidate(`t${i}`, 16, 2.4));
  const blocked = [candidate("outer", 17, 3, { scoringPossiblyTruncated: true }), candidate("inner", 17, 2.3)];
  const medians = [candidate("left", 20, -2.4), candidate("right", 20, 2.4)];
  const result = targetTailSelection(payload([...tied, ...blocked, ...medians]));
  assert.equal(result.selected.size, 0);
  assert.deepEqual(result.evidence.get("inner")!.rejectionReasons, ["ineligible-outer-chart-blocks-tail"]);
  assert.equal(result.evidence.get("left")!.protectedMedian, true);
  assert.equal(result.evidence.get("right")!.protectedMedian, true);
  assert.ok((result.evidence.get("t0")!.rejectionReasons as string[]).includes("global-exception-budget"));
});

test("new target top-three validation treats 994999 as below target and 995000 as success", () => {
  const full = { records: 2, topScores: [994999, 990000], highestScore: 994999, lowestScore: 990000 };
  const details = { endpointPolicy: "mean-top-3-available-v1", endpointScoreCount: 2 };
  assert.equal(noSssBoundaryEndpoint(full, details, 2, 2, 995000), 992499.5);
  assert.throws(() => noSssBoundaryEndpoint(full, details, 2, 2));
  assert.throws(() => noSssBoundaryEndpoint({ ...full, topScores: [995000, 990000], highestScore: 995000 }, details, 2, 2, 995000));
});
