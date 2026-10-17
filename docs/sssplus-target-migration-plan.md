# SSS+ difficulty implementation plan

October 18, 2026. Follow-up proposal after the local SSS centering implementation,
including limits on extreme target ratings.

Replace SSS with SSS+ as the target of the default scoring variant and its
Pumbility list. The target becomes a recorded successful best score of at least
995,000, inclusive. General Scoring, Clearing and General Pumbility retain their
current definitions and values. The grade threshold agrees with the existing
mapping in `phoenix2_pumbility.py` and its grade-boundary tests.

Keep ordinary target ratings inside two difficulty points of the chart's
current official midpoint, level + 0.5. Permit at most ten strongly supported
exceptions across both Singles and Doubles and all rated levels combined.
Ten is a ceiling, with no minimum number of exceptions. This is an explicit
rating-scale constraint; leaderboard data alone do not establish that only
ten charts can be misrated by that amount.

## Current data preflight

The same pinned submitted records and combined official boards can support this
experiment without another data pull. Player deduplication, cross-version score
selection, normalization and chart eligibility remain unchanged. Raising the
threshold changes the binary outcomes, reference populations and boundary cases.

| Current official folder | Mixed SSS+ boards | No SSS+ boards | No data | Potential reference charts |
|---|---:|---:|---:|---:|
| D26 | 10 | 26 | 3 | 0 |
| S25 | 1 | 12 | 2 | 0 |
| S26 | 0 | 2 | 0 | 0 |

These are observation counts before a new model fit. Potential references use
the current shared-player eligibility and comparability rules; they do not
establish convergence, stability or increasing fitted anchor costs. Count
preflight suggests enough potential reference charts for official Singles
20–24, official Doubles 20–25 and submitted Singles/Doubles 16–19. Their curves
still require a successful new fit and the existing monotonicity checks.

D26 can potentially retain its own final median centering because ten mixed
charts exceed its five-chart support requirement. S25 has only one potential
mixed first-pass chart, and S26 has none; their SSS+ centering would borrow an
offset rather than promise a measured median of 25.5 or 26.5. Do not relax
the support rule to force those medians. The current submitted Doubles SSS
calibration failure must be reassessed from the new SSS+ fit, rather than
assumed to persist or to disappear.

The private count audit is
`.local-data/sss-centered-experiment/sssplus-preflight.json`. No SSS+ model has
been fitted and these counts do not represent SSS+ tier ratings.

## Extreme rating diagnosis

The active schema 51 SSS candidate at 990000 has 2077 finite target ratings.
Of these, 90 differ from their current official midpoint by more than two:
75 below it and 15 above it. There are 82 such charts at level 20+ and eight
below 20. By estimation branch, 40 are all-success boundaries, 11 are
no-success boundaries and 39 are mixed-outcome first-pass estimates.

| Chart | Official midpoint | Current SSS rating | Difference |
|---|---:|---:|---:|
| BANG BANG D20 | 20.5 | 9.2110 | -11.2890 |
| The Stranger D22 | 22.5 | 19.1922 | -3.3078 |
| 1948 D29 | 29.5 | 42.7458 | +13.2458 |

The boundary conversion adds an unrestricted score-gap extension to a frozen
range edge, then uses the anchor curve's linear extrapolation outside its
supported domain. An already distant edge and a large gap compound. The
existing 50% compression and subsequent folder translation do not bound this
distance. Thirty-five of the 39 mixed-outcome extremes also extrapolate beyond
their anchor domain, so limiting only the boundary formulas is insufficient.

All 40 extreme all-success boards are marked possibly truncated. Reaching
SSS on every returned top score is evidence about the returned leaderboard,
not proof that every player reaches the target. Conversely, 1948's estimate
uses only three returned records. Neither case supports unrestricted distance
from the official midpoint. Keep their score-gap ordering as provisional
evidence while constraining the published rating scale.

The private audit and soft-tail counterfactual are saved in
`.local-data/sss-centered-experiment/sss-tail-preflight.json`. These are current
SSS results and a scale-policy preview, not predictions from a fitted SSS+
model. The SSS+ fit must receive its own before/after audit.

## Target configuration and historical compatibility

Introduce one explicit target configuration carrying its goal key, grade,
inclusive score threshold, method/policy identities and near-target score
window. Use `sssplus`, `SSS+` and 995000 for the new experiment. Keep the
historical SSS configuration fixed at 990000 so schemas 49–51 remain readable
and their existing data are never relabeled as SSS+.

