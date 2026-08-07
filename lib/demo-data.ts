import type { AnalysisPayload, ChartResult, ModeKey } from "./types";

const groupNames = [
  "Extremely Easy",
  "Very Easy",
  "Clearly Easy",
  "Moderately Easy",
  "Slightly Easy",
  "Typical",
  "Slightly Hard",
  "Moderately Hard",
  "Very Hard",
  "Extremely Hard",
] as const;

const demoRows: Record<ModeKey, Array<[string, number, number | null, number, number]>> = {
  singles: [
    ["Lucid Dream", 21, -0.58, 34, 1],
    ["Becouse of You", 21, -0.36, 28, 2],
    ["Conflict", 22, -0.24, 25, 3],
    ["Vector", 20, -0.14, 22, 4],
    ["Orbit Stabilizer", 23, -0.05, 19, 5],
    ["Bar Bar Bar", 20, 0.01, 18, 6],
    ["District 1", 22, 0.07, 16, 7],
    ["Rising Star", 24, 0.16, 14, 8],
    ["Crossing Delta", 25, 0.29, 12, 9],
    ["Final Audition", 26, 0.53, 11, 10],
  ],
  doubles: [
    ["Slam", 24, -0.55, 38, 1],
    ["8 6 - FULL SONG -", 23, -0.34, 31, 2],
    ["Tomboy", 22, -0.22, 29, 3],
    ["Energy Synergy Matrix", 22, -0.13, 24, 4],
    ["After LIKE", 23, -0.04, 22, 5],
    ["Another Truth", 21, 0.02, 20, 6],
    ["Point Zero One", 22, 0.08, 18, 7],
    ["Le Grand Bleu", 25, 0.17, 16, 8],
    ["Demon of Laplace", 27, 0.31, 13, 9],
    ["PARADOXX", 28, 0.51, 10, 10],
  ],
};

function makeChart(mode: ModeKey, row: [string, number, number | null, number, number], index: number): ChartResult {
  const [songName, level, delta, contributors, group] = row;
  const prefix = mode === "singles" ? "S" : "D";
  const averageDifficulty = level + 0.5;
  return {
    mode: mode === "singles" ? "Singles" : "Doubles",
    modeRank: delta === null ? null : index + 1,
    levelRank: delta === null ? null : group,
    levelPercentile: delta === null ? null : (group - 0.5) / 10,
    levelComparisonCharts: delta === null ? null : 10,
    folder: `${prefix}${level}`,
    relativeGroupRank: delta === null ? null : group,
    relativeGroup: delta === null ? null : groupNames[group - 1],
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
    difficultyCi95Low: delta === null ? null : averageDifficulty + delta - 0.18,
    difficultyCi95High: delta === null ? null : averageDifficulty + delta + 0.18,
    nContributors: contributors,
    nPlayersScored: contributors + 7,
    evidenceStatus: contributors >= 10 ? "Published" : "Provisional",
  };
}

export const demoPayload: AnalysisPayload = {
  generatedAtUtc: "2026-08-07T04:20:00Z",
  summary: {
    scriptVersion: "3.0.0-within-level",
    method: {},
    coverage: { playersReturnedByCredential: 52 },
    modes: {
      singles: {
        eligiblePlayers: 46,
        catalogCharts: 474,
        measuredCharts: 312,
        publishedCharts: 188,
        pumbilityPerLevel: 48.7,
        folders: {},
      },
      doubles: {
        eligiblePlayers: 41,
        catalogCharts: 737,
        measuredCharts: 421,
        publishedCharts: 246,
        pumbilityPerLevel: 51.2,
        folders: {},
      },
    },
  },
  singles: demoRows.singles.map((row, index) => makeChart("singles", row, index)),
  doubles: demoRows.doubles.map((row, index) => makeChart("doubles", row, index)),
  relativeGroups: groupNames.map((name, index) => ({ rank: index + 1, name })),
};
