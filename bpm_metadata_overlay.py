"""Versioned metadata views over retained numerical artifacts; no model refits."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from song_bpm_metadata import BPM_POLICY, bpm_metadata_identity, enrich_chart_bpm


def validate_bpm_policy(policy):
    if policy not in (None, "", BPM_POLICY):
        raise ValueError("bpmPolicy must be focal-v1 when specified.")
    return policy == BPM_POLICY


def overlay_bpm_payload(payload: Mapping, *, mix="Phoenix2") -> dict:
    """Keep the numerical generation and explicitly identify its metadata view."""
    identity = bpm_metadata_identity()

    def visit(value):
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, Mapping):
            return value
        result = {key: (deepcopy(child) if key.endswith("Baseline") else visit(child))
                  for key, child in value.items()}
        if result.get("songName") and result.get("type") in ("Single", "Double", "CoOp"):
            result = enrich_chart_bpm(result, mix=mix)
        return result

    result = visit(payload)
    previous = payload.get("bpmMetadata", {})
    result["bpmMetadata"] = {
        **identity,
        "baseGeneratedAtUtc": previous.get("baseGeneratedAtUtc", payload.get("generatedAtUtc")),
    }
    summary = result.get("summary")
    if isinstance(summary, dict):
        method = summary.setdefault("method", {})
        publication = method.get("productionTierPublication")
        if isinstance(publication, dict) and "publicPayloadSha256" in publication:
            from production_tiers import public_digest
            if payload["summary"]["method"]["productionTierPublication"]["publicPayloadSha256"] != public_digest(payload):
                raise ValueError("The base tier artifact failed its publication identity.")
            result["bpmMetadata"]["basePublicPayloadSha256"] = previous.get(
                "basePublicPayloadSha256", publication["publicPayloadSha256"])
            method["bpmMetadata"] = dict(result["bpmMetadata"])
            publication["publicPayloadSha256"] = public_digest(result)
        else:
            method["bpmMetadata"] = dict(result["bpmMetadata"])
    return result


def legacy_bpm_payload(payload: Mapping) -> dict:
    """Deliberate legacy serializer for old exact-key chart DTO consumers."""
    hidden = {"bpmFocal", "songType", "bpmMetadata", "bpmMetadataVersion", "bpmPolicy", "bpmMetadataRevision"}

    def visit(value):
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, Mapping):
            return {key: visit(child) for key, child in value.items() if key not in hidden}
        return value

    result = visit(payload)
    publication = result.get("summary", {}).get("method", {}).get("productionTierPublication")
    if isinstance(publication, dict) and "publicPayloadSha256" in publication:
        from production_tiers import public_digest
        publication["publicPayloadSha256"] = public_digest(result)
    return result


def select_bpm_payload(payload: Mapping, policy=None, *, mix="Phoenix2") -> dict:
    return overlay_bpm_payload(payload, mix=mix) if validate_bpm_policy(policy) else legacy_bpm_payload(payload)
