import type { CombinedMixInfo, MixInfo, MixKey } from "./mixes";

export type ModeKey = "singles" | "doubles" | "coop";
export type RecommendationModeKey = "overall" | ModeKey;
export type EvidenceStatus = "Published" | "Provisional" | "Insufficient" | "Unrated";
export type TierMetricKey = "scoring" | "clearing" | "pumbility";

export interface TierMetricResult {
  estimatedDifficulty: number | null;
  difficultyDelta: number | null;
  levelRank: number | null;
  levelComparisonCharts: number | null;
  effectBandRank: number | null;
  effectBand: string | null;
  evidenceStatus: EvidenceStatus;
}

export interface ClearingTierMetric extends TierMetricResult {
  clearCount: number;
  ratedClearCount: number;
  missingSkillCount: number;
  selectedCount?: number;
  skillMetric?: "official-clearer-ability";
  abilityCoverage?: number;
  shrinkageWeight?: number;
  spreadCalibration?: OfficialClearingSpread | null;
  extremeOutlier?: boolean;
  difficultyCi95Low?: number | null;
  difficultyCi95High?: number | null;
  q20SkillCi95Low?: number | null;
  q20SkillCi95High?: number | null;
  defaultedAtCap?: boolean;
  capDefaultDifficulty?: number | null;
  capDefaultBasis?: "uncapped-folder-minimum" | "folder-midpoint-no-uncapped-charts" | null;
  // q10Skill is a point percentile when no legacy range endpoint is present.
  q10Skill?: number | null;
  // Earlier point percentiles and ranges remain readable with their original labels.
  q20Skill?: number | null;
  q0Skill?: number | null;
  q25Skill?: number | null;
  q50Skill?: number | null;
  q30Skill?: number | null;
  meanSkill?: number | null;
  folderReferenceSkill?: number | null;
}

export interface OfficialClearingSpread {
  scale: number;
  referenceSpread: number | null;
  supportedCharts: number;
  lowerFence: number | null;
  upperFence: number | null;
  referenceLevel: number | null;
  basis: "folder" | "neighbor-folder" | "fallback";
}

export interface OfficialTierEvidence {
  source: "piuscores-official";
  asOf: string | null;
  rawRowCount: number;
  maxRank: number | null;
  possiblyTruncated: boolean;
  cutoffScore: number | null;
  status: "available" | "possibly-truncated" | "missing";
  unavailableReason: string | null;
}

export interface OfficialTierMethod {
  version: 15;
  enabled: true;
  source: "piuscores-official";
  mix: "Phoenix2";
  asOf: string | null;
  minimumLevels: { Single: 23; Double: 25 };
  capPolicy: "clearing-folder-minimum";
  scoring: {
    metric: "equal-player-score-gaps";
    minimumOtherCharts: 3;
    sparsePolicy: "provisional-same-level";
    aggregation: "equal-player-mean";
    calibration: {
      method: "shared-linear-player-gaps"; version: 6;
      minimumLevels: { Single: 23; Double: 25 };
      referenceQuantile: 0.9; targetHalfWidth: 0.45; maximumScale: 1; rangePolicy: "unbounded";
      minimumSupportedCharts: 8; minimumReferencePlayers: 10; outlierIqrMultiplier: 2;
      outlierUse: "diagnostic-only";
      folderScales: Record<"Single" | "Double", Record<string, number>>;
    };
  };
  clearing: {
    minimumLevels: { Single: 25; Double: 26 };
    skillMetric: "official-clearer-ability";
    difficultyDeltaScale: 0.70;
    normalization: "folder-median-player-ability";
    percentile: 0.20;
    playerSkill: { method: "leave-one-chart-out-top-official-levels"; topCharts: 25; minimumOtherCharts: 25; historyMinimumLevels: { Single: 22; Double: 23 } };
    shrinkage: { priorPlayers: 20 };
    calibration: { method: "robust-folder-spread"; version: 4;
      referenceQuantile: 0.9; targetHalfWidth: 0.45; maximumScale: null; rangePolicy: "unbounded";
      minimumSupportedCharts: 5; minimumReferencePlayers: 10; outlierUse: "diagnostic-only" };
  };
}

export interface OfficialScoringSpread {
  scale: number;
  referenceSpread: number | null;
  supportedCharts: number;
  lowerFence: number | null;
  upperFence: number | null;
  basis: "folder" | "neighbor-folder" | "fallback";
  referenceLevel: number | null;
}

export interface PumbilityTierMetric extends TierMetricResult {
  scoringSupportCount: number;
  clearingSupportCount: number;
}

