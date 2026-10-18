import json
import unittest
from unittest.mock import patch

from tier_diagnostics import event, phase, report_exception


class TierDiagnosticsTests(unittest.TestCase):
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
