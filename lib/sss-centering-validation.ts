import { checkFit, validateRanks } from "./sss-analysis-validation.ts";
import { compressDifficulty } from "./tier-postprocessing.ts";
import type { AnalysisPayload, ScoringVariant, SssCenteringFolder } from "./types";

type Obj = Record<string, unknown>;
const RANKS=["levelRank","levelComparisonCharts","modeRank","levelPercentile","relativeGroup","relativeGroupRank"];
const STATUSES=["Unrated","Insufficient","Provisional","Published"];
function need(value:unknown,message:string):asserts value { if(!value) throw new Error(message); }
function object(value:unknown):Obj { need(value && typeof value==="object" && !Array.isArray(value),"Missing SSS centering object."); return value as Obj; }
function finite(value:unknown):value is number { return typeof value==="number" && Number.isFinite(value); }
function same(a:unknown,b:unknown):boolean {
  if(typeof b==="number") return finite(a) && Math.abs(a-b)<1e-8;
  if(a===b) return true;
  if(Array.isArray(a) && Array.isArray(b)) return a.length===b.length && a.every((v,i)=>same(v,b[i]));
  if(a && b && typeof a==="object" && typeof b==="object") {
    const aa=object(a), bb=object(b);
    return Object.keys(aa).length===Object.keys(bb).length && Object.keys(bb).every(k=>k in aa && same(aa[k],bb[k]));
  }
  return false;
}
function median(values:number[]):number { const v=values.toSorted((a,b)=>a-b), middle=Math.floor(v.length/2); return v.length%2?v[middle]:(v[middle-1]+v[middle])/2; }
function derived(value:number|null,level:number):Obj {
  const delta=value===null?null:value-level-.5;
  const rank=delta===null?null:delta<-.5?1:delta<-.3?2:delta<-.1?3:delta<=.1?4:delta<=.3?5:delta<=.5?6:7;
  return {estimatedDifficulty:value,difficultyDelta:delta,effectBandRank:rank,effectBand:rank===null?null:["Overrated","Very Easy","Easy","Medium","Hard","Very Hard","Underrated"][rank-1]};
}

export function sssCenteringFolders(base:AnalysisPayload, requireMixedFirstPass=true):Record<string,Record<string,SssCenteringFolder>> {
  const result:Record<string,Record<string,SssCenteringFolder>>={};
  for(const [mode,key] of [["Single","singles"],["Double","doubles"]] as const) {
    const folders:Record<string,SssCenteringFolder>={}, supported:Record<number,SssCenteringFolder>={};
    for(const level of [...new Set(base[key].filter(r=>r.level>=20).map(r=>r.level))].sort((a,b)=>a-b)) {
      const rows=base[key].filter(r=>r.level===level);
      const mixed=rows.filter(r=>{const f=r.scoringVariants!.sss!.scoringFirstPass!;return finite(f.estimatedDifficulty) && f.evidenceStatus!=="Unrated" && Number(f.scoringTargetSuccesses)>0 && Number(f.scoringBelowTarget)>0;});
      const population=rows.filter(r=>finite(r.scoringVariants!.sss!.estimatedDifficulty) && r.scoringVariants!.sss!.evidenceStatus!=="Unrated");
      const before=population.length?median(population.map(r=>r.scoringVariants!.sss!.estimatedDifficulty!)):null;
      const firstValues=mixed.map(r=>r.scoringVariants!.sss!.scoringFirstPass!.estimatedDifficulty!);
      const folder:SssCenteringFolder={source:"official",mode,level,basis:"unavailable",donorLevels:[],
        mixedFirstPassChartIds:mixed.map(r=>r.chartId).sort(),populationChartIds:population.map(r=>r.chartId).sort(),
        mixedFirstPassCount:mixed.length,ratedChartCount:population.length,medianBefore:before,medianAfter:before,
        finalShift:0,initialShift:0,donorPopulationChartIds:[],donorMixedChartIds:[]};
      if(population.length>=5 && (!requireMixedFirstPass || (mixed.length>=5 && Math.max(...firstValues)>Math.min(...firstValues)))) {
        Object.assign(folder,{basis:"folder",donorLevels:[level],finalShift:level+.5-before!});
        supported[level]=folder;
      }
      folders[String(level)]=folder;
    }
    for(const folder of Object.values(folders)) {
      if(folder.basis!=="folder") {
        const donors=Object.keys(supported).map(Number).sort((a,b)=>a-b), lower=donors.filter(l=>l<folder.level), upper=donors.filter(l=>l>folder.level);
        if(lower.length && upper.length) {
          const a=lower.at(-1)!,b=upper[0],ratio=(folder.level-a)/(b-a);
          Object.assign(folder,{basis:"interpolated",donorLevels:[a,b],finalShift:supported[a].finalShift+ratio*(supported[b].finalShift-supported[a].finalShift)});
        } else if(donors.length) {
          const donor=donors.toSorted((a,b)=>Math.abs(folder.level-a)-Math.abs(folder.level-b)||a-b)[0];
          Object.assign(folder,{basis:"nearest",donorLevels:[donor],finalShift:supported[donor].finalShift});
        }
      }
      Object.assign(folder,{initialShift:2*folder.finalShift,medianAfter:folder.medianBefore===null?null:folder.medianBefore+folder.finalShift,
        donorPopulationChartIds:[...new Set(folder.donorLevels.flatMap(l=>supported[l].populationChartIds))].sort(),
        donorMixedChartIds:[...new Set(folder.donorLevels.flatMap(l=>supported[l].mixedFirstPassChartIds))].sort()});
    }
    result[mode]=folders;
  }
  return result;
}

