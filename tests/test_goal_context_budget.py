"""Bound context uploads without duplicating the already pinned raw captures."""
import gzip
import json
import random
import unittest
from unittest.mock import patch

from requests import HTTPError

from analysis_runtime import MemoryBlobStore
import production_tiers as tiers
from scoring_targets import SS
from tests.test_production_tiers import context_fixture, fit_for


class LimitedStore(MemoryBlobStore):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit
        self.context_uploads = []

    def put_bytes(self, path, payload, **kwargs):
        if len(payload) > self.limit:
            raise HTTPError("Synthetic private Storage per-object limit")
        value = json.loads(gzip.decompress(payload))
        if "prepared" in value:
            self.context_uploads.append((len(payload), value))
        return super().put_bytes(path, payload, **kwargs)


class GoalContextBudgetTests(unittest.TestCase):
    def test_large_raw_sources_are_pinned_once_and_compact_context_fits(self):
        rng = random.Random(44)
        first, second = ({"opaque": rng.randbytes(32000).hex()} for _ in range(2))
        catalog, policy, merged, prepared = context_fixture()[2:]
        merged = {**merged, "_privateProvenance": {"opaque": rng.randbytes(32000).hex()}}
        context = first, second, catalog, policy, merged, prepared
        old_payload = {"first": first, "second": second, "catalog": catalog,
                       "policy": policy.public(), "policySemanticSha256": policy.semantic_hash(),
                       "merged": merged, "prepared": prepared}
        limit = 90000
        encoded = json.dumps(old_payload, sort_keys=True, separators=(",", ":")).encode()
        self.assertGreater(len(gzip.compress(encoded, mtime=0)), limit)
        store = LimitedStore(limit)
        pinned = tiers.pin_tier_publication_inputs(store, first=first, second=second,
            manifest={}, records=[], metadata={}, generated_at_utc="2026-10-20T00:00:00Z")
        for source in ("official", "submitted"):
            for mode, _ in tiers.MODES:
                boards, _, identity = tiers.cohort(prepared, "ss", source, mode)
                store.put_json(tiers.fit_path(identity), {"identity": identity, "fit": fit_for(boards, SS.score)})
        artifact = {"schemaVersion": 1, "goal": "ss", "targetConfig": SS.public(), "charts": {}, "method": {}}
        with patch("production_tiers.load_context", return_value=context), \
             patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"), \
             patch("target_fit_cache.hydrate_point_context"), \
             patch("target_fit_cache.load_or_fit_target", side_effect=AssertionError("Unexpected numerical fit")), \
             patch("scoring_goal_tiers.build_goal_artifact", return_value=artifact) as builder:
            result = tiers.advance_goal_backfill(store, pinned_inputs_reference=pinned, goal="ss")
        self.assertTrue(result["done"])
        self.assertEqual(len(store.context_uploads), 1)
        size, captured = store.context_uploads[0]
        self.assertLess(size, limit)
        self.assertNotIn("first", captured)
        self.assertNotIn("second", captured)
        self.assertEqual(captured["merged"], {"manifestSha256": merged["manifestSha256"]})
        self.assertEqual(captured["prepared"], prepared)
        self.assertEqual(builder.call_args.kwargs["source_hashes"], {
            "phoenix1": tiers.digest(first), "phoenix2": tiers.digest(second)})
        self.assertFalse(any(path.endswith("/public.json") for path in store.values))


if __name__ == "__main__":
    unittest.main()
