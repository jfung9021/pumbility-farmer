"""PIUScores-only Phoenix2 Top 50 peer model for the General recommender.

Population facts remain private in the serialized model. Public results contain
only the selected player's retained facts and aggregate peer support.
"""
from __future__ import annotations

import io
import json
from decimal import Decimal
from typing import Any, Mapping, Sequence

import numpy as np

from phoenix2_pumbility import GRADE_BANDS, PLATE_CODES, grade_for_score, normalize_plate, phoenix2_pumbility
from pumbility_peer_policy import (
    decimal_number, linear_median, marginal_gain_cents, normalize_minimum_peers, normalize_peer_windows,
    pumbility_cents, select_pumbility_peers,
)
from recommendation_ranking import RANKING_METHOD, rank_recommendations

METHOD = "general-piuscores-p50-modes-overall-top50-phoenix2-v4"
MODEL_SCHEMA_VERSION = 2
TOP_COUNT = 50
MINIMUM_LEVEL = 16
MODES = {"singles": "Single", "doubles": "Double"}
MODE_LABELS = {"Single": "Singles", "Double": "Doubles"}
PLATE_ORDER = ("Rough Game", "Fair Game", "Talented Game", "Marvelous Game",
               "Superb Game", "Extreme Game", "Ultimate Game", "Perfect Game")


def _safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, np.generic):
        return _safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _raw_score(value: Any) -> int | None:
    number = decimal_number(value)
    if number is None or number != number.to_integral_value() or not 0 <= number <= 1_000_000:
        return None
    return int(number)


def _catalog(rows: Sequence[Mapping[str, Any]], mode_types: Sequence[str]) -> dict[str, dict[str, Any]]:
    result = {}
    for raw in rows:
        if not isinstance(raw, Mapping) or raw.get("mix", "Phoenix2") != "Phoenix2":
            continue
        chart_id = str(raw.get("id", raw.get("chartId", "")) or "").strip()
        level = decimal_number(raw.get("level"))
        kind = raw.get("type")
        if not chart_id or kind not in mode_types or level is None or level <= 0 or level != int(level):
            continue
        result[chart_id] = {**_safe(dict(raw)), "chartId": chart_id, "type": kind, "level": int(level)}
    return result


def _facts(rows: Sequence[Mapping[str, Any]], charts: Mapping[str, Any], default_player_id: str | None = None) -> dict[str, dict[str, dict]]:
    result: dict[str, dict[str, dict]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping) or raw.get("mix", "Phoenix2") != "Phoenix2" or bool(raw.get("isBroken", False)):
            continue
        player_id = str(raw.get("playerId", default_player_id) or "").strip()
        chart_id = str(raw.get("chartId") or "").strip()
        cents = pumbility_cents(raw.get("pumbility"))
        if not player_id or chart_id not in charts or cents is None or (default_player_id is not None and player_id != default_player_id):
            continue
        score = _raw_score(raw.get("score"))
        item = {"chartId": chart_id, "cents": cents, "score": score,
                "plate": normalize_plate(raw.get("plate")), "recordedAt": str(raw.get("recordedAt") or "")}
        best = result.setdefault(player_id, {})
        old = best.get(chart_id)
        key = lambda row: (row["cents"], row["score"] if row["score"] is not None else -1,
                           row["recordedAt"], row["plate"] or "")
        if old is None or key(item) > key(old):
            best[chart_id] = item
    return result


