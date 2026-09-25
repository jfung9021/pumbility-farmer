# Separate player skills and restore combined scoring analysis

Status: implemented locally, updated September 26, 2026 to use the entire top 50 for clearing skill and the single linearly interpolated 10th percentile for chart Clearing difficulty. Production has not been changed. Historical verification results appear below.

Keep the existing player skill calculation as **Scoring skill**. Add **Clearing skill**: require at least **50 unique successful clears in a mode**, then average the difficulties of the **50 charts ranked 1-50 inclusive**, ordered hardest first. Calculate Singles and Doubles independently. Use this rating for the Clearing tier list. Restore combined Phoenix 1/Phoenix 2 scoring analysis across legacy and new charts.

## 1. Confirmed requirements and calculation assumptions

- Existing player scoring skill keeps its formula and source-selection policy.
- Require 50 unique clears separately in Singles and Doubles. A player with 50 Singles clears and 49 Doubles clears has a Singles clearing rating only.
- Average the entire top 50. Require all 50 unique clears in the mode; do not use a partial average.
- Count valid clears from both Phoenix sources, deduplicating chart IDs across sources and repeat plays.
- Legacy and new Phoenix 2 charts share the same scoring analysis within each mode. Singles and Doubles remain separate.
- The chart-level Clearing calculation uses the single linearly interpolated 10th percentile of eligible clearers' skill ratings, without averaging a percentile range. There is no difficulty reassessment.

**Implemented difficulty basis:** use the chart's current official Phoenix 2 level. An S22 or D22 contributes `22`, without adding `0.5` or applying a Pumbility conversion. The implementation follows the official-level default in the reviewed plan.

Official levels give a deterministic calculation from stored clears and the catalog. Proposed scoring difficulty would require a scoring-estimate dependency and a policy for unrated charts. Proposed clearing difficulty would create a feedback loop with the Clearing tier list and require a separate initialization/iteration design. Neither alternative is silently included here.

## 2. Restore the combined scoring model

Treat "revert separating" as restoring the pre-separation combined scoring estimator, still used by recommendation model charts. Do not average the outputs of two independently fitted models.

Use complete current-catalog histories within each mode for player baselines, contribution selection, source calibration, ability weighting, shrinkage, official-folder references, confidence intervals, ranks, and What-if estimates. New Phoenix 2 charts participate alongside legacy charts; their available evidence naturally comes from Phoenix 2.

Preserve the existing source-specific mechanics before combining evidence:

- Phoenix 1 note-count/Pumbility normalization and current-catalog rerates; Phoenix 2's existing normalization.
- Existing 30-score Phoenix 1 and 50-score Phoenix 2 scoring-history requirements, counted across the whole mode rather than a chart-origin subset.
- Existing ranks 11-30 scoring baselines, contribution windows, leave-one-chart-out scoring ability, and source precedence for overlapping player/chart evidence.
- Equal source weights of 1; do not restore the former Phoenix 2 double weight.

The new 50-unique-clear requirement belongs only to clearing skill. It must not replace scoring eligibility or the public scoring-skill source policy. Combining chart populations does not mean combining raw Pumbility values across versions without normalization.

**Calibration choice for this rollback:** restore the original combined estimator's residual centering and shrinkage, including its original What-if behavior. Remove the separate-model experiment's extra final-median offsets along with its origin partition. The original model uses `official level + 0.5` as its reference, but unequal shrinkage can make the final folder median differ slightly from that reference. An additional exact-final-median constraint on the combined scorer would be a distinct formula change, not part of this rollback. Clearing retains its existing folder-median calibration.

Implementation coverage:

1. Remove the origin partition from the tier-scoring path in `piu_recommendations.py`, restoring unpartitioned scoring and complete-mode/folder ranks. Remove origin-specific What-if references and centering offsets.
2. Update every caller: `scripts/build_local_recommendations.py`, checkpoint/build/recovery paths in `analysis_runtime.py`, `scripts/build_pumbility_supabase_model.py`, and `scripts/populate_pumbility_production.py`. Avoid fitting identical scoring models twice where their restored shared result can be safely reused.
3. Remove active origin-specific payload fields, diagnostics, local validation requirements, frontend labels/rank wording, and methodology. Replace the cohort method identity with an explicit combined-scoring identity; do not relabel old separated results.
4. Remove the origin-reference staging dependency and retire `scoring_tier_cohorts.py`/`data/phoenix1-chart-origins.json` if no remaining consumers need them. Retain unrelated reference inputs and useful shared scoring helpers.
5. Replace origin-independence tests with combined-model regression tests. Preserve the normal two-source `--tiers-only` path. The explicit Phoenix 2-only experiment must not become the default.

