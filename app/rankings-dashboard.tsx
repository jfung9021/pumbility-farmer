"use client";

import { useEffect, useMemo, useState } from "react";

type ModeKey = "singles" | "doubles";

type ChartResult = {
  mode: "Singles" | "Doubles";
  modeRank: number | null;
  levelRank: number | null;
  folder: string;
  relativeGroupRank: number | null;
  relativeGroup: string | null;
  effectBandRank: number | null;
  effectBand: string | null;
  songName: string;
  difficulty: string;
  level: number;
  chartId: string;
  imageUrl: string | null;
  noteCount: number | null;
  stepArtist: string | null;
  estimatedDifficulty: number | null;
  averageDifficulty: number;
  difficultyDelta: number | null;
  difficultyCi95Low: number | null;
  difficultyCi95High: number | null;
  nContributors: number;
  nPlayersScored: number;
  evidenceStatus: "Published" | "Provisional" | "Insufficient" | "Unrated";
};

type ModeSummary = {
  eligiblePlayers: number;
  catalogCharts: number;
  measuredCharts: number;
  publishedCharts: number;
  pumbilityPerLevel: number | null;
};

type ResultsPayload = {
  generatedAtUtc: string;
  singles: ChartResult[];
  doubles: ChartResult[];
  relativeGroups: Array<{ rank: number; name: string }>;
  effectBands: Array<{ rank: number; name: string; low: number | null; high: number | null }>;
  summary: {
    coverage: {
      playersReturnedByCredential: number;
      targetSelectedContributions: number;
      targetChartsMeasured: number;
    };
    modes: {
      singles: ModeSummary;
      doubles: ModeSummary;
    };
  };
};

const FALLBACK_GROUPS = [
  "Extremely Easy",
  "Very Easy",
  "Easy",
  "Slightly Easy",
  "Typical",
  "Slightly Hard",
  "Hard",
  "Very Hard",
  "Extremely Hard",
].map((name, index) => ({ rank: index + 1, name }));

const GROUP_RANGES = [
  "≤ −0.75",
  "−0.75 to −0.50",
  "−0.50 to −0.25",
  "−0.25 to −0.10",
  "−0.10 to +0.10",
  "+0.10 to +0.25",
  "+0.25 to +0.50",
  "+0.50 to +0.75",
  "≥ +0.75",
];

function formatSigned(value: number | null, digits = 2) {
  if (value === null || !Number.isFinite(value)) return "—";
  if (Math.abs(value) < 0.005) return (0).toFixed(digits);
  return `${value > 0 ? "+" : "−"}${Math.abs(value).toFixed(digits)}`;
}

