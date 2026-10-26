from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import Mock, patch

from analysis_runtime import MemoryBlobStore, MemoryJobStore
from tier_publication_jobs import (CONTINUATION_FIELD, TierPublicationConflict,
    _retryable_io, execute_tier_publication_job, get_tier_publication_status, request_tier_publication)


class Jobs(MemoryJobStore):
    def __init__(self):
        super().__init__()
        self.latest_tier = None
        self.active_tier = None
        self.lease_owner = 'worker-a'
        self.owner = None
        self.expired = False

    def save(self, payload):
        with self._lock:
            value = super().save(payload)
            if value.get('status') == 'failed' and value.get('cancelRequested'):
                self.owner = None
            return value

    def request_tier_publication(self, payload):
        with self._lock:
            old = self.get(self.active_tier) if self.active_tier else None
            if old and old['status'] in ('running', 'queued'):
                return old, False
            self.latest_tier = self.active_tier = payload['id']
            return self.save(payload), True

    def tier_publication_job_id(self):
        return self.latest_tier

    def claim_tier_publication(self, payload):
        with self._lock:
            if self.owner and self.owner != self.lease_owner and not self.expired:
                return False
            self.owner = self.lease_owner
            self.expired = False
            return self.save({**self.get(payload['id']), 'status': 'running'})

    def handoff_continuation(self, job_id, payload):
        self.save({**payload, 'status': 'queued'})
        self.owner = None

    def finish_tier_publication(self, payload):
        value = self.save(payload)
        self.owner = None
        self.active_tier = None
        return value

    def retry_tier_publication(self, payload):
        self.owner = None
        return self.save({**payload, 'status': 'queued', 'completedAtUtc': None})


class Store(MemoryBlobStore):
    def __init__(self, jobs):
        super().__init__()
        self.jobs = jobs
        self.revisions = {'tier': {'sha256': 'previous'}, 'source': {'revision': 'source-1'}}
        self.promotions = 0
        self.reject_promotion = False
        from production_tiers import RETAINED_VERSION
        self.put_json('candidate', {'schemaVersion': 61, 'summary': {'method': {
            'retainedGoalProducer': {'producer': 'retained-goals-v1'},
            'productionTierPublication': {'method': RETAINED_VERSION,
                'inputs': {'producer': 'retained-goals-v1'}}}}})
        self.last_promotion = None

    def tier_publication_revisions(self):
        return copy.deepcopy(self.revisions)

    def artifact_revision(self, path):
        return {'sha256': 'candidate', 'revision': '1'} if self.get_json(path) else None

    def promote_tier_publication(self, **kwargs):
        with self._lock:
            if (self.reject_promotion or kwargs['expected_revisions'] != self.revisions
                    or self.jobs.owner != kwargs['lease_owner'] or self.jobs.expired):
                raise TierPublicationConflict('changed')
            self.last_promotion = copy.deepcopy(kwargs)
            self.values['tier'] = self.get_json(kwargs['candidate_path'])
            self.jobs.finish_tier_publication(kwargs['completed_job'])
            self.promotions += 1


