"""Cross-level source opt-in and checkpoint integration contracts."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analysis_runtime import _official_tier_arguments, _validate_checkpoint_combined_tier
from official_score_merge import merge_official_scores
from official_scoring_sources import canonical_scoring_manifest_sha256, import_research_scoring_sources
from pumbility_contract import CROSS_LEVEL_TIER_SCHEMA_VERSION, official_scoring_manifest_path
from tier_difficulty import tier_metric_method
from tests.test_official_scoring_sources import research


class BlobFixture:
    def __init__(self):
        self.values = {}

    def get_json(self, path):
        return copy.deepcopy(self.values.get(path))


def checkpoint(manifest):
    return {"schemaVersion": CROSS_LEVEL_TIER_SCHEMA_VERSION,
            "singles": [], "doubles": [], "coop": [],
            "summary": {"method": {
                "scoring": {"version": 1, "population": "official-max-per-player-chart",
                            "calibration": "official-cross-level-median-anchored-exponential",
                            "minimumOfficialLevel": 20},
                "tierMetrics": tier_metric_method({}),
                "officialCrossLevelScoring": {
                    "version": 1, "source": "piuscores-official", "minimumOfficialLevel": 20,
                    "mergePolicy": "maximum-raw-score", "minimumOtherCharts": 3,
                    "modelVersion": 1, "calibrationVersion": 1, "bootstrapSamples": 1000,
                    "intervalMethod": "conditional-player-bootstrap-fixed-fit-v1",
                    "manifestSha256": canonical_scoring_manifest_sha256(manifest),
                    "sourceDates": {"Phoenix": "2026-09-06T00:00:00Z", "Phoenix2": "2026-10-11T00:00:00Z"},
                    "calibration": {"Single": None, "Double": None},
                },
            }}}


class CrossLevelRuntimeTests(unittest.TestCase):
    def staged(self, directory):
        path = Path(directory)
        research(path / "research")
        output = path / "private/manifest.json"
        loaded = import_research_scoring_sources(path / "research", output)
        raw = json.loads(output.read_text(encoding="utf-8"))
        blobs = BlobFixture()
        blobs.values[official_scoring_manifest_path()] = raw
        prefix = official_scoring_manifest_path().rsplit("/", 1)[0]
        for ref in raw["captures"].values():
            blobs.values[f"{prefix}/{ref['path']}"] = json.loads((output.parent / ref["path"]).read_text(encoding="utf-8"))
        return blobs, loaded

    def test_hosted_opt_in_hydrates_private_references_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            blobs, manifest = self.staged(directory)
            with patch.dict(os.environ, {"PIU_OFFICIAL_HIGH_LEVEL_TIERS": "0", "PIU_OFFICIAL_CROSS_LEVEL_SCORING": "0"}):
                self.assertEqual(_official_tier_arguments(blobs), {})
            with patch.dict(os.environ, {"PIU_OFFICIAL_HIGH_LEVEL_TIERS": "0", "PIU_OFFICIAL_CROSS_LEVEL_SCORING": "1"}):
                result = _official_tier_arguments(blobs)
                self.assertEqual(merge_official_scores(result["official_scoring_manifest"])["boards"],
                                 merge_official_scores(manifest)["boards"])
                blobs.values.pop(official_scoring_manifest_path())
                with self.assertRaisesRegex(RuntimeError, "manifest is unavailable"):
                    _official_tier_arguments(blobs)

    def test_checkpoint_rejects_source_changes_method_changes_and_disabled_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            blobs, manifest = self.staged(directory)
            candidate = checkpoint(manifest)
            with patch.dict(os.environ, {"PIU_OFFICIAL_HIGH_LEVEL_TIERS": "0", "PIU_OFFICIAL_CROSS_LEVEL_SCORING": "1"}):
                _validate_checkpoint_combined_tier(candidate, blobs)
                altered = copy.deepcopy(candidate)
                altered["summary"]["method"]["officialCrossLevelScoring"]["mergePolicy"] = "phoenix2-precedence"
                with self.assertRaisesRegex(ValueError, "source or method is incompatible"):
                    _validate_checkpoint_combined_tier(altered, blobs)
                altered = copy.deepcopy(candidate)
                altered["summary"]["method"]["scoring"]["minimumOfficialLevel"] = 19
                with self.assertRaisesRegex(ValueError, "incompatible scoring or clearing"):
                    _validate_checkpoint_combined_tier(altered, blobs)
                blobs.values[official_scoring_manifest_path()]["publicationTag"] = "changed-source-contract"
                with self.assertRaisesRegex(ValueError, "manifest changed"):
                    _validate_checkpoint_combined_tier(candidate, blobs)
            with patch.dict(os.environ, {"PIU_OFFICIAL_HIGH_LEVEL_TIERS": "0", "PIU_OFFICIAL_CROSS_LEVEL_SCORING": "0"}):
                with self.assertRaisesRegex(ValueError, "incompatible schema"):
                    _validate_checkpoint_combined_tier(candidate, blobs)


if __name__ == "__main__":
    unittest.main()
