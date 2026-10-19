from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from api.refresh_status import get_refresh_status, read_refresh_status
from pumbility_contract import combined_tier_blob_path, recommendation_blob_path
from pumbility_store import EXPECTED_PUMBILITY_MIGRATION


class RefreshStatusTests(unittest.TestCase):
    def read_row(self, row):
        cursor = MagicMock()
        cursor.fetchone.return_value = row
        context = MagicMock()
        context.__enter__.return_value = cursor
        with patch.dict("os.environ", {"PUMBILITY_DATABASE_URL": "postgresql://localhost/test"}), \
             patch("api.refresh_status._read_cursor", return_value=context), \
             patch("api.refresh_status.utc_now", return_value=datetime(2026, 10, 19, 6, 30, tzinfo=timezone.utc)):
            result = read_refresh_status()
        return result, cursor

    def test_failed_refresh_reports_old_publications_and_safe_failure_code(self):
        result, cursor = self.read_row((EXPECTED_PUMBILITY_MIGRATION,
            "2026-10-18T02:11:43+00:00", "2026-10-17T05:50:30+00:00",
            "failed", "analyzing", "2026-10-19T06:24:20Z", "2026-10-19T06:24:20Z",
            "analysis-no-progress"))
        self.assertEqual(result["tierGeneratedAtUtc"], "2026-10-18T02:11:43Z")
        self.assertEqual(result["modelGeneratedAtUtc"], "2026-10-17T05:50:30Z")
        self.assertEqual(result["latestRefresh"]["status"], "failed")
        self.assertEqual(result["latestRefresh"]["failureCode"], "analysis-no-progress")
        self.assertEqual(set(result["latestRefresh"]),
            {"status", "stage", "updatedAtUtc", "completedAtUtc", "failureCode"})
        cursor.execute.assert_called_once()
        query, params = cursor.execute.call_args.args
        self.assertEqual(params, (combined_tier_blob_path(), recommendation_blob_path()))
        self.assertNotIn("job.payload,", query)
        self.assertNotIn("select payload_json,", query)
        for mutation in ("update ", "insert ", "delete ", "claim_job", "cancel_job"):
            self.assertNotIn(mutation, query.lower())

    def test_successful_refresh_replaces_failure_without_exposing_arbitrary_fields(self):
        result, _ = self.read_row((EXPECTED_PUMBILITY_MIGRATION,
            "2026-10-19T07:00:00Z", "2026-10-19T07:00:00Z", "completed",
            "private-stage-player-key", "2026-10-19T07:01:00Z", "2026-10-19T07:01:00Z", None))
        self.assertEqual(result["latestRefresh"]["status"], "completed")
        self.assertIsNone(result["latestRefresh"]["failureCode"])
        self.assertIsNone(result["latestRefresh"]["stage"])
        self.assertNotIn("private-stage-player-key", json.dumps(result))

    def test_absent_artifacts_or_job_are_null(self):
        result, _ = self.read_row((EXPECTED_PUMBILITY_MIGRATION, None, None, None, None, None, None, None))
        self.assertIsNone(result["latestRefresh"])
        self.assertIsNone(result["tierGeneratedAtUtc"])
        self.assertIsNone(result["modelGeneratedAtUtc"])

    def test_bad_timestamps_and_failure_codes_do_not_leak(self):
        result, _ = self.read_row((EXPECTED_PUMBILITY_MIGRATION, "private-invalid", "not-a-date",
            "failed", "syncing", "private-date", None, "private-exception-message"))
        self.assertIsNone(result["tierGeneratedAtUtc"])
        self.assertIsNone(result["modelGeneratedAtUtc"])
        self.assertIsNone(result["latestRefresh"]["updatedAtUtc"])
        self.assertIsNone(result["latestRefresh"]["failureCode"])
        self.assertNotIn("private", json.dumps(result))

    def test_route_is_no_store_and_sanitizes_storage_errors(self):
        with patch("api.refresh_status.read_refresh_status", side_effect=RuntimeError("private-secret-db-url")):
            response = get_refresh_status()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(json.loads(response.body), {"error": "Refresh status is temporarily unavailable."})

    def test_schema_mismatch_is_unavailable(self):
        with self.assertRaises(RuntimeError):
            self.read_row(("wrong-schema", None, None, None, None, None, None, None))


if __name__ == "__main__":
    unittest.main()
