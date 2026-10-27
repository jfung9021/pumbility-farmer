import type { CombinedMixInfo, MixInfo, MixKey } from "./mixes";

export type ModeKey = "singles" | "doubles" | "coop";
export type RecommendationModeKey = "overall" | ModeKey;
export type EvidenceStatus = "Published" | "Provisional" | "Insufficient" | "Unrated";
export type TierMetricKey = "scoring" | "clearing" | "pumbility";
export type ScoringGoal = "general" | "ss" | "ssplus" | "sss" | "sssplus";
export type TargetScore = 980000 | 985000 | 990000 | 995000;
export interface ScoringGoalPolicy {
  key: Exclude<ScoringGoal, "general">;
  grade: "SS" | "SS+" | "SSS" | "SSS+";
  score: TargetScore;
  inclusive: true;
  policyVersion: string;
  modelMethod: string;
  boundaryMethod: string;
  rareMethod: string;
  nearTargetScoreRange: [number, number];
}
/** Targets are optional so a historical generation cannot imply a new goal exists. */
export type ScoringVariantMap<T> = { general: T } & Partial<Record<Exclude<ScoringGoal, "general">, T>>;
export type ScoringMethod = "official-cross-level-v1" | "submitted-score-profile-v2" | "sss-target-rasch-v1" | "sss-boundary-score-gap-v1" | "sss-rare-success-upper-band-v1" | "target-attainment-rasch-v1" | "target-boundary-score-gap-v1" | "target-rare-success-upper-band-v1" | "target-conditional-pooled-prior-v1";
export type SssEstimateBranch = "first-pass" | "mixed" | "no-sss" | "all-sss" | "rare-sss" | "insufficient-history" | "no-data" | "unsupported-calibration" | "no-target" | "all-target" | "rare-target";

export interface SssFrozenRange {
  source: "submitted" | "official";
  mode: "Single" | "Double";
  level: number;
  lower: number;
  upper: number;
  kind: "observed" | "interpolated" | "nearest";
  supported: boolean;
  provisional: boolean;
  firstPassRatedCharts: number;
  referenceChartIds: string[];
  donorLevels: number[];
  lowerOffset: number;
  upperOffset: number;
}

export interface SssRefinementEvidence {
  version: 1 | 2;
  branch: SssEstimateBranch;
  fullBoard: { records: number; successes: number; belowTarget: number; highestScore: number | null; lowestScore: number | null; possiblyTruncated: boolean; topScores?: number[] };
  modelEligible: { records: number; successes: number; belowTarget: number };
  range: SssFrozenRange | null;
  boundary?: { endpointScore: number; endpointPolicy?: "mean-top-3-available-v1"; endpointScoreCount?: number; gapPoints: number; gapScalePointsPerLatentUnit: number; gapDirection: "shortfall" | "surplus";
    edgeFinal: number; edgeInitial: number; edgeLatent: number; targetLatent: number; initialDifficulty: number; finalDifficulty: number; extrapolated: boolean;
    calibrationPolicy: string; calibrationScoreRange: [number, number]; calibrationCharts: number; calibrationRecords: number;
    calibrationValidationCharts: number; calibrationValidationMeanAbsoluteError: number };
  rare?: { conditionalEffect: number; conditionalInitialDifficulty: number; conditionalDifficulty: number; weightedSuccesses: number;
    conditionalExpectedSuccesses: number; likelihoodResidual: number; upperBandLow: number; upperBandHigh: number; blendWeight: number;
    countWeight: number; supportWeight: number; rateWeight: number; candidateCount: number; conditionalRank: number; upperTarget: number;
    originalDifficulty: number; initialDifficulty: number; finalDifficulty: number };
}

export interface SssCenteringFolder {
  source: "official";
  mode: "Single" | "Double";
  level: number;
  basis: "folder" | "interpolated" | "nearest" | "unavailable";
  donorLevels: number[];
  mixedFirstPassChartIds: string[];
  populationChartIds: string[];
  donorPopulationChartIds: string[];
  donorMixedChartIds: string[];
  mixedFirstPassCount: number;
  ratedChartCount: number;
  medianBefore: number | null;
  medianAfter: number | null;
  finalShift: number;
  initialShift: number;
}

export interface SssChartCentering extends Omit<SssCenteringFolder, "mixedFirstPassChartIds" | "populationChartIds" | "donorPopulationChartIds" | "donorMixedChartIds"> {
  version: 1;
  uncenteredDifficulty: number | null;
  uncenteredInitialDifficulty: number | null;
  effectiveRange: { lower: number; upper: number } | null;
  effectiveRareBand: { lower: number; upper: number } | null;
}

export interface TargetTailEvidence {
  version: 1;
  method: "target-midpoint-soft-tail-global-exceptions-v1";
  midpoint: number;
  preTailDifficulty: number | null;
  preTailDifficultyCi95Low: number | null;
  preTailDifficultyCi95High: number | null;
  ordinaryDifficulty: number | null;
  exceptionEligible: boolean;
  rejectionReasons: string[];
  exceptionSelected: boolean;
  finalDifficulty: number | null;
  finalDifficultyCi95Low: number | null;
  finalDifficultyCi95High: number | null;
  conservativeDistance: number | null;
  protectedMedian: boolean;
  finalFolderRange?: { lower: number; upper: number } | null;
}

