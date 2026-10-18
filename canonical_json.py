"""Canonical artifact hashes without allocating a full serialized copy."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_sha256(value: Any, *, trailing_newline: bool = False) -> str:
    hasher = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    for chunk in encoder.iterencode(value):
        hasher.update(chunk.encode("utf-8"))
    if trailing_newline:
        hasher.update(b"\n")
    return hasher.hexdigest()
