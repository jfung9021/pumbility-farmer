"""Scoring tiers matching weighted profiles of unweighted successful scores."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from phoenix1_score_overrides import build_phoenix1_score_normalizations, convert_phoenix1_score
from piu_misgrade_analyzer import MIN_TARGET_LEVEL, _apply_chart_ranks_and_groups
from piu_recommendations import _clearers_by_chart, build_combined_tier_payload
from player_skill_ratings import clearing_ratings_by_player_mode
from pumbility_contract import COMBINED_TIER_SCHEMA_VERSION
from tier_difficulty import build_tier_metrics, tier_metric_method

LOCAL_PERCENTILE_SCHEMA_VERSION = 25
SCORE_PERCENTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
PROFILE_WEIGHTS = (1, 1, 1, 1, 2)
SCORE_UNIT = 10_000.0
BOOTSTRAP_SAMPLES = 1000
MINIMUM_CI_PLAYERS = 20
MINIMUM_REFERENCE_PLAYERS = 20
MINIMUM_REFERENCE_CHARTS = 5
FULL_REFERENCE_WEIGHT_CHARTS = 20
REFERENCE_SMOOTHING = 4.0
MAX_TWO_GRADE_MOVES = 3
SCALE_STEP = .01
MAXIMUM_SCALE = 1.0
PREFERRED_CENTRAL_WIDTH = 1.0
SPREAD_QUANTILES = (.1, .9)
MINIMUM_SPREAD_CHARTS = 10
MINIMUM_SPREAD_MEDIAN_PLAYERS = 10
SPREAD_RELIABILITY_CHARTS = 30
SPREAD_RELIABILITY_PLAYERS = 20
NEIGHBOR_SMOOTHING = .05
RARITY_THRESHOLD = 10
RARITY_PENALTY = .05


def collect_percentile_scores(
    phoenix1: Mapping[str, Any], phoenix2: Mapping[str, Any],
) -> dict[tuple[str, str], tuple[str, float]]:
    """Private player/chart records: best raw score within source, P2 precedence."""
    catalog = {str(row['id']): row for row in phoenix2.get('charts', [])}
    normalizations = build_phoenix1_score_normalizations(
        phoenix1.get('charts', []), phoenix2.get('charts', []),
    )
    combined = {}
    for name, snapshot in (('phoenix1', phoenix1), ('phoenix2', phoenix2)):
        source_types = {str(row['id']): row.get('type') for row in snapshot.get('charts', [])}
        best = {}
        for row in snapshot.get('scores', []):
            player, cid = str(row.get('playerId') or '').strip(), str(row.get('chartId') or '').strip()
            chart = catalog.get(cid)
            if (not player or not chart or bool(row.get('isBroken', False))
                    or chart.get('type') not in ('Single', 'Double')
                    or source_types.get(cid) != chart['type']):
                continue
            raw = row.get('score')
            if isinstance(raw, bool):
                continue
            try:
                score = float(raw)
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(score) or not 0 <= score <= 1_000_000:
                continue
            if name == 'phoenix1':
                score = convert_phoenix1_score(cid, score, normalizations)
            key = (player, cid)
            best[key] = max(best.get(key, -math.inf), score)
        combined.update({key: (name, value) for key, value in best.items()})
    return combined


def _increasing(values: np.ndarray) -> np.ndarray:
    """Equal-weight pool-adjacent-violators projection onto increasing sequences."""
    blocks = []
    for value in values:
        blocks.append([float(value), 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            total, count = blocks.pop()
            blocks[-1][0] += total
            blocks[-1][1] += count
    return np.concatenate([np.full(count, total / count) for total, count in blocks])


def fit_profile_curve(folders: Mapping[int, Sequence[Sequence[float]]]) -> dict[str, Any] | None:
    """Smooth supported folder profiles, then enforce score and level ordering.

    Official midpoints provide soft coordinates, not exact median constraints.
    The penalized fit minimizes sum(w * (fit - reference)^2) + 4 * sum(D2(fit)^2).
    All score percentiles use the same reference support and smoothing strength.
    Percentile weights apply when matching profiles to the resulting curve.
    """
    supported = {level: values for level, values in folders.items() if len(values) >= MINIMUM_REFERENCE_CHARTS}
    if len(supported) < 2:
        return None
    levels = np.arange(min(supported), max(supported) + 1)
    weights = np.array([min(len(supported.get(int(level), [])) / FULL_REFERENCE_WEIGHT_CHARTS, 1)
                        for level in levels])
    raw = np.array([np.median(supported[int(level)], axis=0) if int(level) in supported else [0.] * len(SCORE_PERCENTILES)
                    for level in levels])
    losses = (1_000_000 - raw) / SCORE_UNIT
    second_difference = np.diff(np.eye(len(levels)), n=2, axis=0)
    system = np.diag(weights) + REFERENCE_SMOOTHING * second_difference.T @ second_difference
    fitted = np.linalg.solve(system, weights[:, None] * losses)
    fitted = np.column_stack([_increasing(fitted[:, column]) for column in range(len(SCORE_PERCENTILES))])
    # Sorting within each profile also preserves its increasing order across levels.
    fitted = np.clip(np.sort(fitted, axis=1)[:, ::-1], 0, 1_000_000 / SCORE_UNIT)
    if np.max(np.abs(fitted[-1] - fitted[0])) < 1e-9:
        return None  # Constant profiles cannot identify a difficulty scale.
    return {
        'difficulties': (levels + .5).tolist(),
        'profiles': (1_000_000 - fitted * SCORE_UNIT).tolist(),
        'references': [
            {'level': int(level), 'charts': len(supported.get(int(level), [])),
             'weight': float(weights[index]),
             'rawProfile': raw[index].tolist() if weights[index] else None}
            for index, level in enumerate(levels)
        ],
    }


def match_profiles(profiles: Sequence[Sequence[float]], curve: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Closest weighted profile on a piecewise linear curve and endpoint rays.

    Neither the chart's official level nor its player skill enters this projection.
    The 90th-percentile squared distance has twice each other percentile's weight.
    Equal-distance solutions use the midpoint of their difficulty range.
    """
    points = (1_000_000 - np.asarray(profiles, dtype=float)) / SCORE_UNIT
    knots = (1_000_000 - np.asarray(curve['profiles'], dtype=float)) / SCORE_UNIT
    weights = np.asarray(PROFILE_WEIGHTS, dtype=float)
    levels = np.asarray(curve['difficulties'], dtype=float)
    directions = np.diff(knots, axis=0)
    lengths = np.sum(weights * directions ** 2, axis=1)
    offsets = points[:, None, :] - knots[None, :-1, :]
    fractions = np.divide(np.sum(weights * offsets * directions, axis=2), lengths,
                          out=np.full((len(points), len(lengths)), .5), where=lengths > 1e-18)
    lower, upper = np.zeros(len(lengths)), np.ones(len(lengths))
    if lengths[0] > 1e-18:
        lower[0] = -np.inf
    if lengths[-1] > 1e-18:
        upper[-1] = np.inf
    fractions = np.clip(fractions, lower, upper)
    residuals = offsets - fractions[:, :, None] * directions
    errors = np.average(residuals ** 2, axis=2, weights=weights)
    best = errors.min(axis=1)
    candidates = levels[:-1] + fractions * np.diff(levels)
    tied = np.isclose(errors, best[:, None], rtol=1e-12, atol=1e-12)
    smallest = np.where(tied, candidates, np.inf).min(axis=1)
    largest = np.where(tied, candidates, -np.inf).max(axis=1)
    return (smallest + largest) / 2, np.sqrt(best) * SCORE_UNIT


