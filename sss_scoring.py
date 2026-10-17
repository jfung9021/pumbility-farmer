"""Local target-attainment Scoring variants over an unchanged schema-48 baseline.

Player identities and source observations stay private. The public overlay contains
only chart aggregates, target effects, calibration and conditional uncertainty.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

import numpy as np

from scoring_targets import SSS, ScoringTarget
from official_cross_level_calibration import fit_cross_level_curve, inverse_chart_cost
from piu_misgrade_analyzer import difficulty_effect_band
from scoring_percentile import collect_percentile_scores
from tier_postprocessing import FIT_FIELDS, _rank_scoring, compress_difficulty
from tier_difficulty import EVIDENCE_ORDER

SCHEMA_VERSION = 49
BASE_SCHEMA_VERSION = 48
SCORE_TARGET = 990_000
MODEL_METHOD = "sss-target-rasch-v1"
INTERVAL_METHOD = "player-cluster-refit-fixed-calibration-v1"
PUMBILITY_INTERVAL_METHOD = "player-bootstrap-fixed-clearing-and-calibration-v1"
GENERAL_FIELDS = (*FIT_FIELDS, "initialFit", "evidenceStatus", "nContributors", "nPlayersScored",
                  "scoringMethod", "scoringUnratedReason", "scoringExtrapolated", "scoringProvisional")
RANK_FIELDS = ("levelRank", "levelComparisonCharts", "modeRank", "levelPercentile", "relativeGroup", "relativeGroupRank")
MODES = (("Single", "singles"), ("Double", "doubles"))


def payload_sha256(value: Any) -> str:
    """Private replay identity (canonical Python JSON, including its final newline)."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def general_scoring_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(row[key]) for key in GENERAL_FIELDS if key in row}


def prepare_target_observations(phoenix1: Mapping[str, Any], phoenix2: Mapping[str, Any],
                                merged: Mapping[str, Any], current_charts: Sequence[Mapping[str, Any]], *, exclusions=None) -> dict[str, Any]:
    """Reuse current successful-record selection; never join the two identity cohorts."""
    if exclusions is not None:
        phoenix1 = exclusions.filter_snapshot(phoenix1, "phoenix1")
        phoenix2 = exclusions.filter_snapshot(phoenix2, "phoenix2")
    catalog = {str(c.get("id", c.get("chartId"))): dict(c) for c in current_charts}
    submitted, submitted_sources = {}, {name: {} for name in ("phoenix1", "phoenix2")}
    selected = collect_percentile_scores(phoenix1, phoenix2)
    individual = {
        "phoenix1": collect_percentile_scores(phoenix1, {**phoenix2, "scores": []}),
        "phoenix2": collect_percentile_scores({**phoenix1, "scores": []}, phoenix2),
    }
    for (player, cid), (_, score) in sorted(selected.items()):
        chart = catalog.get(cid)
        if chart and chart.get("type") in ("Single", "Double") and chart.get("level", 0) >= 16:
            submitted.setdefault(cid, {})["submitted:" + player] = score
    for source, records in individual.items():
        for (player, cid), (_, score) in sorted(records.items()):
            if cid in submitted:
                submitted_sources[source].setdefault(cid, {})["submitted:" + player] = score
    old = {str(c["id"]): c for c in phoenix1.get("charts", [])}
    submitted_metadata = {}
    for cid, board in submitted.items():
        chart = catalog[cid]
        retained = {selected[player.removeprefix("submitted:"), cid][0] for player in board}
        comparable = True
        reasons = []
        for source in retained:
            origin = old.get(cid) if source == "phoenix1" else chart
            counts = (chart.get("noteCount"), origin.get("noteCount") if origin else None)
            known = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in counts)
            if not known or counts[0] != counts[1]:
                comparable = False
                reasons.append("unknown-chart-comparability" if not known else "changed-chart-comparability")
        submitted_metadata[cid] = {"anchorEligible": comparable, "provisional": not comparable,
                                   "comparability": "comparable" if comparable else "unknown-or-changed",
                                   "provisionalReasons": sorted(set(reasons)), "possiblyTruncated": False}
    official_sources = {mix: {} for mix in ("Phoenix", "Phoenix2")}
    for cid, board in merged.get("_privateProvenance", {}).items():
        for player, record in board.items():
            for observation in record["observations"]:
                source = official_sources[observation["mix"]].setdefault(cid, {})
                source[player] = max(source.get(player, -math.inf), observation["score"])
    return {"catalog": catalog, "submitted": {"boards": submitted, "charts": submitted_metadata,
                "sourceBoards": submitted_sources},
            "official": {"boards": copy.deepcopy(merged.get("boards", {})),
                "charts": copy.deepcopy(merged.get("charts", {})), "sourceBoards": official_sources},
            "sourceDates": copy.deepcopy(merged.get("sourceDates", {})),
            "manifestSha256": merged.get("manifestSha256")}


