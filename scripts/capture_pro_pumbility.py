#!/usr/bin/env python3
"""Capture fresh Phoenix 2 pro inputs without modifying generic score snapshots."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from official_scores import normalize_official_board  # noqa: E402
from phoenix2_sync import attach_song_bpm_metadata, sanitize_chart, sanitize_score  # noqa: E402
from piu_misgrade_analyzer import ApiError, PiuScoresClient  # noqa: E402
from piu_recommendations import public_player_key  # noqa: E402
from scripts.capture_official_score_snapshot import _api_key  # noqa: E402
from scripts.capture_private_score_snapshot import _ensure_git_ignored  # noqa: E402

DEFAULT_OUTPUT = ROOT / ".local-data/piu-scores/pro-modes/current"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("A source timestamp is missing.")
    # .NET emits 100-nanosecond precision; Python 3.9 accepts microseconds.
    normalized = re.sub(r"(\.\d{6})\d+(?=Z|[+-]|$)", r"\1", value)
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("A source timestamp lacks a timezone.")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def same_instant(left: Any, right: Any) -> bool:
    try:
        return timestamp(left) == timestamp(right)
    except (TypeError, ValueError):
        return False


def is_candidate(chart: Mapping[str, Any]) -> bool:
    level = chart.get("level")
    if isinstance(level, bool) or not isinstance(level, (int, float)):
        return False
    return (chart.get("type") == "Single" and level >= 23) or (chart.get("type") == "Double" and level >= 24)


def fetch_official_board(client: Any, chart_id: str, snapshot_at: str) -> dict:
    """Capture a board or explicitly record the API's unmirrored-chart response."""
    try:
        payload = client._get_json(f"api/v2/official/charts/{chart_id}/board",
                                   params={"mix": "Phoenix2", "supplemented": "false"})
    except ApiError as exc:
        if str(exc) != "PIU Scores returned HTTP 404. Detail: No official board is mirrored for that chart.":
            raise
        board = normalize_official_board(chart_id, {"asOf": snapshot_at, "data": []})
        return {**board, "mix": "Phoenix2", "source": "piuscores-official", "boardStatus": "unmirrored",
                "asOfSource": "official-ranking-snapshot", "fetchedAtUtc": utc_now(),
                "sourceResponse": {"statusCode": 404, "detail": "No official board is mirrored for that chart."}}
    payload["asOf"] = timestamp(payload.get("asOf"))
    board = normalize_official_board(chart_id, payload)
    if not same_instant(board["asOf"], snapshot_at):
        raise ValueError("An official chart has a different source snapshot; completed generation was not published.")
    return board


def normalize_official_profile(payload: Mapping[str, Any], ranking: Mapping[str, Any],
                               snapshot_at: str, fetched_at: str) -> dict:
    """Keep only current, unsupplemented placements for a known official player."""
    person = payload.get("player")
    expected = ranking["player"]
    if (not isinstance(person, Mapping) or person.get("playerId") != expected["playerId"]
            or person.get("isSupplemented") is not False
            or payload.get("pumbilityIsSupplemented") is not False
            or payload.get("pumbility") != ranking["rating"]
            or payload.get("pumbilityRank") != ranking["rank"]):
        raise ValueError("An official player profile does not match the overall ranking.")
    history = payload.get("history") or []
    if not history or not same_instant(history[-1].get("at"), snapshot_at):
        raise ValueError("An official player profile has a different source snapshot.")
    placements = payload.get("placements")
    if not isinstance(placements, list):
        raise ValueError("The official player placements are unavailable.")
    scores = []
    for row in placements:
        if (not isinstance(row, Mapping) or row.get("isSupplemented") is not False
                or not isinstance(row.get("chartId"), str) or type(row.get("score")) is not int
                or not 0 <= row["score"] <= 1_000_000):
            raise ValueError("An official player placement is supplemented or invalid.")
        scores.append({"chartId": row["chartId"], "score": row["score"],
                       "place": row.get("place"), "isSupplemented": False})
    return {"mix": "Phoenix2", "source": "official-leaderboard",
            "playerId": f"official:{expected['playerId']}", "asOf": timestamp(snapshot_at),
            "fetchedAtUtc": fetched_at, "pumbility": payload["pumbility"],
            "rank": payload["pumbilityRank"], "placements": scores}