def _ordered(facts: Mapping[str, Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(facts.values(), key=lambda row: (-row["cents"], row["chartId"]))


def _raw_score_support(rows: Sequence[Mapping[str, Any]], chart_ids: set[str]) -> dict[str, dict[str, int]]:
    """Keep one best submitted P2 raw score per account/chart, including Co-op."""
    result: dict[str, dict[str, int]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping) or raw.get("mix", "Phoenix2") != "Phoenix2" or bool(raw.get("isBroken", False)):
            continue
        player_id = str(raw.get("playerId") or "").strip()
        chart_id = str(raw.get("chartId") or "").strip()
        score = _raw_score(raw.get("score"))
        if not player_id or chart_id not in chart_ids or score is None:
            continue
        chart = result.setdefault(chart_id, {})
        chart[player_id] = max(score, chart.get(player_id, -1))
    return result


def _annotation_index(raw: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items() if isinstance(value, Mapping)}
    return {str(row.get("chartId", row.get("id", ""))): row for row in raw if isinstance(row, Mapping)}


def _chart_public(chart: Mapping[str, Any], annotation: Mapping[str, Any] | None) -> dict[str, Any]:
    value = {**chart, **(annotation or {})}
    # The current catalog carries one coherent display interval and focal BPM.
    # Historical tier annotations must not replace only part of that group.
    value.update({key: chart.get(key) for key in ("bpmMin", "bpmMax", "bpmFocal", "songType")})
    kind, level, chart_id = chart["type"], chart["level"], chart["chartId"]
    result = {key: _safe(value.get(key)) for key in (
        "imageUrl", "noteCount", "stepArtist", "bpmMin", "bpmMax", "estimatedDifficulty",
        "difficultyDelta", "difficultyCi95Low", "difficultyCi95High", "nContributors",
        "phoenix1Contributors", "phoenix2Contributors", "evidenceStatus",
    )}
    for key in ("bpmFocal", "songType"):
        if value.get(key) is not None:
            result[key] = _safe(value[key])
    result.update(mode=MODE_LABELS[kind], songName=str(value.get("songName") or "Unknown chart"),
                  difficulty=str(chart.get("difficulty") or ("S" if kind == "Single" else "D") + str(level)),
                  type=kind, level=level, chartId=chart_id)
    return result


def _top_public(fact: Mapping[str, Any], charts: Mapping[str, Any], annotations: Mapping[str, Any]) -> dict[str, Any]:
    chart_id = fact["chartId"]
    plate = fact["plate"]
    return {**_chart_public(charts[chart_id], annotations.get(chart_id)),
            "score": fact["score"], "pumbility": fact["cents"] / 100,
            "grade": grade_for_score(fact["score"]), "plate": plate,
            "plateCode": PLATE_CODES.get(plate) if plate else None}


def _sort_key(row: Mapping[str, Any]) -> tuple:
    return (-row["projectedGain"], -row["expectedPumbility"], row["songName"].casefold(), row["chartId"])


def _paired_target(observations: Sequence[tuple[int, str]]) -> dict[str, Any]:
    """Use the identical complete sample for raw-score and ordinal-plate medians."""
    raw_score = linear_median([score for score, _ in observations])
    plates = sorted((plate for _, plate in observations), key=PLATE_ORDER.index)
    plate = plates[(len(plates) - 1) // 2]
    grade = grade_for_score(raw_score)
    boundary = next(threshold for threshold, label, _ in GRADE_BANDS if label == grade)
    return {"rawPercentileScore": float(raw_score), "gradeBoundaryScore": boundary,
            "targetScore": 1_000_000 if plate == "Perfect Game" else boundary,
            "targetGrade": grade, "projectedPlate": plate, "projectedPlateCode": PLATE_CODES[plate]}


class GeneralPumbilityModel:
    """Immutable peer population; current player facts may be supplied at read time."""

    def __init__(self, charts: Mapping[str, Mapping[str, Any]], facts: Mapping[str, Mapping[str, Mapping[str, Any]]], *,
                 source_timestamp: str | None = None, mode_types: Sequence[str] = ("Single", "Double"),
                 support_scores: Mapping[str, Mapping[str, int]] | None = None,
                 peer_windows: Sequence[int] = (200,), minimum_peers: int = 20):
        self.charts = {str(key): dict(value) for key, value in charts.items()}
        self.facts = {str(player): {str(cid): dict(row) for cid, row in rows.items()} for player, rows in facts.items()}
        self.source_timestamp = source_timestamp
        self.mode_types = tuple(kind for kind in MODES.values() if kind in mode_types)
        self.peer_windows = normalize_peer_windows(peer_windows)
        self.minimum_peers = normalize_minimum_peers(minimum_peers)
        self._ratings: dict[str, list[dict[str, Any]]] = {mode: [] for mode in MODES}
        self._scores: dict[str, dict[str, tuple[int, str | None]]] = {}
        for player_id, rows in self.facts.items():
            for cid, fact in rows.items():
                if fact["score"] is not None:
                    self._scores.setdefault(cid, {})[player_id] = (fact["score"], fact["plate"])
            for mode, kind in MODES.items():
                values = _ordered({cid: row for cid, row in rows.items() if self.charts[cid]["type"] == kind})
                if len(values) >= TOP_COUNT:
                    self._ratings[mode].append({"playerId": player_id,
                        "rating": Decimal(sum(row["cents"] for row in values[:TOP_COUNT])) / 100})
        for rows in self._ratings.values():
            rows.sort(key=lambda row: (-row["rating"], row["playerId"]))
        self._support_scores = ({str(cid): {str(pid): score for pid, score in values.items()} for cid, values in support_scores.items()}
                                if support_scores is not None else
                                {cid: {pid: observation[0] for pid, observation in values.items()} for cid, values in self._scores.items()})

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any], *, mode_types: Sequence[str] = ("Single", "Double"),
                      peer_windows: Sequence[int] = (200,), minimum_peers: int = 20) -> "GeneralPumbilityModel":
        if snapshot.get("mix", "Phoenix2") != "Phoenix2":
            raise ValueError("General Pumbility requires submitted Phoenix2 scores.")
        charts = _catalog(snapshot.get("charts", []), mode_types)
        support_chart_ids = set(_catalog(snapshot.get("charts", []), ("Single", "Double", "CoOp")))
        timestamp = next((snapshot.get(key) for key in ("completedAtUtc", "generatedAtUtc", "fetchedAtUtc", "snapshotAt") if snapshot.get(key)), None)
        return cls(charts, _facts(snapshot.get("scores", []), charts), source_timestamp=timestamp, mode_types=mode_types,
                   support_scores=_raw_score_support(snapshot.get("scores", []), support_chart_ids),
                   peer_windows=peer_windows, minimum_peers=minimum_peers)

    @property
    def metadata(self) -> dict[str, Any]:
        return {"id": METHOD, "method": METHOD, "skillMethod": METHOD, "skillSource": "piuscores-submitted", "mix": "Phoenix2",
                "modelSchemaVersion": MODEL_SCHEMA_VERSION, "skillMetric": "mode-top50-pumbility",
                "requiredScoreCount": TOP_COUNT, "topPumbilityCount": TOP_COUNT, "minimumLevel": MINIMUM_LEVEL,
                "peerWindows": list(self.peer_windows), "minimumPeers": self.minimum_peers, "peerQuantile": 0.5,
                "targetObservationPolicy": "paired-valid-score-and-recorded-plate",
                "plateProjection": "lower-ordinal-median-of-paired-peer-plates",
                "rankingMethod": RANKING_METHOD,
                "rankingPolicy": {"method": RANKING_METHOD, "gainWeight": 1, "scoreCountWeight": 1,
                                  "scoreCount": "peer-raw-score-at-or-above-target-score"},
                "sourceTimestamp": self.source_timestamp,
                "modePopulationCounts": {mode: len(rows) for mode, rows in self._ratings.items()}}

    def method_metadata(self) -> dict[str, Any]:
        return self.metadata

    def player_counts(self, player_id: Any) -> dict[str, int]:
        rows = self.facts.get(str(player_id), {})
        return {mode: sum(self.charts[cid]["type"] == kind for cid in rows) for mode, kind in MODES.items()}

    def count_scores_at_least(self, player_id: Any, chart_id: Any, target_score: Any) -> int:
        target = _raw_score(target_score)
        if target is None:
            raise ValueError("A target score must be an integer from 0 to 1000000.")
        subject = str(player_id)
        return sum(pid != subject and score >= target for pid, score in self._support_scores.get(str(chart_id), {}).items())

    @classmethod
    def merge_models(cls, models: Sequence["GeneralPumbilityModel"]) -> "GeneralPumbilityModel":
        if not models:
            raise ValueError("No General Pumbility model parts were supplied.")
        charts, facts, kinds, support = {}, {}, set(), {}
        timestamp = models[0].source_timestamp
        peer_windows = models[0].peer_windows
        minimum_peers = models[0].minimum_peers
        for model in models:
            if model.source_timestamp != timestamp:
                raise ValueError("General model parts have different source timestamps.")
            if model.peer_windows != peer_windows:
                raise ValueError("General model parts have different peer window policies.")
            if model.minimum_peers != minimum_peers:
                raise ValueError("General model parts have different minimum peer policies.")
            kinds.update(model.mode_types)
            for cid, chart in model.charts.items():
                if cid in charts and charts[cid] != chart:
                    raise ValueError("General model parts disagree on chart metadata.")
                charts[cid] = chart
            for pid, rows in model.facts.items():
                target = facts.setdefault(pid, {})
                for cid, fact in rows.items():
                    if cid in target and target[cid] != fact:
                        raise ValueError("General model parts disagree on score facts.")
                    target[cid] = fact
            for cid, scores in model._support_scores.items():
                target = support.setdefault(cid, {})
                for pid, score in scores.items():
                    if pid in target and target[pid] != score:
                        raise ValueError("General model parts disagree on raw support scores.")
                    target[pid] = score
        return cls(charts, facts, source_timestamp=timestamp, mode_types=tuple(kinds), support_scores=support,
                   peer_windows=peer_windows, minimum_peers=minimum_peers)

    def to_npz_bytes(self) -> bytes:
        players, chart_ids = sorted(self.facts), sorted(self.charts)
        chart_indexes = {cid: index for index, cid in enumerate(chart_ids)}
        records, plates, dates = [], [], []
        for player_index, player_id in enumerate(players):
            for cid, fact in sorted(self.facts[player_id].items()):
                records.append((player_index, chart_indexes[cid], fact["cents"], -1 if fact["score"] is None else fact["score"]))
                plates.append(fact["plate"] or "")
                dates.append(fact["recordedAt"])
        header = {"schemaVersion": MODEL_SCHEMA_VERSION, "method": METHOD, "sourceTimestamp": self.source_timestamp,
                  "modeTypes": list(self.mode_types), "peerWindows": list(self.peer_windows), "minimumPeers": self.minimum_peers,
                  "charts": [self.charts[cid] for cid in chart_ids]}
        encoded = json.dumps(_safe(header), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        support_players = sorted({pid for scores in self._support_scores.values() for pid in scores})
        support_charts = sorted(self._support_scores)
        support_player_indexes = {pid: index for index, pid in enumerate(support_players)}
        support_records = [(support_player_indexes[pid], ci, score)
                           for ci, cid in enumerate(support_charts) for pid, score in sorted(self._support_scores[cid].items())]
        stream = io.BytesIO()
        np.savez_compressed(stream, header=np.frombuffer(encoded, dtype=np.uint8), players=np.asarray(players, dtype=str),
                            records=np.asarray(records, dtype=np.int64).reshape((-1, 4)),
                            plates=np.asarray(plates, dtype=str), dates=np.asarray(dates, dtype=str),
                            support_players=np.asarray(support_players, dtype=str), support_charts=np.asarray(support_charts, dtype=str),
                            support_records=np.asarray(support_records, dtype=np.int64).reshape((-1, 3)))
        return stream.getvalue()

    @classmethod
    def from_npz_bytes(cls, payload: bytes) -> "GeneralPumbilityModel":
        try:
            with np.load(io.BytesIO(payload), allow_pickle=False) as saved:
                header = json.loads(saved["header"].tobytes().decode("utf-8"))
                if header.get("schemaVersion") != MODEL_SCHEMA_VERSION or header.get("method") != METHOD:
                    raise ValueError("Incompatible General Pumbility model identity.")
                players = saved["players"].tolist()
                records, plates, dates = saved["records"], saved["plates"].tolist(), saved["dates"].tolist()
                if records.ndim != 2 or records.shape[1] != 4 or len(records) != len(plates) or len(records) != len(dates):
                    raise ValueError("Invalid General Pumbility model records.")
                charts = _catalog(header["charts"], header["modeTypes"])
                chart_ids = [row["chartId"] for row in header["charts"]]
                if len(charts) != len(chart_ids) or len(set(players)) != len(players):
                    raise ValueError("Invalid General Pumbility model identities.")
                rows = []
                for (pi, ci, cents, score), plate, date in zip(records.tolist(), plates, dates):
                    if not 0 <= pi < len(players) or not 0 <= ci < len(chart_ids) or cents < 0 or not -1 <= score <= 1_000_000:
                        raise ValueError("Invalid General Pumbility model score facts.")
                    rows.append({"playerId": players[pi], "chartId": chart_ids[ci], "pumbility": Decimal(cents) / 100,
                                 "score": None if score == -1 else score, "plate": plate, "recordedAt": date})
                facts = _facts(rows, charts)
                support_players, support_charts = saved["support_players"].tolist(), saved["support_charts"].tolist()
                support_records = saved["support_records"]
                if support_records.ndim != 2 or support_records.shape[1] != 3:
                    raise ValueError("Invalid General raw support score records.")
                if len(set(support_players)) != len(support_players) or len(set(support_charts)) != len(support_charts):
                    raise ValueError("Invalid General raw support identities.")
                support = {}
                for pi, ci, score in support_records.tolist():
                    if not 0 <= pi < len(support_players) or not 0 <= ci < len(support_charts) or not 0 <= score <= 1_000_000:
                        raise ValueError("Invalid General raw support score facts.")
                    support.setdefault(support_charts[ci], {})[support_players[pi]] = score
        except (KeyError, TypeError, IndexError, OSError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid General Pumbility model artifact.") from exc
        return cls(charts, facts, source_timestamp=header.get("sourceTimestamp"), mode_types=header["modeTypes"],
                   support_scores=support, peer_windows=header.get("peerWindows", (100, 150, 200)),
                   minimum_peers=header.get("minimumPeers", 10))

    def build_player(self, player_id: Any, *, scores: Sequence[Mapping[str, Any]] | None = None,
                     chart_annotations: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
                     include_candidates: bool = False, player_synced_at_utc: str | None = None,
                     chart_catalog: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        player_id = str(player_id)
        charts = self.charts if chart_catalog is None else _catalog(chart_catalog, self.mode_types)
        facts = ({cid: row for cid, row in self.facts.get(player_id, {}).items() if cid in charts}
                 if scores is None else _facts(scores, charts, player_id).get(player_id, {}))
        annotations = _annotation_index(chart_annotations)
        modes, source_rows = {}, {}
        for mode, kind in MODES.items():
            mode_facts = {cid: row for cid, row in facts.items() if charts[cid]["type"] == kind}
            ordered = _ordered(mode_facts)
            base = self._base(mode, ordered, charts, annotations, player_synced_at_utc)
            rows: list[dict[str, Any]] = []
            cohort = None
            unobserved = 0
            target_count = 0
            paired_observation_count = 0
            missing_plate_count = 0
            if base["eligible"]:
                peers, cohort = select_pumbility_peers(self._ratings[mode],
                    {"playerId": player_id, "rating": Decimal(sum(row["cents"] for row in ordered[:TOP_COUNT])) / 100},
                    upward_windows=self.peer_windows, minimum_peers=self.minimum_peers)
                peer_ids = {row["playerId"] for row in peers}
                top = {row["chartId"]: row["cents"] for row in ordered[:TOP_COUNT]}
                cutoff = ordered[TOP_COUNT - 1]["cents"]
                for cid, chart in charts.items():
                    if chart["type"] != kind or chart["level"] < MINIMUM_LEVEL:
                        continue
                    raw_observed = [observation for pid, observation in self._scores.get(cid, {}).items() if pid in peer_ids]
                    observed = [(score, plate) for score, plate in raw_observed if plate is not None]
                    chart_missing_plates = len(raw_observed) - len(observed)
                    paired_observation_count += len(observed)
                    missing_plate_count += chart_missing_plates
                    if not observed:
                        unobserved += 1
                        continue
                    target_count += 1
                    target = _paired_target(observed)
                    expected = phoenix2_pumbility(kind, chart["level"], target["targetGrade"], target["projectedPlate"])
                    gain, replacement = marginal_gain_cents(pumbility_cents(expected), cid, top, cutoff)
                    if gain <= 0:
                        continue
                    old = mode_facts.get(cid)
                    rows.append({**_chart_public(chart, annotations.get(cid)), **target,
                        "sourceMode": mode, "skillMethod": METHOD, "existingPumbility": old["cents"] / 100 if old else None,
                        "currentPumbility": old["cents"] / 100 if old else None,
                        "currentScore": old["score"] if old else None, "currentPlate": old["plate"] if old else None,
                        "expectedPumbility": expected, "projectedGain": gain / 100,
                        "replacedPumbility": replacement / 100,
                        "projectedScore": target["targetScore"], "projectedGrade": target["targetGrade"],
                        "projectedPlateProbability": None, "plateProjectionSource": "piuscores-peer-median",
                        "scoreProjectionSource": "piuscores-peer-median", "scoreProjectionSupportCount": len(observed),
                        "scoreProjectionConfidence": "limited" if len(observed) < 10 or cohort["limitedData"] else "high",
                        "peerScoreCount": len(observed), "peerPairedScoreCount": len(observed),
                        "scoreAtOrAboveTargetCount": sum(score >= target["targetScore"] for score, _ in observed),
                        "peerRawScoreCount": len(raw_observed), "peerMissingPlateCount": chart_missing_plates,
                        "cohortPlayerCount": len(peers),
                        "limitedData": len(observed) < 10 or cohort["limitedData"], "played": old is not None,
                        "distanceFromRating": None, "farmEdge": None})
            rows = rank_recommendations(rows)
            source_rows[mode] = rows
            modes[mode] = {**base, "cohort": cohort, "unobservedChartCount": unobserved,
                           "pairedObservationCount": paired_observation_count,
                           "missingPlateObservationCount": missing_plate_count,
                           "candidateCount": len(rows), "filterCandidateCount": len(rows),
                           "projectionAvailable": target_count > 0, "topRecommendations": rows[:TOP_COUNT],
                           **({"filterCandidates": rows} if include_candidates else {})}
        ordered = _ordered(facts)
        overall = self._base("overall", ordered, charts, annotations, player_synced_at_utc)
        ready = {mode: modes[mode]["eligible"] for mode in MODES}
        overall_rows = []
        if len(ordered) >= TOP_COUNT:
            top = {row["chartId"]: row["cents"] for row in ordered[:TOP_COUNT]}
            cutoff = ordered[TOP_COUNT - 1]["cents"]
            for rows in source_rows.values():
                for row in rows:
                    gain, replacement = marginal_gain_cents(pumbility_cents(row["expectedPumbility"]), row["chartId"], top, cutoff)
                    if gain > 0:
                        overall_rows.append({**row, "modeProjectedGain": row["projectedGain"],
                                             "projectedGain": gain / 100, "replacedPumbility": replacement / 100})
        overall_rows = rank_recommendations(overall_rows)
        overall_eligible = len(ordered) >= TOP_COUNT and any(ready.values())
        overall.update(eligible=overall_eligible, status="ready" if overall_eligible else "insufficient-data",
                       sourceModeEligibility=ready, sourceRecommendationCounts={mode: len(rows) for mode, rows in source_rows.items()},
                       top50ModeCounts={mode: sum(charts[row["chartId"]]["type"] == kind for row in ordered[:TOP_COUNT]) for mode, kind in MODES.items()},
                       candidateCount=len(overall_rows), filterCandidateCount=len(overall_rows),
                       projectionAvailable=overall_eligible and any(modes[mode]["projectionAvailable"] for mode in MODES),
                       topRecommendations=overall_rows[:TOP_COUNT])
        if not overall_eligible:
            overall["reason"] = "At least 50 usable Phoenix2 results in Singles or Doubles are required."
        if include_candidates:
            overall["filterCandidates"] = overall_rows
        modes["overall"] = overall
        return {"modes": modes}

    def _base(self, mode: str, ordered: Sequence[Mapping[str, Any]], charts: Mapping[str, Any], annotations: Mapping[str, Any], synced_at: str | None) -> dict:
        top = ordered[:TOP_COUNT]
        complete = len(top) == TOP_COUNT
        total = sum(row["cents"] for row in top) / 100
        public_top = [_top_public(row, charts, annotations) for row in top]
        cutoff = top[-1]["cents"] / 100 if complete else None
        baseline = {"mode": mode, "source": "piuscores", "estimated": False, "scoreCount": len(ordered),
                    "status": "ready" if complete else "insufficient-data", "top50Pumbility": total,
                    "cutoffPumbility": cutoff, "snapshotAt": self.source_timestamp,
                    "syncedAtUtc": synced_at or self.source_timestamp, "top50": public_top}
        result = {"skillMethod": METHOD, "skillSource": "piuscores-submitted", "rankingMethod": RANKING_METHOD,
                  "modePumbility": total if complete else None,
                  "eligible": complete, "status": baseline["status"], "validScoreCount": len(ordered),
                  "requiredScoreCount": TOP_COUNT, "phoenix2ScoreCount": len(ordered), "phoenix2ScoreThreshold": TOP_COUNT,
                  "scoreProjectionModel": METHOD, "currentTop50Pumbility": total,
                  "currentTop50CutoffPumbility": cutoff, "currentTop50Count": len(top),
                  "baseline": baseline, "topScores": public_top, "peerGenerationAtUtc": self.source_timestamp,
                  "playerSyncedAtUtc": synced_at or self.source_timestamp}
        if not complete:
            result["reason"] = f"At least 50 usable {mode} Phoenix2 chart results are required; {len(ordered)} are available."
        return result
