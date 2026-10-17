from __future__ import annotations

import copy
import gzip
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from analysis_runtime import (MemoryBlobStore, _official_tier_arguments, _validate_checkpoint_combined_tier,
    _write_typed_checkpoint_combined, _load_typed_checkpoint_combined, IncompatibleCombinedTierCheckpointError)
from mix_registry import resolve_mix
from api.jonathan import router
from production_tiers import (TierAnalysisPending, build_hosted_tiers, cohort, digest,
    public_digest, public_tiers, validate_public_tiers)
from tier_release import seed_source_aware_fit


def fit_for(boards, target):
    return {"policy": {"modelVersion": "target-rasch-observed-best-v1", "scoreTarget": target,
        "bootstrapSamples": 200, "minimumPlayerCharts": 4, "regularizationGrid": [.01, .05, .2, 1.],
        "weighting": "each-player-total-one-inverse-observed-chart-count-v1"},
        "charts": {cid: {} for cid in boards}, "diagnostics": {"converged": True,
            "bootstrap": {"completed": 200, "failed": 0}, "validation": {}}}


def context_fixture():
    catalog = {"s": {"type": "Single", "level": 20}, "d": {"type": "Double", "level": 20}}
    prepared = {"catalog": catalog}
    for source in ("official", "submitted"):
        boards = {cid: {source + ":p": 994000} for cid in catalog}
        prepared[source] = {"boards": boards, "sourceBoards": {source: boards}}
    policy = SimpleNamespace(semantic_hash=lambda: "policy")
    merged = {"manifestSha256": "official"}
    return {}, {}, list(catalog.values()), policy, merged, prepared