def profile_difficulty_interval(values: Sequence[float], chart_id: str,
                                curve: Mapping[str, Any] | None) -> tuple[float | None, float | None]:
    """Bootstrap entire profiles jointly, projecting each onto the frozen curve."""
    if len(values) < MINIMUM_CI_PLAYERS or curve is None:
        return None, None
    ordered = np.sort(np.asarray(values, dtype=float))
    seed = int.from_bytes(hashlib.sha256(chart_id.encode()).digest()[:8], 'little')
    rng = np.random.default_rng(seed)
    estimates = []
    for _ in range(BOOTSTRAP_SAMPLES // 100):
        resampled = rng.choice(ordered, size=(100, len(ordered)), replace=True)
        profiles = np.quantile(resampled, SCORE_PERCENTILES, axis=1, method='linear').T
        estimates.extend(match_profiles(profiles, curve)[0])
    low, high = np.quantile(estimates, [.025, .975], method='linear')
    return float(low), float(high)


def two_grade_count(differences: Sequence[float], scale: float = 1.0,
                    levels: Sequence[int] | None = None) -> int:
    """Count both directions using the same six-decimal export as the tier payload."""
    offsets = np.asarray(differences, dtype=float)
    official = np.zeros(len(offsets)) if levels is None else np.asarray(levels, dtype=float)
    estimates = official + .5 + scale * offsets
    exported = np.asarray(json.loads(pd.Series(estimates).to_json(orient='values', double_precision=6)))
    return int(((exported >= official + 2) | (exported < official - 1)).sum())


def fit_profile_scale(differences: Sequence[float], levels: Sequence[int]) -> float:
    """Largest hundredth in [0, 1] with at most three two-grade proposals."""
    lower, upper = 0, 100
    while lower < upper:
        candidate = (lower + upper + 1) // 2
        if two_grade_count(differences, candidate / 100, levels) <= MAX_TWO_GRADE_MOVES:
            lower = candidate
        else:
            upper = candidate - 1
    return lower / 100


def fit_profile_scales(
    folders: Mapping[tuple[str, int], Mapping[str, Sequence[float]]], *,
    baseline_scale: float | None = None, candidate_scales: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Fit folder spreads jointly, charging a soft cost for global tail overflow.

    Each folder contains centered ``offsets`` and matching ``contributors`` counts.
    Per-mode dynamic programs retain (last scale, min(actual outliers, 10)); overflow
    is paid immediately. Combining the two mode buckets shares the ten free moves
    exactly, without discarding paths with more than ten actual moves. The old
    hard-capped fitter is used only for the baseline target and sparse fallback.
    """
    modes = ('Single', 'Double')
    if any(mode not in modes for mode, _ in folders):
        raise ValueError('Folder scale fitting supports Singles and Doubles only')
    scales = sorted(set(candidate_scales if candidate_scales is not None
                        else [i / 100 for i in range(101)]))
    if not scales or any(not math.isfinite(s) or not 0 <= s <= MAXIMUM_SCALE for s in scales):
        raise ValueError('Candidate scales must be finite values in [0, 1]')
    all_offsets = [value for folder in folders.values() for value in folder['offsets']]
    all_levels = [level for (_, level), folder in folders.items() for _ in folder['offsets']]
    if baseline_scale is None:
        baseline_scale = fit_profile_scale(all_offsets, all_levels)
    if not math.isfinite(baseline_scale) or not 0 <= baseline_scale <= MAXIMUM_SCALE:
        raise ValueError('Baseline scale must be finite and in [0, 1]')
    diagnostics, tables = {}, {}
    for key, folder in sorted(folders.items()):
        offsets = np.asarray(folder['offsets'], dtype=float)
        contributors = np.asarray(folder['contributors'], dtype=float)
        if (not len(offsets) or len(offsets) != len(contributors)
                or not np.all(np.isfinite(offsets)) or not np.all(np.isfinite(contributors))):
            raise ValueError('Each folder needs finite offsets and matching contributor counts')
        raw_width = float(np.diff(np.quantile(offsets, SPREAD_QUANTILES, method='linear'))[0])
        median_players = float(np.median(contributors))
        supported = (len(offsets) >= MINIMUM_SPREAD_CHARTS
                     and median_players >= MINIMUM_SPREAD_MEDIAN_PLAYERS and raw_width > 0)
        weight = (min(len(offsets) / SPREAD_RELIABILITY_CHARTS, 1)
                  * min(median_players / SPREAD_RELIABILITY_PLAYERS, 1)) if supported else 0.
        preferred_width = max(PREFERRED_CENTRAL_WIDTH, baseline_scale * raw_width)
        diagnostics[key] = {
            'ratedCharts': len(offsets), 'medianContributors': median_players,
            'rawWidth': raw_width, 'preferredWidth': preferred_width if raw_width > 0 else None,
            'preferredScale': preferred_width / raw_width if raw_width > 0 else None,
            'spreadSupported': supported, 'reliabilityWeight': weight,
            'fallback': None if supported else 'flat-folder' if raw_width == 0 else 'sparse-folder',
        }
        tables[key] = [
            {'count': two_grade_count(offsets, scale, [key[1]] * len(offsets)),
             'widthCost': weight * ((scale * raw_width - preferred_width) / preferred_width) ** 2}
            for scale in scales
        ]
    for mode in modes:
        mode_keys = sorted(key for key in folders if key[0] == mode)
        if mode_keys and not any(diagnostics[key]['spreadSupported'] for key in mode_keys):
            for key in mode_keys:
                diagnostics[key]['fallback'] = 'mode-without-supported-spread'
                for scale, choice in zip(scales, tables[key]):
                    choice['widthCost'] = ((scale - baseline_scale) / (baseline_scale + SCALE_STEP)) ** 2

    # A state is (objective including mode overflow, actual count, scale-index path).
    # Tuple comparison supplies both deterministic tie-breaks after the objective.
    mode_solutions = {}
    for mode in modes:
        keys = sorted(key for key in folders if key[0] == mode)
        if not keys:
            mode_solutions[mode] = {0: (0., 0, ())}
            continue
        states = {}
        for index, key in enumerate(keys):
            next_states = {}
            gap = key[1] - keys[index - 1][1] if index else None
            for scale_index, scale in enumerate(scales):
                choice = tables[key][scale_index]
                if not index:
                    count = choice['count']
                    state = (choice['widthCost'] + RARITY_PENALTY * max(0, count - RARITY_THRESHOLD),
                             count, (scale_index,))
                    next_states[(scale_index, min(count, RARITY_THRESHOLD))] = state
                    continue
                smooth_costs = [NEIGHBOR_SMOOTHING *
                                (math.log(scale + SCALE_STEP) - math.log(previous + SCALE_STEP)) ** 2 / gap
                                for previous in scales]
                for (previous_index, previous_bucket), (cost, actual_count, path) in states.items():
                    bucket_total = previous_bucket + choice['count']
                    state_key = (scale_index, min(bucket_total, RARITY_THRESHOLD))
                    state = (cost + choice['widthCost'] + smooth_costs[previous_index]
                             + RARITY_PENALTY * max(0, bucket_total - RARITY_THRESHOLD),
                             actual_count + choice['count'], path + (scale_index,))
                    if state_key not in next_states or state < next_states[state_key]:
                        next_states[state_key] = state
            states = next_states
        by_bucket = {}
        for (_, bucket), state in states.items():
            if bucket not in by_bucket or state < by_bucket[bucket]:
                by_bucket[bucket] = state
        mode_solutions[mode] = by_bucket
    best = None
    for single_bucket, single in mode_solutions['Single'].items():
        for double_bucket, double in mode_solutions['Double'].items():
            joint = (single[0] + double[0]
                     + RARITY_PENALTY * max(0, single_bucket + double_bucket - RARITY_THRESHOLD),
                     single[1] + double[1], single[2] + double[2])
            if best is None or joint < best:
                best = joint
    ordered_keys = [key for mode in modes for key in sorted(folders) if key[0] == mode]
    selected = dict(zip(ordered_keys, best[2]))
    result = {'folderScales': {mode: {} for mode in modes},
              'folderDiagnostics': {mode: {} for mode in modes},
              'baselineScale': baseline_scale, 'actualTwoGradeCount': best[1]}
    width_cost = neighbor_cost = maximum_delta = 0.
    for key in ordered_keys:
        mode, level = key
        scale = scales[selected[key]]
        info, choice = diagnostics[key], tables[key][selected[key]]
        maximum = max(abs(float(value) * scale) for value in folders[key]['offsets'])
        maximum_delta = max(maximum_delta, maximum)
        width_cost += choice['widthCost']
        info.update({'selectedScale': scale, 'achievedWidth': scale * info['rawWidth'],
                     'twoGradeCount': choice['count'], 'widthCost': choice['widthCost'],
                     'maximumAbsoluteMidpointDelta': maximum})
        result['folderScales'][mode][str(level)] = scale
        result['folderDiagnostics'][mode][str(level)] = info
    for mode in modes:
        keys = [key for key in ordered_keys if key[0] == mode]
        for previous, key in zip(keys, keys[1:]):
            neighbor_cost += NEIGHBOR_SMOOTHING * (
                math.log(scales[selected[key]] + SCALE_STEP)
                - math.log(scales[selected[previous]] + SCALE_STEP)) ** 2 / (key[1] - previous[1])
    rarity_cost = RARITY_PENALTY * max(0, best[1] - RARITY_THRESHOLD)
    result['maximumAbsoluteMidpointDelta'] = maximum_delta
    result['objective'] = {'widthCost': width_cost, 'neighborCost': neighbor_cost,
                           'rarityCost': rarity_cost, 'total': width_cost + neighbor_cost + rarity_cost}
    result['fallbackWidthCost'] = '((scale - baselineScale) / (baselineScale + 0.01))^2 for modes without supported spreads'
    return result


def build_percentile_tier_payload(
    base_records: Sequence[Mapping[str, Any]], metadata: Mapping[str, Any],
    phoenix1: Mapping[str, Any], phoenix2: Mapping[str, Any],
    *, generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Replace only S/D tier scoring; preserve the existing recommendation model."""
    records = copy.deepcopy([row for row in base_records
                             if row['type'] == 'CoOp' or row['level'] >= MIN_TARGET_LEVEL])
    scoring_rows = [row for row in records if row['type'] in ('Single', 'Double')]
    observations = collect_percentile_scores(phoenix1, phoenix2)
    samples, sources, players = defaultdict(list), defaultdict(Counter), defaultdict(set)
    by_id = {row['chartId']: row for row in scoring_rows}
    for (player, cid), (source, score) in sorted(observations.items()):
        if cid in by_id:
            samples[cid].append(score)
            sources[cid][source] += 1
            players[by_id[cid]['type']].add(player)
    folders, reference_folders = defaultdict(list), defaultdict(dict)
    for row in scoring_rows:
        values = samples[row['chartId']]
        profile = np.quantile(values, SCORE_PERCENTILES, method='linear').tolist() if values else None
        row['scoringScoreProfile'] = profile
        if profile is not None:
            folders[(row['type'], row['level'])].append(profile)
        if len(values) >= MINIMUM_REFERENCE_PLAYERS:
            reference_folders[row['type']].setdefault(row['level'], []).append(profile)
    curves = {mode: fit_profile_curve(reference_folders[mode]) for mode in ('Single', 'Double')}
    for row in scoring_rows:
        cid, level = row['chartId'], row['level']
        values = samples[cid]
        profile, curve = row['scoringScoreProfile'], curves[row['type']]
        midpoint = level + .5
        estimate = error = None
        if profile is not None and curve is not None:
            matched, errors = match_profiles([profile], curve)
            estimate, error = float(matched[0]), float(errors[0])
        low, high = profile_difficulty_interval(values, cid, curve)
        for field in ('scoringPercentileScore', 'scoringFolderReferenceScore',
                      'scoringPercentileCi95Low', 'scoringPercentileCi95High'):
            row.pop(field, None)
        row.update({
            'scoringProfileMatchDifficulty': estimate,
            'scoringProfileMatchCi95Low': low, 'scoringProfileMatchCi95High': high,
            'scoringProfileRmse': error,
            'scoringProfileExtrapolated': bool(estimate is not None and
                (estimate < curve['difficulties'][0] or estimate > curve['difficulties'][-1])),
            'estimatedDifficulty': estimate, 'averageDifficulty': midpoint,
            'difficultyDelta': estimate - midpoint if estimate is not None else None,
            'difficultyCi95Low': low, 'difficultyCi95High': high,
            'difficultyDeltaCi95Low': low - midpoint if low is not None else None,
            'difficultyDeltaCi95High': high - midpoint if high is not None else None,
            'nContributors': len(values), 'nPlayersScored': len(values),
            'phoenix1Contributors': sources[cid]['phoenix1'], 'phoenix2Contributors': sources[cid]['phoenix2'],
            'evidenceStatus': ('Unrated' if estimate is None else 'Published' if len(values) >= 10
                               else 'Provisional' if len(values) >= 5 else 'Insufficient'),
            'folderRangeCompression': 1.0, 'folderMeasuredCharts': len(folders[(row['type'], level)]),
            'pumbilityPerLevel': None,
            'whatIfEstimates': [],  # No hypothetical reassessment in this experiment.
        })
    matched_folders = defaultdict(list)
    rated = [row for row in scoring_rows if row['scoringProfileMatchDifficulty'] is not None]
    for row in rated:
        matched_folders[(row['type'], row['level'])].append(row['scoringProfileMatchDifficulty'])
    centers = {key: float(np.median(values)) for key, values in matched_folders.items()}
    scale_folders = defaultdict(lambda: {'offsets': [], 'contributors': []})
    for row in rated:
        key = (row['type'], row['level'])
        scale_folders[key]['offsets'].append(row['scoringProfileMatchDifficulty'] - centers[key])
        scale_folders[key]['contributors'].append(row['nContributors'])
    scale_fit = fit_profile_scales(scale_folders)
    for row in scoring_rows:
        center = centers.get((row['type'], row['level']))
        row['scoringFolderReferenceDifficulty'] = center
        raw = row['scoringProfileMatchDifficulty']
        row['scoringDifficultyScale'] = (scale_fit['folderScales'][row['type']][str(row['level'])]
                                         if raw is not None else None)
        if raw is None:
            continue
        scale = row['scoringDifficultyScale']
        midpoint = row['level'] + .5
        row['difficultyDelta'] = scale * (raw - center)
        row['estimatedDifficulty'] = midpoint + row['difficultyDelta']
        for suffix in ('Low', 'High'):
            raw_endpoint = row[f'scoringProfileMatchCi95{suffix}']
            endpoint = midpoint + scale * (raw_endpoint - center) if raw_endpoint is not None else None
            row[f'difficultyCi95{suffix}'] = endpoint
            row[f'difficultyDeltaCi95{suffix}'] = endpoint - midpoint if endpoint is not None else None
    clearers = _clearers_by_chart(phoenix1, phoenix2)
    skills = clearing_ratings_by_player_mode(clearers, phoenix2['charts'])
    metrics, clearing_references = build_tier_metrics(scoring_rows, clearers, skills)
    for row, metric in zip(scoring_rows, metrics):
        row['tierMetrics'] = metric
    ranked_records = []
    for chart_type in ('Single', 'Double'):
        frame = pd.DataFrame([row for row in scoring_rows if row['type'] == chart_type])
        if not frame.empty:
            ranked_records.extend(json.loads(_apply_chart_ranks_and_groups(frame).to_json(orient='records', double_precision=6)))
    ranked_records.extend(row for row in records if row['type'] == 'CoOp')
    updated_metadata = copy.deepcopy(metadata)
    updated_metadata['tierMetrics'] = tier_metric_method(clearing_references)
    for chart_type, mode in (('Single', 'singles'), ('Double', 'doubles')):
        relevant = [row for row in scoring_rows if row['type'] == chart_type]
        updated_metadata['modes'][mode] = {
            'eligiblePlayers': len(players[chart_type]),
            **{f'{source}Observations': sum(row[f'{source}Contributors'] for row in relevant)
               for source in ('phoenix1', 'phoenix2')},
        }
    for source in ('phoenix1', 'phoenix2'):
        updated_metadata[f'{source}Observations'] = sum(row[f'{source}Contributors'] for row in scoring_rows)
    updated_metadata['sourceObservations'] = sum(row['nContributors'] for row in scoring_rows)
    payload = build_combined_tier_payload(ranked_records, updated_metadata, generated_at_utc=generated_at_utc)
    payload['schemaVersion'] = LOCAL_PERCENTILE_SCHEMA_VERSION
    payload['summary']['scriptVersion'] += '+local-score-profile-level-scales-v2'
    method = payload['summary']['method']
    method.update({
        'scoring': {'version': 1, 'population': 'combined', 'calibration': 'folder-scaled-score-profile',
                    'percentiles': list(SCORE_PERCENTILES), 'percentileMethod': 'linear interpolation',
                    'profileWeights': list(PROFILE_WEIGHTS), 'scoreUnit': SCORE_UNIT,
                    'referenceSmoothing': REFERENCE_SMOOTHING,
                    'minimumReferencePlayers': MINIMUM_REFERENCE_PLAYERS,
                    'minimumReferenceCharts': MINIMUM_REFERENCE_CHARTS,
                    'fullReferenceWeightCharts': FULL_REFERENCE_WEIGHT_CHARTS,
                    'folderCenter': 'median-profile-match', 'scaleStep': SCALE_STEP, 'maximumScale': MAXIMUM_SCALE,
                    'preferredCentralWidth': PREFERRED_CENTRAL_WIDTH, 'spreadQuantiles': list(SPREAD_QUANTILES),
                    'minimumSpreadCharts': MINIMUM_SPREAD_CHARTS,
                    'minimumSpreadMedianPlayers': MINIMUM_SPREAD_MEDIAN_PLAYERS,
                    'spreadReliabilityCharts': SPREAD_RELIABILITY_CHARTS,
                    'spreadReliabilityPlayers': SPREAD_RELIABILITY_PLAYERS,
                    'neighborSmoothing': NEIGHBOR_SMOOTHING,
                    'rarityThreshold': RARITY_THRESHOLD, 'rarityPenalty': RARITY_PENALTY},
        'localExperiment': 'scoring-profile-level-scales',
        'sourceMinimumScoresPerPlayer': {'phoenix1': 0, 'phoenix2': 0},
        'crossVersionNormalization': 'Phoenix 1 catalog note-count normalization of raw scores; Phoenix 2 raw scores unchanged',
        'observationWeighting': {'sourceWeights': {'phoenix1': 1, 'phoenix2': 1}, 'playerAbility': 'none', 'formula': 'one equal-weight successful score per player/chart'},
        'levelReference': 'raw profile matches centered by the median in each official mode/level folder; final medians anchor at level + 0.5',
        'modeSeparation': 'independent Singles/Doubles profile curves; Co-op unchanged',
        'difficultyDeltaScale': None,
        'folderRangeNormalization': {'method': 'per-mode-level-profile-scale',
                                     'formula': 'selectedScale * centered raw profile match', 'expandsFolders': True},
        'scoreProfileCalibration': {
            'method': 'weighted second-difference smoothing, increasing-loss isotonic projection, ordered profile quantiles',
            'matching': 'minimum weighted mean squared score distance on piecewise linear curve; weights 1/1/1/1/2 for q10/q25/q50/q75/q90; midpoint of tied solutions',
            'tails': 'raw profile matching uses linear endpoint extrapolation, before folder centering and folder-specific spread calibration',
            'finalCalibration': 'official level + 0.5 + scale[mode, level] * (raw profile match - official folder median profile match)',
            'scaleSelection': 'joint dynamic programming over hundredth scales; reliability-weighted width fit, within-mode neighbor smoothing, and a soft penalty beyond ten combined two-grade moves',
            'outlierCap': None,
            'outlierScope': 'rated Singles and Doubles at official level 16+, both directions including limited-data charts',
            **scale_fit,
            'folderCenters': {mode: {str(level): value for (typ, level), value in centers.items() if typ == mode}
                              for mode in ('Single', 'Double')},
            'curves': curves,
            'fallback': 'unrated when fewer than two supported levels or all reference profiles are constant'},
        'confidenceIntervals': {'method': 'joint score-profile bootstrap with final affine calibration, holding the curve, folder center, and selected folder scale fixed; excludes scale-selection uncertainty',
                                'samples': BOOTSTRAP_SAMPLES, 'minimumPlayers': MINIMUM_CI_PLAYERS,
                                'population': 'observed successful-player best scores; excludes attempt and player-selection uncertainty'},
        'whatIfEstimates': {'calculation': 'not generated; no hypothetical folder reassessment'},
    })
    for mode, chart_type in (('singles', 'Single'), ('doubles', 'Double')):
        payload['summary']['modes'][mode].update({
            'pumbilityPerLevel': None,
            'calibration': {'method': 'folder-centered 10th/25th/50th/75th/90th-percentile profile matches with double q90 weight, per-level scales, and a soft rarity penalty',
                            'folderScales': scale_fit['folderScales'][chart_type]},
            'shrinkage': {'method': 'per-level spread scales around exact official-folder midpoints'},
        })
    return payload


def build_production_tier_payload(
    base_records: Sequence[Mapping[str, Any]], metadata: Mapping[str, Any],
    phoenix1: Mapping[str, Any], phoenix2: Mapping[str, Any],
    *, generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Publish the approved profile model without changing recommendation inputs."""
    payload = build_percentile_tier_payload(
        base_records, metadata, phoenix1, phoenix2, generated_at_utc=generated_at_utc,
    )
    payload['schemaVersion'] = COMBINED_TIER_SCHEMA_VERSION
    payload['summary']['scriptVersion'] = payload['summary']['scriptVersion'].replace(
        '+local-score-profile-level-scales-v2', '+score-profile-level-scales-v2',
    )
    payload['summary']['method'].pop('localExperiment', None)
    return payload
