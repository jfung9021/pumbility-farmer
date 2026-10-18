"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChartVideoLink } from "../_components/chart-video-link";
import { ExplanatorySection } from "../_components/explanatory-section";
import { PlayerPicker } from "../_components/player-picker";
import { RefreshMeta } from "../_components/refresh-meta";
import { ScoreSyncLink } from "../_components/score-sync-link";
import { SiteHeader } from "../_components/site-header";
import { readJsonResponse } from "../../lib/api-response";
import { formatEstimatedDifficulty } from "../../lib/format-difficulty";
import { formatTierScore } from "../../lib/tier-filters";
import { RADAR_GROUPS, RADAR_MODES, radarAxisLabelLayout, radarPlayerFromUrl, radarPlayerUrl, radarPoint, validateSkillRadarResponse, type RadarBucket, type RadarMode, type RadarSuggestion, type SkillRadarResponse } from "../../lib/skill-radar";
import type { PlayerRefreshJob, PlayerRefreshResponse, RecommendationPlayersResponse } from "../../lib/types";
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
              return <g aria-label={`Show contributing charts for ${RADAR_MODES[mode].label} ${bucket.label} BPM, ${value.radarScore.toFixed(1)}/100`} aria-haspopup="dialog" className={`radar-skill-trigger radar-mode-${mode}`} key={mode} role="button" tabIndex={0} onClick={event => {event.currentTarget.focus(); onInspect(value.key);}} onKeyDown={event => {if (event.key === "Enter" || event.key === " ") {event.preventDefault(); onInspect(value.key);}}}>
                <rect className="radar-skill-hit-area" x={label.x - (label.textAnchor === "end" ? 104 : label.textAnchor === "middle" ? 54 : 4)} y={label.scoreY + line * 18 - 9} width="108" height="18" rx="3" />
                <text className={`radar-axis-score radar-mode-${mode}`} dominantBaseline="middle" textAnchor={label.textAnchor} x={label.x} y={label.scoreY + line * 18}>{RADAR_MODES[mode].prefix} {value.radarScore.toFixed(1)}/100</text>
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
                <title>{RADAR_MODES[mode].label} · {bucket.label} BPM · {bucket.radarScore.toFixed(1)}/100 · PG {bucket.rawPg.toFixed(1)}</title>
                {mode === "singles" ? <circle cx={point.x} cy={point.y} r="4" /> : <rect x={point.x - 4} y={point.y - 4} height="8" width="8" />}
              </g>;
            })}
          </g>;
        })}
      </g>
    </svg>
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
      <p className="radar-contributor-summary"><strong>{bucket.radarScore.toFixed(1)}<small>/100</small></strong><span>{mode.prefix}{bucket.rawPg.toFixed(1)} PG</span><span>{bucket.selectedCharts}/5 charts</span></p>
      <div className="radar-contributor-columns"><span>Chart / score</span><span>Pumbility</span></div>
      <ol className="radar-contributors">{Array.from({length:5}, (_, index) => {
        const chart = bucket.contributingCharts[index];
        return <li className="radar-contribution" key={chart?.chartId ?? `empty-${index}`}>
          <span className="radar-contribution-rank">{index + 1}</span>
          {chart ? <><div className="radar-contribution-jacket chart-art" data-chart-type={chart.type} aria-hidden="true">{chart.imageUrl ? <img alt="" src={chart.imageUrl} /> : <span>{chart.difficulty}</span>}</div><div className="radar-contribution-copy"><strong>{chart.songName}</strong><span><b>{chart.difficulty}</b> · {chart.score === null ? "No recorded score" : chart.score.toLocaleString()}{chart.plate ? ` ${chart.plate}` : ""}</span></div><b className="radar-contribution-pumbility">{chart.pumbility.toFixed(2)}</b></> : <><span className="radar-contribution-missing">No chart</span><b className="radar-contribution-pumbility">0.00</b></>}
        </li>;
      })}</ol>
      <p className="radar-contributor-total"><span>Total Pumbility</span><strong>{total.toFixed(2)}</strong></p>
      {bucket.needsPlays ? <p className="radar-contributor-note">Missing slots count as zero.</p> : null}
    </div>
  </dialog>;
}

