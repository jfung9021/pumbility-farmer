"""Rebuild affected tier components from stored scores without recommendation fits."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Mapping

from pumbility_contract import combined_tier_blob_path, phoenix1_snapshot_path, recommendation_blob_path


class TierReleaseBusyError(RuntimeError):
    pass


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def require_tier_release_policy() -> None:
    required = ("PIU_HYBRID_TIERS", "PIU_PUMBILITY_AVERAGE", "PIU_OFFICIAL_CROSS_LEVEL_SCORING",
                "PIU_CROSS_LEVEL_CLEARING", "PIU_TIER_MIDPOINT_COMPRESSION")
    if any(os.getenv(name) != "1" for name in required):
        raise ValueError("The hybrid average tier release policy is not enabled.")


def _require_idle(jobs: Any) -> None:
    active = jobs.active_job_id()
    job = jobs.get(active) if active else None
    if job and job.get("status") in {"queued", "running"}:
        raise TierReleaseBusyError("Wait for the active score refresh to finish before rebuilding tiers.")


def rebuild_submitted_profile(base: Mapping[str, Any], phoenix1: Mapping[str, Any],
                              phoenix2: Mapping[str, Any]) -> dict[str, Any]:
    """Recreate profile inputs from the submitted catalog, skipping recommendation models."""
    from scoring_percentile import build_production_tier_payload

    records = []
    for chart in phoenix2["charts"]:
        mode, level = chart.get("type"), chart.get("level")
        if mode not in ("Single", "Double") or not isinstance(level, (int, float)) or level < 16:
            continue
        prefix = "S" if mode == "Single" else "D"
        records.append({**{key: copy.deepcopy(chart.get(key)) for key in (
            "songName", "noteCount", "imageUrl", "stepArtist", "bpmMin", "bpmMax")},
            "chartId": str(chart["id"]), "type": mode, "mode": "Singles" if mode == "Single" else "Doubles",
            "level": int(level), "folder": f"{prefix}{int(level)}",
            "difficulty": chart.get("difficulty") or f"{prefix}{int(level)}"})
    records.extend(copy.deepcopy(base.get("coop", [])))
    method = base["summary"]["method"]
    metadata = {"modes": copy.deepcopy(base["summary"].get("modes", {})),
                "phoenix1ScoreOverrides": copy.deepcopy(method.get("phoenix1ScoreOverrides", []))}
    return build_production_tier_payload(records, metadata, phoenix1, phoenix2)


def rebuild_tiers_only(store: Any, jobs: Any) -> dict[str, Any]:
    """Publish only the tier pointer plus rollback/audit artifacts; make no API requests."""
    from analysis_runtime import current_snapshot_path
    from cross_level_clearing import clearing_input_identity, current_clearing_catalog
    from hybrid_tiers import build_release_hybrid_tiers
    from official_scoring_sources import canonical_scoring_manifest_sha256
    from phoenix2_sync import sanitize_snapshot
    from pumbility_contract import official_scoring_manifest_path, HYBRID_TIER_SCRIPT_VERSION

    require_tier_release_policy()
    _require_idle(jobs)
    base = store.get_json(combined_tier_blob_path())
    if not isinstance(base, Mapping) or base.get("schemaVersion") not in (44, 48):
        raise ValueError("Tier-only release requires the current compressed tier generation.")
    manifest = store.get_json(official_scoring_manifest_path())
    official_sha = base["summary"]["method"]["officialCrossLevelScoring"]["manifestSha256"]
    if not isinstance(manifest, Mapping) or canonical_scoring_manifest_sha256(manifest) != official_sha:
        raise ValueError("Official Scoring inputs changed; publish their fit before the tier-only release.")
    p1 = store.get_json(phoenix1_snapshot_path())
    p2 = store.get_json(current_snapshot_path("phoenix2"))
    if not isinstance(p1, Mapping) or not isinstance(p2, Mapping):
        raise ValueError("Both cached submitted-score snapshots are required.")
    p1, p2 = sanitize_snapshot(p1, mix="phoenix1"), sanitize_snapshot(p2, mix="phoenix2")
    # Relational snapshots reconstruct entity content without the capture timestamp.
    # Replay that metadata from the published fit before comparing exact input hashes.
    dates = base["summary"]["method"]["crossLevelClearing"].get("sourceDates", {})
    expected_inputs = base["summary"]["method"]["crossLevelClearing"]["inputHashes"]
    for mix, snapshot in (("phoenix1", p1), ("phoenix2", p2)):
        if _hash(snapshot) != expected_inputs.get(mix) and not snapshot.get("generatedAtUtc") and dates.get(mix):
            snapshot["generatedAtUtc"] = dates[mix]
    inputs = clearing_input_identity(p1, p2, current_clearing_catalog(base, p2))
    if base.get("schemaVersion") == 48:
        from pumbility_average import recover_maximum_pumbility
        from hybrid_tiers import validate_hybrid_tiers
        validate_hybrid_tiers(recover_maximum_pumbility(base))
        if base["summary"]["method"]["crossLevelClearing"]["inputHashes"] != inputs:
            raise ValueError("The published hybrid tiers use different submitted inputs.")
        return {"outcome": "tiers-already-current", "schemaVersion": 48, "tierSha256": _hash(base)}
    prior_inputs = base["summary"]["method"]["crossLevelClearing"]["inputHashes"]
    if prior_inputs != inputs:
        raise ValueError("The cached scores changed since the published baseline; finish their tier fit first.")
    recommendations = store.get_json(recommendation_blob_path())
    base_sha, recommendation_sha = _hash(base), _hash(recommendations) if recommendations else None
    submitted = rebuild_submitted_profile(base, p1, p2)
    candidate = build_release_hybrid_tiers(base, submitted, p1, p2)
    generated_at = datetime.now(timezone.utc).isoformat()
    candidate["generatedAtUtc"] = candidate["summary"]["generatedAtUtc"] = generated_at
    candidate["summary"]["scriptVersion"] = HYBRID_TIER_SCRIPT_VERSION + "+tier-only"
    candidate["summary"]["tierArtifactProvenance"] = {
        "operation": "tier-only-hybrid-average", "baseTierSha256": base_sha,
        "baseGeneratedAtUtc": base.get("generatedAtUtc"), "officialScoringFitReused": True,
        "recommendationGenerationUnchanged": recommendations.get("generationKey") if recommendations else None}
    _require_idle(jobs)
    if _hash(store.get_json(combined_tier_blob_path())) != base_sha:
        raise TierReleaseBusyError("The published tier baseline changed during analysis.")
    current_recommendations = store.get_json(recommendation_blob_path())
    if (_hash(current_recommendations) if current_recommendations else None) != recommendation_sha:
        raise TierReleaseBusyError("The recommendation generation changed during tier analysis.")
    audit = {"generatedAtUtc": generated_at, "baseTierSha256": base_sha, "tierSha256": _hash(candidate),
             "schemaVersion": 48, "inputHashes": inputs, "recommendationsSha256": recommendation_sha,
             "officialScoringFitReused": True, "scoreFetches": 0, "recommendationFits": 0}
    store.put_json_bundle({
        f"analysis/private/tier-releases/baselines/{base_sha}.json": dict(base),
        "analysis/private/tier-releases/latest.json": audit,
        combined_tier_blob_path(): candidate})
    return {"outcome": "tiers-rebuilt", **audit,
            "ratedPumbility": sum(row["tierMetrics"]["pumbility"]["estimatedDifficulty"] is not None
                                  for mode in ("singles", "doubles") for row in candidate[mode])}
