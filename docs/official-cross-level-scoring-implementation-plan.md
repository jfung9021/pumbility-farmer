# Official-only cross-level Scoring implementation plan

Status: revised after one parallel review by three subagents. Date: October 17, 2026 (JST).
This document plans implementation; it does not authorize a deployment or change production data.

## Objective and scope

Replace Singles and Doubles Scoring evidence with official Phoenix 1 and Phoenix 2
leaderboards for charts whose **current Phoenix 2 official level is at least 20**.
Keep the higher raw official score for every deterministically matched player/chart.
Fit chart difficulty across levels, separately for Singles and Doubles, and calibrate
each supported official-folder median chart cost to official level + 0.5.
Use exponential segments between these median anchors so higher levels can require
larger score differences. No submitted scores or charts currently below level 20
may enter the new Scoring fit, player comparisons, or anchors.

Interpretation for review: below-20 Singles/Doubles Scoring becomes out of scope
and Unrated, rather than retaining a submitted-score fallback. Clearing values and
Co-op retain their existing methods. Pumbility is recomputed from the new Scoring
and existing Clearing; it is null if either component is unrated, including below
20. Player skill estimates and recommendation inputs, methods, fitted parameters
and predictions retain their existing definitions and values on pinned inputs.
Publication generation IDs, artifact paths and timestamps may change to satisfy
the existing atomic-bundle contract. These product choices must be visible in the
final plan.

## Evidence and limits

Research artifacts: `output/official-cross-level-2026-10-17/summary.json`,
`chart-estimates.json`, and `cross-level-curves.png`; research scripts live in `tmp/`.
The capture contains 355,633 merged observations on 1,314 nonempty chart boards.
Phoenix 1 and Phoenix 2 source dates are respectively September 6 and October 11,
2026. The current catalog has 1,389 level-20+ Singles/Doubles charts, so catalog
membership is not equivalent to having usable official evidence.

Numeric player IDs differ between mixes. Full game tags including discriminators
gave unambiguous matches in the research capture, but renamed players cannot be
guaranteed to match. Official boards are truncated observations, not complete play
histories. The additive raw-score player model outperformed the tested log-loss
model on a deterministic 20% held-out sample of observed records; this is not
evidence of unbiased predictions for missing attempts or new players.

## 1. Define separate source and scoring contracts

Add a versioned official Scoring manifest without repurposing the current Phoenix 2
Clearing snapshot. It references both per-mix captures, their catalog hashes,
source-specific `asOf` and fetch times, content hashes, merge-policy version, scope,
and model/calibration versions. Different dates between mixes are valid; mixed
dates **within one mix capture** are invalid. Pin exact source artifacts for a build.

For every target chart/source record distinguish:

- Available nonempty board.
- Available empty board.
- Chart absent from that mix catalog.
- Explicit upstream 404 indicating no mirrored board.
- Fetch failure/incomplete capture; never reinterpret this as an empty board.

Capture through the existing authenticated client and shared request limiter, with
`mix=Phoenix` or `mix=Phoenix2` and `supplemented=false` on every attempt. Validate
every entry has `isSupplemented=false` and a finite integer score in [0, 1,000,000].
Checkpoint source captures independently and publish a new manifest atomically only
after all expected target states have been captured and validated. Keep the last
complete generation on transport, authorization, or snapshot-consistency failure.
Cache Phoenix 1 only while its upstream source identity remains unchanged; continue
refreshing Phoenix 2 using the existing refresh cadence.

The existing production normalizer discards `gameTag`, so its numeric-ID-only
snapshot cannot supply cross-mix identities. The new private scoring capture must
retain tags before normalization. Research captures may seed it only after complete
validation against the new contract; never reconstruct tags or cross-mix mappings
from numerical IDs alone.

Retain raw rank, count, cutoff, mix, and source date as board metadata. Track possible
truncation per source; the observed Phoenix 1 boundary is about 100 entries and
Phoenix 2 about 300, with ties possibly increasing row counts. Never infer a
Clearing cap from merged row count, and never claim a short board is a full history.

## 2. Resolve identity and keep the maximum score

Chart identity is the stable chart UUID plus mode consistency, using the current
Phoenix 2 chart metadata and official level. Re-leveling does not create a new chart.
Do not match different chart UUIDs by title or difficulty label.

Build identity maps across the complete pinned capture, not separately per board:

1. Retain `(mix, numeric player ID)` as the source identity.
2. Recover a missing row tag from that same source ID only when the capture has
   exactly one consistent nonempty full tag for that source identity.
