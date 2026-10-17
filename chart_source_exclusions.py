"""Versioned performance exclusions for tier analysis, independent of raw captures."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import UUID

DEFAULT_PATH = Path(__file__).resolve().parent / "config/chart-source-exclusions.json"


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def source_name(value: str) -> str:
    aliases = {"phoenix": "Phoenix", "phoenix1": "Phoenix", "phoenix2": "Phoenix2"}
    try:
        return aliases[value.lower()]
    except (KeyError, AttributeError):
        raise ValueError("Unknown chart-exclusion source.") from None


class ExclusionPolicy:
    def __init__(self, config: Mapping[str, Any], catalog: Sequence[Mapping[str, Any]]):
        if config.get("version") != 1 or not isinstance(config.get("rules"), list):
            raise ValueError("Unsupported chart source exclusion configuration.")
        charts = {str(c.get("id", c.get("chartId"))): c for c in catalog}
        self.rules = []
        seen = set()
        for raw in config["rules"]:
            if not isinstance(raw, dict):
                raise ValueError("A chart exclusion rule must be an object.")
            cid, mode = raw.get("chartId"), raw.get("mode")
            try:
                valid_uuid = str(UUID(cid)) == cid
            except (ValueError, TypeError, AttributeError):
                valid_uuid = False
            if (not valid_uuid or mode not in ("Single", "Double")
                    or type(raw.get("enabled")) is not bool
                    or raw.get("scope") != "tier-analysis-performance"
                    or not isinstance(raw.get("reason"), str) or not raw["reason"].strip()
                    or not isinstance(raw.get("label"), str) or not raw["label"].strip()):
                raise ValueError("Malformed chart source exclusion rule.")
            source = source_name(raw.get("source"))
            key = source, cid
            if key in seen:
                raise ValueError("Duplicate or contradictory chart source exclusion rule.")
            seen.add(key)
            if raw["enabled"] and (cid not in charts or charts[cid].get("type") != mode):
                raise ValueError("Enabled chart exclusion is absent from the current catalog or has the wrong mode.")
            self.rules.append({**raw, "source": source, "ruleId": f"{source}:{cid}"})
        self.rules.sort(key=lambda r: r["ruleId"])
        self.config_hash = canonical_hash(config)

    def excludes(self, source: str, chart_id: str) -> bool:
        name = source_name(source)
        return any(r["enabled"] and r["source"] == name and r["chartId"] == str(chart_id) for r in self.rules)

    def semantic_hash(self, chart_ids: set[str] | None = None) -> str:
        return canonical_hash({"version": 1, "rules": [
            {k: r[k] for k in ("source", "chartId", "mode", "scope")}
            for r in self.rules if r["enabled"] and (chart_ids is None or r["chartId"] in chart_ids)]})

    def public(self) -> dict:
        return {"version": 1, "configSha256": self.config_hash,
            "effectivePolicySha256": self.semantic_hash(), "rules": self.rules}

    def filter_snapshot(self, snapshot: Mapping[str, Any], source: str) -> dict:
        return {**snapshot, "scores": [r for r in snapshot.get("scores", [])
            if not self.excludes(source, str(r.get("chartId")))]}


def load_exclusions(catalog: Sequence[Mapping[str, Any]], path: Path = DEFAULT_PATH) -> ExclusionPolicy:
    return ExclusionPolicy(json.loads(path.read_text(encoding="utf-8")), catalog)
