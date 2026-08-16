import type { ModeKey, RecommendationModeKey } from "./types";

export type RecommendationView = "recommendations" | "top50";

export const DEFAULT_TIER_MODE: ModeKey = "singles";
export const DEFAULT_RECOMMENDATION_MODE: RecommendationModeKey = "overall";
export const DEFAULT_RECOMMENDATION_VIEW: RecommendationView = "recommendations";

export function tierModeFromSearchParams(params: URLSearchParams): ModeKey {
  const mode = params.get("mode");
  return mode === "singles" || mode === "doubles" || mode === "coop"
    ? mode
    : DEFAULT_TIER_MODE;
}

export function recommendationModeFromSearchParams(
  params: URLSearchParams,
): RecommendationModeKey {
  const mode = params.get("mode");
  return mode === "overall" || mode === "singles" || mode === "doubles" || mode === "coop"
    ? mode
    : DEFAULT_RECOMMENDATION_MODE;
}

export function recommendationViewFromSearchParams(
  params: URLSearchParams,
): RecommendationView {
  return params.get("view") === "top50" ? "top50" : DEFAULT_RECOMMENDATION_VIEW;
}
