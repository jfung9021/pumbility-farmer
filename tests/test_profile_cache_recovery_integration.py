from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

import requests

from analysis_runtime import MemoryBlobStore
from scoring_percentile import build_percentile_tier_payload
from tests.test_scoring_percentile import base_records, chart, score
from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint


PROFILE_PREFIX = "analysis/private/computation-cache/score-profiles/"


class CountingProfileStore(MemoryBlobStore):
    def __init__(self, *, fail_read=False, fail_write=False):
        super().__init__()
        self.reads = self.writes = 0
        self.fail_read, self.fail_write = fail_read, fail_write

    def get_json(self, path):
        if path.startswith(PROFILE_PREFIX):
            self.reads += 1
            if self.fail_read:
                raise requests.exceptions.SSLError("injected private transport failure")
        return super().get_json(path)

    def put_json(self, path, value):
        if path.startswith(PROFILE_PREFIX):
            self.writes += 1
            if self.fail_write:
                raise requests.exceptions.SSLError("injected private transport failure")
        return super().put_json(path, value)


class ProfileCacheRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        charts, scores = [], []
        for typ, prefix, baseline in (("Single", "s", 975000), ("Double", "d", 950000)):
            for level in (20, 21, 22):
                for offset in range(5):
                    cid = f"{prefix}{level}-{offset}"
                    charts.append(chart(cid, level, typ))
                    scores.extend(score(f"synthetic-{i}", cid,
                                        baseline - (level - 20) * 12000 + offset * 300
                                        + i * 83.25 + (i % 7) * 20)
                                  for i in range(37))
        self.rows, self.metadata = base_records(charts)
        self.first = {"charts": [], "scores": []}
        self.second = {"charts": charts, "scores": scores}

    def build(self, store=None, second=None):
        return build_percentile_tier_payload(
            self.rows, self.metadata, self.first, second or self.second,
            generated_at_utc="2026-10-20T00:00:00Z", cache_store=store)

    def test_complete_cold_and_warm_payloads_match_uncached_exactly(self):
        original = copy.deepcopy((self.rows, self.metadata, self.first, self.second))
        expected = self.build()
        store = CountingProfileStore()
        with patch("score_profile_cache.SHARD_COUNT", 4), \
             patch.object(store, "put_bytes", side_effect=AssertionError("per-chart HTTPS upload")), \
             patch.object(store, "get_bytes", side_effect=AssertionError("per-chart HTTPS download")):
            self.assertEqual(self.build(store), expected)
            self.assertGreater(store.writes, 0)
            self.assertLessEqual(store.reads, 4)
            self.assertLessEqual(store.writes, 4)
            cold_writes = store.writes
            cold_reads = store.reads
            with patch("score_profile_cache.np.random.default_rng",
                       side_effect=AssertionError("warm run resampled")):
                self.assertEqual(self.build(store), expected)
            self.assertEqual(store.writes, cold_writes)
            self.assertLessEqual(store.reads - cold_reads, 4)
        self.assertEqual((self.rows, self.metadata, self.first, self.second), original)

    def test_changed_score_resamples_only_its_chart_and_reprojects_other_profiles(self):
        store = CountingProfileStore()
        self.build(store)
        changed = copy.deepcopy(self.second)
        changed["scores"][0]["score"] += 123.25
        expected = self.build(second=changed)
        import numpy as np
        with patch("score_profile_cache.np.random.default_rng", wraps=np.random.default_rng) as rng:
            self.assertEqual(self.build(store, second=changed), expected)
        self.assertEqual(rng.call_count, 1)

    def test_cache_transport_failures_preserve_entire_payload_and_open_circuit(self):
        expected = self.build()
        for failure in ("read", "write"):
            with self.subTest(operation=failure):
                store = CountingProfileStore(fail_read=failure == "read",
                                             fail_write=failure == "write")
                self.assertEqual(self.build(store), expected)
                if failure == "read":
                    self.assertEqual((store.reads, store.writes), (1, 0))
                else:
                    self.assertEqual(store.writes, 1)

    def test_required_checkpoint_storage_failures_still_raise(self):
        store = MemoryBlobStore()
        error = requests.exceptions.SSLError("injected private transport failure")
        with patch.object(store, "put_bytes", side_effect=error):
            with self.assertRaises(requests.exceptions.SSLError) as caught:
                write_checkpoint(store, {"schemaVersion": 57}, stage="general", identity="test")
        self.assertIs(caught.exception, error)
        reference = write_checkpoint(store, {"schemaVersion": 57}, stage="general", identity="test")
        with patch.object(store, "get_bytes", side_effect=error):
            with self.assertRaises(requests.exceptions.SSLError) as caught:
                read_checkpoint(store, reference)
        self.assertIs(caught.exception, error)
