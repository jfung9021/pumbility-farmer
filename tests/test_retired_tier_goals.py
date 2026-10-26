"""Current refresh and public responses omit retired targets without refitting."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api_service import app
import production_tiers as tiers
from pumbility_contract import combined_tier_blob_path
from scoring_targets import PUBLIC_SCORING_GOALS, scoring_goal_policies
from tests import test_required_scoring_goals as historical_goals
from tests.test_tier_public import source
from tier_public import prepare_tier_publication, tier_browser_payload


class RetiredTierGoalTests(unittest.TestCase):
    def fixture(self):
        fixture = historical_goals.RequiredScoringGoalsTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_current_refresh_completes_without_retired_lookup_fit_or_artifact(self):
        self.assertEqual(tiers.REQUIRED_REFRESH_GOALS, ())
        fixture = self.fixture()
        progress = []
        with patch.object(tiers, "REQUIRED_REFRESH_GOALS", ()), \
                patch.object(tiers, "matching_goal_artifacts", side_effect=AssertionError("Retired cache lookup")):
            published = fixture.complete(progress=progress.append)
        fixture.fitter.assert_not_called()  # Existing approved SSS/SSS+ fits reused.
        fixture.builder.assert_not_called()
        self.assertFalse(any(value.get("stage") == "scoring-goals" for value in progress))
        self.assertEqual(published["summary"]["method"]["sssTailPolicy"]["baseSchemaVersion"], 57)
        browser = tier_browser_payload(published)
        self.assertEqual(browser["summary"]["method"]["availableScoringGoals"], list(PUBLIC_SCORING_GOALS))
        for mode in ("singles", "doubles"):
            for original, row in zip(published[mode], browser[mode]):
                for field in ("scoringVariants", "pumbilityVariants"):
                    self.assertEqual(set(row[field]), set(PUBLIC_SCORING_GOALS))
                    for goal in PUBLIC_SCORING_GOALS:
                        self.assertEqual(row[field][goal]["estimatedDifficulty"], original[field][goal]["estimatedDifficulty"])

    def test_old_independent_goal_artifacts_are_not_reattached_to_new_publication(self):
        fixture = self.fixture()
        historical = fixture.complete()  # Populate real five-goal private caches.
        before_fits = fixture.fitter.call_count
        before_artifacts = fixture.builder.call_count
        with patch.object(tiers, "REQUIRED_REFRESH_GOALS", ()), \
                patch.object(tiers, "matching_goal_artifacts", side_effect=AssertionError("Retired cache lookup")):
            current = fixture.complete("2026-10-25T03:00:00+00:00")
        self.assertEqual(fixture.fitter.call_count, before_fits)
        self.assertEqual(fixture.builder.call_count, before_artifacts)
        self.assertEqual(current["summary"]["method"]["sssTailPolicy"]["baseSchemaVersion"], 57)
        for mode in ("singles", "doubles"):
            for previous, row in zip(historical[mode], current[mode]):
                for field in ("scoringVariants", "pumbilityVariants"):
                    self.assertNotIn("ss", row[field])
                    self.assertNotIn("ssplus", row[field])
                    for goal in PUBLIC_SCORING_GOALS:
                        self.assertEqual(row[field][goal], previous[field][goal])

    def test_current_list_and_both_lazy_detail_paths_filter_historical_goals(self):
        full = source()
        method = full["summary"]["method"]
        method["availableScoringGoals"] = ["general", "ss", "ssplus", "sss", "sssplus"]
        method["scoringGoalPolicies"] = scoring_goal_policies()
        for field in ("scoringVariants", "pumbilityVariants"):
            for goal in ("ss", "ssplus"):
                full["doubles"][0][field][goal] = copy.deepcopy(full["doubles"][0][field]["sssplus"])
        compact, shards = prepare_tier_publication(full, "retired-goal-response")
        for stored in (full, compact):
            with self.subTest(storage="compact" if stored is compact else "full"):
                values = {combined_tier_blob_path(): stored, **shards}
                before = copy.deepcopy(values)

                class Store:
                    def get_json(self, path):
                        return values.get(path)

                with patch("api.tier_list.PrivateBlobStore", return_value=Store()):
                    client = TestClient(app)
                    listing = client.get("/api/tier-list?fresh=1")
                    details = client.get("/api/tier-list/chart", params={
                        "chartId": "d18", "generatedAtUtc": full["generatedAtUtc"]})
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(details.status_code, 200)
                public_method = listing.json()["summary"]["method"]
                self.assertEqual(public_method["availableScoringGoals"], list(PUBLIC_SCORING_GOALS))
                self.assertEqual([goal["key"] for goal in public_method["scoringGoalPolicies"]["goals"]], ["sssplus"])
                for field in ("scoringVariants", "pumbilityVariants"):
                    self.assertEqual(set(listing.json()["doubles"][0][field]), set(PUBLIC_SCORING_GOALS))
                    self.assertEqual(set(details.json()["chart"][field]), set(PUBLIC_SCORING_GOALS))
                    for goal in PUBLIC_SCORING_GOALS:
                        expected = full["doubles"][0][field][goal]
                        self.assertEqual(details.json()["chart"][field][goal],
                                         {key: value for key, value in expected.items() if not key.endswith("Baseline")})
                self.assertEqual(values, before)


if __name__ == "__main__":
    unittest.main()
