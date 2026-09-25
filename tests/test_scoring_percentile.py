from __future__ import annotations

import copy
import itertools
import math
import unittest

import numpy as np

from scoring_percentile import (
    build_percentile_tier_payload, collect_percentile_scores, fit_profile_curve,
    fit_profile_scale, fit_profile_scales, match_profiles, profile_difficulty_interval, two_grade_count,
)
from tier_difficulty import build_tier_metrics, tier_metric_method


def chart(cid, level=20, chart_type='Single', notes=1000):
    return {'id': cid, 'songName': cid, 'type': chart_type, 'level': level,
            'difficulty': f'{"S" if chart_type == "Single" else "D"}{level}', 'noteCount': notes}


def score(player, cid, value, **kwargs):
    return {'playerId': player, 'chartId': cid, 'score': value, 'isBroken': False, **kwargs}


def base_records(charts):
    rows = [{**row, 'chartId': row['id'], 'folder': row['difficulty'],
             'mode': 'Singles' if row['type'] == 'Single' else 'Doubles',
             'estimatedDifficulty': row['level'] + .5, 'difficultyDelta': 0,
             'nContributors': 500, 'nPlayersScored': 500, 'evidenceStatus': 'Published',
             'folderRangeCompression': 1, 'effectBandRank': 4,
             'whatIfEstimates': [{'level': 999, 'estimatedDifficulty': 999}]}
            for row in charts]
    metrics, refs = build_tier_metrics(rows, {}, {})
    for row, metric in zip(rows, metrics):
        row['tierMetrics'] = metric
    return rows, {'modes': {}, 'tierMetrics': tier_metric_method(refs)}


