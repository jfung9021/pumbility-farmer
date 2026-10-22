from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import MagicMock, Mock, patch

from analysis_runtime import MemoryBlobStore, MemoryJobStore
from api.recommendations import get_player_recommendation_refresh
from phoenix2_sync import isoformat_utc
from pumbility_contract import (
    recommendation_phoenix1_shard_path,
    recommendation_phoenix2_shard_path,
    recommendation_player_path,
    recommendation_player_state_path,
)
from pumbility_store import JobLeaseHeldError, JobLeaseLostError, PumbilityJobStore
from recommendation_refresh import refresh_player_recommendations
from worker.tasks import refresh_player_recommendations as player_task


NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
PLAYER_KEY = "a" * 20


def player_job(status="queued"):
    return {
        "id": "player-refresh-test",
        "kind": "player-recommendation-refresh",
        "playerKey": PLAYER_KEY,
        "status": status,
        "stage": "syncing" if status == "running" else "queued",
        "createdAtUtc": isoformat_utc(NOW),
        "updatedAtUtc": isoformat_utc(NOW),
    }


class PlayerLeaseStoreTests(unittest.TestCase):
    def setUp(self):
        self.jobs = PumbilityJobStore(database_url="postgresql://localhost/test")
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.rowcount = 1
        self.connect = patch("pumbility_store._connect", return_value=self.connection)
        self.schema = patch("pumbility_store._assert_schema")
        self.connect.start()
        self.schema.start()
        self.addCleanup(self.connect.stop)
        self.addCleanup(self.schema.stop)

    def queries(self):
        return [call.args[0] for call in self.cursor.execute.call_args_list]

    def test_expired_running_owner_is_reclaimed_by_atomic_claim_primitive(self):
        self.cursor.fetchone.side_effect = [
            ("job-uuid", "running", "dead-owner"),
            ({"claimed": True, "job": {"id": "job-uuid"}},),
        ]
        result = self.jobs.save(player_job("running"))
        self.assertEqual(result["status"], "running")
        self.assertTrue(any("pumbility.claim_job" in query for query in self.queries()))
        self.assertFalse(any("cancel_job" in query for query in self.queries()))

    def test_live_owner_duplicate_is_specific_no_write_error(self):
        self.cursor.fetchone.side_effect = [
            ("job-uuid", "running", "live-owner"),
            ({"claimed": False, "job": {"external_key": "player-refresh-test",
                "status": "running", "lease_expires_at": isoformat_utc(NOW + timedelta(seconds=800))}},),
            (NOW,),
        ]
        with self.assertRaises(JobLeaseHeldError):
            self.jobs.save(player_job("running"))
        self.assertFalse(any(query.lstrip().startswith("update") for query in self.queries()))

    def test_automatic_failure_cannot_cancel_another_worker(self):
        self.cursor.fetchone.return_value = ("job-uuid", "running", "replacement-owner")
        with self.assertRaises(JobLeaseLostError):
            self.jobs.save(player_job("failed"))
        self.assertFalse(any("cancel_job" in query for query in self.queries()))
        self.assertFalse(any(query.lstrip().startswith("update") for query in self.queries()))

    def test_explicit_operator_cancellation_is_preserved(self):
        self.cursor.fetchone.return_value = ("job-uuid", "running", "worker-owner")
        self.jobs.save({**player_job("failed"), "cancelRequested": True})
        self.assertTrue(any("cancel_job" in query for query in self.queries()))

    def test_stale_worker_cannot_overwrite_terminal_outcome(self):
        for status in ("completed", "failed", "cancelled"):
            with self.subTest(status=status):
                self.cursor.fetchone.return_value = ("job-uuid", status, None)
                with self.assertRaises(JobLeaseLostError):
                    self.jobs.save(player_job("failed"))
        self.assertFalse(any(query.lstrip().startswith("update") for query in self.queries()))

    def test_player_publication_checks_owner_and_commits_both_artifacts(self):
        self.cursor.fetchone.return_value = ("job-uuid",)
        payloads = {"state": {"scores": []}, "cache": {"player": {}}}
        with patch("pumbility_store.PumbilityArtifactStore._put_json_row") as put:
            self.jobs.publish_player_artifacts("player-refresh-test", payloads)
        self.assertEqual(put.call_count, 2)
        self.assertEqual(len(self.queries()), 2)
        self.assertTrue(all("lease_owner = %s" in query and "for update" in query for query in self.queries()))
        self.assertTrue(all("lease_expires_at >= clock_timestamp()" in query for query in self.queries()))
        self.connection.transaction.assert_called_once()

    def test_lost_lease_prevents_any_player_publication(self):
        self.cursor.fetchone.return_value = None
        with patch("pumbility_store.PumbilityArtifactStore._put_json_row") as put:
            with self.assertRaises(JobLeaseLostError):
                self.jobs.publish_player_artifacts("player-refresh-test", {"state": {}, "cache": {}})
        put.assert_not_called()

    def test_lease_expiring_during_publication_rolls_back_both_artifacts(self):
        self.cursor.fetchone.side_effect = [("job-uuid",), None]
        with patch("pumbility_store.PumbilityArtifactStore._put_json_row"):
            with self.assertRaises(JobLeaseLostError):
                self.jobs.publish_player_artifacts("player-refresh-test", {"state": {}, "cache": {}})
        self.assertIs(self.connection.transaction.return_value.__exit__.call_args.args[0], JobLeaseLostError)


