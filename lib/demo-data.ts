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
    ["Lucid Dream", 21, -3.32, 34, 1],
    ["Becouse of You", 21, -2.38, 28, 2],
    ["Conflict", 22, -1.54, 25, 3],
    ["Vector", 20, -0.98, 22, 4],
    ["Orbit Stabilizer", 23, -0.43, 19, 5],
    ["Bar Bar Bar", 20, -0.08, 18, 6],
    ["District 1", 22, 0.42, 16, 7],
    ["Rising Star", 24, 0.91, 14, 8],
    ["Crossing Delta", 25, 1.58, 12, 9],
    ["Final Audition", 26, 2.34, 11, 10],
  ],
  doubles: [
    ["Slam", 24, -3.3, 38, 1],
    ["8 6 - FULL SONG -", 23, -2.47, 31, 2],
    ["Tomboy", 22, -1.62, 29, 3],
    ["Energy Synergy Matrix", 22, -1.02, 24, 4],
    ["After LIKE", 23, -0.51, 22, 5],
    ["Another Truth", 21, 0.03, 20, 6],
    ["Point Zero One", 22, 0.39, 18, 7],
    ["Le Grand Bleu", 25, 0.89, 16, 8],
    ["Demon of Laplace", 27, 1.69, 13, 9],
    ["PARADOXX", 28, 2.51, 10, 10],
  ],
};

function makeChart(mode: ModeKey, row: [string, number, number | null, number, number], index: number): ChartResult {
  const [songName, level, delta, contributors, group] = row;
  const prefix = mode === "singles" ? "S" : "D";
  const averageDifficulty = level + 0.5;
  return {
    mode: mode === "singles" ? "Singles" : "Doubles",
    modeRank: delta === null ? null : index + 1,
    levelRank: delta === null ? null : 1,
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
    scriptVersion: "2.0.0-mode-separated",
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
