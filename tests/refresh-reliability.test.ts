import assert from "node:assert/strict";
import test from "node:test";
import { ApiResponseError, readJsonResponse } from "../lib/api-response.ts";
import { createTierChartDetailLoader } from "../lib/tier-chart-details.ts";
import { displayedRecommendationModel, globalRefreshMessage, loadWithGenerationRecovery, MODEL_DELAY_THRESHOLD_MS, waitForPlayerRefresh, type RefreshStatus } from "../lib/refresh-reliability.ts";
import type { PlayerRecommendationsResponse, PlayerRefreshJob, RecommendationPlayersResponse } from "../lib/types.ts";

test("HTTP errors retain the conflict status and server message", async () => {
  await assert.rejects(readJsonResponse(Response.json({ error: "Reload the player list." }, { status: 409 })), error => {
    assert.ok(error instanceof ApiResponseError);
    assert.equal(error.status, 409);
    assert.equal(error.message, "Reload the player list.");
    return true;
  });
});

test("cached recommendations retain their actual model date while stale", () => {
  const index = { modelGeneratedAtUtc: "2026-10-17T05:50:30Z" } as RecommendationPlayersResponse;
  const cache = { modelGeneratedAtUtc: "2026-10-15T06:09:46Z", currentModelGeneratedAtUtc: index.modelGeneratedAtUtc, stale: true } as PlayerRecommendationsResponse;
  assert.equal(displayedRecommendationModel(cache, index), cache.modelGeneratedAtUtc);
  assert.equal(displayedRecommendationModel(null, index), index.modelGeneratedAtUtc);
  assert.equal(displayedRecommendationModel({ ...cache, modelGeneratedAtUtc: undefined }, index), undefined);
  assert.equal(displayedRecommendationModel({ ...cache, stale: false, modelGeneratedAtUtc: index.modelGeneratedAtUtc }, index), index.modelGeneratedAtUtc);
});

test("global failure and delayed suggestions stay visible independently of personal score freshness", () => {
  const now = Date.parse("2026-10-19T06:30:00Z");
  const status: RefreshStatus = {
    checkedAtUtc: new Date(now).toISOString(),
    tierGeneratedAtUtc: "2026-10-18T02:11:43Z", modelGeneratedAtUtc: "2026-10-17T05:50:30Z",
    latestRefresh: { status: "failed", stage: "analysis", updatedAtUtc: "2026-10-19T06:24:00Z", completedAtUtc: null, failureCode: "analysis-no-progress" },
  };
  assert.match(globalRefreshMessage(status, "tier", now)!, /failed/);
  assert.match(globalRefreshMessage(status, "model", now)!, /failed/);
  assert.match(globalRefreshMessage({ ...status, latestRefresh: null }, "tier", now)!, /delayed/);
  assert.equal(globalRefreshMessage({ ...status, tierGeneratedAtUtc: new Date(now).toISOString() }, "tier", now), null);
  assert.equal(globalRefreshMessage({ ...status, tierGeneratedAtUtc: new Date(now - MODEL_DELAY_THRESHOLD_MS).toISOString(), latestRefresh: null }, "tier", now), null);
});

test("tier details recover one publication change using the new generation", async () => {
  const calls: string[] = [];
  let reloads = 0;
  const load = createTierChartDetailLoader((async (url: string) => {
    const generation = new URL(url, "https://example.test").searchParams.get("generatedAtUtc")!;
    calls.push(generation);
    return generation === "A"
      ? Response.json({ error: "Reload this tier list." }, { status: 409 })
      : Response.json({ generatedAtUtc: generation, chart: { chartId: "chart-1" } });
  }) as typeof fetch);
  const result = await loadWithGenerationRecovery("A", generation => load("chart-1", generation), async () => { reloads++; return "B"; });
  assert.equal(result.catalog, "B");
  assert.equal(result.value.chart.chartId, "chart-1");
  assert.deepEqual(calls, ["A", "B"]);
  assert.equal(reloads, 1);
});

test("a second publication conflict stops after one catalog reload", async () => {
  let calls = 0;
  let reloads = 0;
  await assert.rejects(loadWithGenerationRecovery("A", async () => {
    calls++; throw new ApiResponseError("Reload this player list.", 409);
  }, async () => { reloads++; return "B"; }), /Reload this player list/);
  assert.equal(calls, 2);
  assert.equal(reloads, 1);
});

test("ordinary service failures do not reload catalogs and removed players are terminal", async () => {
  let reloads = 0;
  await assert.rejects(loadWithGenerationRecovery("A", async () => { throw new ApiResponseError("Unavailable", 503); }, async () => { reloads++; return "B"; }), /Unavailable/);
  assert.equal(reloads, 0);
  const seen: string[] = [];
  await assert.rejects(loadWithGenerationRecovery({ generation: "A", players: ["selected"] }, async catalog => {
    if (!catalog.players.includes("selected")) throw new Error("This player is no longer in the latest official player list.");
    seen.push(catalog.generation);
    throw new ApiResponseError("Changed", 409);
  }, async () => ({ generation: "B", players: [] })), /no longer/);
  assert.deepEqual(seen, ["A"]);
});

const running: PlayerRefreshJob = { id: "job", kind: "player-recommendation-refresh", playerKey: "player", status: "running", stage: "scores" };

test("a player refresh completing after 30 seconds stays pending and then succeeds", async () => {
  let elapsed = 0;
  let pending = 0;
  const result = await waitForPlayerRefresh({
    initial: running, signal: new AbortController().signal,
    now: () => elapsed, sleep: async ms => { elapsed += ms; }, onPending: () => { pending++; },
    readJob: async () => ({ ...running, status: elapsed >= 45_000 ? "completed" : "running" }),
  });
  assert.equal(result.outcome, "completed");
  assert.equal(pending, 1);
  assert.ok(elapsed >= 45_000);
});

test("bounded polling reports pending without marking a slow job failed", async () => {
  let elapsed = 0;
  const result = await waitForPlayerRefresh({
    initial: running, signal: new AbortController().signal,
    now: () => elapsed, sleep: async ms => { elapsed += ms; }, onPending: () => {}, maxWaitMs: 60_000,
    readJob: async () => running,
  });
  assert.equal(result.outcome, "pending");
  assert.equal(result.job.status, "running");
  assert.ok(elapsed >= 60_000 && elapsed < 63_000);
});

test("polling displays real terminal failure and stops on player cancellation", async () => {
  await assert.rejects(waitForPlayerRefresh({
    initial: { ...running, status: "failed", error: "Score source unavailable" }, signal: new AbortController().signal,
    readJob: async () => running, onPending: () => {},
  }), /Score source unavailable/);
  const controller = new AbortController();
  let reads = 0;
  await assert.rejects(waitForPlayerRefresh({
    initial: running, signal: controller.signal, sleep: async () => { controller.abort(); },
    readJob: async () => { reads++; return running; }, onPending: () => {},
  }), { name: "AbortError" });
  assert.equal(reads, 0);
});
