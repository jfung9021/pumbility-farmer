import { checkFit, validateRanks } from "./sss-analysis-validation.ts";
import { validateSssTailPublicPayload } from "./sss-tail-validation.ts";
import { truncateEstimatedDifficulty } from "./format-difficulty.ts";
import type { AnalysisPayload, ChartResult, ScoringGoal } from "./types";

type Obj = Record<string, any>;
const NS = "level20ScoringWidth";
const METHOD = "level20-target-quantile-width-v1";
const SCRIPT = "6.24.0-level20-target-width";
const GOALS = ["ss", "ssplus", "sss", "sssplus"] as const;
const FIELDS = ["estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"] as const;
const RANKS = ["levelRank", "levelComparisonCharts", "modeRank", "levelPercentile", "relativeGroup", "relativeGroupRank"];
export const LEVEL20_WIDTH_POLICY = {
  version: 1, method: METHOD, level: 20, goals: [...GOALS], minimumCharts: 20,
  targetCoreFraction: .65, coreFractionRange: [.6, .7], coreQuantiles: [.175, .825],
  coreBoundaries: [20, 21], midpoint: 20.5, ordinaryLowerLimit: 18.7,
  supportedLowerLimit: 18.1, extremeLowerLimit: 18, deepTailLimit: 5, extremeTailLimit: 1,
  ordinaryUpperLimit: 22.4, exceptionUpperLimit: 23, minimumDeepContributors: 100,
  minimumExtremeContributors: 200, intervals: "conditional-fixed-level20-width-no-new-evidence-v1",
  source: "stored-full-precision-pre-final-tail-with-published-tie-blocks-preserved",
  ties: "whole-value-blocks-no-rank-jitter", dataFetches: 0, modelRefits: 0,
};
function need(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
function obj(value: unknown): Obj {
  need(value !== null && typeof value === "object" && !Array.isArray(value), "Missing level-20 width evidence.");
  return value as Obj;
}
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function same(a: any, b: any): boolean {
  if (typeof b === "number") return finite(a) && Math.abs(a - b) < 1e-8;
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => same(x, b[i]));
  return !!a && !!b && typeof a === "object" && typeof b === "object"
    && Object.keys(a).length === Object.keys(b).length && Object.keys(b).every(key => key in a && same(a[key], b[key]));
}
function median(values: number[]): number { const x = values.toSorted((a,b) => a-b), i = Math.floor(x.length/2); return x.length%2 ? x[i] : (x[i-1]+x[i])/2; }
function quantile(x: number[], p: number): number { const t = (x.length-1)*p, i = Math.floor(t); return x[i]+(t-i)*(x[Math.ceil(t)]-x[i]); }
function distribution(values: number[]) {
  const n = values.length, core = values.filter(x => truncateEstimatedDifficulty(x) >= 20 && truncateEstimatedDifficulty(x) < 21).length;
  return { ratedCharts: n, coreCharts: core, coreFraction: n ? core/n : null,
    deepTailCharts: values.filter(x => truncateEstimatedDifficulty(x) >= 18 && truncateEstimatedDifficulty(x) <= 18.6).length,
    extremeTailCharts: values.filter(x => truncateEstimatedDifficulty(x) === 18).length,
    minimum: n ? Math.min(...values) : null, maximum: n ? Math.max(...values) : null,
    median: n ? median(values) : null, distinctPoints: new Set(values).size };
}
function tail(value: number, x: number, y: number, slope: number, limit: number): number {
  const span = Math.abs(y-limit), ratio = Math.abs(value-x)*slope/span;
  return y + (limit > y ? 1 : -1)*span*(ratio/(1+ratio));
}
/** Replay the exact Python presentation curve with its frozen folder anchors. */
export function level20WidthValue(value: number | null, parameters: Obj): number | null {
  if (value === null) return null;
  need(finite(value), "Invalid level-20 width input.");
  if (parameters.basis !== "folder") return value;
  const knots = parameters.knots as Array<{input:number;output:number}>;
  if (value < knots[0].input) {
    const [a,b] = knots;
    let result = tail(value,a.input,a.output,(b.output-a.output)/(b.input-a.input),parameters.lowerLimit);
    for (const g of parameters.lowerGuards) if (value < g.input) result = tail(value,g.input,g.output,g.slope,g.limit);
    return result;
  }
  if (value > knots.at(-1)!.input) {
    const [a,b] = knots.slice(-2);
    let result=tail(value,b.input,b.output,(b.output-a.output)/(b.input-a.input),parameters.upperLimit);
    for(const g of parameters.upperGuards) if(value>g.input) result=tail(value,g.input,g.output,g.slope,g.limit);
    return result;
  }
  for (let i=1;i<knots.length;i++) if (value <= knots[i].input) {
    const a=knots[i-1],b=knots[i]; return a.output+(value-a.input)*(b.output-a.output)/(b.input-a.input);
  }
  throw new Error("Invalid level-20 curve knots.");
}
function expectedKnots(values: number[]) {
  const v=values.toSorted((a,b)=>a-b);
  if (v.length < 20) return {basis:"insufficient-charts",knots:[]};
  const low=quantile(v,.175),high=quantile(v,.825),ml=v[Math.floor((v.length-1)/2)],mh=v[Math.floor(v.length/2)];
  if (!(low<ml && ml<=mh && mh<high)) return {basis:"tied-core",knots:[]};
  const center=(ml+mh)/2,h=(mh-ml)/2,gap=Math.min(.5*h/(center-low),.5*h/(high-center));
  const pairs=[[low,20],[ml,20.5-gap],[mh,20.5+gap],[high,21]];
  return {basis:"folder",knots:pairs.filter((p,i)=>!i||p[0]!==pairs[i-1][0]).map(([input,output])=>({input,output}))};
}
function allRows(payload: AnalysisPayload) { return [...payload.singles,...payload.doubles]; }
function namespace(payload: AnalysisPayload): Obj {
  need(payload.schemaVersion===60 && payload.summary.scriptVersion===SCRIPT,"Incorrect level-20 width generation.");
  const n=obj(payload.summary.method[NS]);
  need(n.version===1 && n.method===METHOD && n.baseSchemaVersion===59
    && /^[a-f0-9]{64}$/.test(n.basePayloadSha256) && (n.baseScriptVersion===null || typeof n.baseScriptVersion==="string")
    && same(n.policy,LEVEL20_WIDTH_POLICY) && n.dataFetches===0 && n.modelRefits===0,"Incorrect level-20 width policy.");
  const available=GOALS.filter(goal=>allRows(payload).some(row=>row.scoringVariants?.[goal]));
  need(same(n.goals,available),"Incorrect level-20 target population.");
  need(same(Object.keys(obj(n.folders)).sort(),["Single","Double"].flatMap(mode=>available.map(goal=>`${mode}:${goal}:20`)).sort()),"Missing level-20 folder population.");
  return n;
}
function validateCurrentRanks(payload: AnalysisPayload, full: boolean) {
  for (const mode of [payload.singles,payload.doubles]) for (const goal of GOALS) {
    const rows=mode.filter(row=>row.scoringVariants?.[goal]);
    if (!rows.length) continue;
    const proxies=rows.map(row=>{
      const s=obj(row.scoringVariants?.[goal]),p=obj(row.pumbilityVariants?.[goal]);
      return {...row,scoringVariants:{sss:full?s:{...s,initialFit:s}},pumbilityVariants:{sss:full?p:{estimatedDifficulty:null,initialFit:{estimatedDifficulty:null}}}};
    });
    validateRanks(proxies as unknown as Obj[]);
    if (!full) for (const level of new Set(rows.map(row=>row.level))) {
      const ranked=rows.filter(row=>row.level===level && row.pumbilityVariants![goal]!.estimatedDifficulty!==null)
        .toSorted((a,b)=>Number(a.pumbilityVariants![goal]!.levelRank)-Number(b.pumbilityVariants![goal]!.levelRank));
      ranked.forEach((row,i)=>{
        const p=obj(row.pumbilityVariants?.[goal]);
        need(p.levelRank===i+1 && p.levelComparisonCharts===ranked.length
          && ["modeRank","levelPercentile","relativeGroup","relativeGroupRank"].every(k=>p[k]===null)
          && (!i || p.estimatedDifficulty>=ranked[i-1].pumbilityVariants![goal]!.estimatedDifficulty!),"Incorrect level-20 Pumbility rank.");
      });
    }
  }
}
function validateProjection(payload: AnalysisPayload, full: boolean) {
  const n=namespace(payload);
  for (const [group,raw] of Object.entries(obj(n.folders))) {
    const p=obj(raw),[mode,goal]=group.split(":") as [string,typeof GOALS[number]];
    const rows=(mode==="Single"?payload.singles:payload.doubles).filter(row=>row.level===20 && finite(row.scoringVariants?.[goal]?.estimatedDifficulty));
    const before:number[]=[],after:number[]=[],inputs:number[]=[],order:Array<{id:string;old:number;value:number}>=[];
    need(p.lowerLimit===18.7 && p.upperLimit===22.4 && Array.isArray(p.lowerGuards) && Array.isArray(p.upperGuards)
      && Array.isArray(p.upperEligibleChartIds)
      && Array.isArray(p.deepEligibleChartIds) && p.deepEligibleChartIds.length<=5
      && Array.isArray(p.extremeEligibleChartIds) && p.extremeEligibleChartIds.length<=1,"Incorrect level-20 tail limits.");
    for (const row of rows) {
      const s=obj(row.scoringVariants?.[goal]),e=obj(s.scoringLevel20Width),original=obj(e.original),input=obj(e.input);
      need(e.version===1 && e.method===METHOD && e.group===group
        && /^stored-(?:pre-tail|final-width-pre-tail)(?:-published-tie-block)?$/.test(e.source),"Missing level-20 chart evidence.");
      for (const f of FIELDS) need(same(s[f],level20WidthValue(input[f],p)) && same(s[f],obj(e.final)[f]),"Incorrect level-20 point or interval.");
      checkFit(s,row.level);
      need(s.difficultyCi95Low===null || s.difficultyCi95High===null || s.difficultyCi95Low<=s.difficultyCi95High,"Reversed level-20 interval.");
      const c=obj(row.tierMetrics?.clearing),composite=obj(row.pumbilityVariants?.[goal]);
      need(same(composite.estimatedDifficulty,c.estimatedDifficulty===null?null:(s.estimatedDifficulty+c.estimatedDifficulty)/2),"Incorrect level-20 Pumbility mean.");
      for (const side of ["Low","High"]) need(same(composite[`difficultyCi95${side}`],s[`difficultyCi95${side}`]===null || c.estimatedDifficulty===null
        ?null:(s[`difficultyCi95${side}`]+c.estimatedDifficulty)/2),"Incorrect level-20 Pumbility interval.");
      checkFit(composite,row.level);
      need(composite.scoringSupportCount===s.nContributors && composite.clearingSupportCount===c.ratedClearCount,"Incorrect level-20 evidence counts.");
      need(finite(original.estimatedDifficulty) && finite(input.estimatedDifficulty),"Invalid level-20 stored evidence.");
      before.push(original.estimatedDifficulty);after.push(s.estimatedDifficulty);inputs.push(input.estimatedDifficulty);
      order.push({id:row.chartId,old:original.estimatedDifficulty,value:s.estimatedDifficulty});
      if (truncateEstimatedDifficulty(s.estimatedDifficulty)<=18.6) need(p.deepEligibleChartIds.includes(row.chartId),"Unsupported deep level-20 outlier.");
      if (truncateEstimatedDifficulty(s.estimatedDifficulty)===18) need(p.extremeEligibleChartIds.includes(row.chartId),"Unsupported extreme level-20 outlier.");
      if(s.estimatedDifficulty>22.4) need(p.upperEligibleChartIds.includes(row.chartId),"Unsupported upper level-20 outlier.");
    }
    const expected=expectedKnots(inputs);
    need(p.basis===expected.basis && same(p.knots,expected.knots) && p.ratedCharts===rows.length,"Incorrect level-20 quantile anchors.");
    need(same(p.before,distribution(before)) && same(p.after,distribution(after)),"Incorrect level-20 distribution counts.");
    need(p.coreTargetMet===!!(after.length && distribution(after).coreFraction!>=.6 && distribution(after).coreFraction!<=.7),"Incorrect level-20 core target status.");
    order.sort((a,b)=>a.old-b.old || (a.id<b.id?-1:a.id>b.id?1:0));
    need(order.every((r,i)=>!i || order[i-1].value<=r.value && (order[i-1].old!==r.old || order[i-1].value===r.value)),"Level-20 ordering or ties changed.");
    if (p.basis==="folder") need(same(median(after),20.5) && p.after.deepTailCharts<=5 && p.after.extremeTailCharts<=1
      && after.every(v=>v>=18 && v<=23),"Level-20 median or outlier budget differs.");
    else need(same(before,after),"Unsupported level-20 folder was changed.");
    const ids=new Set(rows.map(r=>r.chartId));
    need(p.deepEligibleChartIds.every((id:string)=>ids.has(id)) && new Set(p.deepEligibleChartIds).size===p.deepEligibleChartIds.length
      && p.extremeEligibleChartIds.every((id:string)=>p.deepEligibleChartIds.includes(id))
      && p.upperEligibleChartIds.every((id:string)=>ids.has(id)) && new Set(p.upperEligibleChartIds).size===p.upperEligibleChartIds.length,"Invalid level-20 exception population.");
    need(p.lowerGuards.length===(p.deepEligibleChartIds.length?1:0)+(p.extremeEligibleChartIds.length?1:0),"Incorrect level-20 exception guards.");
    for (const [i,g] of p.lowerGuards.entries()) need(finite(g.input) && finite(g.output) && finite(g.slope) && g.slope>0
      && g.limit===(i===0?18.1:18) && g.output>g.limit && g.input<p.knots[0].input,"Invalid level-20 tail guard.");
    need(p.upperGuards.length===(p.upperEligibleChartIds.length?1:0),"Incorrect upper level-20 exception guard.");
    for(const g of p.upperGuards) need(finite(g.input) && finite(g.output) && finite(g.slope) && g.slope>0
      && g.limit===23 && g.output<g.limit && g.input>p.knots.at(-1).input,"Invalid upper level-20 tail guard.");
  }
  for (const row of allRows(payload)) for (const [goal,s] of Object.entries(row.scoringVariants??{})) {
    need(!s.scoringLevel20Width || row.level===20 && GOALS.includes(goal as typeof GOALS[number]),"Level-20 policy escaped its folder.");
  }
  validateCurrentRanks(payload,full);
}
/** The private rollback patch restores every original metric and rank exactly. */
export function recoverLevel20WidthBaseline(payload: AnalysisPayload): AnalysisPayload {
  const n=namespace(payload),base=structuredClone(payload);
  base.schemaVersion=59;
  if (n.baseScriptVersion===null) delete (base.summary as unknown as Obj).scriptVersion;
  else base.summary.scriptVersion=n.baseScriptVersion;
  delete base.summary.method[NS];
  for (const row of allRows(base)) {
    const patch=row.level20WidthBaseline??{};
    for (const [name,goals] of Object.entries(patch)) {
      need(["scoringVariants","pumbilityVariants"].includes(name),"Invalid level-20 rollback scope.");
      for (const [goal,value] of Object.entries(goals)) {
        need(GOALS.includes(goal as typeof GOALS[number]),"Invalid level-20 rollback target.");
        const metric=obj((row as unknown as Obj)[name][goal]);
        for (const key of value.added) delete metric[key];
        Object.assign(metric,structuredClone(value.changed));
      }
    }
    delete row.level20WidthBaseline;
  }
  return base;
}
export function validateLevel20WidthAnalysisPayload(payload: AnalysisPayload,validateBase:(base:AnalysisPayload)=>unknown): AnalysisPayload {
  const base=recoverLevel20WidthBaseline(payload);
  validateBase(base);
  validatePreservedFields(payload,base);
  validateProjection(payload,true);
  return payload;
}