3. Link source identities across mixes only on an identical, nonempty full tag
   including `#discriminator`. In each pinned mix capture require both directions:
   that tag maps to one source ID, and that source ID maps to one nonempty tag.
4. Do not case-fold, fuzzy-match, or join solely on numeric IDs, names, or avatars.
   Missing or ambiguous identities remain source-qualified and are counted as
   unresolved. Do not silently merge contradictory mappings or guess renamed tags.
5. Deduplicate each canonical player/chart to the **maximum raw official score**.
   A tie produces one record with both sources recorded. Keep the winning source,
   source IDs, original scores, and match method privately for reproducibility.

The rule guarantees maxima for matched identities, not perfect identification of
every person. Public artifacts contain aggregate evidence and opaque identifiers
only; raw tags and cross-mix mappings stay in private inputs/diagnostics.

Changed-chart policy: honor the requested raw-maximum rule without silently applying
the submitted-score normalization function. Determine comparability from the mixes
actually supplying retained maximum scores, not merely from catalog membership.
Ties may record both source provenances but still contribute only one score.

| Retained evidence | Comparability / anchor policy |
|---|---|
| Phoenix 2 only, positive known Phoenix 2 note count | Eligible on this criterion; no Phoenix 1 count is required |
| Both mixes, positive known equal note counts | Eligible on this criterion |
| Both mixes, positive known different counts | Keep raw maxima; provisional estimate; exclude from anchors |
| Both mixes, either count unknown | Keep raw maxima; provisional estimate; exclude from anchors |
| Phoenix 1 only | Require its positive known count and comparability with the current Phoenix 2 chart to establish an anchor; known differences or missing current/source counts are provisional and excluded |

Flag known count changes separately from unknown/missing counts. Retain flagged
observations in the experimental fit and report their effect in the candidate
analysis; flagging does not discard a higher raw score or guarantee that other
charts' fitted costs are unaffected. No hand-picked numerical overrides. This
reference policy differs from the exploratory prototype and therefore requires a
fresh candidate fit; the six earlier numerical examples are not acceptance targets.

## 3. Fit chart cost globally within each mode

Use one connected comparison graph per mode across **all eligible official levels
20+**, replacing the current independent same-level Scoring fits.

```text
observedScore[player, chart] ≈ playerAbility[player] - chartCost[chart]
```

Reuse the equal-player pair weights in `official_player_scoring.py`: for a player
with n observed eligible charts, each target receives total comparison weight one,
distributed over its n-1 other charts. A player requires at least four distinct
eligible charts. Retain score-point units and solve the shared-player chart effects
jointly; fix the additive offset deterministically. Do not use submitted histories,
below-20 histories, raw fixed-rank cutoffs, or official level as the player's ability.

The largest connected component supplies the mode's common calibrated coordinate;
break size ties deterministically by chart IDs. Charts in other components have
no shared coordinate and remain Unrated with a disconnected-evidence reason.
Charts with no eligible comparisons are Unrated. Five to nine contributing players
can support a provisional estimate; at least ten are needed for normal evidence
and for anchor eligibility. One to four contributors remain insufficient/Unrated.
Each distinct player/chart contributes once, irrespective of appearing in both mixes.

Preserve raw chart costs, contributing-player counts, component diagnostics,
disagreement, and source/identity coverage. Missing leaderboard appearance is never
treated as a failed attempt or a zero score.

## 4. Build median anchors and exponential calibration

For each current mode/official-level folder, form a reference pool of charts in the
main component with at least ten contributing players, comparable known note
counts, and no known chart-change flag. Give each chart one vote. At least five
reference charts are required to establish that folder's anchor; record excluded
counts/reasons and coverage of the whole folder.

Let `m[L]` be the median fitted chart cost in that pool and `x[L] = L + 0.5`.
This definition means the **median eligible chart cost**, not the median observed
player score or a population median pooled across boards. Use full precision.
For even pools the midpoint of the two middle costs defines the anchor; the inverse
of that midpoint is the declared median. Do not promise that averaging the two
transformed middle ratings commutes with the nonlinear inverse.

Require at least three supported anchors, including level 20, and strictly increasing
median costs. If supported medians tie or reverse, fail candidate calibration and
report the conflicting levels; do not quietly move their anchors, clamp chart
ratings, or substitute a different curve. Reconsidering anchor rules would require
an explicit subsequent modeling decision. Unsupported intermediate levels do not
get artificial anchors; interpolate between the surrounding supported levels.
Fewer than three anchors cannot identify the intended three-parameter exponential
fit and makes the candidate mode uncalibrated; do not invent curvature from two
points or silently borrow it from the other mode.

