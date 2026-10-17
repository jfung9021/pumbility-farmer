# Cross level Clearing implementation plan

Date: October 17, 2026. Source choice: cached submitted PIUScores records from both
Phoenix versions, as confirmed by the user. This plan changes chart Clearing tiers;
implementation and a replacement local analysis were completed locally on this date.
The player skill specification below incorporates the requested top-30 average and
minimum of 200 score records. Pumbility uses the higher of Scoring and Clearing,
requiring both; this user-confirmed rule supersedes the earlier average wording.

Local implementation: `cross_level_clearing.py`, `cross_level_clearing_calibration.py`,
and the versioned tier-only helper in `player_skill_ratings.py`. Rebuild with
`scripts/build_local_recommendations.py --cross-level-clearing`. Schema 43 adds the
new Clearing contract while retaining existing Scoring identity and legacy readers.
The current run qualifies 515 Singles and 393 Doubles players, supports S16-S25 and
D16-D26 anchors, and rates 1,285 Singles and 1,313 Doubles charts. Scoring, Co-op and
recommendation artifact preservation is verified in
`output/cross-level-clearing-2026-10-17/verification.json`. Chart comparisons,
source/anchor metadata and sensitivity results are saved in the same directory.
No deployment or hosted setting change was performed.

## Goal and interpretation

Build one Clearing scale across official levels, separately for Singles and
Doubles. A supported official folder's median reference-chart statistic represents
`official level + 0.5`. Every chart is mapped through the same mode-specific curve,
so comparable evidence can produce comparable estimates across official folders.
Keep chart IDs, current official levels and official-folder membership unchanged.

With the existing inputs, the measurable quantity is the clearing skill of observed
successful players. It is a proxy for a chart's clearing threshold, not an estimate
of the population clear rate, first-attempt success probability, or intrinsic
difficulty independent of practice and reporting behavior.

## Confirmed data constraints

The local submitted captures contain:

| Capture | Completion date in UTC | Successful records | Players |
|---|---|---:|---:|
| Phoenix 1 | September 25, 2026 | 616,472 | 936 |
| Phoenix 2 | October 2, 2026 | 60,272 | 965 |

All cached rows have `isBroken=false`. `phoenix2_sync.py::sanitize_score` discards
broken rows, and `scripts/capture_private_score_snapshot.py::validate_snapshot_rows`
rejects them. Neither an absent record nor an absent official-board entry supplies
a negative clearing observation. These are best-record snapshots, not attempt logs.

The cached account IDs overlap for 932 players. These are PIUScores `userId` values
from the account endpoint, rather than the mix-specific identities in the official
leaderboards. Use exact account IDs for the submitted-source union; do not join
by usernames or reuse the official-leaderboard numeric-ID mapping.

The current submitted Clearing method uses a player's top-50 unique-clear official
level average, then the tenth percentile of those skills among each chart's
clearers. It centers each folder independently and applies a fixed 0.70 multiplier.
The existing high-level official Clearing overlay is a different method, using a
twentieth-percentile clearer-ability statistic and capped-board defaults.

## Recommended first implementation

### Prepare submitted clear evidence

Pin both cached inputs and the current Phoenix 2 catalog used by the tier build.
Record source completion times, content hashes, catalog hash and method identity.
Use all eligible current-catalog history when calculating player skill. Continue
publishing the existing level-16-and-above Singles and Doubles chart population;
Clearing has no official-leaderboard-driven level-20 restriction.

For each compatible current chart UUID, union successful clear membership across
the two sources by account ID. Each player contributes once. Include valid
zero-Pumbility clears. Raw numerical score, plate quality, scoring contribution
windows and official leaderboard positions do not select the Clearing population.
Keep source/current mode mismatches and missing chart mappings out of the model
and count their exclusions.

Preserve source provenance for charts whose Phoenix 1/current step counts differ
or cannot establish comparability. Flag affected chart estimates as provisional
and exclude them from reference anchors; a re-level alone is not a changed chart.
Note count equality is a comparability check, not proof that steps are identical.
Do not change the player-skill history rule as an incidental consequence of these
chart-anchor checks.

The official high-level Clearing overlay must not overwrite this method's output.
The new path uses submitted records for every in-scope Clearing chart, including
the high levels previously supplied by official boards. Remove capped-board
defaults from the new Clearing path; preserve the legacy path for rollback.

### Define player skill and record eligibility

Add an explicitly versioned Clearing-tier skill helper alongside the existing
helpers in `player_skill_ratings.py`:

```text
For one player and one mode:
    Collect valid current-catalog score records from both versions.
    Deduplicate by account ID and chart UUID across versions.
    Require at least 200 records in this mode.
    Collect unique successful current-catalog charts from both versions.
    Require at least 30 unique clears in this mode.
    clearingSkill = mean(current official levels of the 30 hardest charts)
```