class PlayerStatusAndWorkerTests(unittest.TestCase):
    def test_status_poll_after_thirty_seconds_has_no_writes(self):
        for status in ("queued", "running"):
            with self.subTest(status=status):
                jobs = MemoryJobStore()
                job = player_job(status)
                jobs.save(job)
                with (
                    patch("api.recommendations.RuntimeJobStore", return_value=jobs),
                    patch("api.recommendations.utc_now", return_value=NOW + timedelta(minutes=3)),
                    patch.object(jobs, "save", side_effect=AssertionError("GET must not write")),
                ):
                    response = get_player_recommendation_refresh(job["id"])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(json.loads(response.body), job)

    def worker_context(self, jobs, refresh):
        stack = ExitStack()
        stack.enter_context(patch("worker.tasks.RuntimeJobStore", return_value=jobs))
        stack.enter_context(patch("worker.tasks.PrivateBlobStore"))
        stack.enter_context(patch("worker.tasks.PiuScoresClient"))
        stack.enter_context(patch("worker.tasks.refresh_one_player", side_effect=refresh))
        stack.enter_context(patch("worker.tasks._begin_topology_worker_invocation", return_value=None))
        stack.enter_context(patch.dict("os.environ", {"PIU_SCORES_API_KEY": "test-only"}))
        return stack

    def test_healthy_slow_worker_heartbeats_publishes_and_completes_after_poll(self):
        jobs = MemoryJobStore()
        job = player_job()
        jobs.save(job)
        heartbeat = Mock()
        jobs.start_lease_heartbeat = Mock(return_value=heartbeat)
        jobs.publish_player_artifacts = Mock()

        def refresh(*_args, **kwargs):
            with (
                patch("api.recommendations.RuntimeJobStore", return_value=jobs),
                patch("api.recommendations.utc_now", return_value=NOW + timedelta(seconds=31)),
            ):
                polled = get_player_recommendation_refresh(job["id"])
            self.assertEqual(json.loads(polled.body)["status"], "running")
            kwargs["publish"]({"state": {}, "cache": {}})
            return {"generatedAtUtc": isoformat_utc(NOW)}

        with self.worker_context(jobs, refresh):
            result = player_task.run(job["id"])
        self.assertEqual(result["status"], "completed")
        jobs.start_lease_heartbeat.assert_called_once_with(job["id"])
        self.assertEqual(heartbeat.pulse.call_count, 3)
        heartbeat.stop.assert_called_once()
        jobs.publish_player_artifacts.assert_called_once_with(job["id"], {"state": {}, "cache": {}})

    def test_live_duplicate_returns_without_sync_or_terminal_write(self):
        jobs = MemoryJobStore()
        job = player_job("running")
        jobs.save(job)
        refresh = Mock()
        with self.worker_context(jobs, refresh), patch.object(jobs, "save", side_effect=JobLeaseHeldError("live owner")):
            result = player_task.run(job["id"])
        self.assertEqual(result, job)
        refresh.assert_not_called()

    def test_lost_publication_owner_does_not_fail_replacement(self):
        jobs = MemoryJobStore()
        job = player_job()
        jobs.save(job)
        jobs.start_lease_heartbeat = Mock(return_value=Mock())
        jobs.publish_player_artifacts = Mock(side_effect=JobLeaseLostError("replacement owner"))
        original_save = jobs.save

        def save(value):
            if value["status"] == "failed":
                raise JobLeaseLostError("replacement owner")
            return original_save(value)

        def refresh(*_args, **kwargs):
            kwargs["publish"]({"state": {}, "cache": {}})
            self.fail("publication must reject the old owner")

        with self.worker_context(jobs, refresh), patch.object(jobs, "save", side_effect=save):
            result = player_task.run(job["id"])
        self.assertEqual(result["status"], "running")
        jobs.start_lease_heartbeat.return_value.stop.assert_called_once()


class PlayerPublicationCallbackTests(unittest.TestCase):
    def test_rejected_publication_leaves_previous_state_and_cache_intact(self):
        store = MemoryBlobStore()
        metadata = {"playerKey": PLAYER_KEY, "internalPlayerId": "player", "inputShard": 0}
        store.put_json("index", {"generationKey": "generation", "players": [metadata]})
        store.put_json(recommendation_phoenix1_shard_path("generation", 0), {"players": []})
        store.put_json(recommendation_phoenix2_shard_path("generation", 0), {"players": [{"playerId": "player", "scores": []}]})
        state_path, cache_path = recommendation_player_state_path(PLAYER_KEY), recommendation_player_path(PLAYER_KEY)
        store.put_json(state_path, {"playerId": "player", "scores": [], "lastSyncedAtUtc": "previous"})
        store.put_json(cache_path, {"previous": True})
        client = Mock()
        client.fetch_page_collection.return_value = []
        before = {path: store.get_json(path) for path in (state_path, cache_path)}
        with (
            patch("recommendation_refresh._load_model", return_value=({}, Mock())),
            patch("recommendation_refresh._phoenix1_player_input", return_value={"scores": [], "clearedChartIds": []}),
            patch("recommendation_refresh._resolve_score_catalog", return_value={}),
            patch("recommendation_refresh.player_recommendation_response", return_value={}),
            patch("recommendation_refresh.compact_player_recommendation_cache", return_value={}),
        ):
            with self.assertRaises(JobLeaseLostError):
                refresh_player_recommendations(store, client, index_path="index", player_key=PLAYER_KEY,
                    publish=Mock(side_effect=JobLeaseLostError("replacement owner")))
        self.assertEqual({path: store.get_json(path) for path in (state_path, cache_path)}, before)


if __name__ == "__main__":
    unittest.main()
