"""Deterministic raw maximum merge of validated private official captures."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from typing import Any, Mapping

from official_scoring_sources import (
    MIXES, SCORING_MERGE_POLICY, _sha, _validate_capture,
    canonical_scoring_manifest_sha256, scoring_target_charts,
)


def _opaque(key: str) -> str:
    return "official-player:" + hashlib.sha256(key.encode("utf-8")).hexdigest()


def _full_tag(tag: Any) -> bool:
    if not isinstance(tag, str):
        return False
    name, separator, discriminator = tag.rpartition("#")
    return bool(name and separator and discriminator and not any(c.isspace() for c in discriminator))


def _known_count(chart: Mapping[str, Any] | None) -> int | None:
    value = chart.get("noteCount") if chart else None
    return value if type(value) is int and value > 0 else None


def _comparability(current: dict, old: dict | None, retained: set[str]) -> tuple[str, bool, bool]:
    if not retained:
        return "no-evidence", False, False
    current_count, old_count = _known_count(current), _known_count(old)
    if retained == {"Phoenix2"}:
        return ("single-source-known", True, False) if current_count else ("single-source-unknown", False, True)
    # Phoenix-only historical scores must still be comparable with today's chart.
    if current_count is None or old_count is None:
        return "unknown", False, True
    if current_count != old_count:
        return "changed", False, True
    return ("same" if len(retained) == 2 else "single-source-known"), True, False


def merge_official_scores(manifest: Mapping[str, Any], *, exclusions=None) -> dict[str, Any]:
    """Use a manifest returned by load_official_scoring_manifest.

    ``boards`` is chart UUID -> opaque player ID -> maximum raw integer score.
    ``charts`` includes every eligible current chart, even with no observations.
    ``_privateProvenance`` is private replay evidence and MUST NOT be published.
    """
    if manifest.get("mergePolicyVersion") != SCORING_MERGE_POLICY or "_captures" not in manifest:
        raise ValueError("Load and validate an official scoring manifest before merging.")
    captures = manifest["_captures"]
    current = manifest["_currentCharts"]
    if (set(captures) != set(MIXES)
            or current != scoring_target_charts(captures["Phoenix2"]["catalog"])
            or manifest.get("_manifestSha256") != canonical_scoring_manifest_sha256(manifest)):
        raise ValueError("Official scoring loaded manifest is inconsistent.")
    for mix in MIXES:
        _validate_capture(captures[mix], current)
        if _sha(captures[mix]) != manifest["captures"][mix]["sha256"]:
            raise ValueError("Official scoring capture changed after loading.")
    tags_by_id = {m: defaultdict(set) for m in MIXES}
    ids_by_tag = {m: defaultdict(set) for m in MIXES}
    all_ids = {m: set() for m in MIXES}
    for mix in MIXES:
        for board in captures[mix]["boards"].values():
            for row in board["entries"]:
                pid, tag = row["playerId"], row["gameTag"]
                all_ids[mix].add(pid)
                if tag:
                    tags_by_id[mix][pid].add(tag)
                    ids_by_tag[mix][tag].add(pid)
    matches = {}
    for tag in sorted(set(ids_by_tag["Phoenix"]) & set(ids_by_tag["Phoenix2"])):
        if not _full_tag(tag) or any(len(ids_by_tag[m][tag]) != 1 for m in MIXES):
            continue
        pair = {m: next(iter(ids_by_tag[m][tag])) for m in MIXES}
        if any(tags_by_id[m][pair[m]] != {tag} for m in MIXES):
            continue
        for mix, pid in pair.items():
            matches[(mix, pid)] = _opaque("tag\0" + tag)
    canonical = {(mix, pid): matches.get((mix, pid), _opaque(f"source\0{mix}\0{pid}"))
                 for mix in MIXES for pid in sorted(all_ids[mix])}
    diagnostics = {
        "mergePolicyVersion": SCORING_MERGE_POLICY,
        "matchedSourceIdentityPairs": len(matches) // 2,
        "unresolvedSourceIdentities": {m: len(all_ids[m]) - sum((m, p) in matches for p in all_ids[m]) for m in MIXES},
        "ambiguousSourceIdentities": {m: sum(len(v) > 1 for v in tags_by_id[m].values()) for m in MIXES},
        "ambiguousFullTags": {m: sum(_full_tag(t) and len(ids) > 1 for t, ids in ids_by_tag[m].items()) for m in MIXES},
        "rawObservations": {m: sum(b["rawRowCount"] for b in captures[m]["boards"].values()) for m in MIXES},
        "sourceBoardStates": {m: dict(sorted(Counter(b["status"] for b in captures[m]["boards"].values()).items())) for m in MIXES},
        "renamedPlayersResolved": False,
    }
    old_catalog = {c["id"]: c for c in captures["Phoenix"]["catalog"]}
    boards, charts, provenance = {}, {}, {}
    winning_mix_counts = Counter()
    for cid, chart in sorted(current.items()):
        observations = defaultdict(list)
        source_states = {}
        for mix in MIXES:
            board = captures[mix]["boards"][cid]
            source_states[mix] = {k: board[k] for k in ("status", "asOf", "rawRowCount", "maxRank", "cutoffScore", "possiblyTruncated")}
            for row in board["entries"]:
                if exclusions is not None and exclusions.excludes(mix, cid):
                    continue
                opaque = canonical[(mix, row["playerId"])]
                observations[opaque].append({"mix": mix, "playerId": row["playerId"], "score": row["score"],
                                             "place": row["place"], "matchMethod": "full-tag" if (mix, row["playerId"]) in matches else "source-id"})
        merged, private = {}, {}
        retained = set()
        cross_mix_pairs = ties = 0
        for opaque, records in sorted(observations.items()):
            score = max(r["score"] for r in records)
            winners = {r["mix"] for r in records if r["score"] == score}
            retained.update(winners)
            for mix in winners:
                winning_mix_counts[mix] += 1
            cross_mix_pairs += len({r["mix"] for r in records}) == 2
            ties += len(winners) == 2
            merged[opaque] = score
            private[opaque] = {"score": score, "winningSources": sorted(winners),
                               "observations": sorted(records, key=lambda r: (r["mix"], r["playerId"], -r["score"], r["place"]))}
        comparability, anchor_eligible, provisional = _comparability(chart, old_catalog.get(cid), retained)
        known_old, known_current = _known_count(old_catalog.get(cid)), _known_count(chart)
        charts[cid] = {"mode": chart["type"], "level": chart["level"], "noteCount": chart.get("noteCount"),
                       "anchorEligible": anchor_eligible, "comparability": comparability, "provisional": provisional,
                       "knownNoteCountChange": known_old is not None and known_current is not None and known_old != known_current,
                       "unknownNoteCount": known_current is None or ("Phoenix" in retained and known_old is None),
                       "retainedSources": sorted(retained), "sourceStates": source_states,
                       "mergedPlayers": len(merged), "matchedPlayerChartPairs": cross_mix_pairs, "tiedCrossMixMaxima": ties}
        if exclusions is not None and any(exclusions.excludes(m, cid) for m in MIXES):
            import copy
            charts[cid]["capturedSourceStates"] = copy.deepcopy(source_states)
            charts[cid]["sourceExclusions"] = [r for r in exclusions.public()["rules"] if r["enabled"] and r["chartId"] == cid]
            for mix in MIXES:
                if exclusions.excludes(mix, cid):
                    source_states[mix].update(status="empty", rawRowCount=0, maxRank=0,
                        cutoffScore=None, possiblyTruncated=False)
        if merged:
            boards[cid] = merged
            provenance[cid] = private
    diagnostics.update(mergedObservations=sum(len(b) for b in boards.values()),
                       nonemptyCharts=len(boards), targetCharts=len(charts),
                       retainedMaximumSources=dict(sorted(winning_mix_counts.items())),
                       comparabilityCounts=dict(sorted(Counter(c["comparability"] for c in charts.values()).items())))
    result = {"boards": boards, "charts": charts,
            "currentCatalog": captures["Phoenix2"]["catalog"],
            "sourceDates": {m: captures[m]["asOf"] for m in MIXES}, "diagnostics": diagnostics,
            "manifestSha256": manifest["_manifestSha256"], "_privateProvenance": provenance}
    if exclusions is not None:
        from chart_source_exclusions import canonical_hash
        result["sourceExclusions"] = exclusions.public()
        result["effectiveInputSha256"] = canonical_hash({"rawManifest": result["manifestSha256"],
            "policy": exclusions.semantic_hash(), "boards": boards})
    return result
