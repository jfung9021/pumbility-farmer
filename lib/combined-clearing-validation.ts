import { compressDifficulty, compressionFactor } from "./tier-postprocessing.ts";
import type { AnalysisPayload } from "./types";

type Obj = Record<string, any>;
const METHOD = "official-d26-clearer-ability-combined-v2";
const NS = "combinedOfficialClearing";
const STAGE = "combinedOfficialClearingBaseline";
const STATUS = ["Unrated", "Insufficient", "Provisional", "Published"];
function need(v: unknown, message: string): asserts v { if (!v) throw new Error(message); }
function obj(v: unknown): Obj { need(v && typeof v === "object" && !Array.isArray(v), "Missing combined Clearing object."); return v as Obj; }
function finite(v: unknown): v is number { return typeof v === "number" && Number.isFinite(v); }
function same(a: any, b: any): boolean {
  if (typeof b === "number") return finite(a) && Math.abs(a-b) < 1e-8;
  if (a===b) return true;
  if (Array.isArray(a)&&Array.isArray(b)) return a.length===b.length&&a.every((v,i)=>same(v,b[i]));
  if (a&&b&&typeof a==="object"&&typeof b==="object") return Object.keys(a).length===Object.keys(b).length&&Object.keys(b).every(k=>k in a&&same(a[k],b[k]));
  return false;
}
function quantile(values: number[], p: number): number { const a=values.toSorted((a,b)=>a-b), t=(a.length-1)*p, i=Math.floor(t); return a[i]+(a[Math.min(i+1,a.length-1)]-a[i])*(t-i); }
function mean(a: any,b: any): number|null { return a===null||b===null?null:(Number(a)+Number(b))/2; }

export function recoverCombinedClearingBaseline(payload: AnalysisPayload): AnalysisPayload {
  need(payload.schemaVersion===55,"Combined Clearing recovery requires schema 55.");
  const base=structuredClone(payload) as unknown as Obj, marker=obj(base.summary.method[NS]);
  base.schemaVersion=54; delete base.summary.method[NS];
  if(marker.previousOfficialHighLevelClearingPresent)base.summary.method.officialHighLevelClearing=marker.previousOfficialHighLevelClearing;
  else delete base.summary.method.officialHighLevelClearing;
  for(const key of ["singles","doubles"])for(const row of base[key]) {
    const saved=obj(row[STAGE]); row.tierMetrics.clearing=saved.clearing;row.tierMetrics.pumbility=saved.pumbility;
    row.pumbilityVariants=saved.pumbilityVariants;delete row[STAGE];
  }
  return base as AnalysisPayload;
}

