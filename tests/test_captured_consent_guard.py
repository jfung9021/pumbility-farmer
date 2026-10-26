"""Saved canonical import cannot restore a player revoked after capture."""
import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from pumbility_store import JobLeaseLostError, PumbilityArtifactStore, TIER_PUBLICATION_LOCK

CAPTURED = datetime(2026, 10, 26, 1, tzinfo=timezone.utc)
NOW = CAPTURED + timedelta(hours=4)
REVISION = {"sha256": "source", "byteSize": 12, "revision": "v1"}
GUARD = {"capturedAtUtc": CAPTURED.isoformat(), "sourceRevision": REVISION}
IMPORT_GUARD = {**GUARD, "jobId": "analysis-replacement", "bundleId": "b" * 64}
PATH = "analysis/private/phoenix2-current.json"


class CapturedConsentGuardTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.connect = patch("pumbility_store._connect", return_value=self.connection)
        self.connect.start(); self.addCleanup(self.connect.stop)
        self.schema = patch("pumbility_store._assert_schema")
        self.schema.start(); self.addCleanup(self.schema.stop)
        self.store = PumbilityArtifactStore(database_url="postgresql://unused/test")
        self.receipts = {}
        self.current_revision = dict(REVISION)
        self.clock = NOW
        self.revocations = []
        self.cursor.execute.side_effect = self.execute
        self.snapshot = {"schemaVersion": 1, "mix": "Phoenix2", "generatedAtUtc": CAPTURED.isoformat(),
                         "players": [{"playerId": "private-fixture-player"}], "charts": [], "scores": []}
        self.imp = patch("scripts.backfill_pumbility_supabase._import_mix")
        self.importer = self.imp.start(); self.addCleanup(self.imp.stop)
        self.importer.side_effect = self.import_snapshot
        self.put_receipt = patch.object(PumbilityArtifactStore, "_put_json_row", side_effect=self.write_receipt)
        self.receipt_writer = self.put_receipt.start(); self.addCleanup(self.put_receipt.stop)

    def execute(self, query, args=None):
        if "select payload_json" in query:
            value = self.receipts.get(args[0])
            self.cursor.fetchone.return_value = None if value is None else (copy.deepcopy(value),)
        elif "player_consents" in query:
            self.cursor.fetchone.return_value = (any(stamp >= args[0] for stamp in self.revocations), self.clock)
        elif "select sha256,byte_size,xmin" in query:
            value = self.current_revision
            self.cursor.fetchone.return_value = None if value is None else (value["sha256"], value["byteSize"], value["revision"])
        elif query == "select clock_timestamp()":
            self.cursor.fetchone.return_value = (self.clock,)

    def import_snapshot(self, *args):
        self.current_revision = {"sha256": "adopted", "byteSize": 13, "revision": "v2"}
        # The successful canonical import can itself revoke players omitted by
        # this exact snapshot. Its closures precede the atomic receipt time.
        self.revocations.append(self.clock - timedelta(seconds=1))

    def write_receipt(self, cursor, path, value):
        self.receipts[path] = copy.deepcopy(value)

    def seed_allowed(self):
        self.current_revision, self.clock, self.revocations = dict(REVISION), NOW, []

    def put(self, guard=IMPORT_GUARD):
        self.store.put_json(PATH, self.snapshot, captured_recovery_source=guard)

    def queries(self): return [str(call.args[0]) for call in self.cursor.execute.call_args_list]

    def test_prepare_guard_reads_revision_and_revocations_under_shared_import_lock(self):
        self.seed_allowed()
        self.assertEqual(self.store.captured_snapshot_consent_guard(CAPTURED.isoformat()), GUARD)
        self.importer.assert_not_called()
        queries = self.queries()
        lock = next(i for i,q in enumerate(queries) if "pg_advisory_xact_lock" in q)
        consent = next(i for i,q in enumerate(queries) if "player_consents" in q)
        self.assertLess(lock, consent)
        self.assertEqual(self.cursor.execute.call_args_list[lock].args[1], (TIER_PUBLICATION_LOCK,))
        self.assertIn("consent.valid_to >= %s", queries[consent])
        self.assertIn("scope.scope_key='analysis'", queries[consent])
        self.assertIn("mix.mix_key='phoenix2'", queries[consent])
        self.assertTrue(any("for update" in q for q in queries))

    def test_guarded_import_uses_same_connection_and_transaction_after_checks(self):
        self.seed_allowed()
        self.put()
        self.importer.assert_called_once()
        self.assertIs(self.importer.call_args.args[0], self.connection)
        self.connection.transaction.assert_called_once()
        self.assertIn("set local lock_timeout = '5000ms'", self.queries())
        self.assertIn("set local statement_timeout = '30000ms'", self.queries())

    def test_later_local_consent_revocation_fails_before_import(self):
        self.revocations = [CAPTURED + timedelta(seconds=1)]
        with self.assertRaises(JobLeaseLostError): self.put()
        self.importer.assert_not_called()

    def test_current_source_drift_after_prepare_fails_before_import(self):
        self.current_revision["revision"] = "new-revision"
        with self.assertRaises(JobLeaseLostError): self.put()
        self.importer.assert_not_called()

    def test_future_or_missing_capture_time_fails_before_import(self):
        self.clock = CAPTURED - timedelta(seconds=1)
        with self.assertRaises(JobLeaseLostError): self.put()
        self.snapshot.pop("generatedAtUtc")
        with self.assertRaises(ValueError): self.put()
        self.importer.assert_not_called()

    def test_guard_cannot_be_reused_for_a_different_snapshot_cutoff(self):
        self.seed_allowed()
        with self.assertRaises(JobLeaseLostError): self.put({**IMPORT_GUARD, "capturedAtUtc": NOW.isoformat()})
        self.importer.assert_not_called()

    def test_missing_current_revision_is_not_permission_to_import(self):
        self.current_revision = None
        with self.assertRaises(JobLeaseLostError): self.put()
        self.importer.assert_not_called()

    def test_guard_only_applies_to_canonical_phoenix2_import(self):
        for path in ("analysis/private/phoenix1-current.json", "analysis/private/phoenix1.json", "analysis/private/unrelated.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.store.put_json(path, self.snapshot, captured_recovery_source=GUARD)
        self.importer.assert_not_called()

    def test_ordinary_canonical_import_keeps_existing_contract(self):
        self.store.put_json(PATH, self.snapshot)
        self.importer.assert_called_once()
        self.assertFalse(any("player_consents" in q or "pg_advisory_xact_lock" in q for q in self.queries()))

    def test_retry_after_phase_checkpoint_failure_skips_import_after_revision_changed(self):
        self.put()
        self.assertNotEqual(self.current_revision, REVISION)
        self.assertEqual(self.importer.call_count, 1)
        self.assertEqual(len(self.receipts), 1)
        # Simulate losing the phase checkpoint after the import+receipt commit.
        # Own import-created valid_to closures cross the original capture cutoff
        # and must not make this exact committed adoption poison its retry.
        self.assertTrue(any(stamp >= CAPTURED for stamp in self.revocations))
        self.clock += timedelta(seconds=2)
        self.put()
        self.assertEqual(self.importer.call_count, 1)
        self.assertEqual(self.receipt_writer.call_count, 1)
        receipt = next(iter(self.receipts.values()))
        self.assertEqual(receipt["resultingSourceRevision"], self.current_revision)
        self.assertEqual(receipt["originalGuard"], IMPORT_GUARD)
        self.assertEqual(receipt["jobId"], IMPORT_GUARD["jobId"])
        self.assertEqual(receipt["bundleId"], IMPORT_GUARD["bundleId"])

    def test_receipt_does_not_admit_another_job_bundle_snapshot_or_guard(self):
        for change in ("job", "bundle", "snapshot", "guard", "revision"):
            with self.subTest(change=change):
                self.receipts.clear(); self.seed_allowed(); self.importer.reset_mock()
                self.snapshot["scores"] = []
                self.put()
                guard = dict(IMPORT_GUARD)
                if change == "job": guard["jobId"] = "another-job"
                elif change == "bundle": guard["bundleId"] = "e" * 64
                elif change == "snapshot": self.snapshot["scores"] = [{"score": 999999}]
                elif change == "guard": guard["capturedAtUtc"] = NOW.isoformat()
                else: self.current_revision["revision"] = "outside-write"
                with self.assertRaises(JobLeaseLostError): self.put(guard)
                self.assertEqual(self.importer.call_count, 1)

    def test_receipt_retry_rejects_a_new_consent_closure_even_without_revision_bump(self):
        self.put()
        self.clock += timedelta(seconds=2)
        self.revocations.append(self.clock)
        with self.assertRaises(JobLeaseLostError): self.put()
        self.assertEqual(self.importer.call_count, 1)

    def test_receipt_failure_rolls_back_the_canonical_import_transaction(self):
        self.receipt_writer.side_effect = OSError("receipt write interrupted")
        with self.assertRaises(OSError): self.put()
        self.assertIs(self.connection.transaction.return_value.__exit__.call_args.args[0], OSError)

    def test_job_and_bundle_identity_are_required_before_import(self):
        with self.assertRaises(ValueError): self.put(GUARD)
        self.importer.assert_not_called()


if __name__ == "__main__": unittest.main()
