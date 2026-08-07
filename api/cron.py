"""Secured daily Vercel Cron entrypoint using the standard refresh rules."""

from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler
from typing import Any

from api._shared import start_or_reuse_analysis


def cron_authorized(authorization: str, secret: str) -> bool:
    return bool(secret) and hmac.compare_digest(authorization, f"Bearer {secret}")


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

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        secret = os.getenv("CRON_SECRET", "").strip()
        authorization = self.headers.get("Authorization", "")
        if not cron_authorized(authorization, secret):
            self._send(401, {"error": "Unauthorized cron request."})
            return
        try:
            status, payload = start_or_reuse_analysis()
            self._send(status, payload)
        except RuntimeError as exc:
            self._send(503, {"error": str(exc)})
        except Exception:
            self._send(500, {"error": "The scheduled analysis job could not be started."})
