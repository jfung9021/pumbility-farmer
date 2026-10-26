"""Recoverable schema-56 source-corrected components and final-unit width overlay."""
from __future__ import annotations
import copy
import math
import json
from collections import defaultdict
from statistics import median
from datetime import datetime,timezone
from typing import Any,Mapping

from combined_official_clearing import _refresh_general,_rank_pumbility
from final_folder_width import POLICY,width_parameters,width_value
from sss_scoring import MODES,_composite,_derived,payload_sha256,rank_variants
from target_tail_policy import tail_value
from tier_postprocessing import _rank_secondary

NAMESPACE='sourceAwareTiers'
SCHEMA=56
FIELDS=('estimatedDifficulty','difficultyCi95Low','difficultyCi95High')
HISTORICAL={'refinementBaseline','sssCenteringBaseline','sssplusCenteringBaseline','sssplusMedianBaseline',
    'sssplusRefinementBaseline','sssplusSpreadBaseline','sssplusTailBaseline','combinedOfficialClearingBaseline'}


def _update(metric:dict,level:int):
    metric.update(_derived(metric['estimatedDifficulty'],level))
    for side in ['Low','High']:
        if f'difficultyCi95{side}' in metric:
            v=metric[f'difficultyCi95{side}'];metric[f'difficultyDeltaCi95{side}']=None if v is None else v-level-.5


