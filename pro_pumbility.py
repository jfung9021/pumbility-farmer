"""Phoenix 2 official-peer farming targets and personal overall Top 50 gains.

Raw capture and account links remain private. The returned artifacts contain
only player-facing identities, aggregate peer coverage, and chart suggestions.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

from phoenix2_pumbility import GRADE_BANDS, grade_for_score, phoenix2_pumbility
from piu_recommendations import _top50_marginal_gain


SCHEMA_VERSION = 1
METHOD = "pro-pumbility-p50-adaptive-overall-phoenix2-v2"
MIX = "Phoenix2"
MINIMUM_PEERS = 10
UPWARD_WINDOWS = (100, 150, 200)
MAXIMUM_RANK = 300


class ProPumbilityError(ValueError):
    """The input cannot establish a matching Phoenix 2 recommendation state."""


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _instant(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ProPumbilityError("The official snapshot timestamp is missing.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except ValueError:
        raise ProPumbilityError("The official snapshot timestamp is invalid.") from None


def _official_id(value: Any) -> str:
    text = str(value).removeprefix("official:")
    if isinstance(value, bool) or not text.isdecimal() or int(text) <= 0:
        raise ProPumbilityError("An official player ID is invalid.")
    return f"official:{int(text)}"


def _rankings(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        player = row.get("player")
        if not isinstance(player, Mapping) or player.get("isSupplemented") is not False:
            raise ProPumbilityError("Pro cohorts require official, unsupplemented players.")
        player_id = _official_id(player.get("playerId"))
        rating = _decimal(row.get("rating"))
        rank = row.get("rank")
        if rating is None or rating < 0 or type(rank) is not int or rank < 1:
            raise ProPumbilityError("An official ranking row has an invalid rating or rank.")
        candidate = {"playerId": player_id, "rating": rating, "rank": rank,
                     "gameTag": str(player.get("gameTag") or "")}
        previous = best.get(player_id)
        if previous is None or (rating, -rank) > (previous["rating"], -previous["rank"]):
            best[player_id] = candidate
    return sorted(best.values(), key=lambda row: (row["rank"], row["playerId"]))


def _cohort(rows: Sequence[Mapping[str, Any]], subject: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    rating, player_id = subject["rating"], subject["playerId"]
    peers: list[Mapping[str, Any]] = []
    window = UPWARD_WINDOWS[-1]
    for window in UPWARD_WINDOWS:
        peers = [row for row in rows if row["playerId"] != player_id
                 and rating <= row["rating"] <= rating + window]
        if len(peers) >= MINIMUM_PEERS:
            break
    backfill_count = 0
    if len(peers) < MINIMUM_PEERS:
        lower = sorted((row for row in rows if row["playerId"] != player_id and row["rating"] < rating),
                       key=lambda row: (-row["rating"], row["rank"], row["playerId"]))
        addition = lower[:MINIMUM_PEERS - len(peers)]
        peers.extend(addition)
        backfill_count = len(addition)
    values = [row["rating"] for row in peers]
    return peers, {"minimumPlayers": MINIMUM_PEERS, "upwardWindow": window,
                   "backfillCount": backfill_count, "playerCount": len(peers),
                   "minimumPumbility": float(min(values)) if values else None,
                   "maximumPumbility": float(max(values)) if values else None,
                   "limitedData": len(peers) < MINIMUM_PEERS}


def select_peer_cohort(rankings: Sequence[Mapping[str, Any]], official_player_id: str | int) -> tuple[list[str], dict[str, Any]]:
    """Select one adaptive overall cohort; the selected player never contributes."""
    rows = _rankings(rankings)
    player_id = _official_id(official_player_id)
    subject = next((row for row in rows if row["playerId"] == player_id), None)
    if subject is None:
        raise ProPumbilityError("The selected player is absent from the overall ranking.")
    peers, metadata = _cohort(rows, subject)
    return [row["playerId"] for row in peers], metadata


def _percentile(scores: Sequence[int]) -> Decimal:
    if not scores:
        raise ProPumbilityError("No official peer scores are available for this chart.")
    ordered = sorted(scores)
    if any(type(score) is not int or not 0 <= score <= 1_000_000 for score in ordered):
        raise ProPumbilityError("An official peer score is outside the expected range.")
    position = Decimal(len(ordered) - 1) * Decimal("0.5")
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return Decimal(ordered[lower]) + (Decimal(ordered[upper]) - Decimal(ordered[lower])) * (position - lower)


def percentile50(scores: Sequence[int]) -> float:
    """Unweighted median with linear interpolation and exact decimal boundaries."""
    return float(_percentile(scores))


def projected_target(raw_score: Any) -> dict[str, Any]:
    """Choose plate on the raw median, then truncate its grade without losing PG."""
    value = _decimal(raw_score)
    if value is None or not 0 <= value <= 1_000_000:
        raise ProPumbilityError("A projected score is outside the expected range.")
    plate = "PG" if value == 1_000_000 else "SG" if value > 995_000 else "MG"
    grade = grade_for_score(value)
    boundary = next(threshold for threshold, label, _ in GRADE_BANDS if label == grade)
    return {"rawPercentileScore": float(value), "gradeBoundaryScore": boundary,
            "targetScore": 1_000_000 if plate == "PG" else boundary,
            "targetGrade": grade, "assumedPlate": plate}


@dataclass(frozen=True)
class _Context:
    snapshot: Mapping[str, Any]
    rankings: Sequence[Mapping[str, Any]]
    by_player: Mapping[str, Mapping[str, Any]]
    charts: Mapping[str, Mapping[str, Any]]
    candidates: Sequence[Mapping[str, Any]]
    boards: Mapping[str, Mapping[str, Any]]
    official_profiles: Mapping[str, Mapping[str, Any]]


def _prepare(snapshot: Mapping[str, Any]) -> _Context:
    if snapshot.get("schemaVersion") != SCHEMA_VERSION or snapshot.get("mix") != MIX:
        raise ProPumbilityError("Pro recommendations require a Phoenix2-only snapshot.")
    rankings = snapshot.get("rankings")
    if not isinstance(rankings, Mapping) or rankings.get("ratingIsOfficial") is not True:
        raise ProPumbilityError("The official overall Pumbility ranking is unavailable.")
    snapshot_at = _instant(snapshot.get("snapshotAt"))
    _instant(snapshot.get("fetchedAtUtc"))
    if _instant(rankings.get("snapshotAt")) != snapshot_at:
        raise ProPumbilityError("Official rankings and chart scores use different snapshots.")
    ranking_rows = _rankings(rankings.get("data") or [])
    if not ranking_rows:
        raise ProPumbilityError("The official overall Pumbility ranking is empty.")
    charts: dict[str, Mapping[str, Any]] = {}
    candidates: list[Mapping[str, Any]] = []
    for raw in snapshot.get("charts", []):
        if not isinstance(raw, Mapping) or raw.get("mix", MIX) != MIX:
            raise ProPumbilityError("Pro chart metadata must contain only Phoenix2 charts.")
        cid = str(raw.get("id", raw.get("chartId", "")))
        level = _decimal(raw.get("level"))
        kind = raw.get("type")
        if not cid or kind not in ("Single", "Double") or level is None or level != int(level):
            continue
        chart = {**raw, "chartId": cid, "level": int(level)}
        if cid in charts:
            raise ProPumbilityError("The Phoenix2 chart catalog repeats a chart ID.")
        charts[cid] = chart
        if (kind == "Single" and level >= 24) or (kind == "Double" and level >= 25):
            candidates.append(chart)
    boards: dict[str, Mapping[str, Any]] = {}
    for board in snapshot.get("boards", []):
        if not isinstance(board, Mapping) or board.get("mix", MIX) != MIX:
            raise ProPumbilityError("Pro chart boards must contain only Phoenix2 evidence.")
        if _instant(board.get("asOf")) != snapshot_at:
            raise ProPumbilityError("Official rankings and chart scores use different snapshots.")
        cid = str(board.get("chartId") or "")
        if cid in boards:
            raise ProPumbilityError("The official capture repeats a chart board.")
        if not isinstance(board.get("entries"), list):
            raise ProPumbilityError("An official chart board is invalid.")
        entries: dict[str, int] = {}
        for row in board["entries"]:
            if not isinstance(row, Mapping) or row.get("isSupplemented", False) is not False:
                raise ProPumbilityError("Pro chart scores must be unsupplemented official evidence.")
            peer_id = _official_id(row.get("playerId"))
            score = row.get("score")
            if type(score) is not int or not 0 <= score <= 1_000_000:
                raise ProPumbilityError("An official chart score is outside the expected range.")
            entries[peer_id] = max(score, entries.get(peer_id, -1))
        boards[cid] = {**board, "scores": entries}
    if any(chart["chartId"] not in boards for chart in candidates):
        raise ProPumbilityError("The official capture is incomplete for S24+ and D25+ charts.")
    by_player = {row["playerId"]: row for row in ranking_rows}
    official_profiles: dict[str, Mapping[str, Any]] = {}
    for profile in snapshot.get("officialProfiles", []):
        if (not isinstance(profile, Mapping) or profile.get("mix") != MIX
                or profile.get("source") != "official-leaderboard"
                or _instant(profile.get("asOf")) != snapshot_at):
            raise ProPumbilityError("The official baseline must use the same Phoenix2 snapshot.")
        player_id = _official_id(profile.get("playerId"))
        subject = by_player.get(player_id)
        if (player_id in official_profiles or subject is None
                or _decimal(profile.get("pumbility")) != subject["rating"]
                or profile.get("rank") != subject["rank"]
                or not isinstance(profile.get("placements"), list)):
            raise ProPumbilityError("An official baseline does not match the overall ranking.")
        for row in profile["placements"]:
            if (not isinstance(row, Mapping) or row.get("isSupplemented") is not False
                    or not isinstance(row.get("chartId"), str) or type(row.get("score")) is not int
                    or not 0 <= row["score"] <= 1_000_000):
                raise ProPumbilityError("The official baseline contains an invalid or supplemented score.")
        official_profiles[player_id] = profile
    return _Context(snapshot, ranking_rows, by_player, charts, candidates, boards, official_profiles)


def _baseline(context: _Context, subject: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Mapping[str, Any]]]:
    """Reconstruct the observed official Top 50 using the declared plate guesses."""
    profile = context.official_profiles.get(subject["playerId"])
    if profile is None:
        return None, {}
    best: dict[str, Mapping[str, Any]] = {}
    for row in profile["placements"]:
        cid = str(row.get("chartId") or "")
        chart = context.charts.get(cid)
        if chart is None:
            continue
        target = projected_target(row["score"])
        value = phoenix2_pumbility(chart["type"], chart["level"], target["targetGrade"], target["assumedPlate"])
        cents = int(round(value * 100))
        candidate = {"chartId": cid, "score": row["score"], "plate": target["assumedPlate"], "cents": cents,
                     "songName": str(chart.get("songName") or "Untitled chart"),
                     "chartLabel": str(chart.get("difficulty") or ("S" if chart["type"] == "Single" else "D") + str(chart["level"])),
                     "type": chart["type"], "level": chart["level"], "grade": target["targetGrade"],
                     "plateEstimated": True, "pumbility": value}
        previous = best.get(cid)
        if previous is None or (cents, row["score"]) > (previous["cents"], previous["score"]):
            best[cid] = candidate
    return _baseline_summary(best, subject, "official-leaderboard", profile.get("fetchedAtUtc"), profile["asOf"]), best


def _baseline_summary(best: Mapping[str, Mapping[str, Any]], subject: Mapping[str, Any], source: str,
                       synced_at: Any, snapshot_at: str) -> dict[str, Any]:
    top = sorted(best.values(), key=lambda row: (-row["cents"], str(row["chartId"])))[:50]
    total = sum(row["cents"] for row in top) / 100
    return {"source": source, "estimated": source == "official-leaderboard", "scoreCount": len(best),
            "top50Pumbility": total, "officialPumbility": float(subject["rating"]),
            "differenceFromOfficial": round(total - float(subject["rating"]), 2),
            "cutoffPumbility": top[-1]["cents"] / 100 if len(top) == 50 else None,
            "syncedAtUtc": synced_at, "snapshotAt": snapshot_at, "fallbackReason": None,
            "top50": [{key: value for key, value in row.items() if key != "cents"} for row in top]}


def _player_baseline(context: _Context, subject: Mapping[str, Any], profile: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Mapping[str, Any]]]:
    """Use a populated personal Top 50; otherwise reconstruct official scores."""
    best: dict[str, Mapping[str, Any]] = {}
    if profile.get("scoresComplete") is True:
        for row in profile.get("scores", []):
            if not isinstance(row, Mapping) or row.get("mix", MIX) != MIX or row.get("isBroken"):
                continue
            cid = str(row.get("chartId") or "")
            chart = context.charts.get(cid)
            value = _decimal(row.get("pumbility"))
            if chart is None or value is None or value < 0:
                continue
            cents = int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))
            raw = _decimal(row.get("score"))
            candidate = {"chartId": cid, "cents": cents, "score": float(raw) if raw is not None else None,
                         "plate": row.get("plate"), "grade": grade_for_score(raw), "plateEstimated": False,
                         "pumbility": cents / 100, "songName": str(chart.get("songName") or "Untitled chart"),
                         "chartLabel": str(chart.get("difficulty") or ("S" if chart["type"] == "Single" else "D") + str(chart["level"])),
                         "type": chart["type"], "level": chart["level"]}
            old = best.get(cid)
            if old is None or (cents, raw or Decimal(0)) > (old["cents"], _decimal(old.get("score")) or Decimal(0)):
                best[cid] = candidate
    personal = _baseline_summary(best, subject, "piuscores", profile.get("scoresSyncedAtUtc"), context.snapshot["snapshotAt"])
    # A later synced personal total below the published standing is incomplete;
    # a successful API read alone does not prove the player imported every PB.
    if personal["cutoffPumbility"] is not None and Decimal(str(personal["top50Pumbility"])) + Decimal("0.01") >= subject["rating"]:
        return personal, best
    official, official_best = _baseline(context, subject)
    if official is not None:
        official["fallbackReason"] = ("PIUScores has fewer than 50 usable Phoenix 2 scores." if len(best) < 50
                                      else "The PIUScores Top 50 total is below the official leaderboard total, indicating an incomplete import.")
    return official, official_best


def _build(context: _Context, profile: Mapping[str, Any], generation: str, generated_at: str) -> dict[str, Any]:
    if profile.get("mix") != MIX:
        raise ProPumbilityError("The player identity must belong to the Phoenix2 capture.")
    official_id = profile.get("officialPlayerId")
    subject = context.by_player.get(_official_id(official_id)) if official_id is not None else None
    player = {"playerKey": str(profile.get("playerKey") or ""),
              "displayName": str(profile.get("displayName") or profile.get("username") or "Player"),
              "officialDisplayName": subject["gameTag"] if subject else None,
              "overallRank": subject["rank"] if subject else None,
              "overallPumbility": float(subject["rating"]) if subject else None}
    payload: dict[str, Any] = {"schemaVersion": SCHEMA_VERSION, "method": METHOD, "mix": MIX,
        "scope": "overall", "generation": generation, "snapshotAt": context.snapshot["snapshotAt"],
        "fetchedAtUtc": context.snapshot["fetchedAtUtc"], "generatedAtUtc": generated_at,
        "status": "ready", "message": None, "eligible": False, "player": player,
        "cohort": None, "baseline": None, "recommendations": [],
        "candidateCount": len(context.candidates), "unobservedChartCount": 0}
    if official_id is None:
        payload.update(status="identity-unresolved", message="Link your official Phoenix 2 game tag by importing scores on PIUScores.")
        return payload
    if subject is None or subject["rank"] > MAXIMUM_RANK:
        payload.update(status="ineligible", message="Pro Farming is available to the official overall Pumbility top 300.")
        return payload
    payload["eligible"] = True
    peers, cohort = _cohort(context.rankings, subject)
    payload["cohort"] = cohort
    baseline, existing = _player_baseline(context, subject, profile)
    payload["baseline"] = baseline
    if baseline is None or baseline["cutoffPumbility"] is None:
        payload.update(status="baseline-unavailable", message="At least 50 published Phoenix 2 leaderboard scores are needed to estimate the Top 50 cutoff. Missing scores are not empty slots.")
        return payload
    top_ids = set(sorted(existing, key=lambda cid: (-existing[cid]["cents"], cid))[:50])
    peer_ids = {row["playerId"] for row in peers}
    rows = []
    for chart in context.candidates:
        board = context.boards[chart["chartId"]]
        observed = [score for pid, score in board["scores"].items() if pid in peer_ids]
        if not observed:
            payload["unobservedChartCount"] += 1
            continue
        target = projected_target(_percentile(observed))
        value = phoenix2_pumbility(chart["type"], chart["level"], target["targetGrade"], target["assumedPlate"])
        old = existing.get(chart["chartId"])
        old_value = old["cents"] / 100 if old else None
        gain = _top50_marginal_gain(value, existing_pumbility=old_value,
            existing_in_top50=chart["chartId"] in top_ids,
            current_score_count=baseline["scoreCount"], cutoff=baseline["cutoffPumbility"])
        gain = round(gain, 2)
        if gain <= 0:
            continue
        kind = chart["type"]
        rows.append({"chartId": chart["chartId"], "songName": str(chart.get("songName") or "Untitled chart"),
            "chartLabel": str(chart.get("difficulty") or ("S" if kind == "Single" else "D") + str(chart["level"])),
            "type": kind, "level": chart["level"], **target,
            "expectedPumbility": value, "projectedGain": gain,
            "currentScore": float(old["score"]) if old and _decimal(old.get("score")) is not None else None,
            "currentPumbility": old_value, "currentPlate": old.get("plate") if old else None,
            "currentPlateEstimated": bool(old and old.get("plateEstimated")),
            "peerScoreCount": len(observed), "cohortPlayerCount": len(peers),
            "boardPossiblyTruncated": bool(board.get("possiblyTruncated")),
            "limitedData": len(observed) < MINIMUM_PEERS or bool(board.get("possiblyTruncated"))})
    payload["recommendations"] = sorted(rows, key=lambda row: (-row["projectedGain"], -row["expectedPumbility"],
                                                               row["songName"].casefold(), row["chartId"]))
    if not rows:
        payload["message"] = "No observed peer targets currently improve this player's Phoenix 2 overall Top 50."
    if cohort["limitedData"]:
        payload["message"] = "Fewer than ten other players are available in the official ranking; targets have limited support."
    return payload


def build_pro_recommendation(snapshot: Mapping[str, Any], profile: Mapping[str, Any], *,
                             generated_at_utc: str | None = None, generation: str | None = None) -> dict[str, Any]:
    """Build one overall-only response from a complete matching P2 capture."""
    context = _prepare(snapshot)
    generated_at = generated_at_utc or datetime.now(timezone.utc).isoformat()
    _instant(generated_at)
    identity = generation or _digest({"method": METHOD, "snapshot": snapshot, "profile": profile})
    return _build(context, profile, identity, generated_at)


def build_pro_artifacts(snapshot: Mapping[str, Any], profiles: Sequence[Mapping[str, Any]], *,
                        generated_at_utc: str | None = None) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Build an atomic index/profile generation, including official-only profiles."""
    context = _prepare(snapshot)
    generated_at = generated_at_utc or datetime.now(timezone.utc).isoformat()
    _instant(generated_at)
    inputs = list(profiles)
    represented = {_official_id(profile["officialPlayerId"]) for profile in inputs if profile.get("officialPlayerId") is not None}
    for row in context.rankings:
        if row["rank"] > MAXIMUM_RANK or row["playerId"] in represented:
            continue
        inputs.append({"mix": MIX, "playerKey": "op_" + hashlib.sha256(row["playerId"].encode()).hexdigest(),
            "displayName": row["gameTag"] or "Official player", "officialPlayerId": row["playerId"],
            "scores": [], "scoresComplete": False, "scoresSyncedAtUtc": None})
    keys = [str(profile.get("playerKey") or "") for profile in inputs]
    if any(not key for key in keys) or len(set(keys)) != len(keys):
        raise ProPumbilityError("The Pro player roster contains missing or duplicate player keys.")
    generation = _digest({"method": METHOD, "snapshot": snapshot, "profiles": inputs})
    payloads = {str(profile["playerKey"]): _build(context, profile, generation, generated_at) for profile in inputs}
    summaries = [{**payload["player"], "status": payload["status"], "eligible": payload["eligible"]}
                 for payload in payloads.values()]
    summaries.sort(key=lambda player: (player["overallRank"] or math.inf, player["displayName"].casefold(), player["playerKey"]))
    index = {"schemaVersion": SCHEMA_VERSION, "method": METHOD, "mix": MIX, "scope": "overall",
        "generation": generation, "snapshotAt": snapshot["snapshotAt"], "fetchedAtUtc": snapshot["fetchedAtUtc"],
        "generatedAtUtc": generated_at, "players": summaries}
    return index, payloads
