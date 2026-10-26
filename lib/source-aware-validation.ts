import { createHash } from "node:crypto";
import { compressDifficulty, compressionFactor } from "./tier-postprocessing.ts";
import { validateRanks } from "./sss-analysis-validation.ts";
import { targetTailRating } from "./sssplus-analysis-validation.ts";
import type { AnalysisPayload } from "./types";

type Obj=Record<string,any>;
const NS="sourceAwareTiers";
export const FINAL_WIDTH_POLICY={version:1,method:"final-side-q90-median-pair-width-floor-v1",targetDistance:.4,quantile:.9,
  asinhScale:.75,minimumCharts:4,minimumPositiveSpread:1e-8,expansionOnly:true,compressionApplications:1,
  intervals:"conditional-fixed-width-location-and-selection-no-new-evidence"};
const fields=["estimatedDifficulty","difficultyCi95Low","difficultyCi95High"];
function need(v:unknown,message:string):asserts v {if(!v)throw new Error(message);}
function finite(v:unknown):v is number{return typeof v==="number"&&Number.isFinite(v);}
function same(a:any,b:any):boolean {
  if(typeof b==="number")return finite(a)&&Math.abs(a-b)<1e-8;
  if(a===b)return true;
  if(Array.isArray(a)&&Array.isArray(b))return a.length===b.length&&a.every((v,i)=>same(v,b[i]));
  if(a&&b&&typeof a==="object"&&typeof b==="object")return Object.keys(a).length===Object.keys(b).length&&Object.keys(b).every(k=>k in a&&same(a[k],b[k]));
  return false;
}
function quantile(values:number[],q:number){const x=values.toSorted((a,b)=>a-b),p=q*(x.length-1),i=Math.floor(p);return x[i]+(p-i)*(x[Math.ceil(p)]-x[i]);}
function canonical(v:any):string {return Array.isArray(v)?`[${v.map(canonical).join(",")}]`:v&&typeof v==="object"?`{${Object.keys(v).sort().map(k=>`${JSON.stringify(k)}:${canonical(v[k])}`).join(",")}}`:JSON.stringify(v);}

export function finalWidthParameters(points:Obj[],level:number):Obj {
  const usable=points.filter(p=>finite(p.value)&&!p.capDefault),values=usable.map(p=>p.value).toSorted((a,b)=>a-b);
  const p:Obj={level,midpoint:level+.5,populationChartIds:usable.map(x=>String(x.id)).sort(),ratedCharts:values.length,
    basis:"unavailable",locationShift:0,medianHalfGap:0,lowerReference:null,upperReference:null,provisional:true,policy:FINAL_WIDTH_POLICY};
  if(values.length<4||values.at(-1)!-values[0]<=1e-8)return p;
  const center=quantile(values,.5),i=Math.floor(values.length/2);
  Object.assign(p,{basis:"folder",locationShift:level+.5-center,medianHalfGap:values.length%2?0:(values[i]-values[i-1])/2,provisional:values.length<5});
  for(const [side,key] of [[-1,"lowerReference"],[1,"upperReference"]] as const){const distances=values.filter(v=>side*(v-center)>1e-12).map(v=>Math.abs(v-center));p[key]=distances.length?quantile(distances,.9):null;}
  return p;
}

export function finalWidthRating(value:number|null,p:Obj):number|null {
  if(value===null)return null;need(finite(value),"Nonfinite final width input.");
  const d=value+p.locationShift-p.midpoint,b=p.medianHalfGap,ref=d<0?p.lowerReference:p.upperReference;
  let z=Math.abs(d);
  if(p.basis==="folder"&&ref!==null&&ref<.4&&.4>b&&ref-b>1e-8&&z>b)z=Math.max(z,b+.75*Math.asinh((z-b)/(ref-b)*Math.sinh((.4-b)/.75)));
  return p.midpoint+(d<0?-z:z);
}