def apply_source_aware_tiers(original:Mapping[str,Any],corrected:Mapping[str,Any],provenance:Mapping[str,Any]) -> dict:
    from retained_tiers import scoring_goals
    goals=scoring_goals(corrected)
    if original.get('schemaVersion')!=55 or corrected.get('schemaVersion')!=55:
        raise ValueError('Source-aware tiers require original and corrected schema-55 generations.')
    # Completed historical stages are immutable. Copy the active components below
    # without duplicating their large rollback trees before final validation.
    result=dict(original)
    for _,key in MODES:
        result[key]=[dict(row) for row in original[key]]
    result['summary']=copy.deepcopy(corrected['summary'])
    result['generatedAtUtc']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    result['summary']['generatedAtUtc']=result['generatedAtUtc']
    result['schemaVersion']=SCHEMA
    populations={};excluded=provenance.get('excludedCharts',{})
    for mode,key in MODES:
        updates={r['chartId']:r for r in corrected[key]}
        if set(updates)!={r['chartId'] for r in original[key]}:raise ValueError('Source correction changed the catalog.')
        for row in result[key]:
            current=updates[row['chartId']]
            for field in set(row)|set(current):
                if field in HISTORICAL:continue
                if field in current:row[field]=copy.deepcopy(current[field])
                else:row.pop(field,None)
            if row['chartId'] in excluded:row['chartSourceExclusions']=copy.deepcopy(excluded[row['chartId']])
        for metric_name in ['sssplus','clearing']:
            if metric_name=='clearing' and mode!='Double':continue
            groups=defaultdict(list)
            for row in result[key]:
                if row['level']<(26 if metric_name=='clearing' else 20):continue
                metric=row['tierMetrics']['clearing'] if metric_name=='clearing' else row['scoringVariants']['sssplus']
                if metric['estimatedDifficulty'] is not None:groups[row['level']].append(row)
            for level,rows in sorted(groups.items()):
                def component(r):return r['tierMetrics']['clearing'] if metric_name=='clearing' else r['scoringVariants']['sssplus']
                def before(r):
                    m=component(r)
                    # Exceptions already have their separate supported distance policy.
                    return m['estimatedDifficulty'] if metric_name=='clearing' or m.get('scoringTailPolicy',{}).get('exceptionSelected') else m.get('scoringTailPolicy',{}).get('preTailDifficulty',m['estimatedDifficulty'])
                points=[{'id':r['chartId'],'value':before(r),'capDefault':component(r).get('defaultedAtCap',False)} for r in rows]
                params=width_parameters(points,level);groupkey=f'{mode}:{metric_name}:{level}';populations[groupkey]=params
                for row in rows:
                    m=component(row);tail=m.get('scoringTailPolicy',{});exception=bool(tail.get('exceptionSelected'))
                    snapshot={f:m.get(f) for f in FIELDS}
                    if metric_name=='sssplus' and not exception:
                        snapshot['estimatedDifficulty']=tail.get('preTailDifficulty',m['estimatedDifficulty'])
                        snapshot['difficultyCi95Low']=tail.get('preTailDifficultyCi95Low',m.get('difficultyCi95Low'))
                        snapshot['difficultyCi95High']=tail.get('preTailDifficultyCi95High',m.get('difficultyCi95High'))
                    for f in FIELDS:
                        value=width_value(snapshot[f],params)
                        m[f]=value if metric_name=='clearing' or exception else tail_value(value,level)
                    m['finalFolderWidth']={'group':groupkey,'input':snapshot,'parameters':params,
                        'exceptionPreserved':exception,'initialCompressionApplications':1}
                    if params['provisional'] and m['evidenceStatus']=='Published':m['evidenceStatus']='Provisional'
                    if params['provisional']:
                        reasonkey='provisionalReasons' if metric_name=='clearing' else 'scoringProvisionalReasons'
                        m[reasonkey]=sorted(set(m.get(reasonkey,[]))|{'provisional-local-folder-width'})
                    if m.get('difficultyCi95Low') is not None:m['intervalMethod']=POLICY['intervals']
                    _update(m,level)
                    if metric_name=='clearing' and m.get('defaultedAtCap'):
                        m['capDefaultDifficulty']=m['estimatedDifficulty']
                    if metric_name=='sssplus':
                        for field,suffix in [('estimatedDifficulty','Difficulty'),('difficultyCi95Low','DifficultyCi95Low'),('difficultyCi95High','DifficultyCi95High')]:
                            tail['preTail'+suffix]=width_value(snapshot[field],params)
                            tail['final'+suffix]=m[field]
                        tail['ordinaryDifficulty']=tail_value(tail['preTailDifficulty'],level)
                if metric_name=='sssplus':
                    vals=[component(r)['estimatedDifficulty'] for r in rows]
                    for row in rows:component(row)['scoringTailPolicy']['finalFolderRange']={'lower':min(vals),'upper':max(vals)}
        for row in result[key]:
            _refresh_general(row)
            for goal in goals[1:]:
                row['pumbilityVariants'][goal]=_composite(row,row['scoringVariants'][goal])
                row['pumbilityVariants'][goal]['intervalMethod']='conditional-fixed-source-aware-components-and-width-v1'
        for goal in goals[1:]:rank_variants(result[key],goal=goal)
        for goal in goals:_rank_pumbility(result[key],goal)
        _rank_secondary(result[key],'clearing')
        for row in result[key]:row['tierMetrics']['pumbility']=copy.deepcopy(row['pumbilityVariants']['general'])
    scored=[r for _,key in MODES for r in result[key] if r['level']>=20 and r['scoringVariants']['sssplus']['estimatedDifficulty'] is not None]
    after=[r['scoringVariants']['sssplus']['estimatedDifficulty']-r['level']-.5 for r in scored]
    active_tail=result['summary']['method']['sssplusTailPolicy']
    active_tail['counts'].update(finalAbsAbove1_5=sum(abs(d)>1.5 for d in after),finalAbsAtLeast2=sum(abs(d)>=2 for d in after),
        finalAbsAbove2_5=sum(abs(d)>2.5 for d in after),finalMinDelta=min(after),finalMaxDelta=max(after),
        displayedAbsAtLeast2=sum(abs(math.floor((r['scoringVariants']['sssplus']['estimatedDifficulty']+1e-10)*10)/10-r['level']-.5)>=2 for r in scored))
    if active_tail['counts']['finalAbsAtLeast2']>10 or active_tail['counts']['displayedAbsAtLeast2']>10 or active_tail['counts']['finalAbsAbove2_5']:
        raise ValueError('Final width exceeds the global tail budget.')
    result['summary']['method'][NAMESPACE]={'version':1,'localOnly':True,'baseSchemaVersion':55,
        'basePayloadSha256':payload_sha256(original),'correctedPayloadSha256':payload_sha256(corrected),
        'sourcePolicy':copy.deepcopy(provenance),'widthPolicy':copy.deepcopy(POLICY),'folders':populations,
        'dataFetches':0,'compressionApplications':1,'stageOrder':['source-filter','dependent-refits',
            'existing-calibration-and-single-midpoint-compression','sparse-location-and-final-width-floor',
            'single-tail-policy','component-means-and-ranks']}
    # Retain only changed top-level fields; historical stages remain the original recoverable generation.
    for _,key in MODES:
        for row,old in zip(result[key],original[key]):
            changed={k:copy.deepcopy(v) for k,v in old.items() if k not in row or
                json.dumps(row[k],sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
                !=json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)}
            added=sorted(set(row)-set(old))
            row['sourceAwareBaseline']={'changed':changed,'added':added}
    result['sourceAwareBaseline']={'summary':copy.deepcopy(original['summary']),'generatedAtUtc':original['generatedAtUtc']}
    validate_source_aware_tiers(result,original)
    return result


