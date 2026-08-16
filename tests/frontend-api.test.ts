import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { readJsonResponse } from "../lib/api-response.ts";
import {
  hasLimitedData,
  LIMITED_DATA_CONTRIBUTOR_THRESHOLD,
} from "../lib/chart-evidence.ts";
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
import {
  recommendationModeFromSearchParams,
  recommendationViewFromSearchParams,
  tierModeFromSearchParams,
} from "../lib/page-view-state.ts";
import { pumbilityProgress } from "../lib/pumbility-progress.ts";
import {
  recommendationDifficultyOptions,
  visibleRecommendations,
} from "../lib/recommendation-filters.ts";
import {
  applyPhoenix1Rerates,
  type Phoenix1ReratePayload,
} from "../lib/phoenix1-rerates.ts";
import type { AnalysisPayload, RecommendationChartEstimate } from "../lib/types.ts";
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

test("homepage leads with feature cards and explains the external score sync", async () => {
  const [page, syncLink, css] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "page.tsx"), "utf8"),
    readFile(
      path.join(process.cwd(), "app", "_components", "score-sync-link.tsx"),
      "utf8",
    ),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);

  assert.doesNotMatch(page, /feature-index|Phoenix scoring tools|home-status/);
  assert.doesNotMatch(page, /Use your Phoenix history|home-intro/);
  assert.match(css, /\.home-hero \{[^}]*margin: 0 auto;[^}]*padding: 18px 24px 110px;/);
  assert.match(css, /\.feature-grid \{[^}]*gap: 18px;[^}]*margin-top: 0;/);
  assert.match(page, /Sync your scores before you start/);
  assert.match(page, /Log in to PIU Scores/);
  assert.match(page, /make your account public so your scores can pass through the API/);
  assert.match(page, /<ScoreSyncLink>/);
  assert.match(syncLink, /https:\/\/piuscores\.arroweclip\.se\/UploadPhoenixScores/);
  assert.match(syncLink, /Upload scores in the external PIUScores Tool/);
  assert.match(syncLink, /rel="noopener noreferrer"/);
  assert.match(syncLink, /target="_blank"/);
});

test("recommendation methodology separates top-50 Pumbility from top-20 display and ranks 11-30 projection", async () => {
  const page = await readFile(
    path.join(process.cwd(), "app", "recommendations", "page.tsx"),
    "utf8",
  );

  assert.match(page, /median \(50th percentile\) from all other players/);
  assert.match(page, /plus or minus 0\.2 through 0\.5 rating in 0\.1 steps seeking 20 peers/);
  assert.match(page, /repeats those radii seeking 10, then repeats seeking five/);
  assert.match(page, /Every peer within the narrowest successful radius is used/);
  assert.match(page, /below five peers, the player-balanced population model uses the same Phoenix weighting/);
  assert.match(page, /giving Phoenix 2 results twice the weight of Phoenix 1/);
  assert.doesNotMatch(page, /top 100 at plus or minus/);
  assert.doesNotMatch(page, /through 1\.0/);
  assert.match(page, /top-20 average Pumbility/);
  assert.match(page, /ranks 11–30 Pumbility rating/);
  assert.match(page, /S with Fair Game/);
  assert.match(page, /visible skill rating uses top-20 average Pumbility/);
  assert.match(page, /up to 1\.0 estimated-difficulty point above that mode/);
  assert.match(page, /projected plate is the weighted median/);
  assert.match(page, /projected result is raised by one letter grade, capped at SSS\+/);
  assert.match(page, /Expected Pumbility is then calculated once from that goal grade/);
  assert.match(page, /existing chart Pumbility, and current top 50 use the Pumbility supplied by Phoenix 2/);
  assert.match(page, /Overall Pumbility is the best 50 values across both modes/);
  assert.match(page, /Skill title progress/);
  assert.doesNotMatch(page, /every likely grade-plate outcome/);
  assert.doesNotMatch(page, /chart difficulty fields are averaged for the skill rating/);
  assert.doesNotMatch(page, /reaches 50 valid Phoenix 2 scores/);
});

test("recommendation modes put Overall first and select it by default", async () => {
  const [page, css] = await Promise.all([
    readFile(
      path.join(process.cwd(), "app", "recommendations", "page.tsx"),
      "utf8",
    ),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);

  assert.match(
    page,
    /const RECOMMENDATION_MODES:[\s\S]*"overall",[\s\S]*"singles",[\s\S]*"doubles",[\s\S]*"coop"/,
  );
  assert.match(page, /useState<RecommendationModeKey>\("overall"\)/);
  assert.match(page, /role="tabpanel"/);
  assert.match(page, /role="progressbar"/);
  assert.match(page, /pumbility-progress-scale/);
  assert.match(page, /pumbility-rank-emblem/);
  assert.match(page, /pumbility-progress-heading[\s\S]*pumbility-progress-percent[\s\S]*className="pumbility-progress"[\s\S]*pumbility-progress-scale/);
  assert.match(page, /mode === "overall" \? "" : " no-emblem"/);
  assert.match(css, /\.pumbility-progress-heading \{[^}]*grid-template-columns: auto minmax\(0, 1fr\) auto;[^}]*min-height: 88px;/);
  assert.match(css, /\.pumbility-progress-heading\.no-emblem \{[^}]*grid-template-columns: minmax\(0, 1fr\) auto;/);
  assert.match(css, /@media \(max-width: 560px\)[\s\S]*\.pumbility-progress-heading \{[^}]*min-height: 66px;/);
  assert.match(css, /\.pumbility-progress-percent \{[^}]*margin-top: 16px;/);
  assert.match(css, /\.pumbility-progress \{[^}]*margin-top: 6px;/);
  assert.doesNotMatch(css, /\.pumbility-progress-percent\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/);
  assert.match(page, /Math\.round\(progress\.percent\)/);
  assert.match(page, /cached recommendation predates the Overall model/);
});

