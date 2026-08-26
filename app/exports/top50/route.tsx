import type { NextRequest } from "next/server";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { cloneElement } from "react";
import sharp from "sharp";

import {
  hasExactTop50Scores,
  isSafeTop50JacketUrl,
  isTop50ExportMode,
  splitPumbility,
  TOP50_POSTER_HEIGHT,
  TOP50_EXPORT_SITE,
  TOP50_POSTER_WIDTH,
  top50ExportApiOrigin,
  top50ExportFilename,
  top50ExportRows,
  type Top50ExportMode,
} from "../../../lib/top50-export";
import { renderTop50Poster } from "../../../lib/top50-poster-render";
import { pumbilityProgress } from "../../../lib/pumbility-progress";
import type {
  PlayerRecommendationsResponse,
  RecommendationModeResult,
  RecommendationTopScore,
} from "../../../lib/types";


export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 60;

const API_TIMEOUT_MS = 15_000;
const JACKET_TIMEOUT_MS = 3_000;
const JACKET_TOTAL_TIMEOUT_MS = 8_000;
const JACKET_MAX_BYTES = 800_000;
const JACKET_CONCURRENCY = 8;
const JACKET_RENDER_MAX_WIDTH = 700;
const JACKET_RENDER_MAX_HEIGHT = 393;
const JACKET_CACHE_MAX_ENTRIES = 128;
const JACKET_CACHE_TTL_MS = 10 * 60_000;
const POSTER_CACHE_MAX_BYTES = 64 * 1024 * 1024;
const POSTER_CACHE_MAX_ENTRIES = 4;
const POSTER_CACHE_TTL_MS = 5 * 60_000;
const POSTER_LAYOUT_VERSION = "top50-poster-resvg-2x-v4";
const POSTER_CARD_WIDTH = 212;
const POSTER_CARD_ART_HEIGHT = 108;
const POSTER_RESULT_FONT_SIZE = 18;
const POSTER_RESULT_GAP = 5;
const POSTER_PLATE_FONT_SIZE = 12;
const POSTER_PUMBILITY_FONT_SIZE = 16;
const POSTER_PUMBILITY_GAP = 4;
const POSTER_COLUMN_STEP = 222;
const POSTER_ROW_STEP = 176;
const POSTER_GRID_LEFT = 28;
const POSTER_GRID_TOP = 193;
const PLAYER_KEY_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;
const OVERALL_RANK_EMBLEM_FILENAMES = Array.from(
  { length: 37 },
  (_, rungIndex) => `pumbility_${String(rungIndex).padStart(2, "0")}.webp`,
);

const posterFontData = Promise.all([
  readFile(join(process.cwd(), "assets", "fonts", "NotoSans-Regular.ttf")),
  readFile(join(process.cwd(), "assets", "fonts", "NotoSans-Bold.ttf")),
  readFile(join(process.cwd(), "assets", "fonts", "NotoSansSymbols2-Regular.ttf")),
]);

const overallRankEmblemDataUrlCache = new Map<number, Promise<string>>();

function overallRankEmblemDataUrl(rungIndex: number): Promise<string> {
  const filename = OVERALL_RANK_EMBLEM_FILENAMES[rungIndex];
  if (!filename) throw new Error("The Overall rank emblem is unavailable.");
  const cached = overallRankEmblemDataUrlCache.get(rungIndex);
  if (cached) return cached;

  const pending = readFile(join(
    process.cwd(),
    "public",
    "images",
    "phoenix2-ranks",
    filename,
  )).then(async (source) => {
    const png = await sharp(source).png().toBuffer();
    return `data:image/png;base64,${png.toString("base64")}`;
  }).catch((error: unknown) => {
    overallRankEmblemDataUrlCache.delete(rungIndex);
    throw error;
  });
  overallRankEmblemDataUrlCache.set(rungIndex, pending);
  return pending;
}

type PosterScore = RecommendationTopScore & { jacketData: Buffer | null };

type JacketCacheEntry = {
  expiresAt: number;
  promise: Promise<Buffer | null>;
};

const processedJacketCache = new Map<string, JacketCacheEntry>();

type PosterRenderResult = Awaited<ReturnType<typeof renderTop50Poster>> & {
  jacketFetchNormalizeMs: number;
};

type PosterCacheEntry = {
  byteLength: number;
  expiresAt: number;
  pending: boolean;
  promise: Promise<PosterRenderResult>;
};

