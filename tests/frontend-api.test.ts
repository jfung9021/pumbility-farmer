import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { readJsonResponse } from "../lib/api-response.ts";
import { demoPayloads } from "../lib/demo-data.ts";
import {
  formatEstimatedDifficulty,
  truncateEstimatedDifficulty,
} from "../lib/format-difficulty.ts";
import {
  LocalAnalysisNotFoundError,
  LocalAnalysisValidationError,
  localAnalysisEnabled,
  readLocalAnalysisPayload,
  validateLocalAnalysisPayload,
} from "../lib/local-analysis.ts";
import { archiveForMix, MIXES, mixFromSearchParams } from "../lib/mixes.ts";
import { pumbilityProgress } from "../lib/pumbility-progress.ts";
import {
  applyPhoenix1Rerates,
  type Phoenix1ReratePayload,
} from "../lib/phoenix1-rerates.ts";
import type { AnalysisPayload } from "../lib/types.ts";
import {
  LocalRecommendationsValidationError,
  recommendationPlayerList,
  recommendationsForPlayer,
  recommendationsForRating,
  validateLocalRecommendationIndex,
} from "../lib/local-recommendations.ts";


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

test("mobile styles keep desktop information visible", async () => {
  const css = await readFile(path.join(process.cwd(), "app", "globals.css"), "utf8");
  const mobileStyles = css.slice(css.indexOf("@media (max-width: 820px)"));
  const hiddenSelectors = [...mobileStyles.matchAll(/([^{}]+)\{[^{}]*display:\s*none/g)]
    .map((match) => match[1].trim());

  assert.deepEqual(hiddenSelectors, [".feature-card > b"]);
});

test("recommendation methodology separates top-20 display from ranks 11-30 projection", async () => {
  const page = await readFile(
    path.join(process.cwd(), "app", "recommendations", "page.tsx"),
    "utf8",
  );

  assert.match(page, /median \(50th percentile\) from all other players/);
  assert.match(page, /plus or minus 0\.2 through 0\.5 rating in 0\.1 steps seeking 20 peers/);
  assert.match(page, /repeats those radii seeking 10, then repeats seeking five/);
  assert.match(page, /Every peer within the narrowest successful radius is used/);
  assert.match(page, /below five peers, the player-balanced population model is used/);
  assert.doesNotMatch(page, /top 100 at plus or minus/);
  assert.doesNotMatch(page, /through 1\.0/);
  assert.match(page, /top-20 average Pumbility/);
  assert.match(page, /ranks 11–30 Pumbility rating/);
  assert.match(page, /S with Fair Game/);
  assert.match(page, /visible skill rating and eligible-chart ceiling use top-20/);
  assert.match(page, /projected plate is the weighted median/);
  assert.match(page, /Expected Pumbility is then calculated once from the displayed projected score/);
  assert.match(page, /existing chart Pumbility, and current top 50 use the Pumbility supplied by Phoenix 2/);
  assert.match(page, /Skill title progress/);
  assert.doesNotMatch(page, /every likely grade-plate outcome/);
  assert.doesNotMatch(page, /chart difficulty fields are averaged for the skill rating/);
  assert.doesNotMatch(page, /reaches 50 valid Phoenix 2 scores/);
});

test("recommendation modes put Overall first and select it by default", async () => {
  const page = await readFile(
    path.join(process.cwd(), "app", "recommendations", "page.tsx"),
    "utf8",
  );

  assert.match(
    page,
    /const RECOMMENDATION_MODES:[\s\S]*"overall",[\s\S]*"singles",[\s\S]*"doubles"/,
  );
  assert.match(page, /useState<RecommendationModeKey>\("overall"\)/);
  assert.match(page, /role="tabpanel"/);
  assert.match(page, /role="progressbar"/);
  assert.match(page, /shared Phoenix 2 S\+D top 50/);
  assert.match(page, /cached recommendation predates the Overall model/);
});

test("Pumbility progress uses the Phoenix 2 title and rank boundaries", () => {
  const singleExpert = pumbilityProgress("singles", 17_500);
  assert.equal(singleExpert.label, "Single Expert Lv. 1");
  assert.equal(singleExpert.nextThreshold, 17_700);
  assert.equal(singleExpert.percent, 0);

  const doubleMaster = pumbilityProgress("doubles", 19_000);
  assert.equal(doubleMaster.label, "Double Master");
  assert.equal(doubleMaster.nextThreshold, null);
  assert.equal(doubleMaster.percent, 100);

  const alexandrite = pumbilityProgress("overall", 19_300);
  assert.equal(alexandrite.label, "Alexandrite Lv. 2");
  assert.equal(alexandrite.nextLabel, "Alexandrite Lv. 3");
  assert.equal(alexandrite.percent, 50);

  const phoenix = pumbilityProgress("overall", 20_000);
  assert.equal(phoenix.label, "Phoenix");
  assert.equal(phoenix.nextThreshold, null);
});

test("recommendation page shows one top-50 list without projection evidence details", async () => {
  const page = await readFile(
    path.join(process.cwd(), "app", "recommendations", "page.tsx"),
    "utf8",
  );
  const css = await readFile(path.join(process.cwd(), "app", "globals.css"), "utf8");

  assert.match(page, /Top 50 farmable charts/);
  assert.match(page, /Top 50 Pumbility opportunities/);
  assert.doesNotMatch(page, /FULL MATCHING SET|function CandidateRow|scoreProjectionEvidenceLabel/);
  assert.doesNotMatch(page, /chart\.projectedScore\.toLocaleString/);
  assert.doesNotMatch(css, /\.all-candidates|\.candidate-(?:row|list|copy|jacket|metric|search|heading)/);
});

test("recommendation page renders cache before a deduplicated player refresh", async () => {
  const page = await readFile(
    path.join(process.cwd(), "app", "recommendations", "page.tsx"),
    "utf8",
  );

  assert.match(page, /await loadCached\(\);\s*await refresh\(\);/);
  assert.match(page, /\/api\/recommendations\/refresh\?playerKey=/);
  assert.match(page, /\/api\/recommendations\/refresh\?jobId=/);
  assert.match(page, /Showing cached recommendations\. Refresh failed:/);
  assert.match(page, /Showing cached recommendations\. Live score refresh is temporarily unavailable\./);
  assert.match(page, /playersPayload\?\.refreshSupported === false/);
  assert.match(page, /payload\.playerSyncedAtUtc/);
  assert.match(page, /payload\.modelGeneratedAtUtc/);
  assert.match(page, /const deadline = Date\.now\(\) \+ 30_000/);
  assert.match(page, /Legacy snapshot generated/);
  assert.doesNotMatch(page, /Unknown generation time/);
});

test("legacy local player responses carry a complete generation timestamp contract", () => {
  const generatedAtUtc = "2026-08-08T00:00:00Z";
  const response = recommendationsForPlayer({
    generatedAtUtc,
    method: {},
    charts: [],
    players: [{
      playerKey: "opaque",
      username: "PLAYER",
      displayName: "PLAYER",
      modes: {
        singles: { eligible: false, validScoreCount: 0, topRecommendations: [] },
        doubles: { eligible: false, validScoreCount: 0, topRecommendations: [] },
      },
    }],
  }, "opaque");

  assert.equal(response?.legacySnapshot, true);
  assert.equal(response?.recommendationsGeneratedAtUtc, generatedAtUtc);
  assert.equal(response?.modelGeneratedAtUtc, generatedAtUtc);
  assert.equal(response?.playerSyncedAtUtc, generatedAtUtc);
  assert.equal(response?.player.modes.overall, undefined);
});

test("local recommendations reject stale schemas before rendering", () => {
  const payload = {
    schemaVersion: 18,
    generatedAtUtc: "2026-08-08T00:00:00Z",
    method: {},
    charts: [],
    players: [],
  };

  assert.throws(
    () => validateLocalRecommendationIndex(payload),
    (error: unknown) => error instanceof LocalRecommendationsValidationError
      && /Regenerate schema 19 recommendations/.test(error.message),
  );
});

test("global analysis button sends the protected administrator secret", async () => {
  const tierList = await readFile(
    path.join(process.cwd(), "app", "tier-list", "page.tsx"),
    "utf8",
  );

  assert.match(tierList, /Administrator refresh key/);
  assert.match(tierList, /"X-Analysis-Run-Secret": protectedSecret/);
  assert.match(tierList, /\/api\/analyze\?mix=phoenix2/);
  assert.match(tierList, /\/api\/analyze\?mix=phoenix2&jobId=/);
});

test("estimated difficulties truncate to one decimal place everywhere", async () => {
  const pages = await Promise.all([
    readFile(path.join(process.cwd(), "app", "tier-list", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
  ]);

  assert.equal(formatEstimatedDifficulty(17.49), "17.4");
  assert.equal(formatEstimatedDifficulty(17.99), "17.9");
  assert.equal(formatEstimatedDifficulty(18.0), "18.0");
  assert.equal(truncateEstimatedDifficulty(20.89), 20.8);
  for (const page of pages) {
    assert.match(page, /formatEstimatedDifficulty\(chart\.estimatedDifficulty\)/);
    assert.doesNotMatch(page, /estimatedDifficulty\.toFixed\(/);
  }
});

test("tier list can group charts by truncated estimated difficulty", async () => {
  const page = await readFile(
    path.join(process.cwd(), "app", "tier-list", "page.tsx"),
    "utf8",
  );

  assert.match(page, /type GroupingView = "tiers" \| "estimated"/);
  assert.match(page, />Tier bands<\/button>/);
  assert.match(page, />Estimated difficulty<\/button>/);
  assert.match(page, /truncateEstimatedDifficulty\(chart\.estimatedDifficulty\)/);
  assert.match(page, /\.sort\(\(\[left\], \[right\]\) => left - right\)/);
  assert.match(page, /Grouped by truncated one-decimal estimate/);
});

test("tier list compact layout packs art and song names into group rows", async () => {
  const [page, css] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "tier-list", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);

  assert.match(page, /type LayoutView = "detailed" \| "compact"/);
  assert.match(page, /aria-label="Chart layout"/);
  assert.match(page, />Detailed<\/button>/);
  assert.match(page, />Compact<\/button>/);
  assert.match(page, /className="compact-chart-card"/);
  assert.match(page, /<h3>\{chart\.songName\}<\/h3>/);
  assert.match(page, /compact=\{layoutView === "compact"\}/);
  assert.match(page, /<h2 id=\{sectionId\}>\{label\}<\/h2>/);
  assert.match(css, /\.tier-compact \{[^}]*grid-template-columns: 132px minmax\(0, 1fr\);/);
  assert.match(css, /\.compact-chart-grid \{[^}]*grid-template-columns: repeat\(auto-fill, minmax\(84px, 96px\)\);/);
  assert.match(css, /\.compact-jacket \{[^}]*aspect-ratio: 1;/);
});

test("chart art uses mode-colored borders in every rendering layout", async () => {
  const [tierList, recommendations, css] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "tier-list", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);

  assert.match(tierList, /className="chart-art jacket" data-chart-type=\{chart\.type\}/);
  assert.match(tierList, /className="chart-art compact-jacket" data-chart-type=\{chart\.type\}/);
  assert.match(recommendations, /className="chart-art recommendation-jacket" data-chart-type=\{chart\.type\}/);
  assert.match(css, /--chart-single-border: #ff4a4a;/);
  assert.match(css, /--chart-double-border: #39d96a;/);
  assert.match(css, /\.chart-art\[data-chart-type="Single"\] \{ border: 2px solid var\(--chart-single-border\); \}/);
  assert.match(css, /\.chart-art\[data-chart-type="Double"\] \{ border: 2px solid var\(--chart-double-border\); \}/);
});

test("recommendation cards express projected grade and plate as a concrete goal", async () => {
  const page = await readFile(
    path.join(process.cwd(), "app", "recommendations", "page.tsx"),
    "utf8",
  );

  assert.match(page, /PG: "all Perfects"/);
  assert.match(page, /UG: "Perfects and Greats only"/);
  assert.match(page, /EG: "Perfects, Greats, and Goods only"/);
  assert.match(page, /SG: "0 misses"/);
  assert.match(page, /MG: "1–5 misses"/);
  assert.match(page, /TG: "6–10 misses"/);
  assert.match(page, /FG: "11–20 misses"/);
  assert.match(page, /RG: "21\+ misses"/);
  assert.match(page, /Goal: \$\{chart\.projectedGrade\} \$\{chart\.projectedPlateCode\}/);
  assert.match(page, /"S\+": 975_000/);
  assert.doesNotMatch(page, /most likely|plateSourceLabel/);
});

test("mobile recommendation cards keep gain on the right and place BPM between stepmaker and official difficulty", async () => {
  const page = await readFile(
    path.join(process.cwd(), "app", "recommendations", "page.tsx"),
    "utf8",
  );
  const css = await readFile(path.join(process.cwd(), "app", "globals.css"), "utf8");

  assert.match(page, /chart\.stepArtist \|\| "Unknown step artist"/);
  assert.match(page, /formatBpm\(chart\.bpmMin, chart\.bpmMax\)/);
  assert.match(page, /bpm \? <> · \{bpm\}<\/> : null/);
  assert.match(page, /chart\.difficulty\} official<\/b>/);
  assert.doesNotMatch(page, /formula expected/);
  assert.match(css, /grid-template-columns: 22px 48px minmax\(0, 1fr\) 92px/);
  assert.match(css, /\.recommendation-value \{[^}]*grid-column: 4;[^}]*grid-row: 1;/);
  assert.doesNotMatch(css, /\.recommendation-value small/);
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

test("Phoenix 1 uses stable frozen paths while Phoenix 2 remains refreshable", () => {
  assert.equal(MIXES.phoenix1.archive?.url, "/data/phoenix1.json");
  assert.equal(
    MIXES.phoenix1.archive?.reratesUrl,
    "/data/phoenix1-rerates.json",
  );
  assert.equal(MIXES.phoenix1.archive?.sha256.length, 64);
  assert.equal(MIXES.phoenix2.archive, null);
});

test("local analysis mode reads Phoenix 1 from disk instead of the archive", () => {
  assert.equal(archiveForMix("phoenix1", true), null);
  assert.equal(archiveForMix("phoenix1", false)?.url, "/data/phoenix1.json");
});

test("demo payload uses the seven fixed effect bands", () => {
  assert.deepEqual(
    demoPayloads.phoenix2.effectBands.map(({ low, high }) => [low, high]),
    [
      [null, -0.5],
      [-0.5, -0.3],
      [-0.3, -0.1],
      [-0.1, 0.1],
      [0.1, 0.3],
      [0.3, 0.5],
      [0.5, null],
    ],
  );
});

test("demo payload represents the folder-normalized 0.4-scale methodology", () => {
  const payload = demoPayloads.phoenix2;
  assert.equal(
    payload.summary.scriptVersion,
    "6.3.0-phoenix1-score-override-folder-normalized-0.4-scale",
  );
  assert.equal(payload.summary.method.difficultyDeltaScale, 0.4);
  assert.deepEqual(payload.summary.method.folderRangeNormalization, {
    method: "one-sided expected-normal-maximum order-statistic compression",
    referenceMeasuredCharts: 30,
    expandsFolders: false,
  });
  assert.equal(payload.summary.method.displayMinimumOfficialLevel, 16);
  assert.equal(payload.singles.some((chart) => chart.level === 16), true);
  assert.equal(payload.doubles.some((chart) => chart.level === 16), true);
  assert.equal(payload.singles[0].difficultyDelta, -0.432);
});

test("annotates the frozen Phoenix 1 charts with Phoenix 2 rerates", async () => {
  const [archiveRaw, reratesRaw] = await Promise.all([
    readFile(path.join(process.cwd(), "public", "data", "phoenix1.json"), "utf8"),
    readFile(
      path.join(process.cwd(), "public", "data", "phoenix1-rerates.json"),
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

  assert.equal(changed.length, 231);
  assert.equal(changed.filter((chart) => chart.phoenix2Rerate?.direction === "uprated").length, 197);
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
    await readFile(path.join(process.cwd(), "public", "data", "phoenix1.json"), "utf8"),
  ) as AnalysisPayload;
  const rerates = JSON.parse(
    await readFile(
      path.join(process.cwd(), "public", "data", "phoenix1-rerates.json"),
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

test("accepts the combined tier-list identity", () => {
  const payload = {
    generatedAtUtc: "2026-08-08T00:00:00Z",
    mix: { key: "combined", apiValue: "Phoenix+Phoenix2", label: "Phoenix 1 + 2" },
    summary: { scriptVersion: "test", method: {}, coverage: {}, modes: {} },
    singles: [],
    doubles: [],
    relativeGroups: [],
    effectBands: [],
  };
  assert.equal(validateLocalAnalysisPayload(payload, "combined").mix.key, "combined");
  assert.throws(
    () => validateLocalAnalysisPayload(payload, "phoenix2"),
    /does not contain Phoenix 2 data/,
  );
});

test("reports a missing local analysis without exposing a path", async () => {
  const missing = path.join(tmpdir(), "piu-local-analysis-missing", "web_results.json");
  await assert.rejects(() => readLocalAnalysisPayload(missing), LocalAnalysisNotFoundError);
});

test("recommendation player list exposes names and eligibility without mode payloads", () => {
  const response = recommendationPlayerList({
    generatedAtUtc: "2026-08-08T00:00:00Z",
    method: { baselineRanks: [11, 30] },
    charts: [],
    players: [
      {
        playerKey: "opaque",
        username: "PLAYER",
        displayName: "PLAYER",
        modes: {
          overall: {
            eligible: true,
            validScoreCount: 30,
            candidates: [],
            topRecommendations: [],
          },
          singles: {
            eligible: true,
            validScoreCount: 30,
            candidates: [],
            topRecommendations: [],
          },
          doubles: {
            eligible: false,
            validScoreCount: 4,
            candidates: [],
            topRecommendations: [],
          },
        },
      },
    ],
  });
  assert.deepEqual(response.players, [
    {
      playerKey: "opaque",
      username: "PLAYER",
      displayName: "PLAYER",
      eligibility: { singles: true, doubles: false },
    },
  ]);
  assert.equal("modes" in response.players[0], false);
});

test("manual recommendations include charts up to 0.5 above the scoring rating", () => {
  const chart = (chartId: string, estimatedDifficulty: number, level: number) => ({
    mode: "Singles" as const,
    songName: chartId,
    difficulty: `S${level}`,
    type: "Single" as const,
    level,
    chartId,
    imageUrl: null,
    noteCount: null,
    stepArtist: null,
    estimatedDifficulty,
    difficultyDelta: estimatedDifficulty - level - 0.5,
    difficultyCi95Low: estimatedDifficulty,
    difficultyCi95High: estimatedDifficulty,
    nContributors: 10,
    phoenix1Contributors: 5,
    phoenix2Contributors: 5,
    evidenceStatus: "Published" as const,
  });
  const response = recommendationsForRating({
    generatedAtUtc: "2026-08-08T00:00:00Z",
    method: {},
    charts: [
      chart("level-16", 10, 16),
      chart("rating-edge", 20.5, 21),
      chart("upper-edge", 21, 21),
      chart("too-hard", 21.0000000001, 21),
      chart("level-15", 10, 15),
    ],
    players: [],
  }, 20.5);

  const singles = response.player.modes.singles;
  const overall = response.player.modes.overall;
  assert.ok(overall);
  assert.equal(singles.projectionAvailable, false);
  assert.deepEqual(singles.candidateRange, [null, 21]);
  assert.deepEqual(
    (singles.candidates ?? []).map((candidate) => candidate.chartId),
    ["level-16", "rating-edge", "upper-edge"],
  );
  assert.equal(singles.topRecommendations[0].chartId, "level-16");
  assert.equal(singles.topRecommendations[0].expectedPumbility, null);
  assert.equal(singles.topRecommendations[0].projectedGain, null);
  assert.equal(overall.sourceRecommendationCounts?.singles, 3);
  assert.equal(overall.sourceRecommendationCounts?.doubles, 0);
  assert.deepEqual(
    overall.topRecommendations.map((candidate) => candidate.chartId),
    ["level-16", "rating-edge", "upper-edge"],
  );

  const configuredFloor = recommendationsForRating({
    generatedAtUtc: "2026-08-08T00:00:00Z",
    method: { displayMinimumOfficialLevel: 17 },
    charts: [chart("level-16", 10, 16), chart("level-17", 10, 17)],
    players: [],
  }, 20.5);
  assert.deepEqual(
    (configuredFloor.player.modes.singles.candidates ?? []).map((candidate) => candidate.chartId),
    ["level-17"],
  );

  const fiftyOfFiftyFive = recommendationsForRating({
    generatedAtUtc: "2026-08-08T00:00:00Z",
    method: {},
    charts: Array.from({ length: 55 }, (_, index) =>
      chart(`chart-${String(index).padStart(2, "0")}`, 16 + index / 100, 16),
    ),
    players: [],
  }, 20.5).player.modes.singles.topRecommendations;
  assert.equal(fiftyOfFiftyFive.length, 50);
});
