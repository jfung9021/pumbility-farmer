# Level-specific scoring spread: implementation plan

Status: implemented and verified locally on September 26, 2026. Production unchanged.

Subsequent tuning: the current scoring width target is 1.00 and the Clearing
spread multiplier is 0.70. Scoring now uses q10/q25/q50/q75/q90 with double q90
weight. The initial plan and results below retain their original three-percentile
profile and 1.10 / 0.65 settings; see `local-scoring-percentile-experiment.md` for current behavior.

## Goal

Allow scoring spreads to remain useful as official difficulty rises, without having
large deviations in a few lower folders compress every higher folder. Assess the
result against observed cross-level scoring comparisons, including Extreme Music
School 2nd period feat. Nanahira D26 versus D27 charts. Do not tune that chart to a
predetermined rating or force the Pumbility list into an exact histogram.

This is a local experiment using the existing cached Phoenix 1 and Phoenix 2 data.
Do not fetch scores, rebuild player recommendations, or deploy it as part of this work.

## Requirements retained

- Keep the existing q25/q50/q75 score profiles, equal player weights, Phoenix 2
  overlap precedence, Phoenix 1 normalization, and independent S/D reference curves.
- Keep every rated official scoring folder's median at level + 0.5, including
  even-sized and sparse folders. Preserve within-folder ordering and ties.
- A chart matching a neighboring folder's typical raw profile does not automatically
  inherit that folder's midpoint. No chart reassessment or membership changes.
- Two-grade SCORING moves should be uncommon across Singles and Doubles combined.
  Treat roughly 3-10 as a diagnostic range, not a quota or hard ceiling. Count
  both directions and include limited-data charts; never force a minimum of three.
- Count final six-decimal exports using proposal >= level + 2 or < level - 1,
  consistent with the app's one-decimal truncation. This is not an absolute
  continuous difference >= 2 rule. Fewer than three moves is acceptable.
- Pumbility remains the unrounded arithmetic mean of scoring and clearing.
  Do not impose this scoring rarity target on Clearing or Pumbility.
- Clearing, Co-op, player skills, and recommendations remain unchanged.

## Findings motivating the change

The current shared scale is 0.12. D26 scoring spans approximately 26.103-26.861,
Clearing spans 26.183-26.921, and Pumbility spans 26.312-26.679. Component disagreement
also compresses the average; score and clear spread are distinct from combined spread.

A read-only D26-only scale of 0.32 gives scoring 25.442-27.463 and Pumbility
26.052-26.980 (displayed 26.0-26.9), without adding two-grade scoring moves. This is
a useful diagnostic example, not a hardcoded D26 scale or mandatory endpoint target.

EMS has 14 contributors. Its observed profile is [906255.25, 916145, 924796.5].
Five of six players shared with measured D27 charts have a lower EMS score than
their own median D27 score, with a median difference of -13188.5 points. This
supports investigating D27-range scoring difficulty, but does not establish 27.5.
Five of the nine measured D27 charts have only one contributor. The raw reference
curve ends at D26.5; higher raw matches are extrapolations, not established grades.

## Proposed formula

Keep raw profile matching and folder centering unchanged. Replace only the shared
scale with a scale selected for the chart's mode and official level:

```text
offset_c = raw_profile_match_c - median(raw_profile_matches_in_folder)
scoring_c = official_level_c + 0.5 + scale[mode, level] * offset_c
pumbility_c = (scoring_c + clearing_c) / 2
```

All scales remain on the 0.00, 0.01, ..., 1.00 grid. A positive scale preserves
within-folder ordering and median centering; zero is an explicit last-resort case
that collapses a folder to its midpoint. There is no D20 cutoff and no compulsory
monotonic increase of scale with official level.

## Choosing preferred spreads

Use the central 80% of raw matches to measure typical spread without letting a
single extreme chart determine it:

```text
raw_width_f = q90(offsets_f) - q10(offsets_f)
baseline_scale = current shared-scale fitter on this same snapshot
preferred_width_f = max(1.10, baseline_scale * raw_width_f)
preferred_scale_f = preferred_width_f / raw_width_f
```

