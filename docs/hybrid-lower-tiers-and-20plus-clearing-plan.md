# Lower-level Pumbility and 20+ Clearing calibration

Date: October 17, 2026. Status: implemented locally (schema 48); production rollout
authorized and pending release verification.

The cached-data candidate restores averaged Pumbility for 1,292 charts below
level 20; 2,598 charts have both components overall. Official 20+ Scoring values,
Clearing q10 thresholds/support counts, Co-op, the two prior local generations,
and all 107 cached source/recommendation/personal files passed preservation checks.
On these inputs, S20 Clearing estimates below 20.0 decrease from 54 to 1 of 137;
averaged Pumbility estimates below 20.0 decrease from 34 to 7. The 67 S20 Clearing
extrapolations remain labeled provisional.

Verification: 48 affected Python tests, 96 frontend tests, TypeScript and the
production build pass. The running local API validates and returns schema 48.
Browser interaction was blocked by the browser tool's URL policy on an existing
error page, so the planned visual checks remain unverified. One general code
review found no proven regression requiring repair. Local audit artifacts are in
`.local-data/hybrid-tier-experiment/`.

Interpret “below difficulty two” as below difficulty 20, within the existing
Singles/Doubles display range of levels 16–19. This revises the earlier decision
to leave below-20 Scoring and Pumbility unrated. No expansion below level 16 or
change to Co-op is proposed.

## Confirmed behavior and proposed policy

The average experiment is working as implemented: it requires both components.
The official cross-level overlay deliberately clears Scoring below 20, so there
is currently no Scoring number to average with Clearing. The submitted score
profile model already runs before that overlay and the private cached inputs for
both Phoenix versions are available. Preserve its lower-level results instead of
erasing them. This requires a declared submitted-score fallback; official
leaderboards cannot supply these lower-level scores.

| Current official level | Scoring | Clearing | Local Pumbility |
|---|---|---|---|
| 16–19 | Existing submitted score-profile model | Folder-centered submitted Clearing fallback using the same full-history skills | Average of the two adjusted components |
| 20+ | Existing official Phoenix 1/Phoenix 2 cross-level model | Submitted q10 clearer thresholds calibrated using only 20+ reference anchors | Average of the two adjusted components |

Populate the Scoring tab and Pumbility's Scoring component consistently for 16–19.
Keep official-only Scoring at 20+: missing official evidence there must not select
the submitted fallback. For below-20 Clearing, reuse the established folder-centered
formula with the existing fixed scale 0.70 and the current top-30/200-record
thresholds. This deliberately replaces the below-20 cross-level calibration; it
does not reuse the new 20+ boundary extrapolation for those charts.

For lower-level chart threshold T, folder reference median R and midpoint M:

    initialClearing = M + 0.70 * (T - R)
    finalClearing = M + 0.50 * (initialClearing - M)

Use comparable reference charts with at least 20 qualified clearers, and require
five such charts for a folder reference. Without a supported reference, report
unrated rather than borrowing an unrelated scale. This keeps lower-folder medians
representative without forcing a one-level jump across nearly identical adjacent
thresholds. Below-20 Clearing values can change and must be included in the audit.

All player records remain available for skill and eligibility at every level:
200 distinct valid same-mode records, and the average official level of the
player's 30 hardest unique clears. Below-20 records can satisfy either rule.
The 20+ restriction concerns calibration anchors and which charts use the new
Clearing curve, never player histories or the records used to establish skills.

## Calibration evidence

Using the cached thresholds, with player skills and 50% post-processing fixed:

| Folder | Rated | Currently below official level | With 20+ anchors |
|---|---:|---:|---:|
| S20 | 137 | 54 | 1 |
| S21 | 170 | 6 | 6 |
| D20 | 152 | 38 | 7 |
| D21 | 130 | 23 | 23 |

These are sensitivity results, not target quotas. New data can change the counts.
The reference S19 and S20 thresholds are 21.0333 and 21.0833. Exact median anchors
map this 0.05 gap to one difficulty level. Smoothing through the same exact anchors
cannot change the count below the corresponding boundary. The 20+ candidate
instead uses the S20→S21 boundary slope on the easy side.

The candidate retains the S20 median near 20.5 but makes 67 of its 137 estimates
lower-bound extrapolations. Preserve provisional/extrapolated labels based on the
initial fit, even after compression. Do not clamp estimates to 20 or use an
arbitrary desired number of overrated charts. Each mode has its own anchors.

Reference-chart resampling also shows weak S19/S20 separation in Singles: 12.14%
of 10,000 membership resamples have a non-increasing gap. This is reference-set
sensitivity, not a player-bootstrap confidence interval or a pass-probability
estimate. Revisit the ability proxy or approximate-anchor regularization only in
a separate modeling investigation if this remains unstable.

Evidence: `.local-data/analysis/clearing-boundary-investigation/analysis.json`.

## Implementation sequence

1. **Freeze inputs and the two existing local generations.** Retain the schema-44
   maximum baseline and schema-45 average candidate, source hashes, recommendation
   artifacts and personal-score artifacts. Reuse the cached submitted snapshots;
   this experiment needs no new capture.
