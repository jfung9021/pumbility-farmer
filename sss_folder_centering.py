"""Schema-51 SSS folder location normalization over frozen schema-50 results."""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from statistics import median
from typing import Any, Mapping

from refined_tiers import NO_INTERVAL, validate_refined_tiers
from sss_scoring import MODES, RANK_FIELDS, _composite, _derived, payload_sha256, rank_variants
from tier_postprocessing import compress_difficulty, compression_factor

SCHEMA_VERSION = 51
CENTERING_METHOD = "sss-final-folder-median-centering-v1"
MINIMUM_CHARTS = 5


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def centering_folders(base: Mapping[str, Any], *, goal: str = "sss",
                      require_mixed_first_pass: bool = True) -> dict[str, dict[str, dict]]:
    """Freeze offsets; final scale calibration can use refined-population support."""
    result = {}
    for mode, key in MODES:
        groups = defaultdict(list)
        for row in base.get(key, []):
            if row["level"] >= 20:
                groups[int(row["level"])].append(row)
        records, supported = {}, {}
        for level, rows in sorted(groups.items()):
            mixed, population, first_values = [], [], []
            for row in rows:
                metric = row["scoringVariants"][goal]
                first = metric["scoringFirstPass"]
                if (_finite(first["estimatedDifficulty"]) and first["evidenceStatus"] != "Unrated"
                        and first.get("scoringTargetSuccesses", 0) > 0 and first.get("scoringBelowTarget", 0) > 0):
                    mixed.append(str(row["chartId"]))
                    first_values.append(first["estimatedDifficulty"])
                if _finite(metric["estimatedDifficulty"]) and metric["evidenceStatus"] != "Unrated":
                    population.append((str(row["chartId"]), metric["estimatedDifficulty"]))
            original = median(value for _, value in population) if population else None
            record = {"source": "official", "mode": mode, "level": level,
                "mixedFirstPassChartIds": sorted(mixed), "populationChartIds": sorted(cid for cid, _ in population),
                "mixedFirstPassCount": len(mixed), "ratedChartCount": len(population), "medianBefore": original}
            mixed_supported = (len(mixed) >= MINIMUM_CHARTS and max(first_values) > min(first_values))
            if len(population) >= MINIMUM_CHARTS and (not require_mixed_first_pass or mixed_supported):
                record.update(basis="folder", donorLevels=[level], finalShift=level + .5 - original)
                supported[level] = record
            records[level] = record
        for level, record in records.items():
            if level not in supported:
                lower = [donor for donor in supported if donor < level]
                upper = [donor for donor in supported if donor > level]
                if lower and upper:
                    donors = [max(lower), min(upper)]
                    ratio = (level - donors[0]) / (donors[1] - donors[0])
                    shift = supported[donors[0]]["finalShift"] + ratio * (supported[donors[1]]["finalShift"] - supported[donors[0]]["finalShift"])
                    basis = "interpolated"
                elif supported:
                    donors = [min(supported, key=lambda donor: (abs(level - donor), donor))]
                    shift, basis = supported[donors[0]]["finalShift"], "nearest"
                else:
                    donors, shift, basis = [], 0., "unavailable"
                record.update(basis=basis, donorLevels=donors, finalShift=shift)
            record.update(initialShift=record["finalShift"] / compression_factor(base),
                medianAfter=None if record["medianBefore"] is None else record["medianBefore"] + record["finalShift"],
                donorPopulationChartIds=sorted({cid for donor in record["donorLevels"] for cid in supported[donor]["populationChartIds"]}),
                donorMixedChartIds=sorted({cid for donor in record["donorLevels"] for cid in supported[donor]["mixedFirstPassChartIds"]}))
        result[mode] = {str(level): record for level, record in sorted(records.items())}
    return result


def centering_policy(base: Mapping[str, Any]) -> dict:
    return {"version": 1, "method": CENTERING_METHOD, "baseSchemaVersion": 50,
        "basePayloadSha256": payload_sha256(base), "localOnly": True,
        "source": "official", "minimumLevel": 20,
        "minimumMixedFirstPassCharts": MINIMUM_CHARTS, "minimumFinalCharts": MINIMUM_CHARTS,
        "population": "all-finite-final-sss-excluding-unrated",
        "reference": "current-official-level-plus-0.5", "median": "middle-value-or-mean-of-middle-pair",
        "borrow": "same-source-mode-offset-interpolation-or-nearest", "noDonor": "preserve-point-zero-offset",
        "processing": "frozen-second-pass-preview-then-initial-shift-then-single-compression",
        "compressionFactor": compression_factor(base), "compressionApplications": 1,
        "intervals": "translate-existing-conditional-intervals-fixed-centering-no-new-intervals",
        "pumbility": "mean-centered-sss-and-unchanged-clearing-no-composite-centering",
        "folders": centering_folders(base)}


