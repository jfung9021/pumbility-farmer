import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tier_diagnostics import event, phase, report_exception


class TierDiagnosticsTests(unittest.TestCase):
    def test_cache_transport_metrics_exclude_private_context(self):
        with self.assertLogs("tier_publication", level="WARNING") as logs:
            record = event("score_profile_cache", cache_reads=2, cache_writes=1,
                           cache_hits=12, cache_misses=3, cache_bypassed=4,
                           cache_failures=1, cache_shards=2,
                           read_duration_ms=1.5, write_duration_ms=2.5,
                           cpu_ms=3.5, goal="ss", source="official", mode="Double",
                           tls_category="eof", exception_class="SSLError",
                           url="private-url", headers="private-token",
                           chart_id="private-chart", payload="private-scores")
        self.assertEqual(record["cache_hits"], 12)
        self.assertEqual(record["tls_category"], "eof")
        self.assertEqual(record["read_duration_ms"], 1.5)
        self.assertEqual(record["cpu_ms"], 3.5)
        self.assertEqual(record["goal"], "ss")
        self.assertNotIn("private", "".join(logs.output))
        for key in ("url", "headers", "chart_id", "payload"):
            self.assertNotIn(key, record)

    def test_http_status_is_recorded_without_response_body_or_url(self):
        error = RuntimeError("private response")
        error.response = SimpleNamespace(status_code=413, text="private data", url="private URL")
        with self.assertLogs("tier_publication", level="WARNING") as logs:
            record = report_exception(error, stage="pin-inputs")
        self.assertEqual(record["status_code"], 413)
        self.assertNotIn("private", "".join(logs.output))

    def test_error_does_not_log_message_source_text_or_unlisted_fields(self):
        secret = "postgres://operator:password@private-host/player-record"
        with self.assertLogs("tier_publication", level="WARNING") as logs:
            try:
                raise RuntimeError(secret)
            except RuntimeError as error:
                record = report_exception(error, stage="archive", job_id="job", payload=secret)
        encoded = "".join(logs.output)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("payload", record)
        self.assertEqual(record["exception_class"], "RuntimeError")
        self.assertEqual(record["stage"], "archive")
        self.assertEqual(set(record["stack"][0]), {"file", "line", "function"})

    def test_phase_records_start_and_failure_without_changing_exception(self):
        error = ValueError("private payload")
        with patch("tier_diagnostics.event") as emit:
            with self.assertRaises(ValueError) as caught:
                with phase("project", job_id="j"):
                    raise error
        self.assertIs(caught.exception, error)
        self.assertEqual([call.args[0] for call in emit.call_args_list],
                         ["tier_stage_started", "tier_publication_failed"])
        self.assertGreaterEqual(emit.call_args.kwargs["duration_ms"], 0)
        self.assertGreaterEqual(emit.call_args.kwargs["cpu_ms"], 0)
