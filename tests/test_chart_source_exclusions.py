import copy
import unittest
import tempfile
from pathlib import Path
from chart_source_exclusions import ExclusionPolicy
from scoring_percentile import collect_percentile_scores
from cross_level_clearing import prepare_clearing_evidence

CID='22ee894e-8601-4b55-9d04-c31187f8fa22'
CAT=[{'id':CID,'type':'Double','level':26,'noteCount':100}]
CONFIG={'version':1,'rules':[{'source':'Phoenix1','chartId':CID,'mode':'Double','scope':'tier-analysis-performance','enabled':True,'reason':'Changed chart','label':'Mopemope D26'}]}

class SourceExclusionTests(unittest.TestCase):
    def test_aliases_and_filter_preserves_input(self):
        p=ExclusionPolicy(CONFIG,CAT);snapshot={'scores':[{'chartId':CID,'playerId':'a','score':999000}], 'charts':CAT}
        original=copy.deepcopy(snapshot)
        for source in ['Phoenix','Phoenix1','phoenix1']:self.assertTrue(p.excludes(source,CID))
        self.assertFalse(p.excludes('Phoenix2',CID))
        self.assertEqual(p.filter_snapshot(snapshot,'Phoenix')['scores'],[])
        self.assertEqual(snapshot,original)

    def test_reason_does_not_invalidate_numeric_identity(self):
        c=copy.deepcopy(CONFIG);c['rules'][0]['reason']='New explanation'
        a,b=ExclusionPolicy(CONFIG,CAT),ExclusionPolicy(c,CAT)
        self.assertEqual(a.semantic_hash(),b.semantic_hash());self.assertNotEqual(a.config_hash,b.config_hash)
        self.assertEqual(a.semantic_hash(set()),ExclusionPolicy({'version':1,'rules':[]},CAT).semantic_hash())

    def test_invalid_and_duplicate_and_wrong_mode(self):
        for mutate in [lambda c:c['rules'].append(c['rules'][0]),lambda c:c['rules'][0].update(chartId='bad'),
                lambda c:c['rules'][0].update(mode='Single'),lambda c:c['rules'][0].update(source='Other')]:
            c=copy.deepcopy(CONFIG);mutate(c)
            with self.assertRaises(ValueError):ExclusionPolicy(c,CAT)

    def test_submitted_lower_p2_survives_and_no_p2_is_missing(self):
        p=ExclusionPolicy(CONFIG,CAT)
        first={'charts':CAT,'scores':[{'chartId':CID,'playerId':'a','score':999000,'isBroken':False}]}
        second={'charts':CAT,'scores':[{'chartId':CID,'playerId':'a','score':980000,'isBroken':False}]}
        self.assertEqual(collect_percentile_scores(first,second,exclusions=p),{('a',CID):('phoenix2',980000.)})
        second['scores']=[]
        self.assertEqual(collect_percentile_scores(first,second,exclusions=p),{})
        evidence=prepare_clearing_evidence(first,second,CAT,exclusions=p)
        self.assertEqual(evidence['charts'][CID]['clearCount'],0)
        self.assertEqual(evidence['diagnostics']['phoenix1']['chartSourceExcluded'],1)

    def test_disabled_rule_restores_source(self):
        c=copy.deepcopy(CONFIG);c['rules'][0]['enabled']=False
        self.assertFalse(ExclusionPolicy(c,CAT).excludes('Phoenix',CID))

    def test_official_merge_filters_before_max_and_preserves_raw_capture(self):
        from tests.test_official_scoring_sources import research,row,chart,A,B
        from official_scoring_sources import import_research_scoring_sources
        from official_score_merge import merge_official_scores
        from sss_scoring import prepare_target_observations
        catalog=[chart(A,mode='Double',level=26),chart(B,mode='Double',level=25)]
        config=copy.deepcopy(CONFIG);config['rules'][0]['chartId']=A
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            research(root,old_catalog=catalog,new_catalog=catalog,
                old_rows={A:[row(score=999000),row(2,'ONLY#2222',998000,2)],B:[row(score=970000)]},
                new_rows={A:[row(99,score=980000)],B:[row(99,score=960000)]})
            manifest=import_research_scoring_sources(root,root/'manifest.json')
            untouched=copy.deepcopy(manifest)
            original=merge_official_scores(manifest)
            policy=ExclusionPolicy(config,catalog)
            filtered=merge_official_scores(manifest,exclusions=policy)
            self.assertEqual(list(filtered['boards'][A].values()),[980000])
            self.assertEqual(filtered['boards'][B],original['boards'][B])
            self.assertEqual(filtered['charts'][A]['capturedSourceStates']['Phoenix']['rawRowCount'],2)
            self.assertEqual(filtered['charts'][A]['sourceStates']['Phoenix']['rawRowCount'],0)
            self.assertEqual(filtered['charts'][A]['sourceStates']['Phoenix']['maxRank'],0)
            self.assertEqual(manifest,untouched)
            prepared=prepare_target_observations({'charts':catalog,'scores':[]},{'charts':catalog,'scores':[]},filtered,catalog)
            self.assertNotIn(A,prepared['official']['sourceBoards']['Phoenix'])
            self.assertEqual(len(prepared['official']['sourceBoards']['Phoenix2'][A]),1)
            restored=copy.deepcopy(config);restored['rules'][0]['enabled']=False
            self.assertEqual(merge_official_scores(manifest,exclusions=ExclusionPolicy(restored,catalog))['boards'],original['boards'])

if __name__=='__main__':unittest.main()
