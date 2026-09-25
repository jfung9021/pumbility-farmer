# Clearing and Pumbility tier lists: implementation plan

Status: implemented locally on 2026-09-25 following the three-agent planning and reassessment passes. The implementation used three agents for calculation, UI, and artifact integration. The original implementation specification is retained below; completed verification is recorded at the end.

## Intended behavior

Add Scoring, Clearing, and **Pumbility** views to the existing tier-list page. Use the requested spelling “Pumbility” for the new list.

- Clearing uses the average existing player skill rating among a chart's successful players whose ratings fall inclusively between its 25th and 50th percentiles.
- The median clearing estimate in each Singles/Doubles official-level folder is `official level + 0.5`. Estimates can cross official levels: an S20 can be 19.2.
- Pumbility is the arithmetic mean of the chart's scoring and clearing estimates.

Recommended initial scope is the current published Singles/Doubles catalog (level 16+). Co-op remains available under Scoring: it has player-count folders and a separate difficulty scale, and the existing skill conversion does not provide an equivalent Co-op skill rating. Skill calculation continues using the player's complete eligible mode history, including levels below 16.

The subsequent user-requested release update corrects the name to Pumbility and gives Phoenix 1 and Phoenix 2 equal source weight in scoring tiers and their What-if estimates. Clearing already averages players equally; Pumbility inherits the updated scoring component. Recommendation score projections retain their separate source weights. Existing scoring-preservation requirements below apply apart from this explicitly requested weighting change.

## 1. Specify the calculation

### Player skill and clear membership

Use the existing calculated mode-specific **`scoringRating`**, reconstructed at full precision from the selected source's top 20 Pumbility scores. The prominent rating stat in the current recommendation UI is a Top 50 Pumbility total; it is not the input to this calculation. Reuse the existing source policy:

1. Phoenix 2 when the player has at least 20 valid scores in that mode.
2. Otherwise, normalized Phoenix 1 when it has at least 20 valid scores in that mode.
3. Otherwise, the available Phoenix 2 scores, even when fewer than 20.
4. Otherwise, skill is unavailable.

Relevant functions in `piu_recommendations.py`: `_select_rating_scores`, `_rating_window`, `_skill_rating_from_rows`, `_prepare_phoenix1_rating_frames_from_frames`, and `build_player_recommendation`. Extract a shared helper for this policy so the bulk tier calculation and existing recommendation `scoringRating` agree. Keep full precision internally and preserve existing output/display rounding.

The scoring model's `playerAbility` is different: it uses source-specific, leave-one-out ranks 11–30. Reuse the existing `scoringRating` calculation for the requested player skill input.

For every current-catalog chart, collect distinct `(playerId, chartId)` pairs from available nonbroken records in either Phoenix source. Retain only source/chart mappings compatible with the current chart's mode. A user clearing in both versions contributes once; a failed Phoenix 2 record must not erase a Phoenix 1 clear. Source precedence can select provenance, but must not weight a person twice.

Collect membership from the sanitized raw snapshots **before** positive-Pumbility filtering, contribution-window selection, or destructive snapshot consumption. `sanitize_score` retains finite zero-Pumbility nonbroken rows, while `_clean_snapshot_frames` drops them. A zero-point clear still counts if that player has a usable skill rating from other history. `isBroken` is the existing failure indicator; plate/grade is not a substitute clear predicate.

Join clear membership to the matching Singles or Doubles skill. Use the current skill at analysis time, not an inferred skill at the time of the clear. Do not impose the scoring model's 50-score player minimum, top/recent contribution windows, source weights, or ability weights on this new average.

### Inclusive percentile average

For a chart, let `R` contain one finite skill rating per rated clearer:

```text
q25, q50 = quantile(R, [0.25, 0.50], method="linear")
selected = every r in R satisfying q25 <= r <= q50
chartClearSkill = arithmetic_mean(selected)
```

Use NumPy's explicit linear interpolation convention. Include every boundary tie, even if this makes the selected group larger than 25% of the players. Do not average the two percentile cutoffs or interpolate fictional players into the mean.

If no rated clearers exist, or the interval contains no actual rating, return an unrated/null estimate. The latter can occur with two distinct ratings. Do not silently widen the interval.

### Calibration to difficulty

