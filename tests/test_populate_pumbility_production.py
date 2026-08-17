from __future__ import annotations

import io
import json
import unittest

import numpy as np

from scripts.populate_pumbility_production import (
    NUMERIC_MODEL_ABSOLUTE_TOLERANCE,
    _assert_flags_off,
    _assert_recommendation_live_drift,
    _assert_recommendation_model_live_drift,
    _assert_versioned_index_timestamp_variance,
    _combined_payload_for_active_generation,
    _parity_mismatch_evidence,
    _recommendation_index_for_active_generation,
    _npz_difference_summary,
    _npz_arrays_equal,
    build_parser,
)


class HostedPopulationSafetyTests(unittest.TestCase):
    def _recommendation_index(self, player_keys: list[str]) -> dict[str, object]:
        shard_size = 2
        return {
            "schemaVersion": 21,
            "storageSchemaVersion": 3,
            "generationKey": "generation",
            "modelGeneratedAtUtc": "2026-08-15T00:00:00Z",
            "generatedAtUtc": "2026-08-15T00:00:00Z",
            "modelPath": "model.json",
            "refreshSupported": True,
            "method": {
                "catalog": "same",
                "pumbilityPerLevel": {"singles": 1.0},
                "scoreProjectionCoverage": {"players": len(player_keys)},
            },
            "players": [
                {
                    "playerKey": key,
                    "username": key,
                    "inputShard": offset // shard_size,
                }
                for offset, key in enumerate(player_keys)
            ],
            "inputShardCount": (len(player_keys) + shard_size - 1) // shard_size,
            "inputShardSize": shard_size,
        }

    def test_live_recommendation_drift_is_bounded_and_structure_preserving(self) -> None:
        active = self._recommendation_index(["a", "b", "c"])
        candidate = self._recommendation_index(["a", "b", "d", "e"])
        evidence = _assert_recommendation_live_drift(candidate, active)
        self.assertEqual(evidence["playerCountDifference"], 1)
        self.assertEqual(evidence["playerKeySetDifferenceCount"], 3)

        active_model = {
            "generationKey": "generation",
            "catalog": ["same"],
            "recommendationCharts": ["same"],
            "phoenix2Slopes": {"singles": 1.0},
            "scoreProjectionMetadata": {"players": 3},
            "plateModel": {"players": 3},
            "method": active["method"],
        }
        candidate_model = {
            **active_model,
            "catalog": ["live"],
            "recommendationCharts": ["live"],
            "phoenix2Slopes": {"singles": 2.0},
            "scoreProjectionMetadata": {"players": 4},
            "plateModel": {"players": 4},
            "method": candidate["method"],
        }
        _assert_recommendation_model_live_drift(candidate_model, active_model)

    def test_live_recommendation_drift_rejects_contract_changes(self) -> None:
        active = self._recommendation_index(["a", "b"])
        changed = self._recommendation_index(["a", "b"])
        changed["schemaVersion"] = 22
        with self.assertRaises(RuntimeError):
            _assert_recommendation_live_drift(changed, active)

        excessive = self._recommendation_index(
            [chr(ord("a") + offset) for offset in range(11)]
        )
        with self.assertRaises(RuntimeError):
            _assert_recommendation_live_drift(excessive, self._recommendation_index(["z"]))

    def test_versioned_index_accepts_only_bounded_timestamp_variance(self) -> None:
        active = self._recommendation_index(["a", "b"])
        versioned = json.loads(json.dumps(active))
        versioned["modelGeneratedAtUtc"] = "2026-08-15T01:00:00Z"
        versioned["generatedAtUtc"] = "2026-08-15T01:00:00Z"
        self.assertEqual(
            _assert_versioned_index_timestamp_variance(active, versioned), 3600
        )

        changed = json.loads(json.dumps(versioned))
        changed["players"][0]["username"] = "different"
        with self.assertRaises(RuntimeError):
            _assert_versioned_index_timestamp_variance(active, changed)

        too_old = json.loads(json.dumps(active))
        too_old["modelGeneratedAtUtc"] = "2026-08-13T00:00:00Z"
        too_old["generatedAtUtc"] = "2026-08-13T00:00:00Z"
        with self.assertRaises(RuntimeError):
            _assert_versioned_index_timestamp_variance(active, too_old)

    def test_active_generation_requires_adjacent_what_if_schema(self) -> None:
        current_combined = {
            "schemaVersion": 9,
            "summary": {
                "scriptVersion": "6.0+combined-tier-v9",
                "method": {"catalog": "same", "whatIfEstimates": {"radius": 1}},
            },
            "singles": [{"chartId": "a", "whatIfEstimates": []}],
            "doubles": [{"chartId": "b", "whatIfEstimates": []}],
            "coop": [],
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
        active_v3 = {
            "schemaVersion": 3,
            "summary": {
                "scriptVersion": "6.0+combined-tier-v3",
                "method": {"catalog": "same", "whatIfEstimates": {"radius": 3}},
            },
            "singles": [{"chartId": "a", "whatIfEstimates": []}],
            "doubles": [{"chartId": "b", "whatIfEstimates": []}],
        }
        self.assertEqual(
            _combined_payload_for_active_generation(current_combined, current_combined),
            current_combined,
        )
        for legacy_payload in (active_combined, active_v3):
            with self.assertRaisesRegex(RuntimeError, "adjacent-level What-if"):
                _combined_payload_for_active_generation(current_combined, legacy_payload)
        active_v4 = {
            "schemaVersion": 4,
            "summary": {"scriptVersion": "6.0+combined-tier-v4"},
            "singles": [],
            "doubles": [],
            "coop": [],
        }
        with self.assertRaisesRegex(RuntimeError, "adjacent-level What-if"):
            _combined_payload_for_active_generation(current_combined, active_v4)

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

    def test_recommendation_index_evidence_is_aggregate_only(self) -> None:
        actual = {
            "schemaVersion": 21,
            "method": {"catalog": "private-actual", "unknown": "private"},
            "players": [
                {
                    "playerKey": "private-key-actual",
                    "username": "private-actual",
                    "eligibility": {"singles": True},
                    "inputShard": 0,
                }
            ],
        }
        expected = {
            "schemaVersion": 21,
            "method": {"catalog": "private-expected"},
            "players": [
                {
                    "playerKey": "private-key-expected",
                    "username": "private-expected",
                    "eligibility": {"singles": False},
                    "inputShard": 0,
                }
            ],
        }
        evidence = _parity_mismatch_evidence(actual, expected, "recommendation-index")
        self.assertEqual(evidence["mismatchedFields"], ["method", "players"])
        self.assertEqual(evidence["mismatchedMethodFields"], ["catalog"])
        self.assertEqual(
            evidence["playerFieldDifferenceCounts"],
            {"playerKey": 1, "username": 1, "eligibility": 1},
        )
        self.assertEqual(evidence["lists"]["players"]["differingItems"], 1)
        self.assertEqual(
            evidence["lists"]["players"]["playerKeySetDifferenceCount"], 2
        )
        self.assertNotIn("private-", json.dumps(evidence))

    def test_apply_is_explicit_and_bootstrap_default_is_fixed(self) -> None:
        args = build_parser().parse_args([])
        self.assertFalse(args.apply)
        self.assertFalse(args.pinned_model_only)
        self.assertEqual(args.bootstrap_samples, 500)
        self.assertTrue(build_parser().parse_args(["--apply"]).apply)
        self.assertTrue(
            build_parser()
            .parse_args(["--apply", "--pinned-model-only"])
            .pinned_model_only
        )

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
