"""Generate local Skill Radar profiles from cached files, with no network calls."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from piu_misgrade_analyzer import load_snapshot
from piu_recommendations import public_player_key
from phoenix2_sync import merge_best_scores
from skill_radar import build_skill_radar, build_suggestion_catalog
from skill_radar_runtime import MINIMUM_PHOENIX2_SCORES, skill_metadata
from tier_scores import public_tier_scores, _sync_time


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    os.replace(temp, path)


def build(source_root, output_dir, *, tiers_path=None, player_key=None):
    data = source_root/".local-data/piu-scores"
    players, charts, scores = load_snapshot(data/"phoenix2/current")
    tiers_path = tiers_path or data/"combined/experiments/submitted-sssplus-calibration/web_results.json"
    tiers = json.loads(tiers_path.read_text(encoding="utf-8"))
    if not any("sssplus" in row.get("pumbilityVariants", {}) for mode in ("singles", "doubles") for row in tiers.get(mode, [])):
        raise ValueError("Build the SSS+ Pumbility analysis before generating Skill Radar.")
    catalog = build_suggestion_catalog(tiers, charts)
    del tiers
    gc.collect()
    named = {public_player_key(row.get("playerId", row.get("userId"))): row for row in players
        if str(row.get("username") or "").strip()}
    index_path = data/"recommendations/latest.json"
    listed = None
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        listed = {row["playerKey"]: row for row in index.get("players", [])}
        named = {key: value for key, value in named.items() if key in listed}
    if player_key is not None:
        if player_key not in named:
            raise ValueError("The selected local player was not found.")
        named = {player_key: named[player_key]}
    by_player = defaultdict(list)
    for row in scores:
        by_player[str(row.get("playerId"))].append(row)
    del scores
    allowed = {str(row.get("id", row.get("chartId"))) for row in charts}
    created = datetime.now(timezone.utc).isoformat()
    write_json(output_dir/"metadata.json", skill_metadata(charts))
    write_json(output_dir/"suggestion-catalog.json", catalog)
    counts = []
    public_players = []
    for key, player in named.items():
        internal = str(player.get("playerId", player.get("userId")))
        rows = by_player.get(internal, [])
        label = (listed or {}).get(key, {})
        submitted_count = len({str(row["chartId"]) for row in rows if row.get("chartId") is not None})
        if submitted_count < MINIMUM_PHOENIX2_SCORES:
            continue
        profile = build_skill_radar(charts, rows, catalog, player_key=key,
            display_name=str(label.get("displayName") or label.get("username") or player["username"]),
            generated_at_utc=created, player_synced_at_utc=_sync_time(player.get("lastSyncedAtUtc")),
            personal_scores=public_tier_scores(merge_best_scores([], rows, player_id=internal), allowed))
        write_json(output_dir/"players"/(key+".json"), profile)
        counts.append(key)
        public_players.append({"playerKey": key, "username": str(player["username"]),
            "displayName": profile["player"]["displayName"], "phoenix2ScoreCount": submitted_count,
            "eligibility": {mode: bool(label.get("eligibility", {}).get(mode)) for mode in ("singles", "doubles", "coop")}})
    public_players.sort(key=lambda row: (row["displayName"].casefold(), row["playerKey"]))
    write_json(output_dir/"players.json", {"generatedAtUtc": created, "refreshSupported": False,
        "method": {"minimumPhoenix2Scores": MINIMUM_PHOENIX2_SCORES}, "players": public_players})
    report = {"methodVersion": 1, "generatedAtUtc": created, "tierGeneratedAtUtc": catalog["tierGeneratedAtUtc"],
        "players": counts, "allLevelCharts": len(charts), "ratedSuggestions": len(catalog["charts"]),
        "scoreFetches": 0, "modelRefits": 0}
    write_json(output_dir/"manifest.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=ROOT/".local-data/piu-scores/skill-radar")
    parser.add_argument("--tiers", type=Path)
    parser.add_argument("--player-key")
    args = parser.parse_args()
    report = build(args.source_root.resolve(), args.output_dir.resolve(), tiers_path=args.tiers, player_key=args.player_key)
    print(json.dumps({key: len(value) if key == "players" else value for key, value in report.items()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
