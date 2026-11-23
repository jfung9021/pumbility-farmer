"""Independent Phoenix2 mode targets with reconstructed mode and overall gains."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

from phoenix2_pumbility import grade_for_score, phoenix2_pumbility
from pro_pumbility import ProPumbilityError, _cohort, _decimal, _digest, _instant, _official_id, _prepare, projected_target

SCHEMA_VERSION = 2
METHOD = "pro-pumbility-p50-modes-overall-top50-phoenix2-v6"
SCOPE = "mode-consolidated"
MODES = {"singles": "Single", "doubles": "Double"}


def percentile50(scores: Sequence[int]) -> Decimal:
    if not scores or any(type(score) is not int or not 0 <= score <= 1_000_000 for score in scores):
        raise ProPumbilityError("A q50 target requires valid official scores.")
    ordered = sorted(scores)
    position = Decimal(len(ordered) - 1) * Decimal("0.5")
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return Decimal(ordered[lower]) + (ordered[upper] - ordered[lower]) * (position - lower)


def _sort_key(row: Mapping[str, Any]) -> tuple:
    return (-row["projectedGain"], -row["expectedPumbility"], row["songName"].casefold(), row["chartId"])


def consolidate_pro_modes(modes: Mapping[str, Mapping[str, Any]], generation: str) -> dict[str, Any]:
    """Keep mode targets, but measure Overall gains against the combined Top 50."""
    rows, available, unavailable, seen = [], [], [], set()
    for mode in MODES:
        result = modes[mode]
        if result.get("generation") != generation or result.get("mode") != mode:
            raise ProPumbilityError("Pro mode generations do not match.")
        if result["status"] != "ready":
            unavailable.append(mode)
            continue
        available.append(mode)
        for row in result["recommendations"]:
            if row["chartId"] in seen or row.get("sourceMode") != mode:
                raise ProPumbilityError("Pro mode rows have duplicate or mismatched chart identities.")
            seen.add(row["chartId"])
            rows.append(row)
    baselines = [result["baseline"] for result in modes.values() if result.get("baseline") is not None]
    baseline = None
    if baselines:
        # A chart outside either mode's Top 50 cannot enter the combined Top 50.
        top = sorted([row for source in baselines for row in source["top50"]],
                     key=lambda row: (-round(row["pumbility"] * 100), row["chartId"]))[:50]
        guessed = sum(row["plateEstimated"] for row in top)
        baseline = {"mode": "overall", "source": "official-leaderboard" if guessed == len(top) else "piuscores" if guessed == 0 else "mixed",
                    "estimated": guessed > 0, "scoreCount": sum(source["scoreCount"] for source in baselines),
                    "top50Pumbility": sum(round(row["pumbility"] * 100) for row in top) / 100,
                    "cutoffPumbility": top[-1]["pumbility"] if len(top) == 50 else None,
                    "snapshotAt": baselines[0]["snapshotAt"], "syncedAtUtc": max(source["syncedAtUtc"] for source in baselines),
                    "fallbackReason": "Missing personal results were filled from official leaderboard scores with assumed plates." if guessed else None,
                    "top50": top}
    overall_rows = []
    if baseline and baseline["cutoffPumbility"] is not None:
        current = {row["chartId"]: row["pumbility"] for row in baseline["top50"]}
        for row in rows:
            replacement = current.get(row["chartId"], baseline["cutoffPumbility"])
            gain = max(0, round(row["expectedPumbility"] * 100) - round(replacement * 100)) / 100
            if gain > 0:
                overall_rows.append({**row, "projectedGain": gain, "modeProjectedGain": row["projectedGain"],
                                     "replacedPumbility": replacement})
    return {"kind": "overall-top50", "generation": generation, "baseline": baseline,
            "sourceGenerations": {mode: modes[mode]["generation"] for mode in MODES},
            "availableModes": available, "unavailableModes": unavailable,
            "recommendations": sorted(overall_rows, key=_sort_key)}


def _fact(chart: Mapping[str, Any], score: int | float | None, value: float, plate: Any, estimated: bool) -> dict:
    return {"chartId": chart["chartId"], "songName": str(chart.get("songName") or "Untitled chart"),
            "chartLabel": str(chart.get("difficulty") or ("S" if chart["type"] == "Single" else "D") + str(chart["level"])),
            "type": chart["type"], "level": chart["level"], "score": score,
            "grade": grade_for_score(score), "plate": plate, "plateEstimated": estimated,
            "pumbility": value, "cents": int(round(value * 100))}


def _mode_indexes(context: Any, profiles: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict]]:
    ranked_ids = set(context.by_player)
    if set(context.official_profiles) != ranked_ids:
        raise ProPumbilityError("Capture the complete ranked player population before building mode cohorts.")
    personal: dict[str, dict[str, dict]] = {}
    sync_dates: dict[str, list[str]] = {}
    for profile in profiles:
        if profile.get("mix") != "Phoenix2":
            raise ProPumbilityError("Mode inputs must contain only Phoenix2 profiles.")
        if profile.get("officialPlayerId") is None or profile.get("scoresComplete") is not True:
            continue
        pid = _official_id(profile["officialPlayerId"])
        if pid not in ranked_ids:
            continue
        best = personal.setdefault(pid, {})
        if profile.get("scoresSyncedAtUtc"):
            sync_dates.setdefault(pid, []).append(profile["scoresSyncedAtUtc"])
        for row in profile.get("scores", []):
            if not isinstance(row, Mapping) or row.get("mix", "Phoenix2") != "Phoenix2" or row.get("isBroken"):
                continue
            chart = context.charts.get(str(row.get("chartId") or ""))
            value, raw = _decimal(row.get("pumbility")), _decimal(row.get("score"))
            if chart is None or value is None or value < 0 or (raw is not None and not 0 <= raw <= 1_000_000):
                continue
            cents = int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))
            item = _fact(chart, float(raw) if raw is not None else None, cents / 100, row.get("plate"), False)
            old = best.get(chart["chartId"])
            if old is None or (item["cents"], raw or Decimal(0)) > (old["cents"], _decimal(old["score"]) or Decimal(0)):
                best[chart["chartId"]] = item

    indexes: dict[str, dict[str, dict]] = {mode: {} for mode in MODES}
    for pid, subject in context.by_player.items():
        official = context.official_profiles[pid]
        facts: dict[str, dict] = {}
        for placement in official["placements"]:
            chart = context.charts.get(placement["chartId"])
            if chart is None:
                continue
            target = projected_target(placement["score"])
            value = phoenix2_pumbility(chart["type"], chart["level"], target["targetGrade"], target["assumedPlate"])
            item = _fact(chart, placement["score"], value, target["assumedPlate"], True)
            old = facts.get(chart["chartId"])
            if old is None or (item["cents"], item["score"]) > (old["cents"], old["score"]):
                facts[chart["chartId"]] = item
        for cid, actual in personal.get(pid, {}).items():
            old = facts.get(cid)
            if old is None or (actual["score"] is not None and actual["score"] >= old["score"]) or actual["cents"] >= old["cents"]:
                facts[cid] = actual
        for mode, kind in MODES.items():
            mode_facts = {cid: row for cid, row in facts.items() if row["type"] == kind}
            top = sorted(mode_facts.values(), key=lambda row: (-row["cents"], row["chartId"]))[:50]
            guessed = sum(row["plateEstimated"] for row in top)
            source = "official-leaderboard" if guessed == len(top) else "piuscores" if guessed == 0 else "mixed"
            total = sum(row["cents"] for row in top) / 100
            complete = len(top) == 50
            baseline = {"mode": mode, "source": source, "estimated": guessed > 0, "scoreCount": len(mode_facts),
                        "top50Pumbility": total, "cutoffPumbility": top[-1]["pumbility"] if complete else None,
                        "snapshotAt": context.snapshot["snapshotAt"],
                        "syncedAtUtc": max([official["fetchedAtUtc"], *sync_dates.get(pid, [])]),
                        "fallbackReason": "Missing personal results were filled from official leaderboard scores with assumed plates." if guessed else None,
                        "top50": [{key: value for key, value in row.items() if key != "cents"} for row in top]}
            indexes[mode][pid] = {"baseline": baseline, "facts": mode_facts,
                                   "rating": Decimal(str(total)) if complete else None,
                                   "rank": subject["rank"], "playerId": pid}
    return indexes


def _build_mode(context: Any, indexes: Mapping[str, Any], subject: Mapping[str, Any] | None,
                mode: str, generation: str, status: str) -> dict:
    result = {"mode": mode, "generation": generation, "status": status, "message": None,
              "modePumbility": None, "baseline": None, "cohort": None, "recommendations": [],
              "candidateCount": sum(chart["type"] == MODES[mode] for chart in context.candidates),
              "unobservedChartCount": 0}
    if status != "ready" or subject is None:
        return result
    state = indexes[mode][subject["playerId"]]
    baseline = state["baseline"]
    result["baseline"] = baseline
    if state["rating"] is None:
        result.update(status="insufficient-data", message=f"At least 50 usable {mode} chart results are needed. Unknown results are not empty slots.")
        return result
    result["modePumbility"] = float(state["rating"])
    population = [row for row in indexes[mode].values() if row["rating"] is not None]
    peers, cohort = _cohort(population, state)
    result["cohort"] = cohort
    peer_ids = {row["playerId"] for row in peers}
    existing = state["facts"]
    top_ids = {row["chartId"] for row in baseline["top50"]}
    for chart in context.candidates:
        if chart["type"] != MODES[mode]:
            continue
        board = context.boards[chart["chartId"]]
        scores = [score for pid, score in board["scores"].items() if pid in peer_ids]
        if not scores:
            result["unobservedChartCount"] += 1
            continue
        target = projected_target(percentile50(scores))
        value = phoenix2_pumbility(chart["type"], chart["level"], target["targetGrade"], target["assumedPlate"])
        old = existing.get(chart["chartId"])
        replacement = old["pumbility"] if chart["chartId"] in top_ids else baseline["cutoffPumbility"]
        gain = round(max(0, value - replacement), 2)
        if gain <= 0:
            continue
        result["recommendations"].append({"chartId": chart["chartId"], "songName": str(chart.get("songName") or "Untitled chart"),
            "imageUrl": chart.get("imageUrl"), "stepArtist": chart.get("stepArtist"),
            "bpmMin": chart.get("bpmMin"), "bpmMax": chart.get("bpmMax"),
            "chartLabel": str(chart.get("difficulty") or ("S" if mode == "singles" else "D") + str(chart["level"])),
            "type": chart["type"], "level": chart["level"], "sourceMode": mode, **target,
            "expectedPumbility": value, "projectedGain": gain,
            "currentScore": old["score"] if old else None, "currentPumbility": old["pumbility"] if old else None,
            "currentPlate": old["plate"] if old else None, "currentPlateEstimated": bool(old and old["plateEstimated"]),
            "peerScoreCount": len(scores), "cohortPlayerCount": len(peers),
            "boardPossiblyTruncated": bool(board.get("possiblyTruncated")),
            "limitedData": len(scores) < 10 or bool(board.get("possiblyTruncated"))})
    result["recommendations"].sort(key=_sort_key)
    return result


def _build(context: Any, indexes: Mapping[str, Any], profile: Mapping[str, Any], generation: str, at: str) -> dict:
    subject = context.by_player.get(_official_id(profile["officialPlayerId"])) if profile.get("officialPlayerId") is not None else None
    status = "identity-unresolved" if profile.get("officialPlayerId") is None else "ineligible" if subject is None or subject["rank"] > 300 else "ready"
    player = {"playerKey": str(profile["playerKey"]), "displayName": str(profile.get("displayName") or profile.get("username") or "Player"),
              "officialDisplayName": subject["gameTag"] if subject else None,
              "overallRank": subject["rank"] if subject else None, "overallPumbility": float(subject["rating"]) if subject else None}
    modes = {mode: _build_mode(context, indexes, subject, mode, generation, status) for mode in MODES}
    overall = consolidate_pro_modes(modes, generation)
    if status == "ready" and not overall["availableModes"]:
        status = "insufficient-data"
    message = ("Link your official Phoenix 2 game tag by importing scores on PIUScores." if status == "identity-unresolved"
               else "Pro Farming requires official overall rank 1-300." if status == "ineligible" else None)
    return {"schemaVersion": SCHEMA_VERSION, "method": METHOD, "scope": SCOPE, "mix": "Phoenix2",
            "generation": generation, "snapshotAt": context.snapshot["snapshotAt"], "fetchedAtUtc": context.snapshot["fetchedAtUtc"],
            "generatedAtUtc": at, "player": player, "eligible": bool(subject and subject["rank"] <= 300),
            "status": status, "message": message, "modes": modes, "overall": overall}


def build_pro_artifacts(snapshot: Mapping[str, Any], profiles: Sequence[Mapping[str, Any]], *, generated_at_utc: str | None = None) -> tuple[dict, dict]:
    context = _prepare(snapshot)
    indexes = _mode_indexes(context, profiles)
    inputs = list(profiles)
    represented = {_official_id(profile["officialPlayerId"]) for profile in inputs if profile.get("officialPlayerId") is not None}
    for subject in context.rankings:
        if subject["rank"] <= 300 and subject["playerId"] not in represented:
            inputs.append({"mix": "Phoenix2", "playerKey": "op_" + hashlib.sha256(subject["playerId"].encode()).hexdigest(),
                           "displayName": subject["gameTag"] or "Official player", "officialPlayerId": subject["playerId"]})
    keys = [str(profile.get("playerKey") or "") for profile in inputs]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ProPumbilityError("Pro profiles have duplicate or missing player keys.")
    at = generated_at_utc or datetime.now(timezone.utc).isoformat()
    _instant(at)
    generation = _digest({"method": METHOD, "snapshot": snapshot, "profiles": profiles})
    payloads = {profile["playerKey"]: _build(context, indexes, profile, generation, at) for profile in inputs}
    summaries = [{**payload["player"], "status": payload["status"], "eligible": payload["eligible"],
                  "modes": {mode: {"status": result["status"], "modePumbility": result["modePumbility"]} for mode, result in payload["modes"].items()}}
                 for payload in payloads.values()]
    summaries.sort(key=lambda row: (row["overallRank"] or 1_000_000, row["displayName"].casefold(), row["playerKey"]))
    index = {key: value for key, value in next(iter(payloads.values())).items() if key in
             ("schemaVersion", "method", "scope", "mix", "generation", "snapshotAt", "fetchedAtUtc", "generatedAtUtc")}
    index["players"] = summaries
    return index, payloads