class HostedTierTests(unittest.TestCase):
    def test_one_missing_cohort_per_invocation_then_reuses_every_fit(self):
        context = context_fixture()
        store = MemoryBlobStore()
        fitter = Mock(side_effect=lambda boards, **kw: fit_for(boards, kw["score_target"]))
        public = {"public": "complete"}
        timestamp = "2026-10-18T00:00:00Z"
        args = {"official_scoring_manifest": {"_captures": {"Phoenix2": {}}}}
        with patch("production_tiers.load_context", return_value=context), \
             patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"), \
             patch("scoring_percentile.build_production_tier_payload", return_value={"schemaVersion": 48}) as general, \
             patch("sss_scoring_model.fit_target_model", fitter), \
             patch("production_tiers.finish_generation", return_value={"full": "audit", "summary": {}}) as finish, \
             patch("production_tiers.public_tiers", return_value=public) as project, \
             patch("production_tiers.validate_public_tiers"):
            for count in range(1, 9):
                with self.assertRaises(TierAnalysisPending) as pending:
                    build_hosted_tiers([], {}, {}, {}, store=store, arguments=args, generated_at_utc=timestamp)
                self.assertEqual(pending.exception.completed, count)
                self.assertEqual(fitter.call_count, count)
                finish.assert_not_called()
            self.assertEqual(build_hosted_tiers([], {}, {}, {}, store=store, arguments=args, generated_at_utc=timestamp), public)
            self.assertEqual(build_hosted_tiers([], {}, {}, {}, store=store, arguments=args, generated_at_utc=timestamp), public)
        self.assertEqual(fitter.call_count, 8)
        general.assert_called_once()
        finish.assert_called_once()
        self.assertEqual(project.call_args.args[0]["generatedAtUtc"], timestamp)
        self.assertEqual(project.call_args.args[0]["summary"]["generatedAtUtc"], timestamp)
        self.assertEqual(len(store.binary_values), 1)
        audit = json.loads(gzip.decompress(next(iter(store.binary_values.values()))))
        self.assertEqual(audit, project.call_args.args[0])
        self.assertEqual(project.call_args.kwargs["full_sha256"], digest(audit))

    def test_checkpoint_pins_raw_inputs_separately_from_excluded_clearing_records(self):
        store = MemoryBlobStore()
        mode = resolve_mix("phoenix2")
        checkpoint = {"jobId": "source-aware-inputs", "mix": mode.key}
        raw = {"phoenix1": "a" * 64, "phoenix2": "b" * 64}
        tier = {"schemaVersion": 56, "summary": {"method": {
            "crossLevelClearing": {"inputHashes": {"phoenix1": "c" * 64, "phoenix2": "d" * 64}},
            "productionTierPublication": {"inputs": dict(raw)}}}}
        def write():
            return _write_typed_checkpoint_combined(store, job_id=checkpoint["jobId"], mix_spec=mode,
                generated_at_utc="2026-10-18T00:00:00Z", combined_tier=tier, model_charts=[],
                phoenix2_slopes={}, source_hashes=raw, snapshot_hashes=raw, input_sha256="e" * 64)
        with patch("analysis_runtime._validate_checkpoint_combined_tier"):
            ref = write()
            self.assertEqual(_load_typed_checkpoint_combined(store, checkpoint=checkpoint, reference=ref, mix_spec=mode)["combinedTier"], tier)
            tier["summary"]["method"]["productionTierPublication"]["inputs"]["phoenix1"] = "f" * 64
            ref = write()
            with self.assertRaisesRegex(IncompatibleCombinedTierCheckpointError, "pinned checkpoint"):
                _load_typed_checkpoint_combined(store, checkpoint=checkpoint, reference=ref, mix_spec=mode)

    def test_uploaded_fits_require_exact_current_inputs_and_convergence(self):
        context = context_fixture()
        boards, _, identity = cohort(context[-1], "sssplus", "official", "Double")
        request = {"goal": "sssplus", "source": "official", "mode": "Double",
            "identity": identity, "fit": fit_for(boards, 995000)}
        store = MemoryBlobStore()
        with patch("tier_release.source_aware_inputs", return_value=({}, {}, {}, {}, [], {})), \
             patch("production_tiers.load_context", return_value=context):
            bad = copy.deepcopy(request)
            bad["identity"]["boards"] = "wrong"
            with self.assertRaisesRegex(ValueError, "different current production inputs"):
                seed_source_aware_fit(store, bad)
            bad = copy.deepcopy(request)
            bad["fit"]["diagnostics"]["converged"] = False
            with self.assertRaisesRegex(ValueError, "converged"):
                seed_source_aware_fit(store, bad)
            self.assertEqual(store.values, {})
            self.assertEqual(seed_source_aware_fit(store, request)["outcome"], "target-fit-cached")
            self.assertEqual(len(store.values), 1)

    def test_public_projection_preserves_final_components_and_excludes_rollback_copies(self):
        full = json.loads((Path(__file__).parent / "fixtures/source-aware-width.json").read_text())["candidate"]
        policy_sha = full["summary"]["method"]["sourceAwareTiers"]["sourcePolicy"]["effectivePolicySha256"]
        with patch("chart_source_exclusions.load_exclusions", return_value=SimpleNamespace(semantic_hash=lambda: policy_sha)):
            public = public_tiers(full, private_path="analysis/private/source-aware-tiers/generations/x/full.json.gz", input_identity={})
            self.assertLess(len(json.dumps(public)), len(json.dumps(full)))
            def no_history(value):
                if isinstance(value, dict):
                    self.assertFalse(any(k.endswith("Baseline") for k in value))
                    for v in value.values(): no_history(v)
                elif isinstance(value, list):
                    for v in value: no_history(v)
            no_history(public)
            for key in ("singles", "doubles"):
                for old, new in zip(full[key], public[key]):
                    for goal in ("general", "sss", "sssplus"):
                        self.assertEqual(old["scoringVariants"][goal]["estimatedDifficulty"], new["scoringVariants"][goal]["estimatedDifficulty"])
                        self.assertEqual(old["pumbilityVariants"][goal]["estimatedDifficulty"], new["pumbilityVariants"][goal]["estimatedDifficulty"])
            bad = copy.deepcopy(public)
            bad["doubles"][0]["pumbilityVariants"]["sssplus"]["estimatedDifficulty"] += .1
            with self.assertRaisesRegex(ValueError, "publication identity"):
                validate_public_tiers(bad)
            bad["summary"]["method"]["productionTierPublication"]["publicPayloadSha256"] = public_digest(bad)
            with self.assertRaisesRegex(ValueError, "average"):
                validate_public_tiers(bad)
        self.assertIn("sourceAwareBaseline", full)

    def test_hosted_policy_opts_in_and_rejects_old_checkpoints(self):
        with patch.dict(os.environ, {"PIU_SOURCE_AWARE_TIERS": "1"}):
            with patch("analysis_runtime.os.getenv", side_effect=lambda name, default="": "1" if name == "PIU_SOURCE_AWARE_TIERS" else default):
                with self.assertRaisesRegex(RuntimeError, "hybrid average"):
                    _official_tier_arguments(MemoryBlobStore())
            with self.assertRaises(IncompatibleCombinedTierCheckpointError):
                _validate_checkpoint_combined_tier({"schemaVersion": 48})

    def test_fit_controls_are_authenticated_and_never_return_private_fits(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        with patch.dict(os.environ, {"JONATHAN_PASSWORD": "operator"}), \
             patch("analysis_runtime.PrivateBlobStore"), \
             patch("tier_release.source_aware_fit_status", return_value={"fits": [{"identity": "hash"}]}) as status, \
             patch("tier_release.seed_source_aware_fit", return_value={"outcome": "target-fit-cached"}) as seed:
            self.assertEqual(client.get("/api/jonathan/tier-fits").status_code, 401)
            self.assertEqual(client.post("/api/jonathan/tier-fits", json={}).status_code, 401)
            status.assert_not_called(); seed.assert_not_called()
            headers = {"X-Jonathan-Password": "operator"}
            self.assertEqual(client.get("/api/jonathan/tier-fits", headers=headers).json(), {"fits": [{"identity": "hash"}]})
            self.assertEqual(client.post("/api/jonathan/tier-fits", headers=headers, json={}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
