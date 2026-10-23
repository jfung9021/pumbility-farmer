"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChartVideoLink } from "../_components/chart-video-link";
import aboutStyles from "../about/about.module.css";
import { usePlayerProfile } from "../_components/player-profile-provider";
import { PlayerRequired } from "../_components/player-required";
import { PlayerResult } from "../_components/player-result";
import { RefreshMeta } from "../_components/refresh-meta";
import { GlobalRefreshNotice } from "../_components/global-refresh-notice";
import { SiteHeader } from "../_components/site-header";
import { readJsonResponse } from "../../lib/api-response";
import { MODEL_DELAY_THRESHOLD_MS, waitForPlayerRefresh } from "../../lib/refresh-reliability";
import { formatEstimatedDifficulty } from "../../lib/format-difficulty";
import { formatTierScore } from "../../lib/tier-filters";
import { RADAR_GROUPS, RADAR_MODES, RADAR_INITIAL_SUGGESTIONS, radarAxisLabelLayout, radarPoint, validateSkillRadarResponse, type RadarBucket, type RadarMode, type RadarSuggestion, type SkillRadarResponse } from "../../lib/skill-radar";
import type { PlayerRefreshResponse } from "../../lib/types";
import "./skill-radar.css";

const summaryId = (key: string) => `radar-summary-${encodeURIComponent(key)}`;

function RadarPlot({profile, selected, onSelect, onInspect}: {profile: SkillRadarResponse; selected: string; onSelect: (key: string) => void; onInspect: (key: string) => void}) {
  const [visible, setVisible] = useState<Record<RadarMode, boolean>>({singles:true, doubles:true});
  return <figure className="radar-figure">
    <h3 className="radar-chart-title">Skill by BPM</h3>
    <div className="radar-trace-controls" aria-label="Visible radar traces">
      {(["singles", "doubles"] as const).map(mode => <button aria-pressed={visible[mode]} className={`radar-trace-toggle radar-mode-${mode}`} key={mode} onClick={() => setVisible(current => ({...current, [mode]:!current[mode]}))} type="button"><span aria-hidden="true">{mode === "singles" ? "●" : "■"}</span>{RADAR_MODES[mode].label}</button>)}
    </div>
    <svg aria-labelledby="radar-plot-title radar-plot-description" className="radar-plot" role="group" viewBox="0 0 540 500">
      <title id="radar-plot-title">{profile.player.displayName} Skill Radar</title>
      <desc id="radar-plot-description">Singles use a solid red line and circles. Doubles use a dashed green line and squares. Select a rating to view its contributing charts.</desc>
      <g transform="translate(70 50)">
        {[20,40,60,80,100].map(score => <g key={score}><circle className="radar-ring" cx="200" cy="200" r={132 * score / 100} /><text className="radar-ring-label" x="205" y={196 - 132 * score / 100}>{score}</text></g>)}
        {profile.bpmBuckets.map((bucket, index) => {
          const point = radarPoint(index, 100), label = radarAxisLabelLayout(index);
          return <g key={bucket.id}>
            <line className="radar-axis" x1="200" y1="200" x2={point.x} y2={point.y} />
            <text className="radar-axis-label" x={label.x} y={label.labelY} dominantBaseline="middle" textAnchor={label.textAnchor}>{bucket.label}</text>
            {(["singles", "doubles"] as const).filter(mode => visible[mode]).map((mode, line) => {
              const value = profile.buckets.find(value => value.mode === mode && value.bpmBucketId === bucket.id)!;
              return <g aria-label={`Show contributing charts for ${RADAR_MODES[mode].label} ${bucket.label} BPM, ${value.radarScore.toFixed(1)}/100${value.needsPlays ? ", insufficient data" : ""}`} aria-haspopup="dialog" className={`radar-skill-trigger radar-mode-${mode}`} key={mode} role="button" tabIndex={0} onClick={event => {event.currentTarget.focus(); onInspect(value.key);}} onKeyDown={event => {if (event.key === "Enter" || event.key === " ") {event.preventDefault(); onInspect(value.key);}}}>
                <rect className="radar-skill-hit-area" x={label.x - (label.textAnchor === "end" ? 104 : label.textAnchor === "middle" ? 54 : 4)} y={label.scoreY + line * 18 - 9} width="108" height="18" rx="3" />
                <text className={`radar-axis-score radar-mode-${mode}`} dominantBaseline="middle" textAnchor={label.textAnchor} x={label.x} y={label.scoreY + line * 18}>{RADAR_MODES[mode].prefix} {value.radarScore.toFixed(1)}/100{value.needsPlays ? "*" : ""}</text>
              </g>;
            })}
          </g>;
        })}
        {(["singles", "doubles"] as const).map(mode => {
          if (!visible[mode]) return null;
          const points = profile.bpmBuckets.map(bin => profile.buckets.find(bucket => bucket.mode === mode && bucket.bpmBucketId === bin.id)!);
          return <g className={`radar-trace radar-mode-${mode}`} key={mode}>
            <polygon className="radar-trace-line" points={points.map((bucket, index) => {const point = radarPoint(index, bucket.radarScore); return `${point.x},${point.y}`;}).join(" ")} />
            {points.map((bucket, index) => {
              const point = radarPoint(index, bucket.radarScore);
              return <g className="radar-point" data-selected={selected === bucket.key || undefined} key={bucket.key} onClick={() => onSelect(bucket.key)}>
                <title>{RADAR_MODES[mode].label} · {bucket.label} BPM · {bucket.radarScore.toFixed(1)}/100 · UG {bucket.rawEquivalentLevel.toFixed(1)}</title>
                {mode === "singles" ? <circle cx={point.x} cy={point.y} r="4" /> : <rect x={point.x - 4} y={point.y - 4} height="8" width="8" />}
              </g>;
            })}
          </g>;
        })}
      </g>
    </svg>
    {profile.buckets.some(bucket => visible[bucket.mode] && bucket.needsPlays) ? <figcaption className="radar-data-legend">* Insufficient data</figcaption> : null}
  </figure>;
}

