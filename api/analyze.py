"""JSON-only Vercel API for latest analysis data and async job status."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlsplit

from analysis_runtime import LATEST_BLOB_PATH, PrivateBlobStore, RuntimeJobStore
from api._shared import start_or_reuse_analysis


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


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
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            query = parse_qs(urlsplit(self.path).query)
            job_id = str((query.get("jobId") or [""])[0]).strip()
            if job_id:
                job = RuntimeJobStore().get(job_id)
                if job is None:
                    self._send(404, {"error": "Analysis job not found or its status has expired."})
                    return
                self._send(200, job)
                return
            payload = PrivateBlobStore().get_json(LATEST_BLOB_PATH)
            if payload is None:
                self._send(404, {"error": "No completed analysis is stored yet."})
                return
            self._send(200, payload)
        except (RuntimeError, ValueError, json.JSONDecodeError):
            self._send(503, {"error": "The latest analysis service is temporarily unavailable."})
        except Exception:
            self._send(500, {"error": "The latest analysis could not be read."})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            status, payload = start_or_reuse_analysis()
            self._send(status, payload)
        except RuntimeError as exc:
            self._send(503, {"error": str(exc)})
        except Exception:
            self._send(500, {"error": "The analysis job could not be started."})