test("Co-op tabs and yellow badges are available on both data pages", async () => {
  const [tierList, recommendations, css] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "tier-list", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);

  assert.match(tierList, /\(\["singles", "doubles", "coop"\] as ModeKey\[\]\)\.map/);
  assert.match(tierList, /mode === "coop" \? "Co-op" : mode/);
  assert.match(tierList, /chart\.type === "CoOp" \? `\$\{chart\.level\}x`/);
  assert.match(recommendations, /"overall",\s*"singles",\s*"doubles",\s*"coop"/);
  assert.match(recommendations, /isCoop \? `\$\{chart\.level\}x` : chart\.level/);
  assert.match(css, /\.chart-difficulty-coop \{ background: #d5a91b; color: #171207; \}/);
});

test("tier and recommendation tabs are URL-addressable", async () => {
  assert.equal(tierModeFromSearchParams(new URLSearchParams("mode=singles")), "singles");
  assert.equal(tierModeFromSearchParams(new URLSearchParams("mode=doubles")), "doubles");
  assert.equal(tierModeFromSearchParams(new URLSearchParams("mode=coop")), "coop");
  assert.equal(tierModeFromSearchParams(new URLSearchParams("mode=overall")), "singles");

  assert.equal(recommendationModeFromSearchParams(new URLSearchParams("mode=overall")), "overall");
  assert.equal(recommendationModeFromSearchParams(new URLSearchParams("mode=singles")), "singles");
  assert.equal(recommendationModeFromSearchParams(new URLSearchParams("mode=doubles")), "doubles");
  assert.equal(recommendationModeFromSearchParams(new URLSearchParams("mode=coop")), "coop");
  assert.equal(recommendationModeFromSearchParams(new URLSearchParams("mode=invalid")), "overall");
  assert.equal(recommendationViewFromSearchParams(new URLSearchParams("view=top50")), "top50");
  assert.equal(
    recommendationViewFromSearchParams(new URLSearchParams("view=recommendations")),
    "recommendations",
  );

  const [tierList, recommendations] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "tier-list", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
  ]);
  assert.match(tierList, /url\.searchParams\.set\("mode", mode\)/);
  assert.match(tierList, /window\.addEventListener\("popstate", applyModeFromUrl\)/);
  assert.match(recommendations, /url\.searchParams\.set\("mode", mode\)/);
  assert.match(recommendations, /url\.searchParams\.set\("view", view\)/);
  assert.match(recommendations, /window\.addEventListener\("popstate", applyViewFromUrl\)/);
});

test("Co-op methodology derives Master-title goals from tier difficulty", async () => {
  const [tierList, recommendations, readme] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "tier-list", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "README.md"), "utf8"),
  ]);

  for (const content of [tierList, recommendations, readme]) {
    assert.match(content, /player[- ]strength/);
    assert.match(content, /Phoenix source/);
    assert.match(content, /robust/);
    assert.match(content, /using\s+all observations/);
    assert.match(content, /(?:not trimmed|without trimming)/);
    assert.match(content, /median(?: measured)? chart/);
    assert.match(content, /normal distribution/);
  }
  assert.match(recommendations, /completing all current chart goals clears the 16,000 Co-op Rating \[CO-OP\] Master threshold with extra leeway/i);
  assert.match(recommendations, /folder lookup is fixed and never rebalanced when charts are added/);
  assert.match(recommendations, /every difficulty-17 chart always targets AAA with Fair Game/);
  assert.match(tierList, /recommendation letter-grade goals are assigned from these whole-number difficulties/);
  assert.match(tierList, /easiest chart at continuous difficulty 10, the median chart at 16, and the hardest chart at 24\.9/);
  assert.match(tierList, /const continuous = chart\.difficultyModelContinuous/);
  assert.match(tierList, /chart\.estimatedDifficulty\)\.toFixed\(1\)/);
  assert.match(recommendations, /easiest chart at continuous difficulty 10, the median chart at 16, and the hardest chart at 24\.9/);
  assert.match(readme, /clears the 16,000 Co-op Rating\s+`\[CO-OP\] Master` threshold with extra leeway/);
  assert.match(readme, /one-grade recommendation boost capped at `SSS\+`/);
  assert.match(readme, /raw per-chart\s*q75 result remains analysis\s+provenance/);
  assert.match(readme, /whole-number buckets from 10 through 24/);
  assert.match(readme, /hardest chart can retain a 24\.9 internal\s+rating/);
});

test("recommendation player clicks are tracked by display name", async () => {
  const page = await readFile(
    path.join(process.cwd(), "app", "recommendations", "page.tsx"),
    "utf8",
  );

  assert.match(page, /import \{ track \} from "@vercel\/analytics";/);
  assert.match(
    page,
    /track\("recommendation_player_selected", \{ playerName: inputValue \}\)/,
  );
  assert.match(
    page,
    /onClick=\{\(\) => selectPlayer\(player\.playerKey, player\.displayName\)\}/,
  );
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

  const noCoopTitle = pumbilityProgress("coop", 999);
  assert.equal(noCoopTitle.label, "No Co-op title");
  assert.equal(noCoopTitle.nextLabel, "[CO-OP] Lv.1");

  const coopLevelTen = pumbilityProgress("coop", 10_000);
  assert.equal(coopLevelTen.label, "[CO-OP] Lv.10");
  assert.equal(coopLevelTen.nextLabel, "[CO-OP] Advanced");
  assert.equal(coopLevelTen.nextThreshold, 12_000);

  const coopMaster = pumbilityProgress("coop", 16_000);
  assert.equal(coopMaster.label, "[CO-OP] Master");
  assert.equal(coopMaster.percent, 100);
});

test("recommendation page keeps the filterable recommendation list beside the Top 50 view", async () => {
  const page = await readFile(
    path.join(process.cwd(), "app", "recommendations", "page.tsx"),
    "utf8",
  );
  const css = await readFile(path.join(process.cwd(), "app", "globals.css"), "utf8");

  assert.match(page, /<h2 id="top-recommendations-title">RECOMMENDED CHARTS<\/h2>/);
  assert.match(page, /aria-label=\{activeMode === "coop" \? "Player count" : "Official difficulty"\}/);
  assert.match(page, /activeMode === "coop" \? "All player types" : "All difficulties"/);
  assert.match(page, /recommendationDifficultyOptions/);
  assert.match(page, /recommendationsForDifficulty\(activeMode, mode, effectiveDifficulty\)/);
  assert.match(page, /type RecommendationView,[\s\S]*from "\.\.\/\.\.\/lib\/page-view-state"/);
  assert.match(page, /<h2 id="top-scores-title">TOP 50 PUMBILITY SCORES<\/h2>/);
  assert.match(page, /scores=\{mode\?\.topScores \?\? \[\]\}/);
  assert.doesNotMatch(page, /Top 20 Pumbility opportunities|Top 20 farmable charts/);
  assert.doesNotMatch(page, /FULL MATCHING SET|function CandidateRow|scoreProjectionEvidenceLabel/);
  assert.doesNotMatch(page, /chart\.projectedScore\.toLocaleString/);
  assert.doesNotMatch(css, /\.all-candidates|\.candidate-(?:row|list|copy|jacket|metric|search|heading)/);
  assert.match(css, /\.recommendation-section-heading h2 \{[^}]*font-size: 10px;[^}]*font-weight: 800;[^}]*letter-spacing: 0\.08em;/);
});