export interface Level20WidthValues {
  estimatedDifficulty: number | null;
  difficultyCi95Low: number | null;
  difficultyCi95High: number | null;
}

/** The final presentation scale is separate from the stored target fit. */
export interface Level20ScoringWidthEvidence {
  version: 1;
  method: "level20-target-quantile-width-v1";
  group: string;
  source: "stored-pre-tail" | "stored-final-width-pre-tail" | "stored-pre-tail-published-tie-block" | "stored-final-width-pre-tail-published-tie-block";
  original: Record<string, unknown> & Level20WidthValues;
  input: Level20WidthValues;
  final: Level20WidthValues;
}

export interface SssPlusMedianCalibration {
  version: 1 | 2 | 3;
  method: "sssplus-refined-median-calibration-v1" | "sssplus-refined-median-calibration-v2" | "sssplus-refined-median-calibration-v3";
  finalCurveScale?: { version: 1; method: "bounded-offset-local-lower-scale-v1"; minimumOffsetMarginAnchorGaps: 1;
    offsetLimit: number; lowerSlope: number; lowerAnchorLevels: [number, number] } | null;
  source: "official";
  mode: "Single" | "Double";
  refinedLatent: number | null;
  anchorKind: "derived-provisional-full-folder-median";
  anchor: {
    level: number; supported: boolean; populationChartIds: string[]; mixedFirstPassChartIds: string[];
    branches: Record<string, number>; possiblyTruncatedCharts: number; fewerThanFiveScores: number; medianLatent: number | null;
  };
  calibrationError: string | null;
  preliminaryExtrapolated: boolean;
  finalExtrapolated: boolean | null;
  intervals: string;
}

/** Local spread overlay; the preliminary historical fit remains recoverable. */
export interface SssPlusSpreadEvidence {
  version: 1;
  method: "midpoint-asinh-pre-tail-v1";
  scale: number;
  midpoint: number;
  preSpreadDifficulty: number | null;
  preSpreadDifficultyCi95Low: number | null;
  preSpreadDifficultyCi95High: number | null;
  spreadDifficulty: number | null;
  spreadDifficultyCi95Low: number | null;
  spreadDifficultyCi95High: number | null;
  rare: Record<string, unknown> | null;
  boundaryEvidence: Record<string, unknown> | null;
}

/** Public chart aggregates only; the target fit never publishes player identities. */
export interface FinalFolderWidthEvidence {
  group: string;
  input: Record<string, number | null>;
  parameters: {
    midpoint: number; basis: string; locationShift: number; medianHalfGap: number;
    lowerReference: number | null; upperReference: number | null;
    provisional: boolean; ratedCharts: number;
  };
  exceptionPreserved: boolean;
  initialCompressionApplications: number;
}

export interface ChartSourceExclusionEvidence {
  rules: Array<{ source: string; chartId: string; mode: string; label: string; reason: string; enabled: boolean }>;
  capturedSourceStates: Record<string, OfficialScoringSourceState>;
  effectiveSourceStates: Record<string, OfficialScoringSourceState>;
}

export interface SubmittedSssplusChartCalibration {
  version: 1;
  method: "submitted-folder-centered-shared-q90-v1";
  folder: string;
  effect: number;
  effectCi95Low: number | null;
  effectCi95High: number | null;
  initialCompressionApplications: 1;
}

export interface ScoringVariant extends TierMetricResult {
  scoringLevel20Width?: Level20ScoringWidthEvidence;
  scoringSubmittedCalibration?: SubmittedSssplusChartCalibration;
  finalFolderWidth?: FinalFolderWidthEvidence;
  modeRank?: number | null;
  levelPercentile?: number | null;
  relativeGroup?: string | null;
  relativeGroupRank?: number | null;
  difficultyCi95Low?: number | null;
  difficultyCi95High?: number | null;
  difficultyDeltaCi95Low?: number | null;
  difficultyDeltaCi95High?: number | null;
  nContributors: number;
  nPlayersScored?: number;
  scoringMethod?: ScoringMethod;
  scoringEstimateBranch?: SssEstimateBranch;
  scoringRefinement?: SssRefinementEvidence;
  /** Complete-board records used by a boundary estimate in compact list rows. */
  scoringBoundaryRecords?: number;
  scoringFirstPass?: ScoringVariant;
  scoringFolderCentering?: SssChartCentering;
  scoringMedianCalibration?: SssPlusMedianCalibration;
  scoringSpreadPolicy?: SssPlusSpreadEvidence;
  scoringTailPolicy?: TargetTailEvidence;
  intervalUnavailableReason?: string;
  scoringUnratedReason?: string | null;
  scoringExtrapolated?: boolean;
  scoringProvisional?: boolean;
  scoringTarget?: TargetScore;
  scoringTargetSuccesses?: number;
  scoringBelowTarget?: number;
  scoringSource?: "submitted" | "official";
  scoringComponent?: number | null;
  scoringMainComponent?: boolean;
  scoringChartEffect?: number | null;
  scoringChartEffectCi95Low?: number | null;
  scoringChartEffectCi95High?: number | null;
  scoringInformation?: number | null;
  scoringProvisionalReasons?: string[];
  scoringComparability?: string;
  scoringPossiblyTruncated?: boolean;
  scoringSourceStates?: Record<string, OfficialScoringSourceState>;
  scoringFitConverged?: boolean;
  scoringBootstrapValidReplicates?: number;
  intervalMethod?: string;
}

