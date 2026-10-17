from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from combined_official_clearing import (apply_combined_official_clearing,
    recover_combined_official_clearing, validate_combined_official_clearing)
from official_high_level_clearing import (COMBINED_METHOD, COMBINED_CAP_POLICY, P1_OBSERVED_CAP_POLICY, apply_combined_official_high_level_clearing,
    apply_official_high_level_clearing)
from official_score_merge import merge_official_scores
from official_scoring_sources import import_research_scoring_sources
from tests.test_official_high_level_clearing import payload as simple_payload
from tests.test_official_scoring_sources import research, row as score_row


def chart(index, level=26, notes=1000, mode="Double"):
    return {"id": f"00000000-0000-0000-0000-{index:012d}", "chartId": f"00000000-0000-0000-0000-{index:012d}",
            "type": mode, "level": level, "noteCount": notes, "songName": str(index)}


def merged_fixture(*, old_count=1, new_count=1, overlap=True, target_level=26, changed=False, unknown=False):
    target = chart(1, target_level)
    catalog = [target]+[chart(index+2,23+index%3) for index in range(25)]
    old, new = {}, {}
    for index, item in enumerate(catalog):
        old_players = [score_row(i+1,f"P{i}#1234",score=980000,place=i+1) for i in range(old_count)]
        new_players = [score_row(i+10001,f"P{i if overlap else old_count+i}#1234",score=990000,place=i+1) for i in range(new_count)]
        # Split each player's history across versions: neither source alone has25.
        old[item["id"]] = old_players if index==0 or index<=12 else []
        new[item["id"]] = new_players if index==0 or index>12 else []
    old_catalog = copy.deepcopy(catalog)
    if changed or unknown:
        old_catalog[0]["noteCount"] = None if unknown else 900
        # An old-only clearer makes use of that changed chart unsafe.
        old[target["id"]].append(score_row(99999,"OLDONLY#1234",score=985000,place=old_count+1))
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)/"research"
        research(root,old_rows=old,new_rows=new,old_catalog=old_catalog,new_catalog=catalog)
        merged=merge_official_scores(import_research_scoring_sources(root,Path(temp)/"manifest.json"))
    return target,merged


def overlay_base(targets):
    """Minimal test input; end-to-end historical fixture is generated separately."""
    base={"schemaVersion":54,"summary":{"method":{"officialHighLevelClearing":{"historical":True}}},
          "singles":[],"doubles":[],"coop":[{"preserved":True}]}
    for target in targets:
        level=target["level"]
        scoring={"estimatedDifficulty":level+.7,"evidenceStatus":"Published","nContributors":35,
            "scoringUnratedReason":None,"difficultyCi95Low":level+.6,"difficultyCi95High":level+.8,
            "initialFit":{"estimatedDifficulty":level+.9,"difficultyCi95Low":level+.7,"difficultyCi95High":level+1.1}}
        clearing={"estimatedDifficulty":level+.5,"evidenceStatus":"Published","ratedClearCount":20,
                  "initialFit":{"estimatedDifficulty":level+.5}}
        composite={"estimatedDifficulty":level+.6,"evidenceStatus":"Published","aggregation":"average",
                   "initialFit":{"estimatedDifficulty":level+.7},"maximumBaseline":{"preserved":True}}
        base["doubles"].append({**target,"chartId":target["id"],"nContributors":35,
            "scoringVariants":{key:copy.deepcopy(scoring) for key in ("general","sss","sssplus")},
            "tierMetrics":{"clearing":clearing,"pumbility":copy.deepcopy(composite)},
            "pumbilityVariants":{key:copy.deepcopy(composite) for key in ("general","sss","sssplus")}})
    return base


