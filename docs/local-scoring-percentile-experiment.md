# Local score-profile experiment with level-specific spreads

Updated September 26, 2026 with five score percentiles, double q90 weight,
exact folder centering, and adaptive folder scales. This is a local,
tier model. The approved model is now connected to production workers under
schema 26; the explicit local diagnostic command retains schema 25. Player scoring
skill and recommendation projections remain separate from the profile tier model.

## Observations and profiles

Use the current Phoenix 2 Singles/Doubles catalog, published official levels 16+.
For each player/chart, retain the best valid successful score within each source,
normalize Phoenix 1 by current note count, and use Phoenix 2 for overlapping records.
All retained players have equal weight, including zero-Pumbility clears. There are
no skill adjustments, history minimums, or contribution windows. Scores are cached
best records, not complete attempt histories.

Each chart's profile is `[q10, q25, q50, q75, q90]`, using linear percentile interpolation.
Matching minimizes the weighted mean squared difference in score points with
weights `[1, 1, 1, 1, 2]`. The objective is
`(error10^2 + error25^2 + error50^2 + error75^2 + 2 * error90^2) / 6`.
The 90th-percentile squared error contributes twice as much as each other term;
players still have equal weight. Scores are divided by 10,000 internally for numerical
convenience; this common factor does not change the match.

## Raw profile matching

Fit a separate curve for Singles and Doubles:

1. Reference charts need at least 20 observed successful players. A reference folder
   needs at least five such charts. Take the componentwise median of its profiles.
2. Place reference observations at official level + 0.5 on an integer-spaced grid
   between the lowest and highest supported levels. Missing interior folders have
   zero observation weight. A supported folder has weight `min(chart_count / 20, 1)`.
3. In score-loss coordinates `(1,000,000 - score) / 10,000`, fit each percentile by
   minimizing `sum(weight * (fit - reference)^2) + 4 * sum(second_difference(fit)^2)`.
   The initial smoothing strength is 4, a modeling choice, not a fitted outlier cap.
4. Project each fitted percentile to nondecreasing loss by equal-weight isotonic
   regression. Order the three fitted losses at each knot and bound them to 0..100.
   These operations preserve monotone level order and valid percentile order.
5. Connect the fitted profiles linearly. Find the closest point to each chart's
   profile across every segment using the percentile weights above, with equal-distance solutions mapped to the midpoint
   of their difficulty range. Extend the first and last nonflat boundary segments
   linearly outside the reference range. A flat boundary segment has no extension.

These matches are intermediate scoring signals. They are calibrated below before
being published as proposed difficulties. Official chart membership never changes.

At least two supported reference levels and a nonconstant fitted curve are needed.
Otherwise that mode's scoring estimates are unavailable. Sparse charts still get
estimates if their mode has a curve; their evidence status reflects player support.
Curves and raw references are exported in `summary.method.scoreProfileCalibration`.

## Final folder centering and adaptive spread calibration

For each official Singles/Doubles level folder, take the median raw match across
all rated charts, including limited-data charts. Even-sized folders use the mean
of their two central matches. The final formula is:

```text
proposal = official level + 0.5
         + scale[mode, level] * (raw match - folder median raw match)
```

Every rated folder median is level + 0.5 (within serialization precision). Matching
a neighboring folder's typical raw profile does not force its midpoint rating.
Positive scales preserve order/ties inside a folder; zero collapses to its midpoint.
No charts are reassigned, and no shared final multiplier is applied afterwards.

Scales are selected from 0.00, 0.01, ..., 1.00. The spread signal is q90 minus q10
of chart offsets within each folder. These are across-chart percentiles, distinct
from each chart's q10/q25/q50/q75/q90 player-score profile.

First compute the old shared scale (at most three two-grade moves) as a baseline
reference, not a constraint on the adaptive solution. Supported nonflat folders
have preferred central-80% width `max(1.00, baseline_scale * raw_width)`.
Support requires at least ten rated charts and median contributors >= 10. Their
width weight is `min(chart_count/30, 1) * min(median_contributors/20, 1)`.
Unsupported or flat folders have zero spread-fit weight and borrow through neighbor
smoothing. A mode with no supported folders uses a preference for the baseline scale.