export interface TierPlayerScore {
  chartId: string;
  score: number;
  plateCode: string | null;
}

export interface TierPlayerScoresResponse {
  playerKey: string;
  mode: ModeKey;
  syncedAtUtc: string | null;
  scores: TierPlayerScore[];
}

export interface TierMetricResult {
  initialFit?: Record<string, number | string | null>;
  estimatedDifficulty: number | null;
  difficultyDelta: number | null;
  levelRank: number | null;
  levelComparisonCharts: number | null;
  effectBandRank: number | null;
  effectBand: string | null;
  evidenceStatus: EvidenceStatus;
}

export interface ClearingTierMetric extends TierMetricResult {
  finalFolderWidth?: FinalFolderWidthEvidence;
  method?: "submitted-cross-level-clearing-v1" | "submitted-cross-level-clearing-v2" | "submitted-folder-centered-clearing-v1" | "official-d26-clearer-ability-v1" | "official-d26-clearer-ability-combined-v2" | "official-clearer-ability-combined-v3" | "official-clearer-ability-observed-capped-v1";
  clearingEvidence?: OfficialClearingEvidence;
  observedClearCount?: number;
  recenteringShift?: number;
  unrecenteredDifficulty?: number | null;
  preRecenterCapDefaultDifficulty?: number | null;
  uncertaintyReason?: string | null;
  unratedReason?: string | null;
  sourceCounts?: Record<string, number>;
  retainedSources?: string[];
  comparability?: "comparable" | "changed" | "unknown" | "no-evidence";
  anchorEligible?: boolean;
  qualificationExclusions?: Record<string, number>;
  extrapolated?: boolean;
  provisionalReasons?: string[];
  supportedRange?: [number, number] | null;
  clearCount: number;
  ratedClearCount: number;
  missingSkillCount: number;
  selectedCount?: number;
  skillMetric?: "official-clearer-ability";
  historyMinimumLevel?: number;
  observedAtCap?: boolean;
  abilityCoverage?: number;
  shrinkageWeight?: number;
  spreadCalibration?: OfficialClearingSpread | null;
  extremeOutlier?: boolean;
  difficultyCi95Low?: number | null;
  difficultyCi95High?: number | null;
  q20SkillCi95Low?: number | null;
  q20SkillCi95High?: number | null;
  defaultedAtCap?: boolean;
  capDefaultDifficulty?: number | null;
  capDefaultBasis?: "uncapped-folder-minimum" | "folder-midpoint-no-uncapped-charts" | null;
  // q10Skill is a point percentile when no legacy range endpoint is present.
  q10Skill?: number | null;
  // Earlier point percentiles and ranges remain readable with their original labels.
  q20Skill?: number | null;
  q0Skill?: number | null;
  q25Skill?: number | null;
  q50Skill?: number | null;
  q30Skill?: number | null;
  meanSkill?: number | null;
  folderReferenceSkill?: number | null;
}

export interface OfficialClearingSpread {
  scale: number;
  referenceSpread: number | null;
  supportedCharts: number;
  lowerFence: number | null;
  upperFence: number | null;
  referenceLevel: number | null;
  basis: "folder" | "neighbor-folder" | "fallback";
}

export interface OfficialTierEvidence {
  source: "piuscores-official";
  asOf: string | null;
  rawRowCount: number;
  maxRank: number | null;
  possiblyTruncated: boolean;
  cutoffScore: number | null;
  status: "available" | "possibly-truncated" | "missing";
  unavailableReason: string | null;
}

export interface OfficialClearingEvidence extends Omit<OfficialTierEvidence, "status"> {
  capPolicy?: "phoenix1-diagnostic-phoenix2-default-v1" | "observed-capped-s24-d25-fixed-reference-v1";
  mix: "Phoenix2" | "Phoenix+Phoenix2";
  status: "available" | "empty" | "possibly-truncated" | "missing";
  sourceDates?: Record<string, string | null>;
  sourceStates?: Record<string, OfficialScoringSourceState>;
  manifestSha256?: string;
  comparability?: string;
  mergedPlayers?: number;
}

export interface OfficialScoringSourceState {
  status: "available" | "empty" | "absent-from-catalog" | "not-mirrored" | "not-captured";
  asOf: string | null;
  rawRowCount: number;
  maxRank: number;
  cutoffScore: number | null;
  possiblyTruncated: boolean;
}

