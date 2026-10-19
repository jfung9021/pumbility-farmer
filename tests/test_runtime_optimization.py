from __future__ import annotations

import copy
import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from analysis_runtime import (
    MemoryBlobStore, MemoryJobStore, _canonical_json_sha256, _resume_typed_analysis_checkpoint,
    _verified_snapshot_reference, _write_typed_checkpoint_snapshot, publish_recommendation_model_artifacts,
    TYPED_CHECKPOINT_DATABASE_SHARDS_PHASE, TYPED_CHECKPOINT_SCHEMA_VERSION,
)
from mix_registry import resolve_mix
from pumbility_contract import recommendation_index_path, recommendation_model_path
from pumbility_store import PumbilityArtifactStore, _canonical_json_bytes


class ReceiptStore(MemoryBlobStore):
    def __init__(self):
        super().__init__()
        self.revisions = {}
        self.json_reads = []

    def put_json(self, path, value):
        super().put_json(path, value)
        self.revisions[path] = self.revisions.get(path, 0) + 1

    def get_json(self, path):
        self.json_reads.append(path)
        return super().get_json(path)

    def typed_snapshot_receipt(self, path):
        value = self.values.get(path)
        if value is None:
            return None
        return {"artifactSha256": _canonical_json_sha256(value),
                "byteSize": len(_canonical_json_bytes(value)), "revision": str(self.revisions[path]),
                "snapshotSha256": value["sha256"], "schemaVersion": str(value["schemaVersion"]),
                "jobId": value["jobId"], "mix": value["mix"]}


class SnapshotReceiptTests(unittest.TestCase):
    def setUp(self):
        self.store = ReceiptStore()
        self.spec = resolve_mix("phoenix2")
        self.checkpoint = {"jobId": "receipt-test", "mix": "phoenix2"}
        self.reference = _write_typed_checkpoint_snapshot(self.store, job_id="receipt-test", mix_spec=self.spec,
            snapshot={"mix": "Phoenix2", "players": [], "charts": [], "scores": []})

    def verify(self, reference=None):
        return _verified_snapshot_reference(self.store, checkpoint=self.checkpoint,
            reference=reference or self.reference, mix_spec=self.spec)

    def test_matching_receipt_does_not_download_snapshot(self):
        self.assertTrue(self.verify())
        self.assertEqual(self.store.json_reads, [])

    def test_rewrite_delete_or_reference_change_fails_closed(self):
        path = self.reference["pathname"]
        original = self.store.values[path]
        self.store.put_json(path, original)
        with self.assertRaisesRegex(ValueError, "revision changed"):
            self.verify()
        self.store.delete(path)
        with self.assertRaisesRegex(ValueError, "revision changed"):
            self.verify()
        for field, value in (("sha256", "0" * 64), ("pathname", "another-job")):
            bad = {**self.reference, field: value}
            with self.assertRaises(ValueError):
                self.verify(bad)

    def test_legacy_reference_uses_full_validation_path(self):
        self.assertFalse(self.verify({key: value for key, value in self.reference.items() if key != "artifactReceipt"}))
        self.assertFalse(_verified_snapshot_reference(MemoryBlobStore(), checkpoint=self.checkpoint,
            reference=self.reference, mix_spec=self.spec))

    def test_later_database_phase_passes_no_scores_to_metadata_only_persistence(self):
        class StopAfterPersistence(Exception):
            pass

        checkpoint = {**self.checkpoint, "schemaVersion": TYPED_CHECKPOINT_SCHEMA_VERSION,
            "snapshot": self.reference, "phase": TYPED_CHECKPOINT_DATABASE_SHARDS_PHASE,
            "config": {"mix": "phoenix2"}, "payload": {"generatedAtUtc": "2026-10-20T00:00:00Z"},
            "eligiblePlayerCount": 0, "analysisRunId": "run", "databaseCursor": {"dataset": 0, "shard": 0}}
        manifest = {"datasets": {key: {"shards": [], "shardCount": 0, "rowCount": 0}
                                 for key in ("baselines", "contributions", "chartResults")}}
        self.store.persist_typed_generation = Mock(side_effect=StopAfterPersistence)
        with patch("analysis_runtime._typed_manifest", return_value=manifest), \
             patch("analysis_runtime._audit_checkpoint_resume", side_effect=lambda store, path, value: value), \
             patch("analysis_runtime._load_typed_checkpoint_snapshot", side_effect=AssertionError("downloaded score tree")):
            with self.assertRaises(StopAfterPersistence):
                _resume_typed_analysis_checkpoint(checkpoint, blob_store=self.store, job_store=MemoryJobStore(),
                    job_id="receipt-test", mix_spec=self.spec, staging_path="staging", checkpoint_path="checkpoint",
                    lease_heartbeat=None, yield_after_checkpoint=True)
        self.assertEqual(self.store.persist_typed_generation.call_args.kwargs["snapshot"], {})
        self.assertEqual(self.store.persist_typed_generation.call_args.kwargs["phase"], "analysis-finish")


