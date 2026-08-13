from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _isolated_import(module: str) -> dict[str, object]:
    source = f"""
import json
import sys
import {module}

print(json.dumps({{
    "numpy": "numpy" in sys.modules,
    "pandas": "pandas" in sys.modules,
    "analyzer": "piu_misgrade_analyzer" in sys.modules,
    "recommendations": "piu_recommendations" in sys.modules,
    "recommendationRefresh": "recommendation_refresh" in sys.modules,
    "workerTasks": "worker.tasks" in sys.modules,
}}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(completed.stdout.strip())


class ApiColdStartTests(unittest.TestCase):
    def test_api_service_does_not_import_numeric_or_worker_modules(self) -> None:
        imported = _isolated_import("api_service")

        self.assertEqual(
            imported,
            {
                "analyzer": False,
                "numpy": False,
                "pandas": False,
                "recommendationRefresh": False,
                "recommendations": False,
                "workerTasks": False,
            },
        )

    def test_read_only_routes_keep_numeric_modules_deferred(self) -> None:
        source = """
import json
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient
from api_service import app

class Store:
    def get_json(self, pathname):
        if pathname.endswith("/latest.json") and "recommendations" not in pathname:
            return {"mix": {"key": "phoenix2"}, "summary": {}}
        if pathname == "analysis/combined/latest.json":
            return {"tiers": []}
        if pathname == "analysis/recommendations/latest.json":
            return {"players": [], "method": {}}
        return None

store = Store()
with (
    patch("api.analyze.PrivateBlobStore", return_value=store),
    patch("api.tier_list.PrivateBlobStore", return_value=store),
    patch("api.recommendations.PrivateBlobStore", return_value=store),
):
    client = TestClient(app)
    statuses = [
        client.get("/api/analyze?mix=phoenix2").status_code,
        client.get("/api/tier-list").status_code,
        client.get("/api/recommendations/players").status_code,
    ]

print(json.dumps({
    "statuses": statuses,
    "numpy": "numpy" in sys.modules,
    "pandas": "pandas" in sys.modules,
    "analyzer": "piu_misgrade_analyzer" in sys.modules,
    "recommendations": "piu_recommendations" in sys.modules,
    "recommendationRefresh": "recommendation_refresh" in sys.modules,
    "workerTasks": "worker.tasks" in sys.modules,
}, sort_keys=True))
"""
        completed = subprocess.run(
            [sys.executable, "-c", source],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        result = json.loads(completed.stdout.strip())

        self.assertEqual(result["statuses"], [200, 200, 200])
        self.assertFalse(result["numpy"])
        self.assertFalse(result["pandas"])
        self.assertFalse(result["analyzer"])
        self.assertFalse(result["recommendations"])
        self.assertFalse(result["recommendationRefresh"])
        self.assertFalse(result["workerTasks"])

    def test_worker_entrypoints_register_tasks_without_eager_numeric_imports(self) -> None:
        imported = _isolated_import("worker.run")

        self.assertFalse(imported["numpy"])
        self.assertFalse(imported["pandas"])
        self.assertFalse(imported["analyzer"])
        self.assertFalse(imported["recommendations"])
        self.assertFalse(imported["recommendationRefresh"])
        self.assertTrue(imported["workerTasks"])

    def test_lightweight_contract_matches_compatibility_exports(self) -> None:
        from piu_misgrade_analyzer import SCRIPT_VERSION as analyzer_version
        from piu_recommendations import (
            combined_tier_blob_path as legacy_tier_path,
            recommendation_blob_path as legacy_index_path,
            recommendation_shard_path as legacy_shard_path,
        )
        from pumbility_contract import (
            SCRIPT_VERSION,
            combined_tier_blob_path,
            recommendation_blob_path,
            recommendation_shard_path,
        )

        self.assertEqual(SCRIPT_VERSION, analyzer_version)
        self.assertEqual(combined_tier_blob_path(), legacy_tier_path())
        self.assertEqual(recommendation_blob_path(), legacy_index_path())
        self.assertEqual(
            recommendation_shard_path("generation", 3),
            legacy_shard_path("generation", 3),
        )


if __name__ == "__main__":
    unittest.main()