test("chart video links are accessible external links on every requested surface", async () => {
  const [component, helper, recommendations, tierList] = await Promise.all([
    readFile(
      path.join(process.cwd(), "app", "_components", "chart-video-link.tsx"),
      "utf8",
    ),
    readFile(path.join(process.cwd(), "lib", "chart-videos.ts"), "utf8"),
    readFile(
      path.join(process.cwd(), "app", "recommendations", "page.tsx"),
      "utf8",
    ),
    readFile(
      path.join(process.cwd(), "app", "tier-list", "page.tsx"),
      "utf8",
    ),
  ]);

  assert.match(component, /const href = getChartVideoUrl\(chartId\)/);
  assert.match(component, /if \(!href\) return null/);
  assert.match(component, /`Watch \$\{songName\} \$\{difficulty\} chart on YouTube`/);
  assert.match(component, /href=\{href\}/);
  assert.match(component, /rel="noopener noreferrer"/);
  assert.match(component, /target="_blank"/);
  assert.match(component, /<svg aria-hidden="true" focusable="false"/);
  assert.match(helper, /export function getChartVideoUrl\(chartId: string\): string \| null/);
  assert.match(helper, /YOUTUBE_VIDEO_ID\.test\(videoId\)/);
  assert.match(helper, /`https:\/\/www\.youtube\.com\/watch\?v=\$\{videoId\}`/);

  assert.match(
    recommendations,
    /className="recommendation-leading"[\s\S]*<ChartVideoLink[\s\S]*chartId=\{chart\.chartId\}[\s\S]*difficulty=\{chart\.difficulty\}[\s\S]*songName=\{chart\.songName\}[\s\S]*variant="recommendation"/,
  );
  assert.match(
    tierList,
    /className="chart-art-rail"[\s\S]*<ChartVideoLink[\s\S]*chartId=\{chart\.chartId\}[\s\S]*variant="tier"/,
  );
  assert.match(
    tierList,
    /className="chart-dialog-art-rail"[\s\S]*<ChartVideoLink[\s\S]*chartId=\{chart\.chartId\}[\s\S]*variant="dialog"/,
  );
  const compactChartCard = tierList.slice(
    tierList.indexOf("function CompactChartCard"),
    tierList.indexOf("function CompactChartGrid"),
  );
  assert.doesNotMatch(compactChartCard, /ChartVideoLink/);
  assert.doesNotMatch(component, /"compact-tier"/);
});

test("NEVSISTER catalog has the complete validated chart inventory", async () => {
  const rawCatalog = await readFile(
    path.join(process.cwd(), "lib", "data", "nevsister-chart-videos.json"),
    "utf8",
  );
  const catalog = JSON.parse(rawCatalog) as {
    schemaVersion: number;
    channelId: string;
    charts: Record<string, string>;
  };
  const catalogIds = Object.keys(catalog.charts).sort();

  assert.equal(catalog.schemaVersion, 1);
  assert.equal(catalog.channelId, "UCicVRsgv4iIhZGZcbx7xUkw");
  assert.equal(catalogIds.length, 2712);
  assert.equal(new Set(catalogIds).size, catalogIds.length);
  for (const [chartId, videoId] of Object.entries(catalog.charts)) {
    assert.match(chartId, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
    assert.match(videoId, /^[A-Za-z0-9_-]{11}$/);
  }
});

test("chart video controls use existing card tracks and out-of-flow positioning", async () => {
  const css = await readFile(path.join(process.cwd(), "app", "globals.css"), "utf8");
  const mobileStyles = css.slice(css.indexOf("@media (max-width: 560px)"));

  assert.match(css, /\.recommendation-card \{[^}]*grid-template-columns: 34px 62px minmax\(0, 1fr\) max-content;[^}]*padding: 15px 18px;/);
  assert.match(css, /\.recommendation-leading \{[^}]*height: 62px;[^}]*position: relative;[^}]*width: 34px;/);
  assert.match(css, /\.chart-card \{[^}]*grid-template-columns: 58px minmax\(0, 1fr\) 104px;[^}]*min-height: 86px;/);
  assert.match(css, /\.chart-art-rail \{[^}]*height: 58px;[^}]*position: relative;[^}]*width: 58px;/);
  assert.match(css, /\.chart-video-link \{[^}]*position: absolute;/);
  assert.doesNotMatch(css, /\.chart-video-link-compact-tier/);
  assert.match(css, /\.chart-video-link-dialog \{[^}]*left: 50%;[^}]*transform: translateX\(-50%\);/);
  assert.match(css, /\.chart-video-link-recommendation \{[^}]*left: 50%;[^}]*transform: translateX\(-50%\);/);
  assert.match(css, /\.recommendation-rank \{[^}]*text-align: center;[^}]*width: 100%;/);

  assert.match(mobileStyles, /\.recommendation-card \{[^}]*grid-template-columns: 22px 48px minmax\(0, 1fr\) max-content;[^}]*padding: 14px 10px;/);
  assert.match(mobileStyles, /\.recommendation-leading \{[^}]*height: 48px;[^}]*width: 22px;/);
  assert.match(mobileStyles, /\.chart-card \{[^}]*grid-template-columns: 48px minmax\(0, 1fr\);[^}]*padding: 12px 11px;/);
  assert.match(mobileStyles, /\.chart-art-rail \{[^}]*height: 48px;[^}]*width: 48px;/);
  assert.match(mobileStyles, /\.chart-dialog-art-rail \{[^}]*height: 102px;[^}]*width: 62px;/);
  assert.match(mobileStyles, /\.chart-video-link-dialog \{[^}]*height: 36px;[^}]*top: 64px;/);
});

