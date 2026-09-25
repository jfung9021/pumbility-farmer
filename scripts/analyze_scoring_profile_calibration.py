#!/usr/bin/env python3
"""Read cached scoring profiles and write private aggregate calibration diagnostics.

This does not fit a model, fetch upstream data, or modify tier/recommendation files.
Representative targets are the easiest, median, and hardest raw matches per folder,
plus Extreme Music School D26. Comparisons use immediate official neighboring
levels in the same mode; up to three reference charts with the greatest overlap
are reported per target/folder (minimum three shared players). Folder comparisons
include all shared players, each contributing once against their own median score.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from scoring_percentile import collect_percentile_scores  # noqa: E402
from scripts.build_local_recommendations import (  # noqa: E402
    COMBINED_OUTPUT_PATH, DATA_ROOT, _read_snapshot,
)

MINIMUM_SHARED_PLAYERS = 10
MAX_REFERENCE_PAIRS = 3
MINIMUM_PAIR_OVERLAP = 3


def distribution(values: Sequence[float | None]) -> dict[str, float | int | None]:
    finite = [float(value) for value in values if value is not None and math.isfinite(value)]
    keys = ('min', 'q10', 'median', 'q90', 'max')
    quantiles = np.quantile(finite, [0, .1, .5, .9, 1]).tolist() if finite else [None] * 5
    return {'count': len(finite), **dict(zip(keys, quantiles))}


def difference_summary(values: Sequence[float]) -> dict[str, Any]:
    return {
        'sharedPlayers': len(values),
        'medianScoreDifference': float(np.median(values)) if values else None,
        'lower': sum(value < 0 for value in values),
        'tied': sum(value == 0 for value in values),
        'higher': sum(value > 0 for value in values),
        'sparseEvidence': len(values) < MINIMUM_SHARED_PLAYERS,
    }


def shared_player_comparison(
    target: Mapping[str, tuple[str, float]],
    references: Sequence[Mapping[str, tuple[str, float]]],
) -> dict[str, Any]:
    """Aggregate once per target player; never expose identities or histories."""
    differences, same_source = [], []
    for player, (source, score) in target.items():
        matches = [reference[player] for reference in references if player in reference]
        if matches:
            differences.append(float(score - np.median([value for _, value in matches])))
        compatible = [value for reference_source, value in matches if reference_source == source]
        if compatible:
            same_source.append(float(score - np.median(compatible)))
    return {'allSources': difference_summary(differences),
            'sameSourceOnly': difference_summary(same_source)}


def _chart_label(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ('chartId', 'songName', 'type', 'level')}


def _two_grade_move(row: Mapping[str, Any], value: float | None) -> bool:
    if value is None:
        return False
    exported = round(float(value), 6)
    return exported >= row['level'] + 2 or exported < row['level'] - 1


def _metric(row: Mapping[str, Any], name: str) -> float | None:
    return row.get('tierMetrics', {}).get(name, {}).get('estimatedDifficulty')


def folder_diagnostics(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    method = payload['summary']['method']
    calibration = method['scoreProfileCalibration']
    folders = defaultdict(list)
    for row in [*payload.get('singles', []), *payload.get('doubles', [])]:
        folders[(row['type'], row['level'])].append(row)
    result = []
    for (mode, level), rows in sorted(folders.items()):
        rated = [row for row in rows if row.get('estimatedDifficulty') is not None]
        contributors = [row['nContributors'] for row in rated]
        raw_combined = [
            (row['scoringProfileMatchDifficulty'] + _metric(row, 'clearing')) / 2
            for row in rows if row.get('scoringProfileMatchDifficulty') is not None
            and _metric(row, 'clearing') is not None
        ]
        raw_values = [row.get('scoringProfileMatchDifficulty') for row in rows]
        scales = sorted({row.get('scoringDifficultyScale', method['scoring'].get('difficultyDeltaScale'))
                         for row in rated} - {None})
        curve = calibration.get('curves', {}).get(mode) or {}
        reference = next((item for item in curve.get('references', []) if item['level'] == level), None)
        moves = [row for row in rated if _two_grade_move(row, row['estimatedDifficulty'])]
        median_contributors = float(np.median(contributors)) if contributors else 0
        result.append({
            'mode': mode, 'level': level, 'charts': len(rows), 'ratedCharts': len(rated),
            'contributors': distribution(contributors),
            'supportedSpreadTarget': len(rated) >= 10 and median_contributors >= 10,
            'referenceSupport': reference,
            'chartsWithAtLeast20Contributors': sum(row['nContributors'] >= 20 for row in rows),
            'appliedScales': scales,
            'calibrationDiagnostics': calibration.get('folderDiagnostics', {}).get(mode, {}).get(str(level)),
            'rawScoring': distribution(raw_values),
            'finalScoring': distribution([row.get('estimatedDifficulty') for row in rows]),
            'rawPumbilityDiagnostic': distribution(raw_combined),
            'finalPumbility': distribution([_metric(row, 'pumbility') for row in rows]),
            'rawExtrapolatedCharts': sum(bool(row.get('scoringProfileExtrapolated')) for row in rows),
            'twoGradeScoringMoves': len(moves),
            'twoGradeMoves': [{**_chart_label(row), 'estimate': row['estimatedDifficulty'],
                               'midpointOffset': row['estimatedDifficulty'] - level - .5}
                              for row in moves],
        })
    return result


def build_report(
    payload: Mapping[str, Any], records: Mapping[tuple[str, str], tuple[str, float]],
    baseline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if payload.get('schemaVersion') not in (23, 24, 25, 26):
        raise ValueError('Diagnostics require score-profile schema 23, 24, 25, or 26.')
    if baseline is not None and baseline.get('schemaVersion') not in (23, 24, 25, 26):
        raise ValueError('Baseline must use score-profile schema 23, 24, 25, or 26.')
    charts = [*payload.get('singles', []), *payload.get('doubles', [])]
    by_chart = defaultdict(dict)
    for (player, chart_id), observation in records.items():
        by_chart[chart_id][player] = observation
    folders = defaultdict(list)
    for row in charts:
        folders[(row['type'], row['level'])].append(row)
    folder_profiles = {}
    adjacent_profiles = []
    for key, rows in sorted(folders.items()):
        observed = [row for row in rows if row.get('scoringScoreProfile') is not None]
        supported = [row for row in observed if row['nContributors'] >= 20]
        folder_profiles[key] = {
            'profiledCharts': len(observed), 'supportedCharts': len(supported),
            'medianChartProfile': np.median([row['scoringScoreProfile'] for row in observed], axis=0).tolist() if observed else None,
            'supportedMedianChartProfile': np.median([row['scoringScoreProfile'] for row in supported], axis=0).tolist() if supported else None,
            'sparseEvidence': len(supported) < 5,
        }
    for (mode, level), profile in sorted(folder_profiles.items()):
        neighbor = folder_profiles.get((mode, level + 1))
        if neighbor is None:
            continue
        left, right = profile['medianChartProfile'], neighbor['medianChartProfile']
        adjacent_profiles.append({
            'mode': mode, 'lowerLevel': level, 'higherLevel': level + 1,
            'lowerFolder': profile, 'higherFolder': neighbor,
            'higherMinusLowerProfile': [b - a for a, b in zip(left, right)] if left and right else None,
        })
    comparisons, examples = [], []
    for (mode, level), rows in sorted(folders.items()):
        ordered = sorted((row for row in rows if row.get('scoringProfileMatchDifficulty') is not None),
                         key=lambda row: (row['scoringProfileMatchDifficulty'], str(row['chartId'])))
        if not ordered:
            continue
        targets = {str(ordered[index]['chartId']): ordered[index] for index in (0, len(ordered)//2, len(ordered)-1)}
        illustrative = [row for row in rows if mode == 'Double' and level == 26
                        and 'extreme music school' in row.get('songName', '').casefold()]
        targets.update({str(row['chartId']): row for row in illustrative})
        for row in sorted(targets.values(), key=lambda item: str(item['chartId'])):
            target_scores = by_chart[str(row['chartId'])]
            for other_level in (level - 1, level + 1):
                reference_rows = folders.get((mode, other_level), [])
                if not reference_rows:
                    continue
                reference_scores = [by_chart[str(reference['chartId'])] for reference in reference_rows]
                support = shared_player_comparison(target_scores, reference_scores)
                pairs = sorted(
                    [(len(target_scores.keys() & scores.keys()), str(reference['chartId']), reference, scores)
                     for reference, scores in zip(reference_rows, reference_scores)],
                    key=lambda pair: (-pair[0], pair[1]),
                )
                pair_reports = [
                    {'referenceChart': _chart_label(reference),
                     **shared_player_comparison(target_scores, [scores])}
                    for overlap, _, reference, scores in pairs[:MAX_REFERENCE_PAIRS]
                    if overlap >= MINIMUM_PAIR_OVERLAP
                ]
                entry = {
                    'targetChart': _chart_label(row), 'targetContributors': len(target_scores),
                    'targetProfile': row.get('scoringScoreProfile'),
                    'targetRawMatch': row.get('scoringProfileMatchDifficulty'),
                    'targetFinalScoring': row.get('estimatedDifficulty'),
                    'targetFinalPumbility': _metric(row, 'pumbility'),
                    'referenceLevel': other_level, 'referenceCharts': len(reference_rows),
                    **support, 'representativeChartPairs': pair_reports,
                }
                comparisons.append(entry)
                if row in illustrative:
                    examples.append(entry)
    folders_report = folder_diagnostics(payload)
    report = {
        'schemaVersion': 1, 'tierSchemaVersion': payload['schemaVersion'],
        'tierGeneratedAtUtc': payload.get('generatedAtUtc'),
        'localExperiment': payload['summary']['method'].get('localExperiment'),
        'policy': {
            'data': 'cached successful best scores; shared production-experiment normalization and Phoenix 2 overlap precedence',
            'selection': 'easiest, upper-median, hardest raw match per official folder plus EMS D26; adjacent levels only; up to three greatest-overlap chart pairs with at least three shared players',
            'differenceSign': 'target score minus each shared player\'s median score on reference charts; negative means lower target scores',
            'sameSourceOnly': 'for each target player, retain reference records from the target record\'s Phoenix source before taking their median',
            'sparseEvidence': f'fewer than {MINIMUM_SHARED_PLAYERS} shared players; folder reference sparse below five charts with at least 20 contributors',
            'rawPumbilityDiagnostic': 'arithmetic average of uncalibrated raw profile-match difficulty and clearing; not an exported tier rating',
            'limitations': 'selection and practice/attempt biases remain; adjacent profiles compare different populations; raw extrapolation is not an established grade; aggregate comparisons are diagnostics only and do not tune the estimator',
        },
        'summary': {
            'ratedCharts': sum(row['ratedCharts'] for row in folders_report),
            'twoGradeScoringMoves': sum(row['twoGradeScoringMoves'] for row in folders_report),
            'rawExtrapolatedCharts': sum(row['rawExtrapolatedCharts'] for row in folders_report),
            'targetFolderComparisons': len(comparisons),
        },
        'folders': folders_report, 'adjacentFolderProfiles': adjacent_profiles,
        'sharedPlayerComparisons': comparisons, 'emsExamples': examples,
    }
    if baseline is not None:
        report['baseline'] = {'schemaVersion': baseline['schemaVersion'],
                              'generatedAtUtc': baseline.get('generatedAtUtc'),
                              'folders': folder_diagnostics(baseline)}
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        '# Local scoring profile calibration diagnostics', '',
        f"Rated charts: {report['summary']['ratedCharts']}. Two-grade scoring moves: {report['summary']['twoGradeScoringMoves']}. Raw extrapolations: {report['summary']['rawExtrapolatedCharts']}.", '',
        'Distributions are min / q10 / median / q90 / max. Negative shared-player differences mean lower target scores. Private aggregate report; no player identifiers or individual histories are exported.', '',
        '| Folder | Scale | Charts | Contributors (median) | Scoring distribution | Pumbility distribution | Two-grade moves |',
        '|---|---:|---:|---:|---|---|---:|',
    ]
    def describe(dist: Mapping[str, Any]) -> str:
        return ' / '.join(f'{dist[key]:.3f}' if dist[key] is not None else '—'
                          for key in ('min', 'q10', 'median', 'q90', 'max'))
    for row in report['folders']:
        scales = ', '.join(f'{scale:.2f}' for scale in row['appliedScales']) or '—'
        support = row['contributors']['median']
        lines.append(f"| {row['mode'][0]}{row['level']} | {scales} | {row['ratedCharts']} | {support if support is not None else '—'} | {describe(row['finalScoring'])} | {describe(row['finalPumbility'])} | {row['twoGradeScoringMoves']} |")
    lines.extend(['', '## Extreme Music School illustrative comparisons', ''])
    for row in report['emsExamples']:
        all_sources, same_source = row['allSources'], row['sameSourceOnly']
        lines.append(f"- D{row['targetChart']['level']} against D{row['referenceLevel']}: {all_sources['sharedPlayers']} unique shared players, median score difference {all_sources['medianScoreDifference']}; lower/tied/higher {all_sources['lower']}/{all_sources['tied']}/{all_sources['higher']}. Same-source subset: {same_source['sharedPlayers']} players, median {same_source['medianScoreDifference']}. Sparse evidence: {all_sources['sparseEvidence']}.")
    lines.extend(['', '## Interpretation and bounded selection', ''])
    lines.extend(f'- {key}: {value}' for key, value in report['policy'].items())
    lines.extend(['', 'Complete raw/final distributions, baseline comparisons (when provided), adjacent profiles, and aggregate shared-player comparisons are in diagnostics.json.', ''])
    return '\n'.join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tiers', type=Path, default=COMBINED_OUTPUT_PATH)
    parser.add_argument('--baseline', type=Path, help='Optional previous schema-23/24/25/26 tier artifact.')
    parser.add_argument('--output-dir', type=Path, default=None,
                        help='Defaults to a timestamped private local verification directory.')
    args = parser.parse_args(argv)
    output_dir = args.output_dir or DATA_ROOT / 'verification' / datetime.now(timezone.utc).strftime('scoring-profile-diagnostics-%Y%m%dT%H%M%S%fZ')
    input_paths = {args.tiers.resolve(), COMBINED_OUTPUT_PATH.resolve()}
    if args.baseline:
        input_paths.add(args.baseline.resolve())
    if any((output_dir / name).resolve() in input_paths for name in ('diagnostics.json', 'diagnostics.md')):
        parser.error('Report outputs must not overwrite a tier or baseline input.')
    payload = json.loads(args.tiers.read_text(encoding='utf-8'))
    baseline = json.loads(args.baseline.read_text(encoding='utf-8')) if args.baseline else None
    records = collect_percentile_scores(_read_snapshot('phoenix1'), _read_snapshot('phoenix2'))
    report = build_report(payload, records, baseline)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'diagnostics.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (output_dir / 'diagnostics.md').write_text(render_markdown(report), encoding='utf-8')
    print(json.dumps({'outputDirectory': str(output_dir), **report['summary']}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
