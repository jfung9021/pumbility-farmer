"""Durable tier computation identities, separate from observation/run metadata.

Raw snapshots and their digests remain in each run's provenance. Only the two
documented synchronization fields below are omitted from numerical lookup keys;
scores (including recordedAt), consent membership, catalog, and all other fields
remain exact. Best-record selection must happen before constructing snapshots.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from canonical_json import canonical_json_sha256

PREFIX = "analysis/private/source-aware-tiers/computations/"


class TierComputationCacheError(ValueError):
    """An optional durable cache entry is incompatible; current parents may rebuild it."""


def snapshot_identity(snapshot: Mapping[str, Any]) -> str:
    value = {key: item for key, item in snapshot.items() if key != "generatedAtUtc"}
    if isinstance(value.get("players"), list):
        value["players"] = [
            {key: item for key, item in row.items() if key != "lastSyncedAtUtc"}
            if isinstance(row, Mapping) else row
            for row in value["players"]
        ]
    return canonical_json_sha256(value)


def input_identity(first, second, *, records, metadata, catalog, manifest_sha256,
                   policy, model_code):
    return {"version": 1, "phoenix1": snapshot_identity(first),
            "phoenix2": snapshot_identity(second),
            "records": canonical_json_sha256(records),
            "metadata": canonical_json_sha256(metadata),
            "catalog": canonical_json_sha256(catalog),
            "officialManifest": manifest_sha256,
            # Retain full policy evidence as well as its numerical semantics.
            # A reason/label edit must not borrow old source provenance.
            "sourcePolicy": canonical_json_sha256(policy), "modelCode": model_code}


def identity_key(identity):
    return canonical_json_sha256(identity)


def index_path(kind: str, identity):
    if kind not in {"general", "pipeline"}:
        raise ValueError("Unknown tier computation cache kind.")
    return PREFIX + kind + "/" + identity_key(identity) + ".json"


def read_index(store, kind: str, identity):
    from pumbility_store import PumbilityArtifactIntegrityError
    try:
        value = store.get_json(index_path(kind, identity))
    except (PumbilityArtifactIntegrityError, json.JSONDecodeError):
        return None
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get("version") != 1
            or value.get("identity") != identity):
        raise TierComputationCacheError("The tier computation cache identity differs.")
    return value


def write_index(store, kind: str, identity, **fields):
    store.put_json(index_path(kind, identity),
                   {"version": 1, "identity": identity, **fields})
