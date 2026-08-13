"""Verify one genuine Vercel Cron cycle before post-run reconciliation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis_runtime import VercelRuntimeJobStore  # noqa: E402
from phoenix2_sync import parse_utc  # noqa: E402
from scripts.reconcile_pumbility_production import (  # noqa: E402
    _assert_reconciliation_state,
    main as reconcile_production,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--not-before",
        required=True,
        help="Inclusive scheduled boundary in ISO-8601 UTC form.",
    )
    return parser


def verify_scheduled_job(job: Mapping[str, object], *, not_before: datetime) -> None:
    created = parse_utc(job.get("createdAtUtc"))
    if created is None or created < not_before.astimezone(timezone.utc):
        raise RuntimeError("The latest job predates the required scheduled boundary.")
    if job.get("trigger") != "cron" or job.get("status") != "completed":
        raise RuntimeError("The required genuine scheduled job has not completed.")
    if job.get("fullSync") or job.get("reanalyzeOnly"):
        raise RuntimeError("The scheduled evidence job has an unexpected execution mode.")
    if str(job.get("mix") or "").casefold() != "phoenix2":
        raise RuntimeError("The scheduled evidence job is not Phoenix 2.")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _assert_reconciliation_state(os.environ)
    not_before = parse_utc(args.not_before)
    if not_before is None:
        raise ValueError("--not-before must be an ISO-8601 timestamp.")
    jobs = VercelRuntimeJobStore()
    job_id = jobs.latest_job_id("phoenix2")
    job = jobs.get(job_id) if job_id else None
    if not isinstance(job, Mapping):
        raise RuntimeError("No scheduled Phoenix 2 job evidence is available.")
    verify_scheduled_job(job, not_before=not_before)
    print(json.dumps({"status": "scheduled-cycle-completed", "privateIdsPrinted": False}))
    if reconcile_production() != 0:
        raise RuntimeError("Post-scheduled-cycle reconciliation did not pass.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Scheduled-cycle verification failed safely; private details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