def fit_target_curve(anchors: Sequence[Mapping[str, Any]], *, minimum_level: int) -> dict[str, Any]:
    """Translate the existing exponential helper's origin without changing General."""
    if minimum_level not in (16, 20):
        raise ValueError("SSS calibration minimum level must be 16 or 20.")
    shifted = [{**anchor, "level": int(anchor["level"]) - minimum_level + 20} for anchor in anchors]
    try:
        curve = fit_cross_level_curve(shifted)
    except ValueError as exc:
        actual_levels = sorted(int(anchor["level"]) for anchor in anchors)
        shifted_levels = sorted(int(anchor["level"]) for anchor in shifted)
        detail = str(exc).replace(str(shifted_levels), str(actual_levels)).replace(
            "including level 20", f"including level {minimum_level}")
        raise ValueError(f"SSS calibration requires three strictly increasing supported anchors including level {minimum_level}: {detail}") from exc
    for anchor in curve["anchors"]:
        anchor["level"] += minimum_level - 20
        anchor["difficulty"] += minimum_level - 20
    curve.update(minimumAnchorLevel=minimum_level, referenceMinimumPlayers=20,
                 referenceMinimumPerOutcome=5, referenceMinimumCharts=5,
                 supportedRange=[a + minimum_level - 20 for a in curve["supportedRange"]])
    return curve


def reference_anchors(catalog: Mapping[str, Mapping[str, Any]], metadata: Mapping[str, Mapping[str, Any]],
                      fitted: Mapping[str, Any], *, mode: str, minimum_level: int) -> tuple[list, dict]:
    pools, exclusions = defaultdict(list), defaultdict(lambda: defaultdict(int))
    for cid, chart in sorted(catalog.items()):
        if chart.get("type") != mode or chart.get("level", 0) < minimum_level:
            continue
        level, estimate, meta = int(chart["level"]), fitted["charts"].get(cid), metadata.get(cid, {})
        if not estimate or not estimate.get("ratingEligible"):
            exclusions[level]["ineligibleFit"] += 1
        elif estimate["usableContributors"] < 20:
            exclusions[level]["insufficientContributors"] += 1
        elif min(estimate["targetSuccesses"], estimate["belowTarget"]) < 5:
            exclusions[level]["insufficientOutcomeSupport"] += 1
        elif not meta.get("anchorEligible", False):
            exclusions[level]["chartComparability"] += 1
        else:
            pools[level].append((cid, estimate["effect"]))
    anchors = [{"level": level, "cost": float(np.median([v for _, v in pool])),
                "referenceCharts": len(pool), "chartIds": [cid for cid, _ in pool]}
               for level, pool in sorted(pools.items()) if len(pool) >= 5]
    diagnostics = {str(level): {"referenceCharts": len(pools[level]), "excluded": dict(exclusions[level])}
                   for level in sorted(set(pools) | set(exclusions))}
    return anchors, diagnostics


def _derived(value: float | None, level: int) -> dict[str, Any]:
    delta = None if value is None else value - level - .5
    rank, band = (None, None) if delta is None else difficulty_effect_band(delta)
    return {"estimatedDifficulty": value, "difficultyDelta": delta, "effectBandRank": rank, "effectBand": band}


def _empty_scoring(source: str, target: ScoringTarget = SSS) -> dict[str, Any]:
    return {**{key: None for key in FIT_FIELDS}, "evidenceStatus": "Unrated", "nContributors": 0,
        "nPlayersScored": 0, "scoringMethod": target.model_method, "scoringTarget": target.score,
        "scoringTargetSuccesses": 0, "scoringBelowTarget": 0, "scoringSource": source,
        "scoringComponent": None, "scoringMainComponent": False, "scoringChartEffect": None,
        "scoringChartEffectCi95Low": None, "scoringChartEffectCi95High": None,
        "scoringInformation": None, "scoringProvisionalReasons": [], "scoringProvisional": True,
        "scoringExtrapolated": False, "scoringUnratedReason": "No observed successful best scores.",
        "scoringComparability": "no-evidence", "scoringPossiblyTruncated": False,
        "scoringSourceStates": {}, "scoringFitConverged": False,
        "scoringBootstrapValidReplicates": 0, "intervalMethod": INTERVAL_METHOD}


