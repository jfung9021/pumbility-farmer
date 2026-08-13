from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from scripts.run_pumbility_full_sync import run_full_sync
from scripts.verify_pumbility_scheduled_cycle import verify_scheduled_job


class _Response:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


class FullSyncOperatorTests(unittest.TestCase):
    def test_full_sync_requires_matching_job_contract_and_completion(self) -> None:
        responses = iter(
            [
                _Response(
                    202,
                    {
                        "outcome": "started",
                        "job": {"id": "private-job", "fullSync": True},
                    },
                ),
                _Response(200, {"status": "running", "fullSync": True}),
                _Response(200, {"status": "completed", "fullSync": True}),
            ]
        )
        request = Mock(side_effect=lambda *_args, **_kwargs: next(responses))
        times = iter([0.0, 1.0, 2.0, 3.0])
        sleep = Mock()

        result = run_full_sync(
            secret="private-secret",
            poll_seconds=1,
            timeout_seconds=30,
            request=request,
            monotonic=lambda: next(times),
            sleep=sleep,
        )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["fullSync"])
        self.assertNotIn("private-job", str(result))
        sleep.assert_called_once_with(1)


class ScheduledCycleEvidenceTests(unittest.TestCase):
    def test_accepts_only_completed_cron_job_after_boundary(self) -> None:
        boundary = datetime(2026, 8, 14, 6, 0, tzinfo=timezone.utc)
        verify_scheduled_job(
            {
                "createdAtUtc": "2026-08-14T06:00:01Z",
                "trigger": "cron",
                "status": "completed",
                "fullSync": False,
                "reanalyzeOnly": False,
                "mix": "phoenix2",
            },
            not_before=boundary,
        )
        with self.assertRaises(RuntimeError):
            verify_scheduled_job(
                {
                    "createdAtUtc": "2026-08-14T05:59:59Z",
                    "trigger": "cron",
                    "status": "completed",
                    "mix": "phoenix2",
                },
                not_before=boundary,
            )


if __name__ == "__main__":
    unittest.main()
