"""Local schema-50 integration and preservation checks for the frozen SSS fit."""
from __future__ import annotations

import copy
import math
from typing import Any, Mapping

import numpy as np

from piu_misgrade_analyzer import difficulty_effect_band
from sss_scoring import (MODES, RANK_FIELDS, _composite, payload_sha256,
                         rank_variants, validate_sss_variants)
from tier_difficulty import EVIDENCE_ORDER
from tier_postprocessing import FIT_FIELDS, _rank_secondary, compress_difficulty, compression_factor

SCHEMA_VERSION = 50
INTEGRATION_METHOD = "frozen-sss-second-pass-official-d26-clearing-v1"
NO_INTERVAL = "unavailable-after-sss-refinement-v1"


def _point(value: float | None, level: int) -> dict:
    delta = None if value is None else value - level - .5
    band, label = (None, None) if delta is None else difficulty_effect_band(delta)
    return dict(estimatedDifficulty=value, difficultyDelta=delta, effectBandRank=band, effectBand=label)


def _general_composite(row: dict) -> None:
    scoring, clearing = row, row["tierMetrics"]["clearing"]
    metric = row["tierMetrics"]["pumbility"]
    value = None if scoring["estimatedDifficulty"] is None or clearing["estimatedDifficulty"] is None else (scoring["estimatedDifficulty"] + clearing["estimatedDifficulty"]) / 2
    metric.update(_point(value, row["level"]), aggregation="average",
                  scoringSupportCount=scoring["nContributors"], clearingSupportCount=clearing["ratedClearCount"],
                  evidenceStatus="Unrated" if value is None else EVIDENCE_ORDER[min(
                      EVIDENCE_ORDER.index(scoring["evidenceStatus"]), EVIDENCE_ORDER.index(clearing["evidenceStatus"]))])
    raw_s, raw_c = scoring["initialFit"]["estimatedDifficulty"], clearing["initialFit"]["estimatedDifficulty"]
    metric["initialFit"].update(_point(None if raw_s is None or raw_c is None else (raw_s + raw_c) / 2, row["level"]))


def apply_refined_tiers(base: Mapping[str, Any], prepared: Mapping[str, Any], fits: Mapping[str, Any],
                        capture: Mapping[str, Any], *, seed: int = 20261018,
                        bootstrap_samples: int = 1000) -> tuple[dict, dict]:
    from sss_scoring_refinement import apply_sss_refinement
    from official_high_level_clearing import apply_official_high_level_clearing

    validate_sss_variants(base)
    result, audit = apply_sss_refinement(base, prepared, fits, seed=seed)
    for _, key in MODES:
        for row, original in zip(result[key], base[key]):
            row["refinementBaseline"] = {"sssPumbility": copy.deepcopy(original["pumbilityVariants"]["sss"])}
            if key == "doubles" and row["level"] >= 26:
                row["refinementBaseline"].update(clearing=copy.deepcopy(original["tierMetrics"]["clearing"]),
                    generalPumbility=copy.deepcopy(original["tierMetrics"]["pumbility"]))
    clearing_method = apply_official_high_level_clearing(result, capture, bootstrap_samples=bootstrap_samples)
    result["summary"]["method"]["officialHighLevelClearing"] = clearing_method
    for _, key in MODES:
        for row in result[key]:
            if key == "doubles" and row["level"] >= 26:
                _general_composite(row)
            row["pumbilityVariants"]["sss"] = _composite(row, row["scoringVariants"]["sss"])
            if row["scoringVariants"]["sss"]["scoringMethod"] != "sss-target-rasch-v1":
                row["pumbilityVariants"]["sss"]["intervalMethod"] = NO_INTERVAL
        _rank_secondary(result[key], "clearing")
        _rank_secondary(result[key], "pumbility")
        for row in result[key]:
            row["pumbilityVariants"]["general"] = copy.deepcopy(row["tierMetrics"]["pumbility"])
        rank_variants(result[key])
    result["schemaVersion"] = SCHEMA_VERSION
    result["summary"]["method"]["refinedTiers"] = {"version": 1, "method": INTEGRATION_METHOD,
        "baseSchemaVersion": 49, "basePayloadSha256": payload_sha256(base),
        "localOnly": True, "clearingDispatch": {"mode": "Double", "minimumLevel": 26},
        "pumbilityAggregation": "average", "compressionApplications": 1}
    validate_refined_tiers(result, base)
    audit["officialHighLevelClearing"] = clearing_method
    return result, audit


def recover_first_pass(payload: Mapping[str, Any]) -> dict:
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("Refined tier recovery requires schema 50.")
    base = copy.deepcopy(payload)
    base["schemaVersion"] = 49
    for name in ("sssRefinement", "officialHighLevelClearing", "refinedTiers"):
        base["summary"]["method"].pop(name, None)
    for _, key in MODES:
        for row in base[key]:
            row["scoringVariants"]["sss"] = row["scoringVariants"]["sss"]["scoringFirstPass"]
            original = row.pop("refinementBaseline")
            row["pumbilityVariants"]["sss"] = original["sssPumbility"]
            if "clearing" in original:
                row["tierMetrics"]["clearing"] = original["clearing"]
                row["tierMetrics"]["pumbility"] = original["generalPumbility"]
                row["pumbilityVariants"]["general"] = copy.deepcopy(original["generalPumbility"])
    return base


