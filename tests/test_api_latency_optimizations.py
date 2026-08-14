from __future__ import annotations

import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import analysis_runtime
from analysis_runtime import (
    MemoryBlobStore,
    VercelPrivateBlobStore,
    latest_blob_path,
    read_latest_payload,
)
from api_service import app
from pumbility_contract import combined_tier_blob_path


API_CLIENT = TestClient(app)


class LargeJsonResponseTests(unittest.TestCase):
    def test_analysis_response_matches_fastapi_dict_contract_exactly(self) -> None:
        blobs = MemoryBlobStore()
        stored = {
            "generatedAtUtc": "2026-08-14T00:00:00Z",
            "mix": {
                "key": "phoenix2",
                "apiValue": "Phoenix2",
                "label": "Phoenix 2",
            },
            "summary": {"modes": {}, "note": "\u901f\u5ea6 \u2713"},
            "singles": [
                {
                    "chartId": "chart-1",
                    "score": 1234.5,
                    "eligible": True,
                    "optional": None,
                }
            ],
            "doubles": [],
            "relativeGroups": [],
            "effectBands": [],
        }
        blobs.put_json(latest_blob_path(), stored)
        expected_payload = read_latest_payload(blobs)
        expected = JSONResponse(content=jsonable_encoder(expected_payload))

        with patch("api.analyze.PrivateBlobStore", return_value=blobs):
            response = API_CLIENT.get("/api/analyze?mix=phoenix2")

        self.assertEqual(response.status_code, expected.status_code)
        self.assertEqual(response.headers["content-type"], expected.media_type)
        self.assertEqual(response.content, expected.body)

    def test_tier_response_matches_fastapi_dict_contract_exactly(self) -> None:
        blobs = MemoryBlobStore()
        payload = {
            "mix": {"key": "combined", "label": "Phoenix"},
            "tiers": [
                {
                    "name": "S",
                    "charts": ["\u901f\u5ea6 \u2713", "chart-2"],
                    "weight": 0.75,
                    "published": True,
                    "note": None,
                }
            ],
        }
        blobs.put_json(combined_tier_blob_path(), payload)
        expected = JSONResponse(content=jsonable_encoder(payload))

        with patch("api.tier_list.PrivateBlobStore", return_value=blobs):
            response = API_CLIENT.get("/api/tier-list")

        self.assertEqual(response.status_code, expected.status_code)
        self.assertEqual(response.headers["content-type"], expected.media_type)
        self.assertEqual(response.content, expected.body)


class ConcurrentBlobClient:
    instances: list[ConcurrentBlobClient] = []
    calls: list[tuple[str, str, bool]] = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    def __init__(self, *, token: str) -> None:
        self.token = token
        self._closed = False
        self.instances.append(self)

    def get(self, pathname: str, *, access: str, use_cache: bool):
        with self.lock:
            self.calls.append((pathname, access, use_cache))
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            time.sleep(0.02)
            return SimpleNamespace(
                content=json.dumps({"pathname": pathname}).encode("utf-8")
            )
        finally:
            with self.lock:
                type(self).active -= 1

    def close(self) -> None:
        self._closed = True


class StaleBlobClient:
    instances: list[StaleBlobClient] = []
    calls: list[tuple[str, str, bool]] = []

    def __init__(self, *, token: str) -> None:
        self.token = token
        self._closed = False
        self.sequence = len(self.instances)
        self.instances.append(self)

    def get(self, pathname: str, *, access: str, use_cache: bool):
        self.calls.append((pathname, access, use_cache))
        if self.sequence == 0:
            self._closed = True
            raise RuntimeError("Client is closed")
        return SimpleNamespace(content=b'{"recovered":true}')

    def close(self) -> None:
        self._closed = True


class BlobClientReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        analysis_runtime._close_cached_blob_clients()
        ConcurrentBlobClient.instances = []
        ConcurrentBlobClient.calls = []
        ConcurrentBlobClient.active = 0
        ConcurrentBlobClient.max_active = 0
        StaleBlobClient.instances = []
        StaleBlobClient.calls = []

    def tearDown(self) -> None:
        analysis_runtime._close_cached_blob_clients()

    def test_reads_share_one_token_scoped_client_without_serializing(self) -> None:
        store = VercelPrivateBlobStore(token="shared-token")
        with patch("vercel.blob.BlobClient", ConcurrentBlobClient):
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(
                    executor.map(
                        store.get_json,
                        [f"analysis/{index}.json" for index in range(8)],
                    )
                )

        self.assertEqual(len(ConcurrentBlobClient.instances), 1)
        self.assertGreater(ConcurrentBlobClient.max_active, 1)
        self.assertEqual(
            [result["pathname"] for result in results],
            [f"analysis/{index}.json" for index in range(8)],
        )
        self.assertTrue(
            all(
                access == "private" and use_cache is False
                for _, access, use_cache in ConcurrentBlobClient.calls
            )
        )

    def test_different_tokens_do_not_share_clients(self) -> None:
        with patch("vercel.blob.BlobClient", ConcurrentBlobClient):
            VercelPrivateBlobStore(token="token-a").get_json("analysis/a.json")
            VercelPrivateBlobStore(token="token-b").get_json("analysis/b.json")

        self.assertEqual(
            {client.token for client in ConcurrentBlobClient.instances},
            {"token-a", "token-b"},
        )

    def test_json_and_binary_reads_reuse_the_same_cache_bypassing_client(self) -> None:
        store = VercelPrivateBlobStore(token="shared-token")
        with patch("vercel.blob.BlobClient", ConcurrentBlobClient):
            binary = store.get_bytes("analysis/raw.bin")
            decoded = store.get_json("analysis/value.json")

        self.assertEqual(len(ConcurrentBlobClient.instances), 1)
        self.assertEqual(binary, b'{"pathname": "analysis/raw.bin"}')
        self.assertEqual(decoded, {"pathname": "analysis/value.json"})
        self.assertTrue(
            all(
                access == "private" and use_cache is False
                for _, access, use_cache in ConcurrentBlobClient.calls
            )
        )

    def test_closed_client_is_replaced_and_read_retried_once(self) -> None:
        with patch("vercel.blob.BlobClient", StaleBlobClient):
            result = VercelPrivateBlobStore(token="stale-token").get_json(
                "analysis/latest.json"
            )

        self.assertEqual(result, {"recovered": True})
        self.assertEqual(len(StaleBlobClient.instances), 2)
        self.assertEqual(
            StaleBlobClient.calls,
            [
                ("analysis/latest.json", "private", False),
                ("analysis/latest.json", "private", False),
            ],
        )


if __name__ == "__main__":
    unittest.main()