def identity_profiles(players: list[dict], rankings: Mapping[str, Any]) -> list[dict]:
    """Make private, exact-tag links without copying tags to public artifacts."""
    by_tag: dict[str, set[str]] = defaultdict(set)
    for row in rankings["data"]:
        person = row["player"]
        tag = person.get("gameTag")
        if isinstance(tag, str) and tag:
            by_tag[tag].add(f"official:{person['playerId']}")
    username_counts = Counter(str(row.get("username") or "").strip().casefold() for row in players)
    result = []
    for person in players:
        account_id = str(person.get("userId") or "").strip()
        if not account_id:
            continue
        key = public_player_key(account_id)
        username = str(person.get("username") or "").strip()
        tag = person.get("gameTag")
        matches = by_tag.get(tag, set()) if isinstance(tag, str) and tag else set()
        status = "missing-tag" if not tag else "unmatched" if not matches else "ambiguous" if len(matches) > 1 else "matched"
        result.append({
            "mix": "Phoenix2", "playerKey": key, "accountId": account_id,
            "username": username, "displayName": f"{username} · {key[-4:]}" if username_counts[username.casefold()] > 1 else username or f"Player {key[-4:]}",
            "isPublic": bool(person.get("isPublic")),
            "officialPlayerId": next(iter(matches)) if len(matches) == 1 else None,
            "matchStatus": status, "scores": [], "scoresComplete": False,
            "scoresSyncedAtUtc": None,
        })
    return sorted(result, key=lambda row: row["playerKey"])


