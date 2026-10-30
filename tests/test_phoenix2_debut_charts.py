from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hybrid_tiers import retain_lower_submitted_scoring
from official_cross_level_scoring import _new_row, apply_cross_level_scoring
from phoenix2_sync import sanitize_chart, sanitize_snapshot
from piu_misgrade_analyzer import AnalysisConfig, analyze_snapshot, build_web_payload, make_synthetic_snapshot
from piu_recommendations import build_combined_chart_results, build_combined_tier_payload, build_coop_chart_results
from scripts.build_phoenix2_debut_charts import build, main, render
from tests.test_hybrid_tiers import fixture as hybrid_fixture
from tests.test_official_cross_level_scoring import fixture as official_fixture
from tests.test_tier_public import source
from tier_public import compact_tier_payload, tier_browser_payload


CAPTURED_AT = "2026-10-30T05:57:27Z"
NEW_ID = "00000000-0000-4000-8000-000000000002"
OLD_ID = "00000000-0000-4000-8000-000000000001"


class DebutCatalogTests(unittest.TestCase):
    def catalog(self):
        return [{"id": NEW_ID, "mix": "Phoenix2", "originalMix": "Phoenix2", "songName": "New"},
                {"id": OLD_ID, "mix": "Phoenix2", "originalMix": "Rise", "songName": "Older"}]

    def test_uses_debut_metadata_and_is_order_independent(self):
        rows = self.catalog()
        document = build(rows, captured_at_utc=CAPTURED_AT)
        self.assertEqual(document, build(list(reversed(rows)), captured_at_utc=CAPTURED_AT))
        self.assertEqual(document["chartIds"], [NEW_ID])
        self.assertEqual(document["source"]["catalogCharts"], 2)
        self.assertEqual(len(document["source"]["catalogSha256"]), 64)
        self.assertEqual(set(document), {"schemaVersion", "source", "chartIds"})
        self.assertNotIn("songName", render(document))

    def test_rejects_missing_metadata_duplicates_and_wrong_mix(self):
        for mutation, message in ((lambda rows: rows[0].pop("originalMix"), "originalMix"),
                                  (lambda rows: rows.append(deepcopy(rows[0])), "duplicate"),
                                  (lambda rows: rows[0].update(mix="Phoenix"), "availability")):
            rows = self.catalog()
            mutation(rows)
            with self.assertRaisesRegex(ValueError, message):
                build(rows, captured_at_utc=CAPTURED_AT)

    def test_offline_generation_requires_capture_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog, output = Path(folder) / "catalog.json", Path(folder) / "result.json"
            catalog.write_text(json.dumps(self.catalog()), encoding="utf-8")
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(main(["--catalog", str(catalog), "--captured-at", CAPTURED_AT,
                                       "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")),
                             build(self.catalog(), captured_at_utc=CAPTURED_AT))
            with patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(main(["--catalog", str(catalog), "--output", str(output)]), 1)


