import { writeFile, readFile } from "node:fs/promises";
import { registerHooks } from "node:module";
import { performance } from "node:perf_hooks";
import path from "node:path";
import { createElement, type CSSProperties, type ReactNode } from "react";
import sharp from "sharp";

import {
  TOP50_EXPORT_HEIGHT,
  TOP50_EXPORT_WIDTH,
  TOP50_POSTER_HEIGHT,
  TOP50_POSTER_WIDTH,
  top50ExportFilename,
} from "../lib/top50-export.ts";
import { pumbilityProgress } from "../lib/pumbility-progress.ts";
import type {
  Top50PosterFont,
  Top50PosterJacket,
} from "../lib/top50-poster-render.ts";


const FIXTURE_DATE = new Date("2026-08-26T00:00:00.000Z");
const FIXTURE_PUMBILITY = 16_800.65;
const SCORE_COUNT = 50;
const CARD_WIDTH = 212;
const CARD_HEIGHT = 166;
const ART_HEIGHT = 108;
const COLUMN_STEP = 222;
const ROW_STEP = 176;
const GRID_LEFT = 28;
const GRID_TOP = 193;
const POSTER_FALLBACK_FONT_SIZE = 20;
const POSTER_CARD_BADGE_FONT_SIZE = 17;
const POSTER_TITLE_FONT_SIZE = 14;
const POSTER_RESULT_FONT_SIZE = 18;
const POSTER_RESULT_GAP = 5;
const POSTER_PLATE_FONT_SIZE = 12;
const POSTER_PUMBILITY_FONT_SIZE = 16;
const POSTER_PUMBILITY_GAP = 4;
const POSTER_PF_FONT_SIZE = 26;
const POSTER_HEADER_LABEL_FONT_SIZE = 18;
const POSTER_PLAYER_FONT_SIZE = 41;
const POSTER_HEADER_META_FONT_SIZE = 17;
const POSTER_TOTAL_LABEL_FONT_SIZE = 16;
const POSTER_TOTAL_INTEGER_FONT_SIZE = 60;
const POSTER_TOTAL_FRACTION_FONT_SIZE = 34;
const POSTER_FOOTER_FONT_SIZE = 17;

registerHooks({
  resolve(specifier, context, nextResolve) {
    try {
      return nextResolve(specifier, context);
    } catch (error) {
      if (specifier.startsWith(".") && path.extname(specifier) === "") {
        return nextResolve(`${specifier}.ts`, context);
      }
      throw error;
    }
  },
});

function element(
  type: string,
  style: CSSProperties,
  children: ReactNode,
  key?: string | number,
): ReactNode {
  return createElement(type, { key, style }, children);
}

function benchmarkCard(index: number): ReactNode {
  const rank = index + 1;
  const score = rank === 1 ? "1,000,000" : (1_000_000 - index).toLocaleString("en-US");
  const grade = rank === 1 ? "SSS+" : "SSS";
  const plate = rank === 1 ? "PG" : "TG";
  return element("div", {
    background: "rgba(13, 18, 14, 0.96)",
    border: "1px solid rgba(255,255,255,0.11)",
    color: "#f4f7ee",
    display: "flex",
    flex: "1 1 0",
    flexDirection: "column",
    height: CARD_HEIGHT,
    minWidth: 0,
    overflow: "hidden",
  }, [
    element("div", {
      alignItems: "center",
      display: "flex",
      height: ART_HEIGHT,
      justifyContent: "center",
      overflow: "hidden",
      position: "relative",
      width: "100%",
    }, [
      rank === SCORE_COUNT ? element("div", {
        color: "#c8ff2e",
        display: "flex",
        fontSize: POSTER_FALLBACK_FONT_SIZE,
        fontWeight: 800,
        lineHeight: 1,
      }, "16.0", "fallback") : null,
      element("div", {
        background: "rgba(3, 6, 4, 0.82)",
        color: "#fff",
        display: "flex",
        fontSize: POSTER_CARD_BADGE_FONT_SIZE,
        fontWeight: 900,
        lineHeight: 1,
        left: 0,
        padding: "5px 7px",
        position: "absolute",
        top: 0,
      }, `#${rank}`, "rank"),
      element("div", {
        alignItems: "center",
        background: rank % 2 === 0 ? "#176b3a" : "#b62e35",
        border: "1px solid rgba(255,255,255,0.52)",
        borderRadius: 999,
        bottom: 6,
        display: "flex",
        fontSize: POSTER_CARD_BADGE_FONT_SIZE,
        fontWeight: 800,
        height: 28,
        justifyContent: "center",
        lineHeight: 1,
        position: "absolute",
        right: 6,
        width: 28,
      }, String(15 + (index % 10)), "difficulty"),
    ], "art"),
    element("div", {
      display: "flex",
      height: 23,
      justifyContent: "flex-start",
      overflow: "hidden",
      alignItems: "center",
      padding: "0 4px",
      textAlign: "left",
      width: "100%",
    }, element("div", {
      display: "flex",
      flex: "1 1 auto",
      fontSize: POSTER_TITLE_FONT_SIZE,
      fontWeight: 700,
      lineHeight: 1.2,
      minWidth: 0,
      overflow: "hidden",
      textAlign: "left",
      whiteSpace: "nowrap",
    }, `Jonathan Overall fixture chart with a deliberately long title ${rank}`, "title-text"), "title"),
    element("div", {
      alignItems: "baseline",
      display: "flex",
      gap: POSTER_PUMBILITY_GAP,
      height: 35,
      justifyContent: "space-between",
      padding: "5px 4px 8px",
      width: "100%",
    }, [
      element("div", {
        alignItems: "baseline",
        display: "flex",
        flex: "0 1 auto",
        fontWeight: 700,
        gap: POSTER_RESULT_GAP,
        minWidth: 0,
        whiteSpace: "nowrap",
      }, [
        element("div", {
          display: "flex",
          flex: "0 0 auto",
          fontSize: POSTER_RESULT_FONT_SIZE,
          lineHeight: 1,
        }, score, "score"),
        element("div", {
          display: "flex",
          flex: "0 0 auto",
          fontSize: POSTER_RESULT_FONT_SIZE,
          lineHeight: 1,
        }, grade, "grade"),
        element("div", {
          color: "#7e887f",
          display: "flex",
          flex: "0 0 auto",
          fontSize: POSTER_PLATE_FONT_SIZE,
          lineHeight: 1,
        }, plate, "plate"),
      ], "metadata"),
      element("div", {
        color: "#c8ff2e",
        display: "flex",
        flex: "0 0 auto",
        fontSize: POSTER_PUMBILITY_FONT_SIZE,
        fontWeight: 700,
        whiteSpace: "nowrap",
      }, (356.16 - index).toFixed(2), "pumbility"),
    ], "stats"),
  ], rank);
}

