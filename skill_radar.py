"""Pure Phoenix 2 BPM progression and SSS+ practice ladders.

Pricing, unique-chart selection and PG inversion follow PIU Training Tracker
237e9f00b728c27150cb927dba8b8f79195526db/src/domain/bpm-progression.ts.
Internal player identifiers and play histories stay outside the public DTO.
Only the selected five chart contributions are exposed for the rating dialog.
"""
from __future__ import annotations

from collections import defaultdict
from functools import cmp_to_key
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from phoenix2_pumbility import (GRADE_BANDS, GRADE_PENALTY_UNITS_BY_TYPE,
    PLATE_BONUS_UNITS_BY_TYPE, PLATE_CODES, normalize_plate)

METHOD_VERSION = 1
PROFILE_VERSION = 4
MODES = (("singles", "Single"), ("doubles", "Double"))
BPM_BUCKETS = tuple({"id": str(index), "label": label, "min": low, "max": high}
    for index, (label, low, high) in enumerate((
        ("<150", 0, 150), ("150–159", 150, 160), ("160–169", 160, 170),
        ("170–179", 170, 180), ("180–189", 180, 190), ("190–199", 190, 200),
        ("200–209", 200, 210), ("210–219", 210, 220), ("220+", 220, None))))


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def content_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def bpm_bucket(chart):
    low, high = chart.get("bpmMin"), chart.get("bpmMax")
    if not finite(low) or not finite(high) or low <= 0 or high < low:
        return "unknownBpm"
    return next((index for index, bucket in enumerate(BPM_BUCKETS)
        if low >= bucket["min"] and (bucket["max"] is None or high < bucket["max"])), "spanningBpm")


def calculate_phoenix2_pumbility(mode, level, score, plate):
    if (mode not in ("Single", "Double") or not finite(level) or int(level) != level
            or not 1 <= level <= 30 or not finite(score) or score < 0):
        return None
    plate = normalize_plate(plate)
    if plate is None:
        return None
    grade = next(grade for threshold, grade, _ in GRADE_BANDS
        if min(1_000_000, math.floor(score)) >= threshold)
    penalty = GRADE_PENALTY_UNITS_BY_TYPE[mode][grade]
    bonus = PLATE_BONUS_UNITS_BY_TYPE[mode][plate]
    effective = level-(1 if mode == "Double" else 0)
    base = 7.5*(effective+27) if effective <= 23 else 375+15*(effective-23)
    raw = max(0, base*(750-5*penalty+bonus)/750)
    return math.floor((raw+1e-9)*100)/100


def pg_equivalent(mode, total_pumbility):
    if not finite(total_pumbility) or total_pumbility <= 0:
        return 0.0
    base = total_pumbility/5/(760/750)
    effective = base/7.5-27 if base <= 375 else 23+(base-375)/15
    return max(0.0, effective+(1 if mode == "Double" else 0))


def _compare_priced(a, b):
    for key in ("pumbility", "score"):
        left = a.get(key) if finite(a.get(key)) else -math.inf
        right = b.get(key) if finite(b.get(key)) else -math.inf
        if left != right:
            return -1 if left > right else 1
    left, right = str(a.get("recordedAt") or ""), str(b.get("recordedAt") or "")
    if left != right:
        return -1 if left > right else 1
    left, right = a["chartId"], b["chartId"]
    return (left > right)-(left < right)


