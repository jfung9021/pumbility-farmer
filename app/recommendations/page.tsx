"use client";

import { track } from "@vercel/analytics";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { RefreshMeta } from "../_components/refresh-meta";
import { ChartVideoLink } from "../_components/chart-video-link";
import { ScoreSyncLink } from "../_components/score-sync-link";
import { SiteHeader } from "../_components/site-header";
import { readJsonResponse } from "../../lib/api-response";
import { hasLimitedData } from "../../lib/chart-evidence";
import { formatEstimatedDifficulty } from "../../lib/format-difficulty";
import {
  recommendationModeFromSearchParams,
  recommendationViewFromSearchParams,
  type RecommendationView,
} from "../../lib/page-view-state";
import { pumbilityProgress } from "../../lib/pumbility-progress";
import {
  ALL_DIFFICULTIES,
  recommendationDifficultyOptions,
  visibleRecommendations as recommendationsForDifficulty,
} from "../../lib/recommendation-filters";
import type {
  ModeKey,
  PlayerRecommendationsResponse,
  PlayerRefreshJob,
  PlayerRefreshResponse,
  RecommendationChart,
  RecommendationModeKey,
  RecommendationPlayerSummary,
  RecommendationPlayersResponse,
  RecommendationScoreProgress,
  RecommendationTopScore,
} from "../../lib/types";


const RECOMMENDATION_MODES: RecommendationModeKey[] = [
  "overall",
  "singles",
  "doubles",
  "coop",
];
const INITIAL_DIFFICULTY_FILTERS: Record<RecommendationModeKey, string> = {
  overall: ALL_DIFFICULTIES,
  singles: ALL_DIFFICULTIES,
  doubles: ALL_DIFFICULTIES,
  coop: ALL_DIFFICULTIES,
};
const DEFAULT_RECOMMENDATION_SCORE_TARGET = 30;
const MODEL_DELAY_THRESHOLD_MS = 26 * 60 * 60 * 1000;
type StandardModeKey = Exclude<ModeKey, "coop">;


function signed(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function pumbilityLabel(value: number): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function ProgressStat({
  mode,
  value,
}: {
  mode: RecommendationModeKey;
  value: number;
}) {
  const progress = pumbilityProgress(mode, value);
  const atMaximum = progress.nextThreshold === null;
  const roundedPercent = Math.round(progress.percent);
  const ariaMinimum = atMaximum ? 0 : progress.threshold;
  const ariaMaximum = progress.nextThreshold ?? Math.max(progress.threshold, value);
  const ariaNow = Math.min(ariaMaximum, Math.max(ariaMinimum, value));
  const emblemLevel = String(progress.rungIndex).padStart(2, "0");
  const ratingName = mode === "coop" ? "Co-op Rating" : "Pumbility";
  return (
    <article className="pumbility-progress-panel">
      <div className={`pumbility-progress-heading${mode === "overall" ? "" : " no-emblem"}`}>
        {mode === "overall" ? (
          <img
            alt=""
            aria-hidden="true"
            className="pumbility-rank-emblem"
            height="88"
            src={`/images/phoenix2-ranks/pumbility_${emblemLevel}.webp`}
            width="88"
          />
        ) : null}
        <div>
          <span>{mode === "overall" ? "Rank progress" : "Skill title progress"}</span>
          <strong>{progress.label}</strong>
          <small>
            {progress.nextLabel
              ? `${pumbilityLabel(progress.remaining)} ${ratingName} to ${progress.nextLabel}`
              : `Maximum ${mode === "overall" ? "rank" : "skill title"} reached`}
          </small>
        </div>
        <b className="pumbility-progress-percent">{roundedPercent}%</b>
      </div>
      <div
        aria-label={`${progress.label} progress`}
        aria-valuemax={ariaMaximum}
        aria-valuemin={ariaMinimum}
        aria-valuenow={ariaNow}
        aria-valuetext={progress.nextLabel
          ? `${roundedPercent}%, ${pumbilityLabel(progress.remaining)} ${ratingName} to ${progress.nextLabel}`
          : `100%, maximum ${mode === "overall" ? "rank" : "skill title"} reached`}
        className="pumbility-progress"
        role="progressbar"
      >
        <b style={{ width: `${progress.percent}%` }} />
      </div>
      <div className="pumbility-progress-scale">
        <span><small>Start</small><b>{pumbilityLabel(progress.threshold)}</b></span>
        <span className="current"><small>Current</small><b>{pumbilityLabel(value)}</b></span>
        <span>
          <small>{progress.nextThreshold === null ? "End" : "Next"}</small>
          <b>{progress.nextThreshold === null
            ? "Maximum"
            : pumbilityLabel(progress.nextThreshold)}</b>
        </span>
      </div>
    </article>
  );
}

function scoreProgressForMode(
  summary: RecommendationPlayerSummary | undefined,
  payload: PlayerRecommendationsResponse | null,
  mode: StandardModeKey,
): RecommendationScoreProgress {
  const summaryProgress = summary?.scoreProgress?.[mode];
  const modeResult = payload?.player.modes[mode];
  const required = summaryProgress?.requiredScoreCount
    ?? modeResult?.projectionRatingRequiredScoreCount
    ?? modeResult?.requiredScoreCount
    ?? DEFAULT_RECOMMENDATION_SCORE_TARGET;
  const valid = summaryProgress?.validScoreCount
    ?? (modeResult?.projectionAvailable
      ? modeResult.projectionRatingSourceScoreCount
      : modeResult?.phoenix2ScoreCount)
    ?? modeResult?.validScoreCount
    ?? 0;
  return {
    validScoreCount: Math.max(0, Number.isFinite(valid) ? valid : 0),
    requiredScoreCount: Math.max(1, Number.isFinite(required) ? required : 1),
  };
}

function RecommendationReadiness({
  progress,
  detail,
}: {
  progress: Record<StandardModeKey, RecommendationScoreProgress>;
  detail?: string;
}) {
  const allModesReady = (["singles", "doubles"] as StandardModeKey[]).every(
    (modeKey) => progress[modeKey].validScoreCount
      >= progress[modeKey].requiredScoreCount,
  );
  return (
    <div className="recommendation-empty insufficient-state recommendation-readiness">
      <span aria-hidden="true">PF</span>
      <h2>{allModesReady ? "Recommendations are being prepared" : "Play more charts to unlock recommendations"}</h2>
      <p>{detail || (allModesReady
        ? "Your score history is ready. Recommendations will appear after the next player refresh."
        : "Build a larger score history in Singles and Doubles so we can calculate a reliable route for each mode.")}</p>
      <div className="recommendation-readiness-grid">
        {(["singles", "doubles"] as StandardModeKey[]).map((modeKey) => {
          const modeProgress = progress[modeKey];
          const current = Math.min(
            modeProgress.validScoreCount,
            modeProgress.requiredScoreCount,
          );
          const remaining = Math.max(
            0,
            modeProgress.requiredScoreCount - modeProgress.validScoreCount,
          );
          const percent = Math.min(
            100,
            (current / modeProgress.requiredScoreCount) * 100,
          );
          const label = modeKey === "singles" ? "Singles" : "Doubles";
          return (
            <article className="recommendation-readiness-mode" key={modeKey}>
              <div>
                <strong>{label}</strong>
                <b>{current}/{modeProgress.requiredScoreCount}</b>
              </div>
              <div
                aria-label={`${label} recommendation readiness`}
                aria-valuemax={modeProgress.requiredScoreCount}
                aria-valuemin={0}
                aria-valuenow={current}
                className="recommendation-readiness-progress"
                role="progressbar"
              >
                <b style={{ width: `${percent}%` }} />
              </div>
              <small>{remaining ? `${remaining} more to go` : "Ready for recommendations"}</small>
            </article>
          );
        })}
      </div>
    </div>
  );
}

function formatBpm(minimum: number | null | undefined, maximum: number | null | undefined): string | null {
  const min = typeof minimum === "number" && Number.isFinite(minimum) && minimum > 0 ? minimum : null;
  const max = typeof maximum === "number" && Number.isFinite(maximum) && maximum > 0 ? maximum : null;
  if (min === null && max === null) return null;
  const low = (min ?? max) as number;
  const high = (max ?? min) as number;
  const format = (value: number) => new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 2,
  }).format(value);
  return low === high ? `${format(low)} BPM` : `${format(low)}–${format(high)} BPM`;
}

