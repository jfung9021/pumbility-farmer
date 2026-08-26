import { performance } from "node:perf_hooks";
import { renderAsync as renderSvgAsync } from "@resvg/resvg-js";
import type { ReactNode } from "react";
import satori, { type Font as SatoriFont } from "satori";
import sharp from "sharp";

import {
  TOP50_EXPORT_HEIGHT,
  TOP50_EXPORT_SCALE,
  TOP50_EXPORT_WIDTH,
  TOP50_POSTER_HEIGHT,
  TOP50_POSTER_WIDTH,
} from "./top50-export";

const PNG_COMPRESSION_LEVEL = 1;

export type Top50PosterFont = SatoriFont;

export interface Top50PosterRenderTimings {
  compositeMs: number;
  jacketPrepareMs: number;
  jacketResizeMs: number;
  layoutMs: number;
  pngEncodeMs: number;
  resvgRasterMs: number;
  rasterizeAndEncodeMs: number;
  totalMs: number;
}

export interface Top50PosterJacket {
  data: Buffer;
  height: number;
  left: number;
  top: number;
  width: number;
}

export interface Top50PosterRenderResult {
  png: Buffer;
  timings: Top50PosterRenderTimings;
}

function elapsedMilliseconds(startedAt: number, completedAt: number): number {
  return Math.round((completedAt - startedAt) * 10) / 10;
}

/**
 * Lays out the transparent poster UI at stable CSS dimensions, composites
 * full-resolution jacket crops underneath it, and encodes the 2x PNG once.
 */
export async function renderTop50Poster(
  element: ReactNode,
  fonts: Top50PosterFont[],
  jackets: Top50PosterJacket[],
): Promise<Top50PosterRenderResult> {
  const startedAt = performance.now();
  const svg = await satori(element, {
    embedFont: true,
    fonts,
    height: TOP50_POSTER_HEIGHT,
    width: TOP50_POSTER_WIDTH,
  });
  const layoutCompletedAt = performance.now();
  console.info("Top 50 SVG layout completed.", {
    layoutMs: elapsedMilliseconds(startedAt, layoutCompletedAt),
  });

  const resizedJackets = new Map<Buffer, Map<string, Promise<{
    input: Buffer;
    raw: {
      channels: 1 | 2 | 3 | 4;
      height: number;
      width: number;
    };
  }>>>();
  const jacketLayers = await Promise.all(jackets.map(async (jacket) => {
    const width = Math.round(jacket.width * TOP50_EXPORT_SCALE);
    const height = Math.round(jacket.height * TOP50_EXPORT_SCALE);
    const sizeKey = `${width}x${height}`;
    let resizedBySize = resizedJackets.get(jacket.data);
    if (!resizedBySize) {
      resizedBySize = new Map();
      resizedJackets.set(jacket.data, resizedBySize);
    }
    let resizePromise = resizedBySize.get(sizeKey);
    if (!resizePromise) {
      resizePromise = sharp(jacket.data)
        .resize(width, height, {
          fit: "cover",
          kernel: sharp.kernel.lanczos3,
        })
        .ensureAlpha()
        .raw()
        .toBuffer({ resolveWithObject: true })
        .then(({ data, info }) => ({
          input: data,
          raw: {
            channels: info.channels,
            height: info.height,
            width: info.width,
          },
        }));
      resizedBySize.set(sizeKey, resizePromise);
    }
    const resized = await resizePromise;
    return {
      ...resized,
      left: Math.round(jacket.left * TOP50_EXPORT_SCALE),
      top: Math.round(jacket.top * TOP50_EXPORT_SCALE),
    };
  }));
  const jacketsCompletedAt = performance.now();

  // Satori embeds the supplied fonts as SVG paths by default, so system-font
  // discovery is unnecessary. Resvg handles this path-heavy UI much faster
  // than Sharp/librsvg and exposes RGBA pixels without an intermediate PNG.
  const renderedUi = await renderSvgAsync(svg, {
    fitTo: {
      mode: "zoom",
      value: TOP50_EXPORT_SCALE,
    },
    font: {
      loadSystemFonts: false,
    },
    logLevel: "off",
  });
  if (
    renderedUi.width !== TOP50_EXPORT_WIDTH
    || renderedUi.height !== TOP50_EXPORT_HEIGHT
  ) {
    throw new Error(
      `Top 50 UI rendered at ${renderedUi.width}x${renderedUi.height}; expected ${TOP50_EXPORT_WIDTH}x${TOP50_EXPORT_HEIGHT}.`,
    );
  }
  const uiPixels = renderedUi.pixels;
  const rasterCompletedAt = performance.now();

  const backgroundSvg = Buffer.from(`
    <svg xmlns="http://www.w3.org/2000/svg" width="${TOP50_EXPORT_WIDTH}" height="${TOP50_EXPORT_HEIGHT}">
      <defs>
        <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#0b100c" />
          <stop offset="52%" stop-color="#111a12" />
          <stop offset="100%" stop-color="#07100a" />
        </linearGradient>
      </defs>
      <rect width="100%" height="100%" fill="url(#background)" />
    </svg>
  `);
  const { data: composedPixels, info: composedInfo } = await sharp(backgroundSvg)
    .composite([
      ...jacketLayers,
      {
        input: uiPixels,
        left: 0,
        raw: {
          channels: 4,
          height: renderedUi.height,
          width: renderedUi.width,
        },
        top: 0,
      },
    ])
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  const compositeCompletedAt = performance.now();

  const png = await sharp(composedPixels, {
    raw: {
      channels: composedInfo.channels,
      height: composedInfo.height,
      width: composedInfo.width,
    },
  })
    .png({ compressionLevel: PNG_COMPRESSION_LEVEL })
    .toBuffer();
  const completedAt = performance.now();
  console.info("Top 50 poster raster completed.", {
    compositeMs: elapsedMilliseconds(rasterCompletedAt, compositeCompletedAt),
    jacketPrepareMs: elapsedMilliseconds(layoutCompletedAt, jacketsCompletedAt),
    jacketResizeMs: elapsedMilliseconds(layoutCompletedAt, jacketsCompletedAt),
    pngEncodeMs: elapsedMilliseconds(compositeCompletedAt, completedAt),
    resvgRasterMs: elapsedMilliseconds(jacketsCompletedAt, rasterCompletedAt),
  });

  return {
    png,
    timings: {
      compositeMs: elapsedMilliseconds(rasterCompletedAt, compositeCompletedAt),
      jacketPrepareMs: elapsedMilliseconds(layoutCompletedAt, jacketsCompletedAt),
      jacketResizeMs: elapsedMilliseconds(layoutCompletedAt, jacketsCompletedAt),
      layoutMs: elapsedMilliseconds(startedAt, layoutCompletedAt),
      pngEncodeMs: elapsedMilliseconds(compositeCompletedAt, completedAt),
      resvgRasterMs: elapsedMilliseconds(jacketsCompletedAt, rasterCompletedAt),
      rasterizeAndEncodeMs: elapsedMilliseconds(jacketsCompletedAt, completedAt),
      totalMs: elapsedMilliseconds(startedAt, completedAt),
    },
  };
}
