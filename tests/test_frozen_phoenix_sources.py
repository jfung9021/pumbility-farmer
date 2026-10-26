"""Phoenix can disappear upstream without affecting future official refreshes."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from analysis_runtime import MemoryBlobStore
from official_score_merge import merge_official_scores
from official_scoring_sources import (
    PHOENIX_ARCHIVE_NAME, capture_official_scoring_sources,
    fetch_official_scoring_catalogs, import_research_scoring_sources,
    refresh_hosted_official_scoring_sources, validate_phoenix_archive,
)
from pumbility_contract import official_scoring_manifest_path, phoenix_scoring_archive_path
from tests.test_official_scoring_sources import A, B, C, OLD, NEW, LATER, NOW, api_board, chart, research, row


class PhoenixOfflineClient:
    def __init__(self, catalog, *, date=LATER, fail=False):
        self.catalog = catalog
        self.date = date
        self.fail = fail
        self.paths = []

    def check(self, path):
        self.paths.append(path)
        if parse_qs(urlsplit(path).query).get("mix") != ["Phoenix2"]:
            raise AssertionError("Phoenix must never be requested again")
        if self.fail:
            raise RuntimeError("Phoenix2 unavailable")

    def fetch_page_collection(self, path):
        self.check(path)
        return self.catalog

    def _get_json(self, path):
        self.check(path)
        return api_board([row(99, score=991000)], self.date)


class FrozenPhoenixTests(unittest.TestCase):
    def seed(self, *, old_catalog=None, new_catalog=None):
        store = MemoryBlobStore()
        with tempfile.TemporaryDirectory() as temp:
            root, output = Path(temp) / "research", Path(temp) / "private/manifest.json"
            research(root, old_catalog=old_catalog, new_catalog=new_catalog)
            loaded = import_research_scoring_sources(root, output)
            manifest = {k: v for k, v in loaded.items() if not k.startswith("_")}
            prefix = official_scoring_manifest_path().rsplit("/", 1)[0]
            for mix, ref in manifest["captures"].items():
                store.put_json(f"{prefix}/{ref['path']}", loaded["_captures"][mix])
            store.put_json(official_scoring_manifest_path(), manifest)
            return store, copy.deepcopy(loaded["_captures"]["Phoenix"])

    def loaded(self, store):
        from official_scoring_sources import hydrate_official_scoring_manifest
        prefix = official_scoring_manifest_path().rsplit("/", 1)[0]
        return hydrate_official_scoring_manifest(store.get_json(official_scoring_manifest_path()),
            lambda relative: store.get_json(f"{prefix}/{relative}"))

    def test_adopts_existing_snapshot_and_never_requests_or_rewrites_phoenix(self):
        store, original = self.seed()
        for date in (LATER, "2026-10-25T21:28:00Z"):
            client = PhoenixOfflineClient([chart()], date=date)
            with patch.object(store, "put_json", wraps=store.put_json) as write:
                result = refresh_hosted_official_scoring_sources(client, store)
                archive_writes = [c for c in write.call_args_list if c.args[0] == phoenix_scoring_archive_path()]
                self.assertEqual(len(archive_writes), 1 if date == LATER else 0)
            self.assertEqual(len(client.paths), 2)  # Phoenix2 catalog and one board
            self.assertEqual(result["sourceDates"], {"Phoenix": OLD, "Phoenix2": date})
            archive = store.get_json(phoenix_scoring_archive_path())
            self.assertEqual(validate_phoenix_archive(archive), original)
            self.assertEqual(self.loaded(store)["_captures"]["Phoenix"], original)
        self.assertNotIn("PLAYER#1234", json.dumps(result))

    def test_static_scope_projects_expansion_shrink_and_reexpansion_without_data_loss(self):
        store, original = self.seed(old_catalog=[chart(), chart(B, level=19)])
        full = [chart(), chart(B, level=21), chart(C)]
        first_archive = None
        for scope in (full, [chart()], full):
            refresh_hosted_official_scoring_sources(PhoenixOfflineClient(scope), store)
            archive = store.get_json(phoenix_scoring_archive_path())
            first_archive = first_archive or archive
            self.assertEqual(archive, first_archive)
            loaded = self.loaded(store)
            phoenix = loaded["_captures"]["Phoenix"]
            self.assertEqual(phoenix["boards"][A], original["boards"][A])
            self.assertEqual(phoenix["fetchedAtUtc"], original["fetchedAtUtc"])
            if scope == full:
                self.assertEqual(phoenix["boards"][B]["status"], "not-captured")
                self.assertEqual(phoenix["boards"][C]["status"], "absent-from-catalog")
                self.assertEqual(merge_official_scores(loaded)["charts"][B]["sourceStates"]["Phoenix"]["rawRowCount"], 0)

    def test_archive_survives_initial_phoenix2_failure_and_bootstrap_resumes_without_phoenix(self):
        from tests.test_official_scoring_sources import FakeClient
        class BootstrapClient(FakeClient):
            def fetch_page_collection(self, path):
                return [chart()]
        store = MemoryBlobStore()
        client = BootstrapClient([api_board([row()], OLD), RuntimeError("Phoenix2 unavailable")])
        with self.assertRaisesRegex(RuntimeError, "Phoenix2 unavailable"):
            refresh_hosted_official_scoring_sources(client, store)
        archive = store.get_json(phoenix_scoring_archive_path())
        self.assertIsNotNone(archive)
        self.assertIsNone(store.get_json(official_scoring_manifest_path()))
        result = refresh_hosted_official_scoring_sources(PhoenixOfflineClient([chart()]), store)
        self.assertEqual(result["sourceDates"], {"Phoenix": OLD, "Phoenix2": LATER})
        self.assertEqual(store.get_json(phoenix_scoring_archive_path()), archive)

    def test_partial_batches_reuse_archive_and_publish_only_after_phoenix2_complete(self):
        store, _ = self.seed()
        before = store.get_json(official_scoring_manifest_path())
        result = refresh_hosted_official_scoring_sources(PhoenixOfflineClient([chart(), chart(B)]), store, max_boards=1)
        self.assertEqual(result["outcome"], "official-sources-progress")
        self.assertEqual(result["capturedBoards"], {"Phoenix": 2, "Phoenix2": 1})
        self.assertEqual(store.get_json(official_scoring_manifest_path()), before)
        archive = store.get_json(phoenix_scoring_archive_path())
        result = refresh_hosted_official_scoring_sources(PhoenixOfflineClient([chart(), chart(B)]), store, max_boards=2)
        self.assertEqual(result["sourceDates"]["Phoenix2"], LATER)
        self.assertEqual(store.get_json(phoenix_scoring_archive_path()), archive)

    def test_invalid_archive_blocks_upstream_without_fallback_or_manifest_changes(self):
        store, _ = self.seed()
        refresh_hosted_official_scoring_sources(PhoenixOfflineClient([chart()]), store)
        damaged = store.get_json(phoenix_scoring_archive_path())
        damaged["capture"]["boards"][A]["entries"][0]["score"] -= 1
        store.put_json(phoenix_scoring_archive_path(), damaged)
        before = copy.deepcopy(store.values)
        client = PhoenixOfflineClient([chart()])
        with self.assertRaisesRegex(ValueError, "hash"):
            refresh_hosted_official_scoring_sources(client, store)
        self.assertEqual(client.paths, [])
        self.assertEqual(store.values, before)

    def test_live_failure_and_mode_change_preserve_archive_and_publication(self):
        store, _ = self.seed()
        refresh_hosted_official_scoring_sources(PhoenixOfflineClient([chart()]), store)
        before = copy.deepcopy(store.values)
        with self.assertRaisesRegex(RuntimeError, "Phoenix2 unavailable"):
            refresh_hosted_official_scoring_sources(PhoenixOfflineClient([chart()], fail=True), store)
        self.assertEqual(store.values, before)
        with self.assertRaisesRegex(ValueError, "mode changed"):
            refresh_hosted_official_scoring_sources(PhoenixOfflineClient([chart(mode="Double")]), store)
        self.assertEqual(store.values, before)

    def test_local_restart_never_bypasses_frozen_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root, output = Path(temp) / "research", Path(temp) / "private/manifest.json"
            research(root)
            original = import_research_scoring_sources(root, output)["_captures"]["Phoenix"]
            client = PhoenixOfflineClient([chart()])
            catalogs = fetch_official_scoring_catalogs(client, output)
            frozen_path = output.parent / PHOENIX_ARCHIVE_NAME
            before = frozen_path.read_bytes()
            loaded = capture_official_scoring_sources(client, catalogs, output, restart=True, now=lambda: NOW)
            self.assertEqual(loaded["_captures"]["Phoenix"], original)
            self.assertEqual(frozen_path.read_bytes(), before)
            self.assertEqual(len(client.paths), 2)


if __name__ == "__main__":
    unittest.main()
