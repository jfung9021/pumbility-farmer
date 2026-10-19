from __future__ import annotations

import copy
import json
import os
import unittest
from unittest.mock import patch

from cross_level_clearing import apply_cross_level_clearing, prepare_clearing_evidence
from cross_level_clearing_calibration import clearing_difficulty, fit_clearing_curve
from player_skill_ratings import clearing_tier_skill_for_levels
from pumbility_contract import CLEARING_TIER_MINIMUM_SCORE_RECORDS, cross_level_clearing_method_identity


def fixture():
    charts, scores = [], []
    base = {"schemaVersion": 42, "summary": {"method": {"scoring": {"sentinel": .123456789}, "tierMetrics": {"version": 10}}},
            "singles": [], "doubles": [], "coop": [{"chartId": "coop", "estimatedDifficulty": 16.25}]}
    for mode, key in (("Single", "singles"), ("Double", "doubles")):
        for group in range(3):
            history_ids = [f"{mode}-history-{group}-{i}" for i in range(200)]
            target_ids = [f"{mode}-target-{group}-{i}" for i in range(5)]
            for cid in history_ids:
                charts.append({"id": cid, "type": mode, "level": 16 + 2 * group, "noteCount": 100})
            for cid in target_ids:
                charts.append({"id": cid, "type": mode, "level": 16 + group, "noteCount": 100})
                base[key].append({"chartId": cid, "type": mode, "level": 16 + group, "songName": cid,
                    "estimatedDifficulty": 17.123456789, "nContributors": 33, "evidenceStatus": "Published",
                    "difficultyCi95Low": 16.987654321, "tierMetrics": {"clearing": {"estimatedDifficulty": 77},
                    "pumbility": {"estimatedDifficulty": 77}}})
            for p in range(20):
                for cid in history_ids + target_ids:
                    scores.append({"playerId": f"private:{mode}:{group}:{p}", "chartId": cid,
                                   "pumbility": 0, "score": 1, "isBroken": False})
    first = {"mix": "Phoenix", "generatedAtUtc": "2026-09-25T00:00:00Z", "charts": charts, "scores": scores}
    second = {"mix": "Phoenix2", "generatedAtUtc": "2026-10-02T00:00:00Z", "charts": charts, "scores": scores[::2]}
    return base, first, second, charts