test("recommendation page renders cache before a deduplicated player refresh", async () => {
  const page = await readFile(
    path.join(process.cwd(), "app", "recommendations", "page.tsx"),
    "utf8",
  );

  assert.match(page, /await loadCached\(\);\s*await refresh\(\);/);
  assert.match(page, /\/api\/recommendations\/refresh\?playerKey=/);
  assert.match(page, /\/api\/recommendations\/refresh\?jobId=/);
  assert.match(page, /Showing cached recommendations because score refresh failed/);
  assert.match(page, /if \(cachedLoaded\) \{\s*setRefreshWarning/);
  assert.match(page, /className="recommendation-notice stale-notice" role="status"/);
  assert.match(page, /playersPayload\?\.refreshSupported === false/);
  assert.match(page, /const deadline = Date\.now\(\) \+ 30_000/);
  assert.doesNotMatch(page, /Legacy snapshot generated/);
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
        singles: { eligible: false, validScoreCount: 0, topScores: [], topRecommendations: [] },
        doubles: { eligible: false, validScoreCount: 0, topScores: [], topRecommendations: [] },
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
    schemaVersion: 20,
    generatedAtUtc: "2026-08-08T00:00:00Z",
    method: {},
    charts: [],
    players: [],
  };

  assert.throws(
    () => validateLocalRecommendationIndex(payload),
    (error: unknown) => error instanceof LocalRecommendationsValidationError
      && /Regenerate schema 23 recommendations/.test(error.message),
  );
});

test("local recommendation schema validates privacy-safe Top 50 rows", () => {
  const topScore = {
    mode: "Singles",
    songName: "Test Song",
    difficulty: "S21",
    type: "Single",
    level: 21,
    chartId: "test-song-s21",
    imageUrl: null,
    noteCount: null,
    stepArtist: null,
    bpmMin: null,
    bpmMax: null,
    estimatedDifficulty: null,
    difficultyDelta: null,
    difficultyCi95Low: null,
    difficultyCi95High: null,
    nContributors: null,
    phoenix1Contributors: null,
    phoenix2Contributors: null,
    evidenceStatus: null,
    pumbility: 350.25,
    grade: "SSS+",
    plate: "Perfect Game",
    plateCode: "PG",
  };
  const { pumbility: _pumbility, ...topScoreWithoutPumbility } = topScore;
  const payload = {
    schemaVersion: 23,
    generatedAtUtc: "2026-08-08T00:00:00Z",
    method: {},
    charts: [],
    players: [{
      playerKey: "opaque",
      username: "PLAYER",
      displayName: "PLAYER",
      modes: {
        singles: { topScores: [topScore] },
        doubles: { topScores: [] },
        coop: { topScores: [{
          ...topScoreWithoutPumbility,
          mode: "Co-op",
          difficulty: "CoOp3",
          type: "CoOp",
          level: 3,
          chartId: "test-song-coop3",
          coopRating: 121.6,
        }] },
      },
    }],
  };

  assert.equal(validateLocalRecommendationIndex(payload).schemaVersion, 23);
  const privatePayload = structuredClone(payload);
  Object.assign(privatePayload.players[0].modes.singles.topScores[0], { rawScore: 1_000_000 });
  assert.throws(
    () => validateLocalRecommendationIndex(privatePayload),
    LocalRecommendationsValidationError,
  );
  const oversizedPayload = structuredClone(payload);
  oversizedPayload.players[0].modes.singles.topScores = Array.from(
    { length: 51 },
    () => structuredClone(topScore),
  );
  assert.throws(
    () => validateLocalRecommendationIndex(oversizedPayload),
    LocalRecommendationsValidationError,
  );
});

test("global refresh controls live only on the hidden Jonathan page", async () => {
  const [tierList, refreshMeta, controls, page] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "tier-list", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "_components", "refresh-meta.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "jonathan", "JonathanControls.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "jonathan", "page.tsx"), "utf8"),
  ]);

  assert.doesNotMatch(tierList, /Administrator refresh key|Refresh Rankings|X-Analysis-Run-Secret|Last completed/);
  assert.match(tierList, /<RefreshMeta/);
  assert.match(refreshMeta, /\{label\}:/);
  assert.match(refreshMeta, /refresh-meta-delayed/);
  assert.match(refreshMeta, /refresh-delay-warning/);
  assert.match(refreshMeta, /min-height|loadingLabel/);
  assert.match(controls, /X-Jonathan-Password/);
  assert.match(controls, /\/api\/jonathan\/refresh\?mode=\$\{mode\}/);
  assert.match(controls, /"incremental"/);
  assert.match(controls, /"full"/);
  assert.match(controls, /window\.confirm/);
  assert.match(page, /index: false/);
  assert.match(page, /follow: false/);
});

