"""Validation of the approved final ratings, distinct from historical schema IDs."""
from __future__ import annotations

import math
from collections import defaultdict
from statistics import median

GOALS = ("general", "sssplus")
SCHEMA_VERSION = 162


def validate_approved_rating_chain(payload):
    from centered_maximum_pumbility import validate_centered_maximum_pumbility
    from final_folder_centering import validate_final_folder_centering
    from scoped_tier_width import validate_scoped_tier_width
    from observed_capped_clearing import validate_observed_capped_clearing
    from extended_official_clearing import validate_extended_official_clearing
    from level20_scoring_width import validate_level20_scoring_width
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("Approved ratings require schema 162.")
    base = payload
    for validate in (validate_centered_maximum_pumbility, validate_final_folder_centering,
                     validate_scoped_tier_width, validate_observed_capped_clearing,
                     validate_extended_official_clearing):
        base = validate(base)
    validate_level20_scoring_width(base)
    validate_public_approved_ratings(payload)
    return base


def validate_public_approved_ratings(payload):
    method = payload["summary"]["method"]
    expected = {"extendedOfficialClearing": (61, "official-clearer-ability-combined-v3"),
        "observedCappedClearing": (158, "official-clearer-ability-observed-capped-v1"),
        "scopedTierWidth": (159, "scoped-final-component-width-v1"),
        "finalFolderCentering": (160, "final-all-rated-folder-median-centering-v1"),
        "centeredMaximumPumbility": (161, "folder-centered-maximum-pumbility-v1")}
    for name, (schema, identity) in expected.items():
        marker = method.get(name, {})
        if (marker.get("version") != 1 or marker.get("method") != identity
                or marker.get("localOnly") is not False or marker.get("baseSchemaVersion") != schema):
            raise ValueError("Approved production rating policy differs: " + name)
    if (method["scopedTierWidth"].get("factor") != 1.
            or method["centeredMaximumPumbility"].get("aggregation") != "centered-maximum"
            or method["centeredMaximumPumbility"].get("goals") != list(GOALS)):
        raise ValueError("Approved width or maximum policy differs.")
    groups = defaultdict(list)
    for key in ("singles", "doubles"):
        for row in payload[key]:
            groups[(row["type"], row["level"])].append(row)
            if set(row["scoringVariants"]) != set(GOALS) or set(row["pumbilityVariants"]) != set(GOALS):
                raise ValueError("Approved ratings must retain only current scoring goals.")
    for (mode, level), rows in groups.items():
        midpoint = level+.5
        def centered(values):
            values = [v for v in values if v is not None]
            if values and (not all(math.isfinite(v) for v in values)
                           or not math.isclose(median(values), midpoint, rel_tol=0, abs_tol=1e-8)):
                raise ValueError("An approved final folder median differs from its midpoint.")
        centered([r["tierMetrics"]["clearing"]["estimatedDifficulty"] for r in rows])
        for goal in GOALS:
            centered([r["scoringVariants"][goal]["estimatedDifficulty"] for r in rows])
            centered([r["pumbilityVariants"][goal]["estimatedDifficulty"] for r in rows])
            raw = [max(r["scoringVariants"][goal]["estimatedDifficulty"],r["tierMetrics"]["clearing"]["estimatedDifficulty"])
                for r in rows if r["scoringVariants"][goal]["estimatedDifficulty"] is not None
                and r["tierMetrics"]["clearing"]["estimatedDifficulty"] is not None]
            offset = midpoint-median(raw) if raw else 0.
            marker = method["centeredMaximumPumbility"]["folders"][mode][str(level)][goal]
            if marker["ratedCharts"] != len(raw) or not math.isclose(marker["offset"],offset,rel_tol=0,abs_tol=1e-8):
                raise ValueError("The maximum centering population differs.")
            for row in rows:
                scoring, clearing = row["scoringVariants"][goal]["estimatedDifficulty"],row["tierMetrics"]["clearing"]["estimatedDifficulty"]
                expected = None if scoring is None or clearing is None else max(scoring,clearing)+offset
                actual = row["pumbilityVariants"][goal]["estimatedDifficulty"]
                if expected is None and actual is not None or expected is not None and (actual is None or abs(actual-expected)>1e-8):
                    raise ValueError("Approved Pumbility differs from its centered maximum.")
