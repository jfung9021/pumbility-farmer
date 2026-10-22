from __future__ import annotations

import copy
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from analysis_runtime import MemoryBlobStore
import production_tiers as tiers
from tests.test_production_tiers import context_fixture, fit_for
from tier_computation_cache import input_identity, snapshot_identity
from tier_pipeline_checkpoints import read_checkpoint


class SemanticIdentityTests(TestCase):
    def setUp(self):
        self.snapshot = {"generatedAtUtc": "first-check", "players": [
            {"playerId": "consented-test", "lastSyncedAtUtc": "first-sync", "username": "label"}],
            "charts": [{"id": "chart", "type": "Double", "level": 20, "noteCount": 500}],
            "scores": [{"playerId": "consented-test", "chartId": "chart", "score": 995000,
                        "plate": "FG", "recordedAt": "best-record-selection-time"}]}

    def test_only_run_time_and_sync_time_are_omitted_without_mutating_snapshots(self):
        original = copy.deepcopy(self.snapshot)
        changed = copy.deepcopy(self.snapshot)
        changed["generatedAtUtc"] = "new-check"
        changed["players"][0]["lastSyncedAtUtc"] = "new-sync"
        self.assertEqual(snapshot_identity(self.snapshot), snapshot_identity(changed))
        self.assertEqual(self.snapshot, original)

    def test_consent_catalog_scores_plates_record_selection_and_names_invalidate(self):
        variants = []
        for section, field, value in (("charts", "level", 21), ("charts", "noteCount", 600),
                ("charts", "type", "Single"), ("scores", "score", 996000),
                ("scores", "plate", "AG"), ("scores", "recordedAt", "different-selection-time"),
                ("players", "username", "different-label")):
            changed = copy.deepcopy(self.snapshot)
            changed[section][0][field] = value
            variants.append(changed)
        revoked = copy.deepcopy(self.snapshot)
        revoked["players"] = []
        variants.append(revoked)
        for changed in variants:
            self.assertNotEqual(snapshot_identity(self.snapshot), snapshot_identity(changed))

    def test_official_policy_catalog_metadata_and_code_are_explicit_dependencies(self):
        arguments = dict(records=[], metadata={}, catalog=[], manifest_sha256="capture-one",
                         policy={"rules": [], "effectivePolicySha256": "policy-one"}, model_code="code-one")
        expected = input_identity(self.snapshot, {}, **arguments)
        for field, value in (("records", [{"estimatedDifficulty": 20.5}]), ("metadata", {"policy": 2}),
                ("catalog", [{"id": "new-chart"}]), ("manifest_sha256", "capture-two"),
                ("policy", {"rules": ["new-rule"]}), ("model_code", "code-two")):
            with self.subTest(field=field):
                self.assertNotEqual(expected, input_identity(self.snapshot, {}, **{**arguments, field: value}))

    def test_backend_optional_index_integrity_failure_is_a_miss(self):
        from pumbility_store import PumbilityArtifactIntegrityError
        from tier_computation_cache import read_index
        store = MemoryBlobStore()
        with patch.object(store, "get_json", side_effect=PumbilityArtifactIntegrityError(
                digest_matches=False, byte_size_matches=True)):
            self.assertIsNone(read_index(store, "pipeline", {}))
        with patch.object(store, "get_json", side_effect=TimeoutError("storage offline")):
            with self.assertRaisesRegex(TimeoutError, "storage offline"):
                read_index(store, "pipeline", {})


