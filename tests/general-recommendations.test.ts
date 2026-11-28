import assert from "node:assert/strict";
import test from "node:test";

import {
  ALL_DIFFICULTIES,
  GENERAL_PUMBILITY_METHOD,
  RECOMMENDATION_RANKING_METHOD,
  recommendationDifficultyOptions,
  recommendationScoreProgress,
  visibleRecommendations,
} from "../lib/recommendation-filters.ts";
import {
  recommendationPlayerList,
  recommendationsForMode,
  recommendationsForPlayer,
  recommendationsForRating,
  validateLocalRecommendationIndex,
} from "../lib/local-recommendations.ts";
import type {
  RecommendationChart,
  RecommendationModeResult,
  RecommendationPlayer,
  RecommendationTopScore,
} from "../lib/types.ts";

function chart(id: string, level: number, gain = 10, expected = 400): RecommendationChart {
  return {
    mode: "Singles", songName: id, difficulty: `S${level}`, type: "Single", level,
    chartId: id, imageUrl: null, noteCount: null, stepArtist: null,
    estimatedDifficulty: null, difficultyDelta: null,
    difficultyCi95Low: null, difficultyCi95High: null,
    nContributors: null, phoenix1Contributors: null, phoenix2Contributors: null,
    evidenceStatus: null, distanceFromRating: null, farmEdge: null,
    existingPumbility: null, expectedPumbility: expected, projectedGain: gain,
    projectedScore: 995_000, projectedGrade: "SSS+", projectedPlate: "Fair Game",
    projectedPlateCode: "FG", projectedPlateProbability: null,
    plateProjectionSource: "piuscores-peer-median", played: false,
    rawPercentileScore: 995_000.5, peerScoreCount: 2, peerPairedScoreCount: 2,
    peerMissingPlateCount: 1, peerRawScoreCount: 3,
    cohortPlayerCount: 10, limitedData: true, sourceMode: "singles",
    scoreAtOrAboveTargetCount: 1, gainRank: 1, scoreCountRank: 1, rankScore: 2,
  };
}

function mode(candidates: RecommendationChart[], count = 50): RecommendationModeResult {
  const ranked = candidates.map((row, index) => ({ ...row, recommendationRank: row.recommendationRank ?? index + 1 }));
  return {
    skillMethod: GENERAL_PUMBILITY_METHOD, skillSource: "piuscores-submitted",
    rankingMethod: RECOMMENDATION_RANKING_METHOD,
    eligible: count >= 50, validScoreCount: count, requiredScoreCount: 50,
    status: count >= 50 ? "ready" : "insufficient-data",
    modePumbility: count >= 50 ? 20_000 : null,
    currentTop50Pumbility: 20_000, currentTop50Count: Math.min(count, 50),
    topScores: [], topRecommendations: ranked, filterCandidates: ranked,
    candidateCount: candidates.length,
    cohort: { minimumPlayers: 20, upwardWindow: 200, backfillCount: 0,
      playerCount: 20, limitedData: false },
  };
}

test("General mode filtering retains level 16, distant and unrated Pumbility opportunities", () => {
  const candidates = [chart("below", 15), chart("low", 16), chart("high", 28)];
  const result = mode(candidates);
  assert.equal(result.scoringRating, undefined);
  assert.deepEqual(recommendationDifficultyOptions("singles", candidates, undefined,
    result.skillMethod), ["S16", "S28"]);
  assert.deepEqual(visibleRecommendations("singles", result, ALL_DIFFICULTIES)
    .map(row => row.chartId), ["low", "high"]);
  assert.deepEqual(visibleRecommendations("singles", result, "S28")
    .map(row => row.chartId), ["high"]);
  assert.equal(visibleRecommendations("doubles", result, ALL_DIFFICULTIES).length, 0);
});

