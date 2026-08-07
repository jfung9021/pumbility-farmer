"""Vercel Python Function for running and reading the PIU analysis."""

from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from piu_misgrade_analyzer import (
    AnalysisConfig,
    ApiError,
    PiuScoresClient,
    analyze_snapshot,
    build_web_payload,
    load_snapshot,
    pull_live_snapshot,
)


LATEST_BLOB_PATH = "analysis/latest.json"


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _blob_token() -> str:
    return os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()


def _load_latest_payload() -> dict[str, Any] | None:
    token = _blob_token()
    if not token:
        return None
    from vercel.blob import BlobClient
    from vercel.blob.errors import BlobNotFoundError

    try:
        with BlobClient(token=token) as client:
            result = client.get(LATEST_BLOB_PATH, access="private", use_cache=False)
        payload = json.loads(result.content.decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except BlobNotFoundError:
        return None


def _persist_payload(payload: dict[str, Any]) -> None:
    token = _blob_token()
    if not token:
        return
    from vercel.blob import BlobClient

    body = _json_bytes(payload)
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    with BlobClient(token=token) as client:
        client.put(
            f"analysis/runs/{run_stamp}.json",
            body,
            access="private",
            content_type="application/json",
            add_random_suffix=False,
        )
        client.put(
            LATEST_BLOB_PATH,
            body,
            access="private",
            content_type="application/json",
            overwrite=True,
        )


def _run_analysis() -> dict[str, Any]:
    cooldown_seconds = max(0, int(os.environ.get("ANALYSIS_COOLDOWN_SECONDS", "300")))
    latest = _load_latest_payload() if cooldown_seconds else None
    if latest and latest.get("generatedAtUtc"):
        generated = datetime.fromisoformat(str(latest["generatedAtUtc"]).replace("Z", "+00:00"))
        remaining = cooldown_seconds - (datetime.now(timezone.utc) - generated).total_seconds()
        if remaining > 0:
            raise ApiError(f"Please wait {math.ceil(remaining)} seconds before starting another run.")
    config = AnalysisConfig(
        bootstrap_samples=int(os.environ.get("ANALYSIS_BOOTSTRAP_SAMPLES", "500")),
    )
    raw_dir_setting = os.environ.get("PIU_ANALYSIS_RAW_DIR", "").strip()
    if raw_dir_setting:
        players, charts, scores = load_snapshot(Path(raw_dir_setting))
    else:
        api_key = os.environ.get("PIU_SCORES_API_KEY", "").strip()
        if not api_key:
            raise ApiError(
                "PIU_SCORES_API_KEY is not configured. Set it as a server-side Vercel environment variable."
            )
        client = PiuScoresClient(api_key=api_key)
        with tempfile.TemporaryDirectory(prefix="piu-analysis-") as temp_dir:
            players, charts, scores = pull_live_snapshot(
                client,
                Path(temp_dir) / "raw",
                mix="Phoenix2",
            )

    chart_results, _, summary, _ = analyze_snapshot(players, charts, scores, config)
    payload = build_web_payload(chart_results, summary)
    _persist_payload(payload)
    return payload


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: Any) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            payload = _load_latest_payload()
            if payload is None:
                self._send(404, {"error": "No completed analysis is stored yet."})
                return
            self._send(200, payload)
        except Exception:
            self._send(500, {"error": "The latest analysis could not be read."})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        expected_secret = os.environ.get("ANALYSIS_RUN_SECRET", "").strip()
        supplied_secret = self.headers.get("X-Run-Secret", "").strip()
        if expected_secret and supplied_secret != expected_secret:
            self._send(401, {"error": "A valid analysis run key is required."})
            return
        try:
            self._send(200, _run_analysis())
        except (ApiError, FileNotFoundError, ValueError, RuntimeError) as exc:
            self._send(422, {"error": str(exc)})
        except Exception:
            self._send(500, {"error": "The analysis failed unexpectedly. Check the Vercel function logs."})
