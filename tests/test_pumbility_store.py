from __future__ import annotations

import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pumbility_store import (
    CURRENT_SNAPSHOT_RE,
    EXPECTED_PUMBILITY_MIGRATION,
    JOB_LEASE_SECONDS,
    PumbilityJobStore,
    ShadowJobStore,
    ShadowJsonStore,
    _ContinuousLeaseHeartbeat,
    configured_backend,
    require_loopback_database_url,
    select_job_store,
    select_json_store,
)


class BackendConfigurationTests(unittest.TestCase):
    def test_defaults_to_vercel(self) -> None:
        self.assertEqual(configured_backend({}), "vercel")

    def test_rejects_unknown_backend(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be one of"):
            configured_backend({"PUMBILITY_DATA_BACKEND": "other"})

    def test_local_target_guard_accepts_only_loopback(self) -> None:
        for hostname in ("127.0.0.1", "localhost", "[::1]"):
            require_loopback_database_url(
                f"postgresql://postgres:postgres@{hostname}:54322/postgres"
            )
        with self.assertRaisesRegex(ValueError, "non-loopback"):
            require_loopback_database_url(
                "postgresql://postgres:secret@db.example.supabase.co:5432/postgres"
            )

    def test_selector_does_not_construct_supabase_by_default(self) -> None:
        legacy = Mock()
        with patch.dict(os.environ, {"PUMBILITY_DATA_BACKEND": "vercel"}, clear=False):
            self.assertIs(select_json_store(lambda: legacy), legacy)
            self.assertIs(select_job_store(lambda: legacy), legacy)

    def test_only_mix_current_snapshots_match_canonical_projection_hook(self) -> None:
        self.assertIsNotNone(
            CURRENT_SNAPSHOT_RE.fullmatch("analysis/private/phoenix2-current.json")
        )
        self.assertIsNone(CURRENT_SNAPSHOT_RE.fullmatch("analysis/phoenix2/latest.json"))
        self.assertIsNone(
            CURRENT_SNAPSHOT_RE.fullmatch("analysis/private/player-state.json")
        )


class ShadowStoreTests(unittest.TestCase):
    def test_json_reads_primary_and_mirrors_writes(self) -> None:
        primary = Mock()
        shadow = Mock()
        primary.get_json.return_value = {"source": "legacy"}
        store = ShadowJsonStore(primary, shadow)

        self.assertEqual(store.get_json("analysis/latest.json"), {"source": "legacy"})
        store.put_json("analysis/latest.json", {"value": 1})

        primary.put_json.assert_called_once_with("analysis/latest.json", {"value": 1})
        shadow.put_json.assert_called_once_with("analysis/latest.json", {"value": 1})

    def test_shadow_failure_is_fail_open_unless_strict(self) -> None:
        primary = Mock()
        shadow = Mock()
        shadow.put_json.side_effect = RuntimeError("shadow unavailable")
        ShadowJsonStore(primary, shadow).put_json("key", {"value": 1})
        primary.put_json.assert_called_once()

        with self.assertRaisesRegex(RuntimeError, "shadow unavailable"):
            ShadowJsonStore(primary, shadow, strict=True).put_json("key", {"value": 2})

    def test_shadow_bundle_keeps_legacy_order_and_uses_atomic_mirror(self) -> None:
        primary = Mock()
        shadow = Mock()
        payloads = {"combined": {"value": 1}, "latest": {"value": 2}}
        ShadowJsonStore(primary, shadow).put_json_bundle(payloads)
        self.assertEqual(primary.put_json.call_count, 2)
        shadow.put_json_bundle.assert_called_once_with(payloads)

    def test_shadow_snapshot_projection_requires_explicit_relational_write_flag(self) -> None:
        primary = Mock()
        shadow = Mock()
        store = ShadowJsonStore(primary, shadow)
        with patch.dict(os.environ, {}, clear=True):
            store.put_json(
                "analysis/private/phoenix2-current.json",
                {"players": [], "charts": [], "scores": []},
            )
        primary.put_json.assert_called_once()
        shadow.put_json.assert_not_called()

    def test_job_reads_primary_and_mirrors_heads(self) -> None:
        primary = Mock()
        shadow = Mock()
        primary.active_job_id.return_value = "job-1"
        store = ShadowJobStore(primary, shadow)

        self.assertEqual(store.active_job_id(), "job-1")
        store.set_active_job_id("job-2")
        primary.set_active_job_id.assert_called_once_with("job-2")
        shadow.set_active_job_id.assert_called_once_with("job-2")

    def test_shadow_job_heartbeat_renews_only_the_supabase_shadow(self) -> None:
        primary = Mock()
        shadow = Mock()
        handle = ShadowJobStore(primary, shadow).start_lease_heartbeat("job-1")
        try:
            handle.pulse()
        finally:
            handle.stop()

        shadow.heartbeat.assert_called_once_with("job-1")
        self.assertNotIn("heartbeat", [call[0] for call in primary.method_calls])


class ContinuousLeaseHeartbeatTests(unittest.TestCase):
    def test_background_heartbeat_runs_until_stopped(self) -> None:
        called = threading.Event()
        calls: list[int] = []

        def callback() -> None:
            calls.append(1)
            called.set()

        handle = _ContinuousLeaseHeartbeat(callback, interval_seconds=0.01).start()
        self.assertTrue(called.wait(1.0))
        handle.stop()
        stopped_count = len(calls)
        self.assertGreaterEqual(stopped_count, 1)
        self.assertFalse(handle._thread.is_alive())

    def test_background_failure_is_reported_to_the_worker(self) -> None:
        called = threading.Event()

        def callback() -> None:
            called.set()
            raise RuntimeError("lease lost")

        handle = _ContinuousLeaseHeartbeat(callback, interval_seconds=0.01).start()
        self.assertTrue(called.wait(1.0))
        with self.assertRaisesRegex(RuntimeError, "heartbeat failed"):
            handle.stop()


class PumbilityJobHeartbeatTests(unittest.TestCase):
    def test_heartbeat_renews_owned_lease_and_payload_timestamp(self) -> None:
        store = PumbilityJobStore(database_url="postgresql://localhost/local")
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        cursor.fetchone.side_effect = [
            (EXPECTED_PUMBILITY_MIGRATION,),
            ("job-uuid", "running", store.lease_owner, "analyzing", {"current": 1}),
            (True,),
        ]
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.cursor.return_value = cursor
        fake_json_module = SimpleNamespace(Jsonb=lambda payload: payload)

        with (
            patch("pumbility_store._connect", return_value=connection),
            patch.dict(sys.modules, {"psycopg.types.json": fake_json_module}),
        ):
            store.heartbeat("external-job")

        heartbeat_call = cursor.execute.call_args_list[2]
        self.assertIn("pumbility.heartbeat_job", heartbeat_call.args[0])
        self.assertEqual(heartbeat_call.args[1][-1], JOB_LEASE_SECONDS)
        payload_update = cursor.execute.call_args_list[3]
        self.assertIn("updatedAtUtc", payload_update.args[0])


if __name__ == "__main__":
    unittest.main()
