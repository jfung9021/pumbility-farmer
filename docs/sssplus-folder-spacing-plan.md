# SSS+ median calibration implementation plan

## Local spread and combined Clearing follow-up

The follow-up local cap-policy version 2 treats Phoenix 1's cap as diagnostic
only for D26+ Clearing. Phoenix 2's cap still activates the existing default.
The source flags remain unchanged; `defaultedAtCap` records the effective
calculation decision. Phoenix 1-capped charts contribute their own observed
clearer-skill estimates to the folder reference/spread and receive conditional
bootstrap intervals when supported. These estimates remain provisional and
exclude selection uncertainty. A separate local output preserves the earlier
any-source-cap candidate for rollback. Scoring and player-history qualification
are unchanged; only Clearing calibration and affected Pumbility are rebuilt.

The local schema-54 overlay preserves the complete schema-53 version-3
baseline. Official S20+/D20+ SSS+ ratings now apply a scale-0.55 asinh
transformation to midpoint-centered values after the single 50% adjustment,
then the ordinary tail safeguard. Below-20 estimates, General Scoring,
historical SSS and Co-op are preserved. A roughly central, bell-shaped
tendency is a diagnostic, not a required histogram or a count quota.

The rare-target upper-5% rank allocation is replaced with a chart-only
conditional logistic estimate using frozen player abilities and a Gaussian
folder prior. A positive normalized prior-scale grid is evaluated through
conditional Laplace marginal likelihood on comparable mixed charts with
20 contributors and five outcomes per side. Folder variances can pool
within the same source/mode; insufficient donors or a pooled grid-boundary
optimum fall back to the frozen model's selected regularization. The cached
data selects that fallback in both official modes. The original player
fit is replayed only to recover omitted abilities. This is a conditional
preview without independent predictive validation or new formal intervals.

One-sided outlier exceptions require 200 returned scores, known chart
comparability, supported local range/centering and score-gap calibration,
a gap greater than 1.25 times held-out calibration MAE, matching source
cutoffs, and a distance of at least two under fixed-curve slope and endpoint
sensitivity checks. These are conditional diagnostics, not confidence
intervals. Selected one-sided outliers bypass the ordinary spread and have
a conservative distance ceiling of two. Existing mixed-outcome exceptions
retain their 2.5 ceiling. Both paths share the ten-chart global budget,
protect median-defining ties and preserve ordering. No chart ID controls
eligibility. Capped one-sided estimates remain provisional.

The separate schema-55 overlay changes only D26+ Clearing and its Pumbility
components. Both official Phoenix versions supply target clearers and the
D23+ histories used for the top-25 other unique-clear ability. Exact full
game tags match identities; each merged player/chart counts once. Submitted
and supplemented records are excluded. The calculation retains the existing
q20 statistic, spread calibration, median recentering and single midpoint
compression. A source's truncation flag activates the existing capped-board
default; a union exceeding 300 players does not itself imply truncation.
Clears match the current chart ID and mode. Score-comparability flags are
provisional diagnostics and do not discard valid binary clear records.
Per-source dates, counts and cap flags remain visible. General/SSS/SSS+ Pumbility use the updated
Clearing component with their unchanged respective Scoring values.

Both overlays are cached-only local experiments, preserve exact predecessor
recovery, and retain the source snapshots. No data pull, deployment or
bootstrap target-model refit is part of this follow-up.

October 18, 2026. Follow-up to the implemented local schema 52 SSS+ experiment.

Calibrate both the location and scale of SSS+ ratings from each supported
folder's median refined latent difficulty. D26 should center at 26.5, with
its spread determined by the calibrated evidence. Do not force its charts
into 26.0-26.9, fill empty tenths, or replace model distances with rank spacing.
Keep the existing global extreme-rating policy separate from this calibration.

The initial replay put 34 of the 36 rated D26 charts in displayed 26.x
without a D26-specific clamp. The local implementation uses schema 53.
Policy version 2 separates final-scale support from first-pass model support:
five finite refined estimates can supply a provisional final anchor, including
valid boundary estimates. Policy version 3 stabilizes the lowest-anchor scale.
Versions 1 and 2 remain readable for historical replay.

Policy version 3 constrains the fitted exponential offset to at most
`C20 - (Cnext - C20)`. Below the lowest official anchor, extrapolation uses
the adjacent median increment `(Cnext - C20) / (Lnext - 20)` as its positive
cost-per-level slope. Interior segments remain exponential and the upper
tail remains tangent-linear. These rules apply independently to official
Singles and Doubles; original model fits and submitted below-20 values stay
unchanged. Residual median centering, single midpoint compression, existing
tail limits, interval projection and chart-by-chart Pumbility averaging follow.
The current-data preview reduces D20 scores below D19.0 from 68 to seven;
that count is an output, not a quota or an eligibility rule.

## Why the current scale is distorted

The official Doubles first-pass calibration has reference anchors through
D25.5. D26 lacks enough qualifying two-sided reference charts for its own
first-pass anchor. Its boundary estimates therefore extend the D25 tangent
using score shortfalls, despite the large increase in target difficulty.

