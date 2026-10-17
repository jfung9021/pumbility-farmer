# SSS boundary estimates and official Doubles Clearing plan

October 18, 2026. Local implementation proposal.

Top-three endpoint update (October 18): for a nonempty board with no SSS,
sort the deduplicated player best scores descending and average the first
min(3, record count). Use 990,000 minus this full-precision mean as the
shortfall. Keep the selected scores and their count in the public boundary
diagnostics. Version this endpoint policy, retain reading of the original
single-best policy, update validators/details, and rebuild the local candidate
from the same frozen first pass. Verify one/two/three-score boards, tied
scores, unchanged other branches and Clearing, and recomputed SSS Pumbility.

Preserve the range produced by the current rated SSS charts in a first pass.
Use a second pass to extend that range for charts entirely below or entirely at
SSS, and to move well-supported rare-SSS charts toward its upper edge. Restore
the official clearer-ability calculation for Doubles at current official level
26 and above. General Scoring retains its current fit; both Pumbility variants
use the resulting Clearing component.

## SSS score selection and cases

Use the existing normalized successful best scores and deterministic cross-mix
selection. Official 20+ boards retain each matched player's higher Phoenix 1 or
Phoenix 2 score. Submitted scores remain the source for levels 16–19.

Classify a chart using its complete validated, deduplicated available board,
before the four-chart player-history filter used by the logistic model. That
filter must not hide an actual SSS score or manufacture an all-SSS board.

| Available scores | Estimator | Score used |
|---|---|---|
| All below 990,000 | Extend beyond the first-pass upper edge | Mean of the top three recorded best scores, or all available if fewer than three |
| All at least 990,000 | Extend below the first-pass lower edge | Lowest recorded best score |
| Both outcomes with rare SSS | Correct toward the first-pass upper range | Shared-player evidence and rare-success count |
| Other mixed outcomes | First-pass player and chart estimate | Eligible shared-player observations |
| No valid scores | Unrated | None |

The threshold is inclusive: 990,000 reaches SSS. A nonempty one-sided board can
receive a boundary estimate even when it lacks five contributors; its support
count and sparse-evidence flag remain visible. A mixed full board whose usable
player histories contain only one outcome retains an explicit history-support
reason rather than being relabeled as a genuinely one-sided board.

For a capped board, “last place” means the lowest score returned by the API.
For example, The Stranger D22 currently has 300 returned scores with a lowest
score of 990,551. Its lower-boundary input would be a surplus of 551 points.
That does not establish the lowest score among every person who has cleared it.

## First pass range

Run the current SSS model and its existing median-anchor calibration and 50%
midpoint compression. Construct each range from its finite, rated results,
including Provisional results; exclude Unrated charts. Group by source cohort,
mode and current official level. Use the actual full-precision minimum A and
maximum B, preserving the current range rather than substituting fixed folder
endpoints or trimming its extremes.

Freeze the range, its contributing chart IDs, original chart estimates and
calibration before the second pass. Keep rare-SSS charts that currently have a
finite estimate in this first-pass population. Their second-pass corrections
must not feed back into the range calculation. The added charts must not refit
the median anchors, shift the original range or trigger iterative reruns.

For example, the current 69 rated D25 charts span approximately 25.069772 to
26.016042. The 137 rated D22 charts span approximately 19.483404 to 23.873193.
These measured edges replace the previous proposed fixed L and L+1 edges.

Require at least five rated charts and a positive range width for a supported
folder range. D27 currently has only one rated chart; D28/D29 and S26 have none.
For these folders, interpolate the lower/upper offsets from L+0.5 between
supported folders in the same mode and source. Outside that supported span,
borrow the nearest supported folder's offsets and translate them to the target
midpoint. Record the donor and label the range Provisional. Never borrow across
Singles/Doubles or submitted/official cohorts. With no supported donor or no
valid source calibration, retain an explicit Unrated reason.

## Second pass for one sided charts