export interface ChartRerate {
  from: string;
  to: string;
  delta: number;
  direction: "uprated" | "downrated";
  sourceRow: number;
}

export interface ChartResult {
  mode: "Singles" | "Doubles" | "Co-op";
  modeRank: number | null;
  levelRank: number | null;
  levelPercentile: number | null;
  levelComparisonCharts: number | null;
  folder: string;
  relativeGroupRank: number | null;
  relativeGroup: string | null;
  effectBandRank: number | null;
  effectBand: string | null;
  songName: string;
  difficulty: string;
  type: "Single" | "Double" | "CoOp";
  level: number;
  chartId: string;
  imageUrl: string | null;
  noteCount: number | null;
  stepArtist: string | null;
  bpmMin?: number | null;
  bpmMax?: number | null;
  estimatedDifficulty: number | null;
  officialEvidence?: OfficialTierEvidence;
  officialContributors?: number;
  scoringPlayerCount?: number;
  scoringMinimumOtherCharts?: number;
  scoringComparisonCharts?: number;
  scoringComponentCount?: number;
  scoringProvisional?: boolean;
  scoringMeanGap?: number | null;
  scoringGapStdDev?: number | null;
  scoringGapCi95Low?: number | null;
  scoringGapCi95High?: number | null;
  scoringUnratedReason?: string | null;
  scoringPointsPerLevel?: number | null;
  scoringSpreadCalibration?: OfficialScoringSpread | null;
  scoringExtremeOutlier?: boolean;
  tierMetrics?: {
    clearing: ClearingTierMetric;
    pumbility: PumbilityTierMetric;
  };
  scoringScoreProfile?: [number, number, number, number, number] | null;
  scoringProfileMatchDifficulty?: number | null;
  scoringFolderReferenceDifficulty?: number | null;
  scoringDifficultyScale?: number | null;
  scoringProfileMatchCi95Low?: number | null;
  scoringProfileMatchCi95High?: number | null;
  scoringProfileRmse?: number | null;
  scoringProfileExtrapolated?: boolean;
  difficultyModelContinuous?: number | null;
  difficultyModelSignal?: number | null;
  difficultyModelSupportCount?: number | null;
  percentileScore?: number | null;
  percentileGrade?: string | null;
  percentilePlate?: string | null;
  percentilePlateCode?: string | null;
  percentileSupportCount?: number | null;
  whatIfEstimates?: Array<{
    level: number;
    estimatedDifficulty: number | null;
  }> | null;
  averageDifficulty: number | null;
  difficultyDelta: number | null;
  folderMeasuredCharts?: number | null;
  folderRangeCompression?: number | null;
  difficultyDeltaCi95Low: number | null;
  difficultyDeltaCi95High: number | null;
  difficultyCi95Low: number | null;
  difficultyCi95High: number | null;
  nContributors: number;
  nPlayersScored: number;
  phoenix1Contributors?: number;
  phoenix2Contributors?: number;
  evidenceStatus: EvidenceStatus;
  phoenix2Rerate?: ChartRerate;
}

export interface FolderSummary {
  catalogCharts: number;
  measuredCharts: number;
  publishedCharts: number;
  medianContributors: number | null;
  rangeCompression?: number;
  overratedCharts?: number;
  underratedCharts?: number;
}

export interface CoopDifficultyModelSummary {
  difficultyModel: string;
  difficultyTransform: string;
  difficultyConditionalQuantile: number;
  difficultyReferenceAbilityPercentile: number;
  difficultyReferenceSource: "phoenix2";
  difficultyCalibrationAnchors: {
    easiest: 10;
    median: 17;
    hardest: 25;
  };
  abilityCoverageObservations: number;
  abilitySameSourceObservations: number;
  abilityOppositeSourceObservations: number;
  abilityMedianFallbackObservations: number;
  difficultyFitObservations: number;
  difficultyResidualRefitIterations: 0;
  abilityCoefficients: number[];
  phoenix2SourceCoefficient: number;
}

export interface ModeSummary {
  eligiblePlayers: number;
  catalogCharts: number;
  measuredCharts: number;
  publishedCharts: number;
  pumbilityPerLevel?: number | null;
  calibration: Record<string, unknown>;
  difficultyModel?: CoopDifficultyModelSummary;
  shrinkage?: Record<string, unknown>;
  folders: Record<string, FolderSummary>;
}

