import { ImageResponse } from "next/og";
import type { NextRequest } from "next/server";

import {
  hasExactTop50Scores,
  isSafeTop50JacketUrl,
  isTop50ExportMode,
  splitPumbility,
  TOP50_EXPORT_HEIGHT,
  TOP50_EXPORT_SITE,
  TOP50_EXPORT_WIDTH,
  top50ExportApiOrigin,
  top50ExportFilename,
  top50ExportRows,
  type Top50ExportMode,
} from "../../../lib/top50-export";
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
const JACKET_MAX_BYTES = 800_000;
const JACKET_CONCURRENCY = 8;
const PLAYER_KEY_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;

type PosterScore = RecommendationTopScore & { jacketDataUrl: string | null };

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

async function loadJacketDataUrl(value: string): Promise<string | null> {
  if (!isSafeTop50JacketUrl(value)) return null;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), JACKET_TIMEOUT_MS);
  try {
    const response = await fetch(value, {
      cache: "force-cache",
      redirect: "error",
      signal: controller.signal,
    });
    if (!response.ok) return null;
    const image = await readBoundedImage(response);
    const contentType = detectedImageContentType(image);
    if (!contentType) return null;
    return `data:${contentType};base64,${Buffer.from(image).toString("base64")}`;
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
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
  const loaded = await mapWithConcurrency(urls, JACKET_CONCURRENCY, loadJacketDataUrl);
  const jacketByUrl = new Map(urls.map((url, index) => [url, loaded[index]]));
  return scores.map((score) => ({
    ...score,
    jacketDataUrl: score.imageUrl ? jacketByUrl.get(score.imageUrl) ?? null : null,
  }));
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
        background: "rgba(13, 18, 14, 0.96)",
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
          background: "linear-gradient(135deg, #293723, #111612)",
          display: "flex",
          height: 108,
          justifyContent: "center",
          overflow: "hidden",
          position: "relative",
          width: "100%",
        }}
      >
        {score.jacketDataUrl ? (
          <img
            alt=""
            height="108"
            src={score.jacketDataUrl}
            style={{ height: "100%", objectFit: "cover", width: "100%" }}
            width="173"
          />
        ) : (
          <div style={{ color: "#c8ff2e", display: "flex", fontSize: 15, fontWeight: 800 }}>
            {score.difficulty}
          </div>
        )}
        <div
          style={{
            background: "rgba(3, 6, 4, 0.82)",
            borderRadius: "0 0 6px 0",
            color: "white",
            display: "flex",
            fontSize: 12,
            fontWeight: 900,
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
            fontSize: 12,
            fontWeight: 800,
            height: 28,
            justifyContent: "center",
            position: "absolute",
            right: 6,
            width: 28,
          }}
        >{score.level}</div>
      </div>
      <div
        style={{
          borderTop: "1px solid rgba(255,255,255,0.055)",
          display: "flex",
          height: 23,
          justifyContent: "flex-start",
          overflow: "hidden",
          padding: "8px 9px 2px",
          textAlign: "left",
          width: "100%",
        }}
      >
        <div
          style={{
            color: "#d3dad4",
            display: "flex",
            flex: "1 1 auto",
            fontSize: 10,
            fontWeight: 700,
            lineHeight: 1.3,
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
          display: "flex",
          flex: "0 0 auto",
          gap: 7,
          height: 35,
          justifyContent: "space-between",
          minWidth: 0,
          padding: "5px 9px 8px",
          width: "100%",
        }}
      >
        <div style={{ alignItems: "baseline", display: "flex", flex: "0 0 auto", gap: 4, minWidth: 0 }}>
          <div style={{ color: "#d3dad4", display: "flex", flex: "0 0 auto", fontSize: 13, fontVariantNumeric: "tabular-nums", fontWeight: 700, lineHeight: 1, whiteSpace: "nowrap" }}>
            {scoreValue}
          </div>
          <div style={{ alignItems: "baseline", display: "flex", flex: "0 0 auto", gap: 4, minWidth: 0 }}>
            <div style={{ color: "#d3dad4", display: "flex", fontSize: 13, fontWeight: 700, lineHeight: 1, whiteSpace: "nowrap" }}>
              {score.grade || "—"}
            </div>
            <div style={{ color: "#7e887f", display: "flex", fontSize: 10, lineHeight: 1, whiteSpace: "nowrap" }}>
              {score.plateCode || "—"}
            </div>
          </div>
        </div>
        <div style={{ color: "#c8ff2e", display: "flex", flex: "0 0 auto", fontSize: 13, fontVariantNumeric: "tabular-nums", fontWeight: 700, letterSpacing: "-0.03em", lineHeight: 1, whiteSpace: "nowrap" }}>
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

    const posterScores = await attachJackets(scores);
    const rows = top50ExportRows(posterScores);
    const total = splitPumbility(posterPumbility(modeResult, scores));
    const playerName = value.player.displayName?.trim()
      || value.player.username?.trim()
      || "Player";
    const generatedAt = value.playerSyncedAtUtc
      || value.recommendationsGeneratedAtUtc
      || value.generatedAtUtc;

    return new ImageResponse(
      (
        <div
          style={{
            background: "linear-gradient(145deg, #0b100c 0%, #111a12 52%, #07100a 100%)",
            color: "#f4f7ee",
            display: "flex",
            flexDirection: "column",
            fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
            height: "100%",
            padding: 28,
            width: "100%",
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
            <div style={{ alignItems: "center", display: "flex", minWidth: 0 }}>
              <div
                style={{
                  alignItems: "center",
                  background: "#c8ff2e",
                  color: "#10140c",
                  display: "flex",
                  fontSize: 19,
                  fontWeight: 900,
                  height: 54,
                  justifyContent: "center",
                  marginRight: 16,
                  width: 54,
                }}
              >PF</div>
              <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
                <div style={{ color: "#c8ff2e", display: "flex", fontSize: 13, fontWeight: 850, letterSpacing: 2.2 }}>
                  TOP 50 PUMBILITY
                </div>
                <div style={{ display: "flex", fontSize: 30, fontWeight: 850, marginTop: 7, whiteSpace: "nowrap" }}>
                  {playerName}
                </div>
                <div style={{ color: "#8f9a8f", display: "flex", fontSize: 12, marginTop: 5 }}>
                  {modeLabel(modeValue)} · Phoenix 2{generatedAt ? ` · Scores synced ${generatedAt.slice(0, 10)}` : ""}
                </div>
              </div>
            </div>
            <div style={{ alignItems: "flex-end", display: "flex", flexDirection: "column", marginLeft: 20 }}>
              <div style={{ color: "#8f9a8f", display: "flex", fontSize: 11, fontWeight: 750, letterSpacing: 1.4 }}>
                PUMBILITY
              </div>
              <div style={{ alignItems: "flex-end", display: "flex", height: 45, marginTop: 4 }}>
                <div style={{ color: "#f4f7ee", display: "flex", fontSize: 45, fontWeight: 900, letterSpacing: -2, lineHeight: 1 }}>
                  {total.integer}
                </div>
                <div style={{ color: "#c8ff2e", display: "flex", fontSize: 24, fontWeight: 850, lineHeight: 1 }}>
                  {total.fraction}
                </div>
              </div>
              <div style={{ color: "#6e786f", display: "flex", fontSize: 10, marginTop: 2 }}>
                {scores.length} scored chart{scores.length === 1 ? "" : "s"}
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
                {row.map((score, columnIndex) => (
                  <PosterCard
                    key={score?.chartId || `empty-${rowIndex}-${columnIndex}`}
                    rank={(rowIndex * 5) + columnIndex + 1}
                    score={score}
                  />
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
              fontSize: 12,
              justifyContent: "space-between",
              letterSpacing: 0.2,
              width: "100%",
            }}
          >
            <div style={{ display: "flex" }}>Generated by {TOP50_EXPORT_SITE} · Phoenix 2</div>
            <div style={{ color: "#c8ff2e", display: "flex", fontWeight: 800 }}>PUMBILITY FARMER</div>
          </div>
        </div>
      ),
      {
        height: TOP50_EXPORT_HEIGHT,
        width: TOP50_EXPORT_WIDTH,
        headers: {
          "Cache-Control": "private, no-store, max-age=0",
          "Content-Disposition": `attachment; filename="${top50ExportFilename(modeValue)}"`,
          "X-Content-Type-Options": "nosniff",
        },
      },
    );
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