The joint objective is:

```text
sum(weight * ((scale * raw_width - preferred_width) / preferred_width)^2)
+ 0.05 * sum((log(scale + 0.01) - log(previous_scale + 0.01))^2 / level_gap)
+ 0.05 * max(0, total_two_grade_moves - 10)
```

Neighbors are in the same mode. A dynamic program considers all candidate scales
with count buckets 0..10; extra moves pay a penalty and remain feasible. The two
mode solutions share one soft ten-move allowance. Ties prefer lower objective,
fewer actual outliers, then the smaller scale vector in deterministic mode/level
order. No optimizer dependency is added.

The 1.00 target and both 0.05 coefficients are calibration choices. The width
target was reduced from 1.10 after the initial adaptive run. Three
to ten moves is a diagnostic guideline, not a minimum or hard ceiling. A move is
`proposal >= level + 2` or `proposal < level - 1` after the same six-decimal export
used before UI truncation; S21 -> 23.0 or 19.9 counts, while 20.0 does not. All
rated S/D charts at official level 16+ count, including limited-data charts.

Per-folder scales, support, preferred/achieved widths, objective components, and
actual move counts are exported under `summary.method.scoreProfileCalibration`.
Each rated chart carries `scoringDifficultyScale`; there is no single final scale.
The formula still leaves Pumbility as the unrounded average with Clearing. Neither
Pumbility endpoints nor a named chart's rating is fitted to a predetermined value.

## Uncertainty and UI

Charts with 20+ players get a deterministic 1,000-resample bootstrap interval. Each
resample computes all five percentiles jointly and maps the resulting profile with
the same double q90 weight to
the raw matching signal, then applies the same final affine calibration to both
interval endpoints. This preserves dependence between percentiles. The reference
curve, folder center, and selected folder scale are held fixed; intervals exclude their
estimation uncertainty, player selection, and missing attempts.

Details show the five percentiles, double q90 weight, successful-player count, weighted root mean squared
profile-match error in score points, raw profile match, folder center, applied scale, and whether
the raw match is outside the reference range. Large match error means the chart does not closely resemble the
reference profile, even when its sampling interval is narrow. Raw extrapolations
can be extreme; they are centered and scaled before becoming final proposals. The
official-level What-if control remains hidden because this experiment does not
implement hypothetical folder reassessment.

Pumbility uses the unrounded average of this scoring estimate and Clearing, whose
spread multiplier is now 0.70 (previously 0.65).
Tier-band ordering continues to use proposed difficulty minus (official level + 0.5).
Two-grade diagnostics count estimates >= official level + 2 or < official level - 1,
after the same six-decimal export used before UI truncation.

## Running and isolation

```powershell
.venv/Scripts/python.exe scripts/build_local_recommendations.py --scoring-profile
```

`--scoring-percentile` remains an alias. Only the local combined tier aggregate is
written. Schema 25 and `localExperiment: scoring-profile-level-scales` distinguish this from
schema 24 adaptive three-percentile profiles, schema 23 shared-scale centering,
schema 22 unrestricted profiles, and schema 21 q90. Production schema 26 uses the
same weighted profile calculation without the local experiment marker. Production
checkpoints reject the older methods. `--tiers-only` builds schema-26 profile tiers locally.
Neither command pulls upstream data, rebuilds recommendations, or changes player skills.

Production analysis and checkpoint recovery build profile tiers from both stored
snapshots before discarding raw inputs. The separate recommendation model still
receives the original chart estimates. Stored-snapshot reanalysis regenerates the
public tiers and current recommendation/clearing-skill artifacts without a data pull.

Verification and result artifacts are stored privately under
`.local-data/piu-scores/verification/scoring-profile-level-scales-2026-09-26/`.

## Current five-percentile run