function benchmarkPoster(rankEmblemDataUrl: string): ReactNode {
  const rows = Array.from({ length: 10 }, (_, rowIndex) => element("div", {
    display: "flex",
    gap: 10,
    height: CARD_HEIGHT,
    width: "100%",
  }, Array.from({ length: 5 }, (_, columnIndex) => (
    benchmarkCard((rowIndex * 5) + columnIndex)
  )), rowIndex));

  return element("div", {
    background: "transparent",
    color: "#f4f7ee",
    display: "flex",
    flexDirection: "column",
    fontFamily: '"Noto Sans", "Noto Sans Symbols 2", sans-serif',
    height: TOP50_POSTER_HEIGHT,
    padding: 28,
    width: TOP50_POSTER_WIDTH,
  }, [
    element("div", {
      alignItems: "center",
      borderBottom: "1px solid rgba(255,255,255,0.09)",
      display: "flex",
      height: 145,
      justifyContent: "space-between",
      paddingBottom: 20,
      width: "100%",
    }, [
      element("div", {
        alignItems: "center",
        display: "flex",
        flex: "1 1 auto",
        minWidth: 0,
        overflow: "hidden",
      }, [
        element("div", {
          alignItems: "center",
          background: "#c8ff2e",
          color: "#10140c",
          display: "flex",
          fontSize: POSTER_PF_FONT_SIZE,
          fontWeight: 900,
          height: 54,
          justifyContent: "center",
          marginRight: 16,
          width: 54,
        }, "PF", "pf"),
        element("div", {
          display: "flex",
          flex: "1 1 auto",
          flexDirection: "column",
          minWidth: 0,
          overflow: "hidden",
        }, [
          element("div", {
            color: "#c8ff2e",
            display: "flex",
            fontSize: POSTER_HEADER_LABEL_FONT_SIZE,
            fontWeight: 850,
            letterSpacing: 2.2,
            lineHeight: 1.1,
          }, "TOP 50 PUMBILITY", "label"),
          element("div", {
            display: "flex",
            fontSize: POSTER_PLAYER_FONT_SIZE,
            fontWeight: 850,
            lineHeight: 1.05,
            marginTop: 5,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            width: "100%",
          }, "jonathan", "player"),
          element("div", {
            color: "#8f9a8f",
            display: "flex",
            fontSize: POSTER_HEADER_META_FONT_SIZE,
            lineHeight: 1.15,
            marginTop: 3,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            width: "100%",
          }, "Overall · Phoenix 2 · Scores synced 2026-08-26", "mode"),
        ], "identity"),
      ], "header-left"),
      element("div", {
        alignItems: "flex-end",
        display: "flex",
        flex: "0 0 auto",
        flexDirection: "column",
        marginLeft: 20,
      }, [
        element("div", {
          color: "#8f9a8f",
          display: "flex",
          fontSize: POSTER_TOTAL_LABEL_FONT_SIZE,
          fontWeight: 750,
          letterSpacing: 1.4,
          lineHeight: 1,
        }, "PUMBILITY", "total-label"),
        element("div", { alignItems: "center", display: "flex", height: 88, marginTop: 2 }, [
          createElement("img", {
            alt: "",
            height: "88",
            key: "rank-emblem",
            src: rankEmblemDataUrl,
            style: {
              display: "flex",
              height: 88,
              marginRight: 12,
              objectFit: "contain",
              width: 88,
            },
            width: "88",
          }),
          element("div", { alignItems: "baseline", display: "flex", height: 60 }, [
            element("div", {
              display: "flex",
              fontSize: POSTER_TOTAL_INTEGER_FONT_SIZE,
              fontWeight: 900,
              letterSpacing: -2,
              lineHeight: 1,
            }, "16,800", "integer"),
            element("div", {
              color: "#c8ff2e",
              display: "flex",
              fontSize: POSTER_TOTAL_FRACTION_FONT_SIZE,
              fontWeight: 850,
              lineHeight: 1,
            }, ".65", "fraction"),
          ], "total"),
        ], "rank-and-total"),
      ], "header-right"),
    ], "header"),
    element("div", {
      display: "flex",
      flexDirection: "column",
      height: 1750,
      justifyContent: "space-between",
      marginTop: 20,
      width: "100%",
    }, rows, "grid"),
    element("div", {
      alignItems: "flex-end",
      color: "#7f8a80",
      display: "flex",
      flex: "1 1 auto",
      fontSize: POSTER_FOOTER_FONT_SIZE,
      justifyContent: "space-between",
      letterSpacing: 0.2,
      width: "100%",
    }, [
      element("div", { display: "flex" }, "Generated by pumbility-farmer.vercel.app · Phoenix 2", "credit"),
      element("div", { color: "#c8ff2e", display: "flex", fontWeight: 800 }, "PUMBILITY FARMER", "brand"),
    ], "footer"),
  ], "poster");
}

