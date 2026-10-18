"""Read back a published schema-57 list and its lazy chart evidence."""
from __future__ import annotations

import argparse
import math
import statistics
import time

import requests


def verify(base_url: str) -> dict:
    started = time.perf_counter()
    response = requests.get(base_url.rstrip("/") + "/api/tier-list", timeout=120)
    response.raise_for_status()
    payload = response.json()
    assert payload["schemaVersion"] == 57, "Expected schema 57"
    assert len(response.content) <= 12_000_000, "Expanded list exceeds 12 MB"
    method = payload["summary"]["method"]
    publication = method["productionTierPublication"]
    assert publication["scoreFetches"] == publication["recommendationFits"] == 0
    assert method["submittedSssplusCalibration"]["localOnly"] is False
    folders = {}
    examples = []
    for level, count in ((16, 91), (17, 115), (18, 186), (19, 137)):
        rows = [row for row in payload["doubles"] if row["level"] == level]
        rated = [row for row in rows if row["scoringVariants"]["sssplus"]["estimatedDifficulty"] is not None]
        assert len(rated) == count, (level, len(rated))
        median = statistics.median(row["scoringVariants"]["sssplus"]["estimatedDifficulty"] for row in rated)
        assert math.isclose(median, level + .5, abs_tol=1e-8), (level, median)
        assert all(row["scoringVariants"]["sssplus"]["scoringSpreadPolicy"]["midpoint"] == level + .5 for row in rated)
        folders[str(level)] = {"rated": count, "median": median}
        examples.append(rated[0])
    for row in payload["singles"] + payload["doubles"]:
        clear = row["tierMetrics"]["clearing"]["estimatedDifficulty"]
        for goal in ("general", "sss", "sssplus"):
            scoring = row["scoringVariants"][goal]["estimatedDifficulty"]
            expected = None if clear is None or scoring is None else (clear + scoring) / 2
            assert row["pumbilityVariants"][goal]["estimatedDifficulty"] == expected, row["chartId"]
    examples.extend(next(row for row in payload[mode] if row["level"] == level)
                    for mode, level in (("singles", 16), ("doubles", 26)))
    for row in examples:
        detail = requests.get(base_url.rstrip("/") + "/api/tier-list/chart",
                              params={"chartId": row["chartId"], "generatedAtUtc": payload["generatedAtUtc"]},
                              timeout=60)
        detail.raise_for_status()
        chart = detail.json()["chart"]
        for goal in ("general", "sss", "sssplus"):
            for field in (("estimatedDifficulty",) if goal == "general" else ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High")):
                assert chart["scoringVariants"][goal].get(field) == row["scoringVariants"][goal].get(field)
        assert chart["chartId"] == row["chartId"]
    return {"schemaVersion": 57, "generatedAtUtc": payload["generatedAtUtc"],
            "responseBytes": len(response.content), "seconds": round(time.perf_counter()-started, 2),
            "folders": folders, "pumbilityMeansVerified": True, "chartDetailsVerified": len(examples),
            "publication": publication}


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://pumbility-farmer.vercel.app")
    args = parser.parse_args()
    print(json.dumps(verify(args.base_url)), flush=True)
