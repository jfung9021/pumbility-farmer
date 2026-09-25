from __future__ import annotations

import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import analyze_scoring_profile_calibration as diagnostics


def chart(cid: str, level: int, raw: float, estimate: float, contributors: int = 20) -> dict:
    return {
        'chartId': cid, 'songName': f'Chart {cid}', 'type': 'Double', 'level': level,
        'nContributors': contributors, 'scoringProfileMatchDifficulty': raw,
        'estimatedDifficulty': estimate, 'scoringProfileExtrapolated': raw > 26.5,
        'scoringScoreProfile': [score - level * 100 for score in (950000, 960000, 970000, 980000, 990000)],
        'tierMetrics': {'clearing': {'estimatedDifficulty': level + .2},
                        'pumbility': {'estimatedDifficulty': (estimate + level + .2) / 2}},
    }


def payload(rows: list[dict], schema: int = 25) -> dict:
    rows = copy.deepcopy(rows)
    if schema < 25:
        for row in rows:
            row['scoringScoreProfile'] = row['scoringScoreProfile'][1:4]
    return {
        'schemaVersion': schema, 'singles': [], 'doubles': rows,
        'summary': {'method': {
            'localExperiment': 'scoring-profile-level-scales' if schema >= 24 else 'scoring-profile-centered',
            'scoring': {'difficultyDeltaScale': .12} if schema == 23 else {},
            'scoreProfileCalibration': {'curves': {}, 'folderDiagnostics': {'Double': {'26': {'weight': .5}}}},
        }},
    }


class ScoringProfileDiagnosticsTests(unittest.TestCase):
    def test_unique_player_aggregation_and_same_source_subset(self):
        # A player with three reference charts still contributes only once.
        target = {'private-a': ('phoenix2', 900), 'private-b': ('phoenix1', 800)}
        refs = [
            {'private-a': ('phoenix1', 800), 'private-b': ('phoenix2', 700)},
            {'private-a': ('phoenix2', 940), 'private-b': ('phoenix1', 820)},
            {'private-a': ('phoenix2', 980)},
        ]
        summary = diagnostics.shared_player_comparison(target, refs)
        self.assertEqual(summary['allSources']['sharedPlayers'], 2)
        self.assertEqual(summary['allSources']['medianScoreDifference'], 0)  # -40 and +40
        self.assertEqual(summary['allSources']['lower'], 1)
        self.assertEqual(summary['allSources']['higher'], 1)
        self.assertEqual(summary['sameSourceOnly']['medianScoreDifference'], -40)  # -60 and -20
        self.assertEqual(summary['sameSourceOnly']['lower'], 2)
        self.assertTrue(summary['sameSourceOnly']['sparseEvidence'])
        self.assertNotIn('private-', json.dumps(summary))

    def test_folder_export_boundaries_raw_combined_and_schema23_baseline(self):
        rows = [chart('up', 26, 28, 27.9999996), chart('edge', 26, 20, 25),
                chart('down', 26, 19, 24.999999), chart('middle', 26, 26.5, 26.5)]
        for row in rows:
            row['scoringDifficultyScale'] = .32
        current = payload(rows)
        before = copy.deepcopy(current)
        baseline = payload([{key: value for key, value in row.items() if key != 'scoringDifficultyScale'}
                            for row in rows], schema=23)
        report = diagnostics.build_report(current, {}, baseline)
        folder = report['folders'][0]
        self.assertEqual(folder['appliedScales'], [.32])
        self.assertEqual(folder['twoGradeScoringMoves'], 2)
        self.assertEqual(folder['rawExtrapolatedCharts'], 1)
        self.assertAlmostEqual(folder['rawPumbilityDiagnostic']['min'], (19 + 26.2)/2)
        self.assertEqual(folder['calibrationDiagnostics'], {'weight': .5})
        self.assertEqual(report['baseline']['folders'][0]['appliedScales'], [.12])
        self.assertEqual(current, before)
        self.assertIn('D26', diagnostics.render_markdown(report))

    def test_bounded_representative_selection_all_folder_profiles_and_no_personal_data(self):
        rows = [chart(f'{level}-{index}', level, level + index / 10, level + index / 10)
                for level in (25, 26, 27) for index in range(8)]
        ems = rows[11]
        ems['songName'] = 'Extreme Music School 2nd period feat. Nanahira'
        ems['nContributors'] = 14
        records = {(f'secret-person-{player}', row['chartId']): ('phoenix2', 970000 - row['level'] * 100)
                   for row in rows for player in range(12)}
        report = diagnostics.build_report(payload(rows), records)
        self.assertEqual(len(report['folders']), 3)
        self.assertEqual(len(report['adjacentFolderProfiles']), 2)
        self.assertEqual(len(report['sharedPlayerComparisons'][0]['targetProfile']), 5)
        self.assertEqual(len(report['sharedPlayerComparisons']), 14)  # 3 + (3+EMS)*2 + 3
        self.assertEqual(len(report['emsExamples']), 2)
        for comparison in report['sharedPlayerComparisons']:
            self.assertEqual(comparison['allSources']['sharedPlayers'], 12)
            self.assertFalse(comparison['allSources']['sparseEvidence'])
            self.assertLessEqual(len(comparison['representativeChartPairs']), 3)
        self.assertNotIn('secret-person-', json.dumps(report))

    def test_empty_distributions_and_reject_incompatible_schema(self):
        self.assertEqual(diagnostics.distribution([None, float('nan')]),
                         {'count': 0, 'min': None, 'q10': None, 'median': None, 'q90': None, 'max': None})
        with self.assertRaisesRegex(ValueError, 'schema 23, 24, 25, or 26'):
            diagnostics.build_report(payload([], schema=21), {})

    def test_cli_only_writes_aggregate_reports(self):
        rows = [chart('ems', 26, 27.5, 27.4)]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tiers = root / 'web_results.json'
            baseline = root / 'baseline.json'
            recommendation = root / 'recommendations.json'
            tiers.write_text(json.dumps(payload(rows)), encoding='utf-8')
            baseline.write_text(json.dumps(payload(rows, schema=23)), encoding='utf-8')
            recommendation.write_bytes(b'private recommendations unchanged')
            original = {path: path.read_bytes() for path in (tiers, baseline, recommendation)}
            output = root / 'verification'
            with (patch.object(diagnostics, '_read_snapshot', return_value={'charts': [], 'scores': []}) as read,
                  redirect_stdout(io.StringIO())):
                self.assertEqual(diagnostics.main(['--tiers', str(tiers), '--baseline', str(baseline),
                                                   '--output-dir', str(output)]), 0)
            self.assertEqual([call.args[0] for call in read.call_args_list], ['phoenix1', 'phoenix2'])
            self.assertEqual({path: path.read_bytes() for path in original}, original)
            self.assertEqual({path.name for path in output.iterdir()}, {'diagnostics.json', 'diagnostics.md'})
            self.assertEqual(json.loads((output / 'diagnostics.json').read_text(encoding='utf-8'))['tierSchemaVersion'], 25)

    def test_cli_refuses_output_input_collision_before_reading_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tiers = root / 'diagnostics.json'
            tiers.write_text(json.dumps(payload([])), encoding='utf-8')
            original = tiers.read_bytes()
            with (patch.object(diagnostics, '_read_snapshot', side_effect=AssertionError('Do not load cache')),
                  patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit)):
                diagnostics.main(['--tiers', str(tiers), '--output-dir', str(root)])
            self.assertEqual(tiers.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