class ClearingSkillTests(unittest.TestCase):
    def test_exact_100_gate_top30_and_legacy_default(self):
        levels = {str(i): float(i) for i in range(1, 202)}
        self.assertEqual(CLEARING_TIER_MINIMUM_SCORE_RECORDS, 100)
        self.assertIsNone(clearing_tier_skill_for_levels(levels, record_count=99))
        self.assertEqual(clearing_tier_skill_for_levels(levels, record_count=100), sum(range(172, 202)) / 30)
        self.assertEqual(clearing_tier_skill_for_levels(levels, record_count=200), sum(range(172, 202)) / 30)
        self.assertIsNone(clearing_tier_skill_for_levels({str(i): 20 for i in range(29)}, record_count=100))
        self.assertEqual(clearing_tier_skill_for_levels({str(i): 20 for i in range(30)}, record_count=100), 20)
        method = cross_level_clearing_method_identity()["skillMethod"]
        self.assertEqual(method["minimumScoreRecords"], CLEARING_TIER_MINIMUM_SCORE_RECORDS)
        self.assertEqual(method["version"], 2)
        from player_skill_ratings import clearing_skill_method
        self.assertEqual(clearing_skill_method()["requiredClearCount"], 50)

    def test_dedup_zero_pumbility_below16_mode_and_invalid_rows(self):
        charts = [{"id": str(i), "type": "Single", "level": 10, "noteCount": 100} for i in range(200)]
        scores = [{"playerId": "p", "chartId": str(i), "pumbility": 0, "isBroken": False} for i in range(200)]
        source = {"charts": charts, "scores": scores}
        data = prepare_clearing_evidence(source, source, charts)
        self.assertEqual(data["diagnostics"]["Single"]["qualifiedPlayers"], 1)
        self.assertEqual(data["charts"]["0"]["ratedClearCount"], 1)
        self.assertEqual(data["charts"]["0"]["clearCount"], 1)
        short = {"charts": charts, "scores": scores[:99] + [dict(scores[99], isBroken=True)]}
        data = prepare_clearing_evidence(short, short, charts)
        self.assertEqual(data["charts"]["0"]["ratedClearCount"], 0)
        self.assertEqual(data["charts"]["0"]["qualificationExclusions"], {"recordRequirement": 1})
        wrong_mode = {"charts": [{**c, "type": "Double"} for c in charts], "scores": scores}
        data = prepare_clearing_evidence(wrong_mode, {"charts": [], "scores": []}, charts)
        self.assertEqual(data["diagnostics"]["phoenix1"]["sourceModeMismatch"], 200)
        self.assertEqual(data["charts"]["0"]["clearCount"], 0)

    def test_full_history_boundary_dedup_modes_and_exclusions_share_the_100_gate(self):
        from chart_source_exclusions import ExclusionPolicy

        singles = [{"id": f"s{i}", "type": "Single", "level": 10 if i < 70 else 22,
                    "noteCount": 100} for i in range(100)]
        doubles = [{"id": f"d{i}", "type": "Double", "level": 18,
                    "noteCount": 100} for i in range(100)]
        excluded_id = "00000000-0000-0000-0000-000000000001"
        extras = [{"id": cid, "type": "Single", "level": 25, "noteCount": 100}
                  for cid in (excluded_id, "wrong-mode", "invalid-score")]
        charts = singles + doubles + extras
        # Old source levels must not replace the current official top-30 levels.
        source_charts = [{**chart, "level": 30,
                          "type": "Double" if chart["id"] == "wrong-mode" else chart["type"]}
                         for chart in charts]

        def cleared(cid, **fields):
            return {"playerId": "p", "chartId": cid, "pumbility": 0,
                    "isBroken": False, **fields}

        first = {"charts": source_charts, "scores":
                 [cleared(f"s{i}") for i in range(60)]
                 + [cleared("s0")] * 5
                 + [cleared(chart["id"]) for chart in doubles]
                 + [cleared("s99", isBroken=True), cleared(excluded_id), cleared("wrong-mode"),
                    {"playerId": "p", "chartId": "invalid-score"}]}
        second = {"charts": source_charts, "scores":
                  [cleared(f"s{i}") for i in range(40, 99)]
                  + [cleared("s0"), cleared("s99", isBroken=True)]}
        policy = ExclusionPolicy({"version": 1, "rules": [{"chartId": excluded_id,
            "mode": "Single", "enabled": True, "scope": "tier-analysis-performance",
            "reason": "test source exclusion", "label": "test", "source": "Phoenix"}]}, charts)

        data = prepare_clearing_evidence(first, second, charts, exclusions=policy)
        # Many duplicate records and 100 valid Doubles cannot qualify 99 Singles.
        self.assertEqual(data["diagnostics"]["Single"], {"recordRequirement": 1, "observedPlayers": 1})
        self.assertEqual(data["charts"]["s0"]["qualificationExclusions"], {"recordRequirement": 1})
        self.assertEqual(data["charts"]["s0"]["ratedClearCount"], 0)
        self.assertEqual(data["charts"]["s0"]["clearCount"], 1)
        self.assertEqual(data["charts"]["s0"]["sourceCounts"], {"phoenix1": 1, "phoenix2": 1})
        self.assertEqual(data["diagnostics"]["Double"]["qualifiedPlayers"], 1)
        self.assertEqual(data["diagnostics"]["phoenix1"]["chartSourceExcluded"], 1)
        self.assertEqual(data["diagnostics"]["phoenix1"]["sourceModeMismatch"], 1)
        self.assertEqual(data["diagnostics"]["phoenix1"]["invalidOrBroken"], 2)

        second["scores"].append(cleared("s99"))
        qualified = prepare_clearing_evidence(first, second, charts, exclusions=policy)
        self.assertEqual(qualified["diagnostics"]["Single"], {"qualifiedPlayers": 1, "observedPlayers": 1})
        self.assertEqual(qualified["charts"]["s0"]["qualificationExclusions"], {})
        self.assertEqual(qualified["charts"]["s0"]["ratedClearCount"], 1)
        self.assertEqual(qualified["charts"]["s0"]["q10Skill"], 22)
        self.assertEqual(qualified["charts"]["d0"]["q10Skill"], 18)