test("recommendation refresh metadata distinguishes the model from player scores", async () => {
  const [page, refreshMeta, css] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "_components", "refresh-meta.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);

  assert.match(page, /const MODEL_DELAY_THRESHOLD_MS = 26 \* 60 \* 60 \* 1000/);
  assert.match(page, /currentModelGeneratedAtUtc[\s\S]*modelGeneratedAtUtc/);
  assert.match(page, /label="Model updated"/);
  assert.match(page, /label="Player scores synced"/);
  assert.match(page, /generatedAtUtc=\{playerSyncedAt\}/);
  assert.match(refreshMeta, /Math\.max\(0, nowMs - generatedAtMs\) > delayedAfterMs/);
  assert.match(css, /\.refresh-meta-delayed b, \.refresh-delay-warning \{ color: #f3ce55; \}/);
  assert.match(css, /\.stale-notice \{[^}]*display: flex;/);
});

test("published estimated difficulties truncate to whole numbers everywhere", async () => {
  const pages = await Promise.all([
    readFile(path.join(process.cwd(), "app", "tier-list", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
  ]);

  assert.equal(formatEstimatedDifficulty(10.8), "10");
  assert.equal(formatEstimatedDifficulty(17.99), "17");
  assert.equal(formatEstimatedDifficulty(18.0), "18");
  assert.equal(truncateEstimatedDifficulty(20.89), 20);
  for (const page of pages) {
    assert.match(page, /formatEstimatedDifficulty\(chart\.estimatedDifficulty\)/);
    assert.doesNotMatch(page, /estimatedDifficulty\.toFixed\(/);
  }
});

test("tier list uses compact segmented controls for grouping and layout", async () => {
  const [page, css] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "tier-list", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);

  assert.match(page, /type GroupingView = "tiers" \| "estimated"/);
  assert.match(page, /useState<GroupingView>\("estimated"\)/);
  assert.match(page, /Scoring Difficulty Tier List/);
  assert.doesNotMatch(page, /Combined scoring tier list|Scoring-based Tier List/);
  assert.match(page, /<span>Estimated<br \/>Difficulty<\/span>/);
  assert.match(page, /Tier Bands/);
  assert.match(page, /aria-label="Difficulty grouping"[\s\S]*className=\{`view-switcher\$\{activeMode === "coop" \? " single-option" : ""\}`\}[\s\S]*role="group"/);
  assert.match(page, /aria-label="Chart layout" className="view-switcher" role="group"/);
  assert.match(page, /aria-pressed=\{activeMode === "coop" \|\| groupingView === "estimated"\}/);
  assert.match(css, /\.results-switchers \{[^}]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/);
  assert.match(css, /\.view-switcher button \{[^}]*font-size: clamp\(6px, 2vw, 8px\);/);
  assert.match(css, /\.view-switcher button \{[^}]*line-height: 1\.15;[^}]*min-height: 42px;[^}]*white-space: normal;/);
  assert.match(css, /max-width: calc\(100vw - 24px\)/);
  assert.match(page, /truncateEstimatedDifficulty\(chart\.estimatedDifficulty\)/);
  assert.match(page, /\.sort\(\(\[left\], \[right\]\) => left - right\)/);
  assert.doesNotMatch(page, /Grouped by truncated one-decimal estimate|easier to score|harder to score/);
  assert.doesNotMatch(page, /showUnrated|Include unrated|unrated-toggle/);
  assert.match(page, /className="search-field"[\s\S]*?className="level-field"/);
  assert.match(page, /<span>Search songs or step artists<\/span>/);
  assert.match(page, /placeholder="Sorceress Elise"/);
  assert.match(page, /headingId="unrated-charts" label="Unrated"/);
  assert.match(css, /\.filter-bar \{[^}]*grid-template-columns: minmax\(0, 2fr\) minmax\(120px, 1fr\);/);
  assert.match(css, /\.tier-list-page \{ --tier-list-gap: 16px; \}/);
  assert.match(css, /\.results-controls \{[^}]*padding: var\(--tier-list-gap\) 2px;/);
  assert.match(css, /\.tiers \{[^}]*gap: 24px;/);
  assert.match(css, /\.page-title-hero h1 \{[^}]*font-size: clamp\(14px, 4\.8vw, 20px\);[^}]*white-space: nowrap;/);
});

test("tier list compact layout uses art-only buttons and a details dialog", async () => {
  const [page, css] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "tier-list", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);

  assert.match(page, /type LayoutView = "detailed" \| "compact"/);
  assert.match(page, /useState<LayoutView>\("compact"\)/);
  assert.match(page, />Compact<\/button>/);
  assert.match(page, />Detailed<\/button>/);
  assert.match(page, /className="compact-chart-card"/);
  assert.match(page, /className="compact-chart-button"/);
  assert.doesNotMatch(page, /function CompactChartCard[\s\S]*?<h3>\{chart\.songName\}<\/h3>/);
  assert.match(page, /role="dialog"/);
  assert.match(page, /aria-modal="true"/);
  assert.match(page, /chart-difficulty-badge/);
  assert.match(page, /compact=\{layoutView === "compact"\}/);
  assert.match(page, /<TierDivider[\s\S]*headingId=\{sectionId\}[\s\S]*label=\{label\}/);
  assert.doesNotMatch(page, /<TierDivider[^>]*(?:count|detail)=/);
  const tierDivider = page.slice(page.indexOf("function TierDivider"), page.indexOf("function ChartDetailDialog"));
  assert.doesNotMatch(tierDivider, /tier-divider-detail|tier-count|\{count\}|\{detail\}/);
  assert.doesNotMatch(css, /\.tier, \.unrated-section \{[^}]*background:/);
  assert.doesNotMatch(css, /\.tier, \.unrated-section \{[^}]*border:/);
  assert.doesNotMatch(css, /\.unrated-section header/);
  assert.match(css, /\.tier-divider-leading \{[^}]*flex: 0 0 24px;/);
  assert.match(css, /\.tier-divider-trailing \{[^}]*flex: 1 1 auto;/);
  assert.match(css, /\.tier-divider h2 \{[^}]*font-size: 14px;[^}]*font-weight: 800;[^}]*letter-spacing: 0\.03em;[^}]*text-transform: uppercase;/);
  assert.match(css, /@media \(max-width: 560px\)[\s\S]*\.tier-divider h2 \{ font-size: 14px;/);
  assert.match(css, /\.compact-chart-grid \{[^}]*grid-template-columns: repeat\(auto-fill, minmax\(84px, 96px\)\);/);
  assert.match(css, /@media \(max-width: 560px\)[\s\S]*\.compact-chart-grid \{[^}]*grid-template-columns: repeat\(5, minmax\(0, 1fr\)\);/);
  assert.match(css, /@media \(max-width: 389px\)[\s\S]*\.compact-chart-grid \{[^}]*grid-template-columns: repeat\(4, minmax\(0, 1fr\)\);/);
  assert.doesNotMatch(css, /\.compact-chart-card \{[^}]*background:/);
  assert.doesNotMatch(css, /\.compact-chart-card \{[^}]*border:/);
  assert.match(css, /\.compact-jacket \{[^}]*aspect-ratio: 1;/);
});

