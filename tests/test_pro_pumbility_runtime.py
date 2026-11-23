from __future__ import annotations

import copy
import unittest

from pro_pumbility_runtime import (
    DEFAULT_PREFIX, METHOD, ProGenerationChangedError, ProPlayerNotFoundError,
    ProUnavailableError, read_pro_index, read_pro_profile,
)


class DictStore:
    def __init__(self, values):
        self.values = values

    def get_json(self, key):
        return copy.deepcopy(self.values.get(key))


class ProRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.key = "a" * 20
        self.generation = "b" * 64
        self.metadata = dict(schemaVersion=2, method=METHOD, mix="Phoenix2", scope="mode-consolidated",
                             generation=self.generation, snapshotAt="2026-11-22T17:33:26Z")
        self.index = {**self.metadata, "players": [{"playerKey": self.key}]}
        self.profile = {**self.metadata, "player": {"playerKey": self.key}}
        self.path = f"{DEFAULT_PREFIX}/generations/{self.generation}/players/{self.key}.json"
        self.store = DictStore({f"{DEFAULT_PREFIX}/index.json": self.index, self.path: self.profile})

    def test_reads_only_the_selected_current_generation(self):
        self.assertEqual(read_pro_index(self.store), self.index)
        self.assertEqual(read_pro_profile(self.store, self.key, self.generation), self.profile)
        with self.assertRaises(ProGenerationChangedError):
            read_pro_profile(self.store, self.key, "c" * 64)

    def test_wrong_mix_scope_and_mismatched_profile_are_unavailable(self):
        for field, value in (("mix", "Phoenix"), ("scope", "singles"),
                             ("method", "pro-pumbility-p80-adaptive-overall-phoenix2-v1"),
                             ("method", "pro-pumbility-p50-adaptive-overall-phoenix2-v1")):
            with self.subTest(field=field):
                self.store.values[self.path] = {**self.profile, field: value}
                with self.assertRaises(ProUnavailableError):
                    read_pro_profile(self.store, self.key)
        self.store.values[self.path] = {**self.profile, "player": {"playerKey": "d" * 20}}
        with self.assertRaises(ProUnavailableError):
            read_pro_profile(self.store, self.key)

    def test_missing_player_and_path_traversal_are_rejected(self):
        with self.assertRaises(ProPlayerNotFoundError):
            read_pro_profile(self.store, "e" * 20)
        with self.assertRaises(ValueError):
            read_pro_profile(self.store, "../snapshot")


if __name__ == "__main__":
    unittest.main()
