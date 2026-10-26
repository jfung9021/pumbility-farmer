"""Validated numerical source identities, independent of capture/check envelopes.

The original manifest remains the immutable audit identity. Numerical reuse keeps
the source epoch, catalogs, scope, identities, observations and availability
exact, but a new successful fetch/check time alone is not a different dataset.
"""
from __future__ import annotations

from typing import Any, Mapping

from canonical_json import canonical_json_sha256
from official_scoring_sources import (
    MIXES, canonical_scoring_manifest_sha256, hydrate_official_scoring_manifest,
    scoring_target_charts,
)

_CHECK_TIMES = {"fetchedAtUtc", "sourceCheckedAtUtc", "checkedAtUtc"}
_REFERENCE_FIELDS = {"path", "sha256", "catalogSha256"} | _CHECK_TIMES
_CAPTURE_ENVELOPE = {"captureProvenance", "frozenSourceSha256", "catalogSha256"} | _CHECK_TIMES


def scoring_source_numeric_identity(manifest: Mapping[str, Any]) -> str:
    """Hash actual validated inputs while retaining the official source epoch.

    Do not accept a raw or edited loaded manifest: revalidate the immutable
    content references before omitting their hashes from the numerical key.
    Catalogs and ordered observations are deliberately not normalized/reordered.
    """
    captures = manifest.get("_captures")
    if (not isinstance(captures, Mapping) or set(captures) != set(MIXES)
            or manifest.get("_manifestSha256") != canonical_scoring_manifest_sha256(manifest)):
        raise ValueError("The official scoring numerical identity requires a validated manifest.")
    references = manifest.get("captures")
    if not isinstance(references, Mapping) or set(references) != set(MIXES):
        raise ValueError("The official scoring numerical capture references are invalid.")
    by_path = {}
    for mix in MIXES:
        reference = references[mix]
        if not isinstance(reference, Mapping) or not isinstance(reference.get("path"), str):
            raise ValueError("The official scoring numerical capture reference is invalid.")
        by_path[reference["path"]] = captures[mix]
    loaded = hydrate_official_scoring_manifest(dict(manifest), lambda path: by_path.get(path))
    if manifest.get("_currentCharts") != scoring_target_charts(loaded["_captures"]["Phoenix2"]["catalog"]):
        raise ValueError("The official scoring numerical catalog differs from its manifest.")
    semantic = {key: value for key, value in manifest.items()
                if not key.startswith("_") and key != "captures" and key not in _CHECK_TIMES}
    semantic["captureReferences"] = {
        mix: {key: value for key, value in references[mix].items() if key not in _REFERENCE_FIELDS}
        for mix in MIXES}
    semantic["captures"] = {}
    for mix, capture in loaded["_captures"].items():
        value = {key: item for key, item in capture.items()
                 if key not in _CAPTURE_ENVELOPE and key != "boards"}
        value["boards"] = {cid: {key: item for key, item in board.items() if key not in _CHECK_TIMES}
                           for cid, board in capture["boards"].items()}
        semantic["captures"][mix] = value
    return canonical_json_sha256({"version": 1, "officialScoringInputs": semantic})