Within each exact mode/official-level folder, calculate:

```text
folderReference = median(all finite chartClearSkill values in that folder)
clearingDifficulty = officialLevel + 0.5 + chartClearSkill - folderReference
clearingDelta = clearingDifficulty - (officialLevel + 0.5)
```

One skill-rating unit maps to one difficulty unit. This is a proposed v1 calibration choice, supported by the existing skill conversion already producing continuous level units. It is not an empirically fitted new scale.

This formula gives a folder median of `L + 0.5` within floating-point tolerance, including even chart counts, before serialization/display rounding. Example: an S20 folder reference of 21.0 and a chart clear-skill average of 19.7 produce `20.5 + 19.7 - 21.0 = 19.2`. Allow the existing six-decimal aggregate serialization precision in artifact checks; do not assert exact equality of floating-point values.

Use the same finite-chart population for calibration and the median invariant. Sparse but measurable charts remain in this population with an evidence warning. A folder containing only one measurable chart necessarily anchors that chart at `L + 0.5`.

Do not directly apply `apply_within_level_difficulty`: its negative sign, 0.4 factor, and evidence shrinkage belong to scoring residuals. Do not clamp clearing estimates to their official folder or to the catalog's level-16 publication floor.

### Pumbility

```text
pumbilityDifficulty = (scoringDifficulty + clearingDifficulty) / 2
pumbilityDelta = pumbilityDifficulty - (officialLevel + 0.5)
```

Calculate before rounding/truncating either component. If either component is unavailable, Pumbility is null. Do not recenter this average: the median of the averaged list need not equal the average of the two component medians. One-decimal display truncation can also change the median of displayed clearing numbers or make the average of displayed components differ slightly from the displayed Pumbility result; the invariants apply to the underlying estimates.

### Evidence and diagnostics

Store chart aggregates: total unique clearers, rated clearers, missing-skill count, selected-player count, q25/q50, chart clear-skill mean, folder reference, and metric version. Public payloads must not include player identities or individual skill samples.

Proposed clearing evidence labels, based on selected-player count: 10+ Published, 5–9 Provisional, 1–4 Insufficient, and no estimate Unrated. Pumbility takes the weaker component evidence status and retains both component support counts. These are explicit new policies; no fabricated composite contributor count or confidence interval.

Keep the current limited-data warning threshold of fewer than 20 contributors separate from those evidence labels. For Clearing, apply it to selected-player count. For Pumbility, warn when either scoring contributors or selected clearing players are below 20, and label their counts separately. A Published chart can still carry the existing limited-data warning. Evidence labels do not exclude finite estimates from calibration or the list.

## 2. Implement the backend and artifact contract

1. Add focused pure calculation helpers, preferably in a small new module such as `tier_difficulty.py`, for percentile selection, folder calibration, and metric arithmetic. Keep source preparation in `piu_recommendations.py` and reuse the existing effect-band/ranking behavior where applicable.
2. In `build_combined_chart_results`, capture clear membership before snapshots can be consumed. Build player-mode skills while the full Phoenix 2 frames and normalized Phoenix 1 rating frames are available. Compute once per player/mode, rather than calling the complete recommendation builder for every player or chart.
3. Attach the new estimates after existing scoring results are produced, before final serialization. Compute each new metric's folder rank/comparison count and effect bands required by the existing UI. Sort deterministically by the selected metric. Additional global ranks, within-folder percentiles, relative groups, or measured/published summary counts are not required unless a defined UI or validation consumer needs them. Keep support diagnostics and folder references needed to explain and verify calibration.
4. Retain the existing top-level `estimatedDifficulty`, scoring metadata, and `whatIfEstimates` meanings. Add separately typed metric objects, for example `tierMetrics.clearing` and `tierMetrics.pumbility`, containing their estimate, delta, rank, and evidence fields. Update explicit serialization field lists so the new objects survive export.
5. Extend `build_combined_tier_payload` with metric method descriptions, required support counts, folder references, and calibration version. Reuse the current `/api/tier-list` route and combined aggregate; no separate endpoints or public player data are necessary.
6. Bump the combined-tier schema and `SCRIPT_VERSION`, and synchronize Python producers, `lib/local-analysis.ts`, TypeScript interfaces, fixtures, and relevant smoke checks. Preserve existing recommendation schemas and `RECOMMENDATION_CHART_FIELDS` unless their serialized contract actually changes.