export function recoverUncenteredSss(payload:AnalysisPayload):AnalysisPayload {
  need(payload.schemaVersion===51,"SSS centering recovery requires schema 51.");
  const base=structuredClone(payload);base.schemaVersion=50;delete base.summary.method.sssFolderCentering;
  for(const key of ["singles","doubles"] as const) for(const row of base[key]) {
    const original=row.sssCenteringBaseline;need(original,"Missing uncentered SSS snapshot.");
    row.scoringVariants!.sss=original.scoring;row.pumbilityVariants!.sss=original.pumbility;delete row.sssCenteringBaseline;
  }
  return base;
}

export function expectedScoring(original:ScoringVariant,level:number,folder:SssCenteringFolder,frozen:unknown):Obj {
  const metric=structuredClone(original) as unknown as Obj, initial=object(metric.initialFit);
  const before=metric.estimatedDifficulty, rawBefore=initial.estimatedDifficulty;
  for(const field of ["estimatedDifficulty","difficultyCi95Low","difficultyCi95High"]) {
    const adjusted=initial[field]===null?null:Number(initial[field])+folder.initialShift;
    initial[field]=adjusted;metric[field]=compressDifficulty(adjusted,level);
    if(field!=="estimatedDifficulty") {
      initial[field.replace("difficulty","difficultyDelta")]=adjusted===null?null:adjusted-level-.5;
      metric[field.replace("difficulty","difficultyDelta")]=metric[field]===null?null:Number(metric[field])-level-.5;
    }
  }
  Object.assign(metric,derived(metric.estimatedDifficulty as number|null,level));Object.assign(initial,derived(initial.estimatedDifficulty as number|null,level));
  if(before!==null && ["nearest","interpolated"].includes(folder.basis)) {
    if(metric.evidenceStatus==="Published") metric.evidenceStatus="Provisional";
    metric.scoringProvisional=true;metric.scoringProvisionalReasons=[...new Set([...(metric.scoringProvisionalReasons as string[]),"borrowed-folder-centering"])].sort();
  }
  const range=frozen?object(frozen):null, rare=original.scoringRefinement?.rare;
  metric.scoringFolderCentering={version:1,...Object.fromEntries(["source","mode","level","basis","donorLevels","mixedFirstPassCount","ratedChartCount","medianBefore","medianAfter","finalShift","initialShift"].map(k=>[k,(folder as unknown as Obj)[k]])),
    uncenteredDifficulty:before,uncenteredInitialDifficulty:rawBefore,
    effectiveRange:range?{lower:Number(range.lower)+folder.finalShift,upper:Number(range.upper)+folder.finalShift}:null,
    effectiveRareBand:rare?{lower:rare.upperBandLow+folder.finalShift,upper:rare.upperBandHigh+folder.finalShift}:null};
  return metric;
}
function withoutRanks(metric:Obj):Obj {
  const value=structuredClone(metric);for(const k of RANKS){delete value[k];delete object(value.initialFit)[k];}return value;
}