test("tier list chart details provide local mode-specific what-if estimates", async () => {
  const [page, types, css] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "tier-list", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "lib", "types.ts"), "utf8"),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);
  const whatIfComponent = page.slice(
    page.indexOf("function WhatIfDifficulty"),
    page.indexOf("function ChartDetails"),
  );
  const chartDetails = page.slice(
    page.indexOf("function ChartDetails"),
    page.indexOf("function ChartCard"),
  );

  assert.match(types, /whatIfEstimates\?: Array<\{\s*level: number;\s*estimatedDifficulty: number \| null;\s*\}> \| null;/);
  assert.match(whatIfComponent, /const prefix = chart\.type === "Single" \? "S" : "D";/);
  assert.match(whatIfComponent, /<option value="">\{prefix\}\?\?<\/option>/);
  assert.match(whatIfComponent, /disabled=\{estimate\.estimatedDifficulty === null\}/);
  assert.match(whatIfComponent, /unavailable/);
  assert.match(whatIfComponent, /formatEstimatedDifficulty\(selectedEstimate\)/);
  assert.doesNotMatch(whatIfComponent, /fetch\s*\(/);
  assert.match(chartDetails, /<WhatIfDifficulty chart=\{chart\} \/>/);

  assert.match(css, /\.chart-card \{[^}]*grid-template-columns: 58px minmax\(0, 1fr\) 104px;[^}]*min-height: 86px;[^}]*padding: 13px 18px;/);
  assert.match(css, /\.chart-dialog-body \{[^}]*grid-template-columns: 96px minmax\(0, 1fr\);/);
  assert.match(css, /\.chart-dialog-body \.delta \{[^}]*grid-column: 2;[^}]*min-height: 0;[^}]*padding: 13px 0 0;/);
  assert.match(css, /\.what-if-control \{[^}]*font-size: 8px;[^}]*position: absolute;[^}]*right: calc\(100% \+ 19px\);[^}]*white-space: nowrap;/);
  assert.match(css, /\.what-if-control select \{[^}]*width: calc\(6ch \+ 16px\);/);
  assert.match(css, /\.chart-dialog-body \.what-if-control \{[^}]*bottom: 0;[^}]*right: 0;/);
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
  assert.match(recommendations, /chart-difficulty-badge chart-difficulty-\$\{chart\.type\.toLowerCase\(\)\}[\s\S]*\{chart\.level\}/);
  assert.match(css, /--chart-single-border: #ff4a4a;/);
  assert.match(css, /--chart-double-border: #39d96a;/);
  assert.match(css, /--chart-coop-border: #f3ce55;/);
  assert.match(css, /\.chart-art\[data-chart-type="Single"\] \{ border: 1px solid var\(--chart-single-border\); \}/);
  assert.match(css, /\.chart-art\[data-chart-type="Double"\] \{ border: 1px solid var\(--chart-double-border\); \}/);
  assert.match(css, /\.chart-art\[data-chart-type="CoOp"\] \{ border: 1px solid var\(--chart-coop-border\); \}/);
  assert.match(css, /\.recommendation-jacket \.chart-difficulty-coop \{ background: #d5a91b; color: #171207; \}/);
});

test("recommendation cards show a compact grade and plate goal", async () => {
  const [page, css] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);

  assert.match(page, /`Goal: \$\{chart\.projectedGrade\} \$\{chart\.projectedPlateCode\}`/);
  assert.match(page, /className="recommendation-goal"/);
  assert.match(page, /<b>\{goal\}<\/b>/);
  assert.doesNotMatch(page, /GRADE_GOAL_SCORES|PLATE_CRITERIA|misses|goal\.criterion|goal\.summary/);
  assert.match(css, /\.recommendation-value \{[^}]*border-left: 1px solid var\(--line\);[^}]*padding-left: 18px;/);
  assert.doesNotMatch(css, /\.recommendation-goal \{[^}]*border-top/);
  assert.match(css, /\.recommendation-goal \{[^}]*margin-top: 1px;/);
});

test("projected gain opens an accessible total Pumbility popup", async () => {
  const [page, css] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);

  assert.match(
    page,
    /<button[\s\S]*aria-controls=\{pumbilityPopupId\}[\s\S]*aria-expanded=\{pumbilityOpen\}[\s\S]*className="recommendation-pumbility-trigger"[\s\S]*<span>projected gain<\/span>[\s\S]*<strong>\{projectedGain\}<\/strong>[\s\S]*<\/button>/,
  );
  assert.match(page, /<span>\{projectedRatingLabel\}<\/span>/);
  assert.match(page, /pumbilityLabel\(expectedRating\)/);
  assert.match(page, /document\.addEventListener\("pointerdown", closeOnOutsideClick\)/);
  assert.match(page, /event\.key === "Escape"/);
  assert.match(css, /\.recommendation-pumbility-popup \{[^}]*bottom: calc\(100% \+ 8px\);[^}]*position: absolute;[^}]*right: -4px;/);
  assert.match(css, /\.recommendation-pumbility-popup \{[^}]*width: max-content;/);
  assert.doesNotMatch(css, /\.recommendation-pumbility-popup::after/);
  assert.doesNotMatch(css, /\.recommendation-pumbility-popup \{[^}]*min-width:/);
  assert.match(css, /\.recommendation-list \{[^}]*overflow: visible;/);
});

test("mobile recommendation cards keep gain on the right and show estimated difficulty", async () => {
  const page = await readFile(
    path.join(process.cwd(), "app", "recommendations", "page.tsx"),
    "utf8",
  );
  const css = await readFile(path.join(process.cwd(), "app", "globals.css"), "utf8");

  assert.match(page, /chart\.stepArtist \|\| "Unknown step artist"/);
  assert.match(page, /formatBpm\(chart\.bpmMin, chart\.bpmMax\)/);
  assert.match(page, /bpm \? <> · \{bpm\}<\/> : null/);
  assert.match(page, /const estimate = isCoop[\s\S]*formatEstimatedDifficulty\(chart\.estimatedDifficulty\)[\s\S]*formatEstimatedDifficulty\(chart\.estimatedDifficulty\)/);
  assert.match(page, /<b> · \{estimate\} estimate<\/b>/);
  assert.doesNotMatch(page, /official<\/b>/);
  assert.doesNotMatch(page, /formula expected/);
  assert.match(css, /grid-template-columns: 34px 62px minmax\(0, 1fr\) max-content/);
  assert.match(css, /grid-template-columns: 22px 48px minmax\(0, 1fr\) max-content/);
  assert.match(css, /\.recommendation-card \{[^}]*align-items: start;/);
  assert.doesNotMatch(css, /\.recommendation-card \{[^}]*min-height:/);
  assert.match(css, /\.recommendation-value \{[^}]*align-self: start;[^}]*height: 62px;[^}]*justify-content: flex-start;/);
  assert.match(css, /@media \(max-width: 560px\)[\s\S]*\.recommendation-value \{[^}]*align-self: start;[^}]*height: 48px;[^}]*justify-content: flex-start;/);
  assert.match(css, /\.recommendation-jacket \.chart-difficulty-badge \{[^}]*height: 20px;/);
  assert.match(css, /\.recommendation-copy \{[^}]*height: 62px;/);
  assert.match(css, /@media \(max-width: 560px\)[\s\S]*\.recommendation-copy \{[^}]*height: 48px;/);
  assert.match(css, /\.recommendation-copy > p \{[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap;/);
  assert.match(css, /\.recommendation-tags \{[^}]*flex-wrap: nowrap;[^}]*margin-top: auto;/);
  assert.match(css, /\.recommendation-mode-tabs button span \{[^}]*white-space: nowrap;/);
  assert.match(css, /@media \(max-width: 560px\)[\s\S]*\.recommendation-mode-tabs button \{[^}]*gap: 4px;[^}]*min-width: 0;[^}]*padding: 8px 3px;/);
});

