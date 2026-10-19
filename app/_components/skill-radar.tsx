"use client";

import { useId } from "react";
import { OFFICIAL_BPM_LABELS, type OfficialComparisonBucket } from "../../lib/official-comparison";
import styles from "./skill-radar.module.css";

type SkillRadarProps = {
  buckets: OfficialComparisonBucket[] | null;
  label: string;
  selectedBucket: number;
  onBucketSelect: (bucket: number) => void;
};

const CX = 160;
const CY = 145;
const RADIUS = 101;
const SHORT_LABELS = ["<150", "150s", "160s", "170s", "180s", "190s", "200s", "210s", "220+"];

function point(index: number, radius: number) {
  const angle = -Math.PI / 2 + index * Math.PI * 2 / 9;
  return { x: CX + Math.cos(angle) * radius, y: CY + Math.sin(angle) * radius };
}

function polygon(radius: number) {
  return Array.from({ length: 9 }, (_, index) => {
    const { x, y } = point(index, radius);
    return `${x},${y}`;
  }).join(" ");
}

export function SkillRadar({ buckets, label, selectedBucket, onBucketSelect }: SkillRadarProps) {
  const titleId = useId();
  return (
    <svg className={styles.radar} viewBox="0 0 320 285" role="img" aria-labelledby={titleId}>
      <title id={titleId}>{`${label}: estimated Singles and Doubles skill by BPM, on a shared 0 to 100 scale. Use the BPM comparison below to select a range and inspect exact values.`}</title>
      {[0.25, 0.5, 0.75, 1].map((fraction) => (
        <polygon className={styles.ring} key={fraction} points={polygon(RADIUS * fraction)} />
      ))}
      {SHORT_LABELS.map((shortLabel, index) => {
        const outer = point(index, RADIUS);
        const text = point(index, RADIUS + 29);
        return (
          <g key={shortLabel} onClick={() => onBucketSelect(index)} className={styles.axis}>
            <line className={index === selectedBucket ? styles.selectedAxis : styles.spoke} x1={CX} y1={CY} x2={outer.x} y2={outer.y} />
            <text className={`${styles.axisLabel} ${index === selectedBucket ? styles.selectedLabel : ""}`} x={text.x} y={text.y} textAnchor="middle" dominantBaseline="middle">
              <title>{`${OFFICIAL_BPM_LABELS[index]} BPM`}</title>
              {shortLabel}
            </text>
          </g>
        );
      })}
      {[25, 50, 75, 100].map((value) => (
        <text className={styles.scaleLabel} key={value} x={CX + 5} y={CY - RADIUS * value / 100 + 10}>{value}</text>
      ))}
      {(["singles", "doubles"] as const).map((mode) => {
        const values = Array.from({ length: 9 }, (_, index) => buckets?.find((bucket) => bucket.mode === mode && bucket.bucketIndex === index));
        if (!values.some((bucket) => bucket?.radarScore !== null && bucket?.radarScore !== undefined)) return null;
        const points = values.map((bucket, index) => point(index, RADIUS * Math.max(0, Math.min(100, bucket?.radarScore ?? 0)) / 100));
        const missingIndex = values.findIndex((bucket) => bucket?.radarScore == null);
        let segments = "";
        let connected = false;
        if (missingIndex !== -1) {
          // Start after a gap so known buckets across the final/first axis connect.
          for (let offset = 1; offset <= 9; offset++) {
            const index = (missingIndex + offset) % 9;
            if (values[index]?.radarScore == null) {
              connected = false;
            } else {
              segments += `${connected ? "L" : "M"}${points[index].x},${points[index].y} `;
              connected = true;
            }
          }
        }
        return (
          <g className={mode === "singles" ? styles.singles : styles.doubles} key={mode}>
            {missingIndex === -1
              ? <polygon className={styles.trace} points={points.map(({ x, y }) => `${x},${y}`).join(" ")} />
              : <path className={`${styles.trace} ${styles.partialTrace}`} d={segments.trim()} />}
            {values.map((bucket, index) => {
              if (bucket?.radarScore === null || bucket?.radarScore === undefined) return null;
              const { x, y } = points[index];
              const size = index === selectedBucket ? 4 : 2.8;
              return mode === "singles"
                ? <circle className={styles.marker} key={index} cx={x} cy={y} r={size} />
                : <rect className={styles.marker} key={index} x={x - size} y={y - size} width={size * 2} height={size * 2} />;
            })}
          </g>
        );
      })}
    </svg>
  );
}

export function SkillRadarLegend() {
  return (
    <div className={styles.legend} aria-label="Radar legend">
      <span><svg aria-hidden="true" viewBox="0 0 32 12"><line x1="1" y1="6" x2="31" y2="6" stroke="#ff7474" strokeWidth="2" /><circle cx="16" cy="6" r="3" fill="#ff7474" /></svg>Singles</span>
      <span><svg aria-hidden="true" viewBox="0 0 32 12"><line x1="1" y1="6" x2="31" y2="6" stroke="#64e694" strokeWidth="2" strokeDasharray="4 3" /><rect x="13" y="3" width="6" height="6" fill="#64e694" /></svg>Doubles</span>
      <span className={styles.legendScale}>0–100 · S20+ / D20+</span>
    </div>
  );
}
