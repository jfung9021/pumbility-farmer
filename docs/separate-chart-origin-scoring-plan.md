# Separate scoring models for legacy and Phoenix 2 charts

Status: superseded by the [combined scoring and separate player skills plan](separate-scoring-clearing-skill-plan.md), September 26, 2026. The user requested a return to combined chart analysis. The split below was implemented and verified locally, then removed by the combined-scoring implementation; production never received it. This document is a historical implementation record, not the current specification.

The scoring tier list will use two independent chart populations, with the same calculation rules. Within each population, mode, and current official level, the median final scoring estimate will equal `level + 0.5`. For example, legacy D23 charts and new Phoenix 2 D23 charts will each have a median of 23.5.

## 1. Define chart origin separately from score source

Classify charts by stable chart ID against a fixed, complete Phoenix 1 catalog:

| Chart population | Membership | Eligible score sources |
| --- | --- | --- |
| Legacy | Current Phoenix 2 chart ID present in the Phoenix 1 reference catalog | Phoenix 1 and Phoenix 2 |
| New Phoenix 2 | Current Phoenix 2 chart ID absent from that reference catalog | Phoenix 2 |

A Phoenix 2 score on a legacy chart remains legacy-chart evidence. A new step chart for an old song belongs to the new group. A rerated legacy chart stays legacy and uses its current Phoenix 2 official level. Song names, observed score dates, and whether anybody has a Phoenix 1 score must not determine membership.

Persist a versioned chart-origin reference containing the complete Phoenix 1 ID set and its provenance/hash. Use the same reference locally and in production; do not infer membership from whichever source snapshot happens to contain scores in a run. Include charts below level 16 because the scoring baselines use them. Audit unmatched IDs before adopting the reference, and record explicit corrections only for verified identity changes. A missing reference is an analysis error, not an empty legacy population.

The current local catalogs produce these candidate populations:

| Mode | Legacy charts, all levels | New charts, all levels | Legacy charts, level 16+ | New charts, level 16+ |
| --- | ---: | ---: | ---: | ---: |
| Singles | 2,568 | 184 | 1,185 | 100 |
| Doubles | 1,662 | 121 | 1,216 | 105 |

There are no same-ID mode changes or new-ID matches on exact song/mode/level in this comparison. Catalog membership is the available operational definition; the stored chart data has no release-version field.

## 2. Separate the scoring calculation from the start

Interpret “modelled totally separately” as independence of the complete scoring calculation, including player baselines. Partition catalogs and score histories **before** calculating eligibility or fitting any parameters.

For each `(chart origin, mode, score source)`, independently apply the existing rules:

- Positive-score filtering, current-catalog membership, Phoenix 1 note-count normalization, and rerate normalization.
- Player history requirements: 30 scores for Phoenix 1 and 50 for Phoenix 2, counted within that chart population and mode.
- Ranks 11–30 player baselines, existing top/recent contribution selection, and the current fallback contribution window.
- Score-controlled Pumbility-per-level calibration, fitted using only that population's observations.
- Source-specific leave-one-chart-out player ability and nearby-ability weighting, using only that population's history.

Combine the allowed sources within each population using the existing Phoenix 2 precedence and equal source weights of 1. Reuse the same robust chart statistics and 0.4 difficulty scale. Fit empirical-Bayes shrinkage independently for each `(origin, mode)`, with the existing fixed fallback when that population cannot estimate it. Keep large-folder compression disabled.

Neither group's scores may influence the other's baselines, contribution eligibility, slopes, ability weights, shrinkage, chart references, confidence intervals, or What-if calculations. Keep the documented Phoenix 2 precedence behavior when its qualifying record is outside the selected contribution window.

This changes the meaning of the internal scoring-model ability baseline to ability within the relevant chart population. It does not change the public recommendation skill rating or the skill rating used for Clearing.

## 3. Anchor the final estimates exactly

The current scoring calculation centers chart residuals before applying different reliability weights. That does not guarantee an exact median of `L + 0.5` for the final estimates, particularly for even chart counts. The restored local S26 scoring folder, for example, has a median about 0.0506 below 26.5.

