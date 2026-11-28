"use client";

import { useEffect, useId, useState, type FormEvent, type KeyboardEvent } from "react";
import { readJsonResponse } from "../../lib/api-response";
import { validateProPlayersResponse, validateProRecommendationsResponse, type ProPlayersResponse,
  type ProModeKey, type ProModeView, type ProModeResult, type ProRecommendationChart, type ProOverallRecommendationChart,
  type ProRecommendationsResponse } from "../../lib/pro-recommendations";
import { ChartVideoLink } from "./chart-video-link";
import { PlayerRequired } from "./player-required";
import { ResultGrade, ResultPlate, ResultScore } from "./player-result";
import { SCORE_SYNC_URL } from "./score-sync-link";
import styles from "./pro-recommendations.module.css";

const VIEWS: ProModeView[] = ["overall", "singles", "doubles"];
const name = (mode: ProModeView) => mode[0].toUpperCase() + mode.slice(1);
const pumbility = (v: number) => v.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const score = (v: number) => v.toLocaleString("en-US", {maximumFractionDigits: 2});
function sourceDate(v: string | undefined) {
  if (!v) return "Unavailable";
  const date = new Date(v);
  return Number.isNaN(date.getTime()) ? "Unavailable" : date.toLocaleString("en-GB", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC"}) + " UTC";
}
function ProChart({chart, rank, overall}: {chart: ProRecommendationChart | ProOverallRecommendationChart; rank: number; overall: boolean}) {
  const gainLabel = overall ? "Overall" : name(chart.sourceMode);
  const bpm = chart.bpmMin ? chart.bpmMax && chart.bpmMax !== chart.bpmMin ? `${score(chart.bpmMin)}–${score(chart.bpmMax)} BPM` : `${score(chart.bpmMin)} BPM` : null;
  return <li><article className="recommendation-card">
    <div className="recommendation-leading">
      <span className="recommendation-rank">{String(chart.recommendationRank ?? rank).padStart(2, "0")}</span>
      <ChartVideoLink chartId={chart.chartId} difficulty={chart.chartLabel} songName={chart.songName} variant="recommendation" />
    </div>
    <div className="chart-art recommendation-jacket" data-chart-type={chart.type} aria-hidden="true">
      {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <b>{chart.chartLabel}</b>}
      <span className={`chart-difficulty-badge chart-difficulty-${chart.type.toLowerCase()}`}>{chart.level}</span>
    </div>
    <div className="recommendation-copy">
      <div className="recommendation-title">
        <h3>{chart.songName}</h3>
        {chart.peerScoreCount <= 5 ? <span className="limited-data-warning" aria-label="Uncommon play"
          title={`Uncommon play: ${chart.peerScoreCount}/${chart.cohortPlayerCount} peers have a recorded score for this chart.`}>
          <b aria-hidden="true">!</b><span>Uncommon play</span></span> : null}
      </div>
      <p>{chart.stepArtist || chart.chartLabel}{bpm ? ` · ${bpm}` : ""}</p>
      <div className="recommendation-tags"><span title="Peer results at or above this recommended target score">{chart.scoreAtOrAboveTargetCount} at or above target</span><span>{chart.currentScore === null ? "No recorded PB" : <>
        PB <ResultScore score={chart.currentScore} /> {chart.currentPlate ? <ResultPlate plate={chart.currentPlate} /> : null}
        </>}</span></div>
    </div>
    <div className="recommendation-value">
      <details className={styles.gainDetails}>
        <summary className={styles.gainSummary} aria-label={`${chart.songName} ${chart.chartLabel}: ${gainLabel} gain +${pumbility(chart.projectedGain)}. Calculation details`}>
          <span>{gainLabel} gain</span><strong>+{pumbility(chart.projectedGain)}</strong>
        </summary>
        <div className={styles.gainBreakdown}>
          <b>{chart.chartLabel} · {gainLabel} Top 50</b>
          <p>Target <ResultScore score={chart.targetScore} grade={chart.targetGrade} /> · <ResultPlate plate={chart.assumedPlate} /></p>
          <p>Peer 50th percentile: {score(chart.rawPercentileScore)}</p>
          <p>{chart.peerScoreCount}/{chart.cohortPlayerCount} peer scores{chart.boardPossiblyTruncated ? " · Leaderboard capped" : ""}</p>
          <p>{chart.scoreAtOrAboveTargetCount} peer results at or above target</p>
          <p>Gain rank {chart.gainRank} + target-count rank {chart.scoreCountRank} = {chart.rankScore}</p>
          <p>Target chart Pumbility: {pumbility(chart.expectedPumbility)}</p>
          <p>{chart.currentPumbility === null ? "No recorded chart Pumbility" : `Current chart Pumbility: ${pumbility(chart.currentPumbility)}`}</p>
          {overall && "modeProjectedGain" in chart ? <>
            <p>Replaced overall contribution: {pumbility(chart.replacedPumbility)}</p>
            <p>{name(chart.sourceMode)} gain: +{pumbility(chart.modeProjectedGain)}</p>
            <p>Estimated from the reconstructed overall Top 50.</p>
          </> : <p>Estimated {chart.sourceMode} Top 50 gain.</p>}
        </div>
      </details>
      <div className="recommendation-goal"><b>Goal: <ResultGrade grade={chart.targetGrade} /> <ResultPlate plate={chart.assumedPlate} /></b></div>
    </div>
  </article></li>;
}
function CapturedPlayerChooser({index, currentKey, mode}: {index: ProPlayersResponse; currentKey: string; mode: ProModeView}) {
  const id = useId(); const [chosen, setChosen] = useState("");
  const players = index.players.filter(p => p.status === "ready");
  if (!players.length) return null;
  const open = (event: FormEvent) => {
    event.preventDefault(); if (!players.some(p => p.playerKey === chosen)) return;
    const url = new URL(window.location.href);
    url.searchParams.set("view", "pro"); url.searchParams.set("mode", mode); url.searchParams.set("player", chosen);
    window.location.assign(url.href);
  };
  return <details className={styles.playerChooser} open={!currentKey}>
    <summary>Choose an official top-500 player</summary>
    <form onSubmit={open}><label htmlFor={id}>Player</label><div>
      <select id={id} value={chosen} onChange={e => setChosen(e.target.value)}>
        <option value="">Choose an eligible player</option>
        {players.map(p => <option key={p.playerKey} value={p.playerKey}>#{p.overallRank} · {p.officialDisplayName || p.displayName}</option>)}
      </select><button type="submit" disabled={!chosen}>View player</button>
    </div></form>
  </details>;
}
function ModeSummary({result}: {result: Pick<ProModeResult, "baseline" | "cohort" | "modePumbility" | "status" | "message"> & {mode: ProModeView}}) {
  const {baseline, cohort} = result; const label = name(result.mode);
  return <section className={styles.cohort} aria-label={`${label} Pumbility and comparison group`}>
    <h2>{label} <strong>{result.modePumbility === null ? "Insufficient data" : pumbility(result.modePumbility)}</strong>
      {baseline?.estimated && result.modePumbility !== null ? <small>estimated</small> : null}</h2>
    {result.status !== "ready" ? <p>{result.message || `${label} data is unavailable.`}</p> : null}
    {cohort ? <p>{cohort.playerCount} peers · +{cohort.upwardWindow} window{cohort.backfillCount ? ` · ${cohort.backfillCount} below` : ""}
      {baseline?.cutoffPumbility !== null && baseline?.cutoffPumbility !== undefined ? ` · cutoff ${pumbility(baseline.cutoffPumbility)}` : ""}</p> : null}
    {result.mode === "overall" && baseline?.cutoffPumbility !== null && baseline?.cutoffPumbility !== undefined ?
      <p>Combined Top 50 · cutoff {pumbility(baseline.cutoffPumbility)} · {baseline.scoreCount} observed charts</p> : null}
    {baseline ? <>
      <details className={styles.top50}><summary>View {label} {baseline.top50.length === 50 ? "Top 50" : "observed charts"}</summary>
        <p>{baseline.scoreCount} observed charts · {baseline.source === "piuscores" ? "PIUScores" : baseline.source === "mixed" ? "PIUScores + official estimates" : "Official leaderboard estimates"}</p>
        {baseline.fallbackReason ? <p>{baseline.fallbackReason.replace(" with assumed plates", "")}</p> : null}
        {cohort?.minimumPumbility !== null && cohort?.minimumPumbility !== undefined && cohort.maximumPumbility !== null ?
          <p>Peer range: {pumbility(cohort.minimumPumbility)}–{pumbility(cohort.maximumPumbility)}.</p> : null}
        <p>History captured: {sourceDate(baseline.syncedAtUtc)}.</p>
        <ol>{baseline.top50.map(row => <li key={row.chartId}><span>{row.chartLabel} · {row.songName}</span>
          <span>{row.score !== null ? <ResultScore score={row.score} /> : "Score unknown"} {row.plate ? <ResultPlate plate={row.plate} /> : null}
            · {pumbility(row.pumbility)}</span></li>)}</ol>
      </details>
    </> : null}
    {cohort?.limitedData ? <p className={styles.limited}>Fewer than ten usable peers are available.</p> : null}
  </section>;
}
export function ProRecommendations({playerKey, mode, onModeChange}: {
  playerKey: string; mode: ProModeView; onModeChange: (mode: ProModeView) => void;
}) {
  const [index, setIndex] = useState<ProPlayersResponse | null>(null);
  const [result, setResult] = useState<{key: string; data: ProRecommendationsResponse} | null>(null);
  const [error, setError] = useState(""); const [indexError, setIndexError] = useState("");
  const [loading, setLoading] = useState(false); const [attempt, setAttempt] = useState(0);
  const data = result?.key === playerKey ? result.data : null;
  useEffect(() => {
    const controller = new AbortController(); setIndexError("");
    fetch("/api/pro-recommendations/players", {cache: "no-store", signal: controller.signal})
      .then(r => readJsonResponse<unknown>(r)).then(v => {const next = validateProPlayersResponse(v); if (!controller.signal.aborted) setIndex(next);})
      .catch((e: unknown) => {if (!controller.signal.aborted) setIndexError(e instanceof Error ? e.message : "Pro player data unavailable.");});
    return () => controller.abort();
  }, [attempt]);
  useEffect(() => {
    setError(""); if (!playerKey) {setLoading(false); return;}
    const controller = new AbortController(); setLoading(true);
    fetch(`/api/pro-recommendations?${new URLSearchParams({playerKey})}`, {cache: "no-store", signal: controller.signal})
      .then(r => readJsonResponse<unknown>(r)).then(v => {const next = validateProRecommendationsResponse(v, undefined, playerKey); if (!controller.signal.aborted) setResult({key: playerKey, data: next});})
      .catch((e: unknown) => {if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Pro results unavailable.");})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [playerKey, attempt]);
  const keyDown = (event: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const next = event.key === "ArrowRight" ? (i+1)%3 : event.key === "ArrowLeft" ? (i+2)%3 : event.key === "Home" ? 0 : event.key === "End" ? 2 : null;
    if (next === null) return; event.preventDefault(); onModeChange(VIEWS[next]);
    (event.currentTarget.parentElement?.querySelectorAll("button")[next] as HTMLButtonElement | undefined)?.focus();
  };
  const selected = data && mode !== "overall" ? data.modes[mode] : null;
  const rows = data ? mode === "overall" ? data.overall.recommendations : data.modes[mode].recommendations : [];
  const ready = data && (mode === "overall" ? data.overall.availableModes.length > 0 && data.overall.baseline?.cutoffPumbility !== null : selected?.status === "ready");
  const summaries: ProModeKey[] = mode === "overall" ? ["singles", "doubles"] : [mode];
  return <div className={styles.workspace} aria-busy={loading}>
    <p className={styles.intro}>Official overall Top 500 · Phoenix 2 only · S23+ and D24+</p>
    <div className="recommendation-mode-tabs" role="tablist" aria-label="Pro recommendation mode">
      {VIEWS.map((view,i) => <button id={`pro-mode-${view}`} key={view} type="button" role="tab"
        aria-controls="pro-mode-panel" aria-selected={mode === view} tabIndex={mode === view ? 0 : -1}
        className={mode === view ? "active" : ""} onClick={() => onModeChange(view)} onKeyDown={e => keyDown(e,i)}>
        <b>{view[0].toUpperCase()}</b><span>{name(view)}</span></button>)}
    </div>
    {index ? <CapturedPlayerChooser index={index} currentKey={playerKey} mode={mode} /> : null}
    {!playerKey ? <PlayerRequired feature="Pro Farming" /> : null}
    {loading && !data ? <p role="status">Loading Pro Farming…</p> : null}
    {error || (!data && indexError) ? <div className={styles.notice} role="alert"><p>{error || indexError}</p>
      <button type="button" onClick={() => setAttempt(v => v+1)}>Retry</button></div> : null}
    <div id="pro-mode-panel" role="tabpanel" aria-labelledby={`pro-mode-${mode}`} className={styles.workspace}>
      {data ? <>
        {data.player.overallPumbility !== null ? <section className={styles.overall} aria-label="Official overall standing">
          <div><span className={styles.label}>Official overall Pumbility</span><strong>{pumbility(data.player.overallPumbility)}</strong></div>
          <div><span className={styles.label}>Official overall rank</span><strong>#{data.player.overallRank}</strong></div>
          <div className={styles.playerName}>{data.player.officialDisplayName || data.player.displayName}</div>
        </section> : null}
        {!data.eligible ? <section className={styles.notice} role="status"><h2>{data.status === "ineligible" ? "Official overall Top 500 required" : "Link your official player"}</h2>
          <p>{data.message}</p>{data.status === "identity-unresolved" ? <a href={SCORE_SYNC_URL} target="_blank" rel="noopener noreferrer">Import Phoenix 2 scores on PIUScores ↗</a> : null}</section> : <>
          <div className={styles.modeSummaries}>
            {mode === "overall" ? <ModeSummary result={{mode:"overall",baseline:data.overall.baseline,cohort:null,
              modePumbility:data.overall.baseline?.cutoffPumbility != null ? data.overall.baseline.top50Pumbility : null,
              status:ready ? "ready" : "insufficient-data",message:ready ? null : "At least 50 known chart results are required."}} /> : null}
            {summaries.map(m => <ModeSummary key={m} result={data.modes[m]} />)}
          </div>
          {mode === "overall" ? <p className={styles.baseline}>Targets come from each mode’s peers. Expected overall gains use the reconstructed combined Top 50.</p> : null}
          {ready ? <section aria-labelledby="pro-recommendations-title">
            <div className={styles.listHeading}><h2 id="pro-recommendations-title">{name(mode)} recommendations</h2><span>{rows.length} charts</span></div>
            {rows.length ? <ol className={styles.list}>{rows.map((row,i) => <ProChart key={row.chartId} chart={row} rank={i+1} overall={mode === "overall"} />)}</ol>
              : <p className={styles.notice}>No observed target currently improves this Top 50.</p>}
          </section> : <p className={styles.notice}>This mode needs at least 50 usable chart observations. Available modes remain usable.</p>}
          <details className={styles.method}><summary>About these targets</summary>
            <p>Targets use the official peer scores’ 50th percentile, truncated to the earned grade. Target plates: MG through 995,000; SG above 995,000; PG at 1,000,000.
              Mode ratings and Top 50 lists use actual Phoenix 2 results where available, with official score estimates filling missing results. Unknown scores are never zero-filled.</p>
            <p>Overall uses the same mode targets, then calculates each expected gain against the reconstructed combined Top 50. Existing Top 50 charts replace their own contribution; other charts replace the 50th contribution. Personal results take precedence, with official leaderboard estimates filling gaps.</p>
            <div className={styles.sourceDates}><span>Official snapshot: {sourceDate(data.snapshotAt)}</span><span>Model built: {sourceDate(data.generatedAtUtc)}</span></div>
          </details>
        </>}
      </> : null}
    </div>
  </div>;
}
