import type { CSSProperties } from "react";
import { gradeColor, gradeForScore, normalizePlate, plateColor, plateLabel } from "../../lib/score-appearance";
import styles from "./player-result.module.css";

const tone = (color: string) => ({"--result-color": color} as CSSProperties);

export function ResultGrade({grade}: {grade: string | null | undefined}) {
  return grade?.trim() ? <span className={styles.badge} style={tone(gradeColor(grade))}>{grade.trim().toUpperCase()}</span> : <span className={styles.missing}>Unavailable</span>;
}

export function ResultPlate({plate, full = false}: {plate: string | null | undefined; full?: boolean}) {
  if (!plate?.trim()) return <span className={styles.missing}>Unavailable</span>;
  const code = normalizePlate(plate);
  return <span className={styles.badge} style={tone(plateColor(plate))} title={plateLabel(plate)}>{full ? plateLabel(plate) : code ?? plate.trim()}</span>;
}

export function ResultScore({score, grade, formattedScore, className = ""}: {score: number | null | undefined; grade?: string | null; formattedScore?: string; className?: string}) {
  const valid = typeof score === "number" && Number.isFinite(score) && score >= 0;
  return <span className={`${valid ? styles.score : styles.missing} ${className}`} style={tone(gradeColor(grade || gradeForScore(score)))}>{valid ? formattedScore ?? score.toLocaleString("en-US") : "—"}</span>;
}

export function PlayerResult({score, grade, plate, compact = false, className = "", formattedScore}: {
  score: number | null | undefined;
  grade?: string | null;
  plate?: string | null;
  compact?: boolean;
  className?: string;
  formattedScore?: string;
}) {
  const valid = typeof score === "number" && Number.isFinite(score) && score >= 0;
  const displayedGrade = grade?.trim() || gradeForScore(score);
  const label = [valid ? `${score.toLocaleString("en-US")} points` : "No recorded score", displayedGrade ? `grade ${displayedGrade}` : null, plate?.trim() ? `plate ${plateLabel(plate)}` : null].filter(Boolean).join(", ");
  return <span aria-label={label} className={`${styles.result}${compact ? ` ${styles.compact}` : ""} ${className}`} role="group" title={label}>
    <ResultScore formattedScore={formattedScore} grade={displayedGrade} score={score} />
    {!compact && displayedGrade ? <ResultGrade grade={displayedGrade} /> : null}
    {plate?.trim() ? <ResultPlate plate={plate} /> : null}
  </span>;
}
