"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { SiteHeader } from "../_components/site-header";
import { PlayerChooserDialog } from "../_components/player-chooser-dialog";
import { SkillRadar, SkillRadarLegend } from "../_components/skill-radar";
import aboutStyles from "../about/about.module.css";
import { readJsonResponse } from "../../lib/api-response";
import { loadWithGenerationRecovery } from "../../lib/refresh-reliability";
import { scoreColor } from "../../lib/score-appearance";
import {
  OFFICIAL_BPM_LABELS,
  MINIMUM_OFFICIAL_SCORES,
  validateOfficialComparisonIndex,
  validateOfficialComparisonPlayer,
  type OfficialComparisonBucket,
  type OfficialComparisonChart,
  type OfficialComparisonIndex,
  type OfficialComparisonPlayer,
} from "../../lib/official-comparison";
import {
  comparisonFolderLevels,
  comparisonRows,
  comparisonSearchParams,
  comparisonSummary,
  comparisonViewFromSearchParams,
  compareEstimatedBuckets,
  formatComparisonDelta,
  formatComparisonScore,
  formatEstimatedPgGap,
  scaleEstimatedPgComparison,
  type ComparisonMode,
  type ComparisonView,
} from "../../lib/official-comparison-display";
import styles from "./comparison.module.css";

type Side = "left" | "right";
type PlayerResult =
  | { status: "idle" | "loading"; data?: undefined; error?: undefined }
  | { status: "ready"; data: OfficialComparisonPlayer; error?: undefined }
  | { status: "error"; data?: undefined; error: string };

function useOfficialPlayer(playerKey: string, index: OfficialComparisonIndex | null, recoverIndex: (generation: string) => Promise<OfficialComparisonIndex>) {
  const [attempt, setAttempt] = useState(0);
  const recoveredPlayer = useRef<string | null>(null);
  const generation = index?.generation;
  const requestId = JSON.stringify([playerKey, generation, attempt]);
  const [loaded, setLoaded] = useState<{ requestId: string; result: PlayerResult }>({ requestId: "", result: { status: "idle" } });

  useEffect(() => {
    if (!playerKey || !index || !generation) return;
    const controller = new AbortController();
    setLoaded({ requestId, result: { status: "loading" } });
    loadWithGenerationRecovery(index, async catalog => {
      if (!catalog.players.some(player => player.playerKey === playerKey)) throw new Error("This player is no longer in the latest official player list.");
      const params = new URLSearchParams({ playerKey, generation: catalog.generation, bpmPolicy:"focal-v1" });
      const data = await readJsonResponse<unknown>(await fetch(`/api/official-comparison?${params}`, { signal: controller.signal, cache: "no-store" }));
      return validateOfficialComparisonPlayer(data, catalog, playerKey);
    }, async () => {
      if (recoveredPlayer.current === playerKey) throw new Error("The official data changed again. Reload the page to continue.");
      recoveredPlayer.current = playerKey;
      return recoverIndex(generation);
    })
      .then(({ value: profile }) => {
        if (!controller.signal.aborted) setLoaded({ requestId, result: { status: "ready", data: profile } });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setLoaded({ requestId, result: { status: "error", error: error instanceof Error ? error.message : "Unable to load this player." } });
        }
      });
    return () => controller.abort();
  }, [playerKey, index, generation, requestId, recoverIndex]);

  const result: PlayerResult = !playerKey
    ? { status: "idle" }
    : loaded.requestId === requestId ? loaded.result : { status: "loading" };
  return { ...result, retry: () => setAttempt((value) => value + 1) };
}

function OfficialPlayerChoice({ side, playerKey, displayName, onChoose }: {
  side: Side;
  playerKey: string;
  displayName: string;
  onChoose: () => void;
}) {
  return (
    <div className={styles.picker}>
      <span className={`${styles.playerLabel} ${styles.playerIdentity}`} data-side={side}>Player {side === "left" ? "1" : "2"}</span>
      <div className={styles.playerChoice}>
        <strong title={displayName}>{playerKey ? displayName : "Choose a player"}</strong>
        <button aria-haspopup="dialog" aria-label={`${playerKey ? "Change" : "Choose"} Player ${side === "left" ? "1" : "2"}`} onClick={onChoose} type="button">
          {playerKey ? "Change" : "Choose"}
        </button>
      </div>
    </div>
  );
}

