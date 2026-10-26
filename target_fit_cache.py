"""Exact private target-model components, independent of raw-score diagnostics.

Only identical prepared binary observations reuse a main fit. Source-removal
fits have their own binary identities; General validation always sees the current
raw training scores. Private point parameters never enter the returned fit.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import scipy

from canonical_json import canonical_json_sha256
import sss_scoring_model as model

PREFIX = "analysis/private/source-aware-tiers/fit-components/"
VERSION = 1
_POINT_CONTEXTS: OrderedDict[str, dict] = OrderedDict()
_POINT_ARRAYS = ("parameters", "ability", "effect", "probability", "information")


class TargetFitPending(RuntimeError):
    """A verified private numerical component is ready for the next invocation."""
    def __init__(self, identity, ordinal):
        self.progress = {"identity": canonical_json_sha256(identity), "ordinal": ordinal}
        super().__init__("Target fit checkpointed at its invocation work deadline.")


def _yield_after_component(identity, ordinal):
    from analysis_budget import work_budget_available
    if not work_budget_available():
        raise TargetFitPending(identity, ordinal)


def _code_identity() -> str:
    from numeric_recipe import target_kernel_recipe
    return canonical_json_sha256(target_kernel_recipe())


def adopt_compatible_components(store, boards, fit, *, score_target, bootstrap_samples, seed):
    """Seed new component keys only after a verified f395 outer-fit lookup."""
    from numeric_recipe import legacy_component_code
    code = legacy_component_code()
    if code is None:
        return False
    data = model._prepare(boards, score_target)
    identity = _identity(data, score_target=score_target, bootstrap_samples=bootstrap_samples,
                         seed=seed, regularization_grid=fit["policy"]["regularizationGrid"])
    saved = _read(store, "main", {**identity, "code": code})
    if saved is None or not _usable_main(saved, data, seed) or saved["fit"]["policy"] != fit["policy"]:
        return False
    # The legacy record is private, digest verified, and bound to the same
    # ordered binary observations. Match every numerical chart field while
    # excluding only the separately refreshed source-removal evidence.
    for cid, chart in fit["charts"].items():
        public = copy.deepcopy(chart)
        public["sensitivity"].pop("sourceRemovalEffects", None)
        if public != saved["fit"]["charts"][cid]:
            return False
    _write(store, "main", identity, saved)
    return True


def _binary_inputs(data: Mapping[str, Any]) -> dict:
    # Include empty charts, non-main components, and ineligible-player counts:
    # they affect the returned eligibility and diagnostics even without a solve.
    result = {key: data[key] for key in ("charts", "players", "mainCharts",
        "components", "componentSizes", "eligiblePlayers", "inputPlayers")}
    result["allRecords"] = [list(record) for record in data["allRecords"]]
    return result


def _identity(data, *, score_target, bootstrap_samples, seed, regularization_grid):
    grid = sorted(set(float(value) for value in regularization_grid))
    if (not 0 < score_target <= 1_000_000 or bootstrap_samples < 0
            or not grid or any(not np.isfinite(value) or value <= 0 for value in grid)):
        raise ValueError("Invalid target component policy.")
    return {"version": VERSION, "code": _code_identity(), "binary": _binary_inputs(data),
        "target": score_target, "samples": bootstrap_samples, "seed": seed, "grid": grid}


def _path(kind, identity):
    return PREFIX + kind + "/" + canonical_json_sha256(identity) + ".json"


def _read(store, kind, identity):
    # Backend integrity failures are invalid optional data; operational storage
    # failures still propagate. Pinned recovery checkpoints use another reader.
    from pumbility_store import PumbilityArtifactIntegrityError
    try:
        record = store.get_json(_path(kind, identity))
    except (PumbilityArtifactIntegrityError, json.JSONDecodeError):
        return None
    if not isinstance(record, Mapping):
        return None
    try:
        if (record.get("version") != VERSION or record.get("identity") != identity
                or canonical_json_sha256(record.get("payload")) != record.get("payloadSha256")):
            return None
        payload = record.get("payload")
        fit = payload.get("fit") if kind == "main" else payload
        if (not isinstance(fit, Mapping)
                or any(not isinstance(fit.get(key), Mapping) for key in ("charts", "policy", "diagnostics"))
                or not isinstance(fit["diagnostics"].get("validation"), Mapping)
                or any(not isinstance(chart, Mapping) for chart in fit["charts"].values())
                or (kind == "main" and not isinstance(payload.get("context"), Mapping))):
            return None
        if kind == "main" and any(fit["policy"].get(field) != identity[part]
                for field, part in (("scoreTarget", "target"), ("bootstrapSamples", "samples"),
                                    ("regularizationGrid", "grid"))):
            return None
        return copy.deepcopy(payload)
    except (AttributeError, TypeError, ValueError):
        return None


def _write(store, kind, identity, payload):
    store.put_json(_path(kind, identity), {"version": VERSION, "identity": identity,
        "payload": payload, "payloadSha256": canonical_json_sha256(payload)})


class _BootstrapCheckpoint:
    """Optional batches are bound to the complete binary producer identity."""
    def __init__(self, store, identity):
        self.store, self.identity = store, identity
        self.path = _path("bootstrap", identity)

    def load(self):
        from pumbility_store import PumbilityArtifactIntegrityError
        try:
            record = self.store.get_json(self.path)
        except (PumbilityArtifactIntegrityError, json.JSONDecodeError):
            return None
        if (not isinstance(record, dict) or record.get("identity") != self.identity
                or record.get("payloadSha256") != canonical_json_sha256(record.get("payload"))):
            return None
        payload = record["payload"]
        required = {"parent", "result", "fitted", "sensitivity", "context", "alignment",
                    "completed", "rngState", "effects", "converged"}
        return payload if isinstance(payload, dict) and required <= payload.keys() else None

    def save(self, payload):
        _write(self.store, "bootstrap", self.identity, payload)
        # Checkpoint zero includes CV, the point fit and sensitivity fits. Later
        # checkpoints retain the exact next RNG state and all completed samples.
        _yield_after_component(self.identity, payload["completed"])


def pending_fit_progress(store, boards, *, score_target=990000,
                         bootstrap_samples=model.DEFAULT_BOOTSTRAP_SAMPLES,
                         seed=20261017, regularization_grid=model.REGULARIZATION_GRID,
                         source_boards=None, diagnostics_policy="full"):
    """Recover verified inner work before the enclosing runtime stall audit."""
    _validate_diagnostics_policy(diagnostics_policy)
    data = model._prepare(boards, score_target)
    identity = _identity(data, score_target=score_target, bootstrap_samples=bootstrap_samples,
                         seed=seed, regularization_grid=regularization_grid)
    saved = _read(store, "main", identity)
    if saved is not None and _usable_main(saved, data, seed):
        ordinal = bootstrap_samples + 1
        if diagnostics_policy == "full":
            source_identity = {"main": canonical_json_sha256(identity), "sources": {
                name: _binary_inputs(model._prepare(source, score_target))
                for name, source in sorted((source_boards or {}).items())}}
            source = _read(store, "source", source_identity)
            if (source is not None and source["policy"] == saved["fit"]["policy"]
                    and set(source["charts"]) == set(saved["fit"]["charts"])
                    and source["diagnostics"]["validation"] == saved["fit"]["diagnostics"]["validation"]):
                ordinal += 1
    else:
        bootstrap = _BootstrapCheckpoint(store, identity).load()
        if bootstrap is None:
            return None
        parent = canonical_json_sha256({"target": score_target, "samples": bootstrap_samples,
            "seed": seed, "grid": identity["grid"], "charts": data["charts"],
            "records": data["allRecords"], "players": data["players"],
            "inputPlayers": data["inputPlayers"], "components": data["components"]})
        ordinal = bootstrap["completed"]
        if (bootstrap.get("parent") != parent or type(ordinal) is not int
                or not 0 <= ordinal <= bootstrap_samples):
            raise ValueError("The private target progress belongs to incompatible inputs or policy.")
    return {"identity": canonical_json_sha256(identity), "ordinal": ordinal}


def _point_key(data, penalty):
    return canonical_json_sha256({"code": _code_identity(), "binary": _binary_inputs(data),
                                  "regularization": penalty})


def _remember_point(data, fit, context):
    point = context.get("pointFit")
    if not point or not len(data["y"]):
        return False
    sizes = (len(data["players"]) + len(data["mainCharts"]), len(data["players"]),
             len(data["mainCharts"]), len(data["y"]), len(data["mainCharts"]))
    restored = copy.deepcopy(point)
    for key, size in zip(_POINT_ARRAYS, sizes):
        restored[key] = np.asarray(point.get(key), dtype=float)
        if restored[key].shape != (size,) or not np.all(np.isfinite(restored[key])):
            raise ValueError("The private target point context has invalid dimensions or values.")
    if any(float(restored["effect"][i]) != fit["charts"][cid].get("effect")
           for i, cid in enumerate(data["mainCharts"])):
        raise ValueError("The private target point context differs from the captured fit.")
    key = _point_key(data, fit["policy"]["selectedRegularization"])
    _POINT_CONTEXTS[key] = restored
    _POINT_CONTEXTS.move_to_end(key)
    while len(_POINT_CONTEXTS) > 8:
        _POINT_CONTEXTS.popitem(last=False)
    return True


def cached_point_fit(data, fit):
    """Return an exact private cold-solve result, or let legacy callers replay."""
    key = _point_key(data, fit.get("policy", {}).get("selectedRegularization"))
    point = _POINT_CONTEXTS.get(key)
    if point is None:
        return None
    if any(float(point["effect"][i]) != fit["charts"][cid].get("effect")
           for i, cid in enumerate(data["mainCharts"])):
        return None
    _POINT_CONTEXTS.move_to_end(key)
    return copy.deepcopy(point)


def _usable_main(saved, data, seed):
    """Reject malformed optional context before registering or consuming it."""
    try:
        fit, context = saved["fit"], saved["context"]
        if set(fit["charts"]) != set(data["charts"]):
            return False
        if not len(data["y"]):
            return not context
        heldout = model._holdout(data["p"], data["c"], len(data["players"]), len(data["mainCharts"]), seed)
        if heldout.tolist() != context["heldout"]:
            return False
        for key in ("validationProbability", "validationBaseline"):
            values = np.asarray(context[key], dtype=float)
            if values.shape != (int(np.sum(heldout)),) or not np.all(np.isfinite(values)):
                return False
        required = {*_POINT_ARRAYS, "converged", "iterations", "gradientMaximum", "objective", "message"}
        return required <= context["pointFit"].keys() and _remember_point(data, fit, context)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return False


def hydrate_point_context(store, boards, fit, *, score_target=990000):
    """Load compatible private parameters without altering legacy fit payloads."""
    data = model._prepare(boards, score_target)
    if not len(data["y"]):
        return False
    policy = fit.get("policy", {})
    validation = fit.get("diagnostics", {}).get("validation", {})
    if "seed" not in validation or "selectedRegularization" not in policy:
        return False
    identity = _identity(data, score_target=score_target,
        bootstrap_samples=policy["bootstrapSamples"], seed=validation["seed"],
        regularization_grid=policy["regularizationGrid"])
    saved = _read(store, "main", identity)
    if saved is None or saved["fit"]["policy"] != policy or not _usable_main(saved, data, validation["seed"]):
        return False
    try:
        return _remember_point(data, fit, saved["context"])
    except (IndexError, KeyError, TypeError, ValueError):
        return False


def validated_point_context(data, fit, context, seed):
    """Check private cold-solve dimensions without registering it or solving."""
    if not len(data["y"]):
        return not context
    try:
        point = context["pointFit"]
        if not {*_POINT_ARRAYS, "converged", "iterations", "gradientMaximum", "objective", "message"} <= point.keys():
            return False
        sizes = (len(data["players"]) + len(data["mainCharts"]), len(data["players"]),
                 len(data["mainCharts"]), len(data["y"]), len(data["mainCharts"]))
        for key, size in zip(_POINT_ARRAYS, sizes):
            values = np.asarray(point[key], dtype=float)
            if values.shape != (size,) or not np.all(np.isfinite(values)):
                return False
        if any(float(point["effect"][i]) != fit["charts"][cid].get("effect")
               for i, cid in enumerate(data["mainCharts"])):
            return False
        heldout = model._holdout(data["p"], data["c"], len(data["players"]), len(data["mainCharts"]), seed)
        if heldout.tolist() != context["heldout"]:
            return False
        for key in ("validationProbability", "validationBaseline"):
            values = np.asarray(context[key], dtype=float)
            if values.shape != (int(np.sum(heldout)),) or not np.all(np.isfinite(values)):
                return False
        return True
    except (KeyError, IndexError, TypeError, ValueError):
        return False


def validated_bootstrap_checkpoint(data, identity, saved):
    """Validate the exact parent, accumulated effects and next RNG state."""
    try:
        parent = canonical_json_sha256({"target": identity["target"], "samples": identity["samples"],
            "seed": identity["seed"], "grid": identity["grid"], "charts": data["charts"],
            "records": data["allRecords"], "players": data["players"],
            "inputPlayers": data["inputPlayers"], "components": data["components"]})
        completed, converged = saved["completed"], saved["converged"]
        if (saved["parent"] != parent or type(completed) is not int or not 0 <= completed <= identity["samples"]
                or type(converged) is not int or not 0 <= converged <= completed):
            return False
        policy = saved["result"]["policy"]
        if (policy["scoreTarget"] != identity["target"] or policy["bootstrapSamples"] != identity["samples"]
                or policy["regularizationGrid"] != identity["grid"]
                or policy["selectedRegularization"] not in identity["grid"]
                or saved["result"]["diagnostics"]["validation"]["seed"] != identity["seed"]
                or set(saved["result"]["charts"]) != set(data["charts"])):
            return False
        counts = {cid: [0, 0] for cid in data["charts"]}
        for cid, _, outcome in data["allRecords"]:
            counts[cid][int(outcome)] += 1
        alignment = [i for i, cid in enumerate(data["mainCharts"]) if min(counts[cid]) >= 5]
        if not alignment:
            alignment = [i for i, cid in enumerate(data["mainCharts"])
                         if min(counts[cid]) > 0 and sum(counts[cid]) >= 5]
        alignment = alignment or list(range(len(data["mainCharts"])))
        if saved["alignment"] != alignment or len(saved["effects"]) != len(data["mainCharts"]):
            return False
        for values in saved["effects"]:
            if len(values) > converged or not np.all(np.isfinite(np.asarray(values, dtype=float))):
                return False
        rng = np.random.default_rng()
        rng.bit_generator.state = saved["rngState"]  # Validate without drawing.
        expected_rng = np.random.default_rng(identity["seed"] + 1)
        for _ in range(completed):
            expected_rng.integers(0, len(data["players"]), size=len(data["players"]))
        if canonical_json_sha256(expected_rng.bit_generator.state) != canonical_json_sha256(saved["rngState"]):
            return False
        sizes = (len(data["players"]) + len(data["mainCharts"]), len(data["players"]),
                 len(data["mainCharts"]), len(data["y"]), len(data["mainCharts"]))
        for key, size in zip(_POINT_ARRAYS, sizes):
            values = np.asarray(saved["fitted"][key], dtype=float)
            if (values.shape != (size,) or not np.all(np.isfinite(values))
                    or not np.array_equal(values, np.asarray(saved["context"]["pointFit"][key], dtype=float))):
                return False
        point_fit = copy.deepcopy(saved["result"])
        for i, cid in enumerate(data["mainCharts"]):
            point_fit["charts"][cid]["effect"] = saved["fitted"]["effect"][i]
        if not validated_point_context(data, point_fit, saved["context"], identity["seed"]):
            return False
        if set(saved["sensitivity"]) != {str(value) for value in identity["grid"]}:
            return False
        return all(np.asarray(value["effect"]).shape == (len(data["mainCharts"]),)
                   and np.all(np.isfinite(np.asarray(value["effect"], dtype=float)))
                   for value in saved["sensitivity"].values())
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return False


def _validate_diagnostics_policy(policy):
    if policy not in {"full", "product-only"}:
        raise ValueError("Unknown target diagnostics policy.")


def compatible_legacy_components(store, data, identity, *, legacy_producer_identity=None):
    """Inspect only the reviewed old producer at identical binary inputs/runtime.

    This is a read-only compatibility check. The full identity retains ordered
    observations, graph, target, samples, seed and regularization grid. An unknown
    source/kernel/runtime is a cache miss, never a general rekey permission.
    """
    from numeric_recipe import legacy_component_code, legacy_outer_identities
    # The original inner key did not encode Python. Its verified generation's
    # outer producer receipt supplies that missing numerical-runtime binding.
    if (identity.get("target") != 995000 or identity.get("samples") != 200
            or identity.get("seed") != 20261018 or identity.get("grid") != [.01, .05, .2, 1.]
            or legacy_producer_identity not in legacy_outer_identities()):
        return None
    code = legacy_component_code()
    if code is None or code == identity["code"]:
        return None
    candidate = {**identity, "code": code}
    main = _read(store, "main", candidate)
    if main is not None:
        fit = main["fit"]
        bootstrap = fit["diagnostics"].get("bootstrap", {})
        if (fit["policy"].get("modelVersion") == model.MODEL_VERSION
                and fit["policy"].get("minimumPlayerCharts") == model.MINIMUM_PLAYER_CHARTS
                and fit["policy"].get("weighting") == model.WEIGHTING
                and fit["diagnostics"].get("validation", {}).get("seed") == identity["seed"]
                and (not len(data["y"]) or (fit["diagnostics"].get("converged")
                     and bootstrap.get("completed") == identity["samples"] and bootstrap.get("failed") == 0))
                and validated_point_context(data, fit, main["context"], identity["seed"])):
            return {"kind": "main", "identity": candidate, "payload": main}
    bootstrap = _BootstrapCheckpoint(store, candidate).load()
    if bootstrap is not None and validated_bootstrap_checkpoint(data, candidate, bootstrap):
        return {"kind": "bootstrap", "identity": candidate, "payload": bootstrap}
    return None


def adopt_reviewed_retained_components(store, boards, *, score_target=995000,
                                       bootstrap_samples=200, seed=20261018,
                                       regularization_grid=model.REGULARIZATION_GRID,
                                       legacy_producer_identity=None):
    """Copy verified legacy retained work to its current key; keep originals."""
    grid = sorted(set(float(value) for value in regularization_grid))
    if (score_target != 995000 or bootstrap_samples != 200 or seed != 20261018
            or grid != [.01, .05, .2, 1.]):
        return False
    data = model._prepare(boards, score_target)
    identity = _identity(data, score_target=score_target, bootstrap_samples=bootstrap_samples,
                         seed=seed, regularization_grid=grid)
    main = _read(store, "main", identity)
    if main is not None and _usable_main(main, data, seed):
        return False
    saved = compatible_legacy_components(store, data, identity,
                                         legacy_producer_identity=legacy_producer_identity)
    if saved is None:
        return False
    if saved["kind"] == "bootstrap":
        existing = _BootstrapCheckpoint(store, identity).load()
        if (existing is not None and validated_bootstrap_checkpoint(data, identity, existing)
                and existing["completed"] >= saved["payload"]["completed"]):
            return False
    _write(store, saved["kind"], identity, saved["payload"])
    return True


def _product_fit(fit):
    """Keep numerical results while declaring omitted research diagnostics."""
    result = copy.deepcopy(fit)
    diagnostics = result["diagnostics"]
    diagnostics.pop("sourceRemovalVersion", None)
    diagnostics.pop("generalBenchmarkPolicy", None)
    diagnostics["sourceRemoval"] = {}
    diagnostics["optionalDiagnostics"] = {
        "version": 1, "policy": "product-only", "sourceRemoval": "not-computed",
        "generalTargetBenchmark": "not-computed",
    }
    validation = diagnostics["validation"]
    validation["generalTargetBenchmark"] = None
    validation["generalTargetBenchmarkReason"] = "Not computed by the routine product-only diagnostics policy."
    validation.pop("externalGeneralPredictionDiagnostic", None)
    for chart in result["charts"].values():
        chart.get("sensitivity", {}).pop("sourceRemovalEffects", None)
    return result


def load_or_fit_target(store, boards, *, score_target=990000,
                       bootstrap_samples=model.DEFAULT_BOOTSTRAP_SAMPLES, seed=20261017,
                       regularization_grid=model.REGULARIZATION_GRID,
                       general_predictor=None, general_predictions=None, source_boards=None,
                       diagnostics_policy="full", legacy_producer_identity=None):
    """Return the unchanged fit schema, reusing only mathematically identical work."""
    _validate_diagnostics_policy(diagnostics_policy)
    data = model._prepare(boards, score_target)
    identity = _identity(data, score_target=score_target, bootstrap_samples=bootstrap_samples,
                         seed=seed, regularization_grid=regularization_grid)
    if diagnostics_policy == "product-only" and legacy_producer_identity is not None:
        adopt_reviewed_retained_components(store, boards, score_target=score_target,
            bootstrap_samples=bootstrap_samples, seed=seed, regularization_grid=regularization_grid,
            legacy_producer_identity=legacy_producer_identity)
    saved = _read(store, "main", identity)
    if saved is not None and not _usable_main(saved, data, seed):
        saved = None
    if saved is None:
        context = {}
        fit = model.fit_target_model(boards, score_target=score_target,
            bootstrap_samples=bootstrap_samples, seed=seed,
            regularization_grid=regularization_grid, _private_context=context,
            _bootstrap_checkpoint=_BootstrapCheckpoint(store, identity))
        saved = {"fit": fit, "context": context}
        _write(store, "main", identity, saved)
    fit, context = saved["fit"], saved["context"]
    if not len(data["y"]):
        return _product_fit(fit) if diagnostics_policy == "product-only" else fit
    _yield_after_component(identity, bootstrap_samples + 1)
    _remember_point(data, fit, context)
    if diagnostics_policy == "product-only":
        return _product_fit(fit)
    source_inputs = source_boards or {}
    source_identity = {"main": canonical_json_sha256(identity), "sources": {
        name: _binary_inputs(model._prepare(source, score_target))
        for name, source in sorted(source_inputs.items())}}
    result = _read(store, "source", source_identity)
    if result is not None and (result["policy"] != fit["policy"]
            or set(result["charts"]) != set(fit["charts"])
            or result["diagnostics"]["validation"] != fit["diagnostics"]["validation"]):
        result = None
    if result is None:
        result = model.refresh_source_sensitivity(fit, source_inputs, score_target=score_target)
        _write(store, "source", source_identity, result)
    _yield_after_component(identity, bootstrap_samples + 2)
    heldout = model._holdout(data["p"], data["c"], len(data["players"]), len(data["mainCharts"]), seed)
    if heldout.tolist() != context["heldout"]:
        raise ValueError("The private target validation split differs from its captured context.")
    if np.any(heldout):
        model._general_validation(result["diagnostics"]["validation"], data, boards, heldout,
            np.asarray(context["validationProbability"]), np.asarray(context["validationBaseline"]),
            general_predictor=general_predictor, general_predictions=general_predictions)
    return result
