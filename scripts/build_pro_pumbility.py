#!/usr/bin/env python3
"""Build local overall-only pro recommendations from dedicated Phoenix2 captures."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.capture_pro_pumbility import DEFAULT_OUTPUT, write_json  # noqa: E402


def build_local(output: Path = DEFAULT_OUTPUT) -> dict:
    from pro_pumbility_modes import build_pro_artifacts
    output = output.resolve()
    snapshot = json.loads((output / "snapshot.json").read_text(encoding="utf-8"))
    profiles = json.loads((output / "profiles.json").read_text(encoding="utf-8"))
    index, payloads = build_pro_artifacts(snapshot, profiles)
    generation = index["generation"]
    for player_key, payload in payloads.items():
        write_json(output / "generations" / generation / "players" / f"{player_key}.json", payload)
    write_json(output / "generations" / generation / "index.json", index)
    # The index pointer changes only after every immutable player artifact exists.
    write_json(output / "index.json", index)
    summary = {"generation": generation, "snapshotAt": index.get("snapshotAt"), "players": len(payloads),
               "statuses": dict(Counter(payload["status"] for payload in payloads.values())),
               "recommendations": sum(len(payload["overall"]["recommendations"]) for payload in payloads.values()),
               "readyModes": dict(Counter(mode for payload in payloads.values() for mode, result in payload["modes"].items() if result["status"] == "ready"))}
    print(json.dumps(summary, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        build_local(args.output_dir)
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(f"Pro build failed before advancing the public index: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