export interface OfficialScoringEvidence {
  sourceDates: Record<string, string | null>;
  sourceCounts: Record<string, number>;
  sourceStates: Record<string, OfficialScoringSourceState>;
  mergedPlayerCount: number;
  comparability: string;
  component: number | null;
  anchorEligible: boolean;
}

export interface OfficialCrossLevelScoringMethod {
  version: 1;
  minimumOfficialLevel: 20;
  sourceDates: Record<string, string | null>;
  manifestSha256: string;
  calibration: Record<"Single" | "Double", OfficialCrossLevelCurve | null>;
  intervalMethod: "conditional-player-bootstrap-fixed-fit-v1";
}

export interface CrossLevelClearingCurve {
  version: 1;
  anchors: Array<{ level: number; threshold: number; difficulty: number; referenceCharts: number; chartIds: string[] }>;
  tailPolicy: "boundary-linear";
  supportedRange: [number, number];
}

export interface CrossLevelClearingMethod {
  version: 1 | 2;
  source: "piuscores-submitted";
  baseScoringSchemaVersion: 26 | 41 | 42;
  minimumCalibrationLevel?: 20;
  lowerLevelMethod?: { method: "folder-centered-q10"; difficultyDeltaScale: 0.70; referenceMinimumClearers: 20; referenceMinimumCharts: 5 };
  lowerReferenceSkills?: Record<"Single" | "Double", Record<string, { level: number; threshold: number; referenceCharts: number; chartIds: string[] }>>;
  skillMethod: { version: 1 | 2; topClearCount: 30; minimumScoreRecords: 100 | 200; minimumUniqueClears: 30;
    difficultyBasis: "current-official-level"; scope: "same-mode-current-catalog"; recordDeduplication: "account-chart-across-mixes" };
  sourceDates: Record<string, string | null>;
  inputHashes: Record<string, string>;
  calibration: Record<"Single" | "Double", CrossLevelClearingCurve | null>;
  intervalMethod: "none";
}

export interface HybridTierMethod {
  version: 1;
  minimumDisplayLevel: 16;
  scoring: { below20: "piuscores-submitted-score-profile-v2"; atLeast20: "official-cross-level-v1"; officialMinimumLevel: 20; missingOfficialPolicy: "unrated" };
  clearing: { below20: "submitted-folder-centered-clearing-v1"; atLeast20: "submitted-cross-level-clearing-v2"; minimumCalibrationLevel: 20 };
  submittedScoring: { method: Record<string, unknown>; scoreProfileCalibration: Record<string, unknown>; confidenceIntervals: Record<string, unknown> };
}

export interface OfficialCrossLevelCurve {
  version: 1;
  anchors: Array<{ level: number; difficulty: number; cost: number; referenceCharts: number; chartIds: string[] }>;
  offset: number;
  fit: { growth: number; normalizedOffset: number; normalizedAmplitude: number; normalizedMse: number; boundHit: boolean };
  tailPolicy: "tangent-linear";
  supportedRange: [number, number];
  referenceMinimumPlayers: 10;
  referenceMinimumCharts: 5;
}

export interface OfficialTierMethod {
  version: 15;
  enabled: true;
  source: "piuscores-official";
  mix: "Phoenix2";
  asOf: string | null;
  minimumLevels: { Single: 23; Double: 25 };
  capPolicy: "clearing-folder-minimum";
  scoring: {
    metric: "equal-player-score-gaps";
    minimumOtherCharts: 3;
    sparsePolicy: "provisional-same-level";
    aggregation: "equal-player-mean";
    calibration: {
      method: "shared-linear-player-gaps"; version: 6;
      minimumLevels: { Single: 23; Double: 25 };
      referenceQuantile: 0.9; targetHalfWidth: 0.45; maximumScale: 1; rangePolicy: "unbounded";
      minimumSupportedCharts: 8; minimumReferencePlayers: 10; outlierIqrMultiplier: 2;
      outlierUse: "diagnostic-only";
      folderScales: Record<"Single" | "Double", Record<string, number>>;
    };
  };
  clearing: {
    minimumLevels: { Single: 25; Double: 26 };
    skillMetric: "official-clearer-ability";
    difficultyDeltaScale: 0.70;
    normalization: "folder-median-player-ability";
    percentile: 0.20;
    playerSkill: { method: "leave-one-chart-out-top-official-levels"; topCharts: 25; minimumOtherCharts: 25; historyMinimumLevels: { Single: 22; Double: 23 } };
    shrinkage: { priorPlayers: 20 };
    calibration: { method: "robust-folder-spread"; version: 4;
      referenceQuantile: 0.9; targetHalfWidth: 0.45; maximumScale: null; rangePolicy: "unbounded";
      minimumSupportedCharts: 5; minimumReferencePlayers: 10; outlierUse: "diagnostic-only" };
  };
}

