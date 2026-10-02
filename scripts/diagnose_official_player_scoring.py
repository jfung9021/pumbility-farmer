"""Write private personal lists plus aggregate holdout diagnostics; no network."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from official_player_scoring import fit_player_comparisons
from official_tiers import _board_entries


def holdout_report(boards, fit, seed):
    """Same unseen scores for both methods, with identical player-bias adjustment.

    The top-30% comparator is the former score signal, adjusted to each player's
    other scores so the comparison does not trivially reward having a baseline.
    This diagnostic concerns prediction on observed scores, not unplayed charts.
    """
    rng = np.random.default_rng(seed)
    histories = defaultdict(dict)
    for cid, samples in fit['personal'].items():
        for player in samples:
            histories[player][cid] = boards[cid][player]
    train = copy.deepcopy(boards)
    candidates = sorted(histories)
    rng.shuffle(candidates)
    holdouts = []
    for player in candidates[:40]:
        charts = sorted(histories[player])
        cid = charts[int(rng.integers(len(charts)))]
        if len(train[cid]) < 2:
            continue
        holdouts.append((player, cid, train[cid].pop(player)))
    trained = fit_player_comparisons(train, bootstrap_samples=0)
    if trained['componentCount'] != 1:
        return {'count': 0, 'reason': 'Training comparisons are disconnected.'}
    effects = {c: r['scoringMeanGap'] for c,r in trained['charts'].items()}
    old_means = {c: float(np.mean(sorted(s.values(),reverse=True)[:(len(s)*3+9)//10]))
                 for c,s in train.items() if s}
    errors = []
    for player, cid, actual in holdouts:
        others = [(c,s[player]) for c,s in train.items() if c != cid and player in s]
        if effects.get(cid) is None or any(effects.get(c) is None for c,_ in others) or not others:
            continue
        predicted = np.mean([score + effects[c] for c,score in others]) - effects[cid]
        old_prediction = old_means[cid] + np.mean([score-old_means[c] for c,score in others])
        errors.append((float(predicted-actual),float(old_prediction-actual)))
    if not errors:
        return {'count':0,'reason':'No supported holdouts.'}
    e=np.asarray(errors)
    return {'count':len(e), 'playerGapMae':float(np.mean(np.abs(e[:,0]))),
            'top30WithPlayerAdjustmentMae':float(np.mean(np.abs(e[:,1]))),
            'playerGapRmse':float(np.sqrt(np.mean(e[:,0]**2))),
            'top30WithPlayerAdjustmentRmse':float(np.sqrt(np.mean(e[:,1]**2)))}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'.local-data/piu-scores/official/player-scoring-diagnostics')
    args=parser.parse_args()
    payload=json.loads((ROOT/'.local-data/piu-scores/combined/analysis/web_results.json').read_text(encoding='utf-8'))
    snapshot=json.loads((ROOT/'.local-data/piu-scores/official/phoenix2/current.json').read_text(encoding='utf-8'))
    entries={b['chartId']:_board_entries(b) for b in snapshot['boards']}
    folders=defaultdict(list)
    for row in payload['singles']+payload['doubles']:
        if 'officialEvidence' in row:
            folders[(row['type'],row['level'])].append(row)
    summary={}
    personal=defaultdict(list)
    for index,(key,rows) in enumerate(sorted(folders.items())):
        boards={r['chartId']:entries.get(r['chartId'],{}) for r in rows}
        fit=fit_player_comparisons(boards,seed_key=f'{key[0]}:{key[1]}',bootstrap_samples=0)
        for row in rows:
            samples=fit['personal'][row['chartId']]
            values=[]
            for player,item in samples.items():
                difficulty=key[1]+.5+row['scoringDifficultyScale']*item['gap']/10000
                values.append(difficulty)
                personal[player].append({'chartId':row['chartId'],'songName':row['songName'],
                    'mode':key[0], 'officialLevel':key[1], **item, 'personalDifficulty':difficulty})
            if values:
                # Published coefficients and diagnostics are rounded to six decimals.
                assert abs(np.mean(values)-row['estimatedDifficulty']) < .00003, row['chartId']
            else:
                assert row['estimatedDifficulty'] is None
        label=('S' if key[0]=='Single' else 'D')+str(key[1])
        rated=[r for r in rows if r['estimatedDifficulty'] is not None]
        summary[label]={'charts':len(rows),'rated':len(rated),'eligiblePlayers':fit['eligiblePlayers'],
                        'minimumOtherCharts':fit['minimumOtherCharts'], 'components':fit['componentCount'],
                        'medianContributors':float(np.median([r['scoringPlayerCount'] for r in rows])),
                        'range':[min(r['estimatedDifficulty'] for r in rated),max(r['estimatedDifficulty'] for r in rated)] if rated else None,
                        'holdout':holdout_report(boards,fit,100+index)}
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/'personal-lists.private.json').write_text(json.dumps(personal,indent=2),encoding='utf-8')
    (args.output_dir/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