def _target_metric(row: Mapping[str, Any], source: str, estimate: Mapping[str, Any] | None,
                   metadata: Mapping[str, Any], curve: Mapping[str, Any] | None,
                   calibration_error: str | None, *, target: ScoringTarget = SSS) -> dict[str, Any]:
    metric, level = _empty_scoring(source, target), int(row["level"])
    if estimate:
        reasons = set(estimate.get("provisionalReasons", [])) | set(metadata.get("provisionalReasons", []))
        truncated = any(state.get("possiblyTruncated", False) for state in metadata.get("sourceStates", {}).values())
        if truncated:
            reasons.add("possibly-truncated-source")
        if metadata.get("provisional", True):
            reasons.add("chart-comparability")
        effect = estimate.get("effect")
        metric.update(nContributors=estimate["usableContributors"],
            nPlayersScored=estimate["usableContributors"], scoringTargetSuccesses=estimate["targetSuccesses"],
            scoringBelowTarget=estimate["belowTarget"], scoringComponent=estimate["componentId"],
            scoringMainComponent=estimate["calibratedComponent"], scoringChartEffect=effect,
            scoringInformation=estimate.get("information"), scoringComparability=metadata.get("comparability", "unknown"),
            scoringPossiblyTruncated=truncated, scoringSourceStates=copy.deepcopy(metadata.get("sourceStates", {})),
            scoringFitConverged=bool(estimate.get("fitConverged", estimate.get("ratingEligible", False))),
            scoringBootstrapValidReplicates=int(estimate.get("bootstrapUsableSamples", 0)))
        if not estimate["usableContributors"]:
            reason = "No eligible observed successful best scores; each player needs four eligible charts."
        elif not estimate["targetSuccesses"] or not estimate["belowTarget"]:
            reason = f"One-sided evidence: all observed scores are below {target.grade}." if not estimate["targetSuccesses"] else f"One-sided evidence: all observed scores already reached {target.grade}."
        elif not estimate["calibratedComponent"]:
            reason = "Chart is outside the calibrated shared-player component."
        elif estimate["usableContributors"] < 5:
            reason = "At least five usable contributors are required."
        elif not estimate["ratingEligible"]:
            reason = "Target fit is not converged and stable for this chart."
        elif not curve:
            reason = calibration_error or "Target calibration is unavailable."
        else:
            raw = inverse_chart_cost(effect, curve)
            extrapolated = effect < curve["anchors"][0]["cost"] or effect > curve["anchors"][-1]["cost"]
            if extrapolated:
                reasons.add("boundary-extrapolation")
            if level not in {a["level"] for a in curve["anchors"]}:
                reasons.add("unsupported-folder-anchor")
            initial = {**{key: None for key in FIT_FIELDS}, **_derived(raw, level)}
            for side, field in (("Low", "effectLow"), ("High", "effectHigh")):
                endpoint = estimate.get(field)
                if endpoint is not None:
                    metric[f"scoringChartEffectCi95{side}"] = endpoint
                    value = inverse_chart_cost(endpoint, curve)
                    initial[f"difficultyCi95{side}"] = value
                    initial[f"difficultyDeltaCi95{side}"] = value - level - .5
            metric.update(_derived(compress_difficulty(raw, level), level),
                evidenceStatus="Provisional" if reasons else "Published", scoringExtrapolated=extrapolated)
            for side in ("Low", "High"):
                endpoint = compress_difficulty(initial[f"difficultyCi95{side}"], level)
                metric[f"difficultyCi95{side}"] = endpoint
                metric[f"difficultyDeltaCi95{side}"] = None if endpoint is None else endpoint - level - .5
            metric["initialFit"] = initial
            reason = None
        metric.update(scoringUnratedReason=reason, scoringProvisionalReasons=sorted(reasons),
                      scoringProvisional=bool(reasons) or reason is not None)
    metric.setdefault("initialFit", {key: copy.deepcopy(metric[key]) for key in FIT_FIELDS})
    return metric


