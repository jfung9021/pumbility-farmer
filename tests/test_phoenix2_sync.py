import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

from phoenix2_sync import (
    MIX,
    analyzer_input,
    merge_best_scores,
    synchronize_phoenix2_snapshot,
)
from piu_misgrade_analyzer import PiuScoresClient, SharedRequestLimiter


FIXED_NOW = datetime(2026, 8, 7, 6, 0, tzinfo=timezone.utc)


def chart(chart_id: str, chart_type: str = "Single", level: int = 10) -> dict:
    prefix = "S" if chart_type == "Single" else "D"
    return {
        "id": chart_id,
        "songName": chart_id,
        "type": chart_type,
        "level": level,
        "difficulty": f"{prefix}{level}",
        "imageUrl": None,
        "noteCount": 100,
        "stepArtist": "Test",
        "username": "must-not-be-cached",
    }


def score(player_id: str, chart_id: str, pumbility: float, raw_score: int = 950000) -> dict:
    return {
        "playerId": player_id,
        "chartId": chart_id,
        "pumbility": pumbility,
        "score": raw_score,
        "recordedAt": "2026-08-07T05:00:00Z",
        "isBroken": False,
        "letterGrade": "SS",
    }


class FakeClient:
    def __init__(self, players: list[str], charts: list[dict], scores: dict[str, list[dict]]) -> None:
        self.players = players
        self.charts = charts
        self.scores = scores
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def fetch_page_collection(self, path: str, params=None):
        with self._lock:
            self.calls.append((path, dict(params or {})))
        if path == "api/v2/players":
            return [{"userId": player_id, "username": "private"} for player_id in self.players]
        if path == "api/v2/charts":
            return self.charts
        player_id = path.split("/")[3]
        value = self.scores.get(player_id, [])
        if isinstance(value, Exception):
            raise value
        return value


class Phoenix2SyncTests(unittest.TestCase):
    def test_phoenix2_only_requests_and_empty_player_filter(self) -> None:
        client = FakeClient(["has-scores", "empty"], [chart("low-level")], {
            "has-scores": [score("has-scores", "low-level", 500)],
            "empty": [],
        })
        snapshot, _ = synchronize_phoenix2_snapshot(
            client, None, job_id="job", now=lambda: FIXED_NOW
        )
        score_calls = [params for path, params in client.calls if path.endswith("/scores")]
        self.assertEqual(len(score_calls), 2)
        self.assertTrue(all(params["mix"] == MIX for params in score_calls))
        self.assertTrue(all("minLevel" not in params for params in score_calls))
        self.assertNotIn("username", snapshot["players"][0])
        self.assertNotIn("username", snapshot["charts"][0])
        players, _, _ = analyzer_input(snapshot, eligible_only=False)
        self.assertEqual(players, [{"userId": "has-scores"}])

    def test_eligibility_uses_complete_mode_history(self) -> None:
        charts = [chart(f"s-{index}", "Single", 5 + index % 25) for index in range(30)]
        charts += [chart(f"d-{index}", "Double", 8) for index in range(29)]
        rows = [score("eligible", row["id"], 600 - index) for index, row in enumerate(charts[:30])]
        rows += [score("ineligible", row["id"], 500 - index) for index, row in enumerate(charts[30:])]
        snapshot = {
            "players": [{"playerId": "eligible"}, {"playerId": "ineligible"}],
            "charts": charts,
            "scores": rows,
        }
        players, _, filtered = analyzer_input(snapshot)
        self.assertEqual(players, [{"userId": "eligible"}])
        self.assertEqual(len(filtered), 30)
        self.assertTrue(any(row["level"] < 20 for row in charts))

    def test_incremental_merge_consent_pruning_and_recent_empty_skip(self) -> None:
        current = {
            "players": [
                {"playerId": "known", "lastSyncedAtUtc": "2026-08-07T04:00:00Z"},
                {"playerId": "empty", "lastSyncedAtUtc": "2026-08-07T05:00:00Z"},
                {"playerId": "revoked", "lastSyncedAtUtc": "2026-08-07T04:00:00Z"},
            ],
            "charts": [chart("a"), chart("b")],
            "scores": [score("known", "a", 600), score("revoked", "a", 700)],
        }
        client = FakeClient(["known", "empty", "new"], [chart("a"), chart("b")], {
            "known": [score("known", "a", 590), score("known", "b", 610)],
            "new": [score("new", "a", 550)],
        })
        snapshot, _ = synchronize_phoenix2_snapshot(
            client, current, job_id="job", now=lambda: FIXED_NOW
        )
        by_key = {(row["playerId"], row["chartId"]): row for row in snapshot["scores"]}
        self.assertEqual(by_key[("known", "a")]["pumbility"], 600)
        self.assertEqual(by_key[("known", "b")]["pumbility"], 610)
        self.assertNotIn(("revoked", "a"), by_key)
        paths = {path: params for path, params in client.calls}
        self.assertEqual(paths["api/v2/players/known/scores"]["recordedAfter"], "2026-08-07T04:00:00Z")
        self.assertNotIn("recordedAfter", paths["api/v2/players/new/scores"])
        self.assertNotIn("api/v2/players/empty/scores", paths)

    def test_stale_empty_player_is_fully_rechecked(self) -> None:
        current = {
            "players": [{"playerId": "empty", "lastSyncedAtUtc": "2026-08-06T05:59:59Z"}],
            "charts": [chart("a")],
            "scores": [],
        }
        client = FakeClient(["empty"], [chart("a")], {"empty": []})
        synchronize_phoenix2_snapshot(client, current, job_id="job", now=lambda: FIXED_NOW)
        params = next(params for path, params in client.calls if path.endswith("/scores"))
        self.assertNotIn("recordedAfter", params)

    def test_checkpoint_resume_skips_completed_players(self) -> None:
        checkpoints: list[dict] = []
        first = FakeClient(["p1", "p2", "p3"], [chart("a")], {
            "p1": [score("p1", "a", 501)],
            "p2": [score("p2", "a", 502)],
            "p3": RuntimeError("stop"),
        })
        with self.assertRaises(RuntimeError):
            synchronize_phoenix2_snapshot(
                first,
                None,
                job_id="job",
                workers=1,
                checkpoint_every=1,
                checkpoint=checkpoints.append,
                now=lambda: FIXED_NOW,
            )
        self.assertGreaterEqual(len(checkpoints), 2)
        resume = checkpoints[-1]
        second = FakeClient(["p1", "p2", "p3"], [chart("a")], {
            "p3": [score("p3", "a", 503)],
        })
        snapshot, _ = synchronize_phoenix2_snapshot(
            second,
            None,
            job_id="job",
            resume_staging=resume,
            workers=1,
            checkpoint_every=1,
            now=lambda: FIXED_NOW,
        )
        fetched = [path for path, _ in second.calls if path.endswith("/scores")]
        self.assertEqual(fetched, ["api/v2/players/p3/scores"])
        self.assertEqual(len(snapshot["scores"]), 3)

    def test_invalid_increment_never_replaces_valid_best(self) -> None:
        existing = [score("p", "a", 600)]
        broken = score("p", "a", 900)
        broken["isBroken"] = True
        merged = merge_best_scores(existing, [broken, score("p", "a", 590)])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["pumbility"], 600)

    def test_snapshot_output_is_deterministic(self) -> None:
        charts = [chart("b"), chart("a")]
        scores = {
            "p1": [score("p1", "b", 501), score("p1", "a", 502)],
            "p2": [score("p2", "a", 503)],
        }
        first, _ = synchronize_phoenix2_snapshot(
            FakeClient(["p2", "p1"], charts, scores),
            None,
            job_id="one",
            now=lambda: FIXED_NOW,
        )
        second, _ = synchronize_phoenix2_snapshot(
            FakeClient(["p1", "p2"], list(reversed(charts)), scores),
            None,
            job_id="two",
            now=lambda: FIXED_NOW,
        )
        self.assertEqual(first, second)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class _Response:
    def __init__(self, status: int, payload: dict, headers=None) -> None:
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses

    def get(self, *args, **kwargs):
        return self.responses.pop(0)