export interface OfficialScoringSpread {
  scale: number;
  referenceSpread: number | null;
  supportedCharts: number;
  lowerFence: number | null;
  upperFence: number | null;
  basis: "folder" | "neighbor-folder" | "fallback";
  referenceLevel: number | null;
}

export interface PumbilityTierMetric extends TierMetricResult {
  difficultyCi95Low?: number | null;
  difficultyCi95High?: number | null;
  intervalMethod?: string | null;
  aggregation?: "maximum" | "average" | "centered-maximum";
  maximumBaseline?: PumbilityTierMetric;
  scoringSupportCount: number;
  clearingSupportCount: number;
}

export interface ChartRerate {
  from: string;
  to: string;
  delta: number;
  direction: "uprated" | "downrated";
  sourceRow: number;
}

export interface ChartResult {
  submittedSssplusCalibrationBaseline?: {
    scoring: { changed: Record<string, unknown>; added: string[] };
    pumbility: { changed: Record<string, unknown>; added: string[] };
  };
  combinedOfficialClearingBaseline?: { clearing: ClearingTierMetric; pumbility: PumbilityTierMetric; pumbilityVariants: ScoringVariantMap<PumbilityTierMetric> };
  sssCenteringBaseline?: { scoring: ScoringVariant; pumbility: PumbilityTierMetric };
  sssTailBaseline?: { scoring: ScoringVariant; pumbility: PumbilityTierMetric };
  sssplusCenteringBaseline?: { scoring: ScoringVariant; pumbility: PumbilityTierMetric };
  sssplusTailBaseline?: { scoring: ScoringVariant; pumbility: PumbilityTierMetric };
  sssplusMedianBaseline?: { scoring: ScoringVariant; pumbility: PumbilityTierMetric; tailBaseline: { scoring: ScoringVariant; pumbility: PumbilityTierMetric } };
  sssplusSpreadBaseline?: { scoring: ScoringVariant; pumbility: PumbilityTierMetric; tailBaseline: { scoring: ScoringVariant; pumbility: PumbilityTierMetric } };
  sssplusRefinementBaseline?: { sssPumbility: PumbilityTierMetric };
  scoringVariants?: ScoringVariantMap<ScoringVariant>;
  pumbilityVariants?: ScoringVariantMap<PumbilityTierMetric>;
  /** Client projection marker; absent on the immutable General compatibility rows. */
  scoringGoal?: ScoringGoal;
  scoringLevel20Width?: Level20ScoringWidthEvidence;
  level20WidthBaseline?: Record<string, Record<string, { changed: Record<string, unknown>; added: string[] }>>;
  scoringSubmittedCalibration?: SubmittedSssplusChartCalibration;
  finalFolderWidth?: FinalFolderWidthEvidence;
  chartSourceExclusions?: ChartSourceExclusionEvidence;
  scoringEstimateBranch?: SssEstimateBranch;
  scoringRefinement?: SssRefinementEvidence;
  /** Complete-board records used by a boundary estimate in compact list rows. */
  scoringBoundaryRecords?: number;
  scoringFirstPass?: ScoringVariant;
  scoringFolderCentering?: SssChartCentering;
  scoringMedianCalibration?: SssPlusMedianCalibration;
  scoringSpreadPolicy?: SssPlusSpreadEvidence;
  scoringTailPolicy?: TargetTailEvidence;
  intervalUnavailableReason?: string;
  scoringTarget?: TargetScore;
  scoringTargetSuccesses?: number;
  scoringBelowTarget?: number;
  scoringSource?: "submitted" | "official";
  scoringChartEffect?: number | null;
  scoringChartEffectCi95Low?: number | null;
  scoringChartEffectCi95High?: number | null;
  scoringInformation?: number | null;
  scoringProvisionalReasons?: string[];
  scoringComparability?: string;
  scoringPossiblyTruncated?: boolean;
  scoringSourceStates?: Record<string, OfficialScoringSourceState>;
  scoringFitConverged?: boolean;
  scoringBootstrapValidReplicates?: number;
  intervalMethod?: string;
  initialFit?: Record<string, number | string | null>;
  mode: "Singles" | "Doubles" | "Co-op";
  modeRank: number | null;
  levelRank: number | null;
  levelPercentile: number | null;
  levelComparisonCharts: number | null;
  folder: string;
  relativeGroupRank: number | null;
  relativeGroup: string | null;
  effectBandRank: number | null;
  effectBand: string | null;
  songName: string;
  difficulty: string;
  type: "Single" | "Double" | "CoOp";
  level: number;
  chartId: string;
  imageUrl: string | null;
  noteCount: number | null;
  stepArtist: string | null;
  bpmMin?: number | null;
  bpmMax?: number | null;
  /** Full display endpoints remain separate from the numerical focal tempo. */
  bpmFocal?: number | null;
  songType?: string;
  estimatedDifficulty: number | null;
  officialEvidence?: OfficialTierEvidence;
  officialContributors?: number;
  scoringMethod?: ScoringMethod;
  officialScoringEvidence?: OfficialScoringEvidence | null;
  officialScoringContributors?: number;
  scoringChartCost?: number | null;
  scoringChartCostCi95Low?: number | null;
  scoringChartCostCi95High?: number | null;
  scoringMainComponent?: boolean;
  scoringComponent?: number | null;
  scoringExtrapolated?: boolean;
  scoringPlayerCount?: number;
  scoringMinimumOtherCharts?: number;
  scoringComparisonCharts?: number;
  scoringComponentCount?: number;
  scoringProvisional?: boolean;
  scoringMeanGap?: number | null;
  scoringGapStdDev?: number | null;
  scoringGapCi95Low?: number | null;
  scoringGapCi95High?: number | null;
  scoringUnratedReason?: string | null;
  scoringPointsPerLevel?: number | null;
  scoringSpreadCalibration?: OfficialScoringSpread | null;
  scoringExtremeOutlier?: boolean;
  tierMetrics?: {
    clearing: ClearingTierMetric;
    pumbility: PumbilityTierMetric;
  };
  scoringScoreProfile?: [number, number, number] | [number, number, number, number, number] | null;
  scoringProfileMatchDifficulty?: number | null;
  scoringFolderReferenceDifficulty?: number | null;
  scoringDifficultyScale?: number | null;
  scoringProfileMatchCi95Low?: number | null;
  scoringProfileMatchCi95High?: number | null;
  scoringProfileRmse?: number | null;
  scoringProfileExtrapolated?: boolean;
  difficultyModelContinuous?: number | null;
  difficultyModelSignal?: number | null;
  difficultyModelSupportCount?: number | null;
  percentileScore?: number | null;
  percentileGrade?: string | null;
  percentilePlate?: string | null;
  percentilePlateCode?: string | null;
  percentileSupportCount?: number | null;
  whatIfEstimates?: Array<{
    level: number;
    estimatedDifficulty: number | null;
  }> | null;
  averageDifficulty: number | null;
  difficultyDelta: number | null;
  folderMeasuredCharts?: number | null;
  folderRangeCompression?: number | null;
  difficultyDeltaCi95Low: number | null;
  difficultyDeltaCi95High: number | null;
  difficultyCi95Low: number | null;
  difficultyCi95High: number | null;
  nContributors: number;
  nPlayersScored: number;
  phoenix1Contributors?: number;
  phoenix2Contributors?: number;
  evidenceStatus: EvidenceStatus;
  phoenix2Rerate?: ChartRerate;
}

