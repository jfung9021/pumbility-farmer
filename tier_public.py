"""Bounded tier-list DTOs and immutable, lazily loaded chart evidence shards."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

FIELDS = json.loads((Path(__file__).parent / "config/tier-public-fields.json").read_text(encoding="utf-8"))
MODES = ("singles", "doubles", "coop")
MAX_LIST_BYTES = 12_000_000
# Five target choices carry five variant pairs instead of the legacy three.
# Keep the existing budget for old schemas and bound expanded network payloads.
MAX_EXPANDED_LIST_BYTES = 20_000_000
# Schema 60 adds sparse replay points for four goals across both level-20
# folders. The real publication is 20.17 MB decoded and remains below 4 MB
# with conservative level-one gzip. Preserve older schemas' existing budgets.
MAX_WIDTH_LIST_BYTES = 22_000_000
MAX_COMPRESSED_LIST_BYTES = 4_000_000
DETAIL_PREFIX = "analysis/private/tier-details/"


def tier_detail_generated_at(payload: Mapping[str, Any]) -> str | None:
    """Resolve the immutable detail generation independently of numeric fits."""
    view = payload.get("tierListView", {})
    publication = payload.get("summary", {}).get("method", {}).get("productionTierPublication", {})
    current = payload.get("generatedAtUtc")
    if "detailsGeneratedAtUtc" in view:
        declared = view["detailsGeneratedAtUtc"]
        if (not isinstance(declared, str) or not declared
                or declared != current and publication.get("reusedComputation") is not True):
            return None
        return declared
    if publication.get("reusedComputation") is True:
        # Existing fresh overlays can reuse old numerical work while writing
        # new shards. Their immutable prefix is derived from this run's inputs.
        inputs = publication.get("inputs")
        if isinstance(inputs, Mapping):
            from canonical_json import canonical_json_sha256
            generation = canonical_json_sha256(inputs)
            prefix = DETAIL_PREFIX + hashlib.sha256(generation.encode("utf-8")).hexdigest() + "/"
            if view.get("detailsPrefix") == prefix:
                return current if isinstance(current, str) and current else None
        # PR127 publications reused both the computation and its old shards.
        computed = publication.get("computedAtUtc")
        return computed if isinstance(computed, str) and computed else None
    return current if isinstance(current, str) and current else None


def _pick(value: Mapping[str, Any], keys: list[str]) -> dict:
    return {key: value[key] for key in keys if key in value}


def _metric(value: Mapping[str, Any], kind: str) -> dict:
    result = _pick(value, FIELDS["metric"] + FIELDS[kind])
    if kind in {"chart", "scoring"}:
        result.pop("scoringBoundaryRecords", None)
    if kind in {"chart", "scoring"} and value.get("scoringMethod") in {
        "sss-boundary-score-gap-v1", "target-boundary-score-gap-v1",
    }:
        refinement = value.get("scoringRefinement")
        board = refinement.get("fullBoard") if isinstance(refinement, Mapping) else None
        records = board.get("records") if isinstance(board, Mapping) else None
        if type(records) in (int, float) and 0 <= records <= 2**53 - 1 and int(records) == records:
            result["scoringBoundaryRecords"] = int(records)
    if isinstance(value.get("scoringSpreadPolicy"), Mapping):
        result["scoringSpreadPolicy"] = _pick(value["scoringSpreadPolicy"], ["midpoint"])
    if isinstance(value.get("scoringLevel20Width"), Mapping):
        # Sparse replay evidence contains only the prior points, current input
        # points and a shared folder-policy reference; rollback metrics remain
        # private rather than expanding every chart in the list.
        result["scoringLevel20Width"] = _pick(value["scoringLevel20Width"],
            ["version", "method", "group", "source", "original", "input", "final"])
    return result


def _chart(row: Mapping[str, Any]) -> dict:
    result = _metric(row, "chart")
    if isinstance(row.get("scoringVariants"), Mapping):
        result["scoringVariants"] = {goal: _pick(metric, ["estimatedDifficulty"]) if goal == "general" else _metric(metric, "scoring") for goal, metric in row["scoringVariants"].items() if isinstance(metric, Mapping)}
        tail = row["scoringVariants"].get("sss", {}).get("scoringTailPolicy")
        if isinstance(tail, Mapping):
            result["scoringVariants"]["sss"]["scoringTailPolicy"] = _pick(tail,
                ["preTailDifficulty", "preTailDifficultyCi95Low", "preTailDifficultyCi95High"])
    if isinstance(row.get("pumbilityVariants"), Mapping):
        result["pumbilityVariants"] = {goal: _pick(metric, ["estimatedDifficulty"]) if goal == "general" else _metric(metric, "pumbility") for goal, metric in row["pumbilityVariants"].items() if isinstance(metric, Mapping)}
    tiers = row.get("tierMetrics")
    if isinstance(tiers, Mapping):
        result["tierMetrics"] = {kind: _metric(tiers[kind], kind) for kind in ("clearing", "pumbility") if isinstance(tiers.get(kind), Mapping)}
        evidence = tiers.get("clearing", {}).get("clearingEvidence")
        if isinstance(evidence, Mapping):
            result["tierMetrics"]["clearing"]["clearingEvidence"] = _pick(evidence, ["capPolicy"])
    # These booleans are used by the list's source/method labels. Evidence itself
    # belongs to the lazy chart response rather than every browser list.
    for key in ("officialEvidence", "officialScoringEvidence"):
        if row.get(key):
            result[key] = {"available": True}
    return result


def compact_tier_payload(payload: Mapping[str, Any], *, details_prefix: str | None = None) -> dict:
    """Project validated data without recalculating or rounding any tier values."""
    if payload.get("tierListView", {}).get("kind") == "list":
        return dict(payload)
    result = _pick(payload, ["schemaVersion", "generatedAtUtc", "mix", "relativeGroups", "effectBands", "bpmMetadata"])
    summary = payload.get("summary", {})
    method = summary.get("method", {})
    compact_method = _pick(method, FIELDS["method"])
    for key in ("sssScoring", "sssRefinement"):
        if isinstance(method.get(key), Mapping):
            compact_method[key] = _pick(method[key], ["version", "baseSchemaVersion"])
    if isinstance(method.get("submittedSssplusCalibration"), Mapping):
        compact_method["submittedSssplusCalibration"] = _pick(method["submittedSssplusCalibration"], ["version", "localOnly"])
    result["summary"] = {**_pick(summary, ["scriptVersion", "coverage", "modes"]), "method": compact_method}
    for mode in MODES:
        if mode in payload:
            result[mode] = [_chart(row) for row in payload[mode]]
    result["tierListView"] = {"version": 1, "kind": "list", "detailsAvailable": True}
    if details_prefix is not None:
        if not re.fullmatch(re.escape(DETAIL_PREFIX) + r"[a-f0-9]{64}/", details_prefix):
            raise ValueError("Invalid tier detail prefix.")
        result["tierListView"]["detailsPrefix"] = details_prefix
        result["tierListView"]["detailsGeneratedAtUtc"] = payload.get("generatedAtUtc")
    if isinstance(method.get("productionTierPublication"), Mapping):
        from production_tiers import public_digest
        compact_method["productionTierPublication"] = dict(compact_method["productionTierPublication"])
        compact_method["productionTierPublication"]["publicPayloadSha256"] = public_digest(result)
    return result


def tier_browser_payload(payload: Mapping[str, Any]) -> dict:
    """Trim a validated publication for browsing; evidence stays in its shards.

    This is a response projection, never a replacement for the persisted list.
    Replay validators continue to receive the original publication, and chart
    details retain their immutable generation and all fitted values/intervals.
    """
    compact = compact_tier_payload(payload)
    result = dict(compact)
    method = _pick(compact.get("summary", {}).get("method", {}), FIELDS["browserMethod"])
    for key, keys in FIELDS["browserMethodFields"].items():
        if isinstance(method.get(key), Mapping):
            method[key] = _pick(method[key], keys)
    result["summary"] = {**compact.get("summary", {}), "method": method}

    def metric(value: Mapping[str, Any], kind: str) -> dict:
        # Preserve exact final points, bands and record-count labels. Ranks,
        # intervals, deltas and replay inputs are only read by lazy details.
        return _pick(value, FIELDS["browserMetric"] + FIELDS[kind])

    def chart(row: Mapping[str, Any]) -> dict:
        value = metric(row, "chart")
        for key, kind in (("scoringVariants", "scoring"), ("pumbilityVariants", "pumbility")):
            if isinstance(row.get(key), Mapping):
                value[key] = {goal: _pick(variant, ["estimatedDifficulty"]) if goal == "general"
                              else metric(variant, kind) for goal, variant in row[key].items()
                              if isinstance(variant, Mapping)}
        if isinstance(row.get("tierMetrics"), Mapping):
            tiers = row["tierMetrics"]
            value["tierMetrics"] = {kind: metric(tiers[kind], kind) for kind in ("clearing", "pumbility")
                                    if isinstance(tiers.get(kind), Mapping)}
            evidence = tiers.get("clearing", {}).get("clearingEvidence")
            if isinstance(evidence, Mapping):
                value["tierMetrics"]["clearing"]["clearingEvidence"] = _pick(evidence, ["capPolicy"])
        for key in ("officialEvidence", "officialScoringEvidence"):
            if row.get(key):
                value[key] = {"available": True}
        return value

    for mode in MODES:
        if mode in compact:
            result[mode] = [chart(row) for row in compact[mode]]
    # The existing generation/prefix pins lazy details to the full publication.
    result["tierListView"] = {**compact["tierListView"], "browserVersion": 1}
    publication = method.get("productionTierPublication")
    if isinstance(publication, Mapping):
        from production_tiers import public_digest
        method["productionTierPublication"] = dict(publication)
        method["productionTierPublication"]["publicPayloadSha256"] = public_digest(result)
    return result


def detail_shard_path(prefix: str, chart_id: str) -> str:
    if not re.fullmatch(re.escape(DETAIL_PREFIX) + r"[a-f0-9]{64}/", prefix):
        raise ValueError("Invalid tier detail prefix.")
    bucket = hashlib.sha256(chart_id.encode("utf-8")).digest()[0] % 32
    return f"{prefix}{bucket:02x}.json"


def _without_baselines(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _without_baselines(child) for key, child in value.items() if not key.endswith("Baseline")}
    if isinstance(value, list):
        return [_without_baselines(child) for child in value]
    return value


def tier_chart_details(payload: Mapping[str, Any], chart_id: str) -> dict | None:
    row = next((row for mode in MODES for row in payload.get(mode, []) if row.get("chartId") == chart_id), None)
    return None if row is None else {"generatedAtUtc": payload.get("generatedAtUtc"), "chart": _without_baselines(row)}


def prepare_tier_publication(full: Mapping[str, Any], generation: str) -> tuple[dict, dict[str, dict]]:
    """Return list + 32 private shards; caller persists shards before promotion."""
    if full.get("tierListView"):
        raise ValueError("Tier publication requires the complete validated payload.")
    prefix = DETAIL_PREFIX + hashlib.sha256(generation.encode("utf-8")).hexdigest() + "/"
    compact = compact_tier_payload(full, details_prefix=prefix)
    artifacts = {f"{prefix}{bucket:02x}.json": {"schemaVersion": 1, "generatedAtUtc": full.get("generatedAtUtc"), "charts": {}} for bucket in range(32)}
    for mode in MODES:
        for row in full.get(mode, []):
            chart_id = str(row["chartId"])
            shard = artifacts[detail_shard_path(prefix, chart_id)]["charts"]
            if chart_id in shard:
                raise ValueError("Duplicate chart ID in tier publication.")
            shard[chart_id] = _without_baselines(row)
    validate_tier_publication(full, compact)
    return compact, artifacts


def validate_tier_publication(full: Mapping[str, Any], compact: Mapping[str, Any]) -> None:
    expected = compact_tier_payload(full, details_prefix=compact.get("tierListView", {}).get("detailsPrefix"))
    if compact != expected:
        raise ValueError("The compact list differs from its validated tier source.")
    body = json.dumps(compact, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    expanded = compact.get("schemaVersion") in (58, 59, 60)
    limit = (MAX_WIDTH_LIST_BYTES if compact.get("schemaVersion") == 60
             else MAX_EXPANDED_LIST_BYTES if expanded else MAX_LIST_BYTES)
    if len(body) > limit:
        raise ValueError("The tier list exceeds its decoded publication size budget.")
    # Level one is conservative relative to the normal HTTP gzip compression.
    if expanded and len(gzip.compress(body, compresslevel=1, mtime=0)) > MAX_COMPRESSED_LIST_BYTES:
        raise ValueError("The expanded tier list exceeds its compressed publication size budget.")