Define T = 990,000 and M = L + 0.5. The two nonnegative gaps are:

    no SSS:  g = T - mean(top min(3, record count) scores)
    all SSS: g = lowestScore - T

Fit a positive score-points-per-latent-unit conversion k separately for each
source and mode. Use well-supported mixed charts and their fitted player/chart
effects. Fit a robust positive slope between the score deficit T - score and
the latent difference chartEffect - playerAbility, initially using scores from
980,000 through 999,999. Perfect scores are excluded from this scale fit because
their observed values are capped. Select and assess the fitting settings on
held-out charts, rather than using the eventual one-sided charts as calibration
targets. Record the scale, support, training range and fitting policy.

This scale is a proposed calibration to validate during implementation. Score
gaps do not determine difficulty units on their own. If the scale or the source's
existing SSS median-anchor curve is unsupported, retain an explicit Unrated
calibration reason. The existing submitted Doubles calibration failure therefore
still prevents ratings for D16–19 until that independent issue is addressed.

Let C map initial difficulty to the latent coordinate of the frozen SSS
calibration curve. Reuse its exponential interior and labeled linear tails.
The frozen edges A and B are final, compressed values. Recover their
pre-compression coordinates before extending them:

    lowerInitial = M + 2 * (A - M)
    upperInitial = M + 2 * (B - M)
    no SSS:  initial = inverseC(C(upperInitial) + g / k)
    all SSS: initial = inverseC(C(lowerInitial) - g / k)
    final = M + 0.5 * (initial - M)

At a zero gap the estimate equals the measured edge B or A. A positive shortfall
extends beyond B; a positive surplus extends below A. Adding or subtracting the
gap in the latent coordinate makes difficulty spacing follow the calibrated
curve. The resulting raw estimate receives midpoint compression once; the
already-compressed edge is not compressed a second time.

Mark every boundary estimate Provisional. Show the endpoint score, point gap,
record count, returned-board cap and extrapolation status in chart details.
These are estimates from score boundaries; they do not inherit the logistic
model's bootstrap intervals. Leave unsupported point/interval fields distinct
instead of presenting a regularized fit interval as uncertainty for this rule.

## Second pass for rare SSS results

Treat one SSS among many observed records as an upper-range signal. Apply the
strongest correction when there is one SSS among at least 20 usable players;
reduce it for fewer records, higher success rates and two to four successes.
One SSS among two records does not receive the same correction. A capped board
remains conditional on its returned population, and every corrected estimate is
Provisional.

The Passacaglia diagnosis reproduces its current fit exactly: one SSS among 102
eligible players, yet 12.58 predicted SSS outcomes. Its fixed chart penalty
strongly suppresses difficulty after each player's weight is divided across
their history. A chart-only refit without that penalty, holding player abilities
and calibration fixed, gives a diagnostic final estimate of 26.531348. That
diagnostic supports an upper-range correction; it is not a newly calibrated
unrestricted tier rating.

For qualifying mixed charts, keep the first-pass player abilities and solve for
the target chart effect alone, removing its chart penalty. Use the same player
weights and observed binary outcomes:

    sum(weight * sigmoid(playerAbility - chartEffect))
        = sum(weight * reachedSSS)

Both outcomes are required for this finite conditional solve. The target chart
must be in the calibrated comparison component. Preserve the first-pass result
when usable comparison evidence is insufficient. This second pass does not
change other player abilities, chart effects, reference medians or range edges.

Convert the conditional effect through the frozen calibration and existing
midpoint adjustment to obtain R. Define the uppermost band as the highest 5% of
the numeric first-pass range, not the 95th percentile of chart ranks:

    F = B - 0.05 * (B - A)

For D25, this band is approximately 25.968728 to 26.016042. A numerical pilot
covered 35 currently rated Doubles charts with one to four SSS outcomes in
supported folders. Simply clipping R into this band stacked every one-SSS chart
in that pilot at its folder's maximum. Preserve their conditional ordering by
allocating positions within the upper band instead of hard-clipping every
large R to the same value.

