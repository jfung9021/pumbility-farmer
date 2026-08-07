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
    execute_analysis_job,
    isoformat_utc,
    new_job,
    publish_success,
    request_refresh,
    update_job,
)
from api.cron import cron_authorized
from api_service import app as api_app
from phoenix2_sync import analyzer_input
from piu_misgrade_analyzer import (
    AnalysisConfig,
    analyze_snapshot,
    build_web_payload,
    make_synthetic_snapshot,
)


NOW = datetime(2026, 8, 7, 6, 30, tzinfo=timezone.utc)
API_CLIENT = TestClient(api_app)


def latest_payload(generated: datetime) -> dict:
    return {
        "generatedAtUtc": isoformat_utc(generated),
        "summary": {"modes": {}},
        "singles": [],
        "doubles": [],
        "relativeGroups": [],
    }


class CoordinatorTests(unittest.TestCase):
    def test_fresh_result_suppresses_enqueue_for_one_hour(self) -> None:
        blobs = MemoryBlobStore()
        jobs = MemoryJobStore()
        blobs.put_json(LATEST_BLOB_PATH, latest_payload(NOW - timedelta(minutes=59)))
        enqueued: list[str] = []
        status, body = request_refresh(blobs, jobs, enqueued.append, now=NOW)
        self.assertEqual(status, 200)
        self.assertEqual(body["outcome"], "fresh")
        self.assertEqual(enqueued, [])
        self.assertEqual(body["nextAllowedAtUtc"], isoformat_utc(NOW + timedelta(minutes=1)))

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
        "level": 20 + index % 2,
        "difficulty": f"S{20 + index % 2}",
        "imageUrl": None,
        "noteCount": 1000,
        "stepArtist": "Test",
    }


def score(index: int) -> dict:
    return {
        "chartId": f"chart-{index:02d}",
        "pumbility": 700 - index,
        "score": 990000 - index,
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
        self.assertNotIn("username", serialized)
        self.assertNotIn("gameTag", serialized)
        self.assertIsNone(blobs.get_json(f"{STAGING_PREFIX}{job['id']}.json"))
        self.assertIsNone(jobs.active_job_id())

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
