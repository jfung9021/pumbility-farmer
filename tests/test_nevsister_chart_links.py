from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_nevsister_chart_links",
    ROOT / "scripts/build_nevsister_chart_links.py",
)
assert SPEC and SPEC.loader
catalog = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = catalog
SPEC.loader.exec_module(catalog)


class NevsisterChartLinkTests(unittest.TestCase):
    def test_normalization_removes_korean_translation_but_keeps_english_qualifier(self) -> None:
        title = "Meteo5cience (메테오 사이언스) (GADGET mix) S18"
        self.assertEqual(catalog.normalize(title), "meteo5cience gadget mix s18")

    def test_stylized_parenthesized_letter_matches_plain_title(self) -> None:
        self.assertEqual(catalog.normalize("F(R)IEND"), "friend")

    def test_explicit_difficulties_support_combined_uploads(self) -> None:
        self.assertEqual(catalog.explicit_difficulties("Song S6 & S16 / D20"), {"S6", "S16", "D20"})

    def test_short_and_full_song_variants_do_not_collapse(self) -> None:
        self.assertEqual(catalog.chart_variant("Song - SHORT CUT - S20"), "short-cut")
        self.assertEqual(catalog.chart_variant("Song - FULL SONG - D24"), "full-song")
        self.assertEqual(catalog.chart_variant("Song S20"), "normal")

    def test_committed_catalog_has_complete_valid_shape(self) -> None:
        payload = json.loads(catalog.DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["channelId"], catalog.CHANNEL_ID)
        self.assertEqual(len(payload["charts"]), 2_572)
        for chart_id, video_id in payload["charts"].items():
            self.assertRegex(chart_id, r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
            self.assertRegex(video_id, catalog.VIDEO_ID_RE)


if __name__ == "__main__":
    unittest.main()
