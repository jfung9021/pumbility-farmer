from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from pumbility_store import PumbilityArtifactStore, PumbilityJobStore
from pumbility_contract import combined_tier_blob_path
from tier_publication_jobs import TierPublicationConflict


class TierPublicationStoreTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.rowcount = 1
        self.connect = patch('pumbility_store._connect', return_value=self.connection)
        self.connect.start()
        self.addCleanup(self.connect.stop)
        self.schema = patch('pumbility_store._assert_schema')
        self.schema.start()
        self.addCleanup(self.schema.stop)
        self.store = PumbilityArtifactStore(database_url='postgresql://local/test')
        self.expected = {combined_tier_blob_path(): {'sha256': 'previous'}}
        self.revisions = patch('pumbility_store._tier_publication_revisions', return_value=self.expected)
        self.revisions.start()
        self.addCleanup(self.revisions.stop)
        self.put = patch.object(PumbilityArtifactStore, '_put_json_row')
        self.put.start()
        self.addCleanup(self.put.stop)

    def promote(self):
        self.store.promote_tier_publication(job_id='job', lease_owner='owner',
            candidate_path='immutable-candidate', candidate_revision={'sha256': 'candidate', 'byteSize': 17, 'revision': 'v1'},
            expected_revisions=self.expected, audit={'jobId': 'job'}, completed_job={'id': 'job', 'status': 'completed'})

    def queries(self):
        return [str(call.args[0]) for call in self.cursor.execute.call_args_list]

    def test_promotion_locks_checks_and_copies_pointers_in_one_transaction(self):
        self.cursor.fetchone.side_effect = [('lease-job',), ('candidate', 17, 'v1')]
        self.promote()
        queries = self.queries()
        self.assertTrue(any('lease_expires_at >= clock_timestamp()' in q and 'for update' in q for q in queries))
        copies = [q for q in queries if q.lstrip().startswith('insert into pumbility.artifacts')]
        self.assertEqual(len(copies), 2)
        self.assertTrue(all('select %s,media_type,payload_json' in q for q in copies))
        self.assertFalse(any('recommendations/latest' in str(c) for c in self.cursor.execute.call_args_list))
        self.connection.transaction.assert_called_once()
        self.assertTrue(any("status='completed'" in q and 'lease_expires_at >= clock_timestamp()' in q for q in queries))

    def test_missing_lease_cannot_write_any_pointer(self):
        self.cursor.fetchone.return_value = None
        with self.assertRaises(TierPublicationConflict):
            self.promote()
        self.assertFalse(any(q.lstrip().startswith('insert') for q in self.queries()))

    def test_changed_revision_cannot_write_any_pointer(self):
        self.cursor.fetchone.return_value = ('lease-job',)
        self.expected = {combined_tier_blob_path(): {'sha256': 'changed-source'}}
        with self.assertRaises(TierPublicationConflict):
            self.promote()
        self.assertFalse(any(q.lstrip().startswith('insert') for q in self.queries()))

    def test_candidate_replaced_after_validation_cannot_write_any_pointer(self):
        self.cursor.fetchone.side_effect = [('lease-job',), ('candidate', 17, 'another-revision')]
        with self.assertRaises(TierPublicationConflict):
            self.promote()
        self.assertFalse(any(q.lstrip().startswith('insert') for q in self.queries()))

    def test_database_write_failure_exits_transaction_with_exception(self):
        self.cursor.fetchone.side_effect = [('lease-job',), ('candidate', 17, 'v1')]
        def fail_copy(query, *_args):
            if query.lstrip().startswith('insert into pumbility.artifacts'):
                raise ConnectionError('database write interrupted')
        self.cursor.execute.side_effect = fail_copy
        with self.assertRaises(ConnectionError):
            self.promote()
        self.assertIs(self.connection.transaction.return_value.__exit__.call_args.args[0], ConnectionError)
        self.assertFalse(any("status='completed'" in q for q in self.queries()))

    def test_claim_uses_current_payload_under_lock_instead_of_stale_delivery(self):
        jobs = PumbilityJobStore(database_url='postgresql://local/test')
        self.cursor.fetchone.side_effect = [({'id': 'job', 'progressOrdinal': 16, 'stage': 'public'}, 'queued'), ({'claimed': True},)]
        result = jobs.claim_tier_publication({'id': 'job', 'progressOrdinal': 1, 'updatedAtUtc': '2026-10-18T00:00:00Z'})
        self.assertEqual(result['progressOrdinal'], 16)
        self.assertEqual(result['stage'], 'public')
        self.assertIn('for update', self.queries()[0])
        self.assertTrue(any('pumbility.claim_job' in q for q in self.queries()))

    def test_operator_cancellation_fences_the_old_worker_before_any_pointer_write(self):
        jobs = PumbilityJobStore(database_url='postgresql://local/test')
        self.cursor.fetchone.return_value = ('job-uuid', 'running', 'retired-worker')
        cancelled = jobs.save({'id': 'job', 'status': 'failed', 'stage': 'target-fit-sss-submitted-single',
            'cancelRequested': True, 'error': 'retired-tier-producer-replaced', 'retryAllowedAtUtc': None})
        self.assertTrue(cancelled['cancelRequested'])
        cancellations = [call for call in self.cursor.execute.call_args_list if 'pumbility.cancel_job' in str(call.args[0])]
        self.assertEqual(len(cancellations), 1)
        self.assertEqual(cancellations[0].args[1], ('job-uuid',))
        self.cursor.reset_mock()
        self.cursor.fetchone.return_value = None
        with self.assertRaises(TierPublicationConflict):
            self.promote()
        queries = self.queries()
        self.assertTrue(any("status = 'running'" in q and 'cancellation_requested_at is null' in q for q in queries))
        self.assertFalse(any(q.lstrip().startswith('insert') for q in queries))

    def test_failure_cannot_cancel_a_new_lease_owner(self):
        jobs = PumbilityJobStore(database_url='postgresql://local/test')
        self.cursor.fetchone.return_value = None
        with self.assertRaisesRegex(RuntimeError, 'no longer owns'):
            jobs.finish_tier_publication({'id': 'job', 'status': 'failed', 'stage': 'spread'})
        self.assertFalse(any('cancel_job' in q for q in self.queries()))
        self.assertTrue(any('lease_owner=%s' in q and 'lease_expires_at >= clock_timestamp()' in q for q in self.queries()))


if __name__ == '__main__':
    unittest.main()
