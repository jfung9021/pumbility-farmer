from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

import numpy as np

from analysis_runtime import MemoryBlobStore
from scoring_percentile import profile_difficulty_interval


class ScoreProfileCacheTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryBlobStore()
        self.values = np.linspace(921111.25, 998888.75, 37).tolist()
        self.curve = {"difficulties": [18.5, 19.5, 20.5],
                      "profiles": [[990000, 996000, 999000], [980000, 990000, 995000],
                                   [960000, 975000, 985000]]}

    def interval(self, values=None, curve=None):
        return profile_difficulty_interval(self.values if values is None else values, "S-chart",
                                           self.curve if curve is None else curve,
                                           cache_store=self.store)

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

    def test_corrupt_and_partial_artifacts_are_recomputed_from_same_rng(self):
        expected = self.interval()
        path = next(path for path in self.store.binary_values if "score-profiles" in path)
        self.store.binary_values[path] += b"corrupt"
        self.assertEqual(self.interval(), expected)
        self.store.binary_values.pop(path)
        self.assertEqual(self.interval(), expected)
        manifest = next(path for path in self.store.values if "score-profiles" in path)
        self.store.values[manifest]["version"] = 0
        self.assertEqual(self.interval(), expected)

    def test_unsupported_chart_does_not_sample_or_access_the_cache(self):
        with patch.object(self.store, "get_json", side_effect=AssertionError("cache accessed")):
            self.assertEqual(self.interval(values=self.values[:19]), (None, None))
            self.assertEqual(profile_difficulty_interval(self.values, "S-chart", None,
                                                        cache_store=self.store), (None, None))

    def test_backend_integrity_errors_rebuild_but_timeouts_propagate(self):
        from pumbility_store import PumbilityArtifactIntegrityError
        expected = self.interval()
        with patch.object(self.store, "get_json", side_effect=PumbilityArtifactIntegrityError(
                digest_matches=False, byte_size_matches=True)):
            self.assertEqual(self.interval(), expected)
        path = next(path for path in self.store.binary_values if "score-profiles" in path)
        for message in (f"Pumbility binary artifact {path!r} is missing from Storage.",
                        f"Pumbility binary artifact {path!r} failed checksum validation."):
            with patch.object(self.store, "get_bytes", side_effect=ValueError(message)):
                self.assertEqual(self.interval(), expected)
        with patch.object(self.store, "get_bytes", side_effect=TimeoutError("offline")):
            with self.assertRaisesRegex(TimeoutError, "offline"):
                self.interval()


if __name__ == "__main__":
    unittest.main()
