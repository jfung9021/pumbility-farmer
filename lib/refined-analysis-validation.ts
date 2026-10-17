import { checkFit, inverse, validateRanks } from "./sss-analysis-validation.ts";
import { compressDifficulty, FIT_FIELDS } from "./tier-postprocessing.ts";
import type { AnalysisPayload } from "./types";

type Obj = Record<string, unknown>;
const STATUS = ["Unrated", "Insufficient", "Provisional", "Published"];
const BOUNDARY = "sss-boundary-score-gap-v1", RARE = "sss-rare-success-upper-band-v1";
function need(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
function obj(value: unknown): Obj { need(value && typeof value === "object" && !Array.isArray(value), "Missing refined contract object."); return value as Obj; }
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function count(value: unknown): value is number { return Number.isInteger(value) && Number(value) >= 0; }
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
function median(values: number[]): number { const sorted = values.toSorted((a,b) => a-b), middle = Math.floor(sorted.length / 2); return sorted.length % 2 ? sorted[middle] : (sorted[middle-1] + sorted[middle])/2; }

export function noSssBoundaryEndpoint(full: Obj, boundary: Obj, version: unknown, detailsVersion: unknown, scoreTarget = 990000): number {
  if (version === 1) return Number(full.highestScore);
  const top=full.topScores;
  need(version===2 && detailsVersion===2 && count(full.records) && full.records>0
    && Array.isArray(top) && top.length===Math.min(3,full.records)
    && top.every((score,i)=>finite(score) && score>=0 && score<scoreTarget && (!i || score<=Number(top[i-1])))
    && top[0]===full.highestScore && Number(top.at(-1))>=Number(full.lowestScore)
    && boundary.endpointPolicy==="mean-top-3-available-v1" && boundary.endpointScoreCount===top.length,
    "No-SSS top-three scores or support differ.");
  return top.reduce((sum:number,score:unknown)=>sum+Number(score),0)/top.length;
}
export function forward(difficulty: number, calibration: Obj): number {
  const anchors = (calibration.anchors as unknown[]).map(obj), offset = calibration.offset as number;
  let i = 0;
  if (difficulty > Number(anchors.at(-1)!.difficulty)) i = anchors.length - 2;
  else while (i < anchors.length - 2 && difficulty >= Number(anchors[i+1].difficulty)) i++;
  const a = anchors[i], b = anchors[i+1], beta = Math.log1p((Number(b.cost)-Number(a.cost))/(Number(a.cost)-offset))/(Number(b.level)-Number(a.level));
  if (difficulty < Number(anchors[0].difficulty) || difficulty > Number(anchors.at(-1)!.difficulty)) {
    const endpoint = difficulty < Number(anchors[0].difficulty) ? anchors[0] : anchors.at(-1)!;
    return Number(endpoint.cost) + beta*(Number(endpoint.cost)-offset)*(difficulty-Number(endpoint.difficulty));
  }
  return Number(a.cost) + (Number(a.cost)-offset)*Math.expm1(beta*(difficulty-Number(a.difficulty)));
}

/** Restore the exact schema-49 view; its historical validator still checks the original fit. */
export function recoverRefinedFirstPass(payload: AnalysisPayload): AnalysisPayload {
  const base = structuredClone(payload);
  base.schemaVersion = 49;
  for (const key of ["sssRefinement", "officialHighLevelClearing", "refinedTiers"]) delete base.summary.method[key];
  for (const key of ["singles", "doubles"] as const) for (const typedRow of base[key]) {
    const row = typedRow as unknown as Obj, variants = obj(row.scoringVariants), p = obj(row.pumbilityVariants), frozen = obj(row.refinementBaseline);
    variants.sss = obj(obj(variants.sss!).scoringFirstPass);
    p.sss = obj(frozen.sssPumbility);
    if ("clearing" in frozen) {
      obj(row.tierMetrics).clearing = obj(frozen.clearing);
      obj(row.tierMetrics).pumbility = obj(frozen.generalPumbility);
      p.general = structuredClone(frozen.generalPumbility);
    }
    delete row.refinementBaseline;
  }
  return base;
}

function validateRanges(refinement: Obj, base: AnalysisPayload) {
  const ranges = obj(refinement.ranges);
  for (const source of ["submitted", "official"]) for (const [mode, key] of [["Single", "singles"], ["Double", "doubles"]] as const) {
    const folders = obj(obj(ranges[source])[mode]);
    for (const [levelText, value] of Object.entries(folders)) {
      const range = obj(value), level = Number(levelText);
      need(range.source === source && range.mode === mode && range.level === level && finite(range.lower) && finite(range.upper)
        && range.upper > range.lower && same(range.lowerOffset, range.lower-level-.5) && same(range.upperOffset, range.upper-level-.5)
        && Array.isArray(range.donorLevels) && range.donorLevels.length >= 1 && range.donorLevels.length <= 2, "Invalid frozen SSS range.");
      const donors = range.donorLevels as number[], charts = base[key].filter(r => donors.includes(r.level)
        && (r.level >= 20) === (source === "official") && r.scoringVariants!.sss!.estimatedDifficulty !== null && r.scoringVariants!.sss!.evidenceStatus !== "Unrated");
      need(same((range.referenceChartIds as string[]).toSorted(), charts.map(r => r.chartId).toSorted()), "Frozen range population changed.");
      if (range.kind === "observed") {
        need(range.supported === true && range.provisional === false && charts.length >= 5 && same(donors,[level])
          && same(range.lower,Math.min(...charts.map(r => r.scoringVariants!.sss!.estimatedDifficulty!)))
          && same(range.upper,Math.max(...charts.map(r => r.scoringVariants!.sss!.estimatedDifficulty!))), "Observed range edges changed.");
      } else {
        need(range.supported === false && range.provisional === true && ["nearest","interpolated"].includes(String(range.kind)), "Invalid donor range.");
        const first = obj(folders[String(donors[0])]), last = obj(folders[String(donors.at(-1)!)]);
        need(first.kind === "observed" && last.kind === "observed", "Range donor is not independently supported.");
        const ratio = donors.length === 2 ? (level-donors[0])/(donors[1]-donors[0]) : 0;
        for (const field of ["lowerOffset","upperOffset"]) need(same(range[field],Number(first[field])+ratio*(Number(last[field])-Number(first[field]))), "Borrowed range offsets differ.");
      }
    }
  }
}

/** Schema-50 validation includes frozen references, branch math, source dispatch and both means. */
export function validateRefinedAnalysisPayload(payload: AnalysisPayload, validateFirstPass: (base: AnalysisPayload) => unknown, scoreTarget = 990000): AnalysisPayload {
  need(payload.schemaVersion === 50, "Invalid refined generation.");
  const integration = obj(payload.summary.method.refinedTiers), refinement = obj(payload.summary.method.sssRefinement), official = obj(payload.summary.method.officialHighLevelClearing);
  need(integration.version === 1 && integration.method === "frozen-sss-second-pass-official-d26-clearing-v1" && integration.baseSchemaVersion === 49
    && /^[a-f0-9]{64}$/.test(String(integration.basePayloadSha256)) && integration.localOnly === true && integration.pumbilityAggregation === "average"
    && integration.compressionApplications === 1 && same(integration.clearingDispatch,{mode:"Double",minimumLevel:26}), "Refined integration policy differs.");
  need((refinement.version === 1 || refinement.version === 2) && refinement.method === "frozen-range-sss-second-pass-v1" && refinement.scoreTarget === scoreTarget
    && refinement.firstPassPayloadSha256 === integration.basePayloadSha256 && refinement.compressionApplications === 1
    && same(refinement.rangePolicy,{minimumRatedCharts:5,edges:"actual-finite-first-pass-min-max",borrow:"same-source-mode-offset-interpolation-or-nearest",frozenBeforeSecondPass:true}), "SSS second-pass policy differs.");
  if (refinement.version === 2) need(same(refinement.noSssEndpointPolicy,{method:"mean-top-available",maximumScores:3,minimumScores:1,selection:"deduplicated-full-board-before-history-filter"}), "No-SSS endpoint policy differs.");
  need(official.version === 1 && official.method === "official-d26-clearer-ability-v1" && official.source === "piuscores-official" && official.mix === "Phoenix2"
    && official.mode === "Double" && official.minimumLevel === 26 && official.percentile === .2 && official.difficultyDeltaScale === .7 && official.compressionFactor === .5
    && same(official.playerSkill,{method:"leave-one-chart-out-top-official-levels",topCharts:25,minimumOtherCharts:25,historyMinimumLevel:23,levelMidpointOffset:.5,separateModes:true})
    && obj(official.uncertainty).samples === 1000, "Official D26+ Clearing policy differs.");
  const base = recoverRefinedFirstPass(payload);
  validateFirstPass(base);
  validateRanges(refinement, base);
  const model = obj(payload.summary.method.sssScoring), ranges = obj(refinement.ranges);
  for (const key of ["singles","doubles"] as const) {
    for (let i=0;i<payload[key].length;i++) {
      const row = payload[key][i] as unknown as Obj, original = base[key][i] as unknown as Obj, level = Number(row.level), midpoint=level+.5;
      const variants=obj(row.scoringVariants), pumbilities=obj(row.pumbilityVariants), tiers=obj(row.tierMetrics), s=obj(variants.sss!), c=obj(tiers.clearing), raw=obj(s.initialFit);
      need(same(variants.general, obj(original.scoringVariants).general) && same(pumbilities.general,tiers.pumbility), "General compatibility mirror differs.");
      for (const [field,value] of Object.entries(original)) if (!["scoringVariants","pumbilityVariants","tierMetrics"].includes(field)) need(same(row[field],value), `General chart field changed: ${field}`);
      const isOfficial=key==="doubles" && level>=26;
      if (!isOfficial) need(same(c,obj(original.tierMetrics).clearing), "Clearing outside D26+ changed.");
      for (const metric of [s,c]) {
        const initial=obj(metric.initialFit);
        for (const field of ["estimatedDifficulty","difficultyCi95Low","difficultyCi95High"]) if (field in initial) need(same(metric[field],compressDifficulty(initial[field] as number|null,level)), "Refined compression differs.");
      }
      checkFit(s,level); checkFit(raw,level);
      const changed=s.scoringMethod===BOUNDARY || s.scoringMethod===RARE;
      if (changed) {
        const details=obj(s.scoringRefinement), full=obj(details.fullBoard), eligible=obj(details.modelEligible), range=obj(details.range), source=level>=20?"official":"submitted", mode=key==="singles"?"Single":"Double";
        need(s.evidenceStatus==="Provisional" && s.scoringProvisional===true && s.scoringUnratedReason===null
          && s.intervalMethod==="unavailable-second-pass-rule-v1" && s.scoringBootstrapValidReplicates===0
          && s.difficultyCi95Low===null && s.difficultyCi95High===null && s.scoringChartEffectCi95Low===null && s.scoringChartEffectCi95High===null,
          "Second-pass estimate retained unsupported intervals or status.");
        need(count(full.records) && full.records>0 && count(full.successes) && count(full.belowTarget) && full.records===Number(full.successes)+Number(full.belowTarget)
          && same(range,obj(obj(ranges[source])[mode])[String(level)]) && eligible.records===s.nContributors, "SSS full-board support or range differs.");
        const calibration=obj(obj(obj(model.calibration)[source])[mode]);
        if (s.scoringMethod===BOUNDARY) {
          const b=obj(details.boundary), no=s.scoringEstimateBranch==="no-sss";
          const endpoint = no ? noSssBoundaryEndpoint(full,b,refinement.version,details.version,scoreTarget) : Number(full.lowestScore);
          const gap=no?scoreTarget-endpoint:endpoint-scoreTarget;
          const scale=obj(obj(obj(refinement.gapCalibration)[source])[mode]);
          const edge=Number(no?range.upper:range.lower), initial=midpoint+2*(edge-midpoint), latent=forward(initial,calibration)+(no?1:-1)*gap/Number(scale.pointsPerLatentUnit);
          need((no?full.successes===0:full.belowTarget===0) && gap>=0 && scale.supported===true && finite(scale.pointsPerLatentUnit) && scale.pointsPerLatentUnit>0
            && same(b.endpointScore,endpoint) && same(b.gapPoints,gap) && same(b.edgeFinal,edge)
            && same(b.edgeInitial,initial) && same(b.targetLatent,latent) && same(raw.estimatedDifficulty,inverse(latent,calibration))
            && same(b.gapScalePointsPerLatentUnit,scale.pointsPerLatentUnit), "SSS boundary score-gap formula differs.");
        } else {
          const r=obj(details.rare), n=Number(eligible.records), successes=Number(eligible.successes), clip=(v:number)=>Math.min(1,Math.max(0,v));
          const weight=clip((5-successes)/4)*clip((n-5)/15)*clip((.10-successes/n)/.05), floor=Number(range.upper)-.05*(Number(range.upper)-Number(range.lower));
          const position=Number(r.candidateCount)>1?(Number(r.conditionalRank)-1)/(Number(r.candidateCount)-1):1, target=floor+position*(Number(range.upper)-floor);
          const value=Number(r.originalDifficulty)+weight*(Math.max(Number(r.originalDifficulty),target)-Number(r.originalDifficulty));
          need(successes>=1 && successes<=4 && full.successes===eligible.successes && Number(eligible.belowTarget)>0 && weight>0
            && same(r.blendWeight,weight) && same(r.upperBandLow,floor) && same(r.upperBandHigh,range.upper) && same(r.upperTarget,target)
            && same(s.estimatedDifficulty,value) && same(r.conditionalDifficulty,compressDifficulty(inverse(Number(r.conditionalEffect),calibration),level))
            && Math.abs(Number(r.likelihoodResidual))<1e-7, "Rare-SSS upper-band formula differs.");
        }
      } else {
        const first=obj(s.scoringFirstPass);
        for (const field of [...FIT_FIELDS,"intervalMethod","scoringChartEffect"]) if (!["levelRank","levelComparisonCharts","modeRank","levelPercentile","relativeGroup","relativeGroupRank"].includes(field)) need(same(s[field],first[field]), "Uncorrected first-pass estimate changed.");
      }
      if (isOfficial) {
        const e=obj(c.clearingEvidence), n=Number(c.ratedClearCount), observed=Number(c.observedClearCount);
        need(c.method==="official-d26-clearer-ability-v1" && c.skillMetric==="official-clearer-ability" && e.source==="piuscores-official" && e.mix==="Phoenix2"
          && e.asOf===official.asOf && count(n) && count(observed) && n<=observed && c.clearCount===observed && c.missingSkillCount===observed-n
          && same(c.shrinkageWeight,observed?n/(n+20)*n/observed:0) && c.defaultedAtCap===e.possiblyTruncated, "Official Clearing evidence differs.");
        if (finite(c.estimatedDifficulty)) {
          need(same(obj(c.initialFit).estimatedDifficulty,Number(c.unrecenteredDifficulty)+Number(c.recenteringShift)), "Clearing recentering differs.");
          if (c.defaultedAtCap) need(c.difficultyCi95Low===null && c.difficultyCi95High===null, "Cap floor retained a q20 interval.");
          else need(n>0 && finite(c.q20Skill) && finite(c.folderReferenceSkill)
            && same(c.unrecenteredDifficulty,midpoint+.7*Number(c.shrinkageWeight)*(c.q20Skill-c.folderReferenceSkill)*Number(obj(c.spreadCalibration).scale)), "Official Clearing q20 formula differs.");
        } else need(n===0 && c.evidenceStatus==="Unrated" && typeof c.unratedReason==="string", "Unavailable official Clearing lacks a reason.");
      }
      for (const goal of ["general","sss"]) {
        const scoring=goal==="general"?row:s, p=obj(pumbilities[goal]), initial=obj(p.initialFit), rawS=obj(scoring.initialFit), rawC=obj(c.initialFit);
        const value=scoring.estimatedDifficulty===null || c.estimatedDifficulty===null ? null : (Number(scoring.estimatedDifficulty)+Number(c.estimatedDifficulty))/2;
        const rawValue=rawS.estimatedDifficulty===null || rawC.estimatedDifficulty===null ? null : (Number(rawS.estimatedDifficulty)+Number(rawC.estimatedDifficulty))/2;
        const status=value===null?"Unrated":STATUS[Math.min(STATUS.indexOf(String(scoring.evidenceStatus)),STATUS.indexOf(String(c.evidenceStatus)))];
        need(p.aggregation==="average" && same(p.estimatedDifficulty,value) && same(initial.estimatedDifficulty,rawValue)
          && p.scoringSupportCount===scoring.nContributors && p.clearingSupportCount===c.ratedClearCount && p.evidenceStatus===status, "Refined Pumbility component mean or evidence differs.");
        if (goal==="sss") for (const side of ["Low","High"]) need(same(p[`difficultyCi95${side}`],value===null || s[`difficultyCi95${side}`]===null ? null : (Number(s[`difficultyCi95${side}`])+Number(c.estimatedDifficulty))/2), "Refined Pumbility interval differs.");
      }
    }
    validateRanks(payload[key] as unknown as Obj[]);
    for (const metric of ["clearing","pumbility"]) for (const level of new Set(payload[key].map(r=>r.level))) {
      const values=payload[key].filter(r=>r.level===level && r.tierMetrics![metric as "clearing"|"pumbility"].estimatedDifficulty!==null)
        .map(r=>r.tierMetrics![metric as "clearing"|"pumbility"]).toSorted((a,b)=>Number(a.levelRank)-Number(b.levelRank));
      values.forEach((m,i)=>need(m.levelRank===i+1 && m.levelComparisonCharts===values.length && (!i || Number(m.estimatedDifficulty)>=Number(values[i-1].estimatedDifficulty)), "Refined secondary rank differs."));
      if (key==="doubles" && level>=26 && metric==="clearing" && values.length) need(same(median(values.map(m=>Number(m.estimatedDifficulty))),level+.5), "D26+ actual folder median is not its midpoint.");
    }
  }
  return payload;
}
