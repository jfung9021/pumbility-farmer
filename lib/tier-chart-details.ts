import { readJsonResponse } from "./api-response.ts";
import { requireFocalBpmMetadata } from "./song-bpm.ts";
import type { TierChartDetailsResponse } from "./types";

/** A changed publication never reuses evidence from a previous list generation. */
export function createTierChartDetailLoader(fetcher: typeof fetch = fetch) {
  let currentGeneration = "";
  const pending = new Map<string, Promise<TierChartDetailsResponse>>();
  return (chartId: string, generatedAtUtc: string, bpmMetadataRevision?: string) => {
    const generation = JSON.stringify([generatedAtUtc, bpmMetadataRevision]);
    if (currentGeneration !== generation) { pending.clear(); currentGeneration = generation; }
    const existing = pending.get(chartId);
    if (existing) return existing;
    const params = new URLSearchParams({chartId, generatedAtUtc});
    if (bpmMetadataRevision) { params.set("bpmPolicy", "focal-v1"); params.set("bpmMetadataRevision", bpmMetadataRevision); }
    const request = fetcher(`/api/tier-list/chart?${params}`, {cache:"no-store"})
      .then(response => readJsonResponse<TierChartDetailsResponse>(response))
      .then(payload => {
        if (bpmMetadataRevision) requireFocalBpmMetadata(payload.bpmMetadata);
        if (payload.generatedAtUtc !== generatedAtUtc || payload.chart?.chartId !== chartId || bpmMetadataRevision && payload.bpmMetadata?.bpmMetadataRevision !== bpmMetadataRevision) throw new Error("Chart details do not match this tier list. Reload the tier list.");
        return payload;
      });
    pending.set(chartId, request);
    void request.catch(() => { if (pending.get(chartId) === request) pending.delete(chartId); });
    return request;
  };
}

export const loadTierChartDetails = createTierChartDetailLoader();