Fit an offset exponential to supported medians to obtain a deterministic offset
`a < min(m)`. Use the following fixed version-one estimator, separately per mode:

1. Normalize costs to `z = (m - m[20]) / (m[last] - m[20])`, and let
   `u = anchorDifficulty - 20.5`. This removes the arbitrary additive chart-cost
   origin and keeps the optimizer's numeric scale fixed.
2. On the inclusive 1,600-point grid `k = linspace(0.001, 1.2, 1600)`, solve equal
   **folder-weighted** ordinary least squares for `z = A + B * exp(k*u)`.
   Retain only finite solutions with `A < 0` and `B > 0`.
3. Minimize mean squared residual over supported anchors. For normalized MSEs
   within 1e-12 of the minimum choose the lowest k. Convert the winning offset back
   to score points: `a = m[20] + A * (m[last] - m[20])`.
4. Fail calibration if no valid candidate exists. Record bound hits as a model-fit
   limitation in the candidate report; the fitting grid is versioned rather than
   expanded repeatedly until a desired chart result appears.

Use this offset for interval curvature, not as a substitute for measured anchors.
For neighboring supported anchor levels L and K, where K need not equal L+1:

```text
t = (difficulty - (L + 0.5)) / (K - L)
cost(difficulty) = a + (m[L] - a) * ((m[K] - a) / (m[L] - a)) ** t

difficulty(cost) = L + 0.5
                 + (K - L) * log((cost - a) / (m[L] - a))
                           / log((m[K] - a) / (m[L] - a))
```

This is continuous, strictly increasing, exponential inside each interval, and maps
every supported median to its exact x.5 anchor. It does not force each adjacent
level gap to grow monotonically: exact empirical anchors and globally convex
growth cannot always both hold. Small derivative changes at anchors are acceptable;
avoid adding a spline/smoothing project to this implementation.

Use tangent linear continuation outside the first/last supported anchor so the
inverse is defined for every finite chart cost (including costs below `a`). Mark
these values provisional/extrapolated, identify the boundary anchors, and exclude
them from setting anchors. Below-20 **input charts** remain excluded; a level-20+
chart may receive an extrapolated estimate below 20. No unsupported endpoint is
forced to its official midpoint. Folders without their own anchor are provisional
even when bracketed; with no valid mode curve the new candidate is not publishable.
For the boundary's adjoining exponential interval, define the tail exactly as:

```text
beta = log((m[K] - a) / (m[L] - a)) / (K - L)
endpointSlope = beta * (endpointMedian - a)
tailInverse(cost) = endpointDifficulty
                  + (cost - endpointMedian) / endpointSlope
```

The positive endpoint slope is held fixed in that tail; exponential acceleration
is claimed only inside the supported range, not in the provisional continuation.

Use `difficulty = inverseCurve(mean fitted player gap)` / fitted chart cost. Do
not average individually transformed personal ratings: nonlinear inversion does
not commute with averaging. Retire the old claim that published chart difficulty
is exactly the arithmetic mean of transformed personal difficulties.

## 5. Intervals and evidence labels

For version one retain a clearly **conditional** shared-player bootstrap: resample
player identities deterministically 1,000 times, hold fitted chart effects and the
calibration/anchors fixed, recompute each chart's mean adjusted gap, and invert the
same curve for its interval endpoints. Require at least five contributors. These
intervals exclude uncertainty in fitted player/chart effects, anchor selection,
source truncation, effort, identity matching, and chart comparability. Do not carry
over old within-folder affine interval formulas or present these as full model CIs.
Full model/anchor refitting per bootstrap is outside this first implementation.

Evidence status must combine player count, component membership, identity/chart
comparability diagnostics, anchor support and extrapolation. Possible board
truncation remains visible but does not by itself remove all scoring observations.
Retire old Scoring IQR-outlier labels tied to same-folder affine deltas unless a
replacement definition is explicitly agreed; continue displaying the numerical
crossing of an official level separately from evidence strength.

## 6. Integrate without changing Clearing inputs

Separate Scoring ingestion/model selection from the existing official Clearing
overlay. Do not globally lower the shared `is_official_target` cutoff and thereby
change Clearing's chart set or histories. Existing Clearing keeps its Phoenix 2
source identity, history scope, estimates, intervals, cap policy and references.
The merged sources and expanded Scoring graph must never feed Clearing by accident.
Unchanged Clearing means equal results for identical pinned Clearing inputs; its
existing independent refresh can still legitimately update results as those inputs
change.