export interface FolderSummary {
  catalogCharts: number;
  measuredCharts: number;
  publishedCharts: number;
  medianContributors: number | null;
  rangeCompression?: number;
  overratedCharts?: number;
  underratedCharts?: number;
}

export interface CoopDifficultyModelSummary {
  difficultyModel: string;
  difficultyTransform: string;
  difficultyConditionalQuantile: number;
  difficultyReferenceAbilityPercentile: number;
  difficultyReferenceSource: "phoenix2";
  difficultyCalibrationAnchors: {
    easiest: 10;
    median: 17;
    hardest: 25;
  };
  abilityCoverageObservations: number;
  abilitySameSourceObservations: number;
  abilityOppositeSourceObservations: number;
  abilityMedianFallbackObservations: number;
  difficultyFitObservations: number;
  difficultyResidualRefitIterations: 0;
  abilityCoefficients: number[];
  phoenix2SourceCoefficient: number;
}

export interface ModeSummary {
  eligiblePlayers: number;
  catalogCharts: number;
  measuredCharts: number;
  publishedCharts: number;
  pumbilityPerLevel?: number | null;
  calibration: Record<string, unknown>;
  difficultyModel?: CoopDifficultyModelSummary;
  shrinkage?: Record<string, unknown>;
  folders: Record<string, FolderSummary>;
}

export interface BpmMetadataIdentity {
  bpmMetadataVersion: 1;
  bpmPolicy: "focal-v1";
  bpmMetadataRevision: string;
  baseGeneratedAtUtc?: string;
  basePublicPayloadSha256?: string;
}

export interface AnalysisPayload {
  bpmMetadata?: BpmMetadataIdentity;
  /** Compact list rows keep calculation evidence in the on-demand chart route. */
  tierListView?: { version: 1; kind: "list"; detailsAvailable: boolean; detailsPrefix?: string; detailsGeneratedAtUtc?: string };
  schemaVersion?: number;
  generatedAtUtc: string;
  mix: MixInfo | CombinedMixInfo;
  summary: {
    scriptVersion: string;
    method: Record<string, unknown> & {
      officialTiers?: OfficialTierMethod;
      officialCrossLevelScoring?: OfficialCrossLevelScoringMethod;
      crossLevelClearing?: CrossLevelClearingMethod;
      hybridTiers?: HybridTierMethod;
      scoringGoalPolicies?: { version: 1; goals: ScoringGoalPolicy[] };
      availableScoringGoals?: ScoringGoal[];
      sssTailPolicy?: {
        version: 1; method: "sss-target-bounded-tails-v1"; baseSchemaVersion: 57 | 58;
        baseScriptVersion: string | null; basePayloadSha256: string;
        tailPolicy: Record<string, unknown>;
      };
    };
    coverage: Record<string, number>;
    modes: Partial<Record<ModeKey, ModeSummary>>;
  };
  singles: ChartResult[];
  doubles: ChartResult[];
  coop?: ChartResult[];
  relativeGroups: Array<{ rank: number; name: string }>;
  effectBands: Array<{
    rank: number;
    name: string;
    low: number | null;
    high: number | null;
  }>;
}

