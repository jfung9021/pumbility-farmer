"""Build local combined official D26+ Clearing from the pinned scoring manifest only."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from combined_official_clearing import apply_combined_official_clearing
from official_high_level_clearing import COMBINED_CAP_POLICY, P1_OBSERVED_CAP_POLICY
from official_score_merge import merge_official_scores
from official_scoring_sources import DEFAULT_OFFICIAL_SCORING_MANIFEST_PATH, load_official_scoring_manifest


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=ROOT / ".local-data/piu-scores/combined/experiments/sssplus-spread/web_results.json")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OFFICIAL_SCORING_MANIFEST_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cap-policy", choices=(COMBINED_CAP_POLICY, P1_OBSERVED_CAP_POLICY), default=P1_OBSERVED_CAP_POLICY)
    parser.add_argument("--private-output", type=Path, default=ROOT / ".local-data/sssplus-spread-implementation/combined-clearing")
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    args = parser.parse_args()
    if args.bootstrap_samples != 1000:
        raise ValueError("The combined official Clearing preview requires 1000 conditional bootstrap samples.")
    base = json.loads(args.base.read_text(encoding="utf-8"))
    merged = merge_official_scores(load_official_scoring_manifest(args.manifest))
    reference = base.get("summary", {}).get("method", {}).get("sssplusScoring", {}).get("officialManifestSha256")
    if reference != merged["manifestSha256"]:
        raise ValueError("Combined Clearing must use the same pinned official manifest as Scoring.")
    result, report = apply_combined_official_clearing(base, merged, bootstrap_samples=args.bootstrap_samples, cap_policy=args.cap_policy)
    experiment = "sssplus-spread-p1-observed-clearing" if args.cap_policy == P1_OBSERVED_CAP_POLICY else "sssplus-spread-combined-clearing"
    output = args.output or ROOT / f".local-data/piu-scores/combined/experiments/{experiment}/web_results.json"
    _write(output, result)
    _write(args.private_output / "verification.json", report)
    print(json.dumps({"schemaVersion": result["schemaVersion"], "output": str(output),
        "dataFetches": 0, "modelRefits": 0, "report": report}))


if __name__ == "__main__":
    main()