Use a decreasing blend for one through four SSS outcomes. A proposed starting
policy is:

    countWeight = clip((5 - successes) / 4, 0, 1)
    supportWeight = clip((usablePlayers - 5) / 15, 0, 1)
    rateWeight = clip((0.10 - successes / usablePlayers) / 0.05, 0, 1)
    w = countWeight * supportWeight * rateWeight

Within each source/mode/level, rank the finite conditional estimates of candidates
with w greater than zero. Use mean ranks for exact ties. With m candidates:

    position = (conditionalRank - 1) / (m - 1)  if m > 1, else 1
    upperTarget = F + position * (B - F)
    corrected = original + w * (max(original, upperTarget) - original)

Thus one SSS among 20+ usable records moves into the uppermost band; larger rare
success counts receive progressively less correction. Five or more successes
retain the first-pass estimate in this version. Unbounded conditional R remains
visible in diagnostics; it supplies relative ordering within this explicitly
bounded range policy. The thresholds, band width, ordering policy and blend are
proposals to validate on the current rare-SSS charts and synthetic transitions,
rather than statistical claims about attempt rates.

On the current D25 data, this proposed policy gives approximately 26.010785 for
Passacaglia and 26.000271 for Legendary Dominion. Both display D26.0 while keeping
their full-precision order. These are plan diagnostics, not changes to the local
published tier list, and the frozen D25 upper edge stays 26.016042.

Score selection uses the full board, while the conditional refit uses eligible
shared-player records. Record both counts and require them to agree on the
rare-success case before applying this correction. A missing-history SSS player
must not be counted as a modeled failure or converted into the no-SSS branch.

Store the first-pass and conditional values separately. Translate the chosen
final target back through the inverse midpoint adjustment to produce an
auditable initial-fit coordinate, then use the standard compression once for
the published result. Do not run a second compression over the corrected final
value. Recompute ranks after all second-pass estimates are complete.

The old bootstrap interval does not describe a corrected point. With only one
SSS player, some player-resampled datasets have no SSS at all. Leave a formal
corrected interval unavailable in this version and explain the sparse evidence
instead of retaining a misleading narrow first-pass interval. Report the
zero-to-one-SSS and four-to-five-SSS transitions before selecting the candidate.

## Official Clearing for Doubles at level 26 and above

The source assumption is Phoenix 2 official boards, matching the old method.
Use the latest pinned Phoenix 2 capture already available locally, including
D23+ history boards. Recalculate from that capture rather than restoring the
older saved September 27 ratings. A combined-source choice would require an
explicit variant of the history, identity and board-cap policy.

Apply the source switch by current official mode and level: Double and level
at least 26. Singles and Doubles through level 25 retain their current Clearing
methods, including the submitted top-30/200-record player-skill calculation.

Extract the Clearing calculation from `official_tiers.py` into a Clearing-only
overlay. Calling the existing full overlay would also replace Scoring and its
Pumbility policy.

For every player appearing on the target's official board:

1. Collect unique official D23+ clears, excluding the target chart.
2. Require at least 25 other charts. Average the 25 highest current official
   level midpoints, L+0.5, to obtain the player's official clearer ability.
3. Take the linearly interpolated 20th percentile of those eligible abilities
   as the chart's Clearing threshold. Missing leaderboard appearances remain
   unknown.

Restore the legacy evidence adjustment and spread calculation. With n eligible
clearers and N observed unique clearers:

    evidenceWeight = n / (n + 20) * n / N
    rawDelta = 0.70 * evidenceWeight * (chartThreshold - folderMedianThreshold)

Reuse the robust folder-spread helper and its neighboring supported-folder
fallback. Preserve the old capped-board rule: capped charts receive the lowest
uncapped estimate in their folder, or its midpoint if no uncapped estimate
exists, with the corresponding weaker evidence. Store observed and eligible
counts separately from the cap marker.