type PosterCacheStatus = "coalesced" | "hit" | "miss";

const completedPosterCache = new Map<string, PosterCacheEntry>();
let completedPosterCacheBytes = 0;

function jsonError(message: string, status: number): Response {
  return Response.json(
    { error: message },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    },
  );
}

async function fetchWithTimeout(
  url: URL,
  timeoutMs: number,
): Promise<{ response: Response; body: string }> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      cache: "no-store",
      headers: { Accept: "application/json" },
      redirect: "error",
      signal: controller.signal,
    });
    return { response, body: await response.text() };
  } finally {
    clearTimeout(timeout);
  }
}

function isRecommendationPayload(value: unknown): value is PlayerRecommendationsResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const player = (value as { player?: unknown }).player;
  return Boolean(player && typeof player === "object" && !Array.isArray(player));
}

function responseError(response: Response, responseBody: string): string {
  const body = responseBody.trim();
  try {
    const parsed = JSON.parse(body) as { error?: unknown };
    if (typeof parsed.error === "string" && parsed.error.trim()) return parsed.error;
  } catch {
    // Fall through to the bounded text response.
  }
  return body.replace(/\s+/g, " ").slice(0, 180)
    || `Recommendation service returned HTTP ${response.status}.`;
}

async function readBoundedImage(response: Response): Promise<Uint8Array> {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > JACKET_MAX_BYTES) {
    throw new Error("Jacket image is too large.");
  }
  if (!response.body) throw new Error("Jacket image is empty.");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > JACKET_MAX_BYTES) {
      await reader.cancel();
      throw new Error("Jacket image is too large.");
    }
    chunks.push(value);
  }
  const image = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    image.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return image;
}

function detectedImageContentType(image: Uint8Array): string | null {
  if (image.length >= 8
    && image[0] === 0x89
    && image[1] === 0x50
    && image[2] === 0x4e
    && image[3] === 0x47
    && image[4] === 0x0d
    && image[5] === 0x0a
    && image[6] === 0x1a
    && image[7] === 0x0a) {
    return "image/png";
  }
  if (image.length >= 3
    && image[0] === 0xff
    && image[1] === 0xd8
    && image[2] === 0xff) {
    return "image/jpeg";
  }
  if (image.length >= 12
    && image[0] === 0x52
    && image[1] === 0x49
    && image[2] === 0x46
    && image[3] === 0x46
    && image[8] === 0x57
    && image[9] === 0x45
    && image[10] === 0x42
    && image[11] === 0x50) {
    return "image/webp";
  }
  return null;
}

