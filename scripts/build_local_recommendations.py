#!/usr/bin/env python3
"""Build local tiers and recommendations from cached mix snapshots."""

from __future__ import annotations

import argparse
import json
import math
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
    RECOMMENDATION_CHART_FIELDS,
    build_combined_chart_results,
    build_combined_tier_payload,
    build_recommendation_index,
    recommendation_generation_key,
)
from scoring_percentile import build_production_tier_payload  # noqa: E402


DATA_ROOT = ROOT / ".local-data" / "piu-scores"
OUTPUT_PATH = DATA_ROOT / "recommendations" / "latest.json"
COMBINED_OUTPUT_PATH = DATA_ROOT / "combined" / "analysis" / "web_results.json"
GENERATIONS_PATH = OUTPUT_PATH.parent / "generations"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(
            payload,
            output,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    os.replace(temporary, path)


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


def _published_generation_key() -> str | None:
    if not OUTPUT_PATH.exists():
        return None
    payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    generation_key = payload.get("generationKey")
    return str(generation_key) if generation_key else None


def _prune_unpublished_generations(published_generation_key: str | None) -> int:
    if not GENERATIONS_PATH.exists():
        return 0
    removed = 0
    for generation_path in GENERATIONS_PATH.iterdir():
        if not generation_path.is_dir() or generation_path.name == published_generation_key:
            continue
        shutil.rmtree(generation_path)
        removed += 1
    return removed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--phoenix2-only",
        action="store_true",
        help="Rebuild only local tier lists using cached Phoenix 2 data; leave recommendations unchanged.",
    )
    mode.add_argument(
        "--tiers-only",
        action="store_true",
        help="Rebuild combined tier lists from both cached snapshots; leave recommendations unchanged.",
    )
    mode.add_argument(
        "--scoring-profile", "--scoring-percentile",
        dest="scoring_percentile",
        action="store_true",
        help="Rebuild local tiers with 10th/25th/50th/75th/90th score profiles, double 90th-percentile weight, and level-specific spread scales; preserve recommendations.",
    )
    mode.add_argument(
        "--prune-only",
        action="store_true",
        help="Remove generation directories not referenced by latest.json and exit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    removed = (
        _prune_unpublished_generations(_published_generation_key())
        if args.prune_only or not (args.phoenix2_only or args.tiers_only or args.scoring_percentile) else 0
    )
    if args.prune_only:
        print(f"Removed {removed} unpublished recommendation generation(s).")
        return 0

    phoenix1 = (
        sanitize_snapshot({
            "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
            "mix": "Phoenix",
            "players": [],
            "charts": [],
            "scores": [],
        }, mix="phoenix1")
        if args.phoenix2_only else _read_snapshot("phoenix1")
    )
    phoenix2 = _read_snapshot("phoenix2")
    if args.phoenix2_only:
        combined_charts, _, combined_metadata = build_combined_chart_results(phoenix1, phoenix2)
        combined_payload = build_combined_tier_payload(combined_charts, combined_metadata)
        method = combined_payload["summary"]["method"]
        method["sourceSelection"] = "phoenix2-only"
        method["tierMetrics"]["clearing"]["clearPopulation"] = (
            "unique nonbroken current-catalog clearers from Phoenix 2 only"
        )
        _write_json(COMBINED_OUTPUT_PATH, combined_payload)
        print("Built Phoenix 2-only local tier lists. Recommendations are unchanged.")
        return 0
    combined_charts, combined_slopes, combined_metadata = build_combined_chart_results(phoenix1, phoenix2)
    if args.scoring_percentile:
        from scoring_percentile import build_percentile_tier_payload
        combined_payload = build_percentile_tier_payload(combined_charts, combined_metadata, phoenix1, phoenix2)
        _write_json(COMBINED_OUTPUT_PATH, combined_payload)
        method = combined_payload["summary"]["method"]
        print(f"Built local score-profile tiers with level-specific scales and "
              f"{method['scoreProfileCalibration']['actualTwoGradeCount']} two-grade proposals. Recommendations are unchanged.")
        return 0
    combined_payload = build_production_tier_payload(combined_charts, combined_metadata, phoenix1, phoenix2)
    if args.tiers_only:
        _write_json(COMBINED_OUTPUT_PATH, combined_payload)
        print("Built combined local tier lists from both sources. Recommendations are unchanged.")
        return 0
    generation_key = recommendation_generation_key(combined_payload["generatedAtUtc"])

    def write_shard(shard: int, shard_payload: object) -> None:
        _write_json(
            OUTPUT_PATH.parent
            / "generations"
            / generation_key
            / "shards"
            / f"{shard:04d}.json",
            shard_payload,
        )

    payload = build_recommendation_index(
        phoenix1,
        phoenix2,
        generated_at_utc=combined_payload["generatedAtUtc"],
        combined_charts=combined_charts,
        phoenix2_slopes=combined_slopes,
        generation_key=generation_key,
        shard_writer=write_shard,
    )
    payload["charts"] = [
        {key: chart.get(key) for key in RECOMMENDATION_CHART_FIELDS}
        for chart in combined_charts
        if isinstance(chart.get("estimatedDifficulty"), (int, float))
        and math.isfinite(float(chart["estimatedDifficulty"]))
    ]
    _write_json(OUTPUT_PATH, payload)
    _write_json(COMBINED_OUTPUT_PATH, combined_payload)
    _prune_unpublished_generations(generation_key)
    print(
        "Built combined tiers and recommendations for "
        f"{len(payload['players']):,} named Phoenix 2 players."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