function withoutRanks(metric:Obj):Obj {
  const copy=structuredClone(metric);
  for(const field of RANKS) {delete copy[field];if(copy.initialFit)delete copy.initialFit[field];}
  return copy;
}
function validatePreservedFields(payload:AnalysisPayload,base:AnalysisPayload) {
  const parameters=obj(payload.summary.method[NS]).folders;
  for(const mode of ["singles","doubles"] as const)for(const goal of GOALS) {
    const oldRows=base[mode].filter(r=>r.scoringVariants?.[goal]),group=`${mode==="singles"?"Single":"Double"}:${goal}:20`;
    const points=oldRows.filter(r=>r.level===20&&finite(r.scoringVariants![goal]!.estimatedDifficulty));
    const rawInputs=new Map(points.map(r=>[r.chartId,obj(r.scoringVariants![goal]!.scoringTailPolicy).preTailDifficulty as number]));
    const ties=new Map<number,ChartResult[]>();
    for(const r of points) {const v=r.scoringVariants![goal]!.estimatedDifficulty!,block=ties.get(v)??[];block.push(r);ties.set(v,block);}
    for(const old of oldRows) {
      const current=payload[mode].find(r=>r.chartId===old.chartId)!;
      const s=obj(old.scoringVariants?.[goal]),c=obj(current.scoringVariants?.[goal]);
      const p=obj(old.pumbilityVariants?.[goal]),cp=obj(current.pumbilityVariants?.[goal]);
      if(old.level!==20 || !finite(s.estimatedDifficulty)) {
        need(same(withoutRanks(c),withoutRanks(s)) && same(withoutRanks(cp),withoutRanks(p)),"Level-20 policy changed an unaffected metric.");
        continue;
      }
      const e=obj(c.scoringLevel20Width),params=obj(parameters[group]),tail=obj(s.scoringTailPolicy);
      const block=ties.get(s.estimatedDifficulty)!;
      const input=params.basis==="folder"?{
        estimatedDifficulty:median(block.map(r=>rawInputs.get(r.chartId)!)),
        difficultyCi95Low:tail.preTailDifficultyCi95Low,difficultyCi95High:tail.preTailDifficultyCi95High,
      }:Object.fromEntries(FIELDS.map(f=>[f,s[f]]));
      const source=(s.finalFolderWidth?"stored-final-width-pre-tail":"stored-pre-tail")+(block.length>1?"-published-tie-block":"");
      const snapshot={...Object.fromEntries([...FIELDS,"difficultyDelta","difficultyDeltaCi95Low","difficultyDeltaCi95High","effectBand","effectBandRank","intervalMethod",...RANKS]
        .filter(k=>k in s).map(k=>[k,s[k]])),initialFitRanks:Object.fromEntries(RANKS.filter(k=>k in s.initialFit).map(k=>[k,s.initialFit[k]]))};
      need(same(e.input,input) && e.source===source && same(e.original,snapshot),"Level-20 inputs differ from the frozen target evidence.");
      const expected=structuredClone(s);
      for(const f of FIELDS)expected[f]=level20WidthValue(input[f],params);
      Object.assign(expected,derived(expected.estimatedDifficulty,20));
      for(const side of ["Low","High"])expected[`difficultyDeltaCi95${side}`]=expected[`difficultyCi95${side}`]===null?null:expected[`difficultyCi95${side}`]-20.5;
      if(params.basis==="folder" && FIELDS.slice(1).some(f=>expected[f]!==null))expected.intervalMethod=LEVEL20_WIDTH_POLICY.intervals;
      expected.scoringLevel20Width=e;
      need(same(withoutRanks(c),withoutRanks(expected)),"Level-20 policy changed stored fits or target evidence.");
      const expectedComposite=structuredClone(p),clearing=obj(old.tierMetrics?.clearing).estimatedDifficulty;
      Object.assign(expectedComposite,derived(clearing===null?null:(expected.estimatedDifficulty+clearing)/2,20));
      for(const side of ["Low","High"]) {
        const endpoint=expected[`difficultyCi95${side}`],v=endpoint===null || clearing===null?null:(endpoint+clearing)/2;
        expectedComposite[`difficultyCi95${side}`]=v;expectedComposite[`difficultyDeltaCi95${side}`]=v===null?null:v-20.5;
      }
      if(params.basis==="folder")expectedComposite.intervalMethod=LEVEL20_WIDTH_POLICY.intervals;
      need(same(withoutRanks(cp),withoutRanks(expectedComposite)),"Level-20 composite changed unrelated evidence or raw values.");
    }
  }
}

