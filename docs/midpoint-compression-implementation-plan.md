# Scoring and Clearing midpoint compression implementation plan

Date: October 17, 2026. Status: implemented locally.

Schema 44 is active with the fixed factor 0.5 and producer 6.18. Original fits are retained in `initialFit`; repeated conversion and metadata refresh are numerically identical. Verification covers 263 Python tests, 87 frontend tests, TypeScript, the production build, and browser checks for Scoring, Clearing, Pumbility, demo, original-fit explanations, and the level filter. Every within-folder rank is preserved, including fitted values that collapse to the same float after compression. Co-op and protected personal artifacts retain their baseline hashes. Evidence and screenshots are in `output/midpoint-compression-2026-10-17/`. The local server is running on port 3000.

After the existing fits, halve each rated chart's distance from its current official level plus 0.5. Apply this to Singles and Doubles Scoring and Clearing. Preserve the underlying fitted models, source evidence, median anchors, and player skill calculations.

## Formula and display

For current official level `L`, fitted difficulty `D`, and midpoint `M = L + 0.5`:

```text
finalDifficulty = M + 0.5 × (D − M)
finalDelta      = 0.5 × (D − M)
```

The midpoint comes from the official chart level, not the integer part of the fitted rating or a newly calculated sample median. A chart exactly at its midpoint stays there. Easier estimates move upward toward the midpoint; harder estimates move downward toward it.

The example of a 26.6 rating being 1.1 above its midpoint describes an official S25 chart:

```text
25.5 + 0.5 × (26.6 − 25.5) = 26.05
```

The existing one-decimal formatter truncates, so exactly 26.05 displays as S26.0. A fitted value of 26.7 becomes 26.10 and displays as S26.1. All calculations use the original full precision; displayed tenths never become inputs. For an official S26 chart, the midpoint would instead be 26.5.

This makes the final rating a blend of the fitted difficulty and the official midpoint. Absolute difficulty ordering between charts from different official folders can change because their centers differ. Ordering within each official folder is preserved. Supported reference-folder median anchors remain at `L + 0.5`; the existing fitted curves stay intact.

## Scope and invariants

- Apply Scoring compression to rated current S20+/D20+ charts. Existing below-20 and unsupported Scoring values remain unrated.
- Apply Clearing compression to all currently rated Singles/Doubles Clearing charts, including levels 16–19. Preserve the existing top-30 unique-clear skill and 200-record qualification rules.
- Keep nulls null, eligibility and support counts unchanged, and all evidence/provisional/extrapolation decisions based on the original fit and evidence.
- Preserve Co-op, raw scores, source captures, chart IDs, official levels, BPM, player skills, recommendations, and personal-score overlays.
- Keep the current truncation rule and `Only 20+` filter.

Compression is a fixed presentation policy after model calibration. It must not alter fitted score-point costs, score gaps, clearer-skill thresholds, anchor selection, or any parameter used by a later fit.

## Preserve the initial fit and prevent repeated compression

Add a shared Python finalizer, for example `tier_postprocessing.py`, with one fixed versioned policy. Store the uncompressed estimate and any difficulty interval endpoints for each metric in an explicit `initialFit` object. Current public estimate fields hold the final compressed result.

The finalizer always derives final values from the stored initial fit. Reapplying it, reloading a payload, or refreshing metadata must produce the same numerical result rather than halve the distance again.

Partial rebuilds need explicit handling. Each producer identifies which metric it has just fitted. For a newly fitted metric, replace its stored initial-fit values with the fresh output, including null results. For an untouched metric, retain and reuse its stored initial fit. A first application to the known current uncompressed generation captures its existing full-precision values as the initial fit. Unknown or conflicting processing metadata fails validation.

Suggested interface:

```text
finalize_tiers(payload, newly_fitted_metrics={"scoring", "clearing"})
```

A Scoring-only rebuild supplies `{"scoring"}`, a Clearing-only rebuild supplies `{"clearing"}`, and a metadata-only refresh supplies an empty set. This distinction prevents an older stored initial fit from overwriting a new result and prevents an untouched metric from being compressed twice.

## Finalize derived values in one order

1. Finish the existing model fits and calibration, retaining their full precision.
2. Capture or refresh the initial-fit values for the metrics that were recomputed.
3. Compress Scoring and Clearing values and their available difficulty interval endpoints around the same chart midpoint.
4. Recalculate deltas, effect bands, tier buckets, and affected summary counts from the final values. Recompute existing rank fields with their established sorting and tie rules. Within-folder ordering remains unchanged; absolute cross-folder ordering can change. The existing Scoring mode rank sorts deltas, so multiplying every delta by the same positive factor preserves that ordering.
5. Recompute Pumbility as `max(finalScoring, finalClearing)`, requiring both components. Retain its support counts and weaker evidence status.
6. Serialize the complete artifact and updated method/provenance metadata atomically.

Pumbility receives the compressed components once. It does not receive another independent compression pass. As an independent verification identity, using the same midpoint and positive factor for both components gives:

```text
max(compress(S), compress(C)) = compress(max(S, C))
```

The component that determines Pumbility for an individual chart therefore remains the same.

## Intervals and evidence

Transform each available fitted difficulty endpoint with the same function as the point estimate. Scoring's `difficultyDeltaCi95Low/High` must agree with the transformed endpoints minus the midpoint. The interval width halves in difficulty units. Preserve raw score-point cost and score-gap intervals unchanged.

Current cross-level Clearing has no confidence intervals; keep those fields null. Preserve all evidence counts, qualification exclusions, unsupported-folder flags, and raw-fit extrapolation flags. A compressed value moving inside a displayed range does not improve the underlying evidence status.

