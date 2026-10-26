"""Deterministic admission/drain fixtures; no production or upstream requests."""
import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.captured_recovery import router
from captured_run_handoff import (CapturedRecoveryConflict, HEAD, digest, execute_recovery,
    guard_head, prepare_recovery, store_admission, store_admit, store_begin, summary)

NOW = datetime(2026, 10, 26, 4, tzinfo=timezone.utc)
INTENT = "a" * 64
BUNDLE = "b" * 64
REVISION = "c" * 64


def intent(state="prepared"):
    return {"version": 1, "intentId": INTENT, "requestHash": "request", "sourceJobId": "analysis-source",
        "sourceDeploymentId": "dpl_source", "expectedHead": "analysis-source", "bundleId": BUNDLE,
        "expectedServingRevision": REVISION, "replacementJobId": "analysis-captured-test", "state": state,
        "createdAtUtc": NOW.isoformat(), "cancelledAtUtc": NOW.isoformat(),
        "drainNotBeforeUtc": (NOW + timedelta(seconds=800)).isoformat()}


class CoordinatorJobs:
    def __init__(self):
        self.intents = {}
        self.active = "analysis-source"
        self.source = {"id": self.active, "status": "running", "stage": "analyzing"}
        self.begin_calls = 0
        self.ready = False

    def active_job_id(self): return self.active
    def get(self, _): return self.source
    def captured_recovery_status(self, key): return copy.deepcopy(self.intents.get(key))
    def captured_recovery_serving_revision(self): return REVISION
    def captured_recovery_prepare(self, value):
        self.intents[value["intentId"]] = copy.deepcopy(value)
        return value
    def captured_recovery_begin(self, key):
        self.begin_calls += 1
        self.intents[key]["state"] = "draining"
        return copy.deepcopy(self.intents[key])
    def captured_recovery_admit(self, key):
        if self.ready: self.intents[key]["state"] = "admitted"
        return copy.deepcopy(self.intents[key])


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.jobs = CoordinatorJobs()
        self.request = {"sourceJobId": "analysis-source", "sourceDeploymentId": "dpl_source",
            "expectedHead": "analysis-source", "expectedManifestSha256": "d" * 64,
            "expectedServingRevision": REVISION, "idempotencyKey": "operator-request-1"}
        self.prepare = Mock(return_value={"bundleId": BUNDLE, "summary": {"charts": 1389}})

    def run_prepare(self, request=None):
        return prepare_recovery(object(), self.jobs, request or self.request,
            context_loader=Mock(), prepare_inputs=self.prepare)

    def test_prepare_is_nonmutating_and_idempotent(self):
        first = self.run_prepare()
        self.assertEqual(self.run_prepare(), first)
        self.prepare.assert_called_once()
        self.assertEqual(self.jobs.begin_calls, 0)
        self.assertEqual(self.jobs.active, "analysis-source")
        self.assertEqual(self.jobs.source["status"], "running")
        self.assertNotIn("requestHash", first)
        self.assertNotIn("idempotencyKey", first)

    def test_same_idempotency_key_cannot_change_bundle_inputs(self):
        self.run_prepare()
        changed = {**self.request, "expectedManifestSha256": "e" * 64}
        with self.assertRaises(CapturedRecoveryConflict): self.run_prepare(changed)
        self.prepare.assert_called_once()

    def test_arbitrary_paths_and_wrong_expected_head_are_rejected(self):
        for changed in ({**self.request, "snapshotPath": "private/path"},
                        {**self.request, "expectedHead": "another-job"}):
            with self.assertRaises(ValueError): self.run_prepare(changed)
        self.prepare.assert_not_called()

    def test_active_owner_race_fails_before_bundle_or_cancellation(self):
        self.jobs.active = "another-job"
        with self.assertRaises(CapturedRecoveryConflict): self.run_prepare()
        self.prepare.assert_not_called()
        self.assertEqual(self.jobs.begin_calls, 0)

    def test_completed_source_can_be_inventoried_without_restart(self):
        self.jobs.source["status"] = "completed"
        self.jobs.active = None
        self.assertEqual(self.run_prepare()["state"], "prepared")
        self.assertEqual(self.jobs.begin_calls, 0)

    def test_corrupt_bundle_never_cancels_or_queues(self):
        key = self.run_prepare()["intentId"]
        enqueue = Mock()
        with self.assertRaises(ValueError):
            execute_recovery(object(), self.jobs, key, enqueue, load_inputs=Mock(side_effect=ValueError("corrupt")))
        self.assertEqual(self.jobs.begin_calls, 0)
        enqueue.assert_not_called()

    def test_waiting_recovery_never_queues_and_retries_use_one_job(self):
        key = self.run_prepare()["intentId"]
        enqueue = Mock()
        load = Mock(return_value={"verified": True})
        self.assertEqual(execute_recovery(object(), self.jobs, key, enqueue, load_inputs=load)["state"], "draining")
        enqueue.assert_not_called()
        self.jobs.ready = True
        first = execute_recovery(object(), self.jobs, key, enqueue, load_inputs=load)
        second = execute_recovery(object(), self.jobs, key, enqueue, load_inputs=load)
        self.assertEqual(first["replacementJobId"], second["replacementJobId"])
        self.assertEqual(self.jobs.begin_calls, 1)
        self.assertEqual(enqueue.call_count, 2)
        self.assertEqual(enqueue.call_args_list[0], enqueue.call_args_list[1])

    def test_ambiguous_enqueue_failure_can_retry_same_admitted_job(self):
        key = self.run_prepare()["intentId"]
        self.jobs.ready = True
        enqueue = Mock(side_effect=[OSError("lost reply"), None])
        with self.assertRaises(OSError): execute_recovery(object(), self.jobs, key, enqueue, load_inputs=Mock())
        execute_recovery(object(), self.jobs, key, enqueue, load_inputs=Mock())
        self.assertEqual(self.jobs.begin_calls, 1)
        self.assertEqual(enqueue.call_args_list[0], enqueue.call_args_list[1])


class StoreLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.jobs = SimpleNamespace(database_url="postgresql://unused/test")
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.rowcount = 1
        self.connect = patch("pumbility_store._connect", return_value=self.connection)
        self.connect.start(); self.addCleanup(self.connect.stop)
        self.schema = patch("pumbility_store._assert_schema")
        self.schema.start(); self.addCleanup(self.schema.stop)
        self.write = patch("captured_run_handoff._write_intent")
        self.saved = self.write.start(); self.addCleanup(self.write.stop)
        self.revision = patch("captured_run_handoff._revision", return_value=REVISION)
        self.revisions = self.revision.start(); self.addCleanup(self.revision.stop)

    def queries(self): return [str(call.args[0]) for call in self.cursor.execute.call_args_list]

    def seed_begin(self):
        self.cursor.fetchone.side_effect = [(intent(),), ("source-uuid", "running", "analyzing", {"id": "analysis-source"}),
                                            (NOW,), ("replacement-uuid",)]
        self.cursor.fetchall.return_value = [("active:analysis", "source-uuid")]

    def seed_admit(self, *, now=NOW + timedelta(seconds=800), source=None):
        self.cursor.fetchone.side_effect = [(intent("draining"),), source or ("cancelled", None, None, NOW), (now,)]
        self.cursor.fetchall.return_value = [("analysis-captured-test",), ("analysis-captured-test",)]

    def test_cancel_and_global_reservation_are_one_locked_transaction(self):
        self.seed_begin()
        result = store_begin(self.jobs, INTENT)
        self.assertEqual(result["state"], "draining")
        self.assertEqual(result["drainNotBeforeUtc"], (NOW + timedelta(seconds=800)).isoformat())
        queries = self.queries()
        self.assertTrue(any("pg_advisory_xact_lock" in query for query in queries))
        self.assertIn("set local lock_timeout = '5000ms'", queries)
        self.assertIn("set local statement_timeout = '30000ms'", queries)
        self.assertTrue(any("for update" in query and "pumbility.jobs" in query for query in queries))
        self.assertTrue(any("for update" in query and "pumbility.job_heads" in query for query in queries))
        self.assertEqual(sum("cancel_job" in query for query in queries), 1)
        self.assertLess(next(i for i,q in enumerate(queries) if "cancel_job" in q),
                        next(i for i,q in enumerate(queries) if "insert into pumbility.job_heads" in q))
        job_insert = next(call for call in self.cursor.execute.call_args_list if "insert into pumbility.jobs" in call.args[0])
        replacement = job_insert.args[1][1].obj
        self.assertFalse(replacement["reanalyzeOnly"])
        self.assertFalse(replacement["refreshOfficialSources"])
        self.assertEqual(replacement["capturedRunBundleId"], BUNDLE)
        self.connection.transaction.assert_called_once()

    def test_conflicting_active_owner_never_cancels(self):
        self.seed_begin()
        self.cursor.fetchall.return_value = [("active:analysis", "other-uuid")]
        with self.assertRaises(CapturedRecoveryConflict): store_begin(self.jobs, INTENT)
        self.assertFalse(any("cancel_job" in query for query in self.queries()))

    def test_revision_drift_before_cancel_never_cancels(self):
        self.seed_begin(); self.revisions.return_value = "changed"
        with self.assertRaises(CapturedRecoveryConflict): store_begin(self.jobs, INTENT)
        self.assertFalse(any("cancel_job" in query for query in self.queries()))

    def test_completed_source_is_recorded_without_reservation_or_restart(self):
        self.cursor.fetchone.side_effect = [(intent(),), ("source-uuid", "completed", "publishing", {})]
        result = store_begin(self.jobs, INTENT)
        self.assertEqual(result["state"], "source-completed")
        self.assertFalse(any("cancel_job" in query or "insert into pumbility.jobs" in query for query in self.queries()))

    def test_publishing_source_is_not_cancelled(self):
        self.cursor.fetchone.side_effect = [(intent(),), ("source-uuid", "running", "publishing", {})]
        with self.assertRaises(CapturedRecoveryConflict): store_begin(self.jobs, INTENT)
        self.assertFalse(any("cancel_job" in query for query in self.queries()))

    def test_799_seconds_never_releases_numerical_work_even_without_owner(self):
        self.seed_admit(now=NOW + timedelta(seconds=799))
        self.assertEqual(store_admit(self.jobs, INTENT)["state"], "draining")
        self.saved.assert_not_called()
        self.revisions.assert_not_called()

    def test_bound_terminal_and_no_owner_release_one_replacement(self):
        self.seed_admit()
        result = store_admit(self.jobs, INTENT)
        self.assertEqual(result["state"], "admitted")
        self.assertEqual(result["finalServingRevision"], REVISION)
        self.revisions.assert_called_once()

    def test_bound_alone_does_not_prove_quiescence(self):
        for source in (("running", None, None, NOW), ("cancelled", "old-owner", None, NOW),
                       ("cancelled", None, NOW + timedelta(seconds=900), NOW), ("cancelled", None, None, None)):
            with self.subTest(source=source):
                self.seed_admit(source=source)
                with self.assertRaises(CapturedRecoveryConflict): store_admit(self.jobs, INTENT)
        self.saved.assert_not_called()

    def test_late_publication_during_drain_blocks_admission_and_keeps_reservation(self):
        self.seed_admit(); self.revisions.return_value = "e" * 64
        result = store_admit(self.jobs, INTENT)
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["blocker"], "serving-revisions-changed-during-drain")
        self.assertFalse(any("delete from pumbility.job_heads" in query for query in self.queries()))

    def test_lost_reservation_never_admits(self):
        self.seed_admit(); self.cursor.fetchall.return_value = [("another-job",), ("another-job",)]
        with self.assertRaises(CapturedRecoveryConflict): store_admit(self.jobs, INTENT)
        self.saved.assert_not_called()

    def test_safe_abort_requires_drain_and_final_revision_then_releases_only_matching_heads(self):
        self.seed_admit()
        self.assertEqual(store_admit(self.jobs, INTENT, abort_revision=REVISION)["state"], "aborted")
        deletes = [call for call in self.cursor.execute.call_args_list if "delete from pumbility.job_heads" in call.args[0]]
        self.assertEqual(len(deletes), 1)
        self.assertIn("job_id=(select id", deletes[0].args[0])
        self.assertEqual(deletes[0].args[1][1], "analysis-captured-test")

    def test_failed_reservation_write_rolls_back_cancellation(self):
        self.seed_begin()
        def fail(query, *_):
            if "insert into pumbility.job_heads" in query: raise OSError("write failure")
        self.cursor.execute.side_effect = fail
        with self.assertRaises(OSError): store_begin(self.jobs, INTENT)
        self.assertIs(self.connection.transaction.return_value.__exit__.call_args.args[0], OSError)

    def test_head_guard_prevents_preflight_race_from_ordinary_refresh(self):
        self.cursor.fetchone.side_effect = [("analysis-captured-test", "queued", {})]
        with self.assertRaises(CapturedRecoveryConflict): guard_head(self.cursor, "active:analysis", "ordinary-job")
        self.assertFalse(any("insert" in query or "delete" in query for query in self.queries()))
        self.assertIn("pg_advisory_xact_lock", self.queries()[0])
        self.assertIn("pumbility:captured-run-handoff", self.queries()[0])
        self.assertIn("where h.name", self.queries()[1])

    def test_no_reservation_leaves_normal_head_update_unchanged(self):
        self.cursor.fetchone.return_value = None
        guard_head(self.cursor, "active:analysis", "normal-job")
        self.assertFalse(any("insert" in query or "delete" in query for query in self.queries()))
        self.assertIn("pg_advisory_xact_lock", self.queries()[0])


class ApiAuthorizationTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI(); app.include_router(router)
        self.client = TestClient(app)

    def test_unauthorized_all_operations_have_no_store_access(self):
        with patch.dict("os.environ", {"JONATHAN_PASSWORD": "fixture-password"}), patch("api.captured_recovery._stores") as stores:
            for method, path, payload in (("post", "/prepare", {}), ("post", "/execute", {}), ("post", "/abort", {}), ("get", "", None)):
                args = {} if payload is None else {"json": payload}
                response = getattr(self.client, method)("/api/jonathan/captured-recovery" + path, **args)
                self.assertEqual(response.status_code, 401)
            stores.assert_not_called()

    def test_missing_auth_configuration_fails_closed(self):
        with patch.dict("os.environ", {"JONATHAN_PASSWORD": ""}), patch("api.captured_recovery._stores") as stores:
            self.assertEqual(self.client.get("/api/jonathan/captured-recovery").status_code, 503)
            stores.assert_not_called()

    def test_errors_and_status_never_expose_private_paths(self):
        with patch.dict("os.environ", {"JONATHAN_PASSWORD": "fixture-password"}), patch("api.captured_recovery._stores", side_effect=RuntimeError("analysis/private/player-key secret")):
            response = self.client.get("/api/jonathan/captured-recovery", headers={"x-jonathan-password": "fixture-password"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("player-key", response.text)
            self.assertNotIn("analysis/private", response.text)


if __name__ == "__main__": unittest.main()