function BucketValue({ bucket, mode, status, higher }: { bucket: OfficialComparisonBucket | undefined; mode: ComparisonMode; status: PlayerResult["status"]; higher: boolean }) {
  return (
    <div className={`${styles.bucketValue} ${higher ? styles.higherBucket : ""}`}>
      <span className={mode === "singles" ? styles.singlesLabel : styles.doublesLabel}>{mode === "singles" ? "● Singles" : "■ Doubles"}</span>
      {status === "ready" && bucket?.pg !== null && bucket?.pg !== undefined ? (
        <span><b>{bucket.pg.toFixed(2)}</b><small>{higher ? "▲ Higher · " : ""}{bucket.contributingCount}/5 recorded</small></span>
      ) : (
        <span className={styles.noData}>{status === "loading" ? "Loading…" : status === "error" ? "Unavailable" : status === "idle" ? "Choose player" : "No official data"}</span>
      )}
    </div>
  );
}

function BpmOutcome({ left, right, mode, range, leftStatus, rightStatus }: {
  left: OfficialComparisonBucket | undefined;
  right: OfficialComparisonBucket | undefined;
  mode: ComparisonMode;
  range: string;
  leftStatus: PlayerResult["status"];
  rightStatus: PlayerResult["status"];
}) {
  const outcome = scaleEstimatedPgComparison(left, right);
  let label: string;
  if (leftStatus === "loading" || rightStatus === "loading") label = "Loading…";
  else if (leftStatus === "error" || rightStatus === "error") label = "Unavailable";
  else if (leftStatus === "idle" || rightStatus === "idle") label = "Choose players";
  else if (outcome.winner === null) label = "No comparison";
  else label = outcome.winner === "tie" ? "Tied" : `P${outcome.winner === "left" ? 1 : 2} higher`;
  const comparable = leftStatus === "ready" && rightStatus === "ready" && outcome.winner !== null;
  const limited = comparable && ((left?.contributingCount ?? 0) < 5 || (right?.contributingCount ?? 0) < 5);
  return (
    <span className={styles.bpmOutcome} data-winner={comparable ? outcome.winner : "none"}>
      <span className={styles.srOnly}>{range} BPM, {mode === "singles" ? "Singles: " : "Doubles: "}{label}. {comparable && outcome.capped ? "Bar capped at 1 PG. " : ""}</span>
      {comparable ? (
        <>
          <span className={styles.leaderStrip} aria-hidden="true" title={outcome.capped ? "Bar capped at 1 PG; the actual difference is shown below." : undefined}>
            {(["left", "right"] as const).map((side) => (
              <span className={styles.leaderHalf} data-side={side} key={side}>
                {outcome.winner === side ? <span className={styles.leaderBar} style={{ width: `${(outcome.fraction ?? 0) * 100}%` }} /> : null}
              </span>
            ))}
            <span className={styles.leaderCenter} />
            {outcome.winner === "tie" ? <span className={styles.leaderTie}>=</span> : null}
          </span>
          <small className={styles.gapLabel}>
            <span className={styles.gapValue}>
              {outcome.winner === "left" ? <span className={styles.directionCue} aria-hidden="true">◀</span> : null}
              {outcome.winner === "tie" ? "Tied" : formatEstimatedPgGap(outcome.gap!)}
              {outcome.winner === "right" ? <span className={styles.directionCue} aria-hidden="true">▶</span> : null}
            </span>
            {limited ? <span>Limited</span> : null}
          </small>
        </>
      ) : <span className={styles.bpmUnavailable} aria-hidden="true">{label === "No comparison" ? "--" : label}</span>}
    </span>
  );
}

