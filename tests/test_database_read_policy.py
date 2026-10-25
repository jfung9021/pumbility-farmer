from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import unittest
from unittest.mock import MagicMock, patch

import psycopg

from analysis_budget import invocation_budget, work_budget_available
from database_read_policy import (
    BACKGROUND_READ_ATTEMPTS,
    BACKGROUND_READ_TIMEOUT_MILLISECONDS,
    background_database_reads,
    is_statement_timeout_error,
    read_statement_timeout_milliseconds,
)
from pumbility_store import (
    EXPECTED_PUMBILITY_MIGRATION,
    PumbilityArtifactStore,
    PumbilityJobStore,
    READ_STATEMENT_TIMEOUT_MILLISECONDS,
    _canonical_json_bytes,
)


def statement_timeout():
    return psycopg.errors.QueryCanceled("canceling statement due to statement timeout")


class StatementTimeoutClassificationTests(unittest.TestCase):
    def test_requires_actual_server_statement_timeout(self):
        self.assertTrue(is_statement_timeout_error(statement_timeout()))
        self.assertTrue(is_statement_timeout_error(psycopg.errors.QueryCanceled(
            "canceling statement due to statement timeout\nCONTEXT: private context")))
        for error in (
            RuntimeError("canceling statement due to statement timeout"),
            psycopg.errors.QueryCanceled("canceling statement due to user request"),
            psycopg.errors.QueryCanceled(""),
            psycopg.OperationalError("connection timed out"),
        ):
            with self.subTest(error=type(error).__name__, message=str(error)):
                self.assertFalse(is_statement_timeout_error(error))

    def test_background_policy_resets_after_failure_and_nested_exit(self):
        self.assertEqual(read_statement_timeout_milliseconds(10_000), 10_000)
        self.assertIsNone(read_statement_timeout_milliseconds(None))
        with self.assertRaisesRegex(ValueError, "synthetic"):
            with background_database_reads():
                self.assertEqual(read_statement_timeout_milliseconds(10_000), 30_000)
                with background_database_reads():
                    self.assertEqual(read_statement_timeout_milliseconds(10_000), 30_000)
                self.assertEqual(read_statement_timeout_milliseconds(10_000), 30_000)
                raise ValueError("synthetic")
        self.assertEqual(read_statement_timeout_milliseconds(10_000), 10_000)

    def test_background_deadline_respects_remaining_io_budget(self):
        with patch("analysis_budget.time.monotonic", side_effect=[100.0, 255.0, 260.0]):
            with invocation_budget(10), background_database_reads():
                self.assertEqual(read_statement_timeout_milliseconds(10_000), 5_000)
                self.assertEqual(read_statement_timeout_milliseconds(10_000), 1)

    def test_expired_numerical_budget_preserves_checkpoint_read_deadline(self):
        with patch("analysis_budget.time.monotonic", return_value=100.0) as clock:
            with invocation_budget(), background_database_reads():
                clock.return_value = 700.0
                self.assertFalse(work_budget_available())
                self.assertEqual(read_statement_timeout_milliseconds(10_000), 30_000)