The historical [separate-chart-origin plan](separate-chart-origin-scoring-plan.md) remains an implementation record marked as superseded. Do not revert unrelated local changes when removing the split.

## 3. Define the two player ratings

**Scoring skill:** retain `scoringRating` and its complete current behavior: selected-source top-20 Pumbility average converted to the mode-specific S + Fair Game equivalent level, using Phoenix 2 with at least 20 records, otherwise normalized Phoenix 1 with at least 20, otherwise the existing partial Phoenix 2 fallback. Preserve recommendation eligibility, candidate ceilings, projections, and internal scoring baselines.

**Clearing skill:** for one player and one mode:

```python
cleared_charts = unique_valid_successful_current_catalog_charts(player, mode)
ordered = sorted(cleared_charts, key=lambda chart: (-chart.current_level, chart.id))

if len(ordered) < 50:
    clearing_rating = None
else:
    selected = ordered[:50]  # ranks 1 through 50 inclusive: exactly 50 charts
    clearing_rating = sum(chart.current_level for chart in selected) / 50
```

Average levels directly with equal chart weights. Keep full precision for analysis and round for display only. Twenty level-22 charts plus thirty level-21 charts in the top 50 produce `21.4`.

This is a fixed rank window, not a percentile interval. Ties use chart ID for deterministic membership and do not expand the window beyond 50. Equal official levels mean the tie choice does not change the average. Clears below rank 50 do not enter the mean.

Calculate one Singles rating and one Doubles rating, pooling legacy and new charts within that mode. Do not introduce an Overall or Co-op clearing skill. Use the same player/mode rating for every chart that player cleared; no clearing leave-one-chart-out rule is requested. A target chart may count toward the 50 clears and the selected window, so qualification must not silently require 51 clears.

## 4. Build the clear population correctly

Reuse the global clear-membership rules in `_clearers_by_chart` in `piu_recommendations.py`:

- Successful, nonbroken records from either version count. Failures do not.
- Each chart ID counts once per player. A stored success survives a failure recorded in the other source.
- Include all available valid current-catalog clears, including charts below level 16. The tier page's display floor does not limit player history.
- Join current Phoenix 2 official levels, requiring compatible source/current chart modes. Exclude charts absent from that catalog because they cannot be assigned a current level under this definition.
- Preserve valid zero-Pumbility clears. Do not apply positive-Pumbility filters, scoring history minimums, top/recent scoring windows, grade requirements, or source weights to clearing skill.
- Reject malformed/nonfinite chart difficulty rather than substituting zero. Count otherwise-valid clears lacking usable chart mapping separately where observable.

The snapshots contain stored best-score records, not an exhaustive attempt log. "All clears" means all valid unique successful charts represented in the available inputs.

Invert `chart -> set(players)` into `player/mode -> set(chart IDs)` once per global run. Join levels and calculate the fixed window. Reuse this membership for tier clear counts, skill qualification, and personal views.

## 5. Connect clearing skill to the tier lists

For each chart, use the new mode-specific clearing skills of its unique successful players:

```text
chart_clear_skill = linear_quantile(finite_clearer_clearing_skill_ratings, 0.10)

clearing_difficulty = official_level + 0.5
                    + 0.65 * (chart_clear_skill
                              - median_chart_clear_skill_in_official_folder)

pumbility_difficulty = (scoring_difficulty + clearing_difficulty) / 2
```

Sort the eligible skill values and interpolate at zero-based position `0.10 * (n - 1)`. A singleton returns its own rating; an empty eligible population remains unrated. The percentile value can lie between two players' ratings. This chart statistic is distinct from the player's exactly-50-chart mean.

