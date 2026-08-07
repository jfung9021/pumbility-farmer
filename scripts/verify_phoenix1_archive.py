"""Verify that the immutable Phoenix 1 browser archive matches its manifest."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "public" / "data" / "phoenix1-20260807.manifest.json"
FORBIDDEN_KEYS = {
    "playerid",
    "username",
    "gametag",
    "authorization",
    "apikey",
    "token",
}
CREDENTIAL_PATTERN = re.compile(rb"(?:piu_scores_live_|pst_live_)[0-9a-f]{16,}", re.I)


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    if not isinstance(value, Mapping):
        return False
    return any(
        str(key).casefold() in FORBIDDEN_KEYS or _contains_forbidden_key(child)
        for key, child in value.items()
    )


def verify_archive(manifest_path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive_path = ROOT / "public" / str(manifest["archivePath"]).lstrip("/")
    raw = archive_path.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != manifest["sha256"]:
        raise ValueError("Phoenix 1 archive checksum does not match its manifest.")
    if len(raw) != manifest["bytes"]:
        raise ValueError("Phoenix 1 archive byte count does not match its manifest.")
    if CREDENTIAL_PATTERN.search(raw):
        raise ValueError("Phoenix 1 archive contains a credential-shaped value.")

    payload = json.loads(raw)
    expected_mix = {"key": "phoenix1", "apiValue": "Phoenix", "label": "Phoenix 1"}
    if payload.get("mix") != expected_mix:
        raise ValueError("Phoenix 1 archive has invalid mix metadata.")
    if _contains_forbidden_key(payload):
        raise ValueError("Phoenix 1 archive contains private player fields.")
    if payload.get("generatedAtUtc") != manifest["sourceGeneratedAtUtc"]:
        raise ValueError("Phoenix 1 archive generation time does not match its manifest.")
    if payload.get("summary", {}).get("scriptVersion") != manifest["methodologyVersion"]:
        raise ValueError("Phoenix 1 archive methodology does not match its manifest.")

    charts = [*payload.get("singles", []), *payload.get("doubles", [])]
    measured = sum(chart.get("estimatedDifficulty") is not None for chart in charts)
    coverage = payload.get("summary", {}).get("coverage", {})
    expected_counts = {
        "catalogCharts": len(charts),
        "measuredCharts": measured,
        "selectedContributions": coverage.get("targetSelectedContributions"),
    }
    for key, actual in expected_counts.items():
        if actual != manifest[key]:
            raise ValueError(f"Phoenix 1 archive {key} does not match its manifest.")
    return manifest


if __name__ == "__main__":
    verified = verify_archive()
    print(
        "Verified Phoenix 1 archive: "
        f"{verified['catalogCharts']} charts, "
        f"{verified['measuredCharts']} measured, "
        f"sha256={verified['sha256']}"
    )
