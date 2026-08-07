#!/usr/bin/env python3
"""Capture privacy-safe chart aggregates from the public production API."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


DEFAULT_SOURCE = "https://pumbility-farmer.vercel.app/api/analyze"
ROW_FIELDS = (
    "mode",
    "chartId",
    "folder",
    "level",
    "songName",
    "meanResidualPb",
    "chartResidualPb",
    "residualStdPb",
    "residualCi95LowPb",
    "residualCi95HighPb",
    "nContributors",
    "pumbilityPerLevel",
    "difficultyDelta",
    "effectBandRank",
    "effectBand",
    "relativeGroupRank",
    "shrinkageK",
)


def capture(source: str) -> dict[str, Any]:
    request = Request(source, headers={"Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        raw = response.read()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("The production analysis endpoint did not return an object.")

    rows: list[dict[str, Any]] = []
    for mode_key in ("singles", "doubles"):
        mode_rows = payload.get(mode_key)
        if not isinstance(mode_rows, list):
            raise ValueError(f"The production payload is missing {mode_key!r} rows.")
        for row in mode_rows:
            if isinstance(row, dict):
                rows.append({field: row.get(field) for field in ROW_FIELDS})

    return {
        "schemaVersion": 3,
        "mix": payload.get("mix"),
        "source": source,
        "sourceGeneratedAtUtc": payload.get("generatedAtUtc"),
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "privacy": (
            "Public chart-level aggregates only. No player identifiers, raw scores, "
            "usernames, game tags, or credentials."
        ),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/production-chart-aggregates-20260807.json"),
    )
    args = parser.parse_args()
    fixture = capture(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(fixture['rows']):,} chart aggregates to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