Use the user-selected fixed Clearing spread multiplier `0.65`. On the September 26 cached population, 12 charts across Singles/Doubles and both directions combined have proposals `>= official level + 2` or `< official level - 1`. The original 10-chart target yielded a cutoff near `0.6348`, rounded to `0.63` with 9 charts; the subsequent request for `0.65` supersedes that target. Future data can change the count; the scale is not automatically refitted on each run.

Players with fewer than 50 valid unique clears still contribute to `clearCount`, but not to the skill percentile sample. Include them in `missingSkillCount`; `ratedClearCount` means clearers with available clearing skill. Do not substitute scoring skill.

Retain whole-mode/official-level Clearing calibration, evidence thresholds, limited-data labels, and cross-level estimates. Evidence counts the entire eligible percentile population (`ratedClearCount`): Published at 10+, Provisional at 5-9, Insufficient at 1-4, and limited data below 20. Pumbility's clearing support uses the same population. Do not reassess charts into another folder. Recompute Pumbility from restored combined scoring estimates and new clearing estimates, preserving null propagation and weaker-component evidence. Do not recenter Pumbility separately.

## 6. Share the calculation across analysis and player views

Add a shared helper, such as `player_skill_ratings.py`, for the clearing method definition, rank-window calculation, and reusable membership preparation. Provide bulk and single-player paths using the same pure calculation. Keep scoring-skill helpers unchanged.

Suggested result per player/mode:

```text
clearingRating: number | null
clearingSkill:
  methodVersion: 2
  difficultyBasis: current-official-level
  ranks: [1, 50]
  requiredClearCount: 50
  uniqueClearCount: number
  selectedCount: 50 when rated, otherwise 0
  status: rated | insufficient-clears
```

Implementation points:

1. Calculate memberships/skills before destructive snapshot consumption. Supply the new lookup to `build_tier_metrics` through an explicitly named clearing-skill argument.
2. Stop using the bulk top-20 scoring-skill lookup as Clearing's input. Keep helpers needed for recommendations and scoring skill.
3. Extend `build_player_recommendation` and local player generation. Compute clearing availability independently of scoring eligibility, including branches without recommendations.
4. Extend hosted model generation and player refresh in `recommendation_refresh.py`. Prepared rating frames discard zero-Pumbility records; compact Phoenix 1 `plateScores` omit Pumbility. Neither alone reconstructs the full valid clear set.
5. Capture private `clearedChartIds` from validated raw Phoenix 1 records before normalization/filtering or consumption, checking source/current mode compatibility. Retain equivalent membership from Phoenix 2 raw successful records. Pass these sets to the helper. Do not re-sanitize incomplete compact plate rows as full raw score records.
6. Preserve membership through private shards, model loading, cache materialization, and refresh. Rebuild old inputs lacking membership from stored snapshots instead of interpreting a missing field as zero clears.
7. Player refresh can update personal skill against the generation's catalog and newly stored clears. Global tiers update during global reanalysis. Preserve timestamps to distinguish those observation times.

Chart IDs and membership remain private model inputs. Public responses need ratings and method/count metadata; public tier aggregates must not expose player identities or selected histories.

## 7. UI and artifact contracts

Display **Scoring skill** and **Clearing skill** on Singles and Doubles player views, separately from Pumbility total/title progress. Use mode prefixes where helpful, such as `S22.4` and `D23.1`.

For insufficient history, show "28/50 unique clears; 22 more needed." Clearing availability must not disable scoring-qualified recommendations. Manual-rating views have no history and must not invent clearing ratings. Do not add an Overall clearing value.

Explain both stages in tier methodology: player ranks 1-50, requiring 50 unique clears in that mode; then the chart's single 10th percentile of eligible player clearing skills, using linear interpolation. Replace "usable skill" with "available clearing skill (50+ unique clears in this mode)." Explain combined scoring and remove origin-specific ranking labels.

Update `lib/types.ts`, `lib/local-recommendations.ts`, `lib/local-analysis.ts`, relevant UI pages, and response/cache projection allowlists. Preserve `scoringRating` rather than renaming or repurposing it. Use `q10Skill` and scalar percentile metadata, without legacy selected-mean or percentile-range fields. Retain UI support for explicitly older range-average payloads without relabeling their values as the new percentile.