Apply the 200-record requirement separately to Singles and Doubles, using all
eligible history including charts below the tier page's level-16 display floor.
Repeat records and a chart present in both versions count once, so 100 overlapping
Phoenix 1 records and 100 Phoenix 2 records do not become 200. Do not count malformed
rows, incompatible chart modes or missing current-catalog mappings. Valid
zero-Pumbility records count. In the current success-only cache, this gate is
equivalent to having at least 200 distinct cleared charts in the mode; it is not
a count of attempts.

Order unique clears by current official level descending, breaking ties by chart
UUID. Average exactly ranks 1 through 30, without expanding a tied boundary, adding
0.5 to levels or substituting Scoring difficulty. Fewer than 200 qualifying records
or fewer than 30 unique clears means no usable skill for this chart-tier method.
Those players still contribute to a chart's total clear count, but not its rated
clearer percentile; retain separate qualification failure counts.

This is a clearing-accomplishment measure and does not use score accuracy. The
target chart may count toward the 200-record gate and the top 30; no leave-one-out
rule is introduced. A leave-one-chart-out variant must be evaluated separately.
Do not feed newly estimated chart Clearing difficulties back into this skill
calculation. Preserve the existing personal-rating and recommendation helper
defaults; this new definition belongs to the Clearing-tier calculation.

### Estimate each chart threshold

For chart c, let R[c] contain one finite same-mode clearing skill per unique
observed clearer. Define:

```text
threshold[c] = quantile(R[c], 0.10, method="linear")
```

Keep the existing tenth percentile while changing player skill, eligibility,
cross-level calibration and submitted-source coverage. It is a
percentile of clearer ability, not of scores or the unknown full player population.
Do not use the minimum skill, raw clear count, a fixed leaderboard place, or an
assumed failure for missing players. No finite skills means a null chart threshold.

Store total unique clearers, rated clearers and missing-skill counts separately.
Do not fabricate a pass denominator. All calculations retain full precision.

### Calibrate across levels with median chart anchors

Recommended reference criteria are at least 20 rated clearers per chart, compatible
known chart metadata, and at least five reference charts in a mode/official folder.
Each reference chart gets one vote, regardless of its player count.

For each supported level L:

```text
m[L] = median(threshold[c] for eligible reference charts at official level L)
anchor[L] = (m[L], L + 0.5)
```

Require at least three supported folders and strictly increasing finite m[L].
There is no special requirement for a level-20 anchor. If supported medians tie or
reverse, report the conflicting folders and retain the previous analysis. Do not
silently jitter, reorder, clamp, or move their anchors.

Use a monotone piecewise-linear inverse mapping for version one. For adjacent
supported levels L and K, including gaps where intermediate folders lack support:

```text
difficulty(t) = L + 0.5 + (K - L) * (t - m[L]) / (m[K] - m[L])
```

Apply this same mapping to every chart threshold in the mode. Continue the boundary
segment slopes outside the supported range and mark those estimates provisional.
Charts in unsupported official folders are provisional even if interpolated.

This differs deliberately from Scoring's exponential score-point curve: the input
here is already an average-of-levels ability measure. Exponential growth is not
established for that quantity. Median anchors are the requirement; nonlinear
curvature should be a separate model choice supported by evidence.

The invariant is `difficulty(m[L]) = L + 0.5`. For an even reference pool, m[L] is
the midpoint of its two middle thresholds. Do not promise that the arithmetic
median of all transformed or displayed ratings is identical: the reference pool
excludes sparse charts and nonlinear changes of slope need not commute with that
midpoint. Publish the anchor population and exact definition.

Retire the old 0.70 multiplier, independent folder centering, within-folder spread
calibration and official cap defaults on this new path. Do not reassess a chart
into another official folder or repeatedly recenter the calibrated estimates.

### Evidence and uncertainty

Recommended evidence labels are Published with at least 20 rated clearers;
Provisional with 5 through 19; Insufficient with 1 through 4; and Unrated with none.
An Insufficient chart may retain a finite descriptive estimate. Chart comparability,
unsupported folders or extrapolation can weaken a status, never strengthen it.

Do not carry Scoring's confidence intervals or the official Clearing q20 bootstrap
into this method. Use null Clearing intervals initially and show the evidence
counts. Resampling/stability diagnostics can be reported separately without
claiming coverage for selection, effort or unknown attempts.

## Preflight on the cached inputs

`output/clearing-cross-level-plan-2026-10-17/data-audit.json` records the support-only
audit made before the top-30 average and 200-record requirement were added. It uses
the previous top-50 skill definition without the new record gate. Its player counts,
reference medians and supported ranges are historical baseline results, not
verification of the revised candidate.

Re-run this audit with the top-30 skill, 200-record eligibility, exact current
catalog and comparability policy pinned for implementation. Report qualified and
excluded players by mode, record/clear counts, revised chart support and median
anchors before claiming any folder is supported. The local tier artifact has newer
metadata than the submitted capture; new charts without submitted evidence must
remain Unrated.