class PercentileScoringTests(unittest.TestCase):
    def test_normalization_overlap_success_and_equal_player_membership(self):
        p1 = {'charts': [chart('a', notes=2000), chart('changed', chart_type='Double'), chart('removed')],
              'scores': [score('both', 'a', 990_000), score('p1', 'a', 990_000),
                         score('p1', 'a', 995_000), score('failed', 'a', 999_000, isBroken=True),
                         score('wrong-mode', 'changed', 950_000), score('removed', 'removed', 900_000)]}
        p2 = {'charts': [chart('a'), chart('changed')],
              'scores': [score('both', 'a', 970_000), score('zero', 'a', 0, pumbility=0),
                         score('no-history', 'a', 800_000, pumbility=0), score('broken', 'a', 999_000, isBroken=True),
                         score('invalid', 'a', float('nan')), score('too-high', 'a', 1_000_001),
                         score('boolean', 'a', True), score('p1', 'a', 900_000, isBroken=True)]}
        self.assertEqual(collect_percentile_scores(p1, p2), {
            ('both', 'a'): ('phoenix2', 970_000), ('p1', 'a'): ('phoenix1', 990_000),
            ('zero', 'a'): ('phoenix2', 0), ('no-history', 'a'): ('phoenix2', 800_000),
        })
        altered = copy.deepcopy(p2)
        for row in altered['scores']:
            row.update(pumbility=100_000, plate='PG', scoringRating=99, recordedAt='2050-01-01')
        self.assertEqual(collect_percentile_scores(p1, altered), collect_percentile_scores(p1, p2))

    def test_projection_matches_other_levels_and_interpolates_without_label_input(self):
        curve = {'difficulties': [22.5, 23.5, 24.5],
                 'profiles': [[950000, 960000, 970000, 980000, 990000],
                              [940000, 950000, 960000, 970000, 980000],
                              [920000, 930000, 940000, 950000, 960000]]}
        estimates, errors = match_profiles([
            [940000, 950000, 960000, 970000, 980000], [945000, 955000, 965000, 975000, 985000],
            [960000, 970000, 980000, 990000, 1000000], [900000, 910000, 920000, 930000, 940000],
        ], curve)
        np.testing.assert_allclose(estimates, [23.5, 23., 21.5, 25.5])
        np.testing.assert_allclose(errors, 0, atol=1e-8)
        # q10 has one sixth of the total weight; its 3,000-point change shifts
        # the match by 500 points on this 10,000-point-per-level segment.
        estimate, error = match_profiles([[943000, 950000, 960000, 970000, 980000]], curve)
        self.assertAlmostEqual(estimate[0], 23.45)
        self.assertAlmostEqual(error[0], np.sqrt(1_250_000))
        flat = {'difficulties': [20.5, 21.5, 22.5, 23.5],
                'profiles': [[990000]*5, [980000]*5, [980000]*5, [970000]*5]}
        self.assertEqual(match_profiles([[980000]*5], flat)[0][0], 22.)
        self.assertEqual(two_grade_count([1.5, -1.5, -1.5001, 1.4999]), 2)

    def test_ninetieth_percentile_has_double_weight_in_projection_and_error(self):
        curve = {'difficulties': [20.5, 21.5], 'profiles': [[990000]*5, [980000]*5]}
        estimates, errors = match_profiles([
            [980000]*5,
            [974000, 980000, 980000, 980000, 980000],
            [980000, 980000, 980000, 980000, 986000],
        ], curve)
        # Weighted score means are 980000, 979000, and 982000. A q90-only
        # change shifts twice as far as the same-sized q10-only change.
        np.testing.assert_allclose(estimates, [21.5, 21.6, 21.3])
        np.testing.assert_allclose(errors, [0, np.sqrt(5_000_000), np.sqrt(8_000_000)], atol=1e-8)

    def test_raw_reference_smoothing_and_sparse_data(self):
        folders = {level: [[1_000_000-loss+offset for offset in (-20000, -10000, 0, 10000, 20000)]]*20
                   for level, loss in zip(range(20, 25), [40000, 50000, 50000, 70000, 80000])}
        curve = fit_profile_curve(folders)
        # The raw match can move; the final calibration centers it separately.
        proposed = match_profiles([folders[22][0]], curve)[0][0]
        self.assertLess(proposed, 22.2)
        self.assertGreater(proposed, 21.)
        profiles = np.asarray(curve['profiles'])
        self.assertTrue(np.all(np.diff(profiles, axis=0) <= 1e-7))
        self.assertTrue(np.all(np.diff(profiles, axis=1) >= 0))
        self.assertIsNone(fit_profile_curve({20: folders[20]}))
        self.assertIsNone(fit_profile_curve({20: [[980000]*5]*5, 21: [[980000]*5]*5}))
        sparse = fit_profile_curve({20: folders[20], 22: folders[23], 23: folders[24][:4]})
        self.assertEqual(sparse['difficulties'], [20.5, 21.5, 22.5])
        self.assertEqual(sparse['references'][1]['charts'], 0)
        self.assertIsNone(sparse['references'][1]['rawProfile'])

    def test_joint_bootstrap_is_reproducible_order_independent_and_requires_support(self):
        curve = {'difficulties': [20.5, 21.5], 'profiles': [[980000]*5, [970000]*5]}
        self.assertEqual(profile_difficulty_interval([975000]*19, 'a', curve), (None, None))
        self.assertEqual(profile_difficulty_interval([975000]*20, 'a', None), (None, None))
        self.assertEqual(profile_difficulty_interval([975000]*20, 'a', curve), (21., 21.))
        values = [950_000 + i*1000 for i in range(50)]
        result = profile_difficulty_interval(values, 'a', curve)
        self.assertEqual(result, profile_difficulty_interval(list(reversed(values)), 'a', curve))
        self.assertLess(result[0], result[1])
        self.assertTrue(all(np.isfinite(result)))

    def test_scale_caps_combined_tail_count_and_obeys_serialized_boundaries(self):
        for offsets, expected in [([2]*4, .74), ([-2]*4, .75),
                                  ([10, 10, -10, 2], .74), ([0]*5, 1.),
                                  ([1000]*4, 0.), ([1000]*3, 1.), ([], 1.)]:
            levels = [21]*len(offsets)
            scale = fit_profile_scale(offsets, levels)
            self.assertEqual(scale, expected)
            self.assertLessEqual(two_grade_count(offsets, scale, levels), 3)
            if scale < 1:
                self.assertGreater(two_grade_count(offsets, round(scale+.01, 2), levels), 3)
        self.assertEqual(two_grade_count([1.5, -1.5, -1.500001, 1.499999], levels=[16, 20, 23, 28]), 2)
        self.assertEqual(two_grade_count([1.4999996], levels=[21]), 1)
        self.assertEqual(two_grade_count([1.4999994], levels=[21]), 0)

    def test_folder_scale_dynamic_program_matches_exhaustive_joint_objective(self):
        # Two levels in each mode exercise both neighbor paths and the combined
        # free allowance. The exhaustive oracle has no capped-count states.
        folders = {
            ('Single', 20): {'offsets': [-8]*4 + [-.3]*11 + [.3]*11 + [8]*4, 'contributors': [20]*30},
            ('Single', 22): {'offsets': np.linspace(-1.4, 1.4, 30), 'contributors': [20]*30},
            ('Double', 20): {'offsets': [-7]*4 + [-.5]*11 + [.5]*11 + [7]*4, 'contributors': [20]*30},
            ('Double', 21): {'offsets': np.linspace(-1, 1, 30), 'contributors': [20]*30},
        }
        candidates, baseline = [0., .1, .25, .5, 1.], .1
        keys = [('Single', 20), ('Single', 22), ('Double', 20), ('Double', 21)]
        expected = None
        for scales in itertools.product(candidates, repeat=len(keys)):
            width_cost = 0.
            count = 0
            for key, scale in zip(keys, scales):
                values = folders[key]['offsets']
                width = float(np.quantile(values, .9) - np.quantile(values, .1))
                target = max(1.0, baseline * width)
                width_cost += ((scale * width - target) / target) ** 2
                count += two_grade_count(values, scale, [key[1]]*len(values))
            smoothing = sum(.05 * (math.log(scales[right]+.01) - math.log(scales[left]+.01))**2
                            / (keys[right][1]-keys[left][1]) for left, right in [(0, 1), (2, 3)])
            total = width_cost + smoothing + .05*max(0, count-10)
            proposal = (total, count, scales)
            if expected is None or proposal < expected:
                expected = proposal
        actual = fit_profile_scales(folders, baseline_scale=baseline, candidate_scales=candidates)
        selected = tuple(actual['folderScales'][mode][str(level)] for mode, level in keys)
        self.assertEqual(selected, expected[2])
        self.assertEqual(actual['actualTwoGradeCount'], expected[1])
        self.assertAlmostEqual(actual['objective']['total'], expected[0], places=12)
        # Overflow identities hold even when either/both modes already paid it.
        for single, double in itertools.product(range(25), repeat=2):
            bucket_cost = (max(0, single-10) + max(0, double-10)
                           + max(0, min(single, 10)+min(double, 10)-10))
            self.assertEqual(bucket_cost, max(0, single+double-10))

    def test_soft_rarity_allows_more_than_ten_and_does_not_require_three(self):
        broad_tails = [-200]*6 + np.linspace(-1, 1, 188).tolist() + [200]*6
        result = fit_profile_scales({('Single', 20): {'offsets': broad_tails, 'contributors': [20]*200}},
                                    baseline_scale=.01, candidate_scales=[0, .5, 1])
        self.assertEqual(result['actualTwoGradeCount'], 12)
        self.assertEqual(result['folderScales']['Single']['20'], .5)
        self.assertAlmostEqual(result['objective']['rarityCost'], .1)
        quiet = fit_profile_scales({('Double', 26): {'offsets': np.linspace(-1, 1, 30),
                                                   'contributors': [20]*30}},
                                   baseline_scale=.1, candidate_scales=[0, .5, 1])
        self.assertEqual(quiet['actualTwoGradeCount'], 0)
        self.assertEqual(quiet['objective']['rarityCost'], 0)
        self.assertEqual(quiet['folderScales']['Double']['26'], .5)

    def test_sparse_flat_and_unsupported_fallbacks_and_deterministic_ties(self):
        supported = {'offsets': np.linspace(-1, 1, 30), 'contributors': [20]*30}
        sparse = {'offsets': [-.1, .1], 'contributors': [1, 1]}
        flat = {'offsets': [0]*30, 'contributors': [100]*30}
        folders = {('Single', 20): supported, ('Single', 21): sparse, ('Single', 22): flat,
                   ('Double', 23): sparse, ('Double', 24): flat}
        result = fit_profile_scales(folders, baseline_scale=.25, candidate_scales=[0, .25, .5, 1])
        self.assertEqual(result['folderScales']['Single'], {'20': .5, '21': .5, '22': .5})
        self.assertEqual(result['folderScales']['Double'], {'23': .25, '24': .25})
        self.assertEqual(result['folderDiagnostics']['Single']['21']['reliabilityWeight'], 0)
        self.assertEqual(result['folderDiagnostics']['Single']['22']['rawWidth'], 0)
        self.assertIsNone(result['folderDiagnostics']['Single']['22']['preferredScale'])
        self.assertEqual(result['folderDiagnostics']['Double']['23']['fallback'], 'mode-without-supported-spread')
        self.assertEqual(result, fit_profile_scales(dict(reversed(list(folders.items()))),
                                                  baseline_scale=.25, candidate_scales=[1, .5, .25, 0]))
        # Identical baseline-distance costs, counts, and no neighbors select the
        # lexicographically smaller scale, rather than depending on insertion order.
        tied = fit_profile_scales({('Single', 20): flat}, baseline_scale=.25, candidate_scales=[.5, 0])
        self.assertEqual(tied['folderScales']['Single']['20'], 0)
        self.assertEqual(fit_profile_scales({})['actualTwoGradeCount'], 0)

    def test_restrictive_folder_does_not_impose_a_shared_scale(self):
        folders = {('Single', 16): {'offsets': [-30]*5 + [0]*20 + [30]*5, 'contributors': [20]*30},
                   ('Single', 26): {'offsets': np.linspace(-1, 1, 30), 'contributors': [20]*30},
                   ('Double', 26): {'offsets': np.linspace(-.6, .6, 30), 'contributors': [20]*30}}
        result = fit_profile_scales(folders, baseline_scale=.01)
        self.assertGreater(result['folderScales']['Single']['26'], result['folderScales']['Single']['16'])
        self.assertGreater(result['folderScales']['Double']['26'], result['folderScales']['Single']['26'])

    def test_payload_profiles_folder_centering_adaptive_scales_and_preservation(self):
        charts, scores = [], []
        for typ, prefix, base in [('Single', 's', 970000), ('Double', 'd', 850000)]:
            for level in (20, 21, 22):
                for offset in range(-2, 3):
                    cid = f'{prefix}{level}-{offset}'
                    charts.append(chart(cid, level, typ))
                    scores.extend(score(f'p{i}', cid, base-(level-20)*10000+offset*500+i*100) for i in range(20))
        charts.extend([chart('mislabel', 20), chart('unrated'), chart('sparse', 24)])
        scores.extend(score(f'p{i}', 'mislabel', 960000+i*100) for i in range(20))
        scores.append(score('only', 'sparse', 900000))
        for index in range(4):
            charts.append(chart(f'extreme-{index}', 20))
            scores.append(score('only', f'extreme-{index}', 300000))
        p2 = {'charts': charts, 'scores': scores}
        rows, meta = base_records(charts)
        original = copy.deepcopy((rows, meta, p2))
        generated_at = '2026-09-26T00:00:00Z'
        payload = build_percentile_tier_payload(rows, meta, {'charts': [], 'scores': []}, p2,
                                                generated_at_utc=generated_at)
        by_id = {row['chartId']: row for row in payload['singles']+payload['doubles']}
        self.assertEqual(payload['schemaVersion'], 25)
        self.assertEqual(payload['summary']['method']['localExperiment'], 'scoring-profile-level-scales')
        self.assertEqual(payload['summary']['method']['scoring']['percentiles'], [.1, .25, .5, .75, .9])
        self.assertEqual(payload['summary']['method']['scoring']['profileWeights'], [1, 1, 1, 1, 2])
        calibration = payload['summary']['method']['scoreProfileCalibration']
        self.assertIsNone(calibration['outlierCap'])
        self.assertNotIn('difficultyDeltaScale', payload['summary']['method']['scoring'])
        self.assertNotIn('maxTwoGradeMoves', payload['summary']['method']['scoring'])
        self.assertIsNone(payload['summary']['method']['difficultyDeltaScale'])
        self.assertEqual(calibration['actualTwoGradeCount'], sum(
            row['estimatedDifficulty'] is not None and
            (row['estimatedDifficulty'] >= row['level']+2 or row['estimatedDifficulty'] < row['level']-1)
            for row in by_id.values()))
        self.assertEqual(by_id['s20-0']['scoringScoreProfile'], [970190, 970475, 970950, 971425, 971710])
        self.assertEqual(by_id['mislabel']['scoringProfileMatchDifficulty'], by_id['s21-0']['scoringProfileMatchDifficulty'])
        self.assertNotEqual(by_id['mislabel']['estimatedDifficulty'], by_id['s21-0']['estimatedDifficulty'])
        for typ in ('Single', 'Double'):
            for level in {r['level'] for r in by_id.values() if r['type'] == typ}:
                estimates = [r['estimatedDifficulty'] for r in by_id.values()
                             if r['type'] == typ and r['level'] == level and r['estimatedDifficulty'] is not None]
                if estimates:
                    self.assertAlmostEqual(float(np.median(estimates)), level+.5, places=6)
        self.assertAlmostEqual(by_id['d20-0']['estimatedDifficulty'], 20.5)
        self.assertLess(by_id['s20-2']['estimatedDifficulty'], by_id['s20-0']['estimatedDifficulty'])
        self.assertLess(by_id['s20-0']['estimatedDifficulty'], by_id['s20--2']['estimatedDifficulty'])
        self.assertEqual(by_id['s20-2']['levelRank'], 1)
        for row in by_id.values():
            scale = row['scoringDifficultyScale']
            self.assertEqual(row['whatIfEstimates'], [])
            self.assertNotIn('scoringPercentileScore', row)
            self.assertIsNone(row['tierMetrics']['pumbility']['estimatedDifficulty'])
            self.assertEqual(row['tierMetrics']['clearing'], next(r['tierMetrics']['clearing'] for r in rows if r['chartId']==row['chartId']))
            if row['nContributors'] >= 20:
                self.assertLessEqual(row['difficultyCi95Low'], row['difficultyCi95High'])
                self.assertAlmostEqual(row['difficultyDeltaCi95Low'], row['difficultyCi95Low']-row['level']-.5, places=5)
                for suffix in ('Low', 'High'):
                    expected = row['level']+.5+scale*(row[f'scoringProfileMatchCi95{suffix}']-row['scoringFolderReferenceDifficulty'])
                    self.assertAlmostEqual(row[f'difficultyCi95{suffix}'], expected, places=5)
            if row['estimatedDifficulty'] is not None:
                self.assertEqual(scale, calibration['folderScales'][row['type']][str(row['level'])])
                expected = row['level']+.5+scale*(row['scoringProfileMatchDifficulty']-row['scoringFolderReferenceDifficulty'])
                self.assertAlmostEqual(row['estimatedDifficulty'], expected, places=5)
        self.assertTrue(by_id['sparse']['scoringProfileExtrapolated'])
        self.assertIsNone(by_id['sparse']['difficultyCi95Low'])
        self.assertIsNone(by_id['unrated']['estimatedDifficulty'])
        self.assertIsNone(by_id['unrated']['scoringDifficultyScale'])
        self.assertIsNone(by_id['unrated']['levelRank'])
        self.assertEqual(by_id['unrated']['evidenceStatus'], 'Unrated')
        self.assertEqual((rows, meta, p2), original)
        # Exercise the production worker entry point: same estimates and timestamp,
        # distinct public schema, and original recommendation rows left intact.
        from analysis_runtime import build_combined_tier_payload as production_tiers
        from pumbility_contract import COMBINED_TIER_SCHEMA_VERSION, scoring_tier_method_identity
        production = production_tiers(rows, meta, {'charts': [], 'scores': []}, p2,
                                      generated_at_utc=generated_at)
        expected = copy.deepcopy(payload)
        expected['schemaVersion'] = COMBINED_TIER_SCHEMA_VERSION
        expected['summary']['method'].pop('localExperiment')
        expected['summary']['scriptVersion'] = expected['summary']['scriptVersion'].replace('+local-', '+')
        self.assertEqual(production, expected)
        for key, value in scoring_tier_method_identity().items():
            self.assertEqual(production['summary']['method']['scoring'][key], value)
        self.assertEqual((rows, meta, p2), original)
        # Having scores alone does not invent a cross-level calibration.
        small_rows, small_meta = base_records([chart('single-folder')])
        small = build_percentile_tier_payload(small_rows, small_meta, {'charts': [], 'scores': []},
            {'charts': [chart('single-folder')], 'scores': [score('p', 'single-folder', 990000)]})
        self.assertIsNone(small['singles'][0]['estimatedDifficulty'])
        self.assertEqual(small['singles'][0]['evidenceStatus'], 'Unrated')


if __name__ == '__main__':
    unittest.main()
