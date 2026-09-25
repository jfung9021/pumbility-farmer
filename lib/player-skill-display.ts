import type { RecommendationModeResult } from "./types";

export function playerSkillDisplay(
  modeKey: "singles" | "doubles",
  mode: RecommendationModeResult,
  manual = false,
) {
  const prefix = modeKey === "singles" ? "S" : "D";
  const rating = (value: number | null | undefined) =>
    typeof value === "number" && Number.isFinite(value) ? `${prefix}${value.toFixed(2)}` : null;
  const scoring = {
    value: rating(mode.scoringRating) ?? "Unavailable",
    description: manual || mode.manual
      ? "Manually entered scoring skill."
      : "Top-20 Pumbility average, converted to S + Fair Game difficulty.",
  };
  if (manual || mode.manual) {
    return { scoring, clearing: {
      value: "Unavailable",
      description: "A player's clear history is required.",
    } };
  }
  const skill = mode.clearingSkill;
  if (skill?.methodVersion === 2 && skill.difficultyBasis === "current-official-level"
    && skill.requiredClearCount === 50 && skill.ranks[0] === 1 && skill.ranks[1] === 50) {
    if (skill.status === "insufficient-clears") {
      return { scoring, clearing: {
        value: "Unavailable",
        description: `${skill.uniqueClearCount}/50 unique clears; ${Math.max(0, 50 - skill.uniqueClearCount)} more needed.`,
      } };
    }
    const clearingRating = rating(mode.clearingRating);
    if (skill.status === "rated" && clearingRating !== null) {
      return { scoring, clearing: {
        value: clearingRating,
        description: `Average official difficulty of the top 50 clears; ${skill.uniqueClearCount} unique clears.`,
      } };
    }
  }
  return { scoring, clearing: {
    value: "Not yet calculated",
    description: "Available after this player's ratings are refreshed.",
  } };
}
