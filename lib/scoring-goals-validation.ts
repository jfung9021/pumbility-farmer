import { SCORING_GOALS, SCORING_TARGET_POLICIES, scoringTargetForGoal } from "./scoring-variants.ts";
import { validateRanks } from "./sss-analysis-validation.ts";
import type { AnalysisPayload, ChartResult, ScoringGoal, ScoringVariant } from "./types";

type Obj = Record<string, any>;
const NEW_GOALS = ["ss", "ssplus"] as const;
const HASH = /^[a-f0-9]{64}$/;
function need(value: unknown, message: string): asserts value {
  if (!value) throw new Error(message);
}
function obj(value: unknown): Obj {
  need(value !== null && typeof value === "object" && !Array.isArray(value), "Missing scoring-goal object.");
  return value as Obj;
}
function exact(a: unknown, b: unknown): boolean {
  if (Array.isArray(b)) return Array.isArray(a) && a.length === b.length && b.every((v, i) => exact(a[i], v));
  if (b !== null && typeof b === "object") {
    if (a === null || typeof a !== "object" || Array.isArray(a)) return false;
    const left = a as Obj, right = b as Obj;
    return Object.keys(left).length === Object.keys(right).length && Object.keys(right).every(k => exact(left[k], right[k]));
  }
  return a === b;
}
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function count(value: unknown): value is number { return finite(value) && Number.isSafeInteger(value) && value >= 0; }
function same(a: unknown, b: unknown) { return b === null ? a === null : finite(a) && finite(b) && Math.abs(a - b) < 1e-8; }

/** Recover only the additive goal layer; legacy numerical validators stay strict. */
export function recoverScoringGoalsBaseline(payload: AnalysisPayload): AnalysisPayload {
  need(payload.schemaVersion === 58, "Scoring-goal recovery requires schema 58.");
  const overlay = obj(payload.summary.method.scoringGoalBackfills);
  const base = structuredClone(payload);
  base.schemaVersion = 57;
  if (overlay.baseScriptVersion === null) delete (base.summary as unknown as Obj).scriptVersion;
  else base.summary.scriptVersion = overlay.baseScriptVersion;
  for (const field of ["scoringGoalPolicies", "availableScoringGoals", "scoringGoalBackfills"]) delete base.summary.method[field];
  for (const row of [...base.singles, ...base.doubles]) for (const goal of NEW_GOALS) {
    if (row.scoringVariants) delete row.scoringVariants[goal];
    if (row.pumbilityVariants) delete row.pumbilityVariants[goal];
  }
  return base;
}

function validateMetric(row: ChartResult, goal: "ss" | "ssplus") {
  const scoring = obj(row.scoringVariants?.[goal]), composite = obj(row.pumbilityVariants?.[goal]);
  const clearing = obj(row.tierMetrics?.clearing), target = scoringTargetForGoal(goal);
  need(scoring.scoringTarget === target.score && scoring.scoringSource === (row.level >= 20 ? "official" : "submitted"), "Scoring-goal target or source differs.");
  need(["target-attainment-rasch-v1", "target-boundary-score-gap-v1", "target-rare-success-upper-band-v1", "target-conditional-pooled-prior-v1"].includes(scoring.scoringMethod), "Scoring-goal model method differs.");
  need(count(scoring.nContributors) && count(scoring.scoringTargetSuccesses) && count(scoring.scoringBelowTarget), "Scoring-goal evidence counts differ.");
  need(scoring.nContributors === scoring.scoringTargetSuccesses + scoring.scoringBelowTarget, "Scoring-goal support totals differ.");
  const statuses = ["Unrated", "Insufficient", "Provisional", "Published"];
  need(statuses.includes(scoring.evidenceStatus) && statuses.includes(clearing.evidenceStatus), "Scoring-goal evidence status differs.");
  const value = scoring.estimatedDifficulty, clearingValue = clearing.estimatedDifficulty;
  need(value === null || finite(value), "Scoring-goal estimate is invalid.");
  need(same(scoring.difficultyDelta, value === null ? null : value - row.level - .5), "Scoring-goal delta differs.");
  need(value !== null || scoring.evidenceStatus === "Unrated", "An unavailable scoring-goal estimate must be Unrated.");
  for (const side of ["Low", "High"]) {
    const endpoint = scoring[`difficultyCi95${side}`];
    need(endpoint === null || finite(endpoint), "Scoring-goal interval is invalid.");
    need(same(scoring[`difficultyDeltaCi95${side}`], endpoint === null ? null : endpoint - row.level - .5), "Scoring-goal interval delta differs.");
  }
  need(scoring.difficultyCi95Low === null && scoring.difficultyCi95High === null
    || finite(scoring.difficultyCi95Low) && finite(scoring.difficultyCi95High) && scoring.difficultyCi95Low <= scoring.difficultyCi95High,
  "Scoring-goal interval order differs.");
  const expected = value === null || clearingValue === null ? null : (value + clearingValue) / 2;
  need(composite.aggregation === "average" && same(composite.estimatedDifficulty, expected)
    && same(composite.difficultyDelta, expected === null ? null : expected - row.level - .5)
    && composite.scoringSupportCount === scoring.nContributors && composite.clearingSupportCount === clearing.ratedClearCount,
  "Pumbility differs from the selected scoring goal and Clearing.");
  const status = expected === null ? "Unrated" : statuses[Math.min(statuses.indexOf(scoring.evidenceStatus), statuses.indexOf(clearing.evidenceStatus))];
  need(composite.evidenceStatus === status, "Scoring-goal composite evidence differs.");
  for (const side of ["Low", "High"]) {
    const endpoint = scoring[`difficultyCi95${side}`];
    const expectedEndpoint = endpoint === null || clearingValue === null ? null : (endpoint + clearingValue) / 2;
    need(same(composite[`difficultyCi95${side}`], expectedEndpoint), "Scoring-goal composite interval differs.");
  }
}

