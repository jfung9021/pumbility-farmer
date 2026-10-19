from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from pumbility_contract import cached_player_is_fresh, with_staleness


class PlayerModelCompatibilityTests(unittest.TestCase):
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)

    def setUp(self):
        self.index = {
            "schemaVersion": 28,
            "generationKey": "new-run",
            "numericalModelIdentity": "same-serving-model",
            "modelGeneratedAtUtc": self.now.isoformat(),
            "modelComputedAtUtc": "2026-10-18T00:00:00+00:00",
            "sourceCheckedAtUtc": self.now.isoformat(),
            "players": [{"playerKey": "player", "playerInputIdentity": "same-baseline"}],
        }
        self.cached = {
            "schemaVersion": 28,
            "modelGeneration": "old-run",
            "numericalModelIdentity": "same-serving-model",
            "playerInputIdentity": "same-baseline",
            "modelGeneratedAtUtc": "2026-10-19T00:00:00+00:00",
            "modelComputedAtUtc": "2026-10-18T00:00:00+00:00",
            "playerSyncedAtUtc": self.now.isoformat(),
            "player": {"playerKey": "player", "modes": {}},
        }

    def test_unchanged_publication_reuses_cache_without_relabeling_computation(self):
        self.assertTrue(cached_player_is_fresh(self.cached, self.index, now=self.now))
        result = with_staleness(self.cached, self.index)
        self.assertFalse(result["stale"])
        self.assertEqual(result["modelGeneration"], "old-run")
        self.assertEqual(result["modelComputedAtUtc"], self.cached["modelComputedAtUtc"])
        self.assertEqual(result["currentSourceCheckedAtUtc"], self.index["sourceCheckedAtUtc"])
        self.assertEqual(result["currentModelComputedAtUtc"], self.index["modelComputedAtUtc"])
        self.assertNotIn("stale", self.cached)

    def test_changed_model_player_inputs_schema_or_roster_invalidate_even_same_run(self):
        changes = [
            {"numericalModelIdentity": "changed-serving-model"},
            {"players": [{"playerKey": "player", "playerInputIdentity": "changed-score"}]},
            {"players": []},
            {"schemaVersion": 29},
            {"numericalModelIdentity": ""},
            {"players": [{"playerKey": "player"}]},
        ]
        for change in changes:
            with self.subTest(change=change):
                index = {**self.index, "generationKey": "old-run", **change}
                self.assertFalse(cached_player_is_fresh(self.cached, index, now=self.now))
                self.assertTrue(with_staleness(self.cached, index)["stale"])

    def test_no_change_global_refresh_does_not_extend_personal_score_freshness(self):
        cached = {**self.cached, "playerSyncedAtUtc": (self.now - timedelta(minutes=2)).isoformat()}
        self.assertFalse(cached_player_is_fresh(cached, self.index, now=self.now))
        self.assertFalse(with_staleness(cached, self.index)["stale"])

    def test_legacy_generation_fallback_and_partial_migration_are_conservative(self):
        cached = copy.deepcopy(self.cached)
        index = copy.deepcopy(self.index)
        cached.pop("numericalModelIdentity")
        index.pop("numericalModelIdentity")
        self.assertTrue(with_staleness(cached, index)["stale"])
        index["generationKey"] = cached["modelGeneration"]
        self.assertTrue(cached_player_is_fresh(cached, index, now=self.now))
        index["numericalModelIdentity"] = "same-serving-model"
        self.assertTrue(with_staleness(cached, index)["stale"])

    def test_public_index_exposes_dates_without_private_computation_dependencies(self):
        from api.recommendations import get_recommendation_players
        with patch("api.recommendations._read_index", return_value=self.index):
            result = json.loads(get_recommendation_players().body)
        self.assertEqual(result["modelComputedAtUtc"], self.index["modelComputedAtUtc"])
        self.assertEqual(result["sourceCheckedAtUtc"], self.index["sourceCheckedAtUtc"])
        self.assertNotIn("numericalModelIdentity", result)
        self.assertNotIn("playerInputIdentity", result["players"][0])


if __name__ == "__main__":
    unittest.main()
