"""Refit only source-rule-dependent target cohorts from existing private caches."""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chart_source_exclusions import load_exclusions
from cross_level_clearing import current_clearing_catalog
from official_score_merge import merge_official_scores
from official_scoring_sources import load_official_scoring_manifest
from scripts.build_local_recommendations import _read_snapshot
from scripts.build_local_sss_tiers import _write
from scripts.build_local_sss_tiers import training_general_predictor as sss_predictor
from scripts.build_local_sssplus_tiers import training_general_predictor as plus_predictor
from sss_scoring import MODES,payload_sha256,prepare_target_observations
from sss_scoring_model import fit_target_model

PRIVATE=ROOT/'.local-data/configurable-tier-experiment'


def inputs():
    first,second=_read_snapshot('phoenix1'),_read_snapshot('phoenix2')
    general=json.loads((ROOT/'.local-data/sss-scoring-experiment/general-baseline.json').read_text(encoding='utf-8'))
    catalog=current_clearing_catalog(general,second)
    policy=load_exclusions(catalog)
    merged=merge_official_scores(load_official_scoring_manifest(ROOT/'.local-data/piu-scores/official/scoring/manifest.json'),exclusions=policy)
    first=policy.filter_snapshot(first,'phoenix1');second=policy.filter_snapshot(second,'phoenix2')
    prepared=prepare_target_observations(first,second,merged,catalog)
    return first,second,general,catalog,policy,merged,prepared


def build_fits(goal):
    start=time.monotonic()
    first,second,general,catalog,policy,merged,prepared=inputs()
    target=995000 if goal=='sssplus' else 990000
    olddir=ROOT/('.local-data/sssplus-experiment' if goal=='sssplus' else '.local-data/sss-scoring-experiment')
    report=[]
    for source,minimum in [('official',20),('submitted',16)]:
        for mode,_ in MODES:
            boards={cid:b for cid,b in prepared[source]['boards'].items() if prepared['catalog'][cid]['type']==mode and prepared['catalog'][cid]['level']>=minimum}
            source_boards={name:{cid:b for cid,b in entries.items() if cid in boards} for name,entries in prepared[source]['sourceBoards'].items()}
            identity={'boards':payload_sha256(boards),'sourceBoards':payload_sha256(source_boards),'target':target,'samples':200,'seed':20261018}
            out=PRIVATE/goal/f'{source}-{mode.lower()}-fit.json'
            cached=json.loads(out.read_text(encoding='utf-8')) if out.exists() else None
            old=json.loads((olddir/out.name).read_text(encoding='utf-8'))
            reuse_old=(old['identity']['boards']==identity['boards'] and old['identity']['sourceBoards']==identity['sourceBoards'])
            print(json.dumps({'stage':'target-fit','goal':goal,'source':source,'mode':mode,'reuseOriginal':reuse_old,'reuseNew':bool(cached and cached.get('identity')==identity),'seconds':round(time.monotonic()-start)}),flush=True)
            if cached and cached.get('identity')==identity:fit=cached['fit'];reused=True
            elif reuse_old:fit=old['fit'];reused=True
            else:
                fit=fit_target_model(boards,score_target=target,bootstrap_samples=200,seed=20261018,source_boards=source_boards,
                    general_predictor=(plus_predictor if goal=='sssplus' else sss_predictor) if source=='official' else None)
                if source=='official':fit['diagnostics']['generalBenchmarkPolicy']={'method':'training-only-General-additive-score-cost','logisticScoreScale':10000,'scaleSelection':'fixed-before-holdout','heldoutRecordsUsedInFit':False}
                else:fit['diagnostics']['validation']['generalTargetBenchmarkReason']='Submitted General uses score profiles rather than additive score costs; no equivalent training-only target predictor.'
                reused=False
            _write(out,{'identity':identity,'fit':fit,'sourcePolicy':policy.public(),'reusedOriginal':reuse_old})
            report.append({'source':source,'mode':mode,'reused':reused,'identity':identity,'bootstrap':fit['diagnostics']['bootstrap']})
            print(json.dumps({'stage':'target-complete','goal':goal,'source':source,'mode':mode,'seconds':round(time.monotonic()-start),'bootstrap':fit['diagnostics']['bootstrap']}),flush=True)
    _write(PRIVATE/goal/'fit-report.json',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--goal',choices=['sss','sssplus'],required=True)
    build_fits(parser.parse_args().goal)