def _composite(row: Mapping[str, Any], scoring: Mapping[str, Any]) -> dict[str, Any]:
    clearing, level = row["tierMetrics"]["clearing"], int(row["level"])
    s, c = scoring["estimatedDifficulty"], clearing["estimatedDifficulty"]
    value = None if s is None or c is None else (s + c) / 2
    ranks = {key: None for key in RANK_FIELDS}
    result = {**_derived(value, level), **ranks, "evidenceStatus": "Unrated", "aggregation": "average",
        "scoringSupportCount": scoring["nContributors"], "clearingSupportCount": int(clearing.get("ratedClearCount") or 0),
        "intervalMethod": PUMBILITY_INTERVAL_METHOD,
        "unratedReason": scoring["scoringUnratedReason"] if s is None else "Clearing difficulty is unavailable." if c is None else None}
    raw_s = scoring["initialFit"]["estimatedDifficulty"]
    raw_c = clearing["initialFit"]["estimatedDifficulty"]
    result["initialFit"] = {**_derived(None if raw_s is None or raw_c is None else (raw_s + raw_c) / 2, level), **ranks}
    if value is not None:
        result["evidenceStatus"] = EVIDENCE_ORDER[min(EVIDENCE_ORDER.index(scoring["evidenceStatus"]),
                                                        EVIDENCE_ORDER.index(clearing["evidenceStatus"]))]
    for side in ("Low", "High"):
        endpoint = scoring[f"difficultyCi95{side}"]
        result[f"difficultyCi95{side}"] = None if value is None or endpoint is None else (endpoint + c) / 2
        result[f"difficultyDeltaCi95{side}"] = None if result[f"difficultyCi95{side}"] is None else result[f"difficultyCi95{side}"] - level - .5
        raw_endpoint = scoring["initialFit"][f"difficultyCi95{side}"]
        result["initialFit"][f"difficultyCi95{side}"] = None if value is None or raw_endpoint is None else (raw_endpoint + raw_c) / 2
        result["initialFit"][f"difficultyDeltaCi95{side}"] = None if result["initialFit"][f"difficultyCi95{side}"] is None else result["initialFit"][f"difficultyCi95{side}"] - level - .5
    return result


def rank_variants(rows: Sequence[dict[str, Any]], *, goal: str = "sss") -> None:
    for initial in (False, True):
        proxies = [{**row, **row["scoringVariants"][goal],
                    **(row["scoringVariants"][goal]["initialFit"] if initial else {})} for row in rows]
        _rank_scoring(proxies)
        for row, proxy in zip(rows, proxies):
            metric = row["scoringVariants"][goal]
            target = metric["initialFit"] if initial else metric
            target.update({key: proxy[key] for key in RANK_FIELDS})
    folders = defaultdict(list)
    for row in rows:
        metric = row["pumbilityVariants"][goal]
        if metric["estimatedDifficulty"] is not None:
            folders[row["level"]].append(row)
    for group in folders.values():
        group.sort(key=lambda row: (row["pumbilityVariants"][goal]["estimatedDifficulty"],
            row["pumbilityVariants"][goal]["initialFit"]["estimatedDifficulty"], row.get("songName") or "", row["chartId"]))
        for rank, row in enumerate(group, 1):
            metric = row["pumbilityVariants"][goal]
            metric.update(levelRank=rank, levelComparisonCharts=len(group))
            metric["initialFit"].update(levelRank=rank, levelComparisonCharts=len(group))


