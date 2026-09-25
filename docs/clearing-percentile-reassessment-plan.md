# Clearing percentile and reference-level reassessment plan

Status: accepted for implementation; September 25, 2026.

## Proposed behavior

Use the average skill rating of actual clearers between the inclusive 10th and 30th percentile cutoffs. After the initial Clearing estimate, reassess a chart against the reference for its estimated level when that level differs from its official level. Pumbility then averages Scoring with the final Clearing estimate.

This plan applies reassessment to Clearing. Scoring remains the existing calculation, and Phoenix source weights remain equal for tier calculations. Existing unique-clearer eligibility and mode-specific player skill selection supply the input population.

The accepted behavior is **one reassessment**, based on the initial estimate. Repeated reassessment is not part of this change; it would need a specified cycle policy because it is not guaranteed to converge.

## Calculation

### 1. Select actual clearers in the 10th–30th percentile interval

For each chart, take its finite, usable clearer skill ratings and compute NumPy linear quantiles at 0.10 and 0.30. Select every observed rating satisfying `q10 <= rating <= q30`, including every boundary tie, and average those selected ratings.

Continue counting total clearers, rated clearers, missing ratings, and selected clearers separately. An empty selection produces an unrated chart, even if interpolated cutoffs exist. A single interpolated percentile or an average of the two cutoffs is not a substitute for averaging actual observations.

For example, ratings `[10, 11, 12, 13, 14]` produce cutoffs 10.4 and 11.2, select only 11, and have a mean of 11. The narrower interval can leave more sparse charts unrated; measure that change in the local comparison.

### 2. Build and freeze official-folder references

Calculate the median of measurable chart means separately for each `(mode, official level)` folder, using the existing calibration population. Freeze this reference map before assessing any chart.

Let `s` be the chart's selected-clearer mean skill, `L` its official level, and `M[mode, level]` the frozen reference:

```text
initialDifficulty = L + 0.5 + s - M[mode, L]
```

Initial folder medians remain `L + 0.5`. Singles and Doubles have separate references.

### 3. Reassess against the initial estimate's level

Use the unrounded estimate to select `targetLevel = floor(initialDifficulty)`. Follow the existing display boundary convention for floating-point noise at exact integers; do not round the estimate to one decimal before deciding.

For an official D23:

| Initial estimate | Reference used for reassessment |
| --- | --- |
| 22.9, or any value below 23.0 in the 22 range | D22 |
| 23.0 through values below 24.0 | Keep the initial D23 assessment |
| 24.0 through values below 25.0 | D24 |

An estimate spanning more than one level uses the level containing that estimate directly, for example D23 initially estimated at 21.8 uses the D21 reference.

When the target reference exists:

```text
finalClearingDifficulty = targetLevel + 0.5 + s - M[mode, targetLevel]
```

Keep the same clearers, selected ratings, and player skill values. The chart is evaluated against the target reference without being inserted into that reference's calibration population or changing either folder's reference. It retains its official catalog level, official filters, and official-folder rank membership. The estimated-difficulty view uses its final numeric estimate.

For the proposed one-reassessment policy, store this result even if it falls into another level. The UI can say which reference was used; it must not claim convergence.

If the target folder has no finite reference, retain the initial estimate and record that reassessment was unavailable. Do not invent a reference or clamp the rating to the publication minimum.

### 4. Derive display and composite values from the final estimate

Calculate Clearing difference and effect bands relative to the official midpoint, `finalClearingDifficulty - (officialLevel + 0.5)`. Recompute ranks within the original official folder using final estimates.

```text
finalPumbilityDifficulty = (scoringDifficulty + finalClearingDifficulty) / 2
```

Use unrounded component values before final serialization. Preserve null propagation and the existing evidence thresholds, now based on the new selected-clearer population.

## Calibration consequences and repeated reassessment

The exact median anchor applies to the initial calibration. Final medians are not guaranteed to remain `L + 0.5` after charts are reassessed. Recentring the final results would change the requested target-reference estimates, so this plan does not add a second centering step.

Repeated reassessment can oscillate even with valid fixed references. For example, with chart mean 21.4, D23 reference 22.0, and D22 reference 20.8:

