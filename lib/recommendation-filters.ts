import type {
  RecommendationChart,
  RecommendationModeKey,
  RecommendationModeResult,
} from "./types";


export const ALL_DIFFICULTIES = "All";
export const RECOMMENDATION_DISPLAY_COUNT = 20;
export const MIN_RECOMMENDATION_LEVEL = 16;

export function officialDifficulty(chart: RecommendationChart): string {
  if (chart.type === "CoOp") return `${chart.level}x`;
  return `${chart.type === "Single" ? "S" : "D"}${chart.level}`;
}

function chartMatchesMode(
  chart: RecommendationChart,
  mode: RecommendationModeKey,
): boolean {
  if (mode === "overall") return chart.type !== "CoOp";
  const typeByMode = { singles: "Single", doubles: "Double", coop: "CoOp" } as const;
  return chart.type === typeByMode[mode];
}

function sortOfficialDifficulties(left: string, right: string): number {
  const modeOrder = (value: string) => value.startsWith("S")
    ? 0
    : value.startsWith("D") ? 1 : 2;
  const modeDifference = modeOrder(left) - modeOrder(right);
  return modeDifference
    || Number(left.replace(/\D/g, "")) - Number(right.replace(/\D/g, ""));
}

export function recommendationDifficultyOptions(
  mode: RecommendationModeKey,
  charts: RecommendationChart[],
): string[] {
  return [...new Set(
    charts
      .filter(
        (chart) => (mode === "coop" || chart.level >= MIN_RECOMMENDATION_LEVEL)
          && chartMatchesMode(chart, mode),
      )
      .map(officialDifficulty),
  )].sort(sortOfficialDifficulties);
}

export function visibleRecommendations(
  modeKey: RecommendationModeKey,
  mode: RecommendationModeResult | null,
  difficulty: string,
): RecommendationChart[] {
  if (!mode) return [];
  if (difficulty === ALL_DIFFICULTIES) {
    return mode.topRecommendations
      .filter((chart) => modeKey === "coop" || chart.level >= MIN_RECOMMENDATION_LEVEL)
      .slice(0, RECOMMENDATION_DISPLAY_COUNT);
  }
  return (mode.filterCandidates ?? [])
    .filter(
      (chart) => (modeKey === "coop" || chart.level >= MIN_RECOMMENDATION_LEVEL)
        && chartMatchesMode(chart, modeKey)
        && officialDifficulty(chart) === difficulty,
    )
    .sort((left, right) => {
      const leftGain = left.projectedGain;
      const rightGain = right.projectedGain;
      if (leftGain !== null && rightGain !== null && leftGain !== rightGain) {
        return rightGain - leftGain;
      }
      if (leftGain !== null && rightGain === null) return -1;
      if (leftGain === null && rightGain !== null) return 1;
      return right.farmEdge - left.farmEdge
        || left.estimatedDifficulty - right.estimatedDifficulty
        || (right.expectedPumbility ?? 0) - (left.expectedPumbility ?? 0)
        || left.songName.localeCompare(right.songName)
        || left.chartId.localeCompare(right.chartId);
    });
}