def apply_sss_variants(base: Mapping[str, Any], prepared: Mapping[str, Any],
                       fits: Mapping[str, Mapping[str, Any]], *, source_hashes: Mapping[str, str],
                       bootstrap_samples: int, seed: int) -> dict[str, Any]:
    """Append target metrics, preserving every existing General/Clearing field."""
    if base.get("schemaVersion") != BASE_SCHEMA_VERSION:
        raise ValueError("SSS variants require the pinned schema-48 General baseline.")
    result = copy.deepcopy(base)
    result["schemaVersion"] = SCHEMA_VERSION
    calibrations, diagnostics = {}, {}
    for source, minimum in (("submitted", 16), ("official", 20)):
        calibrations[source], diagnostics[source] = {}, {}
        for mode, key in MODES:
            fit = fits[source][mode]
            anchors, exclusions = reference_anchors(prepared["catalog"], prepared[source]["charts"], fit,
                                                     mode=mode, minimum_level=minimum)
            try:
                curve, error = fit_target_curve(anchors, minimum_level=minimum), None
            except ValueError as exc:
                curve, error = None, str(exc)
            calibrations[source][mode] = curve
            diagnostics[source][mode] = {"calibrationError": error, "referenceExclusions": exclusions,
                "referenceAnchors": copy.deepcopy(anchors),
                "model": copy.deepcopy(fit["diagnostics"]), "modelPolicy": copy.deepcopy(fit["policy"])}
            for row in result.get(key, []):
                if (row["level"] >= 20) != (source == "official"):
                    continue
                cid = str(row["chartId"])
                estimate = fit["charts"].get(cid)
                if estimate:
                    estimate = {**estimate, "fitConverged": bool(fit["diagnostics"].get("converged"))}
                scoring = _target_metric(row, source, estimate, prepared[source]["charts"].get(cid, {}), curve, error)
                row["scoringVariants"] = {"general": general_scoring_view(row), "sss": scoring}
                row["pumbilityVariants"] = {"general": copy.deepcopy(row["tierMetrics"]["pumbility"]),
                                             "sss": _composite(row, scoring)}
    for _, key in MODES:
        rank_variants(result.get(key, []))
    method = result["summary"]["method"]
    method["sssScoring"] = {"version": 1, "method": MODEL_METHOD, "target": SCORE_TARGET,
        "meaning": "conditional-target-attainment-among-observed-best-scores", "baseSchemaVersion": BASE_SCHEMA_VERSION,
        "basePayloadSha256": payload_sha256(base), "sourceHashes": dict(source_hashes),
        "sourceDates": dict(prepared.get("sourceDates", {})), "officialManifestSha256": prepared.get("manifestSha256"),
        "minimumPlayerCharts": 4, "minimumContributors": 5, "minimumReferenceContributors": 20,
        "minimumReferencePerOutcome": 5, "minimumReferenceCharts": 5,
        "weighting": "one-total-weight-per-player-in-source-mode", "bootstrapSamples": bootstrap_samples, "seed": seed,
        "intervalMethod": INTERVAL_METHOD, "intervalExclusions": ["anchor-uncertainty", "clearing-uncertainty", "effort", "selective-observation"],
        "sourceBoundary": {"submitted": [16, 19], "officialMinimum": 20},
        "compression": {"factor": .5, "reference": "current-official-level-plus-0.5", "applications": 1},
        "calibration": calibrations, "diagnostics": diagnostics}
    method["sssPumbility"] = {"version": 1, "aggregation": "average", "requiresBoth": True,
        "calculation": "(SSS scoring difficulty + unchanged clearing difficulty) / 2 before rounding",
        "componentValues": "after-midpoint-compression", "intervalMethod": PUMBILITY_INTERVAL_METHOD,
        "evidence": "weaker component status; separate scoring and clearing support counts"}
    validate_sss_variants(result)
    return result


def recover_general_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("SSS recovery requires schema 49.")
    base = copy.deepcopy(payload)
    base["schemaVersion"] = BASE_SCHEMA_VERSION
    for key in ("sssScoring", "sssPumbility"):
        base["summary"]["method"].pop(key, None)
    for _, key in MODES:
        for row in base.get(key, []):
            row.pop("scoringVariants", None)
            row.pop("pumbilityVariants", None)
    return base