function SuggestionRow({chart}: {chart: RadarSuggestion}) {
  const prefix = chart.type === "Single" ? "S" : "D";
  const bpm = chart.bpmMin === chart.bpmMax ? `${chart.bpmMin}` : `${chart.bpmMin}–${chart.bpmMax}`;
  return <li className="radar-chart-row">
    <div className="radar-jacket chart-art" data-chart-type={chart.type} aria-hidden="true">{chart.imageUrl ? <img alt="" loading="lazy" src={chart.imageUrl} /> : <span>{chart.difficulty}</span>}</div>
    <div className="radar-chart-copy"><strong>{chart.songName}</strong><span><b>{chart.difficulty}</b> official · {prefix}{formatEstimatedDifficulty(chart.estimatedDifficulty)} calculated</span><details className="radar-song-details"><summary>Chart details</summary><span>{bpm} BPM</span></details></div>
    <div className="radar-personal-score" aria-label={chart.personalScore === null ? "No recorded score" : `${chart.personalScore.toLocaleString()} points${chart.personalPlate ? `, ${chart.personalPlate}` : ""}`} title={chart.personalScore?.toLocaleString()}><b>{chart.personalScore === null ? "—" : formatTierScore(chart.personalScore)}</b><span>{chart.personalPlate ?? ""}</span></div>
    <ChartVideoLink chartId={chart.chartId} difficulty={chart.difficulty} songName={chart.songName} variant="recommendation" />
  </li>;
}

function BucketSummary({bucket, expanded, onSelect}: {bucket: RadarBucket; expanded: boolean; onSelect: () => void}) {
  const mode = RADAR_MODES[bucket.mode];
  return <div className={`radar-bucket radar-mode-${bucket.mode}`}>
    <button aria-controls={`${summaryId(bucket.key)}-ladder`} aria-expanded={expanded} className="radar-bucket-summary" id={summaryId(bucket.key)} onClick={onSelect} type="button">
      <span className="radar-bucket-name"><b>{mode.label}</b><span>{bucket.label} BPM</span></span>
      <span className="radar-bucket-skill"><b>{bucket.radarScore.toFixed(1)}<small>/100</small></b><span>{mode.prefix}{bucket.rawPg.toFixed(1)} PG</span></span>
      {bucket.needsPlays ? <span className="radar-needs-plays">Needs plays · {bucket.selectedCharts}/5</span> : null}
      <span aria-hidden="true" className="radar-expand">{expanded ? "−" : "+"}</span>
    </button>
    {expanded ? <section aria-label={`${mode.label} ${bucket.label} BPM chart suggestions`} className="radar-ladder" id={`${summaryId(bucket.key)}-ladder`}>
      <h3>Next charts <span>{mode.prefix}{bucket.suggestedLevel}</span></h3>
      {bucket.suggestions.length ? <ol>{bucket.suggestions.map(chart => <SuggestionRow chart={chart} key={chart.chartId} />)}</ol> : <p>No qualifying charts.</p>}
    </section> : null}
  </div>;
}

