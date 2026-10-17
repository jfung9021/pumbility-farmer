# SSS folder median centering plan

October 18, 2026. Implemented locally as schema 51 after the top-three no-SSS update.

Center the final SSS distribution of supported official level folders at the
official level plus 0.5. Keep the shared-player fit, score-gap calculation and
rare-success rule as separate, auditable stages. This is a final normalization
to the chosen folder reference; it does not create additional SSS observations.

## Frozen D26 baseline

The rebuilt local candidate uses the average of the three highest distinct
player best scores when nobody reaches 990,000. Its D26 results are:

| Quantity | Current value |
|---|---:|
| Charts in the folder | 39 |
| Charts with a final SSS rating | 36 |
| Mixed charts with a finite first-pass rating | 24 |
| First-pass median before midpoint compression | 25.545875 |
| First-pass median after midpoint compression | 26.022938 |
| Final SSS median after boundary and rare-success rules | 26.186575 |
| General Scoring median | 26.498937 |
| Official Clearing median | 26.500000 |
| Final SSS charts displayed as D26.1 | 16 of 36 |

The official Doubles SSS calibration has supported anchors at levels 20 through
25. A reference chart needs 20 usable players, five outcomes on each side of
SSS and comparable chart metadata; a folder needs five reference charts. Only
one D26 chart qualifies. D26 therefore uses the tangent extrapolation beyond
D25 rather than an independently measured D26 anchor.

The regularized mixed-chart effects produce a D26 median effect of 5.759085;
the existing curve would need an effect of 7.641755 for an initial difficulty of
26.5. The 50% midpoint compression pulls the resulting median toward 26.5 but
does not place it there. The first-pass median remains 26.022938.

Crowding has another cause. The frozen D26 range is 25.464091 to 26.194796.
Its highest 5% is only 26.158260 to 26.194796, a width of 0.036535. The
rare-success rule allocates targets in that band for 21 D26 charts with one to
four SSS outcomes. Blend weights reduce the correction for two to four
successes, so their final values need not all be inside that band. Nine charts
have one SSS and receive the full correction. One-decimal display truncation
puts the entire target band in D26.1.

The low center is already present before the top-three update. Averaging the
top three increases no-SSS shortfalls and affects their upper extensions; it
does not create the missing D26 anchor or the narrow rare-success band.

## Proposed centering policy

Apply this follow-up to official SSS Scoring at current levels 20 and above,
independently for Singles and Doubles. General Scoring, all Clearing, submitted
SSS below 20, Co-op and personal recommendation data retain their current
values. Recompute only the affected SSS Pumbility means and ranks afterward.

A folder can supply its own centering offset when it has at least five finite,
mixed-outcome first-pass chart ratings and at least five finite final ratings.
Use the same first-pass population that supports the existing observed folder
range. Compute the center from all finite final SSS points, including added
boundary estimates and rare-success corrections, and exclude Unrated charts.
This support rule permits D26 and avoids treating a folder made entirely of
one-sided boundary estimates as an independently measured center.

For a supported folder, let M be level + 0.5 and v be the current final values:

    finalShift = M - median(v)
    centeredFinal_i = v_i + finalShift

For D26, finalShift is +0.3134248904 and the centered median is exactly 26.5.
Adding a constant preserves ordering and every pairwise distance within that
folder. Different folder offsets can change ordering across official levels;
this follows the chosen requirement that a supported folder median represents
its official midpoint.

Unsupported folders interpolate the centering offset between supported folders
in the same source and mode, or borrow the nearest supported offset outside
that span. Do not borrow across Singles and Doubles or submitted and official
cohorts. Record donor levels and retain Provisional evidence. With no supported
donor, retain the existing estimate and report centering as unavailable.

Borrowed folders do not promise their own median is level + 0.5. D27 has only
one finite mixed first-pass chart, while D28 and D29 have none. Their current
final medians are approximately 29.445, 31.977 and 42.432. Forcing each to its
official midpoint would erase substantial boundary evidence, especially for
the single D29 chart. The proposed policy instead borrows D26's offset.

## Once only midpoint compression