def _recovery_view(payload:Mapping[str,Any]) -> dict:
    if payload.get('schemaVersion')!=SCHEMA:raise ValueError('Source-aware recovery requires schema 56.')
    base=dict(payload);saved=base.pop('sourceAwareBaseline');base.update(saved);base['schemaVersion']=55
    for _,key in MODES:
        base[key]=[dict(row) for row in payload[key]]
        for row in base[key]:
            stage=row.pop('sourceAwareBaseline')
            for k in stage['added']:row.pop(k,None)
            row.update(stage['changed'])
    return base


def recover_source_aware_tiers(payload:Mapping[str,Any]) -> dict:
    return copy.deepcopy(_recovery_view(payload))


def validate_source_aware_tiers(payload:Mapping[str,Any],original:Mapping[str,Any]|None=None):
    from retained_tiers import scoring_goals
    goals=scoring_goals(payload)
    base=_recovery_view(payload);method=payload['summary']['method'][NAMESPACE]
    if method['widthPolicy']!=POLICY or method['compressionApplications']!=1 or method['dataFetches']!=0:
        raise ValueError('Source-aware width policy differs.')
    if payload_sha256(base)!=method['basePayloadSha256'] or (original is not None and base!=original):
        raise ValueError('Source-aware baseline recovery differs.')
    for _,key in MODES:
        for row in payload[key]:
            for metric in [row['scoringVariants']['sssplus'],row['tierMetrics']['clearing']]:
                w=metric.get('finalFolderWidth')
                if not w:continue
                if w['parameters']!=method['folders'][w['group']]:raise ValueError('Width parameters differ.')
                for field in FIELDS:
                    expected=width_value(w['input'][field],w['parameters'])
                    if ':sssplus:' in w['group'] and not w['exceptionPreserved']:expected=tail_value(expected,row['level'])
                    actual=metric.get(field)
                    if actual!=expected:raise ValueError('Final width points or intervals differ.')
            for goal in goals:
                s=row['scoringVariants'][goal]['estimatedDifficulty'];c=row['tierMetrics']['clearing']['estimatedDifficulty']
                if row['pumbilityVariants'][goal]['estimatedDifficulty']!=(None if s is None or c is None else (s+c)/2):
                    raise ValueError('Source-aware Pumbility is not the full-precision mean.')
    for name,params in method['folders'].items():
        mode,metric,level=name.split(':');rows=payload['singles' if mode=='Single' else 'doubles']
        vals=[(r['tierMetrics']['clearing'] if metric=='clearing' else r['scoringVariants']['sssplus'])['estimatedDifficulty'] for r in rows if r['chartId'] in params['populationChartIds']]
        if params['basis']=='folder' and not math.isclose(median(vals),int(level)+.5,abs_tol=1e-8):
            raise ValueError('Final folder median moved.')
    return base
