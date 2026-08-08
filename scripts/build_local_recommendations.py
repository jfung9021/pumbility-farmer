#!/usr/bin/env python3
"""Build the private local recommendation index from cached mix snapshots."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phoenix2_sync import SNAPSHOT_SCHEMA_VERSION, sanitize_snapshot  # noqa: E402
from piu_misgrade_analyzer import load_snapshot  # noqa: E402
from piu_recommendations import (  # noqa: E402
    build_combined_chart_results,
    build_combined_tier_payload,
    build_recommendation_index,
    recommendation_generation_key,
    recommendation_shard_path,
)


DATA_ROOT = ROOT / ".local-data" / "piu-scores"
OUTPUT_PATH = DATA_ROOT / "recommendations" / "latest.json"
COMBINED_OUTPUT_PATH = DATA_ROOT / "combined" / "analysis" / "web_results.json"


def _read_snapshot(mix: str) -> dict:
    raw_dir = DATA_ROOT / mix / "current"
    players, charts, scores = load_snapshot(raw_dir)
    api_mix = "Phoenix" if mix == "phoenix1" else "Phoenix2"
    return sanitize_snapshot(
        {
            "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
            "mix": api_mix,
            "players": players,
            "charts": charts,
            "scores": scores,
        },
        mix=mix,
    )


def main() -> int:
    phoenix1 = _read_snapshot("phoenix1")
    phoenix2 = _read_snapshot("phoenix2")
    combined_charts, combined_slopes, combined_metadata = build_combined_chart_results(
        phoenix1, phoenix2
    )
    combined_payload = build_combined_tier_payload(combined_charts, combined_metadata)
    generation_key = recommendation_generation_key(combined_payload["generatedAtUtc"])

    def write_shard(number: int, value: dict) -> None:
        relative = recommendation_shard_path(generation_key, number).removeprefix(
            "analysis/recommendations/"
        )
        path = OUTPUT_PATH.parent / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary_path, path)

    payload = build_recommendation_index(
        phoenix1,
        phoenix2,
        generated_at_utc=combined_payload["generatedAtUtc"],
        combined_charts=combined_charts,
        phoenix2_slopes=combined_slopes,
        generation_key=generation_key,
        shard_writer=write_shard,
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, OUTPUT_PATH)
    generations_root = OUTPUT_PATH.parent / "generations"
    if generations_root.exists():
        for generation_dir in generations_root.iterdir():
            if generation_dir.is_dir() and generation_dir.name != generation_key:
                shutil.rmtree(generation_dir)
    COMBINED_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined_temporary = COMBINED_OUTPUT_PATH.with_suffix(".tmp")
    combined_temporary.write_text(
        json.dumps(
            combined_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(combined_temporary, COMBINED_OUTPUT_PATH)
    print(
        f"Built the combined tier list and recommendations for "
        f"{len(payload['players']):,} named Phoenix 2 players."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
