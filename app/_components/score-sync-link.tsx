import type { ReactNode } from "react";

export const SCORE_SYNC_URL =
  "https://piuscores.arroweclip.se/UploadPhoenixScores";

type ScoreSyncLinkProps = {
  children?: ReactNode;
  className?: string;
};

export function ScoreSyncLink({
  children = "Upload scores in the external PIUScores Tool",
  className,
}: ScoreSyncLinkProps) {
  const classes = ["score-sync-link", className].filter(Boolean).join(" ");

  return (
    <a
      className={classes}
      href={SCORE_SYNC_URL}
      rel="noopener noreferrer"
      target="_blank"
    >
      <span>{children}</span>
      <span className="score-sync-link-note">(opens in a new tab)</span>
      <span aria-hidden="true">↗</span>
    </a>
  );
}
