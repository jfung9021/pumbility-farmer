# Tier width adjustment: retain 75%

Date: October 27, 2026.

Implementation is complete against the current production branch. Verification:
171 affected Python tests, 329 frontend tests plus the new source-aware 75%
regression case, TypeScript, and the production build. The single review pass
and full local candidate check found one remaining fixed-50% Clearing validator;
its focused repair and nine affected tests pass.

The requested adjustment retains 75% of a fitted rating's distance from its
official midpoint, replacing the current 50% factor. This widens the rating
distribution. It does not change the physical width of tier-list cards.

For official level `L`, midpoint `M = L + 0.5`, and initial difficulty `D`:

```text
adjustedDifficulty = M + 0.75 * (D - M)
```

For example, an S25 initial difficulty of 26.6 becomes 26.325 instead of
26.05. Calculations retain full precision until the existing display formatter.

## Implementation

1. Version the current midpoint policy with factor 0.75. Continue recognizing
   the recorded version-1 factor-0.50 policy when validating historical data.
2. Use the payload's declared factor throughout Scoring, Clearing, target
   variants, interval transforms, and inverse transforms. Replace assumptions
   that undo compression by multiplying by two. Derive Pumbility from adjusted
   components using the generation's existing average or maximum rule.
3. Derive new output from preserved fits rather than multiplying previously
   adjusted values again. Keep historical artifacts readable and keep model
   evidence, nulls, official levels, Co-op, and personal artifacts unchanged.
4. Update tier-list and About explanations to show the recorded adjustment.
   Document a safe local conversion or regeneration path for cached data.
5. Verify historical 50% data, the new 75% policy, point and interval arithmetic,
   inverse transforms, repeated application, component aggregation, and invalid
   policy rejection. Run directly affected Python/frontend suites, TypeScript,
   and the production build.

## Acceptance and stopping rule

- New adjusted generations declare and use factor 0.75 exactly once.
- Historical factor-0.50 generations validate under their recorded policy.
- Positive/negative distances and available difficulty intervals use the same
  factor; supported midpoint anchors stay fixed.
- Existing later spread and tail policies keep their established order.
- Displayed explanations match the recorded generation.
- Relevant checks pass. Perform one general review, repair only demonstrated
  regressions, rerun affected verification, and stop. Deployment, new score
  capture, and unrelated working-tree changes are outside the numeric change.

## Authorized release follow-up

The user subsequently requested push, merge, deployment, and relevant analysis.
Publish only this change from an isolated checkout of current `main`, merge the
PR after verification, deploy that merged revision, and run the cached-input
tier publication workflow. Verify the live generation declares factor 0.75
and that its list and chart-detail responses agree. Preserve existing scores
and recommendations; a new upstream capture is unnecessary for this policy.