function ContributionDialog({bucket, onClose}: {bucket: RadarBucket; onClose: () => void}) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  useEffect(() => {
    const dialog = dialogRef.current!;
    const previous = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.showModal();
    return () => {
      dialog.close();
      document.body.style.overflow = previousOverflow;
      if (previous instanceof HTMLElement || previous instanceof SVGElement) previous.focus();
    };
  }, []);
  const mode = RADAR_MODES[bucket.mode];
  const total = bucket.contributingCharts.reduce((sum, chart) => sum + Math.round(chart.pumbility * 100), 0) / 100;
  return <dialog aria-labelledby="radar-contributor-title" className={`radar-contributor-dialog radar-mode-${bucket.mode}`} ref={dialogRef} onCancel={event => {event.preventDefault(); onClose();}} onClick={event => {if (event.target === event.currentTarget) onClose();}}>
    <div className="radar-contributor-content">
      <header className="radar-contributor-heading"><div><p>{mode.label} · {bucket.label} BPM</p><h2 id="radar-contributor-title">Contributing charts</h2></div><button aria-label="Close contributing charts" onClick={onClose} type="button">×</button></header>
      <p className="radar-contributor-summary"><strong>{bucket.radarScore.toFixed(1)}<small>/100</small></strong><span>{mode.prefix}{bucket.rawEquivalentLevel.toFixed(1)} UG</span><span>{bucket.selectedCharts}/5 charts</span></p>
      <div className="radar-contributor-columns"><span>Chart / score</span><span>Pumbility</span></div>
      <ol className="radar-contributors">{bucket.contributingCharts.map((chart, index) => {
        return <li className="radar-contribution" key={chart.chartId}>
          <span className="radar-contribution-rank">{index + 1}</span>
          <div className="radar-contribution-jacket chart-art" data-chart-type={chart.type} aria-hidden="true">{chart.imageUrl ? <img alt="" src={chart.imageUrl} /> : <span>{chart.difficulty}</span>}</div><div className="radar-contribution-copy"><strong>{chart.songName}</strong><span><b>{chart.difficulty}</b> · <PlayerResult compact plate={chart.plate} score={chart.score} /></span></div><b className="radar-contribution-pumbility">{chart.pumbility.toFixed(2)}</b>
        </li>;
      })}</ol>
      <p className="radar-contributor-total"><span>Average Pumbility</span><strong>{bucket.selectedCharts ? (total / bucket.selectedCharts).toFixed(2) : "—"}</strong></p>
      {bucket.needsPlays ? <p className="radar-contributor-note">{bucket.selectedCharts ? "Only available charts are averaged." : "No usable chart scores."}</p> : null}
    </div>
  </dialog>;
}

function SuggestionRow({chart}: {chart: RadarSuggestion}) {
  const prefix = chart.type === "Single" ? "S" : "D";
  const bpm = chart.bpmMin === chart.bpmMax ? `${chart.bpmMin}` : `${chart.bpmMin}–${chart.bpmMax}`;
  return <li className="radar-chart-row">
    <div className="radar-jacket chart-art" data-chart-type={chart.type} aria-hidden="true">{chart.imageUrl ? <img alt="" loading="lazy" src={chart.imageUrl} /> : <span>{chart.difficulty}</span>}</div>
    <div className="radar-chart-copy"><strong>{chart.songName}</strong><span><b>{chart.difficulty}</b> official · {prefix}{formatEstimatedDifficulty(chart.estimatedDifficulty)} calculated</span><details className="radar-song-details"><summary>Chart details</summary><span>{bpm} BPM</span></details></div>
    <PlayerResult className="radar-personal-score" compact formattedScore={chart.personalScore === null ? undefined : formatTierScore(chart.personalScore)} plate={chart.personalPlate} score={chart.personalScore} />
    <ChartVideoLink chartId={chart.chartId} difficulty={chart.difficulty} songName={chart.songName} variant="recommendation" />
  </li>;
}

