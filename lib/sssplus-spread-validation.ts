import { checkFit, inverse, validateRanks } from "./sss-analysis-validation.ts";
import { forward } from "./refined-analysis-validation.ts";
import { expectedScoring, sssCenteringFolders } from "./sss-centering-validation.ts";
import { recoverMedianBaseline, validateFinalCurve, inverseFinalCost } from "./sssplus-median-validation.ts";
import { targetTailRating, targetTailSelection } from "./sssplus-analysis-validation.ts";
import { compressDifficulty } from "./tier-postprocessing.ts";
import type { AnalysisPayload, ChartResult, ScoringVariant } from "./types";

type Obj = Record<string, unknown>;
const NS = "sssplusSpreadPolicy";
const MODES = [["Single", "singles"], ["Double", "doubles"]] as const;
const FIELDS = ["estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"];
const RANKS = ["levelRank", "levelComparisonCharts", "modeRank", "levelPercentile", "relativeGroup", "relativeGroupRank"];
function need(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
function obj(value: unknown): Obj { need(value && typeof value === "object" && !Array.isArray(value), "Missing spread contract object."); return value as Obj; }
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function same(a: unknown, b: unknown): boolean {
  if (typeof b === "number") return finite(a) && Math.abs(a-b)<1e-8;
  if (a===b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length===b.length && a.every((v,i)=>same(v,b[i]));
  if (a && b && typeof a==="object" && typeof b==="object") {
    const aa=obj(a),bb=obj(b);return Object.keys(aa).length===Object.keys(bb).length && Object.keys(bb).every(k=>k in aa && same(aa[k],bb[k]));
  }
  return false;
}
function median(values:number[]):number { const v=values.toSorted((a,b)=>a-b),i=Math.floor(v.length/2);return v.length%2?v[i]:(v[i-1]+v[i])/2; }
function withoutRanks(value:unknown):Obj { const v=structuredClone(obj(value));for(const k of RANKS){delete v[k];if(v.initialFit)delete obj(v.initialFit)[k];}return v; }
function derived(value:number|null,level:number):Obj {
  const d=value===null?null:value-level-.5,r=d===null?null:d<-.5?1:d<-.3?2:d<-.1?3:d<=.1?4:d<=.3?5:d<=.5?6:7;
  return {estimatedDifficulty:value,difficultyDelta:d,effectBandRank:r,effectBand:r===null?null:["Overrated","Very Easy","Easy","Medium","Hard","Very Hard","Underrated"][r-1]};
}

/** The spread is applied to already-once-compressed centered coordinates. */
export function spreadRating(value:number|null,level:number,scale=.55):number|null {
  if(value===null)return null;
  need(finite(value) && finite(scale) && scale>0,"Invalid spread input or scale.");
  const m=level+.5;return m+scale*Math.asinh((value-m)/scale);
}

/** Restore the exact historical schema-53 generation, including below-20 ranks. */
export function recoverSpreadBaseline(payload:AnalysisPayload):AnalysisPayload {
  need(payload.schemaVersion===54,"Spread recovery requires schema 54.");
  const base=structuredClone(payload);base.schemaVersion=53;
  base.summary.method.sssplusTailPolicy=obj(base.summary.method[NS]).previousTailPolicy;
  delete base.summary.method[NS];
  for(const [,key] of MODES)for(const row of base[key]) {
    const old=row.sssplusSpreadBaseline;need(old,"Missing frozen schema-53 spread baseline.");
    row.scoringVariants!.sssplus=old.scoring;row.pumbilityVariants!.sssplus=old.pumbility;
    row.sssplusTailBaseline=old.tailBaseline;delete row.sssplusSpreadBaseline;
  }
  return base;
}

function replayMedian(base:AnalysisPayload,method:Obj):AnalysisPayload {
  const adjusted=recoverMedianBaseline(base), adjustments=obj(method.rareAdjustments);
  const original=obj(obj(obj(adjusted.summary.method.sssplusScoring).calibration).official);
  const found=new Set<string>();
  for(const [mode,key] of MODES)for(const row of adjusted[key]) {
    if(!(row.chartId in adjustments))continue;
    need(row.level>=20 && row.sssplusCenteringBaseline!.scoring.scoringRefinement?.branch==="rare-target","Rare adjustment escaped the official rare-target population.");
    const adjustment=obj(adjustments[row.chartId]),metadata=obj(adjustment.metadata),effect=adjustment.effect;
    need(finite(effect) && same(metadata.posteriorEffect,effect),"Rare conditional effect and provenance differ.");
    const metric=obj(row.sssplusCenteringBaseline!.scoring),raw=inverse(effect,obj(original[mode]));
    Object.assign(obj(metric.initialFit),derived(raw,row.level));Object.assign(metric,derived(compressDifficulty(raw,row.level),row.level));
    Object.assign(metric,{scoringChartEffect:effect,scoringMethod:"target-conditional-pooled-prior-v1",scoringProvisional:true,evidenceStatus:"Provisional",
      scoringBootstrapValidReplicates:0,intervalMethod:"unavailable-new-conditional-estimator",
      intervalUnavailableReason:"New fixed-ability conditional chart estimator has no player-bootstrap interval."});
    metric.scoringProvisionalReasons=[...new Set([...(metric.scoringProvisionalReasons as string[]).filter(r=>r!=="rare-target-upper-band-policy"),"conditional-pooled-prior-estimate"])].sort();
    for(const f of ["difficultyCi95Low","difficultyCi95High","difficultyDeltaCi95Low","difficultyDeltaCi95High"])metric[f]=obj(metric.initialFit)[f]=null;
    metric.scoringChartEffectCi95Low=metric.scoringChartEffectCi95High=null;
    found.add(row.chartId);
  }
  need(found.size===Object.keys(adjustments).length,"Unknown rare adjustment chart.");
  const preview=structuredClone(adjusted),latents=new Map<string,number|null>();
  for(const [mode,key] of MODES)for(const row of preview[key]) {
    row.scoringVariants!.sss=row.level>=20?structuredClone(row.sssplusCenteringBaseline!.scoring):row.scoringVariants!.sssplus;
    if(row.level>=20){const v=row.scoringVariants!.sss!.initialFit!.estimatedDifficulty;latents.set(row.chartId,v===null?null:forward(Number(v),obj(original[mode])));}
  }
  const support=sssCenteringFolders(preview,false),calibration=obj(method.medianCalibration),curves=obj(calibration.calibration),errors=obj(calibration.calibrationErrors),populations:Obj={};
  need(calibration.version===3 && calibration.method==="sssplus-refined-median-calibration-v3" && calibration.baseSchemaVersion===52
    && calibration.compressionApplications===1 && calibration.compressionFactor===.5 && calibration.modelRefits===0 && calibration.dataFetches===0
    && calibration.minimumFinalCharts===5 && calibration.minimumMixedCharts===0 && calibration.minimumLevel===20
    && calibration.anchorEligibility==="minimum-five-finite-refined-charts-v1"
    && same(calibration.originalOfficialCalibration,original),"Spread median policy or source differs.");
  for(const [mode,key] of MODES) {
    const anchors:Obj[]=[];populations[mode]={};
    for(const [level,folder] of Object.entries(support[mode])) {
      const rows=preview[key].filter(r=>folder.populationChartIds.includes(r.chartId)),branches:Record<string,number>={};
      for(const row of rows){const branch=row.scoringVariants!.sss!.scoringRefinement!.branch;branches[branch]=(branches[branch]??0)+1;}
      const record={level:Number(level),supported:folder.basis==="folder",populationChartIds:folder.populationChartIds,mixedFirstPassChartIds:folder.mixedFirstPassChartIds,
        branches,possiblyTruncatedCharts:rows.filter(r=>r.scoringVariants!.sss!.scoringRefinement!.fullBoard.possiblyTruncated).length,
        fewerThanFiveScores:rows.filter(r=>r.scoringVariants!.sss!.scoringRefinement!.fullBoard.records<5).length,
        medianLatent:rows.length?median(rows.map(r=>latents.get(r.chartId)!)):null};
      obj(populations[mode])[level]=record;
      if(record.supported)anchors.push({level:Number(level),cost:record.medianLatent,referenceCharts:rows.length,chartIds:folder.populationChartIds,difficulty:Number(level)+.5});
    }
    const valid=anchors.length>=3 && anchors[0].level===20 && anchors.every((a,i)=>finite(a.cost)&&(!i||Number(a.cost)>Number(anchors[i-1].cost)));
    const c=validateFinalCurve(curves[mode],3);
    need(valid?c && errors[mode]===null && same(c.anchors,anchors):c===null && typeof errors[mode]==="string","Continuous rare median anchors or availability differ.");
    for(let i=0;i<preview[key].length;i++) {
      const row=preview[key][i];if(row.level<20)continue;
      const metric=obj(row.scoringVariants!.sss!),previous=obj(adjusted[key][i].scoringVariants!.sssplus);
      for(const field of ["evidenceStatus","scoringProvisional","scoringProvisionalReasons","scoringExtrapolated"])if(field in previous)metric[field]=structuredClone(previous[field]);
      const initial=obj(metric.initialFit);
      for(const field of FIELDS){const raw=initial[field],v=raw===null||!c?null:inverseFinalCost(forward(Number(raw),obj(original[mode])),c);initial[field]=v;metric[field]=compressDifficulty(v,row.level);
        if(field!=="estimatedDifficulty"){initial[field.replace("difficulty","difficultyDelta")]=v===null?null:v-row.level-.5;metric[field.replace("difficulty","difficultyDelta")]=metric[field]===null?null:Number(metric[field])-row.level-.5;}}
      Object.assign(initial,derived(initial.estimatedDifficulty as number|null,row.level));Object.assign(metric,derived(metric.estimatedDifficulty as number|null,row.level));
      if(!c)Object.assign(metric,{evidenceStatus:"Unrated",scoringUnratedReason:"Final SSS+ median calibration unavailable: "+errors[mode]});
    }
  }
  need(same(calibration.populations,populations),"Continuous rare median population differs.");
  const residuals=sssCenteringFolders(preview,false);
  need(same(calibration.residualFolders,residuals),"Continuous rare residual median offsets differ.");
  need(/^[a-f0-9]{64}$/.test(String(calibration.basePayloadSha256)) && same(calibration,{...obj(base.summary.method.sssplusMedianCalibration),
    basePayloadSha256:calibration.basePayloadSha256,populations,calibration:curves,calibrationErrors:errors,residualFolders:residuals}),
    "Continuous rare final median contract differs from its historical v3 policy.");
  for(const [mode,key] of MODES)for(const row of preview[key]) {
    if(row.level<20)continue;
    const c=curves[mode]===null?null:obj(curves[mode]),old=obj(original[mode]),midpoint=row.level+.5,ref=row.scoringVariants!.sss!.scoringRefinement!;
    const project=(range:Obj|null):Obj|null=>!range||!c?null:Object.fromEntries(["lower","upper"].map(side=>[side,compressDifficulty(inverseFinalCost(forward(midpoint+2*(Number(range[side])-midpoint),old),c),row.level)]));
    const metric=expectedScoring(row.scoringVariants!.sss!,row.level,residuals[mode][String(row.level)],project(ref.range?obj(ref.range):null));
    const band=project(ref.rare?{lower:ref.rare.upperBandLow,upper:ref.rare.upperBandHigh}:null),folder=residuals[mode][String(row.level)];
    obj(metric.scoringFolderCentering).effectiveRareBand=band?Object.fromEntries(Object.entries(band).map(([k,v])=>[k,Number(v)+folder.finalShift])):null;
    const effect=latents.get(row.chartId)!,limits=c?(c.anchors as unknown[]).map(obj):[];
    metric.scoringMedianCalibration={version:3,method:"sssplus-refined-median-calibration-v3",source:"official",mode,refinedLatent:effect,anchorKind:"derived-provisional-full-folder-median",
      anchor:obj(populations[mode])[String(row.level)],calibrationError:errors[mode],preliminaryExtrapolated:metric.scoringExtrapolated??false,
      finalExtrapolated:effect===null||!limits.length?null:effect<Number(limits[0].cost)||effect>Number(limits.at(-1)!.cost),
      intervals:"existing-player-bootstrap-fixed-final-curve-residual-and-tail-no-anchor-uncertainty",finalCurveScale:c?structuredClone(c.finalScale):null};
    if(row.chartId in adjustments)metric.scoringProvisionalReasons=[...new Set([...(metric.scoringProvisionalReasons as string[]).filter(r=>r!=="rare-target-upper-band-policy"),"conditional-pooled-prior-estimate"])].sort();
    row.scoringVariants!.sss=metric as unknown as ScoringVariant;
    row.scoringVariants!.sssplus=metric as unknown as ScoringVariant;
    row.sssplusTailBaseline={scoring:metric as unknown as ScoringVariant,pumbility:row.pumbilityVariants!.sssplus!};
  }
  return preview;
}

const BOUNDARY_POLICY={version:1,method:"one-sided-conditional-score-gap-v1",minimumRecords:200,minimumGapToValidationMae:1.25,
  slopeMultipliers:[.75,1,1.25],endpointAdjustment:"toward-target-by-mode-heldout-mae",minimumStableDistance:2,distanceCeiling:2,
  formalInterval:false,interpretation:"conditional-sensitivity-diagnostic-not-confidence-interval"};
function boundaryCandidates(preview:AnalysisPayload,base:AnalysisPayload,method:Obj):Obj {
  const result:Obj={},medianPolicy=obj(method.medianCalibration),curves=obj(medianPolicy.calibration),folders=obj(medianPolicy.residualFolders);
  const gaps=obj(obj(obj(base.summary.method.sssplusRefinement).gapCalibration).official);
  for(const [mode,key] of MODES)for(const row of preview[key]) {
    if(row.level<20)continue;
    const metric=row.scoringVariants!.sssplus!,ref=metric.scoringRefinement!,branch=ref.branch;
    if(branch!=="all-target"&&branch!=="no-target")continue;
    const boundary=ref.boundary!,board=ref.fullBoard,range=ref.range,point=metric.estimatedDifficulty,delta=point===null?null:point-row.level-.5;
    const fit=obj(gaps[mode]),gap=boundary?.gapPoints,mae=fit.validationMeanAbsoluteError,ratio=finite(gap)&&finite(mae)&&mae>0?gap/mae:null;
    const sources=Object.entries(metric.scoringSourceStates??{}).sort(([a],[b])=>a<b?-1:1).filter(([,s])=>s.status==="available").map(([source,s])=>({source,records:s.rawRowCount??0,cutoffScore:s.cutoffScore,
      possiblyTruncated:!!s.possiblyTruncated,sameSide:finite(s.cutoffScore)&&(branch==="all-target"?s.cutoffScore>=995000:s.cutoffScore<995000)}));
    const slopes:Obj[]=[],slope=fit.pointsPerLatentUnit;
    if(finite(slope)&&slope>0)for(const factor of BOUNDARY_POLICY.slopeMultipliers)slopes.push({pointsPerLatentUnit:slope*factor,kind:"multiplier",multiplier:factor});
    for(const item of (fit.candidates??[]) as unknown[]){const s=obj(item);if(finite(s.pointsPerLatentUnit)&&s.pointsPerLatentUnit>0)slopes.push({pointsPerLatentUnit:s.pointsPerLatentUnit,kind:"cached-robust-scale",softL1Scale:s.softL1Scale??null});}
    const folder=obj(obj(folders[mode])[String(row.level)]),direction=branch==="all-target"?-1:1,c=curves[mode]===null?null:obj(curves[mode]),stress:Obj[]=[];
    if(c&&finite(gap)&&finite(mae)&&finite(boundary?.edgeLatent))for(const candidate of slopes)for(const endpointGap of [gap,Math.max(0,gap-mae)]) {
      const latent=boundary.edgeLatent+direction*endpointGap/Number(candidate.pointsPerLatentUnit),raw=inverseFinalCost(latent,c)+Number(folder.initialShift),difficulty=compressDifficulty(raw,row.level)!;
      stress.push({...candidate,gapPoints:endpointGap,difficulty,distance:direction*(difficulty-row.level-.5)});
    }
    const stable=!!stress.length&&stress.every(s=>Number(s.distance)>=2),reasons:string[]=[];
    if(board.records<200)reasons.push("fewer-than-200-records");
    if(!["comparable","same","single-source-known"].includes(metric.scoringComparability??""))reasons.push("unknown-or-changed-chart");
    if(!range?.supported||range.kind!=="observed"||!same(range.donorLevels,[row.level]))reasons.push("unsupported-own-folder-range");
    if(folder.basis!=="folder")reasons.push("unsupported-derived-folder-centering");
    if(!fit.supported)reasons.push("unsupported-mode-gap-calibration");
    if(delta===null||direction*delta<2)reasons.push("inside-two-point-boundary-or-opposite-side");
    if(ratio===null||ratio<=1.25)reasons.push("gap-not-above-1.25-times-validation-mae");
    if(!sources.length||!sources.every(s=>s.records>=200&&s.sameSide))reasons.push("source-cutoff-support-unavailable-or-opposite-side");
    if(!stable)reasons.push("conditional-sensitivity-not-stably-beyond-two");
    const minimum=stress.length?Math.min(...stress.map(s=>Number(s.distance))):null;
    result[row.chartId]={eligible:!reasons.length,strength:minimum??0,rejectionReasons:reasons,exceptionBasis:BOUNDARY_POLICY.method,
      oneSidedEvidence:{records:board.records,gapPoints:gap,validationMAE:mae,minimumGapRatio:1.25,gapToMAERatio:ratio,calibrationSupported:!!fit.supported,sourceSupport:sources,
        conditionalSensitivity:{method:"fixed-curve-gap-slope-and-endpoint-v1",slopeMultipliers:BOUNDARY_POLICY.slopeMultipliers,endpointAdjustmentPoints:mae,
          minimumDistance:minimum,sameSide:!!stress.length&&stress.every(s=>Number(s.distance)>0),beyondTwo:stable,scenarios:stress},formalInterval:false,
        sourceRemoval:sources.length===1?"unavailable-single-known-source":"source-cutoff-same-side-only",possiblyTruncated:!!board.possiblyTruncated}};
  }
  return result;
}

function groups(payload:AnalysisPayload):ChartResult[][] {
  const result=new Map<string,ChartResult[]>();for(const [,key] of MODES)for(const row of payload[key])if(row.level>=20&&finite(row.scoringVariants!.sssplus!.estimatedDifficulty)){
    const id=`${key}:${row.level}`;if(!result.has(id))result.set(id,[]);result.get(id)!.push(row);
  }return [...result.values()];
}
function selectBoundary(preview:AnalysisPayload,candidates:Obj,ordinary:Set<string>):Set<string> {
  const selected=new Set(ordinary),tails:ChartResult[][][]=[];
  for(const rows of groups(preview)) {
    const level=rows[0].level,ordered=rows.toSorted((a,b)=>a.scoringVariants!.sssplus!.estimatedDifficulty!-b.scoringVariants!.sssplus!.estimatedDifficulty!||(a.chartId<b.chartId?-1:1));
    const middle=new Set([ordered[Math.floor((ordered.length-1)/2)].scoringVariants!.sssplus!.estimatedDifficulty,ordered[Math.floor(ordered.length/2)].scoringVariants!.sssplus!.estimatedDifficulty]);
    for(const direction of [-1,1]) {
      const outer=ordered.filter(r=>direction*(r.scoringVariants!.sssplus!.estimatedDifficulty!-level-.5)>0).sort((a,b)=>Math.abs(b.scoringVariants!.sssplus!.estimatedDifficulty!-level-.5)-Math.abs(a.scoringVariants!.sssplus!.estimatedDifficulty!-level-.5)||(a.chartId<b.chartId?-1:1));
      const blocks:ChartResult[][]=[];let start=0;
      while(start<outer.length){let end=start+1;const value=outer[start].scoringVariants!.sssplus!.estimatedDifficulty;while(end<outer.length&&outer[end].scoringVariants!.sssplus!.estimatedDifficulty===value)end++;
        const block=outer.slice(start,end);if(middle.has(value)||!block.every(r=>r.chartId in candidates&&obj(candidates[r.chartId]).eligible))break;blocks.push(block);start=end;}
      if(blocks.length)tails.push(blocks);
    }
  }
  let remaining=Math.max(0,10-selected.size);
  const id=(blocks:ChartResult[][])=>blocks[0].map(r=>r.chartId).sort()[0];
  while(tails.length&&remaining){tails.sort((a,b)=>Math.min(...b[0].map(r=>Number(obj(candidates[r.chartId]).strength)))-Math.min(...a[0].map(r=>Number(obj(candidates[r.chartId]).strength)))||(id(a)<id(b)?-1:1));
    const blocks=tails.shift()!,block=blocks.shift()!,fresh=block.filter(r=>!selected.has(r.chartId));if(fresh.length>remaining)continue;
    for(const r of fresh)selected.add(r.chartId);remaining-=fresh.length;if(blocks.length)tails.push(blocks);}
  return selected;
}

const RARE_POLICY={version:1,method:"fixed-ability-pooled-prior-chart-v1",source:"official",minimumLevel:20,
  abilities:"frozen-full-fit-deterministic-point-replay",weighting:"one-total-weight-per-player-in-source-mode",
  prior:"Gaussian-replaces-chart-penalty-not-added-to-fitted-effect",priorCenter:"frozen-original-curve-at-folder-midpoint",
  scaleSelection:"conditional-Laplace-marginal-likelihood",normalizedScaleGrid:[.25,.5,1,2,4,8],minimumLocalCharts:20,
  minimumDonorFolders:3,poolPriorCharts:20,donors:"comparable-mixed-20-contributors-five-per-outcome",
  fallback:"frozen-selected-chart-regularization",unsupportedScale:"insufficient-donors-or-pooled-grid-boundary",conditionalOnly:true,predictiveValidation:"not-performed-full-fit-abilities",
  intervals:"unavailable-new-conditional-estimator"};
function validateRare(base:AnalysisPayload,method:Obj) {
  const policy=obj(method.rarePolicy),modes=obj(policy.modes),adjustments=obj(method.rareAdjustments);
  need(same({...policy,modes:undefined},{...RARE_POLICY,modes:undefined}),"Continuous rare prior policy differs.");
  const original=obj(obj(obj(base.summary.method.sssplusScoring).calibration).official);
  for(const [mode,key] of MODES) {
    const p=obj(modes[mode]),levels=p.donorLevels;
    need(Array.isArray(levels) && levels.every((v,i)=>Number.isInteger(v) && Number(v)>=20 && (!i || Number(v)>Number(levels[i-1])))
      && finite(p.fallbackRegularization) && p.fallbackRegularization>0,"Rare prior donor levels or fallback differ.");
    const donorIds=obj(p.donorChartIds),local=obj(p.local);
    for(const level of levels) {
      const ids=donorIds[String(level)];need(Array.isArray(ids)&&ids.length>=20&&new Set(ids).size===ids.length,"Rare prior donor folder is too small.");
      for(const cid of ids) {
        const row=base[key].find(r=>r.chartId===cid),s=row?.scoringVariants?.sssplus?.scoringFirstPass;
        need(row?.level===level && s && s.nContributors>=20 && Math.min(s.scoringTargetSuccesses??0,s.scoringBelowTarget??0)>=5
          && ["comparable","same","single-source-known"].includes(s.scoringComparability??""),"Rare prior donor lacks two-sided comparable support.");
      }
    }
    const checkScale=(value:unknown):Obj=>{
      const s=obj(value),candidates=s.candidates;
      need(Array.isArray(candidates)&&candidates.length===6,"Rare prior scale grid differs.");
      const grid=RARE_POLICY.normalizedScaleGrid;
      const rows=candidates.map(obj);need(rows.every((c,i)=>c.scale===grid[i]&&finite(c.objective)),"Rare prior objectives are invalid.");
      const selected=rows.toSorted((a,b)=>Number(a.objective)-Number(b.objective)||Number(a.scale)-Number(b.scale))[0];
      need(s.scale===selected.scale && s.boundarySelected===[.25,8].includes(Number(s.scale)),"Rare prior scale was not selected by the likelihood grid.");return s;
    };
    const pooled=p.pooled===null?null:checkScale(p.pooled);
    need(pooled?levels.length>=3:levels.length<3,"Rare prior pooling support differs.");
    if(pooled)for(const level of levels)checkScale(local[String(level)]);
    for(const row of base[key]) {
      const s=row.scoringVariants!.sssplus!,rare=s.scoringRefinement?.rare;
      if(row.level<20 || s.scoringEstimateBranch!=="rare-target")continue;
      need(row.chartId in adjustments && rare,"Continuous rare estimate is missing.");
      const adjustment=obj(adjustments[row.chartId]),m=obj(adjustment.metadata),effect=Number(adjustment.effect),mean=forward(row.level+.5,obj(original[mode]));
      let variance=1/Number(p.fallbackRegularization),source="frozen-regularization";
      const usablePool=pooled!==null&&!pooled.boundarySelected;
      if(usablePool) {
        let scale=Number(pooled.scale)**2;source="pooled";
        if(String(row.level) in local&&!obj(local[String(row.level)]).boundarySelected){const n=(donorIds[String(row.level)] as unknown[]).length,w=n/(n+20);scale=w*Number(obj(local[String(row.level)]).scale)**2+(1-w)*scale;source="local-pooled";}
        const increment=forward(row.level+1.5,obj(original[mode]))-mean;variance=scale*increment**2;
      }
      need(m.version===1&&m.method===RARE_POLICY.method&&m.conditionalOnly===true&&m.intervals===RARE_POLICY.intervals
        &&same(m.priorMean,mean)&&same(m.priorVariance,variance)&&m.priorSource===source
        &&same(m.priorDonorLevels,usablePool?levels:[])&&same(m.originalConditionalEffect,rare.conditionalEffect)
        &&same(m.weightedSuccesses,rare.weightedSuccesses)&&m.observations===s.nContributors
        &&same(m.posteriorEffect,effect)&&finite(m.expectedAtPosterior)&&finite(m.likelihoodScore)&&finite(m.priorScore)&&finite(m.solverResidual),
        "Rare posterior support, prior or frozen conditional evidence differs.");
      need(effect>=Math.min(mean,rare.conditionalEffect)-1e-8 && effect<=Math.max(mean,rare.conditionalEffect)+1e-8
        &&same(m.priorScore,(effect-mean)/variance)&&same(m.likelihoodScore,Number(m.expectedAtPosterior)-Number(m.weightedSuccesses))
        &&same(m.solverResidual,Number(m.likelihoodScore)-Number(m.priorScore))&&Math.abs(Number(m.solverResidual))<1e-7,
        "Rare posterior does not satisfy its conditional-prior stationary equation.");
    }
  }
}

function validateMean(row:ChartResult,s:Obj,p:Obj,finalValue:number|null,finalLow:number|null,finalHigh:number|null) {
  const c=obj(row.tierMetrics!.clearing),rawS=obj(s.initialFit),rawC=obj(c.initialFit),rawP=obj(p.initialFit),statuses=["Unrated","Insufficient","Provisional","Published"];
  const mean=finalValue===null||c.estimatedDifficulty===null?null:(finalValue+Number(c.estimatedDifficulty))/2;
  const rawMean=rawS.estimatedDifficulty===null||rawC.estimatedDifficulty===null?null:(Number(rawS.estimatedDifficulty)+Number(rawC.estimatedDifficulty))/2;
  const status=mean===null?"Unrated":statuses[Math.min(statuses.indexOf(String(s.evidenceStatus)),statuses.indexOf(String(c.evidenceStatus)))];
  need(same(p.estimatedDifficulty,mean)&&same(rawP.estimatedDifficulty,rawMean)&&p.aggregation==="average"&&p.evidenceStatus===status
    &&p.scoringSupportCount===s.nContributors&&p.clearingSupportCount===c.ratedClearCount,"Spread Pumbility mean, status or support differs.");
  need(p.unratedReason===(finalValue===null?s.scoringUnratedReason:c.estimatedDifficulty===null?"Clearing difficulty is unavailable.":null),"Spread Pumbility unavailable reason differs.");
  const interval=s.scoringMethod!=="target-attainment-rasch-v1"?"unavailable-after-sss-refinement-v1":"conditional-target-tail-fixed-clearing-and-calibration-v1";
  need(p.intervalMethod===interval,"Spread Pumbility interval method differs.");
  for(const [side,endpoint] of [["Low",finalLow],["High",finalHigh]] as const) {
    const field=`difficultyCi95${side}`;
    need(same(p[field],mean===null||endpoint===null?null:(endpoint+Number(c.estimatedDifficulty))/2)
      &&same(rawP[field],rawMean===null||rawS[field]===null?null:(Number(rawS[field])+Number(rawC.estimatedDifficulty))/2),"Spread Pumbility endpoint differs.");
  }
  checkFit(p,row.level);checkFit(rawP,row.level);
}

/** Independently replay the local overlay while validating its frozen predecessor. */
export function validateSpreadAnalysisPayload(payload:AnalysisPayload,validateBase:(base:AnalysisPayload)=>unknown):AnalysisPayload {
  const base=recoverSpreadBaseline(payload);validateBase(base);
  const method=obj(payload.summary.method[NS]);
  need(method.version===1&&method.method==="sssplus-continuous-rare-asinh-spread-v1"&&method.baseSchemaVersion===53&&method.localOnly===true
    &&/^[a-f0-9]{64}$/.test(String(method.basePayloadSha256))&&method.target===995000&&method.source==="official"&&method.minimumLevel===20
    &&method.scale===.55&&method.compressionApplications===1&&method.compressionFactor===.5&&method.dataFetches===0&&method.fullPlayerRefits===0
    &&same(method.stageOrder,["recover-frozen-refined-latent","replace-rare-band-with-conditional-posterior","supported-folder-median-anchors","final-v3-curve",
      "residual-median-offset","single-midpoint-compression","midpoint-asinh-spread","bounded-global-tail-policy","component-mean-and-ranks"]),"Spread policy, scale, source or stage order differs.");
  validateRare(base,method);
  const preview=replayMedian(base,method),candidates=boundaryCandidates(preview,base,method);
  need(same(method.boundaryPolicy,BOUNDARY_POLICY)&&same(method.boundaryCandidates,candidates),"Boundary exception policy or frozen sensitivity replay differs.");
  const ordinary=targetTailSelection(preview),officialIds=new Set(preview.singles.concat(preview.doubles).filter(r=>r.level>=20).map(r=>r.chartId));
  const ordinarySelected=new Set([...ordinary.selected].filter(id=>officialIds.has(id))),selected=selectBoundary(preview,candidates,ordinarySelected);
  const finalPoints=new Map<string,number|null>(),maps=new Map<string,(value:number|null)=>number|null>();
  for(const [,key] of MODES)for(const row of preview[key])if(row.level>=20) {
    const exceptional=selected.has(row.chartId),boundary=exceptional&&row.chartId in candidates;
    const map=(value:number|null)=>boundary?(value===null?null:row.level+.5+Math.max(-2,Math.min(2,value-row.level-.5))):targetTailRating(spreadRating(value,row.level),row.level,exceptional);
    maps.set(row.chartId,map);finalPoints.set(row.chartId,map(row.scoringVariants!.sssplus!.estimatedDifficulty));
  }
  const ranges=new Map<string,Obj>();for(const rows of groups(preview)) {
    const values=rows.map(r=>finalPoints.get(r.chartId)!);for(const row of rows)ranges.set(row.chartId,{lower:Math.min(...values),upper:Math.max(...values)});
    const ordered=rows.toSorted((a,b)=>a.scoringVariants!.sssplus!.estimatedDifficulty!-b.scoringVariants!.sssplus!.estimatedDifficulty!);
    need(ordered.every((r,i)=>!i||finalPoints.get(r.chartId)!>=finalPoints.get(ordered[i-1].chartId)!),"Spread exceptions inverted chart ordering.");
    if(rows.some(r=>r.scoringVariants!.sssplus!.scoringFolderCentering?.basis==="folder"))need(same(median(values),rows[0].level+.5),"Spread shifted a supported folder median.");
  }
  const pre:number[]=[],post:number[]=[];let changed=0,displayed=0;
  for(const [,key] of MODES) {
    for(let i=0;i<payload[key].length;i++) {
      const row=payload[key][i],old=base[key][i],rebuilt=preview[key][i];
      if(row.level<20){
        need(same(row.sssplusTailBaseline,old.sssplusTailBaseline),"Spread altered submitted pre-tail evidence.");
        for(const field of ["scoringVariants","pumbilityVariants"] as const)need(same(withoutRanks(row[field]!.sssplus),withoutRanks(old[field]!.sssplus)),"Spread altered submitted target values or evidence.");continue;
      }
      const before=obj(rebuilt.scoringVariants!.sssplus),expected=structuredClone(before),level=row.level,midpoint=level+.5,map=maps.get(row.chartId)!,info=ordinary.evidence.get(row.chartId)??{exceptionEligible:false,rejectionReasons:["unrated"],conservativeDistance:null,protectedMedian:false};
      const detail:Obj={version:1,method:"midpoint-asinh-pre-tail-v1",scale:.55,midpoint,
        preSpreadDifficulty:before.estimatedDifficulty,preSpreadDifficultyCi95Low:before.difficultyCi95Low,preSpreadDifficultyCi95High:before.difficultyCi95High,
        rare:row.chartId in obj(method.rareAdjustments)?obj(obj(method.rareAdjustments)[row.chartId]).metadata:null,
        boundaryEvidence:candidates[row.chartId]??null,spreadDifficulty:spreadRating(before.estimatedDifficulty as number|null,level),
        spreadDifficultyCi95Low:spreadRating(before.difficultyCi95Low as number|null,level),spreadDifficultyCi95High:spreadRating(before.difficultyCi95High as number|null,level)};
      const tail:Obj={version:1,method:"target-midpoint-soft-tail-global-exceptions-v1",midpoint,preTailDifficulty:detail.spreadDifficulty,
        preTailDifficultyCi95Low:detail.spreadDifficultyCi95Low,preTailDifficultyCi95High:detail.spreadDifficultyCi95High,
        ordinaryDifficulty:targetTailRating(detail.spreadDifficulty as number|null,level),exceptionSelected:selected.has(row.chartId),...structuredClone(info)};
      if(row.chartId in candidates){tail.boundaryEvidence=structuredClone(candidates[row.chartId]);if(selected.has(row.chartId))Object.assign(tail,{exceptionEligible:true,rejectionReasons:[],conservativeDistance:obj(candidates[row.chartId]).strength});}
      for(const field of FIELDS)expected[field]=map(before[field] as number|null);
      Object.assign(expected,derived(expected.estimatedDifficulty as number|null,level));
      for(const side of ["Low","High"]){const field=`difficultyCi95${side}`;expected[`difficultyDeltaCi95${side}`]=expected[field]===null?null:Number(expected[field])-midpoint;}
      if(expected.difficultyCi95Low!==null)expected.intervalMethod="conditional-asinh-tail-fixed-calibration-no-anchor-uncertainty-v1";
      obj(expected.scoringFolderCentering).effectiveRareBand=null;expected.scoringSpreadPolicy=detail;
      Object.assign(tail,{finalDifficulty:expected.estimatedDifficulty,finalDifficultyCi95Low:expected.difficultyCi95Low,finalDifficultyCi95High:expected.difficultyCi95High});
      if(ranges.has(row.chartId))tail.finalFolderRange=ranges.get(row.chartId);expected.scoringTailPolicy=tail;
      const actual=obj(row.scoringVariants!.sssplus);
      const actualFields=withoutRanks(actual),expectedFields=withoutRanks(expected);
      const differingField=[...new Set([...Object.keys(actualFields),...Object.keys(expectedFields)])].find(k=>!same(actualFields[k],expectedFields[k]));
      need(!differingField,`Spread point, interval, exception evidence or preserved fit differs for ${row.chartId}: ${differingField}.`);
      need(same(withoutRanks(row.sssplusTailBaseline!.scoring),withoutRanks(before)),"Spread centered snapshot differs from final median replay.");
      checkFit(actual,level);checkFit(obj(actual.initialFit),level);
      validateMean(row,actual,obj(row.pumbilityVariants!.sssplus),expected.estimatedDifficulty as number|null,expected.difficultyCi95Low as number|null,expected.difficultyCi95High as number|null);
      const oldTailMap=(v:number|null)=>targetTailRating(v,level,ordinarySelected.has(row.chartId));
      validateMean(row,before,obj(row.sssplusTailBaseline!.pumbility),oldTailMap(before.estimatedDifficulty as number|null),oldTailMap(before.difficultyCi95Low as number|null),oldTailMap(before.difficultyCi95High as number|null));
      if(finite(expected.estimatedDifficulty)){
        pre.push(Number(tail.preTailDifficulty)-midpoint);post.push(expected.estimatedDifficulty-midpoint);
        if(tail.preTailDifficulty!==expected.estimatedDifficulty)changed++;
        if(Math.abs(Math.trunc(expected.estimatedDifficulty*10+1e-9)/10-midpoint)>=2)displayed++;
      }
    }
    const ranked=structuredClone(payload[key]);for(const row of ranked){row.scoringVariants!.sss=row.scoringVariants!.sssplus;row.pumbilityVariants!.sss=row.pumbilityVariants!.sssplus;}validateRanks(ranked as unknown as Obj[]);
  }
  const counts={ratedCharts:post.length,changedCharts:changed,preTailAbsAbove1_5:pre.filter(d=>Math.abs(d)>1.5).length,preTailAbsAtLeast2:pre.filter(d=>Math.abs(d)>=2).length,
    finalAbsAbove1_5:post.filter(d=>Math.abs(d)>1.5).length,finalAbsAtLeast2:post.filter(d=>Math.abs(d)>=2).length,finalAbsAbove2_5:post.filter(d=>Math.abs(d)>2.5).length,
    displayedAbsAtLeast2:displayed,preTailMinDelta:pre.length?Math.min(...pre):null,preTailMaxDelta:pre.length?Math.max(...pre):null,finalMinDelta:post.length?Math.min(...post):null,finalMaxDelta:post.length?Math.max(...post):null};
  const tailPolicy={version:1,method:"target-midpoint-soft-tail-global-exceptions-v1",identityRadius:1.5,ordinaryAsymptote:1.9,exceptionThreshold:2,exceptionLimit:10,exceptionCeiling:2.5,
    targetKey:"sssplus",selectedChartIds:[...selected].sort(),counts,intervalMethod:"conditional-target-tail-fixed-selection-and-calibration-v1",selection:"continuous-spread-outer-block-global-budget-v1"};
  need(same(method.tailPolicy,tailPolicy)&&same(payload.summary.method.sssplusTailPolicy,tailPolicy),"Spread tail aggregate counts or selection differs.");
  need(selected.size<=10&&counts.finalAbsAtLeast2<=10&&displayed<=10&&!counts.finalAbsAbove2_5,"Spread exceeded its global outlier budget.");
  return payload;
}
