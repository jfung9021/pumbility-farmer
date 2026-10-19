"""Private, content-addressed recommendation computations; never publication pointers."""
from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
from typing import Any, Mapping
from zipfile import BadZipFile

import pandas as pd

PREFIX = "analysis/private/recommendation-computations/"
CACHE_VERSION = 1


class InvalidComponentCache(ValueError):
    """Optional cross-run computation data is invalid; rebuild from verified inputs."""


def _optional_json(store, path):
    from pumbility_store import PumbilityArtifactIntegrityError
    try:
        return store.get_json(path)
    except (PumbilityArtifactIntegrityError, json.JSONDecodeError):
        return None
    except ValueError as error:
        if str(error) == f"Pumbility artifact {path!r} is not a JSON object.":
            return None
        raise


def _optional_bytes(store, path):
    try:
        return store.get_bytes(path)
    except ValueError as error:
        # The current backend distinguishes these data failures by exact messages.
        # Connection/schema/HTTP failures must still abort, rather than cause refits.
        if str(error) in {
            f"Pumbility binary artifact {path!r} is missing from Storage.",
            f"Pumbility binary artifact {path!r} failed checksum validation.",
        }:
            return None
        raise


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def computation_code_identity() -> str:
    """Conservative source/dependency identity, including serving and normalization."""
    root = Path(__file__).resolve().parent
    names = ("recommendation_component_cache.py", "recommendation_refresh.py",
             "piu_recommendations.py", "piu_misgrade_analyzer.py", "phoenix1_score_overrides.py",
             "phoenix2_pumbility.py", "player_skill_ratings.py", "recommendation_artifacts.py",
             "pumbility_contract.py", "phoenix2_sync.py", "mix_registry.py", "analysis_runtime.py")
    return canonical_digest({
        "sources": {name: hashlib.sha256((root / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                    for name in names},
        "libraries": {name: version(name) for name in ("numpy", "pandas", "scipy")},
    })


def _scalar(value: Any) -> Any:
    # Float hex retains every bit; decimal JSON round-trips of frames can lose bits.
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        return {"float": value.hex()} if math.isfinite(value) else {"float": str(value)}
    if isinstance(value, (bool, int)):
        return value
    if pd.isna(value):
        return {"missing": type(value).__name__}
    raise ValueError("A normalized recommendation input has an unsupported scalar.")


def _frame_digest(frame: pd.DataFrame, columns: tuple[str, ...]) -> str:
    selected = frame.reindex(columns=list(columns))
    hasher = hashlib.sha256()
    hasher.update(json.dumps(list(columns), separators=(",", ":")).encode("utf-8"))
    # Preserve input order: even a harmless-looking reorder may affect floating sums.
    for row in selected.itertuples(index=False, name=None):
        hasher.update(b"\n")
        hasher.update(json.dumps([_scalar(value) for value in row], sort_keys=True,
                                separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"))
    return hasher.hexdigest()


def mode_input_identity(phoenix1_scores, catalog, phoenix2_scores, combined_charts, chart_type):
    from piu_recommendations import _score_response_estimates, SCORE_RESPONSE_FOLDS
    if chart_type not in ("Single", "Double"):
        raise ValueError("The cached recommendation mode is invalid.")
    selected = catalog[catalog["type"] == chart_type]
    ids = set(selected["chartId"].astype(str))
    estimates = _score_response_estimates(combined_charts)
    estimate_digest = None if estimates is None else _frame_digest(
        estimates[estimates["chartId"].astype(str).isin(ids)], ("chartId", "estimatedDifficulty"))
    fields = ("playerId", "chartId", "pumbility", "score", "plate")
    return {
        "version": CACHE_VERSION, "code": computation_code_identity(), "chartType": chart_type,
        "folds": SCORE_RESPONSE_FOLDS,
        "foldAssignment": "sha256-player-id-first-four-big-endian-mod-folds",
        "catalog": _frame_digest(selected, ("chartId", "type", "level")),
        "phoenix1": _frame_digest(phoenix1_scores[phoenix1_scores["chartId"].astype(str).isin(ids)], fields),
        "phoenix2": _frame_digest(phoenix2_scores[phoenix2_scores["chartId"].astype(str).isin(ids)], fields),
        "estimates": estimate_digest,
    }


def _validate_mode(payload: bytes, metadata: Mapping[str, Any], chart_type: str) -> None:
    from piu_recommendations import ScoreResponseModel, SCORE_RESPONSE_FOLDS
    mode = "singles" if chart_type == "Single" else "doubles"
    try:
        restored = ScoreResponseModel.from_npz_bytes(payload)
    except (ValueError, OSError, EOFError, BadZipFile) as error:
        raise InvalidComponentCache("The cached recommendation arrays are invalid.") from error
    if metadata == {"modes": {}} and not (
            restored.full_surfaces or restored.crossfit_surfaces
            or restored.training_player_ids or restored.peer_cohorts):
        return
    if (not isinstance(metadata.get("modes"), Mapping)
            or set(metadata["modes"]) != {mode}
            or metadata.get("crossfitFolds") != SCORE_RESPONSE_FOLDS):
        raise InvalidComponentCache("The cached recommendation metadata has incompatible modes or folds.")
    if (set(restored.full_surfaces) - {mode}
            or set(restored.crossfit_surfaces) != set(range(SCORE_RESPONSE_FOLDS))
            or any(set(surfaces) - {mode} for surfaces in restored.crossfit_surfaces.values())
            or any(not key.startswith(mode + "\x1f") for key in restored.peer_cohorts)):
        raise InvalidComponentCache("The cached recommendation arrays have incompatible modes or folds.")


def load_mode(store, identity):
    key = canonical_digest(identity)
    marker = _optional_json(store, PREFIX + "modes/" + key + ".json")
    if marker is None:
        return None
    if not isinstance(marker, Mapping):
        raise InvalidComponentCache("The cached recommendation computation marker is invalid.")
    path = PREFIX + "modes/" + key + ".npz"
    metadata = marker.get("metadata")
    if (marker.get("version") != CACHE_VERSION or marker.get("identity") != identity
            or marker.get("path") != path or not isinstance(metadata, Mapping)
            or marker.get("metadataSha256") != canonical_digest(metadata)):
        raise InvalidComponentCache("The cached recommendation computation identity is invalid.")
    payload = _optional_bytes(store, path)
    if (payload is None or len(payload) != marker.get("byteSize")
            or hashlib.sha256(payload).hexdigest() != marker.get("sha256")):
        raise InvalidComponentCache("The cached recommendation computation artifact is invalid.")
    _validate_mode(payload, metadata, identity["chartType"])
    return payload, dict(metadata)


def save_mode(store, identity, payload, metadata):
    _validate_mode(payload, metadata, identity["chartType"])
    key = canonical_digest(identity)
    path = PREFIX + "modes/" + key + ".npz"
    store.put_bytes(path, payload, content_type="application/x-npz")
    # Commit marker is last: an interrupted upload is never a cache hit.
    marker_path = PREFIX + "modes/" + key + ".json"
    marker = {
        "version": CACHE_VERSION, "identity": identity, "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(), "byteSize": len(payload),
        "metadata": dict(metadata), "metadataSha256": canonical_digest(metadata),
    }
    store.put_json(marker_path, marker)
    stored = store.get_json(marker_path)
    if not isinstance(stored, Mapping) or stored != marker:
        raise ValueError("The recommendation computation marker changed during persistence.")
    stored_digest = canonical_digest(stored["metadata"])
    if stored_digest != marker["metadataSha256"]:
        store.put_json(marker_path, {**stored, "metadataSha256": stored_digest})


def serving_identity(model: Mapping[str, Any], score_bytes: bytes) -> str:
    ignored = {"generationKey", "generatedAtUtc", "scoreResponseModelPath",
               "numericalModelIdentity", "modelComputedAtUtc", "sourceCheckedAtUtc"}
    return canonical_digest({"version": CACHE_VERSION, "code": computation_code_identity(),
                             "model": {k: v for k, v in model.items() if k not in ignored},
                             "numericSha256": hashlib.sha256(score_bytes).hexdigest()})


def serving_computed_at(store, identity: str, generated_at_utc: str) -> str:
    from phoenix2_sync import parse_utc
    path = PREFIX + "serving/" + identity + ".json"
    saved = _optional_json(store, path) if store is not None else None
    if (isinstance(saved, Mapping) and saved.get("version") == CACHE_VERSION
            and saved.get("identity") == identity and parse_utc(saved.get("computedAtUtc")) is not None):
        return saved["computedAtUtc"]
    if store is not None:
        store.put_json(path, {"version": CACHE_VERSION, "identity": identity,
                              "computedAtUtc": generated_at_utc})
    return generated_at_utc


def player_input_identity(phoenix1_player, phoenix2_player, metadata):
    return canonical_digest({
        "version": CACHE_VERSION, "phoenix1": dict(phoenix1_player),
        "phoenix2": {k: v for k, v in phoenix2_player.items()
                     if k not in {"lastSyncedAtUtc", "lastScoreRecordedAtUtc"}},
        "display": {k: metadata.get(k) for k in ("username", "displayName", "eligibility", "scoreProgress")},
    })
