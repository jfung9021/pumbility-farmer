"""Guarded stale-analysis recovery and optional isolated PostgreSQL race checks."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import os
from queue import Queue
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import psycopg
from psycopg.types.json import Jsonb

import pumbility_store
from pumbility_store import EXPECTED_PUMBILITY_MIGRATION, PumbilityJobStore, require_loopback_database_url


class ExpiredAnalysisRecoveryStoreTests(unittest.TestCase):
    def call(self, rows):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.side_effect = rows
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor
        with patch("pumbility_store._connect", return_value=connection), \
             patch("pumbility_store._assert_schema") as schema:
            result = PumbilityJobStore(database_url="postgresql://localhost/local").fail_stale_active_job(
                "synthetic-job", stale_after_seconds=300)
        schema.assert_called_once_with(cursor)
        return result, cursor

    def test_success_locks_and_rechecks_database_fences_before_exact_head_delete(self):
        result, cursor = self.call([("job-uuid",)] * 4)
        self.assertTrue(result)
        calls = cursor.execute.call_args_list
        self.assertEqual(len(calls), 4)
        candidate, head, update, delete = [" ".join(call.args[0].split()).lower() for call in calls]
        self.assertIn("for no key update", candidate)
        self.assertIn("external_key = %s and kind = 'analysis'", candidate)
        for query in (candidate, update):
            self.assertIn("status in ('queued', 'running')", query)
            self.assertIn("cancellation_requested_at is null", query)
            self.assertIn("updated_at <", query)
            self.assertIn("make_interval(secs => %s)", query)
            self.assertIn("lease_expires_at <", query)
            self.assertIn("lease_owner is null and", query)
            self.assertIn("lease_expires_at is null", query)
        self.assertIn("name = 'active:analysis' and job_id = %s for update", head)
        self.assertIn("payload = job.payload || jsonb_build_object", update)
        self.assertIn("safe_error = jsonb_build_object", update)
        self.assertNotRegex(update, r"\berror\s*=")
        self.assertIn("retry_at = recovery.occurred_at", update)
        self.assertIn("completed_at = recovery.occurred_at", update)
        self.assertIn("lease_owner = null, lease_expires_at = null", update)
        self.assertIn("exists (select 1 from pumbility.job_heads", update)
        self.assertIn("name = 'active:analysis' and job_id = job.id", update)
        self.assertEqual(delete, "delete from pumbility.job_heads where name = 'active:analysis' and job_id = %s returning job_id")
        self.assertEqual(calls[0].args[1], ("synthetic-job", 300))
        self.assertEqual(calls[1].args[1], ("job-uuid",))
        self.assertEqual(calls[2].args[1][-2:], ("job-uuid", 300))
        self.assertEqual(calls[3].args[1], ("job-uuid",))

    def test_noop_outcomes_never_delete_an_unmatched_or_protected_head(self):
        for rows, statements in (([None], 1), ([("job-uuid",), None], 2),
                                 ([("job-uuid",), ("job-uuid",), None], 3)):
            with self.subTest(statements=statements):
                result, cursor = self.call(rows)
                self.assertFalse(result)
                self.assertEqual(cursor.execute.call_count, statements)
                self.assertFalse(any("delete from" in call.args[0] for call in cursor.execute.call_args_list))

    def test_missing_locked_head_at_delete_raises_for_transaction_rollback(self):
        with self.assertRaisesRegex(RuntimeError, "released atomically"):
            self.call([("job-uuid",), ("job-uuid",), ("job-uuid",), None])

    def test_invalid_interval_or_job_id_never_connects(self):
        store = PumbilityJobStore(database_url="postgresql://localhost/local")
        with patch("pumbility_store._connect") as connect:
            for job_id, interval in (("", 300), (None, 300), ("job", 0), ("job", -1),
                                     ("job", True), ("job", 1.5)):
                with self.subTest(job_id=job_id, interval=interval), self.assertRaises(ValueError):
                    store.fail_stale_active_job(job_id, stale_after_seconds=interval)
            connect.assert_not_called()


DATABASE_URL = os.getenv("PUMBILITY_EXPIRED_ANALYSIS_TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(DATABASE_URL, "Set the dedicated local expired-analysis recovery database URL")
class ExpiredAnalysisRecoveryDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_loopback_database_url(DATABASE_URL)
        if urlparse(DATABASE_URL).path != "/piu_expired_analysis_recovery_test":
            raise ValueError("Recovery tests require their dedicated disposable database.")
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("""create schema if not exists pumbility;
                create table if not exists pumbility.schema_metadata(key text primary key, value text);
                create table if not exists pumbility.mixes(mix_key text primary key);
                drop table if exists pumbility.job_heads;
                drop table if exists pumbility.jobs;
                -- Jobs/head DDL mirrors the owner migration 20260813010000,
                -- including all column names, defaults, checks and foreign keys.
                create table pumbility.jobs (
                    id uuid primary key default gen_random_uuid(),
                    external_key text not null unique check (btrim(external_key) <> ''),
                    kind text not null check (btrim(kind) <> ''),
                    status text not null default 'queued'
                        check (status in ('queued', 'running', 'completed', 'failed', 'cancelled')),
                    stage text not null default 'queued' check (btrim(stage) <> ''),
                    mix_key text references pumbility.mixes(mix_key) on update cascade on delete restrict,
                    attempt integer not null default 0 check (attempt >= 0),
                    payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
                    progress jsonb not null default '{}'::jsonb check (jsonb_typeof(progress) = 'object'),
                    lease_owner text,
                    lease_expires_at timestamptz,
                    heartbeat_at timestamptz,
                    cancellation_requested_at timestamptz,
                    retry_at timestamptz,
                    safe_error jsonb check (safe_error is null or jsonb_typeof(safe_error) = 'object'),
                    created_at timestamptz not null default statement_timestamp(),
                    updated_at timestamptz not null default statement_timestamp(),
                    started_at timestamptz,
                    completed_at timestamptz,
                    check ((lease_owner is null) = (lease_expires_at is null)),
                    check (status <> 'running' or (lease_owner is not null and started_at is not null)),
                    check (status not in ('completed', 'cancelled') or completed_at is not null)
                );
                create table pumbility.job_heads (
                    name text primary key check (btrim(name) <> ''),
                    job_id uuid not null references pumbility.jobs(id) on delete restrict,
                    updated_at timestamptz not null default statement_timestamp()
                );""")
            cursor.execute("insert into pumbility.schema_metadata values('migration_version',%s) "
                           "on conflict(key) do update set value=excluded.value", (EXPECTED_PUMBILITY_MIGRATION,))

    def setUp(self):
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("truncate pumbility.job_heads, pumbility.jobs")
        self.store = PumbilityJobStore(database_url=DATABASE_URL)
        self.payload = {"id": "synthetic-job", "status": "running", "stage": "analyzing",
                        "updatedAtUtc": "2020-01-01T00:00:00Z", "attempt": 2,
                        "privateCheckpoint": {"generation": "synthetic-generation", "complete": [1, 2]},
                        "progress": {"current": 3, "total": 7, "percent": 42}}

    def seed(self, *, external_key="synthetic-job", status="running", kind="analysis",
             owner="expired-owner", lease_seconds=-10, age_seconds=1200, active=True, cancelled=False):
        payload = {**copy.deepcopy(self.payload), "id": external_key, "status": status}
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("""insert into pumbility.jobs(external_key,kind,status,stage,payload,progress,
                lease_owner,lease_expires_at,updated_at,cancellation_requested_at,started_at,completed_at)
                values(%s,%s,%s,'analyzing',%s,%s,%s,
                    case when %s::integer is null then null else clock_timestamp()+make_interval(secs=>%s) end,
                    clock_timestamp()-make_interval(secs=>%s),
                    case when %s then clock_timestamp() else null end,
                    case when %s='running' then clock_timestamp()-interval '20 minutes' else null end,
                    case when %s in ('completed','cancelled') then clock_timestamp() else null end) returning id""",
                (external_key, kind, status, Jsonb(payload), Jsonb(payload["progress"]), owner,
                 lease_seconds, lease_seconds, age_seconds, cancelled, status, status))
            identifier = cursor.fetchone()[0]
            if active:
                cursor.execute("insert into pumbility.job_heads(name,job_id) values('active:analysis',%s)", (identifier,))
            cursor.execute("insert into pumbility.job_heads(name,job_id) values(%s,%s)", ("latest:"+external_key, identifier))
        return identifier

    def recover(self):
        return self.store.fail_stale_active_job("synthetic-job", stale_after_seconds=300)

    def state(self, identifier):
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("select status,payload,progress,safe_error,lease_owner,lease_expires_at,"
                           "updated_at,completed_at,retry_at from pumbility.jobs where id=%s", (identifier,))
            row = cursor.fetchone()
            cursor.execute("select name,job_id from pumbility.job_heads order by name")
            return row, cursor.fetchall()

    def test_expired_worker_releases_only_active_head_and_preserves_private_payload(self):
        identifier = self.seed()
        self.assertTrue(self.recover())
        row, heads = self.state(identifier)
        self.assertEqual(row[0], "failed")
        for key in ("id", "stage", "attempt", "privateCheckpoint"):
            self.assertEqual(row[1][key], self.payload[key])
        self.assertEqual(row[1]["status"], "failed")
        self.assertEqual(row[1]["error"], "The analysis worker stopped reporting progress.")
        self.assertEqual(row[2], row[1]["progress"])
        self.assertEqual(row[3], {"message": row[1]["error"]})
        self.assertEqual(row[4:6], (None, None))
        self.assertEqual(row[6], row[7])
        self.assertEqual(row[7], row[8])
        self.assertEqual(row[1]["updatedAtUtc"], row[1]["completedAtUtc"])
        self.assertEqual(row[1]["completedAtUtc"], row[1]["retryAllowedAtUtc"])
        self.assertEqual(heads, [("latest:synthetic-job", identifier)])
        self.assertFalse(self.recover())

    def test_database_lease_status_cancellation_kind_and_heartbeat_fences_are_authoritative(self):
        cases = ({"lease_seconds": 600}, {"status": "completed"}, {"status": "failed"},
                 {"status": "cancelled"}, {"age_seconds": 0},
                 {"cancelled": True}, {"kind": "player_recommendation"}, {"active": False})
        for settings in cases:
            with self.subTest(settings=settings):
                self.setUp()
                identifier = self.seed(**settings)
                before = self.state(identifier)
                self.assertFalse(self.recover())
                self.assertEqual(self.state(identifier), before)

    def test_stale_unleased_jobs_can_release_but_recent_queued_jobs_cannot(self):
        for status, age, expected in (("queued", 1200, True), ("queued", 0, False)):
            with self.subTest(status=status, age=age):
                self.setUp()
                self.seed(status=status, owner=None, lease_seconds=None, age_seconds=age)
                self.assertIs(self.recover(), expected)

    def wait_until_locked(self, connection, backend_pid):
        deadline = time.monotonic() + 5
        with connection.cursor() as cursor:
            while time.monotonic() < deadline:
                cursor.execute("select wait_event_type from pg_stat_activity where pid=%s", (backend_pid,))
                row = cursor.fetchone()
                if row and row[0] == "Lock":
                    return
                time.sleep(.01)
        self.fail("The synthetic recovery did not reach its expected row lock.")

    def test_renewal_reclaim_and_completion_winning_the_job_lock_prevent_recovery(self):
        for winner in ("heartbeat", "reclaim", "complete"):
            with self.subTest(winner=winner):
                self.setUp()
                identifier = self.seed()
                backend_pids = Queue()
                original_connect = pumbility_store._connect

                def tracked_connect(url):
                    connection = original_connect(url)
                    backend_pids.put(connection.info.backend_pid)
                    return connection

                with ThreadPoolExecutor(max_workers=1) as executor:
                    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
                        cursor.execute("select id from pumbility.jobs where id=%s for update", (identifier,))
                        with patch("pumbility_store._connect", side_effect=tracked_connect):
                            future = executor.submit(self.recover)
                            self.wait_until_locked(connection, backend_pids.get(timeout=5))
                            if winner == "complete":
                                cursor.execute("update pumbility.jobs set status='completed',lease_owner=null,"
                                               "lease_expires_at=null,completed_at=clock_timestamp(),"
                                               "updated_at=clock_timestamp() where id=%s", (identifier,))
                            else:
                                cursor.execute("update pumbility.jobs set lease_owner=%s,"
                                               "lease_expires_at=clock_timestamp()+interval '600 seconds',"
                                               "updated_at=clock_timestamp() where id=%s",
                                               ("new-owner" if winner == "reclaim" else "expired-owner", identifier))
                    self.assertFalse(future.result(timeout=5))
                row, heads = self.state(identifier)
                self.assertEqual(row[0], "completed" if winner == "complete" else "running")
                self.assertIn(("active:analysis", identifier), heads)

    def test_head_replacement_winning_the_head_lock_is_not_cleared_or_failed(self):
        identifier = self.seed()
        replacement = self.seed(external_key="replacement-job", lease_seconds=600, age_seconds=0, active=False)
        backend_pids = Queue()
        original_connect = pumbility_store._connect

        def tracked_connect(url):
            connection = original_connect(url)
            backend_pids.put(connection.info.backend_pid)
            return connection

        with ThreadPoolExecutor(max_workers=1) as executor:
            with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
                cursor.execute("select job_id from pumbility.job_heads where name='active:analysis' for update")
                with patch("pumbility_store._connect", side_effect=tracked_connect):
                    future = executor.submit(self.recover)
                    self.wait_until_locked(connection, backend_pids.get(timeout=5))
                    cursor.execute("update pumbility.job_heads set job_id=%s where name='active:analysis'", (replacement,))
            self.assertFalse(future.result(timeout=5))
        row, heads = self.state(identifier)
        self.assertEqual(row[0], "running")
        self.assertIn(("active:analysis", replacement), heads)


if __name__ == "__main__":
    unittest.main()
