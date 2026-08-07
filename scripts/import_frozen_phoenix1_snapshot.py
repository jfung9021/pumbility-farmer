#!/usr/bin/env python3
"""Seed the immutable private Phoenix 1 score snapshot used by recommendations."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis_runtime import PrivateBlobStore  # noqa: E402
from phoenix2_sync import sanitize_snapshot  # noqa: E402
from piu_misgrade_analyzer import load_snapshot  # noqa: E402
from piu_recommendations import frozen_phoenix1_snapshot_path  # noqa: E402
from scripts.capture_private_score_snapshot import validate_snapshot_directory  # noqa: E402


SOURCE = ROOT / ".local-data" / "piu-scores" / "phoenix1" / "current"


def main() -> int:
    manifest = validate_snapshot_directory(SOURCE, mix="phoenix1")
    players, charts, scores = load_snapshot(SOURCE)
    snapshot = sanitize_snapshot(
        {
            "schemaVersion": 1,
            "mix": "Phoenix",
            "generatedAtUtc": manifest.get("captureCompletedAtUtc", ""),
            "players": players,
            "charts": charts,
            "scores": scores,
        },
        mix="phoenix1",
    )
    store = PrivateBlobStore()
    path = frozen_phoenix1_snapshot_path()
    store.put_json(path, snapshot)
    restored = store.get_json(path)
    if restored is None or len(restored.get("scores", [])) != len(snapshot["scores"]):
        raise RuntimeError("The frozen Phoenix 1 private snapshot failed verification.")
    print(
        f"Seeded immutable Phoenix 1 recommendation evidence: "
        f"{len(snapshot['charts']):,} charts and {len(snapshot['scores']):,} scores."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
