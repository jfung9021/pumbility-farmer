"""Durable exact bootstrap profiles, independent of the fitted global curve."""
from __future__ import annotations

import hashlib
import io
import json
from functools import lru_cache
from pathlib import Path
from zipfile import BadZipFile

import numpy as np

PREFIX = "analysis/private/computation-cache/score-profiles/v1/"


@lru_cache(maxsize=1)
def _recipe_identity() -> str:
    root = Path(__file__).resolve().parent
    source = b"\0".join((root / name).read_bytes().replace(b"\r\n", b"\n")
                         for name in ("scoring_percentile.py", "score_profile_cache.py"))
    return hashlib.sha256(source + np.__version__.encode()).hexdigest()


def bootstrap_profiles(store, ordered: np.ndarray, chart_id: str, *,
                       samples: int, percentiles: tuple[float, ...]) -> np.ndarray:
    """Reuse only identical sorted samples, RNG recipe, and quantile semantics.

    Draw the same 100-replicate batches as the uncached implementation. The
    caller also projects in those batches to preserve floating-point behavior.
    Cache corruption is a miss; unavailable storage still raises normally.
    """
    values = np.asarray(ordered, dtype="<f8")
    completed = samples // 100 * 100
    seed = int.from_bytes(hashlib.sha256(chart_id.encode()).digest()[:8], "little")
    recipe = {"version": 1, "code": _recipe_identity(), "seed": seed,
              "samples": samples, "batchSize": 100, "percentiles": list(percentiles),
              "quantileMethod": "linear", "rng": "numpy-default_rng-PCG64",
              "scoresSha256": hashlib.sha256(values.tobytes()).hexdigest(),
              "scoreCount": len(values)}
    identity = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()
    path = PREFIX + identity
    from pumbility_store import PumbilityArtifactIntegrityError
    try:
        reference = store.get_json(path + ".json")
    except (PumbilityArtifactIntegrityError, json.JSONDecodeError):
        reference = None
    if (isinstance(reference, dict) and reference.get("version") == 1
            and reference.get("identity") == identity and reference.get("recipe") == recipe):
        binary_path = path + ".npz"
        try:
            body = store.get_bytes(binary_path)
        except ValueError as error:
            if str(error) not in {
                f"Pumbility binary artifact {binary_path!r} is missing from Storage.",
                f"Pumbility binary artifact {binary_path!r} failed checksum validation.",
            }:
                raise
            body = None
        if isinstance(body, bytes) and hashlib.sha256(body).hexdigest() == reference.get("sha256"):
            try:
                with np.load(io.BytesIO(body), allow_pickle=False) as artifact:
                    profiles = artifact["profiles"]
                if (profiles.shape == (completed, len(percentiles))
                        and profiles.dtype == np.dtype("float64")
                        and np.all(np.isfinite(profiles))):
                    return profiles
            except (ValueError, KeyError, OSError, EOFError, BadZipFile):
                pass
    rng = np.random.default_rng(seed)
    batches = []
    for _ in range(samples // 100):
        resampled = rng.choice(values, size=(100, len(values)), replace=True)
        batches.append(np.quantile(resampled, percentiles, axis=1, method="linear").T)
    profiles = np.concatenate(batches) if batches else np.empty((0, len(percentiles)))
    output = io.BytesIO()
    np.savez_compressed(output, profiles=profiles)
    body = output.getvalue()
    store.put_bytes(path + ".npz", body, content_type="application/octet-stream")
    store.put_json(path + ".json", {"version": 1, "identity": identity, "recipe": recipe,
                                   "sha256": hashlib.sha256(body).hexdigest()})
    return profiles
