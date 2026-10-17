#!/usr/bin/env python3
"""Normalize cached SSS folders locally without fetching or refitting models."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_local_sss_tiers import _write
from sss_folder_centering import apply_folder_centering, validate_folder_centering


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=ROOT / ".local-data/sss-centered-experiment/schema50-baseline.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".local-data/piu-scores/combined/experiments/sss-centered/web_results.json")
    parser.add_argument("--audit", type=Path, default=ROOT / ".local-data/sss-centered-experiment/verification.json")
    args = parser.parse_args()
    base = json.loads(args.base.read_text(encoding="utf-8"))
    candidate = apply_folder_centering(base)
    validate_folder_centering(candidate, base)
    _write(args.output, candidate)
    audit = {"schemaVersion": candidate["schemaVersion"], "baselineFileSha256": hashlib.sha256(args.base.read_bytes()).hexdigest(),
             "output": str(args.output), "folders": candidate["summary"]["method"]["sssFolderCentering"]["folders"],
             "dataFetches": 0, "modelRefits": 0, "bootstrapRefits": 0}
    _write(args.audit, audit)
    print(json.dumps({k: v for k, v in audit.items() if k != "folders"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
