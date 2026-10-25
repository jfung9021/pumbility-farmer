"""Publication stays inside the callback I/O deadline without replaying writes."""
from __future__ import annotations

from copy import deepcopy
import re
import unittest
from unittest.mock import Mock, patch

from analysis_budget import invocation_budget
from pumbility_store import (
    EXPECTED_PUMBILITY_MIGRATION,
    PUBLICATION_LOCK_TIMEOUT_MILLISECONDS,
    PUBLICATION_STATEMENT_TIMEOUT_MILLISECONDS,
    PumbilityArtifactStore,
)


class PublicationConnection:
    """Transactional fake retaining public pointers only on successful commit."""

    def __init__(self, after_query=None):
        self.artifacts = {"analysis/recommendations/latest.json": {"generationKey": "previous"}}
        self.pending = None
        self.queries = []
        self.timeouts = []
        self.after_query = after_query
        self.rowcount = 1
        self.row = None
        self.commits = 0
        self.rollbacks = 0
        self.transaction = Mock(side_effect=self.begin)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self

    def begin(self):
        connection = self

        class Transaction:
            def __enter__(self):
                connection.pending = deepcopy(connection.artifacts)

            def __exit__(self, error_type, _error, _traceback):
                if error_type is None:
                    connection.artifacts = connection.pending
                    connection.commits += 1
                else:
                    connection.rollbacks += 1
                connection.pending = None
                return False

        return Transaction()

    def execute(self, query, params=None):
        sql = " ".join(query.split())
        self.queries.append((sql, params))
        timeout = re.fullmatch(r"set local (lock_timeout|statement_timeout) = '(\d+)ms'", sql)
        if timeout:
            self.timeouts.append((timeout[1], int(timeout[2])))
            return self
        if "from pumbility.schema_metadata" in sql:
            self.row = (EXPECTED_PUMBILITY_MIGRATION,)
        elif "select ar.status, mg.status" in sql:
            self.row = ("shadow", "shadow", "candidate", "analysis-uuid")
        elif sql.startswith("select payload_json ->> 'generationKey'"):
            self.row = ("previous",)
        elif sql.startswith("insert into pumbility.artifacts"):
            if "select %s" in sql:
                self.pending[params[0]] = deepcopy(self.pending[params[1]])
            else:
                value = deepcopy(params[1].obj)
                self.pending[params[0]] = value
                self.row = (value,)
        if self.after_query is not None:
            self.after_query(sql)
        return self

    def fetchone(self):
        return self.row


class PublicationIoBudgetTests(unittest.TestCase):
    def setUp(self):
        self.connection = PublicationConnection()
        self.store = PumbilityArtifactStore(database_url="postgresql://unused.example/test")
        self.connect = patch("pumbility_store._connect", return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def publish(self):
        self.store.publish_generation(
            {"analysis/recommendations/latest.json": {"generationKey": "candidate"}},
            generation_key="candidate", analysis_run_id="analysis-uuid",
            model_generation_id="model-uuid",
        )

    def test_standalone_publication_keeps_existing_statement_and_lock_limits(self):
        self.publish()
        self.assertEqual(self.connection.timeouts, [
            ("lock_timeout", PUBLICATION_LOCK_TIMEOUT_MILLISECONDS),
            ("statement_timeout", PUBLICATION_STATEMENT_TIMEOUT_MILLISECONDS),
        ])
        self.assertEqual(self.connection.commits, 1)

    def test_near_deadline_caps_every_statement_and_lock_to_remaining_io(self):
        with patch("analysis_budget.time.monotonic", return_value=0.0) as clock:
            with invocation_budget():
                clock.return_value = 748.0
                self.publish()
        self.assertTrue(self.connection.timeouts)
        self.assertTrue(all(0 < timeout <= 2000 for _, timeout in self.connection.timeouts))
        self.assertEqual(self.connection.commits, 1)

    def test_expired_io_performs_no_connection_transaction_or_dml(self):
        with patch("analysis_budget.time.monotonic", return_value=0.0) as clock:
            with invocation_budget():
                clock.return_value = 750.0
                with self.assertRaisesRegex(TimeoutError, "publication.*deadline"):
                    self.publish()
        self.connect.assert_not_called()
        self.connection.transaction.assert_not_called()
        self.assertEqual(self.connection.queries, [])

    def test_connection_time_cannot_start_a_transaction_after_io_expiry(self):
        with patch("analysis_budget.time.monotonic", return_value=0.0) as clock:
            with invocation_budget():
                def connect(_url):
                    clock.return_value = 750.0
                    return self.connection

                self.connect.side_effect = connect
                with self.assertRaisesRegex(TimeoutError, "publication.*deadline"):
                    self.publish()
        self.connection.transaction.assert_not_called()
        self.assertEqual(self.connection.queries, [])

    def test_time_spent_between_queries_reduces_subsequent_timeout(self):
        with patch("analysis_budget.time.monotonic", return_value=0.0) as clock:
            with invocation_budget():
                clock.return_value = 700.0
                self.connection.after_query = lambda _sql: setattr(clock, "return_value", clock.return_value + 1)
                self.publish()
        statements = [value for name, value in self.connection.timeouts if name == "statement_timeout"]
        self.assertGreater(len(statements), 2)
        self.assertTrue(all(right < left for left, right in zip(statements, statements[1:])))

    def test_expiry_after_pointer_write_rolls_back_before_next_statement(self):
        before = deepcopy(self.connection.artifacts)
        with patch("analysis_budget.time.monotonic", return_value=0.0) as clock:
            with invocation_budget():
                clock.return_value = 749.0

                def expire_after_insert(sql):
                    if sql.startswith("insert into pumbility.artifacts") and "returning payload_json" in sql:
                        clock.return_value = 750.0

                self.connection.after_query = expire_after_insert
                with self.assertRaisesRegex(TimeoutError, "publication.*deadline"):
                    self.publish()
        self.assertEqual(self.connection.artifacts, before)
        self.assertEqual(self.connection.commits, 0)
        self.assertEqual(self.connection.rollbacks, 1)
        self.assertFalse(any(sql.startswith("update pumbility.analysis_runs") for sql, _ in self.connection.queries))

    def test_expiry_after_final_update_rolls_back_instead_of_committing(self):
        before = deepcopy(self.connection.artifacts)
        with patch("analysis_budget.time.monotonic", return_value=0.0) as clock:
            with invocation_budget():
                clock.return_value = 749.0
                self.connection.after_query = lambda sql: setattr(clock, "return_value", 750.0) if sql.startswith("update pumbility.model_generations") else None
                with self.assertRaisesRegex(TimeoutError, "publication.*deadline"):
                    self.publish()
        self.assertEqual(self.connection.artifacts, before)
        self.assertEqual(self.connection.commits, 0)
        self.assertEqual(self.connection.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