Parameterize new target production, refinement, ranking and centering helpers
by this configuration. Preserve compatibility wrappers or versioned readers
for the existing SSS generations. Changing a shared global constant alone
would invalidate historical validation and allow stale target caches to pass.

Audit target-dependent code in `sss_scoring.py`, `sss_scoring_model.py`,
`sss_scoring_refinement.py`, `sss_folder_centering.py`, the local builders,
frontend target projection, validators and chart explanations. The numeric
model already accepts `score_target`; point recovery, source sensitivity and
the training-only General comparison benchmark must receive the same target.

## New model and calibration

Refit the four independent cohorts at 995000: submitted Singles, submitted
Doubles, official Singles and official Doubles. Keep player identity cohorts
separate, four-chart history eligibility, equal total weight per player,
cross-validation and the current regularization candidates. Recompute source
removal diagnostics, reference eligibility, target effects and the 200
player-bootstrap samples. General model and Clearing outputs are reused.

Cache identity must include the numeric target and target-policy version in
addition to boards, source boards, code fingerprint, seed, bootstrap count and
model settings. Reuse new SSS+ fits only when all identities match. Existing
SSS effects, player abilities, bootstrap intervals and score-gap slopes cannot
be reused at the higher target.

Build independent median-anchor curves from the newly qualified SSS+
references. Keep the existing contributor, per-outcome, comparability and
minimum-folder-reference requirements. Freeze the finite first-pass SSS+
ranges and their contributing IDs before refinement. Do not reuse the SSS
ranges or its centering offsets. Unsupported or non-increasing calibration
retains an explicit Unrated reason.

## Boundary and rare-success refinement

Classify each complete validated board before filtering for player history.
Score 995000 reaches SSS+; score 994999 does not. Missing records remain unknown.

For a nonempty board entirely below SSS+, retain the new top-three policy:

    selected = highest min(3, available distinct-player scores)
    gap = 995000 - mean(selected)

For a board where every returned score reaches SSS+:

    gap = lowest returned best score - 995000

Keep the selected scores, their count, full-board and model-eligible counts,
returned-board cap and endpoint policy in the diagnostics. Preserve the
existing calibrated latent extension formulas and once-only midpoint
compression as the pre-tail estimate. The final scale constraint described
below limits their published values. No-data boards remain Unrated.

Refit the positive score-points-per-latent-unit conversion using the new SSS+
player and chart effects. The initial target-relative training window is
985000–999999, clipped below the perfect-score cap. Exclude 1000000 from that
scale fit. Retain supported mixed-chart selection, deterministic chart holdout,
robust fitting candidates and the existing improvement requirement. Record the
new training range and held-out diagnostics; unsupported conversion fails
closed instead of falling back to the old SSS slope.

For one through four SSS+ outcomes, use the existing fixed-player conditional
chart refit, full-board/eligible-success agreement, blend weights and ranking
within the top 5% of the newly frozen numeric range. Five or more successes
retain their first-pass point during refinement. Boundary and rare-corrected estimates remain
Provisional and have no formal interval. This migration retains the current
rare-success band policy; it does not widen that band.

## Folder centering and Pumbility

Apply the implemented centering policy to the new SSS+ results. Official 20+
folders need five finite mixed first-pass charts with positive width and five
finite final charts to supply an offset. All finite final SSS+ estimates set
the supported-folder median at level + 0.5. Sparse folders borrow only within
their source and mode. Preserve original model coordinates, original frozen
edges, effective shifted ranges and interval conditions.

Recompute offsets from SSS+ rather than importing the SSS values. Apply each
offset before the single 50% compression, retain the current unavailable-
interval policy, then apply the final tail policy below. Do not recenter after
the tail policy or feed its values back into player abilities, anchor curves,
frozen first-pass ranges, gap calibration or rare-success ranks.

    SSS+ Pumbility = (final SSS+ Scoring + unchanged Clearing) / 2

Use full precision before display truncation. Both components are required,
evidence follows the weaker component and support counts stay separate.
Pumbility receives no independent centering or additional compression.

## Final tail policy

Apply this new stage to every finite SSS+ target rating, including submitted
below-20 estimates and mixed-outcome first-pass charts. It affects the target
variant only. General Scoring, Clearing and General Pumbility remain unchanged.
Use the chart's current official level, not a leaderboard's historical level.

