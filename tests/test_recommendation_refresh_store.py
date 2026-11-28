from __future__ import annotations

import copy
import unittest
from unittest.mock import MagicMock, patch

from pumbility_contract import recommendation_blob_path, recommendation_index_path
from pumbility_store import EXPECTED_PUMBILITY_MIGRATION, JobLeaseLostError, PumbilityArtifactStore

ANALYSIS = "00000000-0000-0000-0000-000000000001"
MODEL = "00000000-0000-0000-0000-000000000002"
OLD_MODEL = "00000000-0000-0000-0000-000000000003"


def database(*responses):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [(EXPECTED_PUMBILITY_MIGRATION,), *responses]
    cursor.rowcount = 1
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    return connection, cursor


def boundary():
    return {"analysisRunId": ANALYSIS, "previousModelGenerationId": OLD_MODEL,
            "previousGenerationKey": "previous-generation",
            "expectedRevisions": {"base": {"sha256": "a", "byteSize": 1, "revision": "10"}},
            "artifactPaths": {}}


class RecommendationRefreshStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = PumbilityArtifactStore(database_url="postgresql://localhost/local")
        self.index = {"generationKey": "new-generation", "generatedAtUtc": "2026-11-28T00:00:00Z"}
        self.options = {"job_id": "rec-job", "lease_owner": "worker-one", "analysis_run_id": ANALYSIS,
                        "model_generation_id": MODEL, "expected_revisions": boundary()["expectedRevisions"]}
        self.typed = ("published", "shadow", "new-generation", ANALYSIS)

    def test_boundary_resolves_published_model_analysis_and_pins_heads_without_writes(self):
        connection, cursor = database(("previous-generation",),
            (OLD_MODEL, ANALYSIS, "published", "published", "analysis/recommendations/models/previous-generation.json"))
        paths = ("analysis/phoenix2/latest.json", "analysis/combined/latest.json", recommendation_blob_path(),
                 "analysis/recommendations/models/previous-generation.json",
                 "analysis/private/source-revisions/phoenix1.json", "analysis/private/source-revisions/phoenix2.json")
        cursor.fetchall.return_value = [(path, f"hash-{index}", index + 1, str(index + 10)) for index, path in enumerate(paths)]
        with patch("pumbility_store._connect", return_value=connection):
            result = self.store.recommendation_refresh_boundary()
        self.assertEqual(result["analysisRunId"], ANALYSIS)
        self.assertEqual(result["previousModelGenerationId"], OLD_MODEL)
        self.assertEqual(result["previousGenerationKey"], "previous-generation")
        self.assertEqual(result["artifactPaths"]["phoenix1"], "analysis/private/phoenix1.json")
        self.assertEqual(result["artifactPaths"]["phoenix2"], "analysis/private/phoenix2-current.json")
        self.assertIn("analysis/private/phoenix2-current.json", result["expectedRevisions"])
        self.assertIsNone(result["expectedRevisions"]["analysis/private/phoenix2-current.json"])
        queries = [str(call.args[0]).lower().strip() for call in cursor.execute.call_args_list]
        self.assertTrue(any("pg_advisory_xact_lock" in query for query in queries))
        self.assertTrue(any("for share of mg,ar" in query for query in queries))
        self.assertTrue(any("xmin::text" in query and "for update" in query for query in queries))
        self.assertFalse(any(query.startswith(("insert", "update", "delete")) for query in queries))

    def test_boundary_requires_published_analysis_instead_of_reusing_unfinished_run(self):
        connection, _ = database(("previous-generation",),
            (OLD_MODEL, ANALYSIS, "published", "shadow", "analysis/recommendations/models/previous-generation.json"))
        with patch("pumbility_store._connect", return_value=connection), self.assertRaisesRegex(RuntimeError, "published analysis"):
            self.store.recommendation_refresh_boundary()

    def test_publication_changes_only_new_model_and_recommendation_indexes(self):
        connection, cursor = database(self.typed, ("job-uuid",), ("job-uuid",))
        with (patch("pumbility_store._connect", return_value=connection),
              patch("pumbility_store._recommendation_refresh_boundary", return_value=boundary()),
              patch.object(self.store, "_put_json_row") as writer):
            self.store.publish_recommendation_refresh(self.index, **self.options)
        self.assertEqual([call.args[1] for call in writer.call_args_list],
                         [recommendation_index_path("new-generation"), recommendation_blob_path()])
        queries = [str(call.args[0]).lower().strip() for call in cursor.execute.call_args_list]
        writes = [query for query in queries if query.startswith(("insert", "update", "delete"))]
        self.assertEqual(len(writes), 1)
        self.assertTrue(writes[0].startswith("update pumbility.model_generations"))
        self.assertFalse(any("update pumbility.analysis_runs" in query for query in queries))
        lease_calls = [call for call in cursor.execute.call_args_list if "head.name='active:analysis'" in str(call.args[0])]
        self.assertEqual(len(lease_calls), 2)
        self.assertTrue(all(call.args[1] == ("rec-job", "worker-one") for call in lease_calls))
        self.assertEqual(connection.transaction.call_count, 1)

    def test_completion_and_active_head_release_are_in_the_promotion_transaction(self):
        connection, cursor = database(self.typed, ("job-uuid",), ("job-uuid",))
        completed = {"id": "rec-job", "status": "completed", "stage": "publishing"}
        with (patch("pumbility_store._connect", return_value=connection),
              patch("pumbility_store._recommendation_refresh_boundary", return_value=boundary()),
              patch.object(self.store, "_put_json_row") as writer):
            self.store.publish_recommendation_refresh(self.index, completed_job=completed, **self.options)
        queries = [str(call.args[0]).lower() for call in cursor.execute.call_args_list]
        self.assertEqual(writer.call_count, 2)
        self.assertTrue(any("update pumbility.jobs set status='completed'" in query and "lease_owner=null" in query
                            and "cancellation_requested_at is null" in query for query in queries))
        self.assertTrue(any("delete from pumbility.job_heads where name='active:analysis'" in query for query in queries))
        self.assertFalse(any("update pumbility.analysis_runs" in query for query in queries))
        self.assertEqual(connection.transaction.call_count, 1)

    def test_missing_owner_or_cancelled_expired_lease_cannot_publish(self):
        connection, cursor = database(self.typed, None)
        with (patch("pumbility_store._connect", return_value=connection),
              patch("pumbility_store._recommendation_refresh_boundary", return_value=boundary()),
              patch.object(self.store, "_put_json_row") as writer,
              self.assertRaisesRegex(JobLeaseLostError, "lease changed")):
            self.store.publish_recommendation_refresh(self.index, **self.options)
        writer.assert_not_called()
        self.assertFalse(any(str(call.args[0]).strip().lower().startswith("update") for call in cursor.execute.call_args_list))

    def test_changed_input_or_serving_revision_aborts_before_promotion(self):
        for field in ("expectedRevisions", "analysisRunId"):
            actual = copy.deepcopy(boundary())
            actual[field] = {"changed": None} if field == "expectedRevisions" else "different-analysis"
            connection, cursor = database(self.typed, ("job-uuid",))
            with (self.subTest(field=field), patch("pumbility_store._connect", return_value=connection),
                  patch("pumbility_store._recommendation_refresh_boundary", return_value=actual),
                  patch.object(self.store, "_put_json_row") as writer,
                  self.assertRaisesRegex(JobLeaseLostError, "inputs or serving context")):
                self.store.publish_recommendation_refresh(self.index, **self.options)
            writer.assert_not_called()
            self.assertFalse(any(str(call.args[0]).strip().lower().startswith("update") for call in cursor.execute.call_args_list))

    def test_new_model_must_bind_the_existing_published_analysis(self):
        for typed in (("shadow", "shadow", "new-generation", ANALYSIS),
                      ("published", "shadow", "new-generation", "different-analysis")):
            connection, _ = database(typed)
            with (self.subTest(typed=typed), patch("pumbility_store._connect", return_value=connection),
                  patch("pumbility_store._recommendation_refresh_boundary") as reader,
                  patch.object(self.store, "_put_json_row") as writer,
                  self.assertRaisesRegex(RuntimeError, "existing published analysis")):
                self.store.publish_recommendation_refresh(self.index, **self.options)
            reader.assert_not_called()
            writer.assert_not_called()

    def test_lease_expiry_during_writes_rolls_back_the_publication_transaction(self):
        connection, _ = database(self.typed, ("job-uuid",), None)
        with (patch("pumbility_store._connect", return_value=connection),
              patch("pumbility_store._recommendation_refresh_boundary", return_value=boundary()),
              patch.object(self.store, "_put_json_row"),
              self.assertRaisesRegex(JobLeaseLostError, "expired during publication")):
            self.store.publish_recommendation_refresh(self.index, **self.options)
        self.assertIs(connection.transaction.return_value.__exit__.call_args.args[0], JobLeaseLostError)

    def test_interrupted_commit_response_replays_without_rewriting_analysis_or_heads(self):
        current = {**boundary(), "previousGenerationKey": "new-generation", "previousModelGenerationId": MODEL,
                   "expectedRevisions": {"changed-after-commit": None}}
        connection, cursor = database(("published", "published", "new-generation", ANALYSIS))
        with (patch("pumbility_store._connect", return_value=connection),
              patch("pumbility_store._recommendation_refresh_boundary", return_value=current),
              patch.object(self.store, "_put_json_row") as writer):
            self.store.publish_recommendation_refresh(self.index, **self.options)
        writer.assert_not_called()
        self.assertFalse(any(str(call.args[0]).strip().lower().startswith(("update", "insert", "delete")) for call in cursor.execute.call_args_list))


if __name__ == "__main__":
    unittest.main()
