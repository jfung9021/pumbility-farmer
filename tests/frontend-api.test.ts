import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { readJsonResponse } from "../lib/api-response.ts";
import {
  LocalAnalysisNotFoundError,
  LocalAnalysisValidationError,
  localAnalysisEnabled,
  readLocalAnalysisPayload,
} from "../lib/local-analysis.ts";
import { MIXES, mixFromSearchParams } from "../lib/mixes.ts";


test("uses a JSON error when the backend supplies one", async () => {
  const response = new Response(JSON.stringify({ error: "Safe backend error" }), {
    status: 503,
    headers: { "Content-Type": "application/json" },
  });
  await assert.rejects(() => readJsonResponse(response), /Safe backend error/);
});

test("turns platform-generated text into a useful error", async () => {
  const response = new Response("FUNCTION_INVOCATION_TIMEOUT", { status: 504 });
  await assert.rejects(
    () => readJsonResponse(response),
    (error: unknown) => error instanceof Error
      && error.message === "FUNCTION_INVOCATION_TIMEOUT"
      && !error.message.includes("Unexpected token"),
  );
});

test("rejects an empty successful response as non-JSON", async () => {
  const response = new Response("", { status: 200 });
  await assert.rejects(() => readJsonResponse(response), /empty or non-JSON/);
});

test("local analysis mode is explicitly opt-in", () => {
  assert.equal(localAnalysisEnabled({}), false);
  assert.equal(localAnalysisEnabled({ PIU_LOCAL_ANALYSIS: "1" }), true);
});

test("Phoenix 2 is the default and Phoenix 1 is URL-addressable", () => {
  assert.equal(mixFromSearchParams(new URLSearchParams()), "phoenix2");
  assert.equal(mixFromSearchParams(new URLSearchParams("mix=phoenix1")), "phoenix1");
  assert.equal(mixFromSearchParams(new URLSearchParams("mix=Fiesta")), "phoenix2");
});

test("Phoenix 1 is a versioned archive while Phoenix 2 remains refreshable", () => {
  assert.equal(MIXES.phoenix1.archive?.url, "/data/phoenix1-20260807.json");
  assert.equal(MIXES.phoenix1.archive?.sha256.length, 64);
  assert.equal(MIXES.phoenix2.archive, null);
});

test("reads a privacy-safe local aggregate", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "piu-local-analysis-"));
  const resultsPath = path.join(directory, "web_results.json");
  const payload = {
    generatedAtUtc: "2026-08-07T12:00:00Z",
    mix: { key: "phoenix2", apiValue: "Phoenix2", label: "Phoenix 2" },
    summary: { scriptVersion: "test", method: {}, coverage: {}, modes: {} },
    singles: [],
    doubles: [],
    relativeGroups: [],
    effectBands: [],
  };
  try {
    await writeFile(resultsPath, JSON.stringify(payload), "utf8");
    assert.deepEqual(await readLocalAnalysisPayload(resultsPath), payload);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("normalizes legacy Phoenix 2 local aggregates", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "piu-local-analysis-"));
  const resultsPath = path.join(directory, "web_results.json");
  const payload = {
    generatedAtUtc: "2026-08-07T12:00:00Z",
    summary: { scriptVersion: "legacy", method: {}, coverage: {}, modes: {} },
    singles: [],
    doubles: [],
    relativeGroups: [],
    effectBands: [],
  };
  try {
    await writeFile(resultsPath, JSON.stringify(payload), "utf8");
    const normalized = await readLocalAnalysisPayload(resultsPath);
    assert.equal(normalized.mix.key, "phoenix2");
    assert.equal(normalized.mix.apiValue, "Phoenix2");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("rejects private fields in a local aggregate", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "piu-local-analysis-"));
  const resultsPath = path.join(directory, "web_results.json");
  const payload = {
    generatedAtUtc: "2026-08-07T12:00:00Z",
    mix: { key: "phoenix2", apiValue: "Phoenix2", label: "Phoenix 2" },
    summary: { scriptVersion: "test", method: {}, coverage: {}, modes: {} },
    singles: [{ playerId: "private" }],
    doubles: [],
    relativeGroups: [],
    effectBands: [],
  };
  try {
    await writeFile(resultsPath, JSON.stringify(payload), "utf8");
    await assert.rejects(
      () => readLocalAnalysisPayload(resultsPath),
      LocalAnalysisValidationError,
    );
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("validates local aggregates against the requested Phoenix version", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "piu-local-analysis-"));
  const resultsPath = path.join(directory, "web_results.json");
  const payload = {
    generatedAtUtc: "2026-08-07T12:00:00Z",
    mix: { key: "phoenix1", apiValue: "Phoenix", label: "Phoenix 1" },
    summary: { scriptVersion: "test", method: {}, coverage: {}, modes: {} },
    singles: [],
    doubles: [],
    relativeGroups: [],
    effectBands: [],
  };
  try {
    await writeFile(resultsPath, JSON.stringify(payload), "utf8");
    assert.equal((await readLocalAnalysisPayload("phoenix1", resultsPath)).mix.key, "phoenix1");
    await assert.rejects(
      () => readLocalAnalysisPayload("phoenix2", resultsPath),
      /does not contain Phoenix 2 data/,
    );
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("reports a missing local analysis without exposing a path", async () => {
  const missing = path.join(tmpdir(), "piu-local-analysis-missing", "web_results.json");
  await assert.rejects(() => readLocalAnalysisPayload(missing), LocalAnalysisNotFoundError);
});
