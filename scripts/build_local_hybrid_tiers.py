#!/usr/bin/env python3
"""Build a separate local schema-47 hybrid candidate without fetching or publishing."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hybrid_tiers import build_local_hybrid_tiers
from scripts.build_local_recommendations import _read_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=ROOT / ".local-data/hybrid-tier-experiment/before-maximum.json")
    parser.add_argument("--submitted-base", type=Path, default=ROOT / ".local-data/hybrid-tier-experiment/submitted-profile.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".local-data/piu-scores/combined/experiments/hybrid/maximum.json")
    args = parser.parse_args()
    candidate = build_local_hybrid_tiers(json.loads(args.base.read_text(encoding="utf-8")),
        json.loads(args.submitted_base.read_text(encoding="utf-8")), _read_snapshot("phoenix1"), _read_snapshot("phoenix2"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps({"schemaVersion": candidate["schemaVersion"], "output": str(args.output),
                      "ratedScoring": {mode: sum(row["estimatedDifficulty"] is not None for row in candidate[mode])
                                       for mode in ("singles", "doubles")}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