The initial 1.10-grade central-80% width is an explicit experimental calibration
setting, not a claim that all folders truly have the same spread. It provides a
soft target for narrow folders and retains wider baseline targets for broader
folders. The soft rarity penalty and neighbor smoothing may keep a folder below its target. All
percentiles above are ACROSS CHARTS; the existing q25/q50/q75 player-score profile
inside each chart is unchanged.

This target gives D26 an unconstrained preferred scale near 0.32 using current
data. Do not fit or select the target by requiring EMS or any named chart to land
at a particular grade. Report achieved widths and constraints that prevent a fit.

A folder supplies its own spread target only when it has at least ten rated charts
and median contributor count >= 10. Its reliability weight is:

```text
weight_f = min(rated_chart_count / 30, 1) * min(median_contributors / 20, 1)
```

Otherwise its width-fitting weight is zero, and its scale is borrowed through the
neighbor smoothing below. All rated charts still enter final centering and the
outlier count, irrespective of whether the folder supplies a spread target.
Zero-width folders get no inverse-width calculation and no artificial spread.
If an entire mode lacks supported spread targets, prefer the baseline scale in
that mode, within the same soft-penalty objective.

## Joint scale selection with a soft rarity target

For each folder and each candidate hundredth scale, precompute:

1. Its exact two-grade count after the existing six-decimal export.
2. Its achieved central-80% width.
3. Its width cost, for supported nonflat folders:
   `weight * ((achieved_width - preferred_width) / preferred_width)^2`.

Select scales that minimize:

```text
sum(folder_width_cost)
  + 0.05 * sum((log(scale_f + 0.01) - log(scale_previous + 0.01))^2 / level_gap)
  + 0.05 * max(0, total_two_grade_count - 10)
```

Both 0.05 coefficients are recorded initial experiment settings. The final term is
an explicit SOFT penalty: the eleventh move incurs a cost, but remains possible
when the improvement in the fit is larger. No choices are rejected because they
produce more than ten moves. There is no penalty for having fewer than three.
Report count and severity alongside the objective components so the tradeoff is
visible; do not silently retune coefficients merely to print a particular count.

Smoothing connects neighboring official levels within the SAME mode only. It
stabilizes sparse folders and discourages abrupt scale jumps. If a mode has no
supported targets, replace its width costs with a preference for the baseline
scale to make the fallback explicit.

Use a small dynamic program per mode with state:
`(folder_position, candidate_scale, count_bucket_0_to_10)`.
The bucket is `min(actual_count, 10)`. Counts beyond ten add the overflow penalty
rather than becoming infeasible. Carry true counts in the selected paths for
reporting and tie-breaking. When combining S/D solutions, account for the shared
free allowance exactly: if each mode already paid for its own overflow beyond
ten, add `0.05 * max(0, singles_bucket + doubles_bucket - 10)`. This produces
`0.05 * max(0, total_actual_count - 10)` over both modes without an unbounded count
state. Test this identity and the optimum against exhaustive small examples.

Tie-break deterministically: lowest objective, then fewer total outliers, then the
lexicographically smaller scale vector in mode/level order. Existing outlier
charts are not reserved exceptions. This remains practical for 101 scale choices
and the current folder count without a numerical-optimization dependency.

Do not apply another global multiplier after this step, since that would recreate
the compression problem. Do not clamp individual charts to conceal large moves.

## Cross-level diagnostics, separate from model fitting

Add a local diagnostic report using the same normalized, deduplicated records:

- Per-folder raw/final scoring and Pumbility min, q10, median, q90, max, selected
  scale, reference support, extrapolation count, and two-grade count.
- Adjacent-folder score-profile comparisons for both modes, including D26/D27.
- Shared-player score differences for chart comparisons: overlap count, median
  difference, and lower/tied/higher counts. Include a same-Phoenix-source subset.
- For a chart against a neighboring folder, aggregate each shared player's
  differences against that player's median reference-chart score once. Do not
  treat many pairs from one player as independent evidence.
- Clearly label sparse comparisons. Do not infer a precise grade from a single
  contributor or declare extrapolated raw matches to be true difficulty.
- Include EMS as an illustrative check, alongside other charts and levels.

