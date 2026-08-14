from __future__ import annotations

import io
import json
import unittest

import numpy as np

from scripts.populate_pumbility_production import (
    NUMERIC_MODEL_ABSOLUTE_TOLERANCE,
    _assert_flags_off,
    _combined_payload_for_active_generation,
    _parity_mismatch_evidence,
    _recommendation_index_for_active_generation,
    _npz_difference_summary,
    _npz_arrays_equal,
    build_parser,
)


class HostedPopulationSafetyTests(unittest.TestCase):
    def test_active_generation_compatibility_removes_only_pending_fields(self) -> None:
        current_combined = {
            "schemaVersion": 3,
            "summary": {
                "scriptVersion": "6.0+combined-tier-v3",
                "method": {"catalog": "same", "whatIfEstimates": {"radius": 3}},
            },
            "singles": [{"chartId": "a", "whatIfEstimates": []}],
            "doubles": [{"chartId": "b", "whatIfEstimates": []}],
        }
        active_combined = {
            "schemaVersion": 2,
            "summary": {
                "scriptVersion": "6.0+combined-tier-v2",
                "method": {"catalog": "same"},
            },
            "singles": [{"chartId": "a"}],
            "doubles": [{"chartId": "b"}],
        }
        self.assertEqual(
            _combined_payload_for_active_generation(current_combined, active_combined),
            active_combined,
        )

        current_index = {
            "schemaVersion": 21,
            "players": [
                {"playerKey": "a", "scoreProgress": {"singles": {"valid": 1}}}
            ],
        }
        active_index = {"schemaVersion": 21, "players": [{"playerKey": "a"}]}
        self.assertEqual(
            _recommendation_index_for_active_generation(current_index, active_index),
            active_index,
        )

    def test_parity_mismatch_evidence_is_aggregate_only(self) -> None:
        actual = {
            "schemaVersion": 3,
            "summary": {"coverage": {"private": "actual"}},
            "singles": [{"chartId": "private-actual"}],
            "doubles": [],
        }
        expected = {
            "schemaVersion": 3,
            "summary": {"coverage": {"private": "expected"}},
            "singles": [{"chartId": "private-expected"}],
            "doubles": [{"chartId": "private-extra"}],
        }
        evidence = _parity_mismatch_evidence(actual, expected, "combined-tier")
        self.assertEqual(evidence["parityRole"], "combined-tier")
        self.assertEqual(evidence["mismatchedFields"], ["summary", "singles", "doubles"])
        self.assertEqual(evidence["mismatchedSummaryFields"], ["coverage"])
        self.assertEqual(evidence["lists"]["singles"]["differingItems"], 1)
        self.assertEqual(evidence["lists"]["doubles"]["differingItems"], 1)
        self.assertNotIn("private", json.dumps(evidence))

    def test_apply_is_explicit_and_bootstrap_default_is_fixed(self) -> None:
        args = build_parser().parse_args([])
        self.assertFalse(args.apply)
        self.assertEqual(args.bootstrap_samples, 500)
        self.assertTrue(build_parser().parse_args(["--apply"]).apply)

    def test_requires_vercel_authoritative_reads_and_write_flags_off(self) -> None:
        _assert_flags_off({"PUMBILITY_DATA_BACKEND": "vercel"})
        _assert_flags_off({"PUMBILITY_DATA_BACKEND": "shadow"})
        _assert_flags_off({"PUMBILITY_DATA_BACKEND": ""})
        for environment in (
            {"PUMBILITY_DATA_BACKEND": "supabase"},
            {"PUMBILITY_DATA_BACKEND": "vercel", "PUMBILITY_SHADOW_STRICT": "true"},
            {
                "PUMBILITY_DATA_BACKEND": "vercel",
                "PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED": "true",
            },
        ):
            with self.assertRaises(RuntimeError):
                _assert_flags_off(environment)

    def test_npz_parity_compares_array_content_not_zip_metadata(self) -> None:
        first = io.BytesIO()
        second = io.BytesIO()
        changed = io.BytesIO()
        np.savez_compressed(first, values=np.asarray([1.0, np.nan]))
        np.savez_compressed(second, values=np.asarray([1.0, np.nan]))
        np.savez_compressed(changed, values=np.asarray([2.0, np.nan]))
        self.assertTrue(_npz_arrays_equal(first.getvalue(), second.getvalue()))
        self.assertFalse(_npz_arrays_equal(first.getvalue(), changed.getvalue()))

        near = io.BytesIO()
        np.savez_compressed(near, values=np.asarray([1.0 + 1e-10, np.nan]))
        self.assertTrue(_npz_difference_summary(first.getvalue(), near.getvalue()))
        self.assertFalse(
            _npz_difference_summary(
                first.getvalue(),
                near.getvalue(),
                absolute_tolerance=NUMERIC_MODEL_ABSOLUTE_TOLERANCE,
            )
        )


if __name__ == "__main__":
    unittest.main()