test("General difficulty filtering preserves full-pool combined ranks ahead of gain-only order", () => {
  const candidates = [
    { ...chart("highest-gain", 28, 100), scoreAtOrAboveTargetCount: 0, gainRank: 1, scoreCountRank: 4, rankScore: 5, recommendationRank: 11 },
    { ...chart("supported", 28, 90), scoreAtOrAboveTargetCount: 10, gainRank: 2, scoreCountRank: 1, rankScore: 3, recommendationRank: 7 },
    { ...chart("third", 28, 80), scoreAtOrAboveTargetCount: 8, gainRank: 3, scoreCountRank: 2, rankScore: 5, recommendationRank: 17 },
  ];
  assert.deepEqual(visibleRecommendations("singles", mode(candidates), "S28")
    .map(row => [row.chartId, row.recommendationRank]), [["supported", 7], ["highest-gain", 11], ["third", 17]]);
  assert.deepEqual(visibleRecommendations("singles", mode(candidates), ALL_DIFFICULTIES)
    .map(row => row.recommendationRank), [7, 11, 17]);
  const coop = { ...mode(candidates), filterCandidates: candidates.map(row => ({ ...row, type: "CoOp" as const, level: 3 })) };
  assert.deepEqual(visibleRecommendations("coop", coop, "3x").map(row => row.recommendationRank), [7, 11, 17]);
});

test("filtered recommendations retain global ranks beyond the default fifty-row display", () => {
  const candidates = Array.from({ length: 60 }, (_, index) => ({
    ...chart(`chart-${index}`, index < 50 ? 16 : 28), recommendationRank: index + 1,
  }));
  const result = mode(candidates);
  result.topRecommendations = result.topRecommendations.slice(0, 50);
  assert.equal(visibleRecommendations("singles", result, ALL_DIFFICULTIES).length, 50);
  assert.deepEqual(visibleRecommendations("singles", result, "S28").map(row => row.recommendationRank),
    [51, 52, 53, 54, 55, 56, 57, 58, 59, 60]);
});

test("local General projection preserves full candidates and distinct Overall gains", () => {
  const candidates = [chart("low", 16), chart("high", 28)];
  const singles = mode(candidates);
  const doubles = mode([], 49);
  const overall = mode([...candidates].reverse().map(row => ({ ...row, projectedGain: 3,
    modeProjectedGain: row.projectedGain!, replacedPumbility: 397 })));
  overall.sourceModeEligibility = { singles: true, doubles: false };
  const player: RecommendationPlayer = { playerKey: "opaque", username: "Test", displayName: "Test",
    modes: { singles, doubles, overall } };
  const index = { schemaVersion: 30, generatedAtUtc: "2026-11-28T00:00:00Z", method: {},
    charts: candidates, players: [player] };
  const response = recommendationsForPlayer(index, "opaque");
  assert.ok(response);
  assert.deepEqual(response.player.modes.singles?.filterCandidates?.map(row => row.level), [16, 28]);
  const scoped = recommendationsForMode(response, "overall", "S28");
  assert.deepEqual(Object.keys(scoped.player.modes), ["overall"]);
  const selected = scoped.player.modes.overall?.filterCandidates?.[0];
  assert.equal(selected?.estimatedDifficulty, null);
  assert.equal(selected?.rawPercentileScore, 995_000.5);
  assert.equal(selected?.projectedPlateCode, "FG");
  assert.equal(selected?.projectedPlate, "Fair Game");
  assert.equal(selected?.plateProjectionSource, "piuscores-peer-median");
  assert.equal(selected?.peerPairedScoreCount, 2);
  assert.equal(selected?.peerMissingPlateCount, 1);
  assert.equal(selected?.peerRawScoreCount, 3);
  assert.equal("assumedPlate" in selected!, false);
  assert.equal(selected?.projectedGain, 3);
  assert.equal(selected?.modeProjectedGain, 10);
  assert.equal(selected?.replacedPumbility, 397);
  assert.equal(selected?.recommendationRank, 1);
  assert.equal(response.player.modes.singles?.filterCandidates?.[1].recommendationRank, 2);
  assert.deepEqual(recommendationPlayerList(index).players[0].scoreProgress, {
    singles: { validScoreCount: 50, requiredScoreCount: 50 },
    doubles: { validScoreCount: 49, requiredScoreCount: 50 },
  });
});

