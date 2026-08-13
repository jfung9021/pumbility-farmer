from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from analysis_runtime import MemoryBlobStore
from scripts.capture_pumbility_migration_baseline import (
    COMBINED_TIER_POINTER,
    PHOENIX1_PRIVATE_SNAPSHOT,
    PHOENIX2_ANALYSIS_POINTER,
    PHOENIX2_PRIVATE_SNAPSHOT,
    RECOMMENDATION_POINTER,
    BaselineCaptureError,
    canonical_bytes,
    capture_local_baseline,
    capture_production_baseline,
    private_hmac_sha256,
    privacy_scan_manifest,
    public_sha256,
    validate_baseline_manifest,
)
from scripts.verify_pumbility_migration_baseline import (
    BaselineVerificationError,
    compare_manifests,
    verify_manifests,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "pumbility-migration"
HMAC_KEY = b"migration-test-key-that-is-at-least-thirty-two-bytes"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _write_scores(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def _snapshot(root: Path, mix_key: str, api_mix: str) -> None:
    current = root / mix_key / "current"
    players = [
        {
            "userId": f"private-{mix_key}-player",
            "username": f"PRIVATE_{mix_key.upper()}",
            "lastSyncedAtUtc": "2026-08-13T00:00:00Z",
            "lastScoreRecordedAtUtc": "2026-08-12T00:00:00Z",
        }
    ]
    charts = [
        {
            "id": f"public-{mix_key}-chart",
            "songName": "Synthetic Contract Chart",
            "type": "Single",
            "level": 16,
            "difficulty": "S16",
            "imageUrl": None,
            "noteCount": 100,
            "stepArtist": "TEST",
            "bpmMin": 120,
            "bpmMax": 120,
        }
    ]
    scores = [
        {
            "playerId": f"private-{mix_key}-player",
            "chartId": f"public-{mix_key}-chart",
            "pumbility": 100.0,
            "score": 950000,
            "letterGrade": "S",
            "plate": "Fair Game",
            "recordedAt": "2026-08-12T00:00:00Z",
            "isBroken": False,
        }
    ]
    _write_json(current / "players.json", players)
    _write_json(current / "charts.json", charts)
    _write_scores(current / "scores.jsonl.gz", scores)
    _write_json(
        current / "snapshot_manifest.json",
        {
            "schemaVersion": 2,
            "scriptVersion": "test-method",
            "mix": api_mix,
            "captureCompletedAtUtc": "2026-08-13T00:00:00Z",
            "players": 1,
            "charts": 1,
            "scoreRows": 1,
        },
    )


def _analysis(mix_key: str) -> dict[str, object]:
    return {
        "schemaVersion": 2 if mix_key == "combined" else None,
        "generatedAtUtc": "2026-08-13T00:00:00Z",
        "mix": {"key": mix_key},
        "summary": {"scriptVersion": "test-method", "modes": {}},
        "singles": [{"chartId": "public-chart", "difficultyDelta": -0.1}],
        "doubles": [],
        "relativeGroups": [],
        "effectBands": [],
    }


def _prepare_local_tree(project_root: Path) -> Path:
    data_root = project_root / ".local-data" / "piu-scores"
    _snapshot(data_root, "phoenix1", "Phoenix")
    _snapshot(data_root, "phoenix2", "Phoenix2")
    _write_json(data_root / "phoenix1" / "analysis" / "web_results.json", _analysis("phoenix1"))
    _write_json(data_root / "phoenix2" / "analysis" / "web_results.json", _analysis("phoenix2"))
    _write_json(data_root / "combined" / "analysis" / "web_results.json", _analysis("combined"))
    _write_json(
        data_root / "recommendations" / "latest.json",
        {
            "schemaVersion": 21,
            "storageSchemaVersion": 3,
            "generatedAtUtc": "2026-08-13T00:00:00Z",
            "refreshSupported": True,
            "method": {"baselineRanks": [11, 30]},
            "players": [
                {
                    "playerKey": "0123456789abcdefabcd",
                    "internalPlayerId": "private-phoenix2-player",
                    "username": "PRIVATE_PHOENIX2",
                }
            ],
        },
    )

    public_data = project_root / "public" / "data"
    archive = _analysis("phoenix1")
    _write_json(public_data / "phoenix1.json", archive)
    _write_json(
        public_data / "phoenix1.manifest.json",
        {"methodologyVersion": "historical-test", "selectedContributions": 1},
    )
    _write_json(
        public_data / "phoenix1-rerates.json",
        {
            "rerates": [
                {
                    "chartId": "public-chart",
                    "direction": "uprated",
                    "from": "S16",
                    "to": "S17",
                }
            ]
        },
    )
    _write_json(
        project_root / "lib" / "data" / "nevsister-chart-videos.json",
        {"schemaVersion": 1, "charts": {"public-chart": "abcdefghijk"}},
    )
    _write_json(
        project_root / "lib" / "data" / "nevsister-chart-video-overrides.json",
        {"aliases": {}, "charts": {}, "notes": {}},
    )
    (project_root / "phoenix1_score_overrides.py").write_text("OVERRIDES = 2\n", encoding="utf-8")
    (project_root / "phoenix2_pumbility.py").write_text("FORMULA = 1\n", encoding="utf-8")
    return data_root


PRODUCTION_GENERATION = "0123456789abcdefabcd"


def _production_snapshot(mix: str, private_suffix: str) -> dict[str, object]:
    player_id = f"private-{private_suffix}-player"
    chart_id = f"public-{private_suffix}-chart"
    return {
        "schemaVersion": 2,
        "mix": mix,
        "generatedAtUtc": "2026-08-13T00:00:00Z",
        "players": [
            {
                "playerId": player_id,
                "username": f"PRIVATE_{private_suffix.upper()}",
                "lastSyncedAtUtc": "2026-08-13T00:00:00Z",
                "lastScoreRecordedAtUtc": "2026-08-12T00:00:00Z",
            }
        ],
        "charts": [
            {
                "id": chart_id,
                "songName": "Synthetic Production Contract Chart",
                "type": "Single",
                "level": 16,
                "difficulty": "S16",
            }
        ],
        "scores": [
            {
                "playerId": player_id,
                "chartId": chart_id,
                "pumbility": 111.25,
                "score": 987654,
                "letterGrade": "SS",
                "plate": "Perfect Game",
                "recordedAt": "2026-08-12T00:00:00Z",
                "isBroken": False,
            }
        ],
    }


def _prepare_production_store() -> MemoryBlobStore:
    store = MemoryBlobStore()
    recommendation_index = {
        "schemaVersion": 21,
        "storageSchemaVersion": 3,
        "generationKey": PRODUCTION_GENERATION,
        "modelGeneratedAtUtc": "2026-08-13T00:00:00Z",
        "generatedAtUtc": "2026-08-13T00:00:00Z",
        "modelPath": f"analysis/recommendations/models/{PRODUCTION_GENERATION}.json",
        "refreshSupported": True,
        "method": {"baselineRanks": [11, 30]},
        "players": [
            {
                "playerKey": "fedcba98765432100123",
                "internalPlayerId": "private-phoenix2-player",
                "username": "PRIVATE_PHOENIX2",
                "inputShard": 0,
            }
        ],
        "inputShardCount": 1,
        "inputShardSize": 10,
    }
    json_values = {
        PHOENIX2_ANALYSIS_POINTER: _analysis("phoenix2"),
        COMBINED_TIER_POINTER: _analysis("combined"),
        RECOMMENDATION_POINTER: recommendation_index,
        PHOENIX1_PRIVATE_SNAPSHOT: _production_snapshot("Phoenix", "phoenix1"),
        PHOENIX2_PRIVATE_SNAPSHOT: _production_snapshot("Phoenix2", "phoenix2"),
        f"analysis/recommendations/indexes/{PRODUCTION_GENERATION}.json": recommendation_index,
        f"analysis/recommendations/models/{PRODUCTION_GENERATION}.json": {
            "schemaVersion": 3,
            "generationKey": PRODUCTION_GENERATION,
            "privateTrainingMarker": "private-model-evidence",
        },
        (
            "analysis/private/recommendation-inputs/"
            f"{PRODUCTION_GENERATION}/phoenix1/0000.json"
        ): {
            "generationKey": PRODUCTION_GENERATION,
            "players": [{"playerId": "private-phoenix1-player", "scores": []}],
        },
        (
            "analysis/private/recommendation-inputs/"
            f"{PRODUCTION_GENERATION}/phoenix2/0000.json"
        ): {
            "generationKey": PRODUCTION_GENERATION,
            "players": [
                {
                    "playerId": "private-phoenix2-player",
                    "username": "PRIVATE_PHOENIX2",
                    "scores": [{"score": 987654}],
                }
            ],
        },
    }
    for pathname, value in json_values.items():
        store.put_json(pathname, value)
    store.put_bytes(
        f"analysis/recommendations/models/{PRODUCTION_GENERATION}.npz",
        b"synthetic-private-model-bytes",
        content_type="application/x-npz",
    )
    return store


class SequencedMemoryBlobStore(MemoryBlobStore):
    def __init__(self, source: MemoryBlobStore) -> None:
        super().__init__()
        self.values = deepcopy(source.values)
        self.binary_values = deepcopy(source.binary_values)
        self.sequences: dict[str, list[dict[str, object]]] = {}
        self.read_counts: dict[str, int] = {}

    def get_json(self, pathname: str) -> dict[str, object] | None:
        count = self.read_counts.get(pathname, 0)
        self.read_counts[pathname] = count + 1
        sequence = self.sequences.get(pathname)
        if sequence:
            return deepcopy(sequence[min(count, len(sequence) - 1)])
        return super().get_json(pathname)


class CanonicalHashTests(unittest.TestCase):
    def test_public_hash_is_order_and_numeric_spelling_independent(self) -> None:
        left = {"b": [1, {"x": 2.0}], "a": True}
        right = {"a": True, "b": [1.0, {"x": 2}]}
        self.assertEqual(canonical_bytes(left), canonical_bytes(right))
        self.assertEqual(public_sha256(left), public_sha256(right))
        self.assertNotEqual(public_sha256(left), public_sha256({**right, "a": False}))

    def test_private_hash_requires_a_strong_key_and_changes_with_data(self) -> None:
        with self.assertRaisesRegex(BaselineCaptureError, "at least 32 bytes"):
            private_hmac_sha256({"private": "value"}, b"short")
        first = private_hmac_sha256({"private": "value"}, HMAC_KEY)
        second = private_hmac_sha256({"private": "changed"}, HMAC_KEY)
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, second)

    def test_privacy_scan_rejects_private_fields_credentials_and_identifiers(self) -> None:
        rejected = (
            {"playerId": "private"},
            {"value": "piu_scores_live_0123456789abcdef"},
            {"value": "29318994-51d6-47bf-bf69-8c386e77edb4"},
            {"value": "0123456789abcdefabcd"},
        )
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(BaselineCaptureError):
                    privacy_scan_manifest(value)


class LocalCaptureTests(unittest.TestCase):
    def test_local_capture_contains_only_counts_public_hashes_and_private_hmacs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            data_root = _prepare_local_tree(project_root)
            manifest = capture_local_baseline(
                project_root=project_root,
                data_root=data_root,
                boundary_id="local-contract",
                hmac_key=HMAC_KEY,
                captured_at_utc="2026-08-13T00:00:00Z",
            )

        validate_baseline_manifest(manifest)
        serialized = json.dumps(manifest, sort_keys=True)
        self.assertEqual(manifest["captureStatus"], "local-development-only")
        self.assertFalse(manifest["gate"]["productionT0Captured"])
        self.assertEqual(
            manifest["datasets"]["phoenix2Snapshot"]["counts"]["bestScoreRecords"],
            1,
        )
        self.assertNotIn("private-phoenix2-player", serialized)
        self.assertNotIn("PRIVATE_PHOENIX2", serialized)
        self.assertNotIn("0123456789abcdefabcd", serialized)
        self.assertNotIn("950000", serialized)

    def test_committed_manifest_template_is_valid_and_not_production_ready(self) -> None:
        manifest = json.loads(
            (ROOT / "docs" / "pumbility-migration" / "baseline-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        validate_baseline_manifest(manifest)
        self.assertEqual(manifest["captureStatus"], "pending-production-capture")
        self.assertFalse(manifest["gate"]["readyForSchemaImplementation"])


class ProductionCaptureTests(unittest.TestCase):
    def _capture(
        self,
        store: MemoryBlobStore,
        project_root: Path,
        *,
        max_attempts: int = 3,
    ) -> tuple[dict[str, object], dict[str, object]]:
        return capture_production_baseline(
            store=store,
            project_root=project_root,
            boundary_id="production-T0",
            hmac_key=HMAC_KEY,
            max_attempts=max_attempts,
            captured_at_utc="2026-08-13T01:00:00Z",
        )

    def test_stable_boundary_reads_all_pointers_artifacts_and_snapshot_twice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            _prepare_local_tree(project_root)
            store = SequencedMemoryBlobStore(_prepare_production_store())
            manifest, private_evidence = self._capture(store, project_root)

        self.assertEqual(manifest["captureStatus"], "production-t0")
        self.assertTrue(manifest["gate"]["generationConsistencyVerified"])
        self.assertEqual(private_evidence["consistency"]["attemptsUsed"], 1)
        self.assertEqual(store.read_counts[PHOENIX2_PRIVATE_SNAPSHOT], 2)
        for pathname in (
            PHOENIX2_ANALYSIS_POINTER,
            COMBINED_TIER_POINTER,
            RECOMMENDATION_POINTER,
        ):
            self.assertEqual(store.read_counts[pathname], 2)
        self.assertEqual(
            private_evidence["artifactEvidence"]["referencedArtifacts"], 5
        )

    def test_moving_pointer_retries_then_accepts_a_stable_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            _prepare_local_tree(project_root)
            store = SequencedMemoryBlobStore(_prepare_production_store())
            first = deepcopy(store.values[PHOENIX2_ANALYSIS_POINTER])
            second = deepcopy(first)
            second["generatedAtUtc"] = "2026-08-13T00:01:00Z"
            store.sequences[PHOENIX2_ANALYSIS_POINTER] = [
                first,
                second,
                second,
                second,
            ]
            _, private_evidence = self._capture(store, project_root)

        self.assertEqual(private_evidence["consistency"]["attemptsUsed"], 2)
        self.assertEqual(store.read_counts[PHOENIX2_ANALYSIS_POINTER], 4)

    def test_moving_snapshot_exhausts_bound_without_writing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            _prepare_local_tree(project_root)
            store = SequencedMemoryBlobStore(_prepare_production_store())
            first = deepcopy(store.values[PHOENIX2_PRIVATE_SNAPSHOT])
            second = deepcopy(first)
            second["scores"][0]["score"] = 987655
            store.sequences[PHOENIX2_PRIVATE_SNAPSHOT] = [
                first,
                second,
                first,
                second,
            ]
            with self.assertRaisesRegex(BaselineCaptureError, "did not stabilize"):
                self._capture(store, project_root, max_attempts=2)

        self.assertEqual(store.read_counts[PHOENIX2_PRIVATE_SNAPSHOT], 4)

    def test_missing_referenced_artifact_fails_immediately_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            _prepare_local_tree(project_root)
            store = SequencedMemoryBlobStore(_prepare_production_store())
            del store.binary_values[
                f"analysis/recommendations/models/{PRODUCTION_GENERATION}.npz"
            ]
            with self.assertRaisesRegex(BaselineCaptureError, "numeric model"):
                self._capture(store, project_root)

        self.assertEqual(store.read_counts[PHOENIX2_ANALYSIS_POINTER], 1)
        self.assertEqual(store.read_counts[PHOENIX2_PRIVATE_SNAPSHOT], 1)

    def test_manifest_and_private_evidence_never_emit_private_values_or_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            _prepare_local_tree(project_root)
            manifest, private_evidence = self._capture(
                _prepare_production_store(), project_root
            )

        privacy_scan_manifest(manifest)
        privacy_scan_manifest(private_evidence)
        serialized = json.dumps(
            {"manifest": manifest, "privateEvidence": private_evidence}, sort_keys=True
        )
        forbidden_values = (
            PRODUCTION_GENERATION,
            "fedcba98765432100123",
            "private-phoenix1-player",
            "private-phoenix2-player",
            "PRIVATE_PHOENIX2",
            "987654",
            "analysis/recommendations/",
            "analysis/private/",
            "synthetic-private-model-bytes",
        )
        for value in forbidden_values:
            with self.subTest(value=value):
                self.assertNotIn(value, serialized)


class VerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = json.loads((FIXTURES / "source-manifest.json").read_text(encoding="utf-8"))
        self.candidate = json.loads(
            (FIXTURES / "candidate-explained.json").read_text(encoding="utf-8")
        )
        self.explanations = json.loads(
            (FIXTURES / "explanations-valid.json").read_text(encoding="utf-8")
        )

    def test_exact_candidate_passes_without_explanations(self) -> None:
        candidate = deepcopy(self.source)
        candidate["boundary"] = {
            "id": "candidate-exact",
            "capturedAtUtc": "2026-08-14T00:00:00Z",
            "source": "candidate",
            "productionReady": False,
        }
        report = verify_manifests(self.source, candidate)
        self.assertEqual(report["result"], "passed")
        self.assertEqual(report["unexplainedMismatchCount"], 0)

    def test_every_changed_leaf_requires_specific_evidence(self) -> None:
        with self.assertRaisesRegex(BaselineVerificationError, "1 unexplained"):
            verify_manifests(self.source, self.candidate)
        report = verify_manifests(
            self.source,
            self.candidate,
            explanations=self.explanations,
        )
        self.assertEqual(report["explainedChanges"], 1)
        self.assertEqual(report["unexplainedMismatchCount"], 0)

    def test_unused_explanations_fail_instead_of_masking_future_changes(self) -> None:
        candidate = deepcopy(self.source)
        candidate["boundary"]["id"] = "candidate-explained"
        report = compare_manifests(
            self.source,
            candidate,
            explanations=self.explanations,
        )
        self.assertEqual(report["result"], "failed")
        self.assertEqual(report["unusedExplanationCount"], 1)


if __name__ == "__main__":
    unittest.main()
