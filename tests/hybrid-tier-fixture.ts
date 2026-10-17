import { demoPayload } from "../lib/demo-data.ts";
import { COMBINED_MIX } from "../lib/mixes.ts";
import { HYBRID_CLEARING_POLICY, HYBRID_TIER_POLICY, SUBMITTED_PROFILE_POLICY_V2 } from "../lib/local-analysis.ts";
import { MAXIMUM_PUMBILITY_CALCULATION } from "../lib/pumbility-policy.ts";
import { compressDemoPayload, compressionPolicy } from "../lib/tier-postprocessing.ts";
import type { AnalysisPayload, ChartResult } from "../lib/types.ts";

/** Synthetic public aggregates: no player records, identities, or production values. */
export function hybridTierFixture(compressed = true): AnalysisPayload {
  const p = structuredClone(demoPayload);
  p.schemaVersion = 46; p.mix = COMBINED_MIX; p.coop = []; p.doubles = [];
  const dates = { Phoenix: "2026-10-01T00:00:00Z", Phoenix2: "2026-10-02T00:00:00Z" };
  const ids = (level: number) => Array.from({ length: 5 }, (_, i) => `synthetic-${level}-${i}`);
  const anchors = [20, 21, 22].map((level) => ({ level, threshold: level + 1, difficulty: level + .5, referenceCharts: 5, chartIds: ids(level) }));
  const lower = { level: 19, threshold: 21, referenceCharts: 5, chartIds: ids(19) };
  const ci = { method: "joint score-profile bootstrap with final affine calibration, holding the curve, folder center, and selected folder scale fixed; excludes scale-selection uncertainty",
    samples: 1000, minimumPlayers: 20, population: "observed successful-player best scores; excludes attempt and player-selection uncertainty" };
  p.summary.method = {
    scoring: { version: 1, population: "official-max-per-player-chart", calibration: "official-cross-level-median-anchored-exponential", minimumOfficialLevel: 20 },
    hybridTiers: { ...structuredClone(HYBRID_TIER_POLICY), submittedScoring: {
      method: structuredClone(SUBMITTED_PROFILE_POLICY_V2), confidenceIntervals: ci,
      scoreProfileCalibration: { folderScales: { Single: {19: .4}, Double: {} }, folderCenters: { Single: {19: 19.5}, Double: {} }, curves: { Single: null, Double: null } },
    } },
    officialCrossLevelScoring: { version: 1, minimumOfficialLevel: 20, sourceDates: dates, manifestSha256: "a".repeat(64), intervalMethod: "conditional-player-bootstrap-fixed-fit-v1",
      calibration: { Single: { version: 1, anchors: anchors.map((a) => ({ ...a, cost: 10000 * (a.level - 19) })), offset: 0,
        fit: {growth: 1, normalizedOffset: 0, normalizedAmplitude: 1, normalizedMse: 0, boundHit: false}, tailPolicy: "tangent-linear", supportedRange: [20.5, 22.5], referenceMinimumPlayers: 10, referenceMinimumCharts: 5 }, Double: null } },
    crossLevelClearing: { ...structuredClone(HYBRID_CLEARING_POLICY), baseScoringSchemaVersion: 42,
      sourceDates: {phoenix1: dates.Phoenix, phoenix2: dates.Phoenix2}, inputHashes: {phoenix1: "b".repeat(64), phoenix2: "c".repeat(64), currentCatalog: "d".repeat(64)},
      calibration: {Single: {version: 1, anchors, tailPolicy: "boundary-linear", supportedRange: [20.5, 22.5]}, Double: null}, lowerReferenceSkills: {Single: {19: lower}, Double: {}} },
    tierMetrics: {version: 12, clearing: structuredClone(HYBRID_CLEARING_POLICY), pumbility: {version: 2, aggregation: "maximum", requiresBoth: true, calculation: MAXIMUM_PUMBILITY_CALCULATION}},
  };
  p.singles = [19, 20, 21, 22].flatMap((level) => ids(level).map((chartId, i) => {
    const low = level < 20, midpoint = level + .5, value = low ? midpoint + .04 * (i - 2) : null;
    const clearingValue = low ? midpoint + .07 * (i - 2) : midpoint;
    const composite = value === null ? null : Math.max(value, clearingValue);
    return {
      ...structuredClone(demoPayload.singles[0]), chartId, songName: chartId, difficulty: `S${level}`, folder: `S${level}`, level,
      estimatedDifficulty: value, difficultyDelta: value === null ? null : value - midpoint, nContributors: low ? 30 : 0, nPlayersScored: low ? 30 : 0,
      evidenceStatus: low ? "Published" : "Unrated", levelRank: low ? i + 1 : null, levelComparisonCharts: low ? 5 : null, modeRank: low ? i + 1 : null,
      scoringMethod: low ? "submitted-score-profile-v2" : "official-cross-level-v1", scoringScoreProfile: low ? [970000, 980000, 990000] : null,
      scoringProfileMatchDifficulty: low ? 19.5 + .1 * (i - 2) : null, scoringFolderReferenceDifficulty: low ? 19.5 : null,
      scoringProfileMatchCi95Low: null, scoringProfileMatchCi95High: null, scoringDifficultyScale: low ? .4 : null,
      scoringSpreadCalibration: null, scoringPlayerCount: low ? undefined : 0, officialScoringContributors: 0,
      officialScoringEvidence: low ? null : {sourceDates: dates, sourceCounts: {Phoenix: 0, Phoenix2: 0}, sourceStates: {}, mergedPlayerCount: 0, comparability: "no-evidence", component: null, anchorEligible: false},
      scoringProvisional: !low, scoringUnratedReason: low ? null : "No official score evidence.", scoringChartCost: null, scoringChartCostCi95Low: null, scoringChartCostCi95High: null,
      difficultyCi95Low: null, difficultyCi95High: null, difficultyDeltaCi95Low: null, difficultyDeltaCi95High: null,
      tierMetrics: { clearing: {estimatedDifficulty: clearingValue, difficultyDelta: clearingValue - midpoint, effectBandRank: 4, effectBand: "Medium", levelRank: i + 1, levelComparisonCharts: 5,
        evidenceStatus: "Published", clearCount: 20, ratedClearCount: 20, missingSkillCount: 0, q10Skill: low ? 21 + .1 * (i - 2) : level + 1,
        sourceCounts: {phoenix1: 0, phoenix2: 20}, retainedSources: ["phoenix2"], comparability: "comparable", anchorEligible: true, qualificationExclusions: {},
        method: low ? "submitted-folder-centered-clearing-v1" : "submitted-cross-level-clearing-v2", folderReferenceSkill: low ? 21 : level + 1,
        supportedRange: low ? null : [20.5, 22.5], extrapolated: false, provisionalReasons: [], unratedReason: null, difficultyCi95Low: null, difficultyCi95High: null },
        pumbility: {estimatedDifficulty: composite, difficultyDelta: composite === null ? null : composite - midpoint, effectBandRank: low ? 4 : null, effectBand: low ? "Medium" : null,
          levelRank: low ? i + 1 : null, levelComparisonCharts: low ? 5 : null, evidenceStatus: low ? "Published" : "Unrated", aggregation: "maximum", scoringSupportCount: low ? 30 : 0, clearingSupportCount: 20} },
    } as ChartResult;
  }));
  if (!compressed) return p;
  const result = compressDemoPayload(p); result.schemaVersion = 47;
  result.summary.method.ratingPostprocessing = compressionPolicy(46);
  return result;
}
