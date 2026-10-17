import { curve, inverse } from "./sss-analysis-validation.ts";
import { forward } from "./refined-analysis-validation.ts";
import { expectedScoring, sssCenteringFolders } from "./sss-centering-validation.ts";
import { validateTail } from "./sssplus-analysis-validation.ts";
import { compressDifficulty } from "./tier-postprocessing.ts";
import type { AnalysisPayload, ScoringVariant } from "./types";

type Obj = Record<string, unknown>;
const NS = "sssplusMedianCalibration";
const STABLE_TAIL = "lower-secant-upper-tangent-linear-v1";
const SCALE_POLICY = {version:1,method:"bounded-offset-local-lower-scale-v1",minimumOffsetMarginAnchorGaps:1,
  lowerExtrapolation:"adjacent-anchor-secant",upperExtrapolation:"tangent-linear",scope:"official-target-final-curve-only"};
const MODES = [["Single", "singles"], ["Double", "doubles"]] as const;
const FIELDS = ["estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"];
const RANKS = ["levelRank", "levelComparisonCharts", "modeRank", "levelPercentile", "relativeGroup", "relativeGroupRank"];
function need(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
function obj(value: unknown): Obj { need(value && typeof value === "object" && !Array.isArray(value), "Missing final median object."); return value as Obj; }
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function same(a: unknown, b: unknown): boolean {
  if (typeof b === "number") return finite(a) && Math.abs(a - b) < 1e-8;
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => same(v, b[i]));
  if (a && b && typeof a === "object" && typeof b === "object") {
    const aa = obj(a), bb = obj(b);
    return Object.keys(aa).length === Object.keys(bb).length && Object.keys(bb).every(k => k in aa && same(aa[k], bb[k]));
  }
  return false;
}
function median(values: number[]): number { const v = values.toSorted((a,b) => a-b), i = Math.floor(v.length/2); return v.length%2 ? v[i] : (v[i-1]+v[i])/2; }
function withoutRanks(value: unknown): Obj { const v = structuredClone(obj(value)); for (const k of RANKS) { delete v[k]; if (v.initialFit) delete obj(v.initialFit)[k]; } return v; }
function derived(value: number | null, level: number): Obj {
  const d = value === null ? null : value-level-.5, rank = d===null?null:d<-.5?1:d<-.3?2:d<-.1?3:d<=.1?4:d<=.3?5:d<=.5?6:7;
  return {estimatedDifficulty:value,difficultyDelta:d,effectBandRank:rank,effectBand:rank===null?null:["Overrated","Very Easy","Easy","Medium","Hard","Very Hard","Underrated"][rank-1]};
}

export function recoverMedianBaseline(payload: AnalysisPayload): AnalysisPayload {
  need(payload.schemaVersion === 53, "Final median recovery requires schema 53.");
  const base = structuredClone(payload); base.schemaVersion = 52;
  base.summary.method.sssplusTailPolicy = obj(base.summary.method[NS]).previousTailPolicy;
  delete base.summary.method[NS];
  for (const [, key] of MODES) for (const row of base[key]) {
    const snapshot = row.sssplusMedianBaseline; need(snapshot, "Missing frozen schema-52 median baseline.");
    row.scoringVariants!.sssplus = snapshot.scoring; row.pumbilityVariants!.sssplus = snapshot.pumbility;
    row.sssplusTailBaseline = snapshot.tailBaseline; delete row.sssplusMedianBaseline;
  }
  return base;
}

/** Reproduce the fixed growth grid with a two-column least-squares fit. */
function checkCurveFit(c: Obj, guarded=false) {
  const anchors = (c.anchors as unknown[]).map(obj), first = Number(anchors[0].cost), span = Number(anchors.at(-1)!.cost)-first;
  const z = anchors.map(a => (Number(a.cost)-first)/span), n = z.length, sz = z.reduce((s,v)=>s+v,0);
  const candidates: {k:number;o:number;a:number;e:number}[] = [];
  for(let i=0;i<1600;i++) {
    const k=.001+i*(1.2-.001)/1599, x=anchors.map(a=>Math.exp(k*(Number(a.level)-20)));
    const sx=x.reduce((s,v)=>s+v,0), meanX=sx/n, meanZ=sz/n;
    const variance=x.reduce((s,v)=>s+(v-meanX)**2,0);
    const a=x.reduce((s,v,j)=>s+(v-meanX)*(z[j]-meanZ),0)/variance, o=meanZ-a*meanX;
    const e=x.reduce((s,v,j)=>s+(o+a*v-z[j])**2,0)/n;
    const offset=first+o*span, limit=first-(Number(anchors[1].cost)-first);
    if(Number.isFinite(e)&&o<0&&a>0&&(!guarded||offset<=limit))candidates.push({k,o,a,e});
  }
  need(candidates.length, "No valid final median growth fit.");
  const minimum=Math.min(...candidates.map(c=>c.e)), chosen=candidates.find(c=>c.e<=minimum+1e-12)!;
  const fit=obj(c.fit);
  need(same(fit.growth,chosen.k) && same(fit.normalizedOffset,chosen.o) && same(fit.normalizedAmplitude,chosen.a)
    && same(fit.normalizedMse,chosen.e) && same(c.offset,first+chosen.o*span)
    && fit.boundHit===(chosen.k===.001||chosen.k===1.2), "Final median exponential fit differs from its frozen anchors.");
}

