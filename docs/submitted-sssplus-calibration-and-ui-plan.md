# Submitted SSS+ calibration and consistent tier UI

October 18, 2026. This follow-up extends the chart source exclusions and folder
spread plan. The UI changes, schema-57 calibration overlay and bounded weighting
comparison are implemented locally. The active local dataset restores all 529
D16–D19 SSS+ estimates. The approved production methodology and model fits remain
unchanged; the weighting comparison did not select a replacement for publication.

Rebuild the overlay with `npm run analyze:submitted-sssplus`. It reads the frozen
schema-56 artifact and cached submitted Double fit, writes
`.local-data/piu-scores/combined/experiments/submitted-sssplus-calibration/web_results.json`,
and makes no score fetches or model refits. The local selector is
`PIU_LOCAL_SCORING_VARIANT_EXPERIMENT=submitted-sssplus-calibration`.

The immediate analytical goal is to restore submitted SSS+ estimates for
D16–D19 while preserving the existing General, Clearing and official 20+
estimates. Player adjustment remains useful, but noisy fitted folder medians
must not determine the distance between nominal levels. Explanations belong in
closed disclosures, and the selected scoring goal must remain stable while
analysis data loads.

## Evidence from the current local data

All 529 D16–D19 charts have mixed SSS+ outcomes and at least 20 usable
contributors. Their submitted Double fit converged with 650 eligible players,
188,362 observations and 200 successful player bootstrap fits. Before the
overlay they were Unrated because the shared calibration rejects non-increasing reference medians,
including D17 to D18 and D23 to D24. This is a calibration failure rather than
missing records or a below-20 UI filter.

For 369 matched players with at least ten unique recorded charts in both D16
and D18, each player contributes equally to each folder comparison:

| Metric | D16 | D18 |
|---|---:|---:|
| Average player SSS+ fraction | 29.52% | 18.45% |
| Mean recorded best score | 979,090 | 973,888 |

The D18 minus D16 SSS+ difference is −11.08 percentage points. A 5,000-draw
paired player bootstrap gives a conditional 95% interval of −12.82 to −9.47
points. Phoenix 1 and Phoenix 2 panels separately show the same direction.
These compare selected observed charts and best scores, not equal attempt
budgets or a fully crossed player/chart panel.

The comparable-reference effect medians differ by only −0.001716 for D18 minus
D16. Using all finite chart effects reverses that tiny difference to +0.018556,
with D18 harder. The reference median is a different statistic from a matched
player's average attainment fraction; the apparent reversal does not establish
that D18 is intrinsically easier.

The current fit gives each player total observation weight one across all
Double 16+ history. A record from a four-chart player consequently has about
319 times the weight of one from a 1,274-chart player. Median observation
weight per chart is 1.03457 for D16 and 0.47588 for D18. The same absolute chart
penalty therefore has more influence on D18. Cached regularization sensitivity
fits change the ordering of folder effects. The selected penalty, 0.01, is the
smallest tested and has the best held-out loss among the existing candidates.

Practice effort remains a plausible additional influence. Stronger matched
players have broader recorded D18 coverage, but the cache has no attempt counts
or actual play dates. `recordedAt` is the PIUScores record-write time. It cannot
identify old neglected plays, recent practice or grinding time. Neither a shared
player effect nor an unrestricted player-by-folder effect can identify average
folder effort separately from chart difficulty without additional assumptions.

## Restore lower Double calibration first

1. Add a recoverable local overlay after schema 56 for submitted SSS+ Double
   levels 16–19. Reuse the source-corrected fit in
   `.local-data/configurable-tier-experiment/sssplus/submitted-double-fit.json`.
   Retain all valid submitted 16+ histories for player estimation. Restrict
   published lower-folder calibration and spread donors to D16–D19.
2. Center finite eligible chart effects within each folder. Let `mu[L]` be its
   eligible chart median. Use one shared robust residual scale
   `h = median over L of q90(abs(effect - mu[L]))`, with equal weight per folder.
   This avoids dividing by tiny or reversed adjacent-folder anchor gaps.
3. Preview the initial map
   `L + 0.5 + 0.8 * (effect - mu[L]) / h`. Apply the existing 50% midpoint
   adjustment exactly once, followed by the approved continuous spread, final
   width and tail transformations in their declared order. Preserve ties,
   within-folder ordering and supported final medians. Do not impose tier counts,
   normal quantiles or hard folder boundaries.
4. Record this as a declared rating-scale policy: a median D18 is assigned 18.5.
   This does not claim that observed best-score effects independently identify a
   two-level gap between D16 and D18. Keep effects and matched-player diagnostics
   available inside calculation disclosures.
5. Restore cached `effectLow` and `effectHigh` endpoints, which were lost from
   the public variant when calibration failed. Transform them with the same
   fixed map. Preserve conditional uncertainty limits and evidence status;
   chart counts alone must not promote provisional estimates.
6. Recompute affected SSS+ Pumbility as the full-precision average of final
   Scoring and Clearing, then recompute ranks. Whole-mode ranks may change when
   the 529 newly rated charts enter the population.

The generated local overlay has `h = 0.69558004`:

| Folder | Median | Active local range |
|---|---:|---:|
| D16 | 16.5 | 15.836–17.125 |
| D17 | 17.5 | 16.815–18.282 |
| D18 | 18.5 | 17.613–19.360 |
| D19 | 19.5 | 18.913–20.077 |

All 91 D16, 115 D17, 186 D18 and 137 D19 charts are now rated. The build verifies
that healthy Singles S16–S19, official 20+ target estimates, General, Clearing
and Co-op numeric components are unchanged. Strict official calibration checks remain intact.
Current lower charts need no zero-success, rare-success or all-success branch;
future such cases require explicit submitted-source support or remain Unrated.

