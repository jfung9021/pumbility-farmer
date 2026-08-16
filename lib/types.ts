import type { CombinedMixInfo, MixInfo, MixKey } from "./mixes";

export type ModeKey = "singles" | "doubles" | "coop";
export type RecommendationModeKey = "overall" | ModeKey;
export type EvidenceStatus = "Published" | "Provisional" | "Insufficient" | "Unrated";

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
    method: Record<string, unknown>;
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
  pumbility?: number | null;
  coopRating?: number | null;
  grade: string | null;
  plate: string | null;
  plateCode: string | null;
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
    | "estimated-difficulty-master-grade-ladder-v4";
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
  player: RecommendationPlayer;
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
