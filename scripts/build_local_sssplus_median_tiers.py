"""Build the local final-median overlay using only the completed schema-52 analysis."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sssplus_median_calibration import apply_median_calibration


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=ROOT / ".local-data/sssplus-median-experiment/schema52-baseline.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--policy-version", type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    result = apply_median_calibration(json.loads(args.base.read_text(encoding="utf-8")), policy_version=args.policy_version)
    experiment = "sssplus-median" if args.policy_version == 1 else f"sssplus-median-v{args.policy_version}"
    output = args.output or ROOT / f".local-data/piu-scores/combined/experiments/{experiment}/web_results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    os.replace(temporary, output)
    print(json.dumps({"schema": result["schemaVersion"], "policyVersion": args.policy_version, "output": str(output), "dataFetches": 0, "modelRefits": 0}))


if __name__ == "__main__":
    main()