def validate_sss_variants(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Verify preservation, compression, support, means and independent ranks."""
    base = recover_general_payload(payload)
    method = payload["summary"]["method"].get("sssScoring", {})
    if (method.get("method") != MODEL_METHOD or method.get("target") != SCORE_TARGET
            or method.get("version") != 1
            or method.get("baseSchemaVersion") != BASE_SCHEMA_VERSION
            or method.get("basePayloadSha256") != payload_sha256(base)):
        raise ValueError("SSS model identity or General baseline identity differs.")
    declared = {"minimumPlayerCharts": 4, "minimumContributors": 5, "minimumReferenceContributors": 20,
                "minimumReferencePerOutcome": 5, "minimumReferenceCharts": 5,
                "weighting": "one-total-weight-per-player-in-source-mode", "intervalMethod": INTERVAL_METHOD,
                "compression": {"factor": .5, "reference": "current-official-level-plus-0.5", "applications": 1}}
    if any(method.get(key) != value for key, value in declared.items()):
        raise ValueError("SSS target evidence or compression policy differs.")
    composite_method = payload["summary"]["method"].get("sssPumbility", {})
    if (composite_method.get("version") != 1 or composite_method.get("aggregation") != "average"
            or composite_method.get("requiresBoth") is not True
            or composite_method.get("intervalMethod") != PUMBILITY_INTERVAL_METHOD):
        raise ValueError("SSS Pumbility policy differs.")
    for source, minimum in (("submitted", 16), ("official", 20)):
        for mode, _ in MODES:
            curve = method.get("calibration", {}).get(source, {}).get(mode)
            if curve is not None:
                expected = fit_target_curve(curve["anchors"], minimum_level=minimum)
                if curve != expected or any(a["referenceCharts"] < 5 for a in curve["anchors"]):
                    raise ValueError("SSS calibration differs from its declared anchors.")
    for _, key in MODES:
        for row, original in zip(payload.get(key, []), base.get(key, [])):
            if row.get("scoringVariants", {}).get("general") != general_scoring_view(original):
                raise ValueError("General Scoring variant differs from the compatibility view.")
            if row.get("pumbilityVariants", {}).get("general") != original["tierMetrics"]["pumbility"]:
                raise ValueError("General Pumbility variant differs from the compatibility view.")
            s, p, level = row["scoringVariants"]["sss"], row["pumbilityVariants"]["sss"], row["level"]
            if (s["scoringTarget"] != SCORE_TARGET or s["scoringMethod"] != MODEL_METHOD
                    or s["nContributors"] != s["scoringTargetSuccesses"] + s["scoringBelowTarget"]):
                raise ValueError("SSS target support differs.")
            if s["scoringSource"] != ("official" if level >= 20 else "submitted"):
                raise ValueError("SSS source boundary differs.")
            mode = "Single" if key == "singles" else "Double"
            curve = method["calibration"][s["scoringSource"]][mode]
            if s["estimatedDifficulty"] is not None:
                if not curve or not s["scoringFitConverged"]:
                    raise ValueError("SSS point estimate lacks a converged calibrated fit.")
                if not _same(s["initialFit"]["estimatedDifficulty"], inverse_chart_cost(s["scoringChartEffect"], curve)):
                    raise ValueError("SSS initial fit differs from the target effect calibration.")
                for side in ("Low", "High"):
                    effect = s[f"scoringChartEffectCi95{side}"]
                    expected = None if effect is None else inverse_chart_cost(effect, curve)
                    if not _same(s["initialFit"][f"difficultyCi95{side}"], expected):
                        raise ValueError("SSS interval differs from its fixed target calibration.")
                expected_status = "Provisional" if s["scoringProvisionalReasons"] else "Published"
                if s["evidenceStatus"] != expected_status or s["scoringUnratedReason"] is not None:
                    raise ValueError("SSS evidence status differs from its support.")
            elif not s["scoringUnratedReason"] or s["evidenceStatus"] != "Unrated":
                raise ValueError("An unavailable SSS estimate requires its explicit Unrated reason.")
            for side in ("", "Ci95Low", "Ci95High"):
                field = "estimatedDifficulty" if not side else "difficulty" + side
                if not _same(s[field], compress_difficulty(s["initialFit"][field], level)):
                    raise ValueError("SSS estimate or interval was not compressed exactly once.")
            for target in (s, s["initialFit"]):
                if any(not _same(target[k], v) for k, v in _derived(target["estimatedDifficulty"], level).items()):
                    raise ValueError("SSS delta or effect band differs from its full-precision difficulty.")
            if s["estimatedDifficulty"] is not None and (not s["scoringTargetSuccesses"] or not s["scoringBelowTarget"] or s["nContributors"] < 5 or not s["scoringMainComponent"]):
                raise ValueError("SSS point estimate lacks two-sided usable evidence.")
            expected = _composite(row, s)
            for field, value in expected.items():
                if field in RANK_FIELDS or field == "initialFit":
                    continue
                if not _same(p.get(field), value):
                    raise ValueError(f"SSS Pumbility component mean differs at {field}.")
            for field, value in expected["initialFit"].items():
                if field not in RANK_FIELDS and not _same(p["initialFit"].get(field), value):
                    raise ValueError("SSS Pumbility initial fit differs from its component mean.")
        expected_rows = copy.deepcopy(payload.get(key, []))
        rank_variants(expected_rows)
        for row, expected in zip(payload.get(key, []), expected_rows):
            for variant in ("scoringVariants", "pumbilityVariants"):
                for field in RANK_FIELDS:
                    if row[variant]["sss"].get(field) != expected[variant]["sss"].get(field):
                        raise ValueError("SSS ranks differ.")
                    if row[variant]["sss"]["initialFit"].get(field) != expected[variant]["sss"]["initialFit"].get(field):
                        raise ValueError("SSS initial-fit ranks differ.")
    return base


def _same(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isfinite(actual) and abs(actual - expected) < 1e-8
    return actual == expected
