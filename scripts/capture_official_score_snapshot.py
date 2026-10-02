#!/usr/bin/env python3
"""Capture official-only Phoenix 2 high-level boards into the ignored local cache."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from official_scores import DEFAULT_OFFICIAL_SNAPSHOT_PATH, capture_official_snapshot  # noqa: E402
from piu_misgrade_analyzer import ApiError, PiuScoresClient  # noqa: E402
from scripts.capture_private_score_snapshot import _ensure_git_ignored  # noqa: E402


DEFAULT_CATALOG_PATH = PROJECT_ROOT / ".local-data" / "piu-scores" / "phoenix2" / "current" / "charts.json"


def _api_key() -> str:
    """Environment wins; read only the one relevant .env.local setting."""
    api_key = os.environ.get("PIU_SCORES_API_KEY", "").strip()
    if api_key:
        return api_key
    env_path = PROJECT_ROOT / ".env.local"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.strip().removeprefix("export ").partition("=")
            if separator and name.strip() == "PIU_SCORES_API_KEY":
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                return value.strip()
    raise ApiError("PIU_SCORES_API_KEY is missing from the environment and .env.local.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart", action="store_true", help="Ignore an interrupted same-week checkpoint.")
    parser.add_argument("--extend-existing", "--extend-clearing-history", dest="extend_existing", action="store_true",
                        help="Add missing S25+/D26+ tiers and S22+/D23+ history boards without changing existing boards or week.")
    args = parser.parse_args()
    try:
        _ensure_git_ignored(DEFAULT_OFFICIAL_SNAPSHOT_PATH)
        charts = json.loads(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
        if not isinstance(charts, list):
            raise ValueError("The cached Phoenix 2 chart catalog must be an array.")
        client = PiuScoresClient(api_key=_api_key())

        def report_progress(completed: int, total: int) -> None:
            if completed % 25 == 0 or completed == total:
                print(f"Official boards: {completed}/{total}", flush=True)

        snapshot = capture_official_snapshot(
            client, charts, restart=args.restart, include_history=True,
            extend_existing=args.extend_existing, progress=report_progress,
        )
        boards = snapshot["boards"]
        entries = [entry for board in boards for entry in board["entries"]]
        print(json.dumps({
            "source": snapshot["source"],
            "asOf": snapshot["asOf"],
            "boards": len(boards),
            "entries": len(entries),
            "players": len({entry["playerId"] for entry in entries}),
            "possiblyTruncatedBoards": sum(board["possiblyTruncated"] for board in boards),
            "emptyBoards": sum(not board["entries"] for board in boards),
            "httpRequests": client.request_count,
        }, indent=2))
        return 0
    except (ApiError, OSError, ValueError) as exc:
        print(f"Official snapshot failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