After spread calibration and capped-board assignment, recenter the finite chart
estimates in each folder so their actual median is L+0.5. Apply the existing
50% midpoint compression once afterward. This explicitly guarantees a median
of D26.5 for measured D26 charts, D27.5 for measured D27 charts, and so on.
Sparse folders retain the legacy weaker evidence and borrowed-scale metadata.
Uncapped charts without a usable clearer ability remain Unrated; capped charts
use the explicit legacy default with weaker evidence. Restore the legacy
1,000-sample conditional clearer bootstrap and state its fixed-history and
fixed-calibration assumptions.

## Data contracts and interface

Allocate the next unused schema version; 50 is currently available. Preserve
schema-49 reading and keep the existing candidate available for rollback.

Give SSS estimates distinct first-pass, boundary and rare-success method
identities and explicit branch fields. Store each frozen range, actual or donor
population, source/mode/level, first-pass values, endpoint score, gap, conversion
scale, conditional refit, upper band, blend weight, initial fit and final result.
Keep full-board and model-eligible support counts separate. Fit-only metadata
must not describe a score-gap estimate, and first-pass intervals must not be
reused for a corrected point.

Give official D26+ Clearing its own versioned row method and evidence metadata
inside the Clearing component. SSS projection must preserve that provenance.
Chart details and list explanations must identify official clearer ability
instead of submitted top-30/200 skill for those charts.

Recompute both full-precision averages and their independent ranks:

    General Pumbility = (General Scoring + selected Clearing) / 2
    SSS Pumbility = (SSS Scoring + selected Clearing) / 2

Retain the General/SSS selector and SSS default. Composite evidence follows its
weaker component, with separate support counts. Boundary and rare-corrected
estimates have no formal SSS interval in this version; their Pumbility variants
must not fabricate one.

## Implementation and verification

1. Freeze schema 49, source captures and the current General, SSS and Clearing
   outputs. Audit full-board outcomes and endpoint scores separately from
   fit-eligible counts. Save the finite first-pass range of each folder before
   adding or correcting any chart.
2. Implement range construction, sparse-folder borrowing, boundary gap
   calibration and the conditional rare-success refit in separate helpers.
   Integrate the two passes into `sss_scoring.py` and the local builder. Reuse
   completed first-pass fits when inputs and policy match. Recover player
   abilities with one deterministic point fit if the old cache lacks them;
   repeating the 200 first-pass bootstrap refits is unnecessary.
3. Implement the Clearing-only official overlay, reuse official history and
   percentile/spread helpers, and recenter the D26+ folders.
4. Extend schema, Python/frontend validators, selected-variant projection,
   provenance labels and Pumbility recomputation. Retain historical readers.
5. Build a separate local candidate and report frozen/extended ranges, coverage,
   donor folders, score-gap examples, every rare-success correction, outcome
   transitions, D26+ median checks and before/after ratings. General Scoring,
   first-pass SSS point estimates and intervals outside the explicit correction
   rules, unrelated Clearing charts, Co-op, recommendation and personal artifacts
   must remain identical to the frozen inputs. Ranks are recomputed. Second-pass
   results must never change their own reference edges or refit the first-pass
   calibration.
6. Test inclusive 990,000 boundaries, tied scores, cross-version deduplication,
   full-board versus fit eligibility, no-data boards, sparse records, capped
   last-place scores, frozen-range provenance, gap monotonicity, unsupported
   calibration, donor offsets and once-only compression. Use The Stranger D22,
   zero-SSS S26/D26+ charts, Passacaglia and Legendary Dominion as real-data
   examples. Test one success among 2 versus 102 players, conditional likelihood
   agreement, the numeric upper band, one/four/five-success transitions, stable
   ties, unchanged normal fits and removal of obsolete intervals. Test official
   target exclusion, top-25 eligibility, q20, cap rules, exact folder medians,
   both Pumbility variants and D25/D26 dispatch.
7. Run affected Python/frontend tests, TypeScript, a build and browser checks.
   Perform one general review, repair proven regressions, rerun affected checks
   and stop. This phase produces a local preview; publishing is a later release.
