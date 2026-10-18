import { readJsonResponse } from "./api-response.ts";
import type { TierChartDetailsResponse } from "./types";

/** A changed publication never reuses evidence from a previous list generation. */
export function createTierChartDetailLoader(fetcher: typeof fetch = fetch) {
  let currentGeneration = "";
  const pending = new Map<string, Promise<TierChartDetailsResponse>>();
  return (chartId: string, generatedAtUtc: string) => {
    if (currentGeneration !== generatedAtUtc) { pending.clear(); currentGeneration = generatedAtUtc; }
    const existing = pending.get(chartId);
    if (existing) return existing;
    const params = new URLSearchParams({chartId, generatedAtUtc});
    const request = fetcher(`/api/tier-list/chart?${params}`, {cache:"no-store"})
      .then(response => readJsonResponse<TierChartDetailsResponse>(response))
      .then(payload => {
        if (payload.generatedAtUtc !== generatedAtUtc || payload.chart?.chartId !== chartId) throw new Error("Chart details do not match this tier list. Reload the tier list.");
        return payload;
      });
    pending.set(chartId, request);
    void request.catch(() => { if (pending.get(chartId) === request) pending.delete(chartId); });
    return request;
  };
}

export const loadTierChartDetails = createTierChartDetailLoader();
