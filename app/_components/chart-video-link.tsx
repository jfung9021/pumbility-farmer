import { getChartVideoUrl } from "../../lib/chart-videos";

type ChartVideoLinkProps = {
  chartId: string;
  difficulty: string;
  songName: string;
  variant: "recommendation" | "tier" | "compact-tier" | "dialog";
};

export function ChartVideoLink({
  chartId,
  difficulty,
  songName,
  variant,
}: ChartVideoLinkProps) {
  const href = getChartVideoUrl(chartId);
  if (!href) return null;

  const label = `Watch ${songName} ${difficulty} chart on YouTube`;
  return (
    <a
      aria-label={label}
      className={`chart-video-link chart-video-link-${variant}`}
      href={href}
      rel="noopener noreferrer"
      target="_blank"
      title={label}
    >
      <svg aria-hidden="true" focusable="false" viewBox="0 0 28 20">
        <path
          d="M27.4 3.12A3.52 3.52 0 0 0 24.92.64C22.72.05 14 .05 14 .05S5.28.05 3.08.64A3.52 3.52 0 0 0 .6 3.12C.01 5.32.01 10 .01 10s0 4.68.59 6.88a3.52 3.52 0 0 0 2.48 2.48c2.2.59 10.92.59 10.92.59s8.72 0 10.92-.59a3.52 3.52 0 0 0 2.48-2.48c.59-2.2.59-6.88.59-6.88s0-4.68-.59-6.88Z"
          fill="currentColor"
        />
        <path d="m11.2 14.25 7.27-4.25-7.27-4.25v8.5Z" fill="white" />
      </svg>
    </a>
  );
}