class ArtifactBatchTests(unittest.TestCase):
    def test_item_and_byte_limits_create_bounded_transactions_with_singleton_oversize(self):
        store = PumbilityArtifactStore(database_url="postgresql://localhost/local")
        connections = []

        def connect(_):
            connection = MagicMock()
            connections.append(connection)
            return connection

        payloads = {"one": {"v": 1}, "two": {"v": 2}, "large": {"v": "x" * 100}, "three": {"v": 3}}
        with patch("pumbility_store._connect", side_effect=connect), patch("pumbility_store._assert_schema"), \
             patch.object(store, "_put_json_row") as write, patch("tier_diagnostics.event"):
            store.put_json_batch(payloads, max_items=2, max_bytes=16)
        self.assertEqual(len(connections), 3)
        self.assertEqual([call.args[1] for call in write.call_args_list], list(payloads))
        self.assertEqual(write.call_args_list[0].args[0], write.call_args_list[1].args[0])
        self.assertNotEqual(write.call_args_list[1].args[0], write.call_args_list[2].args[0])
        for call, payload in zip(write.call_args_list, payloads.values()):
            body = _canonical_json_bytes(payload)
            self.assertEqual(call.kwargs["metadata"], (hashlib.sha256(body).hexdigest(), len(body)))

    def test_each_jsonb_normalized_return_still_sets_integrity_metadata(self):
        store = PumbilityArtifactStore(database_url="postgresql://localhost/local")
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [({"value": 100000000000000000000},), ({"value": 0.0},)]
        normalized = ({"value": 100000000000000000000}, {"value": 0.0})
        with patch("pumbility_store._connect", return_value=connection), patch("pumbility_store._assert_schema"), \
             patch.dict("sys.modules", {"psycopg.types.json": SimpleNamespace(Jsonb=lambda value: value)}), \
             patch("tier_diagnostics.event"):
            store.put_json_batch({"one": {"value": 1e20}, "two": {"value": -0.0}})
        updates = [call for call in cursor.execute.call_args_list if "update pumbility.artifacts" in call.args[0]]
        self.assertEqual(len(updates), 2)
        for call, path, payload in zip(updates, ("one", "two"), normalized):
            body = _canonical_json_bytes(payload)
            self.assertEqual(call.args[1], (hashlib.sha256(body).hexdigest(), len(body), path))

    def test_failure_remains_strict_and_invalid_limits_write_nothing(self):
        store = PumbilityArtifactStore(database_url="postgresql://localhost/local")
        with patch("pumbility_store._connect") as connect, patch("tier_diagnostics.event"):
            for limits in ({"max_items": 0}, {"max_bytes": 0}):
                with self.assertRaises(ValueError):
                    store.put_json_batch({"one": {}}, **limits)
            connect.assert_not_called()
            connect.side_effect = TimeoutError("private database failure")
            with self.assertRaises(TimeoutError):
                store.put_json_batch({"one": {}})


class BatchedPublicationTests(unittest.TestCase):
    def store(self, *, fail=False):
        class BatchStore(MemoryBlobStore):
            def __init__(self):
                super().__init__()
                self.batches = []
                self.markers = []

            def put_json_batch(self, payloads):
                self.batches.append(dict(payloads))
                if fail:
                    raise TimeoutError("private batch error")
                self.put_json_bundle(payloads)

            def put_json(self, path, value):
                self.markers.append(path)
                return super().put_json(path, value)

        return BatchStore()

    def publish(self, store):
        publish_recommendation_model_artifacts(store, index={"generationKey": "test"}, model={"numeric": 1},
            score_model_bytes=b"model", phoenix1_shards=[{"rows": [n]} for n in range(10)], phoenix2_shards=[],
            index_path="latest", artifact_manifest={"sections": {}})

    def test_batches_are_bounded_and_index_is_committed_last(self):
        store = self.store()
        self.publish(store)
        self.assertEqual([len(batch) for batch in store.batches], [8, 3])
        self.assertIn(recommendation_model_path("test"), store.batches[0])
        self.assertEqual(store.markers, [recommendation_index_path("test"), "latest"])

    def test_failed_batch_does_not_publish_any_index(self):
        store = self.store(fail=True)
        with self.assertRaises(TimeoutError):
            self.publish(store)
        self.assertEqual(store.markers, [])
        self.assertIsNone(store.get_json("latest"))


class RuntimeArtifactBenchmarkTests(unittest.TestCase):
    def test_fixed_synthetic_benchmark_proves_operation_counts_without_a_timing_assertion(self):
        from scripts.benchmark_runtime_artifacts import benchmark
        result = benchmark(iterations=2, players_per_folder=2)
        self.assertTrue(result["synthetic"])
        self.assertFalse(result["productionTiming"])
        self.assertTrue(result["verifiedPayloadParity"])
        self.assertGreater(result["scoreRows"], 1000)
        self.assertEqual(result["fullValidation"]["snapshotReads"], 2)
        self.assertGreater(result["fullValidation"]["decodedBytes"], 0)
        self.assertEqual(result["verifiedReference"]["snapshotReads"], 0)
        self.assertEqual(result["verifiedReference"]["receiptReads"], 2)
        self.assertEqual(result["verifiedReference"]["decodedBytes"], 0)
