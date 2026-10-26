"""Saved-input bundles preserve exact sources without touching active work."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import analysis_runtime as runtime
from canonical_json import canonical_json_sha256 as digest
from captured_run_inputs import (
    CapturedRunInputError, PREFIX, load_captured_run_inputs, prepare_captured_run_inputs,
)
from mix_registry import resolve_mix
from official_scoring_sources import import_research_scoring_sources
from pumbility_contract import (
    combined_tier_blob_path, official_scoring_manifest_path, phoenix1_snapshot_path, recommendation_blob_path,
)
from phoenix2_sync import sanitize_snapshot
from production_tiers import PREFIX as TIER_PREFIX
from tier_pipeline_checkpoints import write_checkpoint
from tests.test_official_scoring_sources import A, NEW, OLD, chart, research

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


class CapturedRunInputsTests(unittest.TestCase):
    def setUp(self):
        self.store, self.jobs = runtime.MemoryBlobStore(), runtime.MemoryJobStore()
        self.job_id = "analysis-captured-test"
        self.spec = resolve_mix("phoenix2")
        self.jobs.save(runtime.new_job(self.job_id, NOW))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            research(root / "research")
            loaded = import_research_scoring_sources(root / "research", root / "private/manifest.json")
            self.raw_manifest = {key: value for key, value in loaded.items() if not key.startswith("_")}
            prefix = official_scoring_manifest_path().rsplit("/", 1)[0]
            for mix, reference in self.raw_manifest["captures"].items():
                self.store.put_json(f"{prefix}/{reference['path']}", loaded["_captures"][mix])
            self.store.put_json(official_scoring_manifest_path(), self.raw_manifest)
            self.manifest_hash = loaded["_manifestSha256"]
        job = self.jobs.get(self.job_id)
        job["officialSourceCapture"] = {"status": "completed", "manifestSha256": self.manifest_hash,
            "sourceDates": {"Phoenix": OLD, "Phoenix2": NEW}, "boardCounts": {"Phoenix": 1, "Phoenix2": 1}}
        self.jobs.save(job)
        base = {"schemaVersion": 1, "generatedAtUtc": NOW.isoformat(),
            "players": [{"playerId": "private-player", "username": "Private player", "lastSyncedAtUtc": NOW.isoformat()}],
            "charts": [chart()], "scores": [{"playerId": "private-player", "chartId": A,
                "score": 990000, "pumbility": 100, "isBroken": False, "plate": "FG"}]}
        self.first = sanitize_snapshot({**base, "mix": "Phoenix"}, mix="phoenix1")
        self.second = sanitize_snapshot({**base, "mix": "Phoenix2"}, mix="phoenix2")
        self.store.put_json(phoenix1_snapshot_path(), self.first)
        snapshot = runtime._write_typed_checkpoint_snapshot(self.store, job_id=self.job_id,
            mix_spec=self.spec, snapshot=self.second)
        typed = runtime._write_typed_frame_shards(blob_store=self.store, job_store=self.jobs,
            job_id=self.job_id, mix_spec=self.spec, lease_heartbeat=None,
            frames={name: pd.DataFrame([{"value": index}]) for index, name in enumerate(("baselines", "contributions", "chartResults"))})
        self.payload = {"generatedAtUtc": NOW.isoformat(), "summary": {"method": "original"}}
        prepared = runtime._save_combined_preparation(self.store, job_id=self.job_id, mix_spec=self.spec,
            payload=self.payload, snapshot_reference=snapshot, model_charts=[], slopes={},
            source_hashes={}, snapshot_hashes={"phoenix1": digest(self.first), "phoenix2": digest(self.second)},
            input_sha256="original-inputs", source_guard={"flags": {}, "sources": {}})
        identity = {"phoenix1": digest(self.first), "phoenix2": digest(self.second),
                    "officialManifest": self.manifest_hash, "pipelineCode": "old-producer"}
        self.generation = digest(identity)
        phase = write_checkpoint(self.store, {"policy": {"rules": []}}, stage="phase-inputs-provenance",
            identity=digest({"generation": self.generation, "kind": "provenance"}))
        self.binary_path = phase["path"]
        self.store.put_json(f"{TIER_PREFIX}generations/{self.generation}/state.json", {
            "generation": self.generation, "identity": identity, "phaseInputs": {"provenance": phase},
            "fitReferences": [], "preparedFitInputs": []})
        self.checkpoint_path = runtime.typed_checkpoint_path(self.job_id)
        self.checkpoint = {"schemaVersion": runtime.TYPED_CHECKPOINT_SCHEMA_VERSION,
            "jobId": self.job_id, "mix": "phoenix2", "phase": "analysis", "snapshot": snapshot,
            "config": {"mix": "phoenix2", "bootstrap_samples": 500}, "payload": self.payload,
            "typedShards": typed, "combinedPreparation": prepared,
            "tierProgress": {"generation": self.generation, "ordinal": 0}, "eligiblePlayerCount": 1}
        self.store.put_json(self.checkpoint_path, self.checkpoint)
        self.store.put_json(combined_tier_blob_path(), {"generation": "previous-tier"})
        self.store.put_json(recommendation_blob_path(), {"generationKey": "previous-model"})

    @staticmethod
    def context_loader(store, checkpoint):
        return {"sourceGuard": {}, "arguments": {"cross_level_clearing": True},
                "policyHash": "captured-policy", "recipes": {"producer": "old-producer"}}

    def prepare(self, **overrides):
        return prepare_captured_run_inputs(self.store, self.jobs, source_job_id=self.job_id,
            expected_manifest_sha256=self.manifest_hash, context_loader=overrides.pop("context_loader", self.context_loader),
            source_deployment="dpl_original", now=NOW, **overrides)

    def test_prepare_copies_validated_inputs_and_does_not_change_job_or_sources(self):
        before, job_before = copy.deepcopy(self.store.values), self.jobs.get(self.job_id)
        binary_before = copy.deepcopy(self.store.binary_values)
        with patch("analysis_runtime.PiuScoresClient", side_effect=AssertionError("No upstream")):
            result = self.prepare()
        bundle = load_captured_run_inputs(self.store, result["bundleId"])
        self.assertEqual(self.jobs.get(self.job_id), job_before)
        for path, value in before.items():
            self.assertEqual(self.store.get_json(path), value)
        for path, value in binary_before.items():
            self.assertEqual(self.store.get_bytes(path), value)
        self.assertEqual(bundle["phoenix1"], self.first)
        self.assertEqual(bundle["phoenix2"], self.second)
        self.assertEqual(bundle["rawOfficialManifest"], self.raw_manifest)
        self.assertEqual(bundle["sourceBinaryObjects"][self.binary_path], binary_before[self.binary_path])
        self.assertEqual(bundle["payload"], self.payload)
        self.assertEqual(bundle["typedFrames"]["contributions"], [{"value": 1}])
        self.assertEqual(result["summary"]["sourceDates"], {"Phoenix": OLD, "Phoenix2": NEW})
        self.assertFalse(result["summary"]["upstreamRefreshed"])
        self.assertNotIn("private-player", json.dumps(result))
        self.assertNotIn("PLAYER#1234", json.dumps(result))
        self.assertNotIn("analysis/private/", json.dumps(result))

    def test_original_cleanup_and_source_pointer_changes_do_not_change_loaded_bundle(self):
        result = self.prepare()
        self.store.values.clear()
        for path in list(self.store.binary_values):
            if not path.startswith(PREFIX):
                self.store.binary_values.pop(path)
        bundle = load_captured_run_inputs(self.store, result["bundleId"])
        self.assertEqual(bundle["phoenix2"], self.second)
        self.assertEqual(bundle["officialManifest"]["_manifestSha256"], self.manifest_hash)
        self.assertEqual(bundle["rollback"]["tiers"], {"generation": "previous-tier"})

    def test_missing_snapshot_changed_manifest_or_invalid_shard_fails(self):
        for kind in ("snapshot", "manifest", "shard", "generation"):
            with self.subTest(kind=kind):
                old = copy.deepcopy(self.store.values)
                if kind == "snapshot":
                    self.store.values.pop(self.checkpoint["snapshot"]["pathname"])
                elif kind == "manifest":
                    changed = copy.deepcopy(self.raw_manifest)
                    changed["calibrationVersion"] = "different"
                    self.store.put_json(official_scoring_manifest_path(), changed)
                elif kind == "generation":
                    self.store.values.pop(f"{TIER_PREFIX}generations/{self.generation}/state.json")
                else:
                    descriptor = self.checkpoint["typedShards"]["datasets"]["baselines"]["shards"][0]
                    changed = self.store.get_json(descriptor["pathname"])
                    changed["rows"][0]["value"] += 1
                    self.store.put_json(descriptor["pathname"], changed)
                with self.assertRaises((CapturedRunInputError, RuntimeError, ValueError)):
                    self.prepare()
                self.store.values = old

    def test_input_drift_is_rejected_but_mutable_fit_progress_is_not_an_input(self):
        def drift(store, checkpoint):
            changed = store.get_json(checkpoint["snapshot"]["pathname"])
            changed["snapshot"]["scores"][0]["score"] += 1
            store.put_json(checkpoint["snapshot"]["pathname"], changed)
            return self.context_loader(store, checkpoint)
        before = copy.deepcopy(self.store.values)
        with self.assertRaisesRegex(CapturedRunInputError, "changed during export"):
            self.prepare(context_loader=drift)
        self.store.values = before
        def progress(store, checkpoint):
            current = store.get_json(self.checkpoint_path)
            current["tierProgress"]["ordinal"] = 1
            store.put_json(self.checkpoint_path, current)
            return self.context_loader(store, checkpoint)
        result = self.prepare(context_loader=progress)
        self.assertEqual(load_captured_run_inputs(self.store, result["bundleId"])["phoenix2"], self.second)

    def test_corrupt_bundle_or_transitive_copy_is_rejected(self):
        result = self.prepare()
        path = f"{PREFIX}bundles/{result['bundleId']}.json"
        original = self.store.binary_values[path]
        self.store.binary_values[path] = original + b" "
        with self.assertRaises(CapturedRunInputError):
            load_captured_run_inputs(self.store, result["bundleId"])
        self.store.binary_values[path] = original
        manifest = json.loads(original)
        copied = next(iter(manifest["sourceObjects"].values()))["path"]
        self.store.binary_values[copied] += b" "
        with self.assertRaises(CapturedRunInputError):
            load_captured_run_inputs(self.store, result["bundleId"])

    def test_revision_receipt_drift_and_exhausted_copy_budget_fail_closed(self):
        path = self.checkpoint["snapshot"]["pathname"]
        calls = {}
        def revision(key):
            calls[key] = calls.get(key, 0) + 1
            return {"sha256": digest(self.store.get_json(key)), "revision": 1 if calls[key] == 1 else 2 if key == path else 1}
        with patch.object(self.store, "artifact_revision", side_effect=revision, create=True):
            with self.assertRaisesRegex(CapturedRunInputError, "revision changed"):
                self.prepare()
        with patch("analysis_budget.work_budget_available", return_value=False):
            with self.assertRaisesRegex(CapturedRunInputError, "work budget"):
                self.prepare()

    def test_summary_includes_only_safe_fit_inventory_aggregates(self):
        def context(store, checkpoint):
            return {**self.context_loader(store, checkpoint), "fitInventory": {
                "version": 1, "generation": self.generation, "producer": "retained-goals-v1",
                "privateArtifacts": [{"path": "analysis/private/secret"}],
                "cohorts": [{"goal": "sssplus", "source": "official", "mode": "Single", "target": 995000,
                    "samples": 200, "seed": 20261017, "bootstrapCompleted": 20, "outerComplete": False,
                    "componentIdentity": "a" * 64, "warnings": ["legacy-component"],
                    "path": "analysis/private/secret", "player": "private-player"}]}}
        result = self.prepare(context_loader=context)
        inventory = result["summary"]["fitInventory"]
        self.assertEqual(inventory["cohorts"][0]["bootstrapCompleted"], 20)
        self.assertNotIn("analysis/private", json.dumps(result))
        self.assertNotIn("private-player", json.dumps(result))

    def test_prepared_cohort_metadata_and_verified_bytes_survive_source_cleanup(self):
        fit_identity = {"boards": "captured-boards", "sourceBoards": "captured-sources",
                        "target": 995000, "samples": 200, "seed": 20261017}
        cohort = {"goal": "sssplus", "source": "official", "mode": "Single", "identity": fit_identity}
        expected = digest({"generation": self.generation, **cohort})
        payload = {"boards": {A: {"official-player": 999000}}, "sources": {"Phoenix2": {A: {"official-player": 999000}}}}
        cohort["inputs"] = write_checkpoint(self.store, payload, stage="cohort-inputs", identity=expected)
        path = f"{TIER_PREFIX}generations/{self.generation}/state.json"
        state = self.store.get_json(path)
        state["preparedFitInputs"] = [{**cohort, "reusedOriginal": False}]
        self.store.put_json(path, state)
        result = self.prepare()
        self.store.values.clear()
        self.store.binary_values.pop(cohort["inputs"]["path"])
        bundle = load_captured_run_inputs(self.store, result["bundleId"])
        self.assertEqual(bundle["sourceGeneration"]["preparedFitInputs"], [cohort])
        self.assertIn(cohort["inputs"]["path"], bundle["sourceBinaryObjects"])


if __name__ == "__main__":
    unittest.main()
