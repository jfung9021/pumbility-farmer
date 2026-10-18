from __future__ import annotations

import copy
import json
from contextlib import ExitStack
from unittest import TestCase
from unittest.mock import patch

from analysis_runtime import MemoryBlobStore
import production_tiers as tiers
from tests.test_production_tiers import context_fixture, fit_for
from tier_pipeline_checkpoints import read_checkpoint, write_checkpoint


class CheckpointTests(TestCase):
    def test_gzip_uploads_use_the_private_buckets_allowed_binary_mime(self):
        import gzip
        store = MemoryBlobStore()
        value = {"schemaVersion": 57, "value": 20.5}
        with patch.object(store, "put_bytes", wraps=store.put_bytes) as upload:
            reference = write_checkpoint(store, value, stage="pinned-inputs", identity="inputs")
            self.assertEqual(upload.call_args.kwargs["content_type"], "application/octet-stream")
        self.assertEqual(json.loads(gzip.decompress(store.get_bytes(reference["path"]))), value)
        with patch.object(store, "put_bytes", wraps=store.put_bytes) as upload:
            result = tiers.write_private_audit(store, "legacy-audit.json.gz", value)
            self.assertEqual(upload.call_args.kwargs["content_type"], "application/octet-stream")
        self.assertEqual(json.loads(gzip.decompress(store.get_bytes("legacy-audit.json.gz"))), value)
        self.assertEqual(result, tiers.digest(value))

    def test_general_cache_adoption_is_portable_across_checkout_line_endings(self):
        with patch("pathlib.Path.read_bytes", return_value=b"same source\r\nnext line\r\n"):
            windows = tiers.general_code_identity()
            windows_fit = tiers.model_code_identity()
        with patch("pathlib.Path.read_bytes", return_value=b"same source\nnext line\n"):
            linux = tiers.general_code_identity()
            linux_fit = tiers.model_code_identity()
        self.assertEqual(windows, linux)
        # Existing hosted fit paths retain their historical byte-based identity.
        self.assertNotEqual(windows_fit, linux_fit)

    def test_content_addressed_roundtrip_preserves_number_representation_and_checks_bytes(self):
        store = MemoryBlobStore()
        value = {"schemaVersion": 57, "integer": 10, "decimal": 10.0, "unicode": "音楽"}
        ref = write_checkpoint(store, value, stage="submitted-calibration", identity="code", predecessor="prior")
        self.assertEqual(read_checkpoint(store, ref, stage="submitted-calibration", identity="code", predecessor="prior"), value)
        self.assertEqual(ref["payloadSha256"], tiers.digest(value))
        again = write_checkpoint(store, value, stage="submitted-calibration", identity="code", predecessor="prior")
        self.assertEqual(again, ref)
        with self.assertRaisesRegex(ValueError, "provenance"):
            read_checkpoint(store, ref, identity="stale-code")
        store.binary_values[ref["path"]] += b"broken"
        with self.assertRaisesRegex(ValueError, "corrupt"):
            read_checkpoint(store, ref)

    def test_reference_cannot_substitute_content_hash_schema_or_private_path(self):
        store = MemoryBlobStore()
        ref = write_checkpoint(store, {"schemaVersion": 56}, stage="source-aware", identity="code")
        for field, value in (("payloadSha256", "0"*64), ("schemaVersion", 57), ("path", "other/path")):
            bad = {**ref, field: value}
            with self.assertRaises(ValueError):
                read_checkpoint(store, bad)