class ClearingCurveTests(unittest.TestCase):
    def anchors(self):
        return [{"level": level, "threshold": threshold, "referenceCharts": 5, "chartIds": list("abcde")}
                for level, threshold in ((16, 17.25), (18, 21.5), (20, 23.75))]

    def test_exact_anchors_global_interpolation_and_tails(self):
        curve = fit_clearing_curve(self.anchors())
        for anchor in curve["anchors"]:
            self.assertEqual(clearing_difficulty(anchor["threshold"], curve), anchor["level"] + .5)
        self.assertEqual(clearing_difficulty((17.25 + 21.5) / 2, curve), 17.5)
        self.assertLess(clearing_difficulty(0, curve), 16.5)
        self.assertGreater(clearing_difficulty(40, curve), 20.5)
        for invalid in (self.anchors()[:2], [dict(a, threshold=10) for a in self.anchors()],
                        [dict(a, threshold=-a["threshold"]) for a in self.anchors()]):
            with self.assertRaises(ValueError):
                fit_clearing_curve(invalid)


class ClearingOverlayTests(unittest.TestCase):
    def test_scoring_coop_inputs_and_full_precision_composite(self):
        base, first, second, charts = fixture()
        original = copy.deepcopy((base, first, second, charts))
        result = apply_cross_level_clearing(base, first, second, charts)
        self.assertEqual((base, first, second, charts), original)
        self.assertEqual(result["schemaVersion"], 43)
        self.assertEqual(result["coop"], base["coop"])
        self.assertEqual(result["summary"]["method"]["scoring"], base["summary"]["method"]["scoring"])
        for key in ("singles", "doubles"):
            for row, old in zip(result[key], base[key]):
                self.assertEqual({k: v for k, v in row.items() if k != "tierMetrics"},
                                 {k: v for k, v in old.items() if k != "tierMetrics"})
                clearing = row["tierMetrics"]["clearing"]
                self.assertEqual(clearing["ratedClearCount"], 20)
                self.assertEqual(clearing["estimatedDifficulty"], row["level"] + .5)
                self.assertEqual(clearing["evidenceStatus"], "Published")
                self.assertIsNone(clearing["difficultyCi95Low"])
                self.assertEqual(row["tierMetrics"]["pumbility"]["estimatedDifficulty"],
                                 max(row["estimatedDifficulty"], clearing["estimatedDifficulty"]))
        self.assertNotIn("private:", json.dumps(result))
        self.assertEqual(result["summary"]["method"]["tierMetrics"]["clearing"], cross_level_clearing_method_identity())
        self.assertEqual(result["summary"]["method"]["crossLevelClearing"]["sourceDates"],
                         {"phoenix1": first["generatedAtUtc"], "phoenix2": second["generatedAtUtc"]})

    def test_sparse_unknown_metadata_no_evidence_and_unsupported_folder(self):
        base, first, second, charts = fixture()
        for name, count, note in (("sparse", 4, 100), ("five", 5, 100), ("unknown", 20, None), ("new", 0, 100)):
            chart = {"id": name, "type": "Single", "level": 22, "noteCount": note}
            charts.append(chart)
            base["singles"].append({"chartId": name, "level": 22, "type": "Single", "estimatedDifficulty": None,
                                    "evidenceStatus": "Unrated", "nContributors": 0})
            first["scores"].extend({"playerId": f"private:Single:2:{p}", "chartId": name, "pumbility": 0,
                                    "isBroken": False} for p in range(count))
        rows = {row["chartId"]: row for row in apply_cross_level_clearing(base, first, second, charts)["singles"]}
        self.assertEqual(rows["sparse"]["tierMetrics"]["clearing"]["evidenceStatus"], "Insufficient")
        self.assertEqual(rows["five"]["tierMetrics"]["clearing"]["evidenceStatus"], "Provisional")
        unknown = rows["unknown"]["tierMetrics"]["clearing"]
        self.assertFalse(unknown["anchorEligible"])
        self.assertIn("chart-comparability-unknown", unknown["provisionalReasons"])
        self.assertIsNone(rows["new"]["tierMetrics"]["clearing"]["estimatedDifficulty"])
        self.assertIsNone(rows["sparse"]["tierMetrics"]["pumbility"]["estimatedDifficulty"])


