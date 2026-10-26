import { compressDifficulty, compressionFactor, FIT_FIELDS } from "./tier-postprocessing.ts";
import type { AnalysisPayload } from "./types";

type ObjectValue = Record<string, unknown>;
const GENERAL_FIELDS = [...FIT_FIELDS, "initialFit", "evidenceStatus", "nContributors", "nPlayersScored",
  "scoringMethod", "scoringUnratedReason", "scoringExtrapolated", "scoringProvisional"];
const RANK_FIELDS = ["levelRank", "levelComparisonCharts", "modeRank", "levelPercentile", "relativeGroup", "relativeGroupRank"];
const STATUS = ["Unrated", "Insufficient", "Provisional", "Published"];
const INTERVAL = "player-cluster-refit-fixed-calibration-v1";
const COMPOSITE_INTERVAL = "player-bootstrap-fixed-clearing-and-calibration-v1";
const HASH = /^[a-f0-9]{64}$/;

function requireValue(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
function object(value: unknown): ObjectValue {
  requireValue(value && typeof value === "object" && !Array.isArray(value), "Expected an SSS contract object.");
  return value as ObjectValue;
}
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function count(value: unknown): value is number { return Number.isInteger(value) && (value as number) >= 0; }
function same(a: unknown, b: unknown): boolean {
  if (typeof b === "number") return finite(a) && Math.abs(a - b) < 1e-8;
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => same(v, b[i]));
  if (a && b && typeof a === "object" && typeof b === "object") {
    const aa = a as ObjectValue, bb = b as ObjectValue;
    return Object.keys(aa).length === Object.keys(bb).length && Object.keys(bb).every(k => k in aa && same(aa[k], bb[k]));
  }
  return false;
}
function exact(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => exact(v, b[i]));
  if (a && b && typeof a === "object" && typeof b === "object") {
    const aa = a as ObjectValue, bb = b as ObjectValue;
    return Object.keys(aa).length === Object.keys(bb).length && Object.keys(bb).every(k => k in aa && exact(aa[k], bb[k]));
  }
  return false;
}
function band(delta: number | null): [number | null, string | null] {
  if (delta === null) return [null, null];
  const rank = delta < -.5 ? 1 : delta < -.3 ? 2 : delta < -.1 ? 3 : delta <= .1 ? 4 : delta <= .3 ? 5 : delta <= .5 ? 6 : 7;
  return [rank, ["Overrated", "Very Easy", "Easy", "Medium", "Hard", "Very Hard", "Underrated"][rank - 1]];
}
export function checkFit(metric: ObjectValue, level: number) {
  const value = metric.estimatedDifficulty;
  requireValue(value === null || finite(value), "Nonfinite SSS difficulty.");
  const delta = value === null ? null : value - level - .5, [rank, label] = band(delta);
  requireValue(same(metric.difficultyDelta, delta) && metric.effectBandRank === rank && metric.effectBand === label, "SSS delta or effect band differs.");
  const low = metric.difficultyCi95Low, high = metric.difficultyCi95High;
  requireValue((low === null && high === null) || (finite(low) && finite(high) && low <= high && value !== null), "Invalid SSS interval.");
  for (const side of ["Low", "High"]) {
    const endpoint = metric[`difficultyCi95${side}`];
    requireValue(same(metric[`difficultyDeltaCi95${side}`], endpoint === null ? null : (endpoint as number) - level - .5), "SSS interval delta differs.");
  }
  if (value === null) requireValue(RANK_FIELDS.every(k => metric[k] === null), "Unrated SSS retains ranks.");
}
export function curve(value: unknown, minimum: number): ObjectValue | null {
  if (value === null) return null;
  const c = object(value), anchors = c.anchors;
  requireValue(c.version === 1 && c.minimumAnchorLevel === minimum && c.tailPolicy === "tangent-linear"
    && c.referenceMinimumPlayers === 20 && c.referenceMinimumPerOutcome === 5 && c.referenceMinimumCharts === 5,
  "SSS calibration identity differs.");
  requireValue(Array.isArray(anchors) && anchors.length >= 3, "SSS calibration needs three anchors.");
  const parsed = anchors.map(object);
  parsed.forEach((a, i) => {
    requireValue(Number.isInteger(a.level) && (a.level as number) >= minimum && finite(a.cost)
      && a.difficulty === (a.level as number) + .5 && count(a.referenceCharts) && a.referenceCharts >= 5
      && Array.isArray(a.chartIds) && a.chartIds.length === a.referenceCharts && a.chartIds.every(id => typeof id === "string")
      && new Set(a.chartIds).size === a.chartIds.length, "Invalid SSS reference anchor.");
    if (i) requireValue((a.level as number) > (parsed[i - 1].level as number)
      && (a.cost as number) > (parsed[i - 1].cost as number), "SSS anchors must increase without reordering costs.");
  });
  const fit = object(c.fit);
  requireValue(parsed[0].level === minimum && finite(c.offset) && c.offset < (parsed[0].cost as number)
    && same(c.supportedRange, [minimum + .5, (parsed.at(-1)!.level as number) + .5])
    && finite(fit.growth) && fit.growth > 0 && finite(fit.normalizedOffset) && fit.normalizedOffset < 0
    && finite(fit.normalizedAmplitude) && fit.normalizedAmplitude > 0
    && finite(fit.normalizedMse) && fit.normalizedMse >= 0 && typeof fit.boundHit === "boolean", "Invalid SSS calibration range or fit.");
  return c;
}
export function inverse(effect: number, c: ObjectValue): number {
  const anchors = (c.anchors as unknown[]).map(object), offset = c.offset as number;
  const first = anchors[0], last = anchors.at(-1)!;
  let index = 0;
  if (effect > (last.cost as number)) index = anchors.length - 2;
  else while (index < anchors.length - 2 && effect >= (anchors[index + 1].cost as number)) index++;
  const a = anchors[index], b = anchors[index + 1];
  const beta = Math.log1p(((b.cost as number) - (a.cost as number)) / ((a.cost as number) - offset)) / ((b.level as number) - (a.level as number));
  if (effect < (first.cost as number) || effect > (last.cost as number)) {
    const endpoint = effect < (first.cost as number) ? first : last;
    return (endpoint.difficulty as number) + (effect - (endpoint.cost as number)) / (beta * ((endpoint.cost as number) - offset));
  }
  return (a.difficulty as number) + Math.log1p((effect - (a.cost as number)) / ((a.cost as number) - offset)) / beta;
}
function textCompare(a: string, b: string): number {
  const aa = Array.from(a), bb = Array.from(b);
  for (let i = 0; i < Math.min(aa.length, bb.length); i++) {
    const difference = aa[i].codePointAt(0)! - bb[i].codePointAt(0)!;
    if (difference) return difference;
  }
  return aa.length - bb.length;
}
export function validateRanks(rows: ObjectValue[]) {
  const get = (row: ObjectValue, name: string, initial = false) => {
    const metric = object(object(row[name]).sss);
    return initial ? object(metric.initialFit) : metric;
  };
  for (const initial of [false, true]) {
    const measured = rows.filter(r => get(r, "scoringVariants", initial).estimatedDifficulty !== null).sort((a, b) => {
      const aa = get(a, "scoringVariants", initial), bb = get(b, "scoringVariants", initial);
      return (aa.difficultyDelta as number) - (bb.difficultyDelta as number)
        || (get(b, "scoringVariants").nContributors as number) - (get(a, "scoringVariants").nContributors as number)
        || textCompare(a.songName as string, b.songName as string) || textCompare(a.chartId as string, b.chartId as string);
    });
    measured.forEach((r, i) => requireValue(get(r, "scoringVariants", initial).modeRank === i + 1, "SSS mode rank differs."));
    for (const level of new Set(rows.map(r => r.level))) {
      const group = measured.filter(r => r.level === level);
      group.forEach((r, i) => {
        const metric = get(r, "scoringVariants", initial), ties = group.map((other, j) => ({metric: get(other, "scoringVariants", initial), j}))
          .filter(other => other.metric.difficultyDelta === metric.difficultyDelta).map(other => other.j + 1);
        const percentile = (ties.reduce((sum, x) => sum + x, 0) / ties.length - .5) / group.length;
        const rank = Math.min(10, Math.max(1, Math.floor(percentile * 10) + 1));
        const label = rank === 1 ? "Easiest 10%" : rank === 10 ? "Hardest 10%" : `${(rank - 1) * 10}–${rank * 10}% percentile`;
        requireValue(metric.levelRank === i + 1 && metric.levelComparisonCharts === group.length
          && same(metric.levelPercentile, percentile) && metric.relativeGroupRank === rank && metric.relativeGroup === label, "SSS folder rank differs.");
      });
    }
  }
  for (const level of new Set(rows.map(r => r.level))) {
    const group = rows.filter(r => r.level === level && get(r, "pumbilityVariants").estimatedDifficulty !== null).sort((a, b) =>
      (get(a, "pumbilityVariants").estimatedDifficulty as number) - (get(b, "pumbilityVariants").estimatedDifficulty as number)
      || (get(a, "pumbilityVariants", true).estimatedDifficulty as number) - (get(b, "pumbilityVariants", true).estimatedDifficulty as number)
      || textCompare(a.songName as string, b.songName as string) || textCompare(a.chartId as string, b.chartId as string));
    group.forEach((r, i) => {
      for (const initial of [false, true]) {
        const metric = get(r, "pumbilityVariants", initial);
        requireValue(metric.levelRank === i + 1 && metric.levelComparisonCharts === group.length
          && ["modeRank", "levelPercentile", "relativeGroup", "relativeGroupRank"].every(k => metric[k] === null), "SSS Pumbility rank differs.");
      }
    });
  }
}

