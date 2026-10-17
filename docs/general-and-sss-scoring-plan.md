# General and SSS Scoring implementation plan

October 17, 2026. Local implementation proposal.

Add a shared **General / SSS** selector to Scoring and Pumbility. SSS is the
default when the URL does not specify a selection. General preserves the current
local hybrid Scoring results exactly. Each selection uses the same existing
Clearing component and produces its own arithmetic-average Pumbility list.
The current local dataset remains selected until implementation is requested.

## Target and meaning

SSS means a best recorded successful score of at least **990,000**, inclusive,
using the current boundaries in `phoenix2_pumbility.py`. A score below the target
is an observed best that has not reached SSS; a missing score is unknown. Broken
records do not become extra target failures. This estimates target attainment
among the observed best-score population, not a probability of succeeding on one
attempt or the number of attempts needed.

SSS needs a new fit. In the existing additive General model, changing the target
score adds the same constant to every chart's required ability and preserves the
ordering. A separate target-achievement model can change the ordering by looking
at which charts the same players have and have not reached 990,000 on.

## Sources and unchanged behavior

| Component | Levels 16–19 | Levels 20+ |
|---|---|---|
| General Scoring | Current submitted score-profile algorithm | Current merged official comparison algorithm |
| SSS Scoring | Target fit from cached submitted scores | Target fit from merged official scores |
| Clearing | Current folder-centered method | Current curve using only 20+ anchors |

Reuse the existing score selection, normalization, comparability and deterministic
player matching. For official boards, keep each matched player/chart's higher
Phoenix 1 or Phoenix 2 score before classifying target attainment. For submitted
data, reuse the existing successful best-record selection and Phoenix 1 score
normalization, then apply the Phoenix 2 threshold. Do not join submitted account
identities with official identities by display name.

Fit Singles and Doubles independently. The submitted SSS model can use the
existing valid submitted 16+ scoring histories to estimate its player effects;
only its 16–19 chart estimates enter the displayed variant. The official model
uses official 20+ histories and supplies the 20+ estimates. These are independent
source cohorts, with independent calibration. Preserve all-history top-30/200
Clearing skill, Co-op, personal score overlays and recommendation artifacts.

## Proposed target model

Use a regularized logistic model with a player effect and a chart effect:

    y[player, chart] = 1 if bestScore >= 990000 else 0
    P(y = 1 | recorded score) = sigmoid(playerTargetSkill - chartTargetDifficulty)

