# Skill Radar: mode-specific BPM ceilings

Status: independent Singles/Doubles calibration implemented and verified locally.
Production deployment is separate.

## Approved behavior

Keep Method 5 Singles ratings. Undo the most recent recalibration for Doubles,
restoring its Method 4 scale. Mode ceilings need not differ by exactly one level.
S10/D11 remains zero. The SSS+ UG reference and actual performance evidence stay
unchanged. 220+ remains unchanged in both modes.

| BPM | Singles raw UG ceiling | Restored Doubles raw UG ceiling |
| --- | ---: | ---: |
| <150 | 22.8340 | 24.80 |
| 150–159 | 22.9735 | 24.95 |
| 160–169 | 22.4215 | 24.65 |
| 170–179 | 23.1130 | 24.95 |
| 180–189 | 23.1130 | 25.10 |
| 190–199 | 23.9680 | 25.55 |
| 200–209 | 24.7000 | 26.00 |
| 210–219 | 24.2500 | 26.00 |
| 220+ | 25.0000 | 26.00 |

The Singles ceilings rescale Method 4's full-precision ratings of 93, 93, 91, 94,
93, 96, 98, 95 and 100 to the new 100. Doubles does not apply that rescaling.

## Contract and calculation

Each public BPM definition carries a separate aligned scale for each mode:

```text
radarScale = {
  singles: { minimumAlignedLevel: 10, maximumAlignedLevel: singlesCeiling },
  doubles: { minimumAlignedLevel: 10, maximumAlignedLevel: doublesRawCeiling - 1 }
}
alignedLevel = max(0, rawEquivalentLevel - (Double ? 1 : 0))
scale = bpmBucket.radarScale[mode]
uncappedScore = 100 * (alignedLevel - scale.minimumAlignedLevel)
    / (scale.maximumAlignedLevel - scale.minimumAlignedLevel)
radarScore = clamp(uncappedScore, 0, 100)
```

The internal Doubles offset is applied once. Independent mode scales mean equal
aligned levels need not occupy the same radar position. Keep full precision;
never restore Doubles by multiplying rounded or clipped Method 5 scores.

## Implementation

1. Store canonical independent bounds in `skill_radar.py`. Pass the mode to the
   shared normalization helper for both output values and uncapped group ranking.
   Return independent copies of nested scale metadata.
2. Update TypeScript types, normalization and strict validation to require the
   two-mode contract. Backend metadata remains the calibration source of truth.
   Bump to Method 6/profile 10; cache identity already includes both versions and
   the full calibration. Reject Method 5 and malformed/shared-scale payloads.
3. Replace the About section's universal Singles/Doubles alignment claim with
   compact wording about independent mode/BPM ceilings. Keep the existing layout.
4. Regenerate all eligible local profiles and FEFEMZ's same-key official preview
   from cached inputs. Preserve his PG-at-perfect/SG-otherwise assumptions.
5. Run focused Python/frontend tests and typecheck, compare every local bucket
   against saved Method 5 Singles and Method 4 Doubles snapshots, and check the
   desktop/mobile radar and contribution dialog.

## Acceptance

- Every Singles rating matches Method 5; every Doubles rating matches Method 4
  at full precision. Both zero anchors and all unchanged 220+ values pass.
- Per-mode ceiling, midpoint, monotonicity, clipping, sparse-data, uncapped ranking,
  nested-metadata-copy and cache invalidation tests pass.
- Exact contributors, actual scores/plates/Pumbility, raw/display equivalent
  levels, usable/selected counts, missing-data flags, recommendation targets and
  recommendation contents remain unchanged for every listed profile.
- Group membership can change because it uses the corrected normalized ratings.
  Continue ranking all modes together using unclipped values.
- All 200 listed profiles pass public contract validation, and the local API
  serves the new method. FEFEMZ remains at player key `52762986a40b18be3aaa`.
- No fresh score pulls, tier-model refits or production deployment are required.
- Stop after directly relevant verification and one general review pass.

## Local verification

- 42 focused Python radar/pricing tests and 15 frontend tests pass; TypeScript
  typecheck and diff whitespace checks pass.
- All 200 listed profiles pass TypeScript contract validation. Compared all 3,600
  buckets: 1,800 Singles ratings exactly match Method 5, and 1,800 Doubles ratings
  exactly match Method 4. All performance evidence and suggestions are unchanged.
- Independently verified cross-mode uncapped strength/weakness ordering for every
  profile. 1,398 Doubles displayed ratings change from Method 5.
- FEFEMZ retains 170 PG and 287 SG assumptions. His <150 ratings are Singles 93.3
  and Doubles 99.6. The local API serves HTTP 200 with Method 6.
- Desktop/mobile rendering, the five-chart contributor dialog and all 18 axis
  ratings were verified. No horizontal overflow at 390 pixels. The temporary
  QA tab was closed and the viewport override reset.
- No fresh score pulls, model refits or production changes were made.
