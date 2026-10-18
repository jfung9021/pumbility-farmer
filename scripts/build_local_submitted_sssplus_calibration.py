"""Restore lower Double SSS+ tiers from the existing source-corrected fit."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True,
                  separators=(",", ":"), allow_nan=False)
    os.replace(temporary, path)


def summary_for(payload: dict, base: dict) -> dict:
    folders = {}
    restored = 0
    for level in range(16, 20):
        rows = [row for row in payload["doubles"] if row["level"] == level]
        old = {row["chartId"]: row for row in base["doubles"] if row["level"] == level}
        values = [row["scoringVariants"]["sssplus"]["estimatedDifficulty"] for row in rows]
        finite = [value for value in values if value is not None]
        restored += sum(row["scoringVariants"]["sssplus"]["estimatedDifficulty"] is not None
                        and old[row["chartId"]]["scoringVariants"]["sssplus"]["estimatedDifficulty"] is None
                        for row in rows)
        folders[str(level)] = {
            "charts": len(rows), "rated": len(finite),
            "min": min(finite) if finite else None,
            "median": median(finite) if finite else None,
            "max": max(finite) if finite else None,
        }
        if finite and not math.isclose(median(finite), level + .5, abs_tol=1e-8):
            raise ValueError("The generated lower-folder median moved.")
    protected_fields = ("estimatedDifficulty", "difficultyDelta", "difficultyCi95Low", "difficultyCi95High")
    for mode in ("singles", "doubles"):
        for row, original in zip(payload[mode], base[mode], strict=True):
            if row["chartId"] != original["chartId"]:
                raise ValueError("The calibration changed the chart catalog.")
            for goal in ("general", "sss", "sssplus"):
                if goal == "sssplus" and mode == "doubles" and 16 <= row["level"] < 20:
                    continue
                for field in protected_fields:
                    if row["scoringVariants"][goal].get(field) != original["scoringVariants"][goal].get(field):
                        raise ValueError("A protected Scoring component changed.")
            if row["tierMetrics"] != original["tierMetrics"]:
                raise ValueError("General Pumbility or Clearing changed.")
            scoring = row["scoringVariants"]["sssplus"]["estimatedDifficulty"]
            clearing = row["tierMetrics"]["clearing"]["estimatedDifficulty"]
            expected = None if scoring is None or clearing is None else (scoring + clearing) / 2
            if row["pumbilityVariants"]["sssplus"]["estimatedDifficulty"] != expected:
                raise ValueError("SSS+ Pumbility is not the exact component mean.")
    if payload["coop"] != base["coop"]:
        raise ValueError("Co-op changed.")
    return {"schemaVersion": payload["schemaVersion"], "restoredCharts": restored,
            "folders": folders, "protectedNumericComponentsUnchanged": True,
            "pumbilityComponentMeansVerified": True, "scoreFetches": 0, "modelRefits": 0,
            "method": payload["summary"]["method"]["submittedSssplusCalibration"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=ROOT / ".local-data/piu-scores/combined/experiments/source-aware-width/web_results.json")
    parser.add_argument("--fit", type=Path, default=ROOT / ".local-data/configurable-tier-experiment/sssplus/submitted-double-fit.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".local-data/piu-scores/combined/experiments/submitted-sssplus-calibration/web_results.json")
    parser.add_argument("--report", type=Path, default=ROOT / ".local-data/submitted-sssplus-calibration/summary.json")
    args = parser.parse_args()
    if args.output.resolve() in {args.base.resolve(), args.fit.resolve()}:
        raise ValueError("The overlay output must be separate from its input artifacts.")
    started = time.monotonic()
    from submitted_sssplus_calibration import apply_submitted_sssplus_calibration
    base = json.loads(args.base.read_text(encoding="utf-8"))
    cached = json.loads(args.fit.read_text(encoding="utf-8"))
    print(json.dumps({"stage": "calibration", "scoreFetches": 0, "modelRefits": 0}), flush=True)
    candidate = apply_submitted_sssplus_calibration(base, cached["fit"], fit_identity=cached["identity"])
    report = summary_for(candidate, base)
    print(json.dumps({"stage": "write-local-overlay", "restoredCharts": report["restoredCharts"]}), flush=True)
    write_json(args.output, candidate)
    report.update(seconds=round(time.monotonic() - started, 2), output=str(args.output))
    write_json(args.report, report)
    print(json.dumps({key: value for key, value in report.items() if key != "method"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
