from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from official_scoring_sources import (
    MIXES, canonical_scoring_manifest_sha256, capture_official_scoring_sources,
    hydrate_official_scoring_manifest, import_research_scoring_sources, load_official_scoring_manifest,
    refresh_hosted_official_scoring_sources,
)
from official_score_merge import merge_official_scores

A = "00000000-0000-0000-0000-000000000020"
B = "00000000-0000-0000-0000-000000000021"
C = "00000000-0000-0000-0000-000000000019"
OLD = "2026-09-06T00:00:00.1234567+00:00"
NEW = "2026-10-11T00:00:00Z"
LATER = "2026-10-18T00:00:00Z"
NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def row(pid=1, tag="PLAYER#1234", score=990000, place=1, supplemented=False):
    return {"playerId": pid, "gameTag": tag, "score": score, "place": place, "isSupplemented": supplemented}


def chart(cid=A, mode="Single", level=20, notes=1000):
    return {"id": cid, "type": mode, "level": level, "noteCount": notes}


def api_board(rows, date):
    return {"asOf": date, "data": [{"score": r["score"], "place": r["place"], "player": r} for r in rows]}


def research(root, old_rows=None, new_rows=None, old_catalog=None, new_catalog=None):
    catalogs = {"Phoenix": [chart()] if old_catalog is None else old_catalog,
                "Phoenix2": [chart()] if new_catalog is None else new_catalog}
    rows = {"Phoenix": {A: [row()]} if old_rows is None else old_rows,
            "Phoenix2": {A: [row(99, score=991000)]} if new_rows is None else new_rows}
    for mix in MIXES:
        (root / mix).mkdir(parents=True, exist_ok=True)
        (root / f"{mix}-catalog.json").write_text(json.dumps(catalogs[mix]), encoding="utf-8")
        for cid, entries in rows[mix].items():
            saved = {"chartId": cid, "mix": mix}
            if entries is None:
                saved["unavailable"] = "not-mirrored"
            else:
                saved.update(asOf=OLD if mix == "Phoenix" else NEW, entries=entries)
            (root / mix / f"{cid}.json").write_text(json.dumps(saved), encoding="utf-8")


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.paths = []

    def _get_json(self, path):
        self.paths.append(path)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class OfficialScoringSourcesTests(unittest.TestCase):
    def test_hosted_batches_resume_across_requests_and_publish_only_when_complete(self):
        from analysis_runtime import MemoryBlobStore
        from pumbility_contract import official_scoring_manifest_path
        store = MemoryBlobStore()
        class Client(FakeClient):
            def fetch_page_collection(self, path):
                return [chart(), chart(B, level=21)]
        first = Client([api_board([row()], OLD), api_board([row()], OLD)])
        result = refresh_hosted_official_scoring_sources(first, store, max_boards=2)
        self.assertEqual(result["outcome"], "official-sources-progress")
        self.assertIsNone(store.get_json(official_scoring_manifest_path()))
        second = Client([api_board([row()], OLD), api_board([row(99)], NEW)])
        result = refresh_hosted_official_scoring_sources(second, store, max_boards=2)
        self.assertEqual(result["outcome"], "official-sources-progress")
        self.assertIsNone(store.get_json(official_scoring_manifest_path()))
        third = Client([api_board([row()], OLD), api_board([row(99)], NEW), api_board([row(99)], NEW)])
        result = refresh_hosted_official_scoring_sources(third, store, max_boards=3)
        self.assertEqual(result["sourceDates"], {"Phoenix": OLD, "Phoenix2": NEW})
        self.assertIsNotNone(store.get_json(official_scoring_manifest_path()))
        pending_key = official_scoring_manifest_path().rsplit("/", 1)[0] + "/refresh-pending.json"
        self.assertEqual(store.get_json(pending_key), {})

    def test_hosted_refresh_stages_complete_sources_and_reuses_archived_boards(self):
        from analysis_runtime import MemoryBlobStore
        from pumbility_contract import official_scoring_manifest_path
        store = MemoryBlobStore()
        class Client(FakeClient):
            def fetch_page_collection(self, path):
                return [chart(), chart(B, level=21)]
        first = Client([api_board([row()], OLD), api_board([row()], OLD),
                        api_board([row(99)], NEW), api_board([row(99)], NEW)])
        result = refresh_hosted_official_scoring_sources(first, store)
        self.assertEqual(result["boardCounts"], {"Phoenix": 2, "Phoenix2": 2})
        self.assertNotIn("PLAYER#1234", json.dumps(result))
        second = Client([api_board([row()], OLD), api_board([row(99)], LATER), api_board([row(99)], LATER)])
        result = refresh_hosted_official_scoring_sources(second, store)
        self.assertEqual(len(second.paths), 3)
        self.assertEqual(result["sourceDates"]["Phoenix2"], LATER)
        manifest = store.get_json(official_scoring_manifest_path())
        self.assertEqual(canonical_scoring_manifest_sha256(manifest), result["manifestSha256"])
        before = copy.deepcopy(store.values)
        failed = Client([api_board([row()], OLD), RuntimeError("upstream unavailable")])
        with self.assertRaisesRegex(RuntimeError, "upstream unavailable"):
            refresh_hosted_official_scoring_sources(failed, store)
        self.assertEqual(before, store.values)

    def test_hosted_storage_failure_does_not_advance_manifest(self):
        from analysis_runtime import MemoryBlobStore
        from pumbility_contract import official_scoring_manifest_path
        from unittest.mock import patch
        store = MemoryBlobStore()
        class Client(FakeClient):
            def fetch_page_collection(self, path):
                return [chart()]
        client = Client([api_board([row()], OLD), api_board([row(99)], NEW)])
        with patch.object(store, "put_json", side_effect=RuntimeError("storage unavailable")):
            with self.assertRaisesRegex(RuntimeError, "storage unavailable"):
                refresh_hosted_official_scoring_sources(client, store)
        self.assertIsNone(store.get_json(official_scoring_manifest_path()))

    def test_research_import_max_raw_and_private_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root, output = Path(temp) / "research", Path(temp) / "private/manifest.json"
            research(root, old_rows={A: [row(score=995000), row(score=994000, place=2)]})
            loaded = import_research_scoring_sources(root, output)
            merged = merge_official_scores(loaded)
            self.assertEqual(merged["sourceDates"], {"Phoenix": OLD, "Phoenix2": NEW})
            self.assertEqual(list(merged["boards"][A].values()), [995000])
            self.assertEqual(merged["charts"][A]["matchedPlayerChartPairs"], 1)
            private = next(iter(merged["_privateProvenance"][A].values()))
            self.assertEqual(private["winningSources"], ["Phoenix"])
            self.assertEqual(len(private["observations"]), 3)
            self.assertNotIn("PLAYER#1234", json.dumps({k: v for k, v in merged.items() if k not in ("_privateProvenance", "currentCatalog")}))
            self.assertEqual(merged["manifestSha256"], canonical_scoring_manifest_sha256(loaded))
            persisted = json.loads(output.read_text())
            hydrated = hydrate_official_scoring_manifest(persisted, lambda p: json.loads((output.parent / p).read_text()))
            self.assertEqual(merge_official_scores(hydrated)["boards"], merged["boards"])

    def test_different_tags_equal_ids_missing_tags_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as temp:
            root, output = Path(temp) / "research", Path(temp) / "private/manifest.json"
            research(root,
                     old_rows={A: [row(1, "SAME#1"), row(2, None), row(3, "RENAME#1"), row(4, "DUP#1")],
                               B: [row(2, "RECOVER#1"), row(3, "RENAME#2"), row(5, "DUP#1")]},
                     new_rows={A: [row(1, "OTHER#1"), row(20, "RECOVER#1"), row(30, "RENAME#1"), row(40, "DUP#1")], B: []},
                     old_catalog=[chart(), chart(B)], new_catalog=[chart(), chart(B)])
            merged = merge_official_scores(import_research_scoring_sources(root, output))
            # Missing tag recovered from source ID; contradictory rename/duplicate tags never link.
            self.assertEqual(len(merged["boards"][A]), 7)
            self.assertEqual(merged["charts"][A]["matchedPlayerChartPairs"], 1)
            self.assertEqual(merged["diagnostics"]["ambiguousSourceIdentities"]["Phoenix"], 1)
            self.assertEqual(merged["diagnostics"]["ambiguousFullTags"]["Phoenix"], 1)

    def test_cap_empty_absent_and_missing_states_and_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root, output = Path(temp) / "research", Path(temp) / "private/manifest.json"
            research(root,
                     old_rows={A: [row(place=100)]}, new_rows={A: [], B: None, C: [row(score=1)]},
                     old_catalog=[chart()], new_catalog=[chart(), chart(B, level=21), chart(C, level=19)])
            merged = merge_official_scores(import_research_scoring_sources(root, output))
            self.assertEqual(set(merged["charts"]), {A, B})
            self.assertTrue(merged["charts"][A]["sourceStates"]["Phoenix"]["possiblyTruncated"])
            self.assertEqual(merged["charts"][A]["sourceStates"]["Phoenix2"]["status"], "empty")
            self.assertEqual(merged["charts"][B]["sourceStates"]["Phoenix"]["status"], "absent-from-catalog")
            self.assertEqual(merged["charts"][B]["sourceStates"]["Phoenix2"]["status"], "not-mirrored")
            self.assertNotIn(B, merged["boards"])

    def test_source_aware_note_counts_and_tied_maxima(self):
        scenarios = [
            (None, 1000, [], [row(99)], "single-source-known", True),
            (1000, 1100, [row(score=980000)], [row(99, score=990000)], "single-source-known", True),
            (1000, 1100, [row(score=995000)], [row(99, score=990000)], "changed", False),
            (None, 1100, [row(score=995000)], [row(99, score=990000)], "unknown", False),
            (1000, 1000, [row()], [row(99)], "same", True),
            (1000, 1100, [row()], [row(99)], "changed", False),
        ]
        for old_notes, notes, old_rows, new_rows, expected, eligible in scenarios:
            with self.subTest(expected=expected, old=old_notes, notes=notes), tempfile.TemporaryDirectory() as temp:
                root, output = Path(temp) / "research", Path(temp) / "private/manifest.json"
                old_catalog = [] if old_notes is None and not old_rows else [chart(notes=old_notes)]
                research(root, old_rows={} if not old_catalog else {A: old_rows}, new_rows={A: new_rows},
                         old_catalog=old_catalog, new_catalog=[chart(notes=notes)])
                # Import requires a dated board for every source, so absent-only Phoenix needs another chart.
                if not old_catalog:
                    (root / "Phoenix-catalog.json").write_text(json.dumps([chart(B)]), encoding="utf-8")
                    (root / "Phoenix2-catalog.json").write_text(json.dumps([chart(notes=notes), chart(B)]), encoding="utf-8")
                    for mix, date in (("Phoenix", OLD), ("Phoenix2", NEW)):
                        (root / mix / f"{B}.json").write_text(json.dumps({"chartId": B, "mix": mix, "asOf": date, "entries": []}), encoding="utf-8")
                merged = merge_official_scores(import_research_scoring_sources(root, output))
                self.assertEqual(merged["charts"][A]["comparability"], expected)
                self.assertEqual(merged["charts"][A]["anchorEligible"], eligible)

    def test_invalid_import_never_replaces_manifest_and_hashes_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root, output = Path(temp) / "research", Path(temp) / "private/manifest.json"
            research(root)
            import_research_scoring_sources(root, output)
            before = output.read_bytes()
            board = root / "Phoenix2" / f"{A}.json"
            saved = json.loads(board.read_text())
            for value in [True, -1, 1_000_001, 1.5]:
                saved["entries"][0]["score"] = value
                board.write_text(json.dumps(saved), encoding="utf-8")
                with self.assertRaises(ValueError):
                    import_research_scoring_sources(root, output)
                self.assertEqual(output.read_bytes(), before)
            loaded = load_official_scoring_manifest(output)
            loaded["_captures"]["Phoenix"]["boards"][A]["entries"][0]["score"] -= 1
            with self.assertRaises(ValueError):
                merge_official_scores(loaded)
            ref = json.loads(output.read_text())["captures"]["Phoenix"]["path"]
            with (output.parent / ref).open("a") as handle:
                handle.write(" ")
            with self.assertRaisesRegex(ValueError, "hash"):
                load_official_scoring_manifest(output)

    def test_capture_refresh_cache_resume_and_failure_preserve_current(self):
        catalogs = {m: [chart(), chart(B, level=21)] for m in MIXES}
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "manifest.json"
            first = FakeClient([api_board([row()], OLD), api_board([row()], OLD),
                                api_board([row(99)], NEW), api_board([row(99)], NEW)])
            capture_official_scoring_sources(first, catalogs, output, now=lambda: NOW)
            self.assertTrue(all("supplemented=false" in p and "mix=" in p for p in first.paths))
            before = output.read_bytes()
            interrupted = FakeClient([api_board([row()], OLD), api_board([row(99)], LATER), RuntimeError("transport")])
            with self.assertRaisesRegex(RuntimeError, "transport"):
                capture_official_scoring_sources(interrupted, catalogs, output, now=lambda: NOW)
            self.assertEqual(output.read_bytes(), before)
            resumed = FakeClient([api_board([row()], OLD), api_board([row(99)], LATER), api_board([row(99)], LATER)])
            loaded = capture_official_scoring_sources(resumed, catalogs, output, now=lambda: NOW)
            self.assertEqual(len(resumed.paths), 3)  # one old-source probe and two current-source boards
            self.assertEqual(loaded["_captures"]["Phoenix2"]["asOf"], LATER)

    def test_intra_mix_date_change_supplemented_and_catalog_drift_rejected(self):
        catalogs = {m: [chart(), chart(B, level=21)] for m in MIXES}
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "manifest.json"
            client = FakeClient([api_board([row()], OLD), api_board([row()], NEW)])
            with self.assertRaisesRegex(ValueError, "source dates"):
                capture_official_scoring_sources(client, catalogs, output, now=lambda: NOW)
            self.assertFalse(output.exists())
            with self.assertRaisesRegex(ValueError, "supplemented"):
                capture_official_scoring_sources(FakeClient([api_board([row(supplemented=True)], OLD)]), catalogs, output, restart=True)
            root = Path(temp) / "research"
            research(root)
            import_research_scoring_sources(root, output)
            load_official_scoring_manifest(output, [chart()])
            with self.assertRaisesRegex(ValueError, "current chart catalog"):
                load_official_scoring_manifest(output, [chart(level=21)])
            with self.assertRaisesRegex(ValueError, "current chart catalog"):
                load_official_scoring_manifest(output, [chart(level=19)])


if __name__ == "__main__":
    unittest.main()