function RecommendationCard({
  chart,
  rank,
}: {
  chart: RecommendationChart;
  rank: number;
}) {
  const bpm = formatBpm(chart.bpmMin, chart.bpmMax);
  const isCoop = chart.type === "CoOp";
  const estimate = isCoop
    ? formatEstimatedDifficulty(chart.estimatedDifficulty)
    : `${chart.type === "Single" ? "S" : "D"}${formatEstimatedDifficulty(chart.estimatedDifficulty)}`;
  const goal = chart.projectedGrade && chart.projectedPlateCode
    ? `Goal: ${chart.projectedGrade} ${chart.projectedPlateCode}`
    : null;
  const [pumbilityOpen, setPumbilityOpen] = useState(false);
  const pumbilityPopupId = useId();
  const pumbilityControlRef = useRef<HTMLDivElement>(null);
  const projectedGain = chart.projectedGain === null ? "-" : signed(chart.projectedGain);
  const currentRating = isCoop ? chart.existingCoopRating : chart.existingPumbility;
  const expectedRating = isCoop ? chart.expectedCoopRating : chart.expectedPumbility;
  const hasProjectedRating = expectedRating !== null && expectedRating !== undefined;
  const projectedRatingLabel = isCoop
    ? "Projected chart contribution"
    : "Total projected Pumbility";

  useEffect(() => {
    if (!pumbilityOpen) return;

    const closeOnOutsideClick = (event: PointerEvent) => {
      if (
        event.target instanceof Node
        && !pumbilityControlRef.current?.contains(event.target)
      ) {
        setPumbilityOpen(false);
      }
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setPumbilityOpen(false);
    };

    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [pumbilityOpen]);

  return (
    <article className="recommendation-card">
      <div className="recommendation-leading">
        <span className="recommendation-rank">{String(rank).padStart(2, "0")}</span>
        <ChartVideoLink
          chartId={chart.chartId}
          difficulty={chart.difficulty}
          songName={chart.songName}
          variant="recommendation"
        />
      </div>
      <div className="chart-art recommendation-jacket" data-chart-type={chart.type} aria-hidden="true">
        {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <b>{chart.difficulty}</b>}
        <span className={`chart-difficulty-badge chart-difficulty-${chart.type.toLowerCase()}`}>
          {isCoop ? `${chart.level}x` : chart.level}
        </span>
      </div>
      <div className="recommendation-copy">
        <div className="recommendation-title">
          <h3>{chart.songName}</h3>
          {hasLimitedData(chart.nContributors) ? (
            <span
              aria-label="Limited data"
              className="limited-data-warning"
              title={`Limited data: ${chart.nContributors} unique player observations`}
            >
              <b aria-hidden="true">!</b>
              <span>Limited data</span>
            </span>
          ) : null}
        </div>
        <p>
          {chart.stepArtist || "Unknown step artist"}
          {bpm ? <> · {bpm}</> : null}
          <b> · {estimate} estimate</b>
        </p>
        <div className="recommendation-tags">
          <span>{chart.played
            ? `Current ${currentRating?.toFixed(2) ?? "-"} ${isCoop ? "Co-op Rating contribution" : "PB"}`
            : "Unplayed in Phoenix 2"}</span>
        </div>
      </div>
      <div className="recommendation-value">
        <div
          className="recommendation-pumbility-control"
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) {
              setPumbilityOpen(false);
            }
          }}
          ref={pumbilityControlRef}
        >
          {hasProjectedRating ? (
            <button
              aria-controls={pumbilityPopupId}
              aria-expanded={pumbilityOpen}
              aria-label={`Projected gain ${projectedGain}. ${pumbilityOpen ? "Hide" : "Show"} ${projectedRatingLabel}.`}
              className="recommendation-pumbility-trigger"
              onClick={() => setPumbilityOpen((open) => !open)}
              type="button"
            >
              <span>projected gain</span>
              <strong>{projectedGain}</strong>
            </button>
          ) : (
            <div className="recommendation-pumbility-unavailable">
              <span>projected gain</span>
              <strong>{projectedGain}</strong>
            </div>
          )}
          {pumbilityOpen && expectedRating !== null && expectedRating !== undefined ? (
            <div
              className="recommendation-pumbility-popup"
              id={pumbilityPopupId}
              role="status"
            >
              <span>{projectedRatingLabel}</span>
              <strong>{pumbilityLabel(expectedRating)}</strong>
            </div>
          ) : null}
        </div>
        {goal ? (
          <div className="recommendation-goal">
            <b>{goal}</b>
          </div>
        ) : null}
      </div>
    </article>
  );
}