export interface AnalysisPayload {
  schemaVersion?: number;
  generatedAtUtc: string;
  mix: MixInfo | CombinedMixInfo;
  summary: {
    scriptVersion: string;
    method: Record<string, unknown> & { officialTiers?: OfficialTierMethod };
    coverage: Record<string, number>;
    modes: Partial<Record<ModeKey, ModeSummary>>;
  };
  singles: ChartResult[];
  doubles: ChartResult[];
  coop?: ChartResult[];
  relativeGroups: Array<{ rank: number; name: string }>;
  effectBands: Array<{
    rank: number;
    name: string;
    low: number | null;
    high: number | null;
  }>;
}

export type AnalysisJobState = "queued" | "running" | "completed" | "failed";
export type AnalysisJobStage = "discovering" | "syncing" | "analyzing" | "publishing";

export interface AnalysisJobStatus {
  id: string;
  status: AnalysisJobState;
  stage: AnalysisJobStage;
  progress: {
    current: number;
    total: number;
    percent: number;
    message: string;
  };
  createdAtUtc: string;
  updatedAtUtc: string;
  startedAtUtc: string | null;
  completedAtUtc: string | null;
  generatedAtUtc: string | null;
  retryAllowedAtUtc: string | null;
  error: string | null;
  mix: MixKey;
}

export type AnalysisRefreshResponse =
  | {
      outcome: "fresh";
      generatedAtUtc: string;
      nextAllowedAtUtc: string;
    }
  | {
      outcome: "busy";
      activeMix: MixKey;
      error: string;
    }
  | {
      outcome: "started" | "existing";
      job: AnalysisJobStatus;
    };

export interface RecommendationChartEstimate {
  mode: "Singles" | "Doubles" | "Co-op";
  songName: string;
  difficulty: string;
  type: "Single" | "Double" | "CoOp";
  level: number;
  chartId: string;
  imageUrl: string | null;
  noteCount: number | null;
  stepArtist: string | null;
  bpmMin?: number | null;
  bpmMax?: number | null;
  estimatedDifficulty: number;
  difficultyModelContinuous?: number | null;
  difficultyModelSignal?: number | null;
  percentileScore?: number | null;
  percentileGrade?: string | null;
  percentilePlate?: string | null;
  percentilePlateCode?: string | null;
  percentileSupportCount?: number | null;
  difficultyDelta: number | null;
  difficultyCi95Low: number | null;
  difficultyCi95High: number | null;
  nContributors: number;
  phoenix1Contributors: number;
  phoenix2Contributors: number;
  evidenceStatus: EvidenceStatus;
}

export interface RecommendationChart extends RecommendationChartEstimate {
  distanceFromRating: number;
  farmEdge: number;
  existingPumbility: number | null;
  expectedPumbility: number | null;
  existingCoopRating?: number | null;
  expectedCoopRating?: number | null;
  projectedGain: number | null;
  projectedScore: number | null;
  projectedGrade: string | null;
  projectedPlate: string | null;
  projectedPlateCode: string | null;
  projectedPlateProbability: number | null;
  plateProjectionSource: "phoenix1" | "phoenix2" | "population" | "fixed-fair-game" | null;
  scoreProjectionSource?: string | null;
  scoreProjectionSupportCount?: number | null;
  scoreProjectionConfidence?: "high" | "medium" | "low" | "limited" | "unavailable";
  played: boolean;
}

export interface RecommendationTopScore {
  mode: "Singles" | "Doubles" | "Co-op";
  songName: string;
  difficulty: string;
  type: "Single" | "Double" | "CoOp";
  level: number;
  chartId: string;
  imageUrl: string | null;
  noteCount: number | null;
  stepArtist: string | null;
  bpmMin: number | null;
  bpmMax: number | null;
  estimatedDifficulty: number | null;
  difficultyDelta: number | null;
  difficultyCi95Low: number | null;
  difficultyCi95High: number | null;
  nContributors: number | null;
  phoenix1Contributors: number | null;
  phoenix2Contributors: number | null;
  evidenceStatus: EvidenceStatus | null;
  /** Present on recommendation schema 26+ public Top 50 rows. */
  score?: number;
  pumbility?: number | null;
  coopRating?: number | null;
  grade: string | null;
  plate: string | null;
  plateCode: string | null;
}

export interface ClearingSkillMetadata {
  methodVersion: 2;
  difficultyBasis: "current-official-level";
  ranks: [1, 50];
  requiredClearCount: 50;
  uniqueClearCount: number;
  selectedCount: 0 | 50;
  status: "rated" | "insufficient-clears";
}

