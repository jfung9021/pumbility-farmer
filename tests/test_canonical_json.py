import hashlib
import json
import math
from pathlib import Path
import random
import tracemalloc
import unittest

from canonical_json import BLOCK_BYTES, _canonical_blocks, canonical_json_sha256


def encoder():
    return json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def legacy_digest(value, *, trailing_newline=False):
    hasher = hashlib.sha256()
    for chunk in encoder().iterencode(value):
        hasher.update(chunk.encode("utf-8"))
    if trailing_newline:
        hasher.update(b"\n")
    return hasher.hexdigest()


class CanonicalJsonTests(unittest.TestCase):
    def test_streamed_identity_preserves_existing_artifact_and_replay_hashes(self):
        value = {"z": [None, True, 1, 1.0, -.0, 1e-20, "日本語\n\""], "a": {"nested": [2, 3]}}
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        for newline in (False, True):
            expected = hashlib.sha256((encoded + ("\n" if newline else "")).encode("utf-8")).hexdigest()
            self.assertEqual(canonical_json_sha256(value, trailing_newline=newline), expected)
        with self.assertRaises(ValueError):
            canonical_json_sha256({"invalid": float("nan")})

    def assert_exact_bytes(self, value):
        expected = "".join(encoder().iterencode(value)).encode("utf-8")
        blocks = list(_canonical_blocks(value, encoder(), set()))
        self.assertEqual(b"".join(blocks), expected)
        self.assertTrue(all(len(block) <= BLOCK_BYTES for block in blocks))
        for newline in (False, True):
            self.assertEqual(canonical_json_sha256(value, trailing_newline=newline),
                             legacy_digest(value, trailing_newline=newline))

    def test_float_integer_unicode_and_key_contracts_are_exact(self):
        values = [None, False, True, 0, -1, 2**4095, 1.0, -0.0, 1e-320,
                  math.nextafter(0., 1.), 1e20, 1e21, 1e-5, 1e-6, 1e-7,
                  1.7976931348623157e308, -2.2250738585072014e-308,
                  math.nextafter(1., 2.), 1.2345678901234567,
                  "日本語 😀 é e\u0301 \u2028\u2029", "\x00\x1f\n\r\t\\\"/"]
        for value in (values, (values, {"reverse": values, "a": None}),
                      {3: "three", -1: "negative", 0: "zero"},
                      {2.5: "fraction", -0.0: "zero", 1e-7: "exponent"},
                      {None: "null"}, {True: "true", False: "false"}):
            with self.subTest(kind=type(value).__name__):
                self.assert_exact_bytes(value)

    def test_large_containers_and_strings_use_bounded_identical_blocks(self):
        rows = [{"chart": str(index), "value": index / 7, "flags": [True, None, -0.0],
                 "text": "日本語😀\n\\\""} for index in range(3000)]
        for value in (rows, {str(index): row for index, row in enumerate(rows)},
                      {"largeString": "日本語😀\x00\n\\\"" * 20_000},
                      {"long-" + "\x00" * 20_000: "value"}):
            self.assert_exact_bytes(value)

    def test_ordered_random_nested_json_matches_legacy(self):
        rng = random.Random(20261020)
        leaves = [None, False, True, 1, 1.0, -0.0, "日本語😀", 3.25, 1e-40]
        def nested(depth):
            if not depth:
                return rng.choice(leaves)
            if rng.randrange(2):
                return [nested(depth-1) for _ in range(rng.randrange(2, 8))]
            keys = [f"key-{index}" for index in range(rng.randrange(2, 8))]
            rng.shuffle(keys)
            return {key: nested(depth-1) for key in keys}
        for _ in range(6):
            self.assert_exact_bytes(nested(5))

    def test_custom_supported_json_subclasses_keep_legacy_behavior(self):
        class Text(str):
            def __str__(self):
                return "must-not-use-this-override"
        class Integer(int):
            def __repr__(self):
                return "must-not-use-this-override"
        class Float(float):
            def __repr__(self):
                return "must-not-use-this-override"
        class Rows(list):
            pass
        for value in (Rows([Integer(7), Float(1.0), Text("underlying-text")]),
                      {Text("key"): "x" * BLOCK_BYTES}, {Integer(3): "x" * BLOCK_BYTES}):
            self.assertEqual(canonical_json_sha256(value), legacy_digest(value))

    def test_invalid_values_preserve_exception_type_and_message(self):
        circular_list = []; circular_list.append(circular_list)
        circular_dict = {}; circular_dict["self"] = circular_dict
        values = [float("nan"), float("inf"), float("-inf"),
                  {"later": [1, float("nan")]}, {float("inf"): 1},
                  {"x": object()}, {"x": b"bytes"}, {("tuple",): 1}, {1: 1, "x": 2},
                  circular_list, circular_dict, {"surrogate": "\ud800"},
                  {"large": "x" * (2*BLOCK_BYTES) + "\udfff"}]
        for value in values:
            try:
                legacy_digest(value)
            except Exception as expected:
                with self.subTest(error=type(expected).__name__), self.assertRaises(type(expected)) as actual:
                    canonical_json_sha256(value)
                self.assertEqual(str(actual.exception), str(expected))
            else:
                self.fail("The invalid-value fixture unexpectedly serialized.")

    def test_complete_historical_fixture_hashes_remain_identical(self):
        directory = Path(__file__).parent / "fixtures"
        for filename in ("sss-target-variants.json", "sssplus-target-tiers.json", "sssplus-median-tiers.json",
                         "sssplus-spread.json", "combined-official-clearing.json", "source-aware-width.json",
                         "submitted-sssplus-calibration.json", "scoring-goal-overlays.json", "sss-tail-policy.json"):
            with self.subTest(fixture=filename):
                value = json.loads((directory / filename).read_text(encoding="utf-8"))
                self.assertEqual(canonical_json_sha256(value), legacy_digest(value))

    def test_serialization_workspace_stays_bounded_as_input_grows(self):
        # Allocate inputs before tracing; measure only serialization workspace.
        # Shared immutable rows are legal JSON aliases, not circular references.
        row = {"values": list(range(128)), "text": "日本語😀\x00" * 20}
        small, large = [row] * 64, [row] * 8192
        peaks = []
        for value in (small, large):
            tracemalloc.start()
            try:
                canonical_json_sha256(value)
                peaks.append(tracemalloc.get_traced_memory()[1])
            finally:
                tracemalloc.stop()
        self.assertLess(max(peaks), 1024 * 1024)
        self.assertLess(peaks[1] - peaks[0], 256 * 1024)