These diagnostics do not add skill weights, player baselines, or pairwise signals
to the tier estimator. They assess whether the calibrated profile model behaves
plausibly and expose disagreement for review.

## Integration

1. Add a pure folder-scale fitter in `scoring_percentile.py`; retain the current
   shared-scale function for the baseline comparison and fallback.
2. Use each folder's scale for final proposals and bootstrap interval endpoints.
   Hold raw curves, folder centers, and selected scales fixed in these conditional
   intervals; do not imply that scale-selection uncertainty is included.
3. Recompute Scoring ranks/bands and Pumbility from unrounded calibrated values.
4. Export per-chart applied scale and per-folder scale/width/support diagnostics.
   Remove the misleading single final scale from adaptive-method metadata.
5. Advance the local experiment schema to 24 with a distinct method marker.
   Update `lib/local-analysis.ts`, `lib/types.ts`, and tier details/footer to
   validate and explain the per-folder scale and soft global rarity target.
6. Use the existing local `--scoring-profile` entry point. Save the current schema-23
   artifact and recommendation hashes before overwriting the local tier aggregate.
   Leave regular scoring/production contracts unchanged.
7. Add a read-only diagnostic script and write private aggregate comparison reports.
   Do not export player identifiers or individual player histories to the tier payload.

## Verification and stopping criteria

- Tests for independent mode scales, exact median centering, preserved ordering,
  sparse/flat/all-unsupported fallbacks, deterministic ties, and rounded boundary counts.
- For a small synthetic problem, compare the dynamic-program result with exhaustive
  enumeration to verify the objective, combined count, and soft overflow penalty.
  Include cases where more than ten moves is the optimal result and where zero
  moves is optimal; neither should be artificially changed.
- Test that one restrictive folder does not force an unrelated supported higher
  folder down to the same scale; exercise sharing the soft ten-move allowance across modes.
- Check every final interval conversion and Pumbility average.
- On current data, independently verify all folder medians and actual move counts after
  display truncation, unchanged raw profiles/curves, and unmodified Clearing/Co-op
  records and recommendation hashes.
- Report current-data D26/EMS results and broad level trends. Do not make exact
  D26 endpoints or an EMS grade hardcoded test assertions. If the proposed initial
  settings fail to resolve progressive compression or produce many moves beyond
  the 3-10 guideline, report the discrepancy and supporting evidence before
  expanding the model. Do not automatically compress all folders to hit the band.
- Run relevant Python/frontend tests, build, and local API checks. Perform one
  general review, repair only proven regressions, and stop once verification passes.

## Expected scope of the result

Higher folders can receive wider scoring spreads while lower-folder extremes
are discouraged locally and through the soft rarity penalty. Pumbility changes only through its scoring component;
its range and ranking may change because the relative scoring differences widen.
Its median is not forced to x.5, and its exact endpoints remain an observed result.
The approach does not remove player-selection or best-score-history bias. It is
an experiment in calibration, supported by cross-level diagnostics, not proof of
an absolute scoring-difficulty scale.

## Implementation result

Implemented through three parallel agents (model/tests, UI/contracts, diagnostics)
and integrated locally. All 24 rated scoring folder medians remain at x.5. The
selected solution has 10 two-grade moves with a soft rarity penalty, not a hard
limit. D26 scale is 0.32: scoring 25.441939-27.462660; Pumbility
26.051570-26.980030 (displayed 26.0-26.9). EMS D26 scoring is 27.462660.

All raw profiles, reference curves, raw matching estimates, source counts, and raw
bootstrap intervals match the prior artifact. All 2,606 Clearing records, 140 Co-op
records, and 95 recommendation files are unchanged. Pumbility remains the unrounded
component average. No fresh data was pulled and nothing was deployed.

Verification: 138 Python tests, 67 frontend tests, Next production build, one general
review, independent per-chart calibration/objective checks, and local API/page smoke
checks passed. No critical implementation issue or unresolved blocker was found.
Existing pandas/module-type/line-ending notices were not changed. Sparse cross-level
comparisons remain explicitly marked as limited evidence.

Private results: `.local-data/piu-scores/verification/scoring-profile-level-scales-2026-09-26/`.
