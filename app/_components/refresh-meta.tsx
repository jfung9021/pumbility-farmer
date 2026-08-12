type RefreshMetaProps = {
  generatedAtUtc?: string | null;
  loading?: boolean;
  loadingLabel: string;
  nowMs: number;
};

export function refreshAge(value: string, nowMs: number): string {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "unknown age";
  const elapsed = Math.max(0, nowMs - timestamp);
  if (elapsed < 60_000) return "just now";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}m ago`;
  return `${Math.floor(elapsed / 3_600_000)}h ago`;
}

export function RefreshMeta({
  generatedAtUtc,
  loading = false,
  loadingLabel,
  nowMs,
}: RefreshMetaProps) {
  let content = <span aria-hidden="true">&nbsp;</span>;
  if (generatedAtUtc && nowMs) {
    content = <span>Refresh age: <b>{refreshAge(generatedAtUtc, nowMs)}</b></span>;
  } else if (loading) {
    content = <span>{loadingLabel}</span>;
  }

  return (
    <div className="refresh-meta" aria-live="polite">
      {content}
    </div>
  );
}