2. **Retain lower-level submitted Scoring.** In
   `scoring_percentile.py::build_production_tier_payload`, retain the submitted
   profile values, calibration and evidence for 16–19 before applying the official
   overlay. Refactor `official_cross_level_scoring.py` to replace only current
   20+ charts under the new hybrid policy. Retain official curve, costs, intervals,
   support, source merging and higher-level Scoring values exactly on pinned inputs.
3. **Separate Clearing calibration scope from history scope.** In
   `cross_level_clearing.py`, compute the same full-history skills and q10 chart
   thresholds first. Fit a separate 20+ curve from eligible current-level-20+
   reference charts. Apply it only to 20+ rows. Route 16–19 through the folder-centered
   fallback above, without fitting an additional cross-level curve through the
   unstable S19/S20 pair. Require sufficient increasing upper anchors including 20;
   fail closed on an invalid upper curve. Preserve raw thresholds and support
   counts for every chart.
4. **Finalize in one order.** Work in initial-fit coordinates. Select the Scoring
   and Clearing methods by current official level, apply the existing 0.5 midpoint
   compression once, then derive local Pumbility as `(Scoring + Clearing) / 2` at
   full precision. Retain both-component requirements, weaker evidence and separate
   support counts. Recompute affected ranks, deltas, bands, availability and summary
   counts. Existing one-decimal truncation remains unchanged.
5. **Version the hybrid contract.** Allocate unused schema/method identities at
   implementation time; do not reinterpret schemas 44 or 45. Record per-range
   source policies, submitted Scoring calibration, the two Clearing paths, minimum
   upper calibration level 20, full-history skill policy, source hashes and the
   average composite policy. Update `pumbility_contract.py`, runtime checkpoint
   identities, frontend types, validators and the local experiment builder. Keep
   explicit readers for the existing maximum and average generations. Historical
   compressed generations must never receive another compression pass.
6. **Update local UI and explanations.** Dispatch labels, support, intervals and
   component details by each chart's actual method. Replace blanket statements
   that below-20 Scoring/Pumbility are unrated for this hybrid generation. Show
   submitted Scoring at 16–19 and official Scoring at 20+. Explain 20+ Clearing
   anchors separately from full-history player skills. Preserve All, Only 20+,
   individual folders, BPM filters, personal-score overlays and demo behavior.
7. **Build a separate local candidate and preview it.** Keep the current average
   experiment available for rollback. Produce a before/after table by mode/folder,
   restored lower-level coverage, component and composite changes, unchanged source
   hashes, extrapolation counts, and examples near the 19/20 boundary. Open the
   candidate only after validation passes.

## Acceptance and bounded verification

- Levels 16–19 with both usable components have an averaged Pumbility estimate;
  its displayed components agree with the Scoring/Clearing tabs. Missing either
  component remains unrated and its reason is shown.
- Official 20+ Scoring remains numerically and semantically unchanged. A missing
  official score does not trigger the lower-level submitted fallback.
- Below-20 Clearing follows its declared folder-centered formula and its eligible
  reference-chart median maps to the official midpoint. The new 20+ curve contains
  no below-20 anchors; its eligible reference medians map to their declared points.
- Player skill/qualification and q10 thresholds are identical before and after.
  Tests explicitly include players who need below-20 records to reach 200 records
  and players whose top-30 clears include lower-level charts.
- With skills/thresholds fixed, changing the below-20 anchor pool cannot change
  the 20+ curve. This does not assert that changing player history has no effect.
- Full-precision averages, null handling, single compression, intervals, evidence,
  ranks and method dispatch validate. Unsupported/extrapolated rows retain their
  appropriate evidence labels. Legacy schemas still render correctly.
- Co-op, recommendations and personal-score artifacts remain unchanged. Run the
  affected Python and frontend suites, TypeScript and a production build. Read the
  installed Next.js guidance before frontend edits. Check S19, S20, S21, D19/D20,
  metric switching, component details and Only 20+ in the browser.
- Perform at most one general review, repair only a demonstrated regression and
  rerun affected checks. Stop when these criteria pass.

## Rollout boundary

The user has authorized production deployment of the current hybrid tiers with
arithmetic-average Pumbility. Release work must deploy compatible readers, enable
the versioned schema-48 writer, and republish the tiers from cached inputs through
the existing atomic bundle. Publication and production verification remain
pending. Existing generations remain available to their explicit readers.

The proposed SSS threshold scoring selector remains a plan only and is outside
this release. Recommendations and personal-score artifacts retain their current
definitions.

## Local candidate commands

The hybrid writer uses schema 46 for the initial fit and schema 47 after the
single midpoint adjustment. The local averaging step creates schema 48 separately:

    .venv/Scripts/python.exe scripts/build_local_hybrid_tiers.py
    node --experimental-strip-types scripts/build_local_pumbility_experiment.ts --hybrid --input <schema-47-maximum.json>

Without `--input`, the command reads
`.local-data/piu-scores/combined/experiments/hybrid/maximum.json`. It validates
both generations and exact recovery of the maximum source before writing
`.local-data/piu-scores/combined/experiments/hybrid/web_results.json` atomically.
Select this candidate using `PIU_LOCAL_PUMBILITY_EXPERIMENT=hybrid` while local
analysis mode is enabled, then restart the local server. The `average` selector
continues to read the retained schema-45 candidate; removing the selector restores
the normal maximum aggregate. The builder's default command still produces the
existing schema-45 experiment from schema 44.