class ClearingRuntimeTests(unittest.TestCase):
    def test_typed_checkpoint_binds_clearing_to_the_same_source_snapshots(self):
        from analysis_runtime import _load_typed_checkpoint_combined, _write_typed_checkpoint_combined
        from mix_registry import resolve_mix
        from pumbility_contract import scoring_tier_method_identity
        class Store:
            def __init__(self):
                self.values = {}
            def put_json(self, path, value):
                self.values[path] = copy.deepcopy(value)
            def get_json(self, path):
                return copy.deepcopy(self.values.get(path))
        source = {"charts": [], "scores": []}
        base = {"schemaVersion": 26, "singles": [], "doubles": [], "coop": [],
                "summary": {"method": {"scoring": scoring_tier_method_identity()}}}
        candidate = apply_cross_level_clearing(base, source, source, [])
        hashes = {mix: candidate["summary"]["method"]["crossLevelClearing"]["inputHashes"][mix]
                  for mix in ("phoenix1", "phoenix2")}
        flags = {"PIU_CROSS_LEVEL_CLEARING": "1", "PIU_OFFICIAL_CROSS_LEVEL_SCORING": "0", "PIU_OFFICIAL_HIGH_LEVEL_TIERS": "0"}
        with patch.dict(os.environ, flags):
            for correct in (True, False):
                store = Store()
                ref = _write_typed_checkpoint_combined(store, job_id="clearing", mix_spec=resolve_mix("phoenix2"),
                    generated_at_utc="2026-10-17T00:00:00Z", combined_tier=candidate, model_charts=[],
                    phoenix2_slopes={}, source_hashes=hashes,
                    snapshot_hashes=hashes if correct else {**hashes, "phoenix2": "f" * 64}, input_sha256="a" * 64)
                kwargs = dict(checkpoint={"jobId": "clearing"}, reference=ref, mix_spec=resolve_mix("phoenix2"))
                if correct:
                    self.assertEqual(_load_typed_checkpoint_combined(store, **kwargs)["combinedTier"], candidate)
                else:
                    with self.assertRaisesRegex(ValueError, "Clearing inputs"):
                        _load_typed_checkpoint_combined(store, **kwargs)

    def test_independent_opt_in_policy_and_checkpoint_invalidation(self):
        from analysis_runtime import _official_tier_arguments, _validate_checkpoint_combined_tier
        from pumbility_contract import scoring_tier_method_identity
        source = {"charts": [], "scores": []}
        base = {"schemaVersion": 26, "singles": [], "doubles": [], "coop": [],
                "summary": {"method": {"scoring": scoring_tier_method_identity()}}}
        candidate = apply_cross_level_clearing(base, source, source, [])
        flags = {"PIU_CROSS_LEVEL_CLEARING": "1", "PIU_OFFICIAL_CROSS_LEVEL_SCORING": "0", "PIU_OFFICIAL_HIGH_LEVEL_TIERS": "0"}
        with patch.dict(os.environ, flags):
            self.assertEqual(_official_tier_arguments(None), {"cross_level_clearing": True})
            _validate_checkpoint_combined_tier(candidate)
            altered = copy.deepcopy(candidate)
            altered["summary"]["method"]["crossLevelClearing"]["skillMethod"]["minimumScoreRecords"] = 199
            with self.assertRaisesRegex(ValueError, "Clearing checkpoint"):
                _validate_checkpoint_combined_tier(altered)
            legacy = copy.deepcopy(candidate)
            legacy["summary"]["method"]["crossLevelClearing"]["skillMethod"].update(
                version=1, minimumScoreRecords=200)
            with self.assertRaisesRegex(ValueError, "Clearing checkpoint"):
                _validate_checkpoint_combined_tier(legacy)
            with self.assertRaisesRegex(ValueError, "incompatible schema"):
                _validate_checkpoint_combined_tier(base)
        with patch.dict(os.environ, {**flags, "PIU_CROSS_LEVEL_CLEARING": "0"}):
            with self.assertRaisesRegex(ValueError, "incompatible schema"):
                _validate_checkpoint_combined_tier(candidate)


if __name__ == "__main__":
    unittest.main()