The local model now uses q10/q25/q50/q75/q90 with weights 1/1/1/1/2. All 2,601
rated charts have five-coordinate profiles; the prior q25/q50/q75 values and
contributor counts are unchanged. All 24 scoring folder medians remain x.5.
The preferred scoring width stays 1.00, the achieved median central-80% width is
1.003621, and there are 10 two-grade scoring moves under the existing soft penalty.

D26 Scoring spans 25.566106-27.422248. Pumbility spans 26.081294-26.959724,
displayed 26.0-26.9. All Clearing and Co-op records are unchanged, as are the 101
cached source/recommendation file hashes. Pumbility was recomputed from the new
Scoring estimates and unchanged Clearing component. No upstream data was fetched
and production was not changed.

Verification passed: 139 Python tests, 67 frontend tests, Next build, independent
weighted profile-error/calibration/interval/median checks, and local API/page
checks. A calculation test checks that a q90-only score change contributes twice
as much as a same-sized q10-only change on a linear reference segment. Diagnostics
contain 130 target/folder comparisons and 501 raw extrapolations.

Baseline, rebuilt tiers, checks, and diagnostics are saved privately in
`.local-data/piu-scores/verification/weighted-five-percentiles-2026-09-26/`.

## Prior three-percentile tuned run (1.00 scoring width, 0.70 clearing scale)

The September 26 local tuning widens Clearing deviations by about 7.7% and
reduces the median scoring central-80% width from 1.097 to 1.002. The joint scoring
fit can still widen an individual folder; D16's scale changes from 0.11 to 0.12.
Scoring has 10 two-grade moves and Clearing has 15. Folder medians remain x.5.

D26 uses a 0.29 scoring scale. Its ranges are:

- Scoring: 25.541133-27.372411.
- Clearing: 26.158400-26.953600.
- Pumbility: 26.107366-26.934805 (displayed 26.1-26.9).

Verification passed: 132 relevant Python tests, 67 frontend tests, the Next build,
independent formula/median/interval checks, and local API/page smoke checks. All
101 saved source/recommendation file hashes, raw scoring evidence, and Co-op
records are unchanged. Seven Clearing rank positions changed only within groups
with identical exported estimates; this existing numerical tie behavior was left
unchanged. No new data was fetched and production was not changed.

The baseline, rebuilt tiers, and verification report are stored privately in
`.local-data/piu-scores/verification/spread-tuning-070-100-2026-09-26/`.

## Initial adaptive run and verification (1.10 scoring width, 0.65 clearing scale)

- 2,601 rated charts across 24 scoring folders; every folder median remains x.5.
- 10 two-grade scoring moves. The optimizer permits more when supported by its
  objective; there is no hard cap or forced minimum. Baseline shared scale was 0.12.
