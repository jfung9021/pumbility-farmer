import hashlib
import hmac
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from analysis_runtime import (
    CURRENT_SNAPSHOT_PATH,
    FAILED_RETRY_DELAY,
    LATEST_BLOB_PATH,
    RUNS_PREFIX,
    STAGING_PREFIX,
    MemoryBlobStore,
    MemoryJobStore,
    cleanup_abandoned_staging,
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
    update_job,
)
from api.cron import cron_authorized
from api_service import app as api_app
from phoenix2_sync import analyzer_input, synchronize_phoenix2_snapshot
from piu_misgrade_analyzer import (
    AnalysisConfig,
    SCRIPT_VERSION,
    analyze_snapshot,
    build_web_payload,
    make_synthetic_snapshot,
)
from piu_recommendations import combined_tier_blob_path, recommendation_blob_path
NOW = datetime(2026, 8, 7, 6, 30, tzinfo=timezone.utc)
API_CLIENT = TestClient(api_app)


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
        self.assertEqual(body["archiveUrl"], "/data/phoenix1-20260807.json")
        self.assertEqual(enqueued, [])

    def test_successful_result_has_no_manual_refresh_cooldown(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        blobs.put_json(LATEST_BLOB_PATH, latest_payload(NOW - timedelta(seconds=1)))
        enqueued: list[str] = []
        status, body = request_refresh(blobs, jobs, enqueued.append, now=NOW)
        self.assertEqual((status, body["outcome"]), (202, "started"))
        self.assertEqual(enqueued, ["analysis-20260807T06"])

    def test_deployment_refresh_is_full_and_deduplicated_by_deployment(self) -> None:
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
            full_sync=True,
            trigger="deployment",
        )
        self.assertEqual((status, body["outcome"]), (202, "started"))
        self.assertEqual(enqueued, [job_id])
        self.assertTrue(body["job"]["fullSync"])
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
            full_sync=True,
            trigger="deployment",
        )
        self.assertEqual((status, duplicate["outcome"]), (202, "existing"))
        self.assertEqual(enqueued, [job_id])

    def test_failed_deployment_refresh_reuses_id_after_retry_delay(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        job_id = deterministic_deployment_job_id("dpl_retry")
        failed = new_job(job_id, NOW, full_sync=True, trigger="deployment")
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
            full_sync=True,
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
            full_sync=True,
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
                "generatedAtUtc": isoformat_utc(NOW),
                "method": {"baselineRanks": [11, 30]},
                "charts": [
                    {
                        "chartId": "single-easy-farm",
                        "songName": "Easy Farm",
                        "type": "Single",
                        "level": 22,
                        "estimatedDifficulty": 21.75,
                    },
                    {
                        "chartId": "single-too-hard",
                        "songName": "Too Hard",
                        "type": "Single",
                        "level": 23,
                        "estimatedDifficulty": 23.01,
                    },
                ],
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
            manual = API_CLIENT.get("/api/recommendations?rating=22.5")
            missing = API_CLIENT.get("/api/recommendations?playerKey=missing")
        self.assertEqual(players.status_code, 200)
        self.assertEqual(players.json()["players"][0]["username"], "PLAYER")
        self.assertEqual(
            players.json()["players"][0]["eligibility"],
            {"singles": True, "doubles": False},
        )
        self.assertNotIn("modes", players.json()["players"][0])
        self.assertEqual(selected.json()["player"]["playerKey"], "public-key")
        self.assertEqual(manual.status_code, 200)
        self.assertTrue(manual.json()["player"]["manual"])
        self.assertEqual(
            [
                row["chartId"]
                for row in manual.json()["player"]["modes"]["singles"]["candidates"]
            ],
            ["single-easy-farm"],
        )
        self.assertEqual(
            manual.json()["player"]["modes"]["singles"]["candidateRange"],
            [None, 23.0],
        )
        self.assertEqual(missing.status_code, 404)

    def test_recommendations_require_a_player_key_and_generated_index(self) -> None:
        blobs = MemoryBlobStore()
        with patch("api.recommendations.PrivateBlobStore", return_value=blobs):
            no_key = API_CLIENT.get("/api/recommendations")
            invalid_rating = API_CLIENT.get("/api/recommendations?rating=not-a-number")
            no_index = API_CLIENT.get("/api/recommendations/players")
        self.assertEqual(no_key.status_code, 400)
        self.assertEqual(invalid_rating.status_code, 400)
        self.assertEqual(no_index.status_code, 404)

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
        self.assertEqual(first.headers["location"], "/data/phoenix1-20260807.json")
        self.assertEqual(second.json()["mix"]["key"], "phoenix2")
        self.assertEqual(invalid.status_code, 400)

    def test_archived_phoenix1_manual_refresh_and_job_lookup_are_rejected(self) -> None:
        refresh = API_CLIENT.post("/api/analyze?mix=phoenix1")
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

    def test_promoted_deployment_webhook_starts_deduplicated_full_refresh(self) -> None:
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
        job_id = deterministic_deployment_job_id("dpl_example")
        job = new_job(job_id, NOW, full_sync=True, trigger="deployment")
        with (
            patch.dict(
                "os.environ",
                {
                    "VERCEL_DEPLOY_WEBHOOK_SECRET": secret,
                    "VERCEL_PROJECT_ID": "prj_example",
                },
            ),
            patch(
                "api.deploy.start_or_reuse_analysis",
                return_value=(202, {"outcome": "started", "job": job}),
            ) as start,
        ):
            response = API_CLIENT.post(
                "/api/deploy",
                content=raw,
                headers={"x-vercel-signature": signature},
            )
        self.assertEqual((response.status_code, response.json()["outcome"]), (202, "started"))
        start.assert_called_once_with(
            force_refresh=True,
            deterministic_job_id=job_id,
            full_sync=True,
            trigger="deployment",
        )

    def test_deployment_webhook_retries_while_another_job_is_active(self) -> None:
        secret = "deployment-secret"
        raw = json.dumps({
            "type": "deployment.promoted",
            "payload": {
                "deployment": {"id": "dpl_example"},
                "project": {"id": "prj_example"},
            },
        }, separators=(",", ":")).encode()
        signature = hmac.new(secret.encode(), raw, hashlib.sha1).hexdigest()
        other = new_job("analysis-manual", NOW)
        with (
            patch.dict(
                "os.environ",
                {
                    "VERCEL_DEPLOY_WEBHOOK_SECRET": secret,
                    "VERCEL_PROJECT_ID": "prj_example",
                },
            ),
            patch(
                "api.deploy.start_or_reuse_analysis",
                return_value=(202, {"outcome": "existing", "job": other}),
            ),
        ):
            response = API_CLIENT.post(
                "/api/deploy",
                content=raw,
                headers={"x-vercel-signature": signature},
            )
        self.assertEqual(response.status_code, 503)

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
        with patch("api.analyze.start_or_reuse_analysis", return_value=(
            202, {"outcome": "started", "job": job}
        )):
            response = API_CLIENT.post("/api/analyze")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["outcome"], "started")

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
        if path == "api/v2/players/player/scores":
            return [score(index) for index in range(30)]
        raise AssertionError(path)


class WorkerTests(unittest.TestCase):
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
        self.assertNotIn("gameTag", serialized)
        self.assertIsNone(blobs.get_json(f"{STAGING_PREFIX}{job['id']}.json"))
        self.assertIsNone(jobs.active_job_id())

    def test_deployment_job_discards_current_snapshot_for_full_sync(self) -> None:
        blobs = MemoryBlobStore()
        blobs.put_json(CURRENT_SNAPSHOT_PATH, {
            "players": [{"playerId": "old-player"}],
            "charts": [],
            "scores": [],
        })
        jobs = MemoryJobStore()
        job = new_job(
            "analysis-deploy-test", NOW, full_sync=True, trigger="deployment"
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
        self.assertIsNone(synchronize.call_args.args[1])

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
