"""Build the local source-exclusion and final-width generation from cached data."""
from __future__ import annotations
import argparse
import copy
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.build_local_configurable_fits import inputs,PRIVATE
from scripts.build_local_sss_tiers import _write
from sss_scoring import MODES,payload_sha256,apply_sss_variants


def general_identity(first,second,old,catalog,policy,merged):
    return {'version':1,'first':payload_sha256(first),'second':payload_sha256(second),
        'baseline':payload_sha256(old),'catalog':payload_sha256(catalog),
        'effectiveOfficial':merged['effectiveInputSha256'],'sourcePolicy':policy.semantic_hash()}


def rebuild_general():
    from scoring_percentile import build_percentile_tier_payload
    from official_cross_level_scoring import apply_cross_level_scoring
    from cross_level_clearing import apply_cross_level_clearing
    from hybrid_tiers import refresh_hybrid_summary
    from tier_postprocessing import initial_fit_payload,finalize_tiers
    from pumbility_average import apply_average_pumbility
    first,second,old,catalog,policy,merged,prepared=inputs()
    identity=general_identity(first,second,old,catalog,policy,merged)
    cache=PRIVATE/'general-inputs.json'
    if cache.exists() and json.loads(cache.read_text(encoding='utf-8'))==identity and (PRIVATE/'general-baseline.json').exists():
        print(json.dumps({'stage':'General-reused','refits':0}),flush=True)
        return
    raw=initial_fit_payload(old)
    records=raw['singles']+raw['doubles']+raw['coop']
    print(json.dumps({'stage':'submitted-General-profile','sourcePolicy':policy.public()}),flush=True)
    submitted=build_percentile_tier_payload(records,{'modes':{}},first,second)
    replacement={r['chartId']:r for r in submitted['doubles'] if r['level']<20}
    raw['doubles']=[replacement.get(r['chartId'],r) for r in raw['doubles']]
    for field in ['scoring','scoreProfileCalibration','confidenceIntervals']:
        raw['summary']['method'][field]=copy.deepcopy(submitted['summary']['method'][field])
    print(json.dumps({'stage':'official-General-Double-refit','bootstraps':1000}),flush=True)
    result=apply_cross_level_scoring(raw,merged,catalog,retain_submitted_below20=True,modes_to_fit={'Double'})
    print(json.dumps({'stage':'submitted-Clearing-histories'}),flush=True)
    result=apply_cross_level_clearing(result,first,second,catalog,hybrid_lower_tiers=True)
    refresh_hybrid_summary(result)
    result=apply_average_pumbility(finalize_tiers(result))
    # No Singles performance input changed. Keep its completed numeric fit exactly.
    if prepared['official']['boards']:
        assert all(a['chartId']==b['chartId'] for a,b in zip(old['singles'],result['singles']))
        result['singles']=copy.deepcopy(old['singles'])
    _write(PRIVATE/'general-baseline.json',result)
    _write(cache,identity)
    print(json.dumps({'stage':'General-complete','schema':result['schemaVersion']}),flush=True)