export function validateScoringGoalsPayload(payload: AnalysisPayload, validateBase: (base: AnalysisPayload) => unknown): AnalysisPayload {
  need(payload.schemaVersion === 58 && Array.isArray(payload.singles) && Array.isArray(payload.doubles), "Invalid expanded scoring-goal generation.");
  const method = payload.summary.method, overlay = obj(method.scoringGoalBackfills), goals = obj(overlay.goals);
  need(exact(method.scoringGoalPolicies, { version: 1, goals: SCORING_TARGET_POLICIES }), "Scoring-goal policies differ.");
  need(overlay.version === 1 && overlay.method === "independent-target-goal-overlays-v1" && overlay.baseSchemaVersion === 57
    && HASH.test(overlay.basePayloadSha256) && (overlay.baseScriptVersion === null || typeof overlay.baseScriptVersion === "string"), "Scoring-goal provenance differs.");
  obj(overlay.inputIdentity);
  const added = NEW_GOALS.filter(goal => Object.hasOwn(goals, goal));
  need(added.length > 0 && Object.keys(goals).every(goal => (NEW_GOALS as readonly string[]).includes(goal)), "Unexpected scoring-goal overlay.");
  const available: ScoringGoal[] = SCORING_GOALS.filter(goal => goal !== "ss" && goal !== "ssplus" || added.includes(goal));
  need(exact(method.availableScoringGoals, available), "Advertised scoring goals differ.");
  const expectedKeys = [...available].sort();
  for (const row of [...payload.singles, ...payload.doubles]) {
    need(exact(Object.keys(obj(row.scoringVariants)).sort(), expectedKeys)
      && exact(Object.keys(obj(row.pumbilityVariants)).sort(), expectedKeys), "Scoring-goal chart variants differ from availability.");
    for (const goal of added) validateMetric(row, goal);
  }
  for (const goal of added) {
    const evidence = obj(goals[goal]), policy = SCORING_TARGET_POLICIES.find(p => p.key === goal);
    need(exact(evidence.targetConfig, policy) && HASH.test(evidence.artifactSha256)
      && exact(evidence.inputIdentity, overlay.inputIdentity), "Scoring-goal fit policy or input identity differs.");
    const artifact = obj(evidence.artifact), fitMethod = obj(evidence.method);
    need(artifact.schemaVersion === 1 && artifact.goal === goal && exact(artifact.targetConfig, policy)
      && exact(artifact.inputIdentity, overlay.inputIdentity) && exact(artifact.method, fitMethod)
      && typeof artifact.generatedAtUtc === "string" && Number.isFinite(Date.parse(artifact.generatedAtUtc)),
    "Scoring-goal artifact metadata differs.");
    const sourceHashes = obj(artifact.sourceHashes);
    need(exact(Object.keys(sourceHashes).sort(), ["manifest", "phoenix1", "phoenix2", "sourcePolicy"])
      && Object.values(sourceHashes).every(hash => typeof hash === "string" && HASH.test(hash)), "Scoring-goal source hashes differ.");
    need(fitMethod.version === 1 && fitMethod.method === "target-median-anchor-refinement-centering-tail-v1"
      && fitMethod.target === policy!.score && exact(fitMethod.targetConfig, policy)
      && fitMethod.bootstrapSamples === 200 && fitMethod.minimumPlayerCharts === 4
      && fitMethod.minimumContributors === 5 && fitMethod.minimumReferenceContributors === 20
      && fitMethod.minimumReferencePerOutcome === 5 && fitMethod.minimumReferenceCharts === 5
      && fitMethod.compressionApplications === 1
      && fitMethod.scoreFetches === 0 && fitMethod.recommendationFits === 0
      && exact(fitMethod.sourceBoundary, { submitted: [16, 19], officialMinimum: 20 }),
    "Scoring-goal fit methodology differs.");
    need(Array.isArray(fitMethod.fitReferences) && fitMethod.fitReferences.length === 4, "Scoring-goal fit references differ.");
    const cohorts = fitMethod.fitReferences.map((reference: Obj) => {
      const identity = obj(reference.identity);
      need(reference.goal === goal && identity.target === policy!.score && identity.samples === 200
        && HASH.test(identity.boards) && HASH.test(identity.sourceBoards), "Scoring-goal fit reference identity differs.");
      return `${reference.source}:${reference.mode}`;
    }).sort();
    need(exact(cohorts, ["official:Double", "official:Single", "submitted:Double", "submitted:Single"]), "Scoring-goal cohort population differs.");
    for (const rows of [payload.singles, payload.doubles]) validateRanks(rows.map(row => ({
      ...row, scoringVariants: { sss: row.scoringVariants![goal] as ScoringVariant },
      pumbilityVariants: { sss: row.pumbilityVariants![goal] },
    })));
  }
  validateBase(recoverScoringGoalsBaseline(payload));
  return payload;
}
