from __future__ import annotations

import hashlib
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pumbility_store import (
    CanaryJobStore,
    CanaryJsonStore,
    CURRENT_SNAPSHOT_RE,
    EXPECTED_PUMBILITY_MIGRATION,
    JOB_LEASE_SECONDS,
    PumbilityArtifactStore,
    PumbilityJobStore,
    READ_CONNECT_TIMEOUT_SECONDS,
    READ_POOL_ACQUIRE_TIMEOUT_SECONDS,
    READ_POOL_ENABLED_ENV,
    READ_POOL_MAX_SIZE_ENV,
    READ_POOL_MAX_WAITING_ENV,
    ShadowJobStore,
    ShadowJsonStore,
    SupabasePrimaryJsonStore,
    _ContinuousLeaseHeartbeat,
    _canonical_json_bytes,
    _check_read_connection,
    _create_read_pool,
    _read_connect,
    _read_phase_started,
    _read_pool_configuration,
    _read_telemetry,
    _record_read_phase,
    configured_backend,
    configured_read_canaries,
    drain_blob_mirror_outbox,
    require_loopback_database_url,
    select_job_store,
    select_json_store,
    validate_rollout_configuration,
)


class BackendConfigurationTests(unittest.TestCase):
    def test_defaults_to_vercel(self) -> None:
        self.assertEqual(configured_backend({}), "vercel")

    def test_rejects_unknown_backend(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be one of"):
            configured_backend({"PUMBILITY_DATA_BACKEND": "other"})

    def test_read_canary_allowlist_rejects_unknown_domains(self) -> None:
        self.assertEqual(
            configured_read_canaries(
                {"PUMBILITY_SUPABASE_READ_CANARY": "analysis, tier-list"}
            ),
            frozenset({"analysis", "tier-list"}),
        )
        with self.assertRaisesRegex(RuntimeError, "unsupported domains"):
            configured_read_canaries(
                {"PUMBILITY_SUPABASE_READ_CANARY": "analysis,typo"}
            )

    def test_supabase_authority_requires_all_rollback_controls(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "requires enabled rollback controls"):
            validate_rollout_configuration({"PUMBILITY_DATA_BACKEND": "supabase"})
        validate_rollout_configuration(
            {
                "PUMBILITY_DATA_BACKEND": "supabase",
                "PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED": "true",
                "PUMBILITY_BLOB_MIRROR_ENABLED": "true",
                "PUMBILITY_BLOB_READ_FALLBACK_ENABLED": "true",
            }
        )

    def test_pre_cutover_modes_reject_cutover_only_blob_flags(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "valid only"):
            validate_rollout_configuration(
                {
                    "PUMBILITY_DATA_BACKEND": "shadow",
                    "PUMBILITY_BLOB_READ_FALLBACK_ENABLED": "true",
                }
            )

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


class ReadCanaryTests(unittest.TestCase):
    def test_json_rollout_telemetry_has_fixed_private_safe_phase_schema(self) -> None:
        private_path = "analysis/private/player-secret.json"
        private_value = {"playerId": "private-player", "value": 1}

        class InstrumentedCandidate:
            def get_json(self, _: str) -> dict[str, object]:
                for phase in ("connect", "schema", "fetch", "decode", "integrity"):
                    started = _read_phase_started()
                    _record_read_phase(phase, started)
                return dict(private_value)

        authoritative = Mock()
        authoritative.get_json.return_value = dict(private_value)
        store = CanaryJsonStore(
            authoritative, InstrumentedCandidate(), domain="analysis"
        )

        with self.assertLogs("pumbility.rollout", level="WARNING") as captured:
            self.assertEqual(store.get_json(private_path), private_value)

        event = captured.output[0]
        for field in (
            "authoritative_ms",
            "candidate_ms",
            "comparison_ms",
            "overall_ms",
            "authoritative_acquire_ms",
            "authoritative_connect_ms",
            "authoritative_health_ms",
            "authoritative_schema_ms",
            "authoritative_fetch_ms",
            "authoritative_decode_ms",
            "authoritative_integrity_ms",
            "candidate_acquire_ms",
            "candidate_connect_ms",
            "candidate_health_ms",
            "candidate_schema_ms",
            "candidate_fetch_ms",
            "candidate_decode_ms",
            "candidate_integrity_ms",
        ):
            self.assertRegex(event, rf"\b{field}=(?:none|\d+\.\d{{3}})\b")
        for field in (
            "candidate_connect_ms",
            "candidate_schema_ms",
            "candidate_fetch_ms",
            "candidate_decode_ms",
            "candidate_integrity_ms",
        ):
            self.assertNotIn(f"{field}=none", event)
        self.assertIn("authoritative_error=none", event)
        self.assertIn("candidate_error=none", event)
        self.assertNotIn(private_path, event)
        self.assertNotIn("private-player", event)

    def test_json_reads_authority_and_candidate_concurrently(self) -> None:
        authoritative = Mock()
        candidate = Mock()
        rendezvous = threading.Barrier(2)

        def read(value: dict[str, int]) -> dict[str, int]:
            rendezvous.wait(timeout=1)
            return value

        authoritative.get_json.side_effect = lambda _: read({"value": 1})
        candidate_value = {"value": 1}
        candidate.get_json.side_effect = lambda _: read(candidate_value)

        store = CanaryJsonStore(authoritative, candidate, domain="analysis")

        self.assertIs(store.get_json("private-key"), candidate_value)

    def test_json_candidate_is_served_only_after_exact_equality(self) -> None:
        authoritative = Mock()
        candidate = Mock()
        authoritative.get_json.return_value = {"value": 1}
        candidate_value = {"value": 1}
        candidate.get_json.return_value = candidate_value
        store = CanaryJsonStore(authoritative, candidate, domain="analysis")

        with self.assertLogs("pumbility.rollout", level="WARNING") as captured:
            self.assertIs(store.get_json("private-key"), candidate_value)
        self.assertIn("outcome=candidate-served", captured.output[0])
        self.assertNotIn("private-key", captured.output[0])

        candidate.get_json.return_value = {"value": 2}
        authoritative_value = {"value": 1}
        authoritative.get_json.return_value = authoritative_value
        self.assertIs(store.get_json("private-key"), authoritative_value)

    def test_json_candidate_failure_falls_back_without_changing_writes(self) -> None:
        authoritative = Mock()
        candidate = Mock()
        authoritative.get_json.return_value = {"source": "vercel"}
        private_error = (
            "private candidate unavailable at "
            "postgresql://runtime:secret@private-db.example/player-private"
        )
        candidate.get_json.side_effect = RuntimeError(private_error)
        store = CanaryJsonStore(authoritative, candidate, domain="tier-list")

        with self.assertLogs("pumbility.rollout", level="WARNING") as captured:
            self.assertEqual(store.get_json("private-key"), {"source": "vercel"})
        self.assertIn("candidate_error=RuntimeError:other:none", captured.output[0])
        self.assertNotIn(private_error, captured.output[0])
        self.assertNotIn("private-db.example", captured.output[0])
        store.put_json("private-key", {"value": 1})
        authoritative.put_json.assert_called_once_with("private-key", {"value": 1})
        candidate.put_json.assert_not_called()

    def test_comparison_failure_is_also_fail_open(self) -> None:
        authoritative = Mock()
        candidate = Mock()
        authoritative_value = {"value": object()}
        authoritative.get_json.return_value = authoritative_value
        candidate.get_json.return_value = {"value": object()}

        store = CanaryJsonStore(authoritative, candidate, domain="analysis")

        self.assertIs(store.get_json("private-key"), authoritative_value)

    def test_json_exact_evidence_mismatch_and_non_mapping_authority_fall_back(self) -> None:
        class ExactCandidate:
            def _get_json_with_evidence(self, _: str):
                from pumbility_store import _ExactJsonRead

                value = {"value": 2}
                return _ExactJsonRead(value, _canonical_json_bytes(value))

            def get_json(self, _: str) -> dict[str, int]:  # pragma: no cover
                raise AssertionError("The canary should use exact read evidence.")

        authoritative = Mock()
        store = CanaryJsonStore(authoritative, ExactCandidate(), domain="analysis")

        authoritative_value = {"value": 1}
        authoritative.get_json.return_value = authoritative_value
        self.assertIs(store.get_json("private-key"), authoritative_value)

        non_mapping_authority = b"not-json"
        authoritative.get_json.return_value = non_mapping_authority
        self.assertIs(store.get_json("private-key"), non_mapping_authority)

    def test_json_exact_evidence_preserves_numeric_normalization_semantics(self) -> None:
        from pumbility_store import _ExactJsonRead

        candidate_value = {"value": 100000000000000000000}

        class ExactCandidate:
            def _get_json_with_evidence(self, _: str):
                return _ExactJsonRead(
                    candidate_value, _canonical_json_bytes(candidate_value)
                )

            def get_json(self, _: str) -> dict[str, int]:  # pragma: no cover
                raise AssertionError("The canary should use exact read evidence.")

        authoritative = Mock()
        authority_value = {"value": 1e20}
        authoritative.get_json.return_value = authority_value

        result = CanaryJsonStore(
            authoritative, ExactCandidate(), domain="analysis"
        ).get_json("private-key")

        self.assertIs(result, authority_value)
        self.assertNotEqual(
            _canonical_json_bytes(authority_value),
            _canonical_json_bytes(candidate_value),
        )

    def test_bytes_reads_do_not_use_json_evidence_path(self) -> None:
        class BytesCandidate:
            def _get_json_with_evidence(self, _: str):  # pragma: no cover
                raise AssertionError("Binary reads must not use JSON evidence.")

            def get_bytes(self, _: str) -> bytes:
                return candidate_value

        authoritative = Mock()
        authoritative.get_bytes.return_value = b"binary-value"
        candidate_value = bytes(bytearray(b"binary-value"))
        store = CanaryJsonStore(authoritative, BytesCandidate(), domain="analysis")

        self.assertIs(store.get_bytes("private-key"), candidate_value)

    def test_job_candidate_is_served_only_after_exact_equality(self) -> None:
        authoritative = Mock()
        candidate = Mock()
        candidate_value = {"id": "private-id", "status": "completed"}
        authoritative.get.return_value = dict(candidate_value)
        candidate.get.return_value = candidate_value

        store = CanaryJobStore(authoritative, candidate, domain="job-status")

        self.assertIs(store.get("private-id"), candidate_value)

    def test_job_reads_authority_and_candidate_concurrently(self) -> None:
        authoritative = Mock()
        candidate = Mock()
        rendezvous = threading.Barrier(2)

        def read(value: dict[str, str]) -> dict[str, str]:
            rendezvous.wait(timeout=1)
            return value

        authoritative.get.side_effect = lambda _: read({"status": "completed"})
        candidate_value = {"status": "completed"}
        candidate.get.side_effect = lambda _: read(candidate_value)

        store = CanaryJobStore(authoritative, candidate, domain="job-status")

        self.assertIs(store.get("private-id"), candidate_value)


class SupabasePrimaryStoreTests(unittest.TestCase):
    def test_missing_or_failed_primary_read_uses_enabled_legacy_fallback(self) -> None:
        primary = Mock()
        legacy = Mock()
        legacy.get_json.return_value = {"source": "vercel"}
        store = SupabasePrimaryJsonStore(
            primary, legacy, mirror_enabled=False, fallback_enabled=True
        )

        primary.get_json.return_value = None
        self.assertEqual(store.get_json("private-key"), {"source": "vercel"})
        primary.get_json.side_effect = RuntimeError("primary unavailable")
        self.assertEqual(store.get_json("private-key"), {"source": "vercel"})

    def test_write_mirror_is_independently_gated_and_fail_open(self) -> None:
        primary = Mock()
        legacy = Mock()
        legacy.put_json.side_effect = RuntimeError("mirror unavailable")
        primary.retry_blob_mirror_event.side_effect = RuntimeError("retry unavailable")
        store = SupabasePrimaryJsonStore(
            primary, legacy, mirror_enabled=True, fallback_enabled=False
        )

        store.put_json("private-key", {"value": 1})

        primary.put_json.assert_called_once_with("private-key", {"value": 1})
        legacy.put_json.assert_called_once_with("private-key", {"value": 1})
        primary.enqueue_blob_mirror_event.assert_called_once_with(
            "put_json", "private-key", content_type=None
        )
        primary.retry_blob_mirror_event.assert_called_once()

    def test_outbox_replay_reads_payload_by_reference_and_completes(self) -> None:
        primary = Mock()
        legacy = Mock()
        primary.claim_blob_mirror_events.return_value = [
            (
                "event-1",
                {"operation": "put_json", "objectKeys": ["private-key"]},
            )
        ]
        primary.get_json.return_value = {"private": "payload"}

        self.assertEqual(drain_blob_mirror_outbox(primary, legacy), (1, 0))

        legacy.put_json.assert_called_once_with(
            "private-key", {"private": "payload"}
        )
        primary.complete_blob_mirror_event.assert_called_once_with("event-1")


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


class PumbilityReadPoolTests(unittest.TestCase):
    def test_pool_is_lazy_bounded_and_transaction_pooler_compatible(self) -> None:
        pool = Mock()
        with patch("psycopg_pool.ConnectionPool", return_value=pool) as constructor:
            self.assertIs(
                _create_read_pool(
                    "postgresql://localhost/local", max_size=2, max_waiting=2
                ),
                pool,
            )

        settings = constructor.call_args.kwargs
        self.assertEqual(settings["min_size"], 0)
        self.assertEqual(settings["max_size"], 2)
        self.assertEqual(settings["max_waiting"], 2)
        self.assertFalse(settings["open"])
        self.assertEqual(settings["timeout"], READ_POOL_ACQUIRE_TIMEOUT_SECONDS)
        self.assertEqual(
            settings["kwargs"]["connect_timeout"], READ_CONNECT_TIMEOUT_SECONDS
        )
        self.assertIsNone(settings["kwargs"]["prepare_threshold"])
        self.assertIs(settings["check"], _check_read_connection)
        pool.open.assert_called_once_with(wait=False)

    def test_pool_capacity_settings_are_bounded(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_read_pool_configuration(), (2, 2))
        with patch.dict(
            os.environ,
            {READ_POOL_MAX_SIZE_ENV: "3", READ_POOL_MAX_WAITING_ENV: "2"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, READ_POOL_MAX_SIZE_ENV):
                _read_pool_configuration()
        with patch.dict(
            os.environ,
            {READ_POOL_MAX_SIZE_ENV: "2", READ_POOL_MAX_WAITING_ENV: "0"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, READ_POOL_MAX_WAITING_ENV):
                _read_pool_configuration()

    def test_pool_acquisition_records_wait_and_one_time_physical_connect(self) -> None:
        connection = Mock()
        connection._pumbility_connect_ms = 12.5
        manager = Mock()
        manager.__enter__ = Mock(return_value=connection)
        manager.__exit__ = Mock(return_value=False)
        pool = Mock()
        pool.connection.return_value = manager
        phases: dict[str, float] = {}
        _read_telemetry.phases = phases
        try:
            with (
                patch.dict(os.environ, {READ_POOL_ENABLED_ENV: "true"}, clear=False),
                patch("pumbility_store._get_read_pool", return_value=pool),
            ):
                with _read_connect("postgresql://localhost/local") as acquired:
                    self.assertIs(acquired, connection)
        finally:
            del _read_telemetry.phases

        pool.connection.assert_called_once_with(
            timeout=READ_POOL_ACQUIRE_TIMEOUT_SECONDS
        )
        self.assertIn("acquire", phases)
        self.assertEqual(phases["connect"], 12.5)
        self.assertIsNone(connection._pumbility_connect_ms)
        manager.__exit__.assert_called_once_with(None, None, None)

    def test_pool_context_receives_errors_for_transaction_rollback(self) -> None:
        connection = Mock()
        connection._pumbility_connect_ms = None
        manager = Mock()
        manager.__enter__ = Mock(return_value=connection)
        manager.__exit__ = Mock(return_value=False)
        pool = Mock()
        pool.connection.return_value = manager

        with (
            patch.dict(os.environ, {READ_POOL_ENABLED_ENV: "true"}, clear=False),
            patch("pumbility_store._get_read_pool", return_value=pool),
            self.assertRaisesRegex(ValueError, "read failed"),
        ):
            with _read_connect("postgresql://localhost/local"):
                raise ValueError("read failed")

        exit_args = manager.__exit__.call_args.args
        self.assertIs(exit_args[0], ValueError)

    def test_failed_pool_acquisition_is_bounded_and_telemetried(self) -> None:
        manager = Mock()
        manager.__enter__ = Mock(side_effect=RuntimeError("pool unavailable"))
        manager.__exit__ = Mock(return_value=False)
        pool = Mock()
        pool.connection.return_value = manager
        phases: dict[str, float] = {}
        _read_telemetry.phases = phases
        try:
            with (
                patch.dict(os.environ, {READ_POOL_ENABLED_ENV: "true"}, clear=False),
                patch("pumbility_store._get_read_pool", return_value=pool),
                self.assertRaisesRegex(RuntimeError, "pool unavailable"),
            ):
                with _read_connect("postgresql://localhost/local"):
                    pass
        finally:
            del _read_telemetry.phases

        pool.connection.assert_called_once_with(
            timeout=READ_POOL_ACQUIRE_TIMEOUT_SECONDS
        )
        self.assertIn("acquire", phases)
        self.assertNotIn("connect", phases)

    def test_pool_acquisition_failure_preserves_canary_fallback(self) -> None:
        authoritative = Mock()
        authoritative.get_json.return_value = {"source": "vercel"}
        candidate = PumbilityArtifactStore(
            database_url="postgresql://localhost/local"
        )
        with (
            patch(
                "pumbility_store._read_connect",
                side_effect=RuntimeError("pool unavailable"),
            ),
            self.assertLogs("pumbility.rollout", level="WARNING") as captured,
        ):
            result = CanaryJsonStore(
                authoritative, candidate, domain="analysis"
            ).get_json("analysis/public.json")

        self.assertEqual(result, {"source": "vercel"})
        self.assertIn("outcome=candidate-error-fallback", captured.output[0])

    def test_stale_connection_is_checked_but_recent_connection_skips_probe(self) -> None:
        from psycopg_pool import ConnectionPool

        stale = SimpleNamespace(_pumbility_last_checked_at=0.0)
        recent = SimpleNamespace(_pumbility_last_checked_at=90.0)
        phases: dict[str, float] = {}
        _read_telemetry.phases = phases
        try:
            with (
                patch("pumbility_store.time.monotonic", return_value=100.0),
                patch.object(ConnectionPool, "check_connection") as check,
            ):
                _check_read_connection(stale)
                _check_read_connection(recent)
        finally:
            del _read_telemetry.phases

        check.assert_called_once_with(stale)
        self.assertEqual(stale._pumbility_last_checked_at, 100.0)
        self.assertIn("health", phases)


class PumbilityArtifactStoreTests(unittest.TestCase):
    @staticmethod
    def _database_mocks(*normalized_payloads: dict[str, object]):
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        cursor.fetchone.side_effect = [
            (EXPECTED_PUMBILITY_MIGRATION,),
            *((payload,) for payload in normalized_payloads),
        ]
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.cursor.return_value = cursor
        pipeline = Mock()
        pipeline.__enter__ = Mock(return_value=pipeline)
        pipeline.__exit__ = Mock(return_value=False)
        connection.pipeline.return_value = pipeline
        return connection, cursor

    @staticmethod
    def _read_connection(row: tuple[object, ...]):
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        cursor.fetchone.return_value = row
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.cursor.return_value = cursor
        pipeline = Mock()
        pipeline.__enter__ = Mock(return_value=pipeline)
        pipeline.__exit__ = Mock(return_value=False)
        connection.pipeline.return_value = pipeline
        return connection, cursor

    def test_canary_records_supabase_artifact_read_phases(self) -> None:
        payload = {"value": 1}
        body = _canonical_json_bytes(payload)
        connection, cursor = self._database_mocks()
        cursor.fetchone.side_effect = [
            (
                EXPECTED_PUMBILITY_MIGRATION,
                True,
                payload,
                hashlib.sha256(body).hexdigest(),
                len(body),
            ),
        ]
        fake_psycopg = SimpleNamespace(connect=Mock(return_value=connection))
        authoritative = Mock()
        authoritative.get_json.return_value = dict(payload)
        candidate = PumbilityArtifactStore(
            database_url="postgresql://localhost/local"
        )

        with (
            patch.dict(os.environ, {READ_POOL_ENABLED_ENV: "false"}, clear=False),
            patch.dict(sys.modules, {"psycopg": fake_psycopg}),
            self.assertLogs("pumbility.rollout", level="WARNING") as captured,
        ):
            result = CanaryJsonStore(
                authoritative, candidate, domain="analysis"
            ).get_json("analysis/public.json")

        self.assertEqual(result, payload)
        event = captured.output[0]
        for field in (
            "candidate_connect_ms",
            "candidate_schema_ms",
            "candidate_fetch_ms",
            "candidate_decode_ms",
            "candidate_integrity_ms",
        ):
            self.assertRegex(event, rf"\b{field}=\d+\.\d{{3}}\b")
        fake_psycopg.connect.assert_called_once_with(
            "postgresql://localhost/local",
            connect_timeout=3,
            prepare_threshold=None,
        )

    def test_hot_json_read_combines_schema_and_artifact_fetch(self) -> None:
        payload = {"value": 1}
        body = _canonical_json_bytes(payload)
        connection, cursor = self._read_connection(
            (
                EXPECTED_PUMBILITY_MIGRATION,
                True,
                payload,
                hashlib.sha256(body).hexdigest(),
                len(body),
            )
        )

        with patch("pumbility_store._read_connect", return_value=connection):
            result = PumbilityArtifactStore(
                database_url="postgresql://localhost/local"
            ).get_json("analysis/public.json")

        self.assertEqual(result, payload)
        self.assertEqual(cursor.execute.call_count, 2)
        self.assertIn("statement_timeout", cursor.execute.call_args_list[0].args[0])
        query = cursor.execute.call_args_list[1].args[0]
        self.assertIn("pumbility.schema_metadata", query)
        self.assertIn("pumbility.artifacts", query)

    def test_missing_json_still_fails_closed_on_missing_schema(self) -> None:
        missing_connection, missing_cursor = self._read_connection(
            (EXPECTED_PUMBILITY_MIGRATION, False, None, None, None)
        )
        invalid_connection, invalid_cursor = self._read_connection(
            (None, False, None, None, None)
        )
        store = PumbilityArtifactStore(database_url="postgresql://localhost/local")

        with patch("pumbility_store._read_connect", return_value=missing_connection):
            self.assertIsNone(store.get_json("analysis/missing.json"))
        self.assertEqual(missing_cursor.execute.call_count, 2)

        with (
            patch("pumbility_store._read_connect", return_value=invalid_connection),
            self.assertRaisesRegex(RuntimeError, "schema is not"),
        ):
            store.get_json("analysis/missing.json")
        self.assertEqual(invalid_cursor.execute.call_count, 2)

    def test_missing_binary_still_fails_closed_on_missing_schema(self) -> None:
        missing_connection, missing_cursor = self._read_connection(
            (EXPECTED_PUMBILITY_MIGRATION, False, None, None, None)
        )
        invalid_connection, invalid_cursor = self._read_connection(
            (None, False, None, None, None)
        )
        store = PumbilityArtifactStore(database_url="postgresql://localhost/local")

        with patch("pumbility_store._read_connect", return_value=missing_connection):
            self.assertIsNone(store.get_bytes("models/missing.npz"))
        self.assertEqual(missing_cursor.execute.call_count, 2)

        with (
            patch("pumbility_store._read_connect", return_value=invalid_connection),
            self.assertRaisesRegex(RuntimeError, "schema is not"),
        ):
            store.get_bytes("models/missing.npz")
        self.assertEqual(invalid_cursor.execute.call_count, 2)

    def test_canary_reuses_checksum_validated_candidate_bytes(self) -> None:
        authority = {"authority": True, "value": 1}
        candidate_payload = {"value": 1, "authority": True}
        body = _canonical_json_bytes(candidate_payload)
        connection, _ = self._read_connection(
            (
                EXPECTED_PUMBILITY_MIGRATION,
                True,
                candidate_payload,
                hashlib.sha256(body).hexdigest(),
                len(body),
            )
        )
        authoritative_store = Mock()
        authoritative_store.get_json.return_value = authority
        candidate_store = PumbilityArtifactStore(
            database_url="postgresql://localhost/local"
        )

        with (
            patch("pumbility_store._read_connect", return_value=connection),
            patch(
                "pumbility_store._canonical_json_bytes",
                wraps=_canonical_json_bytes,
            ) as canonicalize,
        ):
            result = CanaryJsonStore(
                authoritative_store, candidate_store, domain="analysis"
            ).get_json("analysis/public.json")

        self.assertEqual(result, candidate_payload)
        self.assertEqual(canonicalize.call_count, 2)

    def test_missing_exact_candidate_requires_no_json_serialization(self) -> None:
        connection, _ = self._read_connection(
            (EXPECTED_PUMBILITY_MIGRATION, False, None, None, None)
        )
        authoritative_store = Mock()
        authoritative_store.get_json.return_value = None
        candidate_store = PumbilityArtifactStore(
            database_url="postgresql://localhost/local"
        )

        with (
            patch("pumbility_store._read_connect", return_value=connection),
            patch(
                "pumbility_store._canonical_json_bytes",
                wraps=_canonical_json_bytes,
            ) as canonicalize,
            self.assertLogs("pumbility.rollout", level="WARNING") as captured,
        ):
            self.assertIsNone(
                CanaryJsonStore(
                    authoritative_store, candidate_store, domain="analysis"
                ).get_json("analysis/missing.json")
            )

        canonicalize.assert_not_called()
        self.assertIn("outcome=candidate-served", captured.output[0])

    def test_corrupt_exact_candidate_preserves_authority_fallback(self) -> None:
        candidate_payload = {"value": 1}
        body = _canonical_json_bytes(candidate_payload)
        connection, _ = self._read_connection(
            (
                EXPECTED_PUMBILITY_MIGRATION,
                True,
                candidate_payload,
                "0" * 64,
                len(body),
            )
        )
        authority_value = {"source": "vercel"}
        authoritative_store = Mock()
        authoritative_store.get_json.return_value = authority_value
        candidate_store = PumbilityArtifactStore(
            database_url="postgresql://localhost/local"
        )

        with (
            patch("pumbility_store._read_connect", return_value=connection),
            self.assertLogs("pumbility.rollout", level="WARNING") as captured,
        ):
            result = CanaryJsonStore(
                authoritative_store, candidate_store, domain="analysis"
            ).get_json("analysis/public.json")

        self.assertIs(result, authority_value)
        self.assertIn("outcome=candidate-error-fallback", captured.output[0])
        self.assertIn(
            "candidate_error=PumbilityArtifactIntegrityError:other:none",
            captured.output[0],
        )

    def test_json_digest_uses_database_normalized_numeric_value(self) -> None:
        incoming = {"value": 1e20}
        normalized = {"value": 100000000000000000000}
        self.assertNotEqual(_canonical_json_bytes(incoming), _canonical_json_bytes(normalized))
        connection, cursor = self._database_mocks(normalized)
        fake_json_module = SimpleNamespace(Jsonb=lambda payload: payload)

        with (
            patch("pumbility_store._connect", return_value=connection),
            patch.dict(sys.modules, {"psycopg.types.json": fake_json_module}),
        ):
            PumbilityArtifactStore(database_url="postgresql://localhost/local").put_json(
                "analysis/model.json", incoming
            )

        self.assertIn("returning payload_json", cursor.execute.call_args_list[1].args[0])
        metadata_call = cursor.execute.call_args_list[2]
        normalized_body = _canonical_json_bytes(normalized)
        self.assertEqual(
            metadata_call.args[1],
            (hashlib.sha256(normalized_body).hexdigest(), len(normalized_body), "analysis/model.json"),
        )

    def test_json_bundle_normalizes_each_entry_in_one_connection(self) -> None:
        normalized = ({"value": 100000000000000000000}, {"value": 2})
        connection, cursor = self._database_mocks(*normalized)
        fake_json_module = SimpleNamespace(Jsonb=lambda payload: payload)

        with (
            patch("pumbility_store._connect", return_value=connection),
            patch.dict(sys.modules, {"psycopg.types.json": fake_json_module}),
        ):
            PumbilityArtifactStore(database_url="postgresql://localhost/local").put_json_bundle(
                {"analysis/one.json": {"value": 1e20}, "analysis/two.json": {"value": 2}}
            )

        self.assertEqual(connection.__enter__.call_count, 1)
        metadata_calls = [
            call for call in cursor.execute.call_args_list if "update pumbility.artifacts" in call.args[0]
        ]
        self.assertEqual(len(metadata_calls), 2)
        for call, pathname, payload in zip(
            metadata_calls, ("analysis/one.json", "analysis/two.json"), normalized
        ):
            body = _canonical_json_bytes(payload)
            self.assertEqual(
                call.args[1], (hashlib.sha256(body).hexdigest(), len(body), pathname)
            )

    def test_supabase_staging_root_omits_the_whole_snapshot(self) -> None:
        normalized = {
            "schemaVersion": 1,
            "jobId": "job",
            "mix": "Phoenix2",
            "completedPlayerIds": ["private-player"],
            "storageSchemaVersion": 2,
            "checkpointKind": "player-delta",
            "snapshotSchemaVersion": 2,
        }
        connection, cursor = self._database_mocks(normalized)
        fake_json_module = SimpleNamespace(Jsonb=lambda payload: payload)
        payload = {
            "schemaVersion": 1,
            "jobId": "job",
            "mix": "Phoenix2",
            "completedPlayerIds": ["private-player"],
            "snapshot": {"schemaVersion": 2, "players": [], "charts": [], "scores": []},
        }

        with (
            patch("pumbility_store._connect", return_value=connection),
            patch.dict(sys.modules, {"psycopg.types.json": fake_json_module}),
        ):
            PumbilityArtifactStore(database_url="postgresql://localhost/local").put_json(
                "analysis/phoenix2/staging/job.json", payload
            )

        inserted = cursor.execute.call_args_list[1].args[1][1]
        self.assertNotIn("snapshot", inserted)
        self.assertEqual(inserted["storageSchemaVersion"], 2)

    def test_supabase_staging_read_reassembles_checksum_gated_player_deltas(self) -> None:
        root = {
            "schemaVersion": 1,
            "jobId": "job",
            "mix": "Phoenix2",
            "completedPlayerIds": ["private-player"],
            "storageSchemaVersion": 2,
            "checkpointKind": "player-delta",
        }
        child = {
            "schemaVersion": 1,
            "player": {"playerId": "private-player"},
            "scores": [],
        }

        def connection_with(*, fetched: object = None, rows: list[tuple] | None = None):
            cursor = Mock()
            cursor.__enter__ = Mock(return_value=cursor)
            cursor.__exit__ = Mock(return_value=False)
            cursor.fetchone.return_value = (
                (EXPECTED_PUMBILITY_MIGRATION, True, *fetched)
                if fetched is not None
                else (EXPECTED_PUMBILITY_MIGRATION,)
            )
            cursor.fetchall.return_value = rows or []
            connection = Mock()
            connection.__enter__ = Mock(return_value=connection)
            connection.__exit__ = Mock(return_value=False)
            connection.cursor.return_value = cursor
            pipeline = Mock()
            pipeline.__enter__ = Mock(return_value=pipeline)
            pipeline.__exit__ = Mock(return_value=False)
            connection.pipeline.return_value = pipeline
            return connection

        root_body = _canonical_json_bytes(root)
        child_body = _canonical_json_bytes(child)
        root_connection = connection_with(
            fetched=(root, hashlib.sha256(root_body).hexdigest(), len(root_body))
        )
        child_connection = connection_with(
            rows=[(child, hashlib.sha256(child_body).hexdigest(), len(child_body))]
        )
        with (
            patch("pumbility_store._read_connect", return_value=root_connection),
            patch("pumbility_store._connect", return_value=child_connection),
        ):
            result = PumbilityArtifactStore(
                database_url="postgresql://localhost/local"
            ).get_json("analysis/phoenix2/staging/job.json")

        self.assertEqual(result["playerCheckpoints"], [child])
        self.assertNotIn("snapshot", result)

        with (
            patch(
                "pumbility_store._read_connect",
                return_value=connection_with(
                    fetched=(
                        root,
                        hashlib.sha256(root_body).hexdigest(),
                        len(root_body),
                    )
                ),
            ),
            patch("pumbility_store._connect", return_value=connection_with(rows=[])),
            self.assertRaisesRegex(RuntimeError, "checkpoint set is incomplete"),
        ):
            PumbilityArtifactStore(
                database_url="postgresql://localhost/local"
            ).get_json("analysis/phoenix2/staging/job.json")

    def test_runtime_typed_persistence_uses_the_same_canonical_snapshot(self) -> None:
        snapshot = {
            "schemaVersion": 2,
            "mix": "Phoenix2",
            "generatedAtUtc": "2026-08-13T00:00:00Z",
            "players": [],
            "charts": [],
            "scores": [],
        }
        database_input = SimpleNamespace(
            snapshot={**snapshot, "generatedAtUtc": ""}
        )
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        cursor.fetchone.side_effect = [
            (EXPECTED_PUMBILITY_MIGRATION,),
            ("job-uuid",),
        ]
        connection.cursor.return_value = cursor
        config = SimpleNamespace()
        payload = {"generatedAtUtc": "2026-08-13T00:00:00Z"}

        with (
            patch("pumbility_store._connect", return_value=connection),
            patch(
                "scripts.analyze_pumbility_supabase._read_database_input",
                return_value=database_input,
            ),
            patch(
                "scripts.analyze_pumbility_supabase._persist_analysis",
                return_value="analysis-run",
            ) as persist_analysis,
        ):
            result = PumbilityArtifactStore(
                database_url="postgresql://localhost/local"
            ).persist_typed_generation(
                job_external_key="external-job",
                mix_key="phoenix2",
                snapshot=snapshot,
                config=config,
                payload=payload,
                baselines=[],
                contributions=[],
                chart_results=[],
            )

        self.assertEqual(result, ("analysis-run", None))
        persisted_output = persist_analysis.call_args.args[1]
        self.assertEqual(persisted_output.database_input, database_input)
        self.assertEqual(persisted_output.payload, payload)
        self.assertEqual(persist_analysis.call_args.kwargs["job_id"], "job-uuid")

    def test_runtime_typed_model_phase_resumes_from_analysis_generation(self) -> None:
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        artifacts = (
            {"generationKey": "generation"},
            {"generationKey": "generation"},
            b"model",
            [],
            [],
        )
        database_input = SimpleNamespace(snapshot={})

        with (
            patch("pumbility_store._connect", return_value=connection),
            patch(
                "scripts.analyze_pumbility_supabase._read_database_input",
                return_value=database_input,
            ) as read_input,
            patch(
                "scripts.analyze_pumbility_supabase._persist_analysis"
            ) as persist_analysis,
            patch(
                "scripts.populate_pumbility_production._persist_model_generation",
                return_value="model-generation",
            ) as persist_model,
        ):
            result = PumbilityArtifactStore(
                database_url="postgresql://localhost/local"
            ).persist_typed_generation(
                job_external_key="external-job",
                mix_key="phoenix2",
                snapshot={"generatedAtUtc": "2026-08-13T00:00:00Z"},
                config=SimpleNamespace(),
                payload={"generatedAtUtc": "2026-08-13T00:00:00Z"},
                baselines=[],
                contributions=[],
                chart_results=[],
                model_artifacts=artifacts,
                phase="model",
                analysis_run_id="analysis-run",
            )

        self.assertEqual(result, ("analysis-run", "model-generation"))
        self.assertEqual(read_input.call_count, 2)
        persist_analysis.assert_not_called()
        self.assertEqual(
            persist_model.call_args.kwargs["analysis_run_id"], "analysis-run"
        )
        self.assertEqual(persist_model.call_args.kwargs["artifacts"][3:], (0, 0))


class PumbilityJobReadTests(unittest.TestCase):
    @staticmethod
    def _connection(row: tuple[object, ...]):
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        cursor.fetchone.return_value = row
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.cursor.return_value = cursor
        pipeline = Mock()
        pipeline.__enter__ = Mock(return_value=pipeline)
        pipeline.__exit__ = Mock(return_value=False)
        connection.pipeline.return_value = pipeline
        return connection, cursor

    def test_hot_job_read_combines_schema_and_payload_fetch(self) -> None:
        payload = {"id": "job-id", "status": "completed"}
        connection, cursor = self._connection(
            (EXPECTED_PUMBILITY_MIGRATION, True, payload)
        )

        with patch("pumbility_store._read_connect", return_value=connection):
            result = PumbilityJobStore(
                database_url="postgresql://localhost/local"
            ).get("job-id")

        self.assertEqual(result, payload)
        self.assertEqual(cursor.execute.call_count, 2)
        self.assertIn("statement_timeout", cursor.execute.call_args_list[0].args[0])
        query = cursor.execute.call_args_list[1].args[0]
        self.assertIn("pumbility.schema_metadata", query)
        self.assertIn("pumbility.jobs", query)

    def test_missing_job_still_fails_closed_on_missing_schema(self) -> None:
        missing_connection, missing_cursor = self._connection(
            (EXPECTED_PUMBILITY_MIGRATION, False, None)
        )
        invalid_connection, invalid_cursor = self._connection((None, False, None))
        store = PumbilityJobStore(database_url="postgresql://localhost/local")

        with patch("pumbility_store._read_connect", return_value=missing_connection):
            self.assertIsNone(store.get("missing-job"))
        self.assertEqual(missing_cursor.execute.call_count, 2)

        with (
            patch("pumbility_store._read_connect", return_value=invalid_connection),
            self.assertRaisesRegex(RuntimeError, "schema is not"),
        ):
            store.get("missing-job")
        self.assertEqual(invalid_cursor.execute.call_count, 2)


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
