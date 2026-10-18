import hashlib
import json
import unittest

from canonical_json import canonical_json_sha256


class CanonicalJsonTests(unittest.TestCase):
    def test_streamed_identity_preserves_existing_artifact_and_replay_hashes(self):
        value = {"z": [None, True, 1, 1.0, -.0, 1e-20, "日本語\n\""], "a": {"nested": [2, 3]}}
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        for newline in (False, True):
            expected = hashlib.sha256((encoded + ("\n" if newline else "")).encode("utf-8")).hexdigest()
            self.assertEqual(canonical_json_sha256(value, trailing_newline=newline), expected)
        with self.assertRaises(ValueError):
            canonical_json_sha256({"invalid": float("nan")})