This is the person/item structure of the
[Rasch model](https://rasch.org/memo42.htm), proposed here for PIU best records.
Its assumptions and validation need to be assessed on this dataset. The chart
effect represents the target skill at which the fitted conditional attainment
probability is 50%; it is a latent model coordinate before tier calibration.

Use a sparse deterministic optimizer, fix the location of the player effects,
and record convergence and component membership. Keep the existing minimum of
four observed eligible charts per player. Balance observation weights so a
player with a much longer history does not dominate the fit; version and record
the weighting formula. Select regularization on held-out observed player/chart
records, with a fixed seed and candidate grid. Official levels must not be used
as priors that predetermine the chart ordering.

Regularization makes computation finite; it does not create evidence for
all-success or all-below-target charts. Initially publish point estimates only
for charts with both observed outcomes, at least five usable contributors,
membership in the calibrated comparison component and a converged stable fit.
Fewer than five contributors on either side, weak information, truncation,
comparability problems and extrapolation receive provisional evidence. Charts
with only one observed outcome retain counts and an explicit one-sided-evidence
reason and remain Unrated in this first version. Do not silently substitute
General. [Extreme outcomes do not identify a finite unregularized Rasch
measure](https://www.rasch.org/rmt/rmt122h.htm).

Resample players and refit the target model for uncertainty, preserving each
resampled player's records together. Keep the calibration fixed for the initial
interval implementation and state that anchor uncertainty, effort and selective
observation are excluded. Test sensitivity to regularization and source removal.

## Calibration and Pumbility

For official SSS, select comparable reference charts with at least 20 usable
contributors and at least five outcomes on each side of 990,000. Require five
reference charts per folder and three increasing supported folder medians,
including level 20. Calibrate these target-specific chart effects to official
level plus 0.5 using the existing offset-exponential interpolation and labeled
boundary extrapolation. Do not reuse General chart costs or its fitted anchors.
Fail closed on non-increasing or unsupported calibration instead of sorting
medians or imposing desired tier counts.

For submitted SSS, fit a separate median-anchor calibration from supported
submitted folders at 16+, applying the same reference support rules. Generalize
the calibration helper to declare its minimum anchor level; the submitted curve
must include 16 and have three increasing supported anchors. Apply its estimates
only to displayed 16–19 charts. This preserves the source boundary at 20 and
does not mix submitted and official latent coordinates. The 20+ anchor
restriction for Clearing remains unchanged.

Apply midpoint compression once to each new initial SSS estimate and interval:

    M = officialLevel + 0.5
    finalSSS = M + 0.5 * (initialSSS - M)
    GeneralPumbility = (finalGeneralScoring + finalClearing) / 2
    SSSPumbility = (finalSSS + finalClearing) / 2

Use full precision until the existing display truncation. Require both components
for each composite, retain their separate support counts and weaker evidence,
and compute independent ranks, groups and bands for each variant. General and
Clearing are already compressed and must not receive another pass.

## Data and interface work

1. Freeze the current schema-48 candidate and its General/ Clearing values. Add
   a private target-observation builder and a separate SSS analysis module.
   Produce coverage and fit diagnostics before publishing a local candidate.
2. Allocate an unused schema and model identity at implementation time. Add
   explicit `general` and `sss` Scoring/Pumbility variants, each with its
   own method, initial fit, intervals, evidence, support and ranks. Retain current
   fields as the General compatibility view; use one shared selector to project
   the chosen variant consistently throughout the UI. Publish no player IDs.
3. Extend Python/frontend validators and local candidate selection. Validate
   both variants as a complete generation, including source hashes, score target,
   weighting, calibration, compression and averaging. Keep readers for existing
   generations and a rollback to the current local candidate.
4. Add the accessible segmented control labeled **Scoring goal** on both Scoring
   and Pumbility. Use `scoring=general` or `scoring=sss` in the URL; missing
   means SSS. Preserve it when switching mode or metric, through history
   navigation and in shared links. Clearing ignores the choice while retaining
   it for returning to the other tabs. Co-op keeps its existing behavior.
5. Make cards, sorting, tier groups, contributor labels, interval tooltips and
   chart details use the same selected variant. In Pumbility details, show the
   matching Scoring number and the unchanged Clearing number. Show `SSS ≥990,000`
   and the reason when the selected variant is unavailable. Retain All, Only 20+,
   folder, BPM, search and personal-score filters.

## Coverage findings and acceptance

The pinned local official inputs have 120 of 137 populated S20 boards with scores
on both sides of the target. At S25, 5 of 13 populated boards contain no observed
SSS; S26 has none on either populated board. High-end coverage will therefore be
lower in a strict target-only model. These counts precede player eligibility and
comparability filtering; they are not counts of publishable estimates. Preserve
source truncation flags because a leaderboard containing only SSS scores may
omit below-target players. The local diagnostic is
`.local-data/sss-scoring-plan/target-coverage.json`.

Before selecting the new candidate, verify:

- General ratings, intervals, evidence and ordering are unchanged; Clearing,
  Co-op, recommendations and personal artifacts are unchanged.
- Threshold boundaries at 989,999 / 990,000 / 1,000,000, cross-mix deduplication,
  score normalization, missing observations, broken records, separated outcomes,
  truncation and disconnected components behave as declared.
- Synthetic charts can change order between General and SSS, and changing the
  surrounding player population does not directly become an unadjusted target
  success-rate ranking. Diagnose fit on held-out observations against an
  unadjusted chart-rate baseline and a target prediction from General; require
  convergence, finite values and recorded loss/calibration results.
- Supported reference medians map to their declared midpoints, new intervals
  receive one compression, and both composites use the correct full-precision
  average. Missing SSS remains missing in its Pumbility variant.
- Fresh Scoring and Pumbility links default to SSS. Explicit General links,
  mode/metric switching, back/forward, details and Only 20+ preserve the selection
  and matching numbers. Historical payloads render their existing General data
  and explicitly indicate that SSS is unavailable.
- Run affected Python/frontend tests, TypeScript, a production build and browser
  checks. Read the installed Next.js guide before UI edits. Perform one general
  review, repair proven regressions only and stop when relevant checks pass.

Implement and select this locally. Production remains on its already deployed
policy until a later release instruction. An ordinal or censored-score extension
for charts with no observed SSS is separate modeling work; do not add it silently
to make the high-level folders fully populated.
