"""Recoverable, target-local S/D20 width calibration over frozen schema 59.

This is a display-scale policy, not another statistical fit. Conditional intervals
use the same frozen monotone curve as points; raw fits and evidence stay intact.
"""
from __future__ import annotations

import copy
import math
from statistics import median
from typing import Any, Mapping, Sequence

from canonical_json import canonical_json_sha256 as digest
from sss_scoring import MODES, RANK_FIELDS, _composite, _derived, rank_variants
from target_tail_policy import finite

SCHEMA_VERSION = 60
SCRIPT_VERSION = "6.24.0-level20-target-width"
NAMESPACE = "level20ScoringWidth"
METHOD = "level20-target-quantile-width-v1"
STAGE = "level20WidthBaseline"
GOALS = ("ss", "ssplus", "sss", "sssplus")
FIELDS = ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High")
INTERVAL_METHOD = "conditional-fixed-level20-width-no-new-evidence-v1"
POLICY = {"version": 1, "method": METHOD, "level": 20, "goals": list(GOALS),
    "minimumCharts": 20, "targetCoreFraction": .65, "coreFractionRange": [.6, .7],
    "coreQuantiles": [.175, .825], "coreBoundaries": [20., 21.], "midpoint": 20.5,
    "ordinaryLowerLimit": 18.7, "supportedLowerLimit": 18.1, "extremeLowerLimit": 18.,
    "deepTailLimit": 5, "extremeTailLimit": 1, "ordinaryUpperLimit": 22.4,
    "exceptionUpperLimit": 23., "minimumDeepContributors": 100,
    "minimumExtremeContributors": 200, "intervals": INTERVAL_METHOD,
    "source": "stored-full-precision-pre-final-tail-with-published-tie-blocks-preserved",
    "ties": "whole-value-blocks-no-rank-jitter", "dataFetches": 0, "modelRefits": 0}


