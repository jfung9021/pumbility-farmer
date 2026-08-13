"""Trigger and supervise one production Phoenix 2 full synchronization.

Run only through ``vercel env run -e production`` after the genuine scheduled
cycle and its exact reconciliation pass. The command never prints the job ID,
secret, response payload, or private data.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.reconcile_pumbility_production import _assert_reconciliation_state  # noqa: E402


PRODUCTION_URL = "https://pumbility-farmer.vercel.app"
CONFIRMATION_ENV = "PUMBILITY_FULL_SYNC_CONFIRMATION"
CONFIRMATION = "RUN PUMBILITY PHOENIX2 FULL SYNC"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--timeout-seconds", type=float, default=2400.0)
    return parser


def _json_object(response: requests.Response) -> dict[str, Any]:
    value = response.json()
    if not isinstance(value, Mapping):
        raise RuntimeError("The production API returned an invalid JSON contract.")
    return dict(value)


def run_full_sync(
    *,
    secret: str,
    poll_seconds: float,
    timeout_seconds: float,
    request: Callable[..., requests.Response] = requests.request,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if poll_seconds <= 0 or timeout_seconds <= 0:
        raise ValueError("Polling and timeout durations must be positive.")
    started = monotonic()
    trigger = request(
        "POST",
        f"{PRODUCTION_URL}/api/analyze?mix=phoenix2&fullSync=true",
        headers={"x-analysis-run-secret": secret},
        timeout=30,
    )
    if trigger.status_code != 202:
        raise RuntimeError("The protected full-sync trigger was not accepted.")
    trigger_payload = _json_object(trigger)
    job = trigger_payload.get("job")
    if not isinstance(job, Mapping) or not job.get("id") or not job.get("fullSync"):
        raise RuntimeError("The accepted production job is not the requested full sync.")
    job_id = str(job["id"])

    while monotonic() - started < timeout_seconds:
        status = request(
            "GET",
            f"{PRODUCTION_URL}/api/analyze?mix=phoenix2&jobId={job_id}",
            timeout=30,
        )
        if status.status_code != 200:
            raise RuntimeError("The production full-sync status could not be read.")
        current = _json_object(status)
        state = str(current.get("status") or "")
        if not current.get("fullSync"):
            raise RuntimeError("The production job lost its full-sync contract.")
        if state == "completed":
            return {
                "status": "completed",
                "fullSync": True,
                "durationSeconds": round(monotonic() - started, 3),
            }
        if state == "failed":
            raise RuntimeError("The production full sync failed safely.")
        if state not in {"queued", "running"}:
            raise RuntimeError("The production full sync returned an invalid state.")
        sleep(poll_seconds)
    raise TimeoutError("The production full sync did not complete before the operator timeout.")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.apply:
        raise RuntimeError("The production full sync requires --apply.")
    _assert_reconciliation_state(os.environ)
    if os.getenv(CONFIRMATION_ENV, "").strip() != CONFIRMATION:
        raise RuntimeError(f"Set {CONFIRMATION_ENV} to the documented confirmation phrase.")
    secret = os.getenv("CRON_SECRET", "").strip()
    if len(secret) < 16:
        raise RuntimeError("The protected production trigger secret was not injected.")
    print(run_full_sync(
        secret=secret,
        poll_seconds=args.poll_seconds,
        timeout_seconds=args.timeout_seconds,
    ))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("The production full sync failed safely; private details were suppressed.", file=sys.stderr)
        raise SystemExit(2) from None