function formatDate(value?: string) {
  if (!value) return "No completed run";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function estimatedLabel(chart: ChartResult) {
  if (chart.estimatedDifficulty === null) return "Unrated";
  return `${chart.mode === "Singles" ? "S" : "D"}${chart.estimatedDifficulty.toFixed(1)}`;
}

function modeLabel(mode: ModeKey) {
  return mode === "singles" ? "Singles" : "Doubles";
}

export function RankingsDashboard() {
  const [results, setResults] = useState<ResultsPayload | null>(null);
  const [activeMode, setActiveMode] = useState<ModeKey>("singles");
  const [initialLoading, setInitialLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runSecret, setRunSecret] = useState("");
  const [search, setSearch] = useState("");
  const [level, setLevel] = useState("all");
  const [group, setGroup] = useState("all");
  const [evidence, setEvidence] = useState("all");
  const [showUnrated, setShowUnrated] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/analyze", { cache: "no-store" })
      .then(async (response) => {
        if (response.status === 404) return null;
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || "Could not load the latest analysis.");
        return body as ResultsPayload;
      })
      .then((body) => {
        if (!cancelled && body) setResults(body);
      })
      .catch((caught: Error) => {
        if (!cancelled) setError(caught.message);
      })
      .finally(() => {
        if (!cancelled) setInitialLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const runAnalysis = async () => {
    setRunning(true);
    setError(null);
    try {
      const response = await fetch("/api/analyze", {
        method: "POST",
        headers: runSecret ? { "X-Analysis-Run-Secret": runSecret } : undefined,
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "The analysis did not complete.");
      setResults(body as ResultsPayload);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The analysis did not complete.");
    } finally {
      setRunning(false);
    }
  };

  const charts = results?.[activeMode] ?? [];
  const groups = results?.effectBands ?? FALLBACK_GROUPS;
  const summary = results?.summary.modes[activeMode];
  const levels = useMemo(
    () => [...new Set(charts.map((chart) => chart.level))].sort((a, b) => a - b),
    [charts],
  );
  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return charts.filter((chart) => {
      if (!showUnrated && chart.evidenceStatus === "Unrated") return false;
      if (level !== "all" && chart.level !== Number(level)) return false;
      if (group !== "all" && chart.effectBandRank !== Number(group)) return false;
      if (evidence !== "all" && chart.evidenceStatus !== evidence) return false;
      if (query && !`${chart.songName} ${chart.stepArtist ?? ""}`.toLocaleLowerCase().includes(query)) {
        return false;
      }
      return true;
    });
  }, [charts, evidence, group, level, search, showUnrated]);

  const groupedCharts = useMemo(() => {
    const map = new Map<number, ChartResult[]>();
    for (const definition of groups) map.set(definition.rank, []);
    for (const chart of filtered) {
      if (chart.effectBandRank !== null) map.get(chart.effectBandRank)?.push(chart);
    }
    for (const rows of map.values()) {
      rows.sort((a, b) =>
        (a.difficultyDelta ?? Number.POSITIVE_INFINITY) -
          (b.difficultyDelta ?? Number.POSITIVE_INFINITY) ||
        a.songName.localeCompare(b.songName),
      );
    }
    return map;
  }, [filtered, groups]);

  const unrated = filtered.filter((chart) => chart.effectBandRank === null);

  const switchMode = (nextMode: ModeKey) => {
    setActiveMode(nextMode);
    setLevel("all");
    setGroup("all");
  };

  return (
    <main>
      <header className="hero">
        <div className="heroGlow" />
        <nav className="topbar">
          <a className="brand" href="#top" aria-label="Pumbility Farmer home">
            <span className="brandMark">PF</span>
            <span>Pumbility Farmer</span>
          </a>
          <span className="liveLabel"><i /> Phoenix analysis</span>
        </nav>

        <div className="heroContent" id="top">
          <div>
            <p className="eyebrow">FIND THE VALUE HIDING IN THE CHART LIST</p>
            <h1>Score smarter.<br /><em>Farm harder.</em></h1>
            <p className="heroCopy">
              Separate Single and Double rankings, normalized to what players at the same
              mode-specific skill level can actually score.
            </p>
          </div>
          <div className="runPanel">
            <div className="runStatus">
              <span>Latest analysis</span>
              <strong>{formatDate(results?.generatedAtUtc)}</strong>
            </div>
            <button className="runButton" onClick={runAnalysis} disabled={running}>
              <span>{running ? "Running analysis…" : "Run fresh analysis"}</span>
              <b aria-hidden="true">{running ? "•••" : "↗"}</b>
            </button>
            <details className="runKey">
              <summary>Protected trigger?</summary>
              <label>
                Run access key
                <input
                  type="password"
                  value={runSecret}
                  onChange={(event) => setRunSecret(event.target.value)}
                  autoComplete="current-password"
                  placeholder="Only needed when configured"
                />
              </label>
            </details>
            <p>The API credential stays on the server. Runs combine each player’s top 20% by Pumbility and most recent 20% per mode, then fall back to the top 100 when that union is smaller.</p>
          </div>
        </div>
      </header>

      <section className="workspace" aria-busy={running || initialLoading}>
        {error && <div className="notice errorNotice"><strong>Analysis unavailable.</strong> {error}</div>}
        {running && (
          <div className="notice runningNotice">
            <span className="spinner" /> Pulling scores and recalculating both independent rankings…
          </div>
        )}

        <div className="modeTabs" role="tablist" aria-label="Play mode rankings">
          {(["singles", "doubles"] as const).map((mode) => {
            const modeSummary = results?.summary.modes[mode];
            return (
              <button
                key={mode}
                role="tab"
                aria-selected={activeMode === mode}
                className={activeMode === mode ? "active" : ""}
                onClick={() => switchMode(mode)}
              >
                <span className="tabIcon">{mode === "singles" ? "S" : "D"}</span>
                <span>
                  <strong>{modeLabel(mode)}</strong>
                  <small>{modeSummary?.measuredCharts ?? 0} measured charts</small>
                </span>
              </button>
            );
          })}
        </div>

        {initialLoading && !results ? (
          <div className="emptyState"><span className="spinner" /><h2>Loading the latest run</h2></div>
        ) : !results ? (
          <div className="emptyState">
            <span className="emptyGlyph">PF</span>
            <h2>No analysis has been stored yet</h2>
            <p>Run the analyzer to create the first independent Singles and Doubles rankings.</p>
            <button onClick={runAnalysis} disabled={running}>Run analysis</button>
          </div>
        ) : (
          <>
            <div className="summaryGrid">
              <article><span>Eligible {modeLabel(activeMode)} players</span><strong>{summary?.eligiblePlayers ?? 0}</strong><small>30+ scores in this mode</small></article>
              <article><span>Measured charts</span><strong>{summary?.measuredCharts ?? 0}</strong><small>of {summary?.catalogCharts ?? 0} level 16+</small></article>
              <article><span>Selected observations</span><strong>{results.summary.coverage.targetSelectedContributions.toLocaleString()}</strong><small>hybrid windows with a top-100 floor</small></article>
              <article><span>Published charts</span><strong>{summary?.publishedCharts ?? 0}</strong><small>10+ contributors</small></article>
            </div>

            <section className="legend" aria-labelledby="legend-title">
              <div className="sectionHeading">
                <div><p className="eyebrow">THE SCALE</p><h2 id="legend-title">Measured scoring-difficulty effect</h2></div>
                <p>Extreme means at least half a level from the typical chart in that folder.</p>
              </div>
              <div className="legendScale">
                {groups.map((definition, index) => (
                  <button
                    key={definition.rank}
                    className={group === String(definition.rank) ? "selected" : ""}
                    style={{ "--tier": definition.rank } as React.CSSProperties}
                    onClick={() => setGroup(group === String(definition.rank) ? "all" : String(definition.rank))}
                  >
                    <i />
                    <span>{definition.name}</span>
                    <small>{GROUP_RANGES[index]}</small>
                  </button>
                ))}
              </div>
            </section>

            <section className="rankings" aria-labelledby="ranking-title">
              <div className="sectionHeading rankingHeading">
                <div><p className="eyebrow">{modeLabel(activeMode).toUpperCase()} ONLY</p><h2 id="ranking-title">Scoring difficulty ranking</h2></div>
                <p>{filtered.length} charts match the current view</p>
              </div>

              <div className="filters">
                <label className="searchField">
                  <span aria-hidden="true">⌕</span>
                  <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search song or step artist" />
                </label>
                <label>
                  <span className="srOnly">Official level</span>
                  <select value={level} onChange={(event) => setLevel(event.target.value)}>
                    <option value="all">All levels</option>
                    {levels.map((value) => <option key={value} value={value}>{activeMode === "singles" ? "S" : "D"}{value}</option>)}
                  </select>
                </label>
                <label>
                  <span className="srOnly">Evidence status</span>
                  <select value={evidence} onChange={(event) => setEvidence(event.target.value)}>
                    <option value="all">All evidence</option>
                    <option value="Published">Published</option>
                    <option value="Provisional">Provisional</option>
                    <option value="Insufficient">Insufficient</option>
                    <option value="Unrated">Unrated</option>
                  </select>
                </label>
                <label className="toggle">
                  <input type="checkbox" checked={showUnrated} onChange={(event) => setShowUnrated(event.target.checked)} />
                  <span /> Show unrated
                </label>
              </div>

              <div className="tierList">
                {groups.map((definition, index) => {
                  const rows = groupedCharts.get(definition.rank) ?? [];
                  if (group !== "all" && group !== String(definition.rank)) return null;
                  return (
                    <section className="tierRow" key={definition.rank} style={{ "--tier": definition.rank } as React.CSSProperties}>
                      <header>
                        <span className="tierNumber">{String(definition.rank).padStart(2, "0")}</span>
                        <div><h3>{definition.name}</h3><p>{GROUP_RANGES[index]} levels from average</p></div>
                        <strong>{rows.length}</strong>
                      </header>
                      <div className="chartGrid">
                        {rows.length ? rows.map((chart) => <ChartCard chart={chart} key={chart.chartId} />) : <p className="emptyTier">No matching charts in this band.</p>}
                      </div>
                    </section>
                  );
                })}
                {showUnrated && unrated.length > 0 && (
                  <section className="tierRow unratedRow">
                    <header><span className="tierNumber">—</span><div><h3>Unrated</h3><p>No observation selected by the hybrid contribution rule</p></div><strong>{unrated.length}</strong></header>
                    <div className="chartGrid">{unrated.map((chart) => <ChartCard chart={chart} key={chart.chartId} />)}</div>
                  </section>
                )}
              </div>
            </section>
          </>
        )}
      </section>

      <footer>
        <strong>Pumbility Farmer</strong>
        <p>Ranks 11–30 estimate player skill. Contributions combine the top 20% by Pumbility and most recent 20% in the selected mode, with overlaps counted once and a top-100 fallback for smaller unions.</p>
      </footer>
    </main>
  );
}

function ChartCard({ chart }: { chart: ChartResult }) {
  const confidence = chart.difficultyCi95Low !== null && chart.difficultyCi95High !== null
    ? `${chart.difficultyCi95Low.toFixed(1)}–${chart.difficultyCi95High.toFixed(1)}`
    : "—";
  return (
    <article className="chartCard">
      <div className="jacket">
        {chart.imageUrl ? <img src={chart.imageUrl} alt="" loading="lazy" /> : <span>{chart.difficulty}</span>}
        <b>{chart.difficulty}</b>
      </div>
      <div className="chartBody">
        <div className="chartTitle"><h4 title={chart.songName}>{chart.songName}</h4><span>#{chart.modeRank ?? "—"}</span></div>
        <p>{chart.stepArtist || "Unknown step artist"}{chart.noteCount ? ` · ${chart.noteCount.toLocaleString()} notes` : ""}</p>
        <div className="difficultyReadout">
          <div><small>Estimated</small><strong>{estimatedLabel(chart)}</strong></div>
          <div><small>Difference</small><strong className={(chart.difficultyDelta ?? 0) < 0 ? "easyDelta" : "hardDelta"}>{formatSigned(chart.difficultyDelta, 2)}</strong></div>
        </div>
        <div className="chartMeta">
          <span title={`95% estimated difficulty interval: ${confidence}`}>{chart.nContributors} contributor{chart.nContributors === 1 ? "" : "s"}</span>
          <span className={`evidence ${chart.evidenceStatus.toLowerCase()}`}>{chart.evidenceStatus}</span>
        </div>
      </div>
    </article>
  );
}
