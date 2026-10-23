from contextlib import nullcontext
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from bpm_metadata_migration import (
    BPM_REVISION_PATH, SOURCE_REVISION_PATH, BpmMigrationConflict,
    apply_migration, build_migration_plan, read_active_chart_rows,
    stage_local_snapshot,
)
from pumbility_store import _canonical_json_bytes
from scripts.backfill_song_bpm_metadata import main
from song_bpm_metadata import bpm_metadata_identity, enrich_chart_bpm, load_runtime_catalog


def digest(value):
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def record(payload, *, db_id="db-1", revision=3):
    return {"chartDbId": db_id, "chartId": payload["id"], "revisionNumber": revision,
            "contentHash": digest(payload), "payload": payload,
            "bpmMin": payload.get("bpmMin"), "bpmMax": payload.get("bpmMax")}


SOURCE_TOKEN = {"sha256": "source-hash", "byteSize": 10, "revision": "42"}


class BpmMetadataMigrationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_runtime_catalog()
        self.payload = {"id": "chart-1", "songName": "Transmission", "type": "Double", "level": 25,
                        "difficulty": "D25", "noteCount": 2400, "bpmMin": 100, "bpmMax": 225,
                        "customRetainedField": {"proof": [1, 2, 3]}}
        self.rows = [record(self.payload)]
        self.plan = build_migration_plan(self.rows, source_revision=SOURCE_TOKEN, catalog=self.catalog)
        self.connection = MagicMock()
        self.cursor = MagicMock()
        self.cursor.rowcount = 1
        self.connection.transaction.side_effect = lambda: nullcontext()
        self.connection.cursor.side_effect = lambda: nullcontext(self.cursor)

    def apply(self, *, plan=None, rows=None, revisions=None):
        with patch("bpm_metadata_migration._assert_schema"), \
             patch("bpm_metadata_migration.read_active_chart_rows", return_value=rows if rows is not None else self.rows), \
             patch("bpm_metadata_migration.read_artifact_revisions", return_value=revisions if revisions is not None else {SOURCE_REVISION_PATH: SOURCE_TOKEN}), \
             patch("bpm_metadata_migration.bump_tier_source_revision") as bump, \
             patch("bpm_metadata_migration.PumbilityArtifactStore._put_json_row") as put:
            result = apply_migration(self.connection, plan or self.plan, catalog=self.catalog)
            return result, bump, put

    def test_dry_run_pins_all_ids_hashes_and_only_approved_metadata(self):
        saved = self.plan["charts"][0]
        self.assertEqual(saved["expectedContentHash"], digest(self.payload))
        self.assertEqual(saved["chartId"], self.payload["id"])
        self.assertEqual(saved["after"], {"songType": "Arcade", "bpmMin": 100, "bpmMax": 225, "bpmFocal": 169})
        self.assertEqual(self.plan["summary"], {"activeCharts": 1, "changedCharts": 1})
        self.assertNotIn("customRetainedField", saved["after"])
        self.assertNotIn("bpmFocal", self.payload)
        with self.assertRaisesRegex(BpmMigrationConflict, "content_hash"):
            build_migration_plan([{**self.rows[0], "contentHash": "wrong"}], source_revision=SOURCE_TOKEN)

    def test_apply_uses_only_chart_revision_sql_preserves_source_and_bumps_once(self):
        result, bump, put = self.apply()
        self.assertEqual(result["changedCharts"], 1)
        bump.assert_called_once_with(self.cursor, "phoenix2")
        put.assert_called_once_with(self.cursor, BPM_REVISION_PATH, bpm_metadata_identity())
        statements = [(call.args[0], call.args[1] if len(call.args) > 1 else None) for call in self.cursor.execute.call_args_list]
        mutations = [sql.lower() for sql, _ in statements if sql.lower().startswith(("update ", "insert ", "delete "))]
        self.assertEqual(len(mutations), 2)
        self.assertTrue(all("pumbility.chart_revisions" in sql for sql in mutations))
        self.assertIn("source_sync_run_id", mutations[1])
        self.assertIn("level,difficulty,step_artist,image_url,note_count", mutations[1])
        inserted = next(params for sql, params in statements if sql.lower().startswith("insert "))
        target = json.loads(inserted[2])
        self.assertEqual(target["customRetainedField"], self.payload["customRetainedField"])
        self.assertEqual(target["id"], self.payload["id"])
        self.assertEqual(inserted[:2], (100, 225))
        self.assertEqual(inserted[3], digest(target))

    def test_same_saved_plan_rerun_is_noop_without_new_revisions_or_source_bump(self):
        target = enrich_chart_bpm(self.payload)
        rows = [record(target, revision=4)]
        result, bump, put = self.apply(rows=rows, revisions={SOURCE_REVISION_PATH: {**SOURCE_TOKEN, "revision": "43"}})
        self.assertTrue(result["noop"])
        self.assertEqual(result["changedCharts"], 0)
        bump.assert_not_called()
        put.assert_not_called()
        self.assertFalse(any(call.args[0].lower().startswith(("update ", "insert ", "delete ")) for call in self.cursor.execute.call_args_list))

    def test_active_roster_revision_and_source_conflicts_stop_before_mutations(self):
        conflicts = [
            ([], {SOURCE_REVISION_PATH: SOURCE_TOKEN}),
            ([{**self.rows[0], "revisionNumber": 4}], {SOURCE_REVISION_PATH: SOURCE_TOKEN}),
            (self.rows, {SOURCE_REVISION_PATH: {**SOURCE_TOKEN, "revision": "43"}}),
            ([{**self.rows[0], "payload": {**self.payload, "noteCount": 2401}}], {SOURCE_REVISION_PATH: SOURCE_TOKEN}),
        ]
        for rows, revisions in conflicts:
            self.cursor.reset_mock()
            with self.subTest(rows=rows), self.assertRaises(BpmMigrationConflict):
                self.apply(rows=rows, revisions=revisions)
            self.assertFalse(any(call.args[0].lower().startswith(("update ", "insert ", "delete ")) for call in self.cursor.execute.call_args_list))

    def test_saved_plan_tamper_and_wrong_runtime_revision_are_rejected(self):
        modified = deepcopy(self.plan)
        modified["charts"][0]["after"]["bpmFocal"] = 225
        with self.assertRaisesRegex(BpmMigrationConflict, "modified"):
            self.apply(plan=modified)
        modified = deepcopy(self.plan)
        modified["bpmMetadataRevision"] = "old-revision"
        modified["planSha256"] = digest({key: value for key, value in modified.items() if key != "planSha256"})
        with self.assertRaisesRegex(BpmMigrationConflict, "different compiled"):
            self.apply(plan=modified)

    def test_artifact_bundle_promotion_is_inside_same_transaction_and_hash_pinned(self):
        path = "analysis/combined/latest.json"
        old_pointer = {"sha256": "old-artifact", "byteSize": 42, "revision": "21"}
        prepared = {path: {"payload": {"bpmMetadata": bpm_metadata_identity(), "charts": []}, "expectedRevision": old_pointer}}
        plan = build_migration_plan(self.rows, source_revision=SOURCE_TOKEN, prepared_artifacts=prepared)
        result, bump, put = self.apply(plan=plan, revisions={SOURCE_REVISION_PATH: SOURCE_TOKEN, path: old_pointer})
        self.assertEqual(result["promotedArtifacts"], 1)
        self.assertEqual(put.call_args_list[-1].args, (self.cursor, path, prepared[path]["payload"]))
        self.connection.transaction.assert_called_once()
        wrong = {path: {"payload": {"bpmMetadata": {**bpm_metadata_identity(), "bpmMetadataRevision": "wrong"}}, "expectedRevision": old_pointer}}
        with self.assertRaisesRegex(BpmMigrationConflict, "different BPM"):
            build_migration_plan(self.rows, source_revision=SOURCE_TOKEN, prepared_artifacts=wrong)
        with self.assertRaisesRegex(BpmMigrationConflict, "release pointer"):
            self.apply(plan=plan, revisions={SOURCE_REVISION_PATH: SOURCE_TOKEN, path: {**old_pointer, "revision": "22"}})

    def test_unknown_future_range_blocks_a_current_migration_without_recorded_focal(self):
        payload = {**self.payload, "songName": "Unknown Future Song", "songType": "Arcade"}
        with self.assertRaisesRegex(BpmMigrationConflict, "No focal"):
            build_migration_plan([record(payload)], source_revision=SOURCE_TOKEN)

    def test_read_roster_detects_active_chart_without_current_revision(self):
        cursor = MagicMock()
        cursor.fetchall.side_effect = [[("db-1", "chart-1")], []]
        with self.assertRaisesRegex(BpmMigrationConflict, "missing/duplicate"):
            read_active_chart_rows(cursor, lock=True)

    def test_snapshot_candidate_preserves_player_and_compressed_score_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, candidate = Path(temporary) / "source", Path(temporary) / "candidate"
            source.mkdir()
            players = [{"userId": "player-1", "username": "A", "lastSyncedAtUtc": "now", "lastScoreRecordedAtUtc": None}]
            chart = {key: value for key, value in self.payload.items() if key != "customRetainedField"}
            scores = [{"playerId": "player-1", "chartId": "chart-1", "pumbility": 500, "score": 990000, "isBroken": False}]
            (source / "players.json").write_text(json.dumps(players), encoding="utf-8")
            (source / "charts.json").write_text(json.dumps([chart]), encoding="utf-8")
            with gzip.open(source / "scores.jsonl.gz", "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(scores[0]) + "\n")
            checksums = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in ("players.json", "charts.json", "scores.jsonl.gz")}
            manifest = {"mix": "Phoenix2", "players": 1, "charts": 1, "scoreRows": 1,
                        "credentialStored": False, "checksums": checksums}
            (source / "snapshot_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = stage_local_snapshot(source, candidate)
            self.assertTrue(result["bpmMetadataOverlay"]["playersAndScoresBytesPreserved"])
            for name in ("players.json", "scores.jsonl.gz"):
                self.assertEqual((source / name).read_bytes(), (candidate / name).read_bytes())
            self.assertEqual(json.loads((candidate / "charts.json").read_text(encoding="utf-8"))[0]["bpmFocal"], 169)
            self.assertEqual(json.loads((source / "charts.json").read_text(encoding="utf-8"))[0], chart)
            with self.assertRaisesRegex(BpmMigrationConflict, "separate"):
                stage_local_snapshot(source, candidate)

    def test_cli_refuses_remote_targets_before_connect_and_apply_requires_saved_plan(self):
        with patch("scripts.backfill_song_bpm_metadata._connect") as connect:
            self.assertEqual(main(["--apply", "--plan", "missing-plan.json", "--database-url", "postgresql://example.com/db"]), 1)
            connect.assert_not_called()
            self.assertEqual(main(["--apply", "--database-url", "postgresql://localhost/db"]), 1)
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