def historical_stage_fixture(*, cap_policy=COMBINED_CAP_POLICY, phoenix1_capped=False):
    """Real schema chain with synthetic shared, old-only and new-only clearers."""
    import tests.test_sss_scoring as general_tests
    from tests.test_sssplus_tiers import stage_fixture
    from refined_tiers import _general_composite
    from sss_scoring import _composite, rank_variants
    from sss_folder_centering import apply_folder_centering
    from sssplus_median_calibration import apply_median_calibration
    from sssplus_spread import apply_sssplus_spread
    from tier_postprocessing import _rank_secondary

    general,prepared,fits=general_tests.fixture()
    targets=[chart(index+1) for index in range(5)]
    template=copy.deepcopy(next(r for r in general['doubles'] if r['level']==22))
    for index,target in enumerate(targets):
        item=copy.deepcopy(template);item.update(target,chartId=target['id'],level=26,songName=f'Synthetic D26 {index}')
        for metric in (item,item['initialFit']):
            metric['estimatedDifficulty']+=4
        for metric in (item['tierMetrics']['clearing'],item['tierMetrics']['clearing']['initialFit'],item['tierMetrics']['pumbility']):
            metric['estimatedDifficulty']+=4
        general['doubles'].append(item)
        prepared['catalog'][target['id']]=target
        prepared['official']['charts'][target['id']]={'anchorEligible':True,'comparability':'same','provisional':False}
        estimate=copy.deepcopy(fits['official']['Double']['charts']['Double-22-0'])
        estimate.update(effect=49+index*.02,effectLow=48.9+index*.02,effectHigh=49.1+index*.02)
        fits['official']['Double']['charts'][target['id']]=estimate
    catalog=copy.deepcopy(targets);old={};new={}
    for index,target in enumerate(targets):
        shared_old=[score_row(index*100+p+1,f'GROUP{index}PLAYER{p}#1234',970000,p+1) for p in range(10)]
        shared_new=[score_row(index*100+p+10001,f'GROUP{index}PLAYER{p}#1234',985000,p+1) for p in range(10)]
        old_only=score_row(50000+index,f'OLDONLY{index}#1234',975000,11)
        new_only=score_row(60000+index,f'NEWONLY{index}#1234',980000,11)
        old[target['id']]=shared_old+[old_only];new[target['id']]=shared_new+[new_only]
        if phoenix1_capped:
            old[target['id']].extend(score_row(70000+index*100+p,f'EXTRA{index}PLAYER{p}#1234',970000,p+12) for p in range(89))
        for offset in range(25):
            history=chart(1000+index*25+offset,23+index)
            catalog.append(history)
            old[history['id']]=(shared_old if offset<12 else [])+[old_only]
            new[history['id']]=(shared_new if offset>=12 else [])+[new_only]
    with tempfile.TemporaryDirectory() as temp:
        directory=Path(temp)/'research'
        research(directory,old_rows=old,new_rows=new,old_catalog=catalog,new_catalog=catalog)
        manifest=import_research_scoring_sources(directory,Path(temp)/'manifest.json')
        merged=merge_official_scores(manifest)
        legacy=manifest['_captures']['Phoenix2']
    prepared['manifestSha256']=merged['manifestSha256']

    def center_with_legacy(refined):
        for item in refined['doubles']:
            if item['level']>=26:
                item['refinementBaseline'].update(clearing=copy.deepcopy(item['tierMetrics']['clearing']),
                    generalPumbility=copy.deepcopy(item['tierMetrics']['pumbility']))
        refined['summary']['method']['officialHighLevelClearing']=apply_official_high_level_clearing(refined,legacy)
        for key in ('singles','doubles'):
            for item in refined[key]:
                if key=='doubles' and item['level']>=26:_general_composite(item)
                item['pumbilityVariants']['sss']=_composite(item,item['scoringVariants']['sss'])
            _rank_secondary(refined[key],'clearing');_rank_secondary(refined[key],'pumbility')
            for item in refined[key]:item['pumbilityVariants']['general']=copy.deepcopy(item['tierMetrics']['pumbility'])
            rank_variants(refined[key])
        return apply_folder_centering(refined)
    with patch.object(general_tests,'fixture',return_value=(general,prepared,fits)),patch('sss_folder_centering.apply_folder_centering',side_effect=center_with_legacy):
        _,stage52=stage_fixture()
    stage53=apply_median_calibration(stage52,policy_version=3)
    stage54=apply_sssplus_spread(stage53,rare_adjustments={},rare_policy={'fixture':True})
    candidate,report=apply_combined_official_clearing(stage54,merged,cap_policy=cap_policy)
    return stage54,candidate,report