export function validateFinalCurve(value:unknown,version:number):Obj|null {
  if(value===null)return null;
  const c=obj(value);
  if(version===3) {
    need(c.tailPolicy===STABLE_TAIL,"Final lower-extrapolation policy differs.");
    curve({...c,tailPolicy:"tangent-linear"},20);
    const anchors=(c.anchors as unknown[]).map(obj),a=anchors[0],b=anchors[1],gap=Number(b.cost)-Number(a.cost);
    const limit=Number(a.cost)-gap;
    need(Number(c.offset)<=limit && same(c.finalScale,{version:1,method:SCALE_POLICY.method,minimumOffsetMarginAnchorGaps:1,
      offsetLimit:limit,lowerSlope:gap/(Number(b.level)-Number(a.level)),lowerAnchorLevels:[a.level,b.level]}),
      "Final offset margin or adjacent-anchor lower scale differs.");
  } else {
    need(!("finalScale" in c),"Historical final curve contains a new scale policy.");
    curve(c,20);
  }
  checkCurveFit(c,version===3);
  return c;
}

export function inverseFinalCost(cost:number,c:Obj):number {
  need(finite(cost),"Nonfinite final chart cost.");
  const first=obj((c.anchors as unknown[])[0]);
  if(c.tailPolicy===STABLE_TAIL && cost<Number(first.cost))return Number(first.difficulty)+(cost-Number(first.cost))/Number(obj(c.finalScale).lowerSlope);
  return inverse(cost,c);
}

