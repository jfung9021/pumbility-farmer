import copy
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from pro_pumbility_runtime import DEFAULT_PREFIX, METHOD
from scripts.publish_pro_pumbility import publish_bundle


class Store:
    def __init__(self, fail=False):
        self.fail = fail
        self.values = {DEFAULT_PREFIX + "/index.json": {"generation": "previous"}}
        self.writes = []

    def get_json(self, key):
        return copy.deepcopy(self.values.get(key))

    def put_json(self, key, value):
        self.writes.append(key)
        self.values[key] = copy.deepcopy(value)

    def put_json_bundle(self, values):
        if self.fail:
            raise RuntimeError("Staging failed")
        for key, value in values.items():
            self.put_json(key, value)


class ProPublishTests(unittest.TestCase):
    def bundle(self):
        key = "a" * 20
        metadata = {"schemaVersion": 2, "method": METHOD, "scope": "mode-consolidated", "mix": "Phoenix2",
                    "generation": "b" * 64, "snapshotAt": "2026-11-22T17:33:26Z"}
        return {"index": {**metadata, "players": [{"playerKey": key}]},
                "profiles": {key: {**metadata, "player": {"playerKey": key}}}}

    def write_bundle(self, root, value):
        path = Path(root) / "pro.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(value, handle)
        return path

    def test_index_is_published_after_generation_and_read_back(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store()
            result = publish_bundle(self.write_bundle(root, self.bundle()), store)
        self.assertEqual(result["profiles"], 1)
        self.assertEqual(store.writes[-1], DEFAULT_PREFIX + "/index.json")
        self.assertEqual(store.values[store.writes[-1]]["generation"], "b" * 64)

    def test_staging_failure_preserves_the_previous_pointer(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(fail=True)
            with self.assertRaisesRegex(RuntimeError, "Staging failed"):
                publish_bundle(self.write_bundle(root, self.bundle()), store)
        self.assertEqual(store.values[DEFAULT_PREFIX + "/index.json"], {"generation": "previous"})

    def test_mixed_generation_is_rejected_before_writes(self):
        bundle = self.bundle()
        bundle["profiles"]["a" * 20]["generation"] = "c" * 64
        with tempfile.TemporaryDirectory() as root:
            store = Store()
            with self.assertRaises(ValueError):
                publish_bundle(self.write_bundle(root, bundle), store)
        self.assertEqual(store.writes, [])
