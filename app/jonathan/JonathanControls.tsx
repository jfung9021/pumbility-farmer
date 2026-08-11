"use client";

import { useEffect, useMemo, useState } from "react";

import { readJsonResponse } from "../../lib/api-response";
import type {
  AnalysisJobStatus,
  AnalysisRefreshResponse,
} from "../../lib/types";

import styles from "./jonathan.module.css";


type RefreshMode = "incremental" | "full";

const JOB_STORAGE_KEY = "analysisJobId:phoenix2";

function durationLabel(milliseconds: number): string {
  const seconds = Math.max(0, Math.ceil(milliseconds / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export default function JonathanControls() {
  const [password, setPassword] = useState("");
  const [job, setJob] = useState<AnalysisJobStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<RefreshMode | null>(null);
  const [nowMs, setNowMs] = useState(0);
  const [tabVisible, setTabVisible] = useState(true);

  useEffect(() => {
    setNowMs(Date.now());
    const clock = window.setInterval(() => setNowMs(Date.now()), 1000);
    const onVisibility = () => setTabVisible(document.visibilityState === "visible");
    onVisibility();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(clock);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  useEffect(() => {
    const storedJobId = window.localStorage.getItem(JOB_STORAGE_KEY);
    if (!storedJobId) return;
    fetch(`/api/analyze?mix=phoenix2&jobId=${encodeURIComponent(storedJobId)}`, {
      cache: "no-store",
    })
      .then((response) => readJsonResponse<AnalysisJobStatus>(response))
      .then(setJob)
      .catch(() => window.localStorage.removeItem(JOB_STORAGE_KEY));
  }, []);

  const jobIsActive = job?.status === "queued" || job?.status === "running";
  useEffect(() => {
    if (!job?.id || !jobIsActive) return;
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const response = await fetch(
          `/api/analyze?mix=phoenix2&jobId=${encodeURIComponent(job.id)}`,
          { cache: "no-store" },
        );
        const status = await readJsonResponse<AnalysisJobStatus>(response);
        if (cancelled) return;
        setJob(status);
        if (status.status === "completed") {
          window.localStorage.removeItem(JOB_STORAGE_KEY);
          setMessage("Phoenix 2 refresh and combined analysis completed.");
          return;
        }
        if (status.status === "failed") {
          setMessage(status.error || "The refresh failed.");
          return;
        }
      } catch (error) {
        if (!cancelled) {
          setMessage(error instanceof Error ? error.message : "Could not read refresh progress.");
        }
      }
      if (!cancelled) {
        timer = window.setTimeout(poll, tabVisible ? 2000 : 10_000);
      }
    };

    timer = window.setTimeout(poll, 0);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [job?.id, jobIsActive, tabVisible]);

  const retryMilliseconds = useMemo(() => {
    if (job?.status !== "failed" || !job.retryAllowedAtUtc || !nowMs) return 0;
    return Math.max(0, new Date(job.retryAllowedAtUtc).getTime() - nowMs);
  }, [job?.retryAllowedAtUtc, job?.status, nowMs]);

  const startRefresh = async (mode: RefreshMode) => {
    const providedPassword = password.trim();
    if (!providedPassword) {
      setMessage("Enter the operator password first.");
      return;
    }
    if (
      mode === "full"
      && !window.confirm(
        "Full refresh refetches every consented player's complete score history and can take substantially longer. Continue?",
      )
    ) {
      return;
    }

    setSubmitting(mode);
    setMessage(mode === "full" ? "Starting full refresh…" : "Starting incremental refresh…");
    try {
      const response = await fetch(`/api/jonathan/refresh?mode=${mode}`, {
        method: "POST",
        headers: { "X-Jonathan-Password": providedPassword },
      });
      const body = await readJsonResponse<AnalysisRefreshResponse>(response);
      setPassword("");
      if (body.outcome === "fresh") {
        setMessage("The current analysis is already fresh.");
        return;
      }
      if (body.outcome === "busy") {
        setMessage(body.error);
        return;
      }
      setJob(body.job);
      window.localStorage.setItem(JOB_STORAGE_KEY, body.job.id);
      setMessage(
        body.outcome === "existing"
          ? "Following the Phoenix 2 refresh already in progress."
          : null,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The refresh could not be started.");
    } finally {
      setSubmitting(null);
    }
  };

  const controlsDisabled = submitting !== null || jobIsActive || retryMilliseconds > 0;

  return (
    <div className={styles.controls}>
      <label className={styles.passwordField}>
        <span>Operator password</span>
        <input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          disabled={jobIsActive}
        />
      </label>

      <div className={styles.actions}>
        <button
          type="button"
          onClick={() => void startRefresh("incremental")}
          disabled={controlsDisabled}
        >
          {submitting === "incremental" ? "Starting…" : "Incremental refresh"}
        </button>
        <button
          className={styles.fullButton}
          type="button"
          onClick={() => void startRefresh("full")}
          disabled={controlsDisabled}
        >
          {submitting === "full" ? "Starting…" : "Full refresh"}
        </button>
      </div>

      {job ? (
        <section className={styles.status} aria-live="polite">
          <div className={styles.statusHeading}>
            <strong>{job.status === "queued" ? "Queued" : job.status}</strong>
            <span>{Math.round(job.progress.percent)}%</span>
          </div>
          <div
            className={styles.progressTrack}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(job.progress.percent)}
          >
            <span style={{ width: `${Math.max(0, Math.min(100, job.progress.percent))}%` }} />
          </div>
          <p>{job.progress.message}</p>
          {job.error ? <p className={styles.error}>{job.error}</p> : null}
          {retryMilliseconds > 0 ? (
            <p>Retry available in {durationLabel(retryMilliseconds)}.</p>
          ) : null}
        </section>
      ) : null}

      {message ? <p className={styles.message} role="status">{message}</p> : null}
    </div>
  );
}
