from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import unittest
from unittest.mock import patch

import numpy as np
import requests

from analysis_runtime import MemoryBlobStore
from score_profile_cache import PREFIX, ScoreProfileCache, _chart_key, _shard_number
from scoring_percentile import profile_difficulty_interval


class ScoreProfileCacheTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryBlobStore()
        self.values = np.linspace(921111.25, 998888.75, 37).tolist()
        self.curve = {"difficulties": [18.5, 19.5, 20.5],
                      "profiles": [[990000, 996000, 999000], [980000, 990000, 995000],
                                   [960000, 975000, 985000]]}

    def interval(self, values=None, curve=None, chart_id="S-chart", store=None):
        return profile_difficulty_interval(self.values if values is None else values, chart_id,
                                           self.curve if curve is None else curve,
                                           cache_store=self.store if store is None else store)

    def shard_entry(self, chart_id="S-chart"):
        path = PREFIX + f"{_shard_number(chart_id):02x}.json"
        return self.store.values[path]["entries"][_chart_key(chart_id)]

    def ordered_chart_ids(self):
        # Stable different buckets exercise flush failures before later charts.
        first = "S-chart"
        second = next(f"chart-{index}" for index in range(100)
                      if _shard_number(f"chart-{index}") != _shard_number(first))
        return [first, second]

    def test_cached_profiles_keep_exact_intervals_and_can_project_a_new_curve(self):
        reference = profile_difficulty_interval(self.values, "S-chart", self.curve)
        self.assertEqual(self.interval(), reference)
        revised = copy.deepcopy(self.curve)
        revised["difficulties"] = [18.25, 19.75, 21.0]
        projected = profile_difficulty_interval(self.values, "S-chart", revised)
        with patch("score_profile_cache.np.random.default_rng", side_effect=AssertionError("resampled")):
            self.assertEqual(self.interval(), reference)
            self.assertEqual(self.interval(curve=revised), projected)
            self.assertEqual(self.interval(values=list(reversed(self.values))), reference)
        self.assertNotEqual(reference, projected)
        self.assertFalse(self.store.binary_values)

    def test_changed_score_recipe_or_sample_count_does_not_reuse_old_profiles(self):
        self.interval()
        revised = [self.values[0] + 1, *self.values[1:]]
        with patch("score_profile_cache.np.random.default_rng", side_effect=AssertionError("cache miss")):
            with self.assertRaisesRegex(AssertionError, "cache miss"):
                self.interval(values=revised)
            with patch("score_profile_cache._recipe_identity", return_value="new-code"):
                with self.assertRaisesRegex(AssertionError, "cache miss"):
                    self.interval()
            with patch("scoring_percentile.BOOTSTRAP_SAMPLES", 1100):
                with self.assertRaisesRegex(AssertionError, "cache miss"):
                    self.interval()

    def test_corrupt_and_partial_entries_are_recomputed_from_same_rng(self):
        expected = self.interval()
        for mutate in (lambda entry: entry.update(sha256="wrong"),
                       lambda entry: entry.pop("profilesNpz"),
                       lambda entry: entry.update(profilesNpz="!!invalid base64!!"),
                       lambda entry: entry.update(identity="wrong"),
                       lambda entry: entry["recipe"].update(batchSize=50)):
            with self.subTest(mutation=mutate):
                mutate(self.shard_entry())
                self.assertEqual(self.interval(), expected)

    def test_corrupt_array_shape_dtype_finite_and_archive_are_recomputed(self):
        expected = self.interval()
        for profiles in (np.zeros((999, 3)), np.zeros((1000, 3), dtype=np.float32),
                         np.full((1000, 3), np.nan), np.full((1000, 3), np.inf),
                         np.zeros((1000, 4)), np.zeros((1000, 3), dtype=object)):
            with self.subTest(dtype=str(profiles.dtype), shape=profiles.shape):
                output = io.BytesIO()
                np.savez_compressed(output, profiles=profiles)
                body = output.getvalue()
                self.shard_entry().update(profilesNpz=base64.b64encode(body).decode(),
                                          sha256=hashlib.sha256(body).hexdigest())
                self.assertEqual(self.interval(), expected)
        body = b"not-a-zip"
        self.shard_entry().update(profilesNpz=base64.b64encode(body).decode(),
                                  sha256=hashlib.sha256(body).hexdigest())
        self.assertEqual(self.interval(), expected)

    def test_corrupt_outer_shard_is_a_miss(self):
        expected = self.interval()
        path = next(iter(self.store.values))
        for value in ({"version": 0}, {"version": 2, "shard": -1, "entries": {}},
                      {"version": 2, "shard": _shard_number("S-chart"), "entries": []}):
            self.store.values[path] = value
            self.assertEqual(self.interval(), expected)
        with patch("score_profile_cache.MAX_SHARD_ENTRIES", 1):
            self.store.values[path]["entries"]["extra"] = {}
            self.assertEqual(self.interval(), expected)
        self.assertEqual(len(self.store.values[path]["entries"]), 1)

    def test_unsupported_chart_does_not_sample_or_access_the_cache(self):
        with patch.object(self.store, "get_json", side_effect=AssertionError("cache accessed")):
            self.assertEqual(self.interval(values=self.values[:19]), (None, None))
            self.assertEqual(profile_difficulty_interval(self.values, "S-chart", None,
                                                        cache_store=self.store), (None, None))

    def test_backend_integrity_and_json_errors_rebuild_without_transport_circuit(self):
        from pumbility_store import PumbilityArtifactIntegrityError
        expected = self.interval()
        for error in (PumbilityArtifactIntegrityError(digest_matches=False, byte_size_matches=True),
                      json.JSONDecodeError("broken", "", 0)):
            with patch.object(self.store, "get_json", side_effect=error):
                with ScoreProfileCache(self.store) as scope:
                    self.assertEqual(self.interval(store=scope), expected)
                    self.assertFalse(scope.disabled)
                    self.assertEqual(scope.stats["cache_corruptions"], 1)

    def test_read_ssl_timeout_and_database_unavailability_open_scoped_circuit(self):
        import psycopg
        chart_ids = self.ordered_chart_ids()
        expected = {cid: profile_difficulty_interval(self.values, cid, self.curve) for cid in chart_ids}
        errors = (requests.exceptions.SSLError("private endpoint secret"),
                  requests.exceptions.ReadTimeout("private endpoint secret"),
                  TimeoutError("offline"), ConnectionError("offline"),
                  psycopg.OperationalError("private database secret"),
                  psycopg.InterfaceError("offline"))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with patch.object(self.store, "get_json", side_effect=error) as read, \
                        patch.object(self.store, "put_json") as write:
                    with ScoreProfileCache(self.store) as scope:
                        for cid in chart_ids:
                            self.assertEqual(self.interval(chart_id=cid, store=scope), expected[cid])
                    self.assertEqual(read.call_count, 1)
                    write.assert_not_called()
                    self.assertEqual(scope.stats["cache_failures"], 1)
                    self.assertEqual(scope.stats["cache_bypassed"], 2)
        # A new run has its own circuit and can cache normally again.
        self.assertEqual(self.interval(), expected["S-chart"])
        self.assertTrue(self.store.values)

    def test_write_ssl_or_timeout_returns_result_and_skips_all_later_cache_io(self):
        chart_ids = self.ordered_chart_ids()
        expected = {cid: profile_difficulty_interval(self.values, cid, self.curve) for cid in chart_ids}
        for error in (requests.exceptions.SSLError("private secret"), TimeoutError("offline")):
            with patch.object(self.store, "get_json", wraps=self.store.get_json) as read, \
                    patch.object(self.store, "put_json", side_effect=error) as write:
                with ScoreProfileCache(self.store) as scope:
                    for cid in chart_ids:
                        self.assertEqual(self.interval(chart_id=cid, store=scope), expected[cid])
                self.assertEqual(read.call_count, 1)
                self.assertEqual(write.call_count, 1)
                self.assertTrue(scope.disabled)
        # A one-chart direct caller also survives failure at final flush.
        with patch.object(self.store, "put_json", side_effect=TimeoutError("offline")):
            self.assertEqual(self.interval(), expected["S-chart"])

    def test_programming_and_configuration_errors_still_raise(self):
        import psycopg
        for operation in ("get_json", "put_json"):
            for error in (AssertionError("bug"), TypeError("bug"), ValueError("bug"),
                          requests.exceptions.InvalidURL("bug"),
                          psycopg.ProgrammingError("bug")):
                with self.subTest(operation=operation, error=type(error).__name__):
                    with patch.object(self.store, operation, side_effect=error):
                        with self.assertRaises(type(error)):
                            self.interval()

    def test_one_read_and_write_per_grouped_shard_and_no_storage_objects(self):
        rows = [{"chartId": f"chart-{index}"} for index in range(12)]
        original_order = copy.deepcopy(rows)
        with patch("score_profile_cache.SHARD_COUNT", 2):
            buckets = {_shard_number(row["chartId"]) for row in rows}
            with patch.object(self.store, "get_json", wraps=self.store.get_json) as read, \
                    patch.object(self.store, "put_json", wraps=self.store.put_json) as write, \
                    patch.object(self.store, "get_bytes", side_effect=AssertionError("Storage read")), \
                    patch.object(self.store, "put_bytes", side_effect=AssertionError("Storage write")):
                with ScoreProfileCache(self.store) as scope:
                    results = {row["chartId"]: self.interval(chart_id=row["chartId"], store=scope)
                               for row in scope.ordered_rows(rows)}
                self.assertEqual(read.call_count, len(buckets))
                self.assertEqual(write.call_count, len(buckets))
            with patch.object(self.store, "get_json", wraps=self.store.get_json) as read, \
                    patch.object(self.store, "put_json") as write, \
                    patch("score_profile_cache.np.random.default_rng", side_effect=AssertionError("resampled")):
                with ScoreProfileCache(self.store) as scope:
                    for row in scope.ordered_rows(rows):
                        self.assertEqual(self.interval(chart_id=row["chartId"], store=scope),
                                         results[row["chartId"]])
                self.assertEqual(read.call_count, len(buckets))
                write.assert_not_called()
            changed = rows[0]["chartId"]
            revised = [self.values[0] + 1, *self.values[1:]]
            with patch("score_profile_cache.np.random.default_rng", wraps=np.random.default_rng) as rng:
                with ScoreProfileCache(self.store) as scope:
                    for row in scope.ordered_rows(rows):
                        cid = row["chartId"]
                        self.interval(chart_id=cid, values=revised if cid == changed else None, store=scope)
                self.assertEqual(rng.call_count, 1)
        self.assertEqual(rows, original_order)
        self.assertFalse(self.store.binary_values)

    def test_shard_entry_and_byte_limits_skip_persistence_without_changing_result(self):
        rows = [{"chartId": f"chart-{index}"} for index in range(4)]
        expected = {row["chartId"]: profile_difficulty_interval(self.values, row["chartId"], self.curve)
                    for row in rows}
        with patch("score_profile_cache.SHARD_COUNT", 1), \
                patch("score_profile_cache.MAX_SHARD_ENTRIES", 2):
            with ScoreProfileCache(self.store) as scope:
                for row in scope.ordered_rows(rows):
                    cid = row["chartId"]
                    self.assertEqual(self.interval(chart_id=cid, store=scope), expected[cid])
            self.assertEqual(len(next(iter(self.store.values.values()))["entries"]), 2)
        empty = MemoryBlobStore()
        with patch("score_profile_cache.MAX_SHARD_BYTES", 100):
            self.assertEqual(self.interval(store=empty), profile_difficulty_interval(self.values, "S-chart", self.curve))
        self.assertFalse(empty.values)

    def test_abort_does_not_flush_pending_cache_or_mask_the_original_error(self):
        with patch.object(self.store, "put_json", side_effect=TypeError("secondary")) as write:
            with self.assertRaisesRegex(AssertionError, "original"):
                with ScoreProfileCache(self.store) as scope:
                    self.interval(store=scope)
                    raise AssertionError("original")
            write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
