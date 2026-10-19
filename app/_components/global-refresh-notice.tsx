"use client";

import { useEffect, useState } from "react";
import { readJsonResponse } from "../../lib/api-response";
import { globalRefreshMessage, type RefreshStatus } from "../../lib/refresh-reliability";

export function GlobalRefreshNotice({ surface, nowMs, publishedAtUtc }: { surface: "tier" | "model"; nowMs: number; publishedAtUtc?: string | null }) {
  const [status, setStatus] = useState<RefreshStatus | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    fetch("/api/refresh-status", { cache: "no-store", signal: controller.signal })
      .then(response => readJsonResponse<RefreshStatus>(response))
      .then(value => { if (!controller.signal.aborted) setStatus(value); })
      .catch(() => { /* Data and its age remain usable when status is unavailable. */ })
      .finally(() => window.clearTimeout(timeout));
    return () => { window.clearTimeout(timeout); controller.abort(); };
  }, [surface, publishedAtUtc]);
  const message = status && nowMs ? globalRefreshMessage(status, surface, nowMs, publishedAtUtc) : null;
  return message ? <p className="refresh-meta refresh-meta-delayed" role="status"><b>{message}</b></p> : null;
}
