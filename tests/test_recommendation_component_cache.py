from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

import pandas as pd

from analysis_runtime import MemoryBlobStore
from piu_recommendations import (
    ScoreResponseModel, fit_score_response_model_mode_from_frames,
)
from recommendation_component_cache import (
    PREFIX, canonical_digest, load_mode, mode_input_identity, save_mode,
)
from recommendation_refresh import build_recommendation_model_artifacts, build_recommendation_score_mode_artifact
from tests import test_recommendations as recommendation_tests


def training_frames():
    charts, estimates = [], []
    for chart_type, prefix in (("Single", "s"), ("Double", "d")):
        for index in range(32):
            row = {"chartId": f"{prefix}{index}", "songName": f"{prefix}{index}",
                   "type": chart_type, "level": 18 + index % 4}
            charts.append(row)
            estimates.append({**row, "estimatedDifficulty": 18.2 + index % 4})
    def scores(source):
        return pd.DataFrame([{"playerId": f"{source}-{player}", "chartId": row["chartId"],
                              "pumbility": 420.0 - index - player,
                              "score": 985000 - 500 * index - 100 * player, "plate": "Fair Game"}
                             for player in range(6) for index, row in enumerate(charts)])
    return scores("p1"), pd.DataFrame(charts), scores("p2"), estimates


class RecommendationComponentCacheTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryBlobStore()
        self.frames = training_frames()

    def identity(self, frames=None, mode="Single"):
        return mode_input_identity(*(frames or self.frames), mode)

    def test_persisted_mode_is_exact_across_new_process_and_partial_upload(self):
        p1, catalog, p2, charts = self.frames
        model, metadata = fit_score_response_model_mode_from_frames(p1, catalog, p2, charts, "Single")
        payload = model.to_npz_bytes()
        identity = self.identity()
        self.assertIsNone(load_mode(self.store, identity))
        # A binary without the final manifest is not a committed computation.
        key = canonical_digest(identity)
        self.store.put_bytes(PREFIX + "modes/" + key + ".npz", payload, content_type="application/x-npz")
        self.assertIsNone(load_mode(self.store, identity))
        save_mode(self.store, identity, payload, metadata)
        restored_store = MemoryBlobStore()
        restored_store.values = copy.deepcopy(self.store.values)
        restored_store.binary_values = dict(self.store.binary_values)
        actual, actual_metadata = load_mode(restored_store, identity)
        self.assertEqual(actual, payload)
        self.assertEqual(actual_metadata, metadata)
        self.assertEqual(ScoreResponseModel.from_npz_bytes(actual).to_payload(), model.to_payload())

    def test_mode_invalidation_is_local_and_exact(self):
        first = self.identity()
        frames = copy.deepcopy(self.frames)
        p1, catalog, p2, charts = frames
        p2.loc[p2["chartId"] == "d0", "score"] += 1
        self.assertEqual(self.identity(frames), first)
        self.assertNotEqual(self.identity(frames, "Double"), self.identity(mode="Double"))
        for change in ("score", "pumbility", "plate", "removed-player", "catalog", "estimate", "code"):
            with self.subTest(change=change):
                frames = copy.deepcopy(self.frames)
                p1, catalog, p2, charts = frames
                if change in ("score", "pumbility"):
                    p2[change] = p2[change].astype(float)
                    p2.loc[0, change] += 0.0000000001
                elif change == "plate":
                    p2.loc[0, "plate"] = "Perfect Game"
                elif change == "removed-player":
                    p2.drop(index=p2[p2["playerId"] == "p2-0"].index, inplace=True)
                elif change == "catalog":
                    catalog.loc[0, "level"] += 1
                elif change == "estimate":
                    charts[0]["estimatedDifficulty"] += 1e-12
                if change == "code":
                    with patch("recommendation_component_cache.computation_code_identity", return_value="new-code"):
                        self.assertNotEqual(self.identity(frames), first)
                else:
                    self.assertNotEqual(self.identity(frames), first)

    def test_corrupt_npz_metadata_and_wrong_fold_are_rejected(self):
        model, metadata = fit_score_response_model_mode_from_frames(*self.frames, "Single")
        identity, payload = self.identity(), model.to_npz_bytes()
        save_mode(self.store, identity, payload, metadata)
        marker_path = PREFIX + "modes/" + canonical_digest(identity) + ".json"
        marker = self.store.get_json(marker_path)
        self.store.put_bytes(marker["path"], payload + b"corrupt", content_type="application/x-npz")
        with self.assertRaisesRegex(ValueError, "artifact"):
            load_mode(self.store, identity)
        save_mode(self.store, identity, payload, metadata)
        changed = self.store.get_json(marker_path)
        changed["metadata"]["crossfitFolds"] = 99
        self.store.put_json(marker_path, changed)
        with self.assertRaisesRegex(ValueError, "identity"):
            load_mode(self.store, identity)
        changed["metadataSha256"] = canonical_digest(changed["metadata"])
        self.store.put_json(marker_path, changed)
        with self.assertRaisesRegex(ValueError, "folds"):
            load_mode(self.store, identity)

    def test_builder_reuses_modes_despite_new_sync_times_and_preserves_numerical_bytes(self):
        fixture = recommendation_tests.PlayerRecommendationTests()
        fixture.setUp()
        first = copy.deepcopy(fixture.snapshot)
        p1 = copy.deepcopy(first)
        kwargs = {"combined_charts": fixture.combined, "chart_type": "Single", "component_store": self.store}
        expected = build_recommendation_score_mode_artifact(p1, first, **kwargs)
        first["generatedAtUtc"] = "2026-10-20T00:00:00Z"
        for player in first["players"]:
            player["lastSyncedAtUtc"] = first["generatedAtUtc"]
            player["username"] = "changed-label"
        with patch("recommendation_refresh.fit_score_response_model_mode_from_frames",
                   side_effect=AssertionError("unchanged mode was fitted again")):
            actual = build_recommendation_score_mode_artifact(copy.deepcopy(p1), first, **kwargs)
        self.assertEqual(actual, expected)

    def test_optional_corrupt_cache_rebuilds_but_storage_errors_propagate(self):
        fixture = recommendation_tests.PlayerRecommendationTests()
        fixture.setUp()
        def build():
            return build_recommendation_score_mode_artifact(copy.deepcopy(fixture.snapshot),
                copy.deepcopy(fixture.snapshot), combined_charts=fixture.combined,
                chart_type="Single", component_store=self.store)
        expected = build()
        marker_path = next(path for path in self.store.values
                           if path.startswith(PREFIX + "modes/") and path.endswith(".json"))
        marker = self.store.get_json(marker_path)
        self.store.binary_values.pop(marker["path"])
        with patch("recommendation_refresh.fit_score_response_model_mode_from_frames",
                   wraps=fit_score_response_model_mode_from_frames) as fitted:
            self.assertEqual(build(), expected)
        fitted.assert_called_once()
        with patch.object(self.store, "get_bytes", side_effect=TimeoutError("storage unavailable")), \
             patch("recommendation_refresh.fit_score_response_model_mode_from_frames") as fitted:
            with self.assertRaisesRegex(TimeoutError, "storage unavailable"):
                build()
        fitted.assert_not_called()

    def test_backend_optional_artifact_integrity_errors_are_misses(self):
        from pumbility_store import PumbilityArtifactIntegrityError
        model, metadata = fit_score_response_model_mode_from_frames(*self.frames, "Single")
        identity = self.identity()
        save_mode(self.store, identity, model.to_npz_bytes(), metadata)
        with patch.object(self.store, "get_json", side_effect=PumbilityArtifactIntegrityError(
                digest_matches=False, byte_size_matches=True)):
            self.assertIsNone(load_mode(self.store, identity))
        path = PREFIX + "modes/" + canonical_digest(identity) + ".npz"
        for message in (f"Pumbility binary artifact {path!r} is missing from Storage.",
                        f"Pumbility binary artifact {path!r} failed checksum validation."):
            with patch.object(self.store, "get_bytes", side_effect=ValueError(message)):
                with self.assertRaisesRegex(ValueError, "artifact"):
                    load_mode(self.store, identity)
        with patch.object(self.store, "get_bytes", side_effect=ValueError("invalid database target")):
            with self.assertRaisesRegex(ValueError, "invalid database target"):
                load_mode(self.store, identity)

    def test_full_serving_identity_and_player_inputs_are_separate_from_run_envelope(self):
        fixture = recommendation_tests.PlayerRecommendationTests()
        fixture.setUp()
        def build(snapshot, generation, at, **extra):
            return build_recommendation_model_artifacts(copy.deepcopy(fixture.snapshot), snapshot,
                combined_charts=fixture.combined, phoenix2_slopes={"singles": 10.0},
                generation_key=generation, generated_at_utc=at, component_store=self.store, **extra)
        first = build(fixture.snapshot, "first", "2026-10-19T00:00:00Z")
        snapshot = copy.deepcopy(fixture.snapshot)
        snapshot["generatedAtUtc"] = "2026-10-20T00:00:00Z"
        for player in snapshot["players"]:
            player["lastSyncedAtUtc"] = snapshot["generatedAtUtc"]
        second = build(snapshot, "second", snapshot["generatedAtUtc"])
        self.assertEqual(first[2], second[2])
        self.assertEqual(first[0]["numericalModelIdentity"], second[0]["numericalModelIdentity"])
        self.assertEqual(first[0]["modelComputedAtUtc"], second[0]["modelComputedAtUtc"])
        self.assertNotEqual(first[0]["sourceCheckedAtUtc"], second[0]["sourceCheckedAtUtc"])
        self.assertEqual(first[0]["players"][0]["playerInputIdentity"], second[0]["players"][0]["playerInputIdentity"])
        snapshot["players"][0]["username"] = "new-name"
        renamed = build(snapshot, "renamed", snapshot["generatedAtUtc"])
        self.assertEqual(first[0]["numericalModelIdentity"], renamed[0]["numericalModelIdentity"])
        self.assertNotEqual(first[0]["players"][0]["playerInputIdentity"], renamed[0]["players"][0]["playerInputIdentity"])
        snapshot["scores"][0]["score"] -= 1
        changed = build(snapshot, "changed", snapshot["generatedAtUtc"])
        self.assertNotEqual(first[0]["players"][0]["playerInputIdentity"], changed[0]["players"][0]["playerInputIdentity"])
        # A serving dependency can change without changing the numeric NPZ.
        with patch("recommendation_refresh._recommendation_method", return_value={"changed-policy": True}):
            changed_policy = build(fixture.snapshot, "policy", snapshot["generatedAtUtc"])
        self.assertEqual(first[2], changed_policy[2])
        self.assertNotEqual(first[0]["numericalModelIdentity"], changed_policy[0]["numericalModelIdentity"])


if __name__ == "__main__":
    unittest.main()
