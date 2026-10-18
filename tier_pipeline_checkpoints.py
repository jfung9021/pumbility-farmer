"""Content-addressed private checkpoints for bounded tier publication phases.

The JSON references are small; all large reversible aggregates use private gzip
objects. Both compressed bytes and canonical JSON content are verified on load.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tempfile
from typing import Any, Mapping

PREFIX = "analysis/private/source-aware-tiers/checkpoints/"


def write_checkpoint(store, payload: Mapping[str, Any], *, stage: str,
                     identity: str, predecessor: str | None = None) -> dict:
    with tempfile.TemporaryFile() as staged:
        with gzip.GzipFile(filename="", fileobj=staged, mode="wb", mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8") as writer:
                json.dump(payload, writer, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
        staged.seek(0)
        content = hashlib.sha256()
        with gzip.GzipFile(fileobj=staged, mode="rb") as reader:
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                content.update(chunk)
        staged.seek(0)
        body = staged.read()
    blob_sha = hashlib.sha256(body).hexdigest()
    path = PREFIX + blob_sha + ".json.gz"
    # An identical content-addressed write is idempotent. No mutable artifact
    # path is used for checkpoint payloads, including concurrent retries.
    # The private bucket accepts opaque binary artifacts, including gzip files.
    store.put_bytes(path, body, content_type="application/octet-stream")
    return {"version": 1, "stage": stage, "identity": identity,
            "predecessorSha256": predecessor, "path": path,
            "blobSha256": blob_sha, "payloadSha256": content.hexdigest(),
            "compressedBytes": len(body), "schemaVersion": payload.get("schemaVersion"),
            "validation": "transition-validated-v1"}


def read_checkpoint(store, reference: Mapping[str, Any], *, stage: str | None = None,
                    identity: str | None = None, predecessor: str | None = None) -> dict:
    if (reference.get("version") != 1
            or reference.get("validation") != "transition-validated-v1"
            or reference.get("path") != PREFIX + str(reference.get("blobSha256")) + ".json.gz"
            or (stage is not None and reference.get("stage") != stage)
            or (identity is not None and reference.get("identity") != identity)
            or (predecessor is not None and reference.get("predecessorSha256") != predecessor)):
        raise ValueError("The tier checkpoint has incompatible stage provenance.")
    body = store.get_bytes(reference["path"])
    if body is None or hashlib.sha256(body).hexdigest() != reference["blobSha256"]:
        raise ValueError("The tier checkpoint bytes are missing or corrupt.")
    content = hashlib.sha256()
    with gzip.GzipFile(fileobj=io.BytesIO(body), mode="rb") as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            content.update(chunk)
    if content.hexdigest() != reference.get("payloadSha256"):
        raise ValueError("The tier checkpoint content identity differs.")
    with gzip.GzipFile(fileobj=io.BytesIO(body), mode="rb") as reader:
        result = json.load(reader)
    if not isinstance(result, dict) or result.get("schemaVersion") != reference.get("schemaVersion"):
        raise ValueError("The tier checkpoint schema differs.")
    return result
