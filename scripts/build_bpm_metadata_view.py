"""Stage a focal BPM metadata view of an existing artifact without refitting it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bpm_metadata_overlay import overlay_bpm_payload
from production_tiers import _public_copy


def stage(source: Path, destination: Path, *, public: bool = False) -> dict:
    if source.resolve() == destination.resolve():
        raise ValueError("Stage into a separate path to retain the numerical base artifact.")
    raw = source.read_bytes()
    base = json.loads(raw)
    if not isinstance(base, dict):
        raise ValueError("The source artifact must be a JSON object.")
    if public:
        base = _public_copy(base)
    result = overlay_bpm_payload(base)
    encoded = (json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, destination)
    return {"source": str(source.resolve()), "destination": str(destination.resolve()),
            "sourceSha256": hashlib.sha256(raw).hexdigest(),
            "viewSha256": hashlib.sha256(encoded).hexdigest(),
            "bytes": len(encoded), "bpmMetadata": result["bpmMetadata"],
            "baselinesOmitted": public, "scoreFetches": 0, "modelRefits": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--public", action="store_true", help="Omit recovery baselines in a derived public/radar input only")
    args = parser.parse_args()
    print(json.dumps(stage(args.source, args.output, public=args.public), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
