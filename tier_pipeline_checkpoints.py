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


class CheckpointValidationError(ValueError):
    """Checkpoint integrity/provenance differs; pinned jobs must fail closed."""


class _HashingWriter(io.RawIOBase):
    def __init__(self, writer, digest):
        self.writer, self.digest = writer, digest

    def writable(self):
        return True

    def write(self, value):
        written = self.writer.write(value)
        self.digest.update(value[:written])
        return written


class _HashingReader:
    def __init__(self, reader, digest):
        self.reader, self.digest = reader, digest

    def read(self, size=-1):
        value = self.reader.read(size)
        self.digest.update(value)
        return value


def write_checkpoint(store, payload: Mapping[str, Any], *, stage: str,
                     identity: str, predecessor: str | None = None) -> dict:
    content = hashlib.sha256()
    with tempfile.TemporaryFile() as staged:
        with gzip.GzipFile(filename="", fileobj=staged, mode="wb", mtime=0) as compressed:
            with io.TextIOWrapper(_HashingWriter(compressed, content), encoding="utf-8") as writer:
                json.dump(payload, writer, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
        staged.seek(0)
        body = staged.read()
    blob_sha = hashlib.sha256(body).hexdigest()
    path = PREFIX + blob_sha + ".json.gz"
    # An identical content-addressed write is idempotent. No mutable artifact
    # path is used for checkpoint payloads, including concurrent retries.
    # The private bucket accepts opaque binary artifacts, including gzip files.
    from tier_diagnostics import event
    event("tier_checkpoint_upload", stage=stage, artifact_bytes=len(body))
    store.put_bytes(path, body, content_type="application/octet-stream")
    return {"version": 1, "stage": stage, "identity": identity,
            "predecessorSha256": predecessor, "path": path,
            "blobSha256": blob_sha, "payloadSha256": content.hexdigest(),
            "compressedBytes": len(body), "schemaVersion": payload.get("schemaVersion"),
            "validation": "transition-validated-v1"}


def verified_checkpoint_bytes(store, reference: Mapping[str, Any], *, stage: str | None = None,
                              identity: str | None = None, predecessor: str | None = None) -> bytes:
    """Check an immutable validated checkpoint without decoding its audit tree."""
    if (reference.get("version") != 1
            or reference.get("validation") != "transition-validated-v1"
            or reference.get("path") != PREFIX + str(reference.get("blobSha256")) + ".json.gz"
            or (stage is not None and reference.get("stage") != stage)
            or (identity is not None and reference.get("identity") != identity)
            or (predecessor is not None and reference.get("predecessorSha256") != predecessor)):
        raise CheckpointValidationError("The tier checkpoint has incompatible stage provenance.")
    try:
        body = store.get_bytes(reference["path"])
    except ValueError as error:
        if str(error) not in {
            f"Pumbility binary artifact {reference['path']!r} is missing from Storage.",
            f"Pumbility binary artifact {reference['path']!r} failed checksum validation.",
        }:
            raise
        raise CheckpointValidationError("The tier checkpoint bytes are missing or corrupt.") from error
    if body is None or hashlib.sha256(body).hexdigest() != reference["blobSha256"]:
        raise CheckpointValidationError("The tier checkpoint bytes are missing or corrupt.")
    return body


def read_checkpoint(store, reference: Mapping[str, Any], *, stage: str | None = None,
                    identity: str | None = None, predecessor: str | None = None) -> dict:
    body = verified_checkpoint_bytes(store, reference, stage=stage, identity=identity,
                                     predecessor=predecessor)
    content = hashlib.sha256()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(body), mode="rb") as reader:
            result = json.load(_HashingReader(reader, content))
    except (json.JSONDecodeError, gzip.BadGzipFile, EOFError, UnicodeDecodeError) as error:
        raise CheckpointValidationError("The tier checkpoint content is corrupt.") from error
    if content.hexdigest() != reference.get("payloadSha256"):
        raise CheckpointValidationError("The tier checkpoint content identity differs.")
    if not isinstance(result, dict) or result.get("schemaVersion") != reference.get("schemaVersion"):
        raise CheckpointValidationError("The tier checkpoint schema differs.")
    return result