export function validateCenteredAnalysisPayload(payload:AnalysisPayload,validateBase:(base:AnalysisPayload)=>unknown):AnalysisPayload {
  need(payload.schemaVersion===51,"Invalid centered SSS generation.");
  const base=recoverUncenteredSss(payload);validateBase(base);
  const method=object(payload.summary.method.sssFolderCentering), folders=sssCenteringFolders(base);
  need(/^[a-f0-9]{64}$/.test(String(method.basePayloadSha256)) && same(method,{version:1,method:"sss-final-folder-median-centering-v1",baseSchemaVersion:50,
    basePayloadSha256:method.basePayloadSha256,localOnly:true,source:"official",minimumLevel:20,minimumMixedFirstPassCharts:5,minimumFinalCharts:5,
    population:"all-finite-final-sss-excluding-unrated",reference:"current-official-level-plus-0.5",median:"middle-value-or-mean-of-middle-pair",
    borrow:"same-source-mode-offset-interpolation-or-nearest",noDonor:"preserve-point-zero-offset",
    processing:"frozen-second-pass-preview-then-initial-shift-then-single-compression",compressionFactor:.5,compressionApplications:1,
    intervals:"translate-existing-conditional-intervals-fixed-centering-no-new-intervals",pumbility:"mean-centered-sss-and-unchanged-clearing-no-composite-centering",folders}),"SSS centering policy, population or donors differ.");
  const frozenRanges=object(object(payload.summary.method.sssRefinement).ranges);
  for(const [mode,key] of [["Single","singles"],["Double","doubles"]] as const) {
    for(let i=0;i<payload[key].length;i++) {
      const row=payload[key][i],old=base[key][i],s=object(row.scoringVariants!.sss!),p=object(row.pumbilityVariants!.sss!),c=row.tierMetrics!.clearing;
      const folder=folders[mode][String(row.level)];
      const frozen=row.level>=20?object(object(frozenRanges.official)[mode])[String(row.level)]:undefined;
      const expected=row.level>=20?expectedScoring(old.scoringVariants!.sss!,row.level,folder,frozen):object(old.scoringVariants!.sss!);
      need(same(withoutRanks(s),withoutRanks(expected)),"Centered SSS point, interval, evidence, range or preserved model metadata differs.");
      checkFit(s,row.level);checkFit(object(s.initialFit),row.level);
      if(row.level<20) need(same(row.pumbilityVariants!.sss!,old.pumbilityVariants!.sss!),"Submitted SSS Pumbility changed.");
      const value=s.estimatedDifficulty===null || c.estimatedDifficulty===null?null:(Number(s.estimatedDifficulty)+c.estimatedDifficulty)/2;
      const rawS=object(s.initialFit),rawC=object(c.initialFit),rawP=object(p.initialFit);
      const rawValue=rawS.estimatedDifficulty===null || rawC.estimatedDifficulty===null?null:(Number(rawS.estimatedDifficulty)+Number(rawC.estimatedDifficulty))/2;
      const status=value===null?"Unrated":STATUSES[Math.min(STATUSES.indexOf(String(s.evidenceStatus)),STATUSES.indexOf(c.evidenceStatus))];
      need(same(p.estimatedDifficulty,value) && same(rawP.estimatedDifficulty,rawValue) && p.aggregation==="average" && p.evidenceStatus===status
        && p.scoringSupportCount===s.nContributors && p.clearingSupportCount===c.ratedClearCount && p.intervalMethod===old.pumbilityVariants!.sss!.intervalMethod,"Centered SSS Pumbility mean, support or evidence differs.");
      for(const side of ["Low","High"]) {
        const field=`difficultyCi95${side}`;
        need(same(p[field],value===null || s[field]===null?null:(Number(s[field])+c.estimatedDifficulty!)/2)
          && same(rawP[field],rawValue===null || rawS[field]===null?null:(Number(rawS[field])+Number(rawC.estimatedDifficulty))/2),"Centered SSS Pumbility interval differs.");
      }
      checkFit(p,row.level);checkFit(rawP,row.level);
    }
    validateRanks(payload[key] as unknown as Obj[]);
    for(const folder of Object.values(folders[mode])) if(folder.basis==="folder") {
      const points=payload[key].filter(r=>r.level===folder.level && r.scoringVariants!.sss!.estimatedDifficulty!==null).map(r=>r.scoringVariants!.sss!.estimatedDifficulty!);
      need(same(median(points),folder.level+.5),"Supported SSS folder median is not its midpoint.");
    }
  }
  return payload;
}
