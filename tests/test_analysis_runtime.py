import hashlib
import hmac
import json
import time
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from analysis_runtime import (
    ANALYSIS_CONTINUATION_FIELD,
    ANALYSIS_CONTINUATION_SEQUENCE_FIELD,
    CURRENT_SNAPSHOT_PATH,
    FAILED_RETRY_DELAY,
    LATEST_BLOB_PATH,
    PrivateBlobStore,
    RUNS_PREFIX,
    STAGING_PREFIX,
    TYPED_CHECKPOINT_ANALYSIS_PHASE,
    TYPED_CHECKPOINT_DATABASE_ANALYSIS_PHASE,
    TYPED_CHECKPOINT_DATABASE_SHARDS_PHASE,
    TYPED_CHECKPOINT_DATABASE_MODEL_PHASE,
    TYPED_CHECKPOINT_MODEL_PHASE,
    TYPED_CHECKPOINT_SCHEMA_VERSION,
    TYPED_CHECKPOINT_SNAPSHOT_PHASE,
    MemoryBlobStore,
    MemoryJobStore,
    _canonical_json_sha256,
    _checkpoint_continuation,
    _load_typed_checkpoint_shard,
    _load_checkpoint_model_artifacts,
    _write_typed_frame_shards,
    cleanup_abandoned_staging,
    cleanup_abandoned_typed_checkpoints,
    current_snapshot_path,
    deterministic_deployment_job_id,
    execute_analysis_job,
    isoformat_utc,
    new_job,
    latest_blob_path,
    publish_success,
    read_latest_payload,
    request_refresh,
    runs_prefix,
    typed_checkpoint_path,
    typed_checkpoint_shard_path,
    update_job,
)
from api.cron import cron_authorized
from api_service import app as api_app
from mix_registry import resolve_mix
from phoenix2_sync import analyzer_input, synchronize_phoenix2_snapshot
from piu_misgrade_analyzer import (
    AnalysisConfig,
    SCRIPT_VERSION,
    analyze_snapshot,
    build_web_payload,
    make_synthetic_snapshot,
)
from piu_recommendations import (
    RECOMMENDATION_SCHEMA_VERSION,
    combined_tier_blob_path,
    recommendation_blob_path,
    recommendation_shard_path,
)
from recommendation_refresh import (
    player_refresh_job_id,
    recommendation_index_path,
    recommendation_player_path,
    recommendation_player_state_path,
)
from worker.tasks import (
    refresh_player_recommendations as refresh_player_recommendations_task,
)
NOW = datetime(2026, 8, 7, 6, 30, tzinfo=timezone.utc)
API_CLIENT = TestClient(api_app)


class RecordingBlobClient:
    delete_calls: list[list[str]] = []

    def __init__(self, *, token: str) -> None:
        self.token = token

    def __enter__(self) -> "RecordingBlobClient":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def delete(self, pathnames: list[str]) -> None:
        self.delete_calls.append(pathnames)


def latest_payload(generated: datetime, mix: str = "phoenix2") -> dict:
    mix_value = "Phoenix" if mix == "phoenix1" else "Phoenix2"
    mix_label = "Phoenix 1" if mix == "phoenix1" else "Phoenix 2"
    return {
        "generatedAtUtc": isoformat_utc(generated),
        "mix": {"key": mix, "apiValue": mix_value, "label": mix_label},
        "summary": {"scriptVersion": SCRIPT_VERSION, "modes": {}, "mix": {
            "key": mix, "apiValue": mix_value, "label": mix_label,
        }},
        "singles": [],
        "doubles": [],
        "relativeGroups": [],
        "effectBands": [],
    }


class PrivateBlobStoreTests(unittest.TestCase):
    def test_delete_batches_large_retention_sets(self) -> None:
        paths = [f"analysis/stale/{index:04d}.json" for index in range(205)]
        RecordingBlobClient.delete_calls = []

        with patch("vercel.blob.BlobClient", RecordingBlobClient):
            PrivateBlobStore(token="test-token").delete(paths)

        self.assertEqual(
            [len(batch) for batch in RecordingBlobClient.delete_calls],
            [100, 100, 5],
        )
        self.assertEqual(
            [path for batch in RecordingBlobClient.delete_calls for path in batch],
            paths,
        )


