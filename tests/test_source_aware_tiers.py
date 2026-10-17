import copy
import json
import unittest
from pathlib import Path
from source_aware_tiers import apply_source_aware_tiers,recover_source_aware_tiers,validate_source_aware_tiers
from sss_scoring import payload_sha256

class SourceAwareTierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        f=json.loads((Path(__file__).parent/'fixtures/source-aware-width.json').read_text(encoding='utf-8'))
        cls.base=f['base'];cls.candidate=f['candidate']

    def test_recovery_is_exact_and_components_are_means(self):
        self.assertEqual(validate_source_aware_tiers(self.candidate,self.base),self.base)
        self.assertEqual(payload_sha256(recover_source_aware_tiers(self.candidate)),payload_sha256(self.base))

    def test_exact_recovery_retains_numeric_representation(self):
        a=copy.deepcopy(self.base);b=copy.deepcopy(a)
        a['doubles'][0]['bpmMin']=100.0;b['doubles'][0]['bpmMin']=100
        c=apply_source_aware_tiers(a,b,self.candidate['summary']['method']['sourceAwareTiers']['sourcePolicy'])
        self.assertEqual(payload_sha256(recover_source_aware_tiers(c)),payload_sha256(a))

    def test_point_and_pumbility_tampering_are_rejected(self):
        for component in ['scoringVariants','pumbilityVariants']:
            p=copy.deepcopy(self.candidate)
            row=next(r for r in p['doubles'] if r['scoringVariants']['sssplus'].get('finalFolderWidth'))
            row[component]['sssplus']['estimatedDifficulty']+=.1
            with self.assertRaises(ValueError):validate_source_aware_tiers(p,self.base)

if __name__=='__main__':unittest.main()