export interface RecommendationModeResult {
  eligible: boolean;
  manual?: boolean;
  validScoreCount: number;
  requiredScoreCount?: number;
  phoenix2ScoreCount?: number;
  phoenix2ScoreThreshold?: number;
  ratingSource?: "phoenix1" | "phoenix2" | null;
  ratingSourceScoreCount?: number;
  ratingBaselineRanks?: [number, number];
  ratingBaselineLabel?: string;
  reason?: string;
  baselineRanks?: [number, number];
  baselineLabel?: string;
  baselinePumbility?: number | null;
  scoringRating?: number;
  clearingRating?: number | null;
  clearingSkill?: ClearingSkillMetadata;
  projectionRating?: number | null;
  projectionRatingSource?: "phoenix1" | "phoenix2" | null;
  projectionRatingSourceScoreCount?: number;
  projectionRatingRequiredScoreCount?: number;
  projectionRatingRanks?: [number, number];
  projectionRatingLabel?: string;
  ratingReferenceGrade?: string;
  ratingReferencePlate?: string;
  ratingReferenceMultiplier?: number;
  projectionAvailable?: boolean;
  scoreProjectionModel?:
    | "population-crossfit-monotone-v1"
    | "population-crossfit-monotone-v2"
    | "similar-skill-top100-q75-v1"
    | "similar-skill-top100-q50-v1"
    | "similar-skill-top100-q50-v2"
    | "similar-skill-staged-q50-v3"
    | "similar-skill-all-q50-v4"
    | "similar-skill-all-q50-v5"
    | "similar-skill-pumbility-11-30-q50-v6"
    | "similar-skill-pumbility-11-30-weighted-q50-v8"
    | "similar-skill-pumbility-11-30-weighted-q50-v9"
    | "chart-population-q75-v1"
    | "estimated-difficulty-master-grade-ladder-v1"
    | "estimated-difficulty-master-grade-ladder-v2"
    | "estimated-difficulty-master-grade-ladder-v3"
    | "estimated-difficulty-master-grade-ladder-v4"
    | "estimated-difficulty-master-grade-ladder-v5";
  pumbilityPerLevel?: number | null;
  currentTop50Pumbility?: number;
  currentTop50CutoffPumbility?: number | null;
  currentTop50Count?: number;
  currentCoopRating?: number;
  top50ModeCounts?: Record<Exclude<ModeKey, "coop">, number>;
  sourceModeEligibility?: Record<Exclude<ModeKey, "coop">, boolean>;
  sourceRecommendationCounts?: Record<Exclude<ModeKey, "coop">, number>;
  candidateRange?: [number | null, number];
  candidateCount?: number;
  filterCandidateCount?: number;
  difficultyOptions?: string[];
  filterCandidates?: RecommendationChart[];
  topScores: RecommendationTopScore[];
  topRecommendations: RecommendationChart[];
}

export interface RecommendationScoreProgress {
  validScoreCount: number;
  requiredScoreCount: number;
}

export interface RecommendationPlayerSummary {
  playerKey: string;
  username: string;
  displayName: string;
  eligibility: Record<ModeKey, boolean>;
  scoreProgress?: Partial<Record<ModeKey, RecommendationScoreProgress>>;
}

export interface RecommendationPlayer {
  playerKey: string;
  username: string;
  displayName: string;
  manual?: boolean;
  modes: Record<Exclude<ModeKey, "coop">, RecommendationModeResult>
    & Partial<Record<"coop", RecommendationModeResult>>
    & Partial<Record<"overall", RecommendationModeResult>>;
}

export interface RecommendationPlayersResponse {
  generatedAtUtc: string;
  modelGeneratedAtUtc?: string;
  refreshSupported?: boolean;
  method: Record<string, unknown>;
  players: RecommendationPlayerSummary[];
}

export interface PlayerRecommendationsResponse {
  generatedAtUtc: string;
  recommendationsGeneratedAtUtc?: string;
  modelGeneratedAtUtc?: string;
  currentModelGeneratedAtUtc?: string;
  playerSyncedAtUtc?: string;
  modelGeneration?: string;
  stale?: boolean;
  legacySnapshot?: boolean;
  method: Record<string, unknown>;
  player: Omit<RecommendationPlayer, "modes"> & {
    modes: Partial<Record<RecommendationModeKey, RecommendationModeResult>>;
  };
}

export interface PlayerRefreshJob {
  id: string;
  kind: "player-recommendation-refresh";
  playerKey: string;
  status: "queued" | "running" | "completed" | "failed";
  stage: string;
  error?: string | null;
  progress?: {
    current: number;
    total: number;
    percent: number;
    message: string;
  };
}

export type PlayerRefreshResponse =
  | {
      outcome: "fresh";
      recommendation: PlayerRecommendationsResponse;
      refreshEligibleAtUtc: string;
    }
  | {
      outcome: "started" | "existing";
      job: PlayerRefreshJob;
    };
