"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { clampBpmRange, type BpmRange } from "../../lib/tier-filters";

export function BpmRangeFilter({ bounds, selected, onChange }: {
  bounds: BpmRange | null;
  selected: BpmRange | null;
  onChange: (value: BpmRange | null) => void;
}) {
  const limits = bounds || { min: 0, max: 1 };
  const value = selected ? clampBpmRange(selected, limits) : limits;
  const span = limits.max - limits.min;
  const trackRef = useRef<HTMLDivElement>(null);
  const [trackWidth, setTrackWidth] = useState(0);
  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    const observer = new ResizeObserver(([entry]) => setTrackWidth(entry.contentRect.width));
    observer.observe(track);
    return () => observer.disconnect();
  }, []);
  const labelsOverlap = Boolean(bounds && trackWidth && (value.max - value.min) / span * (trackWidth - 18) < 56);
  const setMinimum = (minimum: number) => {
    const next = clampBpmRange({ min: Math.min(minimum, value.max), max: value.max }, limits);
    if (next.min !== value.min) onChange(next);
  };
  const setMaximum = (maximum: number) => {
    const next = clampBpmRange({ min: value.min, max: Math.max(maximum, value.min) }, limits);
    if (next.max !== value.max) onChange(next);
  };
  const style = {
    "--bpm-start": `${100 * (value.min - limits.min) / span}%`,
    "--bpm-end": `${100 * (value.max - limits.min) / span}%`,
    "--bpm-start-label": `calc(${100 * (value.min - limits.min) / span}% + ${9 - 18 * (value.min - limits.min) / span}px)`,
    "--bpm-end-label": `calc(${100 * (value.max - limits.min) / span}% + ${9 - 18 * (value.max - limits.min) / span}px)`,
  } as CSSProperties;

  return (
    <fieldset className="bpm-filter" disabled={!bounds}>
      <legend className="bpm-filter-heading">
        <span>BPM range</span>
        <button disabled={!bounds || !selected} onClick={() => onChange(null)} type="button">Reset</button>
      </legend>
      <div className="bpm-range-track" data-stacked-labels={labelsOverlap || undefined} ref={trackRef} style={style}>
        <input aria-label="Minimum BPM slider" aria-valuetext={`${value.min} BPM`} max={limits.max} min={limits.min} onChange={(event) => setMinimum(Number(event.target.value))} step="1" style={{ zIndex: value.min === limits.max ? 2 : undefined }} type="range" value={value.min} />
        <input aria-label="Maximum BPM slider" aria-valuetext={`${value.max} BPM`} max={limits.max} min={limits.min} onChange={(event) => setMaximum(Number(event.target.value))} step="1" type="range" value={value.max} />
        {bounds ? <>
          <span aria-hidden="true" className="bpm-handle-label bpm-minimum-label">{value.min}</span>
          <span aria-hidden="true" className="bpm-handle-label bpm-maximum-label">{value.max}</span>
        </> : <span className="bpm-unavailable">BPM unavailable</span>}
      </div>
    </fieldset>
  );
}