def capture(client: Any, output: Path = DEFAULT_OUTPUT, *, workers: int = 4, restart: bool = False) -> tuple[dict, list[dict]]:
    """Capture complete source generations; publish the public index separately."""
    output = output.resolve()
    _ensure_git_ignored(output / "snapshot.json")
    fetched_at = utc_now()
    rankings = client._get_json("api/v2/official/rankings", params={"mix": "Phoenix2", "type": "All", "supplemented": "false"})
    if rankings.get("ratingIsOfficial") is not True or not isinstance(rankings.get("data"), list) or not rankings["data"]:
        raise ValueError("The Phoenix2 official overall ranking is unavailable.")
    snapshot_at = timestamp(rankings.get("snapshotAt"))
    rankings["snapshotAt"] = snapshot_at
    if any(row.get("player", {}).get("isSupplemented") is not False for row in rankings["data"]):
        raise ValueError("Supplemented players cannot enter the official pro capture.")
    songs = client.fetch_page_collection("api/v2/songs", {"mix": "Phoenix2", "limit": 500})
    raw_charts = client.fetch_page_collection("api/v2/charts", {"mix": "Phoenix2", "limit": 500})
    charts = [clean for chart in attach_song_bpm_metadata(raw_charts, songs, mix="phoenix2") if (clean := sanitize_chart(chart)) is not None]
    charts.sort(key=lambda row: row["id"])
    if not charts:
        raise ValueError("The current Phoenix2 catalog is empty.")
    targets = [chart for chart in charts if is_candidate(chart)]
    if not targets:
        raise ValueError("The current Phoenix2 catalog has no S23+/D24+ charts.")
    source_key = hashlib.sha256(json.dumps([rankings, charts], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    staging = ROOT / ".local-data/piu-scores/pro/capture" / (source_key + ("-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") if restart else ""))
    staging.mkdir(parents=True, exist_ok=True)

    def board_for(chart: dict) -> dict:
        chart_id = chart["id"]
        checkpoint = staging / "boards" / f"{chart_id}.json"
        if checkpoint.exists():
            previous = json.loads(checkpoint.read_text(encoding="utf-8"))
            if previous.get("chartId") == chart_id and same_instant(previous.get("asOf"), snapshot_at):
                return previous
        board = fetch_official_board(client, chart_id, snapshot_at)
        write_json(checkpoint, board)
        return board

    boards = []
    print(f"Capturing Phoenix2 official boards: {len(targets)} charts", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, min(8, workers))) as executor:
        futures = [executor.submit(board_for, chart) for chart in targets]
        for future in as_completed(futures):
            boards.append(future.result())
            if len(boards) % 20 == 0 or len(boards) == len(targets):
                print(f"Official boards: {len(boards)}/{len(targets)}", flush=True)
    boards.sort(key=lambda row: row["chartId"])

    readable = client.fetch_page_collection("api/v2/players", {"limit": 500})
    profiles = identity_profiles(readable, rankings)
    population = rankings["data"]

    def official_profile(ranking: dict) -> dict:
        person = ranking["player"]
        checkpoint = staging / "official-players" / f"{person['playerId']}.json"
        if checkpoint.exists():
            previous = json.loads(checkpoint.read_text(encoding="utf-8"))
            if (previous.get("mix") == "Phoenix2" and same_instant(previous.get("asOf"), snapshot_at)
                    and previous.get("playerId") == f"official:{person['playerId']}"):
                return previous
        payload = client._get_json("api/v2/official/players/" + quote(person["gameTag"], safe=""),
                                   params={"mix": "Phoenix2", "supplemented": "false"})
        result = normalize_official_profile(payload, ranking, snapshot_at, utc_now())
        write_json(checkpoint, result)
        return result

    official_profiles = []
    print(f"Capturing Phoenix2 official mode population: {len(population)} ranked players", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, min(8, workers))) as executor:
        futures = [executor.submit(official_profile, ranking) for ranking in population]
        for future in as_completed(futures):
            official_profiles.append(future.result())
            if len(official_profiles) % 50 == 0 or len(official_profiles) == len(population):
                print(f"Official score histories: {len(official_profiles)}/{len(population)}", flush=True)
    official_profiles.sort(key=lambda row: row["rank"])

    eligible_ids = {row["playerId"] for row in official_profiles}
    matched = [profile for profile in profiles if profile["officialPlayerId"] in eligible_ids]
    valid_chart_ids = {chart["id"] for chart in charts}

    def personal_profile(profile: dict) -> dict:
        raw_scores = client.fetch_page_collection(f"api/v2/players/{profile['accountId']}/scores",
                                                   {"mix": "Phoenix2", "limit": 500})
        best: dict[str, dict] = {}
        for row in raw_scores:
            clean = sanitize_score(row, profile["accountId"])
            if clean is None or clean["chartId"] not in valid_chart_ids:
                continue
            clean["mix"] = "Phoenix2"
            old = best.get(clean["chartId"])
            if old is None or (clean["pumbility"], clean["score"] or 0) > (old["pumbility"], old["score"] or 0):
                best[clean["chartId"]] = clean
        return {**profile, "scores": sorted(best.values(), key=lambda row: row["chartId"]),
                "scoresComplete": True, "scoresSyncedAtUtc": utc_now()}

    personal = {}
    print(f"Capturing Phoenix2 personal histories where available: {len(matched)} accounts", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, min(8, workers))) as executor:
        for result in executor.map(personal_profile, matched):
            personal[result["playerKey"]] = result
    profiles = [personal.get(profile["playerKey"], profile) for profile in profiles]
    snapshot = {"schemaVersion": 1, "mix": "Phoenix2", "source": "piuscores-official", "snapshotAt": snapshot_at,
                "fetchedAtUtc": fetched_at, "rankings": rankings, "charts": charts, "boards": boards,
                "officialProfiles": official_profiles}
    # These are private source inputs. The public index is only advanced by build.
    write_json(output / "snapshot.json", snapshot)
    write_json(output / "profiles.json", profiles)
    return snapshot, profiles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--skip-build", action="store_true", help="Capture private source inputs without publishing new public artifacts.")
    args = parser.parse_args()
    try:
        client = PiuScoresClient(api_key=_api_key())
        snapshot, profiles = capture(client, args.output_dir, workers=args.workers, restart=args.restart)
        if not args.skip_build:
            from scripts.build_pro_pumbility import build_local
            build_local(args.output_dir)
        print(json.dumps({"snapshotAt": snapshot["snapshotAt"], "fetchedAtUtc": snapshot["fetchedAtUtc"],
                          "charts": len(snapshot["boards"]), "officialPlayers": len(snapshot["rankings"]["data"]),
                          "readableProfiles": len(profiles), "officialScoreProfiles": len(snapshot["officialProfiles"]),
                          "officialPlacements": sum(len(row["placements"]) for row in snapshot["officialProfiles"]),
                          "httpRequests": client.request_count}, indent=2))
        return 0
    except (ApiError, OSError, ValueError, KeyError) as error:
        print(f"Pro capture failed without publishing a public generation: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