test("General readiness ignores legacy skill windows and stale summary progress", () => {
  const result = { ...mode([], 49), projectionRatingRequiredScoreCount: 30,
    projectionRatingSourceScoreCount: 30, projectionAvailable: true };
  assert.deepEqual(recommendationScoreProgress(result,
    { validScoreCount: 30, requiredScoreCount: 30 }),
  { validScoreCount: 49, requiredScoreCount: 50 });
  assert.deepEqual(recommendationScoreProgress(mode([], 50)),
    { validScoreCount: 50, requiredScoreCount: 50 });
  assert.deepEqual(recommendationScoreProgress(undefined),
    { validScoreCount: 0, requiredScoreCount: 50 });
});

test("schema 29 retains known Pumbility history without a fabricated raw score or tier estimate", () => {
  const candidate = chart("history", 16);
  const topScore: RecommendationTopScore = { ...candidate, bpmMin: null, bpmMax: null,
    score: null, pumbility: 0, grade: null, plate: null, plateCode: null };
  const { distanceFromRating: _distance, farmEdge: _edge, existingPumbility: _existing,
    expectedPumbility: _expected, projectedGain: _gain, projectedScore: _score,
    projectedGrade: _grade, projectedPlate: _plate, projectedPlateCode: _plateCode,
    projectedPlateProbability: _probability, plateProjectionSource: _source, played: _played,
    rawPercentileScore: _median, peerScoreCount: _peerCount,
    peerPairedScoreCount: _pairs, peerMissingPlateCount: _missingPlates, peerRawScoreCount: _rawCount,
    cohortPlayerCount: _cohort, limitedData: _limited, sourceMode: _mode,
    scoreAtOrAboveTargetCount: _targetCount, gainRank: _gainRank, scoreCountRank: _countRank,
    rankScore: _rankScore, recommendationRank: _recommendationRank,
    ...publicTopScore } = topScore as RecommendationTopScore & RecommendationChart;
  const singles = { ...mode([candidate]), topScores: [publicTopScore] };
  const payload = { schemaVersion: 30, generatedAtUtc: "2026-11-28T00:00:00Z", method: {},
    charts: [candidate], players: [{ playerKey: "opaque", username: "Test", displayName: "Test",
      modes: { singles, doubles: mode([], 49) } }] };
  assert.equal(validateLocalRecommendationIndex(payload).schemaVersion, 30);
  const invalidRank = structuredClone(payload);
  invalidRank.players[0].modes.singles.topRecommendations[0].rankScore = 3;
  assert.throws(() => validateLocalRecommendationIndex(invalidRank));
  assert.throws(() => validateLocalRecommendationIndex({ ...payload, schemaVersion: 28 }));
  const manual = recommendationsForRating({ ...payload, players: [] }, 20);
  assert.equal(manual.player.modes.singles?.manual, true);
  assert.equal(manual.player.modes.singles?.filterCandidates?.length, 0);
});

test("sharded General index includes public chart annotations for the manual difficulty route", () => {
  const annotation = { ...chart("manual-chart", 20), estimatedDifficulty: 19.5 };
  const index = { schemaVersion: 30, storageSchemaVersion: 2,
    generationKey: "a".repeat(20), generatedAtUtc: "2026-11-28T00:00:00Z",
    method: { skillMethod: GENERAL_PUMBILITY_METHOD }, charts: [annotation],
    players: [{ playerKey: "opaque", username: "Test", displayName: "Test", shard: 0,
      eligibility: { singles: true, doubles: false, coop: false },
      scoreProgress: { singles: { validScoreCount: 50, requiredScoreCount: 50 } } }] };
  const validated = validateLocalRecommendationIndex(index);
  assert.equal(recommendationPlayerList(validated).players[0].eligibility.singles, true);
  assert.deepEqual(recommendationsForRating(validated, 20).player.modes.singles
    ?.topRecommendations.map(row => row.chartId), ["manual-chart"]);
  const { charts: _charts, ...missingCharts } = index;
  assert.throws(() => validateLocalRecommendationIndex(missingCharts));
});