Version the clearing-skill method, tier metric method, combined-tier schema, and script. Version recommendation response/model and private shard contracts where changed fields require it; do not bump unrelated contracts. Use a new monotonic combined-tier schema/method identity rather than restoring an older number, since Clearing is also changing.

Replace origin-reference checkpoint validation with combined-scoring/new-clearing-method compatibility checks. Cover early combined-tier checkpoints, later embedded `combinedTier`, model inputs, and player caches. Rebuild incompatible artifacts from stored snapshots. Old player responses should refresh or show "not yet calculated," never scoring skill under the clearing label. Deploy compatible readers before regenerated artifacts during a later authorized rollout.

## 8. Verification and rollout

Cached local snapshots contain 850 Singles players and 808 Doubles players with at least one valid current-catalog clear. Under the 50-clear requirement, **740 Singles players and 604 Doubles players** qualify. The other 110 Singles and 204 Doubles players have fewer than 50. These are preliminary skill-availability counts, not predictions of final chart coverage.

Required checks:

- Exactly ranks 1-50 are averaged for 50+ clears. Test 0, 10, 11, 29, 30, 49, 50, and 51 clears, plus a larger history. Forty-nine clears remains unrated. Cutoff ties still select exactly 50.
- Fifty Singles and 49 Doubles clears qualifies only for Singles. Fifty source records representing 25 unique IDs does not qualify. Legacy/new charts both contribute within their mode.
- Exclude failures, deduplicate sources/repeats, preserve success across a source failure, and include zero-Pumbility/sub-16 clears. Use current rerates consistently. Do not exclude the target chart from clearing skill.
- Changing score/grade/Pumbility on an already-cleared chart leaves clearing skill unchanged while successful membership and official levels stay fixed.
- Bulk, local player, hosted refresh, and consuming/non-consuming calculations agree. Membership and metadata survive serialization/mode projection. Clearing-qualified players can have a rating without scoring recommendations.
- Combined scoring estimates, intervals, ranks, support counts, and What-if values match the saved pre-separation combined baseline on identical inputs. They are expected to differ from separated local results. Exclude intentionally changed Clearing/Pumbility metrics from this equality check.
- Existing player scoring skill, recommendation ranking/projections, and Co-op remain numerically unchanged. Scoring pools chart-origin histories while retaining source normalization, precedence, and equal weights.
- Clearing uses only clearing skill and the single interpolated 10th percentile, original-folder calibration, and empty/unrated behavior. Pumbility equals the unrounded component average with current null/evidence rules. Evidence counts the full eligible percentile population.
- Old separated/old-clearing-method checkpoints cannot publish as the new method. Verify skill labels, unavailable states, combined rank labels, and cache transitions.

Implementation sequence:

1. Restore combined scoring and add the shared clearing helper, preserving scoring skill and recommendation behavior.
2. Wire global tiers, private model inputs, personal generation/refresh, and artifact compatibility.
3. Update UI/types/methodology and focused regression tests.
4. Rebuild tiers from both cached snapshots and save scoring/Clearing/Pumbility/coverage comparisons. The saved pre-separation baseline is `.local-data/piu-scores/verification/phoenix2-only-2026-09-26/combined-before.json`; compare only outputs whose inputs match it.
5. Regenerate local personal/model artifacts to verify display and refresh parity. Recommendation numbers should match, allowing new metadata/schema and timestamps.
6. Run relevant Python/frontend checks and the build, then one general implementation review. Repair only proven regressions and rerun affected checks.

No fresh PIUScores capture is needed. A later authorized rollout should deploy compatible readers and regenerate tiers plus player/model artifacts from stored snapshots. Tier-only reanalysis cannot populate personal clearing ratings. The subsequent implementation request authorizes the local implementation and cached-data validation. Deployment is not part of this step.

## 9. One-pass plan review

Reviewed the revised requirements against the current scoring/tier builders, caller/checkpoint paths, clear-membership calculation, hosted player inputs, and frontend contracts. The revised plan covers these issues:

- Qualification and averaging both require 50 unique clears; counts, windows, examples, unavailable states, and tests now agree. Modes qualify independently.
- Removing the origin split includes fitted inputs, calibration, ranks, What-if references, staging, and checkpoint validation, not just chart labels. The comparison baseline is the original combined scorer, not the separated local output.
- Source normalization and equal weighting survive the rollback. The new clearing requirement does not change scoring history eligibility or scoring skill.
- Valid zero-Pumbility clears must survive private input compaction and hosted refresh. Existing positive-Pumbility frames and compact plate-only rows are insufficient to reconstruct that population.
- Personal ratings need player/model regeneration as well as global tier reanalysis. Old schemas/checkpoints must not publish stale calculations under the new method.
- No extra clearing leave-one-chart-out rule, reassessment, partial-window average, origin split, Overall skill, or composite recentering is included.

No further implementation dependency was identified in this review. The official-level difficulty basis remains a recommended assumption; restoring the original combined calibration is the explicit interpretation of the requested rollback. No application code, generated analysis, or production state was changed during this plan update.


## 10. Initial local implementation verification (before the top-50/lower-half update)

Implemented with three subagents covering the shared model, hosted player inputs/runtime compatibility, and UI/contracts. The original combined scorer is restored; clearing skill uses current official levels at ranks 11-30 after 50 unique clears per mode. Old origin modules/reference dependencies are retired. Private clear membership survives hosted compaction; stale model/checkpoint contracts cannot publish as the new method.

- 240 relevant Python tests and 66 frontend tests passed; TypeScript and production build passed.
- Rebuilt both tiers and all 936 personal records from cached Phoenix snapshots, without an API capture.
- All 2,746 chart records match the saved original combined scorer outside intentionally changed tier metrics, including intervals, ranks, What-if estimates, and Co-op.
- Existing recommendation fields are unchanged in all four modes for each of the 936 players. Clearing skill is available for 740 Singles players and 604 Doubles players.
- Rated Clearing/Pumbility coverage: 1,285 Singles charts and 1,314 Doubles charts. All 23 measured Clearing folder medians remain official level + 0.5; Pumbility equals the component mean within serialization tolerance.
- One general review found no additional critical issue. Restarted the local server's older loaded bundle; the current tier API, Singles/Doubles player APIs, and both page routes pass.
- Local verification artifacts: `.local-data/piu-scores/verification/separate-skills-2026-09-26/verification.json`, `player-verification.json`, and `chart-comparison.csv`.

Production was not changed. Existing pandas deprecation warnings were not acted on.


## 11. Historical top-50 and 0th-50th percentile update verification

Clearing skill now averages the 50 hardest unique current-official-level clears per mode, with 50 required and no partial averages. Clearing tiers average eligible clearing skills from the minimum (0th percentile) through the median inclusively, retaining boundary ties. Output uses q0Skill/q50Skill; earlier q10 payloads are treated as historical. Scoring and its ranks 11-30 baselines remain unchanged.

- Clearing skill method 2, tier metric method 7, combined schema 17, recommendation schema 28. Private membership/model shape is unchanged.
- 217 relevant Python tests passed for the top-50 change; all 206 affected Python tests passed again after the percentile change. All 66 frontend tests and the production build (including TypeScript) passed.
- Independently verified all 1,872 S/D player skill results against the top-50 official-level mean. Eligibility remains 740 Singles / 604 Doubles players. Existing fields in all four recommendation modes remain unchanged for 936 players.
- All 2,746 scoring/Co-op chart records match the preceding combined results. Clearing and Pumbility cover 1,285 Singles / 1,316 Doubles charts. All 24 measurable Clearing folders retain median level + 0.5.
- One general review found no additional critical issue. Local tier/player APIs and page routes passed after the server restart. No fresh data was pulled and production was not changed.
- Results and chart comparison: `.local-data/piu-scores/verification/clearing-top50-2026-09-26/verification.json` and `chart-comparison.csv`.

## 12. Historical single 20th-percentile update verification

Clearing tiers now use the single linearly interpolated 20th-percentile clearing skill, stored as `q20Skill`. The percentile population is all finite eligible clearers; evidence and Pumbility clearing support count that population. Player clearing skill remains the top-50 official-level mean, requiring 50 unique clears separately in Singles and Doubles. Folder calibration and combined scoring are unchanged.