Add metric-specific public row fields such as `officialScoringEvidence` and
`officialScoringContributors` for the merged sources. Preserve existing Phoenix 2
`officialEvidence` and `officialContributors` semantics where Clearing uses them.
Current frontend validators derive Clearing counts/cap defaults from these legacy
fields, so copying only the Clearing estimate is insufficient. Update metric
selectors, validation and support labels to use the appropriate evidence object.

Build a new scoring overlay after the existing Clearing calculation (or extract
the existing Clearing implementation with exact-output regression coverage).
Replace Singles/Doubles Scoring evidence, estimates, intervals, ranks, diagnostics,
methodology and support counts under the new version. Recompute Scoring rank/band
fields and Pumbility from unrounded component values. Null Scoring means null
Pumbility; never average only the available component. Co-op remains unchanged.

Remove stale same-folder scale/profile/What-if metadata from the new Scoring rows.
Keep score-point cost fields separate from difficulty-level fields. The new public
method description must explain that all 20+ levels share one coordinate per mode,
median anchors use reference charts, and source dates differ. Labels must no longer
say 'same-level comparisons' or 'one fixed number of score points per level'.

Version the schema, scoring method, source manifest and checkpoint identity. Include
both catalogs/captures, merge policy, scope, identity policy, change flags, model
rules, anchor membership/costs, offset fit, tail policy and interval method in cache
keys. Existing generation/checkpoint artifacts must not be accepted under the new
method merely because chart IDs match.

Primary implementation locations:

| Concern | Files / proposed module |
|---|---|
| Per-mix capture and official validation | `official_scores.py`, `scripts/capture_official_score_snapshot.py`; separate scoring manifest/capture helpers |
| Source identity and max merge | new `official_score_merge.py` |
| Global player comparisons | `official_player_scoring.py`, with an explicit cross-level entry point |
| Anchors and inverse curve | new `official_cross_level_calibration.py` |
| Scoring overlay / Clearing isolation | `official_tiers.py`, `scoring_percentile.py` |
| Input contracts / hosted refresh / artifacts | `pumbility_contract.py`, `analysis_runtime.py`, `recommendation_artifacts.py`, `scripts/build_local_recommendations.py`, existing worker orchestration |
| Public types, validation, rendering | `lib/types.ts`, `lib/local-analysis.ts`, `lib/tier-metrics.ts`, tier-list components and API consumers |
| Explanation and provenance | README / methodology UI and evidence diagnostics |

The exact new schema numbers are assigned against the then-current repository
versions rather than assumed in this plan. Before modifying Next.js code, read the
relevant installed guides in `node_modules/next/dist/docs/` as AGENTS.md requires.

## 7. Verification and bounded rollout

Implement and verify in this order:

1. Source manifests/capture and identity/max merge.
2. Global raw-score fit and graph/evidence diagnostics.
3. Median anchors, interval curves, inverse, tails and conditional intervals.
4. Versioned overlay, validators/UI and derived Pumbility.
5. Reproducible full local candidate, one comparison report and preview.
6. After the candidate is reviewable and deployment is explicitly authorized,
   deploy readers that validate **both** the exact legacy schema/method and the new
   schema/method. Dispatch to separate validators and explanations; do not simply
   weaken existing checks. Serve the legacy generation while verifying this stage.
7. Enable the new writer and atomically publish the complete candidate through the
   existing analysis/tier/recommendation publication bundle. Do not introduce a
   separate tier-only publication architecture. Bundle generation metadata may
   change while recommendation calculations remain identical on pinned inputs.
8. Rehearse reverting the publication pointer to the retained prior generation
   during this reader compatibility window. Preserve legacy validation until the
   rollback window is explicitly closed. Later rollback after removing legacy
   support would require coordinated application-code rollback as well.

Required semantic tests:

- Same full tag with different cross-mix IDs merges; equal numeric IDs with
  different tags do not. Within-source missing tags use only verified source-ID
  mappings; contradictions in either tag-to-ID or ID-to-tag direction and renames
  never cause guessed joins. Numeric-ID-only legacy snapshots cannot seed identity
  matches without verified raw scoring identity data.
- Duplicates and ties produce one maximum raw score. New-mix-only and old-mix-only
  observations remain available. Source ordering does not change results.