class TierPublicationJobTests(unittest.TestCase):
    def setUp(self):
        self.jobs = Jobs()
        self.store = Store(self.jobs)
        self.policy = patch('tier_release.require_tier_release_policy')
        self.policy.start()
        self.addCleanup(self.policy.stop)
        self.events = patch('tier_diagnostics.event')
        self.events.start()
        self.addCleanup(self.events.stop)
        self.errors = patch('tier_diagnostics.report_exception')
        self.errors.start()
        self.addCleanup(self.errors.stop)

    def request(self, enqueue=None):
        return request_tier_publication(enqueue or Mock(), jobs=self.jobs, store=self.store)[1]

    def ready(self, **overrides):
        return {'done': True, 'stage': 'ready', 'schemaVersion': 61,
                'producer': 'retained-goals-v1', 'completedTargetFits': 4, 'candidatePath': 'candidate',
                'candidateSha256': 'candidate', **overrides}

    def pin(self):
        job = self.request()
        with patch('tier_publication_jobs._pin_inputs', return_value=({'inputs': 'immutable', 'producer': 'retained-goals-v1'}, self.store.tier_publication_revisions())):
            result = execute_tier_publication_job(job['id'], jobs=self.jobs, store=self.store)
        return job['id'], result

    def test_concurrent_triggers_share_one_job_and_enqueue_without_source_reads(self):
        enqueue = Mock()
        self.store.tier_publication_revisions = Mock(side_effect=AssertionError('HTTP trigger read sources'))
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.request(enqueue), range(8)))
        self.assertEqual(len({r['id'] for r in results}), 1)
        enqueue.assert_called_once()

    def legacy_job(self, *, status='running', operation=None):
        result = self.request()
        job = self.jobs.get(result['id'])
        job.pop('producer', None)
        job.update(status=status, stage='target-fit-sss-submitted-single')
        if operation is not None:
            job['operation'] = operation
        self.jobs.save(job)
        self.jobs.owner = 'retired-worker' if status == 'running' else None
        return job['id']

    def test_legacy_running_or_queued_job_is_cancelled_before_replacement_enqueue(self):
        for status in ('running', 'queued'):
            with self.subTest(status=status):
                self.jobs, self.store = Jobs(), None
                self.store = Store(self.jobs)
                old_id = self.legacy_job(status=status)
                events = []
                save = self.jobs.save
                def record_save(payload):
                    if payload.get('cancelRequested'):
                        events.append(('cancel', payload['id']))
                    return save(payload)
                def enqueue(new_id):
                    old = self.jobs.get(old_id)
                    self.assertEqual(old['status'], 'failed')
                    self.assertTrue(old['cancelRequested'])
                    self.assertIsNone(old['retryAllowedAtUtc'])
                    self.assertEqual(old['error'], 'retired-tier-producer-replaced')
                    self.assertEqual(self.jobs.get(new_id)['producer'], 'retained-goals-v1')
                    events.append(('enqueue', new_id))
                with patch.object(self.jobs, 'save', side_effect=record_save), \
                     patch.object(self.jobs, 'request_tier_publication', wraps=self.jobs.request_tier_publication) as requests:
                    result = self.request(enqueue)
                self.assertNotEqual(result['id'], old_id)
                self.assertEqual(result['outcome'], 'tier-publication-queued')
                self.assertEqual(events, [('cancel', old_id), ('enqueue', result['id'])])
                self.assertEqual(requests.call_count, 2)
                self.assertIsNone(self.jobs.owner)

    def test_failed_or_unconfirmed_legacy_cancellation_does_not_request_replacement(self):
        for failure in ('write-failed', 'unconfirmed'):
            with self.subTest(failure=failure):
                self.jobs, self.store = Jobs(), None
                self.store = Store(self.jobs)
                old_id = self.legacy_job()
                enqueue = Mock()
                def failed_save(payload):
                    if failure == 'write-failed':
                        raise RuntimeError('Cancellation failed')
                    return {**payload, 'cancelRequested': False}
                with patch.object(self.jobs, 'save', side_effect=failed_save), \
                     patch.object(self.jobs, 'request_tier_publication', wraps=self.jobs.request_tier_publication) as requests:
                    with self.assertRaises(RuntimeError):
                        self.request(enqueue)
                self.assertEqual(requests.call_count, 1)
                enqueue.assert_not_called()
                self.assertEqual(self.jobs.get(old_id)['status'], 'running')
                self.assertEqual(self.jobs.owner, 'retired-worker')
                self.assertEqual(self.store.promotions, 0)

    def test_current_running_or_queued_duplicate_is_not_cancelled_or_requeued(self):
        for status in ('running', 'queued'):
            with self.subTest(status=status):
                self.jobs, self.store = Jobs(), None
                self.store = Store(self.jobs)
                original = self.request()
                self.jobs.save({**self.jobs.get(original['id']), 'status': status})
                enqueue = Mock()
                with patch.object(self.jobs, 'save', side_effect=AssertionError('Current job was mutated')):
                    result = self.request(enqueue)
                self.assertEqual(result['id'], original['id'])
                self.assertEqual(result['outcome'], 'tier-publication-active')
                self.assertEqual(result['status'], status)
                enqueue.assert_not_called()

    def test_legacy_backfill_duplicate_is_not_cancelled_by_normal_tier_request(self):
        old_id = self.legacy_job(operation='scoring-goal-backfill')
        enqueue = Mock()
        with patch.object(self.jobs, 'save', side_effect=AssertionError('Backfill job was cancelled')):
            result = self.request(enqueue)
        self.assertEqual(result['id'], old_id)
        enqueue.assert_not_called()

    def test_cancelled_legacy_worker_cannot_promote_after_handoff(self):
        old_id = self.legacy_job()
        self.store.put_json('tier', {'previous': True})
        self.store.put_json('recommendations', {'generation': 'unchanged'})
        before = copy.deepcopy(self.store.values)
        result = self.request()
        self.assertNotEqual(result['id'], old_id)
        with self.assertRaises(TierPublicationConflict):
            self.store.promote_tier_publication(job_id=old_id, lease_owner='retired-worker',
                candidate_path='candidate', candidate_revision={'sha256': 'candidate'},
                expected_revisions=self.store.tier_publication_revisions(),
                audit={'jobId': old_id}, completed_job={'id': old_id, 'status': 'completed'})
        self.assertEqual(self.store.values, before)
        self.assertEqual(self.store.promotions, 0)
        self.assertEqual(self.jobs.get(old_id)['status'], 'failed')

    def test_new_job_uses_retained_producer_and_fifteen_outer_stages(self):
        result = self.request()
        stored = self.jobs.get(result['id'])
        self.assertEqual(stored['producer'], 'retained-goals-v1')
        self.assertEqual(result['totalStages'], 15)

    def test_legacy_jobs_fail_terminally_before_pinning_or_advancing(self):
        for pinned in (False, True):
            with self.subTest(pinned=pinned):
                result = self.request()
                job = self.jobs.get(result['id'])
                job.pop('producer', None)
                if pinned:
                    job['pipelineState'] = {'inputs': 'legacy-inputs', 'generation': 'legacy-generation'}
                    job['expectedRevisions'] = self.store.tier_publication_revisions()
                self.jobs.save(job)
                with patch('tier_publication_jobs._pin_inputs') as pin, \
                     patch('production_tiers.advance_tier_publication') as advance:
                    failed = execute_tier_publication_job(job['id'], jobs=self.jobs, store=self.store)
                pin.assert_not_called()
                advance.assert_not_called()
                self.assertEqual(failed['status'], 'failed')
                self.assertEqual(failed['attempt'], 1)
                self.assertNotIn(CONTINUATION_FIELD, failed)
                self.assertIsNone(self.jobs.active_tier)
                self.assertEqual(self.store.promotions, 0)

    def test_retired_candidate_schema_or_producer_cannot_promote(self):
        for overrides in ({'schemaVersion': 57}, {'schemaVersion': 60}, {'producer': 'legacy-eight-fits'}):
            with self.subTest(overrides=overrides):
                identity, _ = self.pin()
                with patch('production_tiers.advance_tier_publication', return_value=self.ready(**overrides)):
                    failed = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
                self.assertEqual(failed['status'], 'failed')
                self.assertNotIn(CONTINUATION_FIELD, failed)
                self.assertEqual(self.store.promotions, 0)

    def test_stale_stored_candidate_cannot_use_current_ready_metadata(self):
        valid = copy.deepcopy(self.store.get_json('candidate'))
        for schema in (57, 60):
            with self.subTest(schema=schema):
                self.store.put_json('candidate', {**valid, 'schemaVersion': schema})
                identity, _ = self.pin()
                with patch('production_tiers.advance_tier_publication', return_value=self.ready()):
                    failed = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
                self.assertEqual(failed['status'], 'failed')
                self.assertNotIn(CONTINUATION_FIELD, failed)
                self.assertEqual(self.store.promotions, 0)
        self.store.put_json('candidate', valid)

    def test_status_is_lightweight_and_omits_internal_sources(self):
        identity, _ = self.pin()
        status = get_tier_publication_status(identity, jobs=self.jobs)
        self.assertNotIn('pipelineState', status)
        self.assertNotIn('expectedRevisions', status)
        self.assertIsNone(get_tier_publication_status('../private', jobs=self.jobs))
        self.assertEqual(get_tier_publication_status(jobs=self.jobs)['id'], identity)

    def test_queue_failure_is_persisted_and_does_not_leave_an_active_job(self):
        with self.assertRaisesRegex(RuntimeError, 'could not be queued'):
            self.request(Mock(side_effect=ConnectionError('private connection URL')))
        status = get_tier_publication_status(jobs=self.jobs)
        self.assertEqual(status['error'], 'queue-unavailable')
        self.assertIsNone(self.jobs.active_tier)

    def test_phase_resume_uses_pinned_state_and_monotonic_sequence(self):
        identity, first = self.pin()
        advance = {'done': False, 'stage': 'median', 'progressOrdinal': 10, 'state': {'inputs': 'immutable', 'producer': 'retained-goals-v1', 'phase': 'median'}}
        with patch('production_tiers.advance_tier_publication', return_value=advance) as run:
            second = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
        self.assertEqual(run.call_args.args[1], {'inputs': 'immutable', 'producer': 'retained-goals-v1'})
        self.assertGreater(second[CONTINUATION_FIELD], first[CONTINUATION_FIELD])
        self.assertEqual(self.jobs.get(identity)['pipelineState']['phase'], 'median')
        self.assertEqual(second['status'], 'queued')

    def test_duplicate_delivery_cannot_run_an_owned_phase(self):
        identity, _ = self.pin()
        self.jobs.owner = 'another-worker'
        self.jobs.save({**self.jobs.get(identity), 'status': 'running'})
        with patch('production_tiers.advance_tier_publication') as advance:
            result = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
        advance.assert_not_called()
        self.assertEqual(result['status'], 'running')

    def test_actual_active_stage_is_persisted_before_expensive_work(self):
        identity, _ = self.pin()
        def advance(_store, _state, *, on_stage_start):
            on_stage_start('spread')
            self.assertEqual(get_tier_publication_status(identity, jobs=self.jobs)['stage'], 'spread')
            return {'done': False, 'stage': 'spread', 'progressOrdinal': 9, 'totalStages': 14}
        with patch('production_tiers.advance_tier_publication', side_effect=advance):
            result = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
        self.assertEqual(result['totalStages'], 15)

    def test_expired_worker_lease_can_resume(self):
        identity, _ = self.pin()
        self.jobs.owner = 'dead-worker'
        self.jobs.expired = True
        with patch('production_tiers.advance_tier_publication', return_value={'done': False, 'stage': 'spread'}):
            result = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
        self.assertEqual(result['status'], 'queued')

    def test_changed_sources_fail_without_advancing_or_promoting(self):
        identity, _ = self.pin()
        self.store.revisions['source'] = {'revision': 'source-2'}
        with patch('production_tiers.advance_tier_publication') as run:
            result = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
        run.assert_not_called()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.store.promotions, 0)

    def test_validation_failure_is_not_retried(self):
        identity, _ = self.pin()
        with patch('production_tiers.advance_tier_publication', side_effect=ValueError('private bad data')):
            result = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
        self.assertNotIn(CONTINUATION_FIELD, result)
        self.assertEqual(result['exceptionClass'], 'ValueError')
        self.assertNotIn('private', str(result))

    def test_http_retry_policy_rejects_deterministic_errors(self):
        import requests
        for status in (400, 401, 403, 404, 413, 415, 422):
            response = requests.Response()
            response.status_code = status
            with self.subTest(status=status):
                self.assertFalse(_retryable_io(requests.HTTPError('private response', response=response)))
        for status in (408, 429, 500, 502, 503, 504):
            response = requests.Response()
            response.status_code = status
            with self.subTest(status=status):
                self.assertTrue(_retryable_io(requests.HTTPError('private response', response=response)))
        self.assertFalse(_retryable_io(requests.HTTPError('unknown response')))

    def test_storage_rejection_finishes_after_one_attempt_without_continuation(self):
        import requests
        identity, _ = self.pin()
        response = requests.Response()
        response.status_code = 415
        with patch('production_tiers.advance_tier_publication', side_effect=requests.HTTPError('private response', response=response)):
            result = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['attempt'], 1)
        self.assertNotIn(CONTINUATION_FIELD, result)
        self.assertIsNone(self.jobs.active_tier)

    def test_transient_io_retry_is_bounded_and_retains_active_job(self):
        identity, _ = self.pin()
        sequence = 0
        with patch('production_tiers.advance_tier_publication', side_effect=TimeoutError('private upstream')):
            for attempt in range(1, 4):
                result = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
                self.assertEqual(result['attempt'], attempt)
                if attempt < 3:
                    self.assertIn(CONTINUATION_FIELD, result)
                    self.assertGreater(result[CONTINUATION_FIELD], sequence)
                    sequence = result[CONTINUATION_FIELD]
                    self.assertEqual(self.jobs.active_tier, identity)
        self.assertEqual(result['status'], 'failed')

    def test_ready_candidate_promotes_once_and_recovers_after_lost_ack(self):
        identity, _ = self.pin()
        ready = self.ready()
        with patch('production_tiers.advance_tier_publication', return_value=ready):
            result = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
            duplicate = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['completedTargetFits'], 4)
        self.assertEqual(duplicate['status'], 'completed')
        self.assertEqual(self.store.promotions, 1)
        self.assertEqual(self.store.last_promotion['audit']['schemaVersion'], 61)

    def test_transaction_conflict_leaves_tier_and_recommendations_untouched(self):
        identity, _ = self.pin()
        self.store.put_json('tier', {'old': True})
        self.store.put_json('recommendations', {'generation': 'unchanged'})
        self.store.reject_promotion = True
        before = copy.deepcopy(self.store.values)
        with patch('production_tiers.advance_tier_publication', return_value=self.ready()):
            result = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.store.values, before)

    def test_changed_candidate_hash_cannot_be_promoted(self):
        identity, _ = self.pin()
        with patch('production_tiers.advance_tier_publication', return_value=self.ready(candidateSha256='different-content')):
            result = execute_tier_publication_job(identity, jobs=self.jobs, store=self.store)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['exceptionClass'], 'TierPublicationConflict')
        self.assertEqual(self.store.promotions, 0)

    def test_worker_continuation_uses_unique_queue_keys_for_retry(self):
        from worker.tasks import publish_tiers
        from worker.constants import QUEUE_NAME
        result = {'status': 'queued', 'attempt': 1, CONTINUATION_FIELD: 8}
        with patch('tier_publication_jobs.execute_tier_publication_job', return_value=result.copy()), \
                patch.object(publish_tiers, 'apply_async') as enqueue:
            returned = publish_tiers.run('job')
        enqueue.assert_called_once_with(args=['job'], task_id='job-phase-8-attempt-1', queue=QUEUE_NAME)
        self.assertNotIn(CONTINUATION_FIELD, returned)


if __name__ == '__main__':
    unittest.main()
