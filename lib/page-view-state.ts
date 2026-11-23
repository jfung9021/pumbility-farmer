import type { ModeKey, RecommendationModeKey, TierMetricKey } from "./types";

export type RecommendationView = "recommendations" | "pro" | "top50";

export const DEFAULT_TIER_MODE: ModeKey = "singles";
export const DEFAULT_TIER_METRIC: TierMetricKey = "scoring";
export const DEFAULT_RECOMMENDATION_MODE: RecommendationModeKey = "overall";
export const DEFAULT_RECOMMENDATION_VIEW: RecommendationView = "recommendations";

export function tierModeFromSearchParams(params: URLSearchParams): ModeKey {
  const mode = params.get("mode");
  return mode === "singles" || mode === "doubles" || mode === "coop"
    ? mode
    : DEFAULT_TIER_MODE;
}

export function tierMetricFromSearchParams(params: URLSearchParams): TierMetricKey {
  const metric = params.get("metric");
  return metric === "clearing" || metric === "pumbility" ? metric : DEFAULT_TIER_METRIC;
}

export function recommendationModeFromSearchParams(
  params: URLSearchParams,
): RecommendationModeKey {
  const mode = params.get("mode");
  if (params.get("view") === "pro") return mode === "singles" || mode === "doubles" ? mode : "overall";
  return mode === "overall" || mode === "singles" || mode === "doubles" || mode === "coop"
    ? mode
    : DEFAULT_RECOMMENDATION_MODE;
}

export function recommendationViewFromSearchParams(
  params: URLSearchParams,
): RecommendationView {
  const view = params.get("view");
  return view === "top50" || view === "pro" ? view : DEFAULT_RECOMMENDATION_VIEW;
}