### Ordinary ratings

After refinement, folder centering and the existing single 50% compression,
let `m = level + 0.5`, `d = preTailRating - m` and `a = abs(d)`. Leave the
central range unchanged and taper larger distances smoothly:

    if a <= 1.5:
        finalDelta = d
    else:
        excess = a - 1.5
        finalDelta = sign(d) * (1.5 + 0.4 * excess / (0.4 + excess))
    finalRating = m + finalDelta

This function is continuous, has matching slope at 1.5, preserves ordering
and tends toward a distance of 1.9. The 0.1 margin below two also prevents
ordinary ratings from displaying as a two-point outlier under the current
one-decimal truncation policy. It avoids placing every extreme at one hard
clipping value, although distinct values can still share a displayed tenth.

Applying this ordinary rule alone to the current SSS data changes 131 of
2077 finite ratings, removes all 90 two-point extremes and preserves every
supported official folder's exact x.5 median. For example, BANG BANG D20
becomes 18.6157, The Stranger D22 becomes 20.6725 and 1948 D29 becomes 31.3868.
These illustrate scale control at 990000; their eventual 995000 values must
come from the new fit.

### Exceptional ratings

At most ten unique charts may retain a distance of at least two, counting
both directions, both modes and all rated levels together. Do not allocate
ten per folder, mode, source or side, and do not fill unused places.
One-sided boundaries, rare-success corrections, capped boards and borrowed
calibration cannot qualify through a large score gap alone.

Use the following conservative eligibility gates, evaluated from pre-tail
diagnostics. A candidate must have absolute pre-tail delta at least two and
be an ordinary mixed-outcome first-pass estimate
with at least 20 eligible players and at least five outcomes on each side of
995000; a converged, comparable fit in the main player component; no
regularization-sensitivity or weak-information flag; an uncapped board;
supported local calibration without anchor extrapolation; and a finite
player-bootstrap interval lying entirely beyond the same +2 or -2 boundary.
For an official 20+ folder, its centering offset must also be measured from
that folder. An unavailable interval fails eligibility. Preserve the existing
interval validity requirements rather than manufacturing support.

Preserve within-folder order while selecting exceptions. For each
source/mode/level and side, order charts from the largest absolute pre-tail
distance inward. Only the contiguous eligible outer tail may qualify: stop
at the first ineligible chart. Protect the central chart, or central pair,
that determines each supported folder's median. Treat equal pre-tail ratings
as an indivisible block so the policy cannot split an existing tie.

Allocate the global ten-chart budget to these tails one outer block at a time.
Among each tail's next available block, prioritize the largest conservative
distance, defined by the bootstrap endpoint nearest the midpoint. Use the
minimum of that distance within a tied block; break remaining ties by the
block's minimum eligible player count, then its smallest chart ID. A block must fit in the remaining budget and its
outer predecessors must already be selected. If it cannot fit, close that
tail and consider the others. This selection never skips an outer chart to
promote an inner chart.

Selected exceptions retain their pre-tail delta, clipped to [-2.5, +2.5].
All other charts use the ordinary taper. Since selected exceptions form the
outer tails and have absolute pre-tail delta at least two, this preserves
within-folder ordering; clipping may create ties. A sparse or capped
one-sided chart cannot become an exception simply because its raw estimate
is extremely large. Fewer than five exceptions, including zero, is acceptable
when the evidence does not support more.

### Medians, intervals and diagnostics

The ordinary transform is odd around each official midpoint. A supported
centered folder's middle value stays zero, and an even folder's middle pair
remain equal and opposite. Protecting those median-defining charts from the
exception policy preserves the exact x.5 median without another recentering
step. Sparse folders retain their documented borrowed-offset status and do
not acquire a claim of exact median calibration.

Store pre-tail ratings and intervals, policy parameters, ordinary projection,
exception eligibility/rejection reasons, selected exception IDs and final
values. Keep the original initial fit, frozen range and centering diagnostics
intact. The new schema must replay compression, centering and this nonlinear
stage separately; do not synthesize a different initial fit merely to satisfy
an older linear-only validator.

Transform available interval endpoints using the selected chart's fixed tail
map. Describe these as conditional intervals for the postprocessed rating,
with the exception decision and calibration held fixed. Clipped or tapered
intervals do not imply more measurement precision. Boundary and rare-success
estimates continue to have no formal interval. Show original centered ranges
separately from final post-policy chart ranges; a folder can contain both
ordinary and exceptional maps, so a single translated range is insufficient.