/** Replay public calibration and source contracts; private clear histories stay private. */
export function validateCombinedClearingPayload(payload: AnalysisPayload, validateBase:(base:AnalysisPayload)=>unknown):AnalysisPayload {
  const base=recoverCombinedClearingBaseline(payload);validateBase(base);
  const p=payload as unknown as Obj,b=base as unknown as Obj, marker=obj(p.summary.method[NS]),policy=obj(p.summary.method.officialHighLevelClearing);
  const p1Observed=marker.version===2,capPolicy=p1Observed?"phoenix1-diagnostic-phoenix2-default-v1":"any-source-possibly-truncated-v1";
  need((marker.version===1||marker.version===2)&&marker.method===METHOD&&marker.baseSchemaVersion===54&&marker.localOnly===true
    &&/^[a-f0-9]{64}$/.test(marker.basePayloadSha256)&&marker.modelRefits===0&&marker.dataFetches===0
    &&same(marker.scope,{mode:"Double",minimumLevel:26,historyMinimumLevel:23})&&marker.compressionApplications===1
    &&marker.mergePolicy==="full-tag-bidirectional-raw-max-v1"&&marker.uniqueClearPolicy==="one-merged-player-chart-clear-regardless-of-winning-source"
    &&marker.capPolicy===capPolicy&&marker.comparabilityPolicy==="current-chart-id-and-mode", "Combined Clearing policy differs.");
  need(policy.version===2&&policy.method===METHOD&&policy.source==="piuscores-official"&&policy.mix==="Phoenix+Phoenix2"
    &&policy.mode==="Double"&&policy.minimumLevel===26&&policy.percentile===.2&&policy.difficultyDeltaScale===.7&&policy.compressionFactor===compressionFactor(payload)
    &&same(policy.playerSkill,{method:"leave-one-chart-out-top-official-levels",topCharts:25,minimumOtherCharts:25,historyMinimumLevel:23,levelMidpointOffset:.5,separateModes:true})
    &&same(policy.shrinkage,{priorPlayers:20,formula:"n/(n+20)*n/N"})
    &&policy.capPolicy===marker.capPolicy&&policy.mergePolicy===marker.mergePolicy&&policy.comparabilityPolicy===marker.comparabilityPolicy
    &&same(policy.sourceDates,marker.sourceDates)&&policy.manifestSha256===marker.manifestSha256
    &&policy.manifestSha256===obj(p.summary.method.sssplusScoring).officialManifestSha256
    &&obj(policy.uncertainty).samples===1000,"Combined Clearing sources or numeric method differ.");
  need(same(Object.keys(marker.sourceDates).sort(),["Phoenix","Phoenix2"]),"Combined Clearing must identify both source dates.");
  need(same(policy.calibration,{method:"robust-folder-spread",version:4,referenceQuantile:.9,targetHalfWidth:.45,maximumScale:null,
    rangePolicy:"unbounded",minimumSupportedCharts:5,minimumReferencePlayers:10,minimumIqr:.07,outlierIqrMultiplier:3,
    outlierMinimumPlayers:20,outlierMinimumCoverage:.5,requireIntervalBeyondFence:true,outlierUse:"diagnostic-only",fallbackScale:1}),"Combined Clearing spread policy differs.");
  const groups=new Map<number,Obj[]>();
  for(const key of ["singles","doubles"]) {
    need(p[key].length===b[key].length,"Combined Clearing changed the catalog.");
    for(let i=0;i<p[key].length;i++) {
      const row=obj(p[key][i]),old=obj(b[key][i]);
      const clean=structuredClone(row);delete clean[STAGE];
      if(key!=="doubles"||row.level<26) {need(same(clean,old),"Combined Clearing changed a chart outside D26+.");continue;}
      for(const field of Object.keys(old))if(!["tierMetrics","pumbilityVariants"].includes(field))need(same(row[field],old[field]),`Combined Clearing changed frozen Scoring or chart field ${field}.`);
      const tiers=obj(row.tierMetrics);for(const field of Object.keys(old.tierMetrics))if(!["clearing","pumbility"].includes(field))need(same(tiers[field],old.tierMetrics[field]),"An unrelated tier metric changed.");
      const c=obj(tiers.clearing),e=obj(c.clearingEvidence),l=Number(row.level),mid=l+.5,n=Number(c.ratedClearCount),N=Number(c.clearCount);
      need(c.method===METHOD&&e.source==="piuscores-official"&&e.mix==="Phoenix+Phoenix2"&&same(e.sourceDates,marker.sourceDates)&&e.comparabilityPolicy===marker.comparabilityPolicy,"Combined Clearing evidence sources differ.");
      if(p1Observed)need(e.capPolicy===capPolicy,"Combined Clearing effective cap policy differs.");
      const states=obj(e.sourceStates),available=Object.keys(states).length>0;
      if(available) {
        need(same(Object.keys(states).sort(),["Phoenix","Phoenix2"])&&e.manifestSha256===marker.manifestSha256,"Combined Clearing board provenance differs.");
        const cap=Object.values(states).some(s=>obj(s).possiblyTruncated===true);
        const effectiveCap=p1Observed?obj(states.Phoenix2).possiblyTruncated===true:cap;
        need(e.possiblyTruncated===cap&&c.defaultedAtCap===effectiveCap,"Combined Clearing cap must reflect the selected source policy.");
        if(p1Observed&&obj(states.Phoenix).possiblyTruncated===true)need(c.provisionalReasons.includes("phoenix1-cap-ignored-for-estimation")&&c.evidenceStatus!=="Published","Ignored Phoenix 1 caps must retain their provisional diagnostic.");
        need(e.rawRowCount===Object.values(states).reduce((sum:number,s)=>sum+Number(obj(s).rawRowCount),0)&&e.mergedPlayers===N&&N<=e.rawRowCount,"Combined Clearing merged counts differ.");
        if(!["same","single-source-known"].includes(e.comparability)&&N>0&&c.estimatedDifficulty!==null)need(c.evidenceStatus==="Provisional"||c.evidenceStatus==="Insufficient","Uncertain score comparability must remain a provisional Clearing diagnostic.");
      }
      need(Number.isInteger(n)&&Number.isInteger(N)&&0<=n&&n<=N&&c.observedClearCount===N&&c.missingSkillCount===N-n
        &&same(c.abilityCoverage,N?n/N:0)&&same(c.shrinkageWeight,n/(n+20)*(N?n/N:0)),"Combined Clearing support or weights differ.");
      for(const f of ["estimatedDifficulty","difficultyCi95Low","difficultyCi95High"])need(same(c[f],compressDifficulty(obj(c.initialFit)[f]===null?null:Number(obj(c.initialFit)[f]),l,compressionFactor(payload))),"Combined Clearing compression must apply once.");
      if(c.defaultedAtCap)need(c.difficultyCi95Low===null&&c.difficultyCi95High===null,"Capped Clearing gained a formal interval.");
      if(!groups.has(l))groups.set(l,[]);groups.get(l)!.push(row);
      for(const goal of ["general","sss","sssplus"]) {
        const s=obj(row.scoringVariants[goal]),v=obj(row.pumbilityVariants[goal]),value=mean(s.estimatedDifficulty,c.estimatedDifficulty);
        need(v.aggregation==="average"&&same(v.estimatedDifficulty,value)&&same(obj(v.initialFit).estimatedDifficulty,mean(obj(s.initialFit).estimatedDifficulty,obj(c.initialFit).estimatedDifficulty))
          &&v.scoringSupportCount===s.nContributors&&v.clearingSupportCount===n
          &&v.evidenceStatus===(value===null?"Unrated":STATUS[Math.min(STATUS.indexOf(s.evidenceStatus),STATUS.indexOf(c.evidenceStatus))]),"Combined Pumbility must use full-precision components and weaker evidence.");
        if(goal!=="general")for(const side of ["Low","High"])need(same(v[`difficultyCi95${side}`],value===null?null:mean(s[`difficultyCi95${side}`],c.estimatedDifficulty)),"Combined Pumbility conditional interval differs.");
      }
      need(same(tiers.pumbility,row.pumbilityVariants.general),"Combined General Pumbility aliases differ.");
    }
  }
  const spreads:Record<string,Obj>={};
  for(const [level,rows] of groups) {
    const measured=rows.map(r=>r.tierMetrics.clearing).filter(c=>!c.defaultedAtCap&&finite(c.q20Skill));
    const reference=measured.length?quantile(measured.map(c=>c.q20Skill),.5):null;
    const values=measured.filter(c=>c.ratedClearCount>=10).map(c=>.7*c.shrinkageWeight*(c.q20Skill-Number(reference)));
    const supported=values.length>=5,spread=supported?quantile(values.map(Math.abs),.9):null;
    const q25=supported?quantile(values,.25):null,q75=supported?quantile(values,.75):null,iqr=supported?Math.max(q75!-q25!,.07):null;
    spreads[level]={scale:supported?(spread!>0?.45/spread!:1):1,referenceSpread:spread,supportedCharts:values.length,
      lowerFence:supported?q25!-3*iqr!:null,upperFence:supported?q75!+3*iqr!:null,basis:supported?"folder":"fallback",referenceLevel:supported?level:null};
    need(rows.every(r=>same(r.tierMetrics.clearing.folderReferenceSkill,reference)),"Combined Clearing reference is not the measured folder median.");
  }
  for(const [level,rows] of groups) {
    const parameters=spreads[level];if(parameters.basis!=="folder") {
      const donors=Object.entries(spreads).filter(([,p])=>p.basis==="folder").sort(([a],[b])=>Math.abs(Number(a)-level)-Math.abs(Number(b)-level)||Number(a)-Number(b));
      if(donors.length)Object.assign(parameters,{scale:donors[0][1].scale,referenceSpread:donors[0][1].referenceSpread,basis:"neighbor-folder",referenceLevel:Number(donors[0][0])});
    }
    const folder=obj(policy.folders[String(level)]),uncapped:number[]=[];
    for(const row of rows) {
      const c=row.tierMetrics.clearing;
      need(same(c.spreadCalibration,parameters)&&same(folder.spreadCalibration,parameters),"Combined Clearing robust spread differs.");
      if(!c.defaultedAtCap&&finite(c.q20Skill)) {
        const raw=.7*c.shrinkageWeight*(c.q20Skill-c.folderReferenceSkill),value=level+.5+parameters.scale*raw;
        need(same(c.rawDifficultyDelta,raw)&&same(c.unrecenteredDifficulty,value),"Combined Clearing raw ability projection differs.");uncapped.push(value);
      }
    }
    const floor=uncapped.length?Math.min(...uncapped):level+.5;
    const before=rows.map(r=>r.tierMetrics.clearing.unrecenteredDifficulty).filter(finite),shift=before.length?level+.5-quantile(before,.5):null;
    for(const row of rows) {
      const c=row.tierMetrics.clearing;
      need(same(c.recenteringShift,shift)&&same(c.initialFit.estimatedDifficulty,c.unrecenteredDifficulty===null?null:c.unrecenteredDifficulty+shift),"Combined Clearing median offset differs.");
      if(c.defaultedAtCap)need(same(c.unrecenteredDifficulty,floor)&&same(c.capDefaultDifficulty,c.estimatedDifficulty),"Combined Clearing capped default differs.");
    }
    const finiteValues=rows.map(r=>r.tierMetrics.clearing.estimatedDifficulty).filter(finite);if(finiteValues.length)need(same(quantile(finiteValues,.5),level+.5),"Combined Clearing median differs from official midpoint.");
    for(const goal of ["clearing","general","sss","sssplus"]) {
      const values=rows.map(r=>goal==="clearing"?r.tierMetrics.clearing:r.pumbilityVariants[goal]).filter(c=>c.estimatedDifficulty!==null).sort((a,b)=>a.levelRank-b.levelRank);
      values.forEach((v,i)=>need(v.levelRank===i+1&&v.levelComparisonCharts===values.length&&(!i||v.estimatedDifficulty>=values[i-1].estimatedDifficulty),"Combined Clearing or Pumbility ranks differ."));
    }
  }
  need(same(p.coop,b.coop),"Combined Clearing changed Co-op.");
  return payload;
}
