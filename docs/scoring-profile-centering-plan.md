# Folder-centered score-profile calibration

## Scope and calculation

Local tier experiment only, using existing cached Phoenix 1 and Phoenix 2 data.
Retain the equal-weight q25/q50/q75 score profiles and separate Singles/Doubles
reference curves. Treat the closest-curve match as an intermediate signal.

For every rated chart in an official mode/level folder:

```text
center = median(raw profile matches in this official mode/level folder)
offset = chart raw profile match - center
proposal = official level + 0.5 + scale * offset
```

This anchors every rated folder median to level + 0.5. Matching the raw profile of
a typical S23 does not force an officially S22 chart to receive 23.5: its own
folder center and the common scale determine the final rating. Preserve ordering
and ties within each folder. Do not reassess or change official chart membership.

Select the largest shared scale in {0.00, 0.01, ..., 1.00} that yields at most
three two-grade moves across rated Singles and Doubles at official levels 16+.
Count higher and lower moves together, including charts with limited data.
Use the serialized six-decimal estimates: a move is proposal >= level + 2 or
proposal < level - 1. For example, S21 -> 23.0 or 19.9 counts; 20.0 does not.
Ties may yield fewer than three moves. Do not select individual exceptions or
increase an already acceptable spread merely to reach three.

## Implementation

1. Add a second calibration pass after raw profile matching. Export raw matches,
   folder centers, the fitted scale, count, and method metadata for inspection.
2. Apply the same positive affine calibration to the existing joint-bootstrap
   interval endpoints, holding the curve, folder center, and scale fixed.
3. Recalculate scoring ranks/bands and Pumbility. Preserve Clearing, Co-op,
   player skills, recommendation files, and the production scoring path.
4. Give the local experiment a new schema/method identifier. Update local-reader
   validation and UI explanations, including the distinction between raw profile
   extrapolation and the final calibrated estimate. Keep What-if hidden for this
   experiment; no chart reassessment is introduced.
5. Rebuild locally from cached snapshots and record before/after diagnostics.

## Acceptance checks

- Each rated official folder median is level + 0.5 within serialization precision.
- At most three two-grade moves in the final exported/displayed scoring values.
- The next 0.01 scale violates the cap unless the selected scale is already 1.00.
- Matching another folder's typical profile does not bypass final calibration.
- Interval endpoints, ranks, and Pumbility use the calibrated scoring estimates.
- All Clearing/Co-op records and recommendation file hashes remain unchanged.
- Focused Python/frontend tests, build, and local API smoke checks pass.
- Perform one general review; repair only proven regressions, then stop.