Recompute final ranks, bands and SSS+ Pumbility after this stage. Record counts
at both `abs(finalRating - midpoint) >= 2` and the corresponding displayed
rating distance, not just strictly greater than two. Both counts must be at
most ten across the entire target dataset. No finite target rating may extend
more than 2.5 points. Do not apply a second tail policy to Pumbility; its
Clearing component retains its existing definition.

## Data contract and interface

Allocate the next unused schema version; 52 is the expected next generation
after schema 51, subject to an implementation-time check. Give the target
configuration and each target-dependent policy an explicit identity. Use
target-specific variant keys and generic outcome branch names in the new
generation, while preserving the historical SSS contracts.

Version the tail policy and its parameters in the candidate identity and
validation contract. Its changes invalidate postprocessing outputs, not an
otherwise identical target-model fit. Historical schemas 49–51 retain their
original values and are never silently subjected to this new constraint.

Update schema types to represent the target actually present rather than
requiring every historical/new target key in every payload. Validators must
check that the target label, numeric threshold, source policy, methods and
selected variant agree. A 990000 payload cannot claim SSS+ availability.

The new local candidate exposes General and SSS+ toggles, with SSS+ selected by
default for Scoring and Pumbility. Use canonical `scoring=sssplus` links. On the
new generation, normalize an old `scoring=sss` bookmark to the replacement
target and its canonical URL; the page must visibly identify SSS+ and 995000.
Historical SSS payloads retain their correct SSS label and 990000 threshold.

Derive target labels from the selected dataset/configuration throughout chart
details, target counts, boundary shortfall/surplus explanations, provisional
reasons, support labels, interval descriptions and method notes. Clearing
continues to hide the target selector. Preserve Back/Forward behavior, mode,
filters, General selection and the Only 20+ option. An unavailable SSS+ point
must not display a General or historical SSS estimate as its substitute.
Chart details should explain ordinary tail shrinkage or an exceptional rating
and show the pre-tail estimate without implying that it is the published tier.

## Implementation and acceptance

1. Freeze the current centered SSS candidate, source identities and preserved
   General/Clearing outputs. Build SSS+ into a separate local candidate and fit
   cache directory, leaving schema 51 available for rollback.
2. Add target configuration, parameterized helpers and target-aware cache
   checks. Rebuild only the four target cohorts and their dependent calibration,
   refinement and centering. Add the tail policy after the single compression,
   then recompute final ranks and target Pumbility. No source fetch is required
   for a controlled comparison.
3. Extend schema validation, historical reading, selected-target projection,
   labels and local candidate selection. Select SSS+ locally only after its
   complete candidate validates.
4. Test 994999/995000 boundaries, 990000–994999 below-target outcomes, perfect
   scores, fractional normalized values, top-three means with one/two/three
   players, deduplication, caps, rare-success transitions, donor isolation,
   unsupported curves, target-aware cache rejection and once-only compression.
   Test tail continuity, monotonicity, unchanged central values, both signs,
   extreme finite inputs, odd/even folder medians, protected central pairs,
   indivisible ties, ineligible outer blockers and a global budget spanning
   modes, sources, folders and directions. Test both exact and displayed
   two-point counts, the 2.5 absolute ceiling and conditional interval replay.
5. Verify exact preservation of General Scoring, Clearing, General Pumbility,
   Co-op, recommendation and personal artifacts. Verify target mean formulas,
   evidence, independent ranks, supported-folder medians, interval conditions
   and the complete historical SSS readers.
6. Report before/after coverage, reference and centering support, one-sided
   counts, borrowed folders, changed targets and real chart examples including
   Passacaglia, The Stranger, BANG BANG D20, 1948 D29, Paradoxx S26 and D26.
   Include pre-tail/final extrema, counts outside 1.5/2/2.5, exception IDs with
   evidence, and display clustering at the ends. Verify no median drift or
   within-folder order inversion. Distinguish coverage from statistical fit
   quality and provisional boundary extrapolation.
7. Run affected Python/frontend tests, TypeScript, a build and browser checks
   for General/SSS+ Scoring, Pumbility and historical navigation. Perform one
   general review, repair proven regressions, rerun affected verification and
   stop. The implementation remains a local experiment until a release is
   separately requested.
