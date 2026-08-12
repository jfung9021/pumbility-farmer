import videoCatalog from "./data/nevsister-chart-videos.json";

const YOUTUBE_VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;

type ChartVideoCatalog = {
  schemaVersion: number;
  channelId: string;
  charts: Record<string, string>;
};

const catalog = videoCatalog as ChartVideoCatalog;

export function getChartVideoId(chartId: string): string | null {
  const videoId = catalog.charts[chartId];
  return typeof videoId === "string" && YOUTUBE_VIDEO_ID.test(videoId) ? videoId : null;
}

export function getChartVideoUrl(chartId: string): string | null {
  const videoId = getChartVideoId(chartId);
  return videoId ? `https://www.youtube.com/watch?v=${videoId}` : null;
}