function topScoreRating(score: RecommendationTopScore): number | null {
  return score.type === "CoOp"
    ? score.coopRating ?? null
    : score.pumbility ?? null;
}

function TopScoreCard({
  rank,
  score,
  onSelect,
}: {
  rank: number;
  score: RecommendationTopScore;
  onSelect: (score: RecommendationTopScore, rank: number) => void;
}) {
  const limitedData = score.nContributors !== null
    && hasLimitedData(score.nContributors);
  const result = [score.grade, score.plateCode].filter(Boolean).join(" ") || "Result unavailable";
  const rating = topScoreRating(score);
  return (
    <article className="top-score-card">
      <button
        aria-label={`View details for rank ${rank}, ${score.songName}, ${score.difficulty}, ${result}`}
        className="top-score-card-button"
        onClick={() => onSelect(score, rank)}
        type="button"
      >
        <span className="chart-art top-score-jacket" data-chart-type={score.type}>
          {score.imageUrl
            ? <img alt="" loading="lazy" src={score.imageUrl} />
            : <b>{score.difficulty}</b>}
          <span aria-hidden="true" className="top-score-rank">#{rank}</span>
          {limitedData ? (
            <span
              aria-label={`Limited data: ${score.nContributors} unique player observations`}
              className="limited-data-warning compact-warning top-score-warning"
              role="img"
              title="Limited data"
            ><b aria-hidden="true">!</b></span>
          ) : null}
          <span
            aria-hidden="true"
            className={`chart-difficulty-badge chart-difficulty-${score.type.toLowerCase()}`}
          >
            {score.type === "CoOp" ? `${score.level}x` : score.level}
          </span>
        </span>
        <span className="top-score-result">
          <span>
            <b>{score.grade || "—"}</b>
            <small>{score.plateCode || "—"}</small>
          </span>
          <strong>{rating === null ? "—" : pumbilityLabel(rating)}</strong>
        </span>
      </button>
    </article>
  );
}