def aggregate_bpm_progression(charts: Sequence[Mapping], scores: Sequence[Mapping]):
    """Tracker-equivalent aggregates; diagnostics/contributors are private only."""
    catalog = {str(row.get("chartId", row.get("id"))): row for row in charts
        if row.get("type", row.get("chartType")) in ("Single", "Double")
        and row.get("mix", "Phoenix2") == "Phoenix2"}
    by_chart = defaultdict(list)
    for row in scores:
        if isinstance(row, Mapping) and row.get("chartId") is not None:
            by_chart[str(row["chartId"])].append(row)
    partitions = [{"singles": [], "doubles": []} for _ in BPM_BUCKETS]
    excluded = dict.fromkeys(("unknownBpm", "spanningBpm", "unpriced", "broken", "unknownChart"), 0)
    for chart_id, rows in by_chart.items():
        nonbroken = [row for row in rows if not row.get("isBroken")]
        if not nonbroken:
            excluded["broken"] += 1
            continue
        chart = catalog.get(chart_id)
        if chart is None:
            excluded["unknownChart"] += 1
            continue
        index = bpm_bucket(chart)
        if not isinstance(index, int):
            excluded[index] += 1
            continue
        mode = chart.get("type", chart.get("chartType"))
        priced = []
        for row in nonbroken:
            supplied = row.get("pumbility")
            earned = supplied if finite(supplied) and supplied >= 0 else calculate_phoenix2_pumbility(
                mode, chart.get("level"), row.get("score"), row.get("plate"))
            if earned is not None:
                priced.append({"chartId": chart_id, "pumbility": earned, "score": row.get("score"), "plate": row.get("plate"),
                               "recordedAt": row.get("recordedAt") or ""})
        if not priced:
            excluded["unpriced"] += 1
            continue
        partitions[index]["singles" if mode == "Single" else "doubles"].append(
            sorted(priced, key=cmp_to_key(_compare_priced))[0])
    result = []
    for index, bucket in enumerate(BPM_BUCKETS):
        value = {"label": bucket["label"]}
        for key, mode in MODES:
            ordered = sorted(partitions[index][key], key=cmp_to_key(_compare_priced))
            selected = ordered[:5]
            # JS Math.round, not Python's round-to-even; supplied values retain
            # their raw ordering before summing integer cents.
            total = sum(math.floor(row["pumbility"]*100+.5) for row in selected)/100
            value[key] = {"mode": mode, "scoreCount": len(selected), "usableCharts": len(ordered),
                          "totalPumbility": total, "equivalentLevel": pg_equivalent(mode, total),
                          "contributors": selected}
        result.append(value)
    return {"buckets": result, "excluded": excluded}


def build_suggestion_catalog(tiers: Mapping, chart_metadata: Sequence[Mapping]):
    metadata = {str(c.get("chartId", c.get("id"))): c for c in chart_metadata}
    charts, seen = [], set()
    for mode, chart_type in MODES:
        for row in tiers.get(mode, []):
            rating = row.get("pumbilityVariants", {}).get("sssplus", {}).get("estimatedDifficulty")
            if not finite(rating):
                continue
            cid = str(row.get("chartId") or "")
            if cid in seen or not finite(row.get("level")) or not 16 <= row["level"] <= 30 or int(row["level"]) != row["level"]:
                continue
            current = metadata.get(cid)
            if not current or current.get("type", current.get("chartType")) != chart_type:
                continue
            chart = {**row, "bpmMin": current.get("bpmMin"), "bpmMax": current.get("bpmMax")}
            index = bpm_bucket(chart)
            if not isinstance(index, int):
                continue
            seen.add(cid)
            charts.append({"chartId": cid, "songName": str(row.get("songName") or ""),
                "difficulty": str(row.get("difficulty") or f"{'S' if mode == 'singles' else 'D'}{row['level']}"),
                "type": chart_type, "level": int(row["level"]), "imageUrl": row.get("imageUrl") or None,
                "bpmMin": chart["bpmMin"], "bpmMax": chart["bpmMax"],
                "estimatedDifficulty": rating, "bpmBucketId": str(index)})
    charts.sort(key=lambda c: (c["estimatedDifficulty"], c["songName"], c["chartId"]))
    return {"schemaVersion": 1, "methodVersion": METHOD_VERSION,
        "tierGeneratedAtUtc": tiers.get("generatedAtUtc"),
        "tierIdentity": tiers.get("summary", {}).get("method", {}).get("productionTierPublication", {}).get("publicPayloadSha256")
            or content_digest(charts),
        "metadataRevision": content_digest(list(chart_metadata)), "charts": charts}


