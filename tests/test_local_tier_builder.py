from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from piu_recommendations import _clean_snapshot_frames
from scripts import build_local_recommendations as builder
from tier_difficulty import percentile_skill


class LocalTierBuilderTests(unittest.TestCase):
    def test_percentile_experiment_only_writes_tiers(self) -> None:
        snapshots = {mix: {'charts': [], 'scores': []} for mix in ('phoenix1', 'phoenix2')}
        payload = {'summary': {'method': {'scoreProfileCalibration': {'actualTwoGradeCount': 12}}}}
        with tempfile.TemporaryDirectory() as temporary:
            tier_path = Path(temporary) / 'tiers.json'
            with (
                patch.object(sys, 'argv', ['build_local_recommendations.py', '--scoring-percentile']),
                patch.object(builder, '_read_snapshot', side_effect=lambda mix: snapshots[mix]),
                patch.object(builder, 'build_combined_chart_results', return_value=([], {}, {})),
                patch('scoring_percentile.build_percentile_tier_payload', return_value=payload) as build,
                patch.object(builder, 'COMBINED_OUTPUT_PATH', tier_path),
                patch.object(builder, 'build_recommendation_index', side_effect=AssertionError('Recommendations must not rebuild')),
                patch.object(builder, '_prune_unpublished_generations', side_effect=AssertionError('Recommendations must not be pruned')),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(builder.main(), 0)
            self.assertEqual(json.loads(tier_path.read_text()), payload)
            build.assert_called_once_with([], {}, snapshots['phoenix1'], snapshots['phoenix2'])

    def test_combined_tiers_only_preserves_the_recommendation_generation(self) -> None:
        phoenix1 = {"charts": [], "scores": []}
        phoenix2 = {"charts": [], "scores": []}
        combined = [{"chartId": "legacy"}, {"chartId": "new"}]
        metadata = {"combined": True}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_path = root / "latest.json"
            index_path.write_bytes(b'{"generationKey":"combined-original"}')
            tier_path = root / "tiers.json"
            with (
                patch.object(sys, "argv", ["build_local_recommendations.py", "--tiers-only"]),
                patch.object(builder, "_read_snapshot", side_effect=lambda mix: phoenix1 if mix == "phoenix1" else phoenix2) as read,
                patch.object(builder, "build_combined_chart_results", return_value=(combined, {}, metadata)) as analyze,
                patch.object(builder, "build_production_tier_payload", return_value={"singles": combined}) as payload_builder,
                patch.object(builder, "OUTPUT_PATH", index_path),
                patch.object(builder, "COMBINED_OUTPUT_PATH", tier_path),
                patch.object(builder, "build_recommendation_index", side_effect=AssertionError("Recommendations must not rebuild")),
                patch.object(builder, "_prune_unpublished_generations", side_effect=AssertionError("Recommendations must not be pruned")),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(builder.main(), 0)
            self.assertEqual([call.args[0] for call in read.call_args_list], ["phoenix1", "phoenix2"])
            self.assertEqual(index_path.read_bytes(), b'{"generationKey":"combined-original"}')
            payload = json.loads(tier_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["singles"], combined)
            analyze.assert_called_once_with(phoenix1, phoenix2)
            payload_builder.assert_called_once_with(combined, metadata, phoenix1, phoenix2)

    def test_phoenix2_only_rebuild_isolates_tiers_and_preserves_recommendations(self) -> None:
        charts, scores = [], []
        for chart_type, prefix in (("Single", "S"), ("Double", "D")):
            for index in range(60):
                chart_id = f"{prefix}-{index}"
                charts.append({
                    "id": chart_id, "songName": chart_id, "type": chart_type,
                    "level": 20 + index // 20, "difficulty": f"{prefix}{20 + index // 20}",
                    "noteCount": 1000,
                })
                for player in range(6):
                    # This player clears the chart but lacks 50 unique clears,
                    # so must be excluded from its clearing skill sample.
                    if player == 0 and index >= 3:
                        continue
                    scores.append({
                        "playerId": f"player-{player}", "chartId": chart_id,
                        "pumbility": 1000 + player * 200 + index * 5,
                        "score": 930_000 + player * 1000 + index * 100,
                        "plate": "RG", "isBroken": False,
                        "recordedAt": "2026-09-25T00:00:00Z",
                    })
        charts.append({
            "id": "coop", "songName": "Co-op", "type": "CoOp",
            "level": 2, "difficulty": "2x", "noteCount": 1000,
        })
        scores.extend({
            "playerId": f"player-{player}", "chartId": "coop",
            "pumbility": 500, "score": 950_000 + player * 1000,
            "plate": "RG", "isBroken": False,
        } for player in range(6))
        snapshot = {"mix": "Phoenix2", "players": [], "charts": charts, "scores": scores}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_path = root / "recommendations" / "latest.json"
            generation_path = root / "recommendations" / "generations"
            old_shard = generation_path / "old" / "shards" / "0000.json"
            old_shard.parent.mkdir(parents=True)
            old_shard.write_bytes(b'{"combinedRecommendation":"preserve"}')
            index_path.write_bytes(b'{"generationKey":"old"}')
            tier_path = root / "combined" / "web_results.json"
            with (
                patch.object(sys, "argv", ["build_local_recommendations.py", "--phoenix2-only"]),
                patch.object(builder, "_read_snapshot", return_value=snapshot) as read,
                patch.object(builder, "OUTPUT_PATH", index_path),
                patch.object(builder, "GENERATIONS_PATH", generation_path),
                patch.object(builder, "COMBINED_OUTPUT_PATH", tier_path),
                patch.object(builder, "build_recommendation_index", side_effect=AssertionError("Recommendations must not rebuild")),
                patch.object(builder, "_prune_unpublished_generations", side_effect=AssertionError("Recommendations must not be pruned")),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(builder.main(), 0)
            read.assert_called_once_with("phoenix2")
            self.assertEqual(index_path.read_bytes(), b'{"generationKey":"old"}')
            self.assertEqual(old_shard.read_bytes(), b'{"combinedRecommendation":"preserve"}')
            self.assertEqual(list(generation_path.iterdir()), [generation_path / "old"])
            payload = json.loads(tier_path.read_text(encoding="utf-8"))

        method = payload["summary"]["method"]
        self.assertEqual(method["sourceSelection"], "phoenix2-only")
        self.assertEqual(method["tierMetrics"]["clearing"]["percentile"], 0.1)
        for mode in ("singles", "doubles", "coop"):
            self.assertTrue(payload[mode])
            self.assertTrue(all(chart["phoenix1Contributors"] == 0 for chart in payload[mode]))
            self.assertGreater(sum(chart["phoenix2Contributors"] for chart in payload[mode]), 0, mode)
        for mode, chart_type in (("singles", "Single"), ("doubles", "Double")):
            chart = next(row for row in payload[mode] if row["chartId"].endswith("-0"))
            clearing = chart["tierMetrics"]["clearing"]
            self.assertEqual(clearing["clearCount"], 6)
            self.assertEqual(clearing["ratedClearCount"], 5)
            self.assertEqual(clearing["missingSkillCount"], 1)
            skills = [21.2] * 5
            expected = percentile_skill(skills)
            self.assertNotIn("selectedCount", clearing)
            self.assertAlmostEqual(clearing["q10Skill"], expected, places=5)
            self.assertAlmostEqual(
                chart["tierMetrics"]["pumbility"]["estimatedDifficulty"],
                (chart["estimatedDifficulty"] + clearing["estimatedDifficulty"]) / 2,
                places=5,
            )

    def test_empty_source_is_explicit_and_malformed_snapshots_still_fail(self) -> None:
        catalog, scores = _clean_snapshot_frames({"charts": [], "scores": []})
        self.assertTrue(catalog.empty)
        self.assertTrue(scores.empty)
        for snapshot in ({}, {"charts": []}, {"charts": [], "scores": [{}]}):
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                _clean_snapshot_frames(snapshot)


if __name__ == "__main__":
    unittest.main()