function SuggestionLadder({bucket}: {bucket: RadarBucket}) {
  const [showAll, setShowAll] = useState(false);
  const mode = RADAR_MODES[bucket.mode];
  const visible = showAll ? bucket.suggestions : bucket.suggestions.slice(0, RADAR_INITIAL_SUGGESTIONS);
  const listId = `${summaryId(bucket.key)}-suggestions`;
  return <section aria-label={`${mode.label} ${bucket.label} BPM chart suggestions`} className="radar-ladder" id={`${summaryId(bucket.key)}-ladder`}>
    <h3>Next charts <span>{mode.prefix}{bucket.suggestedLevel}{bucket.suggestions.some(chart => chart.level > bucket.suggestedLevel) ? "+" : ""}</span></h3>
    {visible.length ? <ol id={listId}>{visible.map(chart => <SuggestionRow chart={chart} key={chart.chartId} />)}</ol> : <p>No qualifying charts.</p>}
    {bucket.suggestions.length > RADAR_INITIAL_SUGGESTIONS ? <div className="radar-ladder-actions">
      <span aria-live="polite">Showing {visible.length} of {bucket.suggestions.length}</span>
      <button aria-controls={listId} aria-expanded={showAll}
        aria-label={`${showAll ? "Show fewer" : `Show ${bucket.suggestions.length - RADAR_INITIAL_SUGGESTIONS} more`} ${mode.label} ${bucket.label} BPM chart suggestions`}
        onClick={() => setShowAll(value => !value)} type="button">{showAll ? "Show less" : "More"}</button>
    </div> : null}
  </section>;
}

function BucketSummary({bucket, expanded, onSelect}: {bucket: RadarBucket; expanded: boolean; onSelect: () => void}) {
  const mode = RADAR_MODES[bucket.mode];
  return <div className={`radar-bucket radar-mode-${bucket.mode}`}>
    <button aria-controls={`${summaryId(bucket.key)}-ladder`} aria-expanded={expanded} className="radar-bucket-summary" id={summaryId(bucket.key)} onClick={onSelect} type="button">
      <span className="radar-bucket-name"><b>{mode.label}</b><span>{bucket.label} BPM</span></span>
      <span className="radar-bucket-skill"><b>{bucket.radarScore.toFixed(1)}<small>/100</small></b><span>{mode.prefix}{bucket.rawEquivalentLevel.toFixed(1)} UG</span></span>
      {bucket.needsPlays ? <span className="radar-needs-plays">Needs plays · {bucket.selectedCharts}/5</span> : null}
      <span aria-hidden="true" className="radar-expand">{expanded ? "−" : "+"}</span>
    </button>
    {expanded ? <SuggestionLadder bucket={bucket} /> : null}
  </div>;
}