export interface TierChartDetailsResponse {
  bpmMetadata?: BpmMetadataIdentity;
  generatedAtUtc: string;
  chart: ChartResult;
}

export type AnalysisJobState = "queued" | "running" | "completed" | "failed";
export type AnalysisJobStage = "discovering" | "syncing" | "analyzing" | "publishing";

export interface AnalysisJobStatus {
  id: string;
  status: AnalysisJobState;
  stage: AnalysisJobStage;
  progress: {
    current: number;
    total: number;
    percent: number;
    message: string;
    phase?: string;
    indeterminate?: boolean;
  };
  createdAtUtc: string;
  updatedAtUtc: string;
  startedAtUtc: string | null;
  completedAtUtc: string | null;
  generatedAtUtc: string | null;
  retryAllowedAtUtc: string | null;
  error: string | null;
  mix: MixKey;
}

export type AnalysisRefreshResponse =
  | {
      outcome: "fresh";
      generatedAtUtc: string;
      nextAllowedAtUtc: string;
    }
  | {
      outcome: "busy";
      activeMix: MixKey;
      error: string;
    }
  | {
      outcome: "started" | "existing";
      job: AnalysisJobStatus;
    };

export interface RecommendationChartEstimate {
  mode: "Singles" | "Doubles" | "Co-op";
  songName: string;
  difficulty: string;
  type: "Single" | "Double" | "CoOp";
  level: number;
  chartId: string;
  imageUrl: string | null;
  noteCount: number | null;
  stepArtist: string | null;
  bpmMin?: number | null;
  bpmMax?: number | null;
  /** Full display endpoints remain separate from the numerical focal tempo. */
  bpmFocal?: number | null;
  songType?: string;
  estimatedDifficulty: number;
  difficultyModelContinuous?: number | null;
  difficultyModelSignal?: number | null;
  percentileScore?: number | null;
  percentileGrade?: string | null;
  percentilePlate?: string | null;
  percentilePlateCode?: string | null;
  percentileSupportCount?: number | null;
  difficultyDelta: number | null;
  difficultyCi95Low: number | null;
  difficultyCi95High: number | null;
  nContributors: number;
  phoenix1Contributors: number;
  phoenix2Contributors: number;
  evidenceStatus: EvidenceStatus;
}

export interface RecommendationChart extends RecommendationChartEstimate {
  distanceFromRating: number;
  farmEdge: number;
  existingPumbility: number | null;
  expectedPumbility: number | null;
  existingCoopRating?: number | null;
  expectedCoopRating?: number | null;
  projectedGain: number | null;
  projectedScore: number | null;
  projectedGrade: string | null;
  projectedPlate: string | null;
  projectedPlateCode: string | null;
  projectedPlateProbability: number | null;
  plateProjectionSource: "phoenix1" | "phoenix2" | "population" | "fixed-fair-game" | null;
  scoreProjectionSource?: string | null;
  scoreProjectionSupportCount?: number | null;
  scoreProjectionConfidence?: "high" | "medium" | "low" | "limited" | "unavailable";
  played: boolean;
}

export interface RecommendationTopScore {
  mode: "Singles" | "Doubles" | "Co-op";
  songName: string;
  difficulty: string;
  type: "Single" | "Double" | "CoOp";
  level: number;
  chartId: string;
  imageUrl: string | null;
  noteCount: number | null;
  stepArtist: string | null;
  bpmMin: number | null;
  bpmMax: number | null;
  bpmFocal?: number | null;
  songType?: string;
  estimatedDifficulty: number | null;
  difficultyDelta: number | null;
  difficultyCi95Low: number | null;
  difficultyCi95High: number | null;
  nContributors: number | null;
  phoenix1Contributors: number | null;
  phoenix2Contributors: number | null;
  evidenceStatus: EvidenceStatus | null;
  /** Present on recommendation schema 26+ public Top 50 rows. */
  score?: number;
  pumbility?: number | null;
  coopRating?: number | null;
  grade: string | null;
  plate: string | null;
  plateCode: string | null;
}

export interface ClearingSkillMetadata {
  methodVersion: 2;
  difficultyBasis: "current-official-level";
  ranks: [1, 50];
  requiredClearCount: 50;
  uniqueClearCount: number;
  selectedCount: 0 | 50;
  status: "rated" | "insufficient-clears";
}