function TopScoreDetailDialog({
  rank,
  score,
  onClose,
}: {
  rank: number;
  score: RecommendationTopScore;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const headingId = useId();
  const descriptionId = useId();
  const bpm = formatBpm(score.bpmMin, score.bpmMax);
  const limitedData = score.nContributors !== null
    && hasLimitedData(score.nContributors);
  const isCoop = score.type === "CoOp";
  const prefix = score.type === "Single" ? "S" : "D";
  const rating = topScoreRating(score);

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();

    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable?.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus();
    };
  }, [onClose]);

  return (
    <div
      className="chart-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        aria-describedby={descriptionId}
        aria-labelledby={headingId}
        aria-modal="true"
        className="chart-dialog top-score-dialog"
        ref={dialogRef}
        role="dialog"
      >
        <button
          aria-label="Close score details"
          className="chart-dialog-close"
          onClick={onClose}
          ref={closeButtonRef}
          type="button"
        ><span aria-hidden="true">&times;</span></button>
        <div className="top-score-dialog-body">
          <div className="chart-dialog-art-rail top-score-dialog-art-rail">
            <div className="chart-art jacket chart-dialog-jacket" data-chart-type={score.type} aria-hidden="true">
              {score.imageUrl ? <img alt="" src={score.imageUrl} /> : <span>{score.difficulty}</span>}
            </div>
            <ChartVideoLink
              chartId={score.chartId}
              difficulty={score.difficulty}
              songName={score.songName}
              variant="dialog"
            />
          </div>
          <div className="chart-copy top-score-dialog-copy">
            <div className="chart-heading">
              <h3 id={headingId}>{score.songName}</h3>
              {limitedData ? (
                <span
                  aria-label={`Limited data: ${score.nContributors} unique player observations`}
                  className="limited-data-warning"
                  role="img"
                  title="Limited data"
                ><b aria-hidden="true">!</b><span>Limited data</span></span>
              ) : null}
            </div>
            <p id={descriptionId}>
              {score.stepArtist || "Unknown step artist"}
              {score.noteCount !== null ? ` · ${score.noteCount.toLocaleString()} notes` : ""}
              {bpm ? ` · ${bpm}` : ""}
            </p>
            <div className="chart-meta">
              {isCoop
                ? <span><b>{score.difficulty}</b> chart</span>
                : <span><b>{score.difficulty}</b> official</span>}
              <span><b>{score.estimatedDifficulty === null
                ? "Unavailable"
                : isCoop
                  ? formatEstimatedDifficulty(score.estimatedDifficulty)
                  : `${prefix}${formatEstimatedDifficulty(score.estimatedDifficulty)}`}</b> estimated</span>
              {!isCoop && score.difficultyDelta !== null ? (
                <span><b>{signed(score.difficultyDelta)}</b> difference</span>
              ) : null}
              {!isCoop && score.difficultyCi95Low !== null && score.difficultyCi95High !== null ? (
                <span><b>{formatEstimatedDifficulty(score.difficultyCi95Low)}–{formatEstimatedDifficulty(score.difficultyCi95High)}</b> CI</span>
              ) : null}
              {score.nContributors !== null ? (
                <span><b>{score.nContributors}</b> contributors</span>
              ) : null}
              {score.phoenix1Contributors !== null && score.phoenix2Contributors !== null ? (
                <span><b>{score.phoenix1Contributors}/{score.phoenix2Contributors}</b> P1/P2</span>
              ) : null}
              {score.evidenceStatus ? <span><b>{score.evidenceStatus}</b> evidence</span> : null}
            </div>
          </div>
          <div className="top-score-dialog-result">
            <span><small>Rank</small><strong>#{rank}</strong></span>
            <span>
              <small>{isCoop ? "Co-op Rating" : "Pumbility"}</small>
              <strong>{rating === null ? "Unavailable" : pumbilityLabel(rating)}</strong>
            </span>
            <span><small>Grade</small><strong>{score.grade || "Unavailable"}</strong></span>
            <span><small>Plate</small><strong>{score.plate
              ? `${score.plate}${score.plateCode ? ` (${score.plateCode})` : ""}`
              : score.plateCode || "Unavailable"}</strong></span>
          </div>
        </div>
      </div>
    </div>
  );
}

function TopScoresSection({
  mode,
  scores,
  onSelect,
}: {
  mode: RecommendationModeKey;
  scores: RecommendationTopScore[];
  onSelect: (score: RecommendationTopScore, rank: number) => void;
}) {
  const modeLabel = mode === "overall"
    ? "Overall"
    : mode === "singles" ? "Singles" : mode === "doubles" ? "Doubles" : "Co-op";
  const isCoop = mode === "coop";
  return (
    <section className="top-scores" aria-labelledby="top-scores-title">
      <div className="recommendation-section-heading top-scores-heading">
        {isCoop
          ? <h2 id="top-scores-title">CO-OP RATING SCORES</h2>
          : <h2 id="top-scores-title">TOP 50 PUMBILITY SCORES</h2>}
        <span>{isCoop ? `Showing ${scores.length} scored charts` : `Showing ${scores.length} of up to 50`}</span>
      </div>
      {scores.length ? (
        <div className="top-score-grid">
          {scores.map((score, index) => (
            <TopScoreCard
              key={`${score.type}-${score.chartId}`}
              onSelect={onSelect}
              rank={index + 1}
              score={score}
            />
          ))}
        </div>
      ) : (
        <div className="top-score-empty">
          <h3>{isCoop ? "No Co-op scores yet" : "No Top 50 scores yet"}</h3>
          <p>{modeLabel} Phoenix 2 {isCoop ? "Co-op Rating" : "Pumbility"} scores will appear here after score sync.</p>
        </div>
      )}
    </section>
  );
}

