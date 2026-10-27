import copy
import json
import math
import unittest
from pathlib import Path
from statistics import median
from unittest.mock import patch

from approved_tier_ratings import validate_public_approved_ratings
from extended_official_clearing import apply_extended_official_clearing, validate_extended_official_clearing
from observed_capped_clearing import apply_observed_capped_clearing, validate_observed_capped_clearing
from scoped_tier_width import apply_scoped_tier_width, validate_scoped_tier_width
from final_folder_centering import apply_final_folder_centering, validate_final_folder_centering
from centered_maximum_pumbility import apply_centered_maximum_pumbility, validate_centered_maximum_pumbility
from production_tiers import _public_copy
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

    def test_scoped_width_is_one_and_lower_folders_preserve_the_previous_width(self):
        before,after=self.stages[1:3]
        for k in ('singles','doubles'):
            for a,b in zip(before[k],after[k]):
                ratio=4/3 if a['level']>=(24 if k=='singles' else 25) else 1
                midpoint=a['level']+.5
                for goal in ('general','sssplus'):
                    x,y=a['scoringVariants'][goal]['estimatedDifficulty'],b['scoringVariants'][goal]['estimatedDifficulty']
                    if x is not None:self.assertAlmostEqual(y,midpoint+ratio*(x-midpoint))

    def test_final_components_and_maximum_are_centered_and_nulls_remain_unrated(self):
        validate_public_approved_ratings(self.result)
        for k in ('singles','doubles'):
            for r in self.result[k]:
                c=r['tierMetrics']['clearing']['estimatedDifficulty']
                for goal in ('general','sssplus'):
                    s=r['scoringVariants'][goal]['estimatedDifficulty'];p=r['pumbilityVariants'][goal]
                    self.assertEqual(p['estimatedDifficulty'],None if s is None or c is None else max(s,c)+p['centeredMaximumPumbility']['offset'])
                    if s is None or c is None:self.assertEqual(p['evidenceStatus'],'Unrated')

    def test_compact_and_browser_values_keep_the_production_policy(self):
        public=_public_copy(self.result);compact=compact_tier_payload(public);browser=tier_browser_payload(compact)
        validate_public_approved_ratings(compact);validate_public_approved_ratings(browser)
        self.assertIn('centeredMaximumPumbility',browser['summary']['method'])
        self.assertNotIn('centeredMaximumPumbilityBaseline',json.dumps(public))

    def test_public_rejects_changed_centers_components_policy_and_cap_defaults(self):
        for mutate in (
            lambda p:p['singles'][0]['pumbilityVariants']['sssplus'].update(estimatedDifficulty=99),
            lambda p:p['summary']['method']['centeredMaximumPumbility'].update(localOnly=True),
            lambda p:p['doubles'][0]['scoringVariants']['sssplus'].update(estimatedDifficulty=99),
        ):
            p=copy.deepcopy(self.result);mutate(p)
            with self.assertRaises(ValueError):validate_public_approved_ratings(p)


if __name__=='__main__':unittest.main()
