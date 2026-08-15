from __future__ import annotations

import gzip
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "probe_pumbility_read_domains.ps1"


class _CompressedJsonHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        payload = gzip.compress(json.dumps({"ok": True}).encode("utf-8"))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("x-vercel-cache", "MISS")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


class PumbilityLatencyProbeTests(unittest.TestCase):
    def test_job_status_requires_a_current_job_id_before_network(self) -> None:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-File",
                str(PROBE),
                "-Domains",
                "job-status",
                "-Samples",
                "1",
                "-SkipP99",
                "-BaseUrl",
                "https://invalid.example.test",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("requires -JobId", completed.stderr)

    def test_rejects_p99_scoring_below_one_hundred_samples_before_network(self) -> None:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-File",
                str(PROBE),
                "-Domains",
                "analysis",
                "-Samples",
                "99",
                "-BaseUrl",
                "https://invalid.example.test",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("P99 scoring requires at least 100", completed.stderr)

    def test_smoke_run_records_separate_sanitized_compressed_timings(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _CompressedJsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as output_directory:
                completed = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-File",
                        str(PROBE),
                        "-Domains",
                        "analysis",
                        "-Samples",
                        "1",
                        "-WarmupSamples",
                        "0",
                        "-WindowMinutes",
                        "0",
                        "-SkipP99",
                        "-ExpectCanaryTelemetry",
                        "-BaseUrl",
                        f"http://127.0.0.1:{server.server_port}",
                        "-OutputDirectory",
                        output_directory,
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                summaries = list(Path(output_directory).glob("*-summary.json"))
                samples = list(Path(output_directory).glob("*-samples.jsonl"))
                self.assertEqual(len(summaries), 1)
                self.assertEqual(len(samples), 1)

                summary = json.loads(summaries[0].read_text(encoding="utf-8"))
                raw = json.loads(samples[0].read_text(encoding="utf-8"))
                self.assertTrue(summary["compressionRequested"])
                self.assertTrue(summary["telemetry"]["expected"])
                self.assertFalse(summary["telemetry"]["countGateComplete"])
                self.assertEqual(
                    summary["results"]["analysis"]["expectedCandidateReadEvents"],
                    1,
                )
                self.assertTrue(raw["ok"])
                self.assertEqual(raw["contentEncoding"], "gzip")
                self.assertEqual(raw["vercelCache"], "MISS")
                self.assertIn("ttfbMs", raw)
                self.assertIn("downloadMs", raw)
                self.assertIn("jsonParseMs", raw)
                self.assertIn("endToEndMs", raw)
                self.assertRegex(raw["responseSha256"], r"^[0-9a-f]{64}$")
                self.assertEqual(
                    raw["responseSha256"],
                    hashlib.sha256(json.dumps({"ok": True}).encode("utf-8")).hexdigest(),
                )

                evidence_text = samples[0].read_text(encoding="utf-8")
                self.assertNotIn("probeNonce", evidence_text)
                self.assertNotIn("/api/", evidence_text)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
