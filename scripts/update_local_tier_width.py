"""Regenerate the current local tier variant at 75% using saved fits and captures."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build_candidate():
    from official_scoring_sources import load_official_scoring_manifest
    from production_tiers import FINISH_STAGES, _finish_stage, cohort, validate_fit
    from scripts.build_local_configurable_fits import PRIVATE, inputs
    from sss_scoring import MODES
    from tier_postprocessing import CURRENT_COMPRESSION_FACTOR, compression_factor, finalize_tiers

    first, second, _, catalog, policy, merged, prepared = inputs()
    general = json.loads((PRIVATE / "general-baseline.json").read_text(encoding="utf-8"))
    # Recover original Scoring/Clearing fits before changing their fixed factor.
    candidate = finalize_tiers(general)
    assert compression_factor(candidate) == CURRENT_COMPRESSION_FACTOR
    manifest = load_official_scoring_manifest(ROOT / ".local-data/piu-scores/official/scoring/manifest.json")
    merged["_releasePhoenix2Capture"] = manifest["_captures"]["Phoenix2"]
    context = first, second, catalog, policy, merged, prepared
    fits, identities = {}, []
    for goal in ("sss", "sssplus"):
        fits[goal] = {}
        for source in ("submitted", "official"):
            fits[goal][source] = {}
            for mode, _ in MODES:
                cached = json.loads((PRIVATE / goal / f"{source}-{mode.lower()}-fit.json").read_text(encoding="utf-8"))
                _, _, expected = cohort(prepared, goal, source, mode)
                if cached["identity"] != expected:
                    raise ValueError("A saved target fit does not match the cached captures.")
                validate_fit(cached["fit"], expected)
                fits[goal][source][mode] = cached["fit"]
                identities.append({"goal": goal, "source": source, "mode": mode,
                    "identity": expected, "reusedOriginal": cached["reusedOriginal"]})
    generated = datetime.now(timezone.utc).isoformat()
    for stage in FINISH_STAGES:
        print(json.dumps({"stage": stage, "retainedWidth": CURRENT_COMPRESSION_FACTOR,
                          "scoreFetches": 0, "targetModelRefits": 0}), flush=True)
        candidate = _finish_stage(stage, candidate, context, fits, identities, generated)
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local-data/piu-scores/combined/experiments/submitted-sssplus-calibration/web_results.json")
    parser.add_argument("--report-dir", type=Path, default=ROOT / ".local-data/tier-width-75")
    args = parser.parse_args()
    from scripts.build_local_submitted_sssplus_calibration import write_json
    from tier_postprocessing import compression_factor

    candidate = build_candidate()
    if args.output.exists():
        before = json.loads(args.output.read_text(encoding="utf-8"))
        if candidate.get("coop") != before.get("coop"):
            raise ValueError("The width update changed Co-op.")
        for mode in ("singles", "doubles"):
            if [r["chartId"] for r in candidate[mode]] != [r["chartId"] for r in before[mode]]:
                raise ValueError("The width update changed the chart catalog.")
        args.report_dir.mkdir(parents=True, exist_ok=True)
        backup = args.report_dir / ("before-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        shutil.copyfile(args.output, backup)
    else:
        backup = None
    write_json(args.output, candidate)
    report = {"schemaVersion": candidate["schemaVersion"], "factor": compression_factor(candidate),
              "output": str(args.output), "backup": str(backup) if backup else None,
              "scoreFetches": 0, "targetModelRefits": 0}
    write_json(args.report_dir / "verification.json", report)
    print(json.dumps(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
