"""Build public comparison artifacts from pinned local official evidence."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from official_player_comparison import DEFAULT_CHART_METADATA, DEFAULT_COMPARISON_DIR, build_official_comparison, write_comparison_artifacts
from official_scoring_sources import DEFAULT_OFFICIAL_SCORING_MANIFEST_PATH, load_official_scoring_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OFFICIAL_SCORING_MANIFEST_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_COMPARISON_DIR)
    parser.add_argument("--chart-metadata", type=Path, default=DEFAULT_CHART_METADATA)
    parser.add_argument("--songs", type=Path, help="Optional local v2 songs JSON with name and bpm min/max.")
    args = parser.parse_args()
    manifest = load_official_scoring_manifest(args.manifest)
    metadata = json.loads(args.chart_metadata.read_bytes()) if args.chart_metadata.exists() else []
    songs = json.loads(args.songs.read_bytes()) if args.songs else []
    if not isinstance(metadata, list) or not isinstance(songs, list):
        parser.error("Chart metadata and songs must each contain a JSON array.")
    index, profiles = build_official_comparison(manifest, chart_metadata=metadata, songs=songs)
    write_comparison_artifacts(index, profiles, args.output_dir)
    print(json.dumps({"generation": index["generation"], "players": len(profiles), "charts": len(index["charts"]),
                      "chartsWithBpm": sum(row["bpmMin"] is not None and row["bpmMax"] is not None for row in index["charts"]),
                      "scores": sum(row["recordedScoreCount"] for row in profiles.values()),
                      "sourceDates": index["sourceDates"], "outputDir": str(args.output_dir.resolve())}, indent=2))


if __name__ == "__main__":
    main()
