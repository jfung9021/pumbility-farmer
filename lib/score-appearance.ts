// Phoenix 2 boundaries mirror phoenix2_pumbility.py. Palette values are from
// PIU Scores; grade and earned plate are deliberately independent mappings.
export const NEUTRAL_RESULT_COLOR = "#8B9691";

const GRADE_BANDS = [
  [995_000, "SSS+"], [990_000, "SSS"], [985_000, "SS+"], [980_000, "SS"],
  [975_000, "S+"], [970_000, "S"], [960_000, "AAA+"], [950_000, "AAA"],
  [940_000, "AA+"], [920_000, "AA"], [900_000, "A+"], [800_000, "A"],
  [700_000, "B"], [600_000, "C"], [500_000, "D"], [0, "F"],
] as const;

const GRADE_COLORS: Record<string, string> = {
  "SSS+": "#7FC7EF", SSS: "#62B4E8", "SS+": "#F2C233", SS: "#F2C233",
  "S+": "#EDBB2E", S: "#EDBB2E", "AAA+": "#D9DDE1", AAA: "#C4C9CE",
  "AA+": "#DE863A", AA: "#DE863A", "A+": "#CE6A2E", A: "#CE6A2E",
  B: "#34BE6A", C: "#34BE6A", D: "#34BE6A", F: "#34BE6A",
};

const PLATES: Record<string, {label: string; color: string}> = {
  PG: {label: "Perfect Game", color: "#7FC7EF"},
  UG: {label: "Ultimate Game", color: "#62B4E8"},
  EG: {label: "Extreme Game", color: "#F2C233"},
  SG: {label: "Superb Game", color: "#EDBB2E"},
  MG: {label: "Marvelous Game", color: "#D9DDE1"},
  TG: {label: "Talented Game", color: "#C4C9CE"},
  FG: {label: "Fair Game", color: "#DE863A"},
  RG: {label: "Rough Game", color: "#CE6A2E"},
};

export function gradeForScore(score: number | null | undefined): string | null {
  if (typeof score !== "number" || !Number.isFinite(score)) return null;
  const value = Math.trunc(score);
  if (value < 0) return null;
  return GRADE_BANDS.find(([threshold]) => Math.min(value, 1_000_000) >= threshold)?.[1] ?? null;
}

export function gradeColor(grade: string | null | undefined): string {
  return GRADE_COLORS[grade?.trim().toUpperCase() ?? ""] ?? NEUTRAL_RESULT_COLOR;
}

export function scoreColor(score: number | null | undefined): string {
  return gradeColor(gradeForScore(score));
}

export function normalizePlate(value: string | null | undefined): string | null {
  const normalized = value?.trim().toLowerCase();
  if (!normalized) return null;
  return Object.keys(PLATES).find(code => code.toLowerCase() === normalized
    || PLATES[code].label.toLowerCase() === normalized) ?? null;
}

export function plateColor(value: string | null | undefined): string {
  const code = normalizePlate(value);
  return code ? PLATES[code].color : NEUTRAL_RESULT_COLOR;
}

export function plateLabel(value: string | null | undefined): string {
  const code = normalizePlate(value);
  return code ? PLATES[code].label : value?.trim() || "Plate unavailable";
}