export function recoverSourceAwareBaseline(payload:AnalysisPayload):AnalysisPayload {
  need(payload.schemaVersion===56,"Source-aware recovery requires schema 56.");
  const p=structuredClone(payload) as unknown as Obj,saved=p.sourceAwareBaseline;
  need(saved?.summary&&typeof saved.generatedAtUtc==="string","Missing source-aware baseline.");
  p.summary=saved.summary;p.generatedAtUtc=saved.generatedAtUtc;p.schemaVersion=55;delete p.sourceAwareBaseline;
  for(const key of ["singles","doubles"])for(const row of p[key]){
    const s=row.sourceAwareBaseline;need(s&&s.changed&&Array.isArray(s.added),"Missing chart recovery stage.");
    delete row.sourceAwareBaseline;for(const k of s.added)delete row[k];Object.assign(row,s.changed);
  }
  return p as AnalysisPayload;
}

export function validateSourceAwarePayload(payload:AnalysisPayload,validateBase:(p:AnalysisPayload)=>unknown):AnalysisPayload {
  const base=recoverSourceAwareBaseline(payload);validateBase(base);
  const p=payload as unknown as Obj,m=p.summary.method[NS],source=m?.sourcePolicy;
  need(m?.version===1&&m.localOnly===true&&m.baseSchemaVersion===55&&m.compressionApplications===1&&m.dataFetches===0
    &&/^[a-f0-9]{64}$/.test(m.basePayloadSha256)&&/^[a-f0-9]{64}$/.test(m.correctedPayloadSha256)
    &&same(m.widthPolicy,FINAL_WIDTH_POLICY),"Source-aware policy differs.");
  need(source?.version===1&&Array.isArray(source.rules)&&/^[a-f0-9]{64}$/.test(source.configSha256)
    &&/^[a-f0-9]{64}$/.test(source.manifestSha256)&&/^[a-f0-9]{64}$/.test(source.effectiveInputSha256),"Invalid source-exclusion identity.");
  const semantic={version:1,rules:source.rules.filter((r:Obj)=>r.enabled).map((r:Obj)=>Object.fromEntries(["source","chartId","mode","scope"].map(k=>[k,r[k]])))};
  need(createHash("sha256").update(canonical(semantic)).digest("hex")===source.effectivePolicySha256,"Source exclusion semantic hash differs.");
  need(source.targetFits.length===8,"Missing target-fit provenance.");
  need(new Set(source.targetFits.map((f:Obj)=>`${f.goal}:${f.source}:${f.mode}`)).size===8,"Duplicate target-fit provenance.");
  for(const fit of source.targetFits)need(["sss","sssplus"].includes(fit.goal)&&["official","submitted"].includes(fit.source)
    &&["Single","Double"].includes(fit.mode)&&fit.identity.target===(fit.goal==="sssplus"?995000:990000)
    &&fit.identity.samples===200&&/^[a-f0-9]{64}$/.test(fit.identity.boards)&&/^[a-f0-9]{64}$/.test(fit.identity.sourceBoards),"Stale target fit identity.");
  const groups=new Map<string,Obj[]>();
  for(const [mode,key] of [["Single","singles"],["Double","doubles"]]){
    for(const row of p[key]){
      for(const kind of ["sssplus","clearing"]){
        const metric=kind==="clearing"?row.tierMetrics.clearing:row.scoringVariants.sssplus,w=metric.finalFolderWidth;
        const applicable=row.level>=(kind==="clearing"?26:20)&&(kind!=="clearing"||mode==="Double")&&metric.estimatedDifficulty!==null;
        need(Boolean(w)===applicable,"Missing or out-of-scope final width.");if(!w)continue;
        need(w.group===`${mode}:${kind}:${row.level}`&&w.initialCompressionApplications===1,"Width scope or compression count differs.");
        const list=groups.get(w.group)??[];list.push({id:row.chartId,value:w.input.estimatedDifficulty,capDefault:Boolean(metric.defaultedAtCap),row,metric,w});groups.set(w.group,list);
        for(const f of fields){
          let expected=finalWidthRating(w.input[f],w.parameters);
          if(kind==="sssplus"&&!w.exceptionPreserved)expected=targetTailRating(expected,row.level,false);
          need(same(metric[f],expected),"Final width points or intervals differ.");
          if(kind==="clearing"){
            const initial=metric.initialFit?.[f],once=initial==null?null:compressDifficulty(initial,row.level,compressionFactor(payload));
            need(same(w.input[f],once),"Clearing input was not compressed exactly once.");
          }else if(!w.exceptionPreserved){
            const spreadKey=f==="estimatedDifficulty"?"spreadDifficulty":f==="difficultyCi95Low"?"spreadDifficultyCi95Low":"spreadDifficultyCi95High";
            need(same(w.input[f],metric.scoringSpreadPolicy?.[spreadKey]),"SSS+ width is detached from its refitted pre-tail projection.");
          }
        }
        const d=metric.estimatedDifficulty-row.level-.5;need(same(metric.difficultyDelta,d),"Width delta differs.");
        if(metric.difficultyCi95Low!==null&&metric.difficultyCi95High!==null)need(metric.difficultyCi95Low<=metric.difficultyCi95High,"Width interval order differs.");
      }
      for(const goal of ["general","sss","sssplus"]){
        const s=row.scoringVariants[goal],c=row.tierMetrics.clearing,v=s.estimatedDifficulty===null||c.estimatedDifficulty===null?null:(s.estimatedDifficulty+c.estimatedDifficulty)/2;
        if(goal!=="general")need(s.scoringTarget===(goal==="sssplus"?995000:990000)&&s.scoringSource===(row.level>=20?"official":"submitted"),"Scoring target or source boundary differs.");
        need(same(row.pumbilityVariants[goal].estimatedDifficulty,v),"Pumbility is not the final component mean.");
      }
      need(same(row.tierMetrics.pumbility,row.pumbilityVariants.general),"General Pumbility compatibility differs.");
      const exclusions=source.excludedCharts[row.chartId];
      if(exclusions){
        need(same(row.chartSourceExclusions,exclusions),"Chart source-exclusion evidence differs.");
        for(const rule of exclusions.rules){
          need(rule.enabled&&rule.scope==="tier-analysis-performance"&&rule.mode===mode,"Invalid effective source rule.");
          const state=exclusions.effectiveSourceStates[rule.source];
          need(state.rawRowCount===0&&!state.possiblyTruncated,"Excluded source retains effective observations or cap.");
          for(const goal of ["sss","sssplus"]){const s=row.scoringVariants[goal];if(s.scoringSource==="official")need(s.scoringSourceStates[rule.source].rawRowCount===0,"Excluded source entered target fit evidence.");}
          need(row.officialScoringEvidence.sourceStates[rule.source].rawRowCount===0,"Excluded source entered General scoring.");
          if(row.tierMetrics.clearing.clearingEvidence?.mix==="Phoenix+Phoenix2")need(row.tierMetrics.clearing.clearingEvidence.sourceStates[rule.source].rawRowCount===0,"Excluded source entered Clearing.");
        }
      }
    }
    for(const goal of ["sss","sssplus"])validateRanks(p[key].map((row:Obj)=>({...row,scoringVariants:{sss:row.scoringVariants[goal]},pumbilityVariants:{sss:row.pumbilityVariants[goal]}})));
  }
  need(groups.size===Object.keys(m.folders).length,"Final width folder population differs.");
  for(const [name,points] of groups){
    const params=finalWidthParameters(points,points[0].row.level);
    need(same(m.folders[name],params)&&points.every(x=>same(x.w.parameters,params)),"Final width calibration population differs.");
    const ordered=points.toSorted((a,b)=>a.value-b.value);need(ordered.every((x,i)=>!i||ordered[i-1].metric.estimatedDifficulty<=x.metric.estimatedDifficulty),"Final width inverted chart order.");
    if(params.basis==="folder")need(Math.abs(quantile(points.filter(x=>!x.capDefault).map(x=>x.metric.estimatedDifficulty),.5)-params.midpoint)<1e-8,"Final folder median differs.");
  }
  const official=[...p.singles,...p.doubles].filter((r:Obj)=>r.level>=20&&r.scoringVariants.sssplus.estimatedDifficulty!==null);
  need(official.filter((r:Obj)=>Math.abs(r.scoringVariants.sssplus.estimatedDifficulty-r.level-.5)>=2).length<=10
    &&official.every((r:Obj)=>Math.abs(r.scoringVariants.sssplus.estimatedDifficulty-r.level-.5)<=2.5),"Final width exceeded the tail budget.");
  need(same(payload.coop,base.coop),"Source-aware tiers changed Co-op.");
  return payload;
}