async function loadJacketData(
  value: string,
  timeoutMs: number,
): Promise<Buffer | null> {
  if (!isSafeTop50JacketUrl(value)) return null;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(value, {
      cache: "force-cache",
      redirect: "error",
      signal: controller.signal,
    });
    if (!response.ok) return null;
    const image = await readBoundedImage(response);
    if (!detectedImageContentType(image)) return null;
    const optimized = await sharp(image, {
      failOn: "error",
      limitInputPixels: 16_000_000,
    })
      .rotate()
      .resize(JACKET_RENDER_MAX_WIDTH, JACKET_RENDER_MAX_HEIGHT, {
        fit: "inside",
        kernel: sharp.kernel.lanczos3,
        withoutEnlargement: true,
      })
      .jpeg({ chromaSubsampling: "4:4:4", quality: 94 })
      .toBuffer();
    return optimized;
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

function loadCachedJacketData(
  value: string,
  timeoutMs: number,
): Promise<Buffer | null> {
  const cacheKey = `${JACKET_RENDER_MAX_WIDTH}x${JACKET_RENDER_MAX_HEIGHT}:${value}`;
  const now = Date.now();
  const cached = processedJacketCache.get(cacheKey);
  if (cached && cached.expiresAt > now) {
    // Refresh insertion order so the bounded map behaves as an LRU cache.
    processedJacketCache.delete(cacheKey);
    processedJacketCache.set(cacheKey, cached);
    return cached.promise;
  }
  if (cached) processedJacketCache.delete(cacheKey);

  let entry: JacketCacheEntry;
  const promise = loadJacketData(value, timeoutMs).then((result) => {
    // Failed fetches should remain retryable rather than occupying the warm cache.
    if (result === null && processedJacketCache.get(cacheKey) === entry) {
      processedJacketCache.delete(cacheKey);
    }
    return result;
  });
  entry = {
    expiresAt: now + JACKET_CACHE_TTL_MS,
    promise,
  };
  processedJacketCache.set(cacheKey, entry);

  while (processedJacketCache.size > JACKET_CACHE_MAX_ENTRIES) {
    const oldestKey = processedJacketCache.keys().next().value as string | undefined;
    if (oldestKey === undefined) break;
    processedJacketCache.delete(oldestKey);
  }
  return promise;
}

async function mapWithConcurrency<T, R>(
  values: T[],
  concurrency: number,
  mapper: (value: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(values.length);
  let nextIndex = 0;
  const workers = Array.from(
    { length: Math.min(concurrency, values.length) },
    async () => {
      while (nextIndex < values.length) {
        const index = nextIndex;
        nextIndex += 1;
        results[index] = await mapper(values[index]);
      }
    },
  );
  await Promise.all(workers);
  return results;
}

async function attachJackets(scores: RecommendationTopScore[]): Promise<PosterScore[]> {
  const urls = [...new Set(scores
    .map((score) => score.imageUrl)
    .filter(isSafeTop50JacketUrl))];
  const deadline = Date.now() + JACKET_TOTAL_TIMEOUT_MS;
  const loaded = await mapWithConcurrency(urls, JACKET_CONCURRENCY, async (url) => {
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) return null;
    return loadCachedJacketData(url, Math.min(JACKET_TIMEOUT_MS, remainingMs));
  });
  const jacketByUrl = new Map(urls.map((url, index) => [url, loaded[index]]));
  return scores.map((score) => ({
    ...score,
    jacketData: score.imageUrl ? jacketByUrl.get(score.imageUrl) ?? null : null,
  }));
}

function deletePosterCacheEntry(cacheKey: string, entry: PosterCacheEntry): void {
  if (completedPosterCache.get(cacheKey) !== entry) return;
  completedPosterCache.delete(cacheKey);
  completedPosterCacheBytes = Math.max(0, completedPosterCacheBytes - entry.byteLength);
}

function prunePosterCache(now = Date.now()): void {
  for (const [cacheKey, entry] of completedPosterCache) {
    if (entry.expiresAt <= now) deletePosterCacheEntry(cacheKey, entry);
  }
  while (completedPosterCache.size > POSTER_CACHE_MAX_ENTRIES
    || completedPosterCacheBytes > POSTER_CACHE_MAX_BYTES) {
    const oldest = completedPosterCache.entries().next().value as
      | [string, PosterCacheEntry]
      | undefined;
    if (!oldest) break;
    deletePosterCacheEntry(oldest[0], oldest[1]);
  }
}

function getCachedPoster(
  cacheKey: string,
  create: () => Promise<PosterRenderResult>,
): { cacheStatus: PosterCacheStatus; promise: Promise<PosterRenderResult> } {
  const now = Date.now();
  prunePosterCache(now);
  const cached = completedPosterCache.get(cacheKey);
  if (cached) {
    completedPosterCache.delete(cacheKey);
    completedPosterCache.set(cacheKey, cached);
    return {
      cacheStatus: cached.pending ? "coalesced" : "hit",
      promise: cached.promise,
    };
  }

  let entry: PosterCacheEntry;
  const promise = Promise.resolve()
    .then(create)
    .then((result) => {
      if (completedPosterCache.get(cacheKey) === entry) {
        entry.pending = false;
        entry.byteLength = result.png.byteLength;
        completedPosterCacheBytes += entry.byteLength;
        prunePosterCache();
      }
      return result;
    })
    .catch((error: unknown) => {
      deletePosterCacheEntry(cacheKey, entry);
      throw error;
    });
  entry = {
    byteLength: 0,
    expiresAt: now + POSTER_CACHE_TTL_MS,
    pending: true,
    promise,
  };
  completedPosterCache.set(cacheKey, entry);
  prunePosterCache(now);
  return { cacheStatus: "miss", promise };
}

function posterContentCacheKey(input: {
  generatedDate: string | null;
  mode: Top50ExportMode;
  playerName: string;
  pumbility: number;
  scores: RecommendationTopScore[];
}): string {
  return createHash("sha256")
    .update(JSON.stringify({
      generatedDate: input.generatedDate,
      layoutVersion: POSTER_LAYOUT_VERSION,
      mode: input.mode,
      playerName: input.playerName,
      pumbility: input.pumbility,
      scores: input.scores,
    }))
    .digest("hex");
}

function formatDuration(milliseconds: number): string {
  return Number.isFinite(milliseconds) ? milliseconds.toFixed(1) : "0.0";
}

function modeLabel(mode: Top50ExportMode): string {
  return mode === "overall" ? "Overall" : mode === "singles" ? "Singles" : "Doubles";
}

function posterPumbility(modeResult: RecommendationModeResult, scores: RecommendationTopScore[]): number {
  if (typeof modeResult.currentTop50Pumbility === "number"
    && Number.isFinite(modeResult.currentTop50Pumbility)) {
    return modeResult.currentTop50Pumbility;
  }
  return scores.reduce((total, score) => (
    total + (typeof score.pumbility === "number" && Number.isFinite(score.pumbility)
      ? score.pumbility
      : 0)
  ), 0);
}

function PosterCard({ rank, score }: { rank: number; score: PosterScore | null }) {
  if (!score) {
    return (
      <div
        style={{
          background: "rgba(18, 27, 20, 0.44)",
          border: "1px solid rgba(255,255,255,0.045)",
          display: "flex",
          flex: "1 1 0",
          height: 166,
        }}
      />
    );
  }
  const scoreValue = (score.score as number).toLocaleString("en-US");
  const pumbility = typeof score.pumbility === "number"
    ? score.pumbility.toFixed(2)
    : "—";
  const difficultyColor = score.type === "Double" ? "#176b3a" : "#b62e35";
  return (
    <div
      style={{
        background: "transparent",
        border: "1px solid rgba(255,255,255,0.11)",
        color: "#f4f7ee",
        display: "flex",
        flex: "1 1 0",
        flexDirection: "column",
        height: 166,
        minWidth: 0,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          alignItems: "center",
          background: score.jacketData
            ? "transparent"
            : "linear-gradient(135deg, #293723, #111612)",
          display: "flex",
          height: 108,
          justifyContent: "center",
          overflow: "hidden",
          position: "relative",
          width: "100%",
        }}
      >
        {!score.jacketData ? (
          <div style={{ color: "#c8ff2e", display: "flex", fontSize: 20, fontWeight: 800, lineHeight: 1 }}>
            {score.difficulty}
          </div>
        ) : null}
        <div
          style={{
            background: "rgba(3, 6, 4, 0.82)",
            borderRadius: "0 0 6px 0",
            color: "white",
            display: "flex",
            fontSize: 17,
            fontWeight: 900,
            lineHeight: 1,
            left: 0,
            padding: "5px 7px",
            position: "absolute",
            top: 0,
          }}
        >#{rank}</div>
        <div
          style={{
            alignItems: "center",
            background: difficultyColor,
            border: "1px solid rgba(255,255,255,0.52)",
            borderRadius: 999,
            bottom: 6,
            color: "white",
            display: "flex",
            fontSize: 17,
            fontWeight: 800,
            height: 28,
            justifyContent: "center",
            lineHeight: 1,
            position: "absolute",
            right: 6,
            width: 28,
          }}
        >{score.level}</div>
      </div>
      <div
        style={{
          alignItems: "center",
          background: "rgba(13, 18, 14, 0.96)",
          borderTop: "1px solid rgba(255,255,255,0.055)",
          display: "flex",
          height: 23,
          justifyContent: "flex-start",
          overflow: "hidden",
          padding: "0 4px",
          textAlign: "left",
          width: "100%",
        }}
      >
        <div
          style={{
            color: "#d3dad4",
            display: "flex",
            flex: "1 1 auto",
            fontSize: 14,
            fontWeight: 700,
            lineHeight: 1.2,
            minWidth: 0,
            overflow: "hidden",
            textAlign: "left",
            whiteSpace: "nowrap",
          }}
        >{score.songName}</div>
      </div>
      <div
        style={{
          alignItems: "baseline",
          background: "rgba(13, 18, 14, 0.96)",
          display: "flex",
          flex: "0 0 auto",
          gap: POSTER_PUMBILITY_GAP,
          height: 35,
          justifyContent: "space-between",
          minWidth: 0,
          padding: "5px 4px 8px",
          width: "100%",
        }}
      >
        <div style={{ alignItems: "baseline", display: "flex", flex: "0 1 auto", gap: POSTER_RESULT_GAP, minWidth: 0 }}>
          <div style={{ color: "#d3dad4", display: "flex", flex: "0 0 auto", fontSize: POSTER_RESULT_FONT_SIZE, fontVariantNumeric: "tabular-nums", fontWeight: 700, lineHeight: 1, whiteSpace: "nowrap" }}>
            {scoreValue}
          </div>
          <div style={{ color: "#d3dad4", display: "flex", flex: "0 0 auto", fontSize: POSTER_RESULT_FONT_SIZE, fontWeight: 700, lineHeight: 1, whiteSpace: "nowrap" }}>
            {score.grade || "—"}
          </div>
          <div style={{ color: "#7e887f", display: "flex", flex: "0 0 auto", fontSize: POSTER_PLATE_FONT_SIZE, lineHeight: 1, whiteSpace: "nowrap" }}>
            {score.plateCode || "—"}
          </div>
        </div>
        <div style={{ color: "#c8ff2e", display: "flex", flex: "0 0 auto", fontSize: POSTER_PUMBILITY_FONT_SIZE, fontVariantNumeric: "tabular-nums", fontWeight: 700, letterSpacing: "-0.03em", lineHeight: 1, whiteSpace: "nowrap" }}>
          {pumbility}
        </div>
      </div>
    </div>
  );
}

export async function GET(request: NextRequest): Promise<Response> {
  const playerKey = request.nextUrl.searchParams.get("playerKey")?.trim() || "";
  const modeValue = request.nextUrl.searchParams.get("mode")?.trim().toLowerCase() || "";
  if (!PLAYER_KEY_PATTERN.test(playerKey)) {
    return jsonError("A valid playerKey is required.", 400);
  }
  if (!isTop50ExportMode(modeValue)) {
    return jsonError("mode must be overall, singles, or doubles.", 400);
  }

  try {
    const requestStartedAt = performance.now();
    const apiUrl = new URL("/api/recommendations", top50ExportApiOrigin(request.nextUrl.origin));
    apiUrl.searchParams.set("playerKey", playerKey);
    apiUrl.searchParams.set("mode", modeValue);
    const { response, body } = await fetchWithTimeout(apiUrl, API_TIMEOUT_MS);
    if (!response.ok) return jsonError(responseError(response, body), response.status);
    let value: unknown;
    try {
      value = JSON.parse(body);
    } catch {
      return jsonError("The recommendation service returned invalid JSON.", 502);
    }
    if (!isRecommendationPayload(value)) {
      return jsonError("The recommendation service returned an invalid player payload.", 502);
    }
    const modeResult = value.player.modes[modeValue];
    if (!modeResult || !Array.isArray(modeResult.topScores)) {
      return jsonError(`No ${modeLabel(modeValue)} Top 50 scores are available.`, 404);
    }
    const scores = modeResult.topScores.slice(0, 50);
    if (!hasExactTop50Scores(scores)) {
      return jsonError(
        "Exact Top 50 scores are still being prepared. Try again after the next score sync.",
        409,
      );
    }

    const apiCompletedAt = performance.now();
    const pumbilityValue = posterPumbility(modeResult, scores);
    const total = splitPumbility(pumbilityValue);
    const playerName = value.player.displayName?.trim()
      || value.player.username?.trim()
      || "Player";
    const generatedAt = value.playerSyncedAtUtc
      || value.recommendationsGeneratedAtUtc
      || value.generatedAtUtc;
    const cacheKey = posterContentCacheKey({
      generatedDate: generatedAt?.slice(0, 10) ?? null,
      mode: modeValue,
      playerName,
      pumbility: pumbilityValue,
      scores,
    });
    const posterCacheLookupStartedAt = performance.now();
    const cachedPoster = getCachedPoster(cacheKey, async () => {
      const jacketsStartedAt = performance.now();
      const posterScores = await attachJackets(scores);
      const jacketsCompletedAt = performance.now();
      const posterJackets = posterScores.flatMap((score, index) => {
        if (!score.jacketData) return [];
        const column = index % 5;
        const row = Math.floor(index / 5);
        return [{
          data: score.jacketData,
          height: POSTER_CARD_ART_HEIGHT,
          left: POSTER_GRID_LEFT + (column * POSTER_COLUMN_STEP),
          top: POSTER_GRID_TOP + (row * POSTER_ROW_STEP),
          width: POSTER_CARD_WIDTH,
        }];
      });
      const rows = top50ExportRows(posterScores);
      const [notoSansRegular, notoSansBold, notoSansSymbols] = await posterFontData;
      const rankEmblemDataUrl = modeValue === "overall"
        ? await overallRankEmblemDataUrl(pumbilityProgress("overall", pumbilityValue).rungIndex)
        : null;

      const rendered = await renderTop50Poster(
        <div
          style={{
            background: "transparent",
            color: "#f4f7ee",
            display: "flex",
            flexDirection: "column",
            fontFamily: '"Noto Sans", "Noto Sans Symbols 2", sans-serif',
            height: TOP50_POSTER_HEIGHT,
            padding: 28,
            width: TOP50_POSTER_WIDTH,
          }}
        >
          <div
            style={{
              alignItems: "center",
              borderBottom: "1px solid rgba(255,255,255,0.09)",
              display: "flex",
              height: 145,
              justifyContent: "space-between",
              paddingBottom: 20,
              width: "100%",
            }}
          >
            <div style={{ alignItems: "center", display: "flex", flex: "1 1 auto", minWidth: 0, overflow: "hidden" }}>
              <div
                style={{
                  alignItems: "center",
                  background: "#c8ff2e",
                  color: "#10140c",
                  display: "flex",
                  fontSize: 26,
                  fontWeight: 900,
                  height: 54,
                  justifyContent: "center",
                  marginRight: 16,
                  width: 54,
                }}
              >PF</div>
              <div style={{ display: "flex", flex: "1 1 auto", flexDirection: "column", minWidth: 0, overflow: "hidden" }}>
                <div style={{ color: "#c8ff2e", display: "flex", fontSize: 18, fontWeight: 850, letterSpacing: 2.2, lineHeight: 1.1 }}>
                  TOP 50 PUMBILITY
                </div>
                <div style={{ display: "flex", fontSize: 41, fontWeight: 850, lineHeight: 1.05, marginTop: 5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", width: "100%" }}>
                  {playerName}
                </div>
                <div style={{ color: "#8f9a8f", display: "flex", fontSize: 17, lineHeight: 1.15, marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", width: "100%" }}>
                  {modeLabel(modeValue)} · Phoenix 2{generatedAt ? ` · Scores synced ${generatedAt.slice(0, 10)}` : ""}
                </div>
              </div>
            </div>
            <div style={{ alignItems: "flex-end", display: "flex", flex: "0 0 auto", flexDirection: "column", marginLeft: 20 }}>
              <div style={{ color: "#8f9a8f", display: "flex", fontSize: 16, fontWeight: 750, letterSpacing: 1.4, lineHeight: 1 }}>
                PUMBILITY
              </div>
              <div style={{ alignItems: "center", display: "flex", height: 88, marginTop: 2 }}>
                {rankEmblemDataUrl ? (
                  <img
                    alt=""
                    height="88"
                    src={rankEmblemDataUrl}
                    style={{ display: "flex", height: 88, marginRight: 12, objectFit: "contain", width: 88 }}
                    width="88"
                  />
                ) : null}
                <div style={{ alignItems: "baseline", display: "flex", height: 60 }}>
                  <div style={{ color: "#f4f7ee", display: "flex", fontSize: 60, fontWeight: 900, letterSpacing: -2, lineHeight: 1 }}>
                    {total.integer}
                  </div>
                  <div style={{ color: "#c8ff2e", display: "flex", fontSize: 34, fontWeight: 850, lineHeight: 1 }}>
                    {total.fraction}
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div
            style={{
              display: "flex",
              flexDirection: "column",
              height: 1750,
              justifyContent: "space-between",
              marginTop: 20,
              width: "100%",
            }}
          >
            {rows.map((row, rowIndex) => (
              <div
                key={`row-${rowIndex}`}
                style={{ display: "flex", gap: 10, height: 166, width: "100%" }}
              >
                {row.map((score, columnIndex) => cloneElement(
                  PosterCard({
                    rank: (rowIndex * 5) + columnIndex + 1,
                    score,
                  }),
                  { key: score?.chartId || `empty-${rowIndex}-${columnIndex}` },
                ))}
              </div>
            ))}
          </div>

          <div
            style={{
              alignItems: "flex-end",
              color: "#7f8a80",
              display: "flex",
              flex: "1 1 auto",
              fontSize: 17,
              justifyContent: "space-between",
              letterSpacing: 0.2,
              width: "100%",
            }}
          >
            <div style={{ display: "flex" }}>Generated by {TOP50_EXPORT_SITE} · Phoenix 2</div>
            <div style={{ color: "#c8ff2e", display: "flex", fontWeight: 800 }}>PUMBILITY FARMER</div>
          </div>
        </div>,
      [
          {
            name: "Noto Sans",
            data: notoSansRegular,
            style: "normal",
            weight: 400,
          },
          {
            name: "Noto Sans",
            data: notoSansBold,
            style: "normal",
            weight: 700,
          },
          {
            name: "Noto Sans Symbols 2",
            data: notoSansSymbols,
            style: "normal",
            weight: 400,
          },
        ],
        posterJackets,
      );
      return {
        ...rendered,
        jacketFetchNormalizeMs: jacketsCompletedAt - jacketsStartedAt,
      };
    });
    const posterCacheLookupMs = performance.now() - posterCacheLookupStartedAt;
    const posterWaitStartedAt = performance.now();
    const { jacketFetchNormalizeMs, png: poster, timings } = await cachedPoster.promise;
    const posterWaitMs = performance.now() - posterWaitStartedAt;
    const apiMs = apiCompletedAt - requestStartedAt;
    const totalMs = performance.now() - requestStartedAt;
    const serverTimingParts = [
      `recommendations;dur=${formatDuration(apiMs)}`,
      `poster-cache;desc="${cachedPoster.cacheStatus}";dur=${formatDuration(posterCacheLookupMs)}`,
    ];
    if (cachedPoster.cacheStatus === "miss") {
      serverTimingParts.push(
        `jacket-fetch-normalize;dur=${formatDuration(jacketFetchNormalizeMs)}`,
        `jacket-resize;dur=${formatDuration(timings.jacketResizeMs)}`,
        `satori;dur=${formatDuration(timings.layoutMs)}`,
        `resvg;dur=${formatDuration(timings.resvgRasterMs)}`,
        `composite;dur=${formatDuration(timings.compositeMs)}`,
        `png-encode;dur=${formatDuration(timings.pngEncodeMs)}`,
      );
    } else if (cachedPoster.cacheStatus === "coalesced") {
      serverTimingParts.push(`poster-wait;dur=${formatDuration(posterWaitMs)}`);
    }
    serverTimingParts.push(`total;dur=${formatDuration(totalMs)}`);
    console.info("Top 50 export generated.", {
      cacheStatus: cachedPoster.cacheStatus,
      compositeMs: cachedPoster.cacheStatus === "miss" ? timings.compositeMs : undefined,
      jacketFetchNormalizeMs: cachedPoster.cacheStatus === "miss"
        ? Math.round(jacketFetchNormalizeMs)
        : undefined,
      jacketResizeMs: cachedPoster.cacheStatus === "miss" ? timings.jacketResizeMs : undefined,
      mode: modeValue,
      pngEncodeMs: cachedPoster.cacheStatus === "miss" ? timings.pngEncodeMs : undefined,
      posterCacheBytes: completedPosterCacheBytes,
      posterCacheEntries: completedPosterCache.size,
      posterCacheLookupMs: Math.round(posterCacheLookupMs),
      posterWaitMs: Math.round(posterWaitMs),
      recommendationsMs: Math.round(apiMs),
      resvgRasterMs: cachedPoster.cacheStatus === "miss" ? timings.resvgRasterMs : undefined,
      satoriMs: cachedPoster.cacheStatus === "miss" ? timings.layoutMs : undefined,
      scoreCount: scores.length,
      totalMs: Math.round(totalMs),
    });
    return new Response(new Uint8Array(poster), {
      headers: {
        "Cache-Control": "private, no-store, max-age=0",
        "Content-Disposition": `attachment; filename="${top50ExportFilename(modeValue)}"`,
        "Content-Type": "image/png",
        "Server-Timing": serverTimingParts.join(", "),
        "X-Top50-Poster-Cache": cachedPoster.cacheStatus,
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return jsonError("The recommendation service took too long to respond.", 504);
    }
    return jsonError(
      error instanceof Error ? error.message : "The Top 50 image could not be created.",
      500,
    );
  }
}