def finish():
    from refined_tiers import apply_refined_tiers
    from sss_folder_centering import apply_folder_centering
    from sssplus_tiers import apply_sssplus_tiers
    from sssplus_median_calibration import apply_median_calibration
    from sssplus_rare_continuous import build_rare_adjustments
    from sssplus_spread import apply_sssplus_spread
    from combined_official_clearing import apply_combined_official_clearing
    from official_high_level_clearing import P1_OBSERVED_CAP_POLICY
    from official_scoring_sources import load_official_scoring_manifest
    from source_aware_tiers import apply_source_aware_tiers
    first,second,old,catalog,policy,merged,prepared=inputs()
    base=json.loads((PRIVATE/'general-baseline.json').read_text(encoding='utf-8'))
    source_hashes={'phoenix1':payload_sha256(first),'phoenix2':payload_sha256(second),
        'currentCatalog':payload_sha256(catalog),'officialManifest':merged['manifestSha256']}
    allfits={}
    fit_provenance=[]
    for goal in ['sss','sssplus']:
        allfits[goal]={source:{} for source in ['submitted','official']}
        for source,minimum in [('official',20),('submitted',16)]:
            for mode,_ in MODES:
                cached=json.loads((PRIVATE/goal/f'{source}-{mode.lower()}-fit.json').read_text(encoding='utf-8'))
                boards={cid:b for cid,b in prepared[source]['boards'].items() if prepared['catalog'][cid]['type']==mode and prepared['catalog'][cid]['level']>=minimum}
                sb={name:{cid:b for cid,b in entries.items() if cid in boards} for name,entries in prepared[source]['sourceBoards'].items()}
                if cached['identity']['boards']!=payload_sha256(boards) or cached['identity']['sourceBoards']!=payload_sha256(sb):raise ValueError('Cached target fit has stale source-exclusion inputs.')
                if cached['fit']['policy']['scoreTarget']!=(995000 if goal=='sssplus' else 990000):raise ValueError('Cached fit has the wrong score target.')
                allfits[goal][source][mode]=cached['fit']
                fit_provenance.append({'goal':goal,'source':source,'mode':mode,'identity':cached['identity'],'reusedOriginal':cached['reusedOriginal']})
    print(json.dumps({'stage':'target-projection'}),flush=True)
    s49=apply_sss_variants(base,prepared,allfits['sss'],source_hashes=source_hashes,bootstrap_samples=200,seed=20261018)
    manifest=load_official_scoring_manifest(ROOT/'.local-data/piu-scores/official/scoring/manifest.json')
    s50,_=apply_refined_tiers(s49,prepared,allfits['sss'],manifest['_captures']['Phoenix2'])
    del s49
    s51=apply_folder_centering(s50);del s50
    s52,_=apply_sssplus_tiers(s51,prepared,allfits['sssplus'],source_hashes=source_hashes);del s51
    s53=apply_median_calibration(s52,policy_version=3);del s52
    boards={mode:{cid:b for cid,b in merged['boards'].items() if cid in {str(r['chartId']) for r in s53[key] if r['level']>=20}} for mode,key in MODES}
    adjustments,rare=build_rare_adjustments(s53,boards,allfits['sssplus']['official'])
    print(json.dumps({'stage':'continuous-rare-and-spread','rareCharts':len(adjustments)}),flush=True)
    s54=apply_sssplus_spread(s53,rare_adjustments=adjustments,rare_policy=rare);del s53
    s55,_=apply_combined_official_clearing(s54,merged,bootstrap_samples=1000,cap_policy=P1_OBSERVED_CAP_POLICY);del s54
    _write(PRIVATE/'corrected-schema55.json',s55)
    original=json.loads((ROOT/'.local-data/piu-scores/combined/experiments/sssplus-spread-p1-observed-clearing/web_results.json').read_text(encoding='utf-8'))
    exclusions={cid:{'rules':meta['sourceExclusions'],'capturedSourceStates':meta['capturedSourceStates'],
        'effectiveSourceStates':meta['sourceStates']} for cid,meta in merged['charts'].items() if meta.get('sourceExclusions')}
    provenance={**policy.public(),'manifestSha256':merged['manifestSha256'],'effectiveInputSha256':merged['effectiveInputSha256'],
        'sourceDates':merged['sourceDates'],'excludedCharts':exclusions,'targetFits':fit_provenance,
        'correctedSourceHashes':source_hashes}
    candidate=apply_source_aware_tiers(original,s55,provenance)
    output=ROOT/'.local-data/piu-scores/combined/experiments/source-aware-width/web_results.json'
    _write(output,candidate)
    _write(PRIVATE/'provenance.json',provenance)
    print(json.dumps({'stage':'candidate-complete','schema':candidate['schemaVersion'],'output':str(output)}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--stage',choices=['general','finish','all'],default='all')
    args=parser.parse_args()
    if args.stage=='all':
        from scripts.build_local_configurable_fits import build_fits
        rebuild_general()
        for goal in ['sss','sssplus']:build_fits(goal)
        finish()
    else:(rebuild_general if args.stage=='general' else finish)()
