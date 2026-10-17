"""Equal-player same-level score comparisons; missing appearances are not failures."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Mapping
import numpy as np

MINIMUM_OTHER_CHARTS = 3
BOOTSTRAP_SAMPLES = 1000


def fit_player_comparisons(boards: Mapping[str, Mapping[str, float]], *, seed_key: str = "",
                           bootstrap_samples: int = BOOTSTRAP_SAMPLES) -> dict:
    histories = defaultdict(dict)
    for cid, entries in sorted(boards.items()):
        for player, score in sorted(entries.items()):
            histories[player][cid] = score
    minimum = min(MINIMUM_OTHER_CHARTS, max(1, len(boards) - 1))
    panel = {p: h for p, h in histories.items() if len(h) > minimum}
    # A folder without any normal comparisons may use a provisional panel.
    if not panel and minimum > 1:
        minimum = 1
        panel = {p: h for p, h in histories.items() if len(h) > minimum}
    ids = sorted(boards)
    positions = {cid: i for i, cid in enumerate(ids)}
    laplacian = np.zeros((len(ids), len(ids)))
    rhs = np.zeros(len(ids))
    for history in panel.values():
        indices = np.array([positions[c] for c in history])
        scores = np.array(list(history.values()), dtype=float)
        n = len(indices)
        # Each player's total pair weight per target is one, regardless of
        # history size. Shared players connect differently selected chart sets.
        laplacian[np.ix_(indices, indices)] += (n * np.eye(n) - np.ones((n, n))) / (n - 1)
        rhs[indices] += (scores.sum() - n * scores) / (n - 1)
    components = []
    unseen = set(range(len(ids)))
    while unseen:
        pending = [min(unseen)]
        component = set()
        while pending:
            i = pending.pop()
            if i in component:
                continue
            component.add(i)
            pending.extend(j for j in np.flatnonzero(laplacian[i] < 0) if j not in component)
        unseen -= component
        components.append(sorted(component))
    effects = np.zeros(len(ids))
    for component in components:
        if len(component) < 2:
            continue
        matrix = laplacian[np.ix_(component, component)]
        solution = np.linalg.solve(matrix + np.ones_like(matrix) / len(component), rhs[component])
        effects[component] = solution - np.median(solution)
    personal = {cid: {} for cid in ids}
    for player, history in panel.items():
        total = sum(score + effects[positions[cid]] for cid, score in history.items())
        for cid, score in history.items():
            baseline = (total - score - effects[positions[cid]]) / (len(history) - 1)
            personal[cid][player] = {"gap": float(baseline - score), "score": score,
                                     "baseline": float(baseline), "otherCharts": len(history) - 1}
    player_ids = sorted(panel)
    player_positions = {p: i for i, p in enumerate(player_ids)}
    seed = int.from_bytes(hashlib.sha256(seed_key.encode()).digest()[:8], "little")
    rng = np.random.default_rng(seed)
    weights = (rng.multinomial(len(panel), np.full(len(panel), 1 / len(panel)), size=bootstrap_samples)
               if panel else np.empty((bootstrap_samples, 0)))
    results = {}
    for component in components:
        for index in component:
            cid = ids[index]
            samples = personal[cid]
            gaps = np.array([v["gap"] for v in samples.values()])
            low = high = None
            if len(gaps) >= 5 and bootstrap_samples:
                w = weights[:, [player_positions[p] for p in samples]]
                totals = w.sum(axis=1)
                means = (w[totals > 0] @ gaps) / totals[totals > 0]
                low, high = map(float, np.quantile(means, [.025, .975]))
            results[cid] = {
                "scoringPlayerCount": len(samples), "scoringMinimumOtherCharts": minimum,
                "scoringComparisonCharts": len(component), "scoringComponentCount": len(components),
                "scoringProvisional": minimum < 3 or len(components) > 1 or len(samples) < 10,
                "scoringMeanGap": float(np.mean(gaps)) if len(gaps) else None,
                "scoringGapStdDev": float(np.std(gaps)) if len(gaps) else None,
                "scoringGapCi95Low": low, "scoringGapCi95High": high,
                "scoringUnratedReason": None if len(gaps) else "No eligible player has another chart to compare in this official level.",
            }
    return {"charts": results, "personal": personal, "minimumOtherCharts": minimum,
            "eligiblePlayers": len(panel), "componentCount": len(components)}


def fit_cross_level_player_comparisons(boards: Mapping[str, Mapping[str, float]], *, seed_key: str = "",
                                      bootstrap_samples: int = BOOTSTRAP_SAMPLES) -> dict:
    """Strict four-chart global fit; old same-folder behavior remains unchanged."""
    histories = defaultdict(dict)
    ids = sorted(boards)
    positions = {cid: index for index, cid in enumerate(ids)}
    for cid in ids:
        for player, score in sorted(boards[cid].items()):
            if not np.isfinite(score) or not 0 <= score <= 1_000_000:
                raise ValueError("Official comparisons require finite raw scores in [0, 1000000].")
            histories[player][cid] = float(score)
    panel = {player: history for player, history in sorted(histories.items()) if len(history) >= 4}
    matrix = np.zeros((len(ids), len(ids)))
    rhs = np.zeros(len(ids))
    for history in panel.values():
        indices = np.array([positions[cid] for cid in history])
        scores = np.array(list(history.values()))
        n = len(indices)
        matrix[np.ix_(indices, indices)] += (n * np.eye(n) - np.ones((n, n))) / (n - 1)
        rhs[indices] += (scores.sum() - n * scores) / (n - 1)
    components, unseen = [], set(range(len(ids)))
    while unseen:
        pending, component = [min(unseen)], set()
        while pending:
            index = pending.pop()
            if index not in component:
                component.add(index)
                pending.extend(int(other) for other in np.flatnonzero(matrix[index] < 0) if other not in component)
        unseen -= component
        components.append(sorted(component))
    components.sort(key=lambda component: (-len(component), tuple(ids[index] for index in component)))
    effects, component_of = np.zeros(len(ids)), {}
    for component_index, component in enumerate(components):
        for index in component:
            component_of[ids[index]] = component_index
        if len(component) >= 2:
            block = matrix[np.ix_(component, component)]
            solution = np.linalg.solve(block + np.ones_like(block) / len(component), rhs[component])
            effects[component] = solution - np.median(solution)
    personal = {cid: {} for cid in ids}
    for player, history in panel.items():
        adjusted_total = sum(score + effects[positions[cid]] for cid, score in history.items())
        for cid, score in history.items():
            baseline = (adjusted_total - score - effects[positions[cid]]) / (len(history) - 1)
            personal[cid][player] = {"gap": float(baseline - score), "otherCharts": len(history) - 1}
    players = sorted(panel)
    player_positions = {player: index for index, player in enumerate(players)}
    seed = int.from_bytes(hashlib.sha256(seed_key.encode()).digest()[:8], "little")
    rng = np.random.default_rng(seed)
    weights = (rng.multinomial(len(players), np.full(len(players), 1 / len(players)), size=bootstrap_samples)
               if players and bootstrap_samples else np.empty((0, len(players))))
    results = {}
    for cid in ids:
        samples = personal[cid]
        gaps = np.array([sample["gap"] for sample in samples.values()])
        low = high = None
        if len(gaps) >= 5 and len(weights):
            local_weights = weights[:, [player_positions[player] for player in samples]]
            totals = local_weights.sum(axis=1)
            means = (local_weights[totals > 0] @ gaps) / totals[totals > 0]
            low, high = map(float, np.quantile(means, [.025, .975]))
        component = component_of[cid]
        reason = ("No eligible player has four distinct current level-20+ charts in this mode." if not len(gaps) else
                  "Chart is outside the main shared-player component." if component != 0 else
                  "At least five contributing players are required." if len(gaps) < 5 else None)
        results[cid] = {
            "scoringPlayerCount": len(gaps), "scoringMinimumOtherCharts": 3,
            "scoringComparisonCharts": len(components[component]), "scoringComponentCount": len(components),
            "scoringComponent": component, "scoringMainComponent": component == 0 and len(components[0]) >= 2,
            "scoringProvisional": len(gaps) < 10, "scoringChartCost": float(effects[positions[cid]]) if len(gaps) else None,
            "scoringMeanGap": float(np.mean(gaps)) if len(gaps) else None,
            "scoringGapStdDev": float(np.std(gaps)) if len(gaps) else None,
            "scoringGapCi95Low": low, "scoringGapCi95High": high,
            "scoringChartCostCi95Low": low, "scoringChartCostCi95High": high,
            "scoringUnratedReason": reason,
        }
    return {"charts": results, "personal": personal, "minimumOtherCharts": 3,
            "eligiblePlayers": len(panel), "componentCount": len(components),
            "componentSizes": [len(component) for component in components]}
