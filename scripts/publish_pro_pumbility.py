"""Publish a prevalidated Pro generation before advancing its private index."""
from __future__ import annotations

import gzip
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pro_pumbility_runtime import PLAYER_KEY, _validate_metadata, pro_blob_prefix, read_pro_index, read_pro_profile


def publish_bundle(path: Path, store: Any = None) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        bundle = json.load(handle)
    index = _validate_metadata(bundle["index"])
    profiles = bundle["profiles"]
    if not isinstance(index.get("players"), list) or not index["players"] or not isinstance(profiles, dict):
        raise ValueError("Invalid Pro release bundle.")
    keys = [row.get("playerKey") for row in index["players"]]
    if any(not isinstance(key, str) or not PLAYER_KEY.fullmatch(key) for key in keys) or len(set(keys)) != len(keys) or set(keys) != set(profiles):
        raise ValueError("Pro release profile identities do not match the index.")
    for key, payload in profiles.items():
        _validate_metadata(payload)
        if payload["generation"] != index["generation"] or payload.get("snapshotAt") != index.get("snapshotAt") or payload.get("player", {}).get("playerKey") != key:
            raise ValueError("Pro release profiles must share one generation.")
    if store is None:
        from analysis_runtime import PrivateBlobStore
        store = PrivateBlobStore()
    prefix, generation = pro_blob_prefix(), index["generation"]
    base = f"{prefix}/generations/{generation}"
    writes = [(f"{base}/players/{key}.json", profiles[key]) for key in keys]
    batches = [dict(writes[i:i+100]) for i in range(0, len(writes), 100)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(store.put_json_bundle, batches))
    store.put_json(f"{base}/index.json", index)
    # Validate the stored generation before exposing its current pointer.
    if store.get_json(f"{base}/players/{keys[0]}.json") != profiles[keys[0]]:
        raise RuntimeError("The staged Pro profile failed read-back validation.")
    store.put_json(prefix + "/index.json", index)
    published = read_pro_index(store)
    read_pro_profile(store, keys[0], generation)
    if published["generation"] != generation:
        raise RuntimeError("The Pro index did not advance to the staged generation.")
    return {"generation": generation, "profiles": len(profiles)}
