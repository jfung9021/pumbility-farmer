"""Build the chart-ID fallback for Phoenix 2 debut filtering on older tier lists."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CATALOG_URL = "https://piuscores.arroweclip.se/api/v2/charts?mix=Phoenix2&limit=500"
DEFAULT_OUTPUT = ROOT / "config" / "phoenix2-debut-charts.json"


def build(catalog: list[dict], *, captured_at_utc: str) -> dict:
    """Use chart debut metadata; availability and score history are irrelevant."""
    if not isinstance(catalog, list) or not catalog:
        raise ValueError("A nonempty Phoenix 2 chart catalog is required.")
    timestamp = datetime.fromisoformat(captured_at_utc.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("The catalog capture timestamp must include a timezone.")
    captured_at_utc = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    ids, debut_ids = set(), []
    for row in catalog:
        if not isinstance(row, dict):
            raise ValueError("Catalog charts must be objects.")
        chart_id = row.get("id")
        if not isinstance(chart_id, str) or str(UUID(chart_id)) != chart_id:
            raise ValueError("Catalog charts must have canonical UUID IDs.")
        if chart_id in ids:
            raise ValueError("The catalog contains duplicate chart IDs.")
        ids.add(chart_id)
        original_mix = row.get("originalMix")
        if not isinstance(original_mix, str) or not original_mix.strip():
            raise ValueError("Every catalog chart must have originalMix metadata.")
        if row.get("mix", "Phoenix2") != "Phoenix2":
            raise ValueError("The catalog must describe Phoenix 2 availability.")
        if original_mix == "Phoenix2":
            debut_ids.append(chart_id)
    encoded = json.dumps(sorted(catalog, key=lambda row: row["id"]), sort_keys=True,
                         ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return {
        "schemaVersion": 1,
        "source": {
            "url": CATALOG_URL,
            "capturedAtUtc": captured_at_utc,
            "catalogSha256": hashlib.sha256(encoded).hexdigest(),
            "catalogCharts": len(catalog),
            "definition": 'originalMix === "Phoenix2"',
        },
        "chartIds": sorted(debut_ids),
    }


def render(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, help="Read a saved API chart array instead of making requests")
    parser.add_argument("--captured-at", help="Original capture time for a saved catalog, as an ISO timestamp")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        if args.catalog:
            if not args.captured_at:
                raise ValueError("Offline --catalog input requires its original --captured-at timestamp.")
            rows = json.loads(args.catalog.read_text(encoding="utf-8"))
        else:
            from piu_misgrade_analyzer import ApiError, PiuScoresClient

            try:
                rows = PiuScoresClient(os.getenv("PIU_SCORES_API_KEY", "")).fetch_page_collection(
                    "api/v2/charts", {"mix": "Phoenix2", "limit": 500})
            except ApiError as error:
                print(str(error), file=sys.stderr)
                return 1
        captured_at = args.captured_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        document = build(rows, captured_at_utc=captured_at)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render(document), encoding="utf-8")
    except (OSError, ValueError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps({"catalogCharts": document["source"]["catalogCharts"],
                      "debutCharts": len(document["chartIds"]),
                      "capturedAtUtc": document["source"]["capturedAtUtc"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