- The source-aware note-count matrix is enforced. Phoenix 2-only charts are not
  excluded solely because they have no Phoenix 1 catalog row; unknown counts on
  cross-mix records cause explicit provisional status and anchor exclusion.
- Supplemented/submitted/below-20 rows cannot affect fitted costs, eligibility,
  anchors, intervals or estimates. Re-leveling follows the current catalog UUID.
- Different inter-mix dates are allowed; changing a date within one source,
  malformed input or incomplete capture cannot replace the last complete manifest.
- Merged counts above 300 never trigger a new Clearing cap or default.
- Synthetic connected charts with known player abilities recover the expected
  chart-cost differences up to an additive constant. Disconnected graphs cannot
  acquire a spurious shared calibration.
- Both mode curves invert monotonically and continuously, and every declared
  median-cost anchor maps to level + 0.5 within 1e-6 before display rounding.
  Unsupported/skipped folders, equal/reversed medians, no anchors, negative raw
  effects, and both tails have explicit deterministic outcomes.
- Two anchors do not trigger an unidentified exponential fit. Adding a common
  constant to all fitted chart costs, anchors and offset leaves the calibrated
  ratings, fitted growth, and tail slopes unchanged. Positive rescaling of all
  costs leaves ratings unchanged as well.
- The inverse of an aggregated cost is used; no test asserts the false nonlinear
  mean-of-personal-ratings property. Conditional intervals use the same inverse and
  contain finite ordered endpoints where available.
- On a pinned baseline, Clearing values/history/reference/cap fields, Co-op and
  player skills plus recommendation inputs, methods, fitted parameters and
  predictions remain unchanged. Compare semantic content separately from bundle
  generation IDs, paths and timestamps. Rankings shared with Scoring and derived
  Pumbility may change only as explicitly intended.
- Public payload contains no raw identities; API/frontend accept new valid
  artifacts, accept the explicitly supported legacy contract during migration,
  and reject unsupported contracts or malformed calibration data. A publication
  pointer rollback actually renders the legacy generation correctly.

Verification commands include focused Python tests for capture/merge/model/curve,
existing official Clearing/runtime regression tests, `npm run test:frontend`,
`npm run typecheck`, and `npm run build` for frontend changes, plus a local API/UI
smoke check of Scoring, Clearing, Pumbility, Unrated and extrapolated states.

The candidate report records source coverage and match ambiguity, note-count flags,
reference membership, monotonicity, supported ranges, biggest rating changes,
all six user-selected charts, held-out errors by mode/level, and build time/memory.
Freeze the held-out split and model choices before comparing results. Reproduce
the deterministic full-capture method, not exact exploratory rounded estimates
after changing anchor exclusions. Report sensitivity to source truncation and
changed-chart observations without tuning individual chart results.

Stop once these acceptance checks pass. Perform at most one general review of the
implementation, repair only demonstrated regressions, rerun affected checks, and
stop without another general review. For this planning request, perform the single
requested parallel review of the draft, revise it once, and present the resulting
plan and any unresolved modeling/product choices. No production implementation or
deployment is part of the current request.

## Requested review and revisions

One parallel review pass was completed by three subagents covering mathematics,
source/identity contracts, and repository integration. No production files were
edited by reviewers and no second review pass is planned.

| Review finding | Incorporated resolution |
|---|---|
| Exponential curvature is unidentified with only two anchors; offset selection and tails were underspecified | Require three anchors; normalized fixed-grid fitting and deterministic tie breaking; explicit tangent tails and translation/scale-invariance tests |
| Catalog-only comparability could exclude Phoenix 2-only charts; unknown-count status and ID/tag checks were incomplete | Source-aware comparability matrix; explicit unknown-count provisional status; bidirectional identity checks; identity-retaining private captures |
| Shared row evidence could make merged Scoring counts alter Clearing | New metric-specific Scoring evidence/counts; retain Phoenix 2 Clearing evidence semantics |
| Previous-generation retention alone could not roll back across exact schema validators | Reader-first legacy/new compatibility, version-specific validators and a tested pointer rollback |
| Publication coherence prevents byte-for-byte preservation of every recommendation artifact | Preserve recommendation calculations and content while allowing required publication metadata changes in the existing bundle |

Visible policy decisions for the final presentation: below-20 Singles/Doubles
Scoring and tier-list Pumbility are Unrated; Co-op and Clearing retain their
methods; anchors represent eligible reference-chart cost medians; empirical
anchors take priority over globally convex growth; extrapolations and flagged
charts are provisional; raw maxima are retained even when comparability is flagged.