export default function SkillRadarPage() {
  const { playerKey, players } = usePlayerProfile();
  const activePlayerKeyRef = useRef(playerKey);
  activePlayerKeyRef.current = playerKey;
  const [profilePayload, setProfile] = useState<SkillRadarResponse | null>(null);
  const profile = profilePayload?.player.playerKey === playerKey ? profilePayload : null;
  const [selected, setSelected] = useState("");
  const [inspected, setInspected] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [now, setNow] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const refreshController = useRef<AbortController | null>(null);

  useEffect(() => {
    setInspected("");
    if (!playerKey) {setProfile(null); setError(null); setLoading(false); return;}
    const controller = new AbortController();
    const isCurrent = () => !controller.signal.aborted && activePlayerKeyRef.current === playerKey;
    setSelected(""); setLoading(true); setError(null); setProfile(null);
    fetch(`/api/skill-radar?${new URLSearchParams({playerKey})}`, {signal:controller.signal, cache:"no-store"})
      .then(response => readJsonResponse<unknown>(response)).then(value => validateSkillRadarResponse(value, playerKey))
      .then(value => {if (isCurrent()) {setProfile(value); setSelected(value.groups.weaknesses[0] ?? value.buckets[0]?.key ?? "");}})
      .catch(error => {if (isCurrent()) setError(error instanceof Error ? error.message : "Skill Radar could not be loaded.");})
      .finally(() => {if (isCurrent()) setLoading(false);});
    return () => controller.abort();
  }, [attempt, playerKey]);
  useEffect(() => {setNow(Date.now()); const timer = window.setInterval(() => setNow(Date.now()), 60_000); return () => window.clearInterval(timer);}, []);
  useEffect(() => {setRefreshing(false); setRefreshMessage(null); return () => refreshController.current?.abort();}, [playerKey]);

  const refreshScores = async () => {
    if (!playerKey || refreshing) return;
    const controller = new AbortController(); refreshController.current = controller;
    const isCurrent = () => !controller.signal.aborted && activePlayerKeyRef.current === playerKey;
    setRefreshing(true); setRefreshMessage(null);
    try {
      const started = await readJsonResponse<PlayerRefreshResponse>(await fetch(`/api/recommendations/refresh?${new URLSearchParams({playerKey, mode:"singles"})}`, {method:"POST", signal:controller.signal, cache:"no-store"}));
      if (!isCurrent()) return;
      if (started.outcome !== "fresh") {
        const waited = await waitForPlayerRefresh({
          initial: started.job,
          signal: controller.signal,
          onPending: () => setRefreshMessage("Your scores are still updating. Saved radar data remains available."),
          readJob: async jobId => readJsonResponse(await fetch(`/api/recommendations/refresh?${new URLSearchParams({jobId})}`, {signal:controller.signal, cache:"no-store"})),
        });
        if (waited.outcome === "pending") {
          setRefreshMessage("Your score update is still pending. Use Update scores later to check the result.");
          return;
        }
        if (!isCurrent()) return;
      }
      if (isCurrent()) {setRefreshMessage(null); setAttempt(value => value + 1);}
    } catch (error) {
      if (isCurrent()) setRefreshMessage(error instanceof Error ? error.message : "Scores could not be updated.");
    } finally {if (isCurrent()) setRefreshing(false);}
  };

  const buckets = useMemo(() => new Map(profile?.buckets.map(bucket => [bucket.key, bucket]) ?? []), [profile]);
  const selectBucket = (key: string) => setSelected(current => current === key ? "" : key);
  const closeContributions = useCallback(() => setInspected(""), []);
  const inspectedBucket = buckets.get(inspected);

  return <main className="skill-radar-page">
    <SiteHeader active="skill-radar" />
    <div className="radar-page-content">
      <header className="radar-page-heading"><h1>Skill Radar</h1><div className="refresh-meta-group"><RefreshMeta generatedAtUtc={profile?.playerSyncedAtUtc} label="Scores updated" loading={loading} loadingLabel="Loading Skill Radar..." nowMs={now} /><RefreshMeta delayedAfterMs={MODEL_DELAY_THRESHOLD_MS} generatedAtUtc={profile?.tierGeneratedAtUtc} label="Suggestions updated" loadingLabel="Loading suggestions..." nowMs={now} /></div><GlobalRefreshNotice surface="tier" nowMs={now} publishedAtUtc={profile?.tierGeneratedAtUtc} /></header>
      {playerKey && players?.refreshSupported && process.env.NEXT_PUBLIC_LOCAL_ANALYSIS !== "1" ? <button className="radar-refresh-button" disabled={refreshing || loading} onClick={() => void refreshScores()} type="button">{refreshing ? "Updating scores..." : "Update scores"}</button> : null}
      {refreshMessage ? <p className="radar-error" role="status">{refreshMessage}</p> : null}
      <div aria-busy={loading} className="radar-profile">
        {!playerKey ? <PlayerRequired feature="skill radar" /> : error ? <div className="radar-empty radar-error" role="alert"><p>{error}</p><button type="button" onClick={() => setAttempt(value => value + 1)}>Retry</button></div> : loading ? <p className="radar-empty" role="status">Loading Skill Radar...</p> : !profile ? <p className="radar-empty" role="status">Preparing Skill Radar...</p> : <div className="radar-workspace">
          <div className="radar-plot-panel"><h2>{profile.player.displayName}</h2><RadarPlot profile={profile} selected={selected} onInspect={setInspected} onSelect={key => {setSelected(key); document.getElementById(summaryId(key))?.focus();}} /></div>
          <div className="radar-groups">{RADAR_GROUPS.map(group => <section aria-labelledby={`radar-group-${group.key}`} className="radar-group" key={group.key}><h2 id={`radar-group-${group.key}`}>{group.label}</h2>{profile.groups[group.key].length ? profile.groups[group.key].map(key => {const bucket = buckets.get(key)!; return <BucketSummary bucket={bucket} expanded={selected === key} key={key} onSelect={() => selectBucket(key)} />;}) : <p className="radar-group-empty">No established strengths yet.</p>}</section>)}</div>
        </div>}
      </div>
      {inspectedBucket ? <ContributionDialog bucket={inspectedBucket} onClose={closeContributions} /> : null}
      <footer><Link className={aboutStyles.contextLink} href="/about#skill-radar">How Skill Radar works ↗</Link></footer>
    </div>
  </main>;
}
