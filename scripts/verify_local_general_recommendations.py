#!/usr/bin/env python3
"""Verify a local General generation against its Phoenix 2 submitted inputs."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from general_pumbility import GeneralPumbilityModel, METHOD  # noqa: E402
from piu_recommendations import public_player_key  # noqa: E402
from pumbility_contract import RECOMMENDATION_SCHEMA_VERSION  # noqa: E402
from scripts.build_local_recommendations import (  # noqa: E402
    COMBINED_OUTPUT_PATH, GENERATIONS_PATH, OUTPUT_PATH, _read_snapshot,
)


def verify_ranking(result: dict) -> None:
    rows = result["filterCandidates"]
    ranks = []
    for field in ("projectedGain", "scoreAtOrAboveTargetCount"):
        counts = Counter(row[field] for row in rows)
        better = 0
        values = {}
        for value in sorted(counts, reverse=True):
            values[value] = better + 1
            better += counts[value]
        ranks.append(values)
    for position, row in enumerate(rows, 1):
        assert row["gainRank"] == ranks[0][row["projectedGain"]]
        assert row["scoreCountRank"] == ranks[1][row["scoreAtOrAboveTargetCount"]]
        assert row["rankScore"] == row["gainRank"] + row["scoreCountRank"]
        assert row["recommendationRank"] == position
        assert type(row["scoreAtOrAboveTargetCount"]) is int and row["scoreAtOrAboveTargetCount"] >= 0
    key = lambda row: (
        row["rankScore"], -row["projectedGain"], -row["scoreAtOrAboveTargetCount"],
        -(row.get("expectedPumbility") or row.get("expectedCoopRating") or 0),
        row["songName"].casefold(), row["chartId"],
    )
    assert rows == sorted(rows, key=key)
    assert result["topRecommendations"] == rows[:50]


def verify() -> dict:
    snapshot = _read_snapshot("phoenix2")
    index = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    assert index["schemaVersion"] == RECOMMENDATION_SCHEMA_VERSION == 30
    assert index["method"]["id"] == METHOD
    generation = GENERATIONS_PATH / index["generationKey"]
    restored = GeneralPumbilityModel.from_npz_bytes((generation / "peer-model.npz").read_bytes())
    fresh = GeneralPumbilityModel.from_snapshot(
        snapshot, peer_windows=restored.peer_windows, minimum_peers=restored.minimum_peers,
    )
    assert restored.charts == fresh.charts
    assert restored.facts == fresh.facts
    assert restored.metadata == fresh.metadata
    assert restored.source_timestamp == snapshot["generatedAtUtc"]
    tiers = json.loads(COMBINED_OUTPUT_PATH.read_text(encoding="utf-8"))
    annotations = [row for key in ("singles", "doubles", "coop") for row in tiers.get(key, [])]
    accounts = {public_player_key(row["playerId"]): row for row in snapshot["players"]}
    scores = defaultdict(list)
    for row in snapshot["scores"]:
        scores[row["playerId"]].append(row)
    shards, ready, suggestions, limited, samples = {}, Counter(), Counter(), Counter(), {}
    for metadata in index["players"]:
        shard_number = metadata["shard"]
        if shard_number not in shards:
            shard = json.loads((generation / "shards" / f"{shard_number:04d}.json").read_text(encoding="utf-8"))
            assert shard["generationKey"] == index["generationKey"]
            shards[shard_number] = {row["playerKey"]: row for row in shard["players"]}
        actual = shards[shard_number][metadata["playerKey"]]
        account = accounts[metadata["playerKey"]]
        expected = restored.build_player(
            account["playerId"], scores=scores[account["playerId"]],
            chart_annotations=annotations, chart_catalog=snapshot["charts"],
            include_candidates=True, player_synced_at_utc=account.get("lastSyncedAtUtc"),
        )["modes"]
        for mode in ("singles", "doubles", "overall"):
            result = actual["modes"][mode]
            assert result == expected[mode], f"Stored/direct mismatch for {metadata['playerKey']} {mode}"
            assert result["skillMethod"] == METHOD
            assert not {"scoringRating", "projectionRating", "clearingRating", "clearingSkill"}.intersection(result)
            assert result["requiredScoreCount"] == 50
            assert len(result["topRecommendations"]) <= 50
            assert result["currentTop50Count"] == min(50, result["validScoreCount"])
            if mode != "overall":
                assert result["eligible"] == (result["validScoreCount"] >= 50)
            ready[mode] += bool(result["eligible"])
            rows = result["filterCandidates"]
            verify_ranking(result)
            assert result["filterCandidateCount"] == len(rows)
            assert len({row["chartId"] for row in rows}) == len(rows)
            for row in rows:
                assert row["level"] >= 16 and row["projectedGain"] > 0
                assert row["scoreProjectionSource"] == "piuscores-peer-median"
                assert row["peerScoreCount"] > 0
                assert row["plateProjectionSource"] == "piuscores-peer-median"
                assert "assumedPlate" not in row
                assert row["peerPairedScoreCount"] == row["peerScoreCount"]
                assert row["scoreAtOrAboveTargetCount"] <= row["peerPairedScoreCount"]
                assert mode == "overall" or row["sourceMode"] == mode
                limited[mode] += bool(row["limitedData"])
            suggestions[mode] += len(rows)
        coop = actual["modes"]["coop"]
        verify_ranking(coop)
        for row in coop["filterCandidates"]:
            assert row["scoreAtOrAboveTargetCount"] == restored.count_scores_at_least(
                account["playerId"], row["chartId"], row["projectedScore"],
            )
        suggestions["coop"] += len(coop["filterCandidates"])
        states = [actual["modes"][key]["eligible"] for key in ("singles", "doubles")]
        category = "both-modes-ready" if all(states) else "one-mode-ready" if any(states) else "insufficient-history"
        samples.setdefault(category, metadata["playerKey"])
    return {
        "schemaVersion": index["schemaVersion"], "method": METHOD,
        "generation": index["generationKey"], "sourceCapturedAtUtc": restored.source_timestamp,
        "peerWindows": restored.metadata["peerWindows"], "minimumPeers": restored.metadata["minimumPeers"],
        "playersVerified": len(index["players"]), "sourcePlayers": len(snapshot["players"]),
        "sourceCharts": len(snapshot["charts"]), "sourceScores": len(snapshot["scores"]),
        "modelRoundTrip": True, "allPlayerDirectBuildParity": True,
        "combinedRankVerification": True, "coopSupportVerification": True,
        "readyPlayers": dict(ready), "recommendations": dict(suggestions),
        "limitedSupportRecommendations": dict(limited), "samplePlayerKeys": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary = verify()
    encoded = json.dumps(summary, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