For a chart `i` in group `g = (origin, mode, official level)`:

```text
r_i = robust chart residual in normalized level units
m_g = median of finite r_i values in g
w_i = effectiveSupport_i / (effectiveSupport_i + shrinkageK_origin_mode)
rawDelta_i = -0.4 * w_i * (r_i - m_g)
offset_g = median of finite rawDelta_i values in g

finalDelta_i = rawDelta_i - offset_g
scoringDifficulty_i = officialLevel_i + 0.5 + finalDelta_i
```

Apply the same final offset to both confidence-interval endpoints and recompute the exported deltas, bands, and ranks from the final estimates. These remain the existing conditional intervals, rather than a new estimate of calibration uncertainty.

Use every finite scoring estimate, including limited-data charts, for both the reference and median checks. Exclude null estimates. Empty groups remain unrated; a group with one measurable chart anchors it at `L + 0.5`. Preserve cross-level estimates and existing display truncation. Verify median equality before display rounding, allowing aggregate serialization tolerance.

This final centering belongs to the new tier scorer. Do not change the shared legacy formula for recommendations or standalone Phoenix analysis as a side effect.

## 4. Preserve the surrounding calculations

Clearing continues using unique clearers from both sources, the existing mode-specific skill/source policy, and the inclusive 10th–50th percentile average. Its calibration still uses the whole official-level folder. Do not apply chart-origin isolation or reassessment to Clearing.

Pumbility becomes `(new scoring estimate + unchanged clearing estimate) / 2`, before rounding. Preserve null propagation and weaker-component evidence. Its median is not separately forced to `L + 0.5`.

Co-op retains its current combined model. It has player-count folders and its own difficulty anchors, so the requested official-level midpoint rule applies to Singles and Doubles.

Recommendations retain the existing combined scoring model, player skills, projections, and ranking behavior. Currently, `build_combined_chart_results` supplies both public tiers and recommendation model charts; changing its default output would also change recommendations. Introduce an explicit tier-scoring entry point and keep the recommendation entry point on the existing calculation. Reuse source/statistics helpers with explicit inputs, without mutable global model switches.

## 5. Ranks, What-if, and presentation

Scoring folder ranks, percentiles, comparison counts, and relative groups use `(origin, mode, official level)`. Mode-wide scoring ranks use `(origin, mode)`. Recalculate these after final centering. Keep Clearing and Pumbility ranks on their existing complete official folders.

For scoring What-if estimates, freeze the target folder's reference and final centering offset within the chart's own origin and mode. Apply the existing hypothetical-level residual and ability-weight changes against those references. Never use another origin's reference when the target group is empty; return an unavailable estimate. Moving to another hypothetical official level does not change chart origin or trigger automatic reassessment.

Keep one tier-list page and its existing sorting/grouping controls. Add a chart-origin label in chart details and make scoring ranks explicit, such as “#3 of 15 new Phoenix 2 S20 charts.” Explain the two independent midpoints in the scoring methodology. A new page or additional filter control is not required for this implementation.

Add chart-origin and calibration diagnostics to the public aggregate: reference version/hash, per-origin source/eligible-player counts, measured chart counts, per-origin shrinkage, and per-folder centering offsets. Keep player identities and individual histories private. Retain whole-catalog summary totals alongside origin-specific summaries so existing consumers do not silently change meaning.

## 6. Implementation sequence

