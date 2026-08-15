from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.compare_pumbility_protected_previews import (
    METRIC_MARKER,
    PROJECT_ROOT,
    ProtectedPreviewProbeError,
    _deployment_reference_from_environment,
    _windows_safe_vercel_command,
    main,
    run_comparison,
)


class FakeVercelRunner:
    def __init__(
        self,
        *,
        bodies: dict[str, bytes] | None = None,
        metrics: bytes | None = None,
        cache_status: bytes = b"MISS",
        pre_response_failures: int = 0,
    ) -> None:
        self.bodies = bodies or {}
        self.metrics = metrics or (
            METRIC_MARKER + b"200\t0.100\t0.125\t321\tapplication/json"
        )
        self.cache_status = cache_status
        self.pre_response_failures = pre_response_failures
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_kwargs: object):
        self.commands.append(command)
        if command[1] == "inspect":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=b"Environment Preview\nRegions iad1 cle1\n",
                stderr=b"",
            )
        if command[1] != "curl":
            raise AssertionError("Unexpected command")
        if self.pre_response_failures:
            self.pre_response_failures -= 1
            return subprocess.CompletedProcess(
                command,
                28,
                stdout=(
                    METRIC_MARKER
                    + b"000\t0.000\t0.010\t0\tapplication/octet-stream"
                ),
                stderr=b"PRIVATE_TRANSPORT_ERROR",
            )
        deployment = command[command.index("--deployment") + 1]
        body_path = Path(command[command.index("--output") + 1])
        header_path = Path(command[command.index("--dump-header") + 1])
        body_path.write_bytes(
            self.bodies.get(
                deployment,
                b'{"stable":"PRIVATE_BODY_SENTINEL","values":[1,2,3]}',
            )
        )
        header_path.write_bytes(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Encoding: gzip\r\n"
            b"x-vercel-cache: " + self.cache_status + b"\r\n\r\n"
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=self.metrics,
            stderr=b"PRIVATE_COMMAND_ERROR",
        )


class ProtectedPreviewComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.local_data = PROJECT_ROOT / ".local-data"
        self.local_data.mkdir(exist_ok=True)

    def test_direct_script_entrypoint_resolves_repository_imports(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "compare_pumbility_protected_previews.py"),
                "--help",
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8"))

    @unittest.skipUnless(os.name == "nt", "Windows npm shim regression")
    def test_windows_cli_bypasses_cmd_shim_for_ampersand_query(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.local_data) as temporary:
            root = Path(temporary)
            shim = root / "vercel.cmd"
            entrypoint = root / "node_modules" / "vercel" / "dist" / "vc.js"
            node = root / "node.exe"
            entrypoint.parent.mkdir(parents=True)
            shim.write_text("@echo off\n", encoding="utf-8")
            entrypoint.write_text("", encoding="utf-8")
            node.write_bytes(b"")
            query = "/api/analyze?mix=phoenix2&probeNonce=exact"

            command = _windows_safe_vercel_command(
                [str(shim), "curl", query, "--deployment", "preview-reference"]
            )

        self.assertEqual(command[:2], [str(node), str(entrypoint)])
        self.assertEqual(command[3], query)

    def _run_smoke(
        self, output_root: Path, runner: FakeVercelRunner
    ) -> tuple[int, dict[str, object], Path]:
        return run_comparison(
            first_deployment="preview-control-reference",
            first_label="control",
            second_deployment="preview-candidate-reference",
            second_label="candidate",
            domains=("analysis", "tier-list"),
            scored_samples=1,
            warmup_samples=0,
            window_minutes=0,
            skip_p99=True,
            expect_canary_telemetry=True,
            job_id="",
            output_root=output_root,
            vercel_cli="vercel",
            command_runner=runner,
        )

    def test_smoke_retains_timings_and_counts_without_private_evidence(self) -> None:
        runner = FakeVercelRunner()
        with tempfile.TemporaryDirectory(dir=self.local_data) as output_root:
            status, report, run_directory = self._run_smoke(
                Path(output_root), runner
            )

            self.assertEqual(status, 0)
            self.assertEqual(report["status"], "smoke-passed")
            self.assertTrue(report["responseParity"]["passed"])
            self.assertFalse(report["telemetryCountGateComplete"])
            for deployment in report["deployments"]:
                analysis = deployment["results"]["analysis"]
                self.assertEqual(analysis["expectedCandidateReadEvents"], 1)
                self.assertEqual(analysis["gzipResponses"], 1)
                self.assertEqual(analysis["cacheHits"], 0)
                self.assertEqual(analysis["ttfbMs"]["p50"], 100.0)
                self.assertEqual(analysis["downloadMs"]["p50"], 25.0)
                self.assertIsNotNone(analysis["jsonParseMs"]["p50"])
                self.assertIsNotNone(analysis["endToEndMs"]["p50"])

            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in run_directory.iterdir()
            )
            digest = hashlib.sha256(
                b'{"stable":"PRIVATE_BODY_SENTINEL","values":[1,2,3]}'
            ).hexdigest()
            for forbidden in (
                "preview-control-reference",
                "preview-candidate-reference",
                "PRIVATE_BODY_SENTINEL",
                "PRIVATE_COMMAND_ERROR",
                digest,
                "/api/",
                "probeNonce",
                "https://",
            ):
                self.assertNotIn(forbidden, persisted)
            sample_records = [
                json.loads(line)
                for line in (run_directory / "samples.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(sample_records), 4)
            self.assertTrue(all(record["exactPairMatch"] for record in sample_records))
            self.assertTrue(all("responseSha256" not in record for record in sample_records))

    def test_vercel_curl_command_requests_required_timing_and_cache_controls(self) -> None:
        runner = FakeVercelRunner()
        with tempfile.TemporaryDirectory(dir=self.local_data) as output_root:
            self._run_smoke(Path(output_root), runner)

        curl_commands = [command for command in runner.commands if command[1] == "curl"]
        self.assertEqual(len(curl_commands), 4)
        for command in curl_commands:
            self.assertIn("--deployment", command)
            self.assertIn("--compressed", command)
            self.assertIn("Cache-Control: no-cache, no-store, max-age=0", command)
            self.assertIn("Pragma: no-cache", command)
            metric_format = command[command.index("--write-out") + 1]
            self.assertIn("%{time_starttransfer}", metric_format)
            self.assertIn("%{time_total}", metric_format)
            self.assertIn("%{size_download}", metric_format)
            self.assertFalse(any("bypass" in value.casefold() for value in command))

    def test_missing_timing_decomposition_fails_closed(self) -> None:
        runner = FakeVercelRunner(metrics=b"no-curl-metrics")
        with (
            tempfile.TemporaryDirectory(dir=self.local_data) as output_root,
            self.assertRaisesRegex(
                ProtectedPreviewProbeError, "timing evidence"
            ),
        ):
            self._run_smoke(Path(output_root), runner)

        curl_commands = [command for command in runner.commands if command[1] == "curl"]
        self.assertEqual(len(curl_commands), 1)

    def test_one_pre_response_transport_failure_retries_and_is_audited(self) -> None:
        runner = FakeVercelRunner(pre_response_failures=1)
        with tempfile.TemporaryDirectory(dir=self.local_data) as output_root:
            status, report, run_directory = self._run_smoke(
                Path(output_root), runner
            )
            records = [
                json.loads(line)
                for line in (run_directory / "samples.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            persisted = (run_directory / "samples.jsonl").read_text(
                encoding="utf-8"
            )

        self.assertEqual(status, 0)
        self.assertEqual(
            report["probeConfiguration"][
                "maxPreResponseTransportRetriesPerRequest"
            ],
            1,
        )
        self.assertFalse(
            report["identityDisclosure"]["rawTransportErrorsPrintedOrStored"]
        )
        analysis = report["deployments"][0]["results"]["analysis"]
        self.assertEqual(analysis["scoredAttempts"], 2)
        self.assertEqual(analysis["scoredSuccesses"], 1)
        self.assertEqual(analysis["scoredErrors"], 0)
        self.assertEqual(analysis["scoredTransportFailures"], 1)
        self.assertEqual(analysis["scoredTransportRetries"], 1)
        self.assertEqual(analysis["expectedCandidateReadEvents"], 2)
        self.assertEqual(len(records), 5)
        first_logical_attempts = records[:2]
        self.assertEqual(
            [record["attemptIndex"] for record in first_logical_attempts], [1, 2]
        )
        self.assertEqual(
            [record["transportOutcome"] for record in first_logical_attempts],
            ["no-http-response", "http-response"],
        )
        self.assertEqual(
            [record["retryScheduled"] for record in first_logical_attempts],
            [True, False],
        )
        self.assertNotIn("PRIVATE_TRANSPORT_ERROR", persisted)
        self.assertNotIn("preview-control-reference", persisted)

    def test_exhausted_transport_retry_fails_with_sanitized_evidence(self) -> None:
        runner = FakeVercelRunner(pre_response_failures=2)
        with tempfile.TemporaryDirectory(dir=self.local_data) as output_root:
            root = Path(output_root)
            with self.assertRaisesRegex(
                ProtectedPreviewProbeError, "exhausted its bounded transport retry"
            ):
                self._run_smoke(root, runner)
            run_directories = list(root.iterdir())
            self.assertEqual(len(run_directories), 1)
            records = [
                json.loads(line)
                for line in (run_directories[0] / "samples.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            failure = json.loads(
                (run_directories[0] / "failure.json").read_text(encoding="utf-8")
            )
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in run_directories[0].iterdir()
            )

        self.assertEqual(len(records), 2)
        self.assertEqual([record["attemptIndex"] for record in records], [1, 2])
        self.assertEqual(
            [record["retryScheduled"] for record in records], [True, False]
        )
        self.assertTrue(
            all(record["transportOutcome"] == "no-http-response" for record in records)
        )
        self.assertEqual(
            failure["failureKind"], "pre-response-transport-retry-exhausted"
        )
        self.assertEqual(
            failure["attemptCounts"],
            {
                "total": 2,
                "successfulResponses": 0,
                "preResponseTransportFailures": 2,
                "retriesScheduled": 1,
                "terminalFailures": 1,
            },
        )
        self.assertNotIn("PRIVATE_TRANSPORT_ERROR", persisted)
        self.assertNotIn("preview-control-reference", persisted)

    def test_http_failure_is_not_retried(self) -> None:
        runner = FakeVercelRunner(
            metrics=METRIC_MARKER + b"503\t0.100\t0.125\t321\tapplication/json"
        )
        with (
            tempfile.TemporaryDirectory(dir=self.local_data) as output_root,
            self.assertRaisesRegex(ProtectedPreviewProbeError, "unsuccessful status"),
        ):
            self._run_smoke(Path(output_root), runner)

        curl_commands = [command for command in runner.commands if command[1] == "curl"]
        self.assertEqual(len(curl_commands), 1)

    def test_any_cache_hit_fails_the_comparison(self) -> None:
        runner = FakeVercelRunner(cache_status=b"HIT")
        with tempfile.TemporaryDirectory(dir=self.local_data) as output_root:
            status, report, _ = self._run_smoke(Path(output_root), runner)

        self.assertEqual(status, 4)
        self.assertFalse(report["cacheBypassGatePassed"])

    def test_exact_body_mismatch_is_sanitized_and_fails(self) -> None:
        runner = FakeVercelRunner(
            bodies={
                "preview-control-reference": b'{"value":"CONTROL_PRIVATE"}',
                "preview-candidate-reference": b'{"value":"CANDIDATE_PRIVATE"}',
            }
        )
        with tempfile.TemporaryDirectory(dir=self.local_data) as output_root:
            status, report, run_directory = self._run_smoke(
                Path(output_root), runner
            )

            self.assertEqual(status, 4)
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["responseParity"]["passed"])
            persisted = (run_directory / "comparison.json").read_text(encoding="utf-8")
            self.assertNotIn("CONTROL_PRIVATE", persisted)
            self.assertNotIn("CANDIDATE_PRIVATE", persisted)
            self.assertNotRegex(persisted, r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")

    def test_default_gate_counts_three_warmups_and_one_hundred_samples(self) -> None:
        runner = FakeVercelRunner()
        with tempfile.TemporaryDirectory(dir=self.local_data) as output_root:
            status, report, _ = run_comparison(
                first_deployment="preview-control-reference",
                first_label="control",
                second_deployment="preview-candidate-reference",
                second_label="candidate",
                domains=("analysis",),
                scored_samples=100,
                warmup_samples=3,
                window_minutes=0,
                skip_p99=False,
                expect_canary_telemetry=True,
                job_id="",
                output_root=Path(output_root),
                vercel_cli="vercel",
                command_runner=runner,
            )

        self.assertEqual(status, 0)
        for deployment in report["deployments"]:
            result = deployment["results"]["analysis"]
            self.assertEqual(result["scoredAttempts"], 100)
            self.assertEqual(result["warmupAttempts"], 3)
            self.assertEqual(result["expectedCandidateReadEvents"], 103)
            self.assertTrue(result["p99Scored"])

    def test_environment_reference_validation_never_echoes_value(self) -> None:
        private_value = "https://PRIVATE_DEPLOYMENT_REFERENCE/api"
        with (
            patch.dict(os.environ, {"PREVIEW_REFERENCE": private_value}),
            self.assertRaises(ValueError) as captured,
        ):
            _deployment_reference_from_environment(
                "PREVIEW_REFERENCE", ordinal="first"
            )

        self.assertNotIn(private_value, str(captured.exception))

    def test_main_reads_references_from_environment_without_reporting_them(self) -> None:
        runner = FakeVercelRunner()
        environment = {
            "FIRST_PREVIEW": "preview-control-reference",
            "SECOND_PREVIEW": "preview-candidate-reference",
        }
        with (
            tempfile.TemporaryDirectory(dir=self.local_data) as output_root,
            patch.dict(os.environ, environment, clear=False),
            patch(
                "scripts.compare_pumbility_protected_previews.shutil.which",
                return_value="vercel",
            ),
            patch(
                "scripts.compare_pumbility_protected_previews.subprocess.run",
                side_effect=runner,
            ),
        ):
            captured = io.StringIO()
            with redirect_stdout(captured):
                status = main(
                    [
                        "--first-deployment-env",
                        "FIRST_PREVIEW",
                        "--first-label",
                        "control",
                        "--second-deployment-env",
                        "SECOND_PREVIEW",
                        "--second-label",
                        "candidate",
                        "--domain",
                        "analysis",
                        "--samples",
                        "1",
                        "--warmup-samples",
                        "0",
                        "--window-minutes",
                        "0",
                        "--skip-p99",
                        "--expect-canary-telemetry",
                        "--output-root",
                        output_root,
                    ]
                )

        self.assertEqual(status, 0)
        output = captured.getvalue()
        self.assertNotIn(environment["FIRST_PREVIEW"], output)
        self.assertNotIn(environment["SECOND_PREVIEW"], output)
        self.assertNotIn("PRIVATE_COMMAND_ERROR", output)
        self.assertNotRegex(output, r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")

    def test_output_is_restricted_to_ignored_local_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "under .local-data"):
            run_comparison(
                first_deployment="preview-control-reference",
                first_label="control",
                second_deployment="preview-candidate-reference",
                second_label="candidate",
                domains=("analysis",),
                scored_samples=1,
                warmup_samples=0,
                window_minutes=0,
                skip_p99=True,
                expect_canary_telemetry=True,
                job_id="",
                output_root=PROJECT_ROOT.parent / "outside-protected-evidence",
                vercel_cli="vercel",
                command_runner=FakeVercelRunner(),
            )


if __name__ == "__main__":
    unittest.main()