def shift_scoring(metric: dict, level: int, folder: Mapping[str, Any], frozen_range: Mapping[str, Any] | None, factor: float = .5) -> None:
    before, initial_before = metric["estimatedDifficulty"], metric["initialFit"]["estimatedDifficulty"]
    shift = folder["finalShift"]
    for field in ("estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"):
        original = metric["initialFit"][field]
        adjusted = None if original is None else original + folder["initialShift"]
        metric["initialFit"][field] = adjusted
        metric[field] = compress_difficulty(adjusted, level, factor)
        if field != "estimatedDifficulty":
            metric["initialFit"][field.replace("difficulty", "difficultyDelta", 1)] = None if adjusted is None else adjusted - level - .5
            metric[field.replace("difficulty", "difficultyDelta", 1)] = None if metric[field] is None else metric[field] - level - .5
    metric.update(_derived(metric["estimatedDifficulty"], level))
    metric["initialFit"].update(_derived(metric["initialFit"]["estimatedDifficulty"], level))
    if before is not None and folder["basis"] in ("nearest", "interpolated"):
        if metric["evidenceStatus"] == "Published":
            metric["evidenceStatus"] = "Provisional"
        metric["scoringProvisional"] = True
        metric["scoringProvisionalReasons"] = sorted(set(metric["scoringProvisionalReasons"]) | {"borrowed-folder-centering"})
    rare = metric.get("scoringRefinement", {}).get("rare")
    metric["scoringFolderCentering"] = {"version": 1, **{k: folder[k] for k in (
        "source", "mode", "level", "basis", "donorLevels", "mixedFirstPassCount", "ratedChartCount",
        "medianBefore", "medianAfter", "finalShift", "initialShift")},
        "uncenteredDifficulty": before, "uncenteredInitialDifficulty": initial_before,
        "effectiveRange": None if not frozen_range else {"lower": frozen_range["lower"] + shift, "upper": frozen_range["upper"] + shift},
        "effectiveRareBand": None if not rare else {"lower": rare["upperBandLow"] + shift, "upper": rare["upperBandHigh"] + shift}}


def _apply(base: Mapping[str, Any], policy: Mapping[str, Any]) -> dict:
    result = copy.deepcopy(base)
    result["schemaVersion"] = SCHEMA_VERSION
    result["summary"]["method"]["sssFolderCentering"] = copy.deepcopy(policy)
    for mode, key in MODES:
        for row in result[key]:
            metric = row["scoringVariants"]["sss"]
            row["sssCenteringBaseline"] = {"scoring": copy.deepcopy(metric), "pumbility": copy.deepcopy(row["pumbilityVariants"]["sss"])}
            if row["level"] >= 20:
                frozen = base["summary"]["method"]["sssRefinement"]["ranges"]["official"][mode].get(str(row["level"]))
                shift_scoring(metric, int(row["level"]), policy["folders"][mode][str(row["level"])], frozen, compression_factor(base))
                composite = _composite(row, metric)
                composite["intervalMethod"] = row["pumbilityVariants"]["sss"]["intervalMethod"]
                row["pumbilityVariants"]["sss"] = composite
        rank_variants(result[key])
    return result


def apply_folder_centering(base: Mapping[str, Any]) -> dict:
    if base.get("schemaVersion") != 50:
        raise ValueError("Folder centering requires an uncentered schema-50 baseline.")
    validate_refined_tiers(base)
    return _apply(base, centering_policy(base))


def recover_uncentered(payload: Mapping[str, Any]) -> dict:
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("Folder centering recovery requires schema 51.")
    base = copy.deepcopy(payload)
    base["schemaVersion"] = 50
    base["summary"]["method"].pop("sssFolderCentering", None)
    for _, key in MODES:
        for row in base[key]:
            original = row.pop("sssCenteringBaseline")
            row["scoringVariants"]["sss"] = original["scoring"]
            row["pumbilityVariants"]["sss"] = original["pumbility"]
    return base


def validate_folder_centering(payload: Mapping[str, Any], frozen: Mapping[str, Any] | None = None) -> dict:
    base = recover_uncentered(payload)
    if frozen is not None and base != frozen:
        raise ValueError("Centering recovery differs from its frozen baseline.")
    validate_refined_tiers(base)
    expected_policy = centering_policy(base)
    if payload["summary"]["method"].get("sssFolderCentering") != expected_policy:
        raise ValueError("Folder centering population, donor, offset or policy differs.")
    expected = _apply(base, expected_policy)
    if payload != expected:
        raise ValueError("Centered points, intervals, preserved fields, composites or ranks differ.")
    for mode, key in MODES:
        for level, folder in expected_policy["folders"][mode].items():
            values = [r["scoringVariants"]["sss"]["estimatedDifficulty"] for r in payload[key]
                      if r["level"] == int(level) and r["scoringVariants"]["sss"]["estimatedDifficulty"] is not None]
            if values and folder["basis"] == "folder" and not math.isclose(median(values), int(level) + .5, abs_tol=1e-10):
                raise ValueError("Supported SSS folder median differs from its midpoint.")
    return base