## Implementation work packages

1. Add a small submitted Clearing evidence/threshold module. Reuse clear-membership
   preparation before snapshot consumption and add the versioned top-30/200-record
   skill helper without changing legacy helper defaults. Keep the official score
   merger and official Scoring overlay independent.
2. Add a Clearing-specific calibration module for median references, piecewise
   linear inversion, boundary continuation and reference diagnostics. Do not modify
   `official_cross_level_calibration.py` in a way that changes Scoring outputs.
3. Add a Clearing overlay after existing Scoring/source overlays in
   `scoring_percentile.py` and the production tier-build entry points. Replace only
   Singles/Doubles Clearing and derived Pumbility. Preserve all Scoring row fields,
   source metadata, estimates and intervals, Co-op, personal ratings and
   recommendation artifacts. Pumbility takes the unrounded maximum of
   Scoring and Clearing, null when either is null. Thus below-20 Clearing
   can be rated while Pumbility stays null under current Scoring policy.
4. Add a dedicated local `--cross-level-clearing` rebuild mode to
   `scripts/build_local_recommendations.py`, with pinned source inputs and atomic
   output promotion. Do not require a scoring rebuild or overwrite saved research.
5. Version the Clearing method, calibration, combined-tier schema and checkpoint
   policy in `pumbility_contract.py` and `analysis_runtime.py`. Use the next free
   schema version at implementation time. Reject stale combined and later-phase
   embedded tier checkpoints; keep legacy reader support for rollback. Existing
   Scoring capture manifests remain compatible. Make the new path independently
   selectable without enabling official Clearing or changing Scoring selection.
   Pin `topClearCount=30`, `minimumScoreRecords=200`, same-mode scope, deduplication
   policy and current-official-level basis in the method identity and cache keys.
6. Extend `lib/types.ts`, `lib/local-analysis.ts`, `lib/tier-metrics.ts` and
   `app/tier-list/page.tsx` for separate Clearing provenance and exact method labels.
   Details should show rated/total observed clearers, threshold, reference median,
   supported range and provisional reason. Remove official-cap explanations from
   this method, retain the Only 20+ option, and preserve the selected metric/filter
   behavior. Read the installed Next documentation before editing frontend code.

## Verification and local comparison

Save the current tier payload and recommendation hashes. Test unique membership
across sources, source-mode mismatches, zero-Pumbility clears, exact 199/200-record
eligibility, the top-30 window, deterministic ties at ranks 30/31, and 29/30-clear
eligibility independently of the record gate. Verify cross-version duplicates do
not inflate the gate, Singles records do not qualify Doubles, and below-16 history
can count. Test q10 interpolation, missing skills and empty charts. Test reference
thresholds at 19/20 clearers and four/five charts per folder,
odd/even median anchors, global monotonic mapping, sparse folders, extrapolation,
and deterministic failure on tied/reversed reference medians.

Verify Scoring row fields and Co-op are identical before and after, recommendation
hashes are unchanged, and Pumbility uses the new Clearing value before rounding.
Check publication/checkpoint validation, metric-specific evidence, private-data
exclusion and actual frontend parsing of the rebuilt payload. Run directly affected
Python suites, frontend tests, TypeScript and the production build; perform one
browser check of Clearing details and existing filters.

Produce an audit per chart with official level, total/rated clearers, threshold,
anchor eligibility, calibrated estimate, old/new difference and evidence reason.
Summarize support by mode/folder, source coverage, reference medians, tail usage,
largest shifts and the six previously selected charts. Include cohort stability
and source-removal sensitivity where the same reference support remains available;
do not call positive-only holdout results validation of a pass probability.

Perform at most one general review. Repair only demonstrated regressions, rerun
affected verification and stop. No deployment or new source pull belongs to the
planning request.

## Later clear outcome modeling

A joint player/chart model such as
`P(clear | player, chart, observed exposure) = sigmoid(ability - difficulty)` is a
candidate only after a separate data investigation establishes explicit usable
clear and non-clear outcomes. The current cache cannot supply its negative class.

If the API exposes broken best records, capture them through a separate private
contract before success-only normalization. Verify completeness and the meaning of
`isBroken` before modeling; a broken best record represents a recorded outcome,
not a known number of failed attempts. Cross-version and temporal success/failure
conflicts need a defined achievement/time policy. Keep missing rows unknown and
account for consent, selection and reporting effects.

The [Rasch framework](https://rasch.org/memo46.htm) provides the player/item
separation principle under model assumptions. [Research on selective observation](https://proceedings.neurips.cc/paper_files/paper/2019/hash/d828725179d622a56f951e527a966ed7-Abstract.html)
shows why self-selected records do not automatically identify population behavior.
A genuine outcome model, capture changes and evaluation are a later workstream,
rather than a hidden requirement for the cached-data candidate.

The deferred Scoring discussion is saved in
[Scoring difficulty methodology research](scoring-difficulty-methodology-research.md).