def build_skill_radar(charts, scores, suggestion_catalog, *, player_key, display_name,
                      generated_at_utc, player_synced_at_utc=None, personal_scores=None):
    aggregate = aggregate_bpm_progression(charts, scores)
    metadata = {str(row.get("chartId", row.get("id"))): row for row in charts}
    scores_by_chart = {row["chartId"]: row for row in personal_scores or []}
    buckets = []
    for index, bucket in enumerate(BPM_BUCKETS):
        for key, mode in MODES:
            bar = aggregate["buckets"][index][key]
            raw = bar["equivalentLevel"]
            display = max(0.0, raw-(1 if mode == "Double" else 0))
            target_level = max(16, math.floor(raw+1.5+1e-9))
            contributing_charts = []
            for selected in bar["contributors"]:
                chart = metadata[selected["chartId"]]
                selected_score = selected.get("score")
                valid_score = (finite(selected_score) and float(selected_score).is_integer()
                               and 0 <= selected_score <= 1_000_000)
                contributing_charts.append({"chartId": selected["chartId"],
                    "songName": str(chart.get("songName") or ""), "type": mode,
                    "level": int(chart["level"]),
                    "difficulty": ("S" if mode == "Single" else "D")+str(int(chart["level"])),
                    "imageUrl": chart.get("imageUrl") or None,
                    "pumbility": math.floor(selected["pumbility"]*100+.5)/100,
                    "score": int(selected_score) if valid_score else None,
                    "plate": PLATE_CODES.get(normalize_plate(selected.get("plate")))})
            suggestions = []
            for chart in suggestion_catalog["charts"]:
                if (chart["type"] != mode or chart["bpmBucketId"] != bucket["id"]
                        or chart["level"] != target_level):
                    continue
                personal = scores_by_chart.get(chart["chartId"], {})
                suggestions.append({**{k: v for k, v in chart.items() if k != "bpmBucketId"},
                    "personalScore": personal.get("score"), "personalPlate": personal.get("plateCode")})
                if len(suggestions) == 10:
                    break
            buckets.append({"key": key+":"+bucket["id"], "mode": key, "bpmBucketId": bucket["id"],
                "label": bucket["label"], "rawPg": raw, "displayPg": display,
                "radarScore": max(0.0, min(100.0, 100*(display-10)/15)),
                "usableCharts": bar["usableCharts"], "selectedCharts": bar["scoreCount"],
                "needsPlays": bar["scoreCount"] < 5, "suggestedLevel": target_level,
                "group": "everythingElse", "suggestions": suggestions,
                "contributingCharts": contributing_charts})
    order = lambda row: (int(row["bpmBucketId"]), 0 if row["mode"] == "singles" else 1)
    weaknesses = sorted(buckets, key=lambda row: (row["displayPg"], *order(row)))[:3]
    weak_keys = {row["key"] for row in weaknesses}
    strengths = sorted((row for row in buckets if row["key"] not in weak_keys
        and row["selectedCharts"] > 0 and row["displayPg"] > 0),
        key=lambda row: (-row["displayPg"], *order(row)))[:3]
    strong_keys = {row["key"] for row in strengths}
    remaining = [row for row in buckets if row["key"] not in weak_keys | strong_keys]
    groups = {"weaknesses": weaknesses, "strengths": strengths, "everythingElse": remaining}
    for group, members in groups.items():
        for row in members:
            row["group"] = group
    return {"methodVersion": METHOD_VERSION, "player": {"playerKey": player_key, "displayName": display_name},
        "generatedAtUtc": generated_at_utc, "tierGeneratedAtUtc": suggestion_catalog["tierGeneratedAtUtc"],
        "playerSyncedAtUtc": player_synced_at_utc, "bpmBuckets": [dict(row) for row in BPM_BUCKETS],
        "buckets": buckets, "groups": {key: [row["key"] for row in rows] for key, rows in groups.items()}}
