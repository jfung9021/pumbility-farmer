"""Pinned callback contracts and optional disposable-local-PostgreSQL recovery.

Database tests create and drop their own random database, never the shared one.
Set PUMBILITY_GLOBAL_REFRESH_TEST_ADMIN_URL to a loopback /postgres URL and
PUMBILITY_OWNER_SCHEMA_SQL to the sibling's 20260813010000_pumbility_schema.sql.
The exact owner migration is loaded through its SQL-only section; Storage
policies are outside this lease-contract fixture. No production credentials or
upstream/queue network calls are used. Numerical computation/publication is a
test seam in the callback recovery scenarios, not a full model rehearsal.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from vercel.workers import callback as queue_callback
from vercel.workers.celery.app import handle_queue_callback

import analysis_runtime
from pumbility_store import (
    JobLeaseHeldError,
    JobLeaseLostError,
    PumbilityArtifactStore,
    PumbilityJobStore,
    require_loopback_database_url,
)
from worker import tasks
from worker.celery import QUEUE_NAME, app


def _envelope(job_id):
    return {
        "vercel": {"kind": "celery", "version": 1},
        "task": "worker.tasks.refresh_analysis",
        "id": "retained-" + job_id,
        "args": [job_id],
        "kwargs": {},
    }


@contextmanager
def _delivery(job_id, *, visibility_error=None):
    """Real parser, Celery task.apply and receipt finalization; network is mocked."""
    event = json.dumps({
        "type": "com.vercel.queue.v1beta",
        "data": {"queueName": QUEUE_NAME, "consumerGroup": "default",
                 "messageId": "retained-message"},
    }).encode()
    with patch.object(queue_callback, "receive_message_by_id", return_value=(
            _envelope(job_id), 2, datetime.now(timezone.utc).isoformat(), "receipt")), \
         patch.object(queue_callback, "change_visibility", side_effect=visibility_error) as visibility, \
         patch.object(queue_callback, "delete_message") as delete, \
         patch.object(tasks.refresh_analysis, "apply_async") as enqueue, \
         patch.object(tasks, "_begin_topology_worker_invocation", return_value=None), \
         patch.object(tasks, "_complete_topology_worker_invocation_best_effort"):
        def invoke():
            status, _headers, body = handle_queue_callback(app, event, {})
            return status, json.loads(body)
        yield invoke, visibility, delete, enqueue


class PinnedAnalysisQueueContractTests(unittest.TestCase):
    def test_exact_deployment_adapter_versions(self):
        self.assertEqual(version("vercel-workers"), "0.0.25")
        self.assertEqual(version("celery"), "5.6.3")

    def conflict(self):
        return JobLeaseHeldError(
            "Synthetic live lease.", blocking_job_id="synthetic-job",
            blocking_status="running", lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            retry_after_seconds=17.25,
        )

    def test_live_owner_changes_existing_receipt_visibility_without_new_task(self):
        with patch.object(tasks, "execute_analysis_job", side_effect=self.conflict()), \
             _delivery("synthetic-job") as (invoke, visibility, delete, enqueue):
            status, body = invoke()
        self.assertEqual(status, 200)
        self.assertTrue(body["delayed"])
        self.assertGreaterEqual(body["timeoutSeconds"], 18)
        self.assertLessEqual(body["timeoutSeconds"], 25)
        self.assertEqual(visibility.call_args.args[:4],
                         (QUEUE_NAME, "default", "retained-message", "receipt"))
        self.assertEqual(visibility.call_args.args[4], body["timeoutSeconds"])
        visibility.assert_called_once()
        delete.assert_not_called()
        enqueue.assert_not_called()

    def test_visibility_update_failure_is_transport_failure_without_ack(self):
        with patch.object(tasks, "execute_analysis_job", side_effect=self.conflict()), \
             _delivery("synthetic-job", visibility_error=RuntimeError("synthetic visibility outage")) \
                as (invoke, visibility, delete, enqueue):
            status, _body = invoke()
        self.assertEqual(status, 500)
        visibility.assert_called_once()
        delete.assert_not_called()
        enqueue.assert_not_called()

    def test_database_error_cannot_be_acknowledged_as_duplicate(self):
        with patch.object(tasks, "execute_analysis_job", side_effect=psycopg.OperationalError("synthetic unavailable")), \
             _delivery("synthetic-job") as (invoke, visibility, delete, enqueue):
            status, _body = invoke()
        self.assertEqual(status, 500)
        visibility.assert_not_called()
        delete.assert_not_called()
        enqueue.assert_not_called()

    def test_terminal_delivery_acknowledges_without_republishing(self):
        for state in ("completed", "failed", "cancelled"):
            with self.subTest(state=state), \
                 patch.object(tasks, "execute_analysis_job", return_value={"id": "synthetic-job", "status": state}), \
                 _delivery("synthetic-job") as (invoke, visibility, delete, enqueue):
                status, body = invoke()
                self.assertEqual(status, 200)
                self.assertFalse(body["delayed"])
                delete.assert_called_once()
                visibility.assert_not_called()
                enqueue.assert_not_called()


ADMIN_URL = os.getenv("PUMBILITY_GLOBAL_REFRESH_TEST_ADMIN_URL", "").strip()
OWNER_SCHEMA = os.getenv("PUMBILITY_OWNER_SCHEMA_SQL", "").strip()


@unittest.skipUnless(ADMIN_URL and OWNER_SCHEMA, "Set disposable local recovery DB admin URL and owner migration path")
class GlobalRefreshPostgreSQLIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_loopback_database_url(ADMIN_URL)
        if urlparse(ADMIN_URL).path != "/postgres":
            raise ValueError("Recovery fixture requires the local postgres administration database.")
        migration = Path(OWNER_SCHEMA).resolve()
        if migration.name != "20260813010000_pumbility_schema.sql":
            raise ValueError("Use the canonical sibling Pumbility owner migration.")
        schema_sql = migration.read_text(encoding="utf-8")
        marker = "insert into storage.buckets"
        if schema_sql.count(marker) != 1:
            raise ValueError("Owner migration Storage boundary changed; inspect fixture before running.")
        # Required roles must already exist; the fixture never creates cluster roles.
        required = ["anon", "authenticated", "service_role", "pumbility_reader", "pumbility_worker"]
        cls.database_name = "piu_global_refresh_test_" + uuid4().hex
        with psycopg.connect(ADMIN_URL, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute("select current_database(), (select count(*) from pg_roles where rolname = any(%s))", (required,))
            if cursor.fetchone() != ("postgres", len(required)):
                raise ValueError("Expected existing local Supabase roles are unavailable.")
            cursor.execute(sql.SQL("create database {} template template0").format(sql.Identifier(cls.database_name)))
        cls.addClassCleanup(cls.drop_database)
        connection_options = conninfo_to_dict(ADMIN_URL)
        connection_options["dbname"] = cls.database_name
        cls.database_url = make_conninfo(**connection_options)
        with psycopg.connect(cls.database_url) as connection, connection.cursor() as cursor:
            cursor.execute("select current_database()")
            if cursor.fetchone()[0] != cls.database_name:
                raise ValueError("Disposable test database identity mismatch.")
            cursor.execute(schema_sql.split(marker, 1)[0], prepare=False)

    @classmethod
    def drop_database(cls):
        # Only this run's random database is eligible; never drop an env-selected DB.
        if not cls.database_name.startswith("piu_global_refresh_test_") or len(cls.database_name) != 56:
            raise ValueError("Refusing to remove a non-fixture database.")
        with psycopg.connect(ADMIN_URL, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(cls.database_name)))

    def setUp(self):
        self.env = patch.dict(os.environ, {"PUMBILITY_SUPABASE_READ_POOL_ENABLED": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute("select current_database()")
            self.assertEqual(cursor.fetchone()[0], self.database_name)
            cursor.execute("truncate pumbility.job_heads, pumbility.job_events, pumbility.jobs, pumbility.artifacts cascade")
        self.owner_a = PumbilityJobStore(database_url=self.database_url)
        self.owner_b = PumbilityJobStore(database_url=self.database_url)
        self.blobs = PumbilityArtifactStore(database_url=self.database_url)
        self.job_id = "synthetic-recovery-job"

    def seed(self):
        stamp = datetime.now(timezone.utc).isoformat()
        job = {"id": self.job_id, "status": "queued", "stage": "analyzing",
               "mix": "phoenix2", "createdAtUtc": stamp, "updatedAtUtc": stamp,
               "startedAtUtc": stamp, "progress": {"current": 1, "total": 8},
               "privateCheckpoint": {"generation": "synthetic-generation", "preparationSequence": 1}}
        self.owner_a.save(job)
        self.owner_a.save({**job, "status": "running"})
        self.owner_a.set_active_job_id(self.job_id)
        return self.owner_a.get(self.job_id)

    def state(self):
        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute("select id, status, payload, progress, lease_owner, lease_expires_at, heartbeat_at "
                           "from pumbility.jobs where external_key=%s", (self.job_id,))
            return cursor.fetchone()

    def expire(self):
        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute("update pumbility.jobs set lease_expires_at=clock_timestamp()-interval '1 second' "
                           "where external_key=%s", (self.job_id,))

    def test_actual_claim_heartbeat_reclaim_and_former_owner_fences(self):
        job = self.seed()
        self.owner_a.heartbeat(self.job_id)
        before = self.state()
        with self.assertRaises(JobLeaseHeldError) as conflict:
            self.owner_b.save(job)
        self.assertEqual(conflict.exception.blocking_job_id, self.job_id)
        self.assertEqual(conflict.exception.blocking_status, "running")
        self.assertGreater(conflict.exception.retry_after_seconds, 0)
        self.assertLessEqual(conflict.exception.retry_after_seconds, 800)
        self.assertEqual(self.state(), before)
        self.expire()
        self.owner_b.save(self.owner_b.get(self.job_id))
        self.assertEqual(self.state()[4], self.owner_b.lease_owner)
        for operation in (
            lambda: self.owner_a.heartbeat(self.job_id),
            lambda: self.owner_a.save({**job, "status": "completed"}),
            lambda: self.owner_a.save({**job, "status": "failed"}),
        ):
            with self.assertRaises(JobLeaseLostError):
                operation()
        self.owner_b.save({**job, "status": "completed"})
        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute("select pumbility.heartbeat_job(%s,%s,%s,%s,%s)",
                           (before[0], self.owner_a.lease_owner, "analyzing", Jsonb({}), 800))
            self.assertFalse(cursor.fetchone()[0])
        self.assertEqual(self.owner_b.get(self.job_id)["status"], "completed")

    @contextmanager
    def actual_worker(self, resume):
        original = analysis_runtime.execute_analysis_job
        def execute(job_id, **kwargs):
            return original(job_id, jobs=self.owner_b, blobs=self.blobs, **kwargs)
        with patch.object(tasks, "execute_analysis_job", side_effect=execute), \
             patch.object(tasks, "RuntimeJobStore", return_value=self.owner_b), \
             patch.object(analysis_runtime, "cleanup_abandoned_staging"), \
             patch.object(analysis_runtime, "cleanup_abandoned_typed_checkpoints"), \
             patch.object(analysis_runtime, "_resume_typed_analysis_checkpoint", side_effect=resume) as resumed:
            yield resumed

    def checkpoint(self):
        path = analysis_runtime.typed_checkpoint_path(self.job_id, "phoenix2")
        checkpoint = {"generation": "synthetic-generation", "prepared": "synthetic/prepared.json"}
        self.blobs.put_json(checkpoint["prepared"], {"committedPreparation": 1})
        self.blobs.put_json(path, checkpoint)
        return path, checkpoint

    def test_callback_retains_before_expiry_then_resumes_same_checkpoint_once(self):
        self.seed()
        path, checkpoint = self.checkpoint()
        publication = Mock(side_effect=lambda: self.blobs.put_json("synthetic/published.json", {"generation": checkpoint["generation"]}))
        def resume(value, *, job_store, job_id, **_kwargs):
            self.assertEqual(value, checkpoint)
            self.assertEqual(self.blobs.get_json(value["prepared"]), {"committedPreparation": 1})
            job_store.heartbeat(job_id)
            publication()
            return analysis_runtime.update_job(job_store, job_id, status="completed", stage="completed")
        before = self.state()
        with self.actual_worker(resume) as resumed:
            with _delivery(self.job_id) as (invoke, visibility, delete, enqueue):
                status, body = invoke()
                self.assertEqual(status, 200)
                self.assertTrue(body["delayed"])
                visibility.assert_called_once()
                delete.assert_not_called()
                enqueue.assert_not_called()
            self.assertEqual(self.state(), before)
            resumed.assert_not_called()
            publication.assert_not_called()
            self.assertEqual(self.blobs.get_json(path), checkpoint)
            self.expire()
            with _delivery(self.job_id) as (invoke, visibility, delete, enqueue):
                status, body = invoke()
                self.assertEqual(status, 200)
                self.assertFalse(body["delayed"])
                delete.assert_called_once()
                visibility.assert_not_called()
                enqueue.assert_not_called()
            with _delivery(self.job_id) as (invoke, _visibility, delete, enqueue):
                self.assertEqual(invoke()[0], 200)
                delete.assert_called_once()
                enqueue.assert_not_called()
        resumed.assert_called_once()
        publication.assert_called_once()
        self.assertEqual(self.blobs.get_json("synthetic/published.json"), {"generation": "synthetic-generation"})
        self.assertEqual(self.owner_b.get(self.job_id)["status"], "completed")
        with self.assertRaises(JobLeaseLostError):
            self.owner_a.heartbeat(self.job_id)

    def test_healthy_owner_renews_while_duplicate_waits_then_terminal_ack(self):
        job = self.seed()
        with self.actual_worker(Mock(side_effect=AssertionError("Duplicate must not compute"))):
            for _attempt in range(2):
                self.owner_a.heartbeat(self.job_id)
                before = self.state()
                with _delivery(self.job_id) as (invoke, visibility, delete, enqueue):
                    status, body = invoke()
                    self.assertEqual(status, 200)
                    self.assertTrue(body["delayed"])
                    visibility.assert_called_once()
                    delete.assert_not_called()
                    enqueue.assert_not_called()
                self.assertEqual(self.state(), before)
            self.owner_a.save({**job, "status": "completed"})
            with _delivery(self.job_id) as (invoke, visibility, delete, enqueue):
                self.assertEqual(invoke()[0], 200)
                delete.assert_called_once()
                visibility.assert_not_called()
                enqueue.assert_not_called()

    def test_enqueue_failure_retains_receipt_and_reuses_committed_checkpoint(self):
        self.seed()
        _path, checkpoint = self.checkpoint()
        self.expire()
        calls = []
        def resume(value, *, job_store, job_id, **_kwargs):
            self.assertEqual(value, checkpoint)
            calls.append(value)
            if len(calls) == 1:
                current = job_store.get(job_id)
                job_store.handoff_continuation(job_id, current)
                return {**current, "status": "queued", analysis_runtime.ANALYSIS_CONTINUATION_FIELD: "combined",
                        analysis_runtime.ANALYSIS_CONTINUATION_SEQUENCE_FIELD: 1}
            return analysis_runtime.update_job(job_store, job_id, status="completed", stage="completed")
        with self.actual_worker(resume):
            with _delivery(self.job_id) as (invoke, visibility, delete, enqueue):
                enqueue.side_effect = RuntimeError("Synthetic enqueue unavailable")
                self.assertEqual(invoke()[0], 500)
                enqueue.assert_called_once()
                delete.assert_not_called()
                visibility.assert_not_called()
            self.assertEqual(self.owner_b.get(self.job_id)["status"], "queued")
            self.assertEqual(self.blobs.get_json(checkpoint["prepared"]), {"committedPreparation": 1})
            with _delivery(self.job_id) as (invoke, visibility, delete, enqueue):
                self.assertEqual(invoke()[0], 200)
                delete.assert_called_once()
                visibility.assert_not_called()
                enqueue.assert_not_called()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.owner_b.get(self.job_id)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