Describe Scoring intervals as transformed conditional intervals under a fixed compression factor. The narrower displayed interval is a consequence of this fixed transformation, not new evidence or an additional statistical fit. Label supported ranges and fitted costs as properties of the initial fit where they appear beside a compressed rating.

## Contracts and integration

Use a separate public method declaration, such as:

```json
{
  "ratingPostprocessing": {
    "version": 1,
    "method": "official-midpoint-compression",
    "factor": 0.5,
    "reference": "current-official-level-plus-0.5",
    "metrics": ["scoring", "clearing"],
    "baseAnalysisSchemaVersion": 43
  }
}
```

Assign a new combined schema, proposed as 44, after confirming it is still available when implementing. Keep the existing Scoring and Clearing method identities as the identities of the initial fits. Retain the existing maximum Pumbility policy. Bump the producer identity and include the exact post-processing policy in cache/checkpoint compatibility checks.

Update frontend validation to recognize the new schema and its underlying fit contract. Validate original fitted values against their source curves and evidence, then validate final values and interval endpoints against the compression formula. Validate final deltas/bands/ranks and maximum Pumbility separately. Existing schema 41/42/43 artifacts continue to read and display under their recorded methods without automatic transformation.

Integrate the finalizer at the end of `scoring_percentile.py::build_production_tier_payload`, after both selected overlays. Apply the same finalizer to the independent Scoring/Clearing overlay commands and the metadata refresh path in `scripts/build_local_recommendations.py`. Underlying producer updates must identify newly fitted metrics explicitly. Publication and resumed checkpoints in `analysis_runtime.py` must require the matching processing policy for the new generation.

The local conversion command should consume the current complete aggregate and write a new processed candidate without refitting either model or rebuilding recommendations. It should preserve source dates, retain initial producer provenance, and advance only the new artifact's generation time and method identity. Serve the candidate through the existing local API after validation and preservation checks pass.

Primary implementation locations:

| Concern | Files |
|---|---|
| Shared transform and finalization | New `tier_postprocessing.py`, existing rank/metric helpers in `tier_difficulty.py` |
| Fit lifecycle and final publication | `official_cross_level_scoring.py`, `cross_level_clearing.py`, `scoring_percentile.py`, `scripts/build_local_recommendations.py` |
| Schema, cache, and checkpoint identity | `pumbility_contract.py`, `analysis_runtime.py` |
| Types and validation | `lib/types.ts`, `lib/local-analysis.ts`, a small shared frontend processing-policy helper |
| Detail explanations and demo | `app/tier-list/page.tsx`, `lib/demo-data.ts`, README |
| Regression coverage | Focused processing tests plus existing producer, runtime, builder, and frontend suites |

The production report exporter already treats the local cross-level model as unsupported. Retain its clear unsupported-model response for the new processed local schema; a new report design is outside this change.

## Projected results using the current local data

These are one-decimal displays calculated from the original full-precision values and verified against the active processed tier list. The original projection is saved at `output/midpoint-compression-plan-2026-10-17/projection.json`.

| Chart | Scoring before → after | Clearing before → after |
|---|---|---|
| Iolite Sky S23 | 23.8 → 23.6 | 23.0 → 23.2 |
| Dement S23 | 24.0 → 23.7 | 23.5 → 23.5 |
| Pavane D23 | 25.2 → 24.3 | 24.1 → 23.8 |
| Rush-Hour D24 | 25.1 → 24.8 | 24.5 → 24.5 |
| HTTP D24 | 25.0 → 24.7 | 24.4 → 24.4 |
| Eternal Universe D25 | 25.3 → 25.4 | 24.9 → 25.2 |

The current inputs retain 1,312 rated Scoring values, 2,598 rated Clearing values, and 1,306 rated Pumbility values. The displayed tenth would change for 1,175 Scoring, 2,187 Clearing, and 1,133 Pumbility values. Some full-precision changes remain inside the same displayed tenth.

## Acceptance and bounded verification

Use meaningful tests for symmetry above/below the midpoint, a fixed midpoint, full precision at display boundaries, missing values, and the factor 0.5. Verify idempotence, refreshing only one metric, replacing a formerly rated result with null, and deriving a different policy from initial-fit values rather than already processed values.

Verify interval ordering/containment, halved interval width, unchanged raw intervals and evidence flags, maximum Pumbility from the final components, and preservation of folder ordering. Confirm reference anchors still map to the same midpoints. Unsupported Scoring and unqualified Clearing remain unrated.

Add contract tests for genuine historical generations, the new valid schema, stale composites, wrong factors, mismatched base methods, and already processed checkpoints under incompatible policy. Maintain the distinction between historical reading and resumable current production work.

Freeze the current aggregate and protected artifact hashes before implementation. The candidate must preserve all initial fitted values, costs, thresholds, anchors, player eligibility, source metadata, Co-op, recommendation artifacts, and personal-score artifacts. Verify every rated final delta equals half its initial delta within numerical tolerance.

Run affected Python model/finalizer/runtime/builder tests, frontend tests, TypeScript, and a production build. Read the relevant installed Next.js guides before editing frontend code. Browser checks should cover Scoring, Clearing, Pumbility, original-fit detail values, interval text, a historical generation, demo, and `Only 20+`. Save a before/after comparison with the six charts above.

Perform at most one general review after implementation. Repair proven regressions, rerun only affected checks, and stop when these criteria pass. This implementation and preview are local; production deployment is outside this change.
