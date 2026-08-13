from __future__ import annotations

import gzip
import io
import json
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from scripts.compare_pumbility_preview_regions import (
    PROJECT_ROOT,
    compare_response_hashes,
    main,
    validate_preview_url,
)


def _handler(payload_value: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            payload = gzip.compress(
                json.dumps({"stable": payload_value}, sort_keys=True).encode("utf-8")
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("x-vercel-cache", "MISS")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


class _Server:
    def __init__(self, payload: str) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(payload))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> "_Server":
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _record(digest: str, *, sample: int = 1) -> dict[str, object]:
    return {
        "ok": True,
        "domain": "analysis",
        "phase": "scored",
        "sampleIndex": sample,
        "responseSha256": digest,
    }


class PreviewRegionComparisonTests(unittest.TestCase):
    def test_job_status_requires_current_job_id_before_probe(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires --job-id"):
            main(
                [
                    "--first-url",
                    "https://first-preview.vercel.app",
                    "--first-label",
                    "iad1",
                    "--second-url",
                    "https://second-preview.vercel.app",
                    "--second-label",
                    "cle1",
                    "--domain",
                    "job-status",
                    "--skip-p99",
                ]
            )

    def test_refuses_production_non_https_and_non_origin_urls(self) -> None:
        rejected = (
            "https://pumbility-farmer.vercel.app",
            "http://preview.example.test",
            "https://preview.example.test/api/analyze",
            "https://user:secret@preview.example.test",
        )
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_preview_url(value)

    def test_accepts_https_preview_and_http_loopback_only(self) -> None:
        self.assertEqual(
            validate_preview_url("https://preview-branch.vercel.app/"),
            "https://preview-branch.vercel.app",
        )
        self.assertEqual(
            validate_preview_url("http://127.0.0.1:3210"),
            "http://127.0.0.1:3210",
        )

    def test_exact_hash_comparison_never_returns_hash_values(self) -> None:
        digest = "a" * 64
        result = compare_response_hashes([_record(digest)], [_record(digest)])
        self.assertTrue(result["passed"])
        self.assertNotIn(digest, json.dumps(result))

        mismatch = compare_response_hashes(
            [_record(digest)], [_record("b" * 64)]
        )
        self.assertFalse(mismatch["passed"])
        self.assertEqual(mismatch["domains"]["analysis"]["mismatches"], 1)

    def test_local_smoke_compares_identical_probes_without_disclosing_endpoints(self) -> None:
        local_data = PROJECT_ROOT / ".local-data"
        local_data.mkdir(exist_ok=True)
        with (
            _Server("same-body") as first,
            _Server("same-body") as second,
            tempfile.TemporaryDirectory(dir=local_data) as output_root,
        ):
            captured = io.StringIO()
            with redirect_stdout(captured):
                result = main(
                    [
                        "--first-url",
                        first.url,
                        "--first-label",
                        "iad1",
                        "--second-url",
                        second.url,
                        "--second-label",
                        "cle1",
                        "--domain",
                        "analysis",
                        "--samples",
                        "1",
                        "--warmup-samples",
                        "0",
                        "--window-minutes",
                        "0",
                        "--skip-p99",
                        "--output-root",
                        output_root,
                    ]
                )

            self.assertEqual(result, 0)
            report = json.loads(captured.getvalue())
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["adoptionDecision"], "pending")
            self.assertEqual(
                report["requiredBeforeRegionAdoption"],
                [
                    "worker",
                    "blob",
                    "cron",
                    "queue",
                    "cold-start",
                    "connection-capacity",
                    "failure-and-rollback",
                ],
            )
            self.assertTrue(report["responseParity"]["passed"])
            labels = {
                deployment["deploymentRegionLabel"]
                for deployment in report["deployments"]
            }
            self.assertEqual(labels, {"iad1", "cle1"})

            stdout = captured.getvalue()
            self.assertNotIn(first.url, stdout)
            self.assertNotIn(second.url, stdout)
            self.assertNotIn("127.0.0.1", stdout)
            self.assertNotIn("same-body", stdout)
            self.assertNotRegex(stdout, r"[0-9a-f]{64}")

            reports = list(Path(output_root).glob("*/comparison.json"))
            self.assertEqual(len(reports), 1)
            persisted = reports[0].read_text(encoding="utf-8")
            self.assertNotIn("127.0.0.1", persisted)
            probe_summaries = list(Path(output_root).glob("*/*/*-summary.json"))
            self.assertEqual(len(probe_summaries), 2)
            for summary_path in probe_summaries:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                self.assertIsNone(summary["baseHost"])

    def test_evidence_output_is_restricted_to_local_data(self) -> None:
        outside = PROJECT_ROOT.parent / "outside-preview-evidence"
        with self.assertRaisesRegex(ValueError, "under .local-data"):
            main(
                [
                    "--first-url",
                    "https://first-preview.vercel.app",
                    "--first-label",
                    "iad1",
                    "--second-url",
                    "https://second-preview.vercel.app",
                    "--second-label",
                    "cle1",
                    "--domain",
                    "analysis",
                    "--skip-p99",
                    "--output-root",
                    str(outside),
                ]
            )


if __name__ == "__main__":
    unittest.main()
