import type { CombinedMixInfo, MixInfo, MixKey } from "./mixes";

export type ModeKey = "singles" | "doubles";
export type EvidenceStatus = "Published" | "Provisional" | "Insufficient" | "Unrated";

export interface ChartRerate {
  from: string;
  to: string;
  delta: number;
  direction: "uprated" | "downrated";
  sourceRow: number;
}

export interface ChartResult {
  mode: "Singles" | "Doubles";
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
  type: "Single" | "Double";
  level: number;
  chartId: string;
  imageUrl: string | null;
  noteCount: number | null;
  stepArtist: string | null;
  bpmMin?: number | null;
  bpmMax?: number | null;
  estimatedDifficulty: number | null;
  averageDifficulty: number;
  difficultyDelta: number | null;
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
  extremelyEasyCharts: number;
  extremelyHardCharts: number;
}

export interface ModeSummary {
  eligiblePlayers: number;
  catalogCharts: number;
  measuredCharts: number;
  publishedCharts: number;
  pumbilityPerLevel: number | null;
  calibration: Record<string, unknown>;
  shrinkage: Record<string, unknown>;
  folders: Record<string, FolderSummary>;
}

export interface AnalysisPayload {
  generatedAtUtc: string;
  mix: MixInfo | CombinedMixInfo;
  summary: {
    scriptVersion: string;
    method: Record<string, unknown>;
    coverage: Record<string, number>;
    modes: Record<ModeKey, ModeSummary>;
  };
  singles: ChartResult[];
  doubles: ChartResult[];
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
  mode: "Singles" | "Doubles";
  songName: string;
  difficulty: string;
  type: "Single" | "Double";
  level: number;
  chartId: string;
  imageUrl: string | null;
  noteCount: number | null;
  stepArtist: string | null;
  bpmMin?: number | null;
  bpmMax?: number | null;
  estimatedDifficulty: number;
  difficultyDelta: number;
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
  projectedGain: number | null;
  projectedScore: number | null;
  projectedGrade: string | null;
  projectedPlate: string | null;
  projectedPlateCode: string | null;
  projectedPlateProbability: number | null;
  plateProjectionSource: "phoenix1" | "phoenix2" | "population" | null;
  scoreProjectionSource?: string | null;
  scoreProjectionSupportCount?: number | null;
  scoreProjectionConfidence?: "high" | "medium" | "low" | "unavailable";
  played: boolean;
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
  ratingFallbackCharts?: number;
  projectionAvailable?: boolean;
  scoreProjectionModel?:
    | "population-crossfit-monotone-v1"
    | "population-crossfit-monotone-v2"
    | "similar-skill-top100-q75-v1"
    | "similar-skill-top100-q50-v1";
  pumbilityPerLevel?: number | null;
  currentTop50Pumbility?: number;
  currentTop50CutoffPumbility?: number | null;
  candidateRange?: [number | null, number];
  candidateCount?: number;
  candidates?: RecommendationChart[];
  topRecommendations: RecommendationChart[];
}

export interface RecommendationPlayerSummary {
  playerKey: string;
  username: string;
  displayName: string;
  eligibility: Record<ModeKey, boolean>;
}

export interface RecommendationPlayer {
  playerKey: string;
  username: string;
  displayName: string;
  manual?: boolean;
  modes: Record<ModeKey, RecommendationModeResult>;
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
