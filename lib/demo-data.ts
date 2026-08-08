import type { AnalysisPayload, ChartResult, ModeKey } from "./types";

const DEMO_DIFFICULTY_DELTA_SCALE = 0.7;
const DEMO_DELTA_CI_HALF_WIDTH = 0.18 * DEMO_DIFFICULTY_DELTA_SCALE;

const groupNames = [
  "Easiest 10%",
  "10–20% percentile",
  "20–30% percentile",
  "30–40% percentile",
  "40–50% percentile",
  "50–60% percentile",
  "60–70% percentile",
  "70–80% percentile",
  "80–90% percentile",
  "Hardest 10%",
] as const;

const effectBands = [
  { rank: 1, name: "Extremely Easy", low: null, high: -1.0 },
  { rank: 2, name: "Very Easy", low: -1.0, high: -0.75 },
  { rank: 3, name: "Easy", low: -0.75, high: -0.5 },
  { rank: 4, name: "Slightly Easy", low: -0.5, high: -0.25 },
  { rank: 5, name: "Typical", low: -0.25, high: 0.25 },
  { rank: 6, name: "Slightly Hard", low: 0.25, high: 0.5 },
  { rank: 7, name: "Hard", low: 0.5, high: 0.75 },
  { rank: 8, name: "Very Hard", low: 0.75, high: 1.0 },
  { rank: 9, name: "Extremely Hard", low: 1.0, high: null },
] as const;

function effectBand(delta: number | null) {
  if (delta === null) return null;
  if (delta <= -1.0) return effectBands[0];
  if (delta <= -0.75) return effectBands[1];
  if (delta <= -0.5) return effectBands[2];
  if (delta <= -0.25) return effectBands[3];
  if (delta < 0.25) return effectBands[4];
  if (delta < 0.5) return effectBands[5];
  if (delta < 0.75) return effectBands[6];
  if (delta < 1.0) return effectBands[7];
  return effectBands[8];
}

const demoRows: Record<ModeKey, Array<[string, number, number | null, number, number]>> = {
  singles: [
    ["Lucid Dream", 16, -1.08, 34, 1],
    ["Becouse of You", 21, -0.82, 28, 2],
    ["Conflict", 22, -0.62, 25, 3],
    ["Vector", 20, -0.36, 22, 4],
    ["Orbit Stabilizer", 23, -0.05, 19, 5],
    ["Bar Bar Bar", 20, 0.01, 18, 6],
    ["District 1", 22, 0.36, 16, 7],
    ["Rising Star", 24, 0.62, 14, 8],
    ["Crossing Delta", 25, 0.82, 12, 9],
    ["Final Audition", 26, 1.08, 11, 10],
  ],
  doubles: [
    ["Slam", 16, -1.12, 38, 1],
    ["8 6 - FULL SONG -", 23, -0.84, 31, 2],
    ["Tomboy", 22, -0.61, 29, 3],
    ["Energy Synergy Matrix", 22, -0.35, 24, 4],
    ["After LIKE", 23, -0.04, 22, 5],
    ["Another Truth", 21, 0.02, 20, 6],
    ["Point Zero One", 22, 0.35, 18, 7],
    ["Le Grand Bleu", 25, 0.61, 16, 8],
    ["Demon of Laplace", 27, 0.84, 13, 9],
    ["PARADOXX", 28, 1.12, 10, 10],
  ],
};

function makeChart(mode: ModeKey, row: [string, number, number | null, number, number], index: number): ChartResult {
  const [songName, level, unscaledDelta, contributors, group] = row;
  const prefix = mode === "singles" ? "S" : "D";
  const averageDifficulty = level + 0.5;
  const delta = unscaledDelta === null
    ? null
    : Number((unscaledDelta * DEMO_DIFFICULTY_DELTA_SCALE).toFixed(6));
  const effect = effectBand(delta);
  return {
    mode: mode === "singles" ? "Singles" : "Doubles",
    modeRank: delta === null ? null : index + 1,
    levelRank: delta === null ? null : group,
    levelPercentile: delta === null ? null : (group - 0.5) / 10,
    levelComparisonCharts: delta === null ? null : 10,
    folder: `${prefix}${level}`,
    relativeGroupRank: delta === null ? null : group,
    relativeGroup: delta === null ? null : groupNames[group - 1],
    effectBandRank: effect?.rank ?? null,
    effectBand: effect?.name ?? null,
    songName,
    difficulty: `${prefix}${level}`,
    type: mode === "singles" ? "Single" : "Double",
    level,
    chartId: `demo-${mode}-${index}`,
    imageUrl: null,
    noteCount: 1120 + index * 87,
    stepArtist: index % 2 ? "NIMGO" : "EXC",
    averageDifficulty,
    estimatedDifficulty: delta === null ? null : averageDifficulty + delta,
    difficultyDelta: delta,
    difficultyDeltaCi95Low: delta === null ? null : delta - DEMO_DELTA_CI_HALF_WIDTH,
    difficultyDeltaCi95High: delta === null ? null : delta + DEMO_DELTA_CI_HALF_WIDTH,
    difficultyCi95Low: delta === null ? null : averageDifficulty + delta - DEMO_DELTA_CI_HALF_WIDTH,
    difficultyCi95High: delta === null ? null : averageDifficulty + delta + DEMO_DELTA_CI_HALF_WIDTH,
    nContributors: contributors,
    nPlayersScored: contributors + 7,
    evidenceStatus: contributors >= 10 ? "Published" : "Provisional",
  };
}

export const demoPayload: AnalysisPayload = {
  generatedAtUtc: "2026-08-07T04:20:00Z",
  mix: { key: "phoenix2", apiValue: "Phoenix2", label: "Phoenix 2" },
  summary: {
    scriptVersion: "6.0.0-level-16-and-0.7-scale",
    method: {
      difficultyDeltaScale: DEMO_DIFFICULTY_DELTA_SCALE,
      displayMinimumOfficialLevel: 16,
    },
    coverage: { playersReturnedByCredential: 52 },
    modes: {
      singles: {
        eligiblePlayers: 46,
        catalogCharts: 474,
        measuredCharts: 312,
        publishedCharts: 188,
        pumbilityPerLevel: 7.3,
        calibration: {},
        shrinkage: {},
        folders: {},
      },
      doubles: {
        eligiblePlayers: 41,
        catalogCharts: 737,
        measuredCharts: 421,
        publishedCharts: 246,
        pumbilityPerLevel: 7.3,
        calibration: {},
        shrinkage: {},
        folders: {},
      },
    },
  },
  singles: demoRows.singles.map((row, index) => makeChart("singles", row, index)),
  doubles: demoRows.doubles.map((row, index) => makeChart("doubles", row, index)),
  relativeGroups: groupNames.map((name, index) => ({ rank: index + 1, name })),
  effectBands: effectBands.map((band) => ({ ...band })),
};

export const demoPayloads: Record<"phoenix1" | "phoenix2", AnalysisPayload> = {
  phoenix1: {
    ...demoPayload,
    mix: { key: "phoenix1", apiValue: "Phoenix", label: "Phoenix 1" },
  },
  phoenix2: demoPayload,
};
