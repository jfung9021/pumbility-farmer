#!/usr/bin/env python3
"""Publish a freshly analyzed Phoenix 1 snapshot to stable public paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_phoenix2_rerates import build_rerate_payload  # noqa: E402
from scripts.verify_phoenix1_archive import verify_archive  # noqa: E402


DEFAULT_ANALYSIS = (
    ROOT / ".local-data" / "piu-scores" / "phoenix1" / "analysis" / "web_results.json"
)
ARCHIVE = ROOT / "public" / "data" / "phoenix1.json"
MANIFEST = ROOT / "public" / "data" / "phoenix1.manifest.json"
RERATES = ROOT / "public" / "data" / "phoenix1-rerates.json"
def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_temporary(path: Path, raw: bytes) -> Path:
    temporary = path.with_name(path.name + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(raw)
    return temporary


def publish(analysis_path: Path, workbook_path: Path) -> dict[str, Any]:
    raw = analysis_path.read_bytes()
    payload = json.loads(raw)
    expected_mix = {"key": "phoenix1", "apiValue": "Phoenix", "label": "Phoenix 1"}
    if payload.get("mix") != expected_mix:
        raise ValueError("The analysis file is not a Phoenix 1 public payload.")

    archive_temp = _write_temporary(ARCHIVE, raw)
    rerates = build_rerate_payload(workbook_path, archive_temp)
    rerates_raw = (
        json.dumps(rerates, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    )
    charts = [*payload.get("singles", []), *payload.get("doubles", [])]
    coverage = payload.get("summary", {}).get("coverage", {})
    frozen_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest = {
        "schemaVersion": 1,
        "mix": "phoenix1",
        "archivePath": "/data/phoenix1.json",
        "sourceGeneratedAtUtc": payload.get("generatedAtUtc"),
        "frozenAtUtc": frozen_at,
        "methodologyVersion": payload.get("summary", {}).get("scriptVersion"),
        "sha256": _sha256(raw),
        "bytes": len(raw),
        "catalogCharts": len(charts),
        "measuredCharts": sum(
            chart.get("estimatedDifficulty") is not None for chart in charts
        ),
        "selectedContributions": coverage.get("targetSelectedContributions"),
    }
    manifest_raw = (
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    )
    rerates_temp = _write_temporary(RERATES, rerates_raw)
    manifest_temp = _write_temporary(MANIFEST, manifest_raw)
    os.replace(archive_temp, ARCHIVE)
    os.replace(rerates_temp, RERATES)
    os.replace(manifest_temp, MANIFEST)
    verify_archive(MANIFEST)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--analysis", type=Path, default=DEFAULT_ANALYSIS)
    args = parser.parse_args()
    manifest = publish(args.analysis.resolve(), args.workbook.resolve())
    print(
        "Published fresh Phoenix 1 archive: "
        f"{manifest['catalogCharts']:,} charts, sha256={manifest['sha256']}, "
        f"frozenAtUtc={manifest['frozenAtUtc']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