test("recommendation player picker shares a 2-to-1 row with the view switch", async () => {
  const [page, css] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);

  assert.match(page, /<ScoreSyncLink className="player-score-sync-link" \/>/);
  assert.doesNotMatch(page, /playersPayload\.players\.length\.toLocaleString\(\).*usernames/);
  assert.doesNotMatch(page, /Only usernames shared with this community tool are listed/);
  assert.match(css, /\.player-picker-meta \.player-score-sync-link \{[^}]*background: transparent;[^}]*color: #7d867e;[^}]*text-decoration: underline;/);
  assert.match(css, /\.recommendations-page \{ --recommendations-stack-gap: 16px; \}/);
  assert.match(css, /\.recommendations-hero \{[^}]*padding: 0 24px var\(--recommendations-stack-gap\);/);
  assert.match(css, /\.recommendation-mode-row \{[^}]*margin-bottom: var\(--recommendations-stack-gap\);/);
  assert.match(css, /\.top-recommendations \{[^}]*margin-top: var\(--recommendations-stack-gap\);/);
  assert.match(page, /role="switch"/);
  assert.match(page, /aria-checked=\{recommendationView === "top50"\}/);
  assert.match(page, /<span>Recommendations<\/span>[\s\S]*<span>\{activeMode === "coop" \? "Scores" : "Top 50"\}<\/span>/);
  assert.match(css, /\.player-picker \{[^}]*grid-template-columns: minmax\(0, 2fr\) minmax\(0, 1fr\);[^}]*width: 100%;/);
  assert.match(css, /\.player-picker-meta \{[^}]*grid-column: 1 \/ -1;/);
  assert.match(css, /@media \(max-width: 560px\)[\s\S]*\.recommendations-page \{ --recommendations-stack-gap: 14px; \}/);
  assert.match(css, /@media \(max-width: 560px\)[\s\S]*\.recommendations-hero \{[^}]*padding: 0 12px var\(--recommendations-stack-gap\);/);
});

test("Top 50 cards expose PIU result data and open an accessible detail dialog", async () => {
  const [page, css] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);

  assert.match(page, /function TopScoreCard/);
  assert.match(page, /className="top-score-rank">#\{rank\}/);
  assert.match(page, /\{score\.songName\}/);
  assert.doesNotMatch(page, /className="top-score-copy"/);
  assert.match(page, /\{score\.grade \|\| "—"\}/);
  assert.match(page, /\{score\.plateCode \|\| "—"\}/);
  assert.match(
    page,
    /className="top-score-result">\s*<span>\s*<b>\{score\.grade \|\| "—"\}<\/b>\s*<small>\{score\.plateCode \|\| "—"\}<\/small>/,
  );
  assert.match(page, /function topScoreRating\(score: RecommendationTopScore\)/);
  assert.match(page, /pumbilityLabel\(rating\)/);
  assert.match(page, /className="chart-dialog top-score-dialog"/);
  assert.match(page, /aria-modal="true"/);
  assert.match(page, /document\.body\.style\.overflow = "hidden"/);
  assert.match(page, /previouslyFocused\?\.focus\(\)/);
  assert.match(page, /event\.key === "Escape"/);
  assert.match(page, /event\.key !== "Tab"/);
  assert.match(page, /event\.target === event\.currentTarget/);
  assert.match(page, /<ChartVideoLink[\s\S]*variant="dialog"/);
  assert.match(css, /\.top-score-grid \{[^}]*grid-template-columns: repeat\(5, minmax\(0, 1fr\)\);/);
  assert.match(css, /\.top-score-card \{[^}]*border-radius: 0;/);
  assert.match(css, /\.top-score-result > span \{[^}]*display: flex;[^}]*gap: 4px;/);
  assert.match(css, /\.top-score-rank \{[^}]*font-size: 11px;/);
  assert.match(css, /\.top-score-jacket \.chart-difficulty-badge \{[^}]*font-size: 11px;/);
  assert.match(css, /\.top-score-result > span b \{[^}]*font-size: 13px;/);
  assert.match(css, /\.top-score-result > span small \{[^}]*font-size: 10px;/);
  assert.match(css, /\.top-score-result > strong \{[^}]*font-size: 13px;/);
  assert.match(css, /@media \(max-width: 560px\)[\s\S]*\.top-score-grid \{[^}]*grid-template-columns: repeat\(3, minmax\(0, 1fr\)\);/);
});

test("limited-data presentation uses the shared 20-player boundary", async () => {
  const pages = await Promise.all([
    readFile(path.join(process.cwd(), "app", "tier-list", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
  ]);

  assert.equal(LIMITED_DATA_CONTRIBUTOR_THRESHOLD, 20);
  assert.equal(hasLimitedData(19), true);
  assert.equal(hasLimitedData(20), false);
  for (const page of pages) {
    assert.match(page, /hasLimitedData\(chart\.nContributors\)/);
    assert.doesNotMatch(page, /evidenceStatus\.toLowerCase\(\)/);
  }
});

test("all Phoenix 2 Overall rank emblems are vendored locally", async () => {
  const files = await readdir(
    path.join(process.cwd(), "public", "images", "phoenix2-ranks"),
  );
  assert.deepEqual(
    files.sort(),
    Array.from({ length: 37 }, (_, level) =>
      `pumbility_${String(level).padStart(2, "0")}.webp`,
    ),
  );
  assert.equal(pumbilityProgress("overall", 0).rungIndex, 0);
  assert.equal(pumbilityProgress("overall", 20_000).rungIndex, 36);
});

test("Recommendations and Tier List use the same shared header", async () => {
  const pages = await Promise.all([
    readFile(path.join(process.cwd(), "app", "tier-list", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
  ]);
  assert.match(pages[0], /<SiteHeader active="tier-list" \/>/);
  assert.match(pages[1], /<SiteHeader active="recommendations" \/>/);
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
    coop: [],
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
    schemaVersion: 5,
    generatedAtUtc: "2026-08-08T00:00:00Z",
    mix: { key: "combined", apiValue: "Phoenix+Phoenix2", label: "Phoenix 1 + 2" },
    summary: { scriptVersion: "test", method: {}, coverage: {}, modes: {} },
    singles: [],
    doubles: [],
    coop: [],
    relativeGroups: [],
    effectBands: [],
  };
  assert.equal(validateLocalAnalysisPayload(payload, "combined").mix.key, "combined");
  assert.throws(
    () => validateLocalAnalysisPayload({ ...payload, schemaVersion: 4 }, "combined"),
    /unsupported schema/,
  );
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
            filterCandidates: [],
            topScores: [],
            topRecommendations: [],
          },
          singles: {
            eligible: true,
            validScoreCount: 30,
            filterCandidates: [],
            topScores: [],
            topRecommendations: [],
          },
          doubles: {
            eligible: false,
            validScoreCount: 4,
            filterCandidates: [],
            topScores: [],
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
      eligibility: { singles: true, doubles: false, coop: false },
      scoreProgress: {
        singles: { validScoreCount: 30, requiredScoreCount: 30 },
        doubles: { validScoreCount: 4, requiredScoreCount: 30 },
      },
    },
  ]);
  assert.equal("modes" in response.players[0], false);
});

