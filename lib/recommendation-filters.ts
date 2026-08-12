import type {
  RecommendationChart,
  RecommendationModeKey,
  RecommendationModeResult,
} from "./types";


export const ALL_DIFFICULTIES = "All";
export const RECOMMENDATION_DISPLAY_COUNT = 20;
export const MIN_RECOMMENDATION_LEVEL = 16;

export function officialDifficulty(chart: RecommendationChart): string {
  return `${chart.type === "Single" ? "S" : "D"}${chart.level}`;
}

function chartMatchesMode(
  chart: RecommendationChart,
  mode: RecommendationModeKey,
): boolean {
  if (mode === "overall") return true;
  return chart.type === (mode === "singles" ? "Single" : "Double");
}

function sortOfficialDifficulties(left: string, right: string): number {
  const modeDifference = (left.startsWith("S") ? 0 : 1)
    - (right.startsWith("S") ? 0 : 1);
  return modeDifference
    || Number(left.slice(1)) - Number(right.slice(1));
}

export function recommendationDifficultyOptions(
  mode: RecommendationModeKey,
  charts: RecommendationChart[],
): string[] {
  return [...new Set(
    charts
      .filter(
        (chart) => chart.level >= MIN_RECOMMENDATION_LEVEL
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
      .filter((chart) => chart.level >= MIN_RECOMMENDATION_LEVEL)
      .slice(0, RECOMMENDATION_DISPLAY_COUNT);
  }
  return (mode.filterCandidates ?? [])
    .filter(
      (chart) => chart.level >= MIN_RECOMMENDATION_LEVEL
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
