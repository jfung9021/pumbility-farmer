import type { RecommendationModeKey, RecommendationTopScore } from "./types";


export type Top50ExportMode = Exclude<RecommendationModeKey, "coop">;

export const TOP50_EXPORT_WIDTH = 1156;
export const TOP50_EXPORT_HEIGHT = 2048;
export const TOP50_EXPORT_SITE = "pumbility-farmer.vercel.app";
export const TOP50_EXPORT_PRODUCTION_ORIGIN = `https://${TOP50_EXPORT_SITE}`;
export const TOP50_JACKET_HOST = "piuimages.arroweclip.se";

const TOP50_EXPORT_MODES = new Set<Top50ExportMode>([
  "overall",
  "singles",
  "doubles",
]);
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

export function isTop50ExportMode(value: string): value is Top50ExportMode {
  return TOP50_EXPORT_MODES.has(value as Top50ExportMode);
}

export function top50ExportFilename(
  mode: Top50ExportMode,
  generatedAt: Date = new Date(),
): string {
  return `pumbility-top50-${mode}-${generatedAt.toISOString().slice(0, 10)}.png`;
}

export function top50ExportDownloadFilename(
  contentDisposition: string | null,
  mode: Top50ExportMode,
  fallbackDate: Date = new Date(),
): string {
  const fallback = top50ExportFilename(mode, fallbackDate);
  const candidate = contentDisposition
    ?.match(/filename="?([^";]+)"?/i)?.[1]
    ?.trim();
  return candidate && new RegExp(
    `^pumbility-top50-${mode}-\\d{4}-\\d{2}-\\d{2}\\.png$`,
  ).test(candidate)
    ? candidate
    : fallback;
}

export function hasExactTop50Scores(scores: RecommendationTopScore[]): boolean {
  return scores.length > 0 && scores.every(
    (score) => Number.isInteger(score.score)
      && (score.score as number) >= 0
      && (score.score as number) <= 1_000_000,
  );
}

export function splitPumbility(value: number): { integer: string; fraction: string } {
  const normalized = Number.isFinite(value) ? Math.max(0, value) : 0;
  const [integer, fraction] = normalized.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).split(".");
  return { integer, fraction: `.${fraction}` };
}

export function isSafeTop50JacketUrl(value: string | null): value is string {
  if (!value) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:"
      && url.hostname === TOP50_JACKET_HOST
      && url.port === ""
      && url.username === ""
      && url.password === ""
      && url.pathname.startsWith("/songs/")
      && url.search === ""
      && url.hash === "";
  } catch {
    return false;
  }
}

function normalizedOrigin(value: string): URL | null {
  try {
    const url = new URL(value);
    const loopback = LOOPBACK_HOSTS.has(url.hostname);
    if (url.username || url.password || url.pathname !== "/" || url.search || url.hash) return null;
    if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) return null;
    return url;
  } catch {
    return null;
  }
}

export function top50ExportApiOrigin(
  requestOrigin: string,
  environment: Readonly<Record<string, string | undefined>> = process.env,
): URL {
  const configured = environment.PUMBILITY_EXPORT_API_ORIGIN?.trim();
  if (configured) {
    const origin = normalizedOrigin(configured);
    if (!origin) throw new Error("PUMBILITY_EXPORT_API_ORIGIN must be an HTTPS origin or a loopback HTTP origin.");
    return origin;
  }
  if (environment.PIU_LOCAL_ANALYSIS === "1") {
    const localOrigin = normalizedOrigin(requestOrigin);
    if (!localOrigin || !LOOPBACK_HOSTS.has(localOrigin.hostname)) {
      throw new Error("Local Top 50 export requires a loopback request origin.");
    }
    return localOrigin;
  }
  return new URL(TOP50_EXPORT_PRODUCTION_ORIGIN);
}

export function top50ExportRows<T>(items: T[], columns = 5, rows = 10): Array<Array<T | null>> {
  const cells = items.slice(0, columns * rows);
  return Array.from({ length: rows }, (_, rowIndex) => (
    Array.from({ length: columns }, (_, columnIndex) => (
      cells[(rowIndex * columns) + columnIndex] ?? null
    ))
  ));
}