- Tier metric method 8, combined schema 18, script `6.13.3-clearing-top50-q20`. Player skill method 2 and recommendation schema 28 remain unchanged.
- All 207 relevant Python tests and 67 frontend tests passed; production build including TypeScript passed.
- Rebuilt local tiers from both cached snapshots, without fetching data or regenerating recommendations.
- Independently reconstructed top-50 skills and manually interpolated the 20th percentile for all 2,606 S/D charts. All estimates, eligible-player counts, evidence labels, and Pumbility averages agree with the exported results.
- All 24 measurable folders retain median Clearing difficulty of official level + 0.5. Rated Clearing/Pumbility coverage remains 1,285 Singles and 1,316 Doubles charts.
- All 2,746 scoring/Co-op chart records and the recommendation index are unchanged. The existing player generation is retained.
- One general review found no critical issue. The local tier API serves schema 18/method 8, both mode-specific player APIs preserve top-50 skills, and tier/recommendation page routes return 200 after restarting the local server.
- Results and comparison: `.local-data/piu-scores/verification/clearing-q20-2026-09-26/verification.json` and `chart-comparison.csv`.

Production was not changed. Historical demo data keeps its original percentile-range labels; existing line-ending notices were not acted on.

## 13. Single 10th-percentile update verified

Clearing tiers now use the single linearly interpolated 10th-percentile clearing skill (`q10Skill`), with no percentile-range mean. Player clearing skill remains the top-50 official-level mean. The UI distinguishes the current point percentile from older 10th-50th/10th-30th averages and the previous 20th-percentile value. Tier band ordering by signed difference remains in place.

- Tier metric method 9, combined schema 19, script `6.13.4-clearing-top50-q10`. Player skill method 2 and recommendation schema 28 remain unchanged.
- All 207 relevant Python tests and 67 frontend tests passed; the production build including TypeScript passed. One general review found no critical issue.
- Rebuilt local Clearing and Pumbility tiers using both cached snapshots without fetching data. Independently reconstructed the percentile and calibration for all 2,606 Singles/Doubles charts.
- All 24 measured folders retain median Clearing difficulty of official level + 0.5. Rated coverage remains 1,285 Singles and 1,316 Doubles charts.
- All 2,746 scoring/Co-op chart records and the recommendation index are unchanged; the existing player generation remains active.
- After restarting the local server, the tier API serves schema 19/method 9/percentile 0.10. All Singles/Doubles charts resolve to the 10th-percentile label; Clearing and Pumbility page routes return 200.
- Results: `.local-data/piu-scores/verification/clearing-q10-2026-09-26/verification.json` and `chart-comparison.csv`.

Production was not changed. Existing module-type and line-ending notices were not acted on.

## 14. Fixed Clearing spread 0.65 verified

Applied the final user-selected `0.65` multiplier to the difference between each chart's 10th-percentile skill and its official-folder reference. Pumbility uses the updated Clearing component. Metadata and the methodology footer show the active scale; older payloads retain their original scale of 1.

- Tier metric method 10, combined schema 20, script `6.13.5-clearing-top50-q10-scale065`. Player skill and recommendation contracts remain unchanged.
- The broader 207 Python checks passed for the spread implementation. After the final 0.65 adjustment, all 121 affected Python tests passed. All 67 frontend tests and the build including TypeScript passed for the unchanged frontend implementation.
- Rebuilt local tiers from cached snapshots. Independently verified all 2,606 S/D percentile/calibration results and Pumbility averages, plus all 24 measured folder medians at official level + 0.5.
- The exported proposals contain 12 two-grade moves: 5 higher and 7 lower; 5 Singles and 7 Doubles. The original unscaled population had 121. The initially requested cap of 10 would use approximately 0.6348, rounded to 0.63, giving 9; the later explicit request for 0.65 supersedes it.
- All 2,746 scoring/Co-op records and the recommendation index are unchanged. One general review found no critical issue; the subsequent scale-only adjustment passed focused verification.
- Restarted the local app; the tier API reports scale 0.65/schema 20/method 10, and Clearing/Pumbility pages return 200.
- Results: `.local-data/piu-scores/verification/clearing-scale065-2026-09-26/verification.json` and `chart-comparison.csv`.

Production was not changed. Existing pandas deprecation and line-ending notices were not acted on.
