#!/usr/bin/env python3
"""Capture or import private official-only cross-level Scoring sources."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from official_score_merge import merge_official_scores
from official_scoring_sources import (
    DEFAULT_OFFICIAL_SCORING_MANIFEST_PATH,
    capture_official_scoring_sources, fetch_official_scoring_catalogs, import_research_scoring_sources,
)
from piu_misgrade_analyzer import ApiError, PiuScoresClient
from scripts.capture_official_score_snapshot import _api_key
from scripts.capture_private_score_snapshot import _ensure_git_ignored


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OFFICIAL_SCORING_MANIFEST_PATH)
    parser.add_argument("--import-research", type=Path, help="Migrate an existing complete capture without any API calls.")
    parser.add_argument("--restart", action="store_true", help="Restart partial live capture; always retain the frozen Phoenix source.")
    args = parser.parse_args()
    try:
        _ensure_git_ignored(args.output)
        if args.import_research:
            manifest = import_research_scoring_sources(args.import_research, args.output)
        else:
            client = PiuScoresClient(api_key=_api_key())
            catalogs = fetch_official_scoring_catalogs(client, args.output)

            def progress(mix: str, done: int, total: int) -> None:
                if done % 25 == 0 or done == total:
                    print(f"Official Scoring {mix}: {done}/{total}", flush=True)

            manifest = capture_official_scoring_sources(client, catalogs, args.output, restart=args.restart, progress=progress)
        merged = merge_official_scores(manifest)
        # Diagnostics are aggregate only. Raw tags/provenance stay in ignored inputs.
        print(json.dumps({"manifest": str(args.output.resolve()), "manifestSha256": merged["manifestSha256"],
                          "sourceDates": merged["sourceDates"], "diagnostics": merged["diagnostics"]}, indent=2))
        return 0
    except (ApiError, OSError, ValueError, KeyError) as exc:
        print(f"Official Scoring capture failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
