#!/usr/bin/env python3
"""Build local tiers and recommendations from cached mix snapshots."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from datetime import datetime, timezone
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
    public_player_key,
)
from scoring_percentile import build_production_tier_payload  # noqa: E402
from pumbility_contract import SCRIPT_VERSION  # noqa: E402
from tier_difficulty import TIER_METRIC_VERSION, refresh_pumbility_metrics  # noqa: E402
from tier_scores import build_local_tier_score_artifact  # noqa: E402
from tier_postprocessing import finalize_tiers, initial_fit_payload, SCHEMA_VERSION as PROCESSED_SCHEMA  # noqa: E402


DATA_ROOT = ROOT / ".local-data" / "piu-scores"
OUTPUT_PATH = DATA_ROOT / "recommendations" / "latest.json"
COMBINED_OUTPUT_PATH = DATA_ROOT / "combined" / "analysis" / "web_results.json"
OFFICIAL_SNAPSHOT_PATH = DATA_ROOT / "official" / "phoenix2" / "current.json"
OFFICIAL_SCORING_MANIFEST_PATH = DATA_ROOT / "official" / "scoring" / "manifest.json"
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


def _stamp_tier_overlay(payload: dict, base: dict, operation: str) -> None:
    """Identify this producer while retaining the original model generation."""
    inherited = {
        "scriptVersion": base.get("summary", {}).get("scriptVersion"),
        "schemaVersion": base.get("schemaVersion"),
        "generatedAtUtc": base.get("generatedAtUtc"),
    }
    existing = base.get("summary", {}).get("tierArtifactProvenance", {})
    generated_at = datetime.now(timezone.utc).isoformat()
    summary = payload.setdefault("summary", {})
    summary["scriptVersion"] = f"{SCRIPT_VERSION}+{operation}"
    summary["tierArtifactProvenance"] = {
        "base": existing.get("base", inherited),
        "previous": inherited,
        "operation": operation,
        "producerScriptVersion": SCRIPT_VERSION,
    }
    summary.setdefault("method", {}).setdefault("tierMetrics", {})["version"] = TIER_METRIC_VERSION
    payload["generatedAtUtc"] = summary["generatedAtUtc"] = generated_at


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
        help="Rebuild local tiers with 50th/75th/90th score profiles, double 90th-percentile weight, and level-specific spread scales; preserve recommendations.",
    )
    mode.add_argument(
        "--official-tiers",
        action="store_true",
        help="Rebuild S23+/D25+ Scoring and S25+/D26+ Clearing tiers with official per-player scoring and lower-percentile clearer ability; preserve recommendations.",
    )
    mode.add_argument(
        "--cross-level-scoring",
        action="store_true",
        help="Overlay official-only Phoenix 1/Phoenix 2 Scoring at S20+/D20+ on existing local tiers; preserve Clearing and recommendation artifacts.",
    )
    mode.add_argument("--cross-level-clearing", action="store_true",
                      help="Rebuild submitted Clearing using top-30 skill, 200 same-mode records, and cross-level median anchors; preserve Scoring and recommendations.")
    mode.add_argument("--refresh-tier-metadata", action="store_true",
                      help="Refresh Pumbility composites and current producer metadata on existing tiers; preserve Scoring, Clearing, Co-op, recommendations and player score artifacts without fitting models.")
    mode.add_argument("--compress-tiers", action="store_true",
                      help="Halve fitted Scoring/Clearing deviations from official level + 0.5; preserve initial fits and personal artifacts without refitting.")
    parser.add_argument("--clearing-audit-dir", type=Path,
                        default=ROOT / "output" / "cross-level-clearing-2026-10-17",
                        help="Directory for the public Clearing comparison and verification artifacts.")
    parser.add_argument(
        "--official-scoring-manifest",
        type=Path,
        default=OFFICIAL_SCORING_MANIFEST_PATH,
        help="Private dual-mix official Scoring manifest for --cross-level-scoring.",
    )
    mode.add_argument(
        "--tier-scores-only",
        action="store_true",
        help="Build minimized player score overlays from cached Phoenix 2 scores and the existing tier list; skip numeric analysis.",
    )
    mode.add_argument(
        "--prune-only",
        action="store_true",
        help="Remove generation directories not referenced by latest.json and exit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.refresh_tier_metadata or args.compress_tiers:
        from copy import deepcopy

        base = json.loads(COMBINED_OUTPUT_PATH.read_text(encoding="utf-8"))
        candidate = deepcopy(base)
        if args.compress_tiers or base.get("schemaVersion") == PROCESSED_SCHEMA:
            candidate = finalize_tiers(base)
        else:
            refresh_pumbility_metrics(candidate)
        _stamp_tier_overlay(candidate, base, "midpoint-compression" if args.compress_tiers else "refresh-pumbility")
        _write_json(COMBINED_OUTPUT_PATH, candidate)
        print("Updated tier metadata and derived ratings; initial fitted values, Co-op and personal artifacts are preserved.")
        return 0
    if args.cross_level_clearing:
        from cross_level_clearing import apply_cross_level_clearing, current_clearing_catalog

        base = json.loads(COMBINED_OUTPUT_PATH.read_text(encoding="utf-8"))
        phoenix1, phoenix2 = _read_snapshot("phoenix1"), _read_snapshot("phoenix2")
        dates = {}
        for mix in ("phoenix1", "phoenix2"):
            manifest = json.loads((DATA_ROOT / mix / "current" / "snapshot_manifest.json").read_text(encoding="utf-8"))
            dates[mix] = manifest.get("captureCompletedAtUtc", manifest.get("pulledAtUtc"))
        fitted_base = initial_fit_payload(base)
        candidate = apply_cross_level_clearing(fitted_base, phoenix1, phoenix2,
            current_clearing_catalog(fitted_base, phoenix2), source_dates=dates)
        _stamp_tier_overlay(candidate, base, "cross-level-clearing")
        generated_at = candidate["generatedAtUtc"]
        def scoring_rows(payload):
            return [{k: v for k, v in row.items() if k != "tierMetrics"}
                    for key in ("singles", "doubles") for row in payload[key]]
        if scoring_rows(fitted_base) != scoring_rows(candidate) or base.get("coop") != candidate.get("coop"):
            raise ValueError("Clearing rebuild changed Scoring or Co-op.")
        if base.get("schemaVersion") == PROCESSED_SCHEMA:
            candidate = finalize_tiers(candidate)
        records, summary = [], {"schemaVersion": candidate["schemaVersion"], "generatedAtUtc": generated_at,
            "method": candidate["summary"]["method"]["crossLevelClearing"],
            "scoringUnchanged": True, "coopUnchanged": True, "modes": {}}
        for mode in ("singles", "doubles"):
            before = {row["chartId"]: row for row in base[mode]}
            for row in candidate[mode]:
                clearing = row["tierMetrics"]["clearing"]
                previous = before[row["chartId"]]["tierMetrics"]["clearing"]["estimatedDifficulty"]
                estimate = clearing["estimatedDifficulty"]
                records.append({"chartId": row["chartId"], "songName": row["songName"], "difficulty": row["difficulty"],
                    "mode": row["type"], "level": row["level"], **clearing, "previousDifficulty": previous,
                    "change": estimate - previous if estimate is not None and previous is not None else None,
                    "scoringDifficulty": row["estimatedDifficulty"],
                    "pumbilityDifficulty": row["tierMetrics"]["pumbility"]["estimatedDifficulty"]})
            values = [row["tierMetrics"]["clearing"] for row in candidate[mode]]
            summary["modes"][mode] = {"charts": len(values), "rated": sum(c["estimatedDifficulty"] is not None for c in values),
                "published": sum(c["evidenceStatus"] == "Published" for c in values),
                "extrapolated": sum(c["extrapolated"] for c in values)}
        args.clearing_audit_dir.mkdir(parents=True, exist_ok=True)
        if not (args.clearing_audit_dir / "before.json").exists():
            _write_json(args.clearing_audit_dir / "before.json", base)
        _write_json(args.clearing_audit_dir / "chart-analysis.json", records)
        _write_json(args.clearing_audit_dir / "summary.json", summary)
        _write_json(COMBINED_OUTPUT_PATH, candidate)
        print(json.dumps(summary["modes"], indent=2))
        print("Built submitted cross-level Clearing tiers; Scoring, Co-op and recommendations are preserved.")
        return 0
    if args.cross_level_scoring:
        from official_score_merge import merge_official_scores
        from official_scoring_sources import load_official_scoring_manifest
        from official_cross_level_scoring import apply_cross_level_scoring

        # This path does not rebuild player/recommendation models or Clearing.
        base = json.loads(COMBINED_OUTPUT_PATH.read_text(encoding="utf-8"))
        manifest = load_official_scoring_manifest(args.official_scoring_manifest)
        merged = merge_official_scores(manifest)
        candidate = apply_cross_level_scoring(base, merged, merged['currentCatalog'])
        if base.get("schemaVersion") == PROCESSED_SCHEMA:
            candidate = finalize_tiers(candidate)
        _stamp_tier_overlay(candidate, base, "cross-level-scoring")
        _write_json(COMBINED_OUTPUT_PATH, candidate)
        print("Built local official-only cross-level S20+/D20+ Scoring tiers. Clearing and recommendation artifacts are preserved.")
        return 0
    removed = (
        _prune_unpublished_generations(_published_generation_key())
        if args.prune_only or not (args.phoenix2_only or args.tiers_only or args.scoring_percentile or args.official_tiers or args.tier_scores_only) else 0
    )
    if args.prune_only:
        print(f"Removed {removed} unpublished recommendation generation(s).")
        return 0
    if args.tier_scores_only:
        tiers = json.loads(COMBINED_OUTPUT_PATH.read_text(encoding="utf-8"))
        _write_json(
            OUTPUT_PATH.parent / "tier-scores.json",
            build_local_tier_score_artifact(_read_snapshot("phoenix2"), tiers, public_player_key),
        )
        print("Built local player tier scores from the cached Phoenix 2 snapshot.")
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
    official_arguments = {}
    if args.official_tiers:
        # Missing/invalid official inputs must fail before replacing the aggregate.
        official_arguments["official_snapshot"] = json.loads(OFFICIAL_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    combined_payload = build_production_tier_payload(
        combined_charts, combined_metadata, phoenix1, phoenix2,
        **official_arguments,
        **({"midpoint_compression": True} if os.getenv("PIU_TIER_MIDPOINT_COMPRESSION") == "1"
           or (COMBINED_OUTPUT_PATH.exists() and json.loads(COMBINED_OUTPUT_PATH.read_text(encoding="utf-8")).get("schemaVersion") == PROCESSED_SCHEMA) else {}),
    )
    if args.tiers_only or args.official_tiers:
        _write_json(COMBINED_OUTPUT_PATH, combined_payload)
        print("Built local tier lists" + (" with official S23+/D25+ Scoring and S25+/D26+ Clearing evidence." if args.official_tiers
                                         else " from both Phoenix sources.") + " Recommendations are unchanged.")
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
    _write_json(
        OUTPUT_PATH.parent / "tier-scores.json",
        build_local_tier_score_artifact(phoenix2, combined_payload, public_player_key),
    )
    _prune_unpublished_generations(generation_key)
    print(
        "Built combined tiers and recommendations for "
        f"{len(payload['players']):,} named Phoenix 2 players."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