```text
Against D23: 23.5 + 21.4 - 22.0 = 22.9  -> try D22
Against D22: 22.5 + 21.4 - 20.8 = 23.1  -> try D23
```

There is no stable level for that example under this rule. If repeated reassessment is chosen, define which estimate wins a cycle, track visited levels, and stop on a repeated or unavailable reference. A numeric iteration limit alone would make the selected result depend on an arbitrary stopping point. Final-median anchoring and a mandatory match between reference level and final integer rating cannot both be assumed.

## Implementation work

1. **Calculation — `tier_difficulty.py`.** Change the percentile interval, extract construction of the frozen folder references, calculate all initial estimates, apply chart-only reassessment, then derive final Clearing/Pumbility ranks and bands. Keep the passes separate so chart order cannot affect references or results.
2. **Contract and serialization — `lib/types.ts`, `piu_recommendations.py`.** Replace new-generation `q25Skill`/`q50Skill` with `q10Skill`/`q30Skill`. Add `initialEstimatedDifficulty`, `assessmentLevel`, and `reassessmentStatus` (`not-needed`, `applied`, `unavailable`). `folderReferenceSkill` describes the reference actually used for the final estimate; the method metadata retains the complete frozen reference map. Existing `estimatedDifficulty` becomes the final Clearing value. Carry these fields through public and checkpoint serialization.
3. **Versioning — `pumbility_contract.py`, `lib/local-analysis.ts`.** Advance the combined tier schema from 10 to 11, the tier method version from 1 to 2, and the analysis script version. Keep the existing incompatible-checkpoint guards and persisted-number checksum handling. Update version-specific fixtures and deployment checks.
4. **UI and examples — `app/tier-list/page.tsx`, `lib/demo-data.ts`.** Label the interval 10th–30th percentile and show reassessment details when applicable, such as the initial estimate and “Assessed against D22.” Use final ratings for the existing selected-metric sorting/grouping. Update demo calculations and README methodology. Read the installed Next.js guide before UI implementation.
5. **Deployment transition.** New frontend code must correctly render the still-published schema-10 aggregate until schema 11 is ready: retain an explicit legacy read path for the 25th–50th percentile fields and label the old method accurately. Do not call formatting methods on missing new fields or relabel old results as 10th–30th percentile results.

## Direct verification

- Percentile averaging: inclusive boundaries, ties, missing/nonfinite skills, singleton input, and interpolated cutoffs with no observed ratings inside the interval.
- Frozen reference calibration: separate modes, initial odd/even folder medians, sparse measurable charts, and deterministic results regardless of chart order.
- Reassessment: downward and upward crossing; exact 23.0/24.0 boundaries; a value such as 22.95; multi-level crossing; missing references; and the one-reassessment bounce example.
- Population preservation: target references, target chart counts, official levels, and rank membership remain based on the original official cohorts. Changing an incoming probe must not change the target reference or other charts' target-folder calibration.
- Final values: differences/bands/ranks reflect the final estimate; Pumbility uses the final Clearing component at full precision; empty inputs propagate null; support counts use the 10th–30th selection.
- Contracts: new fields survive serialization/checkpoint resume, incompatible old checkpoints are rejected, and the PostgreSQL number-normalization regression remains passing.
- Frontend: both current and transitioning legacy payloads render correctly, method labels match the data, and existing metric selection and navigation work. Run relevant frontend tests and the production build.

## Local comparison and rollout sequence

Run three variants on the same cached snapshots with equal Phoenix source weights:

1. Current production method: 25th–50th average, original-folder calibration.
2. 10th–30th average with original-folder calibration, isolating the percentile change.
3. 10th–30th average plus reassessment, isolating the reassessment effect.

Report initial/final ratings, assessment levels, crossing counts, unavailable references, rated/unrated coverage, selected-player support, largest shifts, and initial/final folder medians. Include examples of low-sample and well-supported charts. Save the comparison as local artifacts.

For release, deploy the implementation, start a fresh normal production analysis, and verify the completed public generation's schema/method version, percentile metadata, equal source weights, reassessment fields, and Pumbility arithmetic. Check the initial median invariant and report final medians rather than asserting that reassessment preserves them. Keep the previous public generation until the new one completes.

Perform one bounded review after implementation and directly relevant verification. Only concrete failures or unmet acceptance criteria justify a focused repair.
