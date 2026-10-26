"""Numerical identities omit check envelopes only after source validation."""
import copy
import tempfile
import unittest
from pathlib import Path

from analysis_runtime import MemoryBlobStore
from official_scoring_identity import scoring_source_numeric_identity
from official_scoring_sources import (
    _sha, _state, canonical_scoring_manifest_sha256, import_research_scoring_sources,
    normalize_scoring_board, publish_official_scoring_manifest,
)
from tier_computation_cache import preparation_descriptor, read_index, write_index
from tests.test_official_scoring_sources import A, B, NEW, LATER, api_board, chart, research, row


class OfficialNumericIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        research(root / "research", old_catalog=[chart(), chart(B)], new_catalog=[chart(), chart(B)],
            old_rows={A: [row()], B: [row(2)]}, new_rows={A: [row(99), row(100, place=2)], B: [row(101)]})
        self.path = root / "private/manifest.json"
        self.manifest = import_research_scoring_sources(root / "research", self.path)

    def rebuilt(self, captures):
        for capture in captures.values():
            capture["catalogSha256"] = _sha(capture["catalog"])
        return publish_official_scoring_manifest(captures, self.path)

    def test_fetch_check_and_provenance_envelopes_do_not_change_numerical_key(self):
        before = copy.deepcopy(self.manifest)
        captures = copy.deepcopy(before["_captures"])
        for capture in captures.values():
            capture["fetchedAtUtc"] = LATER
            capture["checkedAtUtc"] = LATER
            capture["captureProvenance"] = {"method": "authenticated-api", "check": "new-envelope"}
            for board in capture["boards"].values():
                board["fetchedAtUtc"] = LATER
        changed = self.rebuilt(captures)
        changed["sourceCheckedAtUtc"] = LATER
        changed["_manifestSha256"] = canonical_scoring_manifest_sha256(changed)
        self.assertNotEqual(before["_manifestSha256"], changed["_manifestSha256"])
        self.assertEqual(scoring_source_numeric_identity(before), scoring_source_numeric_identity(changed))
        self.assertEqual(self.manifest, before)

    def test_scores_source_player_ids_tags_order_and_truncation_invalidate(self):
        baseline = scoring_source_numeric_identity(self.manifest)
        for entries in ([row(99, score=990001), row(100, place=2)],
                        [row(199), row(100, place=2)],
                        [row(99, tag="OTHER#4321"), row(100, place=2)],
                        [row(100, place=2), row(99)],
                        [row(99, place=300), row(100, place=2)]):
            with self.subTest(entries=entries):
                captures = copy.deepcopy(self.manifest["_captures"])
                old = captures["Phoenix2"]["boards"][A]
                captures["Phoenix2"]["boards"][A] = normalize_scoring_board(A, "Phoenix2", api_board(entries, NEW), old["fetchedAtUtc"])
                self.assertNotEqual(baseline, scoring_source_numeric_identity(self.rebuilt(captures)))

    def test_catalog_availability_scope_and_epoch_invalidate(self):
        baseline = scoring_source_numeric_identity(self.manifest)
        for kind in ("catalog-level", "catalog-notes", "empty", "not-mirrored", "epoch", "scope"):
            with self.subTest(kind=kind):
                captures = copy.deepcopy(self.manifest["_captures"])
                current = captures["Phoenix2"]
                if kind.startswith("catalog"):
                    current["catalog"][0]["level" if kind.endswith("level") else "noteCount"] += 1
                elif kind == "empty":
                    current["boards"][A] = normalize_scoring_board(A, "Phoenix2", api_board([], NEW), current["fetchedAtUtc"])
                elif kind == "not-mirrored":
                    current["boards"][A] = _state(A, "Phoenix2", "not-mirrored", current["fetchedAtUtc"])
                elif kind == "epoch":
                    current["asOf"] = LATER
                    for board in current["boards"].values():
                        board["asOf"] = LATER
                else:
                    current["catalog"] = [c for c in current["catalog"] if c["id"] != B]
                    for capture in captures.values():
                        capture["boards"].pop(B)
                        capture["targetChartIds"].remove(B)
                self.assertNotEqual(baseline, scoring_source_numeric_identity(self.rebuilt(captures)))

    def test_raw_manifest_bad_reference_mutated_content_or_catalog_fails(self):
        variants = [{k: v for k, v in self.manifest.items() if not k.startswith("_")}]
        altered = copy.deepcopy(self.manifest)
        altered["captures"]["Phoenix2"]["path"] = "untrusted.json"
        altered["_manifestSha256"] = canonical_scoring_manifest_sha256(altered)
        variants.append(altered)
        altered = copy.deepcopy(self.manifest)
        altered["_captures"]["Phoenix2"]["boards"][A]["entries"][0]["score"] += 1
        variants.append(altered)
        altered = copy.deepcopy(self.manifest)
        altered["_currentCharts"][A]["noteCount"] += 1
        variants.append(altered)
        for value in variants:
            with self.assertRaises(ValueError):
                scoring_source_numeric_identity(value)


class PreparationDescriptorTests(unittest.TestCase):
    def setUp(self):
        self.first = {"generatedAtUtc": "old", "players": [{"playerId": "test", "lastSyncedAtUtc": "old"}],
                      "scores": [{"playerId": "test", "chartId": A, "score": 990000, "recordedAt": "best-time"}]}
        self.arguments = dict(records=[{"chartId": A, "estimatedDifficulty": 20.5}], metadata={"recipe": 1},
            catalog=[chart()], policy={"rules": []}, general_arguments={"cross_level_clearing": True},
            source_numeric_hash="a" * 64, model_code="model-one", pipeline_code="pipeline-one", producer="retained-v1")

    def descriptor(self, first=None, **overrides):
        return preparation_descriptor(first if first is not None else self.first, {}, **{**self.arguments, **overrides})

    def test_descriptor_ignores_known_sync_envelope_and_can_index_reference(self):
        changed = copy.deepcopy(self.first)
        changed["generatedAtUtc"] = "new"
        changed["players"][0]["lastSyncedAtUtc"] = "new"
        descriptor = self.descriptor()
        self.assertEqual(descriptor, self.descriptor(changed))
        store = MemoryBlobStore()
        write_index(store, "preparation", descriptor, generation="captured-computation")
        self.assertEqual(read_index(store, "preparation", self.descriptor(changed))["generation"], "captured-computation")

    def test_every_actual_dependency_and_record_selection_time_remains_exact(self):
        baseline = self.descriptor()
        for field, value in (("records", []), ("metadata", {}), ("catalog", []),
                ("policy", {"rules": ["exclude"]}), ("general_arguments", {"cross_level_clearing": False}),
                ("source_numeric_hash", "b" * 64), ("model_code", "model-two"),
                ("pipeline_code", "pipeline-two"), ("producer", "retained-v2")):
            with self.subTest(field=field):
                self.assertNotEqual(baseline, self.descriptor(**{field: value}))
        for section, replacement in (("scores", []), ("players", [])):
            changed = copy.deepcopy(self.first)
            changed[section] = replacement
            self.assertNotEqual(baseline, self.descriptor(changed))
        changed = copy.deepcopy(self.first)
        changed["scores"][0]["recordedAt"] = "different-selection-time"
        self.assertNotEqual(baseline, self.descriptor(changed))


if __name__ == "__main__":
    unittest.main()
