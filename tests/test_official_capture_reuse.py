"""Unchanged official epochs reuse complete evidence without freezing gaps."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from analysis_runtime import MemoryBlobStore
from official_scoring_sources import (
    hydrate_official_scoring_manifest, import_research_scoring_sources,
    refresh_hosted_official_scoring_sources,
)
from pumbility_contract import official_scoring_manifest_path, phoenix_scoring_archive_path
from tests.test_official_scoring_sources import A, B, C, NEW, LATER, api_board, chart, research, row


class LiveClient:
    def __init__(self, catalog, boards):
        self.catalog, self.boards = catalog, boards
        self.catalog_calls, self.board_calls = [], []

    def fetch_page_collection(self, path):
        self._current_only(path)
        self.catalog_calls.append(path)
        return self.catalog

    @staticmethod
    def _current_only(path):
        if parse_qs(urlsplit(path).query).get("mix") != ["Phoenix2"]:
            raise AssertionError("Frozen Phoenix must not be requested")

    def _get_json(self, path):
        self._current_only(path)
        cid = urlsplit(path).path.split("/")[-2]
        self.board_calls.append(cid)
        value = self.boards[cid]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


class OfficialCaptureReuseTests(unittest.TestCase):
    def seed(self, *, rows=None, catalog=None):
        catalog = catalog or [chart(), chart(B)]
        rows = {A: [row(99)], B: [row(99)]} if rows is None else rows
        store = MemoryBlobStore()
        with tempfile.TemporaryDirectory() as directory:
            root, path = Path(directory) / "research", Path(directory) / "private/manifest.json"
            research(root, new_rows=rows, new_catalog=catalog)
            loaded = import_research_scoring_sources(root, path)
            manifest = {k: v for k, v in loaded.items() if not k.startswith("_")}
            prefix = official_scoring_manifest_path().rsplit("/", 1)[0]
            for mix, reference in manifest["captures"].items():
                store.put_json(f"{prefix}/{reference['path']}", loaded["_captures"][mix])
            store.put_json(official_scoring_manifest_path(), manifest)
        return store, manifest

    def loaded(self, store):
        prefix = official_scoring_manifest_path().rsplit("/", 1)[0]
        return hydrate_official_scoring_manifest(store.get_json(official_scoring_manifest_path()),
            lambda relative: store.get_json(f"{prefix}/{relative}"))

    def test_complete_epoch_reuses_after_one_probe_and_preserves_fetch_times(self):
        store, manifest = self.seed()
        before = self.loaded(store)["_captures"]
        client = LiveClient([chart(), chart(B)], {A: api_board([row(99)], NEW)})
        with patch.object(store, "put_json", wraps=store.put_json) as writes:
            result = refresh_hosted_official_scoring_sources(client, store, refresh_id="unchanged")
        self.assertEqual(client.board_calls, [A])
        self.assertEqual(len(client.catalog_calls), 1)
        self.assertEqual(store.get_json(official_scoring_manifest_path()), manifest)
        self.assertEqual(self.loaded(store)["_captures"], before)
        capture_paths = {f"{official_scoring_manifest_path().rsplit('/', 1)[0]}/{r['path']}" for r in manifest["captures"].values()}
        self.assertFalse(any(call.args[0] in capture_paths for call in writes.call_args_list))
        self.assertIn("sourceCheckedAtUtc", result)
        self.assertNotIn("PLAYER#1234", str(result))

    def test_every_new_refresh_retries_empty_and_not_mirrored_boards(self):
        catalog = [chart(), chart(B), chart(C)]
        store, _ = self.seed(catalog=catalog, rows={A: [row(99)], B: None, C: []})
        for refresh_id in ("gap-one", "gap-two"):
            client = LiveClient(catalog, {A: api_board([row(99)], NEW),
                B: RuntimeError("HTTP 404"), C: api_board([], NEW)})
            refresh_hosted_official_scoring_sources(client, store, refresh_id=refresh_id)
            self.assertEqual(client.board_calls, [A, C, B])
        client = LiveClient(catalog, {A: api_board([row(99)], NEW),
            B: api_board([row(101)], NEW), C: api_board([row(102)], NEW)})
        refresh_hosted_official_scoring_sources(client, store, refresh_id="gaps-filled")
        self.assertTrue(all(board["status"] == "available" for board in self.loaded(store)["_captures"]["Phoenix2"]["boards"].values()))

    def test_catalog_change_uses_full_path_even_with_unchanged_epoch(self):
        store, _ = self.seed()
        catalog = [chart(), chart(B), chart(C)]
        client = LiveClient(catalog, {cid: api_board([row(99)], NEW) for cid in (A, B, C)})
        refresh_hosted_official_scoring_sources(client, store, refresh_id="new-chart")
        self.assertEqual(set(client.board_calls), {A, B, C})
        self.assertEqual(len(self.loaded(store)["_captures"]["Phoenix2"]["boards"]), 3)

    def test_changed_epoch_and_force_both_fetch_all_current_boards(self):
        for force, date in ((False, LATER), (True, NEW)):
            with self.subTest(force=force):
                store, _ = self.seed()
                client = LiveClient([chart(), chart(B)], {cid: api_board([row(99)], date) for cid in (A, B)})
                result = refresh_hosted_official_scoring_sources(client, store, force=force, refresh_id="full")
                self.assertEqual(client.board_calls, [A, B])
                self.assertEqual(result["sourceDates"]["Phoenix2"], date)

    def test_same_epoch_populated_probe_change_falls_back_to_full_capture(self):
        store, _ = self.seed()
        client = LiveClient([chart(), chart(B)], {A: api_board([row(99, score=999000)], NEW), B: api_board([row(99)], NEW)})
        refresh_hosted_official_scoring_sources(client, store, refresh_id="correction")
        self.assertEqual(client.board_calls, [A, B])
        self.assertEqual(self.loaded(store)["_captures"]["Phoenix2"]["boards"][A]["entries"][0]["score"], 999000)

    def test_mixed_epoch_or_upstream_error_preserves_complete_publication(self):
        for value in (api_board([row(99)], NEW), RuntimeError("upstream unavailable")):
            with self.subTest(error=isinstance(value, Exception)):
                store, manifest = self.seed()
                client = LiveClient([chart(), chart(B)], {A: api_board([row(99)], LATER), B: value})
                with self.assertRaises((ValueError, RuntimeError)):
                    refresh_hosted_official_scoring_sources(client, store, refresh_id="failed")
                self.assertEqual(store.get_json(official_scoring_manifest_path()), manifest)

    def test_partial_job_pins_catalog_and_completed_gap_retries(self):
        catalog = [chart(), chart(B), chart(C)]
        store, manifest = self.seed(catalog=catalog, rows={A: [row(99)], B: None, C: None})
        first = LiveClient(catalog, {A: api_board([row(99)], NEW), C: RuntimeError("HTTP 404")})
        progress = refresh_hosted_official_scoring_sources(first, store, max_boards=2, refresh_id="bounded")
        self.assertEqual(progress["outcome"], "official-sources-progress")
        self.assertEqual(progress["capturedBoards"]["Phoenix2"], 2)
        self.assertEqual(store.get_json(official_scoring_manifest_path()), manifest)
        archive = store.get_json(phoenix_scoring_archive_path())
        second = LiveClient(catalog, {A: api_board([row(99)], NEW), B: api_board([row(101)], NEW)})
        result = refresh_hosted_official_scoring_sources(second, store, max_boards=2, refresh_id="bounded")
        self.assertEqual(second.catalog_calls, [])
        self.assertEqual(second.board_calls, [A, B])
        self.assertEqual(result["boardCounts"]["Phoenix2"], 3)
        self.assertEqual(store.get_json(phoenix_scoring_archive_path()), archive)

    def test_deadline_yields_and_next_batch_preserves_force_full_policy(self):
        store, manifest = self.seed()
        first = LiveClient([chart(), chart(B)], {A: api_board([row(99)], NEW)})
        with patch("analysis_budget.work_budget_available", side_effect=[True, False]):
            progress = refresh_hosted_official_scoring_sources(first, store, force=True, refresh_id="forced")
        self.assertEqual(progress["capturedBoards"]["Phoenix2"], 1)
        self.assertEqual(store.get_json(official_scoring_manifest_path()), manifest)
        second = LiveClient([], {A: api_board([row(99)], NEW), B: api_board([row(99)], NEW)})
        refresh_hosted_official_scoring_sources(second, store, force=True, refresh_id="forced")
        self.assertEqual(second.board_calls, [A, B])
        self.assertEqual(second.catalog_calls, [])

    def test_forced_legacy_batches_do_not_adopt_normal_partial_work(self):
        catalog = [chart(), chart(B), chart(C)]
        store, _ = self.seed(catalog=catalog, rows={cid: [row(99)] for cid in (A, B, C)})
        ordinary = LiveClient(catalog, {cid: api_board([row(99)], LATER) for cid in (A, C)})
        refresh_hosted_official_scoring_sources(ordinary, store, max_boards=2)
        forced = LiveClient(catalog, {cid: api_board([row(99)], LATER) for cid in (A, B, C)})
        progress = refresh_hosted_official_scoring_sources(forced, store, max_boards=2, force=True)
        self.assertEqual(forced.board_calls, [C, A])
        self.assertEqual(progress["outcome"], "official-sources-progress")
        resumed = LiveClient(catalog, {cid: api_board([row(99)], LATER) for cid in (B, C)})
        refresh_hosted_official_scoring_sources(resumed, store, max_boards=2, force=True)
        self.assertEqual(resumed.board_calls, [C, B])


if __name__ == "__main__":
    unittest.main()