class CoordinatorTests(unittest.TestCase):
    def test_legacy_phoenix2_payload_is_normalized_without_rewriting_it(self) -> None:
        blobs = MemoryBlobStore()
        legacy = latest_payload(NOW)
        legacy.pop("mix")
        legacy["summary"].pop("mix")
        blobs.put_json("analysis/latest.json", legacy)

        normalized = read_latest_payload(blobs, "phoenix2")

        self.assertEqual(normalized["mix"]["key"], "phoenix2")
        self.assertEqual(normalized["summary"]["mix"]["apiValue"], "Phoenix2")
        self.assertNotIn("mix", blobs.get_json("analysis/latest.json"))

    def test_archived_phoenix1_refresh_is_rejected_before_job_coordination(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        active = new_job("analysis-20260807T06", NOW, mix="phoenix2")
        jobs.save(active)
        jobs.set_active_job_id(active["id"])
        enqueued: list[str] = []

        status, body = request_refresh(
            blobs,
            jobs,
            enqueued.append,
            now=NOW,
            mix="phoenix1",
        )

        self.assertEqual((status, body["outcome"]), (409, "archived"))
        self.assertEqual(body["archiveUrl"], "/data/phoenix1.json")
        self.assertEqual(enqueued, [])

    def test_successful_result_has_no_manual_refresh_cooldown(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        blobs.put_json(LATEST_BLOB_PATH, latest_payload(NOW - timedelta(seconds=1)))
        enqueued: list[str] = []
        status, body = request_refresh(blobs, jobs, enqueued.append, now=NOW)
        self.assertEqual((status, body["outcome"]), (202, "started"))
        self.assertEqual(enqueued, ["analysis-20260807T06"])

    def test_deployment_reanalysis_is_deduplicated_by_deployment(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        job_id = deterministic_deployment_job_id("dpl_example")
        enqueued: list[str] = []
        status, body = request_refresh(
            blobs,
            jobs,
            enqueued.append,
            now=NOW,
            force_refresh=True,
            deterministic_job_id=job_id,
            reanalyze_only=True,
            trigger="deployment",
        )
        self.assertEqual((status, body["outcome"]), (202, "started"))
        self.assertEqual(enqueued, [job_id])
        self.assertFalse(body["job"]["fullSync"])
        self.assertTrue(body["job"]["reanalyzeOnly"])
        self.assertEqual(body["job"]["trigger"], "deployment")

        update_job(jobs, job_id, now=NOW, status="completed")
        jobs.set_active_job_id(None)
        status, duplicate = request_refresh(
            blobs,
            jobs,
            enqueued.append,
            now=NOW + timedelta(minutes=1),
            force_refresh=True,
            deterministic_job_id=job_id,
            reanalyze_only=True,
            trigger="deployment",
        )
        self.assertEqual((status, duplicate["outcome"]), (202, "existing"))
        self.assertEqual(enqueued, [job_id])

    def test_failed_deployment_refresh_reuses_id_after_retry_delay(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        job_id = deterministic_deployment_job_id("dpl_retry")
        failed = new_job(job_id, NOW, reanalyze_only=True, trigger="deployment")
        jobs.save(failed)
        update_job(
            jobs,
            job_id,
            now=NOW,
            status="failed",
            retryAllowedAtUtc=isoformat_utc(NOW + FAILED_RETRY_DELAY),
        )
        enqueued: list[str] = []
        _, waiting = request_refresh(
            blobs,
            jobs,
            enqueued.append,
            now=NOW + timedelta(minutes=1),
            force_refresh=True,
            deterministic_job_id=job_id,
            reanalyze_only=True,
            trigger="deployment",
        )
        self.assertEqual(waiting["job"]["status"], "failed")
        self.assertEqual(enqueued, [])

        _, retry = request_refresh(
            blobs,
            jobs,
            enqueued.append,
            now=NOW + timedelta(minutes=6),
            force_refresh=True,
            deterministic_job_id=job_id,
            reanalyze_only=True,
            trigger="deployment",
        )
        self.assertEqual(retry["outcome"], "started")
        self.assertEqual(retry["job"]["id"], job_id)
        self.assertEqual(retry["job"]["attempt"], 1)
        self.assertEqual(enqueued, [job_id])

    def test_active_job_is_deduplicated(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        active = new_job("analysis-20260807T06", NOW)
        jobs.save(active)
        jobs.set_active_job_id(active["id"])
        enqueued: list[str] = []
        status, body = request_refresh(blobs, jobs, enqueued.append, now=NOW)
        self.assertEqual(status, 202)
        self.assertEqual(body["outcome"], "existing")
        self.assertEqual(body["job"]["id"], active["id"])
        self.assertEqual(enqueued, [])

    def test_stale_active_job_is_failed_and_released_for_retry(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        stale = new_job("analysis-stuck", NOW)
        jobs.save(stale)
        jobs.set_active_job_id(stale["id"])
        enqueued: list[str] = []

        status, body = request_refresh(
            blobs,
            jobs,
            enqueued.append,
            now=NOW + timedelta(minutes=6),
        )

        self.assertEqual((status, body["outcome"]), (202, "started"))
        self.assertEqual(jobs.get(stale["id"])["status"], "failed")
        self.assertEqual(
            jobs.get(stale["id"])["error"],
            "The analysis worker stopped reporting progress.",
        )
        self.assertEqual(enqueued, [body["job"]["id"]])
        self.assertEqual(jobs.active_job_id(), body["job"]["id"])

    def test_manual_request_coordination_returns_within_two_seconds(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        started = time.perf_counter()
        status, body = request_refresh(blobs, jobs, lambda _: None, now=NOW)
        elapsed = time.perf_counter() - started
        self.assertEqual((status, body["outcome"]), (202, "started"))
        self.assertLess(elapsed, 2)

    def test_failed_job_observes_retry_delay_then_enqueues_retry(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        failed = new_job("analysis-20260807T06", NOW - timedelta(minutes=2))
        jobs.save(failed)
        jobs.set_latest_job_id(failed["id"])
        update_job(
            jobs,
            failed["id"],
            now=NOW - timedelta(minutes=2),
            status="failed",
            error="safe failure",
            retryAllowedAtUtc=isoformat_utc(NOW + timedelta(minutes=3)),
        )
        enqueued: list[str] = []
        _, body = request_refresh(blobs, jobs, enqueued.append, now=NOW)
        self.assertEqual(body["outcome"], "existing")
        self.assertEqual(enqueued, [])
        status, body = request_refresh(
            blobs, jobs, enqueued.append, now=NOW + timedelta(minutes=4)
        )
        self.assertEqual(status, 202)
        self.assertEqual(body["outcome"], "started")
        self.assertEqual(body["job"]["attempt"], 1)
        self.assertEqual(enqueued, ["analysis-20260807T06-r1"])

    def test_enqueue_failure_is_json_safe_job_failure(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()

        def fail(_: str) -> None:
            raise TimeoutError("platform text should not escape")

        with self.assertRaisesRegex(RuntimeError, "could not be queued"):
            request_refresh(blobs, jobs, fail, now=NOW)
        job = jobs.get(jobs.latest_job_id() or "")
        self.assertEqual(job["status"], "failed")
        self.assertNotIn("platform text", job["error"])

    def test_cron_authorization_requires_exact_bearer_secret(self) -> None:
        self.assertTrue(cron_authorized("Bearer secret-value", "secret-value"))
        self.assertFalse(cron_authorized("secret-value", "secret-value"))
        self.assertFalse(cron_authorized("Bearer secret-value", ""))


class ApiRouteTests(unittest.TestCase):
    def test_jonathan_refresh_requires_a_configured_matching_password(self) -> None:
        with patch.dict("os.environ", {"JONATHAN_PASSWORD": ""}):
            unconfigured = API_CLIENT.post("/api/jonathan/refresh")
        with patch.dict("os.environ", {"JONATHAN_PASSWORD": "operator-secret"}):
            missing = API_CLIENT.post("/api/jonathan/refresh")
            wrong = API_CLIENT.post(
                "/api/jonathan/refresh",
                headers={"X-Jonathan-Password": "wrong-secret"},
            )

        self.assertEqual(unconfigured.status_code, 503)
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(missing.json(), {"error": "Unauthorized refresh request."})
        self.assertEqual(missing.headers["cache-control"], "no-store")

    def test_jonathan_incremental_refresh_is_always_forced(self) -> None:
        job = new_job("analysis-incremental", NOW)
        with (
            patch.dict("os.environ", {"JONATHAN_PASSWORD": "operator-secret"}),
            patch(
                "api.jonathan.start_or_reuse_analysis",
                return_value=(202, {"outcome": "started", "job": job}),
            ) as start,
        ):
            response = API_CLIENT.post(
                "/api/jonathan/refresh?mode=incremental",
                headers={"X-Jonathan-Password": "operator-secret"},
            )

        self.assertEqual(response.status_code, 202)
        start.assert_called_once_with(
            mix=resolve_mix("phoenix2"),
            force_refresh=True,
            full_sync=False,
            trigger="jonathan",
        )

    def test_jonathan_full_refresh_requests_a_complete_resync(self) -> None:
        job = new_job("analysis-full", NOW, full_sync=True)
        with (
            patch.dict("os.environ", {"JONATHAN_PASSWORD": "operator-secret"}),
            patch(
                "api.jonathan.start_or_reuse_analysis",
                return_value=(202, {"outcome": "started", "job": job}),
            ) as start,
        ):
            response = API_CLIENT.post(
                "/api/jonathan/refresh?mode=full",
                headers={"X-Jonathan-Password": "operator-secret"},
            )

        self.assertEqual(response.status_code, 202)
        start.assert_called_once_with(
            mix=resolve_mix("phoenix2"),
            force_refresh=True,
            full_sync=True,
            trigger="jonathan",
        )

    def test_manual_analysis_refresh_requires_matching_admin_secret(self) -> None:
        with patch.dict("os.environ", {"CRON_SECRET": "admin-secret"}):
            missing = API_CLIENT.post("/api/analyze?mix=phoenix2")
            wrong = API_CLIENT.post(
                "/api/analyze?mix=phoenix2",
                headers={"X-Analysis-Run-Secret": "wrong-secret"},
            )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(
            missing.json()["error"], "Unauthorized analysis refresh request."
        )

    def test_operator_can_cancel_a_poisoned_job_and_release_its_lock(self) -> None:
        jobs = MemoryJobStore()
        job = new_job("poisoned-job", NOW)
        jobs.save(job)
        jobs.set_active_job_id(job["id"])

        with (
            patch.dict("os.environ", {"CRON_SECRET": "cancel-secret"}),
            patch("api.analyze.RuntimeJobStore", return_value=jobs),
        ):
            unauthorized = API_CLIENT.post(
                "/api/analyze/cancel?jobId=poisoned-job"
            )
            cancelled = API_CLIENT.post(
                "/api/analyze/cancel?jobId=poisoned-job",
                headers={"Authorization": "Bearer cancel-secret"},
            )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["outcome"], "cancelled")
        self.assertTrue(cancelled.json()["job"]["cancelRequested"])
        self.assertEqual(jobs.get(job["id"])["status"], "failed")
        self.assertIsNone(jobs.active_job_id())

    def test_combined_tier_route_returns_only_the_public_aggregate(self) -> None:
        blobs = MemoryBlobStore()
        blobs.put_json(
            combined_tier_blob_path(),
            {
                "generatedAtUtc": isoformat_utc(NOW),
                "mix": {
                    "key": "combined",
                    "apiValue": "Phoenix+Phoenix2",
                    "label": "Phoenix 1 + 2",
                },
                "summary": {"modes": {}},
                "singles": [],
                "doubles": [],
                "relativeGroups": [],
                "effectBands": [],
            },
        )
        with patch("api.tier_list.PrivateBlobStore", return_value=blobs):
            response = API_CLIENT.get("/api/tier-list")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mix"]["key"], "combined")
        self.assertNotIn("players", response.json())

    def test_recommendation_routes_return_safe_player_slices(self) -> None:
        blobs = MemoryBlobStore()
        blobs.put_json(
            recommendation_blob_path(),
            {
                "schemaVersion": 1,
                "storageSchemaVersion": 2,
                "generationKey": "generation",
                "generatedAtUtc": isoformat_utc(NOW),
                "method": {"baselineRanks": [11, 30]},
                "players": [
                    {
                        "playerKey": "public-key",
                        "username": "PLAYER",
                        "displayName": "PLAYER",
                        "eligibility": {"singles": True, "doubles": False},
                        "shard": 0,
                    }
                ],
            },
        )
        blobs.put_json(
            recommendation_shard_path("generation", 0),
            {
                "storageSchemaVersion": 2,
                "generationKey": "generation",
                "players": [
                    {
                        "playerKey": "public-key",
                        "username": "PLAYER",
                        "displayName": "PLAYER",
                        "modes": {
                            "singles": {
                                "eligible": True,
                                "validScoreCount": 30,
                                "candidates": [],
                                "topRecommendations": [],
                            },
                            "doubles": {
                                "eligible": False,
                                "validScoreCount": 2,
                                "candidates": [],
                                "topRecommendations": [],
                            },
                        },
                    }
                ],
            },
        )
        with patch("api.recommendations.PrivateBlobStore", return_value=blobs):
            players = API_CLIENT.get("/api/recommendations/players")
            selected = API_CLIENT.get("/api/recommendations?playerKey=public-key")
            missing = API_CLIENT.get("/api/recommendations?playerKey=missing")
        self.assertEqual(players.status_code, 200)
        self.assertEqual(players.json()["players"][0]["username"], "PLAYER")
        self.assertEqual(
            players.json()["players"][0]["eligibility"],
            {"singles": True, "doubles": False},
        )
        self.assertNotIn("modes", players.json()["players"][0])
        self.assertEqual(selected.json()["player"]["playerKey"], "public-key")
        self.assertTrue(selected.json()["legacySnapshot"])
        self.assertEqual(selected.json()["modelGeneratedAtUtc"], isoformat_utc(NOW))
        self.assertEqual(selected.json()["playerSyncedAtUtc"], isoformat_utc(NOW))
        self.assertEqual(selected.headers["cache-control"], "no-store")
        self.assertEqual(missing.status_code, 404)

    def test_player_refresh_is_disabled_by_default(self) -> None:
        blobs = MemoryBlobStore()
        blobs.put_json(
            recommendation_blob_path(),
            {
                "storageSchemaVersion": 3,
                "refreshSupported": True,
                "generationKey": "generation",
                "generatedAtUtc": isoformat_utc(NOW),
                "players": [{"playerKey": "public-key", "eligibility": {}}],
            },
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("api.recommendations.PrivateBlobStore", return_value=blobs),
        ):
            players = API_CLIENT.get("/api/recommendations/players")
            refresh = API_CLIENT.post(
                "/api/recommendations/refresh?playerKey=public-key"
            )
        self.assertFalse(players.json()["refreshSupported"])
        self.assertEqual(refresh.status_code, 503)

    def test_player_refresh_dedupes_across_a_minute_boundary(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        effective_now = NOW.replace(second=5)
        player_key = "public-key"
        index = {
            "schemaVersion": RECOMMENDATION_SCHEMA_VERSION,
            "storageSchemaVersion": 3,
            "refreshSupported": True,
            "generationKey": "generation",
            "generatedAtUtc": isoformat_utc(NOW),
            "players": [{"playerKey": player_key, "eligibility": {}}],
        }
        blobs.put_json(recommendation_blob_path(), index)
        prior_created = effective_now - timedelta(seconds=15)
        prior = {
            "id": player_refresh_job_id(player_key, effective_now - timedelta(minutes=1)),
            "kind": "player-recommendation-refresh",
            "playerKey": player_key,
            "status": "running",
            "createdAtUtc": isoformat_utc(prior_created),
            "updatedAtUtc": isoformat_utc(prior_created),
        }
        jobs.save(prior)
        with (
            patch.dict("os.environ", {"PLAYER_RECOMMENDATION_REFRESH_ENABLED": "true"}),
            patch("api.recommendations.PrivateBlobStore", return_value=blobs),
            patch("api.recommendations.RuntimeJobStore", return_value=jobs),
            patch("api.recommendations.utc_now", return_value=effective_now),
            patch("api.recommendations._enqueue_player_refresh") as enqueue,
        ):
            response = API_CLIENT.post(
                f"/api/recommendations/refresh?playerKey={player_key}"
            )
        self.assertEqual((response.status_code, response.json()["outcome"]), (202, "existing"))
        enqueue.assert_not_called()

    def test_stuck_player_refresh_becomes_retryable(self) -> None:
        jobs = MemoryJobStore()
        job = {
            "id": "stuck-player-job",
            "kind": "player-recommendation-refresh",
            "playerKey": "public-key",
            "status": "running",
            "stage": "syncing",
            "createdAtUtc": isoformat_utc(NOW - timedelta(minutes=1)),
            "updatedAtUtc": isoformat_utc(NOW - timedelta(seconds=31)),
        }
        jobs.save(job)
        with (
            patch("api.recommendations.RuntimeJobStore", return_value=jobs),
            patch("api.recommendations.utc_now", return_value=NOW),
        ):
            response = API_CLIENT.get(
                "/api/recommendations/refresh?jobId=stuck-player-job"
            )
        self.assertEqual(response.json()["status"], "failed")
        self.assertEqual(response.json()["retryAllowedAtUtc"], isoformat_utc(NOW))

    def test_protected_recommendation_rollback_repoints_the_stable_index(self) -> None:
        blobs = MemoryBlobStore()
        target = {
            "storageSchemaVersion": 2,
            "generationKey": "legacy-target",
            "generatedAtUtc": isoformat_utc(NOW - timedelta(days=1)),
            "players": [{"playerKey": "public-key", "shard": 0}],
        }
        current = {
            "storageSchemaVersion": 3,
            "generationKey": "current-generation",
            "generatedAtUtc": isoformat_utc(NOW),
            "players": [],
        }
        blobs.put_json(recommendation_index_path("legacy-target"), target)
        blobs.put_json(recommendation_shard_path("legacy-target", 0), {"players": []})
        blobs.put_json(recommendation_blob_path(), current)
        with (
            patch.dict("os.environ", {"CRON_SECRET": "admin-secret"}),
            patch("api.recommendations.PrivateBlobStore", return_value=blobs),
        ):
            unauthorized = API_CLIENT.post(
                "/api/recommendations/rollback?generationKey=legacy-target"
            )
            response = API_CLIENT.post(
                "/api/recommendations/rollback?generationKey=legacy-target",
                headers={"X-Analysis-Run-Secret": "admin-secret"},
            )
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(response.json()["outcome"], "rolled-back")
        self.assertEqual(
            blobs.get_json(recommendation_blob_path())["generationKey"],
            "legacy-target",
        )
        self.assertIsNotNone(
            blobs.get_json(recommendation_index_path("current-generation"))
        )

    def test_player_refresh_routes_cache_dedupe_and_hide_internal_ids(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        index = {
            "schemaVersion": RECOMMENDATION_SCHEMA_VERSION,
            "storageSchemaVersion": 3,
            "refreshSupported": True,
            "generationKey": "daily-generation",
            "generatedAtUtc": isoformat_utc(NOW),
            "modelGeneratedAtUtc": isoformat_utc(NOW),
            "method": {"baselineRanks": [11, 30]},
            "players": [
                {
                    "playerKey": "public-key",
                    "internalPlayerId": "private-id",
                    "username": "PLAYER",
                    "displayName": "PLAYER",
                    "eligibility": {"singles": True, "doubles": False},
                    "inputShard": 0,
                }
            ],
        }
        cached = {
            "schemaVersion": RECOMMENDATION_SCHEMA_VERSION,
            "generatedAtUtc": isoformat_utc(NOW),
            "modelGeneratedAtUtc": isoformat_utc(NOW - timedelta(hours=1)),
            "playerSyncedAtUtc": isoformat_utc(NOW),
            "modelGeneration": "daily-generation",
            "stale": False,
            "method": index["method"],
            "player": {
                "playerKey": "public-key",
                "username": "PLAYER",
                "displayName": "PLAYER",
                "modes": {},
            },
        }
        blobs.put_json(recommendation_blob_path(), index)
        blobs.put_json(recommendation_player_path("public-key"), cached)

        with (
            patch.dict("os.environ", {"PLAYER_RECOMMENDATION_REFRESH_ENABLED": "true"}),
            patch("api.recommendations.PrivateBlobStore", return_value=blobs),
            patch("api.recommendations.RuntimeJobStore", return_value=jobs),
            patch("api.recommendations.utc_now", return_value=NOW),
            patch("api.recommendations._enqueue_player_refresh") as enqueue,
        ):
            players = API_CLIENT.get("/api/recommendations/players")
            selected = API_CLIENT.get(
                "/api/recommendations?playerKey=public-key"
            )
            fresh = API_CLIENT.post(
                "/api/recommendations/refresh?playerKey=public-key"
            )
            stale = {
                **cached,
                "playerSyncedAtUtc": isoformat_utc(NOW - timedelta(minutes=2)),
            }
            blobs.put_json(recommendation_player_path("public-key"), stale)
            started = API_CLIENT.post(
                "/api/recommendations/refresh?playerKey=public-key"
            )
            duplicate = API_CLIENT.post(
                "/api/recommendations/refresh?playerKey=public-key"
            )

        self.assertTrue(players.json()["refreshSupported"])
        self.assertNotIn("internalPlayerId", players.json()["players"][0])
        self.assertEqual(selected.json()["modelGeneration"], "daily-generation")
        self.assertEqual(
            selected.json()["modelGeneratedAtUtc"],
            isoformat_utc(NOW - timedelta(hours=1)),
        )
        self.assertEqual(selected.json()["currentModelGeneratedAtUtc"], isoformat_utc(NOW))
        self.assertEqual((fresh.status_code, fresh.json()["outcome"]), (200, "fresh"))
        self.assertEqual((started.status_code, started.json()["outcome"]), (202, "started"))
        self.assertEqual((duplicate.status_code, duplicate.json()["outcome"]), (202, "existing"))
        enqueue.assert_called_once()

    def test_recommendations_require_a_player_key_and_generated_index(self) -> None:
        blobs = MemoryBlobStore()
        with patch("api.recommendations.PrivateBlobStore", return_value=blobs):
            no_key = API_CLIENT.get("/api/recommendations")
            removed_rating = API_CLIENT.get("/api/recommendations?rating=22.5")
            no_index = API_CLIENT.get("/api/recommendations/players")
        self.assertEqual(no_key.status_code, 400)
        self.assertEqual(removed_rating.status_code, 400)
        self.assertEqual(no_index.status_code, 404)

    def test_cross_schema_player_cache_handles_forward_and_rollback(self) -> None:
        blobs = MemoryBlobStore()
        index = {
            "schemaVersion": RECOMMENDATION_SCHEMA_VERSION,
            "storageSchemaVersion": 3,
            "generationKey": "current-generation",
            "players": [{"playerKey": "public-key"}],
        }
        cached = {
            "schemaVersion": RECOMMENDATION_SCHEMA_VERSION - 1,
            "modelGeneration": "current-generation",
            "player": {"playerKey": "public-key", "modes": {}},
        }
        blobs.put_json(recommendation_blob_path(), index)
        blobs.put_json(recommendation_player_path("public-key"), cached)

        with patch("api.recommendations.PrivateBlobStore", return_value=blobs):
            response = API_CLIENT.get("/api/recommendations?playerKey=public-key")

        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.json()["refreshRequired"])

        newer = {
            **cached,
            "schemaVersion": RECOMMENDATION_SCHEMA_VERSION + 1,
            "modelGeneration": "newer-generation",
        }
        blobs.put_json(recommendation_player_path("public-key"), newer)
        with patch("api.recommendations.PrivateBlobStore", return_value=blobs):
            rollback_response = API_CLIENT.get(
                "/api/recommendations?playerKey=public-key"
            )

        self.assertEqual(rollback_response.status_code, 200)
        self.assertTrue(rollback_response.json()["stale"])

    def test_mix_specific_cron_route_rejects_archived_phoenix1(self) -> None:
        with patch.dict("os.environ", {"CRON_SECRET": "cron-secret-value"}):
            response = API_CLIENT.get(
                "/api/cron/phoenix1",
                headers={"Authorization": "Bearer cron-secret-value"},
            )
        self.assertEqual((response.status_code, response.json()["outcome"]), (409, "archived"))

    def test_analysis_route_reads_only_the_requested_mix(self) -> None:
        blobs = MemoryBlobStore()
        phoenix2 = latest_payload(NOW, "phoenix2")
        blobs.put_json(latest_blob_path("phoenix2"), phoenix2)
        with patch("api.analyze.PrivateBlobStore", return_value=blobs):
            first = API_CLIENT.get("/api/analyze?mix=phoenix1", follow_redirects=False)
            second = API_CLIENT.get("/api/analyze?mix=phoenix2")
            invalid = API_CLIENT.get("/api/analyze?mix=Fiesta")
        self.assertEqual(first.status_code, 307)
        self.assertEqual(first.headers["location"], "/data/phoenix1.json")
        self.assertEqual(second.json()["mix"]["key"], "phoenix2")
        self.assertEqual(invalid.status_code, 400)

    def test_archived_phoenix1_manual_refresh_and_job_lookup_are_rejected(self) -> None:
        with patch.dict("os.environ", {"CRON_SECRET": "admin-secret"}):
            refresh = API_CLIENT.post(
                "/api/analyze?mix=phoenix1",
                headers={"X-Analysis-Run-Secret": "admin-secret"},
            )
        job = API_CLIENT.get("/api/analyze?mix=phoenix1&jobId=stale")
        self.assertEqual((refresh.status_code, refresh.json()["outcome"]), (409, "archived"))
        self.assertEqual((job.status_code, job.json()["outcome"]), (410, "archived"))

    def test_missing_latest_and_unauthorized_cron_are_json(self) -> None:
        blobs = MemoryBlobStore()
        with patch("api.analyze.PrivateBlobStore", return_value=blobs):
            latest = API_CLIENT.get("/api/analyze")
        cron = API_CLIENT.get("/api/cron")
        self.assertEqual((latest.status_code, latest.json()["error"]), (
            404, "No completed analysis is stored yet."
        ))
        self.assertEqual((cron.status_code, cron.json()["error"]), (
            401, "Unauthorized cron request."
        ))
        deploy = API_CLIENT.post("/api/deploy", content=b"{}")
        self.assertEqual((deploy.status_code, deploy.json()["error"]), (
            401, "Unauthorized webhook."
        ))

    def test_promoted_deployment_webhook_does_not_start_reanalysis(self) -> None:
        secret = "deployment-secret"
        event = {
            "id": "evt_1",
            "type": "deployment.promoted",
            "payload": {
                "deployment": {"id": "dpl_example"},
                "project": {"id": "prj_example"},
            },
        }
        raw = json.dumps(event, separators=(",", ":")).encode()
        signature = hmac.new(secret.encode(), raw, hashlib.sha1).hexdigest()
        with patch.dict(
            "os.environ",
            {
                "VERCEL_DEPLOY_WEBHOOK_SECRET": secret,
                "VERCEL_PROJECT_ID": "prj_example",
            },
        ):
            response = API_CLIENT.post(
                "/api/deploy",
                content=raw,
                headers={"x-vercel-signature": signature},
            )
        self.assertEqual((response.status_code, response.json()["outcome"]), (202, "ignored"))
        self.assertEqual(response.json()["deploymentId"], "dpl_example")

    def test_deployment_webhook_is_independent_of_active_analysis_jobs(self) -> None:
        secret = "deployment-secret"
        raw = json.dumps({
            "type": "deployment.promoted",
            "payload": {
                "deployment": {"id": "dpl_example"},
                "project": {"id": "prj_example"},
            },
        }, separators=(",", ":")).encode()
        signature = hmac.new(secret.encode(), raw, hashlib.sha1).hexdigest()
        with patch.dict(
            "os.environ",
            {
                "VERCEL_DEPLOY_WEBHOOK_SECRET": secret,
                "VERCEL_PROJECT_ID": "prj_example",
            },
        ):
            response = API_CLIENT.post(
                "/api/deploy",
                content=raw,
                headers={"x-vercel-signature": signature},
            )
        self.assertEqual((response.status_code, response.json()["outcome"]), (202, "ignored"))

    def test_deployment_webhook_rejects_archived_phoenix1(self) -> None:
        secret = "deployment-secret"
        raw = json.dumps({
            "type": "deployment.promoted",
            "payload": {
                "deployment": {"id": "dpl_phoenix1"},
                "project": {"id": "prj_example"},
            },
        }, separators=(",", ":")).encode()
        signature = hmac.new(secret.encode(), raw, hashlib.sha1).hexdigest()
        with patch.dict(
            "os.environ",
            {
                "VERCEL_DEPLOY_WEBHOOK_SECRET": secret,
                "VERCEL_PROJECT_ID": "prj_example",
            },
        ):
            response = API_CLIENT.post(
                "/api/deploy?mix=phoenix1",
                content=raw,
                headers={"x-vercel-signature": signature},
            )
        self.assertEqual((response.status_code, response.json()["outcome"]), (409, "archived"))

    def test_post_returns_async_refresh_contract(self) -> None:
        job = new_job("analysis-20260807T06", NOW)
        with (
            patch.dict("os.environ", {"CRON_SECRET": "admin-secret"}),
            patch("api.analyze.start_or_reuse_analysis", return_value=(
                202, {"outcome": "started", "job": job}
            )),
        ):
            response = API_CLIENT.post(
                "/api/analyze",
                headers={"X-Analysis-Run-Secret": "admin-secret"},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["outcome"], "started")

    def test_admin_can_request_a_full_score_resynchronization(self) -> None:
        job = new_job("analysis-full-sync", NOW, full_sync=True)
        with (
            patch.dict("os.environ", {"CRON_SECRET": "admin-secret"}),
            patch(
                "api.analyze.start_or_reuse_analysis",
                return_value=(202, {"outcome": "started", "job": job}),
            ) as start,
        ):
            response = API_CLIENT.post(
                "/api/analyze?mix=phoenix2&fullSync=true",
                headers={"X-Analysis-Run-Secret": "admin-secret"},
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["job"]["fullSync"])
        start.assert_called_once_with(
            mix=resolve_mix("phoenix2"),
            force_refresh=True,
            full_sync=True,
        )

    def test_backend_exception_is_a_safe_json_error(self) -> None:
        with patch("api.analyze.PrivateBlobStore", side_effect=RuntimeError("private detail")):
            response = API_CLIENT.get("/api/analyze")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"error": "The latest analysis service is temporarily unavailable."},
        )


def chart(index: int) -> dict:
    return {
        "id": f"chart-{index:02d}",
        "songName": f"Chart {index}",
        "type": "Single",
        "level": 20 + index % 3,
        "difficulty": f"S{20 + index % 3}",
        "imageUrl": None,
        "noteCount": 1000,
        "stepArtist": "Test",
    }


def score(index: int) -> dict:
    return {
        "chartId": f"chart-{index:02d}",
        "pumbility": 200 + 7.3 * (20 + index % 3) - index * 0.1,
        "score": 990999 - index,
        "recordedAt": "2026-08-07T06:00:00Z",
        "isBroken": False,
        "gameTag": "must-not-be-cached",
    }


class WorkerClient:
    def fetch_page_collection(self, path: str, params=None):
        if path == "api/v2/players":
            return [{"userId": "player", "username": "private"}]
        if path == "api/v2/charts":
            return [chart(index) for index in range(30)]
        if path == "api/v2/songs":
            return [
                {"name": f"Chart {index}", "bpm": {"min": 120, "max": 180}}
                for index in range(30)
            ]
        if path == "api/v2/players/player/scores":
            return [score(index) for index in range(30)]
        raise AssertionError(path)


class WorkerTests(unittest.TestCase):
    def test_queue_visibility_covers_the_full_worker_duration(self) -> None:
        from worker.celery import app

        options = app.conf.broker_transport_options
        self.assertEqual(options["visibility_timeout_seconds"], 800)
        self.assertEqual(options["visibility_refresh_interval_seconds"], 240)
        self.assertNotIn("lease_duration", options)

    def test_player_queue_has_a_conservative_four_worker_cap(self) -> None:
        with (Path(__file__).resolve().parents[1] / "pyproject.toml").open("rb") as source:
            project = tomllib.load(source)
        subscribers = project["tool"]["vercel"]["subscribers"]
        player = next(
            row for row in subscribers if row["topics"] == ["player-recommendations"]
        )
        self.assertEqual(player["max_concurrency"], 4)

    def test_analysis_queue_has_a_conservative_four_worker_cap(self) -> None:
        with (Path(__file__).resolve().parents[1] / "pyproject.toml").open("rb") as source:
            project = tomllib.load(source)
        subscribers = project["tool"]["vercel"]["subscribers"]
        analysis = next(row for row in subscribers if row["topics"] == ["analysis"])
        self.assertEqual(analysis["max_concurrency"], 4)

    def test_player_refresh_task_uses_the_dedicated_lightweight_path(self) -> None:
        jobs = MemoryJobStore()
        job = {
            "id": "recommendation-public-key-20260807T0630",
            "kind": "player-recommendation-refresh",
            "playerKey": "public-key",
            "status": "queued",
            "stage": "queued",
            "createdAtUtc": isoformat_utc(NOW),
            "updatedAtUtc": isoformat_utc(NOW),
        }
        jobs.save(job)
        response = {
            "generatedAtUtc": isoformat_utc(NOW),
            "modelGeneratedAtUtc": isoformat_utc(NOW - timedelta(hours=1)),
            "playerSyncedAtUtc": isoformat_utc(NOW),
        }

        with (
            patch.dict("os.environ", {"PIU_SCORES_API_KEY": "test-key"}),
            patch("worker.tasks.RuntimeJobStore", return_value=jobs),
            patch("worker.tasks.PrivateBlobStore") as blobs,
            patch("worker.tasks.PiuScoresClient") as client,
            patch("worker.tasks.refresh_one_player", return_value=response) as refresh,
        ):
            result = refresh_player_recommendations_task.run(job["id"])

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["progress"]["percent"], 100)
        self.assertIn("durationMs", result)
        refresh.assert_called_once_with(
            blobs.return_value,
            client.return_value,
            index_path=recommendation_blob_path(),
            player_key="public-key",
            timings={},
        )

    def test_cancelled_queue_redelivery_is_acknowledged_without_work(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        cancelled = {
            **new_job("cancelled-job", NOW),
            "status": "failed",
            "cancelRequested": True,
        }
        jobs.save(cancelled)

        result = execute_analysis_job(
            cancelled["id"],
            blobs=blobs,
            jobs=jobs,
            client=object(),
            now=lambda: NOW,
        )

        self.assertTrue(result["cancelRequested"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(blobs.values, {})

    def test_archived_phoenix1_cannot_be_published(self) -> None:
        blobs = MemoryBlobStore()
        snapshot1 = {"mix": "Phoenix", "players": [], "charts": [], "scores": []}
        with self.assertRaisesRegex(ValueError, "archived"):
            publish_success(
                blobs,
                job_id="p1",
                snapshot=snapshot1,
                payload=latest_payload(NOW, "phoenix1"),
                mix="phoenix1",
            )
        self.assertEqual(blobs.values, {})

    def test_phoenix2_publish_paths_remain_available(self) -> None:
        blobs = MemoryBlobStore()
        snapshot2 = {"mix": "Phoenix2", "players": [], "charts": [], "scores": []}
        publish_success(
            blobs,
            job_id="p2",
            snapshot=snapshot2,
            payload=latest_payload(NOW, "phoenix2"),
            recommendations={"generatedAtUtc": isoformat_utc(NOW), "players": []},
            combined_tier={"generatedAtUtc": isoformat_utc(NOW), "singles": []},
            mix="phoenix2",
        )
        self.assertEqual(read_latest_payload(blobs, "phoenix2")["mix"]["key"], "phoenix2")
        self.assertEqual(blobs.get_json(current_snapshot_path("phoenix2"))["mix"], "Phoenix2")
        self.assertEqual(len(blobs.list(runs_prefix("phoenix2"))), 1)
        self.assertEqual(blobs.get_json(recommendation_blob_path())["players"], [])
        self.assertEqual(blobs.get_json(combined_tier_blob_path())["singles"], [])

    def test_new_model_publish_keeps_legacy_shards_and_removes_unretained_v3(self) -> None:
        blobs = MemoryBlobStore()
        blobs.put_json("analysis/recommendations/models/old.json", {"old": True})
        blobs.put_json(
            "analysis/private/recommendation-inputs/old/phoenix1/0000.json",
            {"old": True},
        )
        blobs.put_json(recommendation_shard_path("legacy", 0), {"old": True})
        blobs.put_json(
            "analysis/recommendations/models/current.json", {"current": True}
        )
        blobs.put_json(
            "analysis/private/recommendation-inputs/current/phoenix1/0000.json",
            {"current": True},
        )

        with patch.dict(
            "os.environ", {"PLAYER_RECOMMENDATION_REFRESH_ENABLED": "true"}
        ):
            publish_success(
                blobs,
                job_id="current-model",
                snapshot={"mix": "Phoenix2", "players": [], "charts": [], "scores": []},
                payload=latest_payload(NOW, "phoenix2"),
                recommendations={
                    "schemaVersion": RECOMMENDATION_SCHEMA_VERSION,
                    "storageSchemaVersion": 3,
                    "refreshSupported": True,
                    "generationKey": "current",
                    "generatedAtUtc": isoformat_utc(NOW),
                    "players": [],
                },
                mix="phoenix2",
            )

        self.assertIsNone(blobs.get_json("analysis/recommendations/models/old.json"))
        self.assertIsNone(
            blobs.get_json(
                "analysis/private/recommendation-inputs/old/phoenix1/0000.json"
            )
        )
        self.assertIsNotNone(blobs.get_json(recommendation_shard_path("legacy", 0)))
        self.assertIsNotNone(
            blobs.get_json("analysis/recommendations/models/current.json")
        )
        self.assertIsNotNone(
            blobs.get_json(
                "analysis/private/recommendation-inputs/current/phoenix1/0000.json"
            )
        )
        self.assertEqual(
            blobs.get_json(recommendation_blob_path())["generationKey"], "current"
        )

    def test_disabled_v3_rollout_builds_shadow_generation_without_repointing(self) -> None:
        blobs = MemoryBlobStore()
        legacy = {
            "storageSchemaVersion": 2,
            "generationKey": "legacy",
            "generatedAtUtc": isoformat_utc(NOW - timedelta(days=1)),
            "players": [],
        }
        blobs.put_json(recommendation_blob_path(), legacy)
        candidate = {
            "storageSchemaVersion": 3,
            "refreshSupported": True,
            "generationKey": "candidate",
            "generatedAtUtc": isoformat_utc(NOW),
            "players": [],
        }

        with patch.dict("os.environ", {}, clear=True):
            publish_success(
                blobs,
                job_id="shadow",
                snapshot={"mix": "Phoenix2", "players": [], "charts": [], "scores": []},
                payload=latest_payload(NOW),
                recommendations=candidate,
            )

        self.assertEqual(
            blobs.get_json(recommendation_blob_path())["generationKey"], "legacy"
        )

    def test_v3_publish_removes_revoked_player_state_and_result_only(self) -> None:
        blobs = MemoryBlobStore()
        for player_key in ("allowed", "revoked"):
            blobs.put_json(recommendation_player_state_path(player_key), {"scores": []})
            blobs.put_json(recommendation_player_path(player_key), {"player": {}})
        recommendation = {
            "storageSchemaVersion": 3,
            "refreshSupported": True,
            "generationKey": "current",
            "generatedAtUtc": isoformat_utc(NOW),
            "players": [{"playerKey": "allowed"}],
        }
        with patch.dict(
            "os.environ", {"PLAYER_RECOMMENDATION_REFRESH_ENABLED": "true"}
        ):
            publish_success(
                blobs,
                job_id="privacy-cleanup",
                snapshot={"mix": "Phoenix2", "players": [], "charts": [], "scores": []},
                payload=latest_payload(NOW),
                recommendations=recommendation,
            )

        self.assertIsNotNone(blobs.get_json(recommendation_player_state_path("allowed")))
        self.assertIsNotNone(blobs.get_json(recommendation_player_path("allowed")))
        self.assertIsNone(blobs.get_json(recommendation_player_state_path("revoked")))
        self.assertIsNone(blobs.get_json(recommendation_player_path("revoked")))

    def test_v3_retention_keeps_at_least_two_generations(self) -> None:
        blobs = MemoryBlobStore()
        for number, generation in enumerate(("generation-one", "generation-two"), 1):
            index_path = recommendation_index_path(generation)
            model_path = f"analysis/recommendations/models/{generation}.json"
            input_path = (
                f"analysis/private/recommendation-inputs/{generation}/phoenix1/0000.json"
            )
            blobs.put_json(index_path, {
                "storageSchemaVersion": 3,
                "generationKey": generation,
            })
            blobs.put_json(model_path, {"generationKey": generation})
            blobs.put_json(input_path, {"generationKey": generation})
            old = NOW - timedelta(hours=50 - number)
            for path in (index_path, model_path, input_path):
                blobs.uploaded[path] = old
        current = {
            "storageSchemaVersion": 3,
            "refreshSupported": True,
            "generationKey": "generation-three",
            "generatedAtUtc": isoformat_utc(NOW),
            "players": [],
        }
        blobs.put_json(recommendation_index_path("generation-three"), current)
        blobs.put_json(
            "analysis/recommendations/models/generation-three.json", current
        )
        with patch.dict(
            "os.environ", {"PLAYER_RECOMMENDATION_REFRESH_ENABLED": "true"}
        ):
            publish_success(
                blobs,
                job_id="retention",
                snapshot={"mix": "Phoenix2", "players": [], "charts": [], "scores": []},
                payload=latest_payload(NOW),
                recommendations=current,
            )

        self.assertIsNone(
            blobs.get_json("analysis/recommendations/models/generation-one.json")
        )
        self.assertIsNotNone(
            blobs.get_json("analysis/recommendations/models/generation-two.json")
        )
        self.assertIsNotNone(
            blobs.get_json("analysis/recommendations/models/generation-three.json")
        )

    def test_stale_archived_job_fails_without_syncing_or_publishing(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        job = new_job("stale-phoenix1", NOW, mix="phoenix1")
        jobs.save(job)
        jobs.set_active_job_id(job["id"])
        result = execute_analysis_job(
            job["id"], blobs=blobs, jobs=jobs, client=WorkerClient(), now=lambda: NOW
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("archived", result["error"])
        self.assertEqual(blobs.values, {})
        self.assertIsNone(jobs.active_job_id())

    def test_job_transitions_publish_private_snapshot_and_latest(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        job = new_job("analysis-20260807T06", NOW)
        jobs.save(job)
        jobs.set_latest_job_id(job["id"])
        jobs.set_active_job_id(job["id"])
        result = execute_analysis_job(
            job["id"],
            blobs=blobs,
            jobs=jobs,
            client=WorkerClient(),
            now=lambda: NOW,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stage"], "publishing")
        self.assertIsNotNone(blobs.get_json(LATEST_BLOB_PATH))
        snapshot = blobs.get_json(CURRENT_SNAPSHOT_PATH)
        self.assertEqual(len(snapshot["players"]), 1)
        serialized = json.dumps(snapshot)
        self.assertEqual(snapshot["players"][0]["username"], "private")
        self.assertEqual((snapshot["charts"][0]["bpmMin"], snapshot["charts"][0]["bpmMax"]), (120.0, 180.0))
        self.assertNotIn("gameTag", serialized)
        self.assertIsNone(blobs.get_json(f"{STAGING_PREFIX}{job['id']}.json"))
        self.assertIsNone(jobs.active_job_id())

    def test_typed_persistence_completes_before_public_pointer_promotion(self) -> None:
        class TypedMemoryStore(MemoryBlobStore):
            typed_persistence_enabled = True

            def persist_typed_generation(self, **kwargs):
                self.typed_calls = getattr(self, "typed_calls", []) + [kwargs]
                self.latest_before_typed = self.get_json(LATEST_BLOB_PATH)
                self.snapshot_before_typed = self.get_json(CURRENT_SNAPSHOT_PATH)
                return (
                    "analysis-run",
                    "model-generation" if kwargs["phase"] == "model" else None,
                )

        blobs = TypedMemoryStore()
        jobs = MemoryJobStore()
        job = new_job("typed-analysis", NOW)
        jobs.save(job)
        jobs.set_latest_job_id(job["id"])
        jobs.set_active_job_id(job["id"])

        result = execute_analysis_job(
            job["id"],
            blobs=blobs,
            jobs=jobs,
            client=WorkerClient(),
            now=lambda: NOW,
        )

        self.assertEqual(result["status"], "completed")
        self.assertIsNone(blobs.latest_before_typed)
        self.assertIsNotNone(blobs.snapshot_before_typed)
        self.assertEqual(blobs.typed_calls[0]["mix_key"], "phoenix2")
        self.assertEqual(blobs.typed_calls[0]["job_external_key"], job["id"])
        manifest = blobs.typed_calls[0]["analysis_manifest"]
        self.assertEqual(manifest["datasets"]["baselines"]["rowCount"], 1)
        self.assertGreater(manifest["datasets"]["chartResults"]["rowCount"], 0)
        self.assertEqual(
            [call["phase"] for call in blobs.typed_calls],
            [
                "analysis-start",
                "analysis-chunk",
                "analysis-chunk",
                "analysis-chunk",
                "analysis-finish",
                "model",
            ],
        )
        self.assertIsNotNone(blobs.get_json(LATEST_BLOB_PATH))

    def test_typed_persistence_failure_leaves_public_pointer_unchanged(self) -> None:
        class FailingTypedStore(MemoryBlobStore):
            typed_persistence_enabled = True

            def persist_typed_generation(self, **_kwargs):
                raise RuntimeError("typed persistence unavailable")

        blobs = FailingTypedStore()
        jobs = MemoryJobStore()
        job = new_job("typed-analysis-failure", NOW)
        jobs.save(job)
        jobs.set_latest_job_id(job["id"])
        jobs.set_active_job_id(job["id"])

        result = execute_analysis_job(
            job["id"],
            blobs=blobs,
            jobs=jobs,
            client=WorkerClient(),
            now=lambda: NOW,
        )

        self.assertEqual(result["status"], "failed")
        self.assertIsNone(blobs.get_json(LATEST_BLOB_PATH))
        self.assertIsNotNone(blobs.get_json(CURRENT_SNAPSHOT_PATH))
        self.assertIsNotNone(blobs.get_json(f"{STAGING_PREFIX}{job['id']}.json"))
        self.assertIsNotNone(blobs.get_json(typed_checkpoint_path(job["id"])))

    def test_typed_checkpoint_rows_are_bounded_and_hash_validated(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        job = new_job("typed-shards", NOW)
        jobs.save(job)
        manifest = _write_typed_frame_shards(
            blob_store=blobs,
            job_store=jobs,
            job_id=job["id"],
            mix_spec=resolve_mix("phoenix2"),
            frames={
                "baselines": pd.DataFrame(),
                "contributions": pd.DataFrame(
                    [{"row": number} for number in range(5_001)]
                ),
                "chartResults": pd.DataFrame(),
            },
            lease_heartbeat=None,
        )

        contribution_manifest = manifest["datasets"]["contributions"]
        self.assertEqual(contribution_manifest["rowCount"], 5_001)
        self.assertEqual(
            [item["rowCount"] for item in contribution_manifest["shards"]],
            [5_000, 1],
        )
        first_descriptor = contribution_manifest["shards"][0]
        first_path = typed_checkpoint_shard_path(
            job["id"], "contributions", 0
        )
        self.assertEqual(first_descriptor["pathname"], first_path)
        checkpoint = {
            "jobId": job["id"],
            "mix": "phoenix2",
        }
        self.assertEqual(
            len(
                _load_typed_checkpoint_shard(
                    blobs,
                    checkpoint=checkpoint,
                    dataset="contributions",
                    descriptor=first_descriptor,
                )
            ),
            5_000,
        )
        tampered = blobs.get_json(first_path)
        tampered["rows"][0]["row"] = -1
        blobs.put_json(first_path, tampered)
        with self.assertRaisesRegex(ValueError, "count/hash validation"):
            _load_typed_checkpoint_shard(
                blobs,
                checkpoint=checkpoint,
                dataset="contributions",
                descriptor=first_descriptor,
            )

    def test_typed_model_resume_validates_committed_input_shard_paths(self) -> None:
        class RecordingStore(MemoryBlobStore):
            def __init__(self) -> None:
                super().__init__()
                self.json_reads = []
                self.list_prefixes = []

            def get_json(self, pathname):
                self.json_reads.append(pathname)
                return super().get_json(pathname)

            def list(self, prefix):
                self.list_prefixes.append(prefix)
                return super().list(prefix)

        from pumbility_contract import (
            recommendation_index_path,
            recommendation_model_path,
            recommendation_phoenix1_shard_path,
            recommendation_phoenix2_shard_path,
            recommendation_score_model_path,
        )

        blobs = RecordingStore()
        generation = "compact-model-resume"
        index = {
            "generationKey": generation,
            "inputShardCount": 1,
            "players": [],
        }
        model = {"generationKey": generation, "method": {}}
        score_model = b"numeric-model"
        blobs.put_json(recommendation_index_path(generation), index)
        blobs.put_json(recommendation_model_path(generation), model)
        blobs.put_bytes(
            recommendation_score_model_path(generation),
            score_model,
            content_type="application/octet-stream",
        )
        phoenix1_shard = {"rows": [1]}
        phoenix2_shard = {"rows": [2]}
        blobs.put_json(
            recommendation_phoenix1_shard_path(generation, 0), phoenix1_shard
        )
        blobs.put_json(
            recommendation_phoenix2_shard_path(generation, 0), phoenix2_shard
        )

        loaded_index, artifacts = _load_checkpoint_model_artifacts(
            blobs,
            {
                "generationKey": generation,
                "phoenix1ShardCount": 1,
                "phoenix2ShardCount": 1,
                "indexSha256": _canonical_json_sha256(index),
                "modelSha256": _canonical_json_sha256(model),
                "scoreModelSha256": hashlib.sha256(score_model).hexdigest(),
                "phoenix1ShardsSha256": _canonical_json_sha256(
                    [_canonical_json_sha256(phoenix1_shard)]
                ),
                "phoenix2ShardsSha256": _canonical_json_sha256(
                    [_canonical_json_sha256(phoenix2_shard)]
                ),
            },
        )

        self.assertEqual(loaded_index, index)
        self.assertEqual(artifacts[3:], (1, 1))
        self.assertNotIn(
            recommendation_phoenix1_shard_path(generation, 0), blobs.json_reads
        )
        self.assertNotIn(
            recommendation_phoenix2_shard_path(generation, 0), blobs.json_reads
        )
        self.assertEqual(len(blobs.list_prefixes), 2)
        blobs.delete(
            recommendation_phoenix2_shard_path(generation, 0)
        )
        with self.assertRaisesRegex(RuntimeError, "input generation is incomplete"):
            _load_checkpoint_model_artifacts(
                blobs,
                {
                    "generationKey": generation,
                    "phoenix1ShardCount": 1,
                    "phoenix2ShardCount": 1,
                    "indexSha256": _canonical_json_sha256(index),
                    "modelSha256": _canonical_json_sha256(model),
                    "scoreModelSha256": hashlib.sha256(score_model).hexdigest(),
                    "phoenix1ShardsSha256": _canonical_json_sha256(
                        [_canonical_json_sha256(phoenix1_shard)]
                    ),
                    "phoenix2ShardsSha256": _canonical_json_sha256(
                        [_canonical_json_sha256(phoenix2_shard)]
                    ),
                },
            )

    def test_third_no_progress_resume_fails_and_releases_active_job(self) -> None:
        class TypedMemoryStore(MemoryBlobStore):
            typed_persistence_enabled = True

        blobs = TypedMemoryStore()
        jobs = MemoryJobStore()
        job = new_job("typed-no-progress", NOW)
        jobs.save(job)
        jobs.set_active_job_id(job["id"])
        first = execute_analysis_job(
            job["id"],
            blobs=blobs,
            jobs=jobs,
            client=WorkerClient(),
            now=lambda: NOW,
            yield_after_typed_checkpoint=True,
        )
        self.assertEqual(first[ANALYSIS_CONTINUATION_FIELD], "model")
        pathname = typed_checkpoint_path(job["id"])
        checkpoint = blobs.get_json(pathname)
        checkpoint["resumeAudit"] = {"token": "analysis", "observations": 2}
        blobs.put_json(pathname, checkpoint)

        result = execute_analysis_job(
            job["id"],
            blobs=blobs,
            jobs=jobs,
            client=WorkerClient(),
            now=lambda: NOW,
            yield_after_typed_checkpoint=True,
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("no progress", result["error"])
        self.assertIsNone(jobs.active_job_id())

    def test_model_resume_recovers_a_committed_generation_before_stuck_audit(self) -> None:
        from pumbility_contract import (
            recommendation_generation_key,
            recommendation_index_path,
            recommendation_model_path,
            recommendation_score_model_path,
        )

        class TypedMemoryStore(MemoryBlobStore):
            typed_persistence_enabled = True

        blobs = TypedMemoryStore()
        jobs = MemoryJobStore()
        job = new_job("typed-model-commit-redelivery", NOW)
        jobs.save(job)
        jobs.set_active_job_id(job["id"])
        blobs.put_json(
            "analysis/private/phoenix1.json",
            {"mix": "Phoenix", "players": [], "charts": [], "scores": []},
        )
        first = execute_analysis_job(
            job["id"],
            blobs=blobs,
            jobs=jobs,
            client=WorkerClient(),
            now=lambda: NOW,
            yield_after_typed_checkpoint=True,
        )
        self.assertEqual(first[ANALYSIS_CONTINUATION_FIELD], "model")

        pathname = typed_checkpoint_path(job["id"])
        checkpoint = blobs.get_json(pathname)
        checkpoint["resumeAudit"] = {"token": "analysis", "observations": 2}
        blobs.put_json(pathname, checkpoint)
        generated_at = checkpoint["payload"]["generatedAtUtc"]
        generation = recommendation_generation_key(job["id"])
        model = {
            "generationKey": generation,
            "generatedAtUtc": generated_at,
        }
        index = {
            "generationKey": generation,
            "modelGeneratedAtUtc": generated_at,
            "modelPath": recommendation_model_path(generation),
            "inputShardCount": 0,
            "players": [],
        }
        blobs.put_json(recommendation_model_path(generation), model)
        blobs.put_bytes(
            recommendation_score_model_path(generation),
            b"durable-model",
            content_type="application/x-npz",
        )
        blobs.put_json(recommendation_index_path(generation), index)

        with (
            patch(
                "analysis_runtime.build_recommendation_model_artifacts",
                side_effect=AssertionError("the committed model must not rebuild"),
            ),
            patch(
                "analysis_runtime.build_combined_chart_results",
                return_value=([], {}, {}),
            ),
            patch(
                "analysis_runtime.build_combined_tier_payload",
                return_value={"generatedAtUtc": generated_at},
            ),
        ):
            result = execute_analysis_job(
                job["id"],
                blobs=blobs,
                jobs=jobs,
                client=WorkerClient(),
                now=lambda: NOW,
                yield_after_typed_checkpoint=True,
            )

        recovered = blobs.get_json(pathname)
        self.assertEqual(result["status"], "running")
        self.assertEqual(result[ANALYSIS_CONTINUATION_FIELD], "snapshot")
        self.assertEqual(recovered["phase"], TYPED_CHECKPOINT_MODEL_PHASE)
        self.assertEqual(recovered["model"]["generationKey"], generation)
        self.assertEqual(recovered["resumeAudit"]["observations"], 2)

    def test_typed_persistence_retry_resumes_the_private_checkpoint(self) -> None:
        class ResumableTypedStore(MemoryBlobStore):
            typed_persistence_enabled = True

            def __init__(self) -> None:
                super().__init__()
                self.persist_attempts = 0

            def persist_typed_generation(self, **_kwargs):
                self.persist_attempts += 1
                if self.persist_attempts == 1:
                    raise RuntimeError("typed persistence unavailable")
                return "analysis-run", None

        blobs = ResumableTypedStore()
        jobs = MemoryJobStore()
        job = new_job("typed-analysis-resume", NOW)
        jobs.save(job)
        jobs.set_latest_job_id(job["id"])
        jobs.set_active_job_id(job["id"])

        first = execute_analysis_job(
            job["id"],
            blobs=blobs,
            jobs=jobs,
            client=WorkerClient(),
            now=lambda: NOW,
        )
        self.assertEqual(first["status"], "failed")
        self.assertIsNotNone(blobs.get_json(typed_checkpoint_path(job["id"])))

        with patch(
            "analysis_runtime.analyze_snapshot",
            side_effect=AssertionError("analysis must not repeat"),
        ):
            second = execute_analysis_job(
                job["id"],
                blobs=blobs,
                jobs=jobs,
                client=WorkerClient(),
                now=lambda: NOW,
            )

        self.assertEqual(second["status"], "completed")
        self.assertEqual(blobs.persist_attempts, 7)
        self.assertIsNone(blobs.get_json(typed_checkpoint_path(job["id"])))
        self.assertIsNotNone(blobs.get_json(LATEST_BLOB_PATH))

    def test_typed_queue_execution_yields_at_bounded_checkpoint_phases(self) -> None:
        class TypedMemoryStore(MemoryBlobStore):
            typed_persistence_enabled = True

            def __init__(self) -> None:
                super().__init__()
                self.persist_phases = []

            def persist_typed_generation(self, **kwargs):
                phase = kwargs["phase"]
                self.persist_phases.append(phase)
                if phase == "analysis-start":
                    return "analysis-run", None
                self.assertEqual(kwargs["analysis_run_id"], "analysis-run")
                return (
                    "analysis-run",
                    "model-generation" if phase == "model" else None,
                )

            def assertEqual(self, first, second):
                if first != second:
                    raise AssertionError(f"{first!r} != {second!r}")

        blobs = TypedMemoryStore()
        jobs = MemoryJobStore()
        job = new_job("typed-bounded-phases", NOW)
        jobs.save(job)
        jobs.set_latest_job_id(job["id"])
        jobs.set_active_job_id(job["id"])
        blobs.put_json(
            "analysis/private/phoenix1.json",
            {"mix": "Phoenix", "players": [], "charts": [], "scores": []},
        )
        generation = "typed-bounded-phases"
        model_artifacts = (
            {
                "storageSchemaVersion": 3,
                "generationKey": generation,
                "generatedAtUtc": isoformat_utc(NOW),
                "inputShardCount": 0,
                "players": [],
            },
            {"generationKey": generation},
            b"model",
            [],
            [],
        )

        first = execute_analysis_job(
            job["id"],
            blobs=blobs,
            jobs=jobs,
            client=WorkerClient(),
            now=lambda: NOW,
            yield_after_typed_checkpoint=True,
        )

        checkpoint = blobs.get_json(typed_checkpoint_path(job["id"]))
        self.assertEqual(first[ANALYSIS_CONTINUATION_FIELD], "model")
        self.assertEqual(checkpoint["phase"], TYPED_CHECKPOINT_ANALYSIS_PHASE)
        self.assertIsNone(blobs.get_json(LATEST_BLOB_PATH))

        with (
            patch(
                "analysis_runtime.analyze_snapshot",
                side_effect=AssertionError("base analysis must not repeat"),
            ),
            patch("analysis_runtime.build_combined_chart_results", return_value=([], {}, {})),
            patch("analysis_runtime.build_combined_tier_payload", return_value={}),
            patch(
                "analysis_runtime.build_recommendation_model_artifacts",
                return_value=model_artifacts,
            ) as build_model,
        ):
            second = execute_analysis_job(
                job["id"],
                blobs=blobs,
                jobs=jobs,
                client=WorkerClient(),
                now=lambda: NOW,
                yield_after_typed_checkpoint=True,
            )
            second_phase = blobs.get_json(typed_checkpoint_path(job["id"]))["phase"]
            third = execute_analysis_job(
                job["id"],
                blobs=blobs,
                jobs=jobs,
                client=WorkerClient(),
                now=lambda: NOW,
                yield_after_typed_checkpoint=True,
            )
            third_phase = blobs.get_json(typed_checkpoint_path(job["id"]))["phase"]
            continuations = [second, third]
            phases = [second_phase, third_phase]
            while True:
                result = execute_analysis_job(
                    job["id"],
                    blobs=blobs,
                    jobs=jobs,
                    client=WorkerClient(),
                    now=lambda: NOW,
                    yield_after_typed_checkpoint=True,
                )
                if result.get("status") == "completed":
                    completed = result
                    break
                continuations.append(result)
                phases.append(
                    blobs.get_json(typed_checkpoint_path(job["id"]))["phase"]
                )

        self.assertEqual(second[ANALYSIS_CONTINUATION_FIELD], "snapshot")
        self.assertEqual(third[ANALYSIS_CONTINUATION_FIELD], "database-analysis")
        self.assertEqual(
            [result[ANALYSIS_CONTINUATION_FIELD] for result in continuations],
            [
                "snapshot",
                "database-analysis",
                "database-analysis",
                "database-analysis",
                "database-analysis",
                "database-analysis",
                "database-model",
                "publish",
            ],
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(
            phases,
            [
                TYPED_CHECKPOINT_MODEL_PHASE,
                TYPED_CHECKPOINT_SNAPSHOT_PHASE,
                TYPED_CHECKPOINT_DATABASE_SHARDS_PHASE,
                TYPED_CHECKPOINT_DATABASE_SHARDS_PHASE,
                TYPED_CHECKPOINT_DATABASE_SHARDS_PHASE,
                TYPED_CHECKPOINT_DATABASE_SHARDS_PHASE,
                TYPED_CHECKPOINT_DATABASE_ANALYSIS_PHASE,
                TYPED_CHECKPOINT_DATABASE_MODEL_PHASE,
            ],
        )
        self.assertEqual(build_model.call_count, 1)
        self.assertEqual(
            blobs.persist_phases,
            [
                "analysis-start",
                "analysis-chunk",
                "analysis-chunk",
                "analysis-chunk",
                "analysis-finish",
                "model",
            ],
        )
        self.assertIsNone(blobs.get_json(typed_checkpoint_path(job["id"])))
        self.assertIsNotNone(blobs.get_json(LATEST_BLOB_PATH))

    def test_checkpoint_continuation_stops_heartbeat_before_store_handoff(self) -> None:
        events = []

        class Heartbeat:
            def stop(self) -> None:
                events.append("stopped")

        class HandoffJobs(MemoryJobStore):
            def handoff_continuation(self, job_id, payload) -> None:
                events.append(("handoff", job_id, payload["status"]))

        jobs = HandoffJobs()
        job = new_job("handoff-job", NOW)
        jobs.save(job)

        result = _checkpoint_continuation(
            job_store=jobs,
            job_id=job["id"],
            continuation="model",
            stage="analyzing",
            message="Analysis checkpoint saved.",
            lease_heartbeat=Heartbeat(),
        )

        self.assertEqual(
            events,
            ["stopped", ("handoff", "handoff-job", "running")],
        )
        self.assertEqual(result["status"], "running")
        self.assertEqual(result[ANALYSIS_CONTINUATION_FIELD], "model")

    def test_analysis_worker_queues_checkpoint_continuation_once(self) -> None:
        from worker.tasks import refresh_analysis

        result = {
            "status": "running",
            ANALYSIS_CONTINUATION_FIELD: "model",
        }
        with (
            patch("worker.tasks.execute_analysis_job", return_value=result.copy()),
            patch.object(refresh_analysis, "apply_async") as enqueue,
        ):
            returned = refresh_analysis.run("bounded-worker")

        self.assertEqual(returned, {"status": "running"})
        enqueue.assert_called_once_with(
            args=["bounded-worker"],
            task_id=(
                "bounded-worker-model-checkpoint-"
                f"v{TYPED_CHECKPOINT_SCHEMA_VERSION}"
            ),
            queue="analysis",
        )

    def test_analysis_worker_uses_unique_bounded_shard_continuation_ids(self) -> None:
        from worker.tasks import refresh_analysis

        result = {
            "status": "running",
            ANALYSIS_CONTINUATION_FIELD: "database-analysis",
            ANALYSIS_CONTINUATION_SEQUENCE_FIELD: "000007",
        }
        with (
            patch("worker.tasks.execute_analysis_job", return_value=result.copy()),
            patch.object(refresh_analysis, "apply_async") as enqueue,
        ):
            returned = refresh_analysis.run("bounded-worker")

        self.assertEqual(returned, {"status": "running"})
        enqueue.assert_called_once_with(
            args=["bounded-worker"],
            task_id=(
                "bounded-worker-database-analysis-000007-checkpoint-"
                f"v{TYPED_CHECKPOINT_SCHEMA_VERSION}"
            ),
            queue="analysis",
        )

    def test_supabase_capable_store_heartbeats_through_every_heavy_phase(self) -> None:
        class HeartbeatHandle:
            def __init__(self) -> None:
                self.active = True
                self.pulses = 0
                self.stops = 0

            def pulse(self) -> None:
                self.assert_active()
                self.pulses += 1

            def stop(self) -> None:
                self.assert_active()
                self.active = False
                self.stops += 1

            def assert_active(self) -> None:
                if not self.active:
                    raise AssertionError("heartbeat stopped before work completed")

        class HeartbeatJobs(MemoryJobStore):
            def __init__(self) -> None:
                super().__init__()
                self.handle = HeartbeatHandle()

            def start_lease_heartbeat(self, job_id: str) -> HeartbeatHandle:
                self.started_for = job_id
                return self.handle

        jobs = HeartbeatJobs()
        blobs = MemoryBlobStore()
        job = new_job("lease-covered-analysis", NOW)
        jobs.save(job)
        jobs.set_active_job_id(job["id"])
        blobs.put_json(
            "analysis/private/phoenix1.json",
            {"mix": "Phoenix", "players": [], "charts": [], "scores": []},
        )
        client = WorkerClient()
        original_fetch = client.fetch_page_collection

        def observed_fetch(*args, **kwargs):
            jobs.handle.assert_active()
            return original_fetch(*args, **kwargs)

        client.fetch_page_collection = observed_fetch  # type: ignore[method-assign]
        original_analyze = analyze_snapshot
        original_publish = publish_success

        def observed_analyze(*args, **kwargs):
            jobs.handle.assert_active()
            return original_analyze(*args, **kwargs)

        def observed_combined(*_args, **_kwargs):
            jobs.handle.assert_active()
            return [], {}, {}

        def observed_model(*_args, **_kwargs):
            jobs.handle.assert_active()
            return ({}, {}, b"", [], [])

        def observed_model_publish(*_args, **_kwargs):
            jobs.handle.assert_active()

        def observed_publish(*args, **kwargs):
            jobs.handle.assert_active()
            return original_publish(*args, **kwargs)

        with (
            patch("analysis_runtime.analyze_snapshot", side_effect=observed_analyze),
            patch("analysis_runtime.build_combined_chart_results", side_effect=observed_combined),
            patch("analysis_runtime.build_combined_tier_payload", return_value={}),
            patch("analysis_runtime.build_recommendation_model_artifacts", side_effect=observed_model),
            patch("analysis_runtime.publish_recommendation_model_artifacts", side_effect=observed_model_publish),
            patch("analysis_runtime.publish_success", side_effect=observed_publish),
        ):
            result = execute_analysis_job(
                job["id"], blobs=blobs, jobs=jobs, client=client, now=lambda: NOW
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(jobs.started_for, job["id"])
        self.assertGreaterEqual(jobs.handle.pulses, 6)
        self.assertEqual(jobs.handle.stops, 1)
        self.assertFalse(jobs.handle.active)

    def test_lost_heartbeat_fails_before_publication(self) -> None:
        class FailingHeartbeat:
            def __init__(self) -> None:
                self.pulses = 0

            def pulse(self) -> None:
                self.pulses += 1
                if self.pulses == 3:
                    raise RuntimeError("lease lost")

            def stop(self) -> None:
                return None

        class HeartbeatJobs(MemoryJobStore):
            def __init__(self) -> None:
                super().__init__()
                self.handle = FailingHeartbeat()

            def start_lease_heartbeat(self, _job_id: str) -> FailingHeartbeat:
                return self.handle

        blobs = MemoryBlobStore()
        jobs = HeartbeatJobs()
        job = new_job("lost-lease-analysis", NOW)
        jobs.save(job)
        jobs.set_active_job_id(job["id"])
        with patch("analysis_runtime.publish_success") as publish:
            result = execute_analysis_job(
                job["id"], blobs=blobs, jobs=jobs, client=WorkerClient(), now=lambda: NOW
            )

        self.assertEqual(result["status"], "failed")
        publish.assert_not_called()
        self.assertIsNone(blobs.get_json(LATEST_BLOB_PATH))

    def test_deployment_job_reuses_snapshot_without_upstream_sync(self) -> None:
        blobs = MemoryBlobStore()
        stored_snapshot = {
            "schemaVersion": 2,
            "mix": "Phoenix2",
            "generatedAtUtc": isoformat_utc(NOW - timedelta(hours=1)),
            "players": [
                {
                    "playerId": "player",
                    "username": "private",
                    "lastSyncedAtUtc": isoformat_utc(NOW - timedelta(hours=1)),
                }
            ],
            "charts": [chart(index) for index in range(30)],
            "scores": [
                {**score(index), "playerId": "player"}
                for index in range(30)
            ],
        }
        blobs.put_json(CURRENT_SNAPSHOT_PATH, stored_snapshot)
        jobs = MemoryJobStore()
        job = new_job(
            "analysis-deploy-test", NOW, reanalyze_only=True, trigger="deployment"
        )
        jobs.save(job)
        jobs.set_active_job_id(job["id"])
        with patch(
            "analysis_runtime.synchronize_phoenix2_snapshot",
            wraps=synchronize_phoenix2_snapshot,
        ) as synchronize:
            result = execute_analysis_job(
                job["id"],
                blobs=blobs,
                jobs=jobs,
                client=WorkerClient(),
                now=lambda: NOW,
            )
        self.assertEqual(result["status"], "completed")
        synchronize.assert_not_called()
        self.assertEqual(blobs.get_json(CURRENT_SNAPSHOT_PATH), stored_snapshot)

    def test_deployment_reanalysis_fails_closed_without_a_snapshot(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        job = new_job(
            "analysis-deploy-missing", NOW, reanalyze_only=True, trigger="deployment"
        )
        jobs.save(job)
        jobs.set_active_job_id(job["id"])
        with patch("analysis_runtime.synchronize_phoenix2_snapshot") as synchronize:
            result = execute_analysis_job(
                job["id"],
                blobs=blobs,
                jobs=jobs,
                client=object(),
                now=lambda: NOW,
            )
        self.assertEqual(result["status"], "failed")
        self.assertIn("No stored Phoenix 2 snapshot", result["error"])
        synchronize.assert_not_called()

    def test_failed_worker_has_safe_error_and_five_minute_retry(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        job = new_job("analysis-20260807T06", NOW)
        jobs.save(job)
        jobs.set_active_job_id(job["id"])

        class BadClient:
            def fetch_page_collection(self, path, params=None):
                raise ValueError("safe fixture failure")

        result = execute_analysis_job(
            job["id"], blobs=blobs, jobs=jobs, client=BadClient(), now=lambda: NOW
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "safe fixture failure")
        self.assertEqual(
            result["retryAllowedAtUtc"], isoformat_utc(NOW + FAILED_RETRY_DELAY)
        )

    def test_celery_eager_mode_executes_the_real_worker_task(self) -> None:
        from worker.celery import app
        from worker.tasks import refresh_analysis

        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        job = new_job("analysis-20260807T06", NOW)
        jobs.save(job)
        jobs.set_active_job_id(job["id"])
        original_eager = app.conf.task_always_eager
        original_propagates = app.conf.task_eager_propagates
        original_broker = app.conf.broker_url
        app.conf.task_always_eager = True
        app.conf.task_eager_propagates = True
        app.conf.broker_url = "memory://"
        try:
            with (
                patch("analysis_runtime.PrivateBlobStore", return_value=blobs),
                patch("analysis_runtime.RuntimeJobStore", return_value=jobs),
                patch("analysis_runtime.PiuScoresClient", return_value=WorkerClient()),
                patch.dict("os.environ", {"PIU_SCORES_API_KEY": "piu_scores_live_" + "a" * 64}),
            ):
                result = refresh_analysis.apply_async(
                    args=[job["id"]], task_id=job["id"]
                ).get()
        finally:
            app.conf.task_always_eager = original_eager
            app.conf.task_eager_propagates = original_propagates
            app.conf.broker_url = original_broker
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(blobs.get_json(LATEST_BLOB_PATH))

    def test_retention_keeps_latest_ten_aggregate_runs(self) -> None:
        blobs = MemoryBlobStore()
        snapshot = {"players": [], "charts": [], "scores": []}
        for index in range(12):
            generated = NOW + timedelta(seconds=index)
            publish_success(
                blobs,
                job_id=f"job-{index}",
                snapshot=snapshot,
                payload=latest_payload(generated),
            )
        self.assertEqual(len(blobs.list(RUNS_PREFIX)), 10)

    def test_staging_cleanup_removes_only_objects_older_than_24_hours(self) -> None:
        blobs = MemoryBlobStore()
        stale = f"{STAGING_PREFIX}stale.json"
        recent = f"{STAGING_PREFIX}recent.json"
        blobs.put_json(stale, {})
        blobs.put_json(recent, {})
        blobs.uploaded[stale] = NOW - timedelta(hours=25)
        blobs.uploaded[recent] = NOW - timedelta(hours=23)
        self.assertEqual(cleanup_abandoned_staging(blobs, now=NOW), 1)
        self.assertIsNone(blobs.get_json(stale))
        self.assertIsNotNone(blobs.get_json(recent))

    def test_typed_checkpoint_cleanup_is_bounded_and_keeps_active_job(self) -> None:
        blobs = MemoryBlobStore()
        active = typed_checkpoint_shard_path("active", "baselines", 0)
        stale = typed_checkpoint_shard_path("stale", "baselines", 0)
        recent = typed_checkpoint_shard_path("recent", "baselines", 0)
        for pathname in (active, stale, recent):
            blobs.put_json(pathname, {})
        blobs.uploaded[active] = NOW - timedelta(hours=30)
        blobs.uploaded[stale] = NOW - timedelta(hours=25)
        blobs.uploaded[recent] = NOW - timedelta(hours=23)

        self.assertEqual(
            cleanup_abandoned_typed_checkpoints(
                blobs, now=NOW, keep_job_id="active"
            ),
            1,
        )
        self.assertIsNotNone(blobs.get_json(active))
        self.assertIsNone(blobs.get_json(stale))
        self.assertIsNotNone(blobs.get_json(recent))


class EquivalenceTests(unittest.TestCase):
    def test_optimized_and_full_snapshot_payloads_are_identical(self) -> None:
        players, charts, scores, _ = make_synthetic_snapshot(players_per_folder=2)
        stamp = "2026-08-07T06:30:00Z"
        snapshot = {
            "generatedAtUtc": stamp,
            "players": [
                {"playerId": row["userId"], "lastSyncedAtUtc": stamp}
                for row in players
            ],
            "charts": charts,
            "scores": scores,
        }
        config = AnalysisConfig(bootstrap_samples=0)
        optimized = analyzer_input(snapshot, eligible_only=True)
        complete = analyzer_input(snapshot, eligible_only=False)
        optimized_result, _, optimized_summary, _ = analyze_snapshot(*optimized, config)
        complete_result, _, complete_summary, _ = analyze_snapshot(*complete, config)
        optimized_summary["generatedAtUtc"] = stamp
        complete_summary["generatedAtUtc"] = stamp
        self.assertEqual(
            build_web_payload(optimized_result, optimized_summary),
            build_web_payload(complete_result, complete_summary),
        )


if __name__ == "__main__":
    unittest.main()
