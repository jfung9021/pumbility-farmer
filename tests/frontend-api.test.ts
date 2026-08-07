import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { readJsonResponse } from "../lib/api-response.ts";
import { demoPayloads } from "../lib/demo-data.ts";
import {
  LocalAnalysisNotFoundError,
  LocalAnalysisValidationError,
  localAnalysisEnabled,
  readLocalAnalysisPayload,
} from "../lib/local-analysis.ts";
import { archiveForMix, MIXES, mixFromSearchParams } from "../lib/mixes.ts";
import {
  applyPhoenix1Rerates,
  type Phoenix1ReratePayload,
} from "../lib/phoenix1-rerates.ts";
import type { AnalysisPayload } from "../lib/types.ts";


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
  assert.equal(
    MIXES.phoenix1.archive?.reratesUrl,
    "/data/phoenix1-rerates-20260807.json",
  );
  assert.equal(MIXES.phoenix1.archive?.sha256.length, 64);
  assert.equal(MIXES.phoenix2.archive, null);
});

test("local analysis mode reads Phoenix 1 from disk instead of the archive", () => {
  assert.equal(archiveForMix("phoenix1", true), null);
  assert.equal(archiveForMix("phoenix1", false)?.url, "/data/phoenix1-20260807.json");
});

test("demo payload uses the symmetric quarter-level effect bands", () => {
  assert.deepEqual(
    demoPayloads.phoenix2.effectBands.map(({ low, high }) => [low, high]),
    [
      [null, -1.0],
      [-1.0, -0.75],
      [-0.75, -0.5],
      [-0.5, -0.25],
      [-0.25, 0.25],
      [0.25, 0.5],
      [0.5, 0.75],
      [0.75, 1.0],
      [1.0, null],
    ],
  );
});

test("annotates the frozen Phoenix 1 charts with Phoenix 2 rerates", async () => {
  const [archiveRaw, reratesRaw] = await Promise.all([
    readFile(path.join(process.cwd(), "public", "data", "phoenix1-20260807.json"), "utf8"),
    readFile(
      path.join(process.cwd(), "public", "data", "phoenix1-rerates-20260807.json"),
      "utf8",
    ),
  ]);
  const archive = JSON.parse(archiveRaw) as AnalysisPayload;
  const rerates = JSON.parse(reratesRaw) as Phoenix1ReratePayload;
  const annotated = applyPhoenix1Rerates(archive, rerates);
  const charts = [...annotated.singles, ...annotated.doubles];
  const changed = charts.filter((chart) => chart.phoenix2Rerate);
  const kugutsu = charts.find((chart) => chart.songName === "Kugutsu" && chart.difficulty === "D21");
  const halloween = charts.find(
    (chart) => chart.songName === "Halloween Party ~Multiverse~" && chart.difficulty === "D21",
  );

  assert.equal(changed.length, 152);
  assert.equal(changed.filter((chart) => chart.phoenix2Rerate?.direction === "uprated").length, 118);
  assert.equal(changed.filter((chart) => chart.phoenix2Rerate?.direction === "downrated").length, 34);
  assert.deepEqual(kugutsu?.phoenix2Rerate, {
    from: "D21",
    to: "D20",
    delta: -1,
    direction: "downrated",
    sourceRow: 30,
  });
  assert.equal(halloween?.phoenix2Rerate?.to, "D22");
});

test("rejects rerates built for a different Phoenix 1 archive", async () => {
  const archive = JSON.parse(
    await readFile(path.join(process.cwd(), "public", "data", "phoenix1-20260807.json"), "utf8"),
  ) as AnalysisPayload;
  const rerates = JSON.parse(
    await readFile(
      path.join(process.cwd(), "public", "data", "phoenix1-rerates-20260807.json"),
      "utf8",
    ),
  ) as Phoenix1ReratePayload;
  assert.throws(
    () => applyPhoenix1Rerates(archive, { ...rerates, phoenix1ArchiveSha256: "wrong" }),
    /do not match the archived dataset/,
  );
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