class CombinedOfficialClearingTests(unittest.TestCase):
    def test_phoenix1_cap_is_diagnostic_and_does_not_disable_interval(self):
        target,merged=merged_fixture(old_count=100,new_count=6)
        frozen=copy.deepcopy(merged)
        result,_=apply_combined_official_clearing(overlay_base([target]),merged,
            bootstrap_samples=1000,cap_policy=P1_OBSERVED_CAP_POLICY)
        metric=result['doubles'][0]['tierMetrics']['clearing']
        self.assertEqual(merged,frozen)
        self.assertFalse(metric['defaultedAtCap'])
        self.assertTrue(metric['clearingEvidence']['possiblyTruncated'])
        self.assertTrue(metric['clearingEvidence']['sourceStates']['Phoenix']['possiblyTruncated'])
        self.assertIsNotNone(metric['q20SkillCi95Low'])
        self.assertIsNotNone(metric['difficultyCi95Low'])
        self.assertIsNone(metric['capDefaultDifficulty'])
        self.assertEqual(metric['evidenceStatus'],'Provisional')
        self.assertEqual(recover_combined_official_clearing(result),overlay_base([target]))

    def test_phoenix2_cap_still_triggers_default_under_new_policy(self):
        target,merged=merged_fixture(old_count=6,new_count=300)
        result,_=apply_combined_official_clearing(overlay_base([target]),merged,
            bootstrap_samples=1000,cap_policy=P1_OBSERVED_CAP_POLICY)
        metric=result['doubles'][0]['tierMetrics']['clearing']
        self.assertTrue(metric['defaultedAtCap'])
        self.assertIsNone(metric['difficultyCi95Low'])
        self.assertEqual(metric['estimatedDifficulty'],26.5)

    def test_ignored_phoenix1_flag_does_not_change_numeric_estimates(self):
        target,merged=merged_fixture(old_count=100,new_count=6)
        unflagged=copy.deepcopy(merged)
        for meta in unflagged['charts'].values():meta['sourceStates']['Phoenix']['possiblyTruncated']=False
        first,_=apply_combined_official_clearing(overlay_base([target]),merged,cap_policy=P1_OBSERVED_CAP_POLICY)
        second,_=apply_combined_official_clearing(overlay_base([target]),unflagged,cap_policy=P1_OBSERVED_CAP_POLICY)
        a=first['doubles'][0]['tierMetrics']['clearing'];b=second['doubles'][0]['tierMetrics']['clearing']
        for field in ('q20Skill','q20SkillCi95Low','estimatedDifficulty','difficultyCi95Low','spreadCalibration','recenteringShift'):
            self.assertEqual(a[field],b[field])

    def test_invalid_cap_policy_is_rejected(self):
        target,merged=merged_fixture()
        with self.assertRaises(ValueError):
            apply_combined_official_clearing(overlay_base([target]),merged,cap_policy='unknown')

    def test_union_histories_exact_tag_match_dedup_and_higher_score(self):
        target,merged=merged_fixture()
        self.assertEqual(len(merged["boards"][target["id"]]),1)
        self.assertEqual(list(merged["boards"][target["id"]].values()),[990000])
        value=simple_payload([target]); frozen=copy.deepcopy(merged)
        policy=apply_combined_official_high_level_clearing(value,merged,bootstrap_samples=0)
        metric=value["doubles"][0]["tierMetrics"]["clearing"]
        self.assertEqual(metric["clearCount"],1)
        self.assertEqual(metric["ratedClearCount"],1)
        self.assertAlmostEqual(metric["q20Skill"],sum(23.5+i%3 for i in range(25))/25)
        self.assertEqual(metric["method"],COMBINED_METHOD)
        self.assertEqual(policy["historyBoards"],26)
        self.assertEqual(merged,frozen)
        self.assertEqual(metric["clearingEvidence"]["sourceDates"],merged["sourceDates"])
        self.assertEqual(metric["clearingEvidence"]["rawRowCount"],2)
        self.assertNotIn("P0#1234",json.dumps(value))

    def test_union_cardinality_above300_does_not_invent_source_cap(self):
        target,merged=merged_fixture(old_count=99,new_count=250,overlap=False)
        value=simple_payload([target])
        apply_combined_official_high_level_clearing(value,merged,bootstrap_samples=0)
        metric=value["doubles"][0]["tierMetrics"]["clearing"]
        self.assertEqual(metric["clearCount"],349)
        self.assertFalse(metric["defaultedAtCap"])
        self.assertFalse(metric["clearingEvidence"]["possiblyTruncated"])

    def test_old_source_cap_alone_triggers_cap_default(self):
        target,merged=merged_fixture(old_count=100,new_count=1)
        value=simple_payload([target])
        policy=apply_combined_official_high_level_clearing(value,merged,bootstrap_samples=0)
        metric=value["doubles"][0]["tierMetrics"]["clearing"]
        self.assertTrue(metric["defaultedAtCap"])
        self.assertTrue(metric["clearingEvidence"]["sourceStates"]["Phoenix"]["possiblyTruncated"])
        self.assertFalse(metric["clearingEvidence"]["sourceStates"]["Phoenix2"]["possiblyTruncated"])
        self.assertEqual(metric["estimatedDifficulty"],26.5)
        self.assertIsNone(metric["difficultyCi95Low"])
        self.assertEqual(policy["capPolicy"],"any-source-possibly-truncated-v1")

    def test_note_count_changes_do_not_erase_official_clears_or_histories(self):
        for state in ('changed','unknown'):
            with self.subTest(state=state):
                target,merged=merged_fixture(changed=state=='changed',unknown=state=='unknown')
                value=simple_payload([target])
                policy=apply_combined_official_high_level_clearing(value,merged,bootstrap_samples=0)
                metric=value["doubles"][0]["tierMetrics"]["clearing"]
                self.assertEqual(metric["clearCount"],2)
                self.assertEqual(metric["ratedClearCount"],1)
                self.assertIsNotNone(metric["estimatedDifficulty"])
                self.assertIsNone(metric["unratedReason"])
                self.assertEqual(metric["clearingEvidence"]["comparability"],state)
                self.assertEqual(metric['clearingEvidence']['comparabilityPolicy'],'current-chart-id-and-mode')
                self.assertIn('cross-version-note-count-'+state,metric['provisionalReasons'])
                self.assertIn(target["id"],policy["historyComparabilityDiagnostics"][state])

    def test_scope_and_exact_recovery_all_means_and_once_compression(self):
        target,merged=merged_fixture()
        base=overlay_base([target,chart(999,25)])
        frozen=copy.deepcopy(base)
        result,report=apply_combined_official_clearing(base,merged,bootstrap_samples=0)
        self.assertEqual(base,frozen)
        self.assertEqual(recover_combined_official_clearing(result),base)
        self.assertEqual(validate_combined_official_clearing(result,base),report)
        self.assertEqual(result["doubles"][1]["scoringVariants"],base["doubles"][1]["scoringVariants"])
        self.assertEqual(result["coop"],base["coop"])
        row=result["doubles"][0]
        clearing=row["tierMetrics"]["clearing"]
        self.assertEqual(clearing["estimatedDifficulty"],26.5)
        self.assertEqual(clearing["initialFit"]["estimatedDifficulty"],26.5)
        for goal in ("general","sss","sssplus"):
            self.assertEqual(row["scoringVariants"][goal],base["doubles"][0]["scoringVariants"][goal])
            self.assertAlmostEqual(row["pumbilityVariants"][goal]["estimatedDifficulty"],26.6)
            self.assertEqual(row["pumbilityVariants"][goal]["clearingSupportCount"],1)
        for mutate in (lambda p:p["doubles"][0]["pumbilityVariants"]["sssplus"].update(estimatedDifficulty=99),
                       lambda p:p["doubles"][0]["tierMetrics"]["clearing"].update(defaultedAtCap=True),
                       lambda p:p["doubles"][1]["tierMetrics"]["clearing"].update(estimatedDifficulty=99)):
            bad=copy.deepcopy(result);mutate(bad)
            with self.assertRaises(ValueError):validate_combined_official_clearing(bad)

    def test_note_count_changes_do_not_erase_valid_capped_boards(self):
        target,merged=merged_fixture(old_count=100,new_count=1,changed=True)
        result,_=apply_combined_official_clearing(overlay_base([target]),merged,bootstrap_samples=0)
        metric=result['doubles'][0]['tierMetrics']['clearing']
        self.assertEqual(metric['estimatedDifficulty'],26.5)
        self.assertTrue(metric['defaultedAtCap'])
        self.assertTrue(metric['clearingEvidence']['possiblyTruncated'])

    def test_current_catalog_mismatch_and_invalid_merge_rejected(self):
        target,merged=merged_fixture()
        bad=copy.deepcopy(merged);bad["sourceDates"]={"Phoenix2":"2026-10-11T00:00:00Z"}
        with self.assertRaises(ValueError):apply_combined_official_high_level_clearing(simple_payload([target]),bad)
        with self.assertRaises(ValueError):apply_combined_official_high_level_clearing(simple_payload([target]),merged,[{**target,"level":27}])
        with self.assertRaises(ValueError):apply_combined_official_high_level_clearing(simple_payload([{**target,"level":27}]),merged)


if __name__=="__main__":unittest.main()