function ContributingCharts({ bucket, charts, name, side, status }: {
  bucket: OfficialComparisonBucket | undefined;
  charts: Map<string, OfficialComparisonChart>;
  name: string;
  side: Side;
  status: PlayerResult["status"];
}) {
  const contributors = bucket?.contributors ?? [];
  const count = contributors.length;
  return (
    <div className={styles.contributorPanel}>
      <h4><span className={styles.playerIdentity} data-side={side}>P{side === "left" ? 1 : 2}</span><b title={name}>{name}</b></h4>
      {status === "ready" && count ? (
        <>
          <p className={styles.contributorCoverage}>{count}/5 recorded</p>
          <p className={styles.contributorFields}>Score / Est. Pumbility</p>
          <ol className={styles.contributorList}>
            {contributors.map((contributor) => {
              const chart = charts.get(contributor.chartId);
              return (
                <li key={contributor.chartId}>
                  <div className={styles.contributorTitle}><span className={bucket?.mode === "singles" ? styles.singleBadge : styles.doubleBadge}>{chart?.chartLabel}</span><span>{chart?.songName ?? contributor.chartId}</span></div>
                  <p><b style={{ color: scoreColor(contributor.score) }}><span className={styles.srOnly}>Score: </span>{formatComparisonScore(contributor.score)}</b></p>
                  <small><span className={styles.srOnly}>Estimated Pumbility: </span>{(contributor.pumbilityCents / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</small>
                </li>
              );
            })}
          </ol>
          {count < 5 ? <p className={styles.contributorEmpty}>{5 - count} unrecorded {5 - count === 1 ? "slot counts" : "slots count"} as zero.</p> : null}
        </>
      ) : <p className={styles.contributorEmpty}>{status === "loading" ? "Loading charts…" : status === "error" ? "Official charts unavailable." : status === "idle" ? "Choose a player to see their contributing charts." : "No official data in this BPM range."}</p>}
    </div>
  );
}

function ScoreValue({ score, status, winner }: { score: number | null; status: PlayerResult["status"]; winner: boolean }) {
  if (status === "loading") return <span className={styles.missing} aria-label="Loading player scores">…</span>;
  if (status === "error") return <span className={styles.scoreError}>Unavailable</span>;
  return (
    <span className={score === null ? styles.missing : winner ? styles.winningScore : styles.score} style={score === null ? undefined : { color: scoreColor(score) }}>
      {winner ? <span className={styles.winnerMark} aria-label="Higher recorded score">▲</span> : null}
      {formatComparisonScore(score)}
    </span>
  );
}

function sourceDate(value: string | null | undefined) {
  if (!value) return "Unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

export default function OfficialLeaderboardComparisonPage() {
  const [view, setView] = useState<ComparisonView>(() => comparisonViewFromSearchParams(new URLSearchParams()));
  const [index, setIndex] = useState<OfficialComparisonIndex | null>(null);
  const [indexError, setIndexError] = useState("");
  const [indexAttempt, setIndexAttempt] = useState(0);
  const [selectionNotice, setSelectionNotice] = useState<string | null>(null);
  const recoveryRequests = useRef(new Map<string, Promise<OfficialComparisonIndex>>());
  const [openSide, setOpenSide] = useState<Side | null>(null);
  const { bpm: bucketIndex, contributorMode, q: query } = view;

  useEffect(() => {
    const restore = () => { setView(comparisonViewFromSearchParams(new URLSearchParams(window.location.search))); setOpenSide(null); };
    restore();
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setIndexError("");
    fetch("/api/official-comparison/players?bpmPolicy=focal-v1", { signal: controller.signal, cache: "no-store" })
      .then((response) => readJsonResponse<unknown>(response))
      .then((data) => { const catalog = validateOfficialComparisonIndex(data, "focal-v1"); if (!controller.signal.aborted) setIndex(catalog); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setIndexError(error instanceof Error ? error.message : "Unable to load official data."); });
    return () => controller.abort();
  }, [indexAttempt]);

  const recoverIndex = useCallback((generation: string) => {
    const existing = recoveryRequests.current.get(generation);
    if (existing) return existing;
    const request = fetch("/api/official-comparison/players?bpmPolicy=focal-v1", { cache: "no-store" })
      .then(response => readJsonResponse<unknown>(response))
      .then(value => { const catalog = validateOfficialComparisonIndex(value, "focal-v1"); setIndex(catalog); return catalog; });
    recoveryRequests.current.set(generation, request);
    return request;
  }, []);

  useEffect(() => {
    if (!index) return;
    const next = { ...view };
    for (const side of ["left", "right"] as const) {
      if (next[side] && !index.players.some(player => player.playerKey === next[side])) next[side] = "";
    }
    if (next.left !== view.left || next.right !== view.right) {
      setSelectionNotice("A selected player is no longer in the latest official player list. Choose another player.");
      setView(next);
      const params = comparisonSearchParams(next, new URLSearchParams(window.location.search));
      window.history.replaceState(null, "", `${window.location.pathname}?${params}${window.location.hash}`);
    }
  }, [index, view]);

  const left = useOfficialPlayer(view.left, index, recoverIndex);
  const right = useOfficialPlayer(view.right, index, recoverIndex);
  const profiles = { left, right };
  const names = {
    left: left.data?.displayName || index?.players.find((player) => player.playerKey === view.left)?.displayName || (view.left ? "Unknown player" : "Player 1"),
    right: right.data?.displayName || index?.players.find((player) => player.playerKey === view.right)?.displayName || (view.right ? "Unknown player" : "Player 2"),
  };
  const levels = useMemo(() => comparisonFolderLevels(index?.charts ?? [], view.mode), [index, view.mode]);
  const rows = useMemo(() => comparisonRows(index?.charts ?? [], view.mode, view.level, left.data?.scores, right.data?.scores, query), [index, view.mode, view.level, left.data, right.data, query]);
  const summary = useMemo(() => comparisonSummary(rows), [rows]);
  const chartById = useMemo(() => new Map((index?.charts ?? []).map((chart) => [chart.chartId, chart])), [index]);

  function bucketFor(side: Side, mode: ComparisonMode, selectedIndex = bucketIndex) {
    return profiles[side].data?.buckets.find((bucket) => bucket.mode === mode && bucket.bucketIndex === selectedIndex);
  }

  function updateView(changes: Partial<ComparisonView>, history: "push" | "replace" = "push") {
    if (Object.entries(changes).every(([key, value]) => view[key as keyof ComparisonView] === value)) return;
    const next = { ...view, ...changes };
    setView(next);
    const params = comparisonSearchParams(next, new URLSearchParams(window.location.search));
    window.history[history === "replace" ? "replaceState" : "pushState"](null, "", `${window.location.pathname}?${params}${window.location.hash}`);
  }

  function changeMode(mode: ComparisonMode) {
    const available = comparisonFolderLevels(index?.charts ?? [], mode);
    updateView({ mode, level: available.includes(view.level) ? view.level : available[0] ?? 20 });
  }

  return (
    <main className={styles.page}>
      <SiteHeader active="official-comparison" />
      <div className={styles.workspace}>
        <header className={styles.intro}>
          <h1>Official Leaderboard<br className={styles.titleBreak} /> Player Comparison</h1>
        </header>
        <nav className={styles.sectionNav} aria-label="Comparison sections">
          <a href="#bpm-analysis">BPM analysis</a>
          <a href="#chart-scores">Chart scores</a>
        </nav>

        {indexError ? (
          <div className={styles.errorBanner} role="alert"><span>{indexError}</span><button type="button" onClick={() => setIndexAttempt((value) => value + 1)}>Reload official data</button></div>
        ) : !index ? <p className={styles.loading} role="status">Loading official player list…</p> : null}
        {selectionNotice ? <p role="status">{selectionNotice}</p> : null}

        <section className={styles.comparison} aria-label="Side by side player skill comparison">
          <div className={styles.sectionTop}>
            <h2>Estimated skill</h2>
            <button className={styles.swap} type="button" disabled={!view.left && !view.right} onClick={() => { updateView({ left: view.right, right: view.left }); setOpenSide(null); }} aria-label="Swap Player 1 and Player 2"><span aria-hidden="true">⇄</span> Swap</button>
          </div>
          <div className={styles.playerGrid}>
            {(["left", "right"] as const).map((side) => {
              const profile = profiles[side];
              return (
                <div className={styles.playerPanel} key={side} aria-busy={profile.status === "loading"}>
                  <OfficialPlayerChoice
                    side={side}
                    playerKey={view[side]}
                    displayName={view[side] ? names[side] : ""}
                    onChoose={() => setOpenSide(side)}
                  />
                  <SkillRadar buckets={profile.data?.buckets ?? null} label={names[side]} selectedBucket={bucketIndex} onBucketSelect={(bpm) => updateView({ bpm })} />
                  <div className={styles.profileStatus} aria-live="polite">
                    {profile.status === "ready" ? <span><b>{profile.data.recordedScoreCount.toLocaleString("en-US")}</b> / {index?.charts.length.toLocaleString("en-US")} scores recorded</span>
                      : profile.status === "error" ? <div className={styles.playerError}><span>{profile.error}</span><button type="button" onClick={profile.retry}>Retry</button></div>
                        : profile.status === "loading" ? <span>Loading official scores…</span> : <span>Select an official player above</span>}
                  </div>
                </div>
              );
            })}
          </div>
          <SkillRadarLegend />
          <p className={styles.estimateNote}>SG assumed; perfect scores use Perfect Game.</p>
          <section className={styles.bpmOverview} id="bpm-analysis" tabIndex={-1} aria-labelledby="bpm-overview-heading">
            <div className={styles.bpmHeading}><h3 id="bpm-overview-heading">BPM comparison</h3><span>Higher estimated PG level</span></div>
            <div className={styles.playerKey}>
              {(["left", "right"] as const).map((side) => <span key={side}><b className={styles.playerIdentity} data-side={side}>{side === "left" ? "◀ P1" : "P2 ▶"}</b><span title={names[side]}>{names[side]}</span></span>)}
            </div>
            <p className={styles.scaleKey}>Scale: ±1 PG · Larger gaps capped</p>
            <div className={styles.bpmTableHeader} aria-hidden="true"><span>BPM</span><span className={styles.singlesLabel}>● Singles</span><span className={styles.doublesLabel}>■ Doubles</span></div>
            <div className={styles.bpmRows}>
              {OFFICIAL_BPM_LABELS.map((label, selectedIndex) => (
                <div className={styles.bpmRow} key={label} data-selected={bucketIndex === selectedIndex}>
                  <span className={styles.bpmRange}>{label}</span>
                  {(["singles", "doubles"] as const).map((mode) => (
                    <button className={styles.bpmChoice} key={mode} type="button" aria-pressed={bucketIndex === selectedIndex && contributorMode === mode} onClick={() => updateView({ bpm: selectedIndex, contributorMode: mode })}>
                      <BpmOutcome mode={mode} range={label} left={bucketFor("left", mode, selectedIndex)} right={bucketFor("right", mode, selectedIndex)} leftStatus={left.status} rightStatus={right.status} />
                    </button>
                  ))}
                </div>
              ))}
            </div>
            <p className={styles.bpmHint}>Select a result to inspect its charts. Limited = fewer than 5 contributing charts for either player.</p>
          </section>
          <div className={styles.bucketInspector}>
            <div className={styles.bucketHeading}><h3>{OFFICIAL_BPM_LABELS[bucketIndex]} BPM</h3><p>Estimated PG level</p></div>
            <div className={styles.bucketGrid} aria-live="polite">
              {(["left", "right"] as const).map((side) => (
                <div className={styles.bucketPlayer} key={side} aria-label={`${names[side]} at ${OFFICIAL_BPM_LABELS[bucketIndex]} BPM`}>
                  <h4 className={styles.detailPlayer}><span className={styles.playerIdentity} data-side={side}>P{side === "left" ? 1 : 2}</span><b title={names[side]}>{names[side]}</b></h4>
                  {(["singles", "doubles"] as const).map((mode) => <BucketValue key={mode} mode={mode} status={profiles[side].status} bucket={bucketFor(side, mode)} higher={compareEstimatedBuckets(bucketFor("left", mode), bucketFor("right", mode)).winner === side} />)}
                </div>
              ))}
            </div>
            <section className={styles.contributors} aria-labelledby="bpm-contributors-heading">
              <div className={styles.contributorHeading}>
                <h3 id="bpm-contributors-heading">Top 5 contributing charts</h3>
                <div className={styles.modeControl}>
                  <span id="contributor-mode-label">Contributor mode</span>
                  <div className={styles.modeToggle} role="group" aria-labelledby="contributor-mode-label">
                    {(["singles", "doubles"] as const).map((mode) => <button key={mode} type="button" aria-pressed={contributorMode === mode} onClick={() => updateView({ contributorMode: mode })}>{mode === "singles" ? "Singles" : "Doubles"}</button>)}
                  </div>
                </div>
              </div>
              <div className={styles.contributorGrid} aria-live="polite">
                {(["left", "right"] as const).map((side) => <ContributingCharts key={side} side={side} name={names[side]} status={profiles[side].status} bucket={bucketFor(side, contributorMode)} charts={chartById} />)}
              </div>
            </section>
          </div>
        </section>

        <section className={styles.scoreSection} id="chart-scores" tabIndex={-1} aria-labelledby="official-chart-scores">
          <div className={styles.scoreHeading}><h2 id="official-chart-scores">Chart scores</h2><span>{rows.length.toLocaleString("en-US")} charts · A–Z</span></div>
          <div className={styles.filters}>
            <label>Chart type<select value={view.mode} onChange={(event) => changeMode(event.target.value as ComparisonMode)}><option value="singles">Singles</option><option value="doubles">Doubles</option></select></label>
            <label>Difficulty<select value={view.level} onChange={(event) => updateView({ level: Number(event.target.value) })}>{!levels.includes(view.level) ? <option value={view.level}>{view.level}</option> : null}{levels.map((level) => <option key={level} value={level}>{level}</option>)}</select></label>
            <label className={styles.chartSearch}>Find chart<input type="search" placeholder="Search chart titles" value={query} onChange={(event) => updateView({ q: event.target.value }, "replace")} /></label>
          </div>
          {left.status === "ready" && right.status === "ready" ? (
            <p className={styles.matchSummary} aria-live="polite"><span><b>{summary.leftWins}</b> P1 leads</span><span><b>{summary.ties}</b> tied</span><span><b>{summary.rightWins}</b> P2 leads</span><small>{summary.comparable} charts with both scores</small></p>
          ) : <p className={styles.tableHint}>All charts stay visible. Missing official scores appear as --.</p>}
          <div className={styles.tableWrap}>
            <table className={styles.scoreTable}>
              <caption className={styles.srOnly}>{view.mode === "singles" ? "Singles" : "Doubles"} {view.level} chart scores in alphabetical order. Differences are Player 1 minus Player 2, only where both have a score.</caption>
              <thead>
                <tr>
                  <th className={styles.chartColumn} scope="col">Chart</th>
                  {(["left", "right"] as const).map((side) => <th className={styles.playerColumn} scope="col" key={side}><span className={`${styles.columnPlayer} ${styles.playerIdentity}`} data-side={side}>P{side === "left" ? 1 : 2} <b title={names[side]}>{names[side]}</b></span><small>{profiles[side].status === "ready" ? `${side === "left" ? summary.leftRecorded : summary.rightRecorded}/${summary.charts} recorded` : profiles[side].status === "error" ? "Scores unavailable" : profiles[side].status === "loading" ? "Loading scores…" : "Choose player"}</small></th>)}
                  <th className={styles.differenceColumn} scope="col">Difference<small>P1 − P2</small></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.chart.chartId}>
                    <th className={styles.chartName} scope="row"><span className={view.mode === "singles" ? styles.singleBadge : styles.doubleBadge}>{row.chart.chartLabel}</span><span>{row.chart.songName}</span></th>
                    <td className={styles.scoreCell}><span className={styles.srOnly}>Player 1: </span><ScoreValue score={row.leftScore} status={left.status} winner={row.winner === "left"} /></td>
                    <td className={styles.scoreCell}><span className={styles.srOnly}>Player 2: </span><ScoreValue score={row.rightScore} status={right.status} winner={row.winner === "right"} /></td>
                    <td className={styles.deltaCell}><span className={styles.mobileDifference}>P1 − P2 </span><span className={row.delta === null ? styles.missing : styles.delta}>{formatComparisonDelta(row.delta)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!rows.length ? <p className={styles.emptyRows}>{!index && !indexError ? "Loading chart catalog…" : indexError ? "Chart catalog unavailable." : query ? "No charts match this search." : "No charts in this folder."}</p> : null}
          </div>
        </section>

        <section className={styles.about} aria-label="Comparison information">
          <Link className={aboutStyles.contextLink} href="/about#player-comparison">How Player Comparison works ↗</Link>
          <p className={styles.sourceDates}>Phoenix 1: <b>{sourceDate(index?.sourceDates.Phoenix)}</b> <span>·</span> Phoenix 2: <b>{sourceDate(index?.sourceDates.Phoenix2)}</b></p>
        </section>
      </div>
      <PlayerChooserDialog
        open={openSide !== null}
        title={`Choose official Player ${openSide === "right" ? "2" : "1"}`}
        players={index?.players ?? []}
        selectedKey={openSide ? view[openSide] : ""}
        loading={!index && !indexError}
        error={indexError || undefined}
        onClose={() => setOpenSide(null)}
        onSelect={(key) => {
          if (openSide) updateView({ [openSide]: key });
          setOpenSide(null);
        }}
      />
    </main>
  );
}