Integration files:

- `piu_recommendations.py`: source preparation, shared skill helper, combined results, payload serialization.
- `piu_misgrade_analyzer.py`: existing rank/effect-band helpers for reuse; scoring calculation retains its current behavior.
- `analysis_runtime.py`: all combined-analysis paths, including `consume_phoenix1_snapshot=True`, checkpoints, and publication.
- `scripts/build_local_recommendations.py`: local aggregate generation.
- `scripts/build_pumbility_supabase_model.py`, `scripts/populate_pumbility_production.py`: compatibility verification where affected by the schema change, not mandatory feature edits or a feature rollout path. The population script checks parity with an existing active generation; it does not publish changed calculations.
- `pumbility_contract.py`, `api/tier_list.py`, `app/api/tier-list/route.ts`: existing artifact path and serving contract; change only where required.
- `lib/types.ts`, `lib/local-analysis.ts`: metric types and payload validation.

Regenerate the combined aggregate from the existing private snapshots using the normal local/hosted analysis paths. No new upstream collection or database migration is required by this design. Existing typed storage/checkpoint serialization must preserve the added aggregate fields; public API handlers already pass the aggregate through.

Before a resumed generation can publish, ensure compatibility for both the combined-input checkpoint and the `combinedTier` embedded directly in later publication checkpoints. Checking only `_load_typed_checkpoint_combined` misses later `_resume_typed_analysis_checkpoint` phases. Reject/recompute incompatible generations, or explicitly start a fresh generation for rollout without resuming old checkpoints. Bumping `SCRIPT_VERSION` alone affects freshness and does not invalidate checkpoints.

Use normal analysis refresh and the existing atomic publication-generation mechanism for rollout. Stage compatible readers before exposing newly generated schema data. Production population, database migrations, and recommendation-model/schema changes are not deliverables for these two lists. Global tiers follow the current global refresh cycle; player-only refresh continues updating personal recommendations.

## 3. Add the three UI views

In `app/tier-list/page.tsx`, add a Scoring / Clearing / Pumbility selector with Scoring as the default. Reuse the existing layouts and mode controls.

- Store metric in `/tier-list?metric=clearing&mode=singles`. Extend `lib/page-view-state.ts` validation; preserve other query parameters and restore both metric and mode on Back/Forward.
- Add a typed selected-metric accessor so estimates, differences, sorting, estimated buckets, tier bands, folder ranks, evidence warnings, detailed cards, compact dialogs, and Unrated membership use the selected metric. Preserve official chart labels and compact jacket badges.
- Sort cards inside each tier band by the selected metric, with deterministic ties; the current band rendering otherwise inherits the incoming scoring order. Close the chart dialog when the metric changes, as for mode/history changes.
- Preserve official-level filtering: an S20 estimated at 19.2 remains in the official S20 filter but displays/groups at estimated 19.2.
- Continue using `lib/format-difficulty.ts` for one-decimal display truncation after all arithmetic.
- Clearing details show usable/total clearers, selected support, percentile limits, and the clearing estimate. Pumbility details show the two component estimates, their average, and separately labeled scoring/clearing support. Keep scoring contributor and P1/P2 source breakdowns in Scoring; do not present those counts as clearer counts in the new views.
- Show current confidence intervals and official-level What-if only in Scoring; they have no clearing/composite interpretation.
- Make headings and method explanations metric-specific. Use the requested label “Pumbility Tier List.”
- Keep Co-op selectable for Scoring. For Clearing/Pumbility, disable its mode control with a short explanation. A direct Co-op URL or a switch from Scoring Co-op into either new metric shows the same explicit unavailable state, with Singles/Doubles still selectable.
- Update `lib/demo-data.ts`, home-page tier-list copy, and CSS only as needed for the new selector and realistic examples.

Read applicable guides in the installed `node_modules/next/dist/docs/` before implementation. The UI investigation checked the existing client-page/native-history approach against the installed linking/navigation guide.

## 4. Focused verification and acceptance

Add algorithm tests covering:

