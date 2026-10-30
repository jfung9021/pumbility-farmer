import copy
import json
import math
import unittest
from pathlib import Path
from statistics import median
from unittest.mock import patch

from approved_tier_ratings import approved_publication_version, validate_public_approved_ratings
from extended_official_clearing import apply_extended_official_clearing, validate_extended_official_clearing
from observed_capped_clearing import apply_observed_capped_clearing, validate_observed_capped_clearing
from scoped_tier_width import apply_scoped_tier_width, validate_scoped_tier_width
from final_folder_centering import apply_final_folder_centering, validate_final_folder_centering
from centered_maximum_pumbility import apply_centered_maximum_pumbility, validate_centered_maximum_pumbility
from production_tiers import APPROVED_VERSION, RETAINED_PRODUCER, _public_copy, validate_current_tier_publication
from tier_public import compact_tier_payload, tier_browser_payload


def fixture():
    data=json.loads((Path(__file__).parent/'fixtures/approved-tier-ratings.json').read_text(encoding='utf-8'))
    value=data['base']; stages=[]
    for fn in (lambda b:apply_extended_official_clearing(b,data['merged'],local_only=False),apply_observed_capped_clearing,
               apply_scoped_tier_width,apply_final_folder_centering,apply_centered_maximum_pumbility):
        value,_=fn(value);stages.append(value)
    return data['base'],stages


class ApprovedTierRatingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.base,cls.stages=fixture();cls.result=cls.stages[-1]

    def test_hosted_schema_chain_recovers_exact_inputs_and_keeps_retained_goals(self):
        self.assertEqual([p['schemaVersion'] for p in self.stages],[158,159,160,161,162])
        value=self.result
        for check in (validate_centered_maximum_pumbility,validate_final_folder_centering,validate_scoped_tier_width,
                      validate_observed_capped_clearing,validate_extended_official_clearing):value=check(value)
        self.assertEqual(value,self.base)
        for p in self.stages:
            self.assertEqual(p['generatedAtUtc'],self.base['generatedAtUtc'])
            for k in ('singles','doubles'):
                for r in p[k]:self.assertEqual(set(r['scoringVariants']),{'general','sssplus'})
        for name in ('extendedOfficialClearing','observedCappedClearing','scopedTierWidth','finalFolderCentering','centeredMaximumPumbility'):
            self.assertFalse(self.result['summary']['method'][name]['localOnly'])

    def test_observed_capped_s24_d25_are_distinct_provisional_and_use_both_sources(self):
        values={}
        for k,level in (('singles',24),('doubles',25)):
            for r in self.result[k]:
                c=r['tierMetrics']['clearing']
                if r['level']==level and c.get('observedAtCap') and c['estimatedDifficulty'] is not None:
                    self.assertEqual(c['evidenceStatus'],'Provisional');self.assertFalse(c['defaultedAtCap'])
                    self.assertTrue(c['clearingEvidence']['sourceStates']['Phoenix2']['possiblyTruncated'])
                    self.assertEqual(set(c['clearingEvidence']['sourceDates']),{'Phoenix','Phoenix2'})
                    values.setdefault(k,[]).append(c['estimatedDifficulty'])
        self.assertEqual({k:len(set(v)) for k,v in values.items()},{'singles':2,'doubles':2})

    def test_width_is_point_seven_five_and_preserves_intervals_nulls_and_evidence(self):
        before,after=self.stages[1:3]
        policy=after['summary']['method']['scopedTierWidth']
        self.assertEqual((policy['version'],policy['method'],policy['factor'],policy['ratio']),
                         (2,'scoped-final-component-width-v2',.75,1))
        nulls=capped=0
        for k in ('singles','doubles'):
            for a,b in zip(before[k],after[k]):
                if a['level']<(24 if k=='singles' else 25):
                    self.assertEqual(a,b)
                    continue
                pairs=[(a['scoringVariants'][goal],b['scoringVariants'][goal]) for goal in ('general','sssplus')]
                pairs.append((a['tierMetrics']['clearing'],b['tierMetrics']['clearing']))
                for old,new in pairs:
                    for field in ('estimatedDifficulty','difficultyCi95Low','difficultyCi95High'):
                        if old.get(field) is None:
                            self.assertIsNone(new.get(field));nulls+=1
                        else:self.assertAlmostEqual(new[field],old[field])
                    for field in ('evidenceStatus','provisionalReasons','unratedReason','scoringProvisional',
                                  'observedAtCap','defaultedAtCap','clearingEvidence','initialFit'):
                        self.assertEqual(new.get(field),old.get(field))
                    capped+=bool(old.get('observedAtCap'))
        self.assertGreater(nulls,0);self.assertGreater(capped,0)

    def test_final_high_folder_deviations_are_three_quarters_of_the_legacy_full_width(self):
        legacy=json.loads((Path(__file__).parent/'fixtures/approved-tier-ratings.json').read_text(encoding='utf-8'))['legacyCandidate']
        changed=0
        for k in ('singles','doubles'):
            old={r['chartId']:r for r in legacy[k]}
            for row in self.result[k]:
                previous=old[row['chartId']];midpoint=row['level']+.5
                ratio=.75 if row['level']>=(24 if k=='singles' else 25) else 1
                pairs=[(previous['tierMetrics']['clearing'],row['tierMetrics']['clearing'])]
                pairs.extend((previous[field][goal],row[field][goal])
                    for field in ('scoringVariants',) for goal in ('general','sssplus'))
                for before,after in pairs:
                    for field in ('estimatedDifficulty','difficultyCi95Low','difficultyCi95High'):
                        if before.get(field) is None:self.assertIsNone(after.get(field))
                        else:
                            self.assertAlmostEqual(after[field],midpoint+ratio*(before[field]-midpoint))
                            changed+=ratio!=1 and abs(before[field]-midpoint)>1e-8
        self.assertGreater(changed,0)

    def test_legacy_public_policy_remains_readable_and_width_versions_cannot_be_mixed(self):
        legacy=json.loads((Path(__file__).parent/'fixtures/approved-tier-ratings.json').read_text(encoding='utf-8'))['legacyCandidate']
        validate_public_approved_ratings(legacy)
        self.assertEqual(approved_publication_version(legacy),'6.26.0-centered-maximum')
        uniform,_=apply_centered_maximum_pumbility(self.stages[-2],policy_version=1)
        validate_public_approved_ratings(uniform)
        self.assertEqual(approved_publication_version(uniform),'6.27.0-uniform-width')
        self.assertEqual(approved_publication_version(self.result),'6.28.0-half-downward-pumbility')
        prior=validate_centered_maximum_pumbility(legacy)
        validate_scoped_tier_width(validate_final_folder_centering(prior))
        for version,method,factor in ((1,'scoped-final-component-width-v1',.75),
                                     (2,'scoped-final-component-width-v2',1),
                                     (1,'scoped-final-component-width-v2',.75)):
            mixed=copy.deepcopy(self.result)
            mixed['summary']['method']['scopedTierWidth'].update(version=version,method=method,factor=factor)
            with self.assertRaises(ValueError):validate_public_approved_ratings(mixed)

    def test_current_publication_cannot_promote_a_legacy_full_adjustment(self):
        payload=copy.deepcopy(self.result)
        publication={'method':APPROVED_VERSION,'inputs':{'producer':RETAINED_PRODUCER}}
        payload['summary']['method']['productionTierPublication']=publication
        self.assertEqual(validate_current_tier_publication(payload),publication)
        for version in ('6.26.0-centered-maximum','6.27.0-uniform-width'):
            publication['method']=version
            with self.assertRaises(ValueError):validate_current_tier_publication(payload)

    def test_current_publication_excludes_standalone_sss(self):
        self.assertEqual(self.result['summary']['method']['centeredMaximumPumbility']['goals'],['general','sssplus'])
        for field in ('scoringVariants','pumbilityVariants'):
            payload=copy.deepcopy(self.result)
            payload['singles'][0][field]['sss']=copy.deepcopy(payload['singles'][0][field]['sssplus'])
            with self.assertRaises(ValueError):validate_public_approved_ratings(payload)

    def test_final_components_keep_centers_and_maximum_uses_half_downward_offsets(self):
        from tests.test_centered_maximum_pumbility import half_downward_fixture_base
        result,_=apply_centered_maximum_pumbility(half_downward_fixture_base())
        validate_public_approved_ratings(result)
        groups={}
        for k in ('singles','doubles'):
            for r in result[k]:
                c=r['tierMetrics']['clearing']['estimatedDifficulty']
                for goal in ('general','sssplus'):
                    s=r['scoringVariants'][goal]['estimatedDifficulty'];p=r['pumbilityVariants'][goal]
                    self.assertEqual(p['estimatedDifficulty'],None if s is None or c is None else max(s,c)+p['centeredMaximumPumbility']['offset'])
                    if s is None or c is None:self.assertEqual(p['evidenceStatus'],'Unrated')
                    else:groups.setdefault((k,r['level'],goal),[]).append((max(s,c),p['estimatedDifficulty']))
        downward=0
        for (key,level,goal),values in groups.items():
            raw_median=median(a for a,_ in values)
            full_offset=level+.5-raw_median
            offset=full_offset*.5 if full_offset<0 else full_offset
            mode='Single' if key=='singles' else 'Double'
            marker=result['summary']['method']['centeredMaximumPumbility']['folders'][mode][str(level)][goal]
            self.assertAlmostEqual(marker['fullOffset'],full_offset)
            self.assertAlmostEqual(marker['offset'],offset)
            self.assertAlmostEqual(median(b for _,b in values),raw_median+offset)
            downward+=full_offset<0
        self.assertGreater(downward,0)

    def test_compact_and_browser_values_keep_the_production_policy(self):
        public=_public_copy(self.result);compact=compact_tier_payload(public);browser=tier_browser_payload(compact)
        validate_public_approved_ratings(compact);validate_public_approved_ratings(browser)
        self.assertIn('centeredMaximumPumbility',browser['summary']['method'])
        self.assertNotIn('centeredMaximumPumbilityBaseline',json.dumps(public))

    def test_public_rejects_changed_centers_components_policy_and_cap_defaults(self):
        for mutate in (
            lambda p:p['singles'][0]['pumbilityVariants']['sssplus'].update(estimatedDifficulty=99),
            lambda p:p['summary']['method']['centeredMaximumPumbility'].update(localOnly=True),
            lambda p:p['summary']['method']['centeredMaximumPumbility'].update(downwardAdjustmentScale=1),
            lambda p:p['summary']['method']['centeredMaximumPumbility'].update(upwardAdjustmentScale=.5),
            lambda p:p['summary']['method']['centeredMaximumPumbility'].update(method='folder-centered-maximum-pumbility-v1'),
            lambda p:next(iter(next(iter(p['summary']['method']['centeredMaximumPumbility']['folders'].values())).values()))['general'].update(fullOffset=99),
            lambda p:p['doubles'][0]['scoringVariants']['sssplus'].update(estimatedDifficulty=99),
        ):
            p=copy.deepcopy(self.result);mutate(p)
            with self.assertRaises(ValueError):validate_public_approved_ratings(p)


if __name__=='__main__':unittest.main()