async function benchmarkJackets(): Promise<Top50PosterJacket[]> {
  const fixtureJacket = await sharp({
    create: {
      background: { b: 62, g: 120, r: 42 },
      channels: 3,
      height: 393,
      width: 700,
    },
  }).png().toBuffer();
  return Array.from({ length: SCORE_COUNT - 1 }, (_, index) => ({
    data: fixtureJacket,
    height: ART_HEIGHT,
    left: GRID_LEFT + ((index % 5) * COLUMN_STEP),
    top: GRID_TOP + (Math.floor(index / 5) * ROW_STEP),
    width: CARD_WIDTH,
  }));
}

async function main(): Promise<void> {
  const { renderTop50Poster } = await import("../lib/top50-poster-render.ts");
  const fonts = await Promise.all([
    readFile(path.join(process.cwd(), "assets", "fonts", "NotoSans-Regular.ttf")),
    readFile(path.join(process.cwd(), "assets", "fonts", "NotoSans-Bold.ttf")),
    readFile(path.join(process.cwd(), "assets", "fonts", "NotoSansSymbols2-Regular.ttf")),
  ]);
  const posterFonts: Top50PosterFont[] = [
    { name: "Noto Sans", data: fonts[0], style: "normal", weight: 400 },
    { name: "Noto Sans", data: fonts[1], style: "normal", weight: 700 },
    { name: "Noto Sans Symbols 2", data: fonts[2], style: "normal", weight: 400 },
  ];
  const rankProgress = pumbilityProgress("overall", FIXTURE_PUMBILITY);
  const rankEmblemFilename = `pumbility_${String(rankProgress.rungIndex).padStart(2, "0")}.webp`;
  const rankEmblemSource = await readFile(path.join(
    process.cwd(),
    "public",
    "images",
    "phoenix2-ranks",
    rankEmblemFilename,
  ));
  const rankEmblemPng = await sharp(rankEmblemSource).png().toBuffer();
  const rankEmblemDataUrl = `data:image/png;base64,${rankEmblemPng.toString("base64")}`;
  const startedAt = performance.now();
  const result = await renderTop50Poster(
    benchmarkPoster(rankEmblemDataUrl),
    posterFonts,
    await benchmarkJackets(),
  );
  const metadata = await sharp(result.png).metadata();
  if (metadata.width !== TOP50_EXPORT_WIDTH || metadata.height !== TOP50_EXPORT_HEIGHT) {
    throw new Error(`Unexpected output dimensions: ${metadata.width}x${metadata.height}`);
  }
  const outputIndex = process.argv.indexOf("--output");
  const outputPath = outputIndex >= 0 && process.argv[outputIndex + 1]
    ? path.resolve(process.argv[outputIndex + 1])
    : null;
  if (outputPath) await writeFile(outputPath, result.png);
  console.log(JSON.stringify({
    bytes: result.png.byteLength,
    cardGeometry: `${CARD_WIDTH}x${CARD_HEIGHT}`,
    filename: top50ExportFilename("overall", FIXTURE_DATE),
    fixture: "jonathan-overall-local",
    harnessTotalMs: Math.round((performance.now() - startedAt) * 10) / 10,
    height: metadata.height,
    outputPath,
    rankEmblem: rankEmblemFilename,
    scoreCount: SCORE_COUNT,
    timings: result.timings,
    width: metadata.width,
    worstCaseMetadata: "1,000,000 SSS+ PG",
  }, null, 2));
}

await main();