class RequestLimiterTests(unittest.TestCase):
    def test_request_starts_are_globally_spaced(self) -> None:
        clock = _FakeClock()
        limiter = SharedRequestLimiter(0.125, monotonic=clock.monotonic, sleeper=clock.sleep)
        limiter.wait()
        limiter.wait()
        self.assertGreaterEqual(clock.value, 0.125)

    def test_retry_after_blocks_the_shared_limiter(self) -> None:
        clock = _FakeClock()
        limiter = SharedRequestLimiter(0, monotonic=clock.monotonic, sleeper=clock.sleep)
        client = PiuScoresClient(
            "piu_scores_live_" + "a" * 64,
            max_retries=1,
            limiter=limiter,
        )
        client.session = _Session([
            _Response(429, {}, {"Retry-After": "3"}),
            _Response(200, {"data": [], "next": None}),
        ])
        payload = client._get_json("api/v2/players")
        self.assertEqual(payload["data"], [])
        self.assertGreaterEqual(clock.value, 3)


class BenchmarkClient(FakeClient):
    def __init__(self, players: list[str], charts: list[dict], scores: dict[str, list[dict]]) -> None:
        super().__init__(players, charts, scores)
        self.active = 0
        self.max_active = 0
        self.simulated_429s = 0

    def fetch_page_collection(self, path: str, params=None):
        if not path.endswith("/scores"):
            return super().fetch_page_collection(path, params)
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.001)
            player_id = path.split("/")[3]
            if int(player_id[1:]) % 97 == 0:
                self.simulated_429s += 1
                time.sleep(0.001)
            return super().fetch_page_collection(path, params)
        finally:
            with self._lock:
                self.active -= 1


class IntegrationBenchmarkTests(unittest.TestCase):
    def test_mocked_809_player_sync_is_bounded_and_below_worker_limit(self) -> None:
        players = [f"p{index:03d}" for index in range(809)]
        charts = [chart("a")]
        scores = {player_id: [score(player_id, "a", 500)] for player_id in players}
        client = BenchmarkClient(players, charts, scores)
        started = time.perf_counter()
        snapshot, _ = synchronize_phoenix2_snapshot(
            client, None, job_id="benchmark", workers=6, now=lambda: FIXED_NOW
        )
        elapsed = time.perf_counter() - started
        self.assertEqual(len(snapshot["players"]), 809)
        self.assertLessEqual(client.max_active, 6)
        self.assertGreater(client.simulated_429s, 0)
        self.assertLess(elapsed, 180)


if __name__ == "__main__":
    unittest.main()