| D26 chart | Best-three mean | Shortfall to 995000 | Boundary estimate | After centering | Published rating |
|---|---:|---:|---:|---:|---:|
| Vacuum Cleaner | 991175 | 3825 | 28.7106 | 24.9569 | 24.9611 |
| Telling Fortune Flower | 988732 | 6268 | 29.8681 | 26.1144 | 26.1144 |

Both charts use the same zero-target branch. Their 2443-point score-gap
difference becomes 1.1575 difficulty points before the final tail policy.
The current D26 pre-centering median is 30.2537, so a -3.7537 translation
centers it at 26.5. This fixes location while retaining the scale inherited
from the shallower D25 tangent. The tail guard changes the pair's gap only
to 1.1533; display truncation then shows 24.9 and 26.1.

The appropriate next step is a calibration that can learn the larger latent
cost increment at D26. A translation alone cannot do that. A rank template
would change the meaning of the interior spacing, and a small boundary cap
would concentrate most of the zero-target charts near one value.

## Proposed two-stage calibration

### Freeze the target fit and refined latent evidence

Reuse the four completed 995000 target fits, 200 bootstrap samples per cohort,
source-removal diagnostics, original reference curves and held-out score-gap
calibration. General Scoring, Clearing and General Pumbility remain unchanged.

Freeze the current pre-centering second-pass population, including ordinary,
rare-success and one-sided estimates. Retain the current no-target best-three
policy, all-target lowest-returned-score policy and rare-success correction.
Those stages produce preliminary coordinates; they do not set the final
numeric scale in the new generation.

For every finite official 20+ chart, obtain its refined latent coordinate
before the folder shift, midpoint compression and final tail map. The current
payload can recover it from the full-precision pre-centering initial fit:

    latent = preliminaryCurve(preCenteringInitialDifficulty)

For boundary estimates, verify this equals the stored `targetLatent`. For
ordinary first-pass charts it equals the fitted chart effect. Rare-success
charts use the latent coordinate corresponding to their corrected point,
not the unblended conditional fit. Store this coordinate explicitly in the
new contract; never recover it from displayed or truncated ratings.

### Build final median anchors from supported complete folders

An official folder can supply a provisional final calibration point when it
has at least five finite refined chart estimates with rated evidence. Valid
boundary and rare-success estimates count. There is no mixed first-pass count
or first-pass width requirement for this final scale. Original target-model
and first-pass reference eligibility remain unchanged. Record two-sided support
as diagnostics rather than as a second eligibility gate. S25 has 13 finite
estimates, and D27 has ten, so both qualify under policy version 2.

For each qualifying source/mode/level, give each finite chart one vote and
compute the median refined latent coordinate. Pair it with official level
+ 0.5. Apply the same population definition to every supported level, rather
than adding a special anchor only for D26. Unrated charts stay excluded.

These are derived full-folder median anchors. They do not satisfy or replace
the stronger first-pass reference-chart eligibility rules. Label their
provenance and record mixed/one-sided/rare populations, source truncation,
small-board counts and all contributing chart IDs. A boundary-dominated
anchor remains provisional evidence.

Fit independent final curves for official Singles and Doubles using the
existing monotone, piecewise exponential calibration machinery. Keep at least
three increasing anchors including level 20 and the existing failure policy
for unsupported/non-increasing calibration. Submitted below-20 variants keep
their current calibration and points.

The current official Doubles derived medians include:

| Folder midpoint | Median refined latent cost |
|---|---:|
| D24.5 | 5.0612 |
| D25.5 | 5.9384 |
| D26.5 | 10.8878 |

The larger D25-to-D26 increment now informs the numeric scale. A given latent
difference near D26 maps to a smaller difference in rating units, without
specifying a desired percentage of D26 charts inside its official folder.

### Project once, then apply the existing postprocessing

For each official chart, project the frozen refined latent coordinate through
the final mode curve:

    recalibratedInitial = inverseFinalCurve(refinedLatent)
    compressed = midpoint + 0.5 * (recalibratedInitial - midpoint)

Recompute only a residual location correction to keep supported folders'
final median exactly at level + 0.5. This is needed because inverse mapping
is nonlinear: for an even population, the mean of its two mapped middle
values need not equal the inverse of their mean latent value. Implement the
residual as twice its final size before the single 50% compression.

Discard the old coordinate's centering offsets, including D26's -3.7537.
Sparse folders continue to borrow residual offsets within the same source
and mode; do not manufacture anchors or force their medians. Points beyond
the final supported curve retain explicit extrapolation diagnostics.

Apply the existing global tail/exception policy after the new calibration
and residual centering. Recompute SSS+ ranks, bands and Pumbility from the
final Scoring value and unchanged Clearing value. Do not run another midpoint
compression or independently recenter Pumbility.

Freeze all latent coordinates and anchors in one pass. Do not feed final
ratings back into player abilities, original reference selection, boundary
conversions or rare-success allocation, and do not iteratively recalibrate.

