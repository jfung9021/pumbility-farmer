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


def _code_identity() -> str:
    return canonical_json_sha256({
        "model": hashlib.sha256(Path(model.__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "cache": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "numpy": np.__version__, "scipy": scipy.__version__,
    })


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


def load_or_fit_target(store, boards, *, score_target=990000,
                       bootstrap_samples=model.DEFAULT_BOOTSTRAP_SAMPLES, seed=20261017,
                       regularization_grid=model.REGULARIZATION_GRID,
                       general_predictor=None, general_predictions=None, source_boards=None):
    """Return the unchanged fit schema, reusing only mathematically identical work."""
    data = model._prepare(boards, score_target)
    identity = _identity(data, score_target=score_target, bootstrap_samples=bootstrap_samples,
                         seed=seed, regularization_grid=regularization_grid)
    saved = _read(store, "main", identity)
    if saved is not None and not _usable_main(saved, data, seed):
        saved = None
    if saved is None:
        context = {}
        fit = model.fit_target_model(boards, score_target=score_target,
            bootstrap_samples=bootstrap_samples, seed=seed,
            regularization_grid=regularization_grid, _private_context=context)
        saved = {"fit": fit, "context": context}
        _write(store, "main", identity, saved)
    fit, context = saved["fit"], saved["context"]
    if not len(data["y"]):
        return fit  # The reference path returns before source/General diagnostics.
    _remember_point(data, fit, context)
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
    heldout = model._holdout(data["p"], data["c"], len(data["players"]), len(data["mainCharts"]), seed)
    if heldout.tolist() != context["heldout"]:
        raise ValueError("The private target validation split differs from its captured context.")
    if np.any(heldout):
        model._general_validation(result["diagnostics"]["validation"], data, boards, heldout,
            np.asarray(context["validationProbability"]), np.asarray(context["validationBaseline"]),
            general_predictor=general_predictor, general_predictions=general_predictions)
    return result
