from __future__ import annotations

import copy
import os
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.jonathan import router
from tests.test_tier_publication_jobs import Jobs, Store
from tier_publication_jobs import CONTINUATION_FIELD, TierPublicationConflict, execute_tier_publication_job, request_scoring_goal_backfill


class BackfillStore(Store):
    def __init__(self, jobs):
        super().__init__(jobs)
        self.completions = 0
        self.drift_at_completion = False

    def complete_scoring_goal_backfill(self, *, job_id, lease_owner, expected_revisions, completed_job):
        if self.drift_at_completion:
            self.revisions["source"] = {"revision": "changed"}
        if (self.revisions != expected_revisions or self.jobs.owner != lease_owner or self.jobs.expired):
            raise TierPublicationConflict("changed source or owner")
        self.completions += 1
        self.jobs.finish_tier_publication(completed_job)


def result(goal, *, done=True, identity="a" * 64, fits=None):
    return {"done": done, "goal": goal, "inputIdentity": identity,
            "completedTargetFits": (4 if done else 2) if fits is None else fits,
            "artifactReference": {"path": "analysis/private/goal-" + goal, "sha256": "b" * 64}}


class ScoringGoalBackfillJobTests(unittest.TestCase):
    def setUp(self):
        self.jobs = Jobs()
        self.store = BackfillStore(self.jobs)
        self.policy = patch("tier_release.require_tier_release_policy")
        self.policy.start()
        self.addCleanup(self.policy.stop)
        self.events = patch("tier_diagnostics.event")
        self.events.start()
        self.addCleanup(self.events.stop)
        self.errors = patch("tier_diagnostics.report_exception")
        self.errors.start()
        self.addCleanup(self.errors.stop)

    def request(self, **kwargs):
        return request_scoring_goal_backfill(Mock(), store=self.store, jobs=self.jobs, **kwargs)[1]

    def pin(self, **kwargs):
        job = self.request(**kwargs)
        with patch("tier_publication_jobs._pin_inputs", return_value=({"inputs": {"path": "pinned"}}, self.store.tier_publication_revisions())):
            execute_tier_publication_job(job["id"], store=self.store, jobs=self.jobs)
        return job["id"]

    def test_trigger_is_small_and_uses_existing_job_lock(self):
        with patch.object(self.store, "tier_publication_revisions", side_effect=AssertionError("HTTP read sources")):
            first = self.request()
            second = self.request()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["operation"], "scoring-goal-backfill")
        self.assertEqual(first["goals"], ["ss", "ssplus"])
        self.assertNotIn("pipelineState", first)

    def test_invalid_goal_configuration_never_creates_job(self):
        for goals in ((), ("sss",), ("ss", "ss")):
            with self.subTest(goals=goals), self.assertRaises(ValueError):
                self.request(goals=goals)
        self.assertIsNone(self.jobs.active_tier)

    def test_both_goal_artifacts_join_on_one_epoch_without_promoting_and_duplicate_is_noop(self):
        identity = self.pin()
        before = copy.deepcopy(self.store.values)
        with patch("production_tiers.advance_goal_backfill", side_effect=[result("ss"), result("ssplus")], create=True) as advance:
            first = execute_tier_publication_job(identity, store=self.store, jobs=self.jobs)
            second = execute_tier_publication_job(identity, store=self.store, jobs=self.jobs)
            duplicate = execute_tier_publication_job(identity, store=self.store, jobs=self.jobs)
        self.assertEqual(advance.call_count, 2)
        self.assertIn(CONTINUATION_FIELD, first)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["stage"], "cached")
        self.assertEqual(second["completedTargetFits"], 8)
        self.assertFalse(second["backfill"]["promoted"])
        self.assertEqual(set(second["backfill"]["artifactReferences"]), {"ss", "ssplus"})
        self.assertEqual(duplicate, second)
        self.assertEqual(self.store.promotions, 0)
        self.assertEqual(self.store.completions, 1)
        self.assertEqual(self.store.values, before)

    def test_incomplete_goal_continues_from_same_pinned_inputs(self):
        identity = self.pin(goals=("ss",))
        with patch("production_tiers.advance_goal_backfill", side_effect=[result("ss", done=False), result("ss")], create=True) as advance:
            first = execute_tier_publication_job(identity, store=self.store, jobs=self.jobs)
            second = execute_tier_publication_job(identity, store=self.store, jobs=self.jobs)
        self.assertEqual(first["completedTargetFits"], 2)
        self.assertEqual(advance.call_args_list[0].kwargs["pinned_inputs_reference"], advance.call_args_list[1].kwargs["pinned_inputs_reference"])
        self.assertEqual(second["status"], "completed")

    def test_changed_source_epoch_or_mixed_goal_identity_never_completes_or_promotes(self):
        identity = self.pin()
        with patch("production_tiers.advance_goal_backfill", side_effect=[result("ss"), result("ssplus", identity="c" * 64)], create=True):
            execute_tier_publication_job(identity, store=self.store, jobs=self.jobs)
            failed = execute_tier_publication_job(identity, store=self.store, jobs=self.jobs)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.store.completions, 0)
        self.assertEqual(self.store.promotions, 0)

    def test_source_drift_during_final_branch_is_fenced_at_completion(self):
        identity = self.pin(goals=("ss",))
        self.store.drift_at_completion = True
        with patch("production_tiers.advance_goal_backfill", return_value=result("ss"), create=True):
            failed = execute_tier_publication_job(identity, store=self.store, jobs=self.jobs)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.store.completions, 0)
        self.assertEqual(self.store.promotions, 0)

    def test_stale_lease_cannot_finish_artifacts(self):
        identity = self.pin(goals=("ss",))
        def build(*_, **__):
            self.jobs.expired = True
            return result("ss")
        with patch("production_tiers.advance_goal_backfill", side_effect=build, create=True):
            failed = execute_tier_publication_job(identity, store=self.store, jobs=self.jobs)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.store.completions, 0)

    def test_malformed_child_counts_fail_without_automatic_retries(self):
        identity = self.pin(goals=("ss",))
        with patch("production_tiers.advance_goal_backfill", return_value=result("ss", fits=5), create=True):
            failed = execute_tier_publication_job(identity, store=self.store, jobs=self.jobs)
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn(CONTINUATION_FIELD, failed)
        self.assertEqual(self.store.promotions, 0)

    def test_operator_route_authenticates_and_rejects_retired_backfill_without_enqueue(self):
        app = FastAPI(); app.include_router(router)
        client = TestClient(app)
        with patch.dict(os.environ, {"JONATHAN_PASSWORD": "operator"}), \
             patch("tier_publication_jobs.request_scoring_goal_backfill", return_value=(202, {"status": "queued"})) as request, \
             patch("api.jonathan.start_or_reuse_analysis", side_effect=AssertionError("backfill started global refresh")):
            self.assertEqual(client.post("/api/jonathan/refresh?mode=scoring-goal-backfill").status_code, 401)
            request.assert_not_called()
            response = client.post("/api/jonathan/refresh?mode=scoring-goal-backfill", headers={"X-Jonathan-Password": "operator"})
            self.assertEqual(response.status_code, 410)
            self.assertIn("retired", response.json()["error"])
            request.assert_not_called()