export function validateMedianAnalysisPayload(payload: AnalysisPayload, validateBase: (base: AnalysisPayload) => unknown): AnalysisPayload {
  const base = recoverMedianBaseline(payload); validateBase(base);
  const method = obj(payload.summary.method[NS]), original = obj(obj(obj(base.summary.method.sssplusScoring).calibration).official);
  const version=method.version;
  need(version===1 || version===2 || version===3,"Unsupported final median calibration policy.");
  const methodName=`sssplus-refined-median-calibration-v${version}`;
  need(/^[a-f0-9]{64}$/.test(String(method.basePayloadSha256)), "Missing final median baseline identity.");
  const preview = structuredClone(base), latents = new Map<string, number | null>();
  for (const [mode,key] of MODES) for (const row of preview[key]) {
    row.scoringVariants!.sss = row.level>=20 ? structuredClone(row.sssplusCenteringBaseline!.scoring) : row.scoringVariants!.sssplus;
    if(row.level>=20) { const v=row.scoringVariants!.sss!.initialFit!.estimatedDifficulty; latents.set(row.chartId,v===null?null:forward(Number(v),obj(original[mode]))); }
  }
  const support=sssCenteringFolders(preview,version===1), populations:Record<string,Obj>={}, calibrations=obj(method.calibration), errors=obj(method.calibrationErrors);
  for(const [mode,key] of MODES) {
    const anchors:Obj[]=[];populations[mode]={};
    for(const [level,folder] of Object.entries(support[mode])) {
      const rows=preview[key].filter(r=>folder.populationChartIds.includes(r.chartId)), branches:Record<string,number>={};
      for(const row of rows) {const branch=row.scoringVariants!.sss!.scoringRefinement!.branch;branches[branch]=(branches[branch]??0)+1;}
      const record={level:Number(level),supported:folder.basis==="folder",populationChartIds:folder.populationChartIds,mixedFirstPassChartIds:folder.mixedFirstPassChartIds,
        branches,possiblyTruncatedCharts:rows.filter(r=>r.scoringVariants!.sss!.scoringRefinement!.fullBoard.possiblyTruncated).length,
        fewerThanFiveScores:rows.filter(r=>r.scoringVariants!.sss!.scoringRefinement!.fullBoard.records<5).length,
        medianLatent:rows.length?median(rows.map(r=>latents.get(r.chartId)!)):null};
      populations[mode][level]=record;
      if(record.supported) anchors.push({level:Number(level),cost:record.medianLatent,referenceCharts:rows.length,chartIds:folder.populationChartIds,difficulty:Number(level)+.5});
    }
    const validAnchors=anchors.length>=3 && anchors[0].level===20 && anchors.every((a,i)=>finite(a.cost)&&(!i||Number(a.cost)>Number(anchors[i-1].cost)));
    const c=validateFinalCurve(calibrations[mode],version);
    if(validAnchors) { need(c && errors[mode]===null,"Supported final median curve unavailable.");need(same(c.anchors,anchors),"Final median anchor population or equal-chart median differs."); }
    else need(c===null && typeof errors[mode]==="string" && String(errors[mode]).length>0,"Unsupported final median calibration must be explicitly unavailable.");
    for(let i=0;i<preview[key].length;i++) {
      const row=preview[key][i],old=base[key][i];if(row.level<20)continue;
      const s=obj(row.scoringVariants!.sss!), previous=obj(old.scoringVariants!.sssplus);
      for(const field of ["evidenceStatus","scoringProvisional","scoringProvisionalReasons","scoringExtrapolated"]) if(field in previous)s[field]=structuredClone(previous[field]);
      const initial=obj(s.initialFit);
      for(const field of FIELDS) {
        const raw=initial[field],v=raw===null||!c?null:inverseFinalCost(forward(Number(raw),obj(original[mode])),c);
        initial[field]=v;s[field]=compressDifficulty(v,row.level);
        if(field!=="estimatedDifficulty") {initial[field.replace("difficulty","difficultyDelta")]=v===null?null:v-row.level-.5;s[field.replace("difficulty","difficultyDelta")]=s[field]===null?null:Number(s[field])-row.level-.5;}
      }
      Object.assign(initial,derived(initial.estimatedDifficulty as number|null,row.level));Object.assign(s,derived(s.estimatedDifficulty as number|null,row.level));
      if(!c)Object.assign(s,{evidenceStatus:"Unrated",scoringUnratedReason:"Final SSS+ median calibration unavailable: "+errors[mode]});
    }
  }
  const residuals=sssCenteringFolders(preview,version===1);
  need(same(method,{version,method:methodName,baseSchemaVersion:52,basePayloadSha256:method.basePayloadSha256,localOnly:true,target:995000,
    population:"all-finite-refined-official-charts-equal-chart-weight",minimumMixedCharts:version===1?5:0,minimumFinalCharts:5,
    ...(version>=2?{anchorEligibility:"minimum-five-finite-refined-charts-v1"}:{}),
    ...(version===3?{finalScalePolicy:SCALE_POLICY}:{}),anchorKind:"derived-provisional-full-folder-median",minimumLevel:20,
    stageOrder:["frozen-refined-latent","supported-folder-median-anchors","final-curve","residual-median-offset","single-midpoint-compression","unchanged-global-tail-policy","component-mean-and-ranks"],
    compressionApplications:1,compressionFactor:.5,modelRefits:0,dataFetches:0,originalOfficialCalibration:original,populations,calibration:calibrations,calibrationErrors:errors,
    residualFolders:residuals,previousTailPolicy:base.summary.method.sssplusTailPolicy}),"Final median policy, population, curve, source or residual differs.");
  for(const [mode,key] of MODES) for(let i=0;i<payload[key].length;i++) {
    const row=payload[key][i], old=base[key][i], pre=row.sssplusTailBaseline!;
    if(row.level<20) {
      need(same(withoutRanks(pre.scoring),withoutRanks(old.sssplusTailBaseline!.scoring)),"Submitted pre-tail target changed.");
      for(const variants of ["scoringVariants","pumbilityVariants"] as const)need(same(withoutRanks(row[variants]!.sssplus),withoutRanks(old[variants]!.sssplus)),"Submitted target value or evidence changed.");
      continue;
    }
    const c=calibrations[mode]===null?null:obj(calibrations[mode]), originalCurve=obj(original[mode]), level=row.level, midpoint=level+.5;
    const projectRange=(r:Obj|null):Obj|null=>!r||!c?null:Object.fromEntries(["lower","upper"].map(side=>[side,compressDifficulty(inverseFinalCost(forward(midpoint+2*(Number(r[side])-midpoint),originalCurve),c),level)]));
    const originalMetric=preview[key][i].scoringVariants!.sss!, ref=originalMetric.scoringRefinement!,folder=residuals[mode][String(level)];
    const expected=expectedScoring(originalMetric,level,folder,projectRange(ref.range?obj(ref.range):null));
    const band=projectRange(ref.rare?{lower:ref.rare.upperBandLow,upper:ref.rare.upperBandHigh}:null);
    obj(expected.scoringFolderCentering).effectiveRareBand=band?Object.fromEntries(Object.entries(band).map(([k,v])=>[k,Number(v)+folder.finalShift])):null;
    const cost=latents.get(row.chartId)!, limits=c?(c.anchors as unknown[]).map(obj):[];
    expected.scoringMedianCalibration={version,method:methodName,source:"official",mode,refinedLatent:cost,anchorKind:"derived-provisional-full-folder-median",
      anchor:populations[mode][String(level)],calibrationError:errors[mode],preliminaryExtrapolated:expected.scoringExtrapolated??false,
      finalExtrapolated:cost===null||!limits.length?null:cost<Number(limits[0].cost)||cost>Number(limits.at(-1)!.cost),
      intervals:"existing-player-bootstrap-fixed-final-curve-residual-and-tail-no-anchor-uncertainty"};
    if(version===3)obj(expected.scoringMedianCalibration).finalCurveScale=c?structuredClone(c.finalScale):null;
    need(same(withoutRanks(pre.scoring),withoutRanks(expected)),"Final median projection, residual, interval, preserved evidence or diagnostics differs.");
  }
  validateTail(payload);
  return payload;
}