1. Add the chart-origin reference and a deterministic classifier, with the catalog audit above. Suggested new helper module: `scoring_tier_cohorts.py`.
2. Extract the reusable S/D scoring stages from `piu_recommendations.py` as needed, preserving the existing recommendation path's outputs. Add the separate tier entry point, origin-specific fits, final centering, ranks, and What-if references. Compute Clearing once from complete input histories and recompute Pumbility with the new scoring estimates.
3. Update `build_combined_tier_payload`, `lib/types.ts`, the local validator, and `app/tier-list/page.tsx` for the new origin fields and methodology. Version the combined-tier schema and scoring method. Update fixtures and relevant version consumers; recommendation schemas need no change unless their actual contract changes.
4. Add a normal `--tiers-only` local rebuild path in `scripts/build_local_recommendations.py`. It reads both cached snapshots, writes only the tier aggregate, and neither rebuilds nor prunes recommendation generations. Keep the previous Phoenix 2-only flag an explicit experiment, not the default.
5. Wire local and hosted analysis paths in `analysis_runtime.py` to keep tier charts separate from recommendation model charts/slopes. Preserve both outputs through typed and legacy checkpoints, including the embedded `combinedTier` in later publication phases. Compute/capture the tier inputs before a path consumes the Phoenix 1 snapshot. Avoid copying the full score dataset unnecessarily.
6. Include scoring-method and origin-reference compatibility in checkpoint validation. Reject or recompute incompatible tier checkpoints; a `SCRIPT_VERSION` bump alone does not invalidate every resumed checkpoint. Keep reanalysis from stored snapshots supported.

## 7. Data sufficiency and validation

Using the current cached Phoenix 2 data, 65 Singles players and 37 Doubles players meet the 50-score history cutoff using only candidate new charts, before the remaining fitting checks. Some new official folders contain only two or three charts. Report the resulting coverage during the local trial. Keep the existing thresholds for the first implementation; changing them would make the experiment harder to interpret.

If a population cannot fit its score-to-level calibration, its affected scoring estimates remain unrated with an explicit diagnostic. Do not silently borrow the other population's slope, baseline, or median. Retain existing evidence and limited-data labels for sparse but measurable charts.

Required checks:

- Chart-origin membership is disjoint, covers the current S/D catalog, and survives rerates and missing source scores. Both environments use the same reference.
- Changing only new-chart score data leaves legacy scoring estimates, intervals, ranks, and What-if values unchanged; test the reverse too, holding catalog and origin mapping fixed.
- Eligibility, ranks 11–30, contribution windows, source precedence, and ability weights use population-local history. Include a player eligible globally but ineligible within one population.
- Every nonempty `(origin, mode, level)` has final median `L + 0.5`, including even counts with unequal support, ties, singleton groups, and cross-level estimates.
- Missing calibration or target What-if references produce unrated/unavailable output without cross-population fallback.
- Clearing matches the original combined-data result exactly; Pumbility matches the arithmetic mean. Co-op and recommendation outputs are numerically unchanged, allowing only intended metadata/timestamps to differ.
- Consuming and non-consuming paths, checkpoint resumes, and local/hosted payloads preserve the same source partition and model contract.

Run a local tier-only rebuild from the existing snapshots and produce a chart-level before/after comparison with separate coverage and median reports for each origin/mode/level. Verify the recommendation index and active generation remain untouched. Run focused Python tests, frontend checks, and the build, then perform one general review.

## Completed verification

- Rebuilt with `scripts/build_local_recommendations.py --tiers-only` from cached snapshots, without an API capture or recommendation rebuild.
- All 46 measured origin/mode/official-level groups at level 16+ have median `L + 0.5` within serialization tolerance. Rated scoring charts: 1,185 legacy Singles, 100 new Singles, 1,211 legacy Doubles, and 103 new Doubles.
- Clearing and Co-op match the saved pre-change combined aggregate exactly. Pumbility matches the new scoring/unchanged clearing average; two new Doubles charts are unrated for scoring under the existing eligibility rules.
- The recommendation index is byte-identical. A separate run of the recommendation chart model matches every saved pre-change Singles, Doubles, and Co-op record exactly.
- Relevant Python suites and all 64 frontend tests pass, including bidirectional independence, group eligibility, unequal-support centering, missing calibration/What-if references, consumption parity, checkpoint identity, and tier/recommendation separation. The Next.js build passes.
- One general review found two auxiliary model-building scripts still using the old tier scorer. Both were corrected and their relevant checks passed; no further general review was performed.
- The restarted local tier and recommendation pages and both data APIs return HTTP 200. Comparison data and verification are saved under `.local-data/piu-scores/verification/scoring-origins-2026-09-26/`.

No unresolved blocker remains. A later production rollout can use compatible readers followed by stored-snapshot reanalysis; a fresh PIUScores capture is not required.
