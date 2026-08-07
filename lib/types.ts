export type ModeKey = "singles" | "doubles";
export type EvidenceStatus = "Published" | "Provisional" | "Insufficient" | "Unrated";

export interface ChartResult {
  mode: "Singles" | "Doubles";
  modeRank: number | null;
  levelRank: number | null;
  folder: string;
  relativeGroupRank: number | null;
  relativeGroup: string | null;
  songName: string;
  difficulty: string;
  type: "Single" | "Double";
  level: number;
  chartId: string;
  imageUrl: string | null;
  noteCount: number | null;
  stepArtist: string | null;
  estimatedDifficulty: number | null;
  averageDifficulty: number;
  difficultyDelta: number | null;
  difficultyCi95Low: number | null;
  difficultyCi95High: number | null;
  nContributors: number;
  nPlayersScored: number;
  evidenceStatus: EvidenceStatus;
}

export interface FolderSummary {
  catalogCharts: number;
  measuredCharts: number;
  publishedCharts: number;
  medianContributors: number | null;
}

export interface ModeSummary {
  eligiblePlayers: number;
  catalogCharts: number;
  measuredCharts: number;
  publishedCharts: number;
  pumbilityPerLevel: number;
  folders: Record<string, FolderSummary>;
}

export interface AnalysisPayload {
  generatedAtUtc: string;
  summary: {
    scriptVersion: string;
    method: Record<string, unknown>;
    coverage: Record<string, number>;
    modes: Record<ModeKey, ModeSummary>;
  };
  singles: ChartResult[];
  doubles: ChartResult[];
  relativeGroups: Array<{ rank: number; name: string }>;
}