export function recoverSssGeneralPayload(payload: AnalysisPayload): AnalysisPayload {
  const base = structuredClone(payload);
  base.schemaVersion = 48;
  delete base.summary.method.sssScoring;
  delete base.summary.method.sssPumbility;
  for (const key of ["singles", "doubles"] as const) for (const row of base[key]) {
    delete row.scoringVariants;
    delete row.pumbilityVariants;
  }
  return base;
}

/** Validate the complete overlay, then delegate the unchanged compatibility generation. */
export function validateSssAnalysisPayload(payload: AnalysisPayload, validateGeneral: (base: AnalysisPayload) => unknown, scoreTarget = 990000): AnalysisPayload {
  requireValue(payload.schemaVersion === 49 && Array.isArray(payload.singles) && Array.isArray(payload.doubles), "Invalid SSS generation.");
  const method = object(payload.summary?.method.sssScoring), composite = object(payload.summary?.method.sssPumbility);
  requireValue(method.version === 1 && method.method === "sss-target-rasch-v1" && method.target === scoreTarget
    && method.meaning === "conditional-target-attainment-among-observed-best-scores" && method.baseSchemaVersion === 48
    && typeof method.basePayloadSha256 === "string" && HASH.test(method.basePayloadSha256)
    && typeof method.officialManifestSha256 === "string" && HASH.test(method.officialManifestSha256)
    && method.minimumPlayerCharts === 4 && method.minimumContributors === 5 && method.minimumReferenceContributors === 20
    && method.minimumReferencePerOutcome === 5 && method.minimumReferenceCharts === 5
    && method.weighting === "one-total-weight-per-player-in-source-mode" && count(method.bootstrapSamples) && count(method.seed)
    && method.intervalMethod === INTERVAL
    && exact(method.intervalExclusions, ["anchor-uncertainty", "clearing-uncertainty", "effort", "selective-observation"])
    && exact(method.sourceBoundary, {submitted: [16, 19], officialMinimum: 20})
    && exact(method.compression, {factor: compressionFactor(payload), reference: "current-official-level-plus-0.5", applications: 1}), "SSS model or processing policy differs.");
  const hashes = object(method.sourceHashes);
  requireValue(Object.keys(hashes).length > 0 && Object.values(hashes).every(h => typeof h === "string" && HASH.test(h)), "Invalid SSS source hashes.");
  requireValue(Object.values(object(method.sourceDates)).every(d => d === null || typeof d === "string"), "Invalid SSS source dates.");
  requireValue(exact(composite, {version: 1, aggregation: "average", requiresBoth: true,
    calculation: "(SSS scoring difficulty + unchanged clearing difficulty) / 2 before rounding",
    componentValues: "after-midpoint-compression", intervalMethod: COMPOSITE_INTERVAL,
    evidence: "weaker component status; separate scoring and clearing support counts"}), "SSS Pumbility policy differs.");
  const curves: Record<string, Record<string, ObjectValue | null>> = {};
  for (const source of ["submitted", "official"]) {
    curves[source] = {};
    for (const mode of ["Single", "Double"]) {
      const c = curve(object(object(method.calibration)[source])[mode], source === "official" ? 20 : 16);
      const diagnostic = object(object(object(method.diagnostics)[source])[mode]);
      requireValue(c ? diagnostic.calibrationError === null : typeof diagnostic.calibrationError === "string", "SSS calibration availability differs.");
      requireValue(typeof object(diagnostic.model).converged === "boolean", "SSS fit convergence is missing.");
      object(diagnostic.modelPolicy);
      curves[source][mode] = c;
    }
  }
  const base = recoverSssGeneralPayload(payload);
  validateGeneral(base);
  for (const key of ["singles", "doubles"] as const) {
    const rows = payload[key] as unknown as ObjectValue[];
    for (const row of rows) {
      const variants = object(row.scoringVariants), pumbilities = object(row.pumbilityVariants);
      requireValue(exact(Object.keys(variants).sort(), ["general", "sss"]) && exact(Object.keys(pumbilities).sort(), ["general", "sss"]), "SSS variant keys differ.");
      const mirror = Object.fromEntries(GENERAL_FIELDS.filter(k => k in row).map(k => [k, row[k]]));
      requireValue(exact(variants.general, mirror) && exact(pumbilities.general, object(row.tierMetrics).pumbility), "General compatibility view changed.");
      const s = object(variants.sss), p = object(pumbilities.sss), c = object(object(row.tierMetrics).clearing), raw = object(s.initialFit), rawP = object(p.initialFit);
      const level = row.level as number, source = level >= 20 ? "official" : "submitted", calibration = curves[source][key === "singles" ? "Single" : "Double"];
      requireValue(s.scoringMethod === "sss-target-rasch-v1" && s.scoringTarget === scoreTarget && s.scoringSource === source
        && count(s.nContributors) && s.nPlayersScored === s.nContributors && count(s.scoringTargetSuccesses) && count(s.scoringBelowTarget)
        && s.nContributors === s.scoringTargetSuccesses + s.scoringBelowTarget
        && typeof s.scoringMainComponent === "boolean" && (s.scoringComponent === null || count(s.scoringComponent))
        && (s.scoringChartEffect === null || finite(s.scoringChartEffect)) && (s.scoringInformation === null || finite(s.scoringInformation) && s.scoringInformation >= 0)
        && typeof s.scoringFitConverged === "boolean" && typeof s.scoringProvisional === "boolean" && typeof s.scoringExtrapolated === "boolean"
        && typeof s.scoringPossiblyTruncated === "boolean" && typeof s.scoringComparability === "string"
        && Array.isArray(s.scoringProvisionalReasons) && s.scoringProvisionalReasons.every(r => typeof r === "string")
        && count(s.scoringBootstrapValidReplicates) && s.scoringBootstrapValidReplicates <= (method.bootstrapSamples as number)
        && s.intervalMethod === INTERVAL, "SSS target evidence or source differs.");
      const states = object(s.scoringSourceStates);
      requireValue(s.scoringPossiblyTruncated === Object.values(states).some(state => object(state).possiblyTruncated === true), "SSS truncation metadata differs.");
      for (const metric of [s, raw, p, rawP]) checkFit(metric, level);
      requireValue(Object.keys(raw).every(k => (FIT_FIELDS as readonly string[]).includes(k)), "SSS initial fit contains unknown fields.");
      for (const field of ["estimatedDifficulty", "difficultyCi95Low", "difficultyCi95High"]) {
        requireValue(same(s[field], compressDifficulty(raw[field] as number | null, level, compressionFactor(payload))), "SSS compression differs.");
      }
      const rated = s.estimatedDifficulty !== null;
      requireValue(STATUS.includes(s.evidenceStatus as string) && (rated
        ? s.evidenceStatus !== "Unrated" && s.scoringUnratedReason === null && (s.nContributors as number) >= 5
          && (s.scoringTargetSuccesses as number) > 0 && (s.scoringBelowTarget as number) > 0 && s.scoringMainComponent
          && s.scoringComponent === 0 && s.scoringFitConverged && finite(s.scoringChartEffect) && calibration
        : s.evidenceStatus === "Unrated" && typeof s.scoringUnratedReason === "string" && s.scoringUnratedReason.length > 0), "SSS point lacks two-sided calibrated evidence.");
      requireValue(s.scoringProvisional === ((s.scoringProvisionalReasons as string[]).length > 0 || !rated), "SSS provisional marker differs.");
      if (rated) {
        const diagnostic = object(object(object(method.diagnostics)[source])[key === "singles" ? "Single" : "Double"]);
        requireValue(object(diagnostic.model).converged === true, "SSS source fit did not converge.");
        requireValue(same(raw.estimatedDifficulty, inverse(s.scoringChartEffect as number, calibration!)), "SSS chart effect differs from its own calibration.");
        const anchors = (calibration!.anchors as unknown[]).map(object), effect = s.scoringChartEffect as number;
        requireValue(s.scoringExtrapolated === (effect < (anchors[0].cost as number) || effect > (anchors.at(-1)!.cost as number)), "SSS extrapolation marker differs.");
        for (const side of ["Low", "High"]) {
          const endpoint = s[`scoringChartEffectCi95${side}`];
          requireValue(endpoint === null ? raw[`difficultyCi95${side}`] === null
            : finite(endpoint) && same(raw[`difficultyCi95${side}`], inverse(endpoint, calibration!)), "SSS latent interval calibration differs.");
        }
        requireValue(s.evidenceStatus === (s.scoringProvisional ? "Provisional" : "Published"), "SSS evidence status differs.");
        const knownComparable = source === "official" ? ["same", "single-source-known"].includes(s.scoringComparability as string)
          : s.scoringComparability === "comparable";
        if (s.evidenceStatus === "Published") requireValue(Math.min(s.scoringTargetSuccesses as number, s.scoringBelowTarget as number) >= 5
          && finite(s.scoringInformation) && s.scoringInformation >= .25 && !s.scoringPossiblyTruncated
          && !s.scoringExtrapolated && knownComparable, "Published SSS retains weak evidence.");
      }
      else requireValue(s.scoringChartEffectCi95Low === null && s.scoringChartEffectCi95High === null, "Unrated SSS retains latent intervals.");
      if (s.difficultyCi95Low !== null) requireValue((s.scoringBootstrapValidReplicates as number) >= Math.max(10, Math.floor(.8 * (method.bootstrapSamples as number))), "SSS interval lacks refit support.");
      const value = !rated || c.estimatedDifficulty === null ? null : ((s.estimatedDifficulty as number) + (c.estimatedDifficulty as number)) / 2;
      const rawC = object(c.initialFit);
      const rawValue = raw.estimatedDifficulty === null || rawC.estimatedDifficulty === null ? null : ((raw.estimatedDifficulty as number) + (rawC.estimatedDifficulty as number)) / 2;
      const status = value === null ? "Unrated" : STATUS[Math.min(STATUS.indexOf(s.evidenceStatus as string), STATUS.indexOf(c.evidenceStatus as string))];
      requireValue(p.aggregation === "average" && p.intervalMethod === COMPOSITE_INTERVAL && p.scoringSupportCount === s.nContributors
        && p.clearingSupportCount === c.ratedClearCount && STATUS.includes(p.evidenceStatus as string) && p.evidenceStatus === status
        && same(p.estimatedDifficulty, value) && same(rawP.estimatedDifficulty, rawValue)
        && p.unratedReason === (!rated ? s.scoringUnratedReason : c.estimatedDifficulty === null ? "Clearing difficulty is unavailable." : null), "SSS Pumbility mean or evidence differs.");
      for (const side of ["Low", "High"]) {
        const field = `difficultyCi95${side}`;
        requireValue(same(p[field], value === null || s[field] === null ? null : ((s[field] as number) + (c.estimatedDifficulty as number)) / 2)
          && same(rawP[field], value === null || raw[field] === null ? null : ((raw[field] as number) + (rawC.estimatedDifficulty as number)) / 2), "SSS Pumbility interval differs.");
      }
    }
    validateRanks(rows);
  }
  return payload;
}