def validate_refined_tiers(payload: Mapping[str, Any], frozen: Mapping[str, Any] | None = None) -> dict:
    from sss_scoring_refinement import NO_SSS_ENDPOINT_POLICY

    base = recover_first_pass(payload)
    method = payload["summary"]["method"]["refinedTiers"]
    if method != {"version": 1, "method": INTEGRATION_METHOD, "baseSchemaVersion": 49,
                  "basePayloadSha256": payload_sha256(base), "localOnly": True,
                  "clearingDispatch": {"mode": "Double", "minimumLevel": 26},
                  "pumbilityAggregation": "average", "compressionApplications": 1}:
        raise ValueError("Refined tiers policy or frozen baseline differs.")
    if frozen is not None and base != frozen:
        raise ValueError("Recovery differs from the frozen first pass.")
    validate_sss_variants(base)
    refinement = payload["summary"]["method"]["sssRefinement"]
    if refinement["version"] == 2 and refinement.get("noSssEndpointPolicy") != NO_SSS_ENDPOINT_POLICY:
        raise ValueError("No-SSS endpoint policy differs.")
    if payload.get("coop") != base.get("coop"):
        raise ValueError("Co-op changed.")
    for _, key in MODES:
        for row, original in zip(payload[key], base[key]):
            s, c = row["scoringVariants"]["sss"], row["tierMetrics"]["clearing"]
            if refinement["version"] == 2 and s.get("scoringEstimateBranch") == "no-sss" and s["estimatedDifficulty"] is not None:
                details = s["scoringRefinement"]
                board, boundary = details["fullBoard"], details["boundary"]
                scores = board.get("topScores", [])
                if (details["version"] != 2 or len(scores) != min(3, board["records"])
                        or not scores or any(isinstance(score, bool) or not isinstance(score, (int, float))
                            or not math.isfinite(score) or not 0 <= score < 990000 for score in scores)
                        or scores != sorted(scores, reverse=True) or scores[0] != board["highestScore"]
                        or scores[-1] < board["lowestScore"]
                        or boundary.get("endpointPolicy") != "mean-top-3-available-v1"
                        or boundary.get("endpointScoreCount") != len(scores)
                        or abs(boundary["endpointScore"] - sum(scores) / len(scores)) > 1e-8
                        or abs(boundary["gapPoints"] - (990000 - sum(scores) / len(scores))) > 1e-8):
                    raise ValueError("No-SSS estimate differs from the top-three available mean.")
            if row["scoringVariants"]["general"] != original["scoringVariants"]["general"]:
                raise ValueError("General Scoring changed.")
            # Scoring compatibility fields must be untouched as well.
            for field in original:
                if field not in ("scoringVariants", "pumbilityVariants", "tierMetrics") and row[field] != original[field]:
                    raise ValueError(f"General chart field changed: {field}")
            if not (key == "doubles" and row["level"] >= 26) and c != original["tierMetrics"]["clearing"]:
                raise ValueError("Clearing outside Double26+ changed.")
            for metric in (s, c):
                for field in ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"):
                    if field not in metric["initialFit"]:
                        continue
                    expected = compress_difficulty(metric["initialFit"][field], row["level"], compression_factor(payload))
                    if metric.get(field) != expected:
                        raise ValueError("Tier was not compressed exactly once.")
            for goal, scoring in (("general", row), ("sss", s)):
                p = row["pumbilityVariants"][goal]
                expected = None if scoring["estimatedDifficulty"] is None or c["estimatedDifficulty"] is None else (scoring["estimatedDifficulty"] + c["estimatedDifficulty"]) / 2
                if p["estimatedDifficulty"] != expected or p["clearingSupportCount"] != c["ratedClearCount"]:
                    raise ValueError("Pumbility is not the full-precision component mean.")
                status = "Unrated" if expected is None else EVIDENCE_ORDER[min(EVIDENCE_ORDER.index(scoring["evidenceStatus"]), EVIDENCE_ORDER.index(c["evidenceStatus"]))]
                if p["evidenceStatus"] != status:
                    raise ValueError("Pumbility evidence differs from its weaker component.")
            if s["scoringMethod"] != "sss-target-rasch-v1" and any(s[f"difficultyCi95{side}"] is not None or row["pumbilityVariants"]["sss"][f"difficultyCi95{side}"] is not None for side in ("Low", "High")):
                raise ValueError("A second-pass point retained obsolete intervals.")
        expected_rows = copy.deepcopy(payload[key])
        rank_variants(expected_rows)
        _rank_secondary(expected_rows, "clearing")
        _rank_secondary(expected_rows, "pumbility")
        for row, expected in zip(payload[key], expected_rows):
            for variant in ("scoringVariants", "pumbilityVariants"):
                for field in RANK_FIELDS:
                    if row[variant]["sss"].get(field) != expected[variant]["sss"].get(field):
                        raise ValueError("Stale refined variant ranks.")
            for metric in ("clearing", "pumbility"):
                for field in ("levelRank", "levelComparisonCharts"):
                    if row["tierMetrics"][metric].get(field) != expected["tierMetrics"][metric].get(field):
                        raise ValueError("Stale secondary ranks.")
    for level in sorted({r["level"] for r in payload["doubles"] if r["level"] >= 26}):
        values = [r["tierMetrics"]["clearing"]["estimatedDifficulty"] for r in payload["doubles"] if r["level"] == level and r["tierMetrics"]["clearing"]["estimatedDifficulty"] is not None]
        if values and not math.isclose(float(np.median(values)), level + .5, abs_tol=1e-10):
            raise ValueError("Official Clearing folder median differs from midpoint.")
    return base
