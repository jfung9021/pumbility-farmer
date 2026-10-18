"""Start or follow the durable, cached-input tier publication job.

Reads JONATHAN_PASSWORD from the environment (or --env-file), never from command
arguments. A disconnected operator can resume with --job-id.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import requests


def password(env_file: Path | None) -> str:
    value = os.getenv("JONATHAN_PASSWORD", "").strip()
    if value:
        return value
    if env_file:
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            key, separator, raw = line.partition("=")
            if separator and key.strip() == "JONATHAN_PASSWORD":
                raw = raw.strip()
                return json.loads(raw) if raw.startswith('"') else raw.strip("'")
    raise RuntimeError("Set JONATHAN_PASSWORD or provide --env-file.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://pumbility-farmer.vercel.app")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--job-id")
    parser.add_argument("--poll-seconds", type=float, default=10)
    parser.add_argument("--max-minutes", type=float, default=60)
    args = parser.parse_args()
    session = requests.Session()
    session.headers["X-Jonathan-Password"] = password(args.env_file)
    base = args.base_url.rstrip("/")
    job_id = args.job_id
    if not job_id:
        response = session.post(base + "/api/jonathan/refresh?mode=tiers", timeout=60)
        response.raise_for_status()
        body = response.json()
        job = body.get("job", body)
        job_id = job.get("id", body.get("jobId"))
        if not job_id:
            raise RuntimeError("The server did not return a publication job ID.")
    print(json.dumps({"jobId": job_id}), flush=True)
    deadline = time.monotonic() + args.max_minutes * 60
    previous = None
    last_sequence = -1
    while time.monotonic() < deadline:
        response = session.get(base + "/api/jonathan/tier-publication",
                               params={"jobId": job_id}, timeout=60)
        response.raise_for_status()
        body = response.json()
        job = body.get("job", body)
        sequence = int(job.get("progressOrdinal", job.get("sequence", job.get("progress", {}).get("current", 0))))
        if sequence < last_sequence:
            raise RuntimeError("Publication progress moved backwards.")
        last_sequence = sequence
        status = {key: job.get(key) for key in
                  ("id", "status", "stage", "progressOrdinal", "totalStages", "updatedAtUtc", "error", "exceptionClass", "publication")
                  if job.get(key) is not None}
        if status != previous:
            print(json.dumps(status, separators=(",", ":")), flush=True)
            previous = status
        if job.get("status") == "completed":
            return
        if job.get("status") == "failed":
            raise RuntimeError("Publication failed; inspect the reported stage and diagnostics.")
        time.sleep(max(1, min(30, args.poll_seconds)))
    raise RuntimeError("Polling deadline reached; resume with --job-id " + str(job_id))


if __name__ == "__main__":
    main()
