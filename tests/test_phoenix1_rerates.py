from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

from scripts.import_phoenix2_rerates import _rating_pairs


ROOT = Path(__file__).resolve().parents[1]


class Phoenix1RerateTests(unittest.TestCase):
    def test_checked_in_rerates_match_the_frozen_archive(self) -> None:
        archive = json.loads(
            (ROOT / "public/data/phoenix1-20260807.json").read_text(encoding="utf-8")
        )
        source = json.loads(
            (ROOT / "public/data/phoenix1-rerates-20260807.json").read_text(
                encoding="utf-8"
            )
        )
        charts = {
            chart["chartId"]: chart
            for chart in [*archive["singles"], *archive["doubles"]]
        }
        self.assertEqual(len(source["rerates"]), 152)
        self.assertEqual(
            Counter(row["direction"] for row in source["rerates"]),
            {"uprated": 118, "downrated": 34},
        )
        self.assertEqual(len({row["chartId"] for row in source["rerates"]}), 152)
        for row in source["rerates"]:
            self.assertIn(row["chartId"], charts)
            self.assertEqual(row["from"], charts[row["chartId"]]["difficulty"])
            self.assertEqual(row["direction"], "uprated" if row["delta"] > 0 else "downrated")

    def test_multi_chart_spreadsheet_rows_expand_in_order(self) -> None:
        row = {
            "fromText": "D20 / D21",
            "toText": "D21 / D22",
        }
        self.assertEqual(
            _rating_pairs(row),
            [("D20", "D21", 1), ("D21", "D22", 1)],
        )


if __name__ == "__main__":
    unittest.main()
