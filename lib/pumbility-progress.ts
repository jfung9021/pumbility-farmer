import type { RecommendationModeKey } from "./types";

interface ProgressRung {
  label: string;
  threshold: number;
}

export interface PumbilityProgress {
  label: string;
  rungIndex: number;
  threshold: number;
  nextLabel: string | null;
  nextThreshold: number | null;
  remaining: number;
  percent: number;
}

function numberedRungs(
  prefix: string,
  thresholds: number[],
): ProgressRung[] {
  return thresholds.map((threshold, index) => ({
    label: `${prefix} Lv. ${index + 1}`,
    threshold,
  }));
}

const SHARED_INTERMEDIATE_THRESHOLDS = [
  5_000, 6_000, 7_000, 8_000, 9_000,
  10_000, 11_000, 12_000, 13_000, 14_000,
];

const SINGLE_TITLE_LADDER: ProgressRung[] = [
  { label: "Single Beginner", threshold: 0 },
  ...numberedRungs("Single Intermediate", SHARED_INTERMEDIATE_THRESHOLDS),
  ...numberedRungs("Single Advanced", [
    15_000, 15_250, 15_500, 15_750, 16_000,
    16_250, 16_500, 16_750, 17_000, 17_250,
  ]),
  ...numberedRungs("Single Expert", [
    17_500, 17_700, 17_900, 18_100, 18_300,
    18_500, 18_600, 18_700, 18_800, 18_900,
  ]),
  { label: "Single Master", threshold: 19_000 },
];

const DOUBLE_TITLE_LADDER: ProgressRung[] = [
  { label: "Double Beginner", threshold: 0 },
  ...numberedRungs("Double Intermediate", SHARED_INTERMEDIATE_THRESHOLDS),
  ...numberedRungs("Double Advanced", [
    15_000, 15_250, 15_500, 15_750, 16_000,
    16_250, 16_500, 16_750, 17_000, 17_250,
  ]),
  ...numberedRungs("Double Expert", [
    17_500, 17_700, 17_900, 18_100, 18_300,
    18_500, 18_600, 18_700, 18_800, 18_900,
  ]),
  { label: "Double Master", threshold: 19_000 },
];

const OVERALL_RANK_LADDER: ProgressRung[] = [
  { label: "Unranked", threshold: 0 },
  ...numberedRungs("Bronze", [10_000, 10_500, 11_000, 11_500, 12_000]),
  ...numberedRungs("Silver", [12_500, 13_000, 13_500, 14_000, 14_500]),
  ...numberedRungs("Gold", [15_000, 15_200, 15_400, 15_600, 15_800]),
  ...numberedRungs("Platinum", [16_000, 16_200, 16_400, 16_600, 16_800]),
  ...numberedRungs("Diamond", [17_000, 17_200, 17_400, 17_600, 17_800]),
  ...numberedRungs("Red Beryl", [18_000, 18_200, 18_400, 18_600, 18_800]),
  ...numberedRungs("Alexandrite", [19_000, 19_200, 19_400, 19_600, 19_800]),
  { label: "Phoenix", threshold: 20_000 },
];

const LADDERS: Record<RecommendationModeKey, ProgressRung[]> = {
  overall: OVERALL_RANK_LADDER,
  singles: SINGLE_TITLE_LADDER,
  doubles: DOUBLE_TITLE_LADDER,
};

export function pumbilityProgress(
  mode: RecommendationModeKey,
  pumbility: number,
): PumbilityProgress {
  const ladder = LADDERS[mode];
  const value = Number.isFinite(pumbility) ? Math.max(0, pumbility) : 0;
  let currentIndex = 0;
  for (let index = 1; index < ladder.length; index += 1) {
    if (value < ladder[index].threshold) break;
    currentIndex = index;
  }

  const current = ladder[currentIndex];
  const next = ladder[currentIndex + 1] ?? null;
  if (!next) {
    return {
      label: current.label,
      rungIndex: currentIndex,
      threshold: current.threshold,
      nextLabel: null,
      nextThreshold: null,
      remaining: 0,
      percent: 100,
    };
  }

  const span = next.threshold - current.threshold;
  return {
    label: current.label,
    rungIndex: currentIndex,
    threshold: current.threshold,
    nextLabel: next.label,
    nextThreshold: next.threshold,
    remaining: Math.max(0, next.threshold - value),
    percent: Math.max(0, Math.min(100, ((value - current.threshold) / span) * 100)),
  };
}
