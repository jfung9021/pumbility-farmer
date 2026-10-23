"""Compile/check accepted BPM decisions without network or research caches."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from song_bpm_metadata import (  # noqa: E402
    BpmMetadataError, SOURCE_FILENAMES, compile_runtime_catalog,
    enrich_charts_bpm, validate_runtime_catalog,
)


def build(config_dir: Path) -> dict:
    documents, hashes = [], {}
    for name in SOURCE_FILENAMES:
        raw = (config_dir / name).read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        documents.append(json.loads(raw.decode("utf-8")))
    return compile_runtime_catalog(*documents, source_hashes=hashes)


def render(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"


def write_preview(path: Path, source_catalog: Path, runtime: dict) -> None:
    source = json.loads(source_catalog.read_text(encoding="utf-8"))
    if source.get("mix") != "Phoenix2":
        raise BpmMetadataError("Migration preview source mix must be Phoenix2")
    originals = source.get("charts")
    if not isinstance(originals, list):
        raise BpmMetadataError("Migration preview requires source charts")
    song_rows = {(row["name"], row["type"]): row for row in source["songs"]}
    before = []
    for chart in originals:
        song = song_rows.get((chart.get("songName"), chart.get("songType")))
        if song is None:
            raise BpmMetadataError("Migration preview chart lacks an exact source song")
        interval = song.get("bpm") or {}
        before.append({**chart, "bpmMin": interval.get("min"), "bpmMax": interval.get("max")})
    after = enrich_charts_bpm(before, catalog=runtime)
    ids = [chart.get("id") for chart in originals]
    if any(not isinstance(chart_id, str) or not chart_id for chart_id in ids) or len(set(ids)) != len(ids):
        raise BpmMetadataError("Migration preview chart IDs must be unique nonempty strings")
    records = []
    for old, new in zip(before, after):
        records.append({
            "chartId": new["id"], "songName": new["songName"], "songType": new["songType"],
            "before": {key: old.get(key) for key in ("bpmMin", "bpmMax", "bpmFocal")},
            "after": {key: new.get(key) for key in ("bpmMin", "bpmMax", "bpmFocal")},
            "displayChanged": (old["bpmMin"], old["bpmMax"]) != (new["bpmMin"], new["bpmMax"]),
        })
    preview = {
        "kind": "Offline catalog preview; not an active database migration",
        "bpmMetadataRevision": runtime["bpmMetadataRevision"],
        "sourceCatalogSha256": hashlib.sha256(source_catalog.read_bytes()).hexdigest(),
        "summary": {"songs": len(runtime["songs"]), "charts": len(records),
                    "displayChangedCharts": sum(row["displayChanged"] for row in records)},
        "charts": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(preview, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    parser.add_argument("--output", type=Path, default=ROOT / "config" / "phoenix2-song-bpm-runtime.json")
    parser.add_argument("--check", action="store_true", help="Reject missing/stale generated data without writing")
    parser.add_argument("--offline", action="store_true", help="Explicit offline mode (all operation is always offline)")
    parser.add_argument("--preview", type=Path, help="Optional per-chart migration preview output")
    parser.add_argument("--source-catalog", type=Path, default=ROOT / "output" / "phoenix2-bpm-ranges-2026-10-23" / "source-catalog.json")
    args = parser.parse_args(argv)
    try:
        document = build(args.config_dir)
        expected = render(document)
        if args.check:
            if not args.output.exists() or args.output.read_text(encoding="utf-8") != expected:
                raise BpmMetadataError("Generated BPM runtime catalog is stale; run scripts/build_song_bpm_runtime.py")
            validate_runtime_catalog(json.loads(expected))
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(expected, encoding="utf-8")
        if args.preview is not None:
            write_preview(args.preview, args.source_catalog, document)
    except (BpmMetadataError, OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps({"checked": args.check, **document["summary"],
                      "bpmMetadataRevision": document["bpmMetadataRevision"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
