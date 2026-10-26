"""A replacement cannot publish after cancellation, owner loss or source drift."""
import unittest
from unittest.mock import MagicMock, patch

from pumbility_contract import combined_tier_blob_path, official_tier_snapshot_path
from pumbility_store import JobLeaseLostError, PumbilityArtifactStore


class CapturedPublicationTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.rowcount = 1
        self.store = PumbilityArtifactStore(database_url="postgresql://unused/test")
        self.connect = patch("pumbility_store._connect", return_value=self.connection)
        self.connect.start(); self.addCleanup(self.connect.stop)
        self.schema = patch("pumbility_store._assert_schema")
        self.schema.start(); self.addCleanup(self.schema.stop)
        self.put = patch.object(PumbilityArtifactStore, "_put_json_row")
        self.putter = self.put.start(); self.addCleanup(self.put.stop)
        self.sources = {"analysis/private/source-revisions/phoenix1.json": {"sha256": "first", "byteSize": 2, "revision": "v1"},
            "analysis/private/source-revisions/phoenix2.json": {"sha256": "consent", "byteSize": 2, "revision": "v2"},
            "analysis/private/official/scoring/manifest.json": {"sha256": "manifest", "byteSize": 3, "revision": "v2"}}
        self.revision = patch("pumbility_store._tier_publication_revisions", return_value=dict(self.sources))
        self.revisions = self.revision.start(); self.addCleanup(self.revision.stop)
        self.guard = {"jobId": "analysis-captured-test", "leaseOwner": "owner", "sourceRevisions": dict(self.sources)}

    def publish(self, guard=True):
        kwargs = {"captured_recovery": self.guard} if guard else {}
        self.store.publish_generation({combined_tier_blob_path(): {"generatedAtUtc": "2026-10-26T04:00:00Z"}},
            generation_key="generation", analysis_run_id="analysis-run", model_generation_id="model-run", **kwargs)

    def queries(self): return [str(call.args[0]) for call in self.cursor.execute.call_args_list]

    def test_owner_and_source_checks_share_pointer_transaction_before_writes(self):
        self.cursor.fetchone.side_effect = [("shadow", "shadow", "generation", "analysis-run"), ("job-uuid",), None]
        self.publish()
        self.putter.assert_called_once()
        queries = self.queries()
        lease = next(i for i,q in enumerate(queries) if "cancellation_requested_at is null" in q)
        promote = next(i for i,q in enumerate(queries) if q.lstrip().startswith("update pumbility.analysis_runs"))
        self.assertLess(lease, promote)
        self.assertIn("for update of job,head", queries[lease])
        self.assertIn("head.name='active:analysis'", queries[lease])
        self.assertIn("job.lease_owner=%s", queries[lease])
        self.assertIn("job.lease_expires_at >= clock_timestamp()", queries[lease])
        self.connection.transaction.assert_called_once()

    def test_cancelled_expired_or_replaced_owner_never_writes_pointers(self):
        for reason in ("cancelled", "expired", "replaced", "changed-head"):
            with self.subTest(reason=reason):
                self.cursor.fetchone.side_effect = [("shadow", "shadow", "generation", "analysis-run"), None]
                with self.assertRaises(JobLeaseLostError): self.publish()
        self.putter.assert_not_called()
        self.assertFalse(any(q.lstrip().startswith(("insert", "update", "delete")) for q in self.queries()))

    def test_changed_frozen_or_official_source_cannot_write_pointers(self):
        for key in self.sources:
            with self.subTest(source=key):
                changed = dict(self.sources); changed[key] = {**changed[key], "revision": "new"}
                self.revisions.return_value = changed
                self.cursor.fetchone.side_effect = [("shadow", "shadow", "generation", "analysis-run"), ("job-uuid",)]
                with self.assertRaises(JobLeaseLostError): self.publish()
        self.putter.assert_not_called()

    def test_optional_high_level_source_is_locked_and_checked(self):
        key = official_tier_snapshot_path()
        self.guard["sourceRevisions"][key] = {"sha256": "high", "byteSize": 4, "revision": "v3"}
        self.cursor.fetchone.side_effect = [("shadow", "shadow", "generation", "analysis-run"), ("job-uuid",), None]
        self.cursor.fetchall.return_value = [(key, "high", 4, "changed")]
        with self.assertRaises(JobLeaseLostError): self.publish()
        self.putter.assert_not_called()
        self.assertTrue(any("object_key=any" in q and "for update" in q for q in self.queries()))

    def test_invalid_guard_or_arbitrary_revision_paths_fail_closed(self):
        self.guard["sourceRevisions"] = {"analysis/private/unrelated": {"revision": "v1"}}
        self.cursor.fetchone.side_effect = [("shadow", "shadow", "generation", "analysis-run")]
        with self.assertRaises(ValueError): self.publish()
        self.putter.assert_not_called()

    def test_ordinary_publication_keeps_legacy_contract_without_new_job_checks(self):
        self.cursor.fetchone.side_effect = [("shadow", "shadow", "generation", "analysis-run"), None]
        self.publish(guard=False)
        self.putter.assert_called_once()
        self.revisions.assert_not_called()
        self.assertFalse(any("cancellation_requested_at" in q for q in self.queries()))


if __name__ == "__main__": unittest.main()