test("recommendation readiness explains missing score history", async () => {
  const [page, css] = await Promise.all([
    readFile(path.join(process.cwd(), "app", "recommendations", "page.tsx"), "utf8"),
    readFile(path.join(process.cwd(), "app", "globals.css"), "utf8"),
  ]);

  assert.match(page, /Play more charts to unlock recommendations/);
  assert.match(page, /recommendation-readiness-progress/);
  assert.match(page, /Need to play \$\{remaining\} more \$\{label\}/);
  assert.match(page, /recommendation-warning-icon/);
  assert.match(css, /\.recommendation-readiness-grid \{[^}]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/);
  assert.match(css, /@media \(max-width: 560px\)[\s\S]*\.recommendation-readiness-grid \{ grid-template-columns: 1fr; \}/);
});

test("manual recommendations cap chart difficulty at 1.0 above the scoring rating", () => {
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
      chart("upper-edge", 21.5, 21),
      chart("too-hard", 21.5000000001, 21),
      chart("level-15", 10, 15),
    ],
    players: [],
  }, 20.5);

  const singles = response.player.modes.singles;
  const overall = response.player.modes.overall;
  assert.ok(overall);
  assert.equal(singles.projectionAvailable, false);
  assert.deepEqual(singles.candidateRange, [null, 21.5]);
  assert.deepEqual(
    (singles.filterCandidates ?? []).map((candidate) => candidate.chartId),
    ["level-16", "level-15", "rating-edge", "upper-edge", "too-hard"],
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
    (configuredFloor.player.modes.singles.filterCandidates ?? []).map((candidate) => candidate.chartId),
    ["level-17", "level-16"],
  );
  assert.deepEqual(
    configuredFloor.player.modes.singles.topRecommendations.map((candidate) => candidate.chartId),
    ["level-17"],
  );

  const twentyOfFiftyFive = recommendationsForRating({
    generatedAtUtc: "2026-08-08T00:00:00Z",
    method: {},
    charts: Array.from({ length: 55 }, (_, index) =>
      chart(`chart-${String(index).padStart(2, "0")}`, 16 + index / 100, 16),
    ),
    players: [],
  }, 20.5).player.modes.singles.topRecommendations;
  assert.equal(twentyOfFiftyFive.length, 20);
});

test("official difficulty filters use the full mode-specific chart pools", () => {
  const chart = (
    chartId: string,
    type: "Single" | "Double",
    level: number,
  ): RecommendationChartEstimate => ({
    mode: type === "Single" ? "Singles" : "Doubles",
    songName: chartId,
    difficulty: `${type === "Single" ? "S" : "D"}${level}`,
    type,
    level,
    chartId,
    imageUrl: null,
    noteCount: null,
    stepArtist: null,
    estimatedDifficulty: level,
    difficultyDelta: -0.5,
    difficultyCi95Low: level,
    difficultyCi95High: level,
    nContributors: 20,
    phoenix1Contributors: 10,
    phoenix2Contributors: 10,
    evidenceStatus: "Published",
  });
  const response = recommendationsForRating({
    generatedAtUtc: "2026-08-08T00:00:00Z",
    method: {},
    charts: [
      chart("single-10", "Single", 10),
      chart("single-15", "Single", 15),
      chart("single-16", "Single", 16),
      chart("single-18", "Single", 18),
      chart("single-26", "Single", 26),
      chart("double-10", "Double", 10),
      chart("double-15", "Double", 15),
      chart("double-16", "Double", 16),
      chart("double-23", "Double", 23),
      ...Array.from({ length: 25 }, (_, index) =>
        chart(`double-24-${String(index).padStart(2, "0")}`, "Double", 24)),
      chart("double-26", "Double", 26),
    ],
    players: [],
  }, 20);
  const modes = response.player.modes;
  const overall = modes.overall;
  assert.ok(overall);

  assert.deepEqual(
    recommendationDifficultyOptions("singles", modes.singles.filterCandidates ?? []),
    ["S16", "S18", "S26"],
  );
  assert.deepEqual(
    recommendationDifficultyOptions("doubles", modes.doubles.filterCandidates ?? []),
    ["D16", "D23", "D24", "D26"],
  );
  assert.deepEqual(
    recommendationDifficultyOptions("overall", overall.filterCandidates ?? []),
    ["S16", "S18", "S26", "D16", "D23", "D24", "D26"],
  );
  assert.deepEqual(visibleRecommendations("overall", overall, "S10"), []);
  assert.equal(overall.topRecommendations.some((row) => row.level === 24), false);
  const filtered = visibleRecommendations("overall", overall, "D24");
  assert.equal(filtered.length, 25);
  assert.equal(filtered.every((row) => row.type === "Double" && row.level === 24), true);
  const gains = new Map([
    ["double-24-00", 1],
    ["double-24-01", 5],
    ["double-24-02", 3],
  ]);
  const ranked = visibleRecommendations("overall", {
    ...overall,
    filterCandidates: (overall.filterCandidates ?? []).map((row) => ({
      ...row,
      projectedGain: gains.get(row.chartId) ?? null,
    })),
  }, "D24");
  assert.deepEqual(
    ranked.slice(0, 3).map((row) => row.chartId),
    ["double-24-01", "double-24-02", "double-24-00"],
  );

  const coopChart = {
    ...(modes.singles.filterCandidates ?? [])[0],
    mode: "Co-op" as const,
    difficulty: "CoOp2",
    type: "CoOp" as const,
    level: 2,
    chartId: "coop-2",
  };
  assert.deepEqual(recommendationDifficultyOptions("coop", [coopChart]), ["2x"]);
  assert.equal(visibleRecommendations("coop", {
    ...modes.singles,
    topRecommendations: [coopChart],
  }, "All").length, 1);
});