- Inclusive percentile boundaries, ties, empty data, and two-distinct-player empty selection.
- One contribution per player across versions; failed rows excluded without erasing a clear in the other version.
- A valid zero-Pumbility clear by a player with an independently available skill rating.
- A clearer outside scoring contribution windows and below scoring eligibility thresholds still contributing.
- Bulk skill parity with the existing `scoringRating` computation, including Phoenix 1 normalization/fallback, partial Phoenix 2 histories, and independent S/D ratings.
- Separate mode/folder calibration, odd and even chart counts with S20 median 20.5, and an S20 estimate of 19.2.
- Arithmetic mean before display truncation, missing-component nulls, and weaker-component evidence.
- Unchanged existing scoring estimates, scoring What-if behavior, and recommendation semantics.

Add artifact/API tests for normal and consuming build paths, preservation through actual publication/checkpoint serializers, old-checkpoint handling, version validation, nulls, and absence of private player fields. Cover a checkpoint already past model fitting, so an initial-loader-only compatibility check cannot appear sufficient. If rollout relies on a fresh generation instead of adding resume compatibility code, explicitly verify that no old checkpoint is resumed. Core suites are `tests/test_recommendations.py` and `tests/test_analysis_runtime.py`; run population-tool, API cold-start, or API latency tests only when the corresponding contract checks or serving dependencies change.

Use the existing Node test runner for behavioral tests of the pure metric accessor/grouping helper and URL parser. Cover selected-metric sorting within buckets and bands, evidence/null handling, and preservation of official chart labels. Update only affected existing source assertions for the selector and Scoring-only What-if. The browser pass covers actual control/dialog behavior; a new component-test framework is unnecessary. Run the affected Python suites plus `npm run test:frontend`, `npm run typecheck`, and `npm run build`.

Perform one browser verification of all three views for Singles/Doubles, mobile controls, compact/detailed layouts, dialogs, URL history, an S20/19.2 example, unavailable data, and existing Scoring Co-op. On a rebuilt snapshot, check finite-folder median invariants and composite equality before display rounding with tolerances appropriate to calculation/serialization; report coverage and selected support counts.

Stop after these acceptance checks pass. Perform at most one general review, with one focused repair and only affected re-verification if that review finds a proven regression.

## Findings and limits

- There is no identified implementation blocker for this proposed Singles/Doubles scope.
- The population is successful records available to the app through its existing snapshots. It is not every PIU player or every lifetime clear. Players without a usable calculated skill cannot enter a numeric average and must be counted as missing skill.
- This measures the skill distribution of observed successful players, not pass probability or first-clear ability. Present it as an estimated clearing difficulty.
- The pre-existing combined schema 9/8 producer/reader mismatch was resolved by synchronizing the feature contract at schema 10. The affected population-test fixture and deployment smoke expectations were aligned with the new contract.
- The user-requested reassessment corrected the skill-field wording, preserved official badges, clarified metric-specific warning/count behavior and transitions, covered later checkpoint resume phases, and removed mandatory unused metadata and migration-tool work. The formula and Singles/Doubles scope were retained.
- Planning and reassessment changed only this document. The subsequent authorized implementation added the calculation, UI, contract changes, and focused tests.

## Completed implementation verification

- 199 Python tests passed: 109 calculation/recommendation tests, 81 runtime tests, and 9 population-contract tests.
- 63 frontend tests, TypeScript checking, the production build, and deployment smoke-script syntax checking passed.
- Normal local refresh rebuilt the combined aggregate and recommendations for 831 named Phoenix 2 players from the existing private snapshots.
- The public aggregate contains 2,712 charts, including 2,559 finite clearing estimates and 2,558 finite Pumbility estimates. All 23 measurable S/D folders satisfy the median anchor; S20 and D20 both have median 20.5. There are 375 clearing estimates below the official level.
- Before the subsequent equal-source-weighting request, all 98,752 existing chart fields compared against the previous code's results on the same snapshots were unchanged. Clearing/composite arithmetic, support counts, null propagation, and aggregate privacy checks passed.
- The production page and local API returned HTTP 200. The API served the rebuilt schema-10 aggregate with matching JSON values and no-store caching.
- One bounded integration review found an outdated schema fixture; it was corrected and the affected tests passed. No further general review was performed.
- Visual browser verification could not run because the available browser-control service reported no connected browsers or apps. No hosted deployment was performed.
