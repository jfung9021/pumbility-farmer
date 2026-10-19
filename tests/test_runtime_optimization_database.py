"""Real PostgreSQL checks, restricted to a dedicated disposable local database."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

import psycopg
from psycopg.types.json import Jsonb

from analysis_runtime import _verified_snapshot_reference, _write_typed_checkpoint_snapshot
from mix_registry import resolve_mix
from pumbility_store import EXPECTED_PUMBILITY_MIGRATION, PumbilityArtifactStore, require_loopback_database_url
from tier_publication_jobs import TierPublicationConflict

DATABASE_URL = os.getenv("PUMBILITY_RUNTIME_OPTIMIZATION_TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(DATABASE_URL, "Set the dedicated local runtime optimization database URL")
class RuntimeOptimizationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_loopback_database_url(DATABASE_URL)
        if urlparse(DATABASE_URL).path != "/piu_runtime_optimization_test":
            raise ValueError("Runtime optimization tests require their dedicated disposable database.")
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("""create schema if not exists pumbility;
                create table if not exists pumbility.schema_metadata(key text primary key, value text);
                create table if not exists pumbility.artifacts(
                    object_key text primary key, media_type text, payload_json jsonb, storage_bucket text,
                    storage_object_path text, sha256 text, byte_size bigint, validated_at timestamptz, updated_at timestamptz);
                create table if not exists pumbility.jobs(
                    id uuid primary key default gen_random_uuid(), external_key text unique, status text, stage text,
                    payload jsonb, lease_owner text, lease_expires_at timestamptz, cancellation_requested_at timestamptz,
                    completed_at timestamptz, updated_at timestamptz);
                create table if not exists pumbility.job_heads(name text primary key, job_id uuid);""")
            cursor.execute("insert into pumbility.schema_metadata values('migration_version',%s) on conflict(key) do update set value=excluded.value",
                           (EXPECTED_PUMBILITY_MIGRATION,))

    def setUp(self):
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("truncate pumbility.artifacts, pumbility.job_heads, pumbility.jobs")
        self.store = PumbilityArtifactStore(database_url=DATABASE_URL)

    def test_batched_jsonb_normalization_and_snapshot_receipts_round_trip(self):
        self.store.put_json_batch({"one": {"value": 1e20}, "two": {"negativeZero": -0.0}}, max_items=2)
        self.assertEqual(self.store.get_json("one"), {"value": 100000000000000000000})
        self.assertEqual(self.store.get_json("two"), {"negativeZero": 0.0})
        spec = resolve_mix("phoenix2")
        reference = _write_typed_checkpoint_snapshot(self.store, job_id="test", mix_spec=spec,
            snapshot={"mix": "Phoenix2", "players": [], "charts": [], "scores": []})
        self.assertTrue(_verified_snapshot_reference(self.store, checkpoint={"jobId": "test"}, reference=reference, mix_spec=spec))
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("update pumbility.artifacts set payload_json=payload_json where object_key=%s", (reference["pathname"],))
        with self.assertRaisesRegex(ValueError, "revision changed"):
            _verified_snapshot_reference(self.store, checkpoint={"jobId": "test"}, reference=reference, mix_spec=spec)

    def test_failure_rolls_back_current_batch_without_partial_metadata(self):
        original = PumbilityArtifactStore._put_json_row

        def write(cursor, path, value, **kwargs):
            original(cursor, path, value, **kwargs)
            if path == "two":
                raise ValueError("injected batch failure")

        with patch.object(self.store, "_put_json_row", side_effect=write), self.assertRaises(ValueError):
            self.store.put_json_batch({"one": {"value": 1}, "two": {"value": 2}})
        self.assertIsNone(self.store.get_json("one"))
        self.assertIsNone(self.store.get_json("two"))

    def seed_backfill(self):
        payload = {"id": "goal-test", "status": "running", "operation": "scoring-goal-backfill"}
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("""insert into pumbility.jobs(external_key,status,stage,payload,lease_owner,lease_expires_at)
                values('goal-test','running','backfill',%s,'owner',clock_timestamp()+interval '60 seconds') returning id""", (Jsonb(payload),))
            identifier = cursor.fetchone()[0]
            cursor.execute("insert into pumbility.job_heads values('active:tier-publication',%s)", (identifier,))
        return self.store.tier_publication_revisions()

    def finish(self, revisions):
        self.store.complete_scoring_goal_backfill(job_id="goal-test", lease_owner="owner", expected_revisions=revisions,
            completed_job={"id": "goal-test", "status": "completed", "stage": "cached", "operation": "scoring-goal-backfill"})

    def test_cache_completion_is_atomic_and_does_not_publish_a_tier_or_model(self):
        expected = self.seed_backfill()
        self.finish(expected)
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("select status,lease_owner from pumbility.jobs where external_key='goal-test'")
            self.assertEqual(cursor.fetchone(), ("completed", None))
            cursor.execute("select count(*) from pumbility.job_heads where name='active:tier-publication'")
            self.assertEqual(cursor.fetchone()[0], 0)
        self.assertEqual(self.store.tier_publication_revisions(), expected)

    def test_epoch_drift_cancellation_and_expired_owner_never_complete(self):
        for condition in ("source", "cancel", "expired"):
            with self.subTest(condition=condition):
                self.setUp()
                expected = self.seed_backfill()
                with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
                    if condition == "source":
                        cursor.execute("update pumbility.artifacts set updated_at=clock_timestamp()")
                    elif condition == "cancel":
                        cursor.execute("update pumbility.jobs set cancellation_requested_at=clock_timestamp()")
                    else:
                        cursor.execute("update pumbility.jobs set lease_expires_at=clock_timestamp()-interval '1 second'")
                with self.assertRaises(TierPublicationConflict):
                    self.finish(expected)
                with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
                    cursor.execute("select status from pumbility.jobs where external_key='goal-test'")
                    self.assertEqual(cursor.fetchone()[0], "running")
