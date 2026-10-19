"""Optional real PostgreSQL regression checks against an isolated local schema."""

from __future__ import annotations

from contextlib import contextmanager
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg

from api.refresh_status import read_refresh_status
from phoenix2_sync import isoformat_utc, utc_now
from pumbility_contract import combined_tier_blob_path, recommendation_blob_path
from pumbility_store import (
    JobLeaseHeldError, JobLeaseLostError, PumbilityArtifactStore,
    PumbilityJobStore, require_loopback_database_url,
)


DATABASE_URL = os.getenv("PUMBILITY_TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(DATABASE_URL, "Set PUMBILITY_TEST_DATABASE_URL to an isolated local Pumbility schema")
class RefreshReliabilityDatabaseTests(unittest.TestCase):
    def setUp(self):
        require_loopback_database_url(DATABASE_URL)
        self.prefix = "refresh-reliability-test-" + uuid4().hex
        self.owner_a = PumbilityJobStore(database_url=DATABASE_URL)
        self.owner_b = PumbilityJobStore(database_url=DATABASE_URL)

    def tearDown(self):
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("delete from pumbility.job_heads where job_id in "
                           "(select id from pumbility.jobs where external_key like %s)", (self.prefix + "%",))
            cursor.execute("delete from pumbility.jobs where external_key like %s", (self.prefix + "%",))
            cursor.execute("delete from pumbility.artifacts where object_key like %s", (self.prefix + "%",))

    def seed(self, *, player=False):
        stamp = isoformat_utc(utc_now())
        value = {"id": self.prefix + "-job", "status": "queued", "stage": "queued",
                 "mix": "phoenix2", "createdAtUtc": stamp, "updatedAtUtc": stamp, "progress": {}}
        if player:
            value.update(kind="player-recommendation-refresh", playerKey=self.prefix + "-player")
        self.owner_a.save(value)
        value.update(status="running", stage="syncing")
        self.owner_a.save(value)
        return value

    def expire(self, job_id):
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("update pumbility.jobs set lease_expires_at=clock_timestamp()-interval '1 second' "
                           "where external_key=%s", (job_id,))

    def test_live_owner_protected_then_expired_analysis_lease_reclaimed(self):
        value = self.seed()
        with self.assertRaises(JobLeaseHeldError):
            self.owner_b.save(value)
        self.expire(value["id"])
        self.owner_b.save(value)
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("select status,lease_owner from pumbility.jobs where external_key=%s", (value["id"],))
            self.assertEqual(cursor.fetchone(), ("running", self.owner_b.lease_owner))
        with self.assertRaises(JobLeaseLostError):
            self.owner_a.save({**value, "status": "failed"})
        self.assertEqual(self.owner_b.get(value["id"])["status"], "running")

    def test_player_reclaim_fences_former_owner_and_publishes_both_artifacts(self):
        value = self.seed(player=True)
        self.expire(value["id"])
        self.owner_b.save(value)
        payloads = {self.prefix + "-state.json": {"state": "new"},
                    self.prefix + "-cache.json": {"cache": "new"}}
        with self.assertRaises(JobLeaseLostError):
            self.owner_a.publish_player_artifacts(value["id"], payloads)
        self.owner_b.publish_player_artifacts(value["id"], payloads)
        artifacts = PumbilityArtifactStore(database_url=DATABASE_URL)
        for pathname, payload in payloads.items():
            self.assertEqual(artifacts.get_json(pathname), payload)

    def test_expiry_during_publication_rolls_back_both_artifacts(self):
        value = self.seed(player=True)
        payloads = {self.prefix + "-state.json": {"state": "new"},
                    self.prefix + "-cache.json": {"cache": "new"}}
        original = PumbilityArtifactStore._put_json_row
        writes = []

        def write_and_expire(cursor, pathname, payload):
            original(cursor, pathname, payload)
            writes.append(pathname)
            cursor.execute("update pumbility.jobs set lease_expires_at=clock_timestamp()-interval '1 second' "
                           "where external_key=%s", (value["id"],))

        with patch.object(PumbilityArtifactStore, "_put_json_row", side_effect=write_and_expire):
            with self.assertRaises(JobLeaseLostError):
                self.owner_a.publish_player_artifacts(value["id"], payloads)
        self.assertEqual(writes, list(payloads))
        artifacts = PumbilityArtifactStore(database_url=DATABASE_URL)
        for pathname in payloads:
            self.assertIsNone(artifacts.get_json(pathname))

    def test_status_projection_queries_real_schema_without_hydrating_artifacts(self):
        # All canonical-path fixture changes live in a rolled-back transaction.
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            try:
                PumbilityArtifactStore._put_json_row(cursor, combined_tier_blob_path(),
                    {"generatedAtUtc": "2026-10-18T02:11:43Z", "privateData": "must not escape"})
                PumbilityArtifactStore._put_json_row(cursor, recommendation_blob_path(),
                    {"generatedAtUtc": "2026-10-17T05:50:30Z", "players": ["private-player"]})

                @contextmanager
                def use_transaction(_database_url):
                    yield cursor

                with patch.dict(os.environ, {"PUMBILITY_DATABASE_URL": DATABASE_URL}), \
                     patch("api.refresh_status._read_cursor", side_effect=use_transaction):
                    result = read_refresh_status()
                self.assertEqual(result["tierGeneratedAtUtc"], "2026-10-18T02:11:43Z")
                self.assertEqual(result["modelGeneratedAtUtc"], "2026-10-17T05:50:30Z")
                self.assertNotIn("private", str(result))
            finally:
                connection.rollback()


if __name__ == "__main__":
    unittest.main()