export default function SkillRadarPage() {
  const [players, setPlayers] = useState<RecommendationPlayersResponse | null>(null);
  const [playerKey, setPlayerKey] = useState("");
  const [query, setQuery] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [profile, setProfile] = useState<SkillRadarResponse | null>(null);
  const [selected, setSelected] = useState("");
  const [inspected, setInspected] = useState("");
  const [loading, setLoading] = useState(false);
  const [playersLoading, setPlayersLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [playersError, setPlayersError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [now, setNow] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const refreshController = useRef<AbortController | null>(null);

  useEffect(() => {
    const apply = () => { const key = radarPlayerFromUrl(window.location.search); setPlayerKey(key); if (!key) setQuery(""); };
    apply(); window.addEventListener("popstate", apply);
    return () => window.removeEventListener("popstate", apply);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/skill-radar/players", {signal:controller.signal, cache:"no-store"})
      .then(response => readJsonResponse<RecommendationPlayersResponse>(response)).then(setPlayers)
      .catch(error => {if (!controller.signal.aborted) setPlayersError(error instanceof Error ? error.message : "Players could not be loaded.");})
      .finally(() => {if (!controller.signal.aborted) setPlayersLoading(false);});
    return () => controller.abort();
  }, []);
  useEffect(() => {
    setInspected("");
    if (!playerKey) {setProfile(null); setError(null); setLoading(false); return;}
    const controller = new AbortController();
    setLoading(true); setError(null); setProfile(null);
    fetch(`/api/skill-radar?${new URLSearchParams({playerKey})}`, {signal:controller.signal, cache:"no-store"})
      .then(response => readJsonResponse<unknown>(response)).then(value => validateSkillRadarResponse(value, playerKey))
      .then(value => {if (!controller.signal.aborted) {setProfile(value); setSelected(value.groups.weaknesses[0] ?? value.buckets[0]?.key ?? "");}})
      .catch(error => {if (!controller.signal.aborted) setError(error instanceof Error ? error.message : "Skill Radar could not be loaded.");})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [attempt, playerKey]);
  useEffect(() => {
    const player = players?.players.find(player => player.playerKey === playerKey);
    if (playerKey) setQuery(player?.displayName ?? profile?.player.displayName ?? "");
  }, [playerKey, players, profile]);
  useEffect(() => {setNow(Date.now()); const timer = window.setInterval(() => setNow(Date.now()), 60_000); return () => window.clearInterval(timer);}, []);
  useEffect(() => {setRefreshing(false); setRefreshMessage(null); return () => refreshController.current?.abort();}, [playerKey]);

  const refreshScores = async () => {
    if (!playerKey || refreshing) return;
    const controller = new AbortController(); refreshController.current = controller;
    setRefreshing(true); setRefreshMessage(null);
    try {
      const started = await readJsonResponse<PlayerRefreshResponse>(await fetch(`/api/recommendations/refresh?${new URLSearchParams({playerKey, mode:"singles"})}`, {method:"POST", signal:controller.signal, cache:"no-store"}));
      if (started.outcome !== "fresh") {
        let job = started.job;
        for (let poll = 0; job.status !== "completed" && job.status !== "failed" && poll < 180; poll++) {
          await new Promise<void>((resolve, reject) => {
            const done = () => {window.clearTimeout(timer); reject(new DOMException("Cancelled", "AbortError"));};
            const timer = window.setTimeout(() => {controller.signal.removeEventListener("abort", done); resolve();}, 1500);
            controller.signal.addEventListener("abort", done, {once:true});
          });
          job = await readJsonResponse<PlayerRefreshJob>(await fetch(`/api/recommendations/refresh?${new URLSearchParams({jobId:job.id})}`, {signal:controller.signal, cache:"no-store"}));
        }
        if (job.status !== "completed") throw new Error(job.error || "Score update is still pending. Try again shortly.");
      }
      if (!controller.signal.aborted) setAttempt(value => value + 1);
    } catch (error) {
      if (!controller.signal.aborted) setRefreshMessage(error instanceof Error ? error.message : "Scores could not be updated.");
    } finally {if (!controller.signal.aborted) setRefreshing(false);}
  };

  const selectPlayer = useCallback((key: string, displayName = "") => {
    setPlayerKey(key); setQuery(displayName); setProfile(null); setSelected("");
    const url = radarPlayerUrl(window.location.href, key);
    if (url.href !== window.location.href) window.history.pushState({}, "", url);
  }, []);
  const buckets = useMemo(() => new Map(profile?.buckets.map(bucket => [bucket.key, bucket]) ?? []), [profile]);
  const selectBucket = (key: string) => setSelected(current => current === key ? "" : key);
  const closeContributions = useCallback(() => setInspected(""), []);
  const inspectedBucket = buckets.get(inspected);

  return <main className="skill-radar-page">
    <SiteHeader active="skill-radar" />
    <div className="radar-page-content">
      <header className="radar-page-heading"><h1>Skill Radar</h1><RefreshMeta generatedAtUtc={profile?.playerSyncedAtUtc ?? profile?.generatedAtUtc} label="Scores updated" loading={loading} loadingLabel="Loading Skill Radar..." nowMs={now} /></header>
      <section className="player-picker radar-player-picker"><label htmlFor="radar-player-select">Phoenix 2 username</label><PlayerPicker disabled={playersLoading || !players?.players.length} id="radar-player-select" players={players?.players ?? []} selectedKey={playerKey} query={query} open={menuOpen} onOpenChange={setMenuOpen} onQueryChange={value => {selectPlayer("", value); setMenuOpen(true);}} onSelect={selectPlayer} /><ScoreSyncLink className="radar-score-sync">Sync scores</ScoreSyncLink>{playerKey && players?.refreshSupported && process.env.NEXT_PUBLIC_LOCAL_ANALYSIS !== "1" ? <button className="radar-refresh-button" disabled={refreshing || loading} onClick={() => void refreshScores()} type="button">{refreshing ? "Updating scores..." : "Update scores"}</button> : null}</section>
      {playersError ? <p className="radar-error" role="alert">{playersError}</p> : null}
      {refreshMessage ? <p className="radar-error" role="status">{refreshMessage}</p> : null}
      <div aria-busy={loading} className="radar-profile">
        {error ? <div className="radar-empty radar-error" role="alert"><p>{error}</p><button type="button" onClick={() => setAttempt(value => value + 1)}>Retry</button></div> : loading ? <p className="radar-empty" role="status">Loading Skill Radar...</p> : !profile ? <p className="radar-empty">Select a player.</p> : <div className="radar-workspace">
          <div className="radar-plot-panel"><h2>{profile.player.displayName}</h2><RadarPlot profile={profile} selected={selected} onInspect={setInspected} onSelect={key => {setSelected(key); document.getElementById(summaryId(key))?.focus();}} /></div>
          <div className="radar-groups">{RADAR_GROUPS.map(group => <section aria-labelledby={`radar-group-${group.key}`} className="radar-group" key={group.key}><h2 id={`radar-group-${group.key}`}>{group.label}</h2>{profile.groups[group.key].length ? profile.groups[group.key].map(key => {const bucket = buckets.get(key)!; return <BucketSummary bucket={bucket} expanded={selected === key} key={key} onSelect={() => selectBucket(key)} />;}) : <p className="radar-group-empty">No established strengths yet.</p>}</section>)}</div>
        </div>}
      </div>
      {inspectedBucket ? <ContributionDialog bucket={inspectedBucket} onClose={closeContributions} /> : null}
      <footer><ExplanatorySection title="About Skill Radar"><p>Each BPM range uses your five highest Phoenix 2 chart Pumbility values. Missing slots count as zero. PG is the equivalent difficulty for SSS+ with a Perfect Game plate.</p><p>Doubles are drawn one level lower: D20.5 aligns with S19.5. The displayed scale maps S10/D11 to 0 and S25/D26 to 100; the drawing stops at those bounds. Your actual PG remains unchanged.</p><p>Strengths and weaknesses compare the adjusted PG values across both modes. Hiding a line changes only the drawing. Empty and incomplete ranges retain their zero-padded results.</p><p>Suggestions use the official chart level from your actual PG +1.5, rounded down, and show up to 10 charts sorted by SSS+ Pumbility difficulty. Existing scores remain eligible. Rated suggestions currently begin at official level 16; fewer than five may qualify. Songs whose BPM spans multiple ranges are excluded.</p></ExplanatorySection></footer>
    </div>
  </main>;
}
