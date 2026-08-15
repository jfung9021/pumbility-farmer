type RefreshMetaProps = {
  delayedAfterMs?: number;
  generatedAtUtc?: string | null;
  label: string;
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
  delayedAfterMs,
  generatedAtUtc,
  label,
  loading = false,
  loadingLabel,
  nowMs,
}: RefreshMetaProps) {
  const generatedAtMs = generatedAtUtc ? new Date(generatedAtUtc).getTime() : Number.NaN;
  const delayed = Boolean(
    delayedAfterMs
    && nowMs
    && Number.isFinite(generatedAtMs)
    && Math.max(0, nowMs - generatedAtMs) > delayedAfterMs,
  );
  let content = <span aria-hidden="true">&nbsp;</span>;
  if (generatedAtUtc && nowMs) {
    content = (
      <>
        <span>{label}: <b>{refreshAge(generatedAtUtc, nowMs)}</b></span>
        {delayed ? <span className="refresh-delay-warning">Delayed</span> : null}
      </>
    );
  } else if (loading) {
    content = <span>{loadingLabel}</span>;
  }

  return (
    <div className={`refresh-meta${delayed ? " refresh-meta-delayed" : ""}`} aria-live="polite">
      {content}
    </div>
  );
}