class CrossRunTierCacheTests(TestCase):
    first_time = "2026-10-18T12:00:00+00:00"
    second_time = "2026-10-19T12:00:00+00:00"

    def setUp(self):
        self.store = MemoryBlobStore()
        self.context = context_fixture()
        self.arguments = {"official_scoring_manifest": {"_captures": {"Phoenix2": {}}}}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("production_tiers.load_context", return_value=self.context))
        self.stack.enter_context(patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="official"))
        self.stack.enter_context(patch("production_tiers.pipeline_code_identity", return_value="pipeline-one"))
        self.stack.enter_context(patch("production_tiers.stage_code_identity", side_effect=lambda stage: "code:" + stage))
        self.stack.enter_context(patch("chart_source_exclusions.load_exclusions",
                                      return_value=SimpleNamespace(semantic_hash=lambda: "policy")))
        self.general = self.stack.enter_context(patch("scoring_percentile.build_production_tier_payload",
            return_value={"schemaVersion": 48, "values": [20.5], "generatedAtUtc": self.first_time}))
        self.fitter = self.stack.enter_context(patch("sss_scoring_model.fit_target_model"))
        self.transition = self.stack.enter_context(patch("production_tiers._finish_stage", side_effect=self.finish))
        # This harness verifies transport/cache reuse with synthetic numeric rows;
        # the real reversible SSS transformation has dedicated publication tests.
        self.stack.enter_context(patch("sss_tail_tiers.apply_sss_tail_tiers",
                                      side_effect=lambda full: {**full, "schemaVersion": 59}))
        self.stack.enter_context(patch("level20_scoring_width.apply_level20_scoring_width",
                                      side_effect=lambda full: {**full, "schemaVersion": 60}))
        self.stack.enter_context(patch("production_tiers.public_tiers", side_effect=self.public))
        self.stack.enter_context(patch("tier_public.prepare_tier_publication", side_effect=lambda value, generation: (value, {})))
        self.stack.enter_context(patch("tier_public.validate_tier_publication"))
        for goal in ("sss", "sssplus"):
            for source in ("official", "submitted"):
                for mode, _ in tiers.MODES:
                    boards, _, identity = tiers.cohort(self.context[-1], goal, source, mode)
                    self.store.put_json(tiers.fit_path(identity), {"identity": identity, "fit": fit_for(boards, identity["target"])})

    @staticmethod
    def finish(stage, base, context, fits, identities, created):
        return {"schemaVersion": tiers.STAGE_SCHEMAS[stage], "values": base["values"] + [20.1],
                "generatedAtUtc": created}

    @staticmethod
    def public(full, *, private_path, input_identity, full_sha256, computation_identity, **kwargs):
        publication = {"version": 1, "method": tiers.VERSION, "inputs": input_identity,
            "fullArtifactPath": private_path, "fullArtifactSha256": full_sha256,
            "computedAtUtc": full["generatedAtUtc"], "computedInputs": input_identity,
            "computedGeneration": tiers.digest(input_identity),
            "computationIdentity": tiers.digest(computation_identity)}
        value = {"schemaVersion": 57, "generatedAtUtc": full["generatedAtUtc"], "singles": [], "doubles": [],
            "summary": {"method": {"productionTierPublication": publication,
                "sourceAwareTiers": {"sourcePolicy": {"effectivePolicySha256": "policy"}},
                "submittedSssplusCalibration": {"localOnly": False}}},
            "tierListView": {"kind": "list", "detailsPrefix": "unchanged-computed-detail-prefix"}}
        publication["publicPayloadSha256"] = tiers.public_digest(value)
        return value

    def advance(self, created, first=None, progress=None):
        return tiers.build_hosted_tiers([], {}, first or {}, {}, store=self.store,
            arguments=self.arguments, generated_at_utc=created, on_progress=progress)

    def complete(self, created, first=None):
        for _ in range(24):
            try:
                return self.advance(created, first)
            except tiers.TierAnalysisPending:
                pass
        self.fail("The bounded tier pipeline never completed")

    def test_fresh_run_reuses_final_math_details_and_immutable_audit_with_original_provenance(self):
        first = {"generatedAtUtc": self.first_time, "players": [{"playerId": "consented-test", "lastSyncedAtUtc": self.first_time}]}
        old = self.complete(self.first_time, first)
        binaries = copy.deepcopy(self.store.binary_values)
        first["generatedAtUtc"] = self.second_time
        first["players"][0]["lastSyncedAtUtc"] = self.second_time
        progress = []
        current = self.advance(self.second_time, first, progress.append)
        before = old["summary"]["method"]["productionTierPublication"]
        after = current["summary"]["method"]["productionTierPublication"]
        self.assertEqual(current["generatedAtUtc"], self.second_time)
        self.assertEqual(after["computedAtUtc"], self.first_time)
        self.assertEqual(after["computedInputs"], before["inputs"])
        self.assertNotEqual(after["inputs"], before["inputs"])
        self.assertEqual(after["fullArtifactSha256"], before["fullArtifactSha256"])
        self.assertEqual(after["computationIdentity"], before["computationIdentity"])
        self.assertEqual(current["tierListView"], {**old["tierListView"],
            "detailsGeneratedAtUtc": self.first_time})
        self.assertNotIn("detailsGeneratedAtUtc", old["tierListView"])
        self.assertEqual(self.store.binary_values, binaries)
        self.assertEqual(progress[-1]["generation"], tiers.digest(after["inputs"]))
        self.assertEqual(progress[-1]["ordinal"], 18)
        self.assertEqual(self.transition.call_count, 7)
        self.general.assert_called_once()
        self.fitter.assert_not_called()
        tiers.validate_public_tiers(current)

    def test_partial_chain_is_reused_across_runs_with_current_progress_and_pinned_old_time(self):
        for _ in range(14):
            with self.assertRaises(tiers.TierAnalysisPending):
                self.advance(self.first_time)
        with self.assertRaises(tiers.TierAnalysisPending) as pending:
            self.advance(self.second_time)
        self.assertEqual(pending.exception.stage, "sssplus")
        current_generation = pending.exception.generation
        self.assertEqual(self.transition.call_count, 2)
        with patch("production_tiers.load_context", side_effect=AssertionError("Raw inputs hydrated during resume")):
            for _ in range(10):
                try:
                    current = tiers.resume_tier_generation(self.store, current_generation, generated_at_utc=self.second_time)
                    break
                except tiers.TierAnalysisPending:
                    pass
            else:
                self.fail("Reused computation did not complete")
        self.assertEqual(self.transition.call_count, 7)
        self.assertTrue(all(call.args[-1] == self.first_time for call in self.transition.call_args_list))
        publication = current["summary"]["method"]["productionTierPublication"]
        old_state = self.store.get_json(tiers.PREFIX + "generations/" + publication["computedGeneration"] + "/state.json")
        audit = read_checkpoint(self.store, old_state["stages"][-1])
        self.assertEqual(audit["generatedAtUtc"], self.first_time)
        self.assertEqual(current["generatedAtUtc"], self.second_time)

    def test_changed_general_build_arguments_invalidate_the_complete_computation(self):
        self.complete(self.first_time)
        self.arguments["official_snapshot"] = {"capture": "changed-clearing-input"}
        self.complete(self.second_time)
        self.assertEqual(self.general.call_count, 2)
        self.assertEqual(self.transition.call_count, 14)

    def test_backend_optional_state_integrity_failure_rebuilds_current_generation(self):
        from pumbility_store import PumbilityArtifactIntegrityError
        first = self.complete(self.first_time)
        generation = first["summary"]["method"]["productionTierPublication"]["computedGeneration"]
        state_path = tiers.PREFIX + "generations/" + generation + "/state.json"
        original_read = self.store.get_json

        def read(path):
            if path == state_path:
                raise PumbilityArtifactIntegrityError(digest_matches=False, byte_size_matches=True)
            return original_read(path)

        with patch.object(self.store, "get_json", side_effect=read):
            result = self.complete(self.second_time)
        self.assertEqual(result["generatedAtUtc"], self.second_time)
        self.assertEqual(self.transition.call_count, 14)

    def test_postprocessing_change_can_reuse_general_but_not_stage_chain(self):
        with self.assertRaises(tiers.TierAnalysisPending):
            self.advance(self.first_time)
        with patch("production_tiers.pipeline_code_identity", return_value="pipeline-two"):
            with self.assertRaises(tiers.TierAnalysisPending) as pending:
                self.advance(self.second_time)
        self.assertEqual(pending.exception.stage, "preparation-general")
        self.general.assert_called_once()
        current = self.store.get_json(tiers.PREFIX + "generations/" + pending.exception.generation + "/state.json")
        provenance = current["componentComputations"]["general"]
        self.assertEqual(provenance["computedAtUtc"], self.first_time)
        self.assertEqual(provenance["inputs"]["generationTimestamp"], self.first_time)

    def test_new_terminal_width_overlay_reuses_completed_numeric_chain_once(self):
        # Simulate the preceding schema-59 publisher: all numerical stages and
        # their independently versioned index are already complete.
        def no_width(store, state, reference):
            from tier_pipeline_checkpoints import write_checkpoint
            from sss_tail_tiers import apply_sss_tail_tiers
            full = read_checkpoint(store, reference, stage=reference["stage"], identity=reference["identity"])
            full = apply_sss_tail_tiers(full)
            tail = write_checkpoint(store, full, stage="sss-tail",
                identity=tiers._tail_overlay_identity(state, reference), predecessor=reference["payloadSha256"])
            state["tailOverlay"] = tail
            return tail

        with patch("production_tiers._publication_overlays", side_effect=no_width):
            self.complete(self.first_time)
        with patch("production_tiers.pipeline_code_identity", return_value="pipeline-with-width"), \
             patch("level20_scoring_width.apply_level20_scoring_width",
                   side_effect=lambda full: {**full, "schemaVersion": 60}) as width:
            current = self.complete(self.second_time)
            self.assertEqual(width.call_count, 1)
            self.assertEqual(self.transition.call_count, len(tiers.FINISH_STAGES))
            self.general.assert_called_once()
            self.fitter.assert_not_called()
            publication = current["summary"]["method"]["productionTierPublication"]
            state = self.store.get_json(tiers.PREFIX+"generations/"+tiers.digest(publication["inputs"])+"/state.json")
            self.assertTrue(state["completedNumericReuse"])
            self.assertEqual(state["archive"], state["widthOverlay"])
            self.assertEqual(state["widthOverlay"]["predecessorSha256"], state["tailOverlay"]["payloadSha256"])
            binaries = copy.deepcopy(self.store.binary_values)
            self.advance(self.second_time)
            self.assertEqual(width.call_count, 1)
            self.assertEqual(self.store.binary_values, binaries)

    def test_corrupt_optional_computed_audit_is_rebuilt_and_never_published(self):
        old = self.complete(self.first_time)
        publication = old["summary"]["method"]["productionTierPublication"]
        self.store.binary_values[publication["fullArtifactPath"]] += b"corruption"
        with self.assertRaises(tiers.TierAnalysisPending) as pending:
            self.advance(self.second_time)
        self.assertEqual(pending.exception.stage, "preparation-general")
        current = self.complete(self.second_time)
        rebuilt = current["summary"]["method"]["productionTierPublication"]
        self.assertNotEqual(rebuilt["fullArtifactPath"], publication["fullArtifactPath"])
        self.assertEqual(self.transition.call_count, 14)
        tiers.validate_public_tiers(current)

    def test_missing_optional_publication_is_a_miss_but_network_failure_is_not_hidden(self):
        old = self.complete(self.first_time)
        publication = old["summary"]["method"]["productionTierPublication"]
        path = tiers.PREFIX + "generations/" + publication["computedGeneration"] + "/public.json"
        self.store.values.pop(path)
        with self.assertRaises(tiers.TierAnalysisPending) as pending:
            self.advance(self.second_time)
        self.assertEqual(pending.exception.stage, "preparation-general")
        with patch.object(self.store, "get_json", side_effect=OSError("storage unavailable")):
            with self.assertRaisesRegex(OSError, "storage unavailable"):
                self.advance(self.second_time)

    def test_corrupt_optional_publication_is_not_promoted(self):
        old = self.complete(self.first_time)
        publication = old["summary"]["method"]["productionTierPublication"]
        path = tiers.PREFIX + "generations/" + publication["computedGeneration"] + "/public.json"
        self.store.values[path]["summary"]["method"]["productionTierPublication"]["fullArtifactSha256"] = "wrong"
        with self.assertRaises(tiers.TierAnalysisPending) as pending:
            self.advance(self.second_time)
        self.assertEqual(pending.exception.stage, "preparation-general")
        current_state = self.store.get_json(tiers.PREFIX + "generations/" + pending.exception.generation + "/state.json")
        self.assertNotIn("computationGeneration", current_state)

    def test_optional_index_schema_mismatch_rebuilds(self):
        self.complete(self.first_time)
        path = next(path for path in self.store.values if "/computations/pipeline/" in path)
        self.store.values[path]["version"] = "unsupported"
        with self.assertRaises(tiers.TierAnalysisPending) as pending:
            self.advance(self.second_time)
        self.assertEqual(pending.exception.stage, "preparation-general")
        state = self.store.get_json(tiers.PREFIX + "generations/" + pending.exception.generation + "/state.json")
        self.assertNotIn("computationGeneration", state)

    def test_corrupt_optional_general_recomputes_from_current_inputs(self):
        with self.assertRaises(tiers.TierAnalysisPending):
            self.advance(self.first_time)
        entry = next(value for path, value in self.store.values.items() if "/computations/general/" in path)
        self.store.binary_values[entry["reference"]["path"]] += b"bad-general"
        with patch("production_tiers.pipeline_code_identity", return_value="pipeline-two"):
            with self.assertRaises(tiers.TierAnalysisPending) as pending:
                self.advance(self.second_time)
        self.assertEqual(pending.exception.stage, "preparation-general")
        self.assertEqual(self.general.call_count, 2)

    def test_failed_detail_write_never_commits_publication_and_retry_is_idempotent(self):
        for _ in range(21):
            with self.assertRaises(tiers.TierAnalysisPending) as pending:
                self.advance(self.first_time)
        generation = pending.exception.generation
        public_path = tiers.PREFIX + "generations/" + generation + "/public.json"
        details = {"private-details/first.json": {"charts": {"first": {}}},
                   "private-details/failure.json": {"charts": {"second": {}}}}
        write = self.store.put_json

        def fail_one(path, payload):
            if path == "private-details/failure.json":
                raise OSError("injected detail failure")
            return write(path, payload)

        with patch("tier_public.prepare_tier_publication", side_effect=lambda value, _: (value, details)):
            with patch.object(self.store, "put_json", side_effect=fail_one):
                with self.assertRaisesRegex(OSError, "injected detail failure"):
                    self.advance(self.first_time)
            self.assertNotIn(public_path, self.store.values)
            current = self.advance(self.first_time)
        self.assertIn(public_path, self.store.values)
        self.assertEqual(self.store.values["private-details/first.json"], details["private-details/first.json"])
        self.assertEqual(self.store.values["private-details/failure.json"], details["private-details/failure.json"])
        tiers.validate_public_tiers(current)

    def test_resume_rejects_changed_code_time_or_stage_chain(self):
        for _ in range(14):
            with self.assertRaises(tiers.TierAnalysisPending) as pending:
                self.advance(self.first_time)
        generation = pending.exception.generation
        with self.assertRaisesRegex(ValueError, "time"):
            tiers.resume_tier_generation(self.store, generation, generated_at_utc=self.second_time)
        with patch("production_tiers.pipeline_code_identity", return_value="changed"):
            with self.assertRaisesRegex(ValueError, "incompatible"):
                tiers.resume_tier_generation(self.store, generation, generated_at_utc=self.first_time)
        path = tiers.PREFIX + "generations/" + generation + "/state.json"
        state = self.store.get_json(path)
        state["stages"][0]["predecessorSha256"] = "wrong-predecessor"
        self.store.put_json(path, state)
        with self.assertRaisesRegex(ValueError, "chain"):
            tiers.resume_tier_generation(self.store, generation, generated_at_utc=self.first_time)
