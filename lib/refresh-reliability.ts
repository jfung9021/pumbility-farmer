import { ApiResponseError } from "./api-response.ts";
import type { PlayerRecommendationsResponse, PlayerRefreshJob, RecommendationPlayersResponse } from "./types.ts";

export const MODEL_DELAY_THRESHOLD_MS = 26 * 60 * 60 * 1000;

export interface RefreshStatus {
  checkedAtUtc: string;
  tierGeneratedAtUtc: string | null;
  modelGeneratedAtUtc: string | null;
  latestRefresh: {
    status: "queued" | "running" | "completed" | "failed" | null;
    stage: string | null;
    updatedAtUtc: string | null;
    completedAtUtc: string | null;
    failureCode: string | null;
  } | null;
}

export function globalRefreshMessage(status: RefreshStatus, surface: "tier" | "model", nowMs: number): string | null {
  const generated = surface === "tier" ? status.tierGeneratedAtUtc : status.modelGeneratedAtUtc;
  const generatedMs = generated ? Date.parse(generated) : Number.NaN;
  const latest = status.latestRefresh;
  const latestMs = latest?.updatedAtUtc ? Date.parse(latest.updatedAtUtc) : Number.NaN;
  if (latest?.status === "failed" && (!Number.isFinite(generatedMs) || !Number.isFinite(latestMs) || latestMs >= generatedMs)) {
    return "The latest daily refresh failed. Showing saved data while the update is restored.";
  }
  if (Number.isFinite(generatedMs) && nowMs - generatedMs > MODEL_DELAY_THRESHOLD_MS) {
    return latest?.status === "queued" || latest?.status === "running"
      ? "The daily refresh is delayed and is still updating. Showing saved data."
      : "The daily data refresh is delayed. Showing saved data.";
  }
  return null;
}

export function displayedRecommendationModel(payload: PlayerRecommendationsResponse | null | undefined, index: RecommendationPlayersResponse | null | undefined): string | undefined {
  return payload
    ? payload.modelGeneratedAtUtc || (payload.legacySnapshot ? payload.generatedAtUtc : undefined)
    : index?.modelGeneratedAtUtc || index?.generatedAtUtc;
}

/** Reload a catalog only once; a second publication conflict is surfaced to the user. */
export async function loadWithGenerationRecovery<Catalog, Value>(catalog: Catalog, load: (catalog: Catalog) => Promise<Value>, reload: () => Promise<Catalog>): Promise<{ catalog: Catalog; value: Value }> {
  try {
    return { catalog, value: await load(catalog) };
  } catch (error) {
    if (!(error instanceof ApiResponseError) || error.status !== 409) throw error;
    const current = await reload();
    return { catalog: current, value: await load(current) };
  }
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const aborted = () => { clearTimeout(timer); reject(new DOMException("Cancelled", "AbortError")); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", aborted); resolve(); }, ms);
    signal.addEventListener("abort", aborted, { once: true });
  });
}

/** Thirty seconds is a UI progress threshold, never a backend failure deadline. */
export async function waitForPlayerRefresh(options: {
  initial: PlayerRefreshJob;
  signal: AbortSignal;
  readJob: (jobId: string) => Promise<PlayerRefreshJob>;
  onPending: () => void;
  now?: () => number;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  maxWaitMs?: number;
}): Promise<{ outcome: "completed" | "pending"; job: PlayerRefreshJob }> {
  const now = options.now ?? Date.now;
  const sleep = options.sleep ?? pause;
  const started = now();
  let job = options.initial;
  let pendingShown = false;
  while (job.status === "queued" || job.status === "running") {
    options.signal.throwIfAborted();
    const elapsed = now() - started;
    if (elapsed >= 30_000 && !pendingShown) { pendingShown = true; options.onPending(); }
    if (elapsed >= (options.maxWaitMs ?? 300_000)) return { outcome: "pending", job };
    await sleep(elapsed >= 30_000 ? 3000 : 1000, options.signal);
    options.signal.throwIfAborted();
    job = await options.readJob(job.id);
  }
  if (job.status === "failed") throw new Error(job.error || "Could not refresh this player's recommendations.");
  return { outcome: "completed", job };
}