class BackgroundDatabaseReadTests(unittest.TestCase):
    def setUp(self):
        self.store = PumbilityArtifactStore(database_url="postgresql://localhost/synthetic")
        self.transactions = []
        self.cursors = []

    @contextmanager
    def connection(self, outcomes):
        cursor = MagicMock()
        outcome = outcomes[len(self.cursors)]
        if isinstance(outcome, BaseException):
            cursor.fetchone.side_effect = outcome
        else:
            cursor.fetchone.return_value = outcome
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        index = len(self.cursors)
        self.cursors.append(cursor)
        self.transactions.append((index, "enter"))
        try:
            yield connection
        finally:
            self.transactions.append((index, "exit"))

    def connections(self, outcomes):
        return patch("pumbility_store._read_connect", side_effect=lambda _url: self.connection(outcomes))

    def artifact(self, payload):
        body = _canonical_json_bytes(payload)
        return (EXPECTED_PUMBILITY_MIGRATION, True, payload, hashlib.sha256(body).hexdigest(), len(body))

    def test_browser_keeps_ten_second_deadline_and_does_not_retry(self):
        with self.connections([statement_timeout()]), self.assertRaises(psycopg.errors.QueryCanceled):
            self.store.get_json("analysis/private/synthetic-secret.json")
        self.assertEqual(len(self.cursors), 1)
        self.cursors[0].execute.assert_any_call(
            f"set local statement_timeout = '{READ_STATEMENT_TIMEOUT_MILLISECONDS}ms'")

    def test_transient_artifact_timeout_retries_after_transaction_exit(self):
        payload = {"private": "synthetic"}
        with self.connections([statement_timeout(), self.artifact(payload)]), \
                background_database_reads(), self.assertLogs("pumbility.database", level="WARNING") as captured:
            result = self.store.get_json("analysis/private/synthetic-secret.json")
        self.assertEqual(result, payload)
        self.assertEqual(self.transactions, [(0, "enter"), (0, "exit"), (1, "enter"), (1, "exit")])
        for cursor in self.cursors:
            cursor.execute.assert_any_call(
                f"set local statement_timeout = '{BACKGROUND_READ_TIMEOUT_MILLISECONDS}ms'")
        event = json.loads(captured.records[0].message)
        self.assertEqual(event["operation"], "artifact-json")
        self.assertEqual(event["attempt"], 1)
        self.assertTrue(event["retrying"])
        self.assertNotIn("synthetic-secret", captured.output[0])
        self.assertNotIn("canceling statement", captured.output[0])
        self.assertNotIn("select", captured.output[0].lower())

    def test_timeout_exhaustion_is_bounded(self):
        with self.connections([statement_timeout() for _ in range(BACKGROUND_READ_ATTEMPTS)]), \
                background_database_reads(), self.assertLogs("pumbility.database", level="WARNING") as captured, \
                self.assertRaises(psycopg.errors.QueryCanceled):
            self.store.get_json("analysis/private/synthetic.json")
        self.assertEqual(len(self.cursors), BACKGROUND_READ_ATTEMPTS)
        self.assertFalse(json.loads(captured.records[-1].message)["retrying"])

    def test_explicit_cancellation_and_unrelated_errors_are_never_retried(self):
        for error in (
            psycopg.errors.QueryCanceled("canceling statement due to user request"),
            psycopg.OperationalError("synthetic connection failure"),
            ValueError("synthetic invalid data"),
        ):
            with self.subTest(error=type(error).__name__):
                self.cursors.clear()
                self.transactions.clear()
                with self.connections([error]), background_database_reads(), self.assertRaises(type(error)):
                    self.store.get_json("analysis/private/synthetic.json")
                self.assertEqual(len(self.cursors), 1)

    def test_no_retry_after_invocation_io_deadline(self):
        with patch("analysis_budget.time.monotonic", return_value=100.0) as clock:
            with invocation_budget(), self.connections([statement_timeout()]), background_database_reads(), \
                    self.assertRaises(psycopg.errors.QueryCanceled):
                clock.return_value = 850.0
                self.store.get_json("analysis/private/synthetic.json")
        self.assertEqual(len(self.cursors), 1)
        self.cursors[0].execute.assert_any_call("set local statement_timeout = '1ms'")

    def test_fresh_transaction_retry_still_works_after_numerical_work_expires(self):
        payload = {"checkpoint": "synthetic"}
        with patch("analysis_budget.time.monotonic", return_value=100.0) as clock:
            with invocation_budget(), background_database_reads(), \
                    self.connections([statement_timeout(), self.artifact(payload)]):
                clock.return_value = 700.0
                self.assertFalse(work_budget_available())
                self.assertEqual(self.store.get_json("analysis/private/synthetic.json"), payload)
        self.assertEqual(self.transactions, [(0, "enter"), (0, "exit"), (1, "enter"), (1, "exit")])
        for cursor in self.cursors:
            cursor.execute.assert_any_call("set local statement_timeout = '30000ms'")

    def test_revision_receipt_and_job_reads_use_same_policy(self):
        jobs = PumbilityJobStore(database_url="postgresql://localhost/synthetic")
        cases = (
            (lambda: self.store.artifact_revision("private"), ("sha", 5, "revision"),
             {"sha256": "sha", "byteSize": 5, "revision": "revision"}),
            (lambda: self.store.typed_snapshot_receipt("private"),
             ("sha", 5, "rev", "snapshot", "3", "job", "phoenix2"),
             {"artifactSha256": "sha", "byteSize": 5, "revision": "rev", "snapshotSha256": "snapshot",
              "schemaVersion": "3", "jobId": "job", "mix": "phoenix2"}),
            (lambda: jobs.get("synthetic"), (EXPECTED_PUMBILITY_MIGRATION, True, {"id": "synthetic"}),
             {"id": "synthetic"}),
        )
        for read, row, expected in cases:
            with self.subTest(read=read):
                self.cursors.clear()
                self.transactions.clear()
                with self.connections([statement_timeout(), row]), background_database_reads():
                    self.assertEqual(read(), expected)
                self.assertEqual(len(self.cursors), 2)

    def test_canonical_snapshot_read_retries_with_worker_deadline(self):
        snapshot = {"players": [], "charts": [], "scores": []}
        with patch("pumbility_store._connect", side_effect=lambda _url: self.connection([
                (EXPECTED_PUMBILITY_MIGRATION,), (EXPECTED_PUMBILITY_MIGRATION,)])), \
                patch("scripts.reconcile_pumbility_supabase._database_snapshot",
                      side_effect=[statement_timeout(), snapshot]), background_database_reads():
            self.assertEqual(self.store.get_json("analysis/private/phoenix2-current.json"), snapshot)
        self.assertEqual(self.transactions, [(0, "enter"), (0, "exit"), (1, "enter"), (1, "exit")])
        for cursor in self.cursors:
            cursor.execute.assert_any_call("set local statement_timeout = '30000ms'")

    def test_browser_canonical_snapshot_keeps_existing_session_policy(self):
        snapshot = {"players": [], "charts": [], "scores": []}
        with patch("pumbility_store._connect", side_effect=lambda _url: self.connection([
                (EXPECTED_PUMBILITY_MIGRATION,)])), \
                patch("scripts.reconcile_pumbility_supabase._database_snapshot", return_value=snapshot):
            self.assertEqual(self.store.get_json("analysis/private/phoenix2-current.json"), snapshot)
        self.assertFalse(any("statement_timeout" in call.args[0]
                             for call in self.cursors[0].execute.call_args_list))

    def test_write_is_not_retried_by_background_policy(self):
        connection = MagicMock()
        with patch("pumbility_store._connect", return_value=connection) as connect, \
                patch("pumbility_store._assert_schema"), \
                patch.object(self.store, "_put_json_row", side_effect=statement_timeout()) as put, \
                background_database_reads(), self.assertRaises(psycopg.errors.QueryCanceled):
            self.store.put_json("private", {"private": "synthetic"})
        connect.assert_called_once()
        put.assert_called_once()


if __name__ == "__main__":
    unittest.main()