Implement centering before the existing 50% compression in the published
calculation, even though its offset is determined from a deterministic preview
of final values. Let z be the uncompressed second-pass point:

    preview_i = M + 0.5 * (z_i - M)
    initialShift = 2 * (M - median(preview))
    adjustedInitial_i = z_i + initialShift
    final_i = M + 0.5 * (adjustedInitial_i - M)

For D26, initialShift is +0.6268497809. No second compression is applied to
already-compressed values. There is one frozen offset per folder and no
iterative feedback into the fit, reference anchors, score-gap slope, boundary
inputs or rare-success allocation.

Keep original frozen edges and first-pass values. Show published effective
edges as original edges plus finalShift, and translate the rare-success band
by the same amount. This preserves the relationship of boundary estimates to
their edges and rare-success points to their target band. Chart details must
distinguish the original fit, second-pass result and folder normalization.

Translate existing conditional first-pass intervals by the same fixed offset.
State that those intervals condition on the folder offset. Boundary and
rare-corrected estimates retain unavailable formal intervals. Do not use
centering to improve an evidence status.

## Crowding and Pumbility

Keep the existing rare-success band width in this follow-up. The D26 band would
move from 26.158260–26.194796 to 26.471685–26.508220. The preview changes the
current 16 charts in D26.1 to 11 charts in D26.4 and six in D26.5 across the
whole folder. Centering fixes the misplaced median while retaining the current
within-folder distances. A wider or adaptive rare-success band would be a
separate change to the accepted upper-5% policy.

SSS Pumbility remains the full-precision arithmetic mean of centered SSS
Scoring and the existing Clearing point. Do not independently center
Pumbility: the median of chartwise averages need not equal the average of the
component medians. General Pumbility remains unchanged.

## Implementation sequence

1. Freeze the current schema-50 top-three candidate and its source hashes.
   Add a pure local post-processing overlay that reads this candidate. No data
   fetch, shared-player fit, Clearing fit or bootstrap rerun is needed.
2. Allocate schema 51 after checking availability. Add versioned centering
   metadata with source, mode, level, population chart IDs, finite counts,
   support basis, donors, original median and initial/final shifts. Preserve
   schema-50 recovery and reading.
3. Store uncentered metrics and original model coordinates separately from
   adjusted initial/final values. Apply offsets to points, existing intervals,
   deltas and effective range diagnostics, then recompute SSS ranks, bands and
   SSS Pumbility from the displayed components.
4. Extend Python and frontend validation to verify population selection, exact
   supported-folder medians, borrowed offsets, interval handling and one
   compression. Extend selectors and chart details to distinguish centering
   from the SSS model and rare-success correction.
5. Produce a separate local candidate with before/after medians, histograms,
   ordering checks, donor folders and chart examples. Keep the current
   top-three candidate available for comparison and rollback.
6. Test odd/even median populations, unavailable charts, exact ties, boundary
   extensions, rare-success band translation, sparse donors and missing donors.
   Assert unchanged General/Clearing data, unchanged within-folder differences,
   both Pumbility formulas and no fabricated intervals. Verify D26's actual
   median is 26.5 at full precision; an even-sized folder can display its two
   middle charts on opposite sides of that number after truncation.
7. Run affected tests, TypeScript, a build and browser checks. Perform one
   general review, repair only proven regressions, and stop.

The numeric diagnosis is saved privately in
`.local-data/sss-refined-experiment/median-centering-diagnosis.json`. The
top-three implementation audit is
`.local-data/sss-refined-experiment/top-three-change-audit.json`.

## Implementation outcome

The separate centered local candidate has an actual D26 median of 26.5 across
36 finite SSS ratings, shifted by +0.3134248904 from its frozen baseline.
Supported official Singles 20–25 and Doubles 20–26 have exact x.5 medians.
Singles 26 and Doubles 27–29 borrow their nearest supported mode offset.
The data audit confirms preserved within-folder ordering and spacing, unchanged
General/Clearing components, unchanged below-20 point values, unchanged original
model and refinement diagnostics, and 112 unchanged frozen input files.

The builder performs no source fetches, model refits or bootstrap refits. The
schema-50 candidate remains available for rollback. Local verification and
the preservation report are under `.local-data/sss-centered-experiment/`.