class DebutMetadataTests(unittest.TestCase):
    def test_daily_capture_retains_optional_metadata_without_exposing_extra_fields(self):
        chart = {"id": "new", "songName": "New", "type": "Single", "level": 20,
                 "originalMix": "Phoenix2", "privateExtra": "discard"}
        cleaned = sanitize_chart(chart)
        self.assertEqual(cleaned["originalMix"], "Phoenix2")
        self.assertNotIn("privateExtra", cleaned)
        snapshot = sanitize_snapshot({"mix": "Phoenix2", "charts": [chart], "players": [], "scores": []})
        self.assertEqual(snapshot["charts"][0]["originalMix"], "Phoenix2")
        chart.pop("originalMix")
        self.assertNotIn("originalMix", sanitize_chart(chart))

    def test_analyzer_preserves_metadata_and_omits_unknown_rows(self):
        players, charts, scores, _ = make_synthetic_snapshot(players_per_folder=2)
        targets = [chart for chart in charts if chart["type"] in ("Single", "Double") and chart["level"] >= 16]
        targets[0]["originalMix"] = "Rise"
        targets[-1].pop("originalMix", None)
        results, _, summary, _ = analyze_snapshot(players, charts, scores,
                                                  AnalysisConfig(mix="phoenix1", bootstrap_samples=0))
        records = build_web_payload(results, summary)
        by_id = {row["chartId"]: row for mode in ("singles", "doubles") for row in records[mode]}
        self.assertEqual(by_id[targets[0]["id"]]["originalMix"], "Rise")
        self.assertNotIn("originalMix", by_id[targets[-1]["id"]])

    def test_combined_tiers_preserve_metadata_without_changing_ratings(self):
        charts = [{"id": f"chart-{i}", "songName": f"Chart {i}", "type": "Single",
                   "level": 16 + i % 10, "difficulty": f"S{16 + i % 10}", "noteCount": 500 + i}
                  for i in range(60)]
        scores = [{"playerId": f"p{player}", "chartId": chart["id"],
                   "pumbility": chart["level"] * 100 + i % 7 * 3 + player,
                   "score": 920000 + i * 1000 + player, "isBroken": False,
                   "plate": "RG", "recordedAt": "2026-10-01T00:00:00Z"}
                  for player in range(6) for i, chart in enumerate(charts)]
        snapshot = {"charts": charts, "scores": scores}
        old_records, _, _ = build_combined_chart_results(snapshot, snapshot)
        updated = deepcopy(snapshot)
        updated["charts"][0]["originalMix"] = "Phoenix2"
        updated["charts"][1]["originalMix"] = "Rise"
        records, _, metadata = build_combined_chart_results(snapshot, updated)
        by_id = {row["chartId"]: row for row in records}
        self.assertEqual(by_id["chart-0"]["originalMix"], "Phoenix2")
        self.assertEqual(by_id["chart-1"]["originalMix"], "Rise")
        self.assertNotIn("originalMix", by_id["chart-2"])
        self.assertEqual([{k: v for k, v in row.items() if k != "originalMix"} for row in records], old_records)
        payload = build_combined_tier_payload(records, metadata)
        self.assertEqual(next(row for row in payload["singles"] if row["chartId"] == "chart-0")["originalMix"], "Phoenix2")

    def test_coop_rows_retain_optional_metadata(self):
        charts = [{"id": "new-coop", "songName": "New", "type": "CoOp", "level": 2, "originalMix": "Phoenix2"},
                  {"id": "old-coop", "songName": "Old", "type": "CoOp", "level": 3}]
        records, _ = build_coop_chart_results({"charts": [], "scores": []}, {"charts": charts, "scores": []})
        by_id = {row["chartId"]: row for row in records}
        self.assertEqual(by_id["new-coop"]["originalMix"], "Phoenix2")
        self.assertNotIn("originalMix", by_id["old-coop"])

    def test_official_overlay_uses_current_catalog_metadata(self):
        base, merged, charts = official_fixture()
        charts[0]["originalMix"] = "Phoenix2"
        self.assertEqual(_new_row(charts[0])["originalMix"], "Phoenix2")
        self.assertNotIn("originalMix", _new_row(charts[1]))
        result = apply_cross_level_scoring(base, merged, charts)
        self.assertEqual(next(row for row in result["singles"] if row["chartId"] == charts[0]["id"])["originalMix"], "Phoenix2")

    def test_hybrid_replacement_uses_current_metadata_and_public_views_retain_it(self):
        base, _, _, _ = hybrid_fixture()
        base["schemaVersion"] = 43
        submitted = deepcopy(base)
        submitted["schemaVersion"] = 26
        submitted["summary"]["method"]["scoring"] = {"calibration": "folder-scaled-score-profile"}
        base["singles"][-1]["originalMix"] = "Phoenix2"
        submitted["singles"][-1]["originalMix"] = "Rise"
        result = retain_lower_submitted_scoring(base, submitted)
        self.assertEqual(result["singles"][-1]["originalMix"], "Phoenix2")
        payload = source()
        payload["doubles"][0]["originalMix"] = "Phoenix2"
        self.assertEqual(compact_tier_payload(payload)["doubles"][0]["originalMix"], "Phoenix2")
        self.assertEqual(tier_browser_payload(payload)["doubles"][0]["originalMix"], "Phoenix2")


if __name__ == "__main__":
    unittest.main()