function textCompare(a:string,b:string):number {
  const aa=Array.from(a),bb=Array.from(b);
  for(let i=0;i<Math.min(aa.length,bb.length);i++) if(aa[i]!==bb[i]) return aa[i].codePointAt(0)!-bb[i].codePointAt(0)!;
  return aa.length-bb.length;
}
function derived(value:number|null,level:number):Obj {
  const d=value===null?null:value-level-.5;
  const rank=d===null?null:d<-.5?1:d<-.3?2:d<-.1?3:d<=.1?4:d<=.3?5:d<=.5?6:7;
  return {estimatedDifficulty:value,difficultyDelta:d,effectBandRank:rank,effectBand:rank===null?null:["Overrated","Very Easy","Easy","Medium","Hard","Very Hard","Underrated"][rank-1]};
}
/** Restore the previous public SSS projection for the legacy safeguard checks. */
function previousSssPublic(payload:AnalysisPayload):AnalysisPayload {
  const base=structuredClone(payload),n=obj(base.summary.method[NS]);
  base.schemaVersion=59;base.summary.scriptVersion=n.baseScriptVersion;delete base.summary.method[NS];
  for (const rows of [base.singles,base.doubles]) {
    for (const row of rows) {
      const s=obj(row.scoringVariants?.sss),e=s.scoringLevel20Width;
      if(e) {
        const {initialFitRanks,...original}=obj(e.original);
        Object.assign(s,original);delete s.scoringLevel20Width;
        const c=obj(row.tierMetrics?.clearing),p=obj(row.pumbilityVariants?.sss);
        Object.assign(p,derived(c.estimatedDifficulty===null?null:(s.estimatedDifficulty+c.estimatedDifficulty)/2,row.level));
        for(const side of ["Low","High"]) {
          const v=s[`difficultyCi95${side}`]===null || c.estimatedDifficulty===null?null:(s[`difficultyCi95${side}`]+c.estimatedDifficulty)/2;
          p[`difficultyCi95${side}`]=v;p[`difficultyDeltaCi95${side}`]=v===null?null:v-row.level-.5;
        }
      }
    }
    const measured=rows.filter(r=>r.scoringVariants!.sss!.estimatedDifficulty!==null).sort((a,b)=>{
      const aa=obj(a.scoringVariants?.sss),bb=obj(b.scoringVariants?.sss);
      return aa.difficultyDelta-bb.difficultyDelta || bb.nContributors-aa.nContributors || textCompare(a.songName,b.songName) || textCompare(a.chartId,b.chartId);
    });
    measured.forEach((r,i)=>{r.scoringVariants!.sss!.modeRank=i+1;});
    for(const level of new Set(rows.map(r=>r.level))) {
      const group=measured.filter(r=>r.level===level);
      group.forEach((r,i)=>{
        const s=obj(r.scoringVariants?.sss),ties=group.map((other,j)=>({d:other.scoringVariants!.sss!.difficultyDelta,j})).filter(t=>t.d===s.difficultyDelta);
        const pct=(ties.reduce((n,t)=>n+t.j+1,0)/ties.length-.5)/group.length,rank=Math.min(10,Math.max(1,Math.floor(pct*10)+1));
        Object.assign(s,{levelRank:i+1,levelComparisonCharts:group.length,levelPercentile:pct,relativeGroupRank:rank,
          relativeGroup:rank===1?"Easiest 10%":rank===10?"Hardest 10%":`${(rank-1)*10}–${rank*10}% percentile`});
      });
      rows.filter(r=>r.level===level&&r.pumbilityVariants!.sss!.estimatedDifficulty!==null).sort((a,b)=>a.pumbilityVariants!.sss!.estimatedDifficulty!-b.pumbilityVariants!.sss!.estimatedDifficulty!)
        .forEach((r,i,group)=>Object.assign(r.pumbilityVariants!.sss!,{levelRank:i+1,levelComparisonCharts:group.length}));
    }
  }
  return base;
}
export function validateLevel20WidthPublicPayload(payload:AnalysisPayload):AnalysisPayload {
  validateProjection(payload,false);
  validateSssTailPublicPayload(previousSssPublic(payload));
  return payload;
}