- D26 scale is 0.32. Scoring spans 25.441939-27.462660; Pumbility spans
  26.051570-26.980030, displayed 26.0-26.9. EMS D26 scoring is 27.462660 (27.4
  under the app's one-decimal truncation), and its Pumbility is 26.980030.
- Neighbor borrowing applies to unsupported high folders; for example D27 and D28
  use 0.32. These estimates retain their sample-support and extrapolation warnings.
- Independent checks verified all folder centers/scales, final proposals, interval
  conversions, Pumbility averages, move/display counts, ordering, and objective costs.
- Raw profiles, matching curves, raw estimates/intervals, and contributor counts match
  the saved baseline. All 2,606 Clearing records, 140 Co-op records, and 95
  recommendation file hashes are unchanged.
- 138 Python tests, 67 frontend tests, Next build, and local API/page smoke checks
  passed. One general review found no critical regression. Existing pandas,
  module-type, and line-ending notices were not changed. Production is unchanged.
- Aggregate diagnostics cover 130 bounded target/folder comparisons and preserve
  EMS/D27's six shared players, five lower scores, and median difference -13,188.5.
  This remains sparse observational evidence, not a forced rating target.

The private verification directory contains `verification.json`, `chart-comparison.csv`,
`tiers-before.json`, `tiers-after.json`, and `diagnostics/diagnostics.md` / `.json`.

## Local diagnostics

After rebuilding, run:

```powershell
.venv/Scripts/python.exe scripts/analyze_scoring_profile_calibration.py --baseline .local-data/piu-scores/verification/scoring-profile-level-scales-2026-09-26/tiers-before.json --output-dir .local-data/piu-scores/verification/scoring-profile-level-scales-2026-09-26/diagnostics
```

This writes aggregate JSON and Markdown reports, reading cached snapshots without
modifying tiers or recommendations. It covers all folder distributions and adjacent
folder profiles. Shared-player diagnostics use the easiest, upper-median, and
hardest raw matches in each folder, plus EMS D26, against neighboring levels. Each
target/reference-folder comparison counts a player once against their own median
reference-chart score, with a same-source subset reported separately. Up to three
reference chart pairs with greatest overlap and at least three shared players are
shown. Comparisons with fewer than ten shared players are marked sparse. Player
identifiers and individual score histories are not exported. These observations
do not feed back into model fitting.

## Historical shared-scale run (superseded by adaptive scales)

- Fitted shared scale: **0.12**. Exactly three two-grade moves; 0.13 would give four.
- All 24 rated official-folder medians are level + 0.5 within serialization precision.
- The three moves, using the app's one-decimal truncation, are Ercitite D16 -> 14.9,
  Ghost Bloody Train D17 -> 15.9, and Antique Serenade D20 -> 18.9.
- Coverage remains 1,285 rated Singles and 1,316 rated Doubles. Raw profiles,
  matching curves, raw estimates, raw intervals, and contributor counts are unchanged.
- Independent checks reconstructed every final proposal, interval calibration,
  within-folder order, folder median, two-grade/display boundary, and Pumbility average.
- All 2,606 Clearing records, 140 Co-op records, and 95 recommendation file hashes
  match the saved baseline. No upstream data was pulled and production was not changed.
- 128 Python tests, 67 frontend tests, and the Next build including TypeScript passed.
  The local API serves schema 23 with scale 0.12; all three tier pages return HTTP 200.
- One general review found no critical implementation regression. The TypeScript
  test-fixture typing failure found during build was corrected. Existing pandas,
  module-type, and line-ending notices were left unchanged.

Historical shared-scale evidence is in `scoring-profile-centered-2026-09-26/verification.json` and
`chart-comparison.csv` under the private verification directory. Historical outputs
below remain in `scoring-profile-2026-09-26/`.

## Historical unrestricted run (superseded by final calibration)

- Rated 1,285 Singles and 1,316 Doubles; 2,551 charts have bootstrap intervals.
- 1,176 two-grade moves: 484 Singles (219 higher / 265 lower), 692 Doubles
  (303 higher / 389 lower). These counts are measured, not constrained.
- Reference ranges: S16.5-S25.5 and D16.5-D26.5. There are 212 Singles and
  284 Doubles extrapolations. D16 estimates span 4.697 to 23.504: shallow reference
  slopes amplify score differences, especially when extrapolating. These are
  exploratory model outputs, not evidence that those official charts truly belong
  at such low levels. The result was preserved for inspection without retuning.
- The S23 folder median is 23.114, demonstrating that it is not forced to 23.5.
- Independent reconstruction checked every chart profile, closest-curve projection,
  match error, sample/source count, reference population, delta, and Pumbility average.
- All 2,606 Clearing records, 140 Co-op records, and 95 recommendation file hashes
  match the saved baseline. Player skills and recommendations are unchanged.
- 127 Python tests, 67 frontend tests, and the Next production build passed.
- The local API serves schema 22 and all three tier pages return HTTP 200.
- One general review found no critical implementation regression. Existing pandas,
  module-type, and line-ending notices were not changed. No production deployment.

Private evidence: `verification.json`, `chart-comparison.csv`, and before/after tier
artifacts in the verification directory above.
