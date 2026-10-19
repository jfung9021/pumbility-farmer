"""Optional bounded JSONB shards of exact curve-independent bootstrap profiles.

Each chart retains its own numerical identity. Updating one chart preserves its
unchanged shard neighbours. No profile objects are uploaded to Storage.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import ssl
import time
from functools import lru_cache
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import numpy as np
import requests

PREFIX = "analysis/private/computation-cache/score-profiles/v2/"
SHARD_COUNT = 64
MAX_SHARD_ENTRIES = 128
MAX_SHARD_BYTES = 4 * 1024 * 1024
MAX_PROFILE_BYTES = 128 * 1024


@lru_cache(maxsize=1)
def _recipe_identity() -> str:
    root = Path(__file__).resolve().parent
    source = b"\0".join((root / name).read_bytes().replace(b"\r\n", b"\n")
                         for name in ("scoring_percentile.py", "score_profile_cache.py"))
    return hashlib.sha256(source + np.__version__.encode()).hexdigest()


def _chart_key(chart_id: str) -> str:
    return hashlib.sha256(chart_id.encode()).hexdigest()


def _shard_number(chart_id: str) -> int:
    return int(_chart_key(chart_id)[:2], 16) % SHARD_COUNT


def _json_size(value: dict) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode())


def _is_cache_unavailable(error: Exception) -> bool:
    """Only operational failures belong to the optional-cache circuit breaker."""
    if isinstance(error, (TimeoutError, ConnectionError, ssl.SSLError,
                          requests.exceptions.ConnectionError,
                          requests.exceptions.Timeout, requests.exceptions.HTTPError,
                          requests.exceptions.ChunkedEncodingError)):
        return True
    try:
        import psycopg
    except ImportError:
        pass
    else:
        if isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError)):
            return True
    try:
        from psycopg_pool import PoolTimeout
    except ImportError:
        return False
    return isinstance(error, PoolTimeout)


class ScoreProfileCache:
    """One General-build scope; at most one bounded shard retained in memory.

    Iterate independent chart work with ``ordered_rows`` so each shard is read
    once and flushed once. Original records remain in their original order.
    The first operational failure disables cache I/O for the rest of this scope;
    in-memory bootstrap computation continues with exactly the same recipe.
    """

    def __init__(self, store):
        self.store = store
        self.disabled = store is None
        self._shard = None
        self._entries = {}
        self._dirty = False
        self._started = time.perf_counter()
        self._closed = False
        self.stats = dict(cache_reads=0, cache_writes=0, cache_hits=0, cache_misses=0,
                          cache_bypassed=0, cache_failures=0, cache_corruptions=0,
                          cache_shards=0, artifact_bytes=0,
                          read_duration_ms=0.0, write_duration_ms=0.0)

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        self.close(flush=error_type is None)
        return False

    def ordered_rows(self, rows):
        if self.store is None:
            return rows
        return sorted(rows, key=lambda row: (_shard_number(row["chartId"]),
                                             _chart_key(row["chartId"])))

    def _unavailable(self, error: Exception, operation: str):
        self.disabled = True
        self.stats["cache_failures"] += 1
        from tier_diagnostics import event
        event("score_profile_cache_unavailable", stage="general_profile_cache",
              outcome="bypassed", checkpoint=operation,
              exception_class=type(error).__name__)

    def _payload(self):
        return {"version": 2, "shard": self._shard, "entries": self._entries}

    def _flush(self):
        if self.disabled or not self._dirty:
            return
        payload = self._payload()
        size = _json_size(payload)
        assert size <= MAX_SHARD_BYTES
        started = time.perf_counter()
        self.stats["cache_writes"] += 1
        try:
            self.store.put_json(PREFIX + f"{self._shard:02x}.json", payload)
        except Exception as error:
            if not _is_cache_unavailable(error):
                raise
            self._unavailable(error, "write")
        else:
            self.stats["artifact_bytes"] += size
        finally:
            self.stats["write_duration_ms"] += (time.perf_counter() - started) * 1000
            self._dirty = False

    def _select(self, chart_id: str):
        if self.disabled:
            return
        shard = _shard_number(chart_id)
        if self._shard == shard:
            return
        self._flush()
        self._entries, self._dirty, self._shard = {}, False, shard
        if self.disabled:
            return
        from pumbility_store import PumbilityArtifactIntegrityError
        started = time.perf_counter()
        self.stats["cache_reads"] += 1
        self.stats["cache_shards"] += 1
        try:
            payload = self.store.get_json(PREFIX + f"{shard:02x}.json")
        except (PumbilityArtifactIntegrityError, json.JSONDecodeError):
            self.stats["cache_corruptions"] += 1
            return
        except Exception as error:
            if not _is_cache_unavailable(error):
                raise
            self._unavailable(error, "read")
            return
        finally:
            self.stats["read_duration_ms"] += (time.perf_counter() - started) * 1000
        if payload is None:
            return
        if (isinstance(payload, dict) and payload.get("version") == 2
                and payload.get("shard") == shard
                and isinstance(payload.get("entries"), dict)
                and len(payload["entries"]) <= MAX_SHARD_ENTRIES
                and _json_size(payload) <= MAX_SHARD_BYTES):
            self._entries = payload["entries"]
        else:
            self.stats["cache_corruptions"] += 1

    def profiles(self, ordered: np.ndarray, chart_id: str, *,
                 samples: int, percentiles: tuple[float, ...]) -> np.ndarray:
        values = np.asarray(ordered, dtype="<f8")
        completed = samples // 100 * 100
        seed = int.from_bytes(hashlib.sha256(chart_id.encode()).digest()[:8], "little")
        recipe = {"version": 2, "code": _recipe_identity(), "seed": seed,
                  "samples": samples, "batchSize": 100, "percentiles": list(percentiles),
                  "quantileMethod": "linear", "rng": "numpy-default_rng-PCG64",
                  "scoresSha256": hashlib.sha256(values.tobytes()).hexdigest(),
                  "scoreCount": len(values)}
        identity = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":"),
                                             allow_nan=False).encode()).hexdigest()
        self._select(chart_id)
        key = _chart_key(chart_id)
        if self.disabled:
            self.stats["cache_bypassed"] += 1
        else:
            entry = self._entries.get(key)
            if isinstance(entry, dict) and entry.get("identity") == identity and entry.get("recipe") == recipe:
                cached = _decode_profiles(entry, completed, len(percentiles))
                if cached is not None:
                    self.stats["cache_hits"] += 1
                    return cached
                self.stats["cache_corruptions"] += 1
        self.stats["cache_misses"] += 1
        rng = np.random.default_rng(seed)
        batches = []
        for _ in range(samples // 100):
            resampled = rng.choice(values, size=(100, len(values)), replace=True)
            batches.append(np.quantile(resampled, percentiles, axis=1, method="linear").T)
        profiles = np.concatenate(batches) if batches else np.empty((0, len(percentiles)))
        if not self.disabled:
            output = io.BytesIO()
            np.savez_compressed(output, profiles=profiles)
            body = output.getvalue()
            if len(body) <= MAX_PROFILE_BYTES:
                entry = {"identity": identity, "recipe": recipe,
                         "sha256": hashlib.sha256(body).hexdigest(),
                         "profilesNpz": base64.b64encode(body).decode("ascii")}
                previous = self._entries.pop(key, None)
                if len(self._entries) < MAX_SHARD_ENTRIES:
                    self._entries[key] = entry
                    if _json_size(self._payload()) <= MAX_SHARD_BYTES:
                        self._dirty = True
                    else:
                        self._entries.pop(key)
                        if previous is not None:
                            self._entries[key] = previous
                elif previous is not None:
                    self._entries[key] = previous
        return profiles

    def close(self, *, flush=True):
        if self._closed:
            return
        self._closed = True
        try:
            if flush:
                self._flush()
        finally:
            from tier_diagnostics import event
            event("score_profile_cache_completed", stage="general_profile_cache",
                  outcome="bypassed" if self.disabled else "completed" if flush else "aborted",
                  duration_ms=round((time.perf_counter() - self._started) * 1000, 3),
                  **{name: round(value, 3) if isinstance(value, float) else value
                     for name, value in self.stats.items()})
            self._entries = {}


def _decode_profiles(entry: dict, completed: int, columns: int):
    encoded = entry.get("profilesNpz")
    if not isinstance(encoded, str) or len(encoded) > (MAX_PROFILE_BYTES + 2) // 3 * 4:
        return None
    try:
        body = base64.b64decode(encoded, validate=True)
        if len(body) > MAX_PROFILE_BYTES or hashlib.sha256(body).hexdigest() != entry.get("sha256"):
            return None
        with ZipFile(io.BytesIO(body)) as archive:
            members = archive.infolist()
            if (len(members) != 1 or members[0].filename != "profiles.npy"
                    or members[0].file_size > completed * columns * 8 + 1024):
                return None
        with np.load(io.BytesIO(body), allow_pickle=False) as artifact:
            profiles = artifact["profiles"]
        if (profiles.shape == (completed, columns) and profiles.dtype == np.dtype("float64")
                and np.all(np.isfinite(profiles))):
            return profiles
    except (ValueError, KeyError, OSError, EOFError, BadZipFile, binascii.Error):
        pass
    return None


def bootstrap_profiles(store, ordered: np.ndarray, chart_id: str, *,
                       samples: int, percentiles: tuple[float, ...]) -> np.ndarray:
    """Reuse exact profiles, falling back only for optional cache failures."""
    if isinstance(store, ScoreProfileCache):
        return store.profiles(ordered, chart_id, samples=samples, percentiles=percentiles)
    with ScoreProfileCache(store) as scope:
        return scope.profiles(ordered, chart_id, samples=samples, percentiles=percentiles)