export interface RecommendationModeResult {
  eligible: boolean;
  manual?: boolean;
  validScoreCount: number;
  requiredScoreCount?: number;
  phoenix2ScoreCount?: number;
  phoenix2ScoreThreshold?: number;
  ratingSource?: "phoenix1" | "phoenix2" | null;
  ratingSourceScoreCount?: number;
  ratingBaselineRanks?: [number, number];
  ratingBaselineLabel?: string;
  reason?: string;
  baselineRanks?: [number, number];
  baselineLabel?: string;
  baselinePumbility?: number | null;
  scoringRating?: number;
  clearingRating?: number | null;
  clearingSkill?: ClearingSkillMetadata;
  projectionRating?: number | null;
  projectionRatingSource?: "phoenix1" | "phoenix2" | null;
  projectionRatingSourceScoreCount?: number;
  projectionRatingRequiredScoreCount?: number;
  projectionRatingRanks?: [number, number];
  projectionRatingLabel?: string;
  ratingReferenceGrade?: string;
  ratingReferencePlate?: string;
  ratingReferenceMultiplier?: number;
  projectionAvailable?: boolean;
  scoreProjectionModel?:
    | "population-crossfit-monotone-v1"
    | "population-crossfit-monotone-v2"
    | "similar-skill-top100-q75-v1"
    | "similar-skill-top100-q50-v1"
    | "similar-skill-top100-q50-v2"
    | "similar-skill-staged-q50-v3"
    | "similar-skill-all-q50-v4"
    | "similar-skill-all-q50-v5"
    | "similar-skill-pumbility-11-30-q50-v6"
    | "similar-skill-pumbility-11-30-weighted-q50-v8"
    | "similar-skill-pumbility-11-30-weighted-q50-v9"
    | "chart-population-q75-v1"
    | "estimated-difficulty-master-grade-ladder-v1"
    | "estimated-difficulty-master-grade-ladder-v2"
    | "estimated-difficulty-master-grade-ladder-v3"
    | "estimated-difficulty-master-grade-ladder-v4"
    | "estimated-difficulty-master-grade-ladder-v5";
  pumbilityPerLevel?: number | null;
  currentTop50Pumbility?: number;
  currentTop50CutoffPumbility?: number | null;
  currentTop50Count?: number;
  currentCoopRating?: number;
  top50ModeCounts?: Record<Exclude<ModeKey, "coop">, number>;
  sourceModeEligibility?: Record<Exclude<ModeKey, "coop">, boolean>;
  sourceRecommendationCounts?: Record<Exclude<ModeKey, "coop">, number>;
  candidateRange?: [number | null, number];
  candidateCount?: number;
  filterCandidateCount?: number;
  difficultyOptions?: string[];
  filterCandidates?: RecommendationChart[];
  topScores: RecommendationTopScore[];
  topRecommendations: RecommendationChart[];
}

export interface RecommendationScoreProgress {
  validScoreCount: number;
  requiredScoreCount: number;
}

export interface RecommendationPlayerSummary {
  playerKey: string;
  username: string;
  displayName: string;
  eligibility: Record<ModeKey, boolean>;
  scoreProgress?: Partial<Record<ModeKey, RecommendationScoreProgress>>;
}

export interface RecommendationPlayer {
  playerKey: string;
  username: string;
  displayName: string;
  manual?: boolean;
  modes: Record<Exclude<ModeKey, "coop">, RecommendationModeResult>
    & Partial<Record<"coop", RecommendationModeResult>>
    & Partial<Record<"overall", RecommendationModeResult>>;
}

export interface RecommendationPlayersResponse {
  generatedAtUtc: string;
  modelGeneratedAtUtc?: string;
  modelComputedAtUtc?: string;
  sourceCheckedAtUtc?: string;
  refreshSupported?: boolean;
  method: Record<string, unknown>;
  players: RecommendationPlayerSummary[];
}

export interface PlayerRecommendationsResponse {
  bpmMetadata?: BpmMetadataIdentity;
  generatedAtUtc: string;
  recommendationsGeneratedAtUtc?: string;
  modelGeneratedAtUtc?: string;
  modelComputedAtUtc?: string;
  currentModelGeneratedAtUtc?: string;
  currentModelComputedAtUtc?: string;
  sourceCheckedAtUtc?: string;
  currentSourceCheckedAtUtc?: string;
  playerSyncedAtUtc?: string;
  modelGeneration?: string;
  stale?: boolean;
  legacySnapshot?: boolean;
  method: Record<string, unknown>;
  player: Omit<RecommendationPlayer, "modes"> & {
    modes: Partial<Record<RecommendationModeKey, RecommendationModeResult>>;
  };
}

export interface PlayerRefreshJob {
  id: string;
  kind: "player-recommendation-refresh";
  playerKey: string;
  status: "queued" | "running" | "completed" | "failed";
  stage: string;
  error?: string | null;
  progress?: {
    current: number;
    total: number;
    percent: number;
    message: string;
  };
}

export type PlayerRefreshResponse =
  | {
      outcome: "fresh";
      recommendation: PlayerRecommendationsResponse;
      refreshEligibleAtUtc: string;
    }
  | {
      outcome: "started" | "existing";
      job: PlayerRefreshJob;
    };