## Current-data preview

The analysis-only replay uses complete supported-folder medians throughout
both official modes. It preserves within-folder ordering and targets a 26.5
median without imposing a D26 rating boundary.

| D26 result | Current | Median-calibration preview |
|---|---:|---:|
| Charts displayed as 26.x | 9 / 36 | 34 / 36 |
| Median | 26.5000 | 26.5000 |
| Minimum | 24.6555 | 25.9989 |
| Maximum | 28.3680 | 27.0466 |
| Vacuum Cleaner | 24.9611 | 26.3458 |
| Telling Fortune Flower | 26.1144 | 26.4645 |
| Gap between those charts | 1.1533 | 0.1187 |
| Largest adjacent gap | 1.1533 | 0.2012 |
| Residual median offset | -3.7537 | +0.0000031 |

Here, displayed 26.x means an unrounded point in [26.0, 27.0), consistent
with the current one-decimal truncation. Thirty-three charts are literally
within [26.0, 26.9]. The endpoints remain natural outliers; do not snap the
minimum just below 26 to a display boundary or clamp the maximum below 27.

The derived D26 anchor uses many zero-target estimates. Its scale depends
on the existing score-gap model, not independent measured SSS+ successes.
The preview supports this calibration approach, but does not establish its
statistical accuracy. Keep those estimates provisional, expose the anchor's
composition and perform a source/anchor sensitivity check before activation.
Leaving out any single D26 chart moves its median latent cost by at most
0.0409, from 10.8878 into 10.8469-10.9287. This checks single-chart leverage,
not source-selection or score-gap-model uncertainty.

Private evidence is saved in
`.local-data/sssplus-experiment/d26-continuity-audit.json` and
`.local-data/sssplus-experiment/median-recalibration-preview.json`.

## Data contract and interface

Allocate the next unused schema; 53 is expected after the active schema 52.
Create a separate local candidate and keep exact schema 52 recovery for
rollback and historical validation. Version the final-calibration policy and
store the frozen preliminary curve, refined latent coordinates, derived
anchors/populations, final curves, residual offsets and stage order.

Keep original first-pass/refinement/centering/tail diagnostics available as
preliminary historical coordinates. The new initial-fit field must identify
the recalibrated coordinate before the one 50% compression, with the original
initial fit retained separately. Display final-calibration explanations in
chart details instead of presenting the historical -3.7537 offset as current.

Reproject available latent bootstrap interval endpoints through the final
curve, residual offset and tail map with those calibrations fixed. Boundary
and rare-corrected points continue to have no formal interval. Adding a
provisional median anchor does not create new statistical precision or
promote a chart's evidence status. Preserve preliminary and final
extrapolation/anchor-support diagnostics separately; a derived anchor cannot
by itself qualify a chart for a strong-evidence tail exception.

Extend schema dispatch, types and target selection to recognize the new
generation as SSS+ at 995000. Preserve General/SSS+ controls, canonical links,
Clearing's hidden selector and existing filters. SSS+ Pumbility continues to
use the full-precision mean of its final Scoring and unchanged Clearing
components, including the existing conditional interval interpretation.

## Implementation and acceptance

1. Freeze active schema 52 and pinned inputs. Add a pure final-calibration
   module and local overlay builder. Recover the existing refined latent
   population and reuse all fitted target/gap/bootstrap artifacts. No new
   data fetch or target-model refit is required.
2. Build consistent full-folder median anchors, final curves and residual
   centering. Apply compression once, the unchanged global tail policy, then
   derived ranks and SSS+ Pumbility. Add exact recovery and strict validators.
3. Test latent recovery across all branches, equal chart weighting, odd/even
   medians, positive-width support, source/mode isolation, monotone curves,
   unavailable/non-increasing anchors, sparse borrowing and interval replay.
4. Pin the current-data preview: D26 median 26.5, 34 of 36 displayed as 26.x,
   the named pair's gap below 0.2, ordering preserved, and genuine points
   outside the official folder permitted. These are regression expectations
   for the frozen input, not percentage targets enforced on future data.
5. Check existing per-chart source-removal effect diagnostics and compare
   leave-one-chart-out derived D26 anchors. Separately perturb boundary
   score inputs using the pinned individual-source boards with player fits
   and score-gap conversion fixed; label this a conditional diagnostic and
   report branch/support changes. Record effects on median latent cost,
   curve slope, the named pair and folder spread. Reuse original
   player-cluster refits where applicable. Do not invent confidence intervals
   for the boundary-derived anchor or retry policy settings to meet 34/36.
6. Verify exact preservation of General, Clearing, General Pumbility, Co-op,
   submitted below-20 target values and pinned inputs. Verify the final
   Pumbility mean, once-only compression, supported medians and existing
   exact/displayed extreme-rating budget. Report all changed official folders.
7. Run affected Python/frontend tests, typecheck, build and local browser
   checks. Perform one general review, repair proven regressions, rerun only
   affected checks and stop. Activate locally only after the complete
   candidate and sensitivity results validate.