def _need(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _rows(payload):
    return [row for _, key in MODES for row in payload.get(key, [])]


def _quantile(values: Sequence[float], p: float) -> float:
    t = (len(values)-1)*p
    lo, hi = math.floor(t), math.ceil(t)
    return values[lo] + (t-lo)*(values[hi]-values[lo])


def displayed_bucket(value: float) -> float:
    return math.trunc(value*10+1e-9)/10


def distribution(values: Sequence[float]) -> dict:
    n = len(values)
    return {"ratedCharts": n, "coreCharts": sum(20 <= displayed_bucket(v) < 21 for v in values),
        "coreFraction": sum(20 <= displayed_bucket(v) < 21 for v in values)/n if n else None,
        "deepTailCharts": sum(18 <= displayed_bucket(v) <= 18.6 for v in values),
        "extremeTailCharts": sum(displayed_bucket(v) == 18 for v in values),
        "minimum": min(values, default=None), "maximum": max(values, default=None),
        "median": median(values) if n else None, "distinctPoints": len(set(values))}


def source_values(metric: Mapping[str, Any]) -> tuple[dict, str]:
    """Read saved final-tail inputs; never invert a rounded/asymptotic output.

    Source-aware SSS+ finalFolderWidth already rewrites scoringTailPolicy's
    preTail fields after its width expansion. Its selected exceptions bypass that
    last tail, so their current (full precision) values are the proper input.
    """
    tail = metric.get("scoringTailPolicy", {})
    original = {field: metric.get(field) for field in FIELDS}
    if finite(original["estimatedDifficulty"]):
        _need("preTailDifficulty" in tail and finite(tail["preTailDifficulty"]),
              "Level-20 width requires saved full-precision final-tail evidence.")
    values = {field: tail.get("preTail" + field[0].upper() + field[1:]) for field in FIELDS}
    # The tail field is preTailDifficulty, not preTailEstimatedDifficulty.
    values["estimatedDifficulty"] = tail.get("preTailDifficulty")
    return values, ("stored-final-width-pre-tail" if metric.get("finalFolderWidth") else "stored-pre-tail")


def prepared_width_points(rows: Sequence[Mapping[str, Any]], goal: str) -> list[dict]:
    """Use exact saved inputs, retaining exact ties made by older hard caps."""
    points = []
    for row in rows:
        metric = row["scoringVariants"][goal]
        if row["level"] != 20 or not finite(metric.get("estimatedDifficulty")):
            continue
        values, source = source_values(metric)
        _need(all(value is None or finite(value) for value in values.values()), "Invalid saved width evidence.")
        points.append({"id": str(row["chartId"]), "input": values["estimatedDifficulty"], "metric": metric,
                       "values": values, "source": source})
    blocks = {}
    for point in points:
        blocks.setdefault(point["metric"]["estimatedDifficulty"], []).append(point)
    for block in blocks.values():
        if len(block) > 1:
            tied_input = median(p["input"] for p in block)
            for point in block:
                point["values"]["estimatedDifficulty"] = point["input"] = tied_input
                point["source"] += "-published-tie-block"
    ordered = sorted(points, key=lambda p: (p["metric"]["estimatedDifficulty"], p["id"]))
    _need(all(a["input"] <= b["input"] for a, b in zip(ordered, ordered[1:])),
          "Saved level-20 inputs invert existing chart ordering.")
    return points


def _tail(value: float, x: float, y: float, slope: float, limit: float) -> tuple[float, float]:
    """Positive-slope C1 rational continuation; never clips distinct inputs."""
    span = abs(y-limit)
    distance = abs(value-x)
    ratio = distance*slope/span
    direction = 1 if limit > y else -1
    return y + direction*span*(ratio/(1+ratio)), slope/(1+ratio)**2


def _value_and_slope(value: float, parameters: Mapping[str, Any], guards=True) -> tuple[float, float]:
    knots = parameters["knots"]
    if value < knots[0]["input"]:
        first, second = knots[:2]
        slope = (second["output"]-first["output"])/(second["input"]-first["input"])
        result = _tail(value, first["input"], first["output"], slope, parameters["lowerLimit"])
        if guards:
            for guard in parameters["lowerGuards"]:
                if value < guard["input"]:
                    result = _tail(value, guard["input"], guard["output"], guard["slope"], guard["limit"])
        return result
    if value > knots[-1]["input"]:
        first, second = knots[-2:]
        slope = (second["output"]-first["output"])/(second["input"]-first["input"])
        result = _tail(value, second["input"], second["output"], slope, parameters["upperLimit"])
        if guards:
            for guard in parameters["upperGuards"]:
                if value > guard["input"]:
                    result = _tail(value, guard["input"], guard["output"], guard["slope"], guard["limit"])
        return result
    for first, second in zip(knots, knots[1:]):
        if value <= second["input"]:
            slope = (second["output"]-first["output"])/(second["input"]-first["input"])
            return first["output"]+(value-first["input"])*slope, slope
    raise ValueError("Invalid level-20 width knots.")


def width_value(value: float | None, parameters: Mapping[str, Any]) -> float | None:
    if value is None:
        return None
    _need(finite(value), "Level-20 width input must be finite.")
    return value if parameters["basis"] != "folder" else _value_and_slope(value, parameters)[0]


def _supported_lower(metric: Mapping[str, Any], *, extreme=False) -> bool:
    tail = metric.get("scoringTailPolicy", {})
    # Reuse affirmative existing evidence, never interpret a one-sided easy fit
    # or an extrapolated point alone as proof of an extreme chart.
    evidence = tail.get("exceptionSelected") and tail.get("exceptionEligible")
    boundary = metric.get("scoringSpreadPolicy", {}).get("boundaryEvidence", {})
    evidence = evidence or (tail.get("exceptionSelected") and boundary.get("eligible"))
    upper = tail.get("preTailDifficultyCi95High")
    ordinary = bool(evidence and metric.get("nContributors", 0) >= (200 if extreme else 100)
        and not metric.get("scoringPossiblyTruncated")
        and metric.get("scoringComparability") in ("same", "comparable", "single-source-known"))
    if not extreme:
        return ordinary
    # A whole conditional interval must lie at/below 18 before allowing an
    # 18.0 display. One-sided diagnostics have no such interval and cannot pass.
    return ordinary and finite(upper) and upper <= 18.


def width_parameters(points: Sequence[Mapping[str, Any]]) -> dict:
    ordered = sorted(points, key=lambda p: (p["input"], p["id"]))
    values = [p["input"] for p in ordered]
    result = {"basis": "insufficient-charts", "ratedCharts": len(values), "knots": [],
        "lowerLimit": 18.7, "upperLimit": 22.4, "lowerGuards": [], "upperGuards": [],
        "deepEligibleChartIds": [], "extremeEligibleChartIds": [], "upperEligibleChartIds": []}
    if len(values) < POLICY["minimumCharts"]:
        return result
    low, high = (_quantile(values, q) for q in POLICY["coreQuantiles"])
    middle_low, middle_high = values[(len(values)-1)//2], values[len(values)//2]
    if not low < middle_low <= middle_high < high:
        result["basis"] = "tied-core"
        return result
    center = (middle_low+middle_high)/2
    # Protect both median-defining charts symmetrically. An even central gap is
    # scaled by the smaller side slope, rather than allowing a nonlinear median
    # shift or splitting an existing tie.
    half_gap = (middle_high-middle_low)/2
    target_gap = min(.5*half_gap/(center-low), .5*half_gap/(high-center))
    knots = [(low, 20.), (middle_low, 20.5-target_gap),
             (middle_high, 20.5+target_gap), (high, 21.)]
    result.update(basis="folder", knots=[{"input": x, "output": y} for i, (x, y) in enumerate(knots)
        if i == 0 or x != knots[i-1][0]])
    # Only the already-selected contiguous upper exception blocks may cross the
    # ordinary 22.4 asymptote; no new upper exceptions or budget are invented.
    upper_prefix = []
    for point in reversed(ordered):
        block = [p for p in ordered if p["input"] == point["input"]]
        if point in upper_prefix:
            continue
        if not all(p["metric"].get("scoringTailPolicy", {}).get("exceptionSelected") for p in block):
            break
        upper_prefix.extend(block)
    if upper_prefix and len(upper_prefix) < len(ordered):
        boundary = ordered[-len(upper_prefix)-1]["input"]
        if boundary > high:
            output, slope = _value_and_slope(boundary, result)
            result["upperGuards"].append({"input": boundary, "output": output, "slope": slope, "limit": 23.})
            result["upperEligibleChartIds"] = sorted(p["id"] for p in upper_prefix)
    prefix = []
    for point in ordered:
        block = [p for p in ordered if p["input"] == point["input"]]
        if point in prefix:
            continue
        if len(prefix)+len(block) > 5 or not all(_supported_lower(p["metric"]) for p in block):
            break
        prefix.extend(block)
    if prefix and len(prefix) < len(ordered):
        boundary = ordered[len(prefix)]["input"]
        if boundary < low:
            output, slope = _value_and_slope(boundary, result)
            result["lowerGuards"].append({"input": boundary, "output": output, "slope": slope, "limit": 18.1})
            result["deepEligibleChartIds"] = sorted(p["id"] for p in prefix)
            if values[0] < values[1] and _supported_lower(ordered[0]["metric"], extreme=True):
                boundary = values[1]
                output, slope = _value_and_slope(boundary, result)
                result["lowerGuards"].append({"input": boundary, "output": output, "slope": slope, "limit": 18.})
                result["extremeEligibleChartIds"] = [ordered[0]["id"]]
    return result


def _snapshot(metric):
    names = (*FIELDS, "difficultyDelta", "difficultyDeltaCi95Low", "difficultyDeltaCi95High",
             "effectBand", "effectBandRank", "intervalMethod", *RANK_FIELDS)
    return {**{key: copy.deepcopy(metric[key]) for key in names if key in metric},
        "initialFitRanks": {key: metric.get("initialFit", {})[key] for key in RANK_FIELDS
                            if key in metric.get("initialFit", {})}}


def _copy_active(base):
    result = dict(base)
    result["summary"] = copy.deepcopy(base["summary"])
    for _, key in MODES:
        result[key] = []
        for old in base.get(key, []):
            row = dict(old)
            for name in ("scoringVariants", "pumbilityVariants"):
                row[name] = dict(old[name])
                for goal in GOALS:
                    if goal in old[name]:
                        # Only mutable top fields/ranks need copies. All large
                        # historical evidence trees remain immutable shared data.
                        row[name][goal] = dict(old[name][goal])
                        if "initialFit" in old[name][goal]:
                            row[name][goal]["initialFit"] = dict(old[name][goal]["initialFit"])
            result[key].append(row)
    return result


def _patch(before, after):
    return {"changed": {key: copy.deepcopy(value) for key, value in before.items()
                        if key not in after or value != after[key]}, "added": sorted(set(after)-set(before))}


def _apply(base: Mapping[str, Any]) -> dict:
    from retained_tiers import is_retained, PUBLIC_SCHEMA_VERSION, VERSION
    retained = is_retained(base)
    result = _copy_active(base)
    folders = {}
    available = [goal for goal in GOALS if any(goal in row.get("scoringVariants", {}) for row in _rows(base))]
    for mode, key in MODES:
        for goal in available:
            rows = [row for row in result[key] if row["level"] == 20
                    and goal in row["scoringVariants"]
                    and finite(row["scoringVariants"][goal].get("estimatedDifficulty"))]
            points = prepared_width_points(rows, goal)
            parameters = width_parameters(points)
            group = f"{mode}:{goal}:20"
            parameters["before"] = distribution([p["metric"]["estimatedDifficulty"] for p in points])
            for point in points:
                metric = point["metric"]
                original = _snapshot(metric)
                # Unsupported folders remain byte-identical numerically; retain
                # their published conditional intervals rather than undoing tails.
                values = point["values"] if parameters["basis"] == "folder" else {f: metric.get(f) for f in FIELDS}
                for field in FIELDS:
                    metric[field] = width_value(values[field], parameters)
                metric.update(_derived(metric["estimatedDifficulty"], 20))
                for side in ("Low", "High"):
                    endpoint = metric[f"difficultyCi95{side}"]
                    metric[f"difficultyDeltaCi95{side}"] = None if endpoint is None else endpoint-20.5
                if parameters["basis"] == "folder" and any(metric.get(f) is not None for f in FIELDS[1:]):
                    metric["intervalMethod"] = INTERVAL_METHOD
                metric["scoringLevel20Width"] = {"version": 1, "method": METHOD, "group": group,
                    "source": point["source"], "original": original, "input": copy.deepcopy(values),
                    "final": {f: metric.get(f) for f in FIELDS}}
            parameters["after"] = distribution([p["metric"]["estimatedDifficulty"] for p in points])
            after = parameters["after"]
            parameters["coreTargetMet"] = bool(after["ratedCharts"] and .6 <= after["coreFraction"] <= .7)
            parameters["diagnostics"] = ([] if parameters["coreTargetMet"] else [
                "tied-or-sparse-population-preserved" if parameters["basis"] != "folder" else "ties-prevent-core-target"])
            folders[group] = parameters
            # Only affected composites are rebuilt. Other levels' numbers and
            # interval labels stay unchanged even when their global ranks move.
            for row in rows:
                previous = row["pumbilityVariants"][goal]
                composite = _composite(row, row["scoringVariants"][goal])
                composite["intervalMethod"] = (INTERVAL_METHOD if parameters["basis"] == "folder"
                                                else previous.get("intervalMethod"))
                row["pumbilityVariants"][goal] = composite
            goal_rows = [r for r in result[key] if goal in r["scoringVariants"]]
            rank_variants(goal_rows, goal=goal)
    for _, key in MODES:
        for row, old in zip(result[key], base.get(key, [])):
            stage = {}
            for name in ("scoringVariants", "pumbilityVariants"):
                changed = {}
                for goal in available:
                    if goal in old[name]:
                        patch = _patch(old[name][goal], row[name][goal])
                        if patch["changed"] or patch["added"]:
                            changed[goal] = patch
                if changed:
                    stage[name] = changed
            if stage:
                row[STAGE] = stage
    result["schemaVersion"] = PUBLIC_SCHEMA_VERSION if retained else SCHEMA_VERSION
    result["summary"]["scriptVersion"] = VERSION if retained else SCRIPT_VERSION
    result["summary"]["method"][NAMESPACE] = {"version": 1, "method": METHOD,
        "baseSchemaVersion": base["schemaVersion"], "baseScriptVersion": base["summary"].get("scriptVersion"),
        "basePayloadSha256": digest(base), "policy": {**copy.deepcopy(POLICY), **({"goals": available} if retained else {})}, "goals": available,
        "folders": folders, "dataFetches": 0, "modelRefits": 0,
        "stageOrder": ["stored-final-tail-input", "fixed-folder-quantile-width",
                       "conditional-interval-projection", "affected-component-means", "target-ranks"]}
    return result


def apply_level20_scoring_width(payload: Mapping[str, Any]) -> dict:
    _need(payload.get("schemaVersion") == 59, "Level-20 width requires frozen schema 59.")
    _need(not payload.get("tierListView"), "Level-20 width requires complete stored chart evidence.")
    _need(NAMESPACE not in payload["summary"]["method"]
          and all(STAGE not in row and all("scoringLevel20Width" not in metric for metric in row.get("scoringVariants", {}).values())
                  for row in _rows(payload)), "Level-20 width cannot be applied twice.")
    _need(len({str(row["chartId"]) for row in _rows(payload)}) == len(_rows(payload)), "Duplicate level-20 chart identity.")
    result = _apply(payload)
    validate_level20_scoring_width(result, full=False)
    return result


def apply_retained_level20_scoring_width(payload: Mapping[str, Any]) -> dict:
    """Apply the same SSS+ width without executing the retired SSS tail branch."""
    from retained_tiers import is_retained, validate_marker
    _need(payload.get("schemaVersion") == 57 and is_retained(payload),
          "Retained level-20 width requires its schema-57 predecessor.")
    validate_marker(payload)
    _need(not payload.get("tierListView") and NAMESPACE not in payload["summary"]["method"]
          and all(STAGE not in row for row in _rows(payload)), "Retained width cannot be applied twice.")
    _need(len({str(row["chartId"]) for row in _rows(payload)}) == len(_rows(payload)), "Duplicate level-20 chart identity.")
    result = _apply(payload)
    validate_level20_scoring_width(result, full=False)
    return result


def recover_level20_scoring_width(payload: Mapping[str, Any]) -> dict:
    from retained_tiers import is_retained, PUBLIC_SCHEMA_VERSION
    retained = is_retained(payload)
    _need(payload.get("schemaVersion") == (PUBLIC_SCHEMA_VERSION if retained else SCHEMA_VERSION), "Level-20 recovery schema differs.")
    namespace = payload.get("summary", {}).get("method", {}).get(NAMESPACE, {})
    _need(namespace.get("version") == 1 and namespace.get("baseSchemaVersion") == (57 if retained else 59),
          "Missing level-20 predecessor identity.")
    result = _copy_active(payload)
    result["schemaVersion"] = namespace["baseSchemaVersion"]
    result["summary"]["method"].pop(NAMESPACE)
    prior = namespace.get("baseScriptVersion")
    if prior is None:
        result["summary"].pop("scriptVersion", None)
    else:
        result["summary"]["scriptVersion"] = prior
    for row in _rows(result):
        stage = row.pop(STAGE, {})
        for name, goals in stage.items():
            for goal, patch in goals.items():
                metric = row[name][goal]
                for field in patch["added"]:
                    metric.pop(field, None)
                metric.update(copy.deepcopy(patch["changed"]))
    return result


def validate_public_level20_scoring_width(payload: Mapping[str, Any]) -> None:
    from retained_tiers import is_retained, PUBLIC_SCHEMA_VERSION, validate_marker
    retained = is_retained(payload)
    if retained:
        validate_marker(payload)
    _need(payload.get("schemaVersion") == (PUBLIC_SCHEMA_VERSION if retained else SCHEMA_VERSION), "Incorrect level-20 public schema.")
    namespace = payload.get("summary", {}).get("method", {}).get(NAMESPACE, {})
    expected_policy = {**POLICY, **({"goals": ["sssplus"]} if retained else {})}
    _need(namespace.get("version") == 1 and namespace.get("method") == METHOD and namespace.get("policy") == expected_policy
          and namespace.get("baseSchemaVersion") == (57 if retained else 59), "Incorrect level-20 public policy.")
    rows = _rows(payload)
    available = [goal for goal in GOALS if any(goal in row.get("scoringVariants", {}) for row in rows)]
    _need(namespace.get("goals") == available, "Incorrect level-20 target availability.")
    for group, parameters in namespace.get("folders", {}).items():
        mode, goal, level = group.split(":")
        _need(mode in ("Single", "Double") and goal in GOALS and level == "20", "Incorrect level-20 folder.")
        selected = [row for row in payload.get("singles" if mode == "Single" else "doubles", [])
                    if row["level"] == 20 and finite(row.get("scoringVariants", {}).get(goal, {}).get("estimatedDifficulty"))]
        before, after, ordered = [], [], []
        for row in selected:
            metric = row["scoringVariants"][goal]
            evidence = metric.get("scoringLevel20Width", {})
            _need(evidence.get("version") == 1 and evidence.get("method") == METHOD and evidence.get("group") == group,
                  "Missing level-20 chart projection evidence.")
            for field in FIELDS:
                expected = width_value(evidence["input"].get(field), parameters)
                _need(metric.get(field) == expected == evidence["final"].get(field), "Incorrect level-20 point or interval.")
            value = metric["estimatedDifficulty"]
            _need(metric.get("difficultyDelta") == value-20.5, "Incorrect level-20 delta.")
            low, high = metric.get("difficultyCi95Low"), metric.get("difficultyCi95High")
            _need(low is None or high is None or low <= high, "Reversed level-20 conditional interval.")
            composite = row["pumbilityVariants"][goal]
            clearing = row["tierMetrics"]["clearing"]["estimatedDifficulty"]
            _need(composite.get("estimatedDifficulty") == (None if clearing is None else (value+clearing)/2),
                  "Incorrect level-20 Pumbility mean.")
            for side in ("Low", "High"):
                endpoint = metric.get("difficultyCi95"+side)
                _need(composite.get("difficultyCi95"+side) == (None if endpoint is None or clearing is None else (endpoint+clearing)/2),
                      "Incorrect level-20 Pumbility interval.")
            before.append(evidence["original"]["estimatedDifficulty"])
            after.append(value)
            ordered.append((evidence["original"]["estimatedDifficulty"], value))
        _need(parameters.get("before") == distribution(before) and parameters.get("after") == distribution(after),
              "Incorrect level-20 distribution counts.")
        ordered.sort()
        _need(all(a[1] <= b[1] and (a[0] != b[0] or a[1] == b[1]) for a, b in zip(ordered, ordered[1:])),
              "Level-20 ordering or ties changed.")
        if parameters["basis"] == "folder":
            _need(math.isclose(median(after), 20.5, abs_tol=1e-10), "Level-20 median moved.")
            _need(parameters["after"]["deepTailCharts"] <= 5 and parameters["after"]["extremeTailCharts"] <= 1
                  and all(18 <= value <= 23 for value in after), "Level-20 tail budget exceeded.")
    expected_groups = {f"{mode}:{goal}:20" for mode, _ in MODES for goal in namespace.get("goals", [])}
    _need(set(namespace.get("folders", {})) == expected_groups, "Missing level-20 folder population.")
    for row in rows:
        for goal, metric in row.get("scoringVariants", {}).items():
            _need("scoringLevel20Width" not in metric or (row["level"] == 20 and goal in GOALS),
                  "Level-20 policy escaped its target folder.")


def validate_level20_scoring_width(payload: Mapping[str, Any], *, full: bool = True) -> dict:
    from retained_tiers import is_retained, VERSION
    base = recover_level20_scoring_width(payload)
    namespace = payload["summary"]["method"][NAMESPACE]
    _need(payload["summary"].get("scriptVersion") == (VERSION if is_retained(payload) else SCRIPT_VERSION)
          and digest(base) == namespace.get("basePayloadSha256"), "Level-20 predecessor recovery differs.")
    _need(payload == _apply(base), "Level-20 points, evidence, intervals, composites, ranks or preserved fields differ.")
    validate_public_level20_scoring_width(payload)
    if full:
        if is_retained(payload):
            from submitted_sssplus_calibration import validate_submitted_sssplus_calibration
            from source_aware_tiers import validate_source_aware_tiers
            validate_source_aware_tiers(validate_submitted_sssplus_calibration(base))
        else:
            from sss_tail_tiers import validate_sss_tail_tiers
            validate_sss_tail_tiers(base)
    return base