## Evaluate player weighting separately

Keep the player/chart model as the comparison baseline. Compare its current
inverse-history weighting with a bounded alternative that balances each
player's contribution within comparable folders. Define qualification and
engagement cohorts using other charts or held-out records, rather than selecting
players because they reached SSS+ on the evaluated chart.

Evaluate a weaker regularization grid because the current winner is the
smallest tested penalty. Report effective weights, penalty sensitivity and
folder contrasts. Compare held-out log loss, Brier score, calibration and
within-folder rank stability. Use equal-player matched panels with minimum
coverage thresholds of 5, 10 and 20 charts in each folder, and separate-version
panels as sensitivity checks. A matched panel is a diagnostic, not a replacement
for the chart model or an effort-controlled ground truth.

Choose a new weighting rule only after this bounded comparison. Version its
formula, eligibility and penalty policy. Refitting a changed model requires new
player bootstrap fits. The immediate calibration repair can reuse existing
fits and does not depend on approving a new weighting rule. Do not infer an
effort correction from upload times or manufacture unobserved failures.

### Completed weighting comparison

`scripts/analyze_submitted_scoring_weights.py` implements both weighting rules,
training-only weights, deterministic held-out evaluation, matched-player/source
panels and aggregate diagnostics. All 12 permitted training/full fits converged.
The current rule at penalty 0.01 exactly reproduces the cached held-out metrics
and all full-fit chart effects, providing a check on the experiment.

| Weighting | Penalty | Held-out log loss | Brier score |
|---|---:|---:|---:|
| Current inverse history | 0.01 | 0.32394 | 0.09687 |
| Current inverse history | 0.003 | 0.27060 | 0.08107 |
| Current inverse history | 0.001 | 0.24569 | 0.07464 |
| Equal observed folders | 0.001 | 0.25373 | 0.07692 |

The current rule wins both ordinary and equal-player held-out metrics at every
tested penalty. Equal-folder weighting reduces median D18 chart weight further,
from 0.47588 to 0.31808, so it does not equalize per-chart information. Weaker
regularization improves prediction more than this proposed weighting change.
At 0.001, the reference effect medians for D16–D19 are ordered at 2.030, 2.576,
2.961 and 3.754, supporting shrinkage as a contributor to the previous inversion.

Retain the cached 0.01 fit in the active calibration overlay. Both experimental
weighting rules still select the smallest tested penalty, and weaker shrinkage
changes within-folder ranks materially. A later model change needs its own
versioned selection policy and new player bootstrap fits before adoption.
No effort correction follows from this comparison. Full aggregate diagnostics
are saved locally under `.local-data/submitted-sssplus-calibration/weighting/`.

## Keep scoring goals stable and explanations collapsed

The loading flip is caused by two frontend rules: an empty payload selects
SSS+, but the older production schema selects SSS, and URL parsing overrides an
explicit target with that payload-derived choice.

The selected URL goal now owns its label and target. Default to SSS+ independently
of schema or loading state, preserve explicit `general`, `sss` and `sssplus`
URLs, and retain unrelated query parameters. Determine goal availability from
actual stored variants. Missing target variants stay Unrated instead of being
shown as General scores under an SSS or SSS+ label. Clearing and Co-op remain
independent of this target projection.

Use one shared native `details`/`summary` disclosure, closed by default, with
keyboard interaction, visible focus and consistent mobile spacing. Apply it to:

- Tier-list overview and calculation methodology at the bottom of the list,
  chart calculation/evidence and tier-band sorting explanations.
- Recommendation skill explanations and username/privacy guidance.
- Homepage feature descriptions, sync instructions and data explanations.

Keep page titles, navigation, filters, scores, ranks, chart metadata and concise
loading, error, readiness and availability statuses visible. Keep consequential
admin refresh instructions with their controls. Do not reintroduce warning
icons or general warning banners. No data analysis is needed for these UI edits.
The optional "About player scores" section is removed entirely.

## Verification and release boundaries

For the UI, verify stable labels before and after legacy/new payload loading,
explicit URL choices and browser navigation. Check Scoring and Pumbility in
both modes, plus unchanged Clearing/Co-op projection. Confirm explanatory copy
is hidden initially and opens using the keyboard, on desktop and a narrow
viewport. Run focused frontend regression tests, type checking and a build.

For the calibration overlay, verify all 529 eligible lower Double charts
receive finite ratings, supported folder medians equal level + 0.5, intervals
remain ordered, ties and chart order are preserved, midpoint compression runs
once, shared scale is finite and positive, and final Pumbility equals its
components' average. Compare protected numeric components to the frozen input;
permit affected global ranks to update. Read the generated local artifact and
verify the local tier page before proposing publication.

Completed checks: 215 frontend tests, 36 relevant Python tests, type checking
and the Next build passed. Independent frontend replay accepted the complete
generated schema-57 artifact, including its strict schema-56 predecessor. The
build restored all 529 charts, checked the four exact Scoring medians and
verified protected numeric components and full-precision Pumbility means.
The calibration build took 40.08 seconds with zero score fetches and zero model
refits; the separate weighting diagnostic took 32.56 seconds with no bootstrap
model refits or changes to active fits.

The earlier production release's code is merged and deployed, but its source-
aware data publication has returned HTTP 500 and lost client connections. All
eight target fits were cached; the last verified public artifact remains schema
48. Resolve and verify that publication separately. UI fixes must handle older
data accurately while publication is pending. This follow-up does not authorize
an extra full score pull or silently include new calibration experiments in the
approved production generation.