class PipelineResumeTests(TestCase):
    timestamp = "2026-10-18T12:00:00+00:00"

    def setUp(self):
        self.store = MemoryBlobStore()
        self.context = context_fixture()
        self.args = {"official_scoring_manifest": {"_captures": {"Phoenix2": {}}}}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("production_tiers.load_context", return_value=self.context))
        self.stack.enter_context(patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"))
        self.stack.enter_context(patch("production_tiers.pipeline_code_identity", return_value="pipeline-one"))
        self.stack.enter_context(patch("production_tiers.stage_code_identity", side_effect=lambda stage: "code:"+stage))
        self.general = self.stack.enter_context(patch("scoring_percentile.build_production_tier_payload",
            return_value={"schemaVersion": 48, "values": [19.5, 20.5]}))
        self.fitter = self.stack.enter_context(patch("sss_scoring_model.fit_target_model"))
        for goal in ("sss", "sssplus"):
            for source in ("official", "submitted"):
                for mode, _ in tiers.MODES:
                    boards, _, identity = tiers.cohort(self.context[-1], goal, source, mode)
                    self.store.put_json(tiers.fit_path(identity), {"identity": identity,
                        "fit": fit_for(boards, identity["target"])})

    def advance(self):
        return tiers.build_hosted_tiers([], {}, {}, {}, store=self.store,
            arguments=self.args, generated_at_utc=self.timestamp)

    def state(self):
        return next(value for path, value in self.store.values.items() if path.endswith("/state.json"))

    def prepare(self):
        with self.assertRaises(tiers.TierAnalysisPending) as prepared:
            self.advance()
        self.assertEqual(prepared.exception.stage, "prepared-inputs")

    @staticmethod
    def transition(stage, base, context, fits, identities, created):
        return {"schemaVersion": tiers.STAGE_SCHEMAS[stage], "values": base["values"]+[20.1], "generatedAtUtc": created}

    def test_failed_phase_resumes_last_valid_checkpoint_without_refitting_or_repeating_prior_phase(self):
        self.prepare()
        calls = []
        def transition(stage, *args):
            calls.append(stage)
            if calls == ["centered-sss", "sssplus"]:
                raise OSError("injected interruption")
            return self.transition(stage, *args)
        with patch("production_tiers._finish_stage", side_effect=transition):
            with self.assertRaises(tiers.TierAnalysisPending): self.advance()
            first = copy.deepcopy(self.state()["stages"][0])
            with self.assertRaises(OSError): self.advance()
            self.assertEqual(self.state()["stages"], [first])
            with self.assertRaises(tiers.TierAnalysisPending) as resumed: self.advance()
        self.assertEqual(calls, ["centered-sss", "sssplus", "sssplus"])
        self.assertEqual(resumed.exception.progress_ordinal, 11)
        self.assertEqual(self.state()["createdAtUtc"], self.timestamp)
        self.general.assert_called_once()
        self.fitter.assert_not_called()

    def test_corrupt_latest_checkpoint_never_advances(self):
        self.prepare()
        with patch("production_tiers._finish_stage", side_effect=self.transition) as transition:
            with self.assertRaises(tiers.TierAnalysisPending): self.advance()
            ref = self.state()["stages"][0]
            self.store.binary_values[ref["path"]] += b"corruption"
            with self.assertRaisesRegex(ValueError, "corrupt"): self.advance()
            self.assertEqual(transition.call_count, 1)
        self.fitter.assert_not_called()

    def test_postprocessing_code_change_gets_new_generation_but_reuses_general_and_all_eight_fits(self):
        with patch("production_tiers._finish_stage", side_effect=self.transition):
            with self.assertRaises(tiers.TierAnalysisPending) as first: self.advance()
            with patch("production_tiers.pipeline_code_identity", return_value="pipeline-two"):
                with self.assertRaises(tiers.TierAnalysisPending) as second: self.advance()
        self.assertNotEqual(first.exception.generation, second.exception.generation)
        self.assertEqual(first.exception.progress_ordinal, second.exception.progress_ordinal)
        self.general.assert_called_once()
        self.fitter.assert_not_called()

    def test_pinned_timestamp_adopts_compatible_legacy_none_timestamp_general_cache(self):
        from production_tiers import PREVIOUS_VERSION, LEGACY_GENERAL_CODE_IDENTITY
        legacy = {"version": PREVIOUS_VERSION, "phoenix1": tiers.digest({}), "phoenix2": tiers.digest({}),
            "catalog": tiers.digest(self.context[2]), "officialManifest": "official",
            "sourcePolicy": "policy", "modelCode": tiers.model_code_identity(), "generationTimestamp": None}
        path = tiers.PREFIX+"generations/"+tiers.digest(legacy)+"/general.json"
        self.store.put_json(path, {"schemaVersion": 48, "values": [19.5, 20.5]})
        with patch("production_tiers.general_code_identity", return_value=LEGACY_GENERAL_CODE_IDENTITY), \
             patch("production_tiers._finish_stage", side_effect=self.transition):
            with self.assertRaises(tiers.TierAnalysisPending): self.advance()
        self.general.assert_not_called()
        self.fitter.assert_not_called()

    def test_worker_adapter_rejects_changed_pinned_creation_time(self):
        ref = tiers.pin_tier_publication_inputs(self.store, first={}, second={}, manifest={}, records=[],
            metadata={}, generated_at_utc=self.timestamp)
        with self.assertRaisesRegex(ValueError, "creation time"):
            tiers.advance_tier_publication(self.store, {"inputs": ref, "generatedAtUtc": "changed"})

    def test_worker_adapter_rejects_changed_code_before_advancing_pinned_job(self):
        ref = tiers.pin_tier_publication_inputs(self.store, first={}, second={}, manifest={}, records=[],
            metadata={}, generated_at_utc=self.timestamp)
        with patch("production_tiers.pipeline_code_identity", return_value="different-pipeline"), \
             patch("production_tiers.build_hosted_tiers") as build:
            with self.assertRaisesRegex(ValueError, "different pipeline code"):
                tiers.advance_tier_publication(self.store, {"inputs": ref, "generatedAtUtc": self.timestamp})
        build.assert_not_called()

    def test_worker_resume_hydrates_only_phase_inputs_and_required_fit_cohorts(self):
        ref = tiers.pin_tier_publication_inputs(self.store, first={}, second={},
            manifest=self.args["official_scoring_manifest"], records=[], metadata={}, generated_at_utc=self.timestamp)
        state = {"inputs": ref, "generatedAtUtc": self.timestamp}
        started = []
        with patch("production_tiers._finish_stage", side_effect=self.transition):
            prepared = tiers.advance_tier_publication(self.store, state, on_stage_start=started.append)
            self.assertEqual(prepared["stage"], "prepared-inputs")
            self.assertEqual(prepared["progressOrdinal"], 9)
            self.assertEqual(prepared["totalStages"], 18)
            with patch("production_tiers.load_context", side_effect=AssertionError("raw context rehydrated")), \
                 patch.object(self.store, "get_json", wraps=self.store.get_json) as reads:
                centered = tiers.advance_tier_publication(self.store, prepared["state"], on_stage_start=started.append)
                fit_reads = [call.args[0] for call in reads.call_args_list if "/fits/" in call.args[0]]
                self.assertEqual(len(fit_reads), 4)
                plus = tiers.advance_tier_publication(self.store, centered["state"], on_stage_start=started.append)
                reads.reset_mock()
                median = tiers.advance_tier_publication(self.store, plus["state"], on_stage_start=started.append)
                self.assertFalse(any("/fits/" in call.args[0] for call in reads.call_args_list))
            self.assertEqual(median["progressOrdinal"], 12)
        self.assertEqual(started[-3:], ["centered-sss", "sssplus", "median"])
        self.fitter.assert_not_called()


class TimestampOrderingTests(TestCase):
    def test_timestamp_is_pinned_before_schema57_predecessor_hash_and_survives_reload(self):
        from tests.test_submitted_sssplus_calibration import calibration_fixture
        from submitted_sssplus_calibration import validate_submitted_sssplus_calibration
        base, fit, identity = calibration_fixture()
        created = "2026-10-18T18:00:00+00:00"
        provenance = base["summary"]["method"]["sourceAwareTiers"]["sourcePolicy"]
        context = ({}, {}, [], type("Policy", (), {"public": lambda _: provenance})(),
            {"charts": {}, "manifestSha256": "manifest", "effectiveInputSha256": "input", "sourceDates": {}}, {})
        fits = {"sssplus": {"submitted": {"Double": fit}}}
        identities = [{"goal": "sssplus", "source": "submitted", "mode": "Double", "identity": identity}]
        with patch("source_aware_tiers.apply_source_aware_tiers", return_value=copy.deepcopy(base)):
            source = tiers._finish_stage("source-aware", {}, context, fits, identities, created)
        result = tiers._finish_stage("submitted-calibration", source, context, fits, identities, created)
        reloaded = json.loads(json.dumps(result))
        recovered = validate_submitted_sssplus_calibration(reloaded)
        self.assertEqual(recovered["generatedAtUtc"], created)
        self.assertEqual(reloaded["summary"]["generatedAtUtc"], created)
        # The previous ordering deterministically broke the predecessor hash.
        reloaded["generatedAtUtc"] = reloaded["summary"]["generatedAtUtc"] = "late mutation"
        with self.assertRaisesRegex(ValueError, "predecessor recovery"):
            validate_submitted_sssplus_calibration(reloaded)
