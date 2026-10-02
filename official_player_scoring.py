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