export default function RecommendationsPage() {
  const [playersPayload, setPlayersPayload] = useState<RecommendationPlayersResponse | null>(null);
  const [playerPayload, setPlayerPayload] = useState<PlayerRecommendationsResponse | null>(null);
  const [selectedKey, setSelectedKey] = useState("");
  const [playerQuery, setPlayerQuery] = useState("");
  const [playerMenuOpen, setPlayerMenuOpen] = useState(false);
  const [activeMode, setActiveMode] = useState<RecommendationModeKey>("overall");
  const [recommendationView, setRecommendationView] = useState<RecommendationView>("recommendations");
  const [selectedTopScore, setSelectedTopScore] = useState<{
    score: RecommendationTopScore;
    rank: number;
  } | null>(null);
  const [difficultyFilters, setDifficultyFilters] = useState<
    Record<RecommendationModeKey, string>
  >({ ...INITIAL_DIFFICULTY_FILTERS });
  const [loadingPlayers, setLoadingPlayers] = useState(true);
  const [loadingPlayer, setLoadingPlayer] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshWarning, setRefreshWarning] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(0);

  useEffect(() => {
    setNowMs(Date.now());
    const timer = window.setInterval(() => setNowMs(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const applyViewFromUrl = () => {
      const params = new URLSearchParams(window.location.search);
      setActiveMode(recommendationModeFromSearchParams(params));
      setRecommendationView(recommendationViewFromSearchParams(params));
      setSelectedTopScore(null);
    };
    applyViewFromUrl();
    window.addEventListener("popstate", applyViewFromUrl);
    return () => window.removeEventListener("popstate", applyViewFromUrl);
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/recommendations/players")
      .then((response) => readJsonResponse<RecommendationPlayersResponse>(response))
      .then((payload) => {
        if (cancelled) return;
        setPlayersPayload(payload);
        const params = new URLSearchParams(window.location.search);
        const requested = params.get("player") || "";
        const requestedPlayer = payload.players.find((player) => player.playerKey === requested);
        if (requestedPlayer) {
          setSelectedKey(requestedPlayer.playerKey);
          setPlayerQuery(requestedPlayer.displayName);
        }
      })
      .catch((caught) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "Could not load players.");
      })
      .finally(() => {
        if (!cancelled) setLoadingPlayers(false);
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!selectedKey) {
      setPlayerPayload(null);
      setRefreshWarning(null);
      return;
    }
    const controller = new AbortController();
    setPlayerPayload(null);
    setLoadingPlayer(true);
    setError(null);
    setRefreshWarning(null);
    let cachedLoaded = false;

    const loadCached = async () => {
      const response = await fetch(
        `/api/recommendations?playerKey=${encodeURIComponent(selectedKey)}`,
        { cache: "no-store", signal: controller.signal },
      );
      if (response.status === 404) return null;
      const payload = await readJsonResponse<PlayerRecommendationsResponse>(response);
      cachedLoaded = true;
      setPlayerPayload(payload);
      setLoadingPlayer(false);
      return payload;
    };

    const waitForJob = async (initial: PlayerRefreshJob) => {
      let job = initial;
      const deadline = Date.now() + 30_000;
      while (!controller.signal.aborted && ["queued", "running"].includes(job.status)) {
        if (Date.now() >= deadline) {
          throw new Error("The refresh took longer than 30 seconds. Please retry.");
        }
        await new Promise<void>((resolve) => window.setTimeout(resolve, 1000));
        if (controller.signal.aborted) return;
        const response = await fetch(
          `/api/recommendations/refresh?jobId=${encodeURIComponent(job.id)}`,
          { cache: "no-store", signal: controller.signal },
        );
        job = await readJsonResponse<PlayerRefreshJob>(response);
      }
      if (job.status === "failed") {
        throw new Error(job.error || "Could not refresh this player's recommendations.");
      }
    };

    const refresh = async () => {
      if (playersPayload?.refreshSupported === false) return;
      const response = await fetch(
        `/api/recommendations/refresh?playerKey=${encodeURIComponent(selectedKey)}`,
        { method: "POST", cache: "no-store", signal: controller.signal },
      );
      const started = await readJsonResponse<PlayerRefreshResponse>(response);
      if (started.outcome === "fresh") {
        cachedLoaded = true;
        setPlayerPayload(started.recommendation);
        setRefreshWarning(null);
        return;
      }
      await waitForJob(started.job);
      const refreshed = await loadCached();
      if (!refreshed) throw new Error("The refreshed recommendations are unavailable.");
      setRefreshWarning(null);
    };

    void (async () => {
      try {
        await loadCached();
        await refresh();
      } catch (caught) {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        const message = caught instanceof Error
          ? caught.message
          : "Could not refresh recommendations.";
        if (cachedLoaded) {
          setRefreshWarning(`Showing cached recommendations because score refresh failed: ${message}`);
        } else {
          setError(message);
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoadingPlayer(false);
        }
      }
    })();
    return () => controller.abort();
  }, [playersPayload?.refreshSupported, selectedKey]);

  const selectPlayer = (playerKey: string, inputValue = "") => {
    track("recommendation_player_selected", { playerName: inputValue });
    if (playerKey !== selectedKey) {
      setDifficultyFilters({ ...INITIAL_DIFFICULTY_FILTERS });
    }
    setSelectedKey(playerKey);
    setPlayerQuery(inputValue);
    setPlayerMenuOpen(false);
    setSelectedTopScore(null);
    const url = new URL(window.location.href);
    if (playerKey) url.searchParams.set("player", playerKey);
    else url.searchParams.delete("player");
    window.history.replaceState({}, "", url);
  };

  const mode = playerPayload?.player.modes[activeMode] || null;
  const modeRating = activeMode === "coop"
    ? mode?.currentCoopRating ?? 0
    : mode?.currentTop50Pumbility ?? 0;
  const sourceModeEligibility = mode?.sourceModeEligibility;
  const unavailableOverallModes = activeMode === "overall" && sourceModeEligibility
    ? (["singles", "doubles"] as StandardModeKey[]).filter(
        (modeKey) => !sourceModeEligibility[modeKey],
      )
    : [];
  const selectedPlayer = playersPayload?.players.find(
    (player) => player.playerKey === selectedKey,
  );
  const scoreReadiness: Record<StandardModeKey, RecommendationScoreProgress> = {
    singles: scoreProgressForMode(selectedPlayer, playerPayload, "singles"),
    doubles: scoreProgressForMode(selectedPlayer, playerPayload, "doubles"),
  };

  const updatePageUrl = (
    mode: RecommendationModeKey,
    view: RecommendationView,
  ) => {
    const url = new URL(window.location.href);
    if (url.searchParams.get("mode") === mode && url.searchParams.get("view") === view) return;
    url.searchParams.set("mode", mode);
    url.searchParams.set("view", view);
    window.history.pushState({}, "", url);
  };
  const selectMode = (mode: RecommendationModeKey) => {
    setSelectedTopScore(null);
    setActiveMode(mode);
    updatePageUrl(mode, recommendationView);
  };
  const selectView = (view: RecommendationView) => {
    setSelectedTopScore(null);
    setRecommendationView(view);
    updatePageUrl(activeMode, view);
  };

  const handleTabKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    currentIndex: number,
  ) => {
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % RECOMMENDATION_MODES.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + RECOMMENDATION_MODES.length)
        % RECOMMENDATION_MODES.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = RECOMMENDATION_MODES.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    const nextMode = RECOMMENDATION_MODES[nextIndex];
    selectMode(nextMode);
    window.requestAnimationFrame(() => {
      document.getElementById(`recommendation-tab-${nextMode}`)?.focus();
    });
  };
  const handlePlayerInput = (value: string) => {
    setPlayerQuery(value);
    setPlayerMenuOpen(true);
    setSelectedTopScore(null);
    if (!selectedKey) return;
    setDifficultyFilters({ ...INITIAL_DIFFICULTY_FILTERS });
    setSelectedKey("");
    const url = new URL(window.location.href);
    url.searchParams.delete("player");
    window.history.replaceState({}, "", url);
  };

  const filteredPlayers = useMemo(() => {
    const players = playersPayload?.players || [];
    const normalized = selectedKey ? "" : playerQuery.trim().toLocaleLowerCase();
    if (!normalized) return players;
    return players.filter((player) =>
      `${player.displayName} ${player.username}`.toLocaleLowerCase().includes(normalized),
    );
  }, [playerQuery, playersPayload, selectedKey]);

  const difficultyOptions = useMemo(
    () => recommendationDifficultyOptions(
      activeMode,
      mode?.filterCandidates ?? [],
    ),
    [activeMode, mode?.filterCandidates],
  );
  const selectedDifficulty = difficultyFilters[activeMode];
  const effectiveDifficulty = selectedDifficulty === ALL_DIFFICULTIES
    || difficultyOptions.includes(selectedDifficulty)
    ? selectedDifficulty
    : ALL_DIFFICULTIES;
  const visibleRecommendations = useMemo(
    () => recommendationsForDifficulty(activeMode, mode, effectiveDifficulty),
    [activeMode, effectiveDifficulty, mode],
  );
  const closeTopScoreDialog = useCallback(() => setSelectedTopScore(null), []);

  useEffect(() => {
    if (!playerPayload || !mode || selectedDifficulty === ALL_DIFFICULTIES) return;
    if (difficultyOptions.includes(selectedDifficulty)) return;
    setDifficultyFilters((current) => ({
      ...current,
      [activeMode]: ALL_DIFFICULTIES,
    }));
  }, [activeMode, difficultyOptions, mode, playerPayload, selectedDifficulty]);

  const modelGeneratedAt = playerPayload?.currentModelGeneratedAtUtc
    || playerPayload?.modelGeneratedAtUtc
    || playersPayload?.modelGeneratedAtUtc
    || playersPayload?.generatedAtUtc;
  const playerSyncedAt = playerPayload?.playerSyncedAtUtc
    || playerPayload?.generatedAtUtc;

  const hasSelection = Boolean(selectedKey);

  return (
    <main className="recommendations-page">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <SiteHeader active="recommendations" />

      <section className="hero page-title-hero recommendations-title-hero">
        <h1>Recommended Charts</h1>
        <div className="refresh-meta-group">
          <RefreshMeta
            delayedAfterMs={MODEL_DELAY_THRESHOLD_MS}
            generatedAtUtc={modelGeneratedAt}
            label="Model updated"
            loading={loadingPlayers}
            loadingLabel="Loading recommendations..."
            nowMs={nowMs}
          />
          {playerPayload && playerSyncedAt ? (
            <RefreshMeta
              generatedAtUtc={playerSyncedAt}
              label="Player scores synced"
              loadingLabel="Loading player scores..."
              nowMs={nowMs}
            />
          ) : null}
        </div>
      </section>

      <section className="recommendations-hero">
        <div className="player-picker">
          <label htmlFor="player-select">Phoenix 2 username</label>
          <span className="player-picker-view-label" id="recommendation-view-label">View</span>
          <div className="player-combobox">
            <input
              aria-controls="player-options"
              aria-expanded={playerMenuOpen}
              aria-haspopup="listbox"
              autoComplete="off"
              disabled={loadingPlayers || !playersPayload?.players.length}
              id="player-select"
              onBlur={() => setPlayerMenuOpen(false)}
              onChange={(event) => handlePlayerInput(event.target.value)}
              onClick={() => setPlayerMenuOpen(true)}
              onFocus={(event) => {
                event.currentTarget.select();
                setPlayerMenuOpen(true);
              }}
              placeholder="Type or select a player"
              role="combobox"
              type="text"
              value={playerQuery}
            />
            {playerMenuOpen ? (
              <div className="player-options" id="player-options" role="listbox">
                {filteredPlayers.map((player) => (
                  <button
                    aria-selected={selectedKey === player.playerKey}
                    key={player.playerKey}
                    onClick={() => selectPlayer(player.playerKey, player.displayName)}
                    onMouseDown={(event) => event.preventDefault()}
                    role="option"
                    type="button"
                  >
                    {player.displayName}
                  </button>
                ))}
                {!filteredPlayers.length ? <p>No matching usernames</p> : null}
              </div>
            ) : null}
          </div>
          <button
            aria-checked={recommendationView === "top50"}
            aria-labelledby="recommendation-view-label"
            className={`recommendation-view-switch ${recommendationView === "top50" ? "top50-active" : "recommendations-active"}`}
            onClick={() => {
              selectView(recommendationView === "recommendations" ? "top50" : "recommendations");
            }}
            role="switch"
            type="button"
          >
            <span>Recommendations</span>
            <span>{activeMode === "coop" ? "Scores" : "Top 50"}</span>
          </button>
          <div className="player-picker-meta">
            <ScoreSyncLink className="player-score-sync-link" />
          </div>
        </div>
        {error ? <div className="recommendation-notice error-notice">{error}</div> : null}
        {refreshWarning ? (
          <div className="recommendation-notice stale-notice" role="status">
            <span aria-hidden="true" className="recommendation-warning-icon">!</span>
            <span>{refreshWarning}</span>
          </div>
        ) : null}
      </section>

      <section className="recommendations-workspace" aria-busy={loadingPlayer}>
        {!hasSelection ? (
          <div className="recommendation-empty">
            <span>PF</span>
            <h2>Select a username</h2>
            <p>Your internal player ID and raw score history are never returned to the browser.</p>
          </div>
        ) : loadingPlayer && !playerPayload ? (
          <div className="recommendation-empty"><span className="spinner" /><h2>Calculating your route</h2></div>
        ) : playerPayload ? (
          <>
            <div className="recommendation-mode-row">
              <div className="recommendation-mode-tabs" role="tablist" aria-label="Recommendation mode">
                {RECOMMENDATION_MODES.map((modeKey, index) => (
                  <button
                    aria-controls="recommendation-panel"
                    aria-selected={activeMode === modeKey}
                    className={activeMode === modeKey ? "active" : ""}
                    id={`recommendation-tab-${modeKey}`}
                    key={modeKey}
                    onClick={() => selectMode(modeKey)}
                    onKeyDown={(event) => handleTabKeyDown(event, index)}
                    role="tab"
                    tabIndex={activeMode === modeKey ? 0 : -1}
                    type="button"
                  >
                    <b>{modeKey === "overall" ? "O" : modeKey === "singles" ? "S" : modeKey === "doubles" ? "D" : "C"}</b>
                    <span>{modeKey === "coop" ? "Co-op" : modeKey}</span>
                  </button>
                ))}
              </div>
            </div>

            <div
              aria-labelledby={`recommendation-tab-${activeMode}`}
              id="recommendation-panel"
              role="tabpanel"
            >
              {mode && (mode.eligible || recommendationView === "top50") ? (
                <ProgressStat mode={activeMode} value={modeRating} />
              ) : null}

              {recommendationView === "recommendations" ? unavailableOverallModes.map((modeKey) => {
                const label = modeKey === "singles" ? "Singles" : "Doubles";
                const remaining = Math.max(
                  0,
                  scoreReadiness[modeKey].requiredScoreCount
                    - scoreReadiness[modeKey].validScoreCount,
                );
                return (
                  <div className="recommendation-notice overall-source-notice" key={modeKey}>
                    <span aria-hidden="true" className="recommendation-warning-icon">!</span>
                    <span>
                      {remaining
                        ? `Need to play ${remaining} more ${label} chart${remaining === 1 ? "" : "s"} to show ${label} recommendations.`
                        : `${label} recommendations are not available yet. ${playerPayload.player.modes[modeKey].reason || "This mode cannot be rated yet."}`}
                    </span>
                  </div>
                );
              }) : null}

              {activeMode === "overall" && !mode ? (
                <div className="recommendation-empty insufficient-state">
                  <span>O</span>
                  <h2>Overall is being prepared</h2>
                  <p>
                    This cached recommendation predates the Overall model. Single and Double
                    remain available while the latest analysis is published.
                  </p>
                </div>
              ) : activeMode === "coop" && !mode ? (
                <div className="recommendation-empty insufficient-state">
                  <span>C</span>
                  <h2>Co-op is being prepared</h2>
                  <p>This cached recommendation predates Co-op analysis. Overall, Singles, and Doubles remain available.</p>
                </div>
              ) : recommendationView === "top50" ? (
                <TopScoresSection
                  mode={activeMode}
                  onSelect={(score, rank) => setSelectedTopScore({ score, rank })}
                  scores={mode?.topScores ?? []}
                />
              ) : !mode?.eligible && activeMode === "coop" ? (
                <div className="recommendation-empty insufficient-state">
                  <span>C</span>
                  <h2>Co-op recommendations are unavailable</h2>
                  <p>{mode?.reason || "This player cannot be rated for Co-op yet."}</p>
                </div>
              ) : !mode?.eligible ? (
                <RecommendationReadiness
                  detail={mode?.reason || "This mode cannot be rated yet."}
                  progress={scoreReadiness}
                />
              ) : (
                <section className="top-recommendations" aria-labelledby="top-recommendations-title">
                  <div className="recommendation-section-heading">
                    <h2 id="top-recommendations-title">RECOMMENDED CHARTS</h2>
                    <label className="recommendation-difficulty-filter">
                      <span className="visually-hidden">{activeMode === "coop" ? "Player count" : "Official difficulty"}</span>
                      <select
                        aria-label={activeMode === "coop" ? "Player count" : "Official difficulty"}
                        onChange={(event) => setDifficultyFilters((current) => ({
                          ...current,
                          [activeMode]: event.target.value,
                        }))}
                        value={effectiveDifficulty}
                      >
                        <option value={ALL_DIFFICULTIES}>
                          {activeMode === "coop" ? "All player types" : "All difficulties"}
                        </option>
                        {difficultyOptions.map((difficulty) => (
                          <option key={difficulty} value={difficulty}>{difficulty}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <div className="recommendation-list">
                    {visibleRecommendations.length ? visibleRecommendations.map((chart, index) => (
                      <RecommendationCard chart={chart} key={chart.chartId} rank={index + 1} />
                    )) : (
                      <p className="no-recommendations">
                        {effectiveDifficulty === ALL_DIFFICULTIES
                          ? activeMode === "coop"
                            ? "No Co-op chart is projected to increase the current Co-op Rating."
                            : "No chart is projected to improve the current top 50."
                          : `No ${effectiveDifficulty} charts are available in the recommendation dataset.`}
                      </p>
                    )}
                  </div>
                </section>
              )}
            </div>
          </>
        ) : selectedPlayer ? (
          <RecommendationReadiness progress={scoreReadiness} />
        ) : null}
      </section>

      {selectedTopScore ? (
        <TopScoreDetailDialog
          onClose={closeTopScoreDialog}
          rank={selectedTopScore.rank}
          score={selectedTopScore.score}
        />
      ) : null}

      <footer>
        <p><b>How the merge works</b> Phoenix 2 charts.json is a strict allowlist. When a player has a score in both versions, only their best Phoenix 2 score is used.</p>
        <p>Phoenix 1 scores are rebased to Phoenix 2 chart levels before each version is normalized and combined. Removed Phoenix 1 charts never enter this engine.</p>
        <p>Singles and Doubles projected scores use the ranks 11–30 Pumbility rating and the Phoenix-weighted median (50th percentile) from all other players with a normalized result on the exact chart, giving Phoenix 2 results twice the weight of Phoenix 1. The search tries plus or minus 0.2 through 0.5 rating in 0.1 steps seeking 20 peers, repeats those radii seeking 10, then repeats seeking five. Every peer within the narrowest successful radius is used; below five peers, the player-balanced population model uses the same Phoenix weighting.</p>
        <p>For Singles and Doubles, the projected result is raised by one letter grade, capped at SSS+, to set the goal score. The projected plate is the weighted median in Phoenix 2 order from Rough Game through Perfect Game. Expected Pumbility is then calculated once from that goal grade, the median plate, and the chart&apos;s mode-specific formula. Projected gain is the deterministic top-50 change from that same goal result.</p>
        <p>Co-op recommendations assign a letter-grade goal from each chart&apos;s whole-number estimated difficulty, with the same one-grade boost capped at SSS+: harder charts receive lower goals. Every goal uses a Fair Game plate, and completing all current chart goals clears the 16,000 Co-op Rating [CO-OP] Master threshold with extra leeway. Projected gain is additive rather than limited to a top-50 pool; equal gains use the underlying continuous difficulty for ordering.</p>
        <p>The underlying Co-op tier model adjusts miss points for player strength and Phoenix source using all observations, then estimates the conditional 75th-percentile score for a median-strength Phoenix 2 player. The conditional quantile supplies outlier robustness without trimming raw scores or residuals. Chart order anchors the easiest chart at continuous difficulty 10, the median chart at 16, and the hardest chart at 24.9, then truncates the published difficulty to a whole-number range from 10 through 24 without forcing a normal distribution.</p>
        <p>The visible skill rating uses top-20 average Pumbility and is expressed as the continuous chart level where an S with Fair Game earns the selected window&apos;s average Pumbility. Phoenix 2 supplies a window once it is complete; otherwise a complete Phoenix 1 window is used, followed by partial Phoenix 2. Singles and Doubles recommendations extend up to 1.0 estimated-difficulty point above that mode&apos;s rating; Overall merges those capped mode lists.</p>
        <p>Played status, existing chart Pumbility, and current top 50 use the Pumbility supplied by Phoenix 2 rather than recomputing historical results. Overall Pumbility is the best 50 values across both modes; Overall recommendations merge each mode&apos;s displayed top 20 and recalculate their deterministic gain against that shared top-50 pool. Official-difficulty filters show every matching level-16+ chart, ordered by projected Pumbility gain. Projections are estimates, not guaranteed results.</p>
      </footer>
    </main>
  );
}
